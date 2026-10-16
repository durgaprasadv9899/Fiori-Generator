import { AccessMatrix } from "../../components/shell/Access";
// Settings: profile & roles, appearance, workspace storage, AI, environments, about.
import { motion } from "framer-motion";
import { Icon, MessageStrip, Switch } from "@ui5/webcomponents-react";
import { Card, PageHeader, Skeleton, StatusBadge } from "../../components/ui";
import { useApi } from "../../components/shell/useApi";
import { ROLE_LABEL } from "../../components/shell/labels";
import { useMe } from "../../app/MeProvider";

interface Env { label: string; api: string; org: string; space: string; requires_approval: boolean; requires_validation: boolean; configured: boolean }

const PERMS: [string, string][] = [
  ["project.create", "Create applications"], ["version.save", "Save versions"], ["approval.request", "Request releases"],
  ["approval.decide", "Approve releases"], ["deploy.dev", "Deploy to development"], ["deploy.test", "Deploy to test"],
  ["deploy.prod", "Deploy to production"], ["project.view_all", "See all projects"],
];

export default function SettingsPage() {
  const { me, dark, toggleDark } = useMe();
  const envs = useApi<{ envs: Record<string, Env> }>("api/environments");
  const src = { xsuaa: "SAP BTP login (XSUAA)", local: "Studio user", "shared-password": "Shared password", open: "Local development (no sign-in)" } as Record<string, string>;
  return (
    <div>
      <PageHeader kicker="Studio" title="Settings" subtitle="Your profile and permissions, appearance, workspace storage and release environments." />
      <div className="ev-content">
        <div className="ev-settings-grid" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(340px, 1fr))", gap: "1.1rem" }}>
          <Card title="Profile" icon="employee" delay={0}>
            {!me ? <Skeleton height={90} /> : (
              <div style={{ display: "grid", gap: ".6rem" }}>
                <div style={{ fontWeight: 800, fontSize: "1.1rem" }}>{me.user.name}</div>
                <div className="ev-muted ev-small">{me.user.id} · {src[me.user.source] || me.user.source}</div>
                <div className="ev-row">{me.user.roles.map((r) => <StatusBadge key={r} tone="brand">{ROLE_LABEL[r] || r}</StatusBadge>)}</div>
                {me.permissions && (
                  <div style={{ display: "grid", gap: ".3rem", marginTop: ".4rem" }}>
                    {PERMS.map(([k, l]) => {
                      const ok = me.permissions!.includes(k);
                      return <div key={k} className="ev-row ev-small"><Icon name={ok ? "accept" : "decline"} style={{ width: ".8rem", height: ".8rem", color: ok ? "var(--sapPositiveColor)" : "var(--sapContent_LabelColor)" }} /><span className={ok ? "" : "ev-muted"}>{l}</span></div>;
                    })}
                  </div>
                )}
                {me.user.source === "xsuaa" && <div className="ev-muted ev-small">Roles come from your SAP BTP role collections (Evoke_Studio_*). Ask a subaccount administrator to change them.</div>}
              </div>
            )}
          </Card>
          <Card title="Appearance" icon="palette" delay={0.05}>
            <div className="ev-row" style={{ justifyContent: "space-between" }}>
              <div><b>Dark theme</b><div className="ev-muted ev-small">SAP Horizon Dark. Saved in this browser.</div></div>
              <Switch checked={dark} onChange={toggleDark} />
            </div>
            <div className="ev-muted ev-small" style={{ marginTop: ".8rem" }}>Tip: press <kbd>Ctrl</kbd><kbd>K</kbd> anywhere to search pages, actions and projects.</div>
          </Card>
          <Card title="Workspace storage" icon="database" delay={0.1}>
            {!me ? <Skeleton height={60} /> : (
              <div style={{ display: "grid", gap: ".5rem" }}>
                <div className="ev-row"><b>{me.store.kind === "hana" ? "SAP HANA Cloud" : "Local SQLite"}</b>
                  <StatusBadge tone={me.store.durable ? "good" : "warn"}>{me.store.durable ? "durable" : "not durable"}</StatusBadge></div>
                <div className="ev-muted ev-small">Projects, versions, approvals, deployments and the audit trail are stored here.</div>
                {me.store.note && <MessageStrip design="Critical" hideCloseButton>{me.store.note}</MessageStrip>}
              </div>
            )}
          </Card>
          <Card title="AI model" icon="ai" delay={0.15}>
            {!me ? <Skeleton height={60} /> : (
              <div style={{ display: "grid", gap: ".5rem" }}>
                <div className="ev-row"><b>{me.ai.model || "Rule-based designer"}</b><StatusBadge tone={me.ai.available ? "good" : "neutral"}>{me.ai.available ? "connected" : "off"}</StatusBadge></div>
                <div className="ev-muted ev-small">{me.ai.available ? "SAP AI Core · Generative AI Hub. Used for requirement analysis, data models, screen design, refinements and translations." : (me.ai.error || "No AI Core deployment available — designs use the rule-based fallback.")}</div>
              </div>
            )}
          </Card>
        </div>

        <Card title="Release environments" icon="cloud" subtitle="Configured by the administrator (STUDIO_ENVIRONMENTS)." className="ev-envs-card" delay={0.2}>
          {envs.loading && !envs.data ? <Skeleton height={100} /> : (
            <table className="ev-dtable">
              <thead><tr><th>Environment</th><th>Cloud Foundry</th><th>Approval</th><th>Validation</th><th>Status</th></tr></thead>
              <tbody>
                {Object.entries(envs.data?.envs || {}).map(([k, e], i) => (
                  <motion.tr key={k} initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: i * 0.05 }}>
                    <td><b>{e.label}</b> <span className="ev-muted ev-small">({k})</span></td>
                    <td className="ev-small">{e.configured ? <>{e.org}<br /><b>{e.space}</b></> : <span className="ev-muted">not configured</span>}</td>
                    <td>{e.requires_approval ? <StatusBadge tone="warn">required</StatusBadge> : <StatusBadge>no</StatusBadge>}</td>
                    <td>{e.requires_validation ? <StatusBadge tone="info">required</StatusBadge> : <StatusBadge>no</StatusBadge>}</td>
                    <td>{e.configured ? <StatusBadge tone="good">ready</StatusBadge> : <StatusBadge tone="bad">blocked</StatusBadge>}</td>
                  </motion.tr>
                ))}
              </tbody>
            </table>
          )}
          {envs.error && <MessageStrip design="Negative" hideCloseButton>{envs.error}</MessageStrip>}
        </Card>

        <AccessMatrix />

        <Card title="About" icon="hint" delay={0.25} className="ev-envs-card">
          <div className="ev-small" style={{ display: "grid", gap: ".3rem" }}>
            <div><b>Evoke Fiori App Studio</b> · web client 0.1 ({import.meta.env.MODE})</div>
            <div className="ev-muted">React + SAP UI5 Web Components (Horizon) on SAP BTP · engine: Python (FastAPI) · AI: SAP AI Core.</div>
          </div>
        </Card>
      </div>
    </div>
  );
}

