// Release console for one version: environment, release gate, approval, CF sign-in, destination
// credentials, deploy job with live log + stage tracker, success celebration, history, app logs.
import { useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { useNavigate, useParams } from "react-router-dom";
import { Button, Icon, Input, MessageStrip, SegmentedButton, SegmentedButtonItem, TextArea, BusyIndicator, Link } from "@ui5/webcomponents-react";
import { Card, PageHeader, Skeleton, StatusBadge, ago } from "../../components/ui";
import { useApi, fmtDate } from "../../components/shell/useApi";
import { statusTone } from "../../components/shell/labels";
import { useToast } from "../../components/shell/Toasts";
import { useJobs } from "../../jobs/JobProvider";
import { useMe } from "../../app/MeProvider";
import { api } from "../../api/client";
import "./deploy.css";

interface Env { label: string; api: string; org: string; space: string; requires_approval: boolean; requires_validation: boolean; configured: boolean }
interface Approval { id: string; status: string; requester: string; note?: string; decided_by?: string; comment?: string; can_decide?: boolean; requested_at?: number }
interface Check {
  ok: boolean; reasons: string[]; approval: Approval | null;
  needs: { destinations: { id: string; dest_name: string; auth_type: string; use_existing: boolean; url?: string }[] };
  method_choices?: string[]; target?: { api: string; org: string; space: string };
}
interface Session { signed_in: boolean; user?: string; api?: string; org?: string; space?: string; passcode_url?: string; cli_available?: boolean; mode?: string; error?: string }

const STAGES = [
  { key: "prepare", label: "Prepare", icon: "document", re: /project written|written|signed in|target/i },
  { key: "build", label: "Build", icon: "wrench", re: /build|mbt|mtar|archive/i },
  { key: "upload", label: "Upload", icon: "upload", re: /upload|uploading/i },
  { key: "deploy", label: "Deploy", icon: "paper-plane", re: /deploy|staging|starting app|creating service|binding|process finished/i },
  { key: "live", label: "Live", icon: "internet-browser", re: /🌐|https?:\/\/\S+\.(hana\.ondemand|cfapps)/i },
];

export default function DeployPage() {
  const { versionId = "" } = useParams();
  const nav = useNavigate();
  const toast = useToast();
  const jobs = useJobs();
  const { me } = useMe();
  const ver = useApi<{ version: any; snapshot: any }>(`api/versions/${versionId}`, { deps: [versionId] });
  const pid = ver.data?.version?.project_id;
  const proj = useApi<{ project: any; deployments: any[] }>(pid ? `api/projects/${pid}` : null, { deps: [pid] });
  const envs = useApi<{ envs: Record<string, Env> }>("api/environments");
  const [env, setEnv] = useState("dev");
  const check = useApi<Check>(`api/deploy/check?version_id=${versionId}&env=${env}`, { deps: [versionId, env] });
  const session = useApi<Session>("api/cf/session");
  const [creds, setCreds] = useState<Record<string, Record<string, string>>>({});
  const jobKey = `evoke.deploy.${versionId}`;
  const [jobId, setJobId] = useState<string | null>(() => { try { return sessionStorage.getItem(jobKey); } catch { return null; } });
  const job = jobs.get(jobId);
  const [result, setResult] = useState<{ status: string; url: string } | null>(null);

  useEffect(() => {
    if (job && job.status === "done" && job.result && !result) setResult({ status: job.result.status, url: job.result.url });
  }, [job, result]);

  const envList = Object.entries(envs.data?.envs || {});
  const cfg = envs.data?.envs?.[env];
  const needDest = (check.data?.needs?.destinations || []).filter((d) => !d.use_existing);
  const missing = needDest.flatMap((d) => {
    const req = ["url"].concat(d.auth_type === "BasicAuthentication" ? ["user", "password"] :
      d.auth_type === "OAuth2ClientCredentials" ? ["token_url", "client_id", "client_secret"] : []);
    return req.filter((f) => !(creds[d.id]?.[f] || "").trim()).map((f) => `${d.dest_name}: ${f}`);
  });
  const running = job?.status === "running";
  const canDeploy = !!check.data?.ok && !!session.data?.signed_in && missing.length === 0 && !running;

  const deploy = async () => {
    setResult(null);
    try {
      const id = await jobs.start("api/jobs/deploy", { version_id: versionId, env, dest_creds: creds }, {
        onDone: (r) => {
          setResult({ status: r.status, url: r.url });
          if (r.status === "succeeded") toast(`Deployed to ${env} — live at ${r.url}`);
          else toast(`Deployment ${r.status}. See the log for details.`, "error");
          proj.reload(true);
        },
        onError: (e) => toast(`Deployment failed: ${e}`, "error"),
      });
      setJobId(id);
      try { sessionStorage.setItem(jobKey, id); } catch { /* ignore */ }
    } catch (e: any) { toast(e.message, "error"); }
  };

  const v = ver.data?.version;
  return (
    <div>
      <PageHeader kicker="Release" title={<>Deploy {proj.data?.project?.name || "application"} <span className="ev-muted">v{v?.number ?? "…"}</span></>}
        subtitle="Choose an environment, pass the release gate, sign in to Cloud Foundry and deploy. The deployment runs in the background — you can leave this page."
        actions={pid && <Button design="Transparent" icon="nav-back" onClick={() => nav(`/projects/${pid}`)}>Back to project</Button>} />
      <div className="ev-content">
        {ver.error && <MessageStrip design="Negative" hideCloseButton>{ver.error}</MessageStrip>}

        <div className="ev-envs">
          {envs.loading && !envs.data ? [0, 1, 2].map((i) => <Skeleton key={i} height={120} radius={16} />) :
            envList.map(([k, e], i) => (
              <motion.button key={k} className={"ev-env" + (env === k ? " is-on" : "") + (!e.configured ? " is-off" : "")} onClick={() => setEnv(k)}
                initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.06 }} whileHover={{ y: -3 }}>
                <div className="ev-env__top">
                  <span className={`ev-env__icon env-${k}`}><Icon name={k === "prod" ? "shield" : k === "test" ? "lab" : "developer-settings"} /></span>
                  <span className="ev-env__label">{e.label}</span>
                  {env === k && <motion.span layoutId="env-check" className="ev-env__check"><Icon name="accept" /></motion.span>}
                </div>
                <div className="ev-env__space">{e.configured ? <>{e.org.split(/[_ ]/)[0]} · <b>{e.space}</b></> : "No Cloud Foundry space configured"}</div>
                <div className="ev-env__rules">
                  {e.requires_approval && <StatusBadge tone="warn" icon="approvals">approval</StatusBadge>}
                  {e.requires_validation && <StatusBadge tone="info" icon="shield">validation</StatusBadge>}
                  {!e.requires_approval && !e.requires_validation && <StatusBadge tone="good">no gate</StatusBadge>}
                </div>
              </motion.button>
            ))}
        </div>

        <div className="ev-deploy-grid">
          <div className="ev-deploy-left">
            <Card title="Release gate" icon="shield" subtitle={cfg ? `${cfg.label}${cfg.configured ? ` · ${cfg.space}` : ""}` : ""}
              actions={<Button design="Transparent" icon="refresh" tooltip="Re-check" onClick={() => { check.reload(); session.reload(); }} />}>
              {check.loading && !check.data ? <Skeleton height={80} /> : check.error ? <MessageStrip design="Negative" hideCloseButton>{check.error}</MessageStrip> : (
                <Gate check={check.data!} signedIn={!!session.data?.signed_in} missing={missing} />
              )}
            </Card>

            {cfg?.requires_approval && check.data && (
              <ApprovalBox approval={check.data.approval} pid={pid} vid={versionId} env={env} meId={me?.user.id}
                onChange={() => check.reload(true)} />
            )}

            <CfSignIn session={session.data} loading={session.loading} onChange={() => { session.reload(); check.reload(true); }} />

            {needDest.length > 0 && (
              <Card title="Backend destinations" icon="connected" subtitle="Credentials go straight into the deployment — never stored by the studio.">
                {needDest.map((d) => (
                  <div key={d.id} className="ev-destform">
                    <div className="ev-row"><b>{d.dest_name}</b><StatusBadge tone="info">{d.auth_type}</StatusBadge></div>
                    <DestFields d={d} value={creds[d.id] || {}} onChange={(c) => setCreds((s) => ({ ...s, [d.id]: c }))} />
                  </div>
                ))}
              </Card>
            )}
          </div>

          <div className="ev-deploy-right">
            <Card title="Deployment" icon="paper-plane" subtitle={cfg?.configured ? `${cfg.api.replace(/^https:\/\/api\./, "")} · ${cfg.space}` : ""}
              actions={<Button design="Emphasized" icon="paper-plane" disabled={!canDeploy} loading={running} onClick={deploy}>
                {running ? "Deploying…" : `Deploy to ${cfg?.label || env}`}</Button>}>
              <Stages messages={job?.messages || []} status={job?.status} result={result} />
              <Console job={job} />
              <AnimatePresence>
                {result?.status === "succeeded" && <Success url={result.url} env={cfg?.label || env} />}
              </AnimatePresence>
              {!job && (
                <div className="ev-muted ev-small" style={{ marginTop: ".6rem" }}>
                  {canDeploy ? "Everything is ready — press Deploy. Building and staging usually takes 2–5 minutes." :
                    "Complete the release gate on the left to enable the deployment."}
                </div>
              )}
            </Card>

            <Card title="Deployment history" icon="history" delay={0.05}>
              {(proj.data?.deployments || []).length === 0 ? <div className="ev-muted ev-small">This project has not been deployed yet.</div> : (
                <div className="ev-list">
                  {(proj.data?.deployments || []).slice(0, 8).map((d: any, i: number) => (
                    <div key={i} className="ev-li">
                      <span className={`ev-feed__icon tone-${statusTone(d.status)}`}><Icon name={d.status === "succeeded" ? "sys-enter-2" : "error"} /></span>
                      <div className="ev-li__body">
                        <div className="ev-li__title">v{d.version_number ?? "?"} → {d.env} <StatusBadge tone={statusTone(d.status)}>{d.status}</StatusBadge></div>
                        <div className="ev-li__sub">{d.user} · <span title={fmtDate(d.at)}>{ago(d.at)}</span>
                          {d.app_url && <> · <a href={d.app_url} target="_blank" rel="noreferrer">open app ↗</a></>}</div>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </Card>

            <AppLogs appId={ver.data?.snapshot?.app_id || proj.data?.project?.app_id} signedIn={!!session.data?.signed_in} />
          </div>
        </div>
      </div>
    </div>
  );
}

function Gate({ check, signedIn, missing }: { check: Check; signedIn: boolean; missing: string[] }) {
  const items: { ok: boolean; text: string }[] = [
    ...(check.reasons.length ? check.reasons.map((r) => ({ ok: false, text: r })) : [{ ok: true, text: "Permissions, version, validation and approval rules are met" }]),
    { ok: signedIn, text: signedIn ? "Signed in to Cloud Foundry" : "Sign in to Cloud Foundry" },
    ...(missing.length ? [{ ok: false, text: `Fill in the destination credentials (${missing.length} field${missing.length > 1 ? "s" : ""})` }] : []),
  ];
  const allOk = items.every((i) => i.ok);
  return (
    <div className="ev-gate">
      {items.map((it, i) => (
        <motion.div key={it.text} className={"ev-gate__item" + (it.ok ? " ok" : " no")} initial={{ opacity: 0, x: -8 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: i * 0.07 }}>
          <motion.span className="ev-gate__mark" initial={{ scale: 0 }} animate={{ scale: 1 }} transition={{ delay: 0.1 + i * 0.07, type: "spring", stiffness: 500 }}>
            <Icon name={it.ok ? "accept" : "decline"} />
          </motion.span>
          <span>{it.text}</span>
        </motion.div>
      ))}
      <div className={"ev-gate__sum" + (allOk ? " ok" : "")}>{allOk ? "Release gate passed" : "Release blocked until every item is green"}</div>
    </div>
  );
}

function ApprovalBox({ approval, pid, vid, env, meId, onChange }: { approval: Approval | null; pid?: string; vid: string; env: string; meId?: string; onChange: () => void }) {
  const toast = useToast();
  const [note, setNote] = useState("");
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const st = approval?.status;
  const request = async () => {
    setBusy(true);
    try { await api.post("api/approvals", { project_id: pid, version_id: vid, env, note }); toast("Approval requested — reviewers are notified."); setNote(""); onChange(); }
    catch (e: any) { toast(e.message, "error"); } finally { setBusy(false); }
  };
  const decide = async (approve: boolean) => {
    if (!approve && !comment.trim()) { toast("A rejection needs a comment.", "error"); return; }
    setBusy(true);
    try { await api.post(`api/approvals/${approval!.id}/decide`, { approve, comment }); toast(approve ? "Release approved" : "Release rejected"); onChange(); }
    catch (e: any) { toast(e.message, "error"); } finally { setBusy(false); }
  };
  return (
    <Card title="Release approval" icon="approvals" subtitle="A reviewer other than the requester and the version's author decides.">
      <div className="ev-appr-flow">
        {["Requested", "Reviewed", "Approved"].map((s, i) => {
          const done = st === "approved" ? 3 : st === "pending" ? 1 : st === "rejected" ? 2 : 0;
          return (
            <div key={s} className={"ev-appr-step" + (i < done ? " done" : "") + (st === "rejected" && i === 1 ? " bad" : "")}>
              <span>{i < done ? <Icon name={st === "rejected" && i === 1 ? "decline" : "accept"} /> : i + 1}</span>{st === "rejected" && i === 1 ? "Rejected" : s}
            </div>
          );
        })}
      </div>
      {st === "approved" && <MessageStrip design="Positive" hideCloseButton>Approved by {approval?.decided_by}{approval?.comment ? ` — “${approval.comment}”` : ""}</MessageStrip>}
      {st === "pending" && (
        <>
          <MessageStrip design="Critical" hideCloseButton>Waiting for a reviewer · requested by {approval?.requester}{approval?.note ? ` — “${approval.note}”` : ""}</MessageStrip>
          {approval?.can_decide && approval.requester !== meId && (
            <div className="ev-appr-decide">
              <TextArea placeholder="Decision comment (required to reject)" value={comment} onInput={(e) => setComment(e.target.value)} rows={2} />
              <div className="ev-row"><Button design="Positive" icon="accept" disabled={busy} onClick={() => decide(true)}>Approve</Button>
                <Button design="Negative" icon="decline" disabled={busy} onClick={() => decide(false)}>Reject</Button></div>
            </div>
          )}
        </>
      )}
      {st === "rejected" && <MessageStrip design="Negative" hideCloseButton>Rejected by {approval?.decided_by}: “{approval?.comment}”. Fix it, generate a new version, and request again.</MessageStrip>}
      {(!st || st === "rejected") && (
        <div className="ev-appr-decide">
          <TextArea placeholder="Note for the reviewer: what changed, what was tested…" value={note} onInput={(e) => setNote(e.target.value)} rows={2} />
          <Button icon="paper-plane" disabled={busy} onClick={request}>Request approval</Button>
        </div>
      )}
    </Card>
  );
}

function CfSignIn({ session, loading, onChange }: { session: Session | null; loading: boolean; onChange: () => void }) {
  const toast = useToast();
  const [mode, setMode] = useState<"sso" | "pw">("sso");
  const [code, setCode] = useState("");
  const [user, setUser] = useState("");
  const [pw, setPw] = useState("");
  const [origin, setOrigin] = useState("");
  const [busy, setBusy] = useState(false);
  const login = async () => {
    setBusy(true);
    try {
      await api.post("api/cf/login", mode === "sso" ? { passcode: code.trim() } : { user, password: pw, origin });
      toast("Signed in to Cloud Foundry"); setCode(""); setPw(""); onChange();
    } catch (e: any) { toast(`Sign-in failed: ${e.message}`, "error"); } finally { setBusy(false); }
  };
  const logout = async () => { try { await api.post("api/cf/logout"); onChange(); } catch (e: any) { toast(e.message, "error"); } };
  if (loading && !session) return <Card title="Cloud Foundry" icon="cloud"><Skeleton height={60} /></Card>;
  if (session && session.cli_available === false) return (
    <Card title="Cloud Foundry" icon="cloud"><MessageStrip design="Critical" hideCloseButton>The Cloud Foundry CLI is not available on the studio host. Download the project and deploy with mbt + cf deploy.</MessageStrip></Card>);
  if (session?.mode === "technical") return (
    <Card title="Cloud Foundry" icon="cloud" subtitle="Connected automatically — no separate sign-in needed. Your studio role decides what you may deploy; the audit log records you.">
      {session.signed_in ? (
        <div className="ev-cf-ok"><span className="ev-cf-ok__dot" /><div><b>Studio deployment user</b><div className="ev-muted ev-small">{session.org} · {session.space}</div></div></div>
      ) : <MessageStrip design="Negative" hideCloseButton>{session.error || "The studio's deployment user could not connect to Cloud Foundry."}</MessageStrip>}
    </Card>);
  return (
    <Card title="Cloud Foundry" icon="cloud" subtitle={session?.signed_in ? undefined : "Your own CF session — deployments run as you."}
      actions={session?.signed_in ? <Button design="Transparent" icon="log" onClick={logout}>Sign out</Button> : undefined}>
      {session?.signed_in ? (
        <div className="ev-cf-ok"><span className="ev-cf-ok__dot" /><div><b>{(session.user || "").split(/\s[·|]\s|\s+\(/)[0]}</b><div className="ev-muted ev-small">{session.org} · {session.space}</div></div></div>
      ) : (
        <div className="ev-cf">
          <SegmentedButton onSelectionChange={(e) => setMode(((e.detail.selectedItems[0] as any)?.dataset.v || "sso") as any)}>
            <SegmentedButtonItem data-v="sso" selected={mode === "sso"}>SSO passcode</SegmentedButtonItem>
            <SegmentedButtonItem data-v="pw" selected={mode === "pw"}>User + password</SegmentedButtonItem>
          </SegmentedButton>
          {mode === "sso" ? (
            <>
              <ol className="ev-cf__steps">
                <li>Open {session?.passcode_url ? <Link href={session.passcode_url} target="_blank">{session.passcode_url.replace(/^https:\/\//, "")}</Link> : "the passcode page"} and sign in with your BTP user.</li>
                <li>Copy the <b>Temporary Authentication Code</b> and paste it here — it is single-use.</li>
              </ol>
              <div className="ev-row"><Input type="Password" placeholder="Passcode" value={code} onInput={(e) => setCode(e.target.value)} style={{ flex: 1 }} />
                <Button design="Emphasized" disabled={!code.trim() || busy} loading={busy} onClick={login}>Sign in</Button></div>
            </>
          ) : (
            <div className="ev-cf__pw">
              <Input placeholder="User (e-mail)" value={user} onInput={(e) => setUser(e.target.value)} />
              <Input type="Password" placeholder="Password" value={pw} onInput={(e) => setPw(e.target.value)} />
              <Input placeholder="Identity provider origin (optional)" value={origin} onInput={(e) => setOrigin(e.target.value)} />
              <Button design="Emphasized" disabled={!user || !pw || busy} loading={busy} onClick={login}>Sign in</Button>
              <div className="ev-muted ev-small">Users with two-factor authentication must use the SSO passcode.</div>
            </div>
          )}
        </div>
      )}
    </Card>
  );
}

function DestFields({ d, value, onChange }: { d: { auth_type: string; url?: string }; value: Record<string, string>; onChange: (v: Record<string, string>) => void }) {
  const set = (k: string) => (e: any) => onChange({ ...value, [k]: e.target.value });
  return (
    <div className="ev-cf__pw">
      <Input placeholder="Backend base URL (https://host)" value={value.url ?? (d.url || "")} onInput={set("url")} />
      {d.auth_type === "BasicAuthentication" && <><Input placeholder="User" value={value.user || ""} onInput={set("user")} />
        <Input type="Password" placeholder="Password" value={value.password || ""} onInput={set("password")} /></>}
      {d.auth_type === "OAuth2ClientCredentials" && <><Input placeholder="Token service URL" value={value.token_url || ""} onInput={set("token_url")} />
        <Input placeholder="Client ID" value={value.client_id || ""} onInput={set("client_id")} />
        <Input type="Password" placeholder="Client secret" value={value.client_secret || ""} onInput={set("client_secret")} /></>}
    </div>
  );
}

function Stages({ messages, status, result }: { messages: string[]; status?: string; result: { status: string } | null }) {
  const reached = useMemo(() => {
    let r = -1;
    messages.forEach((m) => STAGES.forEach((s, i) => { if (s.re.test(m) && i > r) r = i; }));
    if (result?.status === "succeeded") r = STAGES.length - 1;
    return r;
  }, [messages, result]);
  const failed = status === "error" || (result && result.status !== "succeeded");
  return (
    <div className="ev-stages">
      {STAGES.map((s, i) => {
        const done = i < reached || (i === reached && (result?.status === "succeeded"));
        const active = i === reached && status === "running";
        const bad = failed && i === Math.max(0, reached + (result?.status === "succeeded" ? 0 : 1)) && !done;
        return (
          <div key={s.key} className={"ev-stage" + (done ? " done" : "") + (active ? " active" : "") + (bad ? " bad" : "")}>
            <motion.span className="ev-stage__dot" animate={active ? { scale: [1, 1.12, 1] } : { scale: 1 }} transition={{ repeat: active ? Infinity : 0, duration: 1.2 }}>
              <Icon name={done ? "accept" : bad ? "decline" : s.icon} />
            </motion.span>
            <span className="ev-stage__label">{s.label}</span>
            {i < STAGES.length - 1 && <span className="ev-stage__line"><motion.span initial={{ width: 0 }} animate={{ width: i < reached ? "100%" : "0%" }} transition={{ duration: 0.5 }} /></span>}
          </div>
        );
      })}
    </div>
  );
}

function Console({ job }: { job?: { messages: string[]; status: string; seconds: number } }) {
  const ref = useRef<HTMLDivElement>(null);
  const [follow, setFollow] = useState(true);
  useEffect(() => { if (follow && ref.current) ref.current.scrollTop = ref.current.scrollHeight; }, [job?.messages.length, follow]);
  if (!job) return (
    <div className="ev-console is-empty"><Icon name="command-line-interfaces" /><span>The live deployment log appears here.</span></div>);
  return (
    <div className="ev-console-wrap">
      <div className="ev-console__bar">
        <span className="ev-console__lights"><i /><i /><i /></span>
        <span>deploy log · {Math.round(job.seconds)}s</span>
        {job.status === "running" && <BusyIndicator active size="S" />}
        <span style={{ flex: 1 }} />
        <label className="ev-console__follow"><input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} /> follow</label>
      </div>
      <div className="ev-console" ref={ref}>
        {job.messages.map((l, i) => (
          <div key={i} className={"ev-console__l" + (/❌|error|failed/i.test(l) ? " err" : /✅|succeeded|ok$/i.test(l) ? " ok" : /🌐/.test(l) ? " url" : /warn/i.test(l) ? " warn" : "")}>
            <span className="ev-console__n">{i + 1}</span>{l}
          </div>
        ))}
        {job.status === "running" && <div className="ev-console__l"><span className="ev-console__n" /><span className="ev-caret">▍</span></div>}
      </div>
    </div>
  );
}

function Success({ url, env }: { url: string; env: string }) {
  const bits = Array.from({ length: 26 });
  return (
    <motion.div className="ev-success" initial={{ opacity: 0, scale: 0.95 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0 }}>
      <div className="ev-confetti">
        {bits.map((_, i) => (
          <motion.i key={i} style={{ left: `${(i * 37) % 100}%`, background: `var(--sapChart_OrderedColor_${(i % 6) + 1})` }}
            initial={{ y: -10, opacity: 1, rotate: 0 }} animate={{ y: 140, opacity: 0, rotate: 360 + i * 20 }}
            transition={{ duration: 1.4 + (i % 5) * 0.2, delay: (i % 7) * 0.05, ease: "easeOut" }} />
        ))}
      </div>
      <motion.div className="ev-success__icon" initial={{ scale: 0 }} animate={{ scale: 1 }} transition={{ type: "spring", stiffness: 400, damping: 15, delay: 0.1 }}>
        <Icon name="sys-enter-2" />
      </motion.div>
      <div className="ev-success__t">Live on {env}</div>
      <a className="ev-success__url" href={url} target="_blank" rel="noreferrer">{url} ↗</a>
    </motion.div>
  );
}

function AppLogs({ appId, signedIn }: { appId?: string; signedIn: boolean }) {
  const [text, setText] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [app, setApp] = useState(appId || "");
  useEffect(() => { if (appId) setApp(appId); }, [appId]);
  const fetchLogs = async () => {
    setBusy(true);
    try { const r = await api.get<{ ok: boolean; text: string }>(`api/cf/logs?app=${encodeURIComponent(app)}`); setText(r.text || "(no recent log lines)"); }
    catch (e: any) { setText(`Could not read the logs: ${e.message}`); } finally { setBusy(false); }
  };
  return (
    <Card title="Application logs" icon="log" subtitle="Recent log lines of the deployed app (cf logs --recent).">
      <div className="ev-row"><Input value={app} onInput={(e) => setApp(e.target.value)} style={{ flex: 1 }} placeholder="CF app name" />
        <Button icon="refresh" disabled={!signedIn || !app || busy} loading={busy} onClick={fetchLogs}>Fetch logs</Button></div>
      {!signedIn && <div className="ev-muted ev-small" style={{ marginTop: ".4rem" }}>Sign in to Cloud Foundry to read logs.</div>}
      {text !== null && <pre className="ev-console ev-console--plain">{text}</pre>}
    </Card>
  );
}
