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
  id: string; name: string; mode: "sap" | "standalone" | "import"; owner: string; app_id: string;
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
