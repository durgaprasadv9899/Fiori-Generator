"""
ui_preview.py — interactive wireframe of a designed blueprint (shown in the
generator before any code is generated): side navigation, every page type,
detail pages, with the real field labels from the service metadata.
"""

from __future__ import annotations

import html
import json
from typing import Dict, List

_CSS = """
*{box-sizing:border-box} body{margin:0;font-family:'72','Inter',Arial,sans-serif;font-size:13px;color:#1d2d3e;background:#f5f6f7}
.app{display:flex;flex-direction:column;height:100vh}
.shell{height:44px;background:#fff;border-bottom:1px solid #e5e5e5;display:flex;align-items:center;gap:10px;padding:0 14px;flex:0 0 auto}
.shell .burger{width:18px;height:12px;border-top:2px solid #556b82;border-bottom:2px solid #556b82;position:relative}
.shell .burger:after{content:"";position:absolute;left:0;right:0;top:3px;border-top:2px solid #556b82}
.shell b{font-size:14px;color:#0a6ed1}
.body{display:flex;flex:1 1 auto;min-height:0}
.nav{width:200px;background:#fff;border-right:1px solid #e5e5e5;padding:6px 0;flex:0 0 auto;overflow:auto}
.nav div{padding:8px 14px;cursor:pointer;color:#32363a;display:flex;gap:8px;align-items:center;border-left:3px solid transparent;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.nav div:hover{background:#f0f6fd}
.nav div.on{background:#eaf3fc;border-left-color:#0a6ed1;color:#0a6ed1;font-weight:600}
.nav i{font-style:normal;width:16px;text-align:center;opacity:.75}
.main{flex:1 1 auto;overflow:auto;padding:0}
.page{display:none}.page.on{display:block}
.ph{background:#fff;padding:12px 18px 10px;border-bottom:1px solid #e5e5e5}
.ph h2{margin:0;font-size:18px;font-weight:700}
.crumb{font-size:11px;color:#0a6ed1;margin-bottom:4px;cursor:pointer}
.fb{display:flex;flex-wrap:wrap;gap:10px;margin-top:10px;align-items:flex-end}
.fld{display:flex;flex-direction:column;gap:3px}.fld span{font-size:11px;color:#556b82}
.fld div{width:150px;height:26px;border:1px solid #bfbfbf;border-radius:4px;background:#fff}
.btn{display:inline-block;padding:4px 10px;border-radius:6px;border:1px solid #0a6ed1;color:#0a6ed1;background:#fff;font-size:12px;font-weight:600;white-space:nowrap}
.btn.e{background:#0a6ed1;color:#fff}.btn.g{border-color:transparent;color:#556b82}
.ct{margin:12px 14px;background:#fff;border-radius:10px;box-shadow:0 0 0 1px #e5e5e5}
.tb{display:flex;align-items:center;gap:8px;padding:10px 12px;border-bottom:1px solid #eee;flex-wrap:wrap}
.tb .t{font-weight:700;margin-right:auto}
.srch{width:150px;height:24px;border:1px solid #bfbfbf;border-radius:12px}
table{width:100%;border-collapse:collapse}
th{font-size:11px;text-align:left;color:#556b82;font-weight:600;padding:7px 10px;border-bottom:1px solid #eee;white-space:nowrap}
td{padding:9px 10px;border-bottom:1px solid #f2f2f2}
td .sk{height:8px;border-radius:4px;background:#e8ecf1;width:70%}
tr.row{cursor:pointer}tr.row:hover td{background:#f5faff}
th.f{color:#7a4fd6}th.f:before{content:"ƒ ";font-style:italic}
.hl{border-left:4px solid #bb0000}
.tabs{display:flex;gap:16px;padding:8px 12px 0;border-bottom:1px solid #eee}
.tabs span{padding-bottom:7px;color:#556b82;font-weight:600}.tabs span.on{color:#0a6ed1;border-bottom:2px solid #0a6ed1}
.tabs em{font-style:normal;background:#eef1f4;border-radius:8px;padding:0 6px;margin-left:4px;font-size:11px}
.kpis{display:flex;flex-wrap:wrap;gap:10px;padding:12px 14px 0}
.kpi{width:160px;height:96px;background:#fff;border-radius:10px;box-shadow:0 0 0 1px #e5e5e5;padding:10px 12px;display:flex;flex-direction:column;justify-content:space-between}
.kpi span{font-size:12px;font-weight:600}.kpi b{font-size:26px;font-weight:300;color:#556b82}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:12px;padding:12px 14px}
.card{background:#fff;border-radius:10px;box-shadow:0 0 0 1px #e5e5e5;padding:10px 12px;min-height:150px}
.card h4{margin:0 0 8px;font-size:13px}.card small{color:#7a4fd6}
.bars{display:flex;align-items:flex-end;gap:6px;height:100px;padding-top:6px}
.bars div{flex:1;background:#5899da;border-radius:3px 3px 0 0}
.hbars div{height:12px;background:#5899da;border-radius:0 3px 3px 0;margin:6px 0}
.donut{width:100px;height:100px;border-radius:50%;background:conic-gradient(#5899da 0 45%,#e8743b 45% 70%,#19a979 70% 88%,#ed4a7b 88%);position:relative;margin:4px auto}
.donut:after{content:"";position:absolute;inset:26px;background:#fff;border-radius:50%}
svg.line{width:100%;height:100px}
.board{display:flex;gap:10px;padding:12px 14px;overflow:auto}
.lane{width:210px;flex:0 0 auto;background:#eef1f5;border-radius:10px;padding:8px}
.lane h5{margin:2px 4px 8px;font-size:12px;display:flex;justify-content:space-between}
.kc{background:#fff;border-radius:8px;padding:8px 10px;margin-bottom:8px;box-shadow:0 1px 2px rgba(0,0,0,.08);cursor:pointer}
.kc b{display:block;font-size:12px}.kc span{display:block;font-size:11px;color:#556b82;margin-top:3px}
.cal{display:grid;grid-template-columns:repeat(7,1fr);gap:1px;background:#e5e5e5;margin:12px 14px;border-radius:8px;overflow:hidden}
.cal div{background:#fff;min-height:62px;padding:4px 6px;font-size:11px;color:#556b82}
.cal div.h{min-height:auto;font-weight:600;background:#f7f7f7;text-align:center}
.ap{display:block;margin-top:3px;background:#e1f0fe;border-left:3px solid #0a6ed1;color:#1d2d3e;border-radius:3px;padding:1px 4px;font-size:10px;white-space:nowrap;overflow:hidden}
.split{display:flex;gap:0;margin:12px 14px;background:#fff;border-radius:10px;box-shadow:0 0 0 1px #e5e5e5;min-height:360px}
.split .l{width:260px;border-right:1px solid #eee}.split .r{flex:1;padding:12px 16px}
.li{padding:10px 12px;border-bottom:1px solid #f2f2f2}.li.on{background:#eaf3fc;border-left:3px solid #0a6ed1}
.li b{display:block;font-size:12px}.li span{font-size:11px;color:#556b82}
.sec{margin:12px 14px;background:#fff;border-radius:10px;box-shadow:0 0 0 1px #e5e5e5;padding:12px 16px}
.sec h4{margin:0 0 10px;font-size:14px}
.form{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:10px 18px}
.form div span{display:block;font-size:11px;color:#556b82}.form div i{display:block;height:8px;width:60%;margin-top:6px;border-radius:4px;background:#e8ecf1}
.hdr{display:flex;gap:28px;margin-top:10px;flex-wrap:wrap}.hdr span{display:block;font-size:11px;color:#556b82}.hdr b{font-size:13px}
.acts{display:flex;gap:6px;flex-wrap:wrap;margin-left:auto}
.wiz{display:flex;gap:6px;margin:12px 14px}.wiz span{flex:1;text-align:center;padding:8px;background:#fff;border-radius:8px;box-shadow:0 0 0 1px #e5e5e5;font-size:12px}
.wiz span.on{box-shadow:0 0 0 2px #0a6ed1;color:#0a6ed1;font-weight:600}
.note{font-size:11px;color:#7a4fd6;margin:6px 14px}
"""

_JS = """
function go(id){document.querySelectorAll('.page').forEach(p=>p.classList.toggle('on',p.id==='p_'+id));
document.querySelectorAll('.nav div').forEach(n=>n.classList.toggle('on',n.dataset.nav===(document.getElementById('p_'+id)||{}).dataset.nav));}
"""

_ICONS = {"dashboard": "⌂", "list": "☰", "kanban": "▥", "calendar": "▦", "analytics": "◔"}


def _e(s) -> str:
    return html.escape(str(s or ""))


class _Labels:
    def __init__(self, model: Dict):
        self.sets = {es["name"]: es for es in model["entity_sets"]}

    def __call__(self, es: str, f: str) -> str:
        e = self.sets.get(es) or {}
        p = next((p for p in e.get("properties", []) if p["name"] == f), None)
        return (p or {}).get("label") or f

    def set_label(self, es: str) -> str:
        return (self.sets.get(es) or {}).get("label") or es


def _rows(n: int, cols: int, detail: str, hl: bool) -> str:
    out = ""
    for i in range(n):
        cls = ' class="row hl"' if hl and i == 1 else ' class="row"'
        click = f' onclick="go(\'{detail}\')"' if detail else ""
        out += f"<tr{cls}{click}>" + "".join(f'<td><div class="sk" style="width:{50 + (i * 13 + j * 17) % 40}%"></div></td>'
                                           for j in range(cols)) + "</tr>"
    return out


def _list_page(pid: str, title: str, sub: Dict, lab: _Labels, detail: str, quick_tabs: bool,
               layout: str, nav: str, wizard: List[Dict]) -> str:
    m = sub["main"]
    es = m["entity_set"]
    caps = sub["capabilities"]
    logic = sub.get("logic") or {}
    filt = "".join(f'<div class="fld"><span>{_e(lab(es, f["field"]))}</span><div></div></div>' for f in m["filters"])
    acts = ""
    if caps.get("create"):
        acts += '<span class="btn e">Create</span>'
    acts += "".join(f'<span class="btn">{_e(a["label"])}</span>' for a in sub["actions"] if a["scope"] == "row")
    cols = [lab(es, c["field"]) for c in m["columns"]]
    comp = [c["label"] for c in logic.get("computed") or []]
    head = "".join(f"<th>{_e(c)}</th>" for c in cols) + "".join(f'<th class="f">{_e(c)}</th>' for c in comp)
    tabs = ""
    if quick_tabs and m["status"]["field"]:
        tabs = ('<div class="tabs"><span class="on">All<em>…</em></span>'
                + "".join(f"<span>{_e(v or '(blank)')}<em>…</em></span>" for v in m["status"]["states"]) + "</div>")
    page_attr = f'id="p_{pid}" data-nav="{nav}"'
    if layout == "split":
        items = "".join(f'<div class="li{" on" if i == 0 else ""}"><b>{_e(lab(es, m["title_field"]))} {i + 1}</b>'
                        f'<span>{_e(cols[1] if len(cols) > 1 else "")}</span></div>' for i in range(6))
        secs = "".join(f'<h4 style="margin:14px 0 8px">{_e(s["title"])}</h4><div class="form">'
                       + "".join(f'<div><span>{_e(lab(es, f))}</span><i></i></div>' for f in s["fields"][:9]) + "</div>"
                       for s in sub["object_page"]["sections"][:3])
        obj_acts = "".join(f'<span class="btn">{_e(a["label"])}</span>' for a in sub["actions"] if a["scope"] != "row")
        edit_btn = '<span class="btn e">Edit</span>' if caps.get("update") else ""
        return (f'<div class="page" {page_attr}><div class="ph"><h2>{_e(title)}</h2></div>'
                f'<div class="split"><div class="l"><div class="tb"><div class="srch"></div></div>{items}</div>'
                f'<div class="r"><div style="display:flex;align-items:center"><h3 style="margin:0">{_e(lab(es, m["title_field"]))} 1</h3>'
                f'<div class="acts">{edit_btn}{obj_acts}</div></div>{secs}</div></div></div>')
    note = ""
    if logic.get("row_state"):
        note = '<div class="note">Rows are highlighted by a custom rule (red bar).</div>'
    wiz_note = ""
    if wizard:
        wiz_note = f'<div class="note">Create opens a guided wizard: {" → ".join(_e(s["title"]) for s in wizard)} → Review.</div>'
    return (f'<div class="page" {page_attr}><div class="ph"><h2>{_e(title)}</h2><div class="fb">{filt}'
            f'<span class="btn e">Go</span></div></div>'
            f'<div class="ct">{tabs}<div class="tb"><span class="t">{_e(lab.set_label(es))} (…)</span><div class="srch"></div>{acts}'
            f'<span class="btn g">⇅</span><span class="btn g">⤓</span></div>'
            f'<table><tr>{head}</tr>{_rows(6, len(cols) + len(comp), detail, bool(logic.get("row_state")))}</table></div>{note}{wiz_note}</div>')


def _detail_page(did: str, title: str, sub: Dict, lab: _Labels, back: str, nav: str) -> str:
    m = sub["main"]
    es = m["entity_set"]
    caps = sub["capabilities"]
    hdr = "".join(f'<div><span>{_e(lab(es, f))}</span><b>…</b></div>' for f in sub["object_page"]["header_fields"])
    hdr += "".join(f'<div><span>ƒ {_e(c["label"])}</span><b>…</b></div>' for c in (sub.get("logic") or {}).get("computed") or [])
    acts = ("<span class=\"btn e\">Edit</span>" if caps.get("update") else "") + "".join(
        f'<span class="btn">{_e(a["label"])}</span>' for a in sub["actions"] if a["scope"] != "row") + (
        '<span class="btn g">Delete</span>' if caps.get("delete") else "")
    secs = "".join(f'<div class="sec"><h4>{_e(s["title"])}</h4><div class="form">'
                   + "".join(f'<div><span>{_e(lab(es, f))}</span><i></i></div>' for f in s["fields"]) + "</div></div>"
                   for s in sub["object_page"]["sections"])
    secs += "".join(f'<div class="sec"><h4>{_e(t["title"])}</h4><table><tr>'
                    + "".join(f"<th>{_e(lab(t['entity_set'], c))}</th>" for c in t["columns"]) + "</tr>"
                    + _rows(2, len(t["columns"]), "", False) + "</table></div>" for t in sub["object_page"]["tables"])
    return (f'<div class="page" id="p_{did}" data-nav="{nav}"><div class="ph"><div class="crumb" onclick="go(\'{back}\')">‹ {_e(title)}</div>'
            f'<div style="display:flex;align-items:center"><h2>{_e(lab(es, m["title_field"]))} …</h2><div class="acts">{acts}</div></div>'
            f'<div class="hdr">{hdr}</div></div>{secs}</div>')


def _chart(c: Dict) -> str:
    t = c.get("type", "column")
    if t in ("pie", "donut"):
        return '<div class="donut"></div>'
    if t in ("line", "area"):
        return ('<svg class="line" viewBox="0 0 300 100" preserveAspectRatio="none"><polyline fill="'
                + ("#cfe3f6" if t == "area" else "none") + '" stroke="#5899da" stroke-width="2.5" '
                'points="0,80 40,62 80,70 120,42 160,50 200,28 240,36 300,14"/></svg>')
    if t in ("bar", "stacked_bar"):
        return '<div class="hbars">' + "".join(f'<div style="width:{w}%"></div>' for w in (88, 72, 61, 44, 30)) + "</div>"
    return '<div class="bars">' + "".join(f'<div style="height:{h}%"></div>' for h in (70, 92, 55, 80, 40, 66, 35)) + "</div>"


def wireframe_html(bp: Dict, model: Dict) -> str:
    lab = _Labels(model)
    pages_html, nav_html = [], []
    first = None
    if bp.get("pages"):
        lists = {p["entity_set"]: p for p in bp["pages"] if p["type"] == "list"}
        for p in bp["pages"]:
            pid = p["id"]
            first = first or pid
            nav_html.append(f'<div data-nav="{pid}" onclick="go(\'{pid}\')"><i>{_ICONS.get(p["type"], "•")}</i>{_e(p["title"])}</div>')
            if p["type"] == "list":
                pages_html.append(_list_page(pid, p["title"], p["sub"], lab, p["detail_id"], p.get("quick_tabs"),
                                             p.get("layout"), pid, p["wizard_steps"] if p["create"] == "wizard" else []))
                if p["detail_id"]:
                    pages_html.append(_detail_page(p["detail_id"], p["title"], p["sub"], lab, pid, pid))
            elif p["type"] == "dashboard":
                kp = "".join(f'<div class="kpi"><span>{_e(k["label"])}</span><b>…</b></div>' for k in p["kpis"])
                cards = "".join(f'<div class="card"><h4>{_e(c["title"])}</h4>{_chart(c)}</div>' for c in p["charts"])
                cards += "".join(f'<div class="card"><h4>{_e(l["title"])}</h4><table><tr>'
                                 + "".join(f"<th>{_e(lab(l['entity_set'], f))}</th>" for f in l["fields"]) + "</tr>"
                                 + _rows(3, len(l["fields"]), "", False) + "</table></div>" for l in p["lists"])
                pages_html.append(f'<div class="page" id="p_{pid}" data-nav="{pid}"><div class="ph"><h2>{_e(p["title"])}</h2></div>'
                                  f'<div class="kpis">{kp}</div><div class="grid">{cards}</div></div>')
            elif p["type"] == "kanban":
                lanes = p["lanes"] or [{"title": "…"}, {"title": "…"}, {"title": "…"}]
                det = (lists.get(p["entity_set"]) or {}).get("detail_id", "")
                click = f' onclick="go(\'{det}\')"' if det else ""
                fields = "".join(f"<span>{_e(lab(p['entity_set'], f))}: …</span>" for f in p["card_fields"])
                ln = "".join(f'<div class="lane"><h5>{_e(l["title"])}<span>…</span></h5>'
                             + "".join(f'<div class="kc"{click}><b>{_e(lab(p["entity_set"], p["card_title"]))} {i + 1}</b>{fields}</div>'
                                       for i in range(2)) + "</div>" for l in lanes)
                hint = '<div class="note">Drag cards between columns to update the status.</div>' if p["move"] else ""
                pages_html.append(f'<div class="page" id="p_{pid}" data-nav="{pid}"><div class="ph"><h2>{_e(p["title"])}</h2></div>'
                                  f'{hint}<div class="board">{ln}</div></div>')
            elif p["type"] == "calendar":
                days = "".join(f'<div class="h">{d}</div>' for d in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"))
                for i in range(28):
                    ap = f'<span class="ap">{_e(lab(p["entity_set"], p["title_field"]))}</span>' if i % 5 in (1, 3) else ""
                    days += f"<div>{i + 1}{ap}</div>"
                pages_html.append(f'<div class="page" id="p_{pid}" data-nav="{pid}"><div class="ph"><h2>{_e(p["title"])}</h2>'
                                  f'<div class="fb"><span class="btn e">Month</span><span class="btn">Week</span><span class="btn">Day</span></div></div>'
                                  f'<div class="cal">{days}</div></div>')
            elif p["type"] == "analytics":
                cards = "".join(f'<div class="card"><h4>{_e(c["title"])}</h4><small>{_e(c["type"])} · '
                                f'{_e(", ".join(m.get("label") or m["aggregation"] for m in c["measures"]))} by '
                                f'{_e(c["dimension_label"])}{(" / " + c["time_grain"]) if c["time_grain"] else ""}</small>{_chart(c)}</div>'
                                for c in p["charts"])
                pages_html.append(f'<div class="page" id="p_{pid}" data-nav="{pid}"><div class="ph"><h2>{_e(p["title"])}</h2></div>'
                                  f'<div class="grid">{cards}</div></div>')
        body_nav = f'<div class="nav">{"".join(nav_html)}</div>'
    else:
        sub = {k: bp[k] for k in ("main", "object_page", "capabilities", "form", "actions")}
        sub["logic"] = bp.get("logic")
        first = "list"
        pages_html.append(_list_page("list", bp["app"]["title"], sub, lab, "object", False, "table", "list", []))
        pages_html.append(_detail_page("object", bp["app"]["title"], sub, lab, "list", "list"))
        body_nav = ""
    return (f'<!doctype html><html><head><meta charset="utf-8"><style>{_CSS}</style></head><body>'
            f'<div class="app"><div class="shell">{"<div class=burger></div>" if body_nav else ""}<b>{_e(bp["app"]["title"])}</b>'
            f'<span style="margin-left:auto;color:#556b82;font-size:11px">wireframe · {_e(bp["floorplan"])}</span></div>'
            f'<div class="body">{body_nav}<div class="main">{"".join(pages_html)}</div></div></div>'
            f'<script>{_JS}go({json.dumps(first)});</script></body></html>')
