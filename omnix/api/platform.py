"""Platform routes: workspaces, artifacts, executions, events, usage.

These are the routes the new frontend talks to. They are agent-agnostic on
purpose — an artifact is fetched the same way whoever produced it, and an
execution is followed the same way whatever it is running. That uniformity is
what lets the UI have one execution drawer and one artifact viewer instead of
five of each.
"""

from __future__ import annotations

import json

from fastapi import APIRouter
from fastapi.responses import JSONResponse, StreamingResponse

from ..agents_v2 import adapter
from ..core import artifacts as artifacts_mod
from ..core import entitlements
from ..core import events as events_mod
from ..core import executions as executions_mod
from ..core import workspace as workspace_mod

router = APIRouter(prefix="/api", tags=["platform"])


# ---------------------------------------------------------------------------
# Ownership guards
# ---------------------------------------------------------------------------
# Artifacts and executions are addressed by their OWN id, so they never pass a
# `workspace` parameter and never reach `workspace.resolve()` — the chokepoint
# that protects the sixty routes which do. Each therefore has to be checked
# against the Space it belongs to.
#
# Both helpers return None for "no such thing" and raise WorkspaceAccessError
# (rendered as a flat 404 by the server) for "not yours", which the caller then
# reports identically. An attacker learns the same thing either way: nothing.
def _owned_execution(execution_id: str, **kw) -> dict | None:
    ex = executions_mod.get(execution_id, **kw)
    if ex is None:
        return None
    workspace_mod.resolve(ex["workspaceId"])
    return ex


def _owned_artifact(artifact_id: str, **kw) -> dict | None:
    a = artifacts_mod.get(artifact_id, **kw)
    if a is None:
        return None
    workspace_mod.resolve(a["workspaceId"])
    return a


# ---------------------------------------------------------------------------
# Workspaces
# ---------------------------------------------------------------------------
@router.get("/workspaces")
def list_workspaces():
    uid = workspace_mod.acting_user()
    items = workspace_mod.list_for(uid)
    if not items:
        workspace_mod.default_workspace()
        items = workspace_mod.list_for(uid)
    return {"workspaces": items}


@router.post("/workspaces")
def create_workspace(payload: dict):
    name = (payload or {}).get("name") or ""
    if not name.strip():
        return JSONResponse({"error": "name is required"}, status_code=400)
    uid = workspace_mod.acting_user()
    entitlements.check_new_space(uid)
    return workspace_mod.create(uid, name, (payload or {}).get("description", ""))


# The three routes below take the id in the PATH rather than as the `workspace`
# query parameter, so they never reach `_ws()`/`resolve()` and have to check
# ownership for themselves. `resolve` is called for its side effect — it raises
# WorkspaceAccessError, which the server renders as a flat 404.
@router.get("/workspaces/{workspace_id}")
def get_workspace(workspace_id: str):
    workspace_mod.resolve(workspace_id)
    ws = workspace_mod.get(workspace_id)
    if ws is None:
        return JSONResponse({"error": "unknown workspace"}, status_code=404)
    return ws


@router.patch("/workspaces/{workspace_id}")
def update_workspace(workspace_id: str, payload: dict):
    workspace_mod.resolve(workspace_id)
    ws = workspace_mod.update(workspace_id, **(payload or {}))
    if ws is None:
        return JSONResponse({"error": "unknown workspace"}, status_code=404)
    return ws


@router.delete("/workspaces/{workspace_id}")
def delete_workspace(workspace_id: str):
    workspace_mod.resolve(workspace_id)
    return {"deleted": workspace_mod.delete(workspace_id)}


# ---------------------------------------------------------------------------
# Artifacts
# ---------------------------------------------------------------------------
@router.get("/workspaces/{workspace_id}/artifacts")
def list_artifacts(workspace_id: str, type: str | None = None,
                   execution_id: str | None = None, limit: int = 100):
    workspace_mod.resolve(workspace_id)
    return {"artifacts": artifacts_mod.list_for(
        workspace_id, type=type, execution_id=execution_id,
        limit=max(1, min(500, limit)))}


@router.get("/artifacts/{artifact_id}")
def get_artifact(artifact_id: str):
    a = _owned_artifact(artifact_id)
    if a is None:
        return JSONResponse({"error": "unknown artifact"}, status_code=404)
    return a


@router.get("/artifacts/{artifact_id}/lineage")
def artifact_lineage(artifact_id: str):
    if _owned_artifact(artifact_id, with_content=False) is None:
        return JSONResponse({"error": "unknown artifact"}, status_code=404)
    return artifacts_mod.lineage(artifact_id)


@router.delete("/artifacts/{artifact_id}")
def delete_artifact(artifact_id: str):
    if _owned_artifact(artifact_id, with_content=False) is None:
        return JSONResponse({"error": "unknown artifact"}, status_code=404)
    return {"deleted": artifacts_mod.delete(artifact_id)}


# ---------------------------------------------------------------------------
# Executions
# ---------------------------------------------------------------------------
@router.get("/executions")
def list_executions(workspace_id: str | None = None, agent: str | None = None,
                    status: str | None = None, limit: int = 50):
    ws = workspace_mod.resolve(workspace_id)
    return {"executions": executions_mod.list_for(
        ws, agent=agent, status=status, limit=max(1, min(200, limit)))}


@router.get("/executions/active")
def active_executions():
    """Only the caller's runs.

    `executions_mod.active()` is process-wide — it powers the running-jobs
    indicator, and unfiltered it would show one tenant the titles and agents of
    everyone else's work.
    """
    mine = {w["id"] for w in workspace_mod.list_for(workspace_mod.acting_user())}
    return {"executions": [e for e in executions_mod.active()
                           if e.get("workspaceId") in mine]}


@router.get("/executions/{execution_id}")
def get_execution(execution_id: str):
    ex = _owned_execution(execution_id)
    if ex is None:
        return JSONResponse({"error": "unknown execution"}, status_code=404)
    ex["artifacts"] = artifacts_mod.list_for(ex["workspaceId"],
                                             execution_id=execution_id)
    return ex


@router.post("/executions/{execution_id}/cancel")
def cancel_execution(execution_id: str):
    if _owned_execution(execution_id, with_steps=False) is None:
        return JSONResponse({"error": "unknown execution"}, status_code=404)
    requested = executions_mod.cancel(execution_id)
    # Deliberately not "cancelled": the runner decides that when it observes
    # the flag. Saying otherwise would be a status the backend cannot honour.
    return {"cancelRequested": requested}


@router.get("/executions/{execution_id}/events")
def execution_events(execution_id: str, after: int = 0):
    """SSE. Replays from `after` then follows until the run is terminal."""
    if _owned_execution(execution_id, with_steps=False) is None:
        return JSONResponse({"error": "unknown execution"}, status_code=404)

    def gen():
        for ev in events_mod.stream(execution_id, after_seq=after):
            yield f"data: {json.dumps(ev)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store",
                                      "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------------------
# Running agents through the platform
# ---------------------------------------------------------------------------
@router.post("/agents/{code}/run")
def run_agent(code: str, payload: dict):
    """Body: { input, workspace_id?, mode?, ...unit options }

    The platform-aware sibling of /api/squad/{code}/run. Same units, but the
    run is recorded as an execution in a workspace and its result becomes an
    artifact.
    """
    from ..squad import units as squad_units

    unit = squad_units.get_unit(code)
    if unit is None:
        return JSONResponse({"error": f"unknown agent: {code}"}, status_code=404)

    ctx = dict(payload or {})
    ctx["input"] = (ctx.get("input") or "").strip()
    ctx.pop("image_path", None)   # never trust a client-supplied local path
    # Resolved rather than passed through: a client-supplied workspace_id is an
    # arbitrary string until ownership says otherwise, and this route bills a
    # model call against whatever Space it names.
    workspace_id = workspace_mod.resolve(ctx.pop("workspace_id", None))
    mode = ctx.pop("mode", "auto")
    if unit.needs_input and not ctx["input"]:
        return JSONResponse({"error": "this agent requires input"}, status_code=400)

    execution_id = adapter.run_unit(unit, ctx, workspace_id=workspace_id, mode=mode)
    return {"executionId": execution_id,
            "agent": code,
            "deprecated": code in adapter.DEPRECATED_AGENTS}


@router.post("/executions/{execution_id}/handoff")
def handoff(execution_id: str, payload: dict):
    """Body: { to: <agent code>, input?: str }

    Launches another agent on this run's artifacts — the cross-agent workflow.
    """
    from ..squad import units as squad_units

    to = ((payload or {}).get("to") or "").lower()
    unit = squad_units.get_unit(to)
    if unit is None:
        return JSONResponse({"error": f"unknown agent: {to}"}, status_code=404)
    src = _owned_execution(execution_id, with_steps=False)
    if src is None:
        return JSONResponse({"error": "unknown execution"}, status_code=404)
    if src["status"] not in ("completed",):
        return JSONResponse(
            {"error": f"source execution is {src['status']}, not completed"},
            status_code=409)
    new_id = adapter.handoff(execution_id, unit,
                             input=(payload or {}).get("input", ""))
    return {"executionId": new_id, "agent": to, "from": execution_id}


# ---------------------------------------------------------------------------
# Entitlements — what this account may do, and what it has used
# ---------------------------------------------------------------------------
@router.get("/entitlements")
def entitlements_report():
    """The paywall's data source.

    Deliberately readable before anything is refused: a plan screen that can
    only tell you what you have spent *after* it blocks you is how a user
    discovers a limit by hitting it.
    """
    return entitlements.report(workspace_mod.acting_user())


# ---------------------------------------------------------------------------
# Usage — the data PULSE will render
# ---------------------------------------------------------------------------
@router.get("/usage")
def usage(workspace_id: str | None = None, days: int = 7):
    """Real token and cost totals. Never estimated silently: `estimatedCalls`
    reports how many rows had token counts derived from character length rather
    than reported by the provider, so the UI can label them."""
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import func, select

    from ..core.db import session
    from ..core.schema import ModelCall

    ws = workspace_mod.resolve(workspace_id)
    # Naive UTC on purpose: SQLite stores these columns without an offset, so a
    # tz-aware bound value would be compared against naive strings and silently
    # shift the window by the server's UTC offset.
    since = (datetime.now(timezone.utc)
             - timedelta(days=max(1, min(90, days)))).replace(tzinfo=None)

    with session() as s:
        base = select(ModelCall).where(ModelCall.workspace_id == ws,
                                       ModelCall.ts >= since)
        rows = s.scalars(base).all()

    by_model: dict[str, dict] = {}
    by_agent: dict[str, dict] = {}
    totals = {"calls": 0, "errors": 0, "inputTokens": 0, "outputTokens": 0,
              "costUsd": 0.0, "estimatedCalls": 0}
    for r in rows:
        totals["calls"] += 1
        totals["errors"] += 1 if r.status == "error" else 0
        totals["inputTokens"] += r.input_tokens
        totals["outputTokens"] += r.output_tokens
        totals["costUsd"] += r.cost_usd
        totals["estimatedCalls"] += 1 if r.tokens_estimated else 0
        for bucket, key in ((by_model, r.model), (by_agent, r.agent or "—")):
            e = bucket.setdefault(key, {"calls": 0, "errors": 0, "tokens": 0,
                                        "costUsd": 0.0, "latencyMs": 0})
            e["calls"] += 1
            e["errors"] += 1 if r.status == "error" else 0
            e["tokens"] += r.input_tokens + r.output_tokens
            e["costUsd"] += r.cost_usd
            e["latencyMs"] += r.latency_ms

    for bucket in (by_model, by_agent):
        for e in bucket.values():
            e["avgLatencyMs"] = round(e["latencyMs"] / e["calls"]) if e["calls"] else 0
            e.pop("latencyMs")
            e["costUsd"] = round(e["costUsd"], 6)

    totals["costUsd"] = round(totals["costUsd"], 6)
    return {"workspaceId": ws, "days": days, "totals": totals,
            "byModel": by_model, "byAgent": by_agent}


@router.get("/models/capabilities")
def model_capabilities():
    """What the router would pick right now, plus provider health."""
    from ..models.capabilities import describe
    from ..models.providers import health_all
    return {"capabilities": describe(), "providers": health_all()}
