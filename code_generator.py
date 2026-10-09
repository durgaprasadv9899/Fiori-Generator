"""
code_generator.py — Module 5 orchestrator: the metadata-driven pipeline.

Ties the new modules together and hands off to the EXISTING generator.py, so we
reuse all the file-generation, auth/destination wiring, and validation we've
already built and debugged rather than duplicating it.

Full flow (one call):

    reader (fetch $metadata)  ->  parser (EDMX -> business model)
        ->  analyzer (requirement + model -> validated build plan)
        ->  plan -> AppSpec kwargs  ->  generator.generate_app_files(...)

The caller (Streamlit) supplies:
  - how to reach metadata (already-fetched EDMX text, since the app handles the
    reader UI/mode selection and passes the raw XML here)
  - the requirement text
  - the OData service URL + auth/destination choices (for the DEPLOYED app)
  - the ai_fn callable

Returns everything the UI needs: the parsed model, the chosen plan, the built
AppSpec, and the generated files dict.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

import metadata_parser as mdp
import annotation_reader as ar
import sample_data as sd
import business_analyzer as ba
import design_recommender as dr
import fiori_elements_generator as fe
import quality_validator as qv
from odata_config import ODataService, AUTH_NONE
from generator import (AppSpec, generate_app_files, _make_auth_config_files,
                       _make_mta_yaml_hardcoded, _make_root_xs_app_json_hardcoded)


def deployment_files_for(spec: AppSpec) -> Dict[str, str]:
    """Collect the shared deploy/config files (mta.yaml, xs-app.json,
    xs-security.json, destinations, .env, package.json, ui5.yaml) for an FE app,
    reusing the freestyle generator's hardcoded builders so deployment behaves
    identically regardless of UI target."""
    files: Dict[str, str] = {}
    if not spec.with_deployment:
        return files
    files["mta.yaml"] = _make_mta_yaml_hardcoded(spec)
    files["xs-app.json"] = _make_root_xs_app_json_hardcoded(spec)
    files.update(_make_auth_config_files(spec))  # xs-security, destinations, .env, etc.
    return files


@dataclass
class PipelineResult:
    model: Dict                     # parsed business model (+ annotations)
    plan: Dict                      # validated build plan
    spec: AppSpec                   # the AppSpec handed to the generator
    files: Dict[str, str]           # generated project files
    sample_profiles: Dict           # per-entity-set sample-data profiles (may be empty)
    recommendation: Dict = None     # design recommendation (target/floorplan/controls)
    quality: Dict = None            # quality report (after auto-fix)
    quality_fixes: list = None      # human-readable auto-fixes applied


def build_from_metadata(
    *,
    edmx_text: str,
    requirement: str,
    app_id: str,
    app_name: str,
    service_url: str,
    ai_fn: Callable[[str, str], str],
    # deployed-app connection choices (mirror the Stage-1 destination options)
    auth_type: str = AUTH_NONE,
    odata_version: Optional[str] = None,
    use_existing_destination: bool = False,
    destination_name: str = "",
    on_premise: bool = False,
    theme: str = "sap_horizon",
    with_deployment: bool = True,
    # sample-data inspection (optional; reachability caveat like metadata)
    inspect_samples: bool = False,
    sample_user: Optional[str] = None,
    sample_password: Optional[str] = None,
    sample_verify_ssl: bool = True,
    sample_manual_json: Optional[str] = None,
) -> PipelineResult:
    """Run the whole metadata -> annotations -> sample-data -> plan -> code
    pipeline and return the result."""
    # 1) Parse metadata, then enrich with annotations (UI.LineItem, ValueList,
    #    Capabilities, SelectionFields, semantics).
    model = mdp.parse_metadata(edmx_text)
    model = ar.enrich_model_with_annotations(model, edmx_text)
    version = odata_version or model["odata_version"]
    is_v4 = version.startswith("4")

    # 2) Sample-data inspection (item #6) — best effort. We only need the
    #    primary set for the plan, but profile all sets that we can reach cheaply
    #    is overkill; profile the ones the analyzer is most likely to pick by
    #    doing the primary + its first nav child after we know the plan. To keep
    #    it single-pass, profile every set's properties lazily is avoided; we
    #    inspect the primary candidate = first set here and refine post-plan.
    sample_profiles: Dict = {}
    if inspect_samples:
        for es in model["entity_sets"]:
            props = es["properties"]
            if sample_manual_json:
                # Manual JSON only applies to the primary/first set the user pasted.
                if es is model["entity_sets"][0]:
                    prof = sd.profile_from_manual_json(sample_manual_json, props)
                    if prof.get("reachable"):
                        sample_profiles[es["name"]] = prof["profile"]
                continue
            res = sd.inspect_entity_set(
                service_url, es["name"], props,
                user=sample_user, password=sample_password,
                verify_ssl=sample_verify_ssl, odata_v4=is_v4, top=20,
            )
            if res.get("reachable"):
                sample_profiles[es["name"]] = res["profile"]
            else:
                # Unreachable set — stop probing further sets to avoid long hangs;
                # generation proceeds on metadata + annotations alone.
                break

    # 2b) Design Recommendation (runs before generation): Freestyle vs Fiori
    #     Elements, floorplan, and rich controls — validated against the model.
    recommendation = dr.recommend(requirement, model, ai_fn,
                                   sample_profiles=sample_profiles)

    # 3) AI decides the build plan (Modules 3+4) with the rich context.
    plan = ba.analyze(requirement, model, ai_fn, sample_profiles=sample_profiles)
    kwargs = ba.plan_to_appspec_kwargs(plan, model)

    # 3) Build the primary service object with the metadata-derived config +
    #    the user's chosen connection strategy for the deployed app.
    primary = ODataService(
        id="mainService",
        url=service_url.strip(),
        odata_version=version,
        entity_set=kwargs["entity_set"],
        key_field=kwargs["key_field"],
        title_field=kwargs["title_field"],
        fields=list(kwargs.get("fields", [])),
        auth_type=auth_type,
        is_primary=True,
        use_existing_destination=use_existing_destination,
        destination=destination_name.strip(),
        on_premise=on_premise,
    )

    # 4) Build the AppSpec (mirrors legacy fields from the primary service so the
    #    existing generator's views/controllers bind correctly).
    spec = AppSpec(
        user_prompt=requirement,
        app_type=kwargs["app_type"],
        app_id=app_id,
        app_name=app_name,
        odata_url=service_url.strip(),
        entity_set=kwargs["entity_set"],
        key_field=kwargs["key_field"],
        title_field=kwargs["title_field"],
        fields=list(kwargs.get("fields", [])),
        theme=theme,
        with_deployment=with_deployment,
        items_entity_set=kwargs.get("items_entity_set", ""),
        items_key_field=kwargs.get("items_key_field", "ItemId"),
        items_title_field=kwargs.get("items_title_field", "Product"),
        items_fields=list(kwargs.get("items_fields", [])),
        items_nav_name=kwargs.get("items_nav_name", ""),
        services=[primary],
        auth_type=auth_type,
        plan_hints=_build_plan_hints(plan, sample_profiles, model),
    )

    # 5) Generate the project. If the recommendation is Fiori Elements (only
    #    when the service has UI annotations), emit an FE app (annotations.xml +
    #    FE manifest routing) and merge in the shared deployment files. Else use
    #    the freestyle generator.
    if recommendation.get("target") == "fiori_elements":
        # Shared deploy/config files come from the freestyle generator's helpers
        # (mta.yaml, xs-app.json, xs-security.json, destinations, package.json,
        # ui5.yaml, .env). Generate them for the same spec, then keep only the
        # non-UI ones to accompany the FE UI files.
        deploy_files = deployment_files_for(spec)
        files = fe.build_fe_project(
            model, plan, recommendation,
            app_id=app_id, app_name=app_name,
            service_url=service_url.strip(), odata_version=version,
            extra_files=deploy_files,
        )
    else:
        files = generate_app_files(spec, ai_fn)

    # 6) Quality validation + safe auto-fixes over whatever was generated.
    known_props = {p["name"]
                   for es in model["entity_sets"] for p in es["properties"]}
    qa = qv.validate_and_autofix(files)
    files = qa["files"]

    return PipelineResult(model=model, plan=plan, spec=spec, files=files,
                          sample_profiles=sample_profiles,
                          recommendation=recommendation,
                          quality=qa["report_after"],
                          quality_fixes=qa["fixes_applied"])


def _build_plan_hints(plan: Dict, sample_profiles: Dict, model: Dict = None) -> Dict:
    """Assemble the plan_hints dict the generator consumes, and derive a
    status value->UI5 state map from sample data when a status field exists."""
    hints = {
        "status_field": plan.get("status_field", ""),
        "value_help_fields": plan.get("value_help_fields", []),
        "filter_fields": plan.get("filter_fields", []),
        "child_entity_set": plan.get("child_entity_set", ""),
        "app_type": plan.get("app_type", ""),
    }
    # Field labels from the service metadata (sap:label in V2, Common.Label in
    # V4) so the UI shows "Sold-To Party" instead of "SoldToParty". Keyed by
    # property name across all entity sets (header + items).
    if model:
        labels = {}
        for es in model.get("entity_sets", []):
            for p in es.get("properties", []):
                lbl = p.get("label")
                if lbl and lbl != p["name"]:
                    labels[p["name"]] = lbl
        if labels:
            hints["field_labels"] = labels
        # Entity-level label (e.g. "Sales Order") for the object-page subtitle.
        primary_name = plan.get("primary_entity_set", "")
        for es in model.get("entity_sets", []):
            if es["name"] == primary_name and es.get("label") \
                    and es["label"] != es["name"]:
                hints["entity_label"] = es["label"]
                break
    sf = hints["status_field"]
    if sf and sample_profiles:
        # Find the profile that contains the status field and map its values.
        for prof in sample_profiles.values():
            entry = prof.get(sf)
            if entry and entry.get("distinct"):
                mapping = {}
                for v in entry["distinct"]:
                    mapping[str(v)] = _guess_state(str(v))
                hints["status_value_states"] = mapping
                break
    return hints


def _guess_state(value: str) -> str:
    s = value.lower()
    import re as _re
    # Check negations / problem states FIRST — otherwise "Not Delivered" would
    # match the positive "deliver" keyword and wrongly turn green. A leading
    # "not"/"in"/"un" negation, or any explicit problem word, forces Error.
    if _re.search(r"(cancel|reject|error|fail|block|overdue|not\s|no\s|"
                  r"\bincomplete\b|\bunpaid\b|\bnot\b)", s):
        return "Error"
    # partial/in-progress states before Success, so "Partially Delivered" is a
    # warning rather than green from the "deliver" keyword.
    if _re.search(r"(partial|in\s?progress|pending|hold|await|draft|started)", s):
        return "Warning"
    if _re.search(r"(complete|done|approv|success|closed|paid|deliver|active|"
                  r"finish|invoiced)", s):
        return "Success"
    if _re.search(r"(open|new)", s):
        return "Warning"
    return "None"
