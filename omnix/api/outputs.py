"""Create-as-output-action routes (§12) and the Intents API (§11).

Both live here because they are the two halves of the same idea: an Intent is
something the user wants pursued over time, an output is something produced from
what the workspace found. Neither is a chat message.

Route order matters — `/outputs/styles` is declared before `/outputs/{id}` so
the literal wins over the parameter.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse, Response

from ..core import artifacts as artifacts_mod
from ..core import intents as intents_mod
from ..core import outputs as outputs_mod
from ..core import workspace as workspace_mod

router = APIRouter(prefix="/api", tags=["outputs"])


def _ws(workspace: str | None) -> str:
    return workspace_mod.resolve(workspace)


def _owned_output(artifact_id: str) -> dict | None:
    """The stored output, if it belongs to the caller.

    These two routes address an artifact by its own id and take no
    `workspace`, so they bypass `_ws()`. `render_output` returns the finished
    document — a brief with its sources and citation appendix — which makes it
    the single most valuable thing in the database to read without permission.
    """
    art = artifacts_mod.get(artifact_id)
    if art is None:
        return None
    workspace_mod.resolve(art["workspaceId"])
    return art


# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------
@router.get("/outputs/styles")
def output_styles():
    """What Create can produce. The UI builds its menu from this rather than
    hard-coding a list that drifts from what the backend implements."""
    return {"styles": outputs_mod.styles()}


@router.get("/outputs")
def list_outputs(workspace: str | None = None, style: str | None = None,
                 limit: int = 100):
    ws = _ws(workspace)
    return {"workspace": ws,
            "outputs": outputs_mod.list_outputs(ws, style=style, limit=limit)}


@router.post("/outputs")
def create_output(payload: dict):
    """Produce an output from held objects.

    `objectIds` is the selection — §6's "selection becomes context" applied to
    creation, which is why there is no separate prompt field for describing what
    to write about.
    """
    p = payload or {}
    ws = _ws(p.get("workspace"))
    style = (p.get("style") or "").strip()
    ids = [i for i in (p.get("objectIds") or []) if i]
    try:
        art = outputs_mod.create(
            ws, style, ids, title=(p.get("title") or "").strip(),
            question=(p.get("question") or "").strip(),
            execution_id=p.get("executionId"))
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return {"workspace": ws, "output": art}


@router.get("/outputs/{artifact_id}")
def get_output(artifact_id: str):
    art = _owned_output(artifact_id)
    if art is None:
        return JSONResponse({"error": "unknown output"}, status_code=404)
    art["lineage"] = artifacts_mod.lineage(artifact_id)
    return art


@router.get("/outputs/{artifact_id}/render")
def render_output(artifact_id: str, format: str = "md", download: bool = False):
    """Render a stored output. `download=true` sets a filename disposition."""
    art = _owned_output(artifact_id)
    if art is None:
        return JSONResponse({"error": "unknown output"}, status_code=404)
    style = ((art.get("content") or {}).get("style") or "")
    allowed = outputs_mod.STYLES.get(style)
    fmt = (format or "md").lower()
    if allowed and fmt not in allowed.formats:
        return JSONResponse(
            {"error": f"{allowed.label} cannot render as {fmt}",
             "formats": list(allowed.formats)}, status_code=400)
    body, media, filename = outputs_mod.render(art, fmt)
    headers = {}
    if download:
        headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return Response(content=body, media_type=media, headers=headers)


# ---------------------------------------------------------------------------
# Intents
# ---------------------------------------------------------------------------
@router.get("/intents")
def list_intents(workspace: str | None = None, status: str | None = None):
    ws = _ws(workspace)
    return {"workspace": ws, "intents": intents_mod.list_intents(ws, status=status)}


@router.post("/intents")
def create_intent(payload: dict):
    p = payload or {}
    ws = _ws(p.get("workspace"))
    title = (p.get("title") or "").strip()
    if not title:
        return JSONResponse({"error": "title is required"}, status_code=400)
    try:
        intent = intents_mod.create(
            ws, title,
            description=(p.get("description") or "").strip(),
            object_ids=p.get("objectIds") or [],
            keywords=p.get("keywords") or [],
            relevance_floor=(p.get("relevanceFloor") or "medium"),
            cadence_minutes=int(p.get("cadenceMinutes") or 60))
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return {"workspace": ws, "intent": intent}


@router.get("/intents/{intent_id}")
def get_intent(intent_id: str, workspace: str | None = None):
    intent = intents_mod.get(_ws(workspace), intent_id)
    if intent is None:
        return JSONResponse({"error": "unknown intent"}, status_code=404)
    return intent


@router.patch("/intents/{intent_id}")
def update_intent(intent_id: str, payload: dict, workspace: str | None = None):
    ws = _ws(workspace)
    intent = intents_mod.update(ws, intent_id, **(payload or {}))
    if intent is None:
        return JSONResponse({"error": "unknown intent"}, status_code=404)
    return intent


@router.delete("/intents/{intent_id}")
def delete_intent(intent_id: str, workspace: str | None = None):
    return {"deleted": intents_mod.delete(_ws(workspace), intent_id)}


@router.post("/intents/{intent_id}/check")
def check_intent(intent_id: str, workspace: str | None = None):
    """Evaluate one Intent now. Returns what actually fired, which may be
    nothing — an Intent that reports a hit every time it is polled is noise."""
    ws = _ws(workspace)
    out = intents_mod.evaluate(ws, intent_id)
    if out is None:
        return JSONResponse({"error": "unknown intent"}, status_code=404)
    return out


@router.get("/intents/{intent_id}/hits")
def intent_hits(intent_id: str, workspace: str | None = None, limit: int = 100):
    ws = _ws(workspace)
    return {"hits": intents_mod.hits(ws, intent_id, limit=limit)}


@router.post("/intents/check")
def check_all(payload: dict | None = None):
    ws = _ws((payload or {}).get("workspace"))
    return intents_mod.evaluate_workspace(ws)
