"""Ambient call context, so model calls can be attributed without threading
workspace/execution ids through every agent function signature.

The alternative was passing a context object down through `Unit.run` ->
subagent -> `run_llm`, which would mean editing every agent in the squad to
land metering. A context variable set by the execution engine and read by the
model layer gets the same attribution with no change to agent code — which is
what makes it possible to meter the *existing* agents before rewriting them.

Set by `core.executions` around each step, so anything a step calls — however
deep — is attributed to that step.
"""

from __future__ import annotations

import contextvars
from dataclasses import dataclass

@dataclass(frozen=True)
class CallContext:
    workspace_id: str = ""
    execution_id: str | None = None
    step_id: str | None = None
    agent: str = ""


_current: contextvars.ContextVar[CallContext] = contextvars.ContextVar(
    "omnix_call_context", default=CallContext())


def current() -> CallContext:
    return _current.get()


def set_context(ctx: CallContext):
    """Returns the token needed to restore the previous context."""
    return _current.set(ctx)


def reset(token) -> None:
    try:
        _current.reset(token)
    except Exception:
        pass
