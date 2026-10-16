// Background jobs (AI design, generation, validation, deployment) run on the server; the UI polls
// them. Jobs live in this provider (above the router), so they keep running and finish even while
// the user navigates. Running job ids are kept in sessionStorage to re-attach after a reload.
import React, { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { Job } from "../api/types";

type Handlers = { onDone?: (result: any, job: Job) => void; onError?: (error: string, job: Job) => void };
interface Ctx {
  jobs: Job[];                                   // running + recently finished (newest first)
  start: (path: string, body: unknown, handlers?: Handlers) => Promise<string>;
  get: (id?: string | null) => Job | undefined;
  running: (kind: string) => Job | undefined;
  dismiss: (id: string) => void;
}
const JobContext = createContext<Ctx>(null as unknown as Ctx);
const STORE_KEY = "evoke.jobs.running";

export function JobProvider({ children }: { children: React.ReactNode }) {
  const [jobs, setJobs] = useState<Job[]>([]);
  const handlers = useRef<Record<string, Handlers>>({});

  const upsert = (j: Job) => setJobs((prev) => [j, ...prev.filter((x) => x.id !== j.id)].slice(0, 30));

  const poll = useCallback(async (id: string) => {
    for (;;) {
      let j: Job;
      try {
        j = await api.get<Job>(`api/jobs/${id}`);
      } catch (e: any) {
        const failed: Job = { id, kind: "?", label: "Job", status: "error", seconds: 0, messages: [], error: e.message };
        upsert(failed);
        handlers.current[id]?.onError?.(e.message, failed);
        break;
      }
      upsert(j);
      if (j.status !== "running") {
        if (j.status === "done") handlers.current[id]?.onDone?.(j.result, j);
        else handlers.current[id]?.onError?.(j.error || "failed", j);
        delete handlers.current[id];
        const ids = JSON.parse(sessionStorage.getItem(STORE_KEY) || "[]").filter((x: string) => x !== id);
        sessionStorage.setItem(STORE_KEY, JSON.stringify(ids));
        break;
      }
      await new Promise((r) => setTimeout(r, 1200));
    }
  }, []);

  const start = useCallback(async (path: string, body: unknown, h: Handlers = {}) => {
    const { job_id } = await api.post<{ job_id: string }>(path, body);
    handlers.current[job_id] = h;
    try {
      const ids = JSON.parse(sessionStorage.getItem(STORE_KEY) || "[]");
      sessionStorage.setItem(STORE_KEY, JSON.stringify([...ids, job_id]));
    } catch { /* storage unavailable */ }
    upsert({ id: job_id, kind: path.split("/").pop() || "", label: "Starting…", status: "running", seconds: 0, messages: [] });
    poll(job_id);
    return job_id;
  }, [poll]);

  useEffect(() => {                              // re-attach to jobs still running on the server
    api.get<{ jobs: Job[] }>("api/jobs").then(({ jobs: running }) => running.forEach((j) => { upsert(j); poll(j.id); }))
      .catch(() => undefined);
  }, [poll]);

  const value: Ctx = {
    jobs,
    start,
    get: (id) => jobs.find((j) => j.id === id),
    running: (kind) => jobs.find((j) => j.kind === kind && j.status === "running"),
    dismiss: (id) => setJobs((p) => p.filter((j) => j.id !== id)),
  };
  return <JobContext.Provider value={value}>{children}</JobContext.Provider>;
}

export const useJobs = () => useContext(JobContext);
