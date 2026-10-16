// Step definitions and job helpers of the studio flow.
import { useJobs } from "../../jobs/JobProvider";
import type { Job } from "../../api/types";
import { draftStore, toastBus, useDraft, type Draft, type Mode } from "./draft";

export interface StepDef { key: string; label: string; hint: string; icon: string; ready: (d: Draft) => boolean }

const STANDALONE: StepDef[] = [
  { key: "describe", label: "Describe", hint: "What should the app do?", icon: "write-new-document", ready: () => true },
  { key: "model", label: "Data model", hint: "Entities, fields, process", icon: "org-chart", ready: (d) => !!d.domain },
  { key: "screens", label: "Screens", hint: "AI screen design", icon: "screen-split-three", ready: (d) => !!d.domain },
  { key: "tryit", label: "Try it", hint: "Clickable preview", icon: "play", ready: (d) => !!d.design },
  { key: "generate", label: "Generate & validate", hint: "Project + checks", icon: "source-code", ready: (d) => !!d.design },
  { key: "deploy", label: "Deploy", hint: "Release to BTP", icon: "cloud", ready: (d) => !!d.generated },
];
const SAP: StepDef[] = [
  { key: "connect", label: "Connect", hint: "OData service", icon: "connected", ready: () => true },
  { key: "describe", label: "Describe", hint: "What should the app do?", icon: "write-new-document", ready: (d) => d.services.length > 0 },
  { key: "screens", label: "Screens", hint: "AI screen design", icon: "screen-split-three", ready: (d) => d.services.length > 0 },
  { key: "tryit", label: "Try it", hint: "Clickable preview", icon: "play", ready: (d) => !!d.design },
  { key: "generate", label: "Generate & validate", hint: "Project + checks", icon: "source-code", ready: (d) => !!d.design },
  { key: "deploy", label: "Deploy", hint: "Release to BTP", icon: "cloud", ready: (d) => !!d.generated },
];

export const stepsFor = (mode: Mode | null): StepDef[] => (mode === "sap" ? SAP : STANDALONE);

/** Step reached so far: index of the last step whose prerequisites are met. */
export function reachable(d: Draft): number {
  const steps = stepsFor(d.mode);
  let last = 0;
  steps.forEach((s, i) => { if (s.ready(d)) last = i; });
  return last;
}

/** Start a background job whose result is written into the draft (works while unmounted). */
export function useStudioJobs() {
  const jobs = useJobs();
  const draft = useDraft();
  const start = async (kind: string, path: string, body: unknown, apply: (result: any) => void, doneText?: string) => {
    draftStore.clearError(kind);
    try {
      const id = await jobs.start(path, body, {
        onDone: (r) => { apply(r); if (doneText) toastBus.push(doneText, "good"); },
        onError: (e) => { draftStore.setError(kind, e); toastBus.push(e, "bad"); },
      });
      draftStore.update((d) => ({ jobIds: { ...d.jobIds, [kind]: id } }));
    } catch (e: any) {
      draftStore.setError(kind, e.message);
      toastBus.push(e.message, "bad");
    }
  };
  /** The job of that kind this draft started (live while running, last state afterwards). */
  const jobOf = (kind: string): Job | undefined => {
    const id = draft.jobIds[kind];
    return (id && jobs.get(id)) || jobs.running(kind);
  };
  const busy = (kind: string) => jobOf(kind)?.status === "running";
  return { start, jobOf, busy };
}
