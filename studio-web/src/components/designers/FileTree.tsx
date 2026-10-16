// Collapsible file tree built from flat paths.
import { useMemo, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Icon } from "@ui5/webcomponents-react";
import "./designers.css";

interface Node { name: string; path: string; children: Map<string, Node>; file: boolean }

function build(paths: string[]): Node {
  const root: Node = { name: "", path: "", children: new Map(), file: false };
  paths.forEach((p) => {
    let cur = root;
    p.split("/").forEach((part, i, arr) => {
      const isFile = i === arr.length - 1;
      if (!cur.children.has(part)) cur.children.set(part, { name: part, path: arr.slice(0, i + 1).join("/"), children: new Map(), file: isFile });
      cur = cur.children.get(part)!;
    });
  });
  return root;
}

function fileIcon(name: string): string {
  if (/\.json$/.test(name)) return "syntax";
  if (/\.xml$|\.html$/.test(name)) return "source-code";
  if (/\.js$|\.ts$/.test(name)) return "attachment-text-file";
  if (/\.cds$/.test(name)) return "database";
  if (/\.csv$/.test(name)) return "table-view";
  if (/\.ya?ml$|\.mtaext$/.test(name)) return "settings";
  if (/\.properties$/.test(name)) return "translate";
  return "document";
}

function Branch({ node, depth, selected, onSelect, open, toggle }: {
  node: Node; depth: number; selected: string; onSelect: (p: string) => void; open: Set<string>; toggle: (p: string) => void;
}) {
  const kids = [...node.children.values()].sort((a, b) => (a.file === b.file ? a.name.localeCompare(b.name) : a.file ? 1 : -1));
  return (
    <>
      {kids.map((k) => (
        <div key={k.path}>
          <button
            className={"ev-tree__row" + (k.path === selected ? " is-sel" : "")}
            style={{ paddingLeft: 8 + depth * 14 }}
            onClick={() => (k.file ? onSelect(k.path) : toggle(k.path))}
            title={k.path}
          >
            {!k.file && <Icon name={open.has(k.path) ? "navigation-down-arrow" : "navigation-right-arrow"} className="ev-tree__chev" />}
            <Icon name={k.file ? fileIcon(k.name) : open.has(k.path) ? "open-folder" : "folder-blank"} className={k.file ? "ev-tree__ico" : "ev-tree__ico ev-tree__ico--dir"} />
            <span className="ev-tree__name">{k.name}</span>
          </button>
          <AnimatePresence initial={false}>
            {!k.file && open.has(k.path) && (
              <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }} exit={{ height: 0, opacity: 0 }}
                transition={{ duration: 0.18 }} style={{ overflow: "hidden" }}>
                <Branch node={k} depth={depth + 1} selected={selected} onSelect={onSelect} open={open} toggle={toggle} />
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      ))}
    </>
  );
}

export function FileTree({ paths, selected, onSelect }: { paths: string[]; selected: string; onSelect: (p: string) => void }) {
  const root = useMemo(() => build(paths), [paths]);
  const [open, setOpen] = useState<Set<string>>(() => new Set(["webapp", "backend", "backend/srv", "backend/db", "view", "controller"]));
  const toggle = (p: string) => setOpen((s) => { const n = new Set(s); n.has(p) ? n.delete(p) : n.add(p); return n; });
  return <div className="ev-tree"><Branch node={root} depth={0} selected={selected} onSelect={onSelect} open={open} toggle={toggle} /></div>;
}
