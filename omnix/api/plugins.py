"""The plugin manager's HTTP surface (§53, §78, §44).

Read routes are open to any signed-in user. Write routes — enable, disable,
grant, revoke — are not: a permission grant is exactly the kind of action §5
wants an explicit decision behind, and "the request reached the server" is not
a decision. They are admin-only, and on a single-user install the local account
is the admin.

Secret VALUES never appear in any response here. `secrets.missing` names the
environment variables that are unset, which is what a settings screen needs in
order to prompt, and is also all it needs.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ..core import workspace as workspace_mod
from ..core.db import session
from ..core.plugin_system import Status
from ..core.plugin_system import permissions as perms
from ..core.plugin_system.registry import shared
from ..core.schema import User

router = APIRouter(prefix="/api/plugins", tags=["plugins"])


def _is_admin() -> bool:
    with session() as s:
        u = s.get(User, workspace_mod.acting_user())
        return bool(u and (u.is_admin or u.email == workspace_mod.LOCAL_EMAIL))


def _forbidden():
    return JSONResponse(
        {"error": "Only an administrator can change plugin settings."},
        status_code=403)


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------
@router.get("")
def list_plugins():
    return shared().report()


@router.get("/tools")
def list_tools():
    """What the orchestrator can actually call right now."""
    return {"tools": shared().tools()}


@router.get("/health")
def health():
    """§44. One row per plugin, with what would fix a non-OK state."""
    rows = []
    for p in shared().all():
        h = p.health
        rows.append({
            "id": p.id, "name": p.manifest.name,
            "status": h.status.value, "detail": h.detail,
            "fixKey": h.fix_key, "docsUrl": h.docs_url,
            "calls": h.calls, "errors": h.errors, "errorRate": h.error_rate,
            "latencyMs": h.latency_ms_last, "activeSource": h.active_source,
            "lastSuccess": h.last_success, "lastFailure": h.last_failure,
            "missingSecrets": list(p.missing_secrets()),
        })
    return {"plugins": rows, "broken": shared().broken()}


@router.get("/audit")
def permission_audit(limit: int = 100):
    return {"audit": perms.audit(limit=max(1, min(500, limit)))}


@router.get("/{plugin_id}")
def get_plugin(plugin_id: str):
    p = shared().get(plugin_id)
    if p is None:
        return JSONResponse({"error": "unknown plugin"}, status_code=404)
    return p.describe()


# ---------------------------------------------------------------------------
# Control
# ---------------------------------------------------------------------------
@router.post("/{plugin_id}/enable")
def enable_plugin(plugin_id: str):
    if not _is_admin():
        return _forbidden()
    try:
        health = shared().enable(plugin_id)
    except KeyError:
        return JSONResponse({"error": "unknown plugin"}, status_code=404)
    return {"id": plugin_id, "health": health.to_dict()}


@router.post("/{plugin_id}/disable")
def disable_plugin(plugin_id: str):
    if not _is_admin():
        return _forbidden()
    try:
        health = shared().disable(plugin_id)
    except KeyError:
        return JSONResponse({"error": "unknown plugin"}, status_code=404)
    return {"id": plugin_id, "health": health.to_dict()}


@router.post("/{plugin_id}/permissions/grant")
def grant_permission(plugin_id: str, payload: dict):
    """Body: { permission }

    Only permissions the plugin's own manifest declares can be granted.
    Otherwise this route is a way to give any plugin any capability, which
    would defeat the point of declaring them.
    """
    if not _is_admin():
        return _forbidden()
    p = shared().get(plugin_id)
    if p is None:
        return JSONResponse({"error": "unknown plugin"}, status_code=404)

    permission = ((payload or {}).get("permission") or "").strip()
    if permission not in p.manifest.permissions:
        return JSONResponse(
            {"error": f"{p.manifest.name} does not declare '{permission}'."},
            status_code=400)

    with session() as s:
        actor = getattr(s.get(User, workspace_mod.acting_user()), "email", "")
    perms.grant(plugin_id, permission, actor=actor, scope="explicit")

    # Re-probe: a plugin that was DEGRADED only because it was waiting for this
    # permission should come back without the user having to toggle it.
    if p.health.status is Status.DEGRADED:
        p.enable(actor=actor)
    return {"id": plugin_id, "granted": permission,
            "held": list(perms.held(plugin_id))}


@router.post("/{plugin_id}/permissions/revoke")
def revoke_permission(plugin_id: str, payload: dict):
    if not _is_admin():
        return _forbidden()
    if shared().get(plugin_id) is None:
        return JSONResponse({"error": "unknown plugin"}, status_code=404)
    permission = ((payload or {}).get("permission") or "").strip()
    with session() as s:
        actor = getattr(s.get(User, workspace_mod.acting_user()), "email", "")
    perms.revoke(plugin_id, permission, actor=actor)
    return {"id": plugin_id, "revoked": permission,
            "held": list(perms.held(plugin_id))}


# ---------------------------------------------------------------------------
# Invocation
# ---------------------------------------------------------------------------
@router.post("/{plugin_id}/tools/{tool}")
def call_tool(plugin_id: str, tool: str, payload: dict | None = None):
    """Invoke one tool. Always 200 with an envelope.

    A plugin being unconfigured or degraded is not an HTTP error — it is an
    answer, and it carries the key that fixes it. Returning 5xx would make the
    client's error handler swallow the one piece of actionable information.
    """
    result = shared().call(f"{plugin_id}.{tool}", **(payload or {}))
    return result.to_dict()
