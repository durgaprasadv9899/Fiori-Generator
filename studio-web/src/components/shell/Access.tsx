// Role-based access in the UI (the API enforces the same rules): page guard, "no role yet" page,
// and the roles → permissions overview shown in Settings.
import React from "react";
import { motion } from "framer-motion";
import { Button, Icon, MessageStrip } from "@ui5/webcomponents-react";
import { useMe } from "../../app/MeProvider";
import { Card, EmptyState, Skeleton, StatusBadge } from "../ui";
import { useApi } from "./useApi";
import "./access.css";

export const ROLE_TONE: Record<string, "brand" | "good" | "warn" | "info" | "neutral"> = {
  admin: "brand", release_manager: "warn", reviewer: "info", developer: "good", viewer: "neutral",
};

/** Signs out through the approuter (ends the SAP BTP session) — only on BTP. */
export function signOut() {
  window.location.href = "do/logout";
}

/** Shows children only when the user has (any of) the permission(s); otherwise a clear "no access". */
export function RequirePerm({ perm, children, what }: { perm: string | string[]; children: React.ReactNode; what?: string }) {
  const { me, can } = useMe();
  if (!me) return <div style={{ padding: "2rem" }}><Skeleton height={140} radius={16} /></div>;
  const perms = Array.isArray(perm) ? perm : [perm];
  if (perms.some((p) => can(p))) return <>{children}</>;
  return (
    <div className="ev-content" style={{ paddingTop: "2.5rem" }}>
      <EmptyState icon="locked" title="You don't have access to this page"
        text={`${what || "This page"} needs one of these roles: ${rolesFor(perms).join(", ") || "—"}. Your roles: ${(me.user.roles.map(label).join(", ")) || "none"}. Ask a studio administrator to assign the matching role collection.`}
        action={<Button icon="home" onClick={() => (window.location.hash = "#/")}>Back to the dashboard</Button>} />
    </div>
  );
}

const ROLE_PERMS: Record<string, string[]> = {
  viewer: ["project.view"],
  developer: ["project.view", "project.create", "project.edit", "version.save", "approval.request", "deploy.dev"],
  reviewer: ["project.view", "project.view_all", "approval.decide"],
  release_manager: ["project.view", "project.view_all", "approval.request", "deploy.test", "deploy.prod"],
  admin: ["*"],
};
const LABEL: Record<string, string> = { viewer: "Viewer", developer: "Developer", reviewer: "Reviewer", release_manager: "Release manager", admin: "Administrator" };
const label = (r: string) => LABEL[r] || r;
function rolesFor(perms: string[]): string[] {
  return Object.entries(ROLE_PERMS).filter(([, ps]) => ps.includes("*") || perms.some((p) => ps.includes(p))).map(([r]) => label(r));
}

/** Full-screen page for a signed-in user without any studio role. */
export function NoRolePage() {
  const { me } = useMe();
  return (
    <div className="ev-norole">
      <motion.div className="ev-norole__card" initial={{ opacity: 0, y: 18, scale: .98 }} animate={{ opacity: 1, y: 0, scale: 1 }}>
        <div className="ev-norole__hero"><img src="./evoke_logo.png" alt="" /></div>
        <div className="ev-norole__lock"><Icon name="locked" /></div>
        <h1>Welcome, {me?.user.name}</h1>
        <p>You are signed in, but you don't have a role in the Evoke Fiori App Studio yet. Ask your SAP BTP administrator to assign
          one of these role collections to <b>{me?.user.id}</b>:</p>
        <div className="ev-norole__roles">
          {[["Evoke_Studio_Developer", "create, generate and deploy to development"],
            ["Evoke_Studio_Reviewer", "approve or reject releases"],
            ["Evoke_Studio_ReleaseManager", "deploy to test and production"],
            ["Evoke_Studio_Viewer", "read-only access"],
            ["Evoke_Studio_Admin", "everything"]].map(([rc, t]) => (
            <div key={rc} className="ev-norole__rc"><span className="ev-mono">{rc}</span><span>{t}</span></div>
          ))}
        </div>
        <p className="ev-small ev-muted">After the assignment, sign out and sign in again.</p>
        <div className="ev-norole__acts">
          <Button design="Emphasized" icon="refresh" onClick={() => window.location.reload()}>Check again</Button>
          <Button icon="log" onClick={signOut}>Sign out</Button>
        </div>
      </motion.div>
    </div>
  );
}

interface AccessInfo {
  mode: string;
  me: { id: string; name: string; roles: string[]; source: string; permissions: string[] };
  roles: { key: string; label: string; role_collection: string; permissions: string[]; cross_project: boolean }[];
  permissions: { key: string; text: string }[];
  how_to: string;
}

/** Roles × permissions matrix with the user's own roles highlighted. */
export function AccessMatrix() {
  const { data, error, loading } = useApi<AccessInfo>("api/access");
  if (error) return <MessageStrip design="Negative" hideCloseButton>{error}</MessageStrip>;
  if (loading || !data) return <Skeleton height={220} radius={14} />;
  const mine = new Set(data.me.roles);
  const all = (r: AccessInfo["roles"][number], p: string) => r.key === "admin" || r.permissions.includes(p);
  return (
    <Card title="Access & roles" icon="shield" className="ev-envs-card"
      subtitle="What each role may do. The studio enforces these rules on every request — buttons you cannot use are hidden or disabled.">
      <div className="ev-access__me">
        <span className="ev-muted ev-small">Your roles</span>
        {data.me.roles.length ? data.me.roles.map((r) => <StatusBadge key={r} tone={ROLE_TONE[r] || "neutral"} icon="employee">{label(r)}</StatusBadge>)
          : <StatusBadge tone="bad">no role</StatusBadge>}
        <span className="ev-muted ev-small" style={{ marginLeft: "auto" }}>sign-in: {data.me.source === "xsuaa" ? "SAP BTP" : data.me.source}</span>
      </div>
      <div style={{ overflow: "auto" }}>
        <table className="ev-dtable ev-access">
          <thead>
            <tr><th>Permission</th>{data.roles.map((r) => (
              <th key={r.key} className={mine.has(r.key) ? "is-mine" : ""}>
                <div>{r.label}</div><div className="ev-mono ev-access__rc">{r.role_collection}</div>
              </th>))}</tr>
          </thead>
          <tbody>
            {data.permissions.map((p, i) => (
              <motion.tr key={p.key} initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: i * 0.02 }}>
                <td>{p.text.charAt(0).toUpperCase() + p.text.slice(1)}</td>
                {data.roles.map((r) => (
                  <td key={r.key} className={"ev-access__cell" + (mine.has(r.key) ? " is-mine" : "")}>
                    {all(r, p.key) ? <Icon name="accept" className="ev-access__yes" /> : <span className="ev-access__no">—</span>}
                  </td>
                ))}
              </motion.tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="ev-muted ev-small" style={{ marginTop: ".7rem" }}>
        Reviewers, release managers and administrators act on every project; developers and viewers on the projects they own or are members of.
        {" "}{data.how_to}
      </div>
    </Card>
  );
}
