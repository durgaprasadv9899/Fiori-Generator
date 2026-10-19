// Navigation bar: pill tabs with a sliding highlight, optionally in groups split by a divider.
import { Fragment } from "react";
import { motion } from "framer-motion";
import { Icon } from "@ui5/webcomponents-react";

export interface TabDef { key: string; text: string; icon?: string; count?: number; group?: string }

export function Tabs({ tabs, value, onChange, id = "tabs" }: { tabs: TabDef[]; value: string; onChange: (k: string) => void; id?: string }) {
  const groups: TabDef[][] = [];
  tabs.forEach((t, i) => {
    if (i === 0 || t.group !== tabs[i - 1].group) groups.push([]);
    groups[groups.length - 1].push(t);
  });
  return (
    <nav className="ev-tabs" role="tablist" aria-label="Sections">
      {groups.map((g, gi) => (
        <Fragment key={gi}>
          {gi > 0 && <span className="ev-tabs__sep" aria-hidden="true" />}
          <div className="ev-tabs__group">
            {g.map((t) => {
              const on = value === t.key;
              return (
                <button key={t.key} role="tab" aria-selected={on} className={"ev-tab" + (on ? " is-active" : "")} onClick={() => onChange(t.key)}>
                  {on && <motion.span layoutId={`${id}-pill`} className="ev-tab__pill" transition={{ type: "spring", stiffness: 500, damping: 40 }} />}
                  {t.icon && <Icon name={t.icon} className="ev-tab__icon" />}
                  <span>{t.text}</span>
                  {t.count !== undefined && <span className="ev-tab__count">{t.count}</span>}
                </button>
              );
            })}
          </div>
        </Fragment>
      ))}
    </nav>
  );
}
