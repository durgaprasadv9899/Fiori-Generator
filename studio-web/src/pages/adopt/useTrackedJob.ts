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

  useEffect(() => {                      // finished while the page was closed: fetch the result once
    if (!id || jobs.get(id)) return;
    api.get<Job>(`api/jobs/${id}`).then((j) => {
      if (j.status === "running" || handled.current === j.id) return;
      handled.current = j.id;
      if (j.status === "done") done.current(j.result as R, j); else fail.current?.(j.error || "The job failed.", j);
      remember("");
    }).catch(() => remember(""));
  }, []); // eslint-disable-line

  const start = async (path: string, body: unknown) => {
    const jid = await jobs.start(path, body);
    handled.current = "";
    remember(jid);
    return jid;
  };
  return { job, running: !!job && job.status === "running", start, clear: () => remember("") };
}
