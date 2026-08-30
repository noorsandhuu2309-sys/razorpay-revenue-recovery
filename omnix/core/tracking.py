"""Live objects: tracking, change detection, research diff, intelligence brief.

What makes a workspace worth returning to. A tracked object accumulates events
over time, and the brief tells the user which of those actually matter to what
they are working on.

The hard part is not fetching updates — it is *not* generating noise. Three
rules, each of which exists because the obvious implementation is annoying:

  * **Relevance is computed from workspace structure, not asked of a model.**
    An event about a well-connected, explicitly tracked object ranks high; one
    about a leaf node nobody linked ranks low. A model asked "is this
    important?" says yes to everything.
  * **A change must be a real delta.** Re-seeing the same headline is not news.
    Content hashes and event fingerprints decide, not a model's impression.
  * **Nothing trivial becomes an event.** If the only difference is a
    re-crawl timestamp, no event is written at all.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select

from . import objects as objects_mod
from .db import session
from .schema import (Claim, ObjectEvent, ObjectNode, Relationship, Source,
                     utcnow)


def _fingerprint(*parts: str) -> str:
    h = hashlib.sha256("|".join(p or "" for p in parts).encode("utf-8"))
    return h.hexdigest()[:32]


# ---------------------------------------------------------------------------
# Relevance
# ---------------------------------------------------------------------------
def relevance_for(workspace_id: str, object_id: str | None,
                  *, base: str = "medium") -> str:
    """How much an event about this object should matter to this workspace.

    Deliberately structural. `tracked` is the user's own statement of interest,
    and degree is a measure of how embedded the object is in what they are
    building — both are facts about the workspace rather than opinions about
    the news.
    """
    if not object_id:
        return "low"
    with session() as s:
        obj = s.scalar(select(ObjectNode).where(ObjectNode.id == object_id))
        if obj is None:
            return "low"
        degree = int(s.scalar(select(func.count()).select_from(Relationship).where(
            Relationship.workspace_id == workspace_id,
            or_(Relationship.src_id == object_id,
                Relationship.dst_id == object_id))) or 0)
    if obj.tracked and degree >= 3:
        return "high"
    if obj.tracked or degree >= 8:
        return "high" if obj.tracked else "medium"
    if degree >= 2:
        return "medium"
    return "low"


# ---------------------------------------------------------------------------
# Change detection
# ---------------------------------------------------------------------------
def record_change(workspace_id: str, object_id: str, title: str, *,
                  body: str = "", type_key: str = "change",
                  occurred_at: datetime | None = None,
                  provenance: str = "ai_inferred",
                  properties: dict | None = None,
                  execution_id: str | None = None) -> dict | None:
    """Write an event, unless it is a repeat.

    Returns None when suppressed, which callers should treat as success — the
    absence of a duplicate is the desired outcome, not a failure.
    """
    fp = _fingerprint(workspace_id, object_id, title.strip().lower())
    props = dict(properties or {})
    props["fingerprint"] = fp

    with session() as s:
        dupe = s.scalar(select(ObjectEvent).where(
            ObjectEvent.workspace_id == workspace_id,
            ObjectEvent.object_id == object_id,
            ObjectEvent.properties_json["fingerprint"].as_string() == fp))
        if dupe is not None:
            return None

    return objects_mod.add_event(
        workspace_id, title, object_id=object_id, type_key=type_key,
        body=body, occurred_at=occurred_at,
        relevance=relevance_for(workspace_id, object_id),
        provenance=provenance, properties=props, execution_id=execution_id)


def tracked_objects(workspace_id: str) -> list[dict]:
    return objects_mod.list_objects(workspace_id, tracked=True, limit=500)


def sync_tracked_from_terra(workspace_id: str) -> dict:
    """Turn new TERRA coverage of tracked objects into events.

    The one live-update source OMNIX actually has today. Deliberately narrow:
    it reads the corpus that already exists rather than pretending to a
    general-purpose web monitor that is not wired.
    """
    try:
        from ..terra import store as terra_store
        store = terra_store.shared()
    except Exception as e:
        return {"ok": False, "error": f"TERRA unavailable: {type(e).__name__}: {e}"}

    tracked = tracked_objects(workspace_id)
    if not tracked:
        return {"ok": True, "checked": 0, "events": 0}

    created = 0
    for obj in tracked:
        terra_id = (obj.get("properties") or {}).get("terraId")
        if not terra_id:
            continue
        try:
            articles = store.by_entity(terra_id, hours=168.0)
        except Exception:
            continue
        for art in (articles or [])[:5]:
            title = (art.get("title") or "").strip()
            if not title:
                continue
            ts = art.get("published") or art.get("first_seen")
            occurred = None
            if ts:
                try:
                    occurred = datetime.fromtimestamp(float(ts), tz=timezone.utc)
                except (ValueError, OSError, OverflowError):
                    occurred = None
            ev = record_change(
                workspace_id, obj["id"], title[:400],
                body=(art.get("summary") or "")[:2000],
                type_key="coverage", occurred_at=occurred,
                provenance="source_backed",
                properties={"url": art.get("url"), "publisher": art.get("source")})
            if ev:
                created += 1
    return {"ok": True, "checked": len(tracked), "events": created}


# ---------------------------------------------------------------------------
# Research diff
# ---------------------------------------------------------------------------
def research_diff(workspace_id: str, question: str,
                  previous_execution: str, current_execution: str) -> dict:
    """Compare two runs of the same research question.

    Claims are matched on normalised text, so a re-run that rewords a claim
    slightly is not reported as one removal plus one addition. `CONTRADICTED`
    is the interesting bucket: a claim that was supported and now is not.
    """
    def _claims(exec_id: str) -> dict[str, Claim]:
        with session() as s:
            rows = s.scalars(select(Claim).where(
                Claim.workspace_id == workspace_id,
                Claim.execution_id == exec_id)).all()
            return {objects_mod.normalize_name(c.text): c for c in rows}

    before, after = _claims(previous_execution), _claims(current_execution)

    new, changed, removed, contradicted = [], [], [], []
    for key, c in after.items():
        prior = before.get(key)
        if prior is None:
            new.append({"text": c.text, "verdict": c.verdict,
                        "confidence": c.confidence})
        elif prior.verdict != c.verdict or abs(prior.confidence - c.confidence) >= 15:
            entry = {"text": c.text, "from": prior.verdict, "to": c.verdict,
                     "confidenceFrom": prior.confidence,
                     "confidenceTo": c.confidence}
            if prior.verdict == "verified" and c.verdict in ("weak", "unsupported"):
                contradicted.append(entry)
            else:
                changed.append(entry)
    for key, c in before.items():
        if key not in after:
            removed.append({"text": c.text, "verdict": c.verdict})

    return {
        "question": question,
        "previousExecution": previous_execution,
        "currentExecution": current_execution,
        "new": new, "changed": changed,
        "removed": removed, "contradicted": contradicted,
        "counts": {"new": len(new), "changed": len(changed),
                   "removed": len(removed), "contradicted": len(contradicted)},
    }


# ---------------------------------------------------------------------------
# Intelligence brief
# ---------------------------------------------------------------------------
def brief(workspace_id: str, *, hours: float = 168.0,
          limit: int = 30) -> dict:
    """The workspace home summary.

    Answers "what changed that I should care about", ranked by the structural
    relevance above. Counts are real counts; nothing here is generated prose.
    """
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    events = objects_mod.timeline(workspace_id, since=since, limit=500)

    by_object: dict[str, list[dict]] = {}
    for e in events:
        if e.get("objectId"):
            by_object.setdefault(e["objectId"], []).append(e)

    objs = {o["id"]: o for o in objects_mod.list_objects(workspace_id, limit=1000)}

    buckets: dict[str, list[dict]] = {"high": [], "medium": [], "low": []}
    for oid, evs in by_object.items():
        obj = objs.get(oid)
        if obj is None:
            continue
        rank = max((e.get("relevance") or "low") for e in evs)
        rank = "high" if any(e.get("relevance") == "high" for e in evs) else \
               "medium" if any(e.get("relevance") == "medium" for e in evs) else "low"
        buckets[rank].append({
            "object": obj,
            "eventCount": len(evs),
            "latest": evs[0],
            "events": evs[:5],
        })

    for k in buckets:
        buckets[k].sort(key=lambda b: (-b["eventCount"],
                                       -(b["object"].get("salience") or 0)))

    tracked = tracked_objects(workspace_id)
    changed_tracked = sum(1 for t in tracked if t["id"] in by_object)

    return {
        "workspace": workspace_id,
        "windowHours": hours,
        "trackedCount": len(tracked),
        "trackedChanged": changed_tracked,
        "eventCount": len(events),
        "high": buckets["high"][:limit],
        "medium": buckets["medium"][:limit],
        "low": buckets["low"][:limit],
        "generatedAt": utcnow().isoformat(),
    }


def workspace_summary(workspace_id: str) -> dict:
    """Counts for the workspace home. All measured, none generated."""
    stats = objects_mod.stats(workspace_id)
    with session() as s:
        claims = int(s.scalar(select(func.count()).select_from(Claim).where(
            Claim.workspace_id == workspace_id)) or 0)
        verified = int(s.scalar(select(func.count()).select_from(Claim).where(
            Claim.workspace_id == workspace_id,
            Claim.verdict == "verified")) or 0)
        sources = int(s.scalar(select(func.count()).select_from(Source).where(
            Source.workspace_id == workspace_id)) or 0)
    return {**stats, "claims": claims, "claimsVerified": verified,
            "sources": sources}
