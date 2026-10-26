// Shared API types (see docs/studio-api-v2.md for the full contract).
export interface User { id: string; name: string; roles: string[]; source: string }
export interface Me {
  user: User;
  ai: { available: boolean; model: string | null; error?: string };
  store: { kind: "hana" | "sqlite"; durable: boolean; note: string };
  permissions?: string[];
}
export type JobStatus = "running" | "done" | "error";
export interface Job<R = any> {
  id: string; kind: string; label: string; status: JobStatus; seconds: number;
  messages: string[]; result?: R | null; error?: string;
}
export interface Project {
  id: string; name: string; mode: "sap" | "standalone" | "import" | "adopted"; owner: string; app_id: string;
  description: string; updated_at: number; latest_version: number; archived?: boolean;
}
export interface ValidationSummary { passed: number; failed: number; not_tested: number; warnings: number }
export interface VersionMeta {
  id: string; number: number; label: string; author: string; created_at: number; file_count: number;
  project_id?: string; validation?: { summary: ValidationSummary; blocking: string[] } | null;
}
export interface Check { id: string; name: string; category: string; status: "passed" | "failed" | "not_tested" | "warning"; critical: boolean; detail: string; seconds: number }
export interface ValidationReport {
  checks: Check[]; requirements: { feature: string; status: string; detail?: string }[];
  summary: ValidationSummary; blocking: string[]; level: string; seconds: number;
}
export interface CoverageItem { id?: string; feature: string; status: "covered" | "partial" | "missing"; detail?: string; priority?: string }

// ── Adopt existing projects (docs/adopt-api.md) ──────────────────────────────
export type Cap = "understand" | "preview" | "designers" | "ai_change" | "validate" | "deploy";
export type CapLevel = "y" | "p" | "n";
export interface AdoptModule {
  id: string; name: string; path: string; kind: string; framework: string; evidence: string;
  level: "full" | "code" | "config"; role: "data" | "service" | "ui" | "entry" | "other";
  caps: Record<Cap, CapLevel>; commands: { install?: string; build?: string; test?: string; run?: string };
}
export interface ArchNode { id: string; label: string; sub: string; external?: boolean }
export interface Finding { severity: "bad" | "warn" | "info"; text: string; fix: string }
export interface ModelField { name: string; type: string; key: boolean; assoc: string }
export interface ModelEntity { name: string; fields: ModelField[] }
export interface Understanding {
  summary?: string; business_objects?: string[]; roles?: string[]; processes?: string[];
  integrations?: string[]; risks?: string[]; suggested_missions?: Suggestion[];
}
export interface Suggestion { title: string; request: string; why: string }
export interface QuestionOption { value: string; label: string; hint?: string; checked?: boolean }
export interface Question {
  id: string; title: string; why: string; guess?: string;
  kind: "text" | "textarea" | "code" | "select" | "radio" | "checks";
  options?: QuestionOption[]; value: string | string[];
}
export type Answers = Record<string, string | string[]>;
export interface Analysis {
  id: string; name: string; primary: string;
  source: { kind: "zip" | "git" | "cf"; label: string; url?: string; branch?: string; commit?: string; app?: string };
  files: number; bytes: number; skipped: { path: string; why: string }[];
  stack: string[]; languages: Record<string, number>;
  modules: AdoptModule[]; arch: ArchNode[][]; findings: Finding[];
  model: { source: string; editable: boolean; entities: ModelEntity[]; services: { name: string; path?: string; entities?: any[]; actions?: string[] }[] };
  screens: { name: string; type: string; source: string; editing: "visual" | "ai"; module: string }[];
  endpoints: { method: string; path: string; source: string; module: string }[];
  destinations: { name: string; used_by: string; purpose: string; internal: boolean }[];
  env_vars: string[];
  understanding?: Understanding;
  questions?: Question[];
}
export type MissionStatus = "understanding" | "needs_input" | "planned" | "working" | "ready" | "failed" | "applied" | "discarded";
export interface MissionStep { at: number; kind: "think" | "read" | "search" | "edit" | "check" | "ask" | "info"; text: string; ok: boolean }
export interface Mission {
  id: string; project_id: string; version_id: string; author: string; created_at: number;
  request: string; status: MissionStatus; restated: string;
  questions: { id: string; question: string; options?: string[]; default?: string }[];
  answers: Record<string, string>; criteria: string[]; plan: { step: string; files: string[] }[];
  design?: { screen: string; type: string; purpose: string; layout: string; sections: string[] }[];
  changes: { path: string; status: "added" | "modified" | "deleted"; additions: number; deletions: number }[];
  diff: string; summary: string; checks: ValidationReport | null;
  criteria_results: { criterion: string; met: boolean; evidence: string }[];
  steps: MissionStep[]; rounds: number; applied_version?: { id: string; number: number }; error?: string;
}
export interface MissionSummary {
  id: string; request: string; status: MissionStatus; created_at: number; author: string;
  changes: number; applied_version?: { id: string; number: number } | null;
}
export interface AdoptProjectInfo {
  project: Project; version: VersionMeta; analysis: Analysis; brief: Answers;
  source?: { kind?: string; url?: string; label?: string; related?: { app: string; destination: string; url: string }[] };
  missions: MissionSummary[]; runner: { mode: "local" | "remote"; node?: string; python?: string; ok: boolean };
}
export interface PreviewInfo { preview_id: string; url: string; kind: string; entry: string; log: string; warnings?: string[] }
