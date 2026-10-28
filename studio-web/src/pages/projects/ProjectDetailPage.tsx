// Project detail: object header with KPIs; Versions (timeline, validate, deploy, compare, files),
// Deployments, Members, Settings (rename, duplicate, archive, export).
import { useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { useNavigate, useParams } from "react-router-dom";
import { Button, Icon, Input, MessageStrip, Select, Option, SegmentedButton, SegmentedButtonItem, BusyIndicator } from "@ui5/webcomponents-react";
import { Card, EmptyState, Skeleton, StatusBadge, ago } from "../../components/ui";
import { useApi, download, fmtDate } from "../../components/shell/useApi";
import { MODE_INFO, ROLE_LABEL, statusTone } from "../../components/shell/labels";
import { Tabs } from "../../components/shell/Tabs";
import { CodeView } from "../../components/shell/CodeView";
import { useToast } from "../../components/shell/Toasts";
import { useJobs } from "../../jobs/JobProvider";
import { api } from "../../api/client";
import type { Project, VersionMeta } from "../../api/types";
import { ConfirmDialog, DeleteProjectDialog, EditProjectDialog } from "../../components/shell/ProjectDialogs";
import { ADOPT_KEYS, ADOPT_TABS, AdoptedWorkspace } from "../adopt/AdoptedWorkspace";
import "./projects.css";

interface Detail {
  project: Project & { members?: { user: string; role: string }[] };
  versions: VersionMeta[];
  deployments: { env: string; status: string; app_url: string; user: string; at: number; version_number?: number }[];
  role?: string;
  can_delete?: boolean;
  permissions?: string[];
}

export default function ProjectDetailPage() {
  const { pid = "" } = useParams();
  const nav = useNavigate();
  const toast = useToast();
  const jobs = useJobs();
  const { data, error, loading, reload } = useApi<Detail>(`api/projects/${pid}`, { deps: [pid] });
  const [tab, setTab] = useState("versions");
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState("");
  const [pick, setPick] = useState<string[]>([]);
  const [files, setFiles] = useState<VersionMeta | null>(null);
  const [validating, setValidating] = useState<Record<string, string>>({});
  const [editOpen, setEditOpen] = useState(false);
  const [delOpen, setDelOpen] = useState(false);
  const [delVer, setDelVer] = useState<VersionMeta | null>(null);
  const [undeployEnv, setUndeployEnv] = useState("");
  const [deployVid, setDeployVid] = useState("");
  const tabInit = useRef(false);

  useEffect(() => { if (data) setName(data.project.name); }, [data]);
  useEffect(() => {                      // adopted projects open on their workspace overview
    if (!data || tabInit.current) return;
    tabInit.current = true;
    if (data.project.mode === "adopted") setTab("overview");
  }, [data]);

  if (error) return <div className="ev-content" style={{ paddingTop: "2rem" }}>
    <EmptyState icon="error" title="Project not available" text={error} action={<Button onClick={() => nav("/projects")}>Back to projects</Button>} /></div>;
  if (loading && !data) return <div style={{ padding: "2rem", display: "grid", gap: "1rem" }}><Skeleton height={170} radius={20} /><Skeleton height={300} radius={16} /></div>;
  if (!data) return null;

  const p = data.project;
  const may = (perm: string) => (data.permissions || []).includes(perm);
  const mayDeploy = ["deploy.dev", "deploy.test", "deploy.prod"].some(may);
  const m = MODE_INFO[p.mode] || MODE_INFO.import;
  const latest = data.versions[0];
  const validated = data.versions.filter((v) => v.validation).length;
  const okDeploys = data.deployments.filter((d) => d.status === "succeeded");
  const live = okDeploys[0];
  const adopted = p.mode === "adopted";
  const deployVersion = (vid: string) => { if (adopted) { setDeployVid(vid); setTab("deploy"); } else nav(`/deploy/${vid}`); };

  const rename = async () => {
    if (!name.trim() || name === p.name) { setEditing(false); return; }
    try { await api.post(`api/projects/${pid}/rename`, { name: name.trim() }); toast("Project renamed"); setEditing(false); reload(true); }
    catch (e: any) { toast(e.message, "error"); }
  };

  const validate = async (v: VersionMeta, level: "standard" | "deep") => {
    try {
      const id = await jobs.start("api/jobs/validate", { version_id: v.id, level }, {
        onDone: (r) => {
          setValidating((s) => { const n = { ...s }; delete n[v.id]; return n; });
          toast(`Version ${v.number}: ${r.summary.passed} passed · ${r.summary.failed} failed · ${r.summary.not_tested} not tested`,
            r.blocking?.length ? "error" : "success");
          reload(true);
        },
        onError: (e) => { setValidating((s) => { const n = { ...s }; delete n[v.id]; return n; }); toast(`Validation failed: ${e}`, "error"); },
      });
      setValidating((s) => ({ ...s, [v.id]: id }));
      toast(`Validating version ${v.number} (${level}) — runs in the background`, "info");
    } catch (e: any) { toast(e.message, "error"); }
  };

  const togglePick = (vid: string) => setPick((s) => s.includes(vid) ? s.filter((x) => x !== vid) : [...s, vid].slice(-2));

  return (
    <div>
      <motion.div className="ev-objhead" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}>
        <div className="ev-objhead__row">
          <div className={`ev-objhead__logo ev-pcard__logo mode-${p.mode}`}>{p.name.split(/\s+/).slice(0, 2).map((w) => w[0]).join("").toUpperCase()}</div>
          <div className="ev-objhead__main">
            <div className="ev-objhead__name">
              {editing ? (
                <input autoFocus value={name} onChange={(e) => setName(e.target.value)} onBlur={rename}
                  onKeyDown={(e) => { if (e.key === "Enter") rename(); if (e.key === "Escape") { setName(p.name); setEditing(false); } }} />
              ) : (<>{p.name}<button className="ev-icon-btn" title="Rename" onClick={() => setEditing(true)}><Icon name="edit" /></button></>)}
            </div>
            <div className="ev-objhead__meta">
              <StatusBadge tone={m.tone} icon={m.icon}>{m.label}</StatusBadge>
              {p.archived && <StatusBadge>archived</StatusBadge>}
              <span>App ID <b className="ev-mono">{p.app_id || "—"}</b></span>
              <span>Owner <b>{p.owner}</b></span>
              {data.role && <span>Your role <b>{ROLE_LABEL[data.role] || data.role}</b></span>}
              <span>Updated <b>{ago(p.updated_at)}</b></span>
            </div>
            {p.description && <div className="ev-muted ev-small" style={{ marginTop: ".5rem", maxWidth: "90ch" }}>{p.description}</div>}
          </div>
          <div className="ev-objhead__actions">
            {may("project.edit") && <Button icon="edit" design="Transparent" onClick={() => setEditOpen(true)}>Edit details</Button>}
            {data.can_delete && <Button icon="delete" design="Transparent" tooltip="Delete project" onClick={() => setDelOpen(true)} />}
            {latest && may("project.edit") && (adopted
              ? <Button icon="ai" onClick={() => setTab("robot")}>Ask AI</Button>
              : <Button icon="action" onClick={() => nav(`/studio?version=${latest.id}`)}>Open in studio</Button>)}
            {latest && mayDeploy && <Button icon="paper-plane" design="Emphasized" onClick={() => deployVersion(latest.id)}>Deploy latest</Button>}
          </div>
        </div>
        <div className="ev-objhead__kpis">
          <div className="ev-okpi"><div className="ev-okpi__l">Versions</div><div className="ev-okpi__v">{data.versions.length}</div></div>
          <div className="ev-okpi"><div className="ev-okpi__l">Validated</div><div className="ev-okpi__v">{validated}<span className="ev-muted ev-small">/ {data.versions.length}</span></div></div>
          <div className="ev-okpi"><div className="ev-okpi__l">Deployments</div><div className="ev-okpi__v">{data.deployments.length}</div></div>
          <div className="ev-okpi"><div className="ev-okpi__l">Live</div><div className="ev-okpi__v">
            {live ? <a href={live.app_url} target="_blank" rel="noreferrer" className="ev-small" style={{ fontWeight: 700 }}>{live.env} ↗</a> : <span className="ev-muted ev-small">not deployed</span>}
          </div></div>
        </div>
      </motion.div>

      <div style={{ display: "contents" }}>
        <Tabs id="pd" value={tab} onChange={setTab} tabs={[
          ...(adopted ? ADOPT_TABS.map((t) => ({ ...t, group: "work" })) : []),
          { key: "versions", text: "Versions", icon: "save", count: data.versions.length, group: "manage" },
          { key: "deployments", text: "Deployments", icon: "paper-plane", count: data.deployments.length, group: "manage" },
          { key: "members", text: "Members", icon: "group", count: (p.members || []).length + 1, group: "manage" },
          { key: "settings", text: "Settings", icon: "settings", group: "manage" },
        ]} />
      </div>

      <AnimatePresence mode="wait">
        {/* the adopted workspace stays mounted across its own tabs: it owns the running previews and AI jobs */}
        <motion.div key={adopted && ADOPT_KEYS.includes(tab) ? "adopted" : tab} className="ev-tabbody" initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} transition={{ duration: 0.18 }}>
          {adopted && ADOPT_KEYS.includes(tab) && (
            <AdoptedWorkspace pid={pid} tab={tab} setTab={setTab} versions={data.versions} mayEdit={may("project.edit")} mayDeploy={mayDeploy}
              onChanged={() => reload(true)} deployVid={deployVid} setDeployVid={setDeployVid} />
          )}
          {tab === "versions" && (
            data.versions.length === 0 ? <EmptyState icon="save" title="No versions yet" text={adopted ? "AI changes you apply are saved here as versions." : "Generate the application in the studio — each generation is saved here."} /> : (
              <>
                <div className="ev-muted ev-small" style={{ marginBottom: ".8rem" }}>Tip: tick two versions to compare them.</div>
                <div className="ev-vtl">
                  {data.versions.map((v, i) => {
                    const val = v.validation;
                    const running = !!validating[v.id] && jobs.get(validating[v.id])?.status === "running";
                    return (
                      <motion.div key={v.id} className={"ev-vitem" + (i === 0 ? " is-latest" : "")}
                        initial={{ opacity: 0, x: -10 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: i * 0.04 }}>
                        <div className="ev-vitem__dot">v{v.number}</div>
                        <div className={"ev-vitem__card" + (pick.includes(v.id) ? " is-picked" : "")}>
                          <input type="checkbox" checked={pick.includes(v.id)} onChange={() => togglePick(v.id)} title="Select for comparison" />
                          <div className="ev-vitem__info">
                            <div className="ev-vitem__t">
                              {v.label || `Version ${v.number}`}
                              {i === 0 && <StatusBadge tone="brand">latest</StatusBadge>}
                              {running ? <StatusBadge tone="info"><BusyIndicator active size="S" /> validating…</StatusBadge> :
                                val ? (val.blocking?.length ? <StatusBadge tone="bad" icon="error">blocked</StatusBadge> :
                                  <StatusBadge tone="good" icon="shield">{val.summary.passed} passed{val.summary.not_tested ? ` · ${val.summary.not_tested} not tested` : ""}</StatusBadge>)
                                  : <StatusBadge>not validated</StatusBadge>}
                            </div>
                            <div className="ev-vitem__s">{v.author} · {fmtDate(v.created_at)} · {v.file_count} files</div>
                          </div>
                          <div className="ev-vitem__acts">
                            <Button design="Transparent" icon="documents" tooltip="Browse files" onClick={() => setFiles(v)}>Files</Button>
                            <Button design="Transparent" icon="download" tooltip="Download ZIP" onClick={() => download(`api/versions/${v.id}/zip`)} />
                            <Button design="Transparent" icon="shield" disabled={running} tooltip="Validate (standard)" onClick={() => validate(v, "standard")}>Validate</Button>
                            {!adopted && <Button design="Transparent" icon="edit" tooltip="Open this version in the studio" onClick={() => nav(`/studio?version=${v.id}`)} />}
                            {data.versions.length > 1 && may("project.edit") && <Button design="Transparent" icon="delete" tooltip="Delete this version" onClick={() => setDelVer(v)} />}
                            {mayDeploy && <Button design="Emphasized" icon="paper-plane" onClick={() => deployVersion(v.id)}>Deploy</Button>}
                          </div>
                        </div>
                      </motion.div>
                    );
                  })}
                </div>
                <AnimatePresence>
                  {pick.length > 0 && (
                    <motion.div className="ev-cmpbar" initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 20 }}>
                      <Icon name="compare" />
                      <span>{pick.length === 1 ? "Select one more version to compare" : "Two versions selected"}</span>
                      <span style={{ flex: 1 }} />
                      <Button design="Transparent" onClick={() => setPick([])}>Clear</Button>
                      <Button design="Emphasized" icon="compare" disabled={pick.length < 2} onClick={() => {
                        const sorted = pick.map((id) => data.versions.find((v) => v.id === id)!).sort((a, b) => a.number - b.number);
                        nav(`/compare/${sorted[0].id}/${sorted[1].id}`);
                      }}>Compare</Button>
                    </motion.div>
                  )}
                </AnimatePresence>
              </>
            )
          )}

          {tab === "deployments" && (
            data.deployments.length === 0 ? <EmptyState icon="paper-plane" title="Not deployed yet" text="Deploy a version to SAP BTP — development needs no approval, production needs an approved, validated version."
              action={latest && <Button design="Emphasized" icon="paper-plane" onClick={() => deployVersion(latest.id)}>Deploy the latest version</Button>} /> : (
              <div className="ev-card" style={{ overflow: "auto" }}>
                <table className="ev-dtable">
                  <thead><tr><th>When</th><th>Environment</th><th>Version</th><th>Status</th><th>By</th><th>URL</th><th /></tr></thead>
                  <tbody>
                    {data.deployments.map((d, i) => (
                      <tr key={i}>
                        <td title={fmtDate(d.at)}>{ago(d.at)}</td>
                        <td><StatusBadge tone="info">{d.env}</StatusBadge></td>
                        <td>v{d.version_number ?? "?"}</td>
                        <td><StatusBadge tone={statusTone(d.status)}>{d.status}</StatusBadge></td>
                        <td>{d.user}</td>
                        <td>{d.app_url ? <a href={d.app_url} target="_blank" rel="noreferrer">{d.app_url.replace(/^https?:\/\//, "")}</a> : "—"}</td>
                        <td>{d.status === "succeeded" && may(`deploy.${d.env}`) && data.deployments.findIndex((x) => x.env === d.env) === i &&
                          <Button design="Transparent" icon="delete" onClick={() => setUndeployEnv(d.env)}>Remove from Cloud Foundry</Button>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )
          )}

          {tab === "members" && <Members pid={pid} project={p} onChange={() => reload(true)} />}
          {tab === "settings" && <ProjectSettings pid={pid} project={p} onChange={() => reload(true)}
            canDelete={!!data.can_delete} onEdit={() => setEditOpen(true)} onDelete={() => setDelOpen(true)} />}
        </motion.div>
      </AnimatePresence>

      <AnimatePresence>{files && <FilesPanel version={files} onClose={() => setFiles(null)} />}</AnimatePresence>
      <EditProjectDialog project={p} open={editOpen} onClose={() => setEditOpen(false)} onSaved={() => reload(true)} />
      <DeleteProjectDialog project={p} open={delOpen} onClose={() => setDelOpen(false)} onDeleted={() => nav("/projects")} />
      <ConfirmDialog open={!!delVer} title={`Delete version ${delVer?.number ?? ""}`} onClose={() => setDelVer(null)}
        text={<>Version <b>{delVer?.number}</b> ({delVer?.label || "no label"}, {delVer?.file_count} files) and its approvals are deleted permanently. Other versions stay.</>}
        onConfirm={async () => {
          try { await api.post(`api/versions/${delVer!.id}/delete`); toast(`Version ${delVer!.number} deleted`); setDelVer(null); reload(true); }
          catch (e: any) { toast(e.message, "error"); }
        }} />
      <ConfirmDialog open={!!undeployEnv} title={`Remove from ${undeployEnv}`} confirmText="Remove app" onClose={() => setUndeployEnv("")}
        text={<>The deployed app <b className="ev-mono">{p.app_id}</b> and the services it created are deleted from Cloud Foundry ({undeployEnv}).
          The project and its versions stay in the workspace — you can deploy again at any time.</>}
        onConfirm={async () => {
          const env = undeployEnv;
          try {
            await jobs.start("api/jobs/undeploy", { project_id: pid, env, delete_services: true }, {
              onDone: () => { toast(`${p.app_id} removed from ${env}`); reload(true); },
              onError: (e) => toast(`Removing the app failed: ${e}`, "error"),
            });
            toast(`Removing ${p.app_id} from ${env} — runs in the background`, "info");
            setUndeployEnv("");
          } catch (e: any) { toast(e.message, "error"); }
        }} />
    </div>
  );
}

function Members({ pid, project, onChange }: { pid: string; project: Detail["project"]; onChange: () => void }) {
  const toast = useToast();
  const [user, setUser] = useState("");
  const [role, setRole] = useState("developer");
  const add = async () => {
    try { await api.post(`api/projects/${pid}/members`, { user: user.trim().toLowerCase(), role }); toast(`${user} added as ${role}`); setUser(""); onChange(); }
    catch (e: any) { toast(e.message, "error"); }
  };
  return (
    <div className="ev-members">
      <Card title="People with access" icon="group">
        <div className="ev-list">
          <div className="ev-li"><span className="ev-mini-av" style={{ width: 32, height: 32 }}>{project.owner[0]?.toUpperCase()}</span>
            <div className="ev-li__body"><div className="ev-li__title">{project.owner}</div><div className="ev-li__sub">Owner</div></div><StatusBadge tone="brand">owner</StatusBadge></div>
          {(project.members || []).map((mb) => (
            <div key={mb.user} className="ev-li"><span className="ev-mini-av" style={{ width: 32, height: 32 }}>{mb.user[0]?.toUpperCase()}</span>
              <div className="ev-li__body"><div className="ev-li__title">{mb.user}</div><div className="ev-li__sub">{ROLE_LABEL[mb.role] || mb.role}</div></div>
              <StatusBadge>{mb.role}</StatusBadge></div>
          ))}
        </div>
      </Card>
      <Card title="Add a member" icon="add-employee" subtitle="Developers can change and generate; reviewers approve releases; viewers read.">
        <div className="ev-row">
          <Input placeholder="user@company.com" value={user} onInput={(e) => setUser(e.target.value)} style={{ flex: 1, minWidth: 220 }} />
          <Select onChange={(e) => setRole((e.detail.selectedOption as any).value)} style={{ width: 160 }}>
            <Option value="developer">Developer</Option><Option value="reviewer">Reviewer</Option><Option value="viewer">Viewer</Option>
          </Select>
          <Button design="Emphasized" icon="add" disabled={!user.includes("@") && user.trim().length < 2} onClick={add}>Add</Button>
        </div>
      </Card>
    </div>
  );
}

function ProjectSettings({ pid, project, onChange, canDelete, onEdit, onDelete }: {
  pid: string; project: Detail["project"]; onChange: () => void; canDelete: boolean; onEdit: () => void; onDelete: () => void;
}) {
  const toast = useToast();
  const nav = useNavigate();
  const [dup, setDup] = useState(project.name + " (copy)");
  return (
    <div className="ev-settings-grid">
      <Card title="Details" icon="edit" subtitle="Name, description and the app ID used for the next generation and deployment.">
        <Button icon="edit" onClick={onEdit}>Edit details</Button>
      </Card>
      <Card title="Duplicate" icon="duplicate" subtitle="Copies the latest version into a new project owned by you.">
        <div className="ev-row"><Input value={dup} onInput={(e) => setDup(e.target.value)} style={{ flex: 1 }} />
          <Button icon="duplicate" onClick={async () => {
            try { const np = await api.post<any>(`api/projects/${pid}/duplicate`, { name: dup }); toast("Project duplicated"); nav(`/projects/${np.id}`); }
            catch (e: any) { toast(e.message, "error"); }
          }}>Duplicate</Button></div>
      </Card>
      <Card title="Export" icon="download" subtitle="All versions, deployments, approvals and the audit trail as one ZIP — re-import it in any studio.">
        <Button icon="download" onClick={() => download(`api/projects/${pid}/export`)}>Export project</Button>
      </Card>
      <Card title={project.archived ? "Restore" : "Archive"} icon="inbox" subtitle={project.archived ? "Show the project in lists again." : "Hide the project from lists. Nothing is deleted."}>
        <Button design={project.archived ? "Default" : "Negative"} icon="inbox" onClick={async () => {
          try { await api.post(`api/projects/${pid}/archive`, { archived: !project.archived }); toast(project.archived ? "Project restored" : "Project archived"); onChange(); }
          catch (e: any) { toast(e.message, "error"); }
        }}>{project.archived ? "Restore project" : "Archive project"}</Button>
      </Card>
      <Card title="Delete project" icon="delete" className="ev-danger-zone"
        subtitle={canDelete ? "Permanently deletes the project and all versions. Optionally removes the deployed app from Cloud Foundry."
          : "Only the project's owner or an administrator can delete it."}>
        <Button design="Negative" icon="delete" disabled={!canDelete} onClick={onDelete}>Delete project…</Button>
      </Card>
    </div>
  );
}

/** Slide-in file browser: tree on the left, highlighted code on the right. */
export function FilesPanel({ version, onClose }: { version: VersionMeta; onClose: () => void }) {
  const { data, error } = useApi<{ files: string[] }>(`api/versions/${version.id}`);
  const [sel, setSel] = useState<string>("");
  const [text, setText] = useState("");
  const [loading, setLoading] = useState(false);
  const [filter, setFilter] = useState("");
  const [closed, setClosed] = useState<Record<string, boolean>>({});
  const groups = useMemo(() => {
    const g: Record<string, string[]> = {};
    (data?.files || []).filter((f) => !filter || f.toLowerCase().includes(filter.toLowerCase())).forEach((f) => {
      const dir = f.includes("/") ? f.slice(0, f.lastIndexOf("/")) : "(root)";
      (g[dir] = g[dir] || []).push(f);
    });
    return Object.entries(g).sort(([a], [b]) => (a === "(root)" ? -1 : b === "(root)" ? 1 : a.localeCompare(b)));
  }, [data, filter]);
  useEffect(() => {
    if (!sel && data?.files?.length) setSel(data.files.includes("manifest.json") ? "manifest.json" : data.files[0]);
  }, [data, sel]);
  useEffect(() => {
    if (!sel) return;
    setLoading(true);
    api.text(`api/versions/${version.id}/file?path=${encodeURIComponent(sel)}`).then(setText).catch((e) => setText(`// ${e.message}`)).finally(() => setLoading(false));
  }, [sel, version.id]);
  return (
    <>
      <motion.div className="ev-side__scrim" onClick={onClose} initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} />
      <motion.aside className="ev-side" initial={{ x: "100%" }} animate={{ x: 0 }} exit={{ x: "100%" }} transition={{ type: "spring", stiffness: 300, damping: 34 }}>
        <div className="ev-side__head">
          <Icon name="documents" /> Files of version {version.number}
          <span className="ev-muted ev-small" style={{ fontWeight: 500 }}>{data?.files?.length ?? "…"} files</span>
          <span style={{ flex: 1 }} />
          <Button icon="download" design="Transparent" onClick={() => download(`api/versions/${version.id}/zip`)}>ZIP</Button>
          <Button icon="decline" design="Transparent" onClick={onClose} tooltip="Close" />
        </div>
        {error && <MessageStrip design="Negative" hideCloseButton>{error}</MessageStrip>}
        <div className="ev-side__body">
          <div className="ev-files__tree">
            <Input placeholder="Filter files" value={filter} onInput={(e) => setFilter(e.target.value)} style={{ width: "100%", marginBottom: ".4rem" }} showClearIcon />
            {groups.map(([dir, fs]) => (
              <div key={dir}>
                <div className="ev-files__dir" onClick={() => setClosed((c) => ({ ...c, [dir]: !c[dir] }))}>
                  <Icon name={closed[dir] ? "slim-arrow-right" : "slim-arrow-down"} style={{ width: ".7rem", height: ".7rem" }} />{dir}
                </div>
                {!closed[dir] && fs.map((f) => (
                  <div key={f} className={"ev-files__f" + (sel === f ? " is-sel" : "")} onClick={() => setSel(f)} title={f}>
                    <Icon name={/\.(js|ts)$/.test(f) ? "syntax" : /\.xml$/.test(f) ? "source-code" : /\.json$/.test(f) ? "document-text" : "document"} />
                    {f.split("/").pop()}
                  </div>
                ))}
              </div>
            ))}
          </div>
          <div className="ev-files__view">
            <div className="ev-row"><span className="ev-mono" style={{ fontWeight: 700 }}>{sel}</span>{loading && <BusyIndicator active size="S" />}</div>
            <CodeView text={text} path={sel} maxHeight="calc(100vh - 170px)" />
          </div>
        </div>
      </motion.aside>
    </>
  );
}
