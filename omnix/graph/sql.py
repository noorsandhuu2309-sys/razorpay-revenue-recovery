"""A `NodeSource` over the workspace `object` / `relationship` tables.

Loads the workspace graph into memory once per request and answers traversal
from there. That is a deliberate choice, not laziness: the traversals in
`engine.py` are pointer-chasing by nature, and doing them as SQL means one
query per hop per node. At the size a single workspace actually reaches —
hundreds to low thousands of objects, which is what TERRA runs at with 1,035
nodes — loading the whole graph costs two queries and a few milliseconds, and
every subsequent operation is free.

The seam is `NodeSource`, so when a workspace outgrows this the fix is a
different provider, not a rewrite of the algorithms. A recursive CTE provider
is the obvious next step and needs no caller changes.
"""

from __future__ import annotations

import time
from typing import Iterable

from sqlalchemy import select

from ..core import ontology as onto
from ..core.db import session
from ..core.objects import public_object
from ..core.schema import ObjectNode, Relationship


class WorkspaceGraph:
    """In-memory projection of one workspace's graph.

    Construct per request. Holding one across requests would need invalidation
    on every write, and a stale graph is a worse failure than a cheap reload.
    """

    def __init__(self, workspace_id: str, *,
                 types: set[str] | None = None,
                 relations: set[str] | None = None,
                 min_weight: float = 0.0):
        self.workspace_id = workspace_id
        self._nodes: dict[str, dict] = {}
        self._adj: dict[str, list[dict]] = {}
        self._load(types, relations, min_weight)

    def _load(self, types, relations, min_weight) -> None:
        with session() as s:
            stmt = select(ObjectNode).where(
                ObjectNode.workspace_id == self.workspace_id)
            if types:
                stmt = stmt.where(ObjectNode.type.in_(list(types)))
            for row in s.scalars(stmt).all():
                self._nodes[row.id] = public_object(row)

            rstmt = select(Relationship).where(
                Relationship.workspace_id == self.workspace_id)
            if relations:
                rstmt = rstmt.where(Relationship.relation.in_(list(relations)))
            if min_weight > 0:
                rstmt = rstmt.where(Relationship.weight >= min_weight)

            for r in s.scalars(rstmt).all():
                # Drop edges whose endpoints were filtered out by `types`,
                # otherwise traversal walks into nodes that do not exist here.
                if r.src_id not in self._nodes or r.dst_id not in self._nodes:
                    continue
                last = r.last_seen.timestamp() if r.last_seen else 0.0
                label = onto.relation_label(r.relation)
                fwd = {
                    "id": r.id, "dst": r.dst_id, "relation": r.relation,
                    "label": label, "weight": r.weight or 0.0,
                    "count": r.observations, "sentiment": r.sentiment or 0.0,
                    "provenance": r.provenance, "last": last,
                    # Structural relations describe how things ARE, not what
                    # was reported. Decaying "file contains function" would be
                    # meaningless, so those edges opt out of decay.
                    "static": r.relation in _STATIC_RELATIONS,
                }
                self._adj.setdefault(r.src_id, []).append(fwd)
                # Stored once, read both ways. Symmetric edges are genuinely
                # bidirectional; asymmetric ones are still traversable backwards
                # because "who supplies me" is as valid a question as "whom do
                # I supply", and the label carries the direction.
                back = dict(fwd, dst=r.src_id)
                if not onto.is_symmetric(r.relation):
                    back["label"] = f"{label} (inbound)"
                    back["inbound"] = True
                self._adj.setdefault(r.dst_id, []).append(back)

    # -- NodeSource ---------------------------------------------------------
    def node(self, nid: str) -> dict | None:
        return self._nodes.get(nid)

    def edges_of(self, nid: str) -> Iterable[dict]:
        return self._adj.get(nid, ())

    def all_ids(self) -> Iterable[str]:
        return self._nodes.keys()

    # -- convenience --------------------------------------------------------
    def __len__(self) -> int:
        return len(self._nodes)

    @property
    def edge_count(self) -> int:
        # Each relationship is stored in both directions in `_adj`.
        return sum(len(v) for v in self._adj.values()) // 2

    def degree(self, nid: str) -> int:
        return len(self._adj.get(nid, ()))

    def nodes_with_degree(self) -> list[dict]:
        return [{**n, "degree": self.degree(nid)} for nid, n in self._nodes.items()]

    def stats(self) -> dict:
        by_type: dict[str, int] = {}
        for n in self._nodes.values():
            by_type[n["type"]] = by_type.get(n["type"], 0) + 1
        return {
            "nodes": len(self._nodes),
            "edges": self.edge_count,
            "byType": by_type,
            "loadedAt": time.time(),
        }


# Relations that describe structure rather than reported events. See the note
# in `_load` — decaying these would make a repository's own file tree fade out.
_STATIC_RELATIONS = {
    "contains", "located_in", "imports", "calls", "implements", "verifies",
    "subsidiary_of", "member_of", "about", "derived_from", "cites",
    "supported_by", "contradicted_by",
}


def load(workspace_id: str, **kw) -> WorkspaceGraph:
    return WorkspaceGraph(workspace_id, **kw)
