"""Geofences: watched areas, transition detection, and the events they raise.

The architecture from the brief — GPS -> location engine -> geofence engine ->
event -> agent -> action — lands here as `evaluate()`, which is called with a
position and returns the transitions that just happened.

Two design points carry the weight.

**Transitions, not states.** The `inside` column stores what was true last
time, so crossing a boundary fires exactly once. Evaluating containment alone
would re-fire "you have arrived at college" every few seconds for the whole
day, which is not a notification system, it is an alarm clock that will not
stop.

**Events are raised, never executed.** A fence's `action` is a string of
intent. It is written to `geofence_event` and the agent layer decides what to
do with it. Nothing in this module runs a command, and nothing downstream
should treat that string as one — a location trigger that executes arbitrary
text would turn walking past a building into code execution.
"""

from __future__ import annotations

from datetime import datetime, timezone

from .. import spatial
from ..types import Coord


def create(workspace_id: str, label: str, *, coord: Coord | None = None,
           radius_m: float = 200.0, polygon: list[list[float]] | None = None,
           trigger: str = "both", action: str = "notify",
           payload: dict | None = None) -> dict | None:
    """Define a fence. Circle (coord + radius) or polygon, not both.

    The radius floor is 50m and not negotiable: consumer GPS error routinely
    exceeds that, so a 20m fence would fire and unfire repeatedly while
    standing still — the classic geofence failure, and it looks like a haunted
    app rather than a bad radius.
    """
    shape = "polygon" if polygon else "circle"
    if shape == "circle" and coord is None:
        return None
    if shape == "polygon" and len(polygon or []) < 3:
        return None
    try:
        from ....core import db
        from ....core.schema import Geofence
        with db.session() as s:
            row = Geofence(
                workspace_id=workspace_id, label=label.strip()[:120],
                shape=shape,
                lat=coord.lat if coord else _centroid(polygon)[0],
                lon=coord.lon if coord else _centroid(polygon)[1],
                radius_m=max(50.0, min(float(radius_m), 50_000.0)),
                polygon_json=polygon or [],
                trigger=trigger if trigger in ("enter", "exit", "both") else "both",
                action=action, action_payload_json=payload or {},
            )
            s.add(row)
            s.flush()
            return _row(row)
    except Exception:
        return None


def delete(workspace_id: str, fence_id: str) -> bool:
    try:
        from ....core import db
        from ....core.schema import Geofence, GeofenceEvent
        with db.session() as s:
            (s.query(GeofenceEvent)
             .filter(GeofenceEvent.geofence_id == fence_id)
             .delete(synchronize_session=False))
            return bool(s.query(Geofence)
                        .filter(Geofence.workspace_id == workspace_id,
                                Geofence.id == fence_id)
                        .delete(synchronize_session=False))
    except Exception:
        return False


def set_active(workspace_id: str, fence_id: str, active: bool) -> bool:
    try:
        from ....core import db
        from ....core.schema import Geofence
        with db.session() as s:
            row = (s.query(Geofence)
                   .filter(Geofence.workspace_id == workspace_id,
                           Geofence.id == fence_id).one_or_none())
            if row is None:
                return False
            row.active = active
            # Clear the remembered state when disabling, so re-enabling a fence
            # the user is standing inside does not immediately fire an "exit"
            # the moment they leave a place they never re-entered.
            if not active:
                row.inside = False
            return True
    except Exception:
        return False


def fences(workspace_id: str, active_only: bool = False) -> list[dict]:
    try:
        from ....core import db
        from ....core.schema import Geofence
        with db.session() as s:
            q = s.query(Geofence).filter(Geofence.workspace_id == workspace_id)
            if active_only:
                q = q.filter(Geofence.active.is_(True))
            return [_row(r) for r in q.order_by(Geofence.created_at.desc()).all()]
    except Exception:
        return []


def evaluate(workspace_id: str, coord: Coord,
             accuracy_m: float | None = None) -> list[dict]:
    """Check a position against every active fence; return transitions fired.

    `accuracy_m` widens the fence rather than being ignored. A 200m fence
    checked against a fix accurate to ±150m is a coin toss, so the effective
    radius grows with the uncertainty — TERRA would rather report an arrival a
    little early than flap between states.
    """
    fired: list[dict] = []
    now = datetime.now(timezone.utc)
    slack = min(float(accuracy_m or 0.0), 200.0)

    try:
        from ....core import db
        from ....core.schema import Geofence, GeofenceEvent
        with db.session() as s:
            rows = (s.query(Geofence)
                    .filter(Geofence.workspace_id == workspace_id,
                            Geofence.active.is_(True)).all())
            for row in rows:
                if row.shape == "polygon" and row.polygon_json:
                    polygon = [Coord(p[0], p[1]) for p in row.polygon_json]
                    now_inside = spatial.inside_polygon(coord, polygon)
                else:
                    now_inside = spatial.inside_circle(
                        coord, Coord(row.lat, row.lon), row.radius_m + slack)

                was_inside = bool(row.inside)
                if now_inside == was_inside:
                    continue

                transition = "enter" if now_inside else "exit"
                row.inside = now_inside
                if row.trigger not in ("both", transition):
                    # State still updates — otherwise an exit-only fence would
                    # never see the enter that makes the next exit detectable.
                    continue

                row.last_event_at = now
                event = GeofenceEvent(
                    workspace_id=workspace_id, geofence_id=row.id,
                    transition=transition, lat=coord.lat, lon=coord.lon,
                    label=row.label,
                )
                s.add(event)
                s.flush()
                fired.append({
                    "id": event.id, "geofenceId": row.id, "label": row.label,
                    "transition": transition, "action": row.action,
                    "payload": row.action_payload_json or {},
                    "lat": coord.lat, "lon": coord.lon,
                })
    except Exception:
        return fired
    return fired


def events(workspace_id: str, limit: int = 30,
           undispatched_only: bool = False) -> list[dict]:
    try:
        from ....core import db
        from ....core.schema import GeofenceEvent, iso
        with db.session() as s:
            q = (s.query(GeofenceEvent)
                 .filter(GeofenceEvent.workspace_id == workspace_id))
            if undispatched_only:
                q = q.filter(GeofenceEvent.dispatched.is_(False))
            rows = q.order_by(GeofenceEvent.created_at.desc()).limit(limit).all()
            return [{
                "id": r.id, "geofenceId": r.geofence_id, "label": r.label,
                "transition": r.transition, "lat": r.lat, "lon": r.lon,
                "dispatched": r.dispatched, "createdAt": iso(r.created_at),
            } for r in rows]
    except Exception:
        return []


def mark_dispatched(workspace_id: str, event_ids: list[str]) -> int:
    """Flag events as handed to the agent layer, so they are not acted on
    twice. The agent layer calls this after it has done something."""
    if not event_ids:
        return 0
    try:
        from ....core import db
        from ....core.schema import GeofenceEvent
        with db.session() as s:
            rows = (s.query(GeofenceEvent)
                    .filter(GeofenceEvent.workspace_id == workspace_id,
                            GeofenceEvent.id.in_(event_ids)).all())
            for r in rows:
                r.dispatched = True
            return len(rows)
    except Exception:
        return 0


def route_crossings(workspace_id: str, geometry: list[Coord]) -> list[dict]:
    """Which fences a planned route passes through.

    Answers "will this route take me past the pharmacy" before setting off,
    which is the useful half of geofencing that does not require tracking
    anyone. Uses segment distance, so a fence between two distant route
    vertices is still detected — see `spatial.route_intersects_circle`.
    """
    if not geometry:
        return []
    out: list[dict] = []
    for fence in fences(workspace_id, active_only=True):
        if fence["shape"] == "polygon" and fence["polygon"]:
            polygon = [Coord(p[0], p[1]) for p in fence["polygon"]]
            hit = spatial.route_intersects_polygon(geometry, polygon)
        else:
            hit = spatial.route_intersects_circle(
                geometry, Coord(fence["lat"], fence["lon"]), fence["radiusM"])
        if hit:
            out.append(fence)
    return out


def _centroid(polygon: list[list[float]]) -> tuple[float, float]:
    """Average vertex position — used only to give a polygon fence a point to
    render a label at, never for containment."""
    if not polygon:
        return (0.0, 0.0)
    return (sum(p[0] for p in polygon) / len(polygon),
            sum(p[1] for p in polygon) / len(polygon))


def _row(row) -> dict:
    from ....core.schema import iso
    return {
        "id": row.id, "label": row.label, "shape": row.shape,
        "lat": row.lat, "lon": row.lon, "radiusM": row.radius_m,
        "polygon": row.polygon_json or [], "trigger": row.trigger,
        "action": row.action, "payload": row.action_payload_json or {},
        "active": row.active, "inside": row.inside,
        "lastEventAt": iso(row.last_event_at), "createdAt": iso(row.created_at),
    }


__all__ = ["create", "delete", "set_active", "fences", "evaluate", "events",
           "mark_dispatched", "route_crossings"]
