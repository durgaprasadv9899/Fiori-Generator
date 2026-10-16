// Studio: guided creation of an application — new app (data model + CAP backend + UI) or an app
// on an existing SAP OData service. Steps are routes (/studio/:step); the draft lives in draft.tsx.
import React, { useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { Button, Dialog, Bar, Icon } from "@ui5/webcomponents-react";
import { api } from "../../api/client";
import { draftStore, draftTitle, toastBus, useDraft, useToasts, type Mode } from "./draft";
import { reachable, stepsFor } from "./flow";
import { Stepper } from "./Stepper";
import { ModeChooser } from "./ModeChooser";
import { ConnectStep } from "./steps/ConnectStep";
import { DescribeStep } from "./steps/DescribeStep";
import { DataModelStep } from "./steps/DataModelStep";
import { ScreensStep } from "./steps/ScreensStep";
import { TryItStep } from "./steps/TryItStep";
import { GenerateStep } from "./steps/GenerateStep";
import { DeployStep } from "./steps/DeployStep";
import "./studio.css";

const STEP_COMPONENT: Record<string, () => React.ReactElement | null> = {
  connect: ConnectStep, describe: DescribeStep, model: DataModelStep, screens: ScreensStep,
  tryit: TryItStep, generate: GenerateStep, deploy: DeployStep,
};

export default function StudioPage() {
  const draft = useDraft();
  const { step: routeStep } = useParams();
  const [search, setSearch] = useSearchParams();
  const nav = useNavigate();
  const [confirmReset, setConfirmReset] = useState(false);
  const [loadingVersion, setLoadingVersion] = useState(false);
  const [pendingMode, setPendingMode] = useState<Mode | null>(null);
  const steps = stepsFor(draft.mode);
  const reach = reachable(draft);
  const dirRef = useRef(1);

  // ?version=<vid> → load a saved version into the draft; ?mode=… → preselect a mode
  useEffect(() => {
    const vid = search.get("version");
    const mode = search.get("mode") as Mode | null;
    if (vid) {
      setLoadingVersion(true);
      loadVersion(vid).then((ok) => {
        setLoadingVersion(false);
        setSearch({}, { replace: true });
        if (ok) nav("/studio/screens", { replace: true });
      });
    } else if (mode === "sap" || mode === "standalone") {
      if (draft.mode && draft.mode !== mode && draft.updatedAt) {
        setPendingMode(mode);
        setConfirmReset(true);                    // switching mode with an existing draft → ask first
        setSearch({}, { replace: true });
      } else {
        if (draft.mode !== mode) draftStore.reset(mode);
        nav(`/studio/${draft.mode === mode && draft.step ? draft.step : stepsFor(mode)[0].key}`, { replace: true });
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // current step: route → draft.step → first step; never beyond what is reachable
  const current = useMemo(() => {
    const keys = steps.map((s) => s.key);
    let k = routeStep && keys.includes(routeStep) ? routeStep : draft.step && keys.includes(draft.step) ? draft.step : keys[0];
    if (keys.indexOf(k) > reach) k = keys[reach];
    return k;
  }, [routeStep, draft.step, steps, reach]);
  const idx = steps.findIndex((s) => s.key === current);

  useEffect(() => {
    if (draft.mode && draft.step !== current) draftStore.update({ step: current });
  }, [current, draft.mode, draft.step]);

  const go = (key: string) => {
    const to = steps.findIndex((s) => s.key === key);
    dirRef.current = to >= idx ? 1 : -1;
    nav(`/studio/${key}`);
  };

  if (loadingVersion) {
    return <div className="ev-studio"><div className="ev-st-loading"><Icon name="download" /> Opening the saved version…</div></div>;
  }

  if (!draft.mode) {
    return (
      <div className="ev-studio">
        <ModeChooser onPick={(m) => { draftStore.reset(m); nav(`/studio/${stepsFor(m)[0].key}`); }} />
        <Toasts />
      </div>
    );
  }

  const Step = STEP_COMPONENT[current] || DescribeStep;
  const title = draftTitle(draft);
  return (
    <div className="ev-studio">
      <div className="ev-st-head">
        <div className="ev-st-head__text">
          <div className="ev-st-head__kicker">
            <span className={`ev-mode-pill ev-mode-pill--${draft.mode}`}>
              <Icon name={draft.mode === "sap" ? "connected" : "add-product"} />
              {draft.mode === "sap" ? "SAP service app" : "New application"}
            </span>
            {draft.generated && <span className="ev-st-head__ver">Project version v{draft.generated.version_number}</span>}
          </div>
          <h1 className="ev-st-head__title">{title}</h1>
        </div>
        <div className="ev-st-head__actions">
          <Button design="Transparent" icon="reset" onClick={() => setConfirmReset(true)}>Start over</Button>
        </div>
      </div>
      <Stepper steps={steps} current={idx} reachable={reach} onGo={go} />
      <div className="ev-st-body">
        <AnimatePresence mode="wait" custom={dirRef.current}>
          <motion.div
            key={current}
            custom={dirRef.current}
            initial={{ opacity: 0, x: 28 * dirRef.current }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: -20 * dirRef.current }}
            transition={{ duration: 0.24, ease: "easeOut" }}
          >
            <Step />
          </motion.div>
        </AnimatePresence>
      </div>
      <div className="ev-st-foot">
        <Button design="Transparent" icon="nav-back" disabled={idx === 0} onClick={() => go(steps[idx - 1].key)}>
          {idx > 0 ? steps[idx - 1].label : "Back"}
        </Button>
        <div className="ev-st-foot__dots">
          {steps.map((s, i) => <span key={s.key} className={"ev-st-dot" + (i === idx ? " is-cur" : i <= reach ? " is-done" : "")} />)}
        </div>
        <Button design="Emphasized" endIcon="navigation-right-arrow" disabled={idx >= steps.length - 1 || idx + 1 > reach}
          onClick={() => go(steps[idx + 1].key)}>
          {idx < steps.length - 1 ? steps[idx + 1].label : "Done"}
        </Button>
      </div>

      <Dialog open={confirmReset} headerText="Start over?" onClose={() => setConfirmReset(false)}
        footer={<Bar endContent={<>
          <Button design="Negative" onClick={() => {
            draftStore.reset(pendingMode); setConfirmReset(false);
            nav(pendingMode ? `/studio/${stepsFor(pendingMode)[0].key}` : "/studio");
            setPendingMode(null);
            toastBus.push("Draft discarded", "info");
          }}>Discard draft</Button>
          <Button design="Transparent" onClick={() => { setConfirmReset(false); setPendingMode(null); }}>Keep working</Button>
        </>} />}>
        <div className="ev-dialog-text">
          The current draft <b>{title}</b> will be discarded. Generated versions stay in the project workspace.
        </div>
      </Dialog>
      <Toasts />
    </div>
  );
}

/** Load a saved project version (its source, design and generation) into the draft. */
async function loadVersion(vid: string): Promise<boolean> {
  try {
    const v = await api.get<any>(`api/versions/${vid}`);
    const s = v.snapshot || {};
    const src = s.source || (s.domain ? { mode: "standalone", domain: s.domain, db: s.db || "sqlite" } : null);
    if (!src || !s.blueprint) {
      toastBus.push("This version has no design that can be opened here.", "bad");
      return false;
    }
    const mode: Mode = src.mode === "sap" ? "sap" : "standalone";
    draftStore.reset(mode);
    draftStore.update({
      requirement: s.requirement || "",
      domain: mode === "standalone" ? src.domain : null,
      db: src.db || "sqlite",
      services: mode === "sap" ? (src.services || []) : [],
      destination: src.destination || draftStore.get().destination,
      design: {
        blueprint: s.blueprint, notes: [], source: "saved", spec: s.design?.spec || null, coverage: s.design?.coverage || [],
        coverage_score: null, steps: [], service_url: "", structure: "",
      },
      appId: s.app_id || "",
      generated: {
        project_id: v.version.project_id, version_id: v.version.id, version_number: v.version.number,
        file_count: v.files?.length || 0, files: v.files || [], quality: { errors: 0, warnings: 0, findings: [] }, notes: [],
      },
      step: "screens",
    });
    if (mode === "standalone" && src.domain) {
      api.post<any>("api/domain/validate", { domain: src.domain })
        .then((r) => draftStore.update({ domainSummary: r.summary, domainNotes: r.notes || [] })).catch(() => undefined);
    }
    toastBus.push(`Opened version v${v.version.number}`, "good");
    return true;
  } catch (e: any) {
    toastBus.push(`Could not open the version: ${e.message}`, "bad");
    return false;
  }
}

function Toasts() {
  const toasts = useToasts();
  return (
    <div className="ev-toasts" aria-live="polite">
      <AnimatePresence>
        {toasts.map((t) => (
          <motion.div key={t.id} className={`ev-toast ev-toast--${t.tone}`} initial={{ opacity: 0, y: 16, scale: 0.96 }}
            animate={{ opacity: 1, y: 0, scale: 1 }} exit={{ opacity: 0, y: 10 }} transition={{ duration: 0.2 }}>
            <Icon name={t.tone === "good" ? "sys-enter-2" : t.tone === "bad" ? "error" : "message-information"} />
            <span>{t.text}</span>
          </motion.div>
        ))}
      </AnimatePresence>
    </div>
  );
}
