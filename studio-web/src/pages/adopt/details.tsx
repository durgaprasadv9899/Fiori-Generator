// Everything the scan knows about an adopted application, in one switchable card: pages and screens (with
// their URL in the live preview and the files that render them), APIs, data model, integrations,
// dependencies and project structure.
import { useMemo, useState } from "react";
import { Button, Icon } from "@ui5/webcomponents-react";
import { StatusBadge } from "../../components/ui";
import type { AdoptProjectInfo, Analysis, UiPage } from "../../api/types";
import { EndpointsTable, EntityCards, LanguageBar } from "./parts";
import { Fact } from "./Questions";

export type DetailKey = "pages" | "apis" | "data" | "integrations" | "deps" | "structure";

const KIND_TEXT: Record<string, string> = { "spa-route": "App route", "server-page": "Server page", "ui5-app": "UI5 app", "static-page": "Static page" };

/** Pages from the UI map, or — for projects scanned before it existed — from the screens list. */
export function pagesOf(a: Analysis): UiPage[] {
  if (a.ui_map?.length) return a.ui_map;
  return (a.screens || []).map((s) => {
    const m = /(?:Route|Page|Server page)\s+(\/\S*)/.exec(s.type || "");
    return {
      url: m ? m[1] : "", screen: s.name, module: s.module, files: s.source ? [s.source] : [],
      kind: /server/i.test(s.type) ? "server-page" : /route|page \//i.test(s.type) ? "spa-route" : /fiori|ui5|list report|object page/i.test(s.type) ? "ui5-app" : "static-page",
    };
  });
}

export function detailCounts(a: Analysis) {
  return {
    pages: pagesOf(a).length, apis: a.endpoints?.length || 0, data: a.model?.entities?.length || 0,
    integrations: (a.destinations?.length || 0) + (a.env_vars?.length || 0),
    deps: a.dependencies?.length || 0, structure: a.modules?.length || 0,
  };
}

const SECTIONS: { key: DetailKey; text: string; icon: string }[] = [
  { key: "pages", text: "Pages & screens", icon: "screen-split-three" },
  { key: "apis", text: "APIs", icon: "chain-link" },
  { key: "data", text: "Data model", icon: "database" },
  { key: "integrations", text: "Integrations", icon: "connected" },
  { key: "deps", text: "Dependencies", icon: "course-book" },
  { key: "structure", text: "Structure", icon: "tree" },
];

export function AppDetails({ info, section, setSection, mayEdit, onAsk, onOpenPage }: {
  info: AdoptProjectInfo; section: DetailKey; setSection: (s: DetailKey) => void; mayEdit: boolean;
  onAsk: (request: string) => void; onOpenPage?: (url: string) => void;
}) {
  const a = info.analysis;
  const counts = detailCounts(a);
  return (
    <div className="ev-det">
      <div className="ev-det__tabs" role="tablist" aria-label="Application details">
        {SECTIONS.map((s) => (
          <button key={s.key} role="tab" aria-selected={section === s.key} className={"ev-det__tab" + (section === s.key ? " is-on" : "")}
            onClick={() => setSection(s.key)}>
            <Icon name={s.icon} /><span>{s.text}</span><em>{counts[s.key]}</em>
          </button>
        ))}
      </div>
      <div className="ev-det__body" key={section}>
        {section === "pages" && <PagesSection a={a} mayEdit={mayEdit} onAsk={onAsk} onOpenPage={onOpenPage} />}
        {section === "apis" && (a.endpoints?.length ? <EndpointsTable a={a} /> : <None text="No HTTP endpoints were recognised in the code." />)}
        {section === "data" && <DataSection a={a} mayEdit={mayEdit} onAsk={onAsk} />}
        {section === "integrations" && <IntegrationsSection info={info} />}
        {section === "deps" && <DepsSection a={a} />}
        {section === "structure" && <StructureSection a={a} />}
      </div>
    </div>
  );
}

function None({ text }: { text: string }) {
  return <div className="ev-det__none"><Icon name="hint" />{text}</div>;
}

function PagesSection({ a, mayEdit, onAsk, onOpenPage }: { a: Analysis; mayEdit: boolean; onAsk: (r: string) => void; onOpenPage?: (url: string) => void }) {
  const pages = pagesOf(a);
  if (!pages.length) return <None text="No pages or screens were recognised. Ask the AI to describe the UI, or open the live preview." />;
  return (
    <div className="ev-tblwrap">
      <table className="ev-tbl ev-pages">
        <thead><tr><th>Page</th><th>Address</th><th>Kind</th><th>Rendered by</th><th aria-label="Actions" /></tr></thead>
        <tbody>{pages.map((p, i) => (
          <tr key={i}>
            <td><b>{p.screen}</b><div className="ev-tbl__muted">{p.module}</div></td>
            <td>{p.url ? (onOpenPage
              ? <button className="ev-urlbtn" onClick={() => onOpenPage(p.url)} title="Open this page in the live preview"><Icon name="action" />{p.url}</button>
              : <span className="ev-mono">{p.url}</span>) : <span className="ev-tbl__muted">—</span>}</td>
            <td><StatusBadge tone={p.kind === "server-page" ? "info" : p.kind === "ui5-app" ? "good" : "brand"}>{KIND_TEXT[p.kind] || p.kind}</StatusBadge></td>
            <td><div className="ev-files2">{(p.files || []).slice(0, 4).map((f) => <span key={f} className="ev-chip2 ev-mono">{f}</span>)}
              {(p.files?.length || 0) > 4 && <span className="ev-tbl__muted">+{p.files.length - 4}</span>}</div></td>
            <td className="ev-pages__act">{mayEdit && <Button design="Transparent" icon="ai" onClick={() => onAsk(
              `Change the "${p.screen}" page${p.url ? ` (${p.url})` : ""}` +
              (p.files?.length ? ` — it is rendered by ${p.files.join(", ")}` : "") + ": ")}>Change with AI</Button>}</td>
          </tr>
        ))}</tbody>
      </table>
    </div>
  );
}

function DataSection({ a, mayEdit, onAsk }: { a: Analysis; mayEdit: boolean; onAsk: (r: string) => void }) {
  if (!a.model?.entities?.length) return <None text="No CDS model, OData metadata, ORM models or SQL tables were recognised." />;
  return (
    <>
      <div className="ev-muted ev-small" style={{ marginBottom: ".7rem" }}>
        {a.model.entities.length} entities from {a.model.source}.{mayEdit ? " Add a field below or ask the AI — you review the diff before anything is saved." : ""}
      </div>
      <EntityCards entities={a.model.entities} onAddField={mayEdit ? (e, f, t) => onAsk(
        `Add a field "${f}" (${t}) to the ${e} entity. Show it in the list and on the detail/object page, add the labels to i18n, ` +
        `include it in sample/seed data, and keep existing validations working.`) : undefined} />
      {a.model.services?.length > 0 && (
        <div className="ev-facts" style={{ marginTop: "1rem" }}>
          {a.model.services.map((sv) => (
            <Fact key={sv.name} label={sv.name}><span className="ev-mono">{sv.path || "—"}</span>
              {sv.actions?.length ? <span className="ev-muted"> · actions: {sv.actions.join(", ")}</span> : null}</Fact>
          ))}
        </div>
      )}
    </>
  );
}

function IntegrationsSection({ info }: { info: AdoptProjectInfo }) {
  const a = info.analysis;
  const related = info.source?.related || [];
  if (!a.destinations?.length && !a.env_vars?.length && !related.length) return <None text="No destinations, environment settings or connected apps were found." />;
  return (
    <div className="ev-det__stack">
      {a.destinations?.length > 0 && (
        <div className="ev-tblwrap">
          <table className="ev-tbl">
            <thead><tr><th>Destination</th><th>Used by</th><th>Purpose</th><th>Kind</th></tr></thead>
            <tbody>{a.destinations.map((d) => (
              <tr key={d.name}><td className="ev-mono"><b>{d.name}</b></td><td>{d.used_by}</td><td>{d.purpose}</td>
                <td><StatusBadge tone={d.internal ? "neutral" : "info"}>{d.internal ? "inside the project" : "external system"}</StatusBadge></td></tr>
            ))}</tbody>
          </table>
        </div>
      )}
      {related.length > 0 && (
        <Fact label="Connected apps on Cloud Foundry">
          <div className="ev-files2">{related.map((r) => <span key={r.app} className="ev-chip2"><Icon name="chain-link" />{r.app}<span className="ev-tbl__muted"> via {r.destination}</span></span>)}</div>
        </Fact>
      )}
      {a.env_vars?.length > 0 && (
        <Fact label="Environment settings">
          <div className="ev-files2">{a.env_vars.map((e) => <span key={e} className="ev-chip2 ev-mono">{e}</span>)}</div>
        </Fact>
      )}
    </div>
  );
}

function DepsSection({ a }: { a: Analysis }) {
  const [q, setQ] = useState("");
  const [dev, setDev] = useState(true);
  const deps = a.dependencies || [];
  const groups = useMemo(() => {
    const f = q.trim().toLowerCase();
    const out = new Map<string, typeof deps>();
    for (const d of deps) {
      if ((!dev && d.dev) || (f && !d.name.toLowerCase().includes(f))) continue;
      out.set(d.module || "project", [...(out.get(d.module || "project") || []), d]);
    }
    return [...out.entries()];
  }, [deps, q, dev]);
  if (!deps.length) return <None text="No package lists (package.json, requirements.txt, pom.xml) with dependencies were found — re-analyse the project to collect them." />;
  return (
    <>
      <div className="ev-det__filter">
        <span className="ev-det__search"><Icon name="search" /><input aria-label="Filter dependencies" placeholder="Filter packages" value={q} onChange={(e) => setQ(e.target.value)} /></span>
        <label className="ev-check"><input type="checkbox" checked={dev} onChange={(e) => setDev(e.target.checked)} />include development tools</label>
        <span className="ev-spacer" /><span className="ev-muted ev-small">{deps.length} packages</span>
      </div>
      {groups.length === 0 ? <None text="No package matches the filter." /> : (
        <div className="ev-deps">
          {groups.map(([mod, list]) => (
            <div key={mod} className="ev-deps__grp">
              <div className="ev-deps__h"><Icon name="dimension" />{mod}<em>{list.length}</em></div>
              <ul>{list.map((d) => (
                <li key={d.name + d.version}><span className="ev-mono">{d.name}</span>
                  <span className="ev-deps__v">{d.dev && <StatusBadge tone="neutral">dev</StatusBadge>}<code>{d.version || "*"}</code></span></li>
              ))}</ul>
            </div>
          ))}
        </div>
      )}
    </>
  );
}

function StructureSection({ a }: { a: Analysis }) {
  const folders = a.folders || [];
  const max = Math.max(1, ...folders.map((f) => f.lines || f.files));
  const outs = a.build_outputs || [];
  const cmds = a.modules.filter((m) => Object.values(m.commands || {}).some(Boolean));
  return (
    <div className="ev-det__stack">
      <div>
        <div className="ev-det__h">Languages</div>
        <LanguageBar languages={a.languages} />
      </div>
      {folders.length > 0 && (
        <div>
          <div className="ev-det__h">Folders</div>
          <div className="ev-folders">
            {folders.map((f) => (
              <div key={f.path} className="ev-folders__row">
                <span className="ev-mono"><Icon name="folder-blank" />{f.path || "/"}</span>
                <span className="ev-folders__bar"><i style={{ width: `${Math.max(3, ((f.lines || f.files) / max) * 100)}%` }} /></span>
                <span className="ev-tbl__muted">{f.files} files{f.lines ? ` · ${f.lines.toLocaleString()} lines` : ""}</span>
              </div>
            ))}
          </div>
        </div>
      )}
      {outs.length > 0 && (
        <div>
          <div className="ev-det__h">Build output</div>
          <div className="ev-finds">
            {outs.map((o) => (
              <div key={o.module + o.out_dir} className={`ev-find ev-find--${o.committed ? "info" : "info"}`}>
                <Icon name="product" />
                <div><b>{o.module}</b> builds into <span className="ev-mono">{o.out_dir}</span>
                  {o.committed
                    ? <small><StatusBadge tone="brand" icon="synchronize">committed bundle</StatusBadge> The built files are in the repository and served as they are — the studio rebuilds them after every AI change, so the deployed UI matches the source.</small>
                    : <small>Built during deployment; not stored in the repository.</small>}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
      {cmds.length > 0 && (
        <div>
          <div className="ev-det__h">Commands per module</div>
          <div className="ev-tblwrap">
            <table className="ev-tbl ev-cmds">
              <thead><tr><th>Module</th><th>Install</th><th>Build</th><th>Test</th><th>Start</th></tr></thead>
              <tbody>{cmds.map((m) => (
                <tr key={m.id}><td><b>{m.name}</b><div className="ev-tbl__muted">{m.framework}</div></td>
                  {(["install", "build", "test", "run"] as const).map((k) => <td key={k}>{m.commands[k] ? <code>{m.commands[k]}</code> : <span className="ev-tbl__muted">—</span>}</td>)}</tr>
              ))}</tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
