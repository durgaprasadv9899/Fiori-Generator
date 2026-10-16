// First screen of the studio: what are you building?
import { motion } from "framer-motion";
import { Icon } from "@ui5/webcomponents-react";
import type { Mode } from "./draft";

const CARDS: { mode: Mode; title: string; lead: string; icon: string; badge: string; points: string[]; flow: string[] }[] = [
  {
    mode: "standalone", title: "Create a new application", badge: "AI · CAP · Fiori",
    lead: "Describe the business need. The AI designs the data model, the approval process and sample data — the studio generates a CAP backend with its own database and the Fiori UI on top.",
    icon: "add-product",
    points: ["Visual data model & workflow designer", "Real approval process enforced on the server", "SQLite demo or SAP HANA Cloud database"],
    flow: ["Describe", "Data model", "Screens", "Try it", "Generate", "Deploy"],
  },
  {
    mode: "sap", title: "Build on an SAP service", badge: "S/4HANA · OData V2/V4",
    lead: "Connect an existing OData service through a BTP destination or paste its $metadata. The AI designs screens only with entity sets, fields and actions that really exist.",
    icon: "connected",
    points: ["Browse BTP destinations and the service catalog", "Combine several services in one app", "Uses your existing destination & Cloud Connector"],
    flow: ["Connect", "Describe", "Screens", "Try it", "Generate", "Deploy"],
  },
];

export function ModeChooser({ onPick }: { onPick: (m: Mode) => void }) {
  return (
    <div className="ev-chooser">
      <motion.div className="ev-chooser__intro" initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }}>
        <div className="ev-chooser__kicker">Application studio</div>
        <h1>What would you like to build?</h1>
        <p>Pick a starting point. You can review every design decision, try the app with sample data and validate it before anything is deployed.</p>
      </motion.div>
      <div className="ev-chooser__grid">
        {CARDS.map((c, i) => (
          <motion.button key={c.mode} className={`ev-mode ev-mode--${c.mode}`} onClick={() => onPick(c.mode)}
            initial={{ opacity: 0, y: 18 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.08 + i * 0.1, duration: 0.35 }}
            whileHover={{ y: -6 }} whileTap={{ scale: 0.985 }}>
            <div className="ev-mode__art" aria-hidden>
              <motion.span className="ev-mode__orb ev-mode__orb--1" animate={{ y: [0, -6, 0] }} transition={{ repeat: Infinity, duration: 4 }} />
              <motion.span className="ev-mode__orb ev-mode__orb--2" animate={{ y: [0, 5, 0] }} transition={{ repeat: Infinity, duration: 5 }} />
              <span className="ev-mode__icon"><Icon name={c.icon} /></span>
            </div>
            <div className="ev-mode__badge">{c.badge}</div>
            <div className="ev-mode__title">{c.title}</div>
            <div className="ev-mode__lead">{c.lead}</div>
            <ul className="ev-mode__points">
              {c.points.map((p) => <li key={p}><Icon name="accept" />{p}</li>)}
            </ul>
            <div className="ev-mode__flow">
              {c.flow.map((f, j) => <span key={f}>{f}{j < c.flow.length - 1 && <Icon name="slim-arrow-right" />}</span>)}
            </div>
            <div className="ev-mode__cta"><span className="ev-mode__go">Start <Icon name="navigation-right-arrow" /></span></div>
          </motion.button>
        ))}
      </div>
    </div>
  );
}
