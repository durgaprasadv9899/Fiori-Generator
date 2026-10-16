// Animated donut ring (coverage score, passed checks…).
import { motion } from "framer-motion";
import "./designers.css";

export function Ring({ value, size = 120, stroke = 11, label, sub, tone }: {
  value: number | null; size?: number; stroke?: number; label?: string; sub?: string; tone?: "good" | "warn" | "bad" | "brand";
}) {
  const v = Math.max(0, Math.min(100, value ?? 0));
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const t = tone || (value == null ? "brand" : v >= 85 ? "good" : v >= 60 ? "warn" : "bad");
  return (
    <div className={`ev-ring ev-ring--${t}`} style={{ width: size, height: size }}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
        <circle cx={size / 2} cy={size / 2} r={r} className="ev-ring__track" strokeWidth={stroke} fill="none" />
        <motion.circle
          cx={size / 2} cy={size / 2} r={r} className="ev-ring__bar" strokeWidth={stroke} fill="none" strokeLinecap="round"
          strokeDasharray={c} initial={{ strokeDashoffset: c }} animate={{ strokeDashoffset: c - (c * v) / 100 }}
          transition={{ duration: 1.1, ease: "easeOut" }} transform={`rotate(-90 ${size / 2} ${size / 2})`}
        />
      </svg>
      <div className="ev-ring__center">
        <div className="ev-ring__value">{value == null ? "—" : `${Math.round(v)}${label ?? "%"}`}</div>
        {sub && <div className="ev-ring__sub">{sub}</div>}
      </div>
    </div>
  );
}
