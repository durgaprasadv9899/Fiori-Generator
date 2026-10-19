// Adopt an existing project: bring it (ZIP, Git, Cloud Foundry) → what we found → fill the gaps →
// the project opens in its workspace. Wizard state survives reloads (sessionStorage).
import { useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { useNavigate } from "react-router-dom";
import { BusyIndicator, Button, Icon, Input, MessageStrip } from "@ui5/webcomponents-react";
import { Card, EmptyState, LiveTimeline, Skeleton, StatusBadge } from "../../components/ui";
import { useToast } from "../../components/shell/Toasts";
import { api, ApiError } from "../../api/client";
import type { Analysis, Answers } from "../../api/types";
import { Stepper } from "../studio/Stepper";
import type { StepDef } from "../studio/flow";
import { Fact, FormRow, QuestionList, defaultAnswers } from "./Questions";
import { ArchDiagram, CapMatrix, Findings, LanguageBar, ModuleCards, StackChips, UnderstandingBlock, fmtBytes } from "./parts";
import { useTrackedJob } from "./useTrackedJob";
import { CfPasscodeSignIn, isNoCfSession } from "../../components/shell/CfSignIn";
import "../studio/studio.css";
import "../../components/shell/import.css";
import "./adopt.css";

type SourceKind = "zip" | "git" | "cf";
interface WizardState {
  step: number; kind: SourceKind; upload?: { upload_id: string; name: string; bytes: number };
  git: { url: string; branch: string }; cfApp: string; aid: string; answers: Answers; name: string;
}
const TECH: [string, string][] = [
  ["database", "SAP CAP"], ["table-view", "Fiori Elements"], ["sap-ui5", "SAPUI5"], ["source-code", "React"],
  ["source-code", "Angular"], ["source-code", "Vue"], ["syntax", "Node.js · TypeScript"], ["syntax", "Python"],
];
const KEY = "evoke.adopt.wizard";
const INITIAL: WizardState = { step: 0, kind: "zip", git: { url: "", branch: "" }, cfApp: "", aid: "", answers: {}, name: "" };
const ok = (v: unknown) => !!v;

const STEPS: StepDef[] = [
  { key: "source", label: "Bring the project", hint: "ZIP, Git or Cloud Foundry", icon: "upload-to-cloud", ready: () => true },
  { key: "found", label: "What we found", hint: "Stack, modules, gaps", icon: "inspection", ready: () => true },
  { key: "gaps", label: "Fill the gaps", hint: "Only what the code can't tell", icon: "edit", ready: () => true },
  { key: "workspace", label: "Project workspace", hint: "Preview, AI, deploy", icon: "action", ready: () => true },
];

function load(): WizardState {
  try { return { ...INITIAL, ...JSON.parse(sessionStorage.getItem(KEY) || "{}") }; } catch { return INITIAL; }
}

export default function AdoptPage() {
  const nav = useNavigate();
  const toast = useToast();
  const [st, setSt] = useState<WizardState>(load);
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [loadingA, setLoadingA] = useState(!!st.aid);
  const [err, setErr] = useState("");
  const [token, setToken] = useState("");          // never stored
  const [creating, setCreating] = useState(false);
  const patch = (p: Partial<WizardState>) => setSt((s) => ({ ...s, ...p }));

  useEffect(() => { try { sessionStorage.setItem(KEY, JSON.stringify(st)); } catch { /* storage unavailable */ } }, [st]);
  useEffect(() => {                                  // restore the analysis after a reload
    if (!st.aid || analysis) { setLoadingA(false); return; }
    api.get<Analysis>(`api/adopt/analysis/${st.aid}`).then(setAnalysis)
      .catch(() => { patch({ aid: "", step: 0 }); toast("The previous analysis has expired — analyse the project again.", "info"); })
      .finally(() => setLoadingA(false));
  }, []); // eslint-disable-line

  const analyze = useTrackedJob<Analysis>("adopt-analyze", (a) => {
    setAnalysis(a);
    setSt((s) => ({ ...s, aid: a.id, step: 1, name: a.name, answers: defaultAnswers(a.questions || []) }));
    toast(`Analysed ${a.name}: ${a.modules.length} module(s)`);
  }, (e) => setErr(e));

  const source = st.kind === "zip" ? (st.upload ? { kind: "zip", upload_id: st.upload.upload_id } : null)
    : st.kind === "git" ? (/^https:\/\//.test(st.git.url.trim()) ? { kind: "git", url: st.git.url.trim(), branch: st.git.branch.trim() || undefined, token: token || undefined } : null)
      : st.cfApp ? { kind: "cf", app: st.cfApp } : null;

  const startAnalysis = async () => {
    if (!source) return;
    setErr("");
    try { await analyze.start("api/jobs/adopt-analyze", { source }); }
    catch (e: any) { setErr(e.message); }
  };

  const create = async () => {
    if (!analysis) return;
    setCreating(true); setErr("");
    try {
      const r = await api.post<{ project_id: string; version_id: string }>(`api/adopt/${analysis.id}/create`, { name: st.name.trim() || analysis.name, answers: st.answers });
      toast(`“${st.name || analysis.name}” adopted — version 1 saved`);
      try { sessionStorage.removeItem(KEY); } catch { /* ignore */ }
      nav(`/projects/${r.project_id}`);
    } catch (e: any) { setErr(e.message); } finally { setCreating(false); }
  };

  const reachable = analysis ? 2 : 0;
  const go = (key: string) => {
    const i = STEPS.findIndex((s) => s.key === key);
    if (i <= reachable) patch({ step: i });
  };
  const reset = () => { setAnalysis(null); setSt(INITIAL); setToken(""); setErr(""); };

  return (
    <div>
      <header className="ev-adhead">
        <div className="ev-adhead__row">
          <div className="ev-adhead__icon"><Icon name="upload-to-cloud" /></div>
          <div className="ev-adhead__main">
            <nav className="ev-adhead__crumbs" aria-label="Breadcrumb">
              <button className="ev-linkbtn" onClick={() => nav("/projects")}>Projects</button><Icon name="slim-arrow-right" /><span>Adopt project</span>
            </nav>
            <h1 className="ev-adhead__title">Adopt an existing project</h1>
            <p className="ev-adhead__sub">Bring an existing application into the studio to preview it, change it with AI, validate and deploy it.</p>
          </div>
          {analysis && <div className="ev-adhead__actions"><Button design="Transparent" icon="restart" onClick={reset}>Start over</Button></div>}
        </div>
        <div className="ev-adhead__tech">
          <span className="ev-adhead__techl">Supported</span>
          {TECH.map(([icon, label]) => <span key={label} className="ev-adhead__chip"><Icon name={icon} />{label}</span>)}
        </div>
      </header>
      <div className="ev-content ev-adopt">
        <Stepper steps={STEPS} current={st.step} reachable={reachable} onGo={go} />
        {err && <MessageStrip design="Negative" onClose={() => setErr("")}>{err}</MessageStrip>}
        <AnimatePresence mode="wait">
          <motion.div key={st.step} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -6 }} transition={{ duration: 0.2 }}>
            {loadingA ? <Skeleton height={320} radius={16} /> :
              st.step === 0 ? (
                analyze.running ? (
                  <Card title="Analysing the project" icon="inspection" subtitle="Reading descriptors, the data model, screens and build setup. Nothing is executed yet.">
                    <LiveTimeline messages={analyze.job?.messages.length ? analyze.job.messages : ["Starting…"]} running title="Analysis" />
                  </Card>
                ) : (
                  <SourceStep st={st} patch={patch} token={token} setToken={setToken} canGo={ok(source)} onAnalyze={startAnalysis} onError={setErr} />
                )
              ) : !analysis ? (
                <EmptyState icon="inspection" title="No analysis yet" text="Bring the project first." action={<Button onClick={() => patch({ step: 0 })}>Bring the project</Button>} />
              ) : st.step === 1 ? (
                <FoundStep a={analysis} onNext={() => patch({ step: 2 })} onBack={() => patch({ step: 0 })} />
              ) : (
                <GapsStep a={analysis} name={st.name} setName={(name) => patch({ name })} answers={st.answers}
                  setAnswers={(answers) => patch({ answers })} busy={creating} onBack={() => patch({ step: 1 })} onCreate={create} />
              )}
          </motion.div>
        </AnimatePresence>
      </div>
    </div>
  );
}

// ── step 1 ───────────────────────────────────────────────────────────────────
function SourceStep({ st, patch, token, setToken, canGo, onAnalyze, onError }: {
  st: WizardState; patch: (p: Partial<WizardState>) => void; token: string; setToken: (t: string) => void;
  canGo: boolean; onAnalyze: () => void; onError: (e: string) => void;
}) {
  const cards: [SourceKind, string, string, string][] = [
    ["zip", "upload-to-cloud", "Upload a ZIP", "A project folder or a BAS / Git export, up to 200 MB."],
    ["git", "source-code", "Git repository", "GitHub, GitLab, Bitbucket or SAP Git — one branch."],
    ["cf", "cloud", "Cloud Foundry app", "Download an app that runs in your BTP space."],
  ];
  return (
    <Card title="Bring the project" icon="upload-to-cloud" subtitle="The studio reads the project in a sandbox. Your source is never changed until you approve a change.">
      <div className="ev-srcs" role="radiogroup" aria-label="Source">
        {cards.map(([k, icon, t, s]) => (
          <button key={k} role="radio" aria-checked={st.kind === k} className={"ev-src" + (st.kind === k ? " is-on" : "")} onClick={() => patch({ kind: k })}>
            <span className="ev-src__icon"><Icon name={icon} /></span><b>{t}</b><span>{s}</span>
          </button>
        ))}
      </div>
      <div style={{ marginTop: "1rem" }}>
        {st.kind === "zip" && <ZipSource st={st} patch={patch} onError={onError} />}
        {st.kind === "git" && (
          <div className="ev-form">
            <FormRow label="Repository URL" hint="https:// address of the repository." htmlFor="ad-git-url">
              <Input id="ad-git-url" placeholder="https://github.com/org/repo" value={st.git.url} onInput={(e) => patch({ git: { ...st.git, url: e.target.value } })} />
            </FormRow>
            <FormRow label="Branch" hint="Leave empty for the default branch." htmlFor="ad-git-branch">
              <Input id="ad-git-branch" placeholder="default branch" value={st.git.branch} onInput={(e) => patch({ git: { ...st.git, branch: e.target.value } })} />
            </FormRow>
            <FormRow label="Access token" hint="Only for private repositories. Used for this clone only, not stored." htmlFor="ad-git-token">
              <Input id="ad-git-token" type="Password" placeholder="optional" value={token} onInput={(e) => setToken(e.target.value)} />
            </FormRow>
          </div>
        )}
        {st.kind === "cf" && <CfSource value={st.cfApp} onPick={(cfApp) => patch({ cfApp })} />}
      </div>
      <div className="ev-actions">
        <Button design="Emphasized" icon="inspection" disabled={!canGo} onClick={onAnalyze}>Analyse project</Button>
        {!canGo && <span className="ev-muted ev-small">{st.kind === "zip" ? "Upload a ZIP first." : st.kind === "git" ? "Enter an https:// repository URL." : "Pick an app."}</span>}
      </div>
    </Card>
  );
}

function ZipSource({ st, patch, onError }: { st: WizardState; patch: (p: Partial<WizardState>) => void; onError: (e: string) => void }) {
  const ref = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  const [busy, setBusy] = useState("");
  const upload = async (f?: File) => {
    if (!f) return;
    if (!/\.zip$/i.test(f.name)) { onError("Choose a .zip file."); return; }
    setBusy(f.name); onError("");
    try {
      const fd = new FormData();
      fd.append("file", f);
      const r = await api.post<{ upload_id: string; name: string; bytes: number }>("api/adopt/upload", fd);
      patch({ upload: { upload_id: r.upload_id, name: r.name || f.name, bytes: r.bytes ?? f.size } });
    } catch (e: any) { onError(e.message); } finally { setBusy(""); if (ref.current) ref.current.value = ""; }
  };
  return (
    <div className={"ev-drop" + (over ? " is-over" : "")} role="button" tabIndex={0} onClick={() => !busy && ref.current?.click()}
      onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") ref.current?.click(); }}
      onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)}
      onDrop={(e) => { e.preventDefault(); setOver(false); upload(e.dataTransfer.files?.[0]); }}>
      {busy ? <><BusyIndicator active size="M" /><b>Uploading {busy}…</b></> : st.upload ? <>
        <span className="ev-drop__icon"><Icon name="accept" /></span>
        <b>{st.upload.name}</b><span className="ev-muted ev-small">{fmtBytes(st.upload.bytes)} uploaded · click to choose another file</span>
      </> : <>
        <span className="ev-drop__icon"><Icon name="upload-to-cloud" /></span>
        <b>Drop a ZIP here or click to choose</b>
        <span className="ev-muted ev-small">mta.yaml, package.json, ui5.yaml, webapp/, requirements.txt are found anywhere in the archive. node_modules and build output are skipped.</span>
      </>}
      <input ref={ref} type="file" accept=".zip" hidden onChange={(e) => upload(e.target.files?.[0])} />
    </div>
  );
}

function CfSource({ value, onPick }: { value: string; onPick: (n: string) => void }) {
  const [data, setData] = useState<{ apps: { name: string; state: string; in_workspace?: boolean }[]; org: string; space: string } | null>(null);
  const [err, setErr] = useState("");
  const [needsLogin, setNeedsLogin] = useState(false);
  const [q, setQ] = useState("");
  const load = () => {
    setErr(""); setData(null); setNeedsLogin(false);
    api.get<any>("api/cf/apps").then(setData).catch((e: ApiError) => {
      if (isNoCfSession(e)) setNeedsLogin(true);
      else setErr(e.message);
    });
  };
  useEffect(load, []);
  const apps = useMemo(() => (data?.apps || []).filter((a) => !q || a.name.toLowerCase().includes(q.toLowerCase()))
    .sort((a, b) => a.name.localeCompare(b.name)), [data, q]);
  if (needsLogin) return <CfPasscodeSignIn onSignedIn={load} />;
  if (err) return <MessageStrip design="Critical" hideCloseButton>{err} <Button design="Transparent" icon="refresh" onClick={load}>Retry</Button></MessageStrip>;
  if (!data) return <div className="ev-row"><BusyIndicator active size="S" /><span className="ev-muted ev-small">Reading the apps from Cloud Foundry…</span></div>;
  return (
    <div>
      <div className="ev-row">
        <Icon name="cloud" /><span className="ev-small">{data.org} · <b>{data.space}</b></span><span className="ev-muted ev-small">{data.apps.length} apps</span>
        <span className="ev-spacer" />
        <Input icon={<Icon name="search" />} placeholder="Search apps…" value={q} onInput={(e) => setQ(e.target.value)} showClearIcon style={{ width: 260 }} />
      </div>
      <div className="ev-adopt-cf" role="listbox" aria-label="Cloud Foundry apps">
        {apps.length === 0 ? <div className="ev-muted ev-small" style={{ padding: ".8rem" }}>No app matches.</div> : apps.map((a) => (
          <div key={a.name} role="option" aria-selected={value === a.name} tabIndex={0} className={"ev-adopt-cf__row" + (value === a.name ? " is-on" : "")}
            onClick={() => onPick(a.name)} onKeyDown={(e) => { if (e.key === "Enter") onPick(a.name); }}>
            <span className={"ev-cfapp__state" + (a.state === "started" ? " is-on" : "")} title={a.state} />
            <span className="ev-mono" style={{ flex: 1 }}>{a.name}</span>
            {a.in_workspace && <StatusBadge tone="info">in workspace</StatusBadge>}
            <span className="ev-muted ev-small">{a.state}</span>
            {value === a.name && <Icon name="accept" style={{ color: "var(--sapBrandColor)" }} />}
          </div>
        ))}
      </div>
    </div>
  );
}

// ── step 2 ───────────────────────────────────────────────────────────────────
function FoundStep({ a, onNext, onBack }: { a: Analysis; onNext: () => void; onBack: () => void }) {
  const src = a.source;
  return (
    <div className="ev-adopt">
      <Card title={`What we found in ${a.name}`} icon="inspection"
        subtitle={`${src.label || src.kind} · ${a.files} files · ${fmtBytes(a.bytes)}${src.commit ? ` · commit ${src.commit.slice(0, 7)}` : ""}`}>
        <div className="ev-facts">
          <Fact label="Stack"><StackChips stack={a.stack} /></Fact>
          <Fact label="Modules">{a.modules.length} · {a.modules.map((m) => m.name).join(", ")}</Fact>
          <Fact label="Data model">{a.model.entities.length ? `${a.model.entities.length} entities from ${a.model.source}` : "none recognised"}</Fact>
          <Fact label="Screens & APIs">{a.screens.length} screens · {a.endpoints.length} API endpoints</Fact>
          {a.destinations.length > 0 && <Fact label="Connections">{a.destinations.map((d) => d.name).join(", ")}</Fact>}
        </div>
      </Card>
      <Card title="How it fits together" icon="org-chart">
        <ArchDiagram arch={a.arch} />
        <div style={{ marginTop: "1rem" }}><LanguageBar languages={a.languages} /></div>
      </Card>
      {a.understanding && (
        <Card title="Business scenario" icon="ai" subtitle="How the AI reads the code. Correct it in the next step.">
          <UnderstandingBlock u={a.understanding} />
        </Card>
      )}
      <Card title="Modules" icon="dimension" subtitle="Each part gets the deepest support its framework allows.">
        <ModuleCards modules={a.modules} />
      </Card>
      <Card title="What the studio can do per module" icon="grid">
        <CapMatrix modules={a.modules} />
      </Card>
      <Card title="Needs attention" icon="alert">
        <Findings findings={a.findings} />
        {a.skipped.length > 0 && (
          <details style={{ marginTop: ".7rem" }}><summary className="ev-muted ev-small">{a.skipped.length} file(s) not imported</summary>
            <ul className="ev-small">{a.skipped.slice(0, 50).map((s) => <li key={s.path}><span className="ev-mono">{s.path}</span> — {s.why}</li>)}</ul></details>
        )}
      </Card>
      <div className="ev-actions" style={{ marginTop: 0 }}>
        <Button design="Transparent" icon="nav-back" onClick={onBack}>Back</Button>
        <span className="ev-spacer" />
        <Button design="Emphasized" endIcon="navigation-right-arrow" onClick={onNext}>Fill the gaps</Button>
      </div>
    </div>
  );
}

// ── step 3 ───────────────────────────────────────────────────────────────────
function GapsStep({ a, name, setName, answers, setAnswers, busy, onBack, onCreate }: {
  a: Analysis; name: string; setName: (n: string) => void; answers: Answers; setAnswers: (a: Answers) => void;
  busy: boolean; onBack: () => void; onCreate: () => void;
}) {
  return (
    <div className="ev-adopt">
      <Card title="Fill the gaps" icon="edit"
        subtitle="Pre-filled from the code. Correct anything that is wrong — this becomes the project brief for every AI change.">
        <div className="ev-form">
          <FormRow label="Project name" hint="How the project appears in the workspace." htmlFor="ad-name">
            <Input id="ad-name" value={name} onInput={(e) => setName(e.target.value)} placeholder={a.name} />
          </FormRow>
        </div>
        <QuestionList questions={a.questions || []} answers={answers} onChange={setAnswers} disabled={busy} />
      </Card>
      <div className="ev-actions" style={{ marginTop: 0 }}>
        <Button design="Transparent" icon="nav-back" onClick={onBack} disabled={busy}>Back</Button>
        <span className="ev-spacer" />
        <span className="ev-muted ev-small">Saved as version 1, the adopted baseline.</span>
        <Button design="Emphasized" icon="accept" disabled={busy} onClick={onCreate}>{busy ? "Adopting…" : "Adopt project"}</Button>
      </div>
    </div>
  );
}
