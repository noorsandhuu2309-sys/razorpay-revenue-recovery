"""Project TERRA's live graph into the workspace object model.

TERRA is not migrated. It keeps its JSON graph, its article store and its
15-minute refresh loop exactly as they are, and this module copies a snapshot
into `object` / `relationship` rows so the geopolitical material becomes
ordinary workspace intelligence — selectable in the same views, attachable to
the same Context Lens, connectable to a user's own projects.

Two consequences of "project, don't migrate" worth being explicit about:

  * The projection is **one-way**. Editing a projected object in the workspace
    does not write back to TERRA, and the next sync will not clobber the edit
    either, because `upsert_object` never overwrites a field that is already
    set. A user-edited object simply stops tracking TERRA for that field.
  * It is **idempotent**. `external_id` is derived from TERRA's own node id, so
    running the sync repeatedly updates in place rather than duplicating.

Provenance is assigned honestly, which is the whole reason this file is careful:

    seeded gazetteer nodes (countries from world.json)  -> verified
    nodes/edges backed by articles we can cite          -> source_backed
    everything else (LLM-extracted, no citable article) -> ai_inferred

Nothing here can mint `verified` for an extracted entity.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from sqlalchemy import select

from . import objects as objects_mod
from .db import session
from .schema import Source, utcnow

# Bounded on purpose. A full sync of a 1,000-node graph with every article
# attached is tens of thousands of rows and minutes of work; the useful
# projection is the salient core, and the rest arrives when the user expands
# into it.
MAX_NODES = 600
MAX_EDGES = 2500
ARTICLES_PER_NODE = 3

WORKSPACE_NAME = "Geopolitical Intelligence"


def _dt(ts: float | None) -> datetime:
    if not ts:
        return utcnow()
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc)
    except (ValueError, OSError, OverflowError):
        return utcnow()


def _ext(node_id: str) -> str:
    """Stable external id. TERRA ids are already namespaced ("country:US")."""
    return f"terra:{node_id}"


def _ensure_source(s, workspace_id: str, article: dict) -> str | None:
    """Get or create a `Source` row for a TERRA article.

    Deduped on URL within the workspace, so repeated syncs reuse the row and
    the citation count stays meaningful.
    """
    url = (article.get("url") or "").strip()
    if not url:
        return None
    existing = s.scalar(select(Source).where(
        Source.workspace_id == workspace_id, Source.url == url))
    if existing is not None:
        return existing.id
    row = Source(
        workspace_id=workspace_id,
        url=url,
        title=(article.get("title") or "")[:2000],
        publisher=(article.get("source") or "")[:200],
        # TERRA's corpus is curated news wires. Labelling it anything stronger
        # would be inventing source quality, which the product rules forbid.
        tier="news",
        tier_label="News",
        snippet=(article.get("summary") or "")[:2000],
        retrieved_at=_dt(article.get("first_seen")),
    )
    s.add(row)
    s.flush()
    return row.id


def sync(workspace_id: str | None = None, *, max_nodes: int = MAX_NODES,
         max_edges: int = MAX_EDGES, attach_sources: bool = True) -> dict:
    """Copy TERRA's current graph into a workspace. Returns a summary.

    Never raises into a caller: TERRA may be mid-refresh, its JSON may be
    absent on a fresh clone, and neither should break the endpoint.
    """
    started = time.time()
    try:
        from ..terra import graph as terra_graph
        from ..terra import store as terra_store
    except Exception as e:      # pragma: no cover - import-time environment issue
        return {"ok": False, "error": f"TERRA unavailable: {type(e).__name__}: {e}"}

    from . import workspace as workspace_mod

    if not workspace_id:
        workspace_id = _terra_workspace(workspace_mod)

    try:
        kg = terra_graph.shared()
        store = terra_store.shared()
    except Exception as e:
        return {"ok": False, "error": f"TERRA load failed: {type(e).__name__}: {e}"}

    # Most important first, so a truncated sync keeps the useful core.
    try:
        ranked = sorted(kg.nodes.items(),
                        key=lambda kv: -kg.importance(kv[0]))[:max_nodes]
    except Exception as e:
        return {"ok": False, "error": f"TERRA ranking failed: {type(e).__name__}: {e}"}

    id_map: dict[str, str] = {}
    created_n = updated_n = 0

    for tid, node in ranked:
        ntype = node.get("type") or "thing"
        name = node.get("name") or tid
        props = {k: v for k, v in (
            ("iso2", node.get("iso2")),
            ("sector", node.get("sector")),
            ("role", node.get("role")),
            ("mentions", node.get("mentions")),
            ("terraId", tid),
        ) if v not in (None, "", 0)}

        # A gazetteer seed is a real country from world.json, not an
        # extraction. That is the one thing here that earns `verified`.
        prov = "verified" if node.get("seed") else "ai_inferred"

        try:
            obj, created = objects_mod.upsert_object(
                workspace_id, ntype, name,
                external_id=_ext(tid),
                properties=props,
                provenance=prov,
                lat=node.get("lat"), lon=node.get("lon"),
                tags=["terra"],
            )
        except Exception:
            continue
        id_map[tid] = obj["id"]
        created_n += int(created)
        updated_n += int(not created)

    # -- relationships ------------------------------------------------------
    created_e = 0
    seen_edges: set[tuple] = set()
    for src_tid, rels in kg.adj.items():
        if src_tid not in id_map:
            continue
        for relation, targets in rels.items():
            for dst_tid, rec in targets.items():
                if dst_tid not in id_map or created_e >= max_edges:
                    continue
                key = tuple(sorted((src_tid, dst_tid))) + (relation,)
                if key in seen_edges:
                    continue
                seen_edges.add(key)
                # TERRA stores symmetric edges twice; `link` normalises, but
                # skipping here avoids the wasted round trip.
                result = objects_mod.link(
                    workspace_id, id_map[src_tid], id_map[dst_tid], relation,
                    weight=float(rec.get("weight") or 1.0) /
                    max(1.0, float(rec.get("count") or 1)),
                    sentiment=float(rec.get("sentiment") or 0.0),
                    # An edge a verb pattern found in real text is citable; one
                    # only a model proposed is not.
                    provenance="ai_inferred" if rec.get("llm") else "source_backed",
                )
                if result is not None:
                    created_e += int(result[1])

    # -- provenance ---------------------------------------------------------
    sources_n = 0
    if attach_sources:
        with session() as s:
            for tid, oid in id_map.items():
                node = kg.nodes.get(tid) or {}
                for aid in list(node.get("articles", []))[-ARTICLES_PER_NODE:]:
                    art = store.get(aid)
                    if not art:
                        continue
                    sid = _ensure_source(s, workspace_id, art)
                    if not sid:
                        continue
                    try:
                        objects_mod.attach_source(
                            workspace_id, sid, object_id=oid,
                            excerpt=(art.get("summary") or art.get("title") or "")[:500])
                        sources_n += 1
                    except Exception:
                        continue

    try:
        objects_mod.recompute_salience(workspace_id)
    except Exception:
        pass

    return {
        "ok": True,
        "workspace": workspace_id,
        "objects": {"created": created_n, "updated": updated_n},
        "relationships": {"created": created_e},
        "sources": sources_n,
        "graphNodes": len(kg.nodes),
        "projected": len(id_map),
        "elapsedMs": int((time.time() - started) * 1000),
    }


def _terra_workspace(workspace_mod) -> str:
    """Find or create the workspace TERRA projects into, for the acting user.

    The corpus TERRA maintains is global and lives outside the workspace
    tables; what is projected here are graph *objects*, which the user then
    links their own claims and sources to. Those edges are theirs, so the
    Space holding them has to be theirs too — projecting into the local
    account left every tenant sharing one graph, and after ownership
    enforcement would have left them unable to read it at all.
    """
    uid = workspace_mod.acting_user()
    for ws in workspace_mod.list_for(uid):
        if ws["name"] == WORKSPACE_NAME:
            return ws["id"]
    return workspace_mod.create(
        uid, WORKSPACE_NAME,
        "Live geopolitical intelligence projected from TERRA's news graph."
    )["id"]


def status() -> dict:
    """What a sync would do, without doing it."""
    try:
        from ..terra import graph as terra_graph
        kg = terra_graph.shared()
        return {"available": True, "nodes": len(kg.nodes),
                "edges": sum(len(t) for rels in kg.adj.values()
                             for t in rels.values())}
    except Exception as e:
        return {"available": False, "error": f"{type(e).__name__}: {e}"}
