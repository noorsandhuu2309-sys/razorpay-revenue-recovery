"""Objects, relationships and events — the workspace intelligence graph.

Everything an agent discovers lands here. The two rules this module exists to
enforce:

  1. **Identity before insertion.** An object is looked up by `external_id`,
     then by normalised name within its type, before a new row is written. The
     failure mode this prevents is the one that quietly kills an intelligence
     product: after three research runs the graph holds "NVIDIA", "Nvidia" and
     "NVIDIA Corporation" as separate nodes, every relationship is split across
     them, and the graph is worse than useless because it is confidently wrong.

  2. **Provenance is never upgraded for free.** `ai_inferred` becomes
     `source_backed` only when an `ObjectSource` row exists, and `verified` only
     when something outside the model confirmed it. No path in this module
     raises a provenance level as a side effect of an ordinary write.

Reads never raise. Callers get empty or partial structures, matching the rule
the agent packages already follow.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone

from sqlalchemy import and_, func, or_, select

from . import ontology as onto
from .db import session
from .schema import (ObjectEvent, ObjectNode, ObjectSource, Relationship,
                     SavedView, Source, iso, utcnow)

# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------
_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_WS = re.compile(r"\s+")

# Legal-form suffixes that carry no identity. "NVIDIA Corporation" and "NVIDIA"
# are the same company; "Apple Inc" and "Apple" are the same company. Stripping
# these is the single highest-yield dedup rule for the analyst persona.
_SUFFIXES = {
    "inc", "inc.", "incorporated", "corp", "corp.", "corporation", "co", "co.",
    "company", "ltd", "ltd.", "limited", "llc", "l.l.c", "plc", "gmbh", "ag",
    "sa", "s.a", "nv", "n.v", "bv", "b.v", "ab", "as", "oy", "spa", "srl",
    "pty", "pte", "kk", "kabushiki", "holdings", "holding", "group",
}
_ARTICLES = {"the", "a", "an"}


def normalize_name(name: str) -> str:
    """Fold a name to a dedup key.

    Deliberately lossy: case, accents, punctuation, leading articles and legal
    suffixes all go. Two names that normalise identically are treated as the
    same object within a type, so this must not be so aggressive that genuinely
    different entities collide — which is why it stops at suffix stripping and
    does not, for example, drop numbers or short tokens.
    """
    s = unicodedata.normalize("NFKD", str(name or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = _PUNCT.sub(" ", s.lower())
    s = _WS.sub(" ", s).strip()
    if not s:
        return ""
    parts = s.split(" ")
    while parts and parts[0] in _ARTICLES:
        parts.pop(0)
    while parts and parts[-1] in _SUFFIXES:
        parts.pop()
    return " ".join(parts) or s


def make_external_id(type_key: str, name: str, scope: str = "") -> str:
    """Deterministic natural key for an object.

    `scope` disambiguates objects whose names are only unique inside a parent —
    a file path inside a repository, a module inside a service.
    """
    t = onto.resolve(type_key).key
    base = normalize_name(name).replace(" ", "-")
    return f"{t}:{scope}:{base}" if scope else f"{t}:{base}"


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------
def public_object(o: ObjectNode, *, degree: int | None = None) -> dict:
    """The shape every view consumes.

    Visual properties are resolved here rather than in the client so the
    frontend never carries a second copy of the ontology.
    """
    t = onto.resolve(o.type)
    vis = t.visual
    return {
        "id": o.id,
        "type": o.type,
        "typeLabel": t.label,
        "family": t.family,
        "familyLabel": vis["label"],
        "domain": t.domain,
        "name": o.name,
        "description": o.description or "",
        "externalId": o.external_id or "",
        "properties": o.properties_json or {},
        "tags": o.tags_json or [],
        "provenance": o.provenance,
        "provenanceLabel": onto.PROVENANCE.get(o.provenance, {}).get("label", o.provenance),
        # Null, not zero. "Not measured" and "measured as zero" are different
        # statements and the UI renders them differently.
        "confidence": o.confidence,
        "salience": round(o.salience or 0.0, 4),
        "tracked": bool(o.tracked),
        "lat": o.lat, "lon": o.lon,
        "geo": bool(t.geo and o.lat is not None and o.lon is not None),
        "glyph": vis["glyph"], "color": vis["color"],
        "shape": vis["shape"], "vweight": vis["weight"], "ring": vis["ring"],
        "degree": degree,
        "executionId": o.execution_id,
        "firstSeen": iso(o.first_seen),
        "lastSeen": iso(o.last_seen),
    }


def public_relationship(r: Relationship) -> dict:
    return {
        "id": r.id,
        "src": r.src_id,
        "dst": r.dst_id,
        "relation": r.relation,
        "label": onto.relation_label(r.relation),
        "symmetric": onto.is_symmetric(r.relation),
        "weight": round(r.weight or 0.0, 3),
        "observations": r.observations,
        "sentiment": round(r.sentiment or 0.0, 3),
        "provenance": r.provenance,
        "confidence": r.confidence,
        "properties": r.properties_json or {},
        "firstSeen": iso(r.first_seen),
        "lastSeen": iso(r.last_seen),
    }


def public_event(e: ObjectEvent) -> dict:
    return {
        "id": e.id,
        "objectId": e.object_id,
        "type": e.type,
        "title": e.title,
        "body": e.body or "",
        "occurredAt": iso(e.occurred_at),
        "detectedAt": iso(e.detected_at),
        "relevance": e.relevance,
        "provenance": e.provenance,
        "properties": e.properties_json or {},
        "executionId": e.execution_id,
        "dismissed": bool(e.dismissed),
    }


# ---------------------------------------------------------------------------
# Objects
# ---------------------------------------------------------------------------
def upsert_object(workspace_id: str, type_key: str, name: str, *,
                  external_id: str = "", description: str = "",
                  properties: dict | None = None, tags: list | None = None,
                  provenance: str = onto.DEFAULT_PROVENANCE,
                  confidence: float | None = None,
                  lat: float | None = None, lon: float | None = None,
                  execution_id: str | None = None,
                  scope: str = "") -> tuple[dict, bool]:
    """Create or update an object. Returns `(object, created)`.

    Matching order is exact `external_id`, then normalised name within the same
    type. Merging is additive: a field already set is never overwritten by a
    later, possibly worse, extraction. The exception is `description`, where a
    substantially longer one replaces a stub — a one-line placeholder should not
    permanently block a real summary.
    """
    t = onto.resolve(type_key)
    name = (name or "").strip()
    if not name:
        raise ValueError("object name is required")

    ext = external_id or make_external_id(t.key, name, scope)
    prov = onto.provenance_ok(provenance)

    with session() as s:
        row = s.scalar(select(ObjectNode).where(
            ObjectNode.workspace_id == workspace_id,
            ObjectNode.external_id == ext))

        if row is None:
            norm = normalize_name(name)
            if norm:
                # Name match within type. Scanning candidates of one type is
                # cheap and avoids a second stored column; revisit if a
                # workspace ever holds enough objects for this to matter.
                cands = s.scalars(select(ObjectNode).where(
                    ObjectNode.workspace_id == workspace_id,
                    ObjectNode.type == t.key)).all()
                for c in cands:
                    if normalize_name(c.name) == norm:
                        row = c
                        break

        created = row is None
        if created:
            row = ObjectNode(
                workspace_id=workspace_id, type=t.key, name=name,
                external_id=ext, description=(description or "").strip(),
                properties_json=dict(properties or {}), tags_json=list(tags or []),
                provenance=prov, confidence=confidence,
                lat=lat, lon=lon, execution_id=execution_id,
            )
            s.add(row)
        else:
            row.last_seen = utcnow()
            # Prefer the fuller surface form, as TERRA does.
            if len(name) > len(row.name or ""):
                row.name = name
            new_desc = (description or "").strip()
            if new_desc and len(new_desc) > len(row.description or "") + 40:
                row.description = new_desc
            elif new_desc and not row.description:
                row.description = new_desc
            if properties:
                merged = dict(row.properties_json or {})
                for k, v in properties.items():
                    if v not in (None, "", [], {}) and not merged.get(k):
                        merged[k] = v
                row.properties_json = merged
            if tags:
                row.tags_json = sorted(set(list(row.tags_json or []) + list(tags)))
            if lat is not None and row.lat is None:
                row.lat = lat
            if lon is not None and row.lon is None:
                row.lon = lon
            # Provenance only ever strengthens, and only toward what the caller
            # can actually justify. `rank` is weakest-last, so lower wins.
            if onto.PROVENANCE.get(prov, {}).get("rank", 9) < \
               onto.PROVENANCE.get(row.provenance, {}).get("rank", 9):
                row.provenance = prov

        s.flush()
        return public_object(row), created


def get_object(workspace_id: str, object_id: str) -> dict | None:
    with session() as s:
        row = s.scalar(select(ObjectNode).where(
            ObjectNode.workspace_id == workspace_id, ObjectNode.id == object_id))
        if row is None:
            return None
        deg = s.scalar(select(func.count()).select_from(Relationship).where(
            Relationship.workspace_id == workspace_id,
            or_(Relationship.src_id == object_id, Relationship.dst_id == object_id)))
        return public_object(row, degree=int(deg or 0))


def by_external_id(workspace_id: str, external_id: str) -> list[dict]:
    """Exact lookup on the natural key. Returns a list for API symmetry."""
    if not external_id:
        return []
    with session() as s:
        rows = s.scalars(select(ObjectNode).where(
            ObjectNode.workspace_id == workspace_id,
            ObjectNode.external_id == external_id)).all()
        return [public_object(r) for r in rows]


def list_objects(workspace_id: str, *, type_key: str | None = None,
                 domain: str | None = None, tracked: bool | None = None,
                 query: str = "", limit: int = 200, offset: int = 0) -> list[dict]:
    with session() as s:
        stmt = select(ObjectNode).where(ObjectNode.workspace_id == workspace_id)
        if type_key:
            stmt = stmt.where(ObjectNode.type == onto.resolve(type_key).key)
        if domain:
            keys = [t.key for t in onto.types(domain)]
            stmt = stmt.where(ObjectNode.type.in_(keys or ["__none__"]))
        if tracked is not None:
            stmt = stmt.where(ObjectNode.tracked == tracked)
        if query:
            like = f"%{query.strip()}%"
            stmt = stmt.where(or_(ObjectNode.name.ilike(like),
                                  ObjectNode.description.ilike(like)))
        stmt = stmt.order_by(ObjectNode.salience.desc(), ObjectNode.last_seen.desc())
        rows = s.scalars(stmt.limit(max(1, min(limit, 1000))).offset(max(0, offset))).all()
        return [public_object(r) for r in rows]


def search_objects(workspace_id: str, query: str, limit: int = 20) -> list[dict]:
    """Prefix-and-substring search, ranked so exact matches surface first.

    Not semantic: no embedding provider is wired, and pretending otherwise
    would be inventing a capability. This is the honest interim.
    """
    q = (query or "").strip()
    if not q:
        return []
    norm = normalize_name(q)
    with session() as s:
        rows = s.scalars(select(ObjectNode).where(
            ObjectNode.workspace_id == workspace_id,
            or_(ObjectNode.name.ilike(f"%{q}%"),
                ObjectNode.external_id.ilike(f"%{norm}%"),
                ObjectNode.description.ilike(f"%{q}%"))
        ).limit(200)).all()

    def rank(o: ObjectNode) -> tuple:
        n = normalize_name(o.name)
        return (
            0 if n == norm else 1 if n.startswith(norm) else 2,
            -(o.salience or 0.0),
            len(o.name or ""),
        )

    return [public_object(r) for r in sorted(rows, key=rank)[:limit]]


def update_object(workspace_id: str, object_id: str, **fields) -> dict | None:
    """User edits. Anything a user sets becomes `user_created` provenance —
    they asserted it, which is a stronger and more honest claim than whatever
    the model originally guessed."""
    allowed = {"name", "description", "tags", "properties", "lat", "lon",
               "tracked", "confidence"}
    with session() as s:
        row = s.scalar(select(ObjectNode).where(
            ObjectNode.workspace_id == workspace_id, ObjectNode.id == object_id))
        if row is None:
            return None
        touched_content = False
        for k, v in fields.items():
            if k not in allowed:
                continue
            if k == "tags":
                row.tags_json = list(v or [])
            elif k == "properties":
                row.properties_json = dict(v or {})
            elif k == "tracked":
                row.tracked = bool(v)
                continue          # tracking is not a content assertion
            else:
                setattr(row, k, v)
            touched_content = True
        if touched_content:
            row.provenance = "user_created"
        s.flush()
        return public_object(row)


def set_tracked(workspace_id: str, object_id: str, tracked: bool) -> dict | None:
    return update_object(workspace_id, object_id, tracked=tracked)


def delete_object(workspace_id: str, object_id: str) -> bool:
    """Remove an object and everything that points at it.

    Relationships and provenance rows are deleted explicitly rather than by
    cascade: SQLite enforces foreign keys here (the pragma is on), and leaving
    orphan edges would silently corrupt every traversal.
    """
    with session() as s:
        row = s.scalar(select(ObjectNode).where(
            ObjectNode.workspace_id == workspace_id, ObjectNode.id == object_id))
        if row is None:
            return False
        rel_ids = [r.id for r in s.scalars(select(Relationship).where(
            or_(Relationship.src_id == object_id,
                Relationship.dst_id == object_id))).all()]
        ev_ids = [e.id for e in s.scalars(select(ObjectEvent).where(
            ObjectEvent.object_id == object_id)).all()]
        for src in s.scalars(select(ObjectSource).where(or_(
                ObjectSource.object_id == object_id,
                ObjectSource.relationship_id.in_(rel_ids or ["__none__"]),
                ObjectSource.event_id.in_(ev_ids or ["__none__"])))).all():
            s.delete(src)
        for r in s.scalars(select(Relationship).where(
                Relationship.id.in_(rel_ids or ["__none__"]))).all():
            s.delete(r)
        for e in s.scalars(select(ObjectEvent).where(
                ObjectEvent.id.in_(ev_ids or ["__none__"]))).all():
            s.delete(e)
        s.delete(row)
        return True


def merge_objects(workspace_id: str, keep_id: str, merge_id: str) -> dict | None:
    """Fold `merge_id` into `keep_id`, moving edges, events and provenance.

    The manual escape hatch for when dedup missed. Edges that would collide
    with an existing one on the survivor have their weight folded in instead of
    creating a duplicate.
    """
    if keep_id == merge_id:
        return None
    with session() as s:
        keep = s.scalar(select(ObjectNode).where(
            ObjectNode.workspace_id == workspace_id, ObjectNode.id == keep_id))
        gone = s.scalar(select(ObjectNode).where(
            ObjectNode.workspace_id == workspace_id, ObjectNode.id == merge_id))
        if keep is None or gone is None:
            return None

        existing = {(r.src_id, r.dst_id, r.relation): r for r in s.scalars(
            select(Relationship).where(
                Relationship.workspace_id == workspace_id,
                or_(Relationship.src_id == keep_id,
                    Relationship.dst_id == keep_id))).all()}

        for r in s.scalars(select(Relationship).where(
                Relationship.workspace_id == workspace_id,
                or_(Relationship.src_id == merge_id,
                    Relationship.dst_id == merge_id))).all():
            new_src = keep_id if r.src_id == merge_id else r.src_id
            new_dst = keep_id if r.dst_id == merge_id else r.dst_id
            if new_src == new_dst:
                s.delete(r)
                continue
            hit = existing.get((new_src, new_dst, r.relation))
            if hit is not None and hit.id != r.id:
                hit.weight += r.weight
                hit.observations += r.observations
                s.delete(r)
            else:
                r.src_id, r.dst_id = new_src, new_dst

        for e in s.scalars(select(ObjectEvent).where(
                ObjectEvent.object_id == merge_id)).all():
            e.object_id = keep_id
        for p in s.scalars(select(ObjectSource).where(
                ObjectSource.object_id == merge_id)).all():
            p.object_id = keep_id

        merged = dict(gone.properties_json or {})
        merged.update(keep.properties_json or {})
        keep.properties_json = merged
        keep.tags_json = sorted(set(list(keep.tags_json or []) +
                                    list(gone.tags_json or [])))
        if not keep.description and gone.description:
            keep.description = gone.description
        if keep.lat is None:
            keep.lat, keep.lon = gone.lat, gone.lon
        keep.tracked = bool(keep.tracked or gone.tracked)

        s.delete(gone)
        s.flush()
        return public_object(keep)


def find_duplicates(workspace_id: str, limit: int = 50) -> list[dict]:
    """Objects that normalise to the same key within a type.

    Surfaced for review rather than merged automatically — an automatic merge
    that is wrong is far more expensive to undo than one the user confirmed.
    """
    with session() as s:
        rows = s.scalars(select(ObjectNode).where(
            ObjectNode.workspace_id == workspace_id)).all()
    groups: dict[tuple[str, str], list[ObjectNode]] = {}
    for r in rows:
        groups.setdefault((r.type, normalize_name(r.name)), []).append(r)
    out = []
    for (type_key, _norm), members in groups.items():
        if len(members) < 2:
            continue
        members.sort(key=lambda o: (-(o.salience or 0.0), o.first_seen))
        out.append({
            "type": type_key,
            "keep": public_object(members[0]),
            "duplicates": [public_object(m) for m in members[1:]],
        })
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------------------
# Relationships
# ---------------------------------------------------------------------------
def link(workspace_id: str, src_id: str, dst_id: str, relation: str, *,
         weight: float = 1.0, sentiment: float = 0.0,
         provenance: str = onto.DEFAULT_PROVENANCE,
         confidence: float | None = None, properties: dict | None = None,
         execution_id: str | None = None) -> tuple[dict, bool] | None:
    """Record one observation of a relationship.

    Symmetric relations are normalised by ordering the endpoints, so
    `A allied_with B` and `B allied_with A` land on one row. Without that the
    two halves drift apart the moment one direction is re-observed.
    """
    if not src_id or not dst_id or src_id == dst_id:
        return None
    rel = onto.relation_ok(relation)
    if onto.is_symmetric(rel) and src_id > dst_id:
        src_id, dst_id = dst_id, src_id
    base = onto.relation_weight(rel)
    prov = onto.provenance_ok(provenance)

    with session() as s:
        for oid in (src_id, dst_id):
            exists = s.scalar(select(func.count()).select_from(ObjectNode).where(
                ObjectNode.workspace_id == workspace_id, ObjectNode.id == oid))
            if not exists:
                return None

        row = s.scalar(select(Relationship).where(
            Relationship.workspace_id == workspace_id,
            Relationship.src_id == src_id, Relationship.dst_id == dst_id,
            Relationship.relation == rel))
        created = row is None
        if created:
            row = Relationship(
                workspace_id=workspace_id, src_id=src_id, dst_id=dst_id,
                relation=rel, weight=base * weight, observations=1,
                sentiment=sentiment, provenance=prov, confidence=confidence,
                properties_json=dict(properties or {}), execution_id=execution_id)
            s.add(row)
        else:
            row.weight += base * weight
            row.observations += 1
            n = row.observations
            row.sentiment = round(((row.sentiment or 0.0) * (n - 1) + sentiment) / n, 3)
            row.last_seen = utcnow()
            if onto.PROVENANCE.get(prov, {}).get("rank", 9) < \
               onto.PROVENANCE.get(row.provenance, {}).get("rank", 9):
                row.provenance = prov
        s.flush()
        return public_relationship(row), created


def unlink(workspace_id: str, relationship_id: str) -> bool:
    with session() as s:
        row = s.scalar(select(Relationship).where(
            Relationship.workspace_id == workspace_id,
            Relationship.id == relationship_id))
        if row is None:
            return False
        for p in s.scalars(select(ObjectSource).where(
                ObjectSource.relationship_id == relationship_id)).all():
            s.delete(p)
        s.delete(row)
        return True


def relationships_of(workspace_id: str, object_id: str, *,
                     relation: str | None = None, limit: int = 200) -> list[dict]:
    with session() as s:
        stmt = select(Relationship).where(
            Relationship.workspace_id == workspace_id,
            or_(Relationship.src_id == object_id, Relationship.dst_id == object_id))
        if relation:
            stmt = stmt.where(Relationship.relation == onto.relation_ok(relation))
        rows = s.scalars(stmt.order_by(Relationship.weight.desc()).limit(limit)).all()
        return [public_relationship(r) for r in rows]


def list_relationships(workspace_id: str, *, relations: list[str] | None = None,
                       limit: int = 2000) -> list[dict]:
    with session() as s:
        stmt = select(Relationship).where(Relationship.workspace_id == workspace_id)
        if relations:
            stmt = stmt.where(Relationship.relation.in_(
                [onto.relation_ok(r) for r in relations]))
        rows = s.scalars(stmt.order_by(Relationship.weight.desc()).limit(limit)).all()
        return [public_relationship(r) for r in rows]


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------
def add_event(workspace_id: str, title: str, *, object_id: str | None = None,
              type_key: str = "update", body: str = "",
              occurred_at: datetime | None = None, relevance: str = "medium",
              provenance: str = onto.DEFAULT_PROVENANCE,
              properties: dict | None = None,
              execution_id: str | None = None) -> dict:
    with session() as s:
        row = ObjectEvent(
            workspace_id=workspace_id, object_id=object_id, type=type_key,
            title=(title or "").strip()[:2000], body=body or "",
            occurred_at=occurred_at or utcnow(), detected_at=utcnow(),
            relevance=relevance if relevance in ("low", "medium", "high") else "medium",
            provenance=onto.provenance_ok(provenance),
            properties_json=dict(properties or {}), execution_id=execution_id)
        s.add(row)
        s.flush()
        return public_event(row)


def timeline(workspace_id: str, *, object_ids: list[str] | None = None,
             since: datetime | None = None, until: datetime | None = None,
             relevance: str | None = None, limit: int = 200) -> list[dict]:
    """Temporal slice, ordered newest first. Drives the Timeline view."""
    with session() as s:
        stmt = select(ObjectEvent).where(
            ObjectEvent.workspace_id == workspace_id,
            ObjectEvent.dismissed == False)          # noqa: E712
        if object_ids:
            stmt = stmt.where(ObjectEvent.object_id.in_(object_ids))
        if since:
            stmt = stmt.where(ObjectEvent.occurred_at >= _naive_utc(since))
        if until:
            stmt = stmt.where(ObjectEvent.occurred_at <= _naive_utc(until))
        if relevance:
            stmt = stmt.where(ObjectEvent.relevance == relevance)
        rows = s.scalars(stmt.order_by(ObjectEvent.occurred_at.desc())
                         .limit(max(1, min(limit, 1000)))).all()
        return [public_event(r) for r in rows]


def dismiss_event(workspace_id: str, event_id: str) -> bool:
    with session() as s:
        row = s.scalar(select(ObjectEvent).where(
            ObjectEvent.workspace_id == workspace_id, ObjectEvent.id == event_id))
        if row is None:
            return False
        row.dismissed = True
        return True


def _naive_utc(dt: datetime) -> datetime:
    """SQLite stores naive UTC; comparing an aware value against it silently
    matches nothing. Same trap `/api/usage` hit — see schema.iso()."""
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------
def attach_source(workspace_id: str, source_id: str, *,
                  object_id: str | None = None,
                  relationship_id: str | None = None,
                  event_id: str | None = None, excerpt: str = "") -> dict | None:
    """Bind evidence to something, and promote its provenance if warranted.

    This is the only path that raises `ai_inferred` to `source_backed`, and it
    does so because a real `Source` row now justifies the statement. Nothing
    here can produce `verified` — that requires a check outside the model.
    """
    targets = [t for t in (object_id, relationship_id, event_id) if t]
    if len(targets) != 1:
        raise ValueError("attach_source needs exactly one target")

    with session() as s:
        src = s.scalar(select(Source).where(Source.id == source_id))
        if src is None:
            return None
        dupe = s.scalar(select(ObjectSource).where(
            ObjectSource.source_id == source_id,
            ObjectSource.object_id == object_id,
            ObjectSource.relationship_id == relationship_id,
            ObjectSource.event_id == event_id))
        if dupe is not None:
            return {"id": dupe.id, "sourceId": source_id, "excerpt": dupe.excerpt}

        row = ObjectSource(
            workspace_id=workspace_id, source_id=source_id, object_id=object_id,
            relationship_id=relationship_id, event_id=event_id,
            excerpt=(excerpt or "")[:2000])
        s.add(row)

        if object_id:
            tgt = s.scalar(select(ObjectNode).where(ObjectNode.id == object_id))
            if tgt is not None and tgt.provenance == "ai_inferred":
                tgt.provenance = "source_backed"
        elif relationship_id:
            tgt = s.scalar(select(Relationship).where(
                Relationship.id == relationship_id))
            if tgt is not None and tgt.provenance == "ai_inferred":
                tgt.provenance = "source_backed"
        s.flush()
        return {"id": row.id, "sourceId": source_id, "excerpt": row.excerpt}


def sources_for(workspace_id: str, *, object_id: str | None = None,
                relationship_id: str | None = None,
                event_id: str | None = None) -> list[dict]:
    """The evidence behind a statement. Answers "where did OMNIX get this?"."""
    with session() as s:
        stmt = select(ObjectSource, Source).join(
            Source, Source.id == ObjectSource.source_id).where(
            ObjectSource.workspace_id == workspace_id)
        if object_id:
            stmt = stmt.where(ObjectSource.object_id == object_id)
        elif relationship_id:
            stmt = stmt.where(ObjectSource.relationship_id == relationship_id)
        elif event_id:
            stmt = stmt.where(ObjectSource.event_id == event_id)
        else:
            return []
        out = []
        for link_row, src in s.execute(stmt).all():
            out.append({
                "id": src.id, "url": src.url, "title": src.title,
                "publisher": src.publisher, "tier": src.tier,
                "tierLabel": src.tier_label, "year": src.year,
                "excerpt": link_row.excerpt,
                "retrievedAt": iso(src.retrieved_at),
            })
        return out


# ---------------------------------------------------------------------------
# Salience
# ---------------------------------------------------------------------------
def recompute_salience(workspace_id: str) -> int:
    """Weighted degree, normalised to 0..1.

    Weighted rather than raw degree, for the reason TERRA learned: sizing by
    raw degree makes the graph reward noise, because a node attached to six
    incidental co-mentions outranks one attached to a single heavily attested
    relationship.
    """
    with session() as s:
        rows = s.scalars(select(ObjectNode).where(
            ObjectNode.workspace_id == workspace_id)).all()
        if not rows:
            return 0
        totals: dict[str, float] = {r.id: 0.0 for r in rows}
        for rel in s.scalars(select(Relationship).where(
                Relationship.workspace_id == workspace_id)).all():
            w = rel.weight or 0.0
            if rel.src_id in totals:
                totals[rel.src_id] += w
            if rel.dst_id in totals:
                totals[rel.dst_id] += w
        peak = max(totals.values()) if totals else 0.0
        for r in rows:
            r.salience = round((totals.get(r.id, 0.0) / peak), 4) if peak > 0 else 0.0
        return len(rows)


# ---------------------------------------------------------------------------
# Saved views
# ---------------------------------------------------------------------------
def save_view(workspace_id: str, name: str, view: str, state: dict) -> dict:
    with session() as s:
        row = SavedView(workspace_id=workspace_id, name=name.strip()[:200],
                        view=view or "graph", state_json=dict(state or {}))
        s.add(row)
        s.flush()
        return {"id": row.id, "name": row.name, "view": row.view,
                "state": row.state_json, "createdAt": iso(row.created_at)}


def list_views(workspace_id: str) -> list[dict]:
    with session() as s:
        rows = s.scalars(select(SavedView).where(
            SavedView.workspace_id == workspace_id)
            .order_by(SavedView.created_at.desc())).all()
        return [{"id": r.id, "name": r.name, "view": r.view,
                 "state": r.state_json, "createdAt": iso(r.created_at)} for r in rows]


def delete_view(workspace_id: str, view_id: str) -> bool:
    with session() as s:
        row = s.scalar(select(SavedView).where(
            SavedView.workspace_id == workspace_id, SavedView.id == view_id))
        if row is None:
            return False
        s.delete(row)
        return True


def stats(workspace_id: str) -> dict:
    """Counts the workspace home and the graph legend read."""
    with session() as s:
        by_type: dict[str, int] = {}
        for type_key, n in s.execute(
                select(ObjectNode.type, func.count()).where(
                    ObjectNode.workspace_id == workspace_id)
                .group_by(ObjectNode.type)).all():
            by_type[type_key] = int(n)
        by_prov: dict[str, int] = {}
        for prov, n in s.execute(
                select(ObjectNode.provenance, func.count()).where(
                    ObjectNode.workspace_id == workspace_id)
                .group_by(ObjectNode.provenance)).all():
            by_prov[prov] = int(n)
        rels = int(s.scalar(select(func.count()).select_from(Relationship).where(
            Relationship.workspace_id == workspace_id)) or 0)
        evts = int(s.scalar(select(func.count()).select_from(ObjectEvent).where(
            ObjectEvent.workspace_id == workspace_id)) or 0)
        tracked = int(s.scalar(select(func.count()).select_from(ObjectNode).where(
            ObjectNode.workspace_id == workspace_id,
            ObjectNode.tracked == True)) or 0)    # noqa: E712
        by_family: dict[str, int] = {}
        for type_key, n in by_type.items():
            fam = onto.resolve(type_key).family
            by_family[fam] = by_family.get(fam, 0) + n
        return {
            "objects": sum(by_type.values()),
            "relationships": rels,
            "events": evts,
            "tracked": tracked,
            "byType": by_type,
            "byFamily": by_family,
            "byProvenance": by_prov,
        }
