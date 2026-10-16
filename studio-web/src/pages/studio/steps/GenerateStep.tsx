// Generate & validate: project generation (saved as a version), file browser, build/runtime validation.
import { useEffect, useMemo, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { useNavigate } from "react-router-dom";
import { Button, Icon, Input, Link, MessageStrip, Option, Select } from "@ui5/webcomponents-react";
import { api } from "../../../api/client";
import { CodeViewer, langOf } from "../../../components/designers/CodeViewer";
import { FileTree } from "../../../components/designers/FileTree";
import { Ring } from "../../../components/designers/Ring";
import { StatusBadge, Skeleton } from "../../../components/ui";
import type { Check } from "../../../api/types";
import { defaultAppId, draftStore, sourceOf, useDraft } from "../draft";
import { useStudioJobs } from "../flow";
import { Choice, JobError, JobProgress, StepIntro } from "./common";

const APP_ID_RE = /^[a-zA-Z][\w]*(\.[a-zA-Z][\w]*)+$/;
const THEMES = [["sap_horizon", "Horizon (light)"], ["sap_horizon_dark", "Horizon (dark)"], ["sap_fiori_3", "Quartz light"], ["sap_horizon_hcb", "High contrast"]];
const CAT_ICON: Record<string, string> = { static: "inspection", build: "wrench", backend: "database", runtime: "play", requirement: "target-group" };

export function GenerateStep() {
  const d = useDraft();
  const nav = useNavigate();
  const { start, jobOf, busy } = useStudioJobs();
  const appId = d.appId || defaultAppId(d);
  const idOk = APP_ID_RE.test(appId);
  const [level, setLevel] = useState<"standard" | "deep">("standard");

  const generate = () => start("generate", "api/jobs/generate", {
    requirement: d.requirement, source: sourceOf(d), blueprint: d.design?.blueprint, app_id: appId,
    project_id: d.generated?.project_id || null, design: { spec: d.design?.spec || null, coverage: d.design?.coverage || [] }, theme: d.theme,
  }, (r) => draftStore.update({ generated: r, validation: null, appId }), "Application generated and saved");

  const validate = () => d.generated && start("validate", "api/jobs/validate", { version_id: d.generated.version_id, level },
    (r) => draftStore.update({ validation: r }), "Validation finished");

  const g = d.generated;
  return (
    <div className="ev-step-page">
      <StepIntro icon="source-code" title="Generate & validate"
        text={d.mode === "standalone"
          ? "Generates the complete project — SAPUI5 app, CAP backend (data model, service, workflow, sample data), approuter, XSUAA and MTA — saves it as a project version, then builds and runs it in a sandbox to prove it works."
          : "Generates the complete SAPUI5 project with approuter, XSUAA, destination and MTA descriptors, saves it as a project version, then builds and runs it in a sandbox to prove it works."} />

      <div className="ev-gen-form">
        <div className="ev-field">
          <label className="ev-label">App ID (namespace)</label>
          <Input value={appId} valueState={idOk ? "None" : "Negative"} onInput={(e) => draftStore.update({ appId: (e.target as any).value })}
            valueStateMessage={<div>Use a dotted namespace like com.company.app</div>} />
          <span className="ev-muted">Also the Cloud Foundry application name.</span>
        </div>
        <div className="ev-field">
          <label className="ev-label">SAP theme</label>
          <Select value={d.theme} onChange={(e) => draftStore.update({ theme: (e.target as any).value })}>
            {THEMES.map(([k, t]) => <Option key={k} value={k}>{t}</Option>)}
          </Select>
        </div>
        <div className="ev-field ev-field--btn">
          <Button design="Emphasized" icon="action" disabled={!idOk || busy("generate") || !d.design} onClick={generate}>
            {busy("generate") ? "Generating…" : g ? "Generate new version" : "Generate application"}
          </Button>
        </div>
      </div>
      <JobProgress job={jobOf("generate")} title="Generating the application" />
      <JobError kind="generate" />

      <AnimatePresence>
        {g && (
          <motion.div key={g.version_id} initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }}>
            <div className="ev-gen-done">
              <SuccessCheck ok={g.quality.errors === 0} />
              <div className="ev-gen-done__text">
                <h3>{g.quality.errors === 0 ? "Generated and saved" : "Generated with quality errors"}</h3>
                <p>Version <b>v{g.version_number}</b> · {g.file_count} files · {g.quality.errors} error(s), {g.quality.warnings} warning(s)</p>
              </div>
              <div className="ev-gen-done__actions">
                <a className="ev-btnlink" href={api.url(`api/versions/${g.version_id}/zip`)} download><Icon name="download" />Download ZIP</a>
                <Button design="Transparent" icon="folder-blank" onClick={() => nav(`/projects/${g.project_id}`)}>Open project</Button>
              </div>
            </div>
            {g.quality.findings.filter((f) => f.severity === "error").length > 0 && (
              <MessageStrip design="Negative" hideCloseButton className="ev-gap-top">
                {g.quality.findings.filter((f) => f.severity === "error").slice(0, 5).map((f) => f.message).join(" · ")}
              </MessageStrip>
            )}
            <FilesBrowser versionId={g.version_id} files={g.files} />

            <div className="ev-section-title"><Icon name="inspection" /> Build & runtime validation</div>
            <div className="ev-actions">
              <Choice value={level} onChange={setLevel} options={[{ key: "standard", label: "Standard", icon: "wrench" }, { key: "deep", label: "Deep (browser)", icon: "play" }]} />
              <Button design="Emphasized" icon="inspection" disabled={busy("validate")} onClick={validate}>
                {busy("validate") ? "Validating…" : d.validation ? "Validate again" : "Run validation"}
              </Button>
              <span className="ev-muted">Standard ≈ 1 min (cached later) · Deep also starts the app in a headless browser.</span>
            </div>
            <JobProgress job={jobOf("validate")} title="Building and running the project in a sandbox" />
            <JobError kind="validate" />
            {d.validation && <ValidationView />}

            <div className="ev-cta-row">
              <Button design="Emphasized" endIcon="navigation-right-arrow" onClick={() => nav("/studio/deploy")}>Continue to deploy</Button>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

function SuccessCheck({ ok }: { ok: boolean }) {
  return (
    <motion.div className={"ev-check" + (ok ? "" : " is-warn")} initial={{ scale: 0.4, opacity: 0 }} animate={{ scale: 1, opacity: 1 }}
      transition={{ type: "spring", stiffness: 260, damping: 16 }}>
      <svg viewBox="0 0 52 52" width="52" height="52">
        <circle cx="26" cy="26" r="24" fill="none" strokeWidth="3" className="ev-check__c" />
        <motion.path d={ok ? "M15 27 l7 7 l15 -16" : "M26 15 v14 M26 36 v1"} fill="none" strokeWidth="4" strokeLinecap="round" strokeLinejoin="round"
          className="ev-check__p" initial={{ pathLength: 0 }} animate={{ pathLength: 1 }} transition={{ delay: 0.2, duration: 0.45 }} />
      </svg>
    </motion.div>
  );
}

function FilesBrowser({ versionId, files }: { versionId: string; files: string[] }) {
  const first = useMemo(() => files.find((f) => /manifest\.json$/.test(f)) || files.find((f) => /\.cds$/.test(f)) || files[0] || "", [files]);
  const [sel, setSel] = useState(first);
  const [code, setCode] = useState<string | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => setSel(first), [first]);
  useEffect(() => {
    if (!sel) return;
    setCode(null); setErr("");
    api.text(`api/versions/${versionId}/file?path=${encodeURIComponent(sel)}`).then(setCode).catch((e) => setErr(e.message));
  }, [sel, versionId]);
  const groups = useMemo(() => ({
    ui: files.filter((f) => !f.startsWith("backend/") && !/^(mta\.yaml|xs-app\.json|xs-security\.json|package\.json|ui5.*\.yaml)$/.test(f)).length,
    backend: files.filter((f) => f.startsWith("backend/")).length,
  }), [files]);
  return (
    <div className="ev-files">
      <div className="ev-files__tree">
        <div className="ev-files__head"><Icon name="folder-blank" /> Project files <span className="ev-muted">{files.length}</span></div>
        <div className="ev-files__stats">
          <StatusBadge tone="brand">UI {groups.ui}</StatusBadge>
          {groups.backend > 0 && <StatusBadge tone="info">backend {groups.backend}</StatusBadge>}
        </div>
        <FileTree paths={files} selected={sel} onSelect={setSel} />
      </div>
      <div className="ev-files__code">
        <div className="ev-files__path"><Icon name="document" /><code>{sel}</code></div>
        {err ? <MessageStrip design="Negative" hideCloseButton>{err}</MessageStrip>
          : code === null ? <div className="ev-pad"><Skeleton height={14} width="60%" /><div style={{ height: 8 }} /><Skeleton height={14} width="80%" /><div style={{ height: 8 }} /><Skeleton height={14} width="45%" /></div>
          : <CodeViewer code={code} lang={langOf(sel)} maxHeight={460} />}
      </div>
    </div>
  );
}

function ValidationView() {
  const d = useDraft();
  const rep = d.validation!;
  const n = rep.summary;
  const total = n.passed + n.failed + n.warnings + n.not_tested;
  const pct = total ? Math.round((n.passed / total) * 100) : 0;
  const byCat = useMemo(() => {
    const m: Record<string, Check[]> = {};
    rep.checks.forEach((c) => (m[c.category] = [...(m[c.category] || []), c]));
    return m;
  }, [rep]);
  const tone = (s: string) => (s === "passed" ? "good" : s === "failed" ? "bad" : s === "warning" ? "warn" : "neutral") as any;
  const icon = (s: string) => (s === "passed" ? "accept" : s === "failed" ? "decline" : s === "warning" ? "alert" : "less");
  return (
    <motion.div className="ev-val" initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
      <div className="ev-val__head">
        <Ring value={pct} sub="checks passed" tone={rep.blocking.length ? "bad" : pct >= 80 ? "good" : "warn"} />
        <div className="ev-val__sum">
          <h3>{rep.blocking.length ? "Deployment blocked" : "Ready to deploy"}</h3>
          <div className="ev-val__pills">
            <StatusBadge tone="good" icon="accept">{n.passed} passed</StatusBadge>
            <StatusBadge tone="bad" icon="decline">{n.failed} failed</StatusBadge>
            <StatusBadge tone="warn" icon="alert">{n.warnings} warnings</StatusBadge>
            <StatusBadge tone="neutral" icon="less">{n.not_tested} not tested</StatusBadge>
          </div>
          <span className="ev-muted">Level {rep.level} · {Math.round(rep.seconds)}s · "not tested" means the tool was not available here — never counted as passed.</span>
        </div>
      </div>
      {rep.blocking.length > 0 && <MessageStrip design="Negative" hideCloseButton>Blocking: {rep.blocking.join("; ")}</MessageStrip>}
      <div className="ev-val__grid">
        {Object.entries(byCat).map(([cat, checks], gi) => (
          <div key={cat} className="ev-val__cat">
            <div className="ev-val__cattitle"><Icon name={CAT_ICON[cat] || "inspection"} />{cat}</div>
            {checks.map((c, i) => (
              <motion.div key={c.id} className={`ev-chk ev-chk--${tone(c.status)}`} initial={{ opacity: 0, x: -8 }} animate={{ opacity: 1, x: 0 }}
                transition={{ delay: gi * 0.08 + i * 0.04 }} title={c.detail}>
                <span className="ev-chk__icon"><Icon name={icon(c.status)} /></span>
                <span className="ev-chk__name">{c.name}{c.critical && <em>critical</em>}</span>
                <span className="ev-chk__secs">{c.seconds ? `${c.seconds.toFixed(1)}s` : ""}</span>
                {c.status !== "passed" && c.detail && <span className="ev-chk__detail">{c.detail.slice(0, 280)}</span>}
              </motion.div>
            ))}
          </div>
        ))}
      </div>
      {rep.requirements?.length > 0 && (
        <div className="ev-table-wrap ev-gap-top">
          <table className="ev-table">
            <thead><tr><th>Requirement</th><th>Status</th><th>Evidence</th></tr></thead>
            <tbody>{rep.requirements.map((r, i) => (
              <tr key={i}><td>{r.feature}</td><td><StatusBadge tone={tone(r.status)}>{r.status.replace("_", " ")}</StatusBadge></td><td className="ev-muted">{r.detail}</td></tr>
            ))}</tbody>
          </table>
        </div>
      )}
    </motion.div>
  );
}
