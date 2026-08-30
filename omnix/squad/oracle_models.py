"""ORACLE's model routing — specialist ladders, cheap-first, with fallback.

The rest of the squad resolves to four generic tiers (smart/fast/code/vision).
Research is different: a planner wants breadth, an extractor wants long-context
JSON discipline, a verifier wants entailment reasoning, and a writer wants prose
quality. Pointing all of them at one "smart" model wastes the cheap roles and
under-serves the hard ones, so ORACLE routes per role.

RE-PROBED 2026-08-16 against a research-shaped prompt (five sources, ~9k chars,
three runs each). Time to first CONTENT token, then to a finished answer:

    nvidia/nemotron-3-super-120b-a12b       4.6s ->  8.8s   3/3   <- synthesis
    nvidia/nemotron-3-nano-30b-a3b          2.5s ->  5.7s   3/3   <- extraction
    nvidia/nemotron-3-nano-omni-...reasoning 2.0s ->  6.8s   3/3   <- verifier
    meta/llama-3.1-8b-instruct              0.6s ->  2.2s   3/3   (triage only)
    nvidia/nemotron-mini-4b-instruct        0.7s ->  3.6s   3/3   (triage only)
    openai/gpt-oss-20b                      3.9s -> 17.3s   3/3   (demoted)
    meta/llama-3.1-70b-instruct             1.9s -> 34.9s   2/3   (removed)

**`mistralai/mistral-nemotron` is DEAD on this account** — no content in 50s,
twice, on two separate days. It led BOTH the extractor and the writer ladder,
which is why research had become slow and unreliable: every extraction and
every final write paid a full first-token timeout before the ladder reached a
model that could answer, and a research job makes many extraction calls.

Also gone (probe before re-adding; `GET /v1/models` still lists most of them):
    mistralai/mistral-* — every one, 404/no answer   (the vendor is gone here)
    meta/llama-3.2-90b-vision-instruct    no content in 50s
    nvidia/llama-3.1-nemotron-nano-8b-v1  no content in 50s
    openai/gpt-oss-120b                   no content in 50s
    mistral-small-4-119b / llama-4-maverick / gemma-2-2b-it   410 Gone

The small/fast models answer quickly but only ever produced ONE claim where the
better models produced three, so they are used for triage and never for
extraction or synthesis — recall matters more than latency in research.
"""

from __future__ import annotations

# Live, verified rungs. First that produces a token wins (see omnix/cloud.py).
ORACLE_LADDERS: dict[str, list[str]] = {
    # Decompose a question into search angles. Breadth of framing matters more
    # than depth, and it runs once per job.
    "planner": [
        "nvidia/nemotron-3-nano-30b-a3b",
        "meta/llama-3.1-8b-instruct",
        "nvidia/nemotron-mini-4b-instruct",
    ],
    # Read sources and emit structured claims with citation indices. Runs over
    # the largest prompts in the pipeline and must hold a JSON schema. This is
    # also the role called MOST often per job, so its lead rung's latency is
    # multiplied by every source in the bundle — which is what made the dead
    # `mistral-nemotron` lead so expensive here.
    "extractor": [
        "nvidia/nemotron-3-nano-30b-a3b",
        "nvidia/nemotron-3-super-120b-a12b",
        "meta/llama-3.1-8b-instruct",
    ],
    # Entailment: does this source actually support this claim? Contradiction
    # hunting and red-teaming. A reasoning-tuned model earns its latency here —
    # this is the step that decides what survives into the report.
    "verifier": [
        "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
        "nvidia/nemotron-3-nano-30b-a3b",
        "nvidia/nemotron-3-super-120b-a12b",
    ],
    # Final prose. Quality-weighted; runs once, so the 120B's extra few seconds
    # are paid a single time for the one output the reader actually reads.
    "writer": [
        "nvidia/nemotron-3-super-120b-a12b",
        "nvidia/nemotron-3-nano-30b-a3b",
        "openai/gpt-oss-20b",
    ],
    # Cheap triage: dedup calls, gap detection, short classifications. Cheap-first
    # is the whole point — these run many times per job.
    "triage": [
        "meta/llama-3.1-8b-instruct",
        "nvidia/nemotron-mini-4b-instruct",
        "nvidia/nemotron-3-nano-30b-a3b",
    ],
}

# Local equivalents when OMNIX_LOCAL_ONLY=1 (offline dev).
_LOCAL = {
    "planner": "llama3.2:3b",
    "extractor": "qwen2.5:7b-instruct",
    "verifier": "qwen2.5:7b-instruct",
    "writer": "qwen2.5:7b-instruct",
    "triage": "llama3.2:3b",
}

# Per-role generation budgets. Extraction and synthesis need room; triage does not.
#
# The floor in `nvidia_client.REASONING_MIN_TOKENS` overrides the small numbers
# here whenever a reasoning model is on the rung — planner at 512 and triage at
# 256 are not enough for a model that thinks first, and asking anyway returns a
# successful, completely empty response.
_MAX_TOKENS = {"planner": 512, "extractor": 2048, "verifier": 1024,
               "writer": 3072, "triage": 256}

# Whether each role's model may deliberate privately before answering.
#
# OFF for every role that must return JSON, and that is a correctness decision
# rather than a latency one: when a thinking model exhausts its token budget
# mid-deliberation, NIM flushes the scratchpad into `content`, so the reply
# begins "We need to answer in..." and `extract_json` finds nothing. A research
# job makes many of these calls, and a silently empty extraction is how a run
# ends up with no claims and no relationships at all.
#
# The verifier keeps it. Entailment — does this source actually support this
# claim — is the one judgement here that deliberation measurably improves, it
# runs on the smallest prompts, and it is the step that decides what survives
# into the report.
_THINKING = {"planner": False, "extractor": False, "verifier": True,
             "writer": False, "triage": False}

# Research roles -> router capabilities, for grouping in PULSE. The role still
# selects the ladder; this only describes what kind of work the call was.
_CAPABILITY_OF_ROLE = {
    "planner": "reasoning",
    "extractor": "long_context",
    "verifier": "reasoning",
    "writer": "reasoning",
    "triage": "fast",
}


def describe() -> dict[str, dict]:
    """What ORACLE will actually use right now, for display in the console."""
    out = {}
    for role, rungs in ORACLE_LADDERS.items():
        out[role] = {"lead": rungs[0], "fallbacks": rungs[1:],
                     "local": _LOCAL.get(role, "")}
    return out


def research_llm(role: str, system: str, user: str, *, temperature: float = 0.2,
                 max_tokens: int | None = None) -> str:
    """Run a role on its own ladder. Never raises — returns '' so ORACLE's
    deterministic evidence engine still produces a report when models are down."""
    rungs = ORACLE_LADDERS.get(role) or ORACLE_LADDERS["extractor"]
    budget = max_tokens or _MAX_TOKENS.get(role, 1024)
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": user}]
    try:
        from .. import nvidia_client
        from ..config import local_only, nvidia_enabled
        from ..models.router import router as _router
        if nvidia_enabled() and not local_only() and nvidia_client.available():
            # The per-role ladders above stay authoritative — they were probed
            # against a research-shaped task and collapsing them onto generic
            # capabilities would cost recall. The router is used for metering
            # and provider abstraction, not to re-decide which model runs.
            res = _router.generate(
                _CAPABILITY_OF_ROLE.get(role, "reasoning"),
                messages=messages, models=list(rungs),
                temperature=temperature, max_tokens=budget,
                thinking=_THINKING.get(role, False))
            if res.ok and res.text.strip():
                return res.text.strip()
    except Exception:
        pass
    try:
        from ..ollama_client import chat
        return chat(_LOCAL.get(role, "qwen2.5:7b-instruct"), messages,
                    options={"temperature": temperature, "num_ctx": 8192}).strip()
    except Exception:
        return ""


def research_json(role: str, system: str, user: str, *, temperature: float = 0.1,
                  default=None, max_tokens: int | None = None):
    """research_llm, coerced to JSON, tolerant of fences and stray prose."""
    from .base import extract_json
    system = system + ("\n\nReply with ONLY valid JSON — no prose, no code "
                       "fences. The output must parse with json.loads.")
    parsed = extract_json(research_llm(role, system, user,
                                       temperature=temperature,
                                       max_tokens=max_tokens))
    return parsed if parsed is not None else default
