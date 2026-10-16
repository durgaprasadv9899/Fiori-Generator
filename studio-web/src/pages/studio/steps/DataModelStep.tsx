// Data model (new apps): visual ER diagram + entity editor, business process diagram, database.
import { useEffect, useMemo, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { useNavigate } from "react-router-dom";
import {
  Bar, Button, CheckBox, Dialog, Icon, Input, MessageStrip, Option, Select, TextArea,
} from "@ui5/webcomponents-react";
import { api } from "../../../api/client";
import { ErDiagram } from "../../../components/designers/ErDiagram";
import { WorkflowDiagram } from "../../../components/designers/WorkflowDiagram";
import { StatusBadge } from "../../../components/ui";
import { draftStore, toastBus, useDraft } from "../draft";
import { StepIntro } from "./common";

const TYPES = ["String", "LargeString", "Integer", "Decimal", "Date", "DateTime", "Boolean", "UUID"];
const TONE: Record<string, "good" | "bad" | "warn" | "neutral" | "info"> = { Positive: "good", Negative: "bad", Critical: "warn", Neutral: "neutral", Information: "info" };

export function DataModelStep() {
  const d = useDraft();
  const nav = useNavigate();
  const domain = d.domain;
  const [sel, setSel] = useState<string>(domain?.root || domain?.entities?.[0]?.name || "");
  const [json, setJson] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [tab, setTab] = useState<"model" | "process">("model");

  if (!domain) return null;
  const ents: any[] = domain.entities || [];
  const wf = domain.workflow;
  const fieldCount = ents.reduce((n, e) => n + (e.fields?.length || 0), 0);

  const apply = async (next: any, msg = "Data model updated") => {
    setSaving(true);
    try {
      const r = await api.post<any>("api/domain/validate", { domain: next });
      const hadDesign = !!draftStore.get().design;
      draftStore.update({ domain: r.domain, domainNotes: r.notes || [], domainSummary: r.summary || "", design: null, generated: null, validation: null });
      toastBus.push(hadDesign ? `${msg} — design the screens again` : msg, "good");
      return true;
    } catch (e: any) {
      toastBus.push(e.message, "bad");
      return false;
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="ev-step-page">
      <StepIntro icon="org-chart" title="Review the data model"
        text="Entities, fields and relationships the backend will have, and the business process it enforces. Click an entity to change its fields — the studio checks every change before it is applied."
        aside={<div className="ev-mini-stats">
          <div><b>{ents.length}</b><span>entities</span></div>
          <div><b>{fieldCount}</b><span>fields</span></div>
          <div><b>{wf?.transitions?.length || 0}</b><span>process steps</span></div>
        </div>} />

      {d.domainNotes.length > 0 && (
        <div className="ev-notes">
          {d.domainNotes.slice(0, 4).map((n, i) => <MessageStrip key={i} design="Information" hideCloseButton>{n}</MessageStrip>)}
        </div>
      )}

      <div className="ev-tabs">
        <button className={"ev-tab" + (tab === "model" ? " is-on" : "")} onClick={() => setTab("model")}><Icon name="org-chart" />Entities & relations</button>
        <button className={"ev-tab" + (tab === "process" ? " is-on" : "")} onClick={() => setTab("process")} disabled={!wf}>
          <Icon name="process" />Business process{!wf && " (none)"}
        </button>
        <span className="ev-tabs__spacer" />
        <Button design="Transparent" icon="syntax" onClick={() => setJson(JSON.stringify(domain, null, 2))}>JSON</Button>
      </div>

      <AnimatePresence mode="wait">
        {tab === "model" ? (
          <motion.div key="m" className="ev-model" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
            <div className="ev-model__canvas">
              <ErDiagram domain={domain} selected={sel} onSelect={setSel} height={520} />
              <div className="ev-legend">
                <span><i className="ev-legend__solid" />composition (items)</span>
                <span><i className="ev-legend__dashed" />association</span>
                <span><Icon name="simulate" />calculated</span>
                <span><b className="ev-er__req">*</b> required</span>
              </div>
            </div>
            <EntityEditor key={sel + JSON.stringify(ents.find((e) => e.name === sel)?.fields?.length)} domain={domain} name={sel} saving={saving}
              onApply={(next) => apply(next, "Entity updated")} />
          </motion.div>
        ) : (
          <motion.div key="p" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
            {wf && <ProcessView wf={wf} domain={domain} onApply={(next) => apply(next, "Process updated")} />}
          </motion.div>
        )}
      </AnimatePresence>

      <div className="ev-section-title"><Icon name="database" /> Database of the deployed app</div>
      <div className="ev-dbchoice">
        {([
          { k: "sqlite", t: "SQLite (demo)", icon: "it-instance", text: "In-memory database with the sample data. No extra service — data resets when the app restarts. Best for demos and reviews." },
          { k: "hana", t: "SAP HANA Cloud", icon: "database", text: "Persistent data in an HDI container. Needs HANA Cloud in the subaccount; the database module is built with Node.js (SAP Business Application Studio)." },
        ] as const).map((o) => (
          <motion.button key={o.k} className={"ev-dbcard" + (d.db === o.k ? " is-on" : "")} whileHover={{ y: -2 }}
            onClick={() => draftStore.update({ db: o.k, generated: null, validation: null })} aria-pressed={d.db === o.k}>
            <span className="ev-dbcard__icon"><Icon name={o.icon} /></span>
            <span className="ev-dbcard__text"><b>{o.t}</b><span>{o.text}</span></span>
            <span className="ev-dbcard__radio">{d.db === o.k && <motion.span layoutId="db-radio" />}</span>
          </motion.button>
        ))}
      </div>

      <div className="ev-cta-row">
        <Button design="Emphasized" endIcon="navigation-right-arrow" onClick={() => nav("/studio/screens")}>Looks good — design the screens</Button>
      </div>

      <Dialog open={json !== null} headerText="Data model (JSON)" className="ev-json-dialog" onClose={() => setJson(null)}
        footer={<Bar endContent={<>
          <Button design="Emphasized" disabled={saving} onClick={async () => {
            try {
              const parsed = JSON.parse(json || "{}");
              if (await apply(parsed, "Data model updated from JSON")) setJson(null);
            } catch (e: any) { toastBus.push(`Not valid JSON: ${e.message}`, "bad"); }
          }}>Validate & apply</Button>
          <Button design="Transparent" onClick={() => setJson(null)}>Cancel</Button>
        </>} />}>
        <div className="ev-dialog-text ev-muted">Expert mode — every change is validated (names, types, relations, workflow) before it is applied.</div>
        <TextArea className="ev-json-area" value={json || ""} rows={24} onInput={(e) => setJson((e.target as any).value)} />
      </Dialog>
    </div>
  );
}

/** Side panel: fields of the selected entity. */
function EntityEditor({ domain, name, saving, onApply }: { domain: any; name: string; saving: boolean; onApply: (d: any) => Promise<boolean> }) {
  const ent = (domain.entities || []).find((e: any) => e.name === name);
  const [fields, setFields] = useState<any[]>(() => JSON.parse(JSON.stringify(ent?.fields || [])));
  const [label, setLabel] = useState(ent?.label || "");
  useEffect(() => { setFields(JSON.parse(JSON.stringify(ent?.fields || []))); setLabel(ent?.label || ""); }, [ent]);
  const dirty = useMemo(() => JSON.stringify(fields) !== JSON.stringify(ent?.fields || []) || label !== (ent?.label || ""), [fields, label, ent]);
  if (!ent) return <div className="ev-editor ev-editor--empty"><Icon name="cursor-arrow" /> Select an entity in the diagram.</div>;

  const set = (i: number, patch: any) => setFields((fs) => fs.map((f, j) => (j === i ? { ...f, ...patch } : f)));
  const save = () => {
    const next = { ...domain, entities: domain.entities.map((e: any) => (e.name === name ? { ...e, label, fields } : e)) };
    onApply(next);
  };

  return (
    <motion.aside className="ev-editor" initial={{ opacity: 0, x: 16 }} animate={{ opacity: 1, x: 0 }}>
      <div className="ev-editor__head">
        <Icon name="table-view" />
        <div className="ev-editor__titles">
          <Input value={label} onInput={(e) => setLabel((e.target as any).value)} className="ev-editor__label" />
          <span className="ev-muted">{ent.name}{ent.parent ? ` · item of ${ent.parent}` : ""}</span>
        </div>
      </div>
      <div className="ev-editor__list">
        <div className="ev-fieldrow ev-fieldrow--key"><Icon name="key" /><span>ID</span><em>UUID · generated</em></div>
        {fields.map((f, i) => (
          <motion.div key={i} className={"ev-fieldrow" + (f.formula ? " is-calc" : "")} layout initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
            <div className="ev-fieldrow__main">
              <Input value={f.label || ""} placeholder="Label" onInput={(e) => set(i, { label: (e.target as any).value })} />
              <Select value={f.type} disabled={!!f.formula} onChange={(e) => set(i, { type: (e.target as any).value })}>
                {TYPES.map((t) => <Option key={t} value={t}>{t}</Option>)}
              </Select>
              <Button design="Transparent" icon="delete" tooltip="Remove field" onClick={() => setFields((fs) => fs.filter((_, j) => j !== i))} />
            </div>
            <div className="ev-fieldrow__sub">
              <code>{f.name}</code>
              {f.formula ? <StatusBadge tone="brand" icon="simulate">calculated</StatusBadge> : (
                <CheckBox text="Required" checked={!!f.required} onChange={(e) => set(i, { required: (e.target as any).checked })} />
              )}
              {f.type === "String" && !f.formula && (
                <Input className="ev-fieldrow__enum" placeholder="Allowed values (comma separated)" value={(f.enum || []).join(", ")}
                  onChange={(e) => {
                    const vals = String((e.target as any).value).split(",").map((s) => s.trim()).filter(Boolean);
                    set(i, { enum: vals.length ? vals : undefined });
                  }} />
              )}
            </div>
          </motion.div>
        ))}
      </div>
      <div className="ev-editor__foot">
        <Button icon="add" onClick={() => setFields((fs) => [...fs, { name: `newField${fs.length + 1}`, label: "New field", type: "String", required: false, readonly: false, length: 100 }])}>Add field</Button>
        <span className="ev-tabs__spacer" />
        <Button design="Transparent" disabled={!dirty} onClick={() => { setFields(JSON.parse(JSON.stringify(ent.fields || []))); setLabel(ent.label || ""); }}>Reset</Button>
        <Button design="Emphasized" disabled={!dirty || saving} onClick={save}>{saving ? "Checking…" : "Apply"}</Button>
      </div>
    </motion.aside>
  );
}

/** Business process: state diagram + transitions table + process options. */
function ProcessView({ wf, domain, onApply }: { wf: any; domain: any; onApply: (d: any) => Promise<boolean> }) {
  const setWf = (patch: any) => onApply({ ...domain, workflow: { ...wf, ...patch } });
  return (
    <div className="ev-process">
      <WorkflowDiagram workflow={wf} height={300} />
      <div className="ev-process__grid">
        <div className="ev-table-wrap">
          <table className="ev-table">
            <thead><tr><th>Action</th><th>From</th><th>To</th><th>Who</th><th>Comment</th></tr></thead>
            <tbody>
              {(wf.transitions || []).map((t: any) => {
                const to = (wf.states || []).find((s: any) => s.code === t.to);
                return (
                  <tr key={t.action}>
                    <td><b>{t.label}</b><div className="ev-muted ev-mono">{t.action}</div></td>
                    <td>{(t.from || []).join(" / ")}</td>
                    <td><StatusBadge tone={TONE[to?.criticality] || "neutral"}>{to?.label || t.to}</StatusBadge></td>
                    <td>{t.role && wf.enforce_roles ? <StatusBadge tone="info" icon="employee">{t.role}</StatusBadge> : <span className="ev-muted">any signed-in user</span>}</td>
                    <td>{t.comment === "required" ? <StatusBadge tone="warn">required</StatusBadge> : <span className="ev-muted">{t.comment || "none"}</span>}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <div className="ev-process__opts">
          <div className="ev-optcard">
            <CheckBox text="Enforce approver roles" checked={!!wf.enforce_roles} onChange={(e) => setWf({ enforce_roles: (e.target as any).checked })} />
            <span className="ev-muted">Only users with the generated role collection (e.g. Approver) may decide. Assign it in the BTP cockpit.</span>
          </div>
          <div className="ev-optcard">
            <CheckBox text="Four-eyes principle" checked={!!wf.segregation_of_duties} onChange={(e) => setWf({ segregation_of_duties: (e.target as any).checked })} />
            <span className="ev-muted">The person who created a request cannot approve it.</span>
          </div>
          <div className="ev-optcard ev-optcard--info">
            <Icon name="shield" />
            <span>The backend enforces this process: actions in the wrong state are rejected, records cannot be edited after submission, and every step is recorded in a history table.</span>
          </div>
        </div>
      </div>
    </div>
  );
}
