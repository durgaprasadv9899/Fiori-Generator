// Top-bar menus: running jobs, notifications, theme toggle, profile.
import { useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { useNavigate } from "react-router-dom";
import { Icon, BusyIndicator } from "@ui5/webcomponents-react";
import { signOut } from "./Access";
import { Dropdown } from "./Dropdown";
import { useApi } from "./useApi";
import { ROLE_LABEL } from "./labels";
import { useJobs } from "../../jobs/JobProvider";
import { useMe } from "../../app/MeProvider";
import { StatusBadge, ago } from "../ui";

const STUDIO_KINDS = new Set(["questions", "domain", "design", "refine", "generate"]);

export function JobsMenu() {
  const { jobs, dismiss } = useJobs();
  const [open, setOpen] = useState(false);
  const nav = useNavigate();
  const running = jobs.filter((j) => j.status === "running");
  const recent = jobs.slice(0, 8);
  return (
    <Dropdown open={open} onClose={() => setOpen(false)} width={380}
      anchor={
        <button className={"ev-tb__btn" + (running.length ? " is-busy" : "")} onClick={() => setOpen(!open)}
          title={running.length ? `${running.length} job(s) running` : "Background jobs"}>
          <span className="ev-jobring">{running.length > 0 && <span className="ev-jobring__spin" />}<Icon name="activity-2" /></span>
          <AnimatePresence>
            {running.length > 0 && (
              <motion.span className="ev-tb__count" initial={{ scale: 0 }} animate={{ scale: 1 }} exit={{ scale: 0 }}>{running.length}</motion.span>
            )}
          </AnimatePresence>
        </button>
      }>
      <div className="ev-dd__head">Background jobs<span>{running.length ? `${running.length} running` : "all idle"}</span></div>
      {recent.length === 0 && <div className="ev-dd__empty">AI design, generation, validation and deployments run here — you can keep working meanwhile.</div>}
      {recent.map((j) => (
        <div key={j.id} className="ev-job" onClick={() => {
          setOpen(false);
          if (STUDIO_KINDS.has(j.kind)) nav("/studio");
          else if (j.kind === "validate") nav("/projects");
          // deploy jobs are shown live on their deploy page
        }}>
          <div className="ev-job__icon">
            {j.status === "running" ? <BusyIndicator active size="S" /> :
              <Icon name={j.status === "done" ? "sys-enter-2" : "error"} className={j.status === "done" ? "ok" : "bad"} />}
          </div>
          <div className="ev-job__body">
            <div className="ev-job__title">{j.label}<span>{Math.round(j.seconds)}s</span></div>
            <div className="ev-job__msg">{j.status === "error" ? j.error : (j.messages[j.messages.length - 1] || (j.status === "running" ? "Working…" : "Finished"))}</div>
            {j.status === "running" && <div className="ev-job__bar"><span /></div>}
          </div>
          {j.status !== "running" && (
            <button className="ev-icon-btn" title="Dismiss" onClick={(e) => { e.stopPropagation(); dismiss(j.id); }}><Icon name="decline" /></button>
          )}
        </div>
      ))}
    </Dropdown>
  );
}

interface Notif { id: string; kind: string; title: string; text: string; at: number; link?: string }

export function NotificationsMenu() {
  const [open, setOpen] = useState(false);
  const nav = useNavigate();
  const { data } = useApi<{ items: Notif[]; unread: number }>("api/notifications", { poll: 30000 });
  const [seen, setSeen] = useState<string>(() => { try { return localStorage.getItem("evoke.notif.seen") || ""; } catch { return ""; } });
  const items = data?.items || [];
  const unread = items.filter((n) => !seen || n.at > Number(seen)).length;
  const markSeen = () => {
    const now = String(Date.now() / 1000);
    setSeen(now);
    try { localStorage.setItem("evoke.notif.seen", now); } catch { /* ignore */ }
  };
  const icon: Record<string, string> = { approval_pending: "approvals", approval_decided: "accept", deploy: "paper-plane", job: "activity-2" };
  return (
    <Dropdown open={open} onClose={() => setOpen(false)} width={380}
      anchor={
        <button className="ev-tb__btn" onClick={() => { setOpen(!open); if (!open) setTimeout(markSeen, 1500); }} title="Notifications">
          <Icon name="bell" />
          <AnimatePresence>
            {unread > 0 && <motion.span className="ev-tb__count is-red" initial={{ scale: 0 }} animate={{ scale: 1 }} exit={{ scale: 0 }}>{unread}</motion.span>}
          </AnimatePresence>
        </button>
      }>
      <div className="ev-dd__head">Notifications{items.length > 0 && <button className="ev-link-btn" onClick={markSeen}>Mark all read</button>}</div>
      {items.length === 0 && <div className="ev-dd__empty">You're all caught up. Approval requests, decisions and deployments show up here.</div>}
      <div className="ev-dd__scroll">
        {items.map((n) => (
          <div key={n.id} className={"ev-notif" + (!seen || n.at > Number(seen) ? " is-new" : "")}
            onClick={() => { setOpen(false); if (n.link) nav(n.link); }}>
            <div className="ev-notif__icon"><Icon name={icon[n.kind] || "message-information"} /></div>
            <div className="ev-notif__body">
              <div className="ev-notif__title">{n.title}</div>
              <div className="ev-notif__text">{n.text}</div>
              <div className="ev-notif__at">{ago(n.at)}</div>
            </div>
          </div>
        ))}
      </div>
    </Dropdown>
  );
}

export function ThemeToggle() {
  const { dark, toggleDark } = useMe();
  return (
    <button className="ev-tb__btn ev-theme" onClick={toggleDark} title={dark ? "Switch to light theme" : "Switch to dark theme"}>
      <AnimatePresence mode="wait" initial={false}>
        <motion.span key={dark ? "d" : "l"} initial={{ rotate: -90, opacity: 0, scale: 0.6 }} animate={{ rotate: 0, opacity: 1, scale: 1 }}
          exit={{ rotate: 90, opacity: 0, scale: 0.6 }} transition={{ duration: 0.22 }} style={{ display: "grid" }}>
          <Icon name={dark ? "light-mode" : "dark-mode"} />
        </motion.span>
      </AnimatePresence>
    </button>
  );
}

export function ProfileMenu() {
  const { me } = useMe();
  const [open, setOpen] = useState(false);
  const nav = useNavigate();
  const name = me?.user.name || "…";
  const initials = name.split(/[\s.@]+/).filter(Boolean).slice(0, 2).map((p) => p[0]?.toUpperCase()).join("") || "?";
  const src = { xsuaa: "SAP BTP login", local: "Studio user", "shared-password": "Shared password", open: "Local development" } as Record<string, string>;
  return (
    <Dropdown open={open} onClose={() => setOpen(false)} width={320}
      anchor={<button className="ev-avatar" onClick={() => setOpen(!open)} title={name}>{initials}</button>}>
      <div className="ev-prof">
        <div className="ev-prof__avatar">{initials}</div>
        <div className="ev-prof__name">{name}</div>
        <div className="ev-prof__id">{me?.user.id}</div>
        <div className="ev-prof__roles">
          {(me?.user.roles || []).map((r) => <StatusBadge key={r} tone="brand">{ROLE_LABEL[r] || r}</StatusBadge>)}
        </div>
      </div>
      <div className="ev-prof__rows">
        <div><Icon name="employee" /><span>Signed in via</span><b>{src[me?.user.source || ""] || me?.user.source}</b></div>
        <div><Icon name="database" /><span>Workspace</span><b>{me?.store.kind === "hana" ? "SAP HANA Cloud" : "Local storage"}</b></div>
        <div><Icon name="ai" /><span>AI model</span><b>{me?.ai.model || "Rule-based"}</b></div>
      </div>
      {me?.store.note && <div className="ev-prof__note">{me.store.note}</div>}
      <div className="ev-prof__actions">
        <button className="ev-menu-item" onClick={() => { setOpen(false); nav("/settings"); }}><Icon name="settings" />Settings</button>
        {me?.user.source === "xsuaa" && <button className="ev-menu-item" onClick={signOut}><Icon name="log" />Sign out</button>}
      </div>
    </Dropdown>
  );
}
