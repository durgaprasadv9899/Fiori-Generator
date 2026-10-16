// App-wide toast notifications (animated, stacked bottom-right).
import React, { createContext, useCallback, useContext, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Icon } from "@ui5/webcomponents-react";

type Kind = "success" | "error" | "info";
interface ToastItem { id: number; kind: Kind; text: string }
const Ctx = createContext<(text: string, kind?: Kind) => void>(() => undefined);
let seq = 0;

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const push = useCallback((text: string, kind: Kind = "success") => {
    const id = ++seq;
    setItems((p) => [...p, { id, kind, text }].slice(-4));
    setTimeout(() => setItems((p) => p.filter((t) => t.id !== id)), kind === "error" ? 7000 : 4200);
  }, []);
  const icon = { success: "sys-enter-2", error: "error", info: "message-information" };
  return (
    <Ctx.Provider value={push}>
      {children}
      <div className="ev-toasts" aria-live="polite">
        <AnimatePresence>
          {items.map((t) => (
            <motion.div key={t.id} className={`ev-toast ev-toast--${t.kind}`} layout
              initial={{ opacity: 0, y: 20, scale: 0.96 }} animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, x: 40 }} transition={{ type: "spring", stiffness: 380, damping: 30 }}
              onClick={() => setItems((p) => p.filter((x) => x.id !== t.id))}>
              <Icon name={icon[t.kind]} />
              <span>{t.text}</span>
            </motion.div>
          ))}
        </AnimatePresence>
      </div>
    </Ctx.Provider>
  );
}

export const useToast = () => useContext(Ctx);
