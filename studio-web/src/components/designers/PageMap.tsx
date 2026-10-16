// Page map of a designed app: the pages as cards with their navigation.
import { motion } from "framer-motion";
import { Icon } from "@ui5/webcomponents-react";
import "./designers.css";

export interface MapPage { id: string; type: string; title: string; entity_set?: string }

const ICON: Record<string, string> = {
  dashboard: "business-objects-experience", list: "list", detail: "detail-view", kanban: "card",
  calendar: "calendar", analytics: "bar-chart", wizard: "process", create: "add-document", object: "detail-view",
  report: "table-view", overview: "overview-chart",
};

/** Pages from the API (design.pages) or derived from the blueprint. */
export function pagesOf(design: { pages?: MapPage[]; blueprint?: any } | null): MapPage[] {
  if (!design) return [];
  if (design.pages?.length) return design.pages;
  const bp = design.blueprint || {};
  if (Array.isArray(bp.pages) && bp.pages.length) {
    const out: MapPage[] = [];
    bp.pages.forEach((p: any, i: number) => {
      const es = p.entity_set || p.sub?.main?.entity_set;
      out.push({ id: p.id || `p${i}`, type: p.type || "page", title: p.title || p.id || p.type, entity_set: es });
      if (p.has_detail && p.type !== "detail" && !bp.pages.some((q: any) => q.id === p.detail_id)) {
        out.push({ id: p.detail_id || `${p.id}-detail`, type: "detail", title: `${p.title} — detail`, entity_set: es });
      }
    });
    return out;
  }
  const es = bp.main?.entity_set || "";
  return [
    { id: "list", type: "list", title: bp.app?.title || "List", entity_set: es },
    { id: "detail", type: "detail", title: "Object page", entity_set: es },
  ];
}

export function PageMap({ pages, floorplan }: { pages: MapPage[]; floorplan?: string }) {
  const tops = pages.filter((p) => p.type !== "detail");
  const details = pages.filter((p) => p.type === "detail");
  return (
    <div className="ev-pmap">
      <div className="ev-pmap__app">
        <Icon name="home" /><span>{floorplan === "FreestyleApp" ? "Custom app shell" : floorplan || "App"}</span>
      </div>
      <div className="ev-pmap__row">
        {tops.map((p, i) => (
          <motion.div key={p.id} className="ev-pmap__page" initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.06 }}>
            <span className="ev-pmap__ico"><Icon name={ICON[p.type] || "document"} /></span>
            <div className="ev-pmap__title">{p.title}</div>
            <div className="ev-pmap__meta">{p.type}{p.entity_set ? ` · ${p.entity_set}` : ""}</div>
          </motion.div>
        ))}
      </div>
      {details.length > 0 && (
        <>
          <div className="ev-pmap__arrow"><Icon name="arrow-bottom" /> drill-down</div>
          <div className="ev-pmap__row">
            {details.map((p, i) => (
              <motion.div key={p.id} className="ev-pmap__page ev-pmap__page--detail" initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.2 + i * 0.06 }}>
                <span className="ev-pmap__ico"><Icon name={ICON.detail} /></span>
                <div className="ev-pmap__title">{p.title}</div>
                <div className="ev-pmap__meta">{p.entity_set || "detail"}</div>
              </motion.div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
