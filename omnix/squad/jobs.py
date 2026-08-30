"""Generic background-job manager for the squad — one engine for all units.

Same design as AVALON's JobManager (daemon thread per job, event list + a
condition variable for SSE replay/follow) but unit-agnostic: it runs any
`Unit.run(ctx, emit)` and stores the resulting `UnitResult`. Finished jobs are
persisted under <root>/squad_jobs/<id>/ so history survives restarts.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

JOBS_DIR = Path(__file__).resolve().parents[2] / "squad_jobs"


@dataclass
class Job:
    id: str
    unit: str
    ctx: dict = field(default_factory=dict)
    status: str = "queued"           # queued | running | done | error
    created: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    events: list[dict] = field(default_factory=list)
    result: dict | None = None
    error: str = ""

    def public(self, with_result: bool = False) -> dict:
        out = {
            "id": self.id, "unit": self.unit, "status": self.status,
            "created": self.created, "error": self.error,
            "label": (self.ctx.get("input") or "")[:80],
        }
        if with_result and self.result:
            out["result"] = self.result
        return out


class JobManager:
    def __init__(self):
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)

    def start(self, unit, ctx: dict | None = None) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], unit=unit.code, ctx=ctx or {})
        with self._lock:
            self._jobs[job.id] = job
        t = threading.Thread(target=self._run, args=(job, unit), daemon=True,
                             name=f"squad-{unit.code}-{job.id}")
        t.start()
        return job

    def _emit(self, job: Job, stage: str, detail: str = "") -> None:
        with self._cond:
            self._append(job, stage, detail)
            self._cond.notify_all()

    def _append(self, job: Job, stage: str, detail: str) -> None:
        """Append an event. Caller must hold self._cond."""
        job.events.append({
            "stage": stage, "detail": detail,
            "ts": datetime.now().isoformat(timespec="seconds"),
        })

    def _finish(self, job: Job, status: str, stage: str, detail: str,
                result: dict | None = None) -> None:
        """Flip a job to its terminal state AND queue its terminal event under a
        single lock.

        These must be atomic: events() returns as soon as it observes a terminal
        status with no unread events, so setting the status first (and only then
        emitting) let the stream close before "done"/"error" was ever appended.
        Clients saw the run stop at its last progress stage and hang forever.
        """
        with self._cond:
            if result is not None:
                job.result = result
            if status == "error":
                job.error = detail
            job.status = status
            self._append(job, stage, detail)
            self._cond.notify_all()

    def _run(self, job: Job, unit) -> None:
        with self._cond:
            job.status = "running"
            self._cond.notify_all()
        self._emit(job, "start", f"{unit.name} engaged")
        try:
            result = unit.run(job.ctx, lambda stage, detail="": self._emit(job, stage, detail))
            data = result.to_dict() if hasattr(result, "to_dict") else dict(result)
            # Persist BEFORE going terminal: once status flips, readers may fetch
            # the job and expect its result on disk.
            job.result = data
            self._persist(job, unit)
            self._finish(job, "done", "done", "complete", result=data)
        except Exception as e:  # never let a unit crash the thread silently
            self._finish(job, "error", "error", str(e)[:400])
        finally:
            # An uploaded image is written to a temp file with `delete=False` so
            # the unit can read it; whoever wrote the file cannot delete it,
            # because the job outlives the request that started it. This is the
            # only place that knows the work is over.
            path = job.ctx.get("image_path")
            if path:
                try:
                    os.unlink(path)
                except OSError:
                    pass

    def _persist(self, job: Job, unit) -> None:
        try:
            d = JOBS_DIR / job.id
            d.mkdir(parents=True, exist_ok=True)
            (d / "job.json").write_text(json.dumps({
                "id": job.id, "unit": job.unit, "created": job.created,
                "ctx": job.ctx, "result": job.result,
            }, indent=2), encoding="utf-8")
        except Exception:
            pass  # persistence is best-effort

    # -- queries --------------------------------------------------------------
    def get(self, job_id: str) -> Job | None:
        with self._lock:
            job = self._jobs.get(job_id)
        if job:
            return job
        p = JOBS_DIR / job_id / "job.json"
        if p.exists():
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                job = Job(id=job_id, unit=d.get("unit", ""), ctx=d.get("ctx", {}),
                          status="done", created=d.get("created", ""))
                job.result = d.get("result")
                return job
            except Exception:
                return None
        return None

    def list_jobs(self, unit: str | None = None) -> list[dict]:
        out: dict[str, dict] = {}
        if JOBS_DIR.exists():
            for p in JOBS_DIR.glob("*/job.json"):
                try:
                    d = json.loads(p.read_text(encoding="utf-8"))
                    out[d["id"]] = {
                        "id": d["id"], "unit": d.get("unit", ""),
                        "created": d.get("created", ""), "status": "done",
                        "label": (d.get("ctx", {}).get("input") or "")[:80],
                    }
                except Exception:
                    continue
        with self._lock:
            for job in self._jobs.values():
                out[job.id] = job.public()
        jobs = list(out.values())
        if unit:
            jobs = [j for j in jobs if j.get("unit") == unit]
        return sorted(jobs, key=lambda j: j.get("created", ""), reverse=True)

    # -- SSE ------------------------------------------------------------------
    def events(self, job_id: str, poll_timeout: float = 25.0):
        job = self.get(job_id)
        if job is None:
            yield {"stage": "error", "detail": "unknown job"}
            return
        idx = 0
        while True:
            with self._cond:
                while idx >= len(job.events) and job.status in ("queued", "running"):
                    if not self._cond.wait(timeout=poll_timeout):
                        break
                new = job.events[idx:]
                idx = len(job.events)
                status = job.status
            for ev in new:
                yield ev
            if status in ("done", "error") and idx >= len(job.events):
                return
            if not new:
                yield {"stage": "heartbeat", "detail": status}


# Module-level singleton used by the server.
manager = JobManager()
