"""Run the existing squad units on the new execution engine.

The point of this module is sequencing. Rewriting five agents *and* introducing
workspaces, artifacts and durable executions at the same time would mean a long
window where nothing works. Instead the platform lands first and the old units
are lifted onto it verbatim: a `Unit.run(ctx, emit)` becomes a one-step
execution whose `emit` calls become typed events and whose `UnitResult` becomes
an artifact.

From this point every agent — including the three being retired — produces
addressable output inside a workspace, with cancellation and metered model
calls, without a single line of agent logic changing. Rewrites then happen one
agent at a time against a platform that is already proven.
"""

from __future__ import annotations

from ..core import entitlements, executions, workspace as workspace_mod
from ..core.executions import StepContext, StepSpec

# Which artifact type each unit's result should be filed as. A unit missing
# here still produces an artifact — `document` is the honest generic — so
# adding an agent never silently drops its output.
ARTIFACT_TYPE_BY_UNIT = {
    "oracle": "research-report",
    "challenge": "challenge",
    "forge": "code",
    "sentinel": "security-report",
    "pulse": "execution-summary",
    "nova": "execution-summary",
}

# The five that survive the restructure. Used to tag executions so history can
# be filtered by the product's real surface rather than by legacy unit codes.
PRIMARY_AGENTS = ("nova", "oracle", "challenge", "forge", "sentinel",
                  "pulse")
DEPRECATED_AGENTS = ()  # emptied by the v1 focus pass


def title_for(unit_code: str, ctx: dict) -> str:
    text = (ctx.get("input") or "").strip().replace("\n", " ")
    if not text:
        return unit_code.upper()
    return text[:120] + ("…" if len(text) > 120 else "")


def run_unit(unit, ctx: dict, *, workspace_id: str | None = None,
             mode: str = "auto", parent_execution_id: str | None = None,
             references: list[tuple[str, str]] | None = None,
             title: str | None = None) -> str:
    """Start `unit` as an execution. Returns the execution id immediately.

    `references` lets a handoff record what this run was built on — passing
    [("<artifact id>", "derived_from")] is what makes
    "ORACLE report -> implement with FORGE" a traceable chain rather than a
    copied prompt.

    `title` is separate from the input because a handoff prepends the upstream
    artifacts to the prompt: without it, every handed-off run would be titled
    with the start of the previous agent's report instead of what was asked.
    """
    ws = workspace_mod.resolve(workspace_id)
    # Every way of starting work — research, a named agent, a handoff — reaches
    # this function, so the allowance is checked here rather than at each of
    # the three callers. Before `executions.create`, because a run that is
    # refused should leave no execution row: a Runs counter that includes runs
    # the user was not allowed to make is not a counter anyone can reason about.
    entitlements.check_run(workspace_mod.acting_user())
    execution_id = executions.create(
        ws, unit.code, title=(title or title_for(unit.code, ctx)),
        input=dict(ctx), mode=mode, parent_execution_id=parent_execution_id)

    def step(sctx: StepContext) -> dict:
        # The unit's progress callback becomes typed events. Signature matches
        # the squad contract exactly: emit(stage, detail="").
        def emit(stage: str, detail: str = "") -> None:
            sctx.progress(stage, detail)

        result = unit.run(dict(ctx), emit)
        data = result.to_dict() if hasattr(result, "to_dict") else dict(result)

        sctx.emit_artifact(
            ARTIFACT_TYPE_BY_UNIT.get(unit.code, "document"),
            (title or title_for(unit.code, ctx)),
            {
                "summary": data.get("summary", ""),
                "blocks": data.get("blocks", []),
                "meta": data.get("meta", {}),
                "input": ctx.get("input", ""),
            },
            tags=[unit.code],
            references=references,
        )
        # Keep the summary out of step output: it is already in the artifact,
        # and duplicating a multi-KB briefing into execution_step.output_json
        # doubles the write for no reader.
        return {"blocks": len(data.get("blocks", [])),
                "meta": data.get("meta", {})}

    executions.start(execution_id, [
        StepSpec(unit.code, f"{unit.name} run", step,
                 agent=unit.code, capability="reasoning",
                 inputs={"input": ctx.get("input", "")}),
    ])
    return execution_id


def handoff(from_execution_id: str, to_unit, *, input: str = "",
            workspace_id: str | None = None, extra: dict | None = None) -> str:
    """Launch another agent on the output of a finished run.

    The source execution's artifacts are attached as `derived_from` references
    and their summaries are prepended to the new input, so the receiving agent
    gets the actual content rather than a pointer it cannot dereference.
    """
    from ..core import artifacts as artifacts_mod

    src = executions.get(from_execution_id, with_steps=False)
    if src is None:
        raise ValueError(f"unknown execution {from_execution_id}")
    ws = workspace_id or src["workspaceId"]

    arts = artifacts_mod.list_for(ws, execution_id=from_execution_id)
    refs = [(a["id"], "derived_from") for a in arts]

    context_parts = []
    for a in arts:
        full = artifacts_mod.get(a["id"])
        summary = ((full or {}).get("content") or {}).get("summary") or ""
        if summary:
            context_parts.append(f"--- {a['type']} from {a['sourceAgent'].upper()} ---\n{summary}")

    merged = "\n\n".join(context_parts + ([input] if input else []))
    ctx = {"input": merged or input, **(extra or {})}
    # Title from the user's request, not the merged context.
    title = (input.strip() or f"{to_unit.name} on {src['agent'].upper()} output")[:120]
    return run_unit(to_unit, ctx, workspace_id=ws,
                    parent_execution_id=from_execution_id, references=refs,
                    title=title)
