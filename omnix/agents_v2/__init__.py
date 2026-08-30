"""The five restructured agents, and the bridge to the eight they replace.

NOVA (do) · ORACLE (know) · FORGE (build) · SENTINEL (protect) · PULSE (observe)

Nothing in `omnix/squad/` is deleted while this package is built out. `adapter`
runs the existing units on the new execution engine unchanged, so every agent
gains workspaces, artifacts, durable history, cancellation and real cost
accounting before any of them is rewritten. Agents then move one at a time,
and the old console keeps working until its replacement ships.

See RESTRUCTURE.md for the migration order and what happens to ATLAS, WARDEN
and MUSE.
"""

from __future__ import annotations

__all__ = ["adapter"]
