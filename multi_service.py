"""
multi_service.py — one app over several OData services.

A multi-service app (e.g. sales orders + purchase orders + employees) is designed and
rendered against ONE merged model: every entity set / operation carries the alias
of the service it comes from, and model["services"] lists the services. The first
service is the primary one (the app's default UI5 model); every other service gets a
named model (its alias).

  merge()        several parsed models -> merged model (same OData version required)
  sub_model()    the merged model reduced to one service (single-service validation:
                 a list page, its value helps, child tables and actions stay in its service)
  links          cross-service links {"from", "field", "to", "to_field", "label"}:
                 from.field holds a value of to.to_field (e.g. A_PurchaseOrderItem.Material
                 -> A_Product.Product). Detail pages of "from" link to the target record,
                 detail pages of "to" list the related "from" records.
"""

from __future__ import annotations
import copy
import re
from typing import Dict, List, Optional

# SAP field names that refer to the same business object under different names
SYNONYMS = {
    "Customer": ["SoldToParty", "Customer", "PayerParty", "ShipToParty", "BillToParty"],
    "Supplier": ["Supplier", "InvoicingParty", "Vendor"],
    "BusinessPartner": ["BusinessPartner", "SoldToParty", "Customer", "Supplier"],
    "Product": ["Product", "Material"],
    "Material": ["Material", "Product"],
    "PersonnelNumber": ["PersonnelNumber", "Employee", "EmployeeID", "PersonWorkAgreement"],
    "PersonWorkAgreement": ["PersonWorkAgreement", "PersonnelNumber", "Employee"],
    "Employee": ["Employee", "EmployeeID", "PersonnelNumber"],
    "EmployeeID": ["EmployeeID", "Employee", "PersonnelNumber"],
    "Plant": ["Plant"],
    "CompanyCode": ["CompanyCode"],
    "SalesOrder": ["SalesOrder", "ReferenceSDDocument"],
    "PurchaseOrder": ["PurchaseOrder"],
    "CostCenter": ["CostCenter"],
}
# key names too generic to link on by name alone
_GENERIC = re.compile(r"^(id|key|guid|uuid|language|client|mandt|counter|item|position|"
                      r"\w*item|\w*uuid|\w*counter|lfdnr)$", re.I)
_SKIP_SEG = re.compile(r"^(sap|opu|odata4?|srvd(_a2x)?|default|v\d+|\d+|iwbep|tea|zz?)$", re.I)


def alias_for(service_url: str, used: set) -> str:
    """Short model name for a service: /sap/opu/odata/sap/API_SALES_ORDER_SRV/ -> salesorder."""
    segs = [s for s in re.split(r"[/;=]", service_url or "") if s]
    seg = next((s for s in reversed(segs) if not _SKIP_SEG.match(s)), "svc")
    name = re.sub(r"(?i)^(c_|i_|api_|zapi_|z_|y_)", "", seg)
    name = re.sub(r"(?i)(_srv|_cds|_api|_0001|_\d+)$", "", name)
    name = re.sub(r"[^a-z0-9]", "", name.lower())[:20] or "svc"
    if name[0].isdigit():
        name = "s" + name
    base, i = name, 2
    while name in used:
        name = f"{base}{i}"
        i += 1
    used.add(name)
    return name


def merge(services: List[Dict], load) -> Dict:
    """services: [{"alias"?, "title"?, "edmx", "service_url"}] (first = primary);
    load(edmx) -> parsed + annotated model. Returns the merged model."""
    if not services:
        raise ValueError("No service given.")
    used: set = set()
    merged: Optional[Dict] = None
    notes: List[str] = []
    seen_sets: Dict[str, str] = {}
    for i, s in enumerate(services):
        m = load(s["edmx"])
        alias = s.get("alias") or alias_for(s.get("service_url", ""), used)
        used.add(alias)
        title = s.get("title") or s.get("service_url", "").strip("/").split("/")[-1] or alias
        info = {"alias": alias, "title": title, "service_url": s.get("service_url", ""),
                "odata_version": m["odata_version"], "namespace": m.get("namespace", ""),
                "container": m.get("container", ""),
                "container_namespace": m.get("container_namespace", m.get("namespace", "")),
                "primary": i == 0, "sets": []}
        if merged is None:
            merged = copy.deepcopy(m)
            merged["entity_sets"], merged["operations"], merged["services"] = [], [], []
        elif m["odata_version"][0] != merged["odata_version"][0]:
            raise ValueError(
                f"{title} is an OData V{m['odata_version'][0]} service, {merged['services'][0]['title']} is "
                f"V{merged['odata_version'][0]}. One app can combine services of the same OData version "
                f"only — pick the V{merged['odata_version'][0]} variant of {title}.")
        for es in m["entity_sets"]:
            if es["name"] in seen_sets:
                notes.append(f"{title}: entity set {es['name']} also exists in {seen_sets[es['name']]} — "
                             f"the one of {seen_sets[es['name']]} is used.")
                continue
            seen_sets[es["name"]] = title
            merged["entity_sets"].append(dict(es, service=alias, namespace=info["namespace"]))
            info["sets"].append(es["name"])
        for op in m.get("operations", []):
            merged["operations"].append(dict(op, service=alias))
        merged["services"].append(info)
    merged["merge_notes"] = notes
    return merged


def is_multi(model: Dict) -> bool:
    return len(model.get("services") or []) > 1


def services(model: Dict) -> List[Dict]:
    return model.get("services") or []


def primary_alias(model: Dict) -> str:
    s = services(model)
    return s[0]["alias"] if s else ""


def service_of(model: Dict, es_name: str) -> str:
    es = next((e for e in model["entity_sets"] if e["name"] == es_name), None)
    return (es or {}).get("service", "")


def service_info(model: Dict, alias: str) -> Dict:
    return next((s for s in services(model) if s["alias"] == alias), {})


def sub_model(model: Dict, alias: str) -> Dict:
    """The merged model restricted to one service (unchanged for single-service models)."""
    if not is_multi(model) or not alias:
        return model
    info = service_info(model, alias)
    return dict(model, namespace=info.get("namespace", model.get("namespace")),
                container=info.get("container", model.get("container")),
                container_namespace=info.get("container_namespace", model.get("container_namespace")),
                entity_sets=[e for e in model["entity_sets"] if e.get("service") == alias],
                operations=[o for o in model.get("operations", []) if o.get("service") == alias],
                services=[info])


def model_for_set(model: Dict, es_name: str) -> Dict:
    return sub_model(model, service_of(model, es_name))


# ─────────────────────────────────────────────────────────────────────────────
# Cross-service links
# ─────────────────────────────────────────────────────────────────────────────
def _props(es: Dict) -> Dict[str, Dict]:
    return {p["name"]: p for p in es["properties"]}


def _compatible(a: Dict, b: Dict) -> bool:
    ta, tb = a.get("type", "Edm.String"), b.get("type", "Edm.String")
    if ta != tb:
        return False
    if ta == "Edm.String" and a.get("max_length") and b.get("max_length"):
        return abs(int(a["max_length"]) - int(b["max_length"])) <= 30
    return True


def auto_links(model: Dict, page_sets: List[str]) -> List[Dict]:
    """Heuristic links between entity sets of DIFFERENT services that have pages:
    a field of A that names the single key of B (same name or a known SAP synonym)."""
    sets = {e["name"]: e for e in model["entity_sets"]}
    cand = [sets[s] for s in dict.fromkeys(page_sets) if s in sets]
    out, seen = [], set()
    for to in cand:
        if len(to["keys"]) != 1:
            continue
        key = to["keys"][0]
        if _GENERIC.match(key) or len(key) < 4:
            continue
        kp = _props(to).get(key, {})
        names = SYNONYMS.get(key, [key])
        for frm in cand:
            if frm["name"] == to["name"] or frm.get("service") == to.get("service"):
                continue
            fp = _props(frm)
            for n in names:
                if n in fp and _compatible(fp[n], kp) and (frm["name"], n, to["name"]) not in seen:
                    seen.add((frm["name"], n, to["name"]))
                    out.append({"from": frm["name"], "field": n, "to": to["name"], "to_field": key,
                                "label": to.get("label") or to["name"]})
                    break
    return out[:12]


def validate_links(raw, model: Dict, notes: List[str]) -> List[Dict]:
    sets = {e["name"]: e for e in model["entity_sets"]}
    out, seen = [], set()
    for l in raw or []:
        if not isinstance(l, dict):
            continue
        frm, to = sets.get(l.get("from")), sets.get(l.get("to"))
        if not (frm and to) or frm["name"] == to["name"]:
            notes.append(f"Link {l.get('from')} -> {l.get('to')} dropped — unknown entity set.")
            continue
        field = l.get("field")
        to_field = l.get("to_field") or (to["keys"][0] if len(to["keys"]) == 1 else "")
        fp, tp = _props(frm), _props(to)
        if field not in fp or to_field not in tp:
            notes.append(f"Link {frm['name']}.{field} -> {to['name']}.{to_field} dropped — unknown field.")
            continue
        if fp[field].get("type", "Edm.String") != tp[to_field].get("type", "Edm.String"):
            notes.append(f"Link {frm['name']}.{field} -> {to['name']}.{to_field} dropped — the field types "
                         f"differ ({fp[field].get('type')} vs {tp[to_field].get('type')}).")
            continue
        k = (frm["name"], field, to["name"])
        if k in seen:
            continue
        seen.add(k)
        out.append({"from": frm["name"], "field": field, "to": to["name"], "to_field": to_field,
                    "label": str(l.get("label") or to.get("label") or to["name"])[:40]})
    return out[:16]


# ─────────────────────────────────────────────────────────────────────────────
# AI prompt support
# ─────────────────────────────────────────────────────────────────────────────
def catalog_header(model: Dict) -> str:
    if not is_multi(model):
        return ""
    lines = [f"THIS APP COMBINES {len(services(model))} ODATA SERVICES (one app, several areas):"]
    for s in services(model):
        lines.append(f"  - service '{s['alias']}' = {s['title']} ({len(s['sets'])} entity sets"
                     f"{', primary' if s['primary'] else ''})")
    return "\n".join(lines)


def tag(model: Dict, es: Dict) -> str:
    """' {service X}' after an entity set in AI context (multi-service models only)."""
    return f" {{service {es.get('service')}}}" if is_multi(model) and es.get("service") else ""


def prompt_rules(model: Dict) -> str:
    if not is_multi(model):
        return ""
    sugg = auto_links(model, [e["name"] for e in model["entity_sets"]])
    hint = ("; ".join(f"{l['from']}.{l['field']} -> {l['to']}.{l['to_field']}" for l in sugg[:8])
            or "none found by name")
    return (
        "\nMULTI-SERVICE APP RULES:\n"
        "- Use floorplan FreestyleApp. Build ONE integrated app: give every service its own area — at "
        "least one 'list' page (with detail) for the main business object of EACH service the requirement "
        "mentions — plus a home 'dashboard' first whose KPIs/charts/lists span the services.\n"
        "- A list/kanban/calendar page works on ONE entity set; its child tables, value helps and actions "
        "must come from that same service. Dashboard tiles and analytics charts may mix services.\n"
        "- Interlink the areas with top-level \"links\": [{\"from\": entity set, \"field\": its field, "
        "\"to\": entity set of another page, \"to_field\": the matching (key) field, \"label\": str}] — "
        "the detail page of 'from' then links to the 'to' record, and the detail page of 'to' lists the "
        "related 'from' records. Only link fields that hold the same business key (same type). "
        f"Candidates found by name: {hint}.\n"
    )
