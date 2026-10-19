# Adopt existing projects — API contract

The studio adopts any existing project (CAP + Fiori, custom SAPUI5, React / Angular / Vue / Node.js /
TypeScript, Python) and then works on it like on a generated app: preview, robot changes with diff
review, validation, versions, approvals, deploy. All routes need the usual studio auth; long work runs
as a job (`{job_id}` → poll `GET /api/jobs/{id}`, result in `result`).

Projects created this way have `mode: "adopted"`. Their version snapshots carry `app_mode: "adopted"`,
`analysis` (below, without `questions`), `brief` (the user's answers) and `missions` (applied robot
missions, newest last).

## 1. Bring the project

| Route | Body | Result |
|---|---|---|
| `POST /api/adopt/upload` (multipart `file`) | ZIP | `{upload_id, name, bytes}` |
| `GET /api/cf/apps` | — | existing route: `{apps:[{name,state,…}], org, space}` |
| `POST /api/jobs/adopt-analyze` | `{source}` (below) | job → `Analysis` |

`source` is one of
`{kind:"zip", upload_id}` · `{kind:"git", url, branch?, token?}` · `{kind:"cf", app}`.
The token is used for the clone only and is not stored.

## 2. What we found + fill the gaps

`GET /api/adopt/analysis/{aid}` → `Analysis` (kept 24 h).

```ts
type Cap = "understand" | "preview" | "designers" | "ai_change" | "validate" | "deploy";
interface Analysis {
  id: string; name: string; primary: string;            // primary module kind
  source: { kind: "zip" | "git" | "cf"; label: string; url?: string; branch?: string; commit?: string; app?: string };
  files: number; bytes: number; skipped: { path: string; why: string }[];
  stack: string[];                                     // chips: "MTA", "CAP Node.js …", "Fiori Elements V4 1.120", …
  languages: Record<string, number>;                   // lines per language
  modules: {
    id: string; name: string; path: string; kind: string; framework: string; evidence: string;
    level: "full" | "code" | "config";                 // depth of studio support
    role: "data" | "service" | "ui" | "entry" | "other";
    caps: Record<Cap, "y" | "p" | "n">;                // y supported · p partly · n not applicable
    commands: { install?: string; build?: string; test?: string; run?: string };
  }[];
  arch: { id: string; label: string; sub: string; external?: boolean }[][];   // columns, left → right
  findings: { severity: "bad" | "warn" | "info"; text: string; fix: string }[];
  model: { source: string; editable: boolean;
           entities: { name: string; fields: { name: string; type: string; key: boolean; assoc: string }[] }[];
           services: { name: string; path?: string; entities?: any[]; actions?: string[] }[] };
  screens: { name: string; type: string; source: string; editing: "visual" | "ai"; module: string }[];
  endpoints: { method: string; path: string; source: string; module: string }[];
  destinations: { name: string; used_by: string; purpose: string; internal: boolean }[];
  env_vars: string[];
  understanding?: { summary?: string; business_objects?: string[]; roles?: string[]; processes?: string[];
                    integrations?: string[]; risks?: string[];
                    suggested_missions?: { title: string; request: string; why: string }[] };
  questions: Question[];                               // only on the analysis, not in snapshots
}
interface Question {
  id: string;            // purpose | entry | preview_data | dest:<name> | env | commands | run | protected | rules | deploy_target
  title: string; why: string; guess?: string;
  kind: "text" | "textarea" | "code" | "select" | "radio" | "checks";
  options?: { value: string; label: string; hint?: string; checked?: boolean }[];
  value: string | string[];                            // checks → string[]
}
```

| Route | Body | Result |
|---|---|---|
| `POST /api/adopt/{aid}/create` | `{name, answers: {[questionId]: value}}` | `{project_id, version_id}` |

## 3. Adopted project workspace

| Route | Body | Result |
|---|---|---|
| `GET /api/adopt/projects/{pid}` | — | `{project, version: VersionMeta, analysis, brief, missions: MissionSummary[], runner: {mode:"local"|"remote", node, python, ok}}` |
| `POST /api/adopt/projects/{pid}/brief` | `{answers}` | saves a new version "Brief updated" → `{version_id}` |
| `POST /api/jobs/adopt-reanalyze` | `{project_id}` | job → re-analyses the latest version (+ AI understanding) and saves it into the brief version |

### Robot (AI agent that changes the project)

```ts
interface Mission {
  id: string; project_id: string; version_id: string; author: string; created_at: number;
  request: string;
  status: "understanding" | "needs_input" | "planned" | "working" | "ready" | "failed" | "applied" | "discarded";
  restated: string;                                    // the robot's understanding of the request
  questions: { id: string; question: string; options?: string[]; default?: string }[];
  answers: Record<string, string>;
  criteria: string[];                                  // acceptance criteria
  plan: { step: string; files: string[] }[];
  changes: { path: string; status: "added" | "modified" | "deleted"; additions: number; deletions: number }[];
  diff: string;                                        // unified diff of all changes
  summary: string;
  checks: ValidationReport | null;                     // same shape as build validation
  criteria_results: { criterion: string; met: boolean; evidence: string }[];
  steps: { at: number; kind: "think" | "read" | "search" | "edit" | "check" | "ask" | "info"; text: string; ok: boolean }[];
  rounds: number;                                      // self-repair rounds used
  applied_version?: { id: string; number: number };
  error?: string;
}
```

| Route | Body | Result |
|---|---|---|
| `POST /api/jobs/robot-plan` | `{project_id, request}` | job → `Mission` (status `needs_input` or `planned`) |
| `POST /api/jobs/robot-run` | `{mission_id, answers?: {[qid]: string}}` | job → `Mission` (`ready` or `failed`); job messages stream the robot's steps |
| `POST /api/jobs/robot-revise` | `{mission_id, feedback}` | job → `Mission` (continues on the same change set) |
| `GET /api/adopt/missions/{mid}` | — | `Mission` |
| `GET /api/adopt/missions/{mid}/file?path=` | — | text of the changed file (404 when deleted) |
| `POST /api/adopt/missions/{mid}/apply` | `{label?}` | `{version_id, number}` — saves the change as a new version |
| `POST /api/adopt/missions/{mid}/discard` | — | `Mission` |
| `GET /api/adopt/projects/{pid}/missions` | — | `{missions: MissionSummary[]}` (id, request, status, created_at, author, changes count, applied_version) |
| `POST /api/jobs/robot-suggest` | `{project_id}` | job → `{suggestions: {title, request, why}[]}` |

### Preview, validation, deploy

| Route | Body | Result |
|---|---|---|
| `POST /api/jobs/adopt-preview` | `{version_id}` or `{mission_id}` | job → `{preview_id, url, kind, entry, log}`; `url` is relative (`api/adopt/preview/<id>/…`) for an iframe |
| `GET /api/adopt/preview/{id}/{path}` | — | proxied app |
| `POST /api/adopt/preview/{id}/stop` | — | `{stopped: true}` |
| `GET /api/adopt/preview/{id}/log` | — | text |
| `POST /api/jobs/validate` | `{version_id}` | existing route; for adopted versions it runs the adopted checks (commands from the brief) and stores the report on the version |
| `GET /api/adopt/deploy/{vid}` | — | `{targets: [{value,label,hint}], default, env_gate: {[env]: {allowed, reason}}}` |
| `POST /api/jobs/adopt-deploy` | `{version_id, env, target, git?: {token, base_branch?, branch?, title?}}` | job → `{status, url, log, pr_url?, mtar?}` |
