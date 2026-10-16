// Users & roles (administrators): search subaccount users, toggle their studio role collections,
// add a user for an identity provider. Changes apply at the user's next sign-in.
import { useEffect, useMemo, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Button, Icon, Input, MessageStrip, Option, Select, Switch, BusyIndicator } from "@ui5/webcomponents-react";
import { EmptyState, PageHeader, Skeleton, StatusBadge } from "../../components/ui";
import { useApi } from "../../components/shell/useApi";
import { useToast } from "../../components/shell/Toasts";
import { Modal } from "../../components/shell/ProjectDialogs";
import { ROLE_TONE } from "../../components/shell/Access";
import { useMe } from "../../app/MeProvider";
import { api } from "../../api/client";
import "./users.css";

interface UserRow { id: string; user: string; origin: string; name: string; email: string; roles: string[] }
const ROLES: { key: string; label: string; hint: string; icon: string }[] = [
  { key: "viewer", label: "Viewer", hint: "Read-only access to projects", icon: "show" },
  { key: "developer", label: "Developer", hint: "Create, generate, deploy to development", icon: "source-code" },
  { key: "reviewer", label: "Reviewer", hint: "Approve or reject releases", icon: "approvals" },
  { key: "release_manager", label: "Release manager", hint: "Deploy to test and production", icon: "paper-plane" },
  { key: "admin", label: "Administrator", hint: "Everything, incl. user management", icon: "key-user-settings" },
];

export default function UsersPage() {
  const toast = useToast();
  const { me } = useMe();
  const [q, setQ] = useState("");
  const [term, setTerm] = useState("");
  const [onlyStudio, setOnlyStudio] = useState(true);
  const { data, error, loading, reload } = useApi<{ users: UserRow[] }>(
    `api/admin/users?q=${encodeURIComponent(term)}&studio_only=${onlyStudio ? 1 : 0}`, { deps: [term, onlyStudio] });
  const [saving, setSaving] = useState<Record<string, boolean>>({});
  const [rows, setRows] = useState<UserRow[]>([]);
  const [adding, setAdding] = useState(false);
  useEffect(() => { if (data) setRows(data.users); }, [data]);
  useEffect(() => { const t = setTimeout(() => setTerm(q.trim()), 350); return () => clearTimeout(t); }, [q]);

  const save = async (u: UserRow, roles: string[]) => {
    const key = u.user + u.origin;
    setSaving((s) => ({ ...s, [key]: true }));
    try {
      const r = await api.post<any>("api/admin/users", { user: u.user, origin: u.origin, roles });
      setRows((rs) => rs.map((x) => (x.user === u.user && x.origin === u.origin ? { ...x, roles: r.user.roles } : x)));
      const parts = [r.added.length ? `+ ${r.added.join(", ")}` : "", r.removed.length ? `− ${r.removed.join(", ")}` : ""].filter(Boolean);
      toast(`${u.user}: ${parts.join("  ") || "no change"} — active at the next sign-in`);
    } catch (e: any) { toast(e.message, "error"); } finally { setSaving((s) => ({ ...s, [key]: false })); }
  };
  const toggle = (u: UserRow, role: string) => save(u, u.roles.includes(role) ? u.roles.filter((r) => r !== role) : [...u.roles, role]);
  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    rows.forEach((u) => u.roles.forEach((r) => (c[r] = (c[r] || 0) + 1)));
    return c;
  }, [rows]);

  return (
    <div>
      <PageHeader kicker="Governance" title="Users & roles"
        subtitle="Give people access to the studio. Roles are SAP BTP role collections (Evoke_Studio_*) — only these are changed here; changes apply when the user signs in again."
        actions={<>
          <Button icon="refresh" design="Transparent" tooltip="Refresh" onClick={() => reload()} />
          <Button icon="add-employee" design="Emphasized" onClick={() => setAdding(true)}>Add user</Button>
        </>} />
      <div className="ev-content">
        <div className="ev-urole-sum">
          {ROLES.map((r, i) => (
            <motion.div key={r.key} className="ev-urole-tile" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.04 }}>
              <Icon name={r.icon} /><div><b>{counts[r.key] || 0}</b><span>{r.label}</span></div>
            </motion.div>
          ))}
        </div>
        <div className="ev-toolbar">
          <Input className="ev-toolbar__search" icon={<Icon name="search" />} placeholder="Search users by e-mail…" value={q}
            onInput={(e) => setQ(e.target.value)} showClearIcon />
          <div className="ev-toolbar__right">
            <span className="ev-muted ev-small">Only users with a studio role</span>
            <Switch checked={onlyStudio} onChange={(e) => setOnlyStudio(e.target.checked)} />
          </div>
        </div>
        {error && <MessageStrip design="Negative" hideCloseButton className="ev-mb">{error}</MessageStrip>}
        {loading && !data ? <div className="ev-ulist">{Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} height={64} radius={14} />)}</div>
          : rows.length === 0 ? (
            <EmptyState icon="group" title={term ? "No user matches" : onlyStudio ? "Nobody has a studio role yet" : "No users"}
              text={onlyStudio ? "Switch off the filter to see all users of the subaccount, or add a user." : undefined}
              action={<Button design="Emphasized" icon="add-employee" onClick={() => setAdding(true)}>Add user</Button>} />
          ) : (
            <div className="ev-ulist">
              <AnimatePresence initial={false}>
                {rows.map((u, i) => {
                  const busy = saving[u.user + u.origin];
                  const self = u.user === me?.user.id;
                  return (
                    <motion.div key={u.user + u.origin} className="ev-urow" layout
                      initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: Math.min(i, 12) * 0.03 }}>
                      <span className="ev-urow__av">{(u.name || u.user)[0]?.toUpperCase()}</span>
                      <div className="ev-urow__who">
                        <div className="ev-urow__name">{u.name || u.user}{self && <StatusBadge tone="brand">you</StatusBadge>}</div>
                        <div className="ev-muted ev-small">{u.user} · {u.origin}</div>
                      </div>
                      <div className="ev-urow__roles">
                        {ROLES.map((r) => {
                          const on = u.roles.includes(r.key);
                          const locked = self && r.key === "admin" && on;
                          return (
                            <button key={r.key} className={"ev-rolepill" + (on ? ` is-on tone-${ROLE_TONE[r.key]}` : "")}
                              disabled={busy || locked} title={locked ? "You cannot remove your own Administrator role" : `${r.label}: ${r.hint}`}
                              onClick={() => toggle(u, r.key)}>
                              <Icon name={on ? "accept" : "add"} />{r.label}
                            </button>
                          );
                        })}
                      </div>
                      <div className="ev-urow__state">{busy ? <BusyIndicator active size="S" /> : u.roles.length ? null : <StatusBadge>no access</StatusBadge>}</div>
                    </motion.div>
                  );
                })}
              </AnimatePresence>
            </div>
          )}
      </div>
      <AddUserDialog open={adding} onClose={() => setAdding(false)} onAdded={(u) => { setRows((rs) => [u, ...rs.filter((x) => !(x.user === u.user && x.origin === u.origin))]); }} />
    </div>
  );
}

function AddUserDialog({ open, onClose, onAdded }: { open: boolean; onClose: () => void; onAdded: (u: UserRow) => void }) {
  const toast = useToast();
  const idps = useApi<{ idps: { origin: string; name: string }[] }>(open ? "api/admin/idps" : null);
  const [email, setEmail] = useState("");
  const [origin, setOrigin] = useState("sap.default");
  const [roles, setRoles] = useState<string[]>(["developer"]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  useEffect(() => { if (open) { setEmail(""); setRoles(["developer"]); setErr(""); } }, [open]);
  const ok = /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email.trim()) && roles.length > 0 && !busy;
  const add = async () => {
    setBusy(true); setErr("");
    try {
      const r = await api.post<any>("api/admin/users", { user: email.trim().toLowerCase(), origin, roles });
      toast(`${r.user.user} ${r.created ? "added" : "updated"} — roles active at the next sign-in`);
      onAdded(r.user); onClose();
    } catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  };
  return (
    <Modal open={open} onClose={onClose} title="Add a user" icon="add-employee" width={560}
      footer={<>
        <Button design="Transparent" onClick={onClose}>Cancel</Button>
        <Button design="Emphasized" icon="add-employee" disabled={!ok} onClick={add}>{busy ? "Saving…" : "Add user"}</Button>
      </>}>
      <label className="ev-field"><span>E-mail (the user's login name)</span>
        <Input value={email} placeholder="firstname.lastname@company.com" onInput={(e) => setEmail(e.target.value)} style={{ width: "100%" }} /></label>
      <label className="ev-field"><span>Identity provider</span>
        <Select onChange={(e) => setOrigin((e.detail.selectedOption as any).value)} style={{ width: "100%" }}>
          {(idps.data?.idps || [{ origin: "sap.default", name: "Default identity provider" }]).map((p) => (
            <Option key={p.origin} value={p.origin} selected={p.origin === origin}>{p.name} ({p.origin})</Option>))}
        </Select></label>
      <div className="ev-field"><span>Studio roles</span>
        <div className="ev-addroles">
          {ROLES.map((r) => {
            const on = roles.includes(r.key);
            return (
              <button key={r.key} className={"ev-addrole" + (on ? " is-on" : "")} onClick={() => setRoles((s) => on ? s.filter((x) => x !== r.key) : [...s, r.key])}>
                <Icon name={r.icon} /><div><b>{r.label}</b><span>{r.hint}</span></div><Icon name={on ? "accept" : "add"} className="ev-addrole__chk" />
              </button>
            );
          })}
        </div>
      </div>
      <div className="ev-muted ev-small">The user signs in with this identity provider; the studio roles become active at their next sign-in.</div>
      {err && <MessageStrip design="Negative" hideCloseButton>{err}</MessageStrip>}
    </Modal>
  );
}
