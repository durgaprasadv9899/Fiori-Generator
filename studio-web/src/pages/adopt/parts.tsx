// Building blocks for adopted projects: stack, architecture, modules + capability matrix, findings,
// understanding, languages, data model, screens / endpoints, diff and check results.
import React, { Fragment, useMemo, useState } from "react";
import { Icon } from "@ui5/webcomponents-react";
import { StatusBadge } from "../../components/ui";
import type { AdoptModule, Analysis, ArchNode, Cap, Finding, ModelEntity, Understanding, ValidationReport } from "../../api/types";
import { Fact } from "./Questions";
import "./adopt.css";

export const LEVEL: Record<string, string> = { full: "Full studio", code: "Code + preview", config: "Config" };
const CAPS: [Cap, string][] = [["understand", "Understand"], ["preview", "Preview"], ["designers", "Designers"],
  ["ai_change", "AI changes"], ["validate", "Validate"], ["deploy", "Deploy"]];

export function LevelPill({ level }: { level: string }) {
  return <span className={`ev-level ev-level--${level}`}>{LEVEL[level] || level}</span>;
}

export function StackChips({ stack }: { stack: string[] }) {
  return <div className="ev-chiprow">{stack.map((s) => <span key={s} className="ev-chip2">{s}</span>)}</div>;
}

export function ArchDiagram({ arch }: { arch: ArchNode[][] }) {
  if (!arch?.length) return null;
  return (
    <div className="ev-arch" role="img" aria-label="How the project fits together">
      {arch.map((col, i) => (
        <Fragment key={i}>
          {i > 0 && <Icon name="navigation-right-arrow" className="ev-arch__arrow" />}
          <div className="ev-arch__col">
            {col.map((n) => (
              <div key={n.id} className={"ev-arch__node" + (n.external ? " is-ext" : "")} title={n.external ? "External system" : undefined}>
                {n.label}<small>{n.sub}</small>
              </div>
            ))}
          </div>
        </Fragment>
      ))}
    </div>
  );
}

export function ModuleCards({ modules }: { modules: AdoptModule[] }) {
  return (
    <div className="ev-tblwrap">
      <table className="ev-tbl">
        <thead><tr><th>Module</th><th>Framework</th><th>Folder</th><th>Support</th><th>Details</th></tr></thead>
        <tbody>{modules.map((m) => (
          <tr key={m.id}>
            <td><b>{m.name}</b></td><td>{m.framework}</td><td className="ev-mono">{m.path || "/"}</td>
            <td><LevelPill level={m.level} /></td><td className="ev-tbl__muted">{m.evidence}</td>
          </tr>
        ))}</tbody>
      </table>
    </div>
  );
}

export function CapMatrix({ modules }: { modules: AdoptModule[] }) {
  const cell = (v?: string) => v === "y" ? <span className="ev-cap-y" title="Supported">●</span>
    : v === "p" ? <span className="ev-cap-p" title="Partly">◐</span> : <span className="ev-cap-n" title="Not applicable">–</span>;
  return (
    <>
      <div className="ev-card ev-capt">
        <table className="ev-dtable">
          <thead><tr><th>Module</th>{CAPS.map(([, t]) => <th key={t} className="c">{t}</th>)}</tr></thead>
          <tbody>{modules.map((m) => (
            <tr key={m.id}><td><b>{m.name}</b></td>{CAPS.map(([k]) => <td key={k} className="c">{cell(m.caps?.[k])}</td>)}</tr>
          ))}</tbody>
        </table>
      </div>
      <div className="ev-legend">
        <span><span className="ev-cap-y">●</span> supported</span>
        <span><span className="ev-cap-p">◐</span> partly — changed by AI with a reviewed diff, no drag-and-drop designer</span>
        <span><span className="ev-cap-n">–</span> not applicable</span>
      </div>
    </>
  );
}

const FIND_ICON = { bad: "error", warn: "alert", info: "message-information" };
export function Findings({ findings }: { findings: Finding[] }) {
  if (!findings?.length) return <div className="ev-muted ev-small">Nothing needs attention.</div>;
  const order = { bad: 0, warn: 1, info: 2 };
  return (
    <div className="ev-finds">
      {[...findings].sort((a, b) => order[a.severity] - order[b.severity]).map((f, i) => (
        <div key={i} className={`ev-find ev-find--${f.severity}`}>
          <Icon name={FIND_ICON[f.severity] || "message-information"} />
          <div>{f.text}{f.fix && <small>{f.fix}</small>}</div>
        </div>
      ))}
    </div>
  );
}

export function UnderstandingBlock({ u }: { u?: Understanding }) {
  if (!u || !(u.summary || u.business_objects?.length || u.processes?.length)) return null;
  const list = (items?: string[]) => <ul>{(items || []).map((x) => <li key={x}>{x}</li>)}</ul>;
  return (
    <div className="ev-facts">
      {u.summary && <Fact label="Summary"><span className="ev-summary">{u.summary}</span></Fact>}
      {!!u.business_objects?.length && <Fact label="Business objects">
        <div className="ev-chiprow">{u.business_objects.map((x) => <span key={x} className="ev-chip2">{x}</span>)}</div></Fact>}
      {!!u.roles?.length && <Fact label="Roles">{list(u.roles)}</Fact>}
      {!!u.processes?.length && <Fact label="Processes">{list(u.processes)}</Fact>}
      {!!u.integrations?.length && <Fact label="Integrations">{list(u.integrations)}</Fact>}
      {!!u.risks?.length && <Fact label="Risks">{list(u.risks)}</Fact>}
    </div>
  );
}

const LANG_COLORS = ["var(--sapChart_OrderedColor_1)", "var(--sapChart_OrderedColor_2)", "var(--sapChart_OrderedColor_3)",
  "var(--sapChart_OrderedColor_4)", "var(--sapChart_OrderedColor_5)", "var(--sapChart_OrderedColor_6)",
  "var(--sapChart_OrderedColor_7)", "var(--sapChart_OrderedColor_8)"];
export function LanguageBar({ languages }: { languages: Record<string, number> }) {
  const entries = Object.entries(languages || {});
  const total = entries.reduce((s, [, n]) => s + n, 0);
  if (!total) return null;
  return (
    <div>
      <div className="ev-lang" role="img" aria-label="Lines of code per language">
        {entries.map(([l, n], i) => <span key={l} style={{ width: `${(n / total) * 100}%`, background: LANG_COLORS[i % 8] }} title={`${l}: ${n} lines`} />)}
      </div>
      <div className="ev-lang-legend">
        {entries.map(([l, n], i) => <span key={l}><i style={{ background: LANG_COLORS[i % 8] }} />{l} {Math.round((n / total) * 100)}%</span>)}
      </div>
    </div>
  );
}

export function EntityCards({ entities, onAddField }: { entities: ModelEntity[]; onAddField?: (entity: string, field: string, type: string) => void }) {
  return (
    <div className="ev-ents">
      {entities.map((e) => <EntityCard key={e.name} e={e} onAddField={onAddField} />)}
    </div>
  );
}

const FIELD_TYPES = ["String", "Integer", "Decimal", "Boolean", "Date", "Timestamp", "UUID", "Association"];
function EntityCard({ e, onAddField }: { e: ModelEntity; onAddField?: (entity: string, field: string, type: string) => void }) {
  const [name, setName] = useState("");
  const [type, setType] = useState("String");
  const [more, setMore] = useState(false);
  const fields = more ? e.fields : e.fields.slice(0, 12);
  return (
    <div className="ev-ent">
      <div className="ev-ent__h"><Icon name="table-view" />{e.name}<span className="ev-muted ev-small" style={{ marginLeft: "auto", fontWeight: 500 }}>{e.fields.length}</span></div>
      <ul>
        {fields.map((f) => (
          <li key={f.name} className={f.key ? "is-key" : ""}>
            <span>{f.key && <Icon name="key" style={{ width: ".7rem", height: ".7rem", marginRight: ".25rem" }} />}{f.name}</span><em>{f.type}</em>
          </li>
        ))}
        {e.fields.length > 12 && <li><button className="ev-linkbtn" onClick={() => setMore(!more)}>{more ? "Show less" : `+ ${e.fields.length - 12} more`}</button></li>}
      </ul>
      {onAddField && (
        <div className="ev-ent__add">
          <input aria-label={`New field for ${e.name}`} placeholder="new field" value={name} onChange={(ev) => setName(ev.target.value.replace(/[^\w]/g, ""))} />
          <select aria-label="Type" value={type} onChange={(ev) => setType(ev.target.value)}>{FIELD_TYPES.map((t) => <option key={t}>{t}</option>)}</select>
          <button className="ev-linkbtn" disabled={!name} onClick={() => { onAddField(e.name, name, type); setName(""); }}>Ask AI</button>
        </div>
      )}
    </div>
  );
}

export function ScreensTable({ a }: { a: Analysis }) {
  return a.screens?.length ? (
    <div className="ev-card" style={{ overflow: "auto" }}>
      <table className="ev-dtable">
        <thead><tr><th>Screen</th><th>Type</th><th>Module</th><th>Source</th><th>Editing</th></tr></thead>
        <tbody>{a.screens.map((s, i) => (
          <tr key={i}><td><b>{s.name}</b></td><td>{s.type}</td><td>{s.module}</td><td className="ev-mono">{s.source}</td>
            <td>{s.editing === "visual" ? <StatusBadge tone="good">Visual + AI</StatusBadge> : <StatusBadge tone="brand">AI + diff</StatusBadge>}</td></tr>
        ))}</tbody>
      </table>
    </div>
  ) : <div className="ev-muted ev-small">No screens were recognised.</div>;
}

export function EndpointsTable({ a }: { a: Analysis }) {
  return a.endpoints?.length ? (
    <div className="ev-card" style={{ overflow: "auto" }}>
      <table className="ev-dtable">
        <thead><tr><th>Method</th><th>Path</th><th>Module</th><th>Source</th></tr></thead>
        <tbody>{a.endpoints.map((e, i) => (
          <tr key={i}><td><StatusBadge tone={e.method === "GET" ? "info" : e.method === "DELETE" ? "bad" : "brand"}>{e.method}</StatusBadge></td>
            <td className="ev-mono">{e.path}</td><td>{e.module}</td><td className="ev-mono">{e.source}</td></tr>
        ))}</tbody>
      </table>
    </div>
  ) : null;
}

// ── diff ──────────────────────────────────────────────────────────────────────
interface DiffFile { path: string; lines: string[]; add: number; del: number }
export function parseDiff(diff: string): DiffFile[] {
  const files: DiffFile[] = [];
  let cur: DiffFile | null = null;
  const lines = (diff || "").split("\n");
  for (let i = 0; i < lines.length; i++) {
    const l = lines[i];
    if (l.startsWith("diff --git")) {
      const m = l.match(/ b\/(.+)$/);
      cur = { path: m ? m[1] : l.slice(11), lines: [], add: 0, del: 0 };
      files.push(cur);
      continue;
    }
    if (l.startsWith("--- ") && (lines[i + 1] || "").startsWith("+++ ")) {
      const a = l.slice(4).replace(/^a\//, "").split("\t")[0];
      const b = lines[i + 1].slice(4).replace(/^b\//, "").split("\t")[0];
      const path = b === "/dev/null" ? a : b;
      if (!cur || cur.lines.length || cur.path !== path) { cur = { path, lines: [], add: 0, del: 0 }; files.push(cur); }
      i++;
      continue;
    }
    if (!cur) { cur = { path: "changes", lines: [], add: 0, del: 0 }; files.push(cur); }
    if (/^(index |new file mode|deleted file mode|similarity index|rename )/.test(l)) continue;
    cur.lines.push(l);
    if (l.startsWith("+")) cur.add++;
    else if (l.startsWith("-")) cur.del++;
  }
  return files.filter((f) => f.lines.some((x) => x.trim()));
}

export function DiffView({ diff }: { diff: string }) {
  const files = useMemo(() => parseDiff(diff), [diff]);
  const [closed, setClosed] = useState<Record<string, boolean>>({});
  if (!files.length) return <div className="ev-muted ev-small">No changes.</div>;
  return (
    <div>
      {files.map((f) => (
        <div key={f.path} className="ev-diff">
          <button className="ev-diff__head" onClick={() => setClosed((c) => ({ ...c, [f.path]: !c[f.path] }))} aria-expanded={!closed[f.path]}>
            <Icon name={closed[f.path] ? "slim-arrow-right" : "slim-arrow-down"} />{f.path}<span className="ev-spacer" />
            <span className="ev-add">+{f.add}</span><span className="ev-del">−{f.del}</span>
          </button>
          {!closed[f.path] && (
            <pre>{f.lines.map((l, i) => (
              <span key={i} className={l.startsWith("@@") ? "d-hunk" : l.startsWith("+") ? "d-add" : l.startsWith("-") ? "d-del" : ""}>{l || " "}</span>
            ))}</pre>
          )}
        </div>
      ))}
    </div>
  );
}

// ── checks ────────────────────────────────────────────────────────────────────
export function ChecksSummary({ report }: { report: ValidationReport | null }) {
  if (!report) return <div className="ev-muted ev-small">Checks have not run yet.</div>;
  const s = report.summary;
  const failed = report.checks.filter((c) => c.status === "failed");
  const skipped = report.checks.filter((c) => c.status === "not_tested");
  return (
    <div className="ev-checks2">
      <div className="ev-row">
        <StatusBadge tone="good" icon="accept">{s.passed} passed</StatusBadge>
        {s.failed > 0 && <StatusBadge tone="bad" icon="error">{s.failed} failed</StatusBadge>}
        {s.warnings > 0 && <StatusBadge tone="warn" icon="alert">{s.warnings} warnings</StatusBadge>}
        {s.not_tested > 0 && <StatusBadge tone="neutral">{s.not_tested} not tested</StatusBadge>}
        {report.blocking?.length > 0 && <StatusBadge tone="bad">deploy blocked</StatusBadge>}
      </div>
      <div className="ev-row">
        {report.checks.filter((c) => c.status === "passed").map((c) => <span key={c.id} className="ev-chip2"><Icon name="accept" style={{ width: ".7rem", height: ".7rem", color: "var(--sapPositiveColor)" }} />{c.name}</span>)}
      </div>
      {failed.map((c) => (
        <div key={c.id} className="ev-checks2__fail"><b>{c.name}</b>{c.detail && <pre>{c.detail}</pre>}</div>
      ))}
      {skipped.length > 0 && <div className="ev-muted ev-small">Not tested: {skipped.map((c) => `${c.name}${c.detail ? ` (${c.detail.split("\n")[0]})` : ""}`).join(" · ")}</div>}
    </div>
  );
}

export function fmtBytes(n: number): string {
  if (!n) return "0 B";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

/** Minimal Markdown (headings, lists, bold, inline code, code blocks) for AI summaries. React escapes all text. */
export function Markdown({ text }: { text: string }) {
  const inline = (s: string, k: string) => s.split(/(`[^`]+`|\*\*[^*]+\*\*)/g).map((p, i) =>
    p.startsWith("`") && p.endsWith("`") && p.length > 1 ? <code key={k + i}>{p.slice(1, -1)}</code>
      : p.startsWith("**") && p.endsWith("**") && p.length > 3 ? <b key={k + i}>{p.slice(2, -2)}</b> : <Fragment key={k + i}>{p}</Fragment>);
  const out: React.ReactElement[] = [];
  const lines = (text || "").replace(/\r\n/g, "\n").split("\n");
  let list: { ordered: boolean; items: string[] } | null = null;
  const flush = () => {
    if (!list) return;
    const items = list.items.map((it, i) => <li key={i}>{inline(it, `l${out.length}-${i}-`)}</li>);
    out.push(list.ordered ? <ol key={out.length}>{items}</ol> : <ul key={out.length}>{items}</ul>);
    list = null;
  };
  for (let i = 0; i < lines.length; i++) {
    const l = lines[i];
    if (l.trim().startsWith("```")) {
      flush();
      const code: string[] = [];
      for (i++; i < lines.length && !lines[i].trim().startsWith("```"); i++) code.push(lines[i]);
      out.push(<pre key={out.length} className="ev-md__pre">{code.join("\n")}</pre>);
      continue;
    }
    const h = /^(#{1,4})\s+(.*)$/.exec(l);
    const li = /^\s*(?:([-*])|(\d+)\.)\s+(.*)$/.exec(l);
    if (h) { flush(); out.push(<div key={out.length} className={`ev-md__h ev-md__h${h[1].length}`}>{inline(h[2], `h${i}`)}</div>); }
    else if (li) {
      const ordered = !!li[2];
      if (!list || list.ordered !== ordered) { flush(); list = { ordered, items: [] }; }
      list.items.push(li[3]);
    } else if (!l.trim()) flush();
    else { flush(); out.push(<p key={out.length}>{inline(l, `p${i}`)}</p>); }
  }
  flush();
  return <div className="ev-md">{out}</div>;
}
