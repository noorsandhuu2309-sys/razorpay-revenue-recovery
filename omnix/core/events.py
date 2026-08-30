"""The event bus. The table is the bus.

Rows go into `event` in the same transaction as the state change they describe,
then an in-process condition variable wakes any SSE reader. That split matters:
the database is what makes events durable and replayable, the condition
variable is only an optimisation so followers do not poll. If the process
restarts, a client reconnects and replays from its last sequence number — no
broker, nothing lost.

Carried over from squad/jobs.py, because it was learned the hard way:

    A terminal status and its terminal event must become visible together.

The old JobManager set `status = done` before appending the "done" event, and
readers — which stop as soon as they see a terminal status with no unread
events — closed the stream before that event existed. Clients hung forever on a
run that had actually finished. `executions.finish()` writes both inside one
transaction for exactly this reason; do not split them.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator

from sqlalchemy import func, select

from .db import session
from .schema import Event, Execution, TERMINAL_STATUSES, iso

# One condition for the whole process. Executions are coarse enough that
# per-execution locks would buy nothing but complexity.
_cond = threading.Condition()


def emit(execution_id: str, workspace_id: str, type: str,
         payload: dict | None = None, *, _session=None) -> None:
    """Append an event and wake followers.

    Pass `_session` to enlist in a caller's transaction — required when the
    event must land atomically with a state change.
    """
    def _write(s):
        seq = s.scalar(
            select(func.coalesce(func.max(Event.seq), 0))
            .where(Event.execution_id == execution_id)) or 0
        s.add(Event(execution_id=execution_id, workspace_id=workspace_id,
                    seq=seq + 1, type=type, payload_json=payload or {}))

    if _session is not None:
        _write(_session)
        # Caller commits; notify once they do. Waking early is harmless — the
        # reader queries by seq and simply finds nothing yet.
        _notify()
        return
    with session() as s:
        _write(s)
    _notify()


def _notify() -> None:
    with _cond:
        _cond.notify_all()


def since(execution_id: str, after_seq: int = 0, limit: int = 500) -> list[dict]:
    with session() as s:
        rows = s.scalars(
            select(Event).where(Event.execution_id == execution_id,
                                Event.seq > after_seq)
            .order_by(Event.seq).limit(limit)).all()
        return [_public(e) for e in rows]


def status_of(execution_id: str) -> str | None:
    with session() as s:
        ex = s.get(Execution, execution_id)
        return ex.status if ex else None


def stream(execution_id: str, after_seq: int = 0,
           poll_timeout: float = 20.0) -> Iterator[dict]:
    """Replay-then-follow. Yields event dicts until the execution is terminal
    and every event has been delivered.

    Ordering here is the whole correctness argument: read the status *before*
    draining events, so an execution that finishes mid-drain still gets one more
    pass. Reading it after would let the final events be written between the
    drain and the status check, and the stream would close on top of them.
    """
    if status_of(execution_id) is None:
        yield {"type": "error", "payload": {"detail": "unknown execution"}, "seq": 0}
        return

    seq = after_seq
    while True:
        status = status_of(execution_id)
        batch = since(execution_id, seq)
        for ev in batch:
            seq = ev["seq"]
            yield ev

        if status in TERMINAL_STATUSES and not batch:
            # Terminal and nothing new arrived on this pass: everything the run
            # will ever produce has been delivered.
            return

        if not batch:
            with _cond:
                signalled = _cond.wait(timeout=poll_timeout)
            if not signalled:
                yield {"type": "heartbeat", "payload": {"status": status}, "seq": seq}


def _public(e: Event) -> dict:
    return {
        "seq": e.seq,
        "type": e.type,
        "payload": e.payload_json or {},
        "ts": iso(e.ts),
        "executionId": e.execution_id,
    }
