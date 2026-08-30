"""Intelligence graph routes: objects, relationships, events, traversal.

These are what the workspace frontend draws from. Three conventions run through
all of them:

  * **`workspace` is a query parameter, not a path segment**, and resolves to
    the local default when absent. Every existing entry point keeps working
    without knowing workspaces exist.
  * **Reads never 500 on missing data.** An unknown object is a 404; an empty
    graph is `{"nodes": [], "edges": []}`, never an error. Views render empty
    states, and an exception here becomes a blank screen.
  * **The ontology is served, not duplicated.** `/api/ontology` gives the client
    every type, family, relation and provenance level, so the frontend has no
    second copy of the type list to drift from.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ..core import objects as objects_mod
from ..core import ontology as onto
from ..core import terra_bridge
from ..core import workspace as workspace_mod
from ..graph import engine, sql

router = APIRouter(prefix="/api", tags=["graph"])


def _ws(workspace: str | None) -> str:
    return workspace_mod.resolve(workspace)


def _csv(value: str | None) -> set[str] | None:
    if not value:
        return None
    parts = {p.strip() for p in value.split(",") if p.strip()}
    return parts or None


# ---------------------------------------------------------------------------
# Ontology
# ---------------------------------------------------------------------------
@router.get("/ontology")
def get_ontology():
    """Types, families, relations and provenance levels.

    Cached hard by the client — it changes only when the registry changes.
    """
    return onto.describe()


# ---------------------------------------------------------------------------
# Objects
# ---------------------------------------------------------------------------
@router.get("/objects")
def list_objects(workspace: str | None = None, type: str | None = None,
                 domain: str | None = None, tracked: bool | None = None,
                 q: str = "", externalId: str = "",
                 limit: int = 200, offset: int = 0):
    ws = _ws(workspace)
    # Exact external-id lookup. This is how the Map resolves a clicked country
    # to the same workspace object the Graph selects — a name search would
    # match "United States of America" against half a dozen other rows.
    if externalId:
        return {"workspace": ws,
                "objects": objects_mod.by_external_id(ws, externalId)}
    return {
        "workspace": ws,
        "objects": objects_mod.list_objects(
            ws, type_key=type, domain=domain, tracked=tracked,
            query=q, limit=limit, offset=offset),
    }


@router.post("/objects")
def create_object(payload: dict, workspace: str | None = None):
    p = payload or {}
    name = (p.get("name") or "").strip()
    if not name:
        return JSONResponse({"error": "name is required"}, status_code=400)
    type_key = p.get("type") or "thing"
    ws = _ws(workspace or p.get("workspace"))
    try:
        obj, created = objects_mod.upsert_object(
            ws, type_key, name,
            external_id=p.get("externalId", ""),
            description=p.get("description", ""),
            properties=p.get("properties"), tags=p.get("tags"),
            # A user POSTing an object is asserting it. Anything else would
            # record their own entry as an AI guess.
            provenance=p.get("provenance") or "user_created",
            confidence=p.get("confidence"),
            lat=p.get("lat"), lon=p.get("lon"),
            execution_id=p.get("executionId"), scope=p.get("scope", ""))
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return {"object": obj, "created": created}


@router.get("/objects/search")
def search(q: str, workspace: str | None = None, limit: int = 20):
    """Global object search. Substring, ranked — deliberately not semantic;
    no embedding provider is wired and faking it would be inventing a
    capability."""
    ws = _ws(workspace)
    return {"query": q, "results": objects_mod.search_objects(ws, q, limit)}


@router.get("/objects/duplicates")
def duplicates(workspace: str | None = None, limit: int = 50):
    """Candidate merges, for review. Never merged automatically."""
    ws = _ws(workspace)
    return {"groups": objects_mod.find_duplicates(ws, limit)}


@router.get("/objects/{object_id}")
def get_object(object_id: str, workspace: str | None = None):
    ws = _ws(workspace)
    obj = objects_mod.get_object(ws, object_id)
    if obj is None:
        return JSONResponse({"error": "unknown object"}, status_code=404)
    return obj


@router.patch("/objects/{object_id}")
def update_object(object_id: str, payload: dict, workspace: str | None = None):
    ws = _ws(workspace)
    obj = objects_mod.update_object(ws, object_id, **(payload or {}))
    if obj is None:
        return JSONResponse({"error": "unknown object"}, status_code=404)
    return obj


@router.delete("/objects/{object_id}")
def delete_object(object_id: str, workspace: str | None = None):
    ws = _ws(workspace)
    if not objects_mod.delete_object(ws, object_id):
        return JSONResponse({"error": "unknown object"}, status_code=404)
    return {"ok": True}


@router.post("/objects/{object_id}/track")
def track(object_id: str, payload: dict | None = None,
          workspace: str | None = None):
    ws = _ws(workspace)
    want = bool((payload or {}).get("tracked", True))
    obj = objects_mod.set_tracked(ws, object_id, want)
    if obj is None:
        return JSONResponse({"error": "unknown object"}, status_code=404)
    return obj


@router.post("/objects/{object_id}/merge")
def merge(object_id: str, payload: dict, workspace: str | None = None):
    """Fold another object into this one. `object_id` survives."""
    ws = _ws(workspace)
    other = (payload or {}).get("mergeId") or ""
    if not other:
        return JSONResponse({"error": "mergeId is required"}, status_code=400)
    obj = objects_mod.merge_objects(ws, object_id, other)
    if obj is None:
        return JSONResponse({"error": "unknown object"}, status_code=404)
    return obj


@router.get("/objects/{object_id}/sources")
def object_sources(object_id: str, workspace: str | None = None):
    """"Where did OMNIX get this?" — the provenance answer."""
    ws = _ws(workspace)
    return {"sources": objects_mod.sources_for(ws, object_id=object_id)}


@router.get("/objects/{object_id}/relationships")
def object_relationships(object_id: str, workspace: str | None = None,
                         relation: str | None = None, limit: int = 200):
    ws = _ws(workspace)
    return {"relationships": objects_mod.relationships_of(
        ws, object_id, relation=relation, limit=limit)}


@router.get("/objects/{object_id}/events")
def object_events(object_id: str, workspace: str | None = None, limit: int = 100):
    ws = _ws(workspace)
    return {"events": objects_mod.timeline(ws, object_ids=[object_id], limit=limit)}


# ---------------------------------------------------------------------------
# Relationships
# ---------------------------------------------------------------------------
@router.get("/relationships")
def list_relationships(workspace: str | None = None,
                       relations: str | None = None, limit: int = 2000):
    ws = _ws(workspace)
    rels = list(_csv(relations) or [])
    return {"relationships": objects_mod.list_relationships(
        ws, relations=rels or None, limit=limit)}


@router.post("/relationships")
def create_relationship(payload: dict, workspace: str | None = None):
    p = payload or {}
    src, dst = p.get("src"), p.get("dst")
    if not src or not dst:
        return JSONResponse({"error": "src and dst are required"}, status_code=400)
    ws = _ws(workspace or p.get("workspace"))
    result = objects_mod.link(
        ws, src, dst, p.get("relation") or "related_to",
        weight=float(p.get("weight") or 1.0),
        sentiment=float(p.get("sentiment") or 0.0),
        provenance=p.get("provenance") or "user_created",
        confidence=p.get("confidence"), properties=p.get("properties"),
        execution_id=p.get("executionId"))
    if result is None:
        return JSONResponse({"error": "unknown src or dst object"}, status_code=404)
    rel, created = result
    return {"relationship": rel, "created": created}


@router.delete("/relationships/{relationship_id}")
def delete_relationship(relationship_id: str, workspace: str | None = None):
    ws = _ws(workspace)
    if not objects_mod.unlink(ws, relationship_id):
        return JSONResponse({"error": "unknown relationship"}, status_code=404)
    return {"ok": True}


@router.get("/relationships/{relationship_id}/sources")
def relationship_sources(relationship_id: str, workspace: str | None = None):
    ws = _ws(workspace)
    return {"sources": objects_mod.sources_for(ws, relationship_id=relationship_id)}


# ---------------------------------------------------------------------------
# Graph traversal
# ---------------------------------------------------------------------------
@router.get("/graph")
def graph(workspace: str | None = None, roots: str | None = None,
          hops: int = 1, max_nodes: int = 60, per_node: int = 8,
          types: str | None = None, relations: str | None = None,
          communities: bool = False):
    """The graph view's primary call.

    `roots` is comma-separated and multi-root on purpose: that is what makes
    the Context Lens work — three selected companies produce one neighbourhood
    in one request, with shared neighbours appearing once.

    With no roots, seeds on the most connected object rather than returning
    empty. An empty graph view is a dead end for the reader.
    """
    ws = _ws(workspace)
    g = sql.load(ws, types=_csv(types), relations=_csv(relations))
    if len(g) == 0:
        return {"workspace": ws, "nodes": [], "edges": [], "communities": [],
                "stats": g.stats()}

    root_ids = [r.strip() for r in (roots or "").split(",") if r.strip()]
    root_ids = [r for r in root_ids if g.node(r) is not None]
    if not root_ids:
        root_ids = engine.seed_roots(g, limit=1)

    out = engine.subgraph(g, root_ids, hops=max(0, min(hops, 4)),
                          max_nodes=max(1, min(max_nodes, 600)),
                          per_node=max(1, min(per_node, 40)),
                          relations=_csv(relations), types=_csv(types))
    payload = {"workspace": ws, "roots": root_ids, **out, "stats": g.stats()}

    if communities and out["nodes"]:
        labels = engine.communities(g, [n["id"] for n in out["nodes"]])
        for n in payload["nodes"]:
            n["community"] = labels.get(n["id"], 0)
        payload["communities"] = engine.community_summary(g, labels)
    else:
        payload["communities"] = []
    return payload


@router.post("/graph/expand")
def expand(payload: dict, workspace: str | None = None):
    """One click-to-expand step. `exclude` is what is already on screen."""
    p = payload or {}
    nid = p.get("id") or ""
    if not nid:
        return JSONResponse({"error": "id is required"}, status_code=400)
    ws = _ws(workspace or p.get("workspace"))
    g = sql.load(ws)
    if g.node(nid) is None:
        return JSONResponse({"error": "unknown object"}, status_code=404)
    return engine.expand(
        g, nid, limit=max(1, min(int(p.get("limit") or 8), 40)),
        exclude=set(p.get("exclude") or []),
        relations=set(p.get("relations") or []) or None,
        types=set(p.get("types") or []) or None)


@router.get("/graph/path")
def path(src: str, dst: str, workspace: str | None = None, max_hops: int = 4):
    """Shortest relationship chain. An empty chain means "no path within
    max_hops" — which is not the same as "unrelated", and the UI says so."""
    ws = _ws(workspace)
    g = sql.load(ws)
    chain = engine.path_between(g, src, dst, max_hops=max(1, min(max_hops, 6)))
    return {"path": chain, "found": bool(chain), "maxHops": max_hops}


@router.get("/graph/communities")
def graph_communities(workspace: str | None = None, limit: int = 12):
    ws = _ws(workspace)
    g = sql.load(ws)
    if len(g) == 0:
        return {"communities": []}
    labels = engine.communities(g)
    return {"communities": engine.community_summary(g, labels, limit=limit)}


@router.get("/graph/stats")
def graph_stats(workspace: str | None = None):
    ws = _ws(workspace)
    return {"workspace": ws, **objects_mod.stats(ws)}


@router.post("/graph/salience")
def recompute(workspace: str | None = None):
    """Recompute node salience from current edge weights."""
    ws = _ws(workspace)
    return {"updated": objects_mod.recompute_salience(ws)}


# ---------------------------------------------------------------------------
# Timeline
# ---------------------------------------------------------------------------
@router.get("/timeline")
def timeline(workspace: str | None = None, objects: str | None = None,
             hours: float | None = None, relevance: str | None = None,
             limit: int = 200):
    ws = _ws(workspace)
    since = None
    if hours and hours > 0:
        since = datetime.now(timezone.utc) - timedelta(hours=hours)
    ids = [o.strip() for o in (objects or "").split(",") if o.strip()]
    return {"events": objects_mod.timeline(
        ws, object_ids=ids or None, since=since,
        relevance=relevance, limit=limit)}


@router.post("/events")
def create_event(payload: dict, workspace: str | None = None):
    p = payload or {}
    title = (p.get("title") or "").strip()
    if not title:
        return JSONResponse({"error": "title is required"}, status_code=400)
    ws = _ws(workspace or p.get("workspace"))
    occurred = None
    raw = p.get("occurredAt")
    if raw:
        try:
            occurred = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            occurred = None
    return objects_mod.add_event(
        ws, title, object_id=p.get("objectId"), type_key=p.get("type") or "update",
        body=p.get("body", ""), occurred_at=occurred,
        relevance=p.get("relevance") or "medium",
        provenance=p.get("provenance") or "user_created",
        properties=p.get("properties"), execution_id=p.get("executionId"))


@router.post("/events/{event_id}/dismiss")
def dismiss(event_id: str, workspace: str | None = None):
    ws = _ws(workspace)
    if not objects_mod.dismiss_event(ws, event_id):
        return JSONResponse({"error": "unknown event"}, status_code=404)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Saved views
# ---------------------------------------------------------------------------
@router.get("/views")
def list_views(workspace: str | None = None):
    return {"views": objects_mod.list_views(_ws(workspace))}


@router.post("/views")
def create_view(payload: dict, workspace: str | None = None):
    p = payload or {}
    name = (p.get("name") or "").strip()
    if not name:
        return JSONResponse({"error": "name is required"}, status_code=400)
    ws = _ws(workspace or p.get("workspace"))
    return objects_mod.save_view(ws, name, p.get("view") or "graph",
                                 p.get("state") or {})


@router.delete("/views/{view_id}")
def delete_view(view_id: str, workspace: str | None = None):
    if not objects_mod.delete_view(_ws(workspace), view_id):
        return JSONResponse({"error": "unknown view"}, status_code=404)
    return {"ok": True}


# ---------------------------------------------------------------------------
# TERRA projection
# ---------------------------------------------------------------------------
@router.get("/terra/bridge/status")
def terra_bridge_status():
    return terra_bridge.status()


@router.post("/terra/bridge/sync")
def terra_bridge_sync(payload: dict | None = None):
    """Project TERRA's live graph into a workspace.

    One-way and idempotent — TERRA is never written to, and re-running updates
    in place. Synchronous because it is an explicit user action and the caller
    wants the counts back; it takes seconds at TERRA's current size.
    """
    p = payload or {}
    result = terra_bridge.sync(
        p.get("workspace"),
        max_nodes=int(p.get("maxNodes") or terra_bridge.MAX_NODES),
        max_edges=int(p.get("maxEdges") or terra_bridge.MAX_EDGES),
        attach_sources=bool(p.get("attachSources", True)))
    if not result.get("ok"):
        return JSONResponse(result, status_code=503)
    return result
