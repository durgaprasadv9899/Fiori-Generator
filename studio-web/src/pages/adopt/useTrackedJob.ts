// A background job that a page section follows: the job id survives navigation and reloads
// (sessionStorage), and the result is applied when the job finishes — even after a remount.
import { useEffect, useRef, useState } from "react";
import { useJobs } from "../../jobs/JobProvider";
import { api } from "../../api/client";
import type { Job } from "../../api/types";

export function useTrackedJob<R = any>(key: string, onDone: (result: R, job: Job) => void, onError?: (error: string, job: Job) => void) {
  const jobs = useJobs();
  const skey = `evoke.job.${key}`;
  const [id, setId] = useState<string>(() => { try { return sessionStorage.getItem(skey) || ""; } catch { return ""; } });
  const done = useRef(onDone);
  const fail = useRef(onError);
  done.current = onDone;
  fail.current = onError;
  const handled = useRef("");
  const job = jobs.get(id);

  const remember = (v: string) => {
    setId(v);
    try { if (v) sessionStorage.setItem(skey, v); else sessionStorage.removeItem(skey); } catch { /* storage unavailable */ }
  };

  useEffect(() => {
    if (!job || job.status === "running" || handled.current === job.id) return;
    handled.current = job.id;
    if (job.status === "done") done.current(job.result as R, job);
    else fail.current?.(job.error || "The job failed.", job);
    remember("");
  }, [job?.status, job?.id]); // eslint-disable-line

  const tracked = !!job;
  useEffect(() => {                      // a job the provider does not follow (reload, lost poll): follow it here
    if (!id || tracked) return;
    let stop = false;
    const check = () => api.get<Job>(`api/jobs/${id}`).then((j) => {
      if (stop || handled.current === j.id) return;
      if (j.status === "running") { setTimeout(check, 1500); return; }
      handled.current = j.id;
      if (j.status === "done") done.current(j.result as R, j); else fail.current?.(j.error || "The job failed.", j);
      remember("");
    }).catch(() => { if (!stop) remember(""); });
    const t = setTimeout(check, 400);
    return () => { stop = true; clearTimeout(t); };
  }, [id, tracked]); // eslint-disable-line

  const start = async (path: string, body: unknown) => {
    const jid = await jobs.start(path, body);
    handled.current = "";
    remember(jid);
    return jid;
  };
  // a job id we follow but the provider does not know yet still counts as running
  return { job, running: job ? job.status === "running" : !!id, start, clear: () => remember("") };
}
