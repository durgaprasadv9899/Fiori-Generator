// Command palette (Ctrl/Cmd+K): fuzzy search over pages, actions and projects, keyboard driven.
import React, { useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { useNavigate } from "react-router-dom";
import { Icon } from "@ui5/webcomponents-react";
import { api } from "../../api/client";
import type { Project } from "../../api/types";
import { useMe } from "../../app/MeProvider";

interface Cmd { id: string; group: string; title: string; hint?: string; icon: string; run: () => void; keywords?: string }

/** Subsequence fuzzy score (higher = better), -1 = no match. */
function score(q: string, text: string): number {
  if (!q) return 0;
  const t = text.toLowerCase();
  const i = t.indexOf(q);
  if (i >= 0) return 100 - i;
  let pos = 0, s = 0;
  for (const ch of q) {
    const k = t.indexOf(ch, pos);
    if (k < 0) return -1;
    s += k === pos ? 3 : 1;
    pos = k + 1;
  }
  return s;
}

export function CommandPalette({ open, onClose }: { open: boolean; onClose: () => void }) {
  const nav = useNavigate();
  const { toggleDark, dark } = useMe();
  const [q, setQ] = useState("");
  const [sel, setSel] = useState(0);
  const [projects, setProjects] = useState<Project[]>([]);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open) return;
    setQ(""); setSel(0);
    setTimeout(() => input.current?.focus(), 30);
    api.get<{ projects: Project[] }>("api/projects").then((r) => setProjects(r.projects || [])).catch(() => undefined);
  }, [open]);

  const go = (path: string) => () => { nav(path); onClose(); };
  const cmds: Cmd[] = useMemo(() => [
    { id: "n-dash", group: "Navigate", title: "Dashboard", icon: "home", run: go("/") },
    { id: "n-studio", group: "Navigate", title: "New application", hint: "AI data model + CAP backend + UI", icon: "add-document", run: go("/studio"), keywords: "create build generate" },
    { id: "n-sap", group: "Navigate", title: "Connect an SAP service", hint: "Build on an existing OData service", icon: "connected", run: go("/studio?mode=sap"), keywords: "odata destination s4" },
    { id: "n-proj", group: "Navigate", title: "Projects", icon: "folder-blank", run: go("/projects") },
    { id: "n-appr", group: "Navigate", title: "Approvals", icon: "approvals", run: go("/approvals"), keywords: "release review" },
    { id: "n-audit", group: "Navigate", title: "Audit log", icon: "history", run: go("/audit") },
    { id: "n-set", group: "Navigate", title: "Settings", icon: "settings", run: go("/settings") },
    { id: "a-theme", group: "Actions", title: dark ? "Switch to light theme" : "Switch to dark theme", icon: dark ? "light-mode" : "dark-mode", run: () => { toggleDark(); onClose(); }, keywords: "theme dark light mode" },
    { id: "a-import", group: "Actions", title: "Import a project (.zip)", icon: "upload", run: go("/projects?import=1") },
    ...projects.slice(0, 50).map((p) => ({
      id: "p-" + p.id, group: "Projects", title: p.name, hint: `${p.app_id || p.mode} · v${p.latest_version || 0}`,
      icon: p.mode === "sap" ? "connected" : "database", run: go(`/projects/${p.id}`), keywords: p.app_id,
    })),
  ], [projects, dark]);  // eslint-disable-line

  const ql = q.trim().toLowerCase();
  const results = cmds
    .map((c) => ({ c, s: Math.max(score(ql, c.title), score(ql, c.keywords || "") - 5, score(ql, c.hint || "") - 10) }))
    .filter((x) => x.s >= 0)
    .sort((a, b) => (ql ? b.s - a.s : 0))
    .slice(0, 12)
    .map((x) => x.c);

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setSel((s) => Math.min(results.length - 1, s + 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setSel((s) => Math.max(0, s - 1)); }
    else if (e.key === "Enter") { e.preventDefault(); results[sel]?.run(); }
    else if (e.key === "Escape") onClose();
  };

  let lastGroup = "";
  return (
    <AnimatePresence>
      {open && (
        <motion.div className="ev-cmdk-overlay" onMouseDown={onClose}
          initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.15 }}>
          <motion.div className="ev-cmdk" onMouseDown={(e) => e.stopPropagation()}
            initial={{ opacity: 0, y: -16, scale: 0.97 }} animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -10, scale: 0.98 }} transition={{ type: "spring", stiffness: 420, damping: 32 }}>
            <div className="ev-cmdk__search">
              <Icon name="search" />
              <input ref={input} value={q} placeholder="Search pages, actions and projects…"
                onChange={(e) => { setQ(e.target.value); setSel(0); }} onKeyDown={onKey} />
              <kbd>Esc</kbd>
            </div>
            <div className="ev-cmdk__list">
              {results.length === 0 && <div className="ev-cmdk__none">No matches for “{q}”.</div>}
              {results.map((c, i) => {
                const head = c.group !== lastGroup ? (lastGroup = c.group) : "";
                return (
                  <React.Fragment key={c.id}>
                    {head && <div className="ev-cmdk__group">{head}</div>}
                    <div className={"ev-cmdk__item" + (i === sel ? " is-sel" : "")} onMouseEnter={() => setSel(i)} onClick={c.run}>
                      <span className="ev-cmdk__icon"><Icon name={c.icon} /></span>
                      <span className="ev-cmdk__title">{c.title}</span>
                      {c.hint && <span className="ev-cmdk__hint">{c.hint}</span>}
                      {i === sel && <motion.span layoutId="cmdk-enter" className="ev-cmdk__enter">↵</motion.span>}
                    </div>
                  </React.Fragment>
                );
              })}
            </div>
            <div className="ev-cmdk__foot"><span><kbd>↑</kbd><kbd>↓</kbd> navigate</span><span><kbd>↵</kbd> open</span><span><kbd>Ctrl</kbd><kbd>K</kbd> toggle</span></div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
