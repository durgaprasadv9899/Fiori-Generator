"""
ui5_common.py — shared pieces for the blueprint renderers.

RenderContext bundles the parsed model, the validated blueprint and the
deployment facts; the helpers emit files every generated app needs (index.html,
i18n, local mock server config, package.json) and small XML/annotation builders
used by both the Fiori Elements and Overview Page renderers.
"""

from __future__ import annotations
import json
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from xml.sax.saxutils import escape, quoteattr


def xattr(v) -> str:
    """Quoted, escaped XML attribute value."""
    return quoteattr(str(v))


def xtext(v) -> str:
    return escape(str(v))


def i18n_key(*parts: str) -> str:
    return "_".join(re.sub(r"[^A-Za-z0-9]", "", p) for p in parts if p)


def humanize(name: str) -> str:
    s = re.sub(r"^[A-Za-z]_", "", name or "")
    s = re.sub(r"(Set|Collection)$", "", s).replace("_", " ")
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:1].upper() + s[1:] if s else name


@dataclass
class RenderContext:
    model: Dict
    bp: Dict
    app_id: str
    service_url: str                       # path the DEPLOYED app calls, e.g. /sap/opu/odata/sap/X/
    edmx: str = ""                         # raw metadata (for localService/metadata.xml)
    theme: str = "sap_horizon"
    i18n: Dict[str, str] = field(default_factory=dict)   # key -> English text
    logo: str = ""                         # data: URI of an uploaded logo (optional)
    notes: List[str] = field(default_factory=list)       # render-time notes for the user

    # ── model access ─────────────────────────────────────────────────────────
    @property
    def v4(self) -> bool:
        return self.model["odata_version"].startswith("4")

    @property
    def ns(self) -> str:
        return self.model.get("namespace", "")

    def es(self, name: Optional[str] = None) -> Dict:
        name = name or self.bp["main"]["entity_set"]
        return next(s for s in self.model["entity_sets"] if s["name"] == name)

    def prop(self, field_name: str, es_name: Optional[str] = None) -> Dict:
        return next((p for p in self.es(es_name)["properties"] if p["name"] == field_name),
                    {"name": field_name, "type": "Edm.String", "ui_type": "text", "label": field_name})

    def label(self, field_name: str, es_name: Optional[str] = None) -> str:
        return self.prop(field_name, es_name).get("label") or humanize(field_name)

    def type_fqn(self, es_name: Optional[str] = None) -> str:
        es = self.es(es_name)
        return f"{es.get('namespace') or self.ns}.{es['entity_type']}"

    def op(self, name: str) -> Dict:
        ops = [o for o in self.model.get("operations", []) if o["name"] == name]
        if len(ops) > 1:          # multi-service app: prefer the page's own service
            svc = self.es().get("service")
            ops = [o for o in ops if o.get("service") == svc] or ops
        return ops[0] if ops else next(o for o in [] if o)

    @property
    def service_path(self) -> str:
        p = "/" + self.service_url.strip().strip("/")
        return p + "/"

    @property
    def app_path(self) -> str:
        return self.app_id.replace(".", "/")

    # ── i18n ─────────────────────────────────────────────────────────────────
    def t(self, key: str, text: str) -> str:
        """Register an i18n text and return the key."""
        self.i18n.setdefault(key, text)
        return key


# ─────────────────────────────────────────────────────────────────────────────
# Common files
# ─────────────────────────────────────────────────────────────────────────────
def index_html(ctx: RenderContext, libs: str = "sap.m") -> str:
    roots = json.dumps({ctx.app_id: "./"})
    settings = json.dumps({"id": ctx.app_id})
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{xtext(ctx.bp['app']['title'])}</title>
    <script id="sap-ui-bootstrap"
        src="https://ui5.sap.com/resources/sap-ui-core.js"
        data-sap-ui-theme="{ctx.theme}"
        data-sap-ui-libs="{libs}"
        data-sap-ui-resource-roots='{roots}'
        data-sap-ui-on-init="module:sap/ui/core/ComponentSupport"
        data-sap-ui-compat-version="edge"
        data-sap-ui-async="true"
        data-sap-ui-frame-options="trusted">
    </script>
    <style>html, body, body > div {{ height: 100%; margin: 0; }}</style>
</head>
<body class="sapUiBody {'sapUiSizeCozy' if (ctx.bp.get('style') or {}).get('density') == 'cozy' else 'sapUiSizeCompact'}" id="content">
    <div data-sap-ui-component data-name="{ctx.app_id}" data-id="container"
         data-settings='{settings}' data-height="100%"></div>
</body>
</html>
"""


def i18n_model(ctx: "RenderContext") -> Dict:
    """ResourceModel config: i18n.properties is the English default bundle;
    i18n_<lang>.properties exist for the other blueprint languages."""
    others = [l for l in ctx.bp["app"]["languages"] if l != "en"]
    # bundleName (module path), not bundleUrl: a bundleUrl is resolved against
    # the hosting PAGE, so the app breaks when started from test/flpSandbox.html.
    return {"type": "sap.ui.model.resource.ResourceModel",
            "settings": {"bundleName": f"{ctx.app_id}.i18n.i18n",
                         "supportedLocales": [""] + others, "fallbackLocale": ""}}


def sap_app_i18n(ctx: "RenderContext") -> Dict:
    others = [l for l in ctx.bp["app"]["languages"] if l != "en"]
    return {"bundleName": f"{ctx.app_id}.i18n.i18n", "supportedLocales": [""] + others,
            "fallbackLocale": ""}


def flp_sandbox(ctx: "RenderContext", libs: str) -> str:
    """test/flpSandbox.html — runs the app inside a local Fiori launchpad shell
    (navigation, shell services, personalization) instead of standalone."""
    cfg = json.dumps({
        "defaultRenderer": "fiori2",
        "applications": {"app-display": {
            "title": ctx.bp["app"]["title"], "description": ctx.bp["app"]["description"][:80],
            "additionalInformation": f"SAPUI5.Component={ctx.app_id}",
            "applicationType": "URL", "url": "../"}},
    }, indent=4)
    roots = json.dumps({ctx.app_id: "../"})
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{xtext(ctx.bp['app']['title'])} — launchpad sandbox</title>
    <script>window["sap-ushell-config"] = {cfg};</script>
    <script id="sap-ushell-bootstrap" src="https://ui5.sap.com/test-resources/sap/ushell/bootstrap/sandbox.js"></script>
    <script id="sap-ui-bootstrap"
        src="https://ui5.sap.com/resources/sap-ui-core.js"
        data-sap-ui-libs="sap.m,sap.ushell,{libs}"
        data-sap-ui-theme="{ctx.theme}"
        data-sap-ui-resource-roots='{roots}'
        data-sap-ui-compat-version="edge"
        data-sap-ui-async="true"
        data-sap-ui-frame-options="allow">
    </script>
    <script>
        sap.ui.require(["sap/ui/core/Core"], function (Core) {{
            Core.ready().then(function () {{
                sap.ushell.Container.createRenderer("fiori2", true).then(function (oRenderer) {{
                    oRenderer.placeAt("content");
                    if (!window.location.hash) {{ window.location.hash = "#app-display"; }}
                }});
            }});
        }});
    </script>
</head>
<body class="sapUiBody" id="content"></body>
</html>
"""


def i18n_properties(entries: Dict[str, str]) -> str:
    def esc(v: str) -> str:
        v = str(v).replace("\\", "\\\\").replace("\n", "\\n")
        # .properties are ISO-8859-1 by spec; UI5 reads UTF-8, but escape to be safe
        return "".join(c if ord(c) < 128 else "\\u%04x" % ord(c) for c in v)
    return "".join(f"{k}={esc(v)}\n" for k, v in entries.items())


def mock_yaml(ctx: RenderContext) -> str:
    """ui5-mock.yaml: serves the app with SAP's fe-mockserver, which fabricates
    data from localService/metadata.xml — `npm run start-mock` runs the app with
    no backend at all (V2 and V4)."""
    path = ctx.service_path.rstrip("/")
    return f"""# Local preview with generated mock data — no backend needed.
#   npm install && npm run start-mock
specVersion: "3.0"
metadata:
  name: {ctx.app_id}
type: application
server:
  customMiddleware:
    - name: sap-fe-mockserver
      beforeMiddleware: csp
      configuration:
        mountPath: /
        services:
          - urlPath: {path}
            metadataPath: ./webapp/localService/metadata.xml
            mockdataPath: ./webapp/localService/data
            generateMockData: true
        annotations: []
"""


def package_json(ctx: RenderContext) -> str:
    return json.dumps({
        "name": ctx.app_id.replace(".", "-").lower(),
        "version": "0.0.1",
        "description": ctx.bp["app"]["title"],
        "private": True,
        "dependencies": {"@sap/approuter": "^16.0.0"},
        "devDependencies": {
            "@ui5/cli": "^4.0.0",
            "@sap-ux/ui5-middleware-fe-mockserver": "^2.2.0",
            "@sap/ux-ui5-tooling": "^1.14.0",
        },
        "scripts": {
            # `start` is what the CF Node buildpack runs (approuter).
            "start": "node node_modules/@sap/approuter/approuter.js",
            "start-mock": "ui5 serve --config ui5-mock.yaml --open index.html",
            "start-local": "ui5 serve --config ui5.yaml --open index.html",
        },
        "engines": {"node": ">=20"},
    }, indent=2)


def readme(ctx: RenderContext, extra: str = "") -> str:
    bp = ctx.bp
    caps = ", ".join(k for k, v in bp["capabilities"].items() if v) or "read-only"
    return f"""# {bp['app']['title']}

{bp['app']['description']}

Generated by the Evoke Fiori App Generator.

| | |
|---|---|
| Floorplan | {bp['floorplan']} |
| OData | V{ctx.model['odata_version'][0]} `{ctx.service_path}` |
| Main entity | `{bp['main']['entity_set']}` |
| Capabilities | {caps} |
| Actions | {', '.join(a['label'] for a in bp['actions']) or '-'} |
| Languages | {', '.join(bp['app']['languages'])} |

## Run locally

```bash
npm install
npm run start-mock     # mock data generated from the service metadata
npm run start-local    # real backend via fiori-tools-proxy (fill in .env first)
```

## Deploy to SAP BTP

```bash
mbt build --mtar app.mtar
cf deploy mta_archives/app.mtar -e deploy.mtaext
```
{extra}"""


# ─────────────────────────────────────────────────────────────────────────────
# Annotation XML builders (OData V4 CSDL annotation format — used for both
# V2 and V4 services by Fiori Elements / OVP local annotation files)
# ─────────────────────────────────────────────────────────────────────────────
VOCABS = [
    ("https://sap.github.io/odata-vocabularies/vocabularies/UI.xml", "com.sap.vocabularies.UI.v1", "UI"),
    ("https://sap.github.io/odata-vocabularies/vocabularies/Common.xml", "com.sap.vocabularies.Common.v1", "Common"),
    ("https://oasis-tcs.github.io/odata-vocabularies/vocabularies/Org.OData.Capabilities.V1.xml",
     "Org.OData.Capabilities.V1", "Capabilities"),
    ("https://oasis-tcs.github.io/odata-vocabularies/vocabularies/Org.OData.Core.V1.xml",
     "Org.OData.Core.V1", "Core"),
]


def annotations_document(ctx: RenderContext, body: str) -> str:
    refs = "".join(
        f'  <edmx:Reference Uri="{u}">\n    <edmx:Include Namespace="{ns}" Alias="{a}"/>\n  </edmx:Reference>\n'
        for u, ns, a in VOCABS)
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<edmx:Edmx xmlns:edmx="http://docs.oasis-open.org/odata/ns/edmx" Version="4.0">\n'
        + refs +
        f'  <edmx:Reference Uri="{xtext(ctx.service_path)}$metadata">\n'
        f'    <edmx:Include Namespace="{ctx.ns}"/>\n'
        '  </edmx:Reference>\n'
        '  <edmx:DataServices>\n'
        '    <Schema xmlns="http://docs.oasis-open.org/odata/ns/edm" Namespace="local">\n'
        + body +
        '    </Schema>\n'
        '  </edmx:DataServices>\n'
        '</edmx:Edmx>\n'
    )


def data_field(path: str, label: str = "", importance: str = "", criticality: str = "") -> str:
    out = '          <Record Type="UI.DataField">\n'
    out += f'            <PropertyValue Property="Value" Path={xattr(path)}/>\n'
    if label:
        out += f'            <PropertyValue Property="Label" String={xattr(label)}/>\n'
    if criticality:
        out += criticality
    if importance:
        out += (f'            <Annotation Term="UI.Importance" '
                f'EnumMember="UI.ImportanceType/{importance}"/>\n')
    out += '          </Record>\n'
    return out


def data_field_for_action(action: str, label: str, inline: bool = False) -> str:
    return ('          <Record Type="UI.DataFieldForAction">\n'
            f'            <PropertyValue Property="Label" String={xattr(label)}/>\n'
            f'            <PropertyValue Property="Action" String={xattr(action)}/>\n'
            + ('            <PropertyValue Property="Inline" Bool="true"/>\n' if inline else '') +
            '          </Record>\n')


def collection(term: str, records: str, qualifier: str = "") -> str:
    q = f' Qualifier="{qualifier}"' if qualifier else ""
    return (f'        <Annotation Term="{term}"{q}>\n'
            '          <Collection>\n' + records + '          </Collection>\n'
            '        </Annotation>\n')


def annotations_block(target: str, inner: str) -> str:
    return f'      <Annotations Target={xattr(target)}>\n{inner}      </Annotations>\n'
