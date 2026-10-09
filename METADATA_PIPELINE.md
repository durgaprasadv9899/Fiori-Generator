# Metadata-Driven Fiori Generator — Architecture

This POC now discovers the backend automatically instead of asking the user to
type Entity Set / Key / Fields. The user provides a **requirement** + an **OData
service** (URL or BTP destination); the AI reads the real metadata and designs
the app.

## Pipeline

```
Requirement + OData URL/Destination
        │
        ▼
Module 1  odata_service / metadata_reader   ── fetch $metadata
        │        (direct URL │ via BTP destination │ paste XML)
        ▼
Module 2  metadata_parser                   ── EDMX (v2/v4) → JSON business model
        │
        ▼
Module 3  business_analyzer                 ── requirement + model → build plan
Module 4  screen_planner                    ──   (app type, actions, blocks, fields)
        │        (AI, validated against real metadata — cannot hallucinate fields)
        ▼
Module 5  code_generator → generator.py     ── plan → AppSpec → full UI5 project
        │
        ▼
Module 6  deployment_generator/odata_config ── mta.yaml, xs-app.json, xs-security,
        │                                       destination(s), connectivity, approuter
        ▼
validator → preview → deployer (CF)         ── build, provision services, deploy
```

## Modules (matches the requested folder structure)

| File | Role |
|------|------|
| `app.py` | Streamlit UI; metadata auto-discovery + generate/deploy |
| `odata_service.py` | Module 1 facade — `get_metadata(mode, ...)` |
| `metadata_reader.py` | Module 1 impl — direct / destination / manual fetch |
| `metadata_parser.py` | Module 2 — EDMX (v2+v4) → JSON business model |
| `business_analyzer.py` | Modules 3+4 — AI build plan, validated & repaired |
| `screen_planner.py` | Module 4 facade — screen-plan view of the plan |
| `prompt_builder.py` | Metadata-enriched AI prompt construction |
| `code_generator.py` | Module 5 orchestrator — pipeline → `generator.py` |
| `generator.py` | UI5 file generation (views, controllers, manifest…) |
| `deployment_generator.py` | Module 6 facade over `generator`+`odata_config` |
| `odata_config.py` | Multi-service + auth + destination/MTA config |
| `validator.py` | `validate_and_fix` + pre-deploy structural checks |
| `deployer.py` | Cloud Foundry build/login/deploy + diagnostics |

## Two connection paths (deliberately separate)

- **Generation time** (`metadata_reader`): reads `$metadata` from wherever the
  Streamlit host can reach — direct URL, a BTP destination service key, or
  pasted XML. On-prem systems behind Cloud Connector often aren't reachable
  from the generation host; **paste XML** is the always-works fallback.
- **Runtime** (deployed app): browser → AppRouter → BTP destination →
  (Connectivity → Cloud Connector) → Gateway. Configured via `odata_config`
  and provisioned by the MTA deploy. This is unchanged by the metadata feature.

## Safety property

The analyzer is given a compact summary of the **real** entity sets and fields
and is constrained to choose only from them. Any invalid choice the AI returns
is repaired against the model before generation, so a hallucinated field can
never reach the generated code. If the AI call fails entirely, a deterministic
plan derived from the metadata is used instead — generation never blocks.

## Enterprise enhancements (annotations, relationships, sample data)

Beyond bare metadata, the pipeline now consumes the signals enterprise SAP
services actually carry:

- **Annotation reader** (`annotation_reader.py`): extracts `UI.LineItem`,
  `UI.SelectionField`, `UI.FieldGroup`, `Common.ValueList`, and `Capabilities`
  (Insert/Update/DeleteRestrictions) from V4 inline annotations, and the
  equivalent `sap:` attributes (label, filterable, value-list, semantics,
  creatable/updatable/deletable) from V2. LineItem drives default list columns;
  SelectionField drives the filter bar; ValueList marks value-help fields;
  Capabilities gate which CRUD actions are offered.

- **Relationship-aware generation**: navigation properties (header → items) are
  described to the analyzer; a header entity with a to-many navigation yields an
  ObjectPage/MasterDetail with the child rendered as a related responsive table.

- **Sample-data inspection** (`sample_data.py`): reads the top ~20 rows to detect
  enum-like fields (→ colored status badges), numeric ranges, and mostly-empty
  columns. A field like `Status` with values Open/Approved/Rejected becomes a
  `formatStatusState` formatter mapping each value to a UI5 ValueState
  (Warning/Success/Error) and a colored `ObjectStatus` in the generated view.
  Reachability caveat matches metadata: for on-prem services unreachable from the
  generation host, paste a sample JSON response instead.

- **Enriched AI prompt** (`business_analyzer.py`): the build-plan prompt now
  includes metadata + navigation relationships + annotations + CRUD capabilities
  + sample-data value profiles, so the plan (app type, list/filter/status/
  value-help fields, child set) reflects the real service, not guesses.

All four enrich the existing freestyle generator via an optional `plan_hints`
channel on `AppSpec`; when absent (manual, non-metadata path) output is
byte-for-byte unchanged, so there's no regression risk to the existing flow.

## Design recommendation, Fiori Elements, and quality validation

Three further capabilities sit on top of the pipeline:

- **Design Recommendation Engine** (`design_recommender.py`): runs *before* code
  generation and produces an inspectable recommendation — Freestyle vs Fiori
  Elements, the floorplan (List Report / Object Page / Worklist / Analytical, or
  their freestyle equivalents), and which rich controls to include (table,
  objectPage, chart, timeline, wizard, upload, treeTable, calendar, filterBar,
  kpiCards, valueHelp). The AI proposes; the engine then *validates* against
  what the service can actually support — Fiori Elements is only chosen when the
  service carries UI annotations, and a control is dropped unless the metadata
  justifies it (timeline needs a date, chart/kpiCards need a measure, valueHelp
  needs a value list, etc.). With no AI available it falls back to deterministic
  rules. This makes the tool behave like a solution architect, not just a
  code generator.

- **Fiori Elements output** (`fiori_elements_generator.py`): when the
  recommendation is `fiori_elements`, the pipeline emits a real FE app instead of
  freestyle views — a `manifest.json` declaring `sap.fe.templates.ListReport` /
  `ObjectPage` with List-Report→Object-Page routing, and a local
  `annotations/annotations.xml` (UI.LineItem, UI.SelectionFields, UI.HeaderInfo,
  UI.Identification, UI.Facets). A header entity with a to-many navigation gets a
  UI.ReferenceFacet → child UI.LineItem, so the Object Page renders the item
  table with `$expand` handled by the FE runtime — the header+items+navigation
  the roadmap asked for, the SAP-standard way. The shared deployment files
  (mta.yaml, xs-app.json, xs-security.json, destinations, connectivity) are the
  same regardless of UI target.

- **Quality Validator** (`quality_validator.py`): before deployment, scans the
  generated project for JSON/XML/JS well-formedness, manifest structure, routing
  integrity, i18n coverage, binding paths, and controller references. It
  auto-fixes the safe, unambiguous issues (trailing-comma JSON, missing i18n
  keys with humanized defaults) and reports everything structural or ambiguous
  (bad routing target, malformed XML, unresolved service placeholder) for the
  developer — it never silently changes behaviour. `validate_and_autofix()`
  returns before/after reports plus the merged, repaired project.

## Relationship-aware Object Page + Quality Validator

- **Object Page** (`app_type="ObjectPage"`): when the analyzer finds a header
  entity with a to-many navigation (e.g. SalesOrder → ToItems), it generates a
  real `sap.uxap.ObjectPageLayout`: an object header bound to a single entity
  by key, a General Information field-group section, and an **expand-bound child
  items table** (the controller binds the object by key with `$expand` of the
  navigation, so header + items load in one read). Routing is wired list →
  `RouteObject/{objectId}` → object page, `sap.uxap` is added to the manifest
  dependencies, and status fields render as colored `ObjectStatus` in both the
  header and the form. Generated per the `items_nav_name` (the real OData
  navigation property), not the entity-set name.

- **Quality Validator** (`quality_validator.py`): static, pre-deploy checks over
  the generated files — JSON well-formedness (manifest/xs-app/package), manifest
  structure (required sections, dataSources, routing, models), XML
  well-formedness of every view/fragment, routing integrity (every route target
  defined; every target viewName has a matching view file), binding-path
  sanity (best-effort against known fields), i18n completeness (every
  `{i18n>key}` used in a view exists), and controller references (every view's
  controllerName has a matching file). Findings are severity-tagged
  (error/warning/info); detection only, no auto-fix. Runs automatically after
  generation in both the metadata and manual paths, with a report in the UI.


