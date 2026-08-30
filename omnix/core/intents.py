"""Intents — the fourth primitive (§11).

"Monitor NVIDIA" is not a prompt. A prompt is answered once and forgotten; an
Intent is a standing description of an outcome the workspace should keep
pursuing, and it has to survive the session that created it.

The temptation here is to ship the *shape* of monitoring — a table, a nice card,
a "watching…" label — and let it never actually look at anything. That is the
one thing this module refuses to be. Three properties make it real:

  * **It evaluates against material that already exists.** Events written by
    the tracking loop, relationships formed by research, claims filed by
    ORACLE. No new crawler is implied and none is faked.
  * **It records when it last looked.** `last_checked_at` is NULL until an
    evaluation has genuinely run, so the UI can say "never checked" instead of
    letting silence read as all-clear.
  * **It cannot fire twice for the same thing.** Hits are unique on
    (intent, kind, ref_id). A monitor that re-reports yesterday's headline
    every hour is indistinguishable from a broken one.

Evaluation is a window over time: everything that appeared since the last check.
The first evaluation of a new Intent uses its creation time, so it reports what
happened after the user asked — never a backfill of the whole Space, which
would bury the signal on day one.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

from sqlalchemy import or_, select

from . import objects as objects_mod
from .db import session
from .schema import (Claim, Intent, IntentHit, ObjectEvent, ObjectNode,
                     Relationship, iso, utcnow)

_RELEVANCE_RANK = {"low": 0, "medium": 1, "high": 2}


def _rank(relevance: str | None) -> int:
    return _RELEVANCE_RANK.get((relevance or "medium").lower(), 1)


def _aware(dt: datetime | None) -> datetime | None:
    """SQLite hands back naive datetimes; everything stored is UTC."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------
def create(workspace_id: str, title: str, *, description: str = "",
           object_ids: list[str] | None = None,
           keywords: list[str] | None = None,
           relevance_floor: str = "medium",
           cadence_minutes: int = 60) -> dict:
    title = (title or "").strip()
    if not title:
        raise ValueError("title is required")
    ids = [i for i in (object_ids or []) if i]
    words = [w.strip().lower() for w in (keywords or []) if w and w.strip()]
    if not ids and not words:
        # An Intent with nothing to watch would evaluate to nothing forever and
        # look broken. Refusing is kinder than accepting and going quiet.
        raise ValueError("an Intent needs at least one object or keyword to watch")

    with session() as s:
        row = Intent(
            workspace_id=workspace_id, title=title[:300],
            description=(description or "").strip(),
            object_ids_json=ids, keywords_json=words,
            relevance_floor=(relevance_floor if relevance_floor in _RELEVANCE_RANK
                             else "medium"),
            cadence_minutes=max(5, min(int(cadence_minutes or 60), 10080)),
            status="active")
        s.add(row)
        s.flush()
        return _public(row, s)


def get(workspace_id: str, intent_id: str) -> dict | None:
    with session() as s:
        row = s.scalar(select(Intent).where(
            Intent.workspace_id == workspace_id, Intent.id == intent_id))
        return _public(row, s) if row else None


def list_intents(workspace_id: str, *, status: str | None = None) -> list[dict]:
    with session() as s:
        q = select(Intent).where(Intent.workspace_id == workspace_id)
        if status:
            q = q.where(Intent.status == status)
        rows = s.scalars(q.order_by(Intent.created_at.desc())).all()
        return [_public(r, s) for r in rows]


def update(workspace_id: str, intent_id: str, **fields) -> dict | None:
    """Patch an Intent. Unknown keys are ignored rather than raising, so the
    client can PATCH a whole object back without filtering it first."""
    with session() as s:
        row = s.scalar(select(Intent).where(
            Intent.workspace_id == workspace_id, Intent.id == intent_id))
        if row is None:
            return None
        if "title" in fields and (fields["title"] or "").strip():
            row.title = fields["title"].strip()[:300]
        if "description" in fields:
            row.description = (fields["description"] or "").strip()
        if "status" in fields and fields["status"] in ("active", "paused", "archived"):
            row.status = fields["status"]
        if "objectIds" in fields:
            row.object_ids_json = [i for i in (fields["objectIds"] or []) if i]
        if "keywords" in fields:
            row.keywords_json = [w.strip().lower()
                                 for w in (fields["keywords"] or []) if w and w.strip()]
        if "relevanceFloor" in fields and fields["relevanceFloor"] in _RELEVANCE_RANK:
            row.relevance_floor = fields["relevanceFloor"]
        if "cadenceMinutes" in fields:
            try:
                row.cadence_minutes = max(5, min(int(fields["cadenceMinutes"]), 10080))
            except (TypeError, ValueError):
                pass
        s.flush()
        return _public(row, s)


def delete(workspace_id: str, intent_id: str) -> bool:
    with session() as s:
        row = s.scalar(select(Intent).where(
            Intent.workspace_id == workspace_id, Intent.id == intent_id))
        if row is None:
            return False
        for hit in s.scalars(select(IntentHit).where(
                IntentHit.intent_id == intent_id)).all():
            s.delete(hit)
        s.delete(row)
        return True


def hits(workspace_id: str, intent_id: str, *, limit: int = 100) -> list[dict]:
    with session() as s:
        rows = s.scalars(select(IntentHit).where(
            IntentHit.workspace_id == workspace_id,
            IntentHit.intent_id == intent_id)
            .order_by(IntentHit.created_at.desc()).limit(limit)).all()
        return [_hit_public(h) for h in rows]


# ---------------------------------------------------------------------------
# Evaluation — the part that has to be real
# ---------------------------------------------------------------------------
def evaluate(workspace_id: str, intent_id: str) -> dict | None:
    with session() as s:
        row = s.scalar(select(Intent).where(
            Intent.workspace_id == workspace_id, Intent.id == intent_id))
        if row is None:
            return None
        spec = _spec(row)
    return _evaluate_spec(workspace_id, spec)


def evaluate_workspace(workspace_id: str, *, respect_cadence: bool = False) -> dict:
    """Check every active Intent in a Space."""
    with session() as s:
        rows = s.scalars(select(Intent).where(
            Intent.workspace_id == workspace_id,
            Intent.status == "active")).all()
        specs = [_spec(r) for r in rows if not respect_cadence or _is_due(r)]

    results = [_evaluate_spec(workspace_id, sp) for sp in specs]
    return {"workspace": workspace_id, "checked": len(results),
            "newHits": sum(r["newHits"] for r in results),
            "intents": results}


def _spec(row: Intent) -> dict:
    """Detach what evaluation needs, so no session is held across the work."""
    return {
        "id": row.id, "title": row.title,
        "objectIds": list(row.object_ids_json or []),
        "keywords": list(row.keywords_json or []),
        "relevanceFloor": row.relevance_floor or "medium",
        "since": _aware(row.last_checked_at) or _aware(row.created_at) or utcnow(),
    }


def _is_due(row: Intent) -> bool:
    last = _aware(row.last_checked_at)
    if last is None:
        return True
    age_min = (datetime.now(timezone.utc) - last).total_seconds() / 60.0
    return age_min >= (row.cadence_minutes or 60)


def _evaluate_spec(workspace_id: str, spec: dict) -> dict:
    # The window is INCLUSIVE of its lower bound, and that is not sloppiness.
    # Windows' clock resolution is ~15ms, so an event written in the same tick
    # as the previous `last_checked_at` compares equal — with a strict `>` it
    # falls between two checks and is never reported at all. Silently dropping
    # the signal is the worst failure a monitor has. Re-including the boundary
    # is safe because hits are unique on (intent, kind, ref_id), so the only
    # cost is an extra row lookup.
    since = spec["since"]
    floor = _rank(spec["relevanceFloor"])
    watched = set(spec["objectIds"])
    words = spec["keywords"]

    candidates: list[dict] = []
    candidates.extend(_event_candidates(workspace_id, since, watched, words, floor))
    candidates.extend(_relationship_candidates(workspace_id, since, watched, words))
    candidates.extend(_claim_candidates(workspace_id, since, watched, words))

    created: list[dict] = []
    with session() as s:
        row = s.scalar(select(Intent).where(Intent.id == spec["id"]))
        if row is None:
            return {"intent": spec["id"], "checked": True, "newHits": 0, "hits": []}

        for cand in candidates:
            dupe = s.scalar(select(IntentHit).where(
                IntentHit.intent_id == spec["id"],
                IntentHit.kind == cand["kind"],
                IntentHit.ref_id == cand["refId"]))
            if dupe is not None:
                continue
            hit = IntentHit(
                workspace_id=workspace_id, intent_id=spec["id"],
                kind=cand["kind"], ref_id=cand["refId"],
                object_id=cand.get("objectId"), title=cand["title"][:2000],
                detail=(cand.get("detail") or "")[:4000],
                relevance=cand.get("relevance") or "medium",
                matched_json=cand.get("matched") or {})
            s.add(hit)
            s.flush()
            created.append(_hit_public(hit))

        row.last_checked_at = utcnow()
        if created:
            row.last_hit_at = utcnow()
            row.hit_count = (row.hit_count or 0) + len(created)
        title = row.title

    # Surface hits into the Activity layer so §13's "clickable and navigates to
    # the relevant object" holds for autonomous work too. Written outside the
    # transaction above because add_event opens its own session.
    for hit in created:
        try:
            objects_mod.add_event(
                workspace_id, f"Intent “{title}”: {hit['title'][:220]}",
                object_id=hit.get("objectId"), type_key="intent",
                body=hit.get("detail") or "",
                relevance=hit.get("relevance") or "medium",
                provenance="ai_inferred",
                properties={"intentId": spec["id"], "intentHitId": hit["id"],
                            "kind": hit["kind"]})
        except Exception:
            continue

    return {"intent": spec["id"], "title": title, "checked": True,
            "newHits": len(created), "hits": created,
            "checkedAt": iso(utcnow())}


def _matches(text: str, words: list[str]) -> str | None:
    """Which keyword matched, if any. No keywords means everything passes —
    the object attachment is doing the filtering in that case."""
    if not words:
        return ""
    low = (text or "").lower()
    for w in words:
        if w in low:
            return w
    return None


def _event_candidates(workspace_id: str, since: datetime, watched: set[str],
                      words: list[str], floor: int) -> list[dict]:
    cutoff = since.replace(tzinfo=None) if since.tzinfo else since
    with session() as s:
        q = select(ObjectEvent).where(
            ObjectEvent.workspace_id == workspace_id,
            ObjectEvent.detected_at >= cutoff,
            ObjectEvent.dismissed == False)          # noqa: E712
        if watched:
            q = q.where(ObjectEvent.object_id.in_(list(watched)))
        rows = s.scalars(q.order_by(ObjectEvent.detected_at.desc()).limit(300)).all()

        out = []
        for e in rows:
            # An Intent's own hits become events; re-catching them would make
            # every check echo the previous one forever.
            if (e.properties_json or {}).get("intentId"):
                continue
            if _rank(e.relevance) < floor:
                continue
            # With no keywords `_matches` returns "" and everything passes —
            # the object attachment is already doing the filtering.
            matched = _matches(f"{e.title} {e.body}", words)
            if matched is None:
                continue
            out.append({
                "kind": "event", "refId": e.id, "objectId": e.object_id,
                "title": e.title, "detail": (e.body or "")[:600],
                "relevance": e.relevance,
                "matched": {"keyword": matched or None, "type": e.type},
            })
        return out


def _relationship_candidates(workspace_id: str, since: datetime,
                             watched: set[str], words: list[str]) -> list[dict]:
    """A new edge on a watched object is a real change worth reporting —
    "NVIDIA now partners with X" is exactly what a monitor is for."""
    if not watched:
        return []
    with session() as s:
        cutoff = since.replace(tzinfo=None) if since.tzinfo else since
        rows = s.scalars(select(Relationship).where(
            Relationship.workspace_id == workspace_id,
            Relationship.first_seen >= cutoff,
            or_(Relationship.src_id.in_(list(watched)),
                Relationship.dst_id.in_(list(watched))))
            .order_by(Relationship.first_seen.desc()).limit(120)).all()
        if not rows:
            return []
        ids = {r.src_id for r in rows} | {r.dst_id for r in rows}
        names = {o.id: o.name for o in s.scalars(
            select(ObjectNode).where(ObjectNode.id.in_(list(ids)))).all()}

        out = []
        for r in rows:
            src, dst = names.get(r.src_id, "?"), names.get(r.dst_id, "?")
            title = f"{src} — {r.relation.replace('_', ' ')} — {dst}"
            if words and _matches(title, words) is None:
                continue
            focus = r.src_id if r.src_id in watched else r.dst_id
            out.append({
                "kind": "relationship", "refId": r.id, "objectId": focus,
                "title": f"New relationship: {title}",
                "detail": f"Provenance {r.provenance}, weight {round(r.weight or 0, 2)}.",
                "relevance": "medium",
                "matched": {"relation": r.relation},
            })
        return out


def _claim_candidates(workspace_id: str, since: datetime, watched: set[str],
                      words: list[str]) -> list[dict]:
    """New verified claims. Matched by keyword, or by naming a watched object —
    a claim has no object_id of its own, so the name is the only honest link."""
    with session() as s:
        cutoff = since.replace(tzinfo=None) if since.tzinfo else since
        rows = s.scalars(select(Claim).where(
            Claim.workspace_id == workspace_id,
            Claim.created_at >= cutoff)
            .order_by(Claim.created_at.desc()).limit(120)).all()
        if not rows:
            return []
        watched_names: dict[str, str] = {}
        if watched:
            for o in s.scalars(select(ObjectNode).where(
                    ObjectNode.id.in_(list(watched)))).all():
                watched_names[o.name.lower()] = o.id

        out = []
        for c in rows:
            low = (c.text or "").lower()
            object_id = None
            for name, oid in watched_names.items():
                if name and name in low:
                    object_id = oid
                    break
            matched = _matches(c.text or "", words)
            # With objects attached, a claim must actually name one of them;
            # otherwise every claim in the Space would fire every Intent.
            if watched and object_id is None and not matched:
                continue
            if matched is None and not object_id:
                continue
            out.append({
                "kind": "claim", "refId": c.id, "objectId": object_id,
                "title": f"[{c.verdict}] {c.text[:300]}",
                "detail": f"Confidence {c.confidence}, "
                          f"{len(c.supported_by_json or [])} supporting source(s).",
                "relevance": "high" if c.verdict == "verified" else "medium",
                "matched": {"keyword": matched or None, "verdict": c.verdict},
            })
        return out


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------
class _Scheduler:
    """Walks due Intents across every Space on a fixed tick.

    Cheap by construction: the tick only *selects* Intents whose cadence has
    elapsed, so a Space full of hourly Intents costs one indexed query a minute
    and nothing else.
    """

    def __init__(self, tick_seconds: float = 60.0):
        self.tick = tick_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="omx-intents")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread = None

    def _loop(self) -> None:
        # Let the server finish booting before the first sweep; an evaluation
        # racing table creation on a fresh database is a confusing first log.
        if self._stop.wait(20.0):
            return
        while not self._stop.is_set():
            try:
                self.sweep()
            except Exception as e:
                print(f"[omnix.intents] sweep failed: {type(e).__name__}: {e}")
            if self._stop.wait(self.tick):
                return

    def sweep(self) -> dict:
        with session() as s:
            rows = s.scalars(select(Intent).where(Intent.status == "active")).all()
            due = [(r.workspace_id, _spec(r)) for r in rows if _is_due(r)]
        total = 0
        for workspace_id, spec in due:
            try:
                total += _evaluate_spec(workspace_id, spec)["newHits"]
            except Exception:
                continue
        return {"due": len(due), "newHits": total, "at": time.time()}


_scheduler = _Scheduler()


def shared() -> _Scheduler:
    return _scheduler


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------
def _public(row: Intent, s) -> dict:
    names: list[dict] = []
    ids = list(row.object_ids_json or [])
    if ids:
        for o in s.scalars(select(ObjectNode).where(ObjectNode.id.in_(ids))).all():
            names.append({"id": o.id, "name": o.name, "type": o.type})
    recent = s.scalars(select(IntentHit).where(IntentHit.intent_id == row.id)
                       .order_by(IntentHit.created_at.desc()).limit(5)).all()
    return {
        "id": row.id,
        "workspaceId": row.workspace_id,
        "title": row.title,
        "description": row.description,
        "status": row.status,
        "objectIds": ids,
        "objects": names,
        "keywords": list(row.keywords_json or []),
        "relevanceFloor": row.relevance_floor,
        "cadenceMinutes": row.cadence_minutes,
        "lastCheckedAt": iso(row.last_checked_at),
        "lastHitAt": iso(row.last_hit_at),
        "hitCount": row.hit_count or 0,
        "recentHits": [_hit_public(h) for h in recent],
        "createdAt": iso(row.created_at),
    }


def _hit_public(h: IntentHit) -> dict:
    return {
        "id": h.id, "intentId": h.intent_id, "kind": h.kind, "refId": h.ref_id,
        "objectId": h.object_id, "title": h.title, "detail": h.detail,
        "relevance": h.relevance, "matched": h.matched_json or {},
        "acknowledged": h.acknowledged, "createdAt": iso(h.created_at),
    }
