// Project CRUD dialogs: edit details, delete (optionally removing the deployed app from Cloud
// Foundry first), bulk delete, delete version. Shared by the Projects list and the project page.
import { useEffect, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Button, CheckBox, Icon, Input, MessageStrip, TextArea, BusyIndicator } from "@ui5/webcomponents-react";
import { api } from "../../api/client";
import { useToast } from "./Toasts";
import { LiveTimeline, StatusBadge } from "../ui";
import "./dialogs.css";

/** Animated modal frame (scrim + card). */
export function Modal({ open, onClose, title, icon, tone, children, footer, width = 520 }: {
  open: boolean; onClose: () => void; title: string; icon?: string; tone?: "danger";
  children: React.ReactNode; footer?: React.ReactNode; width?: number;
}) {
  useEffect(() => {
    if (!open) return;
    const k = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", k);
    return () => document.removeEventListener("keydown", k);
  }, [open, onClose]);
  return (
    <AnimatePresence>
      {open && (
        <motion.div className="ev-modal" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} onMouseDown={onClose}>
          <motion.div className={"ev-modal__card" + (tone === "danger" ? " is-danger" : "")} style={{ width }}
            onMouseDown={(e) => e.stopPropagation()}
            initial={{ opacity: 0, y: 24, scale: 0.97 }} animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 16, scale: 0.97 }} transition={{ type: "spring", stiffness: 380, damping: 30 }}>
            <div className="ev-modal__head">
              {icon && <span className="ev-modal__icon"><Icon name={icon} /></span>}
              <div className="ev-modal__title">{title}</div>
              <button className="ev-icon-btn" onClick={onClose} title="Close"><Icon name="decline" /></button>
            </div>
            <div className="ev-modal__body">{children}</div>
            {footer && <div className="ev-modal__foot">{footer}</div>}
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

interface P { id: string; name: string; app_id?: string; description?: string }

/** Edit name, description and app ID. */
export function EditProjectDialog({ project, open, onClose, onSaved }: {
  project: P; open: boolean; onClose: () => void; onSaved: (p: any) => void;
}) {
  const toast = useToast();
  const [name, setName] = useState(project.name);
  const [desc, setDesc] = useState(project.description || "");
  const [appId, setAppId] = useState(project.app_id || "");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  useEffect(() => { if (open) { setName(project.name); setDesc(project.description || ""); setAppId(project.app_id || ""); setErr(""); } }, [open, project]);
  const appOk = !appId || /^[a-zA-Z]\w*(\.[a-zA-Z]\w*)+$/.test(appId);
  const save = async () => {
    setBusy(true); setErr("");
    try {
      const p = await api.post(`api/projects/${project.id}/update`, { name: name.trim(), description: desc, app_id: appId.trim() });
      toast("Project details saved");
      onSaved(p); onClose();
    } catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  };
  return (
    <Modal open={open} onClose={onClose} title="Edit project details" icon="edit"
      footer={<>
        <Button design="Transparent" onClick={onClose}>Cancel</Button>
        <Button design="Emphasized" icon="save" disabled={!name.trim() || !appOk || busy} onClick={save}>{busy ? "Saving…" : "Save"}</Button>
      </>}>
      <label className="ev-field"><span>Name</span>
        <Input value={name} onInput={(e) => setName(e.target.value)} style={{ width: "100%" }} /></label>
      <label className="ev-field"><span>Description</span>
        <TextArea value={desc} rows={3} onInput={(e: any) => setDesc(e.target.value)} style={{ width: "100%" }} /></label>
      <label className="ev-field"><span>App ID (namespace)</span>
        <Input value={appId} valueState={appOk ? "None" : "Negative"} onInput={(e) => setAppId(e.target.value)} style={{ width: "100%" }}
          placeholder="com.company.app" /></label>
      <div className="ev-muted ev-small">The app ID is also the Cloud Foundry application name — a change applies to the next generation and deployment.</div>
      {err && <MessageStrip design="Negative" hideCloseButton style={{ marginTop: ".6rem" }}>{err}</MessageStrip>}
    </Modal>
  );
}

/** Delete a project: type its name; optionally remove the deployed app from Cloud Foundry first. */
export function DeleteProjectDialog({ project, open, onClose, onDeleted }: {
  project: P; open: boolean; onClose: () => void; onDeleted: () => void;
}) {
  const toast = useToast();
  const [detail, setDetail] = useState<any>(null);
  const [confirm, setConfirm] = useState("");
  const [envs, setEnvs] = useState<string[]>([]);
  const [services, setServices] = useState(true);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [log, setLog] = useState<{ msgs: string[]; running: boolean } | null>(null);
  useEffect(() => {
    if (!open) return;
    setConfirm(""); setEnvs([]); setErr(""); setLog(null); setServices(true);
    api.get(`api/projects/${project.id}`).then(setDetail).catch((e) => setErr(e.message));
  }, [open, project.id]);
  // environments where the app is (still) deployed: last record per env succeeded
  const liveEnvs: string[] = [];
  for (const d of detail?.deployments || []) {
    if (!liveEnvs.includes(d.env) && !(detail.deployments as any[]).some((x) => x.env === d.env && x.at > d.at)) {
      if (d.status === "succeeded") liveEnvs.push(d.env);
    }
  }
  const ok = confirm.trim() === project.name && !busy;
  const run = async () => {
    setBusy(true); setErr("");
    try {
      if (!envs.length) {
        await api.post(`api/projects/${project.id}/delete`, { confirm_name: confirm.trim() });
        toast(`“${project.name}” deleted`);
        onDeleted(); onClose();
        return;
      }
      const r = await api.post<any>(`api/projects/${project.id}/delete`, { confirm_name: confirm.trim(), undeploy: envs, delete_services: services });
      const id = r.job_id;
      setLog({ msgs: ["Starting…"], running: true });
      await new Promise<void>((resolve) => {
        // re-attach to the job the server already started
        const t = setInterval(async () => {
          try {
            const j = await api.get(`api/jobs/${id}`);
            setLog({ msgs: j.messages.length ? j.messages : ["Working…"], running: j.status === "running" });
            if (j.status !== "running") {
              clearInterval(t);
              if (j.status === "done") { toast(`“${project.name}” removed from Cloud Foundry and deleted`); onDeleted(); onClose(); }
              else setErr(j.error || "Removing the app failed — the project was not deleted.");
              resolve();
            }
          } catch (e: any) { clearInterval(t); setErr(e.message); resolve(); }
        }, 1500);
      });
    } catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  };
  return (
    <Modal open={open} onClose={busy ? () => undefined : onClose} title="Delete project" icon="delete" tone="danger" width={560}
      footer={<>
        <Button design="Transparent" disabled={busy} onClick={onClose}>Cancel</Button>
        <Button design="Negative" icon="delete" disabled={!ok} onClick={run}>{busy ? "Deleting…" : envs.length ? "Remove & delete" : "Delete project"}</Button>
      </>}>
      <p className="ev-modal__lead">
        This permanently deletes <b>{project.name}</b> from the workspace — all versions, deployment records and approvals.
        The audit log keeps a record of the deletion.
      </p>
      {detail ? (
        <div className="ev-delstats">
          <span><b>{detail.versions.length}</b> version(s)</span>
          <span><b>{detail.deployments.length}</b> deployment record(s)</span>
          <span><b>{(detail.project.members || []).length}</b> member(s)</span>
        </div>
      ) : <BusyIndicator active size="S" />}
      {liveEnvs.length > 0 && (
        <div className="ev-delcf">
          <div className="ev-delcf__t"><Icon name="cloud" /> The app is deployed on Cloud Foundry</div>
          <div className="ev-muted ev-small">Deleting the project does not touch the deployed app. Tick an environment to remove the app there too.</div>
          {liveEnvs.map((e) => (
            <CheckBox key={e} text={`Remove the deployed app from ${e}`} checked={envs.includes(e)}
              onChange={(ev) => setEnvs((s) => ev.target.checked ? [...s, e] : s.filter((x) => x !== e))} />
          ))}
          {envs.length > 0 && (
            <CheckBox text="Also delete the services it created (XSUAA, destination, database…)" checked={services}
              onChange={(ev) => setServices(ev.target.checked)} />
          )}
        </div>
      )}
      <label className="ev-field"><span>Type <b>{project.name}</b> to confirm</span>
        <Input value={confirm} onInput={(e) => setConfirm(e.target.value)} style={{ width: "100%" }} disabled={busy} /></label>
      {log && <div className="ev-delcf__log"><LiveTimeline messages={log.msgs.slice(-6)} running={log.running} title="Removing from Cloud Foundry" /></div>}
      {err && <MessageStrip design="Negative" hideCloseButton style={{ marginTop: ".6rem" }}>{err}</MessageStrip>}
    </Modal>
  );
}

/** Delete several projects from the workspace (deployed apps stay). */
export function BulkDeleteDialog({ projects, open, onClose, onDone }: {
  projects: P[]; open: boolean; onClose: () => void; onDone: () => void;
}) {
  const toast = useToast();
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  useEffect(() => { if (open) { setConfirm(""); setErr(""); } }, [open]);
  const run = async () => {
    setBusy(true);
    try {
      const r = await api.post<any>("api/projects/bulk-delete", { ids: projects.map((p) => p.id) });
      if (r.deleted.length) toast(`${r.deleted.length} project(s) deleted`);
      if (r.failed.length) setErr(r.failed.map((f: any) => `${f.name || f.id}: ${f.error}`).join(" · "));
      else onClose();
      onDone();
    } catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  };
  return (
    <Modal open={open} onClose={onClose} title={`Delete ${projects.length} project(s)`} icon="delete" tone="danger"
      footer={<>
        <Button design="Transparent" onClick={onClose}>Cancel</Button>
        <Button design="Negative" icon="delete" disabled={confirm.trim().toUpperCase() !== "DELETE" || busy} onClick={run}>{busy ? "Deleting…" : "Delete"}</Button>
      </>}>
      <p className="ev-modal__lead">These projects are deleted permanently with all their versions. Apps deployed on Cloud Foundry are not removed — delete a project on its own page to remove its app too.</p>
      <div className="ev-delnames">{projects.map((p) => <StatusBadge key={p.id} tone="bad" icon="delete">{p.name}</StatusBadge>)}</div>
      <label className="ev-field"><span>Type <b>DELETE</b> to confirm</span>
        <Input value={confirm} onInput={(e) => setConfirm(e.target.value)} style={{ width: "100%" }} /></label>
      {err && <MessageStrip design="Negative" hideCloseButton>{err}</MessageStrip>}
    </Modal>
  );
}

/** Small confirm dialog (delete a version, undeploy…). */
export function ConfirmDialog({ open, title, text, confirmText = "Delete", icon = "delete", onConfirm, onClose }: {
  open: boolean; title: string; text: React.ReactNode; confirmText?: string; icon?: string;
  onConfirm: () => Promise<void> | void; onClose: () => void;
}) {
  const [busy, setBusy] = useState(false);
  return (
    <Modal open={open} onClose={onClose} title={title} icon={icon} tone="danger" width={460}
      footer={<>
        <Button design="Transparent" onClick={onClose}>Cancel</Button>
        <Button design="Negative" icon={icon} disabled={busy} onClick={async () => { setBusy(true); try { await onConfirm(); } finally { setBusy(false); } }}>
          {busy ? "Working…" : confirmText}</Button>
      </>}>
      <div className="ev-modal__lead">{text}</div>
    </Modal>
  );
}
