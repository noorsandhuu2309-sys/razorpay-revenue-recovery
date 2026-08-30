"""Spatial memory: saved places, visit history, and the controls over both.

The brief calls this extremely important, and immediately adds "do not blindly
store sensitive location history forever". Those two pull in opposite
directions, so the resolution is made structural rather than left to policy:

  * **Saved places and visit history are different things.** A saved place is
    data the user typed — a name and a point. Visit history is surveillance
    they merely permitted. They live in separate tables with separate switches,
    and `privacy_mode` disables the second while leaving the first working, so
    "take me to college" still functions with tracking entirely off.

  * **Retention is enforced on write, not by a sweeper.** `_prune` runs inside
    `observe`, so history older than the retention window is deleted as a
    matter of course rather than depending on a background job that might not
    be running. A retention policy that only holds while a scheduler is alive
    is not a retention policy.

  * **Every switch is reversible and total.** `forget_history` deletes rows,
    not flags. `export` exists so leaving is possible.

The other job of this module is cost. A saved place is the reason "home" never
needs geocoding again — see `match_place`, which `core.geocoding` consults
before any provider.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from .. import spatial
from ..config import settings
from ..types import Coord, Place

#: A new fix within this distance of the last one continues that visit rather
#: than starting a new one. 120m is comfortably outside consumer GPS error
#: (~10-50m in the open, worse indoors) so a stationary user produces exactly
#: one row, while a genuine walk to the next street starts a new one.
DWELL_RADIUS_M = 120.0

#: Below this, a visit is a passage rather than a stay and is not worth
#: remembering. Without it, history fills with 8-second rows from a bus ride.
MIN_DWELL_S = 120.0


def slugify(label: str) -> str:
    """Normalise a place name for lookup. "My College" and "my college" are the
    same place; so are "College " and "college"."""
    return re.sub(r"[^a-z0-9]+", " ", (label or "").lower()).strip()


# ---------------------------------------------------------------------------
# Saved places
# ---------------------------------------------------------------------------
def save_place(workspace_id: str, label: str, coord: Coord, *,
               kind: str = "saved", address: str = "", category: str = "",
               notes: str = "", tags: list[str] | None = None) -> dict | None:
    """Create or update a named place. Idempotent on the slug."""
    slug = slugify(label)
    if not slug:
        return None
    try:
        from ....core import db
        from ....core.schema import GeoPlace
        with db.session() as s:
            row = (s.query(GeoPlace)
                   .filter(GeoPlace.workspace_id == workspace_id,
                           GeoPlace.slug == slug).one_or_none())
            if row is None:
                row = GeoPlace(workspace_id=workspace_id, slug=slug)
                s.add(row)
            row.label = label.strip()[:120]
            row.kind = kind
            row.lat, row.lon = coord.lat, coord.lon
            row.address = address
            row.category = category
            row.notes = notes
            row.tags_json = tags or []
            s.flush()
            return _place_row(row)
    except Exception:
        return None


def delete_place(workspace_id: str, place_id: str) -> bool:
    try:
        from ....core import db
        from ....core.schema import GeoPlace
        with db.session() as s:
            deleted = (s.query(GeoPlace)
                       .filter(GeoPlace.workspace_id == workspace_id,
                               GeoPlace.id == place_id)
                       .delete(synchronize_session=False))
        return bool(deleted)
    except Exception:
        return False


def places(workspace_id: str, kind: str = "") -> list[dict]:
    try:
        from ....core import db
        from ....core.schema import GeoPlace
        with db.session() as s:
            q = s.query(GeoPlace).filter(GeoPlace.workspace_id == workspace_id)
            if kind:
                q = q.filter(GeoPlace.kind == kind)
            rows = q.order_by(GeoPlace.visit_count.desc(),
                              GeoPlace.label.asc()).all()
            return [_place_row(r) for r in rows]
    except Exception:
        return []


def match_place(workspace_id: str, text: str) -> Place | None:
    """Find a saved place named in free text. The cost rule made concrete.

    Three passes, cheapest first: exact slug, then whole-word containment in
    either direction, then a common-word overlap. The middle pass is what makes
    "take me to college" match a place saved as "College" without matching
    "collect" — word boundaries, not substrings, because substring matching
    made "work" match "network".
    """
    slug = slugify(text)
    if not slug:
        return None
    try:
        from ....core import db
        from ....core.schema import GeoPlace
        with db.session() as s:
            rows = (s.query(GeoPlace)
                    .filter(GeoPlace.workspace_id == workspace_id).all())
            if not rows:
                return None

            for row in rows:
                if row.slug == slug:
                    return _to_place(row)

            words = set(slug.split())
            best, best_len = None, 0
            for row in rows:
                row_words = set((row.slug or "").split())
                if not row_words:
                    continue
                # The saved name appears in the sentence — "college" in "take
                # me to college now".
                if row_words <= words and len(row.slug) > best_len:
                    best, best_len = row, len(row.slug)
                # Or the sentence is a fragment of the saved name — "college"
                # matching a place saved as "college main gate".
                elif words <= row_words and len(slug) > 2 and not best:
                    best, best_len = row, len(row.slug)
            return _to_place(best) if best is not None else None
    except Exception:
        return None


def nearest_saved(workspace_id: str, coord: Coord,
                  radius_m: float = 200.0) -> Place | None:
    """The closest saved place within `radius_m`, or None.

    Bounding box in SQL, haversine in Python — the pattern this whole storage
    design rests on. The box is indexed and throws away everything outside it;
    the refine fixes the corners, where a box is up to 41% wider than the
    circle it approximates.
    """
    south, west, north, east = spatial.bbox_around(coord, radius_m)
    try:
        from ....core import db
        from ....core.schema import GeoPlace
        with db.session() as s:
            rows = (s.query(GeoPlace)
                    .filter(GeoPlace.workspace_id == workspace_id,
                            GeoPlace.lat >= south, GeoPlace.lat <= north,
                            GeoPlace.lon >= west, GeoPlace.lon <= east).all())
            best, best_d = None, radius_m
            for row in rows:
                d = spatial.haversine_m(coord, Coord(row.lat, row.lon))
                if d <= best_d:
                    best, best_d = row, d
            if best is None:
                return None
            place = _to_place(best)
            if place is not None:
                place.distance_m = best_d
            return place
    except Exception:
        return None


def nearby_saved(workspace_id: str, coord: Coord,
                 radius_m: float = 5000.0, limit: int = 20) -> list[dict]:
    """Every saved place within a radius, closest first."""
    south, west, north, east = spatial.bbox_around(coord, radius_m)
    try:
        from ....core import db
        from ....core.schema import GeoPlace
        with db.session() as s:
            rows = (s.query(GeoPlace)
                    .filter(GeoPlace.workspace_id == workspace_id,
                            GeoPlace.lat >= south, GeoPlace.lat <= north,
                            GeoPlace.lon >= west, GeoPlace.lon <= east).all())
            out = []
            for row in rows:
                d = spatial.haversine_m(coord, Coord(row.lat, row.lon))
                if d <= radius_m:
                    item = _place_row(row)
                    item["distanceM"] = round(d, 1)
                    out.append(item)
            out.sort(key=lambda p: p["distanceM"])
            return out[:limit]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Visit history
# ---------------------------------------------------------------------------
def observe(workspace_id: str, coord: Coord, *,
            accuracy_m: float | None = None, label: str = "",
            source: str = "browser") -> dict:
    """Record a position fix, if history is enabled.

    Returns what it did — `{"recorded": False, "reason": "privacy mode"}` when
    it declined — because a silent no-op here is indistinguishable from a bug,
    and the UI shows the reason next to the toggle.

    Collapses into the open visit when the fix is within DWELL_RADIUS_M of it,
    which is what stops this table growing by a row a second.
    """
    cfg = settings()
    if cfg.privacy_mode:
        return {"recorded": False, "reason": "privacy mode is on"}
    if not cfg.history_enabled:
        return {"recorded": False, "reason": "location history is disabled"}

    now = datetime.now(timezone.utc)
    try:
        from ....core import db
        from ....core.schema import GeoVisit
        with db.session() as s:
            open_visit = (s.query(GeoVisit)
                          .filter(GeoVisit.workspace_id == workspace_id,
                                  GeoVisit.departed_at.is_(None))
                          .order_by(GeoVisit.arrived_at.desc()).first())

            if open_visit is not None:
                d = spatial.haversine_m(coord, Coord(open_visit.lat,
                                                     open_visit.lon))
                arrived = open_visit.arrived_at
                if arrived.tzinfo is None:
                    arrived = arrived.replace(tzinfo=timezone.utc)
                if d <= DWELL_RADIUS_M:
                    open_visit.dwell_s = (now - arrived).total_seconds()
                    if label and not open_visit.label:
                        open_visit.label = label
                    return {"recorded": True, "continued": True,
                            "visitId": open_visit.id,
                            "dwellS": round(open_visit.dwell_s, 1)}

                # Moved away — close the old visit, and discard it if it was
                # too brief to be a stay.
                open_visit.departed_at = now
                open_visit.dwell_s = (now - arrived).total_seconds()
                if open_visit.dwell_s < MIN_DWELL_S:
                    s.delete(open_visit)
                else:
                    _credit_place(s, workspace_id, open_visit)

            visit = GeoVisit(workspace_id=workspace_id, lat=coord.lat,
                             lon=coord.lon, accuracy_m=accuracy_m,
                             label=label, source=source, arrived_at=now)
            s.add(visit)
            s.flush()
            _prune(s, workspace_id, cfg.history_retention_days)
            return {"recorded": True, "continued": False, "visitId": visit.id}
    except Exception as exc:
        return {"recorded": False, "reason": str(exc)}


def _credit_place(session, workspace_id: str, visit) -> None:
    """Attribute a completed visit to a saved place, if it was at one.

    This is what makes "frequently visited" real: the counter is incremented
    when a stay ENDS, so passing the office on the bus never counts as a visit
    to it.
    """
    from ....core.schema import GeoPlace
    south, west, north, east = spatial.bbox_around(Coord(visit.lat, visit.lon),
                                                   DWELL_RADIUS_M)
    rows = (session.query(GeoPlace)
            .filter(GeoPlace.workspace_id == workspace_id,
                    GeoPlace.lat >= south, GeoPlace.lat <= north,
                    GeoPlace.lon >= west, GeoPlace.lon <= east).all())
    for row in rows:
        if spatial.haversine_m(Coord(visit.lat, visit.lon),
                               Coord(row.lat, row.lon)) <= DWELL_RADIUS_M:
            row.visit_count = int(row.visit_count or 0) + 1
            row.last_visit_at = visit.departed_at
            visit.place_id = row.id
            return


def _prune(session, workspace_id: str, retention_days: float) -> None:
    """Delete history past the retention window. Runs on every write."""
    if retention_days <= 0:
        return
    from ....core.schema import GeoVisit
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    (session.query(GeoVisit)
     .filter(GeoVisit.workspace_id == workspace_id,
             GeoVisit.arrived_at < cutoff)
     .delete(synchronize_session=False))


def history(workspace_id: str, limit: int = 50) -> list[dict]:
    if settings().privacy_mode:
        return []
    try:
        from ....core import db
        from ....core.schema import GeoVisit, iso
        with db.session() as s:
            rows = (s.query(GeoVisit)
                    .filter(GeoVisit.workspace_id == workspace_id)
                    .order_by(GeoVisit.arrived_at.desc()).limit(limit).all())
            return [{
                "id": r.id, "lat": r.lat, "lon": r.lon,
                "accuracyM": r.accuracy_m, "label": r.label,
                "placeId": r.place_id, "source": r.source,
                "arrivedAt": iso(r.arrived_at),
                "departedAt": iso(r.departed_at),
                "dwellS": round(r.dwell_s or 0.0, 1),
            } for r in rows]
    except Exception:
        return []


def frequent(workspace_id: str, limit: int = 8) -> list[dict]:
    """Saved places ranked by visits. Feeds the spatial context so the LLM
    knows where the user usually is without being told."""
    return [p for p in places(workspace_id) if p["visitCount"] > 0][:limit]


def forget_history(workspace_id: str) -> int:
    """Delete all location history. Rows, not flags."""
    try:
        from ....core import db
        from ....core.schema import GeoVisit
        with db.session() as s:
            return (s.query(GeoVisit)
                    .filter(GeoVisit.workspace_id == workspace_id)
                    .delete(synchronize_session=False))
    except Exception:
        return 0


def export(workspace_id: str) -> dict:
    """Everything TERRA holds about this workspace's locations.

    A privacy control is only credible if leaving is possible, so this returns
    saved places, history and route log in one plain structure.
    """
    return {"places": places(workspace_id),
            "history": history(workspace_id, limit=10_000),
            "routes": _route_export(workspace_id)}


def _route_export(workspace_id: str) -> list[dict]:
    from . import routing
    return routing.history(workspace_id, limit=10_000)


# ---------------------------------------------------------------------------
# shaping
# ---------------------------------------------------------------------------
def _place_row(row) -> dict:
    from ....core.schema import iso
    return {
        "id": row.id, "label": row.label, "slug": row.slug, "kind": row.kind,
        "lat": row.lat, "lon": row.lon, "address": row.address,
        "category": row.category, "notes": row.notes,
        "tags": row.tags_json or [], "visitCount": row.visit_count or 0,
        "lastVisitAt": iso(row.last_visit_at), "createdAt": iso(row.created_at),
    }


def _to_place(row) -> Place | None:
    if row is None:
        return None
    return Place(name=row.label, coord=Coord(row.lat, row.lon),
                 category=row.category or row.kind, address=row.address or "",
                 external_id=row.id, source="memory",
                 tags={"kind": row.kind, "saved": "true"})


__all__ = ["save_place", "delete_place", "places", "match_place",
           "nearest_saved", "nearby_saved", "observe", "history", "frequent",
           "forget_history", "export", "slugify"]
