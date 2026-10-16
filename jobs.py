"""
jobs.py — long-running studio work (AI design, generation, validation, deployment) in
background threads.

Streamlit runs the page script top to bottom on every interaction: clicking another step,
a sidebar button or any widget stops the current run — and with it an AI call that was
still in progress. Jobs run outside the script run instead: the page only starts them,
polls their progress and applies the result when they finish. They also survive a
dropped browser connection: jobs are kept per user, so a reconnected session re-attaches.

A job function never touches st.* — it gets `say(message)` for progress and returns a
result; everything Streamlit-side happens in the page when the result is applied.
"""

from __future__ import annotations

import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

KEEP_SECONDS = 6 * 3600          # finished jobs are kept this long (re-attach after a reconnect)


@dataclass
class Job:
    id: str
    kind: str                     # domain | design | generate | validate | deploy
    owner: str                    # user id
    session: str                  # browser session that started it
    label: str
    context: Dict = field(default_factory=dict)      # what the page needs to apply the result
    status: str = "running"      # running | done | error
    messages: List[str] = field(default_factory=list)
    result: Any = None
    error: str = ""
    started: float = field(default_factory=time.time)
    finished: float = 0.0
    applied: bool = False

    def say(self, msg) -> None:
        self.messages.append(str(msg))
        del self.messages[:-200]

    @property
    def running(self) -> bool:
        return self.status == "running"

    @property
    def seconds(self) -> float:
        return (self.finished or time.time()) - self.started


_JOBS: Dict[str, Job] = {}
_LOCK = threading.Lock()


def start(kind: str, owner: str, session: str, label: str, fn: Callable[..., Any], *args,
          context: Optional[Dict] = None, **kwargs) -> Job:
    """Run fn(say, *args, **kwargs) in a daemon thread."""
    job = Job(id=uuid.uuid4().hex[:12], kind=kind, owner=owner, session=session, label=label,
              context=dict(context or {}))

    def run():
        try:
            job.result = fn(job.say, *args, **kwargs)
            job.status = "done"
        except Exception as e:  # noqa: BLE001 — reported in the page
            job.error = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
            job.say("".join(traceback.format_exception_only(type(e), e)).strip()[:500])
            job.status = "error"
        finally:
            job.finished = time.time()

    with _LOCK:
        _cleanup()
        _JOBS[job.id] = job
    threading.Thread(target=run, name=f"studio-{kind}-{job.id}", daemon=True).start()
    return job


def get(job_id: Optional[str]) -> Optional[Job]:
    return _JOBS.get(job_id or "")


def active(owner: str, session: str) -> List[Job]:
    """Jobs this page should show: its own session's, plus unapplied ones of the same user
    whose browser session is gone (re-attach after a reconnect)."""
    out = [j for j in _JOBS.values() if j.owner == owner and not j.applied
           and (j.session == session or j.session not in _live_sessions())]
    return sorted(out, key=lambda j: j.started)


def running(owner: str, session: str, kind: str) -> Optional[Job]:
    return next((j for j in active(owner, session) if j.kind == kind and j.running), None)


# Live browser sessions (refreshed by the page each run) — a job whose session is no longer
# live may be adopted by the same user's new session.
_SESSIONS: Dict[str, float] = {}


def heartbeat(session: str) -> None:
    _SESSIONS[session] = time.time()


def _live_sessions() -> set:
    now = time.time()
    return {s for s, t in _SESSIONS.items() if now - t < 120}


def _cleanup() -> None:
    now = time.time()
    for jid in [j.id for j in _JOBS.values() if not j.running and now - j.finished > KEEP_SECONDS]:
        _JOBS.pop(jid, None)
