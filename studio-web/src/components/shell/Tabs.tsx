// Tab strip with a sliding underline.
import { motion } from "framer-motion";
import { Icon } from "@ui5/webcomponents-react";

export interface TabDef { key: string; text: string; icon?: string; count?: number }

export function Tabs({ tabs, value, onChange, id = "tabs" }: { tabs: TabDef[]; value: string; onChange: (k: string) => void; id?: string }) {
  return (
    <div className="ev-tabs" role="tablist">
      {tabs.map((t) => (
        <button key={t.key} role="tab" aria-selected={value === t.key} className={"ev-tab" + (value === t.key ? " is-active" : "")} onClick={() => onChange(t.key)}>
          {t.icon && <Icon name={t.icon} style={{ width: ".95rem", height: ".95rem", color: "inherit" }} />}
          {t.text}
          {t.count !== undefined && <span className="ev-tab__count">{t.count}</span>}
          {value === t.key && <motion.span layoutId={`${id}-line`} className="ev-tab__line" transition={{ type: "spring", stiffness: 500, damping: 40 }} />}
        </button>
      ))}
    </div>
  );
}
