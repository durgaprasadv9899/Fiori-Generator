// Workspace of an adopted project (shown inside the project page): overview, the AI assistant (changes the
// project with a reviewed diff), live preview, data & screens, project brief, deploy. One column, aligned rows.
import { useEffect, useMemo, useState } from "react";
import { Button, Icon, Input, MessageStrip, Option, SegmentedButton, SegmentedButtonItem, Select, BusyIndicator } from "@ui5/webcomponents-react";
import { Card, LiveTimeline, Skeleton, StatusBadge, EmptyState, ago } from "../../components/ui";
import { useApi } from "../../components/shell/useApi";
import { useToast } from "../../components/shell/Toasts";
import type { TabDef } from "../../components/shell/Tabs";
import { DeviceFrame, type Device } from "../../components/designers/DeviceFrame";
import { api } from "../../api/client";
import type { AdoptProjectInfo, Answers, Mission, MissionSummary, PreviewInfo, Suggestion, VersionMeta } from "../../api/types";
import { Fact, FormRow, QuestionList, questionsFromBrief } from "./Questions";
import {
  ArchDiagram, ChecksSummary, DiffView, EndpointsTable, EntityCards, Findings, LanguageBar, ModuleCards,
  Markdown, ScreensTable, StackChips, UnderstandingBlock,
} from "./parts";
import { useTrackedJob } from "./useTrackedJob";
import "./adopt.css";

export const ADOPT_TABS: TabDef[] = [
  { key: "overview", text: "Overview", icon: "overview-chart" },
  { key: "robot", text: "AI Assistant", icon: "ai" },
  { key: "tryit", text: "Try it", icon: "play" },
  { key: "model", text: "Data & screens", icon: "database" },
  { key: "brief", text: "Project brief", icon: "document-text" },
  { key: "deploy", text: "Deploy", icon: "cloud" },
];
export const ADOPT_KEYS = [...ADOPT_TABS.map((t) => t.key), "screens"];

const ss = {
  get<T>(k: string, d: T): T { try { const v = sessionStorage.getItem(k); return v ? JSON.parse(v) : d; } catch { return d; } },
  set(k: string, v: unknown) { try { if (v === null || v === undefined || v === "") sessionStorage.removeItem(k); else sessionStorage.setItem(k, JSON.stringify(v)); } catch { /* ignore */ } },
};

export function AdoptedWorkspace({ pid, tab, setTab, versions, mayEdit, mayDeploy, onChanged, deployVid, setDeployVid }: {
  pid: string; tab: string; setTab: (t: string) => void; versions: VersionMeta[]; mayEdit: boolean; mayDeploy: boolean;
  onChanged: () => void; deployVid: string; setDeployVid: (v: string) => void;
}) {
  const toast = useToast();
  const { data: info, error, loading, reload } = useApi<AdoptProjectInfo>(`api/adopt/projects/${pid}`, { deps: [pid] });
  const [draft, setDraft] = useState("");
  const [preview, setPreviewState] = useState<PreviewInfo | null>(() => ss.get(`evoke.adopt.preview.${pid}`, null));
  const setPreview = (p: PreviewInfo | null) => { setPreviewState(p); ss.set(`evoke.adopt.preview.${pid}`, p); };
  const [previewErr, setPreviewErr] = useState("");
  const previewJob = useTrackedJob<PreviewInfo>(`adopt-preview.${pid}`, (r) => { setPreview(r); setPreviewErr(""); toast("Preview is running"); },
    (e) => setPreviewErr(e));
  const startPreview = async (body: { version_id?: string; mission_id?: string }) => {
    setTab("tryit"); setPreviewErr("");
    try { await previewJob.start("api/jobs/adopt-preview", body); } catch (e: any) { setPreviewErr(e.message); }
  };
  const ask = (request: string) => { setDraft(request); setTab("robot"); };
  const changed = () => { reload(true); onChanged(); };

  if (error) return <MessageStrip design="Negative" hideCloseButton>{error}</MessageStrip>;
  if (loading && !info) return <div style={{ display: "grid", gap: "1rem" }}><Skeleton height={140} radius={16} /><Skeleton height={260} radius={16} /></div>;
  if (!info) return null;

  return (
    <div className="ev-aw">
      {tab === "overview" && <Overview pid={pid} info={info} mayEdit={mayEdit} onAsk={ask} onTab={setTab} onChanged={changed} />}
      {tab === "robot" && <Assistant pid={pid} info={info} draft={draft} setDraft={setDraft} mayEdit={mayEdit}
        onPreview={(mid) => startPreview({ mission_id: mid })} onApplied={changed} />}
      {tab === "tryit" && <TryIt info={info} preview={preview} setPreview={setPreview} job={previewJob} err={previewErr}
        onStart={() => startPreview({ version_id: info.version.id })} />}
      {(tab === "model" || tab === "screens") && <DataAndScreens info={info} mayEdit={mayEdit} onAsk={ask} />}
      {tab === "brief" && <Brief pid={pid} info={info} mayEdit={mayEdit} onSaved={changed} />}
      {tab === "deploy" && <Deploy info={info} versions={versions} mayDeploy={mayDeploy} vid={deployVid || info.version.id} setVid={setDeployVid} onDone={changed} />}
    </div>
  );
}

// ── overview ─────────────────────────────────────────────────────────────────
function Overview({ pid, info, mayEdit, onAsk, onTab, onChanged }: {
  pid: string; info: AdoptProjectInfo; mayEdit: boolean; onAsk: (r: string) => void; onTab: (t: string) => void; onChanged: () => void;
}) {
  const toast = useToast();
  const a = info.analysis;
  const [sugg, setSugg] = useState<Suggestion[]>(() => a.understanding?.suggested_missions || []);
  const suggest = useTrackedJob<{ suggestions: Suggestion[] }>(`robot-suggest.${pid}`, (r) => setSugg(r.suggestions || []),
    (e) => toast(`Suggestions failed: ${e}`, "error"));
  const reanalyze = useTrackedJob(`adopt-reanalyze.${pid}`, () => { toast("Analysis refreshed"); onChanged(); }, (e) => toast(`Analysis failed: ${e}`, "error"));
  const applied = info.missions.filter((m) => m.applied_version).length;
  const b = info.brief || {};
  const protectedPaths = Array.isArray(b.protected) ? b.protected : [];
  const link = (t: string, label: string) => <button className="ev-linkbtn" onClick={() => onTab(t)}>{label}</button>;
  return (
    <>
      <Card title="Summary" icon="overview-chart" subtitle={`${a.files} files · source ${a.source.label || a.source.kind}`}>
        <div className="ev-facts">
          <Fact label="Purpose">{String(b.purpose || a.understanding?.summary || "—")}</Fact>
          <Fact label="Stack"><StackChips stack={a.stack} /></Fact>
          <Fact label="Modules">{a.modules.length} · {a.modules.map((m) => m.name).join(", ")}</Fact>
          <Fact label="Data model">{a.model.entities.length ? <>{a.model.entities.length} entities from {a.model.source} · {link("model", "View")}</> : "No data model recognised"}</Fact>
          <Fact label="Screens & APIs">{a.screens.length} screens · {a.endpoints.length} API endpoints · {link("model", "View")}</Fact>
          <Fact label="AI changes">{applied} applied · {info.missions.length} requests · {link("robot", "Open AI Assistant")}</Fact>
          <Fact label="Deploys to">{String(b.deploy_target || "—")}</Fact>
          <Fact label="Never changed by AI">{protectedPaths.length ? protectedPaths.join(", ") : "nothing protected"} · {link("brief", "Edit brief")}</Fact>
          <Fact label="Sandbox">
            <StatusBadge tone={info.runner?.ok ? "good" : "warn"} icon={info.runner?.ok ? "machine" : "alert"}>{info.runner?.ok ? "available" : "unavailable"}</StatusBadge>
            {info.runner?.node && <span className="ev-muted ev-small">&nbsp; Node {info.runner.node}</span>}
            {info.runner?.python && <span className="ev-muted ev-small">&nbsp;· Python {info.runner.python}</span>}
          </Fact>
        </div>
      </Card>

      <Card title="How it fits together" icon="org-chart">
        <ArchDiagram arch={a.arch} />
        <div style={{ marginTop: ".8rem" }}><LanguageBar languages={a.languages} /></div>
      </Card>

      <Card title="Business scenario" icon="ai" subtitle="How the AI reads the project. Correct it in the project brief if something is wrong."
        actions={mayEdit && <Button design="Transparent" icon="refresh" disabled={reanalyze.running}
          onClick={() => reanalyze.start("api/jobs/adopt-reanalyze", { project_id: pid }).catch((e) => toast(e.message, "error"))}>
          {reanalyze.running ? "Analysing…" : "Re-analyse"}</Button>}>
        {reanalyze.running ? <LiveTimeline messages={reanalyze.job?.messages || []} running /> :
          a.understanding?.summary ? <UnderstandingBlock u={a.understanding} /> :
            <div className="ev-muted ev-small">No AI summary yet. Click Re-analyse to let the AI read the code.</div>}
      </Card>

      <Card title="Modules" icon="dimension"><ModuleCards modules={a.modules} /></Card>

      <Card title="Suggested changes" icon="lightbulb" subtitle="Improvements the AI proposes for this project. Pick one to start an AI request."
        actions={mayEdit && <Button design="Transparent" icon="lightbulb" disabled={suggest.running}
          onClick={() => suggest.start("api/jobs/robot-suggest", { project_id: pid }).catch((e) => toast(e.message, "error"))}>
          {suggest.running ? "Thinking…" : "Suggest again"}</Button>}>
        {suggest.running ? <LiveTimeline messages={suggest.job?.messages.length ? suggest.job.messages : ["Reading the project…"]} running /> :
          sugg.length ? (
            <div className="ev-sugg">{sugg.map((s, i) => (
              <div key={i} className="ev-sugg__row">
                <div><b>{s.title}</b><span>{s.why}</span></div>
                <Button icon="ai" disabled={!mayEdit} onClick={() => onAsk(s.request)}>Ask AI</Button>
              </div>
            ))}</div>
          ) : <div className="ev-muted ev-small">No suggestions yet.</div>}
      </Card>

      <Card title="Needs attention" icon="alert"><Findings findings={a.findings} /></Card>
    </>
  );
}

// ── data & screens ───────────────────────────────────────────────────────────
function DataAndScreens({ info, mayEdit, onAsk }: { info: AdoptProjectInfo; mayEdit: boolean; onAsk: (r: string) => void }) {
  const a = info.analysis;
  return (
    <>
      <Card title="Data model" icon="database" subtitle={a.model.entities.length ? `${a.model.entities.length} entities from ${a.model.source}. ` +
        "Add a field below or ask the AI — you review the diff before anything is saved." : undefined}>
        {a.model.entities.length === 0 ? <div className="ev-muted ev-small">No CDS model, OData metadata, ORM models or SQL tables were recognised.</div> : (
          <EntityCards entities={a.model.entities} onAddField={mayEdit ? (e, f, t) => onAsk(
            `Add a field "${f}" (${t}) to the ${e} entity. Show it in the list and on the detail/object page, add the labels to i18n, ` +
            `include it in sample/seed data, and keep existing validations working.`) : undefined} />)}
        {a.model.services?.length > 0 && (
          <div className="ev-facts" style={{ marginTop: "1rem" }}>
            {a.model.services.map((sv) => (
              <Fact key={sv.name} label={sv.name}><span className="ev-mono">{sv.path || "—"}</span>
                {sv.actions?.length ? <span className="ev-muted"> · actions: {sv.actions.join(", ")}</span> : null}</Fact>
            ))}
          </div>
        )}
      </Card>
      <Card title="Screens" icon="screen-split-three" subtitle="Fiori Elements pages change through annotations; custom code is changed by AI with a diff."
        actions={mayEdit && <Button icon="ai" design="Transparent" onClick={() => onAsk("Add a new screen that ")}>Ask AI for a new screen</Button>}>
        <ScreensTable a={a} />
      </Card>
      {a.endpoints.length > 0 && <Card title="API endpoints" icon="chain-link"><EndpointsTable a={a} /></Card>}
    </>
  );
}

// ── AI assistant ─────────────────────────────────────────────────────────────
const STATUS_TONE: Record<string, "good" | "warn" | "bad" | "info" | "neutral" | "brand"> = {
  understanding: "info", needs_input: "warn", planned: "brand", working: "info", ready: "good", failed: "bad", applied: "good", discarded: "neutral",
};
const STATUS_TEXT: Record<string, string> = {
  understanding: "understanding", needs_input: "needs your input", planned: "plan ready", working: "working", ready: "ready for review",
  failed: "failed", applied: "applied", discarded: "discarded",
};

function Assistant({ pid, info, draft, setDraft, mayEdit, onPreview, onApplied }: {
  pid: string; info: AdoptProjectInfo; draft: string; setDraft: (s: string) => void; mayEdit: boolean;
  onPreview: (missionId: string) => void; onApplied: () => void;
}) {
  const toast = useToast();
  const mkey = `evoke.adopt.mission.${pid}`;
  const [mission, setMissionState] = useState<Mission | null>(null);
  const [mid, setMid] = useState<string>(() => ss.get(mkey, ""));
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [feedback, setFeedback] = useState("");
  const [reviseOpen, setReviseOpen] = useState(false);
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");
  const hist = useApi<{ missions: MissionSummary[] }>(`api/adopt/projects/${pid}/missions`);

  const setMission = (m: Mission | null) => {
    setMissionState(m);
    setMid(m?.id || ""); ss.set(mkey, m?.id || "");
    if (m) setAnswers(Object.fromEntries((m.questions || []).map((q) => [q.id, m.answers?.[q.id] ?? q.default ?? ""])));
    hist.reload(true);
  };
  const plan = useTrackedJob<Mission>(`robot-plan.${pid}`, (m) => { setMission(m); setDraft(""); }, (e) => setErr(e));
  const run = useTrackedJob<Mission>(`robot-run.${pid}`, (m) => {
    setMission(m);
    toast(m.status === "ready" ? "The AI change is ready for review" : `The AI assistant could not finish: ${m.error || m.status}`, m.status === "ready" ? "success" : "error");
  }, (e) => { setErr(e); if (mid) api.get<Mission>(`api/adopt/missions/${mid}`).then(setMission).catch(() => undefined); });
  const working = plan.running || run.running;
  const liveJob = plan.running ? plan.job : run.running ? run.job : undefined;

  useEffect(() => {
    if (mid && !mission) api.get<Mission>(`api/adopt/missions/${mid}`).then(setMission).catch(() => { setMid(""); ss.set(mkey, ""); });
  }, []); // eslint-disable-line

  const open = (id: string) => { setErr(""); api.get<Mission>(`api/adopt/missions/${id}`).then(setMission).catch((e) => setErr(e.message)); };
  const send = async () => {
    if (!draft.trim()) return;
    setErr(""); setMissionState(null);
    try { await plan.start("api/jobs/robot-plan", { project_id: pid, request: draft.trim() }); } catch (e: any) { setErr(e.message); }
  };
  const go = async (useDefaults = false) => {
    if (!mission) return;
    setErr("");
    const ans = useDefaults ? Object.fromEntries(mission.questions.map((q) => [q.id, q.default ?? ""])) : answers;
    try { await run.start("api/jobs/robot-run", { mission_id: mission.id, answers: ans }); setMissionState({ ...mission, answers: ans, status: "working" }); }
    catch (e: any) { setErr(e.message); }
  };
  const revise = async () => {
    if (!mission || !feedback.trim()) return;
    setErr("");
    try { await run.start("api/jobs/robot-revise", { mission_id: mission.id, feedback: feedback.trim() }); setFeedback(""); setReviseOpen(false); setMissionState({ ...mission, status: "working" }); }
    catch (e: any) { setErr(e.message); }
  };
  const act = async (what: "apply" | "discard") => {
    if (!mission) return;
    setBusy(what); setErr("");
    try {
      if (what === "apply") {
        const r = await api.post<{ version_id: string; number: number }>(`api/adopt/missions/${mission.id}/apply`, {});
        toast(`Saved as version ${r.number}`);
        onApplied();
        open(mission.id);
      } else {
        setMission(await api.post<Mission>(`api/adopt/missions/${mission.id}/discard`, {}));
        toast("Change discarded", "info");
      }
    } catch (e: any) { setErr(e.message); } finally { setBusy(""); }
  };
  const newRequest = () => { setMission(null); setErr(""); };

  const m = mission;
  const missions = hist.data?.missions || [];
  return (
    <div className="ev-robot">
      {err && <MessageStrip design="Negative" onClose={() => setErr("")}>{err}</MessageStrip>}
      {(!m || plan.running) && (
        <Card title="Ask AI" icon="ai"
          subtitle="Describe what you need in business terms. The AI asks for the type of application and anything unclear, proposes a design and a plan, builds it, runs the checks and fixes its own failures. You review everything before it is saved.">
          <div className="ev-robot__ask">
            <textarea className="ev-textarea" aria-label="Your request" rows={4} disabled={!mayEdit || working} value={draft}
              placeholder="For example: Build an approvals dashboard for managers with open requests, KPIs and approve / reject actions."
              onChange={(e) => setDraft(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) send(); }} />
            <div className="ev-actions" style={{ marginTop: 0 }}>
              <Button design="Emphasized" icon="paper-plane" disabled={!mayEdit || working || !draft.trim()} onClick={send}>Send</Button>
              <span className="ev-muted ev-small">Ctrl + Enter</span>
              {!mayEdit && <span className="ev-muted ev-small">You need developer rights on this project to request changes.</span>}
            </div>
          </div>
        </Card>
      )}
      {liveJob && (
        <Card title={plan.running ? "Understanding your request" : "Working on your request"} icon="ai"
          subtitle={plan.running ? "Reading the brief and the relevant code." : "Reading, designing, editing and checking — each step appears as it happens."}>
          <LiveTimeline messages={liveJob.messages.length ? liveJob.messages : ["Starting…"]} running />
        </Card>
      )}
      {m && <MissionView m={m} answers={answers} setAnswers={setAnswers} working={working} mayEdit={mayEdit} busy={busy}
        onRun={go} onApply={() => act("apply")} onDiscard={() => act("discard")} onPreview={() => onPreview(m.id)}
        reviseOpen={reviseOpen} setReviseOpen={setReviseOpen} feedback={feedback} setFeedback={setFeedback} onRevise={revise}
        onNew={newRequest} />}
      <Card title="Previous requests" icon="history" subtitle={`${missions.length} so far`}
        actions={m && <Button design="Transparent" icon="add" onClick={newRequest}>New request</Button>}>
        {hist.loading && !hist.data ? <Skeleton height={80} /> : missions.length === 0 ?
          <div className="ev-muted ev-small">No requests yet.</div> : (
            <div className="ev-tblwrap">
              <table className="ev-tbl">
                <thead><tr><th>Request</th><th>Status</th><th>Version</th><th>Files</th><th>When</th></tr></thead>
                <tbody>{missions.map((x) => (
                  <tr key={x.id} className={"is-click" + (x.id === m?.id ? " is-on" : "")} onClick={() => open(x.id)}>
                    <td style={{ maxWidth: 560 }}>{x.request.length > 140 ? x.request.slice(0, 140) + "…" : x.request}</td>
                    <td><StatusBadge tone={STATUS_TONE[x.status] || "neutral"}>{STATUS_TEXT[x.status] || x.status}</StatusBadge></td>
                    <td>{x.applied_version ? `v${x.applied_version.number}` : "—"}</td>
                    <td>{x.changes || "—"}</td>
                    <td className="ev-tbl__muted">{ago(x.created_at)}</td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
          )}
      </Card>
    </div>
  );
}

function MissionView({ m, answers, setAnswers, working, mayEdit, busy, onRun, onApply, onDiscard, onPreview, reviseOpen, setReviseOpen,
  feedback, setFeedback, onRevise, onNew }: {
  m: Mission; answers: Record<string, string>; setAnswers: (a: Record<string, string>) => void; working: boolean; mayEdit: boolean; busy: string;
  onRun: (useDefaults?: boolean) => void; onApply: () => void; onDiscard: () => void; onPreview: () => void;
  reviseOpen: boolean; setReviseOpen: (b: boolean) => void; feedback: string; setFeedback: (s: string) => void; onRevise: () => void; onNew: () => void;
}) {
  const done = ["ready", "failed", "applied", "discarded"].includes(m.status);
  const metCount = m.criteria_results?.filter((c) => c.met).length || 0;
  const hasType = m.questions?.some((q) => q.id === "app_type");
  const appType = m.answers?.app_type || answers.app_type || m.questions?.find((q) => q.id === "app_type")?.default;
  return (
    <>
      <Card title="Your request" icon="ai" subtitle={`${m.author} · ${ago(m.created_at)}${m.rounds ? ` · ${m.rounds} self-repair round(s)` : ""}`}
        actions={<><StatusBadge tone={STATUS_TONE[m.status] || "neutral"}>{STATUS_TEXT[m.status] || m.status}</StatusBadge>
          {m.applied_version && <StatusBadge tone="good" icon="save">version {m.applied_version.number}</StatusBadge>}</>}>
        <div className="ev-facts">
          <Fact label="You asked">{m.request}</Fact>
          {m.restated && <Fact label="AI understood"><Markdown text={m.restated} /></Fact>}
          {hasType && m.status !== "needs_input" && appType && <Fact label="Application type">{appType}</Fact>}
        </div>
        {m.error && <MessageStrip design="Negative" hideCloseButton style={{ marginTop: ".8rem" }}>{m.error}</MessageStrip>}
      </Card>

      {m.status === "needs_input" && !working && (
        <Card title="A few questions first" icon="question-mark" subtitle="The defaults are the AI's best guess — change what you want.">
          <div className="ev-form">
            {m.questions.map((q) => (
              <FormRow key={q.id} label={q.question} hint={q.id === "app_type" ? "The AI designs the screens for the type you choose." : undefined}>
                {q.options?.length ? (
                  <div className="ev-radios" role="radiogroup" aria-label={q.question}>
                    {q.options.map((o) => {
                      const on = (answers[q.id] ?? q.default) === o;
                      return (
                        <label key={o} className={"ev-radio" + (on ? " is-on" : "")}>
                          <input type="radio" name={`mq-${q.id}`} checked={on} disabled={!mayEdit} onChange={() => setAnswers({ ...answers, [q.id]: o })} />
                          <span>{o}</span>
                        </label>
                      );
                    })}
                  </div>
                ) : <Input value={answers[q.id] ?? ""} placeholder={q.default || ""} onInput={(e) => setAnswers({ ...answers, [q.id]: e.target.value })} />}
              </FormRow>
            ))}
          </div>
          <div className="ev-actions">
            <Button design="Emphasized" icon="play" disabled={!mayEdit} onClick={() => onRun(false)}>Continue</Button>
            <Button design="Transparent" disabled={!mayEdit} onClick={() => onRun(true)}>Use the defaults</Button>
            <span className="ev-spacer" />
            <Button design="Transparent" onClick={onNew}>Change the request</Button>
          </div>
        </Card>
      )}

      {(m.design?.length || 0) > 0 && (
        <Card title="Proposed design" icon="palette" subtitle={hasType && appType ? `Application type: ${appType}` : "The screens the AI will build."}>
          <div className="ev-tblwrap">
            <table className="ev-tbl">
              <thead><tr><th>Screen</th><th>Type</th><th>Purpose</th><th>Layout and sections</th></tr></thead>
              <tbody>{m.design!.map((d, i) => (
                <tr key={i}>
                  <td><b>{d.screen}</b></td><td>{d.type}</td><td>{d.purpose}</td>
                  <td>{d.layout}{d.sections?.length > 0 && <ul className="ev-design__secs">
                    {d.sections.map((x) => <li key={x}>{x}</li>)}</ul>}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        </Card>
      )}

      {(m.criteria?.length > 0 || m.plan?.length > 0) && (
        <Card title="Plan" icon="task" subtitle={m.status === "planned" ? "Review the design and the plan, then let the AI build it." : undefined}>
          <div className="ev-facts">
            <Fact label="Acceptance criteria">
              {done && m.criteria_results?.length ? (
                <div className="ev-crit">
                  {m.criteria_results.map((c, i) => (
                    <div key={i} className={"ev-crit__row " + (c.met ? "is-met" : "is-not")}>
                      <Icon name={c.met ? "accept" : "decline"} /><div>{c.criterion}{c.evidence && <small>{c.evidence}</small>}</div>
                    </div>
                  ))}
                </div>
              ) : <ul>{m.criteria.map((c, i) => <li key={i}>{c}</li>)}</ul>}
            </Fact>
            <Fact label="Steps">
              <ol>{m.plan.map((p, i) => (
                <li key={i}>{p.step}
                  {p.files?.length > 0 && <div className="ev-files2" style={{ marginTop: ".25rem" }}>{p.files.map((f) => <span key={f} className="ev-chip2 ev-mono">{f}</span>)}</div>}
                </li>
              ))}</ol>
            </Fact>
          </div>
          {m.status === "planned" && !working && (
            <div className="ev-actions">
              <Button design="Emphasized" icon="play" disabled={!mayEdit} onClick={() => onRun(false)}>Build it</Button>
              <Button design="Transparent" onClick={onNew}>Change the request</Button>
            </div>
          )}
        </Card>
      )}

      {done && (m.summary || m.changes?.length > 0) && (
        <Card title="Result" icon="inspection"
          subtitle={m.criteria_results?.length ? `${metCount} of ${m.criteria_results.length} acceptance criteria met` : undefined}>
          <div className="ev-facts">
            {m.summary && <Fact label="What changed"><Markdown text={m.summary} /></Fact>}
            <Fact label="Checks"><ChecksSummary report={m.checks} /></Fact>
            {m.changes?.length > 0 && <Fact label="Files">
              <div className="ev-files2">{m.changes.map((c) => (
                <span key={c.path} className="ev-chip2"><StatusBadge tone={c.status === "added" ? "good" : c.status === "deleted" ? "bad" : "info"}>{c.status}</StatusBadge>
                  <span className="ev-mono">{c.path}</span><span className="ev-add">+{c.additions}</span><span className="ev-del">−{c.deletions}</span></span>
              ))}</div></Fact>}
          </div>
          {(m.status === "ready" || m.status === "failed") && (
            <div className="ev-actions">
              {m.status === "ready" && <Button design="Emphasized" icon="save" disabled={!mayEdit || !!busy || working} onClick={onApply}>{busy === "apply" ? "Saving…" : "Apply as new version"}</Button>}
              {m.changes?.length > 0 && <Button icon="play" disabled={working} onClick={onPreview}>Preview this change</Button>}
              <Button icon="edit" disabled={!mayEdit || working} onClick={() => setReviseOpen(!reviseOpen)}>{m.status === "failed" ? "Try again with guidance" : "Revise"}</Button>
              <Button design="Transparent" icon="delete" disabled={!mayEdit || !!busy || working} onClick={onDiscard}>Discard</Button>
            </div>
          )}
          {reviseOpen && (
            <div className="ev-robot__ask" style={{ marginTop: ".8rem" }}>
              <textarea className="ev-textarea" rows={3} aria-label="What should change" value={feedback} onChange={(e) => setFeedback(e.target.value)}
                placeholder="What should the AI change? For example: use a split screen for the details and show the totals in a header." />
              <div className="ev-actions" style={{ marginTop: 0 }}>
                <Button design="Emphasized" icon="paper-plane" disabled={!feedback.trim()} onClick={onRevise}>Send</Button>
                <Button design="Transparent" onClick={() => setReviseOpen(false)}>Cancel</Button>
              </div>
            </div>
          )}
        </Card>
      )}

      {m.diff && <Card title="Code changes" icon="compare" subtitle="Compared with the version the AI started from."><DiffView diff={m.diff} /></Card>}

      {m.steps?.length > 0 && (
        <Card title="Activity" icon="activity-items">
          <details>
            <summary className="ev-small">{m.steps.length} steps</summary>
            <div className="ev-crit" style={{ marginTop: ".5rem" }}>
              {m.steps.map((st, i) => (
                <div key={i} className={"ev-crit__row " + (st.ok ? "is-met" : "is-not")}>
                  <Icon name={({ think: "ai", read: "document-text", search: "search", edit: "edit", check: "shield", run: "play", ask: "question-mark", info: "message-information" } as Record<string, string>)[st.kind] || "activity-2"} />
                  <div className="ev-small">{st.text}</div>
                </div>
              ))}
            </div>
          </details>
        </Card>
      )}
    </>
  );
}

// ── try it ───────────────────────────────────────────────────────────────────
function TryIt({ info, preview, setPreview, job, err, onStart }: {
  info: AdoptProjectInfo; preview: PreviewInfo | null; setPreview: (p: PreviewInfo | null) => void;
  job: ReturnType<typeof useTrackedJob<PreviewInfo>>; err: string; onStart: () => void;
}) {
  const toast = useToast();
  const [device, setDevice] = useState<Device>("desktop");
  const [log, setLog] = useState("");
  const [frameKey, setFrameKey] = useState(0);
  const stop = async () => {
    if (!preview) return;
    try { await api.post(`api/adopt/preview/${preview.preview_id}/stop`, {}); } catch { /* already gone */ }
    setPreview(null); setLog(""); toast("Preview stopped", "info");
  };
  const showLog = async () => {
    if (!preview) return;
    try { setLog(await api.text(`api/adopt/preview/${preview.preview_id}/log`)); } catch (e: any) { setLog(e.message); }
  };
  if (job.running) return (
    <Card title="Starting the preview" icon="play" subtitle="Installing dependencies and starting the app in the sandbox. The first start of a project takes longest.">
      <LiveTimeline messages={job.job?.messages.length ? job.job.messages : ["Starting…"]} running />
    </Card>
  );
  if (!preview) return (
    <Card title="Try the app" icon="play">
      {err && <MessageStrip design="Negative" hideCloseButton style={{ marginBottom: ".8rem" }}>{err}</MessageStrip>}
      {err && job.job?.messages?.length ? <pre className="ev-log" style={{ marginBottom: ".8rem" }}>{job.job.messages.join("\n")}</pre> : null}
      <EmptyState icon="play" title="Run the app in the sandbox"
        text={`Starts version ${info.version.number} with the preview setup from the project brief. Nothing is deployed.`}
        action={<Button design="Emphasized" icon="play" disabled={!info.runner?.ok} onClick={onStart}>Start preview</Button>} />
      {!info.runner?.ok && <div className="ev-muted ev-small" style={{ textAlign: "center" }}>The sandbox is not available, so the app cannot be started here.</div>}
    </Card>
  );
  return (
    <Card title="Try the app" icon="play" subtitle={`${preview.kind}${preview.entry ? ` · ${preview.entry}` : ""}`}>
      <div className="ev-tryit__bar">
        <SegmentedButton accessibleName="Device" onSelectionChange={(e) => setDevice(((e.detail.selectedItems[0] as any)?.dataset?.device || "desktop") as Device)}>
          {(["desktop", "tablet", "phone"] as Device[]).map((d) => (
            <SegmentedButtonItem key={d} data-device={d} selected={device === d} icon={d === "desktop" ? "laptop" : d === "tablet" ? "ipad" : "iphone"}>{d}</SegmentedButtonItem>
          ))}
        </SegmentedButton>
        <span className="ev-spacer" />
        <Button design="Transparent" icon="refresh" onClick={() => setFrameKey((k) => k + 1)}>Reload</Button>
        <a href={preview.url} target="_blank" rel="noreferrer" className="ev-linkbtn"><Icon name="action" style={{ width: ".8rem", height: ".8rem", color: "inherit" }} /> Open in a new tab</a>
        <Button design="Transparent" icon="document-text" onClick={showLog}>Log</Button>
        <Button design="Transparent" icon="restart" onClick={onStart}>Restart</Button>
        <Button design="Transparent" icon="stop" onClick={stop}>Stop</Button>
      </div>
      {log && <pre className="ev-log" style={{ marginBottom: ".8rem" }}>{log}</pre>}
      <DeviceFrame key={frameKey} device={device} src={preview.url} title={preview.entry || preview.url} />
      <div className="ev-muted ev-small" style={{ marginTop: ".6rem" }}>If the preview stays blank, your browser may block third-party cookies — use Open in a new tab.</div>
    </Card>
  );
}

// ── brief ────────────────────────────────────────────────────────────────────
function Brief({ pid, info, mayEdit, onSaved }: { pid: string; info: AdoptProjectInfo; mayEdit: boolean; onSaved: () => void }) {
  const toast = useToast();
  const questions = useMemo(() => info.analysis.questions?.length ? info.analysis.questions : questionsFromBrief(info.brief || {}), [info]);
  const initial = useMemo(() => ({ ...Object.fromEntries(questions.map((q) => [q.id, q.value])), ...(info.brief || {}) }), [questions, info]);
  const [answers, setAnswers] = useState<Answers>(initial);
  const [saving, setSaving] = useState(false);
  const dirty = JSON.stringify(answers) !== JSON.stringify(initial);
  const save = async () => {
    setSaving(true);
    try {
      const r = await api.post<{ version_id: string }>(`api/adopt/projects/${pid}/brief`, { answers });
      toast(r?.version_id ? "Brief saved as a new version" : "Brief saved");
      onSaved();
    } catch (e: any) { toast(e.message, "error"); } finally { setSaving(false); }
  };
  return (
    <Card title="Project brief" icon="document-text"
      subtitle="What the AI knows about this project beyond the code. Every AI change and every validation uses it."
      actions={mayEdit && <Button design="Emphasized" icon="save" disabled={!dirty || saving} onClick={save}>{saving ? "Saving…" : "Save brief"}</Button>}>
      <QuestionList questions={questions} answers={answers} onChange={setAnswers} disabled={!mayEdit || saving} />
    </Card>
  );
}

// ── deploy ───────────────────────────────────────────────────────────────────
interface DeployOptions { targets: { value: string; label: string; hint?: string }[]; default: string; env_gate: Record<string, { allowed: boolean; reason: string }> }
interface DeployResult { status: string; url?: string; log?: string; pr_url?: string; mtar?: string }

function Deploy({ info, versions, mayDeploy, vid, setVid, onDone }: {
  info: AdoptProjectInfo; versions: VersionMeta[]; mayDeploy: boolean; vid: string; setVid: (v: string) => void; onDone: () => void;
}) {
  const toast = useToast();
  const opts = useApi<DeployOptions>(`api/adopt/deploy/${vid}`, { deps: [vid] });
  const envs = useApi<{ envs: Record<string, any> }>("api/environments");
  const envKeys = Object.keys(envs.data?.envs || { dev: {} });
  const [env, setEnv] = useState("dev");
  const [target, setTarget] = useState("");
  const [git, setGit] = useState({ token: "", base_branch: "", branch: "", title: "" });
  const [result, setResult] = useState<DeployResult | null>(null);
  const [err, setErr] = useState("");
  const job = useTrackedJob<DeployResult>(`adopt-deploy.${info.project.id}`, (r) => {
    setResult(r); onDone();
    toast(r.status === "succeeded" ? "Deployment finished" : `Deployment ${r.status}`, r.status === "succeeded" ? "success" : "error");
  }, (e) => setErr(e));
  useEffect(() => { if (opts.data && !target) setTarget(opts.data.default || opts.data.targets[0]?.value || ""); }, [opts.data]); // eslint-disable-line
  const gate = opts.data?.env_gate?.[env];
  const ver = versions.find((v) => v.id === vid);
  const isGit = target === "git-pr";
  const start = async () => {
    setErr(""); setResult(null);
    try {
      await job.start("api/jobs/adopt-deploy", { version_id: vid, env, target, ...(isGit ? { git: { token: git.token, base_branch: git.base_branch || undefined, branch: git.branch || undefined, title: git.title || undefined } } : {}) });
      setGit((g) => ({ ...g, token: "" }));
    } catch (e: any) { setErr(e.message); }
  };
  const verHint = ver ? (ver.validation ? (ver.validation.blocking?.length ? "Validation found blocking problems." : `Validated: ${ver.validation.summary.passed} checks passed.`)
    : "Not validated yet — Versions tab → Validate.") : undefined;
  return (
    <Card title="Deploy" icon="cloud" subtitle="Production needs a validated version approved by someone other than its author.">
      {opts.error && <MessageStrip design="Negative" hideCloseButton>{opts.error}</MessageStrip>}
      <div className="ev-form">
        <FormRow label="Version" hint={verHint}>
          <Select onChange={(e) => setVid((e.detail.selectedOption as any).value)} disabled={job.running}>
            {versions.map((v) => <Option key={v.id} value={v.id} selected={v.id === vid}>{`v${v.number} · ${v.label || "no label"}`}</Option>)}
          </Select>
        </FormRow>
        <FormRow label="Environment" hint="dev deploys right away; production needs an approved, validated version.">
          <Select onChange={(e) => setEnv((e.detail.selectedOption as any).value)} disabled={job.running}>
            {envKeys.map((k) => <Option key={k} value={k} selected={k === env}>{k}</Option>)}
          </Select>
          {gate && !gate.allowed && <span className="ev-field__hint" style={{ color: "var(--sapCriticalTextColor)" }}>{gate.reason}</span>}
        </FormRow>
        <FormRow label="Target" hint={opts.data?.targets.find((t) => t.value === target)?.hint}>
          {opts.loading && !opts.data ? <Skeleton height={36} /> : (
            <Select onChange={(e) => setTarget((e.detail.selectedOption as any).value)} disabled={job.running}>
              {(opts.data?.targets || []).map((t) => <Option key={t.value} value={t.value} selected={t.value === target}>{t.label}</Option>)}
            </Select>)}
        </FormRow>
        {isGit && <>
          <FormRow label="Access token" hint="Needs write access. Used for this push only, not stored." htmlFor="dp-tok">
            <Input id="dp-tok" type="Password" value={git.token} onInput={(e) => setGit({ ...git, token: e.target.value })} />
          </FormRow>
          <FormRow label="Base branch" htmlFor="dp-base">
            <Input id="dp-base" placeholder={info.analysis.source.branch || "main"} value={git.base_branch} onInput={(e) => setGit({ ...git, base_branch: e.target.value })} />
          </FormRow>
          <FormRow label="New branch" htmlFor="dp-br">
            <Input id="dp-br" placeholder={`evoke/v${ver?.number ?? ""}`} value={git.branch} onInput={(e) => setGit({ ...git, branch: e.target.value })} />
          </FormRow>
          <FormRow label="Pull request title" htmlFor="dp-title">
            <Input id="dp-title" placeholder={ver?.label || "Changes from Evoke Fiori App Studio"} value={git.title} onInput={(e) => setGit({ ...git, title: e.target.value })} />
          </FormRow>
        </>}
      </div>
      <div className="ev-actions">
        <Button design="Emphasized" icon="paper-plane" disabled={!mayDeploy || job.running || !target || (gate && !gate.allowed) || (isGit && !git.token)} onClick={start}>
          {job.running ? "Deploying…" : target === "download" ? "Prepare download" : `Deploy v${ver?.number ?? ""} to ${env}`}</Button>
        {!mayDeploy && <span className="ev-muted ev-small">You have no deploy permission for this project.</span>}
      </div>
      {err && <MessageStrip design="Negative" onClose={() => setErr("")} style={{ marginTop: ".8rem" }}>{err}</MessageStrip>}
      {job.running && <div style={{ marginTop: ".8rem" }}><LiveTimeline messages={job.job?.messages.length ? job.job.messages : ["Starting…"]} running title="Deployment" /></div>}
      {result && (
        <div className="ev-facts" style={{ marginTop: "1rem" }}>
          <Fact label="Result"><StatusBadge tone={result.status === "succeeded" ? "good" : result.status === "skipped" ? "warn" : "bad"}>{result.status}</StatusBadge></Fact>
          {result.url && <Fact label="App"><a href={result.url} target="_blank" rel="noreferrer">{result.url.replace(/^https?:\/\//, "")} ↗</a></Fact>}
          {result.pr_url && <Fact label="Pull request"><a href={result.pr_url} target="_blank" rel="noreferrer">{result.pr_url} ↗</a></Fact>}
          {result.log && <Fact label="Log"><details><summary className="ev-small">Show the deployment log</summary><pre className="ev-log">{result.log}</pre></details></Fact>}
        </div>
      )}
      {!opts.data && !opts.loading && !opts.error && <BusyIndicator active size="S" />}
    </Card>
  );
}
