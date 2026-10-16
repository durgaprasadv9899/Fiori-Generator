// Horizontal animated stepper: done / current / available / locked, progress line fills as you go.
import { motion } from "framer-motion";
import { Icon } from "@ui5/webcomponents-react";
import type { StepDef } from "./flow";

export function Stepper({ steps, current, reachable, onGo }: {
  steps: StepDef[]; current: number; reachable: number; onGo: (key: string) => void;
}) {
  const pct = steps.length > 1 ? (current / (steps.length - 1)) * 100 : 0;
  return (
    <nav className="ev-stepper" aria-label="Steps">
      <div className="ev-stepper__track">
        <motion.div className="ev-stepper__fill" initial={false} animate={{ width: `${pct}%` }}
          transition={{ type: "spring", stiffness: 120, damping: 22 }} />
      </div>
      <ol className="ev-stepper__list">
        {steps.map((s, i) => {
          const state = i === current ? "cur" : i < current ? "done" : i <= reachable ? "open" : "locked";
          return (
            <li key={s.key} className={`ev-step is-${state}`}>
              <button className="ev-step__btn" disabled={state === "locked"} onClick={() => onGo(s.key)}
                aria-current={i === current ? "step" : undefined}
                title={state === "locked" ? "Complete the previous steps first" : s.hint}>
                <motion.span className="ev-step__dot" layout
                  animate={{ scale: i === current ? 1.08 : 1 }} transition={{ type: "spring", stiffness: 300, damping: 20 }}>
                  {state === "done" ? <Icon name="accept" /> : state === "locked" ? <Icon name="locked" /> : <span>{i + 1}</span>}
                </motion.span>
                <span className="ev-step__text">
                  <span className="ev-step__label">{s.label}</span>
                  <span className="ev-step__hint">{s.hint}</span>
                </span>
              </button>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
