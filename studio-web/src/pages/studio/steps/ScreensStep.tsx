// Screens: AI screen design (floorplan, coverage, page map) + refine-by-chat.
import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { useNavigate } from "react-router-dom";
import { Button, Icon, Input, MessageStrip } from "@ui5/webcomponents-react";
import { api } from "../../../api/client";
import { useMe } from "../../../app/MeProvider";
import { Ring } from "../../../components/designers/Ring";
import { PageMap, pagesOf } from "../../../components/designers/PageMap";
import { StatusBadge } from "../../../components/ui";
import { draftStore, sourceOf, useDraft, type DesignResult } from "../draft";
import { useStudioJobs } from "../flow";
import { JobError, JobProgress, StepIntro } from "./common";

const FP_ICON: Record<string, string> = {
  "": "lightbulb", ListReportObjectPage: "table-view", Worklist: "activity-items", AnalyticalListPage: "bar-chart",
  OverviewPage: "overview-chart", FreestyleListDetail: "detail-view", FreestyleMasterDetail: "screen-split-two",
  FreestyleWorklist: "activity-2", FreestyleApp: "business-objects-experience",
};
const FP_FALLBACK: Record<string, [string, string]> = {
  ListReportObjectPage: ["fe", "Fiori Elements List Report + Object Page"],
  Worklist: ["fe", "Fiori Elements Worklist"],
  AnalyticalListPage: ["fe", "Analytical List Page (chart + table)"],
  OverviewPage: ["ovp", "Overview Page (cards dashboard)"],
  FreestyleApp: ["freestyle", "Custom multi-page app: dashboard, lists, boards, calendar, wizard"],
  FreestyleMasterDetail: ["freestyle", "Master-detail (flexible column layout)"],
};

export function ScreensStep() {
  const d = useDraft();
  const nav = useNavigate();
  const { me } = useMe();
  const { start, jobOf, busy } = useStudioJobs();
  const [fps, setFps] = useState<Record<string, [string, string]>>(FP_FALLBACK);
  const [filter, setFilter] = useState<"all" | "covered" | "partial" | "missing">("all");

  useEffect(() => { api.get<any>("api/floorplans").then((r) => r.floorplans && setFps(r.floorplans)).catch(() => undefined); }, []);

  const design = () => start("design", "api/jobs/design",
    { requirement: d.requirement, source: sourceOf(d), floorplan: d.floorplan },
    (r) => draftStore.update({ design: r, chat: [], generated: null, validation: null }), "Screens designed");

  const job = jobOf("design");
  const res = d.design;
  const cov = res?.coverage || [];
  const counts = { covered: cov.filter((c) => c.status === "covered").length, partial: cov.filter((c) => c.status === "partial").length, missing: cov.filter((c) => c.status === "missing").length };
  const shown = filter === "all" ? cov : cov.filter((c) => c.status === filter);
  const pages = pagesOf(res);

  return (
    <div className="ev-step-page">
      <StepIntro icon="screen-split-three" title="Design the screens"
        text="Choose a page style or let the AI decide. The AI designs the pages, columns, filters, actions and calculations, checks the design against your data and reports how well each requirement is covered." />

      <div className="ev-section-title"><Icon name="grid" /> Page style</div>
      <div className="ev-fp-grid">
        {[["", ["auto", "Let the AI choose the best layout for the requirement"]] as [string, [string, string]], ...Object.entries(fps)].map(([k, [fam, desc]], i) => (
          <motion.button key={k || "auto"} className={"ev-fp" + (d.floorplan === k ? " is-on" : "")} aria-pressed={d.floorplan === k}
            onClick={() => draftStore.update({ floorplan: k })} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}
            transition={{ delay: i * 0.03 }} whileHover={{ y: -3 }}>
            <span className="ev-fp__icon"><Icon name={FP_ICON[k] || "web-cam"} /></span>
            <span className="ev-fp__name">{k ? k.replace(/([a-z])([A-Z])/g, "$1 $2") : "Let AI decide"}</span>
            <span className="ev-fp__desc">{desc}</span>
            {fam !== "auto" && <span className="ev-fp__fam">{fam === "fe" ? "Fiori Elements" : fam === "ovp" ? "Overview" : "Freestyle"}</span>}
            {k === "" && <span className="ev-fp__fam ev-fp__fam--rec">Recommended</span>}
          </motion.button>
        ))}
      </div>

      <div className="ev-actions">
        <Button design="Emphasized" icon="ai" disabled={busy("design")} onClick={design}>
          {busy("design") ? "Designing…" : res ? "Design again" : "Design screens"}
        </Button>
        {res && <span className="ev-muted">Designed {res.source === "ai" ? `by ${me?.ai.model || "AI"}` : res.source === "saved" ? "earlier (saved version)" : "with rules"} · {pages.length} pages</span>}
      </div>
      <JobProgress job={job} title="The AI is designing your screens" />
      <JobError kind="design" />

      {!res && !busy("design") && (
        <div className="ev-placeholder"><Icon name="screen-split-three" /><span>No screens designed yet — choose a page style and start the design.</span></div>
      )}

      <AnimatePresence>
        {res && (
          <motion.div key="res" initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}>
            <div className="ev-design-grid">
              <div className="ev-panel">
                <div className="ev-panel__title"><Icon name="target-group" /> Requirement coverage</div>
                <div className="ev-coverage">
                  <Ring value={res.coverage_score} sub={cov.length ? `${counts.covered}/${cov.length} covered` : "not analysed"} />
                  <div className="ev-coverage__legend">
                    <button className={"ev-covf" + (filter === "all" ? " is-on" : "")} onClick={() => setFilter("all")}>All <b>{cov.length}</b></button>
                    <button className={"ev-covf ev-covf--good" + (filter === "covered" ? " is-on" : "")} onClick={() => setFilter("covered")}>Covered <b>{counts.covered}</b></button>
                    <button className={"ev-covf ev-covf--warn" + (filter === "partial" ? " is-on" : "")} onClick={() => setFilter("partial")}>Partial <b>{counts.partial}</b></button>
                    <button className={"ev-covf ev-covf--bad" + (filter === "missing" ? " is-on" : "")} onClick={() => setFilter("missing")}>Missing <b>{counts.missing}</b></button>
                  </div>
                </div>
                {cov.length === 0 ? (
                  <div className="ev-muted ev-pad">Coverage is analysed when an AI model is available.</div>
                ) : (
                  <div className="ev-covlist">
                    <AnimatePresence initial={false}>
                      {shown.map((c, i) => (
                        <motion.div key={c.feature + i} className="ev-covitem" layout initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
                          <StatusBadge tone={c.status === "covered" ? "good" : c.status === "partial" ? "warn" : "bad"}
                            icon={c.status === "covered" ? "accept" : c.status === "partial" ? "less" : "decline"}>{c.status}</StatusBadge>
                          <div><div className="ev-covitem__f">{c.feature}</div>{c.detail && <div className="ev-muted">{c.detail}</div>}</div>
                        </motion.div>
                      ))}
                    </AnimatePresence>
                  </div>
                )}
              </div>
              <div className="ev-panel">
                <div className="ev-panel__title"><Icon name="tree" /> Page map · {res.blueprint?.floorplan}</div>
                <PageMap pages={pages} floorplan={res.blueprint?.floorplan} />
                {res.blueprint?.reasoning && <div className="ev-reason"><Icon name="lightbulb" />{String(res.blueprint.reasoning).slice(0, 400)}</div>}
              </div>
            </div>

            <div className="ev-design-grid ev-design-grid--b">
              <RefineChat />
              <div className="ev-panel">
                <div className="ev-panel__title"><Icon name="list" /> Design details</div>
                {res.steps?.length > 0 && (
                  <div className="ev-steps-mini">
                    {res.steps.map((s, i) => (
                      <span key={i} className={"ev-steps-mini__i" + (s.ok ? "" : " is-bad")}><Icon name={s.ok ? "accept" : "alert"} />{s.step} · {Math.round(s.seconds)}s</span>
                    ))}
                  </div>
                )}
                {res.notes?.length > 0 && (
                  <details className="ev-details"><summary>Corrections while validating against the data ({res.notes.length})</summary>
                    <ul>{res.notes.map((n, i) => <li key={i}>{n}</li>)}</ul></details>
                )}
                {res.structure && <details className="ev-details"><summary>Structure in detail</summary><pre className="ev-pre">{res.structure}</pre></details>}
                {res.spec?.gaps?.length > 0 && (
                  <MessageStrip design="Critical" hideCloseButton className="ev-gap-top">
                    Not possible with this data: {res.spec.gaps.map((g: any) => g.text).join("; ")}
                  </MessageStrip>
                )}
                <div className="ev-cta-row">
                  <Button design="Emphasized" icon="play" onClick={() => nav("/studio/tryit")}>Try the app</Button>
                </div>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

/** Chat to change the design ("add a calendar", "show margin as a column"…). */
function RefineChat() {
  const d = useDraft();
  const { me } = useMe();
  const { start, jobOf, busy } = useStudioJobs();
  const [text, setText] = useState("");
  const endRef = useRef<HTMLDivElement>(null);
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" }); }, [d.chat.length, busy("refine")]);

  const send = () => {
    const instr = text.trim();
    if (!instr || !d.design) return;
    const before = d.design;
    draftStore.update((x) => ({ chat: [...x.chat, { role: "user", text: instr, at: Date.now() }] }));
    setText("");
    start("refine", "api/jobs/refine",
      { instruction: instr, requirement: d.requirement, blueprint: d.design.blueprint, source: sourceOf(d), spec: d.design.spec },
      (r: DesignResult) => {
        const summary = describeChange(before, r);
        draftStore.update((x) => ({
          design: { ...r, coverage: r.coverage?.length ? r.coverage : x.design?.coverage || [], coverage_score: r.coverage_score ?? x.design?.coverage_score ?? null },
          chat: [...x.chat, { role: "ai", text: summary, at: Date.now() }], generated: null, validation: null,
        }));
      });
  };
  const err = d.errors.refine;
  useEffect(() => {
    if (err) { draftStore.update((x) => ({ chat: [...x.chat, { role: "ai", text: `I could not apply that: ${err}`, at: Date.now(), error: true }] })); draftStore.clearError("refine"); }
  }, [err]);

  const suggestions = ["Add a calendar of due dates", "Highlight overdue records in red", "Add German translations", "Add a dashboard with KPIs"];
  return (
    <div className="ev-panel ev-chat">
      <div className="ev-panel__title"><Icon name="discussion-2" /> Refine with AI</div>
      <div className="ev-chat__log">
        {d.chat.length === 0 && (
          <div className="ev-chat__empty">
            <span className="ev-muted">Describe a change in plain language — the design is updated and checked again.</span>
            <div className="ev-chips">{suggestions.map((s) => <button key={s} className="ev-chip" onClick={() => setText(s)} disabled={!me?.ai.available}>{s}</button>)}</div>
          </div>
        )}
        {d.chat.map((m, i) => (
          <motion.div key={i} className={`ev-bubble ev-bubble--${m.role}` + (m.error ? " is-err" : "")} initial={{ opacity: 0, y: 8, scale: 0.98 }} animate={{ opacity: 1, y: 0, scale: 1 }}>
            {m.role === "ai" && <span className="ev-bubble__ava"><Icon name="ai" /></span>}
            <div className="ev-bubble__text">{m.text}</div>
          </motion.div>
        ))}
        {busy("refine") && (
          <motion.div className="ev-bubble ev-bubble--ai" initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
            <span className="ev-bubble__ava"><Icon name="ai" /></span>
            <div className="ev-typing"><span /><span /><span /></div>
            <span className="ev-muted ev-typing__msg">{jobOf("refine")?.messages.slice(-1)[0] || "Applying the change…"}</span>
          </motion.div>
        )}
        <div ref={endRef} />
      </div>
      <div className="ev-chat__input">
        <Input value={text} placeholder={me?.ai.available ? "e.g. make the board draggable · add the margin as a column" : "Refinement needs an AI model"}
          disabled={!me?.ai.available || busy("refine")} onInput={(e) => setText((e.target as any).value)}
          onKeyDown={(e: any) => { if (e.key === "Enter") send(); }} />
        <Button design="Emphasized" icon="paper-plane" disabled={!text.trim() || busy("refine") || !me?.ai.available} onClick={send} tooltip="Apply" />
      </div>
    </div>
  );
}

function describeChange(a: DesignResult, b: DesignResult): string {
  const pa = pagesOf(a).map((p) => p.title), pb = pagesOf(b).map((p) => p.title);
  const added = pb.filter((x) => !pa.includes(x)), removed = pa.filter((x) => !pb.includes(x));
  const parts: string[] = [];
  if (a.blueprint?.floorplan !== b.blueprint?.floorplan) parts.push(`layout is now ${b.blueprint?.floorplan}`);
  if (added.length) parts.push(`added ${added.join(", ")}`);
  if (removed.length) parts.push(`removed ${removed.join(", ")}`);
  const la = a.blueprint?.app?.languages || [], lb = b.blueprint?.app?.languages || [];
  if (JSON.stringify(la) !== JSON.stringify(lb)) parts.push(`languages: ${lb.join(", ")}`);
  if (b.coverage_score != null && b.coverage_score !== a.coverage_score) parts.push(`coverage ${b.coverage_score}%`);
  return parts.length ? `Done — ${parts.join(" · ")}.` : "Done — the design was updated (fields, columns, logic or layout).";
}
