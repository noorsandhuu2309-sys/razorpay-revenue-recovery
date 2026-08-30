"""Startup reconciliation of interrupted runs.

An execution cannot outlive the process that ran it, so a row still marked
`running` at boot is abandoned. Leaving it is what made the agent view show a
worker that would never move.
"""

from __future__ import annotations

from sqlalchemy import select

from omnix.core import executions
from omnix.core.db import session
from omnix.core.schema import Execution


def test_orphaned_runs_are_failed_at_boot(ws):
    stale = executions.create(ws, "oracle", title="killed mid-flight")
    with session() as s:
        s.get(Execution, stale).status = "running"

    assert executions.reconcile_orphans() >= 1

    ex = executions.get(stale, with_steps=False)
    assert ex["status"] == "failed"
    assert "restarted" in ex["error"]
    assert ex["completedAt"], "a terminal run must carry a completion time"


def test_reconcile_leaves_finished_runs_alone(ws):
    done = executions.create(ws, "nova", title="already done")
    executions.finish(done, "completed", duration_ms=42)
    before = executions.get(done, with_steps=False)

    executions.reconcile_orphans()

    after = executions.get(done, with_steps=False)
    assert after["status"] == "completed"
    assert after["error"] == ""
    assert after["completedAt"] == before["completedAt"]


def test_reconcile_is_idempotent(ws):
    stale = executions.create(ws, "forge", title="stale")
    with session() as s:
        s.get(Execution, stale).status = "queued"

    executions.reconcile_orphans()
    assert executions.reconcile_orphans() == 0

    with session() as s:
        left = s.scalars(select(Execution).where(
            Execution.workspace_id == ws,
            Execution.status.notin_(executions.TERMINAL_STATUSES))).all()
    assert not left
