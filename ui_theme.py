"""
ui_theme.py — look & feel of the generator's own Streamlit UI (not the generated apps).

A calm, professional SaaS style: neutral canvas, white cards, one violet accent,
Inter typography, a compact brand bar and a step indicator.
"""

from __future__ import annotations

import base64
import html
from typing import List, Optional

CSS = """
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
:root {
  --ev-ink: #161A33; --ev-text: #2A2F45; --ev-muted: #6B7088; --ev-faint: #9AA0B4;
  --ev-line: #E5E7EF; --ev-line-strong: #D3D6E2;
  --ev-canvas: #F5F6FA; --ev-card: #FFFFFF; --ev-soft: #F8F8FC;
  --ev-accent: #5B3FD1; --ev-accent-600: #4A30BA; --ev-accent-50: #F1EEFC; --ev-accent-100: #E4DEFA;
  --ev-amber: #F7A61B; --ev-brand-1: #1E0B4B; --ev-brand-2: #3A1E86;
  --ev-ok: #12805C; --ev-ok-bg: #E8F6F0; --ev-warn: #A45F00; --ev-warn-bg: #FFF4E0;
  --ev-bad: #C0262D; --ev-bad-bg: #FDECEC; --ev-info: #1F5FBF; --ev-info-bg: #EAF1FC;
  --ev-radius: 12px; --ev-shadow: 0 1px 2px rgba(22,26,51,.04), 0 4px 16px rgba(22,26,51,.05);
}
html, body, [class*="css"], .stApp, .stMarkdown, button, input, textarea, select {
  font-family: 'Inter', system-ui, -apple-system, 'Segoe UI', sans-serif !important;
}
.stApp, [data-testid="stAppViewContainer"] { background: var(--ev-canvas); color: var(--ev-text); }
header[data-testid="stHeader"] { background: transparent; height: 0; }
#MainMenu, footer, [data-testid="stStatusWidget"] { visibility: hidden; }
[data-testid="stDecoration"] { display: none; }
.block-container { padding-top: 0 !important; padding-bottom: 4rem; max-width: 1240px; }
[data-testid="stMain"] { container-type: inline-size; overflow-x: hidden; }
h1, h2, h3, h4 { color: var(--ev-ink); font-weight: 700; letter-spacing: -0.01em; }
h3 { font-size: 1.25rem; } h4 { font-size: 1.05rem; }
p, li { line-height: 1.55; }
code, pre, .stCode { font-family: 'JetBrains Mono', ui-monospace, monospace !important; }

/* ── brand bar: status left, brand centered ─────────────────── */
.ev-bar { width: 100cqw; position: relative; left: 50%; margin-left: -50cqw;
  background: linear-gradient(100deg, #190A42 0%, var(--ev-brand-1) 35%, var(--ev-brand-2) 100%);
  color: #fff; margin-bottom: 1.25rem; box-shadow: 0 6px 20px rgba(30,11,75,.16); }
.ev-bar::after { content: ""; position: absolute; left: 0; right: 0; bottom: 0; height: 2px;
  background: linear-gradient(90deg, var(--ev-amber), rgba(247,166,27,.35) 45%, rgba(142,116,255,.6)); }
.ev-bar-in { position: relative; padding: 24px 2rem 22px 2rem; min-height: 100px;
  display: grid; grid-template-columns: 1fr auto 1fr; align-items: center; gap: 16px; }
.ev-bar-left { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; justify-content: flex-end; }
.ev-brand { display: flex; align-items: center; justify-content: center; gap: 14px; }
.ev-logo { width: 56px; height: 56px; border-radius: 14px; background: #fff; display: flex;
  align-items: center; justify-content: center; flex: 0 0 auto; box-shadow: 0 4px 14px rgba(0,0,0,.22); }
.ev-logo img { width: 40px; height: 40px; object-fit: contain; }
.ev-brand-text { text-align: left; }
.ev-bar p.ev-title { font-size: 1.75rem !important; font-weight: 700; line-height: 1.15 !important; margin: 0 !important;
  color: #fff; letter-spacing: -.015em; white-space: nowrap; }
.ev-title b { color: var(--ev-amber); font-weight: 800; }
.ev-bar p.ev-sub { font-size: .95rem !important; line-height: 1.4 !important; color: rgba(229,223,255,.78);
  margin: 4px 0 0 !important; }
.ev-bar-left { gap: 0; }
.ev-stat { display: flex; flex-direction: column; justify-content: center; padding: 2px 18px 2px 14px;
  border-left: 2px solid rgba(247,166,27,.75); }
.ev-stat + .ev-stat { border-left-color: rgba(255,255,255,.22); }
.ev-stat-k { font-size: .66rem; font-weight: 700; letter-spacing: .12em; text-transform: uppercase;
  color: rgba(229,223,255,.62); line-height: 1.2; }
.ev-stat-v { font-size: .9rem; font-weight: 600; color: #fff; line-height: 1.35; white-space: nowrap;
  display: flex; align-items: center; gap: 7px; }
.ev-stat-v::before { content: ""; width: 8px; height: 8px; border-radius: 50%; background: #3DDC97;
  box-shadow: 0 0 0 3px rgba(61,220,151,.18); flex: 0 0 auto; }
.ev-stat.off .ev-stat-v::before { background: #FF8A80; box-shadow: 0 0 0 3px rgba(255,138,128,.18); }
.ev-pill { font-size: .74rem; font-weight: 600; padding: 5px 12px; border-radius: 8px;
  background: rgba(255,255,255,.08); border: 1px solid rgba(255,255,255,.18); color: #fff; white-space: nowrap; }
.ev-pill.ok::before { content: ""; display: inline-block; width: 7px; height: 7px; border-radius: 50%;
  background: #3DDC97; margin-right: 7px; vertical-align: 1px; }
.ev-pill.off::before { content: ""; display: inline-block; width: 7px; height: 7px; border-radius: 50%;
  background: #FF8A80; margin-right: 7px; vertical-align: 1px; }
@media (max-width: 900px) {
  .ev-bar-in { grid-template-columns: 1fr; justify-items: center; padding-left: 1.5rem; }
  .ev-bar-left { order: 2; justify-content: center; } }
@media (max-width: 640px) {
  .ev-brand { flex-direction: column; gap: 8px; } .ev-brand-text { text-align: center; }
  .ev-bar p.ev-title { font-size: 1.35rem !important; white-space: normal; } }
@media (max-width: 1100px) { .ev-bar-left .ev-stat + .ev-stat { display: none; } }

/* ── sidebar re-open button: appears when the pointer is near the top-left corner ── */
[data-testid="stToolbar"] { display: block !important; }
[data-testid="stToolbarActions"], [data-testid="stMainMenu"] { display: none !important; }
[data-testid="stExpandSidebarButton"] { position: fixed !important; top: 36px; left: 12px; z-index: 1000000;
  width: 2.5rem !important; height: 2.5rem !important; border-radius: 10px !important;
  background: #fff !important; border: 1px solid rgba(91,63,209,.35) !important;
  box-shadow: 0 6px 18px rgba(22,26,51,.35) !important; display: flex !important; align-items: center;
  justify-content: center; opacity: 0; transition: opacity .18s ease, transform .18s ease; }
/* invisible hot zone around it: the button fades in as the pointer reaches the corner */
[data-testid="stExpandSidebarButton"]::before { content: ""; position: absolute; inset: -14px; }
[data-testid="stExpandSidebarButton"]:hover, [data-testid="stExpandSidebarButton"]:focus-visible {
  opacity: 1; transform: translateX(2px); }
[data-testid="stExpandSidebarButton"] span { color: var(--ev-accent) !important; font-weight: 700; }

/* ── login ─────────────────────────────────────────────────── */
.ev-login-hero { text-align: center; margin: 1.5rem 0 1.1rem; }
.ev-login-hero .ev-logo { width: 76px; height: 76px; border-radius: 20px; margin: 0 auto 14px; }
.ev-login-hero .ev-logo img { width: 52px; height: 52px; }
.ev-login-hero h2 { margin: 0; padding: 0 !important; font-size: 1.6rem !important; white-space: nowrap; color: var(--ev-ink); letter-spacing: -.02em; }
.ev-login-hero h2 b { color: var(--ev-accent); }
.ev-login-hero p { margin: .4rem 0 0 !important; color: var(--ev-muted); font-size: .95rem; }
.st-key-login_card { background: var(--ev-card); border-radius: 18px; padding: 1.4rem 1.5rem 1.2rem;
  border: 1px solid var(--ev-line); position: relative;
  box-shadow: 0 1px 2px rgba(22,26,51,.04), 0 18px 48px rgba(58,30,134,.14); }
.st-key-login_card::before { content: ""; position: absolute; left: 18px; right: 18px; top: 0; height: 4px;
  border-radius: 0 0 6px 6px; background: linear-gradient(90deg, var(--ev-amber), #8E74FF, var(--ev-accent)); }
.ev-login-foot { text-align: center; color: var(--ev-faint); font-size: .78rem; margin-top: 1rem; }

/* ── stepper ───────────────────────────────────────────────── */
.ev-steps { display: flex; gap: 0; margin: .25rem 0 1rem; background: var(--ev-card); border: 1px solid var(--ev-line);
  border-radius: var(--ev-radius); box-shadow: var(--ev-shadow); overflow: hidden; }
.ev-step { flex: 1 1 0; display: flex; align-items: center; gap: 10px; padding: 12px 14px; min-width: 0;
  border-right: 1px solid var(--ev-line); }
.ev-step:last-child { border-right: none; }
.ev-num { width: 26px; height: 26px; border-radius: 50%; flex: 0 0 auto; display: flex; align-items: center;
  justify-content: center; font-size: .78rem; font-weight: 700; background: var(--ev-soft); color: var(--ev-muted);
  border: 1px solid var(--ev-line-strong); }
.ev-step .t { font-size: .84rem; font-weight: 600; color: var(--ev-muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.ev-step .d { font-size: .72rem; color: var(--ev-faint); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.ev-step.done .ev-num { background: var(--ev-ok-bg); color: var(--ev-ok); border-color: #BFE6D5; }
.ev-step.done .t { color: var(--ev-text); }
.ev-step.cur { background: var(--ev-accent-50); }
.ev-step.cur .ev-num { background: var(--ev-accent); color: #fff; border-color: var(--ev-accent); }
.ev-step.cur .t { color: var(--ev-accent-600); }
@container (max-width: 760px) { .ev-step .d { display: none; } .ev-step { padding: 10px 8px; } }

/* clickable stepper (st.container key="stepper") */
.st-key-stepper { background: var(--ev-card); border: 1px solid var(--ev-line); border-radius: var(--ev-radius);
  box-shadow: var(--ev-shadow); padding: 8px 10px; margin: .1rem 0 1.1rem; }
.st-key-stepper button { border: none !important; background: transparent !important; color: var(--ev-muted) !important;
  font-weight: 600; justify-content: flex-start; padding: .55rem .8rem; box-shadow: none !important; }
.st-key-stepper button:hover { background: var(--ev-soft) !important; color: var(--ev-text) !important; }
.st-key-stepper .stButton > button[kind="primary"], .st-key-stepper [data-testid="stBaseButton-primary"] {
  background: var(--ev-accent-50) !important; color: var(--ev-accent-600) !important; }
.stAppDeployButton, [data-testid="stAppDeployButton"] { display: none !important; }
[data-testid="stButtonGroup"] button { border-radius: 9px !important; font-weight: 600 !important; }
/* tab rows (Source, Load as): separate tabs spanning the width of the card below */
[class*="st-key-tabs_"] [data-testid="stElementContainer"], [class*="st-key-tabs_"] [data-testid="stButtonGroup"] {
  width: 100% !important; }
[class*="st-key-tabs_"] [role="radiogroup"] { display: flex !important; width: 100% !important; max-width: none !important;
  gap: 10px; flex-wrap: wrap; }
[class*="st-key-tabs_"] [role="radiogroup"] > button { flex: 1 1 auto; white-space: nowrap; min-width: 8.5rem; justify-content: center;
  margin: 0 !important; border: 1px solid var(--ev-line-strong) !important; padding-top: .45rem !important;
  padding-bottom: .45rem !important; }
[class*="st-key-tabs_"] [role="radiogroup"] > button[aria-checked="true"] { border-color: var(--ev-accent) !important;
  background: var(--ev-accent-50) !important; color: var(--ev-accent-600) !important;
  box-shadow: 0 0 0 1px var(--ev-accent) inset; }

/* ── cards & sections ──────────────────────────────────────── */
.ev-card { background: var(--ev-card); border: 1px solid var(--ev-line); border-radius: var(--ev-radius);
  box-shadow: var(--ev-shadow); padding: 18px 20px; margin-bottom: 14px; }
.ev-h { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; margin: .35rem 0 .7rem; }
.ev-h h3 { margin: 0; }
.ev-h .hint { color: var(--ev-muted); font-size: .86rem; }
.ev-kicker { text-transform: uppercase; letter-spacing: .08em; font-size: .7rem; font-weight: 700; color: var(--ev-accent); margin-bottom: 2px; }
.ev-lead { color: var(--ev-muted); font-size: .92rem; margin: -.2rem 0 1rem; }
[data-testid="stVerticalBlockBorderWrapper"] { border-radius: var(--ev-radius) !important; }
div[data-testid="stVerticalBlockBorderWrapper"]:has(> div > div > [data-testid="stVerticalBlock"]) {
  background: var(--ev-card); }
[data-testid="stExpander"] { background: var(--ev-card); border: 1px solid var(--ev-line) !important;
  border-radius: var(--ev-radius) !important; box-shadow: none; }
[data-testid="stExpander"] summary { font-weight: 600; color: var(--ev-text); }
[data-testid="stExpander"] summary:hover { color: var(--ev-accent); }

/* chips / badges */
.ev-chip { display: inline-flex; align-items: center; gap: 6px; font-size: .74rem; font-weight: 600; padding: 3px 10px;
  border-radius: 999px; border: 1px solid var(--ev-line-strong); color: var(--ev-text); background: var(--ev-soft);
  margin: 0 6px 6px 0; white-space: nowrap; }
.ev-chip.ok { background: var(--ev-ok-bg); color: var(--ev-ok); border-color: #BFE6D5; }
.ev-chip.warn { background: var(--ev-warn-bg); color: var(--ev-warn); border-color: #F5D9A8; }
.ev-chip.bad { background: var(--ev-bad-bg); color: var(--ev-bad); border-color: #F5C2C4; }
.ev-chip.info { background: var(--ev-info-bg); color: var(--ev-info); border-color: #C9DBF6; }
.ev-chip.accent { background: var(--ev-accent-50); color: var(--ev-accent-600); border-color: var(--ev-accent-100); }

/* metric tiles */
.ev-metrics { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; margin: .2rem 0 1rem; }
.ev-metric { background: var(--ev-card); border: 1px solid var(--ev-line); border-radius: var(--ev-radius); padding: 12px 14px;
  box-shadow: var(--ev-shadow); }
.ev-metric .l { font-size: .74rem; color: var(--ev-muted); font-weight: 600; text-transform: uppercase; letter-spacing: .04em; }
.ev-metric .v { font-size: 1.45rem; font-weight: 750; color: var(--ev-ink); margin-top: 2px; line-height: 1.2; }
.ev-metric .s { font-size: .76rem; color: var(--ev-faint); margin-top: 2px; }
.ev-metric.accent { border-left: 3px solid var(--ev-accent); }
.ev-score { display: inline-flex; align-items: baseline; gap: 4px; }

/* coverage table */
.ev-cov { width: 100%; border-collapse: separate; border-spacing: 0; font-size: .86rem; }
.ev-cov th { text-align: left; font-size: .72rem; text-transform: uppercase; letter-spacing: .05em; color: var(--ev-muted);
  padding: 8px 10px; border-bottom: 1px solid var(--ev-line); background: var(--ev-soft); }
.ev-cov td { padding: 9px 10px; border-bottom: 1px solid var(--ev-line); vertical-align: top; }
.ev-cov tr:last-child td { border-bottom: none; }
.ev-cov .id { color: var(--ev-faint); font-weight: 600; white-space: nowrap; }
.ev-cov .note { color: var(--ev-muted); font-size: .8rem; }
.ev-tablewrap { background: var(--ev-card); border: 1px solid var(--ev-line); border-radius: var(--ev-radius); overflow: hidden; }

/* pipeline timeline */
.ev-tl { display: flex; flex-wrap: wrap; gap: 8px; margin: .2rem 0 .8rem; }
.ev-tl .s { font-size: .76rem; padding: 4px 10px; border-radius: 8px; background: var(--ev-soft); border: 1px solid var(--ev-line); color: var(--ev-text); }
.ev-tl .s b { font-weight: 650; }
.ev-tl .s.fail { background: var(--ev-bad-bg); border-color: #F5C2C4; color: var(--ev-bad); }

/* empty state */
.ev-empty { text-align: center; padding: 38px 20px; color: var(--ev-muted); background: var(--ev-card);
  border: 1px dashed var(--ev-line-strong); border-radius: var(--ev-radius); }
.ev-empty .i { font-size: 30px; margin-bottom: 8px; }
.ev-empty b { color: var(--ev-text); display: block; font-size: 1rem; margin-bottom: 4px; }

/* widgets */
.stButton > button, .stDownloadButton > button { border-radius: 10px; font-weight: 600; border: 1px solid var(--ev-line-strong);
  background: #fff; color: var(--ev-text); padding: .45rem 1rem; transition: border-color .15s, box-shadow .15s, background .15s; }
.stButton > button:hover, .stDownloadButton > button:hover { border-color: var(--ev-accent); color: var(--ev-accent-600);
  box-shadow: 0 0 0 3px var(--ev-accent-50); }
.stButton > button[kind="primary"], .stDownloadButton > button[kind="primary"], [data-testid="stBaseButton-primary"] {
  background: var(--ev-accent) !important; border-color: var(--ev-accent) !important; color: #fff !important; }
.stButton > button[kind="primary"]:hover, [data-testid="stBaseButton-primary"]:hover {
  background: var(--ev-accent-600) !important; box-shadow: 0 6px 18px rgba(91,63,209,.28) !important; color: #fff !important; }
.stButton > button:disabled { background: #F1F2F6 !important; color: var(--ev-faint) !important; border-color: var(--ev-line) !important; box-shadow: none !important; }
.stTextInput input, .stTextArea textarea, .stNumberInput input, [data-baseweb="select"] > div {
  border-radius: 10px !important; border-color: var(--ev-line-strong) !important; background: #fff !important; }
.stTextInput input:focus, .stTextArea textarea:focus { border-color: var(--ev-accent) !important; box-shadow: 0 0 0 3px var(--ev-accent-50) !important; }
label p { font-weight: 600 !important; color: var(--ev-text) !important; font-size: .86rem !important; }
[data-testid="stCaptionContainer"] { color: var(--ev-muted); }
.stTabs [data-baseweb="tab-list"] { gap: 4px; border-bottom: 1px solid var(--ev-line); }
.stTabs [data-baseweb="tab"] { font-weight: 600; color: var(--ev-muted); padding: 8px 14px; }
.stTabs [aria-selected="true"] { color: var(--ev-accent-600) !important; }
.stTabs [data-baseweb="tab-highlight"] { background: var(--ev-accent) !important; }
[data-testid="stAlert"] { border-radius: 10px; }
[data-testid="stMetric"] { background: var(--ev-card); border: 1px solid var(--ev-line); border-radius: var(--ev-radius); padding: 10px 14px; }

/* sidebar */
[data-testid="stSidebar"] { background: #FBFBFE; border-right: 1px solid var(--ev-line); }
[data-testid="stSidebar"] h3 { font-size: .78rem; text-transform: uppercase; letter-spacing: .07em; color: var(--ev-muted); margin-top: .6rem; }
.ev-hist { border: 1px solid var(--ev-line); border-radius: 10px; padding: 8px 10px; background: #fff; margin-bottom: 6px; font-size: .8rem; }
.ev-hist b { color: var(--ev-text); }
.ev-hist span { color: var(--ev-faint); }

/* logs */
.ev-log { font-family: 'JetBrains Mono', ui-monospace, monospace; font-size: 12px; background: #12102A; color: #DCD8F2;
  padding: 14px 16px; border-radius: 10px; max-height: 340px; overflow-y: auto; line-height: 1.55; border: 1px solid #241F4D; }
</style>
"""


# Streamlit's markdown ends an HTML block at a blank line — keep the CSS one block.
CSS = "\n".join(line for line in CSS.splitlines() if line.strip())


def logo_data_uri(paths=("assets/evoke_logo.png", "evoke_logo.png", "static/evoke_logo.png")) -> str:
    for p in paths:
        try:
            with open(p, "rb") as f:
                return "data:image/png;base64," + base64.b64encode(f.read()).decode()
        except OSError:
            continue
    return ""


def _stat(text: str, kind: str) -> str:
    """Status block of the brand bar: 'AI model' / 'Service' caption over its value."""
    if text.startswith("OData"):
        cap, val = "Service", text
    else:
        cap, val = "AI model", text.split("--", 1)[-1]   # anthropic--claude-4.6-sonnet -> claude-4.6-sonnet
    return (f'<div class="ev-stat {kind}"><span class="ev-stat-k">{cap}</span>'
            f'<span class="ev-stat-v" title="{html.escape(text)}">{html.escape(val)}</span></div>')


def brand_bar(logo_uri: str, pills: List[tuple]) -> str:
    """pills: [(text, kind)] kind in ('', 'ok', 'off')."""
    logo = f'<img src="{logo_uri}" alt="Evoke"/>' if logo_uri else "🔷"
    ph = "".join(_stat(t, k) for t, k in pills)
    return (f'<div class="ev-bar"><div class="ev-bar-in"><div></div>'
            f'<div class="ev-brand"><div class="ev-logo">{logo}</div>'
            f'<div class="ev-brand-text"><p class="ev-title"><b>Evoke</b> Fiori App Studio</p>'
            f'<p class="ev-sub">Describe an application — get a working SAP Fiori app on your real OData service.</p>'
            f'</div></div><div class="ev-bar-left">{ph}</div></div></div>')


def login_hero(logo_uri: str) -> str:
    logo = f'<img src="{logo_uri}" alt="Evoke"/>' if logo_uri else "🔷"
    return (f'<div class="ev-login-hero"><div class="ev-logo">{logo}</div>'
            f'<h2>Welcome to <b>Evoke</b> Fiori App Studio</h2>'
            f'<p>Sign in to design, generate and deploy SAP Fiori apps.</p></div>')


STEPS = [("Start", "SAP service or new app"), ("Describe", "What the app should do"),
         ("Review", "Design & preview"), ("Build", "Generate & validate"), ("Deploy", "Approve & deploy")]


def stepper(current: int, done: List[bool]) -> str:
    out = []
    for i, (t, d) in enumerate(STEPS):
        cls = "cur" if i == current else ("done" if done[i] else "")
        num = "✓" if done[i] and i != current else str(i + 1)
        out.append(f'<div class="ev-step {cls}"><div class="ev-num">{num}</div>'
                   f'<div style="min-width:0"><div class="t">{t}</div><div class="d">{d}</div></div></div>')
    return '<div class="ev-steps">' + "".join(out) + "</div>"


def header(kicker: str, title: str, lead: str = "") -> str:
    return (f'<div class="ev-kicker">{html.escape(kicker)}</div><h3 style="margin:0 0 .3rem">{html.escape(title)}</h3>'
            + (f'<p class="ev-lead">{lead}</p>' if lead else ""))


def chip(text: str, kind: str = "") -> str:
    return f'<span class="ev-chip {kind}">{html.escape(str(text))}</span>'


def metrics(items: List[tuple]) -> str:
    """items: [(label, value, sub, accent?)]"""
    out = []
    for it in items:
        label, value, sub = it[0], it[1], it[2] if len(it) > 2 else ""
        acc = " accent" if len(it) > 3 and it[3] else ""
        out.append(f'<div class="ev-metric{acc}"><div class="l">{html.escape(str(label))}</div>'
                   f'<div class="v">{value}</div>' + (f'<div class="s">{html.escape(str(sub))}</div>' if sub else "") + "</div>")
    return '<div class="ev-metrics">' + "".join(out) + "</div>"


def empty(icon: str, title: str, text: str) -> str:
    return f'<div class="ev-empty"><div class="i">{icon}</div><b>{html.escape(title)}</b>{text}</div>'


STATUS_CHIP = {"covered": ("Covered", "ok"), "partial": ("Partial", "warn"), "missing": ("Missing", "bad")}


def coverage_table(coverage: List[dict]) -> str:
    rows = []
    for c in coverage:
        lab, kind = STATUS_CHIP.get(c["status"], (c["status"], ""))
        rows.append(f'<tr><td class="id">{html.escape(c["id"])}</td>'
                    f'<td>{html.escape(c["text"])}<div class="note">{html.escape(c.get("note") or "")}</div></td>'
                    f'<td>{chip(c["priority"], "accent" if c["priority"] == "must" else "")}</td>'
                    f'<td>{chip(lab, kind)}</td>'
                    f'<td class="note">{html.escape(c.get("where") or "")}</td></tr>')
    return ('<div class="ev-tablewrap"><table class="ev-cov"><thead><tr><th>#</th><th>Requirement</th>'
            '<th>Priority</th><th>Status</th><th>Where</th></tr></thead><tbody>' + "".join(rows) + "</tbody></table></div>")


def timeline(steps: List[dict]) -> str:
    names = {"analyze": "Analysis", "design": "Design", "repair": "Self-repair", "coverage": "Coverage check"}
    out = []
    for s in steps:
        secs = f" · {s['seconds']}s" if s.get("seconds") else ""
        out.append(f'<span class="s{"" if s["ok"] else " fail"}"><b>{names.get(s["step"], s["step"])}</b> — '
                   f'{html.escape(s.get("detail") or "")}{secs}</span>')
    return '<div class="ev-tl">' + "".join(out) + "</div>"


def score_color(score: Optional[int]) -> str:
    if score is None:
        return "var(--ev-muted)"
    return "var(--ev-ok)" if score >= 85 else ("var(--ev-warn)" if score >= 65 else "var(--ev-bad)")
