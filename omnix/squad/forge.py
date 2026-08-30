"""FORGE — Framework for Orchestrated Refactoring & Generative Engineering.

Turns a coding task into working code. Architect plans the approach, Implementer
writes the code (qwen2.5-coder), Reviewer critiques it, Test-Writer adds tests.
Produces code blocks + a review + tests, all rendered in the console.
"""

from __future__ import annotations

import re

from .base import (MODEL_CODE, MODEL_SMART, Subagent, Unit, UnitResult, bullets,
                   clamp, code_block, list_block, markdown_block)

_FENCE_RE = re.compile(r"```([\w+#.\-]*)\s*\n([\s\S]*?)```")


def _first_code(text: str) -> tuple[str, str]:
    """Return (lang, code) of the first fenced block, else ('', whole text)."""
    m = _FENCE_RE.search(text or "")
    if m:
        return (m.group(1).strip() or "text", m.group(2).rstrip())
    return ("text", (text or "").strip())


class Forge(Unit):
    code = "forge"
    name = "FORGE"
    glyph = "⚒"
    tagline = "Framework for Orchestrated Refactoring & Generative Engineering"
    blurb = "Spec → architecture → code → self-review → tests."
    accent = "#ff9a62"
    input_label = "What should FORGE build?"
    input_kind = "textarea"
    placeholder = "e.g. A Python LRU cache decorator with a max size and TTL."

    def __init__(self):
        self.subagents = [
            Subagent("Architect", "plans the approach & interface",
                     model=MODEL_SMART, system=(
                         "You are FORGE's Architect. Given a coding task, decide "
                         "the language (infer if unstated), the public interface, "
                         "and a short numbered implementation plan. Be concrete "
                         "and minimal — no code yet.")),
            Subagent("Implementer", "writes the code", model=MODEL_CODE, system=(
                "You are FORGE's Implementer. Write correct, idiomatic, complete "
                "code for the task and plan. Output ONE fenced code block with a "
                "language tag, then a one-paragraph note on key decisions. No "
                "placeholders or TODOs.")),
            Subagent("Reviewer", "critiques correctness & edge cases",
                     model=MODEL_CODE, system=(
                         "You are FORGE's Reviewer. Critique the given code for "
                         "correctness, edge cases, and clarity. List concrete "
                         "issues as short bullets, most important first. If it's "
                         "solid, say so and note only minor nits.")),
            Subagent("Test-Writer", "adds tests", model=MODEL_CODE, system=(
                "You are FORGE's Test-Writer. Write focused unit tests for the "
                "given code covering the main path and 2-3 edge cases. Output ONE "
                "fenced code block using the language's standard test idiom.")),
        ]

    def run(self, ctx, emit) -> UnitResult:
        task = (ctx.get("input") or "").strip()
        res = UnitResult()
        if not task:
            res.summary = "No task provided."
            return res

        emit("architect", "Architect planning the approach")
        plan = self.subagents[0].complete(f"TASK:\n{task}")
        plan_pts = bullets(plan, limit=8)

        emit("implement", "Implementer writing code")
        impl = self.subagents[1].complete(
            f"TASK:\n{task}\n\nPLAN:\n{plan}\n\nWrite the implementation now.",
            temperature=0.2)
        lang, code = _first_code(impl)
        note = _FENCE_RE.sub("", impl).strip()

        # FORGE is purely generative — with no code it has nothing to review or
        # test, so fail honestly instead of returning an empty block that claims
        # "implemented".
        if not code.strip():
            res.summary = ("FORGE couldn't generate code — the local code model "
                           "(qwen2.5-coder) appears unavailable. Start Ollama and "
                           "make sure the model is pulled, then try again.")
            if plan_pts:
                res.add(list_block("Architecture plan (draft)", plan_pts))
            res.meta = {"error": "no_code_generated"}
            return res

        emit("review", "Reviewer critiquing the code")
        review = self.subagents[2].complete(
            f"TASK:\n{task}\n\nCODE:\n```{lang}\n{code}\n```\n\nReview it.")

        emit("test", "Test-Writer adding tests")
        tests_raw = self.subagents[3].complete(
            f"TASK:\n{task}\n\nCODE:\n```{lang}\n{code}\n```\n\nWrite tests.",
            temperature=0.2)
        test_lang, test_code = _first_code(tests_raw)

        res.summary = clamp(note or f"FORGE implemented: {task}", 1500)
        if plan_pts:
            res.add(list_block("Architecture plan", plan_pts))
        res.add(code_block("Implementation", lang, code))
        if review:
            res.add(markdown_block("Review", clamp(review, 2500)))
        if test_code:
            res.add(code_block("Tests", test_lang, test_code))
        res.meta = {"lang": lang}
        return res
