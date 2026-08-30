"""Agents as visible, interruptible workers (§9).

Pause, resume and redirect are cooperative. These tests pin the semantics the
API promises — a queued redirect is delivered exactly once, a pause actually
stops the run advancing, and neither can be applied to a finished run.
"""

from __future__ import annotations

import threading
import time

from omnix.core import executions


def _wait_for(predicate, timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_redirect_is_delivered_once(ws):
    seen: list[list[str]] = []
    release = threading.Event()

    def step(ctx):
        # Wait until the test has queued an instruction, then drain twice.
        release.wait(5.0)
        seen.append(ctx.redirects())
        seen.append(ctx.redirects())
        return {}

    eid = executions.create(ws, "nova", title="redirect test")
    executions.start(eid, [executions.StepSpec("only", "Only step", step)])

    assert _wait_for(lambda: executions.control_state(eid)["controllable"])
    assert executions.redirect(eid, "focus on datacenter, ignore gaming") is True
    assert executions.control_state(eid)["pendingRedirects"] == [
        "focus on datacenter, ignore gaming"]
    release.set()

    assert _wait_for(lambda: len(seen) == 2)
    assert seen[0] == ["focus on datacenter, ignore gaming"]
    assert seen[1] == [], "a drained redirect was delivered twice"


def test_empty_redirect_is_refused(ws):
    eid = executions.create(ws, "nova", title="empty redirect")
    executions.start(eid, [executions.StepSpec("s", "s", lambda ctx: {})])
    assert executions.redirect(eid, "   ") is False


def test_pause_holds_the_run_at_a_step_boundary(ws):
    """Pause while step A is in flight: A finishes, B must not start.

    Driven off a gate rather than a sleep so the pause is guaranteed to arrive
    while the run is genuinely inside a step — the boundary this promise is
    actually about.
    """
    ran: list[str] = []
    gate = threading.Event()

    def first(ctx):
        ran.append("first")
        gate.wait(5.0)
        return {}

    def second(ctx):
        ran.append("second")
        return {}

    eid = executions.create(ws, "nova", title="pause test")
    executions.start(eid, [
        executions.StepSpec("a", "First", first),
        executions.StepSpec("b", "Second", second, depends_on=["a"]),
    ])

    assert _wait_for(lambda: ran == ["first"]), "step A never started"
    assert executions.pause(eid) is True
    assert executions.control_state(eid)["paused"] is True
    gate.set()                      # let step A finish into the boundary

    time.sleep(0.5)
    assert ran == ["first"], f"a paused run crossed the boundary: {ran}"

    executions.resume(eid)
    assert _wait_for(lambda: executions.get(eid, with_steps=False)["status"]
                     in ("completed", "failed"))
    assert ran == ["first", "second"]


def test_checkpoint_raises_when_cancelled_while_paused(ws):
    """A run paused and then cancelled must stop, not wait for a resume."""
    entered = threading.Event()
    outcome: list[str] = []

    def step(ctx):
        entered.set()
        try:
            for _ in range(200):
                ctx.checkpoint()
                time.sleep(0.02)
        except executions.Cancelled:
            outcome.append("cancelled")
            raise
        outcome.append("finished")
        return {}

    eid = executions.create(ws, "nova", title="pause then cancel")
    executions.start(eid, [executions.StepSpec("s", "Step", step)])
    assert entered.wait(5.0)

    executions.pause(eid)
    time.sleep(0.1)
    executions.cancel(eid)

    assert _wait_for(lambda: executions.get(eid, with_steps=False)["status"]
                     == "cancelled", timeout=8.0)
    assert outcome == ["cancelled"]


def test_controls_refuse_a_finished_run(ws):
    eid = executions.create(ws, "nova", title="done")
    executions.start(eid, [executions.StepSpec("s", "s", lambda ctx: {})])
    assert _wait_for(lambda: executions.get(eid, with_steps=False)["status"]
                     == "completed")

    assert executions.pause(eid) is False
    assert executions.resume(eid) is False
    assert executions.redirect(eid, "too late") is False
    assert executions.cancel(eid) is False


def test_control_state_is_cleaned_up_after_a_run(ws):
    eid = executions.create(ws, "nova", title="cleanup")
    executions.start(eid, [executions.StepSpec("s", "s", lambda ctx: {})])
    assert _wait_for(lambda: executions.get(eid, with_steps=False)["status"]
                     == "completed")
    assert _wait_for(lambda: executions.control_state(eid)["controllable"] is False)
