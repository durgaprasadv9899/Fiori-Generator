# Studio API v2 — contract between `studio_api.py` and `studio-web/`

All JSON. Errors: HTTP 4xx/5xx with `{"detail": "message"}`. Auth: XSUAA token added by the
approuter on BTP; open local mode otherwise (UI never handles tokens). Long work = jobs:
`POST api/jobs/<kind>` → `{job_id}`; poll `GET api/jobs/{id}` →
`{id, kind, label, status: running|done|error, seconds, messages[], result|null, error}`.
`GET api/jobs` → `{jobs: [...]}` running jobs of the user (re-attach after reload).

"Sources" (what an app is built on) are passed by the client in every design/preview/refine/
generate call — the server is stateless between calls:
```
source = {"mode": "standalone", "domain": {...}, "db": "sqlite"|"hana"}
       | {"mode": "sap", "services": [{"edmx": "...", "service_url": "/sap/opu/odata/...", "title": "..."}],
          "destination": {"use_existing": bool, "name": "EvokeSD4", "on_premise": bool, "auth_type": "NoAuthentication"}}
```
(first service = main; more services = multi-service app; all must have the same OData version)

## Existing (v1, unchanged)
- `GET api/me` → `{user:{id,name,roles,source}, ai:{available,model,error}, store:{kind,durable,note}, permissions:[...]}`
- `GET api/examples` → `{new_app:{label:text}, sap:{label:text}}`
- `GET api/projects` → `{projects:[{id,name,mode,owner,app_id,description,updated_at,latest_version,archived}]}`
- `GET api/projects/{pid}` → `{project:{...,members:[{user,role}]}, versions:[{id,number,label,author,created_at,file_count,validation:{summary,blocking}|null}], deployments:[{env,status,app_url,user,at,version_number}], role}`
- `GET api/versions/{vid}` → `{version:{...}, snapshot:{requirement, source, blueprint, design:{spec,coverage}, app_id}, files:[paths]}`
- `GET api/versions/{vid}/file?path=` → text ; `GET api/versions/{vid}/zip` → zip
- jobs: `questions {requirement}` → `{questions:[{id,question,options,why}]}`;
  `domain {requirement, answers}` → `{domain, notes, source, summary}`; `POST api/domain/validate {domain}`
- `validate {version_id, level}` → build_validation report

## New in v2
### Dashboard / notifications / environments
- `GET api/dashboard` → `{kpis:{projects, versions, deployments, deploy_success_rate (0-100|null), validated_versions, pending_approvals, apps_live}, activity:[{at,user,action,project_id,project_name,detail}], recent_projects:[project], running_jobs:int, by_mode:{sap:n, standalone:n, import:n}, versions_per_day:[{day:"YYYY-MM-DD", count}] (last 14 days)}`
- `GET api/notifications` → `{items:[{id, kind: approval_pending|approval_decided|deploy|job, title, text, at, link (hash route e.g. "/approvals")}], unread:int}`
- `GET api/environments` → `{envs:{dev:{label,api,org,space,requires_approval,requires_validation,configured}, test:{...}, prod:{...}}}`

### SAP services
- `GET api/sap/runtime` → `{env:"connectivity"|"bas"|"local", description, cf_target:{org,space}|null}`
- `GET api/sap/destinations` → `{destinations:[{name,url,proxy_type,authentication,reachable,sap_client}], source}`
- `POST api/sap/services {destination}` → `{services:[{path,name,title,odata_version}], warnings:[...]}`
- `POST api/sap/metadata {destination?, path?, edmx?, service_url}` (fetch through a destination, or paste) →
  `{edmx, service_url, summary:{odata_version, entity_sets:[{name,label,keys,field_count,fields:[{name,label,type}],creatable,updatable,deletable,navigations:[{name,target,to_many}]}], operations:[{name,kind,bound_type,label}]}}`
- `POST api/sap/check-services {services:[...]}` → `{ok, error, merged_summary}` (multi-service compatibility)

### Design / refine / preview / generate (both modes)
- `POST api/jobs/design {requirement, source, floorplan, style?}` → result
  `{blueprint, notes, source:"ai"|"rules", spec|null, coverage:[{feature,status,detail}], coverage_score, steps:[{step,ok,seconds,detail}], service_url, structure, pages:[{id,type,title,entity_set}] }`
- `POST api/jobs/refine {instruction, requirement, blueprint, source, spec}` → same shape as design result
- `POST api/preview {blueprint, source}` → text/html (mock-data interactive preview)
- `GET api/floorplans` → `{floorplans:{key:[family, description]}}`
- `POST api/jobs/generate {requirement, source, blueprint, app_id, project_id|null, design:{spec,coverage}, theme?}` →
  `{project_id, version_id, version_number, file_count, files, quality:{errors,warnings,findings}, notes}`

### Projects
- `POST api/projects/{pid}/rename {name}` · `POST api/projects/{pid}/archive {archived}` · `POST api/projects/{pid}/duplicate {name}` → project
- `POST api/projects/{pid}/members {user, role}`
- `GET api/projects/{pid}/export` → zip · `POST api/projects/import` (multipart file) → project
- `GET api/compare?a=<vid>&b=<vid>` → `{a, b, added, removed, changed, diff, blueprint_changes}`

### Approvals / audit
- `GET api/approvals?status=pending|approved|rejected|all` → `{approvals:[{id,project_id,project_name,version_id,version_number,env,requester,note,status,requested_at,decided_by,decided_at,comment,can_decide, validation:{summary,blocking}|null}]}`
- `POST api/approvals {project_id, version_id, env, note}` → approval
- `POST api/approvals/{id}/decide {approve, comment}` → approval
- `GET api/audit?project_id=&limit=` → `{events:[{at,user,action,project_id,project_name,detail}]}`

### Deploy (Cloud Foundry, per-user CF session)
- `GET api/cf/session` → `{signed_in, user, api, org, space, passcode_url, cli_available}`
- `POST api/cf/login {passcode}` or `{user, password, origin}` → session · `POST api/cf/logout`
- `GET api/deploy/check?version_id=&env=` → `{ok, reasons:[...], approval|null, needs:{destinations:[{id,dest_name,auth_type,use_existing,url}]}, method_choices:[...], target:{api,org,space}}`
- `POST api/jobs/deploy {version_id, env, dest_creds:{id:{url,user,password,token_url,client_id,client_secret}}, method?}` →
  messages = live log lines; result `{status: succeeded|failed|skipped, url, log, app_id}` (recorded as a deployment)
- `GET api/cf/logs?app=` → `{ok, text}`

## Implementation notes (as built — `studio_api.py`, `api_sap.py`, `api_deploy.py`)
- `GET api/me` also returns `auth_mode` (xsuaa|local|shared|open). `permissions` = global permissions of the user.
- `GET api/projects/{pid}` also returns `role` (owner|developer|reviewer|viewer|"") and `permissions` (on that project);
  deployments rows include `id` and `version_id`.
- `GET api/versions/{vid}`: `version.validation` = `{summary, blocking}|null`, plus `validation_report` (full report|null).
  `snapshot.source` is rebuilt from the stored studio-format snapshot, so versions made in the classic studio open too.
- `POST api/projects/{pid}/members {user, role, remove?: true}` (role: viewer|developer|reviewer).
- `GET api/sap/runtime` → `env` is `local` | `bas` | `connectivity`. Destination instance (when not bound on CF):
  env `STUDIO_DESTINATION_INSTANCE`, else `fiori-generator-destination`, else `evokesd4-destination-service`; never
  an arbitrary instance. `POST api/sap/services` / `metadata` errors (unreachable, not OData, bad XML) → 400 with detail.
- `POST api/jobs/refine` needs AI → 400 "Refining the design needs an AI model" without it. Coverage is re-assessed when `spec` is given.
- `POST api/jobs/generate`: still accepts the v1 body (`domain`, `db`); `app_id` must be dotted (`com.company.app`) → 400;
  project_id given → needs `version.save` on it (403), else `project.create`.
- `GET api/deploy/check` also returns `env` (config) and `app_id`; `needs.destinations[]` items:
  `{id, dest_name, auth_type, use_existing, on_premise, url, fields:[required credential fields]}` (empty `fields` for
  existing destinations). Quality-gate errors and a missing mta.yaml are part of `reasons`.
- `POST api/jobs/deploy` → 409 when the release gate blocks (reasons joined) or the user has no CF session;
  400 when destination fields are missing. The finished deployment is recorded (`record_deployment`) unless skipped.
- `GET api/cf/logs?app=<cf app name>` → `{ok, text}`.
- CF session: on BTP (VCAP_APPLICATION set) every studio user gets an own CF_HOME (`<data dir>/cfhome/<sha1(user)[:16]>`);
  locally the developer's own `cf login` is used.
