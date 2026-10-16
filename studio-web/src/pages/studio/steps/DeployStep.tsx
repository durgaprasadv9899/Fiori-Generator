// Deploy: summary + hand-over to the deploy console (environments, approval, live log).
import { motion } from "framer-motion";
import { useNavigate } from "react-router-dom";
import { Button, Icon } from "@ui5/webcomponents-react";
import { StatusBadge } from "../../../components/ui";
import { draftTitle, useDraft } from "../draft";
import { StepIntro } from "./common";

export function DeployStep() {
  const d = useDraft();
  const nav = useNavigate();
  const g = d.generated;
  if (!g) return null;
  const v = d.validation;
  const items = [
    { icon: "source-code", label: "Project version", value: `v${g.version_number} · ${g.file_count} files`, tone: "brand" },
    { icon: "inspection", label: "Validation", value: v ? (v.blocking.length ? "blocked" : `${v.summary.passed} checks passed`) : "not run yet",
      tone: v ? (v.blocking.length ? "bad" : "good") : "warn" },
    { icon: d.mode === "standalone" ? "database" : "connected", label: d.mode === "standalone" ? "Backend" : "Backend connection",
      value: d.mode === "standalone" ? (d.db === "hana" ? "CAP + SAP HANA Cloud" : "CAP + SQLite (demo)") :
        d.destination.use_existing ? `destination ${d.destination.name}` : `new destination · ${d.destination.auth_type}`, tone: "info" },
  ] as const;
  return (
    <div className="ev-step-page">
      <StepIntro icon="cloud" title="Deploy to SAP BTP"
        text="The deploy console checks the release rules of the target environment (approval for production, validation), signs you in to Cloud Foundry and deploys the MTA with a live log." />
      <motion.div className="ev-deploy-card" initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }}>
        <div className="ev-deploy-card__hero">
          <motion.span className="ev-deploy-card__rocket" animate={{ y: [0, -6, 0] }} transition={{ repeat: Infinity, duration: 2.6 }}>
            <Icon name="cloud" />
          </motion.span>
          <div>
            <h3>{draftTitle(d)}</h3>
            <span className="ev-muted">{d.appId}</span>
          </div>
        </div>
        <div className="ev-deploy-card__items">
          {items.map((it) => (
            <div key={it.label} className="ev-deploy-item">
              <Icon name={it.icon} />
              <span className="ev-deploy-item__l">{it.label}</span>
              <StatusBadge tone={it.tone as any}>{it.value}</StatusBadge>
            </div>
          ))}
        </div>
        {!v && <div className="ev-muted ev-pad"><Icon name="message-warning" /> Test and production require a validation run — run it in the previous step.</div>}
        <div className="ev-cta-row">
          <Button design="Transparent" icon="nav-back" onClick={() => nav("/studio/generate")}>Validate first</Button>
          <Button design="Emphasized" icon="cloud" onClick={() => nav(`/deploy/${g.version_id}`)}>Open deploy console</Button>
        </div>
      </motion.div>
    </div>
  );
}
