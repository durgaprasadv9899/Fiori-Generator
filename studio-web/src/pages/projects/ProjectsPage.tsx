import { useMe } from "../../app/MeProvider";
// Projects: grid ↔ table, search, mode filter, archived toggle, sort, import.
import React, { useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Button, Icon, Input, MessageStrip, SegmentedButton, SegmentedButtonItem, Select, Option, Switch } from "@ui5/webcomponents-react";
import { EmptyState, PageHeader, Skeleton, StatusBadge, ago } from "../../components/ui";
import { useApi, fmtDate } from "../../components/shell/useApi";
import { MODE_INFO } from "../../components/shell/labels";
import { useToast } from "../../components/shell/Toasts";
import { api } from "../../api/client";
import type { Project } from "../../api/types";
import { Dropdown } from "../../components/shell/Dropdown";
import { BulkDeleteDialog, DeleteProjectDialog, EditProjectDialog } from "../../components/shell/ProjectDialogs";
import { ImportDialog } from "../../components/shell/ImportDialog";
import "./projects.css";

type Sort = "updated" | "name" | "version";

export default function ProjectsPage() {
  const nav = useNavigate();
  const toast = useToast();
  const { can } = useMe();
  const [params, setParams] = useSearchParams();
  const [archived, setArchived] = useState(false);
  const { data, error, loading, reload } = useApi<{ projects: Project[] }>(
    "api/projects" + (archived ? "?include_archived=1" : ""), { deps: [archived] });
  const [q, setQ] = useState("");
  const [mode, setMode] = useState("all");
  const [view, setView] = useState<"grid" | "table">(() => { try { return (localStorage.getItem("evoke.projview") as any) || "grid"; } catch { return "grid"; } });
  const [sort, setSort] = useState<Sort>("updated");
  const fileRef = useRef<HTMLInputElement>(null);
  const [importing, setImporting] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [selecting, setSelecting] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);
  const [editP, setEditP] = useState<Project | null>(null);
  const [delP, setDelP] = useState<Project | null>(null);
  const [bulk, setBulk] = useState(false);
  const toggle = (id: string) => setSelected((s) => s.includes(id) ? s.filter((x) => x !== id) : [...s, id]);
  const archive = async (p: Project, on = !p.archived) => {
    try { await api.post(`api/projects/${p.id}/archive`, { archived: on }); toast(on ? `“${p.name}” archived` : `“${p.name}” restored`); reload(true); }
    catch (e: any) { toast(e.message, "error"); }
  };
  const duplicate = async (p: Project) => {
    try { const np = await api.post<any>(`api/projects/${p.id}/duplicate`, { name: p.name + " (copy)" }); toast("Project duplicated"); nav(`/projects/${np.id}`); }
    catch (e: any) { toast(e.message, "error"); }
  };
  const bulkArchive = async () => {
    for (const id of selected) { const p = (data?.projects || []).find((x) => x.id === id); if (p && !p.archived) await archive(p, true); }
    setSelected([]);
  };
  const actions = (p: Project) => ({ onEdit: () => setEditP(p), onDelete: () => setDelP(p), onArchive: () => archive(p), onDuplicate: () => duplicate(p) });

  useEffect(() => { try { localStorage.setItem("evoke.projview", view); } catch { /* ignore */ } }, [view]);
  useEffect(() => {
    if (params.get("import") === "1") { setImportOpen(true); params.delete("import"); setParams(params, { replace: true }); }
  }, []); // eslint-disable-line

  const rows = useMemo(() => {
    const ql = q.trim().toLowerCase();
    let r = (data?.projects || []).filter((p) => (archived || !p.archived) && (mode === "all" || p.mode === mode) &&
      (!ql || [p.name, p.app_id, p.owner, p.description].some((x) => (x || "").toLowerCase().includes(ql))));
    r = r.slice().sort((a, b) => sort === "name" ? a.name.localeCompare(b.name) :
      sort === "version" ? (b.latest_version || 0) - (a.latest_version || 0) : (b.updated_at || 0) - (a.updated_at || 0));
    return r;
  }, [data, q, mode, archived, sort]);

  const doImport = async (f: File | undefined) => {
    if (!f) return;
    setImporting(true);
    try {
      const fd = new FormData();
      fd.append("file", f);
      const p = await api.post<any>("api/projects/import", fd);
      toast(`Imported “${p.name || f.name}”`);
      nav(`/projects/${p.id}`);
    } catch (e: any) {
      toast(`Import failed: ${e.message}`, "error");
    } finally {
      setImporting(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  const counts = useMemo(() => {
    const c: Record<string, number> = { all: 0, sap: 0, standalone: 0, import: 0 };
    (data?.projects || []).filter((p) => archived || !p.archived).forEach((p) => { c.all++; c[p.mode] = (c[p.mode] || 0) + 1; });
    return c;
  }, [data, archived]);

  return (
    <div>
      <PageHeader kicker="Workspace" title="Projects"
        subtitle="Every generated application is a project with its prompt, design, files, validation results, approvals and deployments."
        actions={<>
          <Button icon="refresh" design="Transparent" tooltip="Refresh" onClick={() => reload()} />
          <Button icon={selecting ? "decline" : "multiselect-all"} design={selecting ? "Attention" : "Default"}
            onClick={() => { setSelecting(!selecting); setSelected([]); }}>{selecting ? "Done" : "Select"}</Button>
          {can("project.create") && <Button icon="upload" onClick={() => setImportOpen(true)}>Import</Button>}
          {can("project.create") && <Button icon="add-document" design="Emphasized" onClick={() => nav("/studio")}>New application</Button>}
          <input ref={fileRef} type="file" accept=".zip" hidden onChange={(e) => doImport(e.target.files?.[0])} />
        </>} />
      <div className="ev-content">
        <motion.div className="ev-toolbar" initial={{ opacity: 0, y: -4 }} animate={{ opacity: 1, y: 0 }}>
          <Input className="ev-toolbar__search" icon={<Icon name="search" />} placeholder="Search by name, app ID, owner…"
            value={q} onInput={(e) => setQ(e.target.value)} showClearIcon />
          <div className="ev-chips">
            {(["all", "standalone", "sap", "import"] as const).map((m) => (
              <button key={m} className={"ev-chip" + (mode === m ? " is-on" : "")} onClick={() => setMode(m)}>
                {m === "all" ? "All" : MODE_INFO[m].label}<span>{counts[m] || 0}</span>
              </button>
            ))}
          </div>
          <div className="ev-toolbar__right">
            <span className="ev-muted ev-small">Archived</span>
            <Switch checked={archived} onChange={(e) => setArchived(e.target.checked)} />
            <Select className="ev-toolbar__sort" onChange={(e) => setSort((e.detail.selectedOption as any).value as Sort)}>
              <Option value="updated" selected={sort === "updated"}>Recently updated</Option>
              <Option value="name" selected={sort === "name"}>Name</Option>
              <Option value="version" selected={sort === "version"}>Most versions</Option>
            </Select>
            <SegmentedButton onSelectionChange={(e) => setView(((e.detail.selectedItems[0] as any)?.dataset.v || "grid") as any)}>
              <SegmentedButtonItem icon="grid" data-v="grid" selected={view === "grid"} tooltip="Cards" />
              <SegmentedButtonItem icon="list" data-v="table" selected={view === "table"} tooltip="Table" />
            </SegmentedButton>
          </div>
        </motion.div>

        {error && <MessageStrip design="Negative" hideCloseButton className="ev-mb">{error}</MessageStrip>}

        {loading && !data ? (
          <div className="ev-pgrid">{Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} height={170} radius={16} />)}</div>
        ) : rows.length === 0 ? (
          <EmptyState icon="folder-blank" title={(data?.projects || []).length ? "No project matches" : "No projects yet"}
            text={(data?.projects || []).length ? "Try another search or filter." : "Create an application — every generation is saved here as a version you can reopen, compare, validate and deploy."}
            action={!(data?.projects || []).length && can("project.create") && <Button design="Emphasized" icon="add-document" onClick={() => nav("/studio")}>Create your first application</Button>} />
        ) : view === "grid" ? (
          <motion.div className="ev-pgrid" layout>
            <AnimatePresence>
              {rows.map((p, i) => <ProjectCard key={p.id} p={p} i={i} selecting={selecting} selected={selected.includes(p.id)}
                onOpen={() => selecting ? toggle(p.id) : nav(`/projects/${p.id}`)} {...actions(p)} />)}
            </AnimatePresence>
          </motion.div>
        ) : (
          <div className="ev-card" style={{ overflow: "auto" }}>
            <table className="ev-dtable">
              <thead><tr>{selecting && <th style={{ width: 36 }} />}<th>Name</th><th>Type</th><th>App ID</th><th>Owner</th><th>Latest</th><th>Updated</th><th style={{ width: 130 }} /></tr></thead>
              <tbody>
                {rows.map((p, i) => {
                  const m = MODE_INFO[p.mode] || MODE_INFO.import;
                  return (
                    <motion.tr key={p.id} className={"is-click" + (selected.includes(p.id) ? " is-sel" : "")} onClick={() => selecting ? toggle(p.id) : nav(`/projects/${p.id}`)}
                      initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: i * 0.02 }}>
                      {selecting && <td><input type="checkbox" checked={selected.includes(p.id)} readOnly /></td>}
                      <td><b>{p.name}</b>{p.archived && <> <StatusBadge>archived</StatusBadge></>}</td>
                      <td><StatusBadge tone={m.tone} icon={m.icon}>{m.label}</StatusBadge></td>
                      <td className="ev-mono">{p.app_id}</td>
                      <td>{p.owner}</td>
                      <td>v{p.latest_version || 0}</td>
                      <td title={fmtDate(p.updated_at)}>{ago(p.updated_at)}</td>
                      <td onClick={(e) => e.stopPropagation()} style={{ whiteSpace: "nowrap" }}>
                        {(p as any).can_edit !== false && <Button design="Transparent" icon="edit" tooltip="Edit details" onClick={() => setEditP(p)} />}
                        {(p as any).can_edit !== false && <Button design="Transparent" icon="inbox" tooltip={p.archived ? "Restore" : "Archive"} onClick={() => archive(p)} />}
                        {(p as any).can_delete && <Button design="Transparent" icon="delete" tooltip="Delete" onClick={() => setDelP(p)} />}
                      </td>
                    </motion.tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        <AnimatePresence>
          {selecting && (
            <motion.div className="ev-selbar" initial={{ opacity: 0, y: 24 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 24 }}>
              <b>{selected.length}</b><span className="ev-muted">selected</span>
              <Button design="Transparent" onClick={() => setSelected(selected.length === rows.length ? [] : rows.map((p) => p.id))}>
                {selected.length === rows.length && rows.length ? "Clear" : "Select all"}</Button>
              <Button icon="inbox" disabled={!selected.length} onClick={bulkArchive}>Archive</Button>
              <Button icon="delete" design="Negative" disabled={!selected.length} onClick={() => setBulk(true)}>Delete</Button>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
      {editP && <EditProjectDialog project={editP} open={!!editP} onClose={() => setEditP(null)} onSaved={() => reload(true)} />}
      {delP && <DeleteProjectDialog project={delP} open={!!delP} onClose={() => setDelP(null)} onDeleted={() => reload(true)} />}
      <ImportDialog open={importOpen} onClose={() => { setImportOpen(false); reload(true); }} />
      <BulkDeleteDialog open={bulk} onClose={() => setBulk(false)} onDone={() => { setSelected([]); reload(true); }}
        projects={(data?.projects || []).filter((p) => selected.includes(p.id))} />
    </div>
  );
}

function ProjectCard({ p, i, onOpen, selecting, selected, onEdit, onDelete, onArchive, onDuplicate }: {
  p: Project; i: number; onOpen: () => void; selecting: boolean; selected: boolean;
  onEdit: () => void; onDelete: () => void; onArchive: () => void; onDuplicate: () => void;
}) {
  const [menu, setMenu] = useState(false);
  const pick = (f: () => void) => (e: React.MouseEvent) => { e.stopPropagation(); setMenu(false); f(); };
  const m = MODE_INFO[p.mode] || MODE_INFO.import;
  const initials = p.name.split(/\s+/).slice(0, 2).map((w) => w[0]).join("").toUpperCase();
  return (
    <motion.div className={"ev-pcard" + (selected ? " is-selected" : "")} layout onClick={onOpen}
      initial={{ opacity: 0, y: 14, scale: 0.98 }} animate={{ opacity: 1, y: 0, scale: 1 }} exit={{ opacity: 0, scale: 0.96 }}
      transition={{ duration: 0.25, delay: Math.min(i, 12) * 0.035 }} whileHover={{ y: -4 }}>
      <div className={`ev-pcard__band mode-${p.mode}`} />
      {selecting ? (
        <input className="ev-pcard__check" type="checkbox" checked={selected} readOnly />
      ) : (
        <div className="ev-pcard__menu" onClick={(e) => e.stopPropagation()}>
          <Dropdown open={menu} onClose={() => setMenu(false)} width={210}
            anchor={<button className="ev-kebab" title="Actions" onClick={() => setMenu(!menu)}><Icon name="overflow" /></button>}>
            <div className="ev-menu">
              <button onClick={pick(onOpen)}><Icon name="action" />Open</button>
              {(p as any).can_edit !== false && <button onClick={pick(onEdit)}><Icon name="edit" />Edit details</button>}
              <button onClick={pick(onDuplicate)}><Icon name="duplicate" />Duplicate</button>
              {(p as any).can_edit !== false && <button onClick={pick(onArchive)}><Icon name="inbox" />{p.archived ? "Restore" : "Archive"}</button>}
              {(p as any).can_delete && <><hr /><button className="is-danger" onClick={pick(onDelete)}><Icon name="delete" />Delete…</button></>}
            </div>
          </Dropdown>
        </div>
      )}
      <div className="ev-pcard__top">
        <div className={`ev-pcard__logo mode-${p.mode}`}>{initials}</div>
        <div className="ev-pcard__titles">
          <div className="ev-pcard__name">{p.name}</div>
          <div className="ev-pcard__id">{p.app_id || "—"}</div>
        </div>
      </div>
      <div className="ev-pcard__desc">{p.description || "No description."}</div>
      <div className="ev-pcard__foot">
        <StatusBadge tone={m.tone} icon={m.icon}>{m.label}</StatusBadge>
        {p.archived && <StatusBadge>archived</StatusBadge>}
        <span className="ev-pcard__ver"><Icon name="save" />v{p.latest_version || 0}</span>
        <span className="ev-pcard__upd" title={fmtDate(p.updated_at)}>{ago(p.updated_at)}</span>
      </div>
      <div className="ev-pcard__owner"><span className="ev-mini-av">{(p.owner || "?")[0].toUpperCase()}</span>{p.owner}</div>
    </motion.div>
  );
}
