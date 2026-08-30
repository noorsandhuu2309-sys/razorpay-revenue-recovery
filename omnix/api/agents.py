"""Agents as visible workers (§9).

The blueprint's requirement is not "show a spinner". It is that an agent be
inspectable — task, status, progress, model, cost, sources, findings — and
interruptible: pause, redirect, cancel. Everything needed for that already
existed in separate tables (executions, steps, events, model_call, artifact);
what was missing was one place that joins them into a worker the user can look
at and interfere with.

Two honesty rules the payloads keep:

  * **Cost is measured or absent.** `costUsd` comes from `model_call` rows the
    router wrote. An unpriced model reports 0.0 with its tokens, and
    `tokensEstimated` says when the counts were derived from character length
    rather than reported by the provider. Nothing here multiplies a guess.
  * **Control is described as it behaves.** Pause and redirect are cooperative:
    they land at the next step boundary, and `pendingRedirects` shows what has
    been queued but not yet picked up. The API never claims an instant stop.

The activity trail deliberately exposes step and tool events only — never model
prompt content. §9 asks for an audit trail without private chain-of-thought.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import func, select

from ..core import artifacts as artifacts_mod
from ..core import events as events_mod
from ..core import executions as executions_mod
from ..core import workspace as workspace_mod
from ..core.db import session
from ..core.schema import ModelCall, Source, TERMINAL_STATUSES, iso, utcnow

router = APIRouter(prefix="/api/agents", tags=["agents"])

# Event types safe to show. `model.request`/`model.response` are excluded on
# purpose: they carry prompt material, which is the reasoning trace the
# blueprint says not to expose.
_TRAIL_TYPES = (
    "execution.created", "execution.started", "execution.progress",
    "execution.completed", "execution.failed", "execution.cancelled",
    "step.started", "step.completed", "step.failed",
    "tool.started", "tool.completed", "tool.failed",
    "artifact.created", "agent.handoff",
)


def _ws(workspace: str | None) -> str:
    return workspace_mod.resolve(workspace)


def _owned(execution_id: str, **kw) -> dict | None:
    """The run, if it belongs to the caller.

    The control routes below address a run by its own id and take no
    `workspace`, so they never reach `_ws()`. Without this, any signed-in user
    could pause, redirect or cancel any other tenant's run — and `redirect`
    injects an instruction into a running agent, which is the most damaging of
    the three.
    """
    ex = executions_mod.get(execution_id, **kw)
    if ex is None:
        return None
    workspace_mod.resolve(ex["workspaceId"])
    return ex


def _usage(execution_id: str) -> dict:
    """Measured model usage for one run."""
    with session() as s:
        rows = s.scalars(select(ModelCall).where(
            ModelCall.execution_id == execution_id)).all()
    if not rows:
        return {"calls": 0, "inputTokens": 0, "outputTokens": 0,
                "costUsd": 0.0, "tokensEstimated": False, "models": [],
                "errors": 0}
    models: list[str] = []
    for r in rows:
        if r.model and r.model not in models:
            models.append(r.model)
    return {
        "calls": len(rows),
        "inputTokens": sum(r.input_tokens or 0 for r in rows),
        "outputTokens": sum(r.output_tokens or 0 for r in rows),
        "costUsd": round(sum(r.cost_usd or 0.0 for r in rows), 6),
        # One estimated call taints the total, and the UI has to say so.
        "tokensEstimated": any(r.tokens_estimated for r in rows),
        "models": models,
        "errors": sum(1 for r in rows if r.status == "error"),
        "latencyMsTotal": sum(r.latency_ms or 0 for r in rows),
    }


def _progress(execution: dict) -> dict:
    """Step counts. A fraction of steps is the only progress number that is
    real — anything finer would be a model's opinion of its own completeness."""
    steps = execution.get("steps") or []
    done = sum(1 for st in steps if st.get("status") == "completed")
    running = next((st for st in steps if st.get("status") == "running"), None)
    return {
        "steps": len(steps),
        "completed": done,
        "fraction": round(done / len(steps), 3) if steps else 0.0,
        "current": {"key": running.get("key"), "title": running.get("title"),
                    "agent": running.get("agent")} if running else None,
    }


def _worker(execution: dict, *, with_trail: bool = False) -> dict:
    eid = execution["id"]
    out = {
        **execution,
        "usage": _usage(eid),
        "progress": _progress(execution),
        "control": executions_mod.control_state(eid),
        "artifacts": artifacts_mod.list_for(execution["workspaceId"],
                                            execution_id=eid, limit=20),
    }
    with session() as s:
        out["sourceCount"] = int(s.scalar(
            select(func.count()).select_from(Source)
            .where(Source.execution_id == eid)) or 0)
    if with_trail:
        out["trail"] = [e for e in events_mod.since(eid, 0, limit=400)
                        if e["type"] in _TRAIL_TYPES]
    return out


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------
@router.get("/live")
def live(workspace: str | None = None, limit: int = 20):
    """Workers running right now, plus the last few that finished.

    Recently-finished runs are included because an agent view that empties the
    instant work completes gives the user nowhere to look at what it did.
    """
    ws = _ws(workspace)
    active = [e for e in executions_mod.active() if e["workspaceId"] == ws]
    recent = [e for e in executions_mod.list_for(ws, limit=limit)
              if e["status"] in TERMINAL_STATUSES]

    return {
        "workspace": ws,
        "active": [_worker(executions_mod.get(e["id"]) or e) for e in active],
        "recent": [_worker(executions_mod.get(e["id"]) or e) for e in recent[:limit]],
    }


@router.get("/{execution_id}")
def detail(execution_id: str):
    ex = _owned(execution_id)
    if ex is None:
        return JSONResponse({"error": "unknown agent run"}, status_code=404)
    return _worker(ex, with_trail=True)


# ---------------------------------------------------------------------------
# Control
# ---------------------------------------------------------------------------
@router.post("/{execution_id}/pause")
def pause(execution_id: str):
    if _owned(execution_id, with_steps=False) is None:
        return JSONResponse({"error": "unknown agent run"}, status_code=404)
    ok = executions_mod.pause(execution_id)
    if not ok:
        return JSONResponse({"error": "run already finished"}, status_code=409)
    return {"paused": True,
            "note": "Takes effect at the next step boundary. A step already "
                    "inside a model call finishes first."}


@router.post("/{execution_id}/resume")
def resume(execution_id: str):
    if _owned(execution_id, with_steps=False) is None:
        return JSONResponse({"error": "unknown agent run"}, status_code=404)
    ok = executions_mod.resume(execution_id)
    if not ok:
        return JSONResponse({"error": "run already finished"}, status_code=409)
    return {"resumed": True}


@router.post("/{execution_id}/redirect")
def redirect(execution_id: str, payload: dict):
    instruction = ((payload or {}).get("instruction") or "").strip()
    if not instruction:
        return JSONResponse({"error": "instruction is required"}, status_code=400)
    if _owned(execution_id, with_steps=False) is None:
        return JSONResponse({"error": "unknown agent run"}, status_code=404)
    ok = executions_mod.redirect(execution_id, instruction)
    if not ok:
        return JSONResponse({"error": "run already finished"}, status_code=409)
    return {"queued": True, "instruction": instruction,
            "note": "Queued. The agent picks it up at its next checkpoint; a "
                    "step that never checks will not receive it."}


@router.post("/{execution_id}/cancel")
def cancel(execution_id: str):
    if _owned(execution_id, with_steps=False) is None:
        return JSONResponse({"error": "unknown agent run"}, status_code=404)
    ok = executions_mod.cancel(execution_id)
    if not ok:
        return JSONResponse({"error": "run already finished"}, status_code=409)
    return {"cancelling": True,
            "note": "Cancellation is cooperative — the current step finishes."}


@router.get("/{execution_id}/events")
def trail(execution_id: str, after: int = 0):
    """The audit trail. Model request/response events are filtered out."""
    if _owned(execution_id, with_steps=False) is None:
        return JSONResponse({"error": "unknown agent run"}, status_code=404)
    if events_mod.status_of(execution_id) is None:
        return JSONResponse({"error": "unknown agent run"}, status_code=404)
    return {"events": [e for e in events_mod.since(execution_id, after, limit=400)
                       if e["type"] in _TRAIL_TYPES],
            "status": events_mod.status_of(execution_id),
            "at": iso(utcnow())}
