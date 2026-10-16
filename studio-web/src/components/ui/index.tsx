// Shared building blocks — every page uses these so the studio looks and moves the same everywhere.
import React, { useEffect, useRef, useState } from "react";
import { animate, motion, useInView } from "framer-motion";
import { Icon, Title, Text, BusyIndicator } from "@ui5/webcomponents-react";
import "./ui.css";

/** Page header: kicker, title, subtitle, actions on the right; optional gradient hero. */
export function PageHeader(props: {
  kicker?: string; title: React.ReactNode; subtitle?: React.ReactNode; actions?: React.ReactNode; hero?: boolean;
}) {
  return (
    <motion.header
      className={"ev-ph" + (props.hero ? " ev-ph--hero" : "")}
      initial={{ opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.3 }}
    >
      <div className="ev-ph__text">
        {props.kicker && <div className="ev-ph__kicker">{props.kicker}</div>}
        <Title level="H2" className="ev-ph__title">{props.title}</Title>
        {props.subtitle && <div className="ev-ph__sub">{props.subtitle}</div>}
      </div>
      {props.actions && <div className="ev-ph__actions">{props.actions}</div>}
    </motion.header>
  );
}

/** Content card with optional header + hover lift. */
export function Card(props: {
  title?: React.ReactNode; subtitle?: React.ReactNode; icon?: string; actions?: React.ReactNode;
  children?: React.ReactNode; className?: string; onClick?: () => void; padded?: boolean; delay?: number;
}) {
  return (
    <motion.section
      className={"ev-card" + (props.onClick ? " ev-card--click" : "") + (props.className ? " " + props.className : "")}
      onClick={props.onClick}
      initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.28, delay: props.delay || 0 }}
      whileHover={props.onClick ? { y: -3 } : undefined}
    >
      {(props.title || props.actions) && (
        <div className="ev-card__head">
          {props.icon && <span className="ev-card__icon"><Icon name={props.icon} /></span>}
          <div className="ev-card__titles">
            {props.title && <div className="ev-card__title">{props.title}</div>}
            {props.subtitle && <div className="ev-card__sub">{props.subtitle}</div>}
          </div>
          {props.actions && <div className="ev-card__actions">{props.actions}</div>}
        </div>
      )}
      <div className={props.padded === false ? "" : "ev-card__body"}>{props.children}</div>
    </motion.section>
  );
}

/** Number that counts up when it scrolls into view. */
export function AnimatedNumber({ value, suffix = "", decimals = 0 }: { value: number; suffix?: string; decimals?: number }) {
  const ref = useRef<HTMLSpanElement>(null);
  const inView = useInView(ref, { once: true });
  const [shown, setShown] = useState(0);
  useEffect(() => {
    if (!inView) return;
    const c = animate(0, value || 0, { duration: 0.9, ease: "easeOut", onUpdate: (v) => setShown(v) });
    return () => c.stop();
  }, [inView, value]);
  return <span ref={ref}>{shown.toFixed(decimals)}{suffix}</span>;
}

/** KPI tile: label, animated value, hint, icon, accent colour. */
export function KpiTile(props: { label: string; value: number; suffix?: string; hint?: string; icon?: string;
  tone?: "brand" | "good" | "warn" | "bad" | "neutral"; delay?: number; onClick?: () => void }) {
  return (
    <motion.div
      className={`ev-kpi ev-kpi--${props.tone || "brand"}` + (props.onClick ? " ev-card--click" : "")}
      onClick={props.onClick}
      initial={{ opacity: 0, y: 12, scale: 0.98 }} animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{ duration: 0.35, delay: props.delay || 0 }} whileHover={{ y: -3 }}
    >
      <div className="ev-kpi__top">
        <span className="ev-kpi__label">{props.label}</span>
        {props.icon && <Icon name={props.icon} className="ev-kpi__icon" />}
      </div>
      <div className="ev-kpi__value"><AnimatedNumber value={props.value} suffix={props.suffix} /></div>
      {props.hint && <div className="ev-kpi__hint">{props.hint}</div>}
    </motion.div>
  );
}

export type Tone = "good" | "warn" | "bad" | "info" | "neutral" | "brand";
/** Small coloured pill. */
export function StatusBadge({ tone = "neutral", children, icon }: { tone?: Tone; children: React.ReactNode; icon?: string }) {
  return <span className={`ev-badge ev-badge--${tone}`}>{icon && <Icon name={icon} />}{children}</span>;
}

/** Friendly empty state. */
export function EmptyState({ icon = "document", title, text, action }: { icon?: string; title: string; text?: string; action?: React.ReactNode }) {
  return (
    <motion.div className="ev-empty" initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
      <div className="ev-empty__icon"><Icon name={icon} /></div>
      <Title level="H5">{title}</Title>
      {text && <Text className="ev-empty__text">{text}</Text>}
      {action && <div className="ev-empty__action">{action}</div>}
    </motion.div>
  );
}

/** Shimmer placeholder while data loads. */
export function Skeleton({ height = 16, width = "100%", radius = 8 }: { height?: number; width?: number | string; radius?: number }) {
  return <div className="ev-skel" style={{ height, width, borderRadius: radius }} />;
}

/** Children appear one after another. */
export function Stagger({ children, className, step = 0.05 }: { children: React.ReactNode; className?: string; step?: number }) {
  return (
    <motion.div className={className} initial="h" animate="s"
      variants={{ h: {}, s: { transition: { staggerChildren: step } } }}>
      {React.Children.map(children, (c) => (
        <motion.div variants={{ h: { opacity: 0, y: 8 }, s: { opacity: 1, y: 0 } }}>{c}</motion.div>
      ))}
    </motion.div>
  );
}

/** Live AI / job progress: messages appear as an animated timeline. */
export function LiveTimeline({ messages, running, title }: { messages: string[]; running: boolean; title?: string }) {
  return (
    <div className="ev-tl">
      {title && <div className="ev-tl__title">{running && <BusyIndicator active size="S" />}{title}</div>}
      {messages.map((m, i) => {
        const last = i === messages.length - 1;
        return (
          <motion.div key={i + m} className={"ev-tl__item" + (last && running ? " is-live" : "")}
            initial={{ opacity: 0, x: -8 }} animate={{ opacity: 1, x: 0 }} transition={{ duration: 0.2 }}>
            <span className="ev-tl__dot">{last && running ? <span className="ev-pulse" /> : <Icon name="accept" />}</span>
            <span className="ev-tl__msg">{m}</span>
          </motion.div>
        );
      })}
    </div>
  );
}

/** Relative time ("3 min ago") for epoch seconds. */
export function ago(ts?: number | null): string {
  if (!ts) return "";
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  if (s < 86400 * 7) return `${Math.floor(s / 86400)} d ago`;
  return new Date(ts * 1000).toLocaleDateString();
}
