"""Domain-agnostic graph traversal for the OMNIX intelligence workspace.

The algorithms here were written for TERRA and proved on a live 1,000-node
geopolitical graph. They are extracted rather than reimplemented: `engine.py`
is `terra/graph.py`'s traversal half with the storage assumptions lifted out
into a `NodeSource` protocol.

    engine      subgraph, neighbours, paths, communities — no storage opinion
    sql         a NodeSource over the `object` / `relationship` tables

TERRA keeps its own JSON-backed graph and its own refresh loop. It is projected
through this package rather than migrated into it, so nothing about the running
geopolitical product changes.
"""

from __future__ import annotations

__all__ = ["engine", "sql"]
