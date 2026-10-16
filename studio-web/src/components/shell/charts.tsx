// Lightweight animated SVG charts (no chart library): bars over time and a donut.
import { motion } from "framer-motion";
import { useState } from "react";

export function BarChart({ data, height = 170 }: { data: { label: string; value: number }[]; height?: number }) {
  const [hover, setHover] = useState<number | null>(null);
  const max = Math.max(1, ...data.map((d) => d.value));
  const w = 100 / Math.max(1, data.length);
  return (
    <div className="ev-bars">
      <svg viewBox={`0 0 100 ${height / 3}`} preserveAspectRatio="none" style={{ height }} role="img" aria-label="Bar chart">
        {[0.25, 0.5, 0.75, 1].map((g) => (
          <line key={g} x1="0" x2="100" y1={(height / 3) * (1 - g)} y2={(height / 3) * (1 - g)} className="ev-bars__grid" />
        ))}
        {data.map((d, i) => {
          const h = ((height / 3) - 2) * (d.value / max);
          return (
            <motion.rect key={d.label} x={i * w + w * 0.18} width={w * 0.64} rx={0.8}
              initial={{ height: 0, y: height / 3 }} animate={{ height: h, y: height / 3 - h }}
              transition={{ duration: 0.6, delay: i * 0.03, ease: "easeOut" }}
              className={"ev-bars__bar" + (hover === i ? " is-hover" : "")}
              onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)} />
          );
        })}
      </svg>
      <div className="ev-bars__axis">
        {data.map((d, i) => (
          <span key={d.label} className={hover === i ? "is-hover" : ""}>
            {hover === i ? `${d.value}` : (i % 2 === 0 || data.length < 8 ? d.label : "")}
          </span>
        ))}
      </div>
    </div>
  );
}

const PALETTE = ["var(--sapChart_OrderedColor_1)", "var(--sapChart_OrderedColor_2)", "var(--sapChart_OrderedColor_3)",
  "var(--sapChart_OrderedColor_4)", "var(--sapChart_OrderedColor_5)"];

export function Donut({ data, size = 150 }: { data: { label: string; value: number }[]; size?: number }) {
  const total = data.reduce((s, d) => s + d.value, 0);
  const r = 15.9155;                       // circumference = 100
  let offset = 25;
  return (
    <div className="ev-donut">
      <svg viewBox="0 0 42 42" width={size} height={size} role="img" aria-label="Donut chart">
        <circle cx="21" cy="21" r={r} fill="none" className="ev-donut__track" strokeWidth="5" />
        {total > 0 && data.map((d, i) => {
          const pct = (d.value / total) * 100;
          const el = (
            <motion.circle key={d.label} cx="21" cy="21" r={r} fill="none" strokeWidth="5" stroke={PALETTE[i % PALETTE.length]}
              strokeDashoffset={offset} initial={{ strokeDasharray: `0 100` }}
              animate={{ strokeDasharray: `${pct} ${100 - pct}` }} transition={{ duration: 0.8, delay: 0.1 + i * 0.12 }} />
          );
          offset -= pct;
          return el;
        })}
        <text x="21" y="20.5" textAnchor="middle" className="ev-donut__num">{total}</text>
        <text x="21" y="25.5" textAnchor="middle" className="ev-donut__lab">apps</text>
      </svg>
      <div className="ev-donut__legend">
        {data.map((d, i) => (
          <div key={d.label}><i style={{ background: PALETTE[i % PALETTE.length] }} />{d.label}<b>{d.value}</b></div>
        ))}
      </div>
    </div>
  );
}

/** Circular progress ring (e.g. validation pass rate). */
export function Ring({ pct, size = 54, tone = "var(--sapPositiveColor)", label }: { pct: number; size?: number; tone?: string; label?: string }) {
  const r = 15.9155;
  return (
    <svg viewBox="0 0 42 42" width={size} height={size} className="ev-ring">
      <circle cx="21" cy="21" r={r} fill="none" strokeWidth="4" className="ev-donut__track" />
      <motion.circle cx="21" cy="21" r={r} fill="none" strokeWidth="4" stroke={tone} strokeLinecap="round"
        strokeDashoffset={25} initial={{ strokeDasharray: "0 100" }}
        animate={{ strokeDasharray: `${Math.max(0, Math.min(100, pct))} ${100 - pct}` }} transition={{ duration: 0.9 }} />
      <text x="21" y="24" textAnchor="middle" className="ev-ring__txt">{label ?? `${Math.round(pct)}%`}</text>
    </svg>
  );
}
