"""The execution engine.

One engine for every agent. A single-agent run is a one-step DAG, so NOVA's
compiled workflows need no separate machinery — they are the same table with
`depends_on` filled in. That equivalence is the point: it is why "research then
implement" can be one execution with a visible plan rather than two unrelated
jobs stitched together in the UI.

Generalised from squad/jobs.py, which already had the hard parts right (daemon
thread per run, event replay, atomic terminal transition). What is new here is
everything the restructure needs and that had no home before: durable state,
DAG steps, cancellation, per-step retry, and artifacts emitted mid-run.

Cancellation is cooperative, and honestly so. A daemon thread running inside a
provider HTTP call cannot be killed; what `cancel()` does is set an event that
the runner checks between steps and that streaming callers check between
chunks. A step already in flight finishes. The UI must say "cancelling" rather
than claim an instant stop.
"""

from __future__ import annotations

import threading
import time
import traceback
from collections.abc import Callable
from datetime import datetime, timezone

from sqlalchemy import select

from . import artifacts as artifacts_mod
from . import events as events_mod
from .db import session
from .schema import Execution, ExecutionStep, TERMINAL_STATUSES, iso

# execution_id -> Event set when a cancel is requested.
_cancels: dict[str, threading.Event] = {}
_cancels_lock = threading.Lock()


class _Directives:
    """Live control state for one run: pause and queued redirects.

    Separate from `_cancels` because cancellation is terminal and these are
    not — a paused run is still a run, and a redirect changes what it does
    without ending it. Both are cooperative in the same honest sense as
    `cancel()`: a step already inside a provider call finishes first.
    """

    def __init__(self) -> None:
        # Set means RUNNING. Inverted deliberately: `wait()` on a set event
        # returns immediately, so the un-paused path costs nothing.
        self.running = threading.Event()
        self.running.set()
        self.redirects: list[str] = []
        self.lock = threading.Lock()


_directives: dict[str, _Directives] = {}


def _directive(execution_id: str) -> _Directives:
    with _cancels_lock:
        d = _directives.get(execution_id)
        if d is None:
            d = _Directives()
            _directives[execution_id] = d
        return d


class Cancelled(Exception):
    """Raised inside a runner when cancellation is observed."""


# ---------------------------------------------------------------------------
# Context handed to step functions
# ---------------------------------------------------------------------------
class StepContext:
    """What a step body is given. Deliberately small.

    A step reports progress, emits artifacts, and checks whether it should
    stop. Everything else — status transitions, timing, retries — belongs to
    the engine, so agent code cannot get it wrong.
    """

    def __init__(self, execution_id: str, workspace_id: str, agent: str,
                 step_id: str, step_key: str, inputs: dict):
        self.execution_id = execution_id
        self.workspace_id = workspace_id
        self.agent = agent
        self.step_id = step_id
        self.step_key = step_key
        self.inputs = inputs or {}
        self.artifacts: list[dict] = []

    # -- progress -------------------------------------------------------------
    def progress(self, stage: str, detail: str = "") -> None:
        self.check_cancelled()
        events_mod.emit(self.execution_id, self.workspace_id, "execution.progress",
                        {"stage": stage, "detail": detail, "step": self.step_key})

    def emit_artifact(self, type: str, title: str, content: dict,
                      *, tags: list[str] | None = None,
                      references: list[tuple[str, str]] | None = None) -> dict:
        """Create an artifact attributed to this run. This is the handoff."""
        art = artifacts_mod.create(
            self.workspace_id, type, title, content,
            source_agent=self.agent, execution_id=self.execution_id,
            tags=tags, references=references)
        self.artifacts.append(art)
        events_mod.emit(self.execution_id, self.workspace_id, "artifact.created",
                        {"artifactId": art["id"], "type": type, "title": art["title"]})
        return art

    # -- cancellation ---------------------------------------------------------
    def cancelled(self) -> bool:
        ev = _cancels.get(self.execution_id)
        return bool(ev and ev.is_set())

    def check_cancelled(self) -> None:
        if self.cancelled():
            raise Cancelled()

    # -- interruption (§9) ----------------------------------------------------
    def redirects(self) -> list[str]:
        """Drain user instructions issued since the last call.

        A step that wants to be redirectable calls this at a point where it can
        actually change course and folds the strings into its next prompt.
        Draining rather than peeking is deliberate: an instruction applied twice
        is worse than one applied late.
        """
        d = _directives.get(self.execution_id)
        if d is None:
            return []
        with d.lock:
            pending, d.redirects = d.redirects, []
        return pending

    def paused(self) -> bool:
        d = _directives.get(self.execution_id)
        return bool(d and not d.running.is_set())

    def checkpoint(self) -> None:
        """Yield to the user: block while paused, raise if cancelled.

        The one call a long step should make between units of work. Cancel is
        checked on both sides of the wait so a run paused and then cancelled
        stops instead of hanging on a resume that never comes.
        """
        self.check_cancelled()
        _await_resume(self.execution_id)
        self.check_cancelled()


# A step body: takes the context, returns a JSON-able output dict.
StepFn = Callable[[StepContext], dict]


class StepSpec:
    """Declared step. `depends_on` holds keys of steps that must finish first."""

    def __init__(self, key: str, title: str, fn: StepFn, *, agent: str = "",
                 capability: str = "", depends_on: list[str] | None = None,
                 inputs: dict | None = None):
        self.key = key
        self.title = title
        self.fn = fn
        self.agent = agent
        self.capability = capability
        self.depends_on = list(depends_on or [])
        self.inputs = inputs or {}


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------
def create(workspace_id: str, agent: str, *, title: str = "",
           input: dict | None = None, mode: str = "auto",
           parent_execution_id: str | None = None) -> str:
    with session() as s:
        ex = Execution(workspace_id=workspace_id, agent=agent, mode=mode,
                       title=(title or "")[:300], input_json=input or {},
                       parent_execution_id=parent_execution_id,
                       status="queued")
        s.add(ex)
        s.flush()
        eid = ex.id
        events_mod.emit(eid, workspace_id, "execution.created",
                        {"agent": agent, "title": ex.title, "mode": mode},
                        _session=s)
        if parent_execution_id:
            events_mod.emit(eid, workspace_id, "agent.handoff",
                            {"from": parent_execution_id, "to": eid, "agent": agent},
                            _session=s)
    return eid


def start(execution_id: str, steps: list[StepSpec]) -> None:
    """Persist the plan and run it on a daemon thread."""
    with session() as s:
        ex = s.get(Execution, execution_id)
        if ex is None:
            raise ValueError(f"unknown execution {execution_id}")
        ex.status = "planning"
        ex.plan_json = {"steps": [{"key": st.key, "title": st.title,
                                   "agent": st.agent, "capability": st.capability,
                                   "dependsOn": st.depends_on} for st in steps]}
        for i, st in enumerate(steps):
            s.add(ExecutionStep(
                execution_id=execution_id, idx=i, key=st.key, title=st.title,
                agent=st.agent or ex.agent, capability=st.capability,
                depends_on_json=st.depends_on, input_json=st.inputs,
                status="queued"))
        workspace_id = ex.workspace_id

    with _cancels_lock:
        _cancels[execution_id] = threading.Event()
        _directives[execution_id] = _Directives()

    t = threading.Thread(target=_run, args=(execution_id, workspace_id, steps),
                         daemon=True, name=f"omx-exec-{execution_id[:8]}")
    t.start()


def _run(execution_id: str, workspace_id: str, steps: list[StepSpec]) -> None:
    started = time.time()
    with session() as s:
        ex = s.get(Execution, execution_id)
        if ex is None:
            return
        ex.status = "running"
        ex.started_at = datetime.now(timezone.utc)
        events_mod.emit(execution_id, workspace_id, "execution.started",
                        {"agent": ex.agent, "steps": len(steps)}, _session=s)

    by_key = {st.key: st for st in steps}
    done: dict[str, dict] = {}
    outputs: dict[str, dict] = {}

    try:
        remaining = list(steps)
        while remaining:
            _raise_if_cancelled(execution_id)
            # The step boundary is where a pause can be honoured without
            # abandoning work in flight, so it is where the run waits.
            _await_resume(execution_id)
            _raise_if_cancelled(execution_id)
            # Ready = every dependency satisfied. Sequential execution of a
            # ready set is deliberate: parallel steps would need a second
            # concurrency budget on top of the provider ladder's hedging, and
            # nothing in the current agents benefits enough to justify it.
            ready = [st for st in remaining if all(d in done for d in st.depends_on)]
            if not ready:
                unresolved = [st.key for st in remaining]
                raise RuntimeError(f"unsatisfiable step dependencies: {unresolved}")
            st = ready[0]
            remaining.remove(st)
            inputs = dict(st.inputs)
            # A step sees its dependencies' outputs without having to ask.
            inputs["_deps"] = {d: outputs.get(d, {}) for d in st.depends_on}
            out = _run_step(execution_id, workspace_id, st, inputs)
            done[st.key] = out
            outputs[st.key] = out

        finish(execution_id, "completed",
               duration_ms=int((time.time() - started) * 1000))

    except Cancelled:
        finish(execution_id, "cancelled",
               duration_ms=int((time.time() - started) * 1000))
    except Exception as e:
        detail = f"{type(e).__name__}: {e}"[:600]
        # Full traceback to the log, short message to the user: a stack trace in
        # a UI error card is noise, but losing it entirely makes support blind.
        try:
            print(f"[omnix.executions] {execution_id} failed\n"
                  f"{traceback.format_exc()}")
        except Exception:
            pass
        finish(execution_id, "failed", error=detail,
               duration_ms=int((time.time() - started) * 1000))
    finally:
        with _cancels_lock:
            _cancels.pop(execution_id, None)
            _directives.pop(execution_id, None)


def _run_step(execution_id: str, workspace_id: str, spec: StepSpec,
              inputs: dict) -> dict:
    with session() as s:
        row = s.scalar(select(ExecutionStep).where(
            ExecutionStep.execution_id == execution_id,
            ExecutionStep.key == spec.key))
        if row is None:
            raise RuntimeError(f"step row missing: {spec.key}")
        row.status = "running"
        row.started_at = datetime.now(timezone.utc)
        row.input_json = {k: v for k, v in inputs.items() if k != "_deps"}
        step_id = row.id
        agent = row.agent
        events_mod.emit(execution_id, workspace_id, "step.started",
                        {"step": spec.key, "title": spec.title, "agent": agent},
                        _session=s)

    ctx = StepContext(execution_id, workspace_id, agent, step_id, spec.key, inputs)
    t0 = time.time()
    # Attribute every model call made anywhere inside this step, however deep,
    # without agent code having to pass ids around. See models/callctx.py.
    token = None
    try:
        from ..models.callctx import CallContext, set_context
        token = set_context(CallContext(workspace_id=workspace_id,
                                        execution_id=execution_id,
                                        step_id=step_id, agent=agent))
    except Exception:
        token = None

    try:
        out = spec.fn(ctx) or {}
    except Cancelled:
        _mark_step(execution_id, spec.key, "cancelled", {}, int((time.time() - t0) * 1000))
        raise
    except Exception as e:
        dur = int((time.time() - t0) * 1000)
        detail = f"{type(e).__name__}: {e}"[:600]
        _mark_step(execution_id, spec.key, "failed", {}, dur, error=detail)
        events_mod.emit(execution_id, workspace_id, "step.failed",
                        {"step": spec.key, "error": detail})
        raise
    finally:
        if token is not None:
            try:
                from ..models.callctx import reset as _r
                _r(token)
            except Exception:
                pass

    dur = int((time.time() - t0) * 1000)
    _mark_step(execution_id, spec.key, "completed", out, dur)
    events_mod.emit(execution_id, workspace_id, "step.completed",
                    {"step": spec.key, "durationMs": dur,
                     "artifacts": [a["id"] for a in ctx.artifacts]})
    return out


def _mark_step(execution_id: str, key: str, status: str, output: dict,
               duration_ms: int, error: str = "") -> None:
    with session() as s:
        row = s.scalar(select(ExecutionStep).where(
            ExecutionStep.execution_id == execution_id, ExecutionStep.key == key))
        if row is None:
            return
        row.status = status
        row.output_json = output or {}
        row.duration_ms = duration_ms
        row.error = error


def finish(execution_id: str, status: str, *, error: str = "",
           duration_ms: int = 0) -> None:
    """Flip to a terminal state AND write the terminal event in one transaction.

    See the note in events.py — splitting these is what made SSE clients hang.
    """
    type_of = {"completed": "execution.completed",
               "failed": "execution.failed",
               "cancelled": "execution.cancelled"}.get(status, "execution.completed")
    with session() as s:
        ex = s.get(Execution, execution_id)
        if ex is None:
            return
        if ex.status in TERMINAL_STATUSES:
            return  # already finished; never emit a second terminal event
        ex.status = status
        ex.error = error
        ex.completed_at = datetime.now(timezone.utc)
        ex.duration_ms = duration_ms
        events_mod.emit(execution_id, ex.workspace_id, type_of,
                        {"status": status, "error": error,
                         "durationMs": duration_ms}, _session=s)


def cancel(execution_id: str) -> bool:
    """Request cancellation. Returns False if the run already finished.

    The status is not flipped here — the runner does that when it observes the
    flag, so a run that completes in the same instant is reported honestly as
    completed rather than as cancelled.
    """
    with session() as s:
        ex = s.get(Execution, execution_id)
        if ex is None or ex.status in TERMINAL_STATUSES:
            return False
        workspace_id = ex.workspace_id
    ev = _cancels.get(execution_id)
    if ev is not None:
        ev.set()
    events_mod.emit(execution_id, workspace_id, "execution.progress",
                    {"stage": "cancel", "detail": "cancellation requested"})
    return True


def reconcile_orphans() -> int:
    """Fail runs left mid-flight by a previous process. Call once, at boot.

    Execution state is durable but the threads running it are not. A server
    killed mid-run leaves rows saying `running` with nothing behind them, and
    the agent view then shows a worker that will never move — the exact
    dishonesty §9 exists to prevent. Since no run can survive the process that
    started it, anything non-terminal at startup is by definition abandoned.

    Safe only before new work begins, which is why it is not a periodic sweep.
    """
    with session() as s:
        rows = s.scalars(select(Execution).where(
            Execution.status.notin_(TERMINAL_STATUSES))).all()
        orphans = [(r.id, r.workspace_id) for r in rows]

    for execution_id, _ in orphans:
        finish(execution_id, "failed",
               error="Interrupted — the server restarted while this run was in "
                     "flight. Its partial results are kept; run it again to "
                     "continue.")
    if orphans:
        print(f"[omnix.executions] marked {len(orphans)} interrupted run(s) failed")
    return len(orphans)


def pause(execution_id: str) -> bool:
    """Ask a run to stop at its next step boundary.

    Returns False for a run that has already finished. The status is not
    changed — a paused run is still `running`, because it holds its resources
    and will continue. Reporting it as some other state would make a resume
    look like a restart.
    """
    with session() as s:
        ex = s.get(Execution, execution_id)
        if ex is None or ex.status in TERMINAL_STATUSES:
            return False
        workspace_id = ex.workspace_id
    _directive(execution_id).running.clear()
    events_mod.emit(execution_id, workspace_id, "execution.progress",
                    {"stage": "pause",
                     "detail": "pause requested — takes effect at the next step"})
    return True


def resume(execution_id: str) -> bool:
    with session() as s:
        ex = s.get(Execution, execution_id)
        if ex is None or ex.status in TERMINAL_STATUSES:
            return False
        workspace_id = ex.workspace_id
    _directive(execution_id).running.set()
    events_mod.emit(execution_id, workspace_id, "execution.progress",
                    {"stage": "resume", "detail": "resumed"})
    return True


def redirect(execution_id: str, instruction: str) -> bool:
    """Queue a course correction for a running agent (§9).

    Delivered to the next step that calls `ctx.redirects()`. A step already
    inside a provider call cannot see it, and a step that never asks will never
    receive it — so callers must describe this as "queued", never as applied.
    """
    text = (instruction or "").strip()
    if not text:
        return False
    with session() as s:
        ex = s.get(Execution, execution_id)
        if ex is None or ex.status in TERMINAL_STATUSES:
            return False
        workspace_id = ex.workspace_id
    d = _directive(execution_id)
    with d.lock:
        d.redirects.append(text)
    events_mod.emit(execution_id, workspace_id, "execution.progress",
                    {"stage": "redirect", "detail": text[:400]})
    return True


def control_state(execution_id: str) -> dict:
    """What the agent inspector needs that the database does not hold."""
    d = _directives.get(execution_id)
    if d is None:
        return {"paused": False, "pendingRedirects": [], "controllable": False}
    with d.lock:
        pending = list(d.redirects)
    return {"paused": not d.running.is_set(), "pendingRedirects": pending,
            "controllable": True}


def _await_resume(execution_id: str) -> None:
    """Block while paused, waking often enough to notice a cancellation."""
    d = _directives.get(execution_id)
    if d is None:
        return
    while not d.running.wait(0.5):
        _raise_if_cancelled(execution_id)


def _raise_if_cancelled(execution_id: str) -> None:
    ev = _cancels.get(execution_id)
    if ev is not None and ev.is_set():
        raise Cancelled()


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------
def get(execution_id: str, *, with_steps: bool = True) -> dict | None:
    with session() as s:
        ex = s.get(Execution, execution_id)
        if ex is None:
            return None
        out = _public(ex)
        if with_steps:
            rows = s.scalars(select(ExecutionStep)
                             .where(ExecutionStep.execution_id == execution_id)
                             .order_by(ExecutionStep.idx)).all()
            out["steps"] = [_step_public(r) for r in rows]
        return out


def list_for(workspace_id: str, *, agent: str | None = None,
             status: str | None = None, limit: int = 50) -> list[dict]:
    with session() as s:
        q = select(Execution).where(Execution.workspace_id == workspace_id)
        if agent:
            q = q.where(Execution.agent == agent)
        if status:
            q = q.where(Execution.status == status)
        rows = s.scalars(q.order_by(Execution.created_at.desc()).limit(limit)).all()
        return [_public(e) for e in rows]


def active() -> list[dict]:
    """Everything not yet terminal — the live job view PULSE reads."""
    with session() as s:
        rows = s.scalars(select(Execution)
                         .where(Execution.status.notin_(TERMINAL_STATUSES))
                         .order_by(Execution.created_at.desc())).all()
        return [_public(e) for e in rows]


def _public(ex: Execution) -> dict:
    return {
        "id": ex.id,
        "workspaceId": ex.workspace_id,
        "agent": ex.agent,
        "status": ex.status,
        "mode": ex.mode,
        "title": ex.title,
        "input": ex.input_json or {},
        "plan": ex.plan_json or {},
        "parentExecutionId": ex.parent_execution_id,
        "error": ex.error,
        "createdAt": iso(ex.created_at),
        "startedAt": iso(ex.started_at),
        "completedAt": iso(ex.completed_at),
        "durationMs": ex.duration_ms,
    }


def _step_public(r: ExecutionStep) -> dict:
    return {
        "id": r.id, "key": r.key, "idx": r.idx, "title": r.title,
        "agent": r.agent, "capability": r.capability, "status": r.status,
        "dependsOn": r.depends_on_json or [], "output": r.output_json or {},
        "retryCount": r.retry_count, "error": r.error,
        "durationMs": r.duration_ms,
    }
