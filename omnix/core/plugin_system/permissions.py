"""Who is allowed to do what, and a record of every time it mattered.

THE RULE THAT MAKES THIS WORTH HAVING
-------------------------------------
A manifest declares what a plugin *wants*. It never decides what it *gets*.
Otherwise the permission system is a comment: any plugin that can write its own
manifest can grant itself `process.execute`, and the prompt in §5 becomes
decoration.

So high-risk permissions require an explicit decision recorded against the
plugin. Low-risk ones (`network.read`, `filesystem.read`) are granted on enable,
because prompting for those trains users to click Allow without reading — which
costs more safety than it buys.

DENIAL IS AN ANSWER, NOT AN ERROR
---------------------------------
A denied permission raises `PermissionDenied`, which the plugin surfaces as an
`Unavailable` result naming the permission. It does not crash OMNIX (§2), and
it does not silently return nothing (§90).
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .manifest import HIGH_RISK, PERMISSIONS
from ...persistence import save_json

_ROOT = Path(__file__).resolve().parents[3]
STORE = _ROOT / "omnix_permissions.json"

_lock = threading.RLock()


class PermissionDenied(PermissionError):
    def __init__(self, plugin_id: str, permission: str):
        super().__init__(
            f"{plugin_id} does not have permission '{permission}'")
        self.plugin_id = plugin_id
        self.permission = permission


@dataclass(frozen=True)
class Decision:
    plugin_id: str
    permission: str
    granted: bool
    scope: str          # "once" | "always"
    at: float
    actor: str = ""     # the email that decided, when known


def _load() -> dict:
    if not STORE.exists():
        return {"grants": {}, "audit": []}
    try:
        data = json.loads(STORE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        # A corrupt permission store must fail CLOSED. Rebuilding it empty
        # revokes everything, which is recoverable; trusting a half-parsed file
        # is not.
        return {"grants": {}, "audit": []}
    data.setdefault("grants", {})
    data.setdefault("audit", [])
    return data


def _save(data: dict) -> None:
    # Atomic, and this one matters more than most: `_load` deliberately fails
    # CLOSED on a corrupt store, so a half-written file does not just lose the
    # save — it revokes every permission the user has granted.
    save_json(STORE, data)


def granted(plugin_id: str, permission: str) -> bool:
    with _lock:
        return permission in (_load()["grants"].get(plugin_id) or [])


def grant(plugin_id: str, permission: str, *, actor: str = "",
          scope: str = "always") -> None:
    """Record an explicit grant. Only ever called from a user decision."""
    if permission not in PERMISSIONS:
        raise ValueError(f"unknown permission: {permission}")
    with _lock:
        data = _load()
        held = set(data["grants"].get(plugin_id) or [])
        held.add(permission)
        data["grants"][plugin_id] = sorted(held)
        data["audit"].append({
            "plugin": plugin_id, "permission": permission, "granted": True,
            "scope": scope, "at": time.time(), "actor": actor})
        _save(data)


def revoke(plugin_id: str, permission: str, *, actor: str = "") -> None:
    with _lock:
        data = _load()
        held = set(data["grants"].get(plugin_id) or [])
        held.discard(permission)
        data["grants"][plugin_id] = sorted(held)
        data["audit"].append({
            "plugin": plugin_id, "permission": permission, "granted": False,
            "scope": "revoked", "at": time.time(), "actor": actor})
        _save(data)


def grant_low_risk(plugin_id: str, permissions: tuple[str, ...], *,
                   actor: str = "") -> tuple[str, ...]:
    """Grant everything in `permissions` that is not high-risk.

    Called when a plugin is enabled. Returns the high-risk permissions that
    were *not* granted, so the caller can prompt for them — a plugin enabled
    with an ungranted high-risk permission is enabled and partly inert, which
    is the correct state, not an error.
    """
    pending = []
    for p in permissions:
        if p in HIGH_RISK:
            pending.append(p)
        else:
            grant(plugin_id, p, actor=actor, scope="on-enable")
    return tuple(pending)


def require(plugin_id: str, permission: str) -> None:
    """Raise :class:`PermissionDenied` unless this plugin holds `permission`."""
    if not granted(plugin_id, permission):
        with _lock:
            data = _load()
            data["audit"].append({
                "plugin": plugin_id, "permission": permission,
                "granted": False, "scope": "denied-at-call", "at": time.time()})
            _save(data)
        raise PermissionDenied(plugin_id, permission)


def held(plugin_id: str) -> tuple[str, ...]:
    with _lock:
        return tuple(_load()["grants"].get(plugin_id) or [])


def audit(limit: int = 200) -> list[dict]:
    """The trail (§66). Never contains secret values — only permission names."""
    with _lock:
        return list(reversed(_load()["audit"][-limit:]))


def describe(permission: str) -> dict:
    """What to show in the prompt (§5)."""
    return {
        "permission": permission,
        "risk": "HIGH" if permission in HIGH_RISK else "LOW",
        "known": permission in PERMISSIONS,
    }
