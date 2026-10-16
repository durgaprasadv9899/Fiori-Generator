// Audit log: filter by text, user, action type, project and date range; timeline grouped by day;
// expandable details; CSV export.
import { useMemo, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { useNavigate } from "react-router-dom";
import { Button, Icon, Input, MessageStrip, Select, Option, DatePicker } from "@ui5/webcomponents-react";
import { EmptyState, PageHeader, Skeleton, StatusBadge } from "../../components/ui";
import { useApi, fmtDate } from "../../components/shell/useApi";
import { actionInfo } from "../../components/shell/labels";
import "./audit.css";

interface Ev { at: number; user: string; action: string; project_id: string; project_name?: string; detail?: any }

const CATS: Record<string, RegExp> = {
  all: /./, generate: /^app\.generate|^version\./, validate: /validat/, approval: /^approval\./, deploy: /^deploy\./,
  project: /^project\.|^member\./, signin: /^sign-in/,
};

export default function AuditPage() {
  const nav = useNavigate();
  const { data, error, loading, reload } = useApi<{ events: Ev[] }>("api/audit?limit=1000");
  const [q, setQ] = useState("");
  const [cat, setCat] = useState("all");
  const [user, setUser] = useState("all");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [open, setOpen] = useState<Record<number, boolean>>({});

  const events = data?.events || [];
  const users = useMemo(() => Array.from(new Set(events.map((e) => e.user))).sort(), [events]);
  const rows = useMemo(() => {
    const ql = q.trim().toLowerCase();
    const f = from ? new Date(from + "T00:00:00").getTime() / 1000 : 0;
    const t = to ? new Date(to + "T23:59:59").getTime() / 1000 : Infinity;
    return events.filter((e) => CATS[cat].test(e.action) && (user === "all" || e.user === user) && e.at >= f && e.at <= t &&
      (!ql || [e.user, e.action, e.project_name, JSON.stringify(e.detail || {})].some((x) => (x || "").toLowerCase().includes(ql))));
  }, [events, q, cat, user, from, to]);
  const byDay = useMemo(() => {
    const g: [string, { e: Ev; i: number }[]][] = [];
    rows.forEach((e, i) => {
      const d = new Date(e.at * 1000).toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long", year: "numeric" });
      if (!g.length || g[g.length - 1][0] !== d) g.push([d, []]);
      g[g.length - 1][1].push({ e, i });
    });
    return g;
  }, [rows]);

  const exportCsv = () => {
    const esc = (s: string) => `"${String(s ?? "").replace(/"/g, '""')}"`;
    const lines = ["time,user,action,project,detail"].concat(rows.map((e) =>
      [new Date(e.at * 1000).toISOString(), e.user, e.action, e.project_name || e.project_id, JSON.stringify(e.detail || {})].map(esc).join(",")));
    const blob = new Blob([lines.join("\n")], { type: "text/csv" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `evoke-studio-audit-${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  };

  return (
    <div>
      <PageHeader kicker="Governance" title="Audit log"
        subtitle="Who did what and when — sign-ins, generations, validations, approvals and deployments. Entries cannot be edited."
        actions={<>
          <Button icon="refresh" design="Transparent" onClick={() => reload()}>Refresh</Button>
          <Button icon="download" disabled={!rows.length} onClick={exportCsv}>Export CSV</Button>
        </>} />
      <div className="ev-content">
        <div className="ev-audit-filters">
          <Input icon={<Icon name="search" />} placeholder="Search user, action, project, details…" value={q} onInput={(e) => setQ(e.target.value)} showClearIcon />
          <div className="ev-chips">
            {[["all", "All"], ["generate", "Generation"], ["validate", "Validation"], ["approval", "Approvals"], ["deploy", "Deployments"], ["project", "Projects"], ["signin", "Sign-ins"]].map(([k, l]) => (
              <button key={k} className={"ev-chip" + (cat === k ? " is-on" : "")} onClick={() => setCat(k)}>{l}</button>
            ))}
          </div>
          <Select onChange={(e) => setUser((e.detail.selectedOption as any).value)} style={{ width: 210 }}>
            <Option value="all">All users</Option>
            {users.map((u) => <Option key={u} value={u}>{u}</Option>)}
          </Select>
          <DatePicker placeholder="From" formatPattern="yyyy-MM-dd" value={from} onChange={(e) => setFrom(e.detail.value)} style={{ width: 150 }} />
          <DatePicker placeholder="To" formatPattern="yyyy-MM-dd" value={to} onChange={(e) => setTo(e.detail.value)} style={{ width: 150 }} />
          <span className="ev-muted ev-small">{rows.length} of {events.length} entries</span>
        </div>
        {error && <MessageStrip design="Negative" hideCloseButton>{error}</MessageStrip>}
        {loading && !data ? <div style={{ display: "grid", gap: ".6rem" }}>{Array.from({ length: 8 }).map((_, i) => <Skeleton key={i} height={46} />)}</div> :
          rows.length === 0 ? <EmptyState icon="history" title={events.length ? "No entry matches the filters" : "The audit log is empty"} /> : (
            <div className="ev-audit">
              {byDay.map(([day, items]) => (
                <div key={day} className="ev-audit__day">
                  <div className="ev-audit__date">{day}<span>{items.length}</span></div>
                  {items.map(({ e, i }, k) => {
                    const info = actionInfo(e.action);
                    const isOpen = !!open[i];
                    return (
                      <motion.div key={i} className="ev-audit__item" initial={{ opacity: 0, x: -6 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: Math.min(k, 15) * 0.02 }}>
                        <span className={`ev-feed__icon tone-${info.tone}`}><Icon name={info.icon} /></span>
                        <div className="ev-audit__body" onClick={() => setOpen((o) => ({ ...o, [i]: !isOpen }))}>
                          <div className="ev-audit__line">
                            <b>{e.user}</b> {info.text}
                            {e.project_name && <> · <a onClick={(ev) => { ev.stopPropagation(); nav(`/projects/${e.project_id}`); }}>{e.project_name}</a></>}
                            <span className="ev-audit__time" title={fmtDate(e.at)}>{new Date(e.at * 1000).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}</span>
                          </div>
                          <div className="ev-audit__meta"><StatusBadge tone="neutral">{e.action}</StatusBadge>
                            {e.detail && Object.keys(e.detail).length > 0 && <span className="ev-link-btn">{isOpen ? "hide details" : "details"}</span>}</div>
                          <AnimatePresence>
                            {isOpen && e.detail && (
                              <motion.pre className="ev-audit__json" initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }} exit={{ height: 0, opacity: 0 }}>
                                {JSON.stringify(e.detail, null, 2)}
                              </motion.pre>
                            )}
                          </AnimatePresence>
                        </div>
                      </motion.div>
                    );
                  })}
                </div>
              ))}
            </div>
          )}
      </div>
    </div>
  );
}
