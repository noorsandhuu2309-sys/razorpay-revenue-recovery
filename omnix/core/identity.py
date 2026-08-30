"""Who is asking — carried from the HTTP middleware to the data layer.

WHY A CONTEXTVAR AND NOT A ROUTE ARGUMENT
-----------------------------------------
Roughly sixty routes take an optional `workspace` and funnel it through
`api/*.py::_ws()` into :func:`omnix.core.workspace.resolve`. That single
chokepoint is where ownership has to be enforced, and threading a `Request`
through sixty signatures to reach it would be a very large diff whose only
purpose is to move one string around. A context variable puts the acting
identity where `resolve` already is.

Contextvars are per-task, not global: two concurrent requests each see their
own value, and neither can observe the other's. That is the property that
makes this safe under an async server.

THE ONE RULE THAT MATTERS
-------------------------
**A new thread does not inherit this.** `contextvars` propagate across `await`
within a task; they do not propagate into `threading.Thread`. OMNIX starts
daemon threads for executions, intents and squad jobs, so any background work
that needs an identity must re-establish it explicitly with :func:`acting_as`.
Work that already holds a concrete, previously-validated `workspace_id` does
not need one — it is operating on an id the request layer already cleared.

Identity here is an **email**, not a database id, because the two identity
stores are separate: accounts and sessions live in `omnix_auth.json` keyed by
email, while `workspace.user_id` points at a row in the `user` table. Email is
the only value both agree on;
:func:`omnix.core.workspace.user_for_email` is the bridge.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token

# None means "no authenticated identity on this task" — either auth is off, or
# this is background work that never established one. It is deliberately NOT
# defaulted to the local account: the fallback decision belongs to
# `workspace.acting_user`, which knows whether auth is enabled, rather than
# being baked into the transport layer.
_current_email: ContextVar[str | None] = ContextVar(
    "omnix_current_email", default=None)


def set_current_email(email: str | None) -> Token:
    """Bind the acting identity for this task. Returns a reset token."""
    return _current_email.set((email or "").strip().lower() or None)


def reset_current_email(token: Token) -> None:
    _current_email.reset(token)


def current_email() -> str | None:
    """The acting identity, or None if this task never established one."""
    return _current_email.get()


@contextmanager
def acting_as(email: str | None):
    """Run a block as `email`.

    This is how a background thread inherits the identity of the request that
    started it — capture `current_email()` while still on the request task,
    then re-bind it inside the thread:

        who = identity.current_email()
        def work():
            with identity.acting_as(who):
                ...

    Capturing at thread-start rather than reading inside the thread is the
    whole point: by the time the thread runs, the request's context is gone.
    """
    token = set_current_email(email)
    try:
        yield
    finally:
        reset_current_email(token)
