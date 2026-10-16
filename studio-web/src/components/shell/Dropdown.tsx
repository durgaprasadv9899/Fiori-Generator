// Animated anchored panel (closes on outside click / Escape) used by the top-bar menus.
import React, { useEffect, useRef } from "react";
import { AnimatePresence, motion } from "framer-motion";

export function Dropdown(props: {
  open: boolean; onClose: () => void; anchor: React.ReactNode; children: React.ReactNode; width?: number; align?: "right" | "left";
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!props.open) return;
    const onDown = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) props.onClose(); };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") props.onClose(); };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("mousedown", onDown); document.removeEventListener("keydown", onKey); };
  }, [props.open, props.onClose]);
  return (
    <div className="ev-dd" ref={ref}>
      {props.anchor}
      <AnimatePresence>
        {props.open && (
          <motion.div className={"ev-dd__panel" + (props.align === "left" ? " is-left" : "")} style={{ width: props.width || 340 }}
            initial={{ opacity: 0, y: -6, scale: 0.97 }} animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -6, scale: 0.97 }} transition={{ duration: 0.16 }}>
            {props.children}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
