// Import an existing application: upload a project ZIP, or pick an app deployed on Cloud Foundry.
// Studio-generated apps come back with their full design; client Fiori apps with files + metadata.
import { useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { useNavigate } from "react-router-dom";
import { Button, Icon, Input, MessageStrip, BusyIndicator } from "@ui5/webcomponents-react";
import { api } from "../../api/client";
import { useJobs } from "../../jobs/JobProvider";
import { useToast } from "./Toasts";
import { Modal } from "./ProjectDialogs";
import { LiveTimeline, Skeleton, StatusBadge } from "../ui";
import "./import.css";

interface CfApp { name: string; state: string; in_workspace?: boolean }

export function ImportDialog({ open, onClose, initial = "zip" }: { open: boolean; onClose: () => void; initial?: "zip" | "cf" }) {
  const [tab, setTab] = useState<"zip" | "cf">(initial);
  useEffect(() => { if (open) setTab(initial); }, [open, initial]);
  return (
    <Modal open={open} onClose={onClose} title="Import an existing application" icon="download-from-cloud" width={720}>
      <div className="ev-imp__tabs">
        {([["zip", "upload", "Upload a ZIP", "A studio export or any UI5 / Fiori project folder"],
           ["cf", "cloud", "From Cloud Foundry", "An app deployed in your BTP space"]] as const).map(([k, icon, t, s]) => (
          <button key={k} className={"ev-imp__tab" + (tab === k ? " is-on" : "")} onClick={() => setTab(k)}>
            <span className="ev-imp__tabicon"><Icon name={icon} /></span>
            <span><b>{t}</b><small>{s}</small></span>
            {tab === k && <motion.span layoutId="imp-tab" className="ev-imp__tabline" />}
          </button>
        ))}
      </div>
      <AnimatePresence mode="wait">
        <motion.div key={tab} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -6 }} transition={{ duration: 0.18 }}>
          {tab === "zip" ? <ZipImport onDone={onClose} /> : <CfImport onDone={onClose} />}
        </motion.div>
      </AnimatePresence>
    </Modal>
  );
}

function ZipImport({ onDone }: { onDone: () => void }) {
  const nav = useNavigate();
  const toast = useToast();
  const ref = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");
  const upload = async (f?: File) => {
    if (!f) return;
    if (!/\.zip$/i.test(f.name)) { setErr("Choose a .zip file."); return; }
    setBusy(f.name); setErr("");
    try {
      const fd = new FormData();
      fd.append("file", f);
      const p = await api.post<any>("api/projects/import", fd);
      toast(`Imported “${p.name}”` + (p.imported?.kind === "generated" ? " — design restored" : ""));
      onDone();
      nav(`/projects/${p.id}`);
    } catch (e: any) { setErr(e.message); } finally { setBusy(""); if (ref.current) ref.current.value = ""; }
  };
  return (
    <div className="ev-imp__body">
      <div className={"ev-drop" + (over ? " is-over" : "")} onClick={() => !busy && ref.current?.click()}
        onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)}
        onDrop={(e) => { e.preventDefault(); setOver(false); upload(e.dataTransfer.files?.[0]); }}>
        {busy ? <><BusyIndicator active size="M" /><b>Importing {busy}…</b></> : <>
          <span className="ev-drop__icon"><Icon name="upload-to-cloud" /></span>
          <b>Drop a ZIP here or click to choose</b>
          <span className="ev-muted ev-small">Workspace export from this studio, a project downloaded from the studio, or any SAPUI5 / Fiori Elements project folder (with webapp/manifest.json).</span>
        </>}
        <input ref={ref} type="file" accept=".zip" hidden onChange={(e) => upload(e.target.files?.[0])} />
      </div>
      {err && <MessageStrip design="Negative" hideCloseButton>{err}</MessageStrip>}
    </div>
  );
}

function CfImport({ onDone }: { onDone: () => void }) {
  const nav = useNavigate();
  const toast = useToast();
  const jobs = useJobs();
  const [data, setData] = useState<{ apps: CfApp[]; space: string; org: string } | null>(null);
  const [err, setErr] = useState("");
  const [q, setQ] = useState("");
  const [jobId, setJobId] = useState("");
  const [picked, setPicked] = useState("");
  const load = () => { setErr(""); setData(null); api.get("api/cf/apps").then(setData).catch((e) => setErr(e.message)); };
  useEffect(load, []);
  const job = jobs.get(jobId);
  const apps = useMemo(() => (data?.apps || []).filter((a) => !q || a.name.toLowerCase().includes(q.toLowerCase()))
    .sort((a, b) => Number(!!b.name.match(/^com\./)) - Number(!!a.name.match(/^com\./)) || a.name.localeCompare(b.name)), [data, q]);
  const start = async (name: string) => {
    setPicked(name); setErr("");
    try {
      const id = await jobs.start("api/jobs/import-cf", { app: name }, {
        onDone: (r) => { toast(`Imported “${r.name}”` + (r.kind === "generated" ? " — design restored" : "")); onDone(); nav(`/projects/${r.project_id}`); },
        onError: (e) => { setErr(e); setPicked(""); },
      });
      setJobId(id);
    } catch (e: any) { setErr(e.message); setPicked(""); }
  };
  return (
    <div className="ev-imp__body">
      {data && <div className="ev-imp__space"><Icon name="cloud" /> {data.org} · <b>{data.space}</b><span className="ev-muted ev-small">{data.apps.length} apps</span>
        <Button design="Transparent" icon="refresh" tooltip="Refresh" onClick={load} /></div>}
      {err && <MessageStrip design="Negative" hideCloseButton>{err}</MessageStrip>}
      {job && job.status === "running" ? (
        <div className="ev-imp__job"><LiveTimeline title={`Importing ${picked}`} messages={job.messages.length ? job.messages : ["Starting…"]} running /></div>
      ) : (
        <>
          <Input icon={<Icon name="search" />} placeholder="Search apps…" value={q} onInput={(e) => setQ(e.target.value)} showClearIcon style={{ width: "100%" }} />
          <div className="ev-cfapps">
            {!data && !err ? (
              <div className="ev-imp__loading"><BusyIndicator active size="M" /><b>Reading the apps from Cloud Foundry…</b>
                <span className="ev-muted ev-small">This takes a few seconds.</span>
                {Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} height={40} radius={10} />)}</div>
            ) :
              apps.length === 0 ? <div className="ev-muted ev-small" style={{ padding: ".8rem" }}>No app matches.</div> :
                apps.map((a, i) => (
                  <motion.div key={a.name} className="ev-cfapp" initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: Math.min(i, 15) * 0.015 }}>
                    <span className={"ev-cfapp__state" + (a.state === "started" ? " is-on" : "")} title={a.state} />
                    <span className="ev-cfapp__name ev-mono">{a.name}</span>
                    {a.in_workspace && <StatusBadge tone="info">in workspace</StatusBadge>}
                    <span className="ev-muted ev-small">{a.state}</span>
                    <Button design="Transparent" icon="download" onClick={() => start(a.name)}>Import</Button>
                  </motion.div>
                ))}
          </div>
          <div className="ev-muted ev-small">The app's deployed source package is downloaded. Apps generated by this studio are restored with their design — change, regenerate and redeploy them. Other Fiori apps are imported with their files.</div>
        </>
      )}
    </div>
  );
}
