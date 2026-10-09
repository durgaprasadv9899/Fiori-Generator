"""
style.py — "make it look like this": reference image / PDF / text -> app style.

Inputs (any combination):
  * images  — screenshots, mockups, brand sheets, logos (PNG/JPEG/WEBP/GIF)
  * PDFs    — brand / style guidelines
  * text    — a description ("dark, rounded, Inter, our red #E30613") or the
              contents of CSS / theme JSON / LESS files

Output: a validated *style* dict (stored in the blueprint as bp["style"]):
  {
    "base_theme": "sap_horizon" | "sap_horizon_dark" | "sap_horizon_hcb" | "sap_horizon_hcw"
                  | "sap_fiori_3" | "sap_fiori_3_dark",
    "colors": {"brand", "accent", "shell", "shell_text", "background", "surface",
               "text", "title", "link", "positive", "critical", "negative"},  # hex, any may be ""
    "font": {"family": "Inter", "heading_family": "", "source": "google" | "system"},
    "radius": "none" | "small" | "medium" | "large" | "pill",
    "density": "compact" | "cozy",
    "header": "default" | "brand",           # brand-coloured page headers
    "layout_hints": str,                      # structural observations -> app design
    "rationale": str
  }

How it is applied: SAPUI5 (Horizon, 1.1xx) renders every control from the SAP
theming parameters exposed as CSS custom properties (--sapBrandColor,
--sapButton_Emphasized_Background, --sapFontFamily, ...). The generator emits
webapp/css/style.css overriding those parameters, so standard controls and Fiori
Elements floorplans pick up the brand without a compiled custom theme. Exact
colours come from the image pixels (palette extraction); the AI only decides
which colour plays which role.
"""

from __future__ import annotations
import io
import json
import re
from typing import Callable, Dict, List, Optional, Tuple

THEMES = ("sap_horizon", "sap_horizon_dark", "sap_horizon_hcb", "sap_horizon_hcw",
          "sap_fiori_3", "sap_fiori_3_dark")
DARK_THEMES = ("sap_horizon_dark", "sap_horizon_hcb", "sap_fiori_3_dark")
RADII = {"none": "0", "small": ".25rem", "medium": ".5rem", "large": ".75rem", "pill": "1.5rem"}
COLOR_ROLES = ("brand", "accent", "shell", "shell_text", "background", "surface", "text",
               "title", "link", "positive", "critical", "negative")
SYSTEM_FONTS = {"Arial", "Helvetica", "Georgia", "Times New Roman", "Verdana", "Tahoma",
                "Trebuchet MS", "Segoe UI", "Courier New", "72"}
TEXT_EXT = (".css", ".less", ".scss", ".json", ".txt", ".md", ".html", ".xml", ".yaml", ".yml")


def default_style() -> Dict:
    return {"base_theme": "sap_horizon", "colors": {r: "" for r in COLOR_ROLES},
            "font": {"family": "", "heading_family": "", "source": "system"},
            "radius": "", "density": "compact", "header": "default",
            "layout_hints": "", "rationale": ""}


def is_default(style: Optional[Dict]) -> bool:
    if not style:
        return True
    return (not any(style.get("colors", {}).values()) and not style.get("font", {}).get("family")
            and not style.get("radius") and style.get("base_theme", "sap_horizon") == "sap_horizon"
            and style.get("header", "default") == "default")


# ─────────────────────────────────────────────────────────────────────────────
# Colour helpers
# ─────────────────────────────────────────────────────────────────────────────
def norm_hex(v) -> str:
    s = str(v or "").strip()
    m = re.fullmatch(r"#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})", s)
    if not m:
        m2 = re.fullmatch(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)", s.split(")")[0] + "") if s else None
        if m2:
            return "#%02x%02x%02x" % tuple(min(255, int(x)) for x in m2.groups())
        return ""
    h = m.group(1)
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return "#" + h.lower()


def _rgb(h: str) -> Tuple[int, int, int]:
    h = norm_hex(h)[1:]
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def luminance(h: str) -> float:
    def ch(c):
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = _rgb(h)
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def contrast(a: str, b: str) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def on_color(bg: str) -> str:
    """Readable text colour on bg (white or near-black)."""
    return "#ffffff" if contrast(bg, "#ffffff") >= contrast(bg, "#1d2d3e") else "#1d2d3e"


def shade(h: str, factor: float) -> str:
    """factor < 1 darkens, > 1 lightens."""
    r, g, b = _rgb(h)
    if factor < 1:
        r, g, b = (int(c * factor) for c in (r, g, b))
    else:
        r, g, b = (int(c + (255 - c) * (factor - 1)) for c in (r, g, b))
    return "#%02x%02x%02x" % (max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b)))


def readable_on(fg: str, bg: str, min_ratio: float = 4.5) -> str:
    """Darken/lighten fg until it reaches min_ratio contrast against bg."""
    if not fg:
        return fg
    cur = fg
    dark_bg = luminance(bg) < 0.4
    for _ in range(12):
        if contrast(cur, bg) >= min_ratio:
            return cur
        cur = shade(cur, 1.15 if dark_bg else 0.85)
    return on_color(bg)


# ─────────────────────────────────────────────────────────────────────────────
# Palette extraction (exact colours from the pixels)
# ─────────────────────────────────────────────────────────────────────────────
def extract_palette(image_bytes: bytes, n: int = 10) -> List[Dict]:
    """Dominant colours of an image: [{"hex", "share"}] by area, largest first."""
    from PIL import Image
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    # NEAREST keeps flat UI colours exact (no blending of header/button edges)
    w, h = img.size
    scale = min(1.0, 480 / max(w, h))
    img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.NEAREST)
    total = img.size[0] * img.size[1]
    counts = img.getcolors(total) or []
    if len(counts) > 4000:                       # photo-like: reduce noise first
        q = img.quantize(colors=24, method=Image.Quantize.FASTOCTREE).convert("RGB")
        counts = q.getcolors(total) or []
    clusters: List[List] = []                    # [r, g, b, count]
    for count, (r, g, b) in sorted(counts, reverse=True):
        for c in clusters:
            if abs(c[0] - r) < 14 and abs(c[1] - g) < 14 and abs(c[2] - b) < 14:
                c[3] += count
                break
        else:
            clusters.append([r, g, b, count])
    out = []
    for r, g, b, count in sorted(clusters, key=lambda c: -c[3]):
        share = count / total
        if share < 0.002:                        # ignore anti-aliasing / text pixels
            continue
        out.append({"hex": "#%02x%02x%02x" % (r, g, b), "share": round(share, 3)})
        if len(out) >= n:
            break
    return out


# ─────────────────────────────────────────────────────────────────────────────
# AI analysis
# ─────────────────────────────────────────────────────────────────────────────
def _system_prompt() -> str:
    return (
        "You are a senior SAP Fiori UX designer. From the user's references (screenshots, "
        "mockups, brand sheets, logos, style-guide PDFs, CSS/theme files, descriptions) "
        "derive the visual style for an SAPUI5 / SAP Fiori application. Reply with STRICT "
        "JSON only:\n"
        "{\n"
        ' "base_theme": "' + "|".join(THEMES) + '",\n'
        ' "colors": {"brand": hex, "accent": hex, "shell": hex, "shell_text": hex, "background": hex,\n'
        '            "surface": hex, "text": hex, "title": hex, "link": hex,\n'
        '            "positive": hex, "critical": hex, "negative": hex},\n'
        ' "font": {"family": str, "heading_family": str, "source": "google|system"},\n'
        ' "radius": "none|small|medium|large|pill",\n'
        ' "density": "compact|cozy",\n'
        ' "header": "default|brand",\n'
        ' "layout_hints": str,\n'
        ' "rationale": str\n'
        "}\n"
        "Rules:\n"
        "- When a MEASURED PALETTE is given, use those exact hex values for the colour roles "
        "(pick the right one per role; never invent near-duplicates).\n"
        "- brand = primary action / emphasis colour; shell = app header bar; background = page "
        "background; surface = cards/panels; positive/critical/negative = status colours seen "
        "(success/warning/error), else empty string.\n"
        "- Use \"\" for any role the references don't define — the SAP theme default is kept.\n"
        "- base_theme: dark references -> sap_horizon_dark; high-contrast -> hcb/hcw; else sap_horizon.\n"
        "- font: name the closest Google Font (e.g. Inter, Roboto, Lato, Merriweather, Playfair "
        "Display, Source Serif 4, Poppins, Montserrat, Open Sans, IBM Plex Sans) or a system font; "
        "empty if the references keep SAP's 72 font.\n"
        "- header=brand when the reference shows a coloured top bar/page header.\n"
        "- layout_hints: 1-3 sentences on STRUCTURE seen (list vs cards, status pills, prominent "
        "primary action, KPI tiles, sections) — used to design the app; empty for pure brand assets."
    )


def analyze(mm_fn: Optional[Callable], images: List[Tuple[bytes, str, str]] = (),
            documents: List[Tuple[bytes, str, str]] = (), text: str = "") -> Tuple[Dict, List[str]]:
    """Return (style, notes). mm_fn = ai_client.make_multimodal_fn(...) or None."""
    notes: List[str] = []
    palette = []
    for data, mime, name in images:
        try:
            for c in extract_palette(data):
                if c["hex"] not in [p["hex"] for p in palette]:
                    palette.append(dict(c, source=name))
        except Exception as e:  # noqa: BLE001
            notes.append(f"Could not read colours from {name}: {e}")
    if mm_fn is None:
        style = _rules_from(text, palette)
        notes.append("AI unavailable — style derived from the measured palette and description only.")
        return validate(style)[0], notes
    user = ""
    if palette:
        user += "MEASURED PALETTE (exact pixel colours, share of image area):\n" + "\n".join(
            f"- {p['hex']}  {p['share'] * 100:.0f}%  ({p['source']})" for p in palette[:16]) + "\n\n"
    if text.strip():
        user += "USER DESCRIPTION / FILE CONTENTS:\n" + text.strip()[:12000] + "\n\n"
    user += "Return the style JSON."
    try:
        raw = mm_fn(_system_prompt(), user, list(images) + list(documents))
        s = raw[raw.find("{"): raw.rfind("}") + 1]
        style = json.loads(s)
    except Exception as e:  # noqa: BLE001
        notes.append(f"AI style analysis failed ({str(e)[:150]}) — using measured palette only.")
        style = _rules_from(text, palette)
    style, vnotes = validate(style)
    return style, notes + vnotes


def _rules_from(text: str, palette: List[Dict]) -> Dict:
    st = default_style()
    t = (text or "").lower()
    hexes = [norm_hex(h) for h in re.findall(r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b", text or "")]
    chromatic = [p["hex"] for p in palette if _saturation(p["hex"]) > 0.35]
    if hexes:
        st["colors"]["brand"] = hexes[0]
    elif chromatic:
        st["colors"]["brand"] = chromatic[0]
    if palette and luminance(palette[0]["hex"]) < 0.12 or "dark" in t:
        st["base_theme"] = "sap_horizon_dark"
    for kw, r in (("pill", "pill"), ("round", "large"), ("square", "none"), ("sharp", "none")):
        if kw in t:
            st["radius"] = r
            break
    m = re.search(r"font[:\s]+([A-Z][A-Za-z0-9 ]{2,30})", text or "")
    if m:
        st["font"] = {"family": m.group(1).strip(), "heading_family": "", "source": "google"}
    return st


def _saturation(h: str) -> float:
    r, g, b = (c / 255 for c in _rgb(h))
    mx, mn = max(r, g, b), min(r, g, b)
    return 0 if mx == 0 else (mx - mn) / mx


def validate(style: Dict) -> Tuple[Dict, List[str]]:
    notes: List[str] = []
    out = default_style()
    s = style or {}
    out["base_theme"] = s.get("base_theme") if s.get("base_theme") in THEMES else "sap_horizon"
    cols = s.get("colors") or {}
    for r in COLOR_ROLES:
        out["colors"][r] = norm_hex(cols.get(r))
        if cols.get(r) and not out["colors"][r]:
            notes.append(f"Ignored invalid colour for {r}: {cols.get(r)!r}")
    c = out["colors"]
    dark = out["base_theme"] in DARK_THEMES
    bg = c["background"] or ("#12171c" if dark else "#f5f6f7")
    surface = c["surface"] or ("#1d232a" if dark else "#ffffff")
    # Keep text readable on the actual surfaces
    for role, against in (("text", surface), ("title", surface), ("link", surface)):
        if c[role]:
            fixed = readable_on(c[role], against)
            if fixed != c[role]:
                notes.append(f"Adjusted {role} colour {c[role]} -> {fixed} for readability.")
                c[role] = fixed
    if c["shell"]:
        want = c["shell_text"] or on_color(c["shell"])
        if contrast(want, c["shell"]) < 4.5:
            notes.append(f"Header text colour changed for contrast on {c['shell']}.")
            want = on_color(c["shell"])
        c["shell_text"] = want
    if c["brand"] and not c["link"]:
        c["link"] = readable_on(c["brand"], surface)
    f = s.get("font") or {}
    fam = re.sub(r"[^A-Za-z0-9 ]", "", str(f.get("family") or "")).strip()[:40]
    head = re.sub(r"[^A-Za-z0-9 ]", "", str(f.get("heading_family") or "")).strip()[:40]
    src = f.get("source") if f.get("source") in ("google", "system") else (
        "system" if fam in SYSTEM_FONTS else "google")
    if fam in ("72", "SAP 72"):
        fam = ""
    out["font"] = {"family": fam, "heading_family": head if head != fam else "", "source": src}
    out["radius"] = s.get("radius") if s.get("radius") in RADII else ""
    out["density"] = s.get("density") if s.get("density") in ("compact", "cozy") else "compact"
    out["header"] = "brand" if s.get("header") == "brand" and c["shell"] else "default"
    out["layout_hints"] = str(s.get("layout_hints") or "")[:600]
    out["rationale"] = str(s.get("rationale") or "")[:400]
    _ = bg
    return out, notes


# ─────────────────────────────────────────────────────────────────────────────
# CSS generation (SAP theming parameters as CSS custom properties)
# ─────────────────────────────────────────────────────────────────────────────
def _font_stack(name: str, src: str) -> str:
    if not name:
        return ""
    generic = "serif" if re.search(r"(serif|georgia|times|merriweather|playfair|garamond|lora|slab)",
                                   name, re.I) and not re.search(r"sans", name, re.I) else "sans-serif"
    return f'"{name}", "72", "72full", Arial, Helvetica, {generic}'


def google_fonts_url(style: Dict) -> str:
    fams = []
    f = style.get("font") or {}
    if f.get("source") == "google":
        for name in (f.get("family"), f.get("heading_family")):
            if name and name not in SYSTEM_FONTS and name not in fams:
                fams.append(name)
    if not fams:
        return ""
    return ("https://fonts.googleapis.com/css2?" + "&".join(
        "family=" + n.replace(" ", "+") + ":wght@400;600;700" for n in fams) + "&display=swap")


def css(style: Dict) -> str:
    """webapp/css/style.css for a validated style ('' when nothing to change)."""
    if is_default(style):
        return ""
    c = style["colors"]
    v: Dict[str, str] = {}
    if c["brand"]:
        b = c["brand"]
        txt = on_color(b)
        hover = shade(b, 0.88) if luminance(b) > 0.05 else shade(b, 1.2)
        v.update({
            "--sapBrandColor": b, "--sapHighlightColor": b, "--sapSelectedColor": b,
            "--sapActiveColor": b, "--sapContent_FocusColor": b,
            "--sapButton_Emphasized_Background": b, "--sapButton_Emphasized_BorderColor": b,
            "--sapButton_Emphasized_TextColor": txt,
            "--sapButton_Emphasized_Hover_Background": hover,
            "--sapButton_Emphasized_Hover_BorderColor": hover,
            "--sapButton_Emphasized_Hover_TextColor": txt,
            "--sapButton_Emphasized_Active_Background": shade(b, 0.78),
            "--sapButton_Emphasized_Active_BorderColor": shade(b, 0.78),
            "--sapButton_Emphasized_Active_TextColor": txt,
            "--sapButton_Lite_TextColor": c["link"] or b,
            "--sapButton_TextColor": c["link"] or b,
            "--sapButton_BorderColor": c["link"] or b,
            "--sapButton_Selected_Background": b, "--sapButton_Selected_BorderColor": b,
            "--sapButton_Selected_TextColor": txt,
            "--sapField_Focus_BorderColor": b, "--sapField_Hover_BorderColor": b,
            "--sapList_SelectionBorderColor": b, "--sapList_SelectionBackgroundColor": shade(b, 1.88),
            "--sapList_Hover_SelectionBackground": shade(b, 1.8),
            "--sapContent_Selected_Background": shade(b, 1.88),
            "--sapTab_Selected_TextColor": b, "--sapTab_Selected_Indicator_Dimension": "0.188rem",
            "--sapObjectHeader_Title_TextColor": c["title"] or "",
        })
    if c["accent"]:
        v.update({"--sapButton_Attention_Background": c["accent"],
                  "--sapButton_Attention_BorderColor": c["accent"],
                  "--sapButton_Attention_TextColor": on_color(c["accent"]),
                  "--sapIndicationColor_6": c["accent"]})
    if c["link"]:
        v.update({"--sapLinkColor": c["link"], "--sapLink_Hover_Color": shade(c["link"], 0.8),
                  "--sapLink_Active_Color": shade(c["link"], 0.7)})
    if c["shell"]:
        v.update({"--sapShellColor": c["shell"], "--sapShell_Background": c["shell"],
                  "--sapShell_TextColor": c["shell_text"],
                  "--sapShell_InteractiveTextColor": c["shell_text"],
                  "--sapShell_Hover_Background": shade(c["shell"], 0.85)})
        if style["header"] == "brand":
            v.update({"--sapPageHeader_Background": c["shell"],
                      "--sapPageHeader_TextColor": c["shell_text"],
                      "--sapObjectHeader_Background": c["shell"],
                      "--sapObjectHeader_Title_TextColor": c["shell_text"],
                      "--sapObjectHeader_Subtitle_TextColor": c["shell_text"]})
    if c["background"]:
        v.update({"--sapBackgroundColor": c["background"],
                  "--sapShell_Background": c["shell"] or c["background"],
                  "--sapPageFooter_Background": c["background"]})
    if c["surface"]:
        v.update({"--sapGroup_ContentBackground": c["surface"], "--sapBaseColor": c["surface"],
                  "--sapList_Background": c["surface"], "--sapTile_Background": c["surface"],
                  "--sapObjectHeader_Background": v.get("--sapObjectHeader_Background", c["surface"])})
    if c["text"]:
        v.update({"--sapTextColor": c["text"], "--sapList_TextColor": c["text"],
                  "--sapField_TextColor": c["text"], "--sapContent_LabelColor": shade(c["text"], 1.35)
                  if luminance(c["text"]) < 0.3 else shade(c["text"], 0.75)})
    if c["title"]:
        v.update({"--sapTitleColor": c["title"], "--sapGroup_TitleTextColor": c["title"]})
    for role, names in (("positive", ("--sapPositiveColor", "--sapPositiveTextColor",
                                      "--sapSuccessColor", "--sapButton_Accept_Background")),
                        ("critical", ("--sapCriticalColor", "--sapCriticalTextColor", "--sapWarningColor")),
                        ("negative", ("--sapNegativeColor", "--sapNegativeTextColor", "--sapErrorColor",
                                      "--sapButton_Reject_Background"))):
        if c[role]:
            for n in names:
                v[n] = c[role]
    if style["radius"]:
        r = RADII[style["radius"]]
        for n in ("--sapButton_BorderCornerRadius", "--sapField_BorderCornerRadius",
                  "--sapElement_BorderCornerRadius", "--sapPopover_BorderCornerRadius",
                  "--sapTile_BorderCornerRadius", "--sapButton_Lite_BorderCornerRadius"):
            v[n] = r
    f = style["font"]
    body = _font_stack(f["family"], f["source"])
    head = _font_stack(f["heading_family"] or f["family"], f["source"])
    if body:
        for n in ("--sapFontFamily", "--sapFontLightFamily", "--sapFontSemiboldFamily",
                  "--sapFontSemiboldDuplexFamily"):
            v[n] = body
    if head:
        for n in ("--sapFontBoldFamily", "--sapFontBlackFamily", "--sapFontHeaderFamily"):
            v[n] = head
    v = {k: val for k, val in v.items() if val}

    gf = google_fonts_url(style)
    lines = ["/* Generated app style — overrides SAP theming parameters (CSS custom",
             "   properties) of the base theme '" + style["base_theme"] + "'.",
             "   " + (style.get("rationale") or "").replace("*/", "") + " */"]
    if gf:
        lines.append(f'@import url("{gf}");')
    lines.append("html:root {")
    lines += [f"    {k}: {val} !important;" for k, val in v.items()]
    lines.append("}")
    if c["background"]:
        lines += [".sapUiBody, .sapMShellBG, .sapFDynamicPageContentWrapper, .sapUxAPObjectPageWrapper {",
                  f"    background-color: {c['background']} !important;", "}"]
    if style["header"] == "brand" and c["shell"]:
        hdr = (".sapFDynamicPageTitle", ".sapFDynamicPageHeader", ".sapUxAPObjectPageHeaderTitle",
               ".sapUxAPObjectPageHeaderContent", ".sapSmartTemplatesListReportPage .sapFDynamicPageTitle")
        on_hdr = c["shell_text"]
        # The header's primary button must stand out from the header itself:
        # accent if it contrasts, else inverted (header-text background).
        cta = c["accent"] if c["accent"] and contrast(c["accent"], c["shell"]) >= 2.5 else on_hdr
        lines += [", ".join(hdr[:4]) + ", .sapUxAPAnchorBar {",
                  f"    background-color: {c['shell']} !important;", "}",
                  # every text-ish element inside the header follows the header text colour
                  ", ".join(f"{h} {t}" for h in hdr[:4] for t in (
                      ".sapMTitle", ".sapMLabel", ".sapMText", ".sapMObjectNumberText",
                      ".sapMObjectNumberUnit", ".sapMObjStatusText", ".sapMLnk",
                      ".sapMBtnTransparent .sapMBtnContent", ".sapMBtnTransparent .sapUiIcon",
                      ".sapMBtnGhost .sapMBtnContent", ".sapMBtnGhost .sapUiIcon"))
                  + ", .sapUxAPAnchorBar .sapMBtnContent {",
                  f"    color: {on_hdr} !important;", "}",
                  ", ".join(f"{h} .sapMBtnEmphasized" for h in hdr[:4]) + " {",
                  f"    background-color: {cta} !important; border-color: {cta} !important;", "}",
                  ", ".join(f"{h} .sapMBtnEmphasized .sapMBtnContent" for h in hdr[:4]) + " {",
                  f"    color: {on_color(cta)} !important;", "}",
                  # Ghost/Default buttons on the coloured header: outline style
                  ", ".join(f"{h} {b}" for h in hdr[:4] for b in (".sapMBtnGhost", ".sapMBtnDefault"))
                  + " {",
                  "    background-color: transparent !important;",
                  f"    border-color: {on_hdr} !important;", "}",
                  ", ".join(f"{h} {b}" for h in hdr[:4] for b in (
                      ".sapMBtnGhost .sapMBtnContent", ".sapMBtnDefault .sapMBtnContent",
                      ".sapMBtnGhost .sapUiIcon", ".sapMBtnDefault .sapUiIcon")) + " {",
                  f"    color: {on_hdr} !important;", "}",
                  # Object Page anchor bar (IconTabBar in current UI5)
                  ".sapUxAPObjectPageNavigation, .sapUxAPObjectPageNavigation .sapMITH,",
                  ".sapUxAPObjectPageNavigation .sapMITBHead {",
                  f"    background-color: {c['shell']} !important;", "}",
                  ".sapUxAPObjectPageNavigation .sapMITHTextContent, .sapUxAPObjectPageNavigation .sapMITBText,",
                  ".sapUxAPObjectPageNavigation .sapMITBFilterWrapper, .sapUxAPObjectPageNavigation .sapUiIcon {",
                  f"    color: {on_hdr} !important;", "}",
                  ".sapUxAPObjectPageNavigation .sapMITBSelected .sapMITBContentArrow,",
                  ".sapUxAPObjectPageNavigation .sapMITBSelected .sapMITHShadow {",
                  f"    background-color: {cta} !important; border-color: {cta} !important;", "}",
                  # collapse/pin toggles of DynamicPage / ObjectPage headers
                  ".sapFDynamicPageToggleHeaderIndicator, .sapFDynamicPageHeaderPinButton,",
                  ".sapUxAPHeaderTitleToggleIndicator {",
                  f"    background-color: {c['shell']} !important; color: {on_hdr} !important;",
                  f"    border-color: {on_hdr} !important;", "}"]
    if style["radius"] == "pill":
        # pill corners for buttons/fields, but keep checkboxes square-ish
        lines += [".sapMCbBg { border-radius: .25rem !important; }"]
    if body:
        lines += [".sapUiBody, .sapMText, .sapMLabel, .sapMInputBaseInner, .sapMBtnContent {",
                  f"    font-family: {body} !important;", "}"]
    if head:
        lines += [".sapMTitle, .sapUxAPObjectPageHeaderTitleText, .sapFDynamicPageTitle .sapMTitle,",
                  ".sapUxAPObjectPageSectionTitle, .sapMObjectIdentifierTitle {",
                  f"    font-family: {head} !important;", "}"]
    return "\n".join(lines) + "\n"
