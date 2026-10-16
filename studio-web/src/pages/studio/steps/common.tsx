// Small pieces shared by the studio steps.
import React from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Icon, MessageStrip } from "@ui5/webcomponents-react";
import { LiveTimeline } from "../../../components/ui";
import type { Job } from "../../../api/types";
import { draftStore, useDraft } from "../draft";

/** Step headline with an explanation of what happens here. */
export function StepIntro({ icon, title, text, aside }: { icon: string; title: string; text: React.ReactNode; aside?: React.ReactNode }) {
  return (
    <div className="ev-intro">
      <span className="ev-intro__icon"><Icon name={icon} /></span>
      <div className="ev-intro__text">
        <h2>{title}</h2>
        <p>{text}</p>
      </div>
      {aside && <div className="ev-intro__aside">{aside}</div>}
    </div>
  );
}

/** Live progress of a background job (stays while it runs, even after navigating away and back). */
export function JobProgress({ job, title }: { job?: Job; title: string }) {
  return (
    <AnimatePresence>
      {job && job.status === "running" && (
        <motion.div className="ev-jobbox" initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }}
          exit={{ opacity: 0, height: 0 }} transition={{ duration: 0.25 }}>
          <div className="ev-jobbox__head">
            <span className="ev-jobbox__spinner" />
            <b>{title}</b>
            <span className="ev-jobbox__secs">{Math.round(job.seconds)}s</span>
          </div>
          <div className="ev-jobbox__bar"><motion.span animate={{ x: ["-100%", "220%"] }} transition={{ repeat: Infinity, duration: 1.6, ease: "easeInOut" }} /></div>
          <div className="ev-jobbox__note">Runs in the background — you can switch pages; the result is applied when it is ready.</div>
          <LiveTimeline messages={job.messages.length ? job.messages.slice(-8) : ["Starting…"]} running />
        </motion.div>
      )}
    </AnimatePresence>
  );
}

/** Error of the last job of that kind (dismissible). */
export function JobError({ kind }: { kind: string }) {
  const d = useDraft();
  const msg = d.errors[kind];
  if (!msg) return null;
  return (
    <MessageStrip design="Negative" className="ev-gap-top" onClose={() => draftStore.clearError(kind)}>{msg}</MessageStrip>
  );
}

/** Segmented choice built from buttons (keyboard friendly, themable). */
export function Choice<T extends string>({ value, options, onChange }: {
  value: T; options: { key: T; label: string; icon?: string }[]; onChange: (v: T) => void;
}) {
  return (
    <div className="ev-seg" role="radiogroup">
      {options.map((o) => (
        <button key={o.key} role="radio" aria-checked={value === o.key} className={"ev-seg__btn" + (value === o.key ? " is-on" : "")}
          onClick={() => onChange(o.key)}>
          {value === o.key && <motion.span layoutId={`seg-${options.map((x) => x.key).join("")}`} className="ev-seg__bg" transition={{ type: "spring", stiffness: 420, damping: 34 }} />}
          {o.icon && <Icon name={o.icon} />}<span>{o.label}</span>
        </button>
      ))}
    </div>
  );
}
