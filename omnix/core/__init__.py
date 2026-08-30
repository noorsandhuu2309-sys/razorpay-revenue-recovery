"""OMNIX platform core — the substrate the five agents sit on.

Everything in this package exists because the restructure needs state that
outlives a single agent run. Before it, an OMNIX execution was a daemon thread
holding a dict: nothing could be handed from one agent to another, nothing
survived a restart, and nothing could be billed. See RESTRUCTURE.md.

    db          engine/session, SQLite now, Postgres-compatible later
    schema      the tables (workspace, artifact, execution, event, ...)
    workspace   projects and their settings
    artifacts   typed, referenceable outputs — the handoff mechanism
    executions  the run engine: DAG steps, cancel, retry, typed events
    events      the event bus (the table IS the bus)

Design rules, kept deliberately narrow so a solo developer can hold them:

  * No new infrastructure. One SQLite file, no broker, no worker fleet. Every
    boundary that would need to become a network call later is already an
    interface, but none of them are network calls today.
  * The database is the source of truth for anything a user can see. In-memory
    structures are caches over it, never the other way round.
  * Never raise into a request. Callers get empty/partial structures instead —
    the same rule the agent packages already follow.
"""

from __future__ import annotations

__all__ = ["db", "schema"]
