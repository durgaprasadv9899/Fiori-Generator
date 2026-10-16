// Compare two versions: design changes, file chips, unified diff per file (collapsible, filterable).
import { useMemo, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { useNavigate, useParams } from "react-router-dom";
import { Button, Icon, Input, MessageStrip, Select, Option } from "@ui5/webcomponents-react";
import { Card, EmptyState, PageHeader, Skeleton, StatusBadge } from "../../components/ui";
import { useApi } from "../../components/shell/useApi";
import "./projects.css";

interface Cmp { a: any; b: any; added: string[]; removed: string[]; changed: string[]; diff: string; blueprint_changes: string[] }

/** Split a unified diff ("--- a/x\n+++ b/x\n@@ …") into per-file chunks. */
function splitDiff(diff: string): { file: string; lines: string[]; add: number; del: number }[] {
  const out: { file: string; lines: string[]; add: number; del: number }[] = [];
  let cur: { file: string; lines: string[]; add: number; del: number } | null = null;
  for (const l of (diff || "").split("\n")) {
    if (l.startsWith("--- ")) {
      cur = { file: l.slice(4).replace(/^(a|v\d+)\//, "").replace(/\t.*$/, ""), lines: [], add: 0, del: 0 };
      out.push(cur);
      continue;
    }
    if (l.startsWith("+++ ")) {
      if (cur) { const f = l.slice(4).replace(/^(b|v\d+)\//, "").replace(/\t.*$/, ""); if (f && f !== "/dev/null") cur.file = f; }
      continue;
    }
    if (!cur) continue;
    cur.lines.push(l);
    if (l.startsWith("+")) cur.add++; else if (l.startsWith("-")) cur.del++;
  }
  return out;
}

export default function ComparePage() {
  const { a = "", b = "" } = useParams();
  const nav = useNavigate();
  const { data, error, loading } = useApi<Cmp>(`api/compare?a=${a}&b=${b}`, { deps: [a, b] });
  const va = useApi<{ version: any }>(`api/versions/${a}`, { deps: [a] });
  const pid = va.data?.version?.project_id;
  const proj = useApi<{ versions: any[]; project: any }>(pid ? `api/projects/${pid}` : null, { deps: [pid] });
  const [filter, setFilter] = useState("");
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const files = useMemo(() => splitDiff(data?.diff || ""), [data]);
  const shown = files.filter((f) => !filter || f.file.toLowerCase().includes(filter.toLowerCase()));
  const versions = proj.data?.versions || [];
  const label = (id: string) => { const v = versions.find((x) => x.id === id); return v ? `v${v.number} ${v.label || ""}` : id.slice(0, 8); };

  const picker = (cur: string, other: string, which: "a" | "b") => (
    <Select style={{ width: 230 }} onChange={(e) => {
      const id = (e.detail.selectedOption as any).value;
      nav(which === "a" ? `/compare/${id}/${other}` : `/compare/${other}/${id}`);
    }}>
      {versions.map((v) => <Option key={v.id} value={v.id} selected={v.id === cur}>{`v${v.number} · ${v.label || "version"}`}</Option>)}
    </Select>
  );

  return (
    <div>
      <PageHeader kicker={proj.data?.project?.name || "Project"} title="Compare versions"
        subtitle={<>What changed from <b>{label(a)}</b> to <b>{label(b)}</b> — design changes first, then every file.</>}
        actions={<>
          {versions.length > 0 && <>{picker(a, b, "a")}<Icon name="arrow-right" />{picker(b, a, "b")}</>}
          {pid && <Button design="Transparent" icon="nav-back" onClick={() => nav(`/projects/${pid}`)}>Project</Button>}
        </>} />
      <div className="ev-content">
        {error && <MessageStrip design="Negative" hideCloseButton>{error}</MessageStrip>}
        {loading && !data ? <div style={{ display: "grid", gap: "1rem" }}><Skeleton height={120} radius={14} /><Skeleton height={320} radius={14} /></div> : data && (
          <>
            <div className="ev-grid" style={{ gridTemplateColumns: "minmax(0,1.2fr) minmax(0,1fr)", marginBottom: "1rem" }}>
              <Card title="Design changes" icon="compare" subtitle="Pages, entity sets, actions, title, floorplan, languages">
                {(data.blueprint_changes || []).length === 0 ? <div className="ev-muted ev-small">The design is identical — only generated files differ.</div> : (
                  <div className="ev-changes">
                    {data.blueprint_changes.map((c, i) => (
                      <motion.div key={i} className="ev-change" initial={{ opacity: 0, x: -6 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: i * 0.04 }}>
                        <Icon name={/added/.test(c) ? "add" : /removed/.test(c) ? "less" : "edit"} />{c}
                      </motion.div>
                    ))}
                  </div>
                )}
              </Card>
              <Card title="Files" icon="documents">
                <div className="ev-row" style={{ marginBottom: ".6rem" }}>
                  <StatusBadge tone="good" icon="add">{data.added.length} added</StatusBadge>
                  <StatusBadge tone="bad" icon="less">{data.removed.length} removed</StatusBadge>
                  <StatusBadge tone="warn" icon="edit">{data.changed.length} changed</StatusBadge>
                </div>
                <div className="ev-row">
                  {[...data.added.map((f) => ["good", f]), ...data.removed.map((f) => ["bad", f]), ...data.changed.map((f) => ["warn", f])]
                    .slice(0, 40).map(([t, f]) => <StatusBadge key={t + f} tone={t as any}>{f}</StatusBadge>)}
                </div>
              </Card>
            </div>
            {files.length === 0 ? <EmptyState icon="compare" title="No file differences" /> : (
              <>
                <div className="ev-row" style={{ marginBottom: ".8rem" }}>
                  <Input placeholder="Filter files" icon={<Icon name="search" />} value={filter} onInput={(e) => setFilter(e.target.value)} showClearIcon style={{ width: 300 }} />
                  <Button design="Transparent" onClick={() => setOpen(Object.fromEntries(files.map((f) => [f.file, true])))}>Expand all</Button>
                  <Button design="Transparent" onClick={() => setOpen({})}>Collapse all</Button>
                </div>
                {shown.map((f, i) => {
                  const isOpen = open[f.file] ?? i < 3;
                  return (
                    <motion.div key={f.file} className="ev-diff" initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: Math.min(i, 10) * 0.03 }}>
                      <div className="ev-diff__head" onClick={() => setOpen((o) => ({ ...o, [f.file]: !isOpen }))}>
                        <Icon name={isOpen ? "slim-arrow-down" : "slim-arrow-right"} style={{ width: ".75rem", height: ".75rem" }} />
                        <span className="ev-mono">{f.file}</span>
                        <span className="ev-diff__stat"><span className="add">+{f.add}</span> <span className="del">−{f.del}</span></span>
                      </div>
                      <AnimatePresence initial={false}>
                        {isOpen && (
                          <motion.div className="ev-diff__body" initial={{ height: 0 }} animate={{ height: "auto" }} exit={{ height: 0 }} transition={{ duration: 0.2 }}>
                            {f.lines.slice(0, 1500).map((l, k) => (
                              <div key={k} className={"ev-diff__line" + (l.startsWith("+") ? " add" : l.startsWith("-") ? " del" : l.startsWith("@@") ? " hunk" : "")}>{l || " "}</div>
                            ))}
                          </motion.div>
                        )}
                      </AnimatePresence>
                    </motion.div>
                  );
                })}
                {data.diff.length >= 59000 && <MessageStrip hideCloseButton>The diff was shortened — download both versions for a full comparison.</MessageStrip>}
              </>
            )}
          </>
        )}
      </div>
    </div>
  );
}
