// Studio draft: everything the user is building, persisted to localStorage.
// It is a module-level store (not React state) on purpose: background jobs finish while the
// user is on another page — their results must still land in the draft.
import { useSyncExternalStore } from "react";
import type { CoverageItem, ValidationReport } from "../../api/types";

export type Mode = "standalone" | "sap";
export interface Question { id: string; question: string; options: string[]; why?: string }
export interface ServiceSummary {
  odata_version: string;
  entity_sets: { name: string; label: string; keys: string[]; field_count: number;
    fields: { name: string; label: string; type: string }[]; creatable?: boolean; updatable?: boolean; deletable?: boolean;
    navigations: { name: string; target: string; to_many: boolean }[] }[];
  operations: { name: string; kind: string; bound_type: string; label: string }[];
}
export interface SapService { edmx: string; service_url: string; title: string; summary?: ServiceSummary; destination?: string }
export interface DestinationCfg { use_existing: boolean; name: string; on_premise: boolean; auth_type: string }
export interface PageInfo { id: string; type: string; title: string; entity_set?: string }
export interface DesignResult {
  blueprint: any; notes: string[]; source: string; spec: any | null; coverage: CoverageItem[];
  coverage_score: number | null; steps: { step: string; ok: boolean; seconds: number; detail?: string }[];
  service_url: string; structure: string; pages?: PageInfo[];
}
export interface ChatTurn { role: "user" | "ai"; text: string; at: number; error?: boolean }
export interface GenResult {
  project_id: string; version_id: string; version_number: number; file_count: number; files: string[];
  quality: { errors: number; warnings: number; findings: { severity: string; message: string; file?: string }[] };
  notes: string[];
}
export interface Draft {
  mode: Mode | null;
  step: string;
  requirement: string;
  questions: Question[];
  answers: Record<string, string>;
  domain: any | null;
  domainNotes: string[];
  domainSummary: string;
  domainSource: string;
  db: "sqlite" | "hana";
  services: SapService[];
  destination: DestinationCfg;
  floorplan: string;
  design: DesignResult | null;
  chat: ChatTurn[];
  appId: string;
  theme: string;
  generated: GenResult | null;
  validation: ValidationReport | null;
  jobIds: Record<string, string>;
  errors: Record<string, string>;
  updatedAt: number;
}

const KEY = "evoke.studio.draft.v2";
export const emptyDraft = (): Draft => ({
  mode: null, step: "", requirement: "", questions: [], answers: {}, domain: null, domainNotes: [], domainSummary: "",
  domainSource: "", db: "sqlite", services: [],
  destination: { use_existing: false, name: "", on_premise: false, auth_type: "NoAuthentication" },
  floorplan: "", design: null, chat: [], appId: "", theme: "sap_horizon", generated: null, validation: null,
  jobIds: {}, errors: {}, updatedAt: 0,
});

function load(): Draft {
  try {
    const raw = localStorage.getItem(KEY);
    if (raw) return { ...emptyDraft(), ...JSON.parse(raw) };
  } catch { /* storage unavailable or corrupt */ }
  return emptyDraft();
}

let state: Draft = load();
const listeners = new Set<() => void>();

export const draftStore = {
  get: () => state,
  subscribe: (fn: () => void) => { listeners.add(fn); return () => listeners.delete(fn); },
  update(patch: Partial<Draft> | ((d: Draft) => Partial<Draft>)) {
    const p = typeof patch === "function" ? patch(state) : patch;
    state = { ...state, ...p, updatedAt: Date.now() };
    try { localStorage.setItem(KEY, JSON.stringify(state)); } catch { /* quota / private mode */ }
    listeners.forEach((l) => l());
  },
  reset(mode: Mode | null = null) {
    state = { ...emptyDraft(), mode, updatedAt: Date.now() };
    try { localStorage.setItem(KEY, JSON.stringify(state)); } catch { /* ignore */ }
    listeners.forEach((l) => l());
  },
  setError(kind: string, msg: string) {
    draftStore.update((d) => ({ errors: { ...d.errors, [kind]: msg } }));
  },
  clearError(kind: string) {
    if (state.errors[kind]) draftStore.update((d) => { const e = { ...d.errors }; delete e[kind]; return { errors: e }; });
  },
};

export function useDraft(): Draft {
  return useSyncExternalStore(draftStore.subscribe, draftStore.get, draftStore.get);
}

/** The "source" object of the API contract (what the app is built on). */
export function sourceOf(d: Draft): any {
  if (d.mode === "standalone") return { mode: "standalone", domain: d.domain, db: d.db };
  return {
    mode: "sap",
    services: d.services.map((s) => ({ edmx: s.edmx, service_url: s.service_url, title: s.title })),
    destination: d.destination,
  };
}

/** Human title of the app being built. */
export function draftTitle(d: Draft): string {
  return d.design?.blueprint?.app?.title || d.domain?.title || (d.services[0]?.title ?? "") || "New application";
}

export function defaultAppId(d: Draft): string {
  const base = (d.domain?.name || d.design?.blueprint?.app?.title || d.services[0]?.title || "app")
    .toString().toLowerCase().replace(/[^a-z0-9]/g, "").slice(0, 30) || "app";
  return `com.evoke.${/^[a-z]/.test(base) ? base : "app" + base}`;
}

// ── tiny toast bus (studio-local) ─────────────────────────────────────────────
type Toast = { id: number; text: string; tone: "good" | "bad" | "info" };
let toasts: Toast[] = [];
const toastListeners = new Set<() => void>();
export const toastBus = {
  get: () => toasts,
  subscribe: (fn: () => void) => { toastListeners.add(fn); return () => toastListeners.delete(fn); },
  push(text: string, tone: Toast["tone"] = "info") {
    const t = { id: Date.now() + Math.random(), text, tone };
    toasts = [...toasts, t];
    toastListeners.forEach((l) => l());
    setTimeout(() => { toasts = toasts.filter((x) => x.id !== t.id); toastListeners.forEach((l) => l()); }, 4200);
  },
};
export function useToasts() { return useSyncExternalStore(toastBus.subscribe, toastBus.get, toastBus.get); }
