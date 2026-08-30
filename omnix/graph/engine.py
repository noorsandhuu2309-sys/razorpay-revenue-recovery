"""Graph traversal, independent of where the graph is stored.

Extracted from `terra/graph.py`. The algorithms are unchanged — breadth-first
subgraph expansion, weighted-degree importance, label-propagation communities,
shortest relationship chains — but they now read through a `NodeSource` instead
of reaching into `self.nodes` / `self.adj`.

That indirection is the whole point of the file. TERRA's JSON graph and the
workspace's SQL tables implement the same three methods, so the geopolitical
product and the research workspace are drawn by identical code.

The properties worth preserving from the original, because each was a bug fix:

  * **Time decay on edge weight.** Without it a relationship attested once in
    2023 outranks one attested last week, and the graph stops reflecting now.
  * **Weighted degree, not raw degree, for importance.** Sizing by raw degree
    makes the graph reward noise: six incidental co-mentions beat one heavily
    attested relationship.
  * **Edges filtered to surviving endpoints.** A subgraph capped at `max_nodes`
    will otherwise emit edges pointing at nodes it did not include, and the
    renderer draws them into empty space.
"""

from __future__ import annotations

import math
import time
from collections import defaultdict, deque
from typing import Iterable, Protocol

# Same half-life TERRA uses. Three days is tuned for news; slower-moving
# workspaces (a repository, a syllabus) pass `decay=False` instead of retuning
# it, because for those the concept of a stale edge does not apply.
HALF_LIFE_HOURS = 72.0


def decay_factor(ts: float, now: float, half_life: float = HALF_LIFE_HOURS) -> float:
    if not ts:
        return 1.0
    age_h = max(0.0, (now - ts) / 3600.0)
    return 0.5 ** (age_h / half_life)


class NodeSource(Protocol):
    """What the algorithms need. Deliberately three methods.

    `edges_of` returns dicts with at least `dst`, `relation`, `weight`, and
    optionally `last` (epoch seconds, for decay), `count`, `sentiment`.
    """

    def node(self, nid: str) -> dict | None: ...

    def edges_of(self, nid: str) -> Iterable[dict]: ...

    def all_ids(self) -> Iterable[str]: ...


# ---------------------------------------------------------------------------
# Traversal
# ---------------------------------------------------------------------------
def neighbors(src: NodeSource, nid: str, *, limit: int = 24,
              relation: str | None = None, relations: set[str] | None = None,
              types: set[str] | None = None, now: float | None = None,
              decay: bool = True) -> list[dict]:
    """Adjacent nodes with the edge that connects them, strongest first."""
    now = now or time.time()
    out: list[dict] = []
    for edge in src.edges_of(nid) or []:
        rel = edge.get("relation")
        if relation and rel != relation:
            continue
        if relations and rel not in relations:
            continue
        node = src.node(edge.get("dst", ""))
        if node is None:
            continue
        if types and node.get("type") not in types:
            continue
        w = float(edge.get("weight") or 0.0)
        if decay and not edge.get("static"):
            w *= decay_factor(float(edge.get("last") or 0.0), now)
        out.append({
            "node": node,
            "relation": rel,
            "label": edge.get("label") or rel,
            "weight": round(w, 4),
            "count": edge.get("count", 1),
            "sentiment": edge.get("sentiment", 0.0),
            "provenance": edge.get("provenance", ""),
            "edgeId": edge.get("id"),
        })
    out.sort(key=lambda e: -e["weight"])
    return out[:limit] if limit else out


def subgraph(src: NodeSource, roots: list[str], *, hops: int = 1,
             max_nodes: int = 60, per_node: int = 8,
             relations: set[str] | None = None, types: set[str] | None = None,
             decay: bool = True) -> dict:
    """Breadth-first expansion around one or more roots.

    Returns `{nodes, edges}` ready for the renderer. Multi-root is what makes
    the Context Lens work: selecting three companies and asking for their
    neighbourhood is one call, and shared neighbours appear once.
    """
    now = time.time()
    seen: dict[str, dict] = {}
    edges: list[dict] = []
    edge_keys: set[tuple] = set()
    queue: deque = deque()

    for r in roots:
        node = src.node(r)
        if node is not None and r not in seen:
            seen[r] = {**node, "root": True, "depth": 0}
            queue.append((r, 0))

    while queue and len(seen) < max_nodes:
        nid, depth = queue.popleft()
        if depth >= hops:
            continue
        for edge in neighbors(src, nid, limit=per_node, relations=relations,
                              types=types, now=now, decay=decay):
            other = edge["node"]
            key = tuple(sorted((nid, other["id"]))) + (edge["relation"],)
            if key not in edge_keys:
                edge_keys.add(key)
                edges.append({
                    "id": edge.get("edgeId"),
                    "source": nid, "target": other["id"],
                    "relation": edge["relation"], "label": edge["label"],
                    "weight": edge["weight"], "count": edge["count"],
                    "sentiment": edge["sentiment"],
                    "provenance": edge["provenance"],
                })
            if other["id"] not in seen and len(seen) < max_nodes:
                seen[other["id"]] = {**other, "depth": depth + 1}
                queue.append((other["id"], depth + 1))

    edges = [e for e in edges if e["source"] in seen and e["target"] in seen]
    return {"nodes": list(seen.values()), "edges": edges}


def expand(src: NodeSource, nid: str, *, limit: int = 8,
           exclude: set[str] | None = None, relations: set[str] | None = None,
           types: set[str] | None = None) -> dict:
    """One click-to-expand step: the best few neighbours not already on screen.

    Progressive disclosure is what keeps a graph readable — the reader asks for
    nodes rather than being handed nine hundred of them.
    """
    exclude = exclude or set()
    picked = [e for e in neighbors(src, nid, limit=limit * 4, relations=relations,
                                   types=types)
              if e["node"]["id"] not in exclude][:limit]
    return {
        "nodes": [e["node"] for e in picked],
        "edges": [{
            "id": e.get("edgeId"), "source": nid, "target": e["node"]["id"],
            "relation": e["relation"], "label": e["label"],
            "weight": e["weight"], "count": e["count"],
            "sentiment": e["sentiment"], "provenance": e["provenance"],
        } for e in picked],
    }


def path_between(src: NodeSource, a: str, b: str, *, max_hops: int = 4) -> list[dict]:
    """Shortest relationship chain — "how is this connected to that".

    Returns [] when unconnected within `max_hops`, which the UI must render as
    "no path found within 4 hops" rather than "not related". The distinction
    matters: absence of a path in a partial graph is not evidence of absence.
    """
    if src.node(a) is None or src.node(b) is None:
        return []
    if a == b:
        return []
    prev: dict[str, tuple[str, str, str]] = {}
    queue: deque = deque([(a, 0)])
    visited = {a}
    found = False
    while queue:
        nid, depth = queue.popleft()
        if nid == b:
            found = True
            break
        if depth >= max_hops:
            continue
        for edge in src.edges_of(nid) or []:
            other = edge.get("dst")
            if not other or other in visited:
                continue
            visited.add(other)
            prev[other] = (nid, edge.get("relation", ""), edge.get("label", ""))
            queue.append((other, depth + 1))
    if not found and b not in prev:
        return []

    chain: list[dict] = []
    cur = b
    while cur != a:
        step = prev.get(cur)
        if step is None:
            return []
        parent, rel, label = step
        chain.append({
            "from": src.node(parent), "to": src.node(cur),
            "relation": rel, "label": label or rel,
        })
        cur = parent
    chain.reverse()
    return chain


# ---------------------------------------------------------------------------
# Structure
# ---------------------------------------------------------------------------
def importance(src: NodeSource, nid: str, *, now: float | None = None) -> float:
    """0..1 salience for node sizing.

    Weighted degree with decay, then squashed. `log1p` keeps a hub from
    dwarfing everything else — without it one country in a news graph is fifty
    times the radius of everything around it and the view is unreadable.
    """
    now = now or time.time()
    total = 0.0
    for edge in src.edges_of(nid) or []:
        w = float(edge.get("weight") or 0.0)
        if not edge.get("static"):
            w *= decay_factor(float(edge.get("last") or 0.0), now)
        total += w
    return min(1.0, math.log1p(total) / math.log1p(40.0))


def communities(src: NodeSource, node_ids: list[str] | None = None,
                iterations: int = 12) -> dict[str, int]:
    """Community id per node, via label propagation.

    Label propagation rather than Louvain: a dozen lines, milliseconds at this
    graph size, no modularity bookkeeping. For colouring clusters the user will
    explore by hand, the partition-quality difference is not observable.
    """
    scope = set(node_ids) if node_ids else set(src.all_ids())
    if not scope:
        return {}
    order = sorted(scope)
    labels = {nid: i for i, nid in enumerate(order)}
    for _ in range(iterations):
        changed = 0
        for nid in order:
            tally: dict[int, float] = defaultdict(float)
            for edge in src.edges_of(nid) or []:
                other = edge.get("dst")
                if other in labels:
                    tally[labels[other]] += float(edge.get("weight") or 0.0)
            if not tally:
                continue
            # Tie-break on lowest label id so the partition is deterministic
            # across runs — a graph that recolours itself on every refresh
            # reads as broken even when the partition is equally good.
            best = max(tally.items(), key=lambda kv: (kv[1], -kv[0]))[0]
            if best != labels[nid]:
                labels[nid] = best
                changed += 1
        if not changed:
            break
    remap: dict[int, int] = {}
    for nid in order:
        remap.setdefault(labels[nid], len(remap))
        labels[nid] = remap[labels[nid]]
    return labels


def community_summary(src: NodeSource, labels: dict[str, int],
                      limit: int = 12) -> list[dict]:
    """Name each community by its most important members."""
    groups: dict[int, list[str]] = defaultdict(list)
    for nid, cid in labels.items():
        groups[cid].append(nid)
    out = []
    for cid, members in groups.items():
        if len(members) < 2:
            continue
        ranked = sorted(members, key=lambda n: -importance(src, n))
        names = []
        for n in ranked[:3]:
            node = src.node(n)
            if node:
                names.append(node.get("name", ""))
        out.append({
            "id": cid, "size": len(members),
            "label": " · ".join([n for n in names if n]),
            "members": ranked[:24],
            "importance": round(sum(importance(src, n) for n in ranked[:8]), 2),
        })
    out.sort(key=lambda c: -c["size"])
    return out[:limit]


def seed_roots(src: NodeSource, limit: int = 1) -> list[str]:
    """Best starting node(s) when the user has not chosen one.

    An empty graph view is a dead end; opening on the most connected object
    gives the reader somewhere to click from.
    """
    scored = [(importance(src, nid), nid) for nid in src.all_ids()]
    scored.sort(reverse=True)
    return [nid for _, nid in scored[:limit]]
