"""Capabilities and the model registry.

A capability is what an *operation* needs, not what an agent is. Intent
classification wants FAST whether NOVA or FORGE asks for it; extracting claims
from fourteen sources wants LONG_CONTEXT regardless of which agent is reading
them. Routing on the operation is what stops a cheap classification from paying
reasoning-model latency, which is exactly what the old per-agent tables did.

Every entry below comes from the measurements already recorded in
`omnix/config.py` and `omnix/squad/oracle_models.py` (probed 2026-07-28). They
are copied rather than invented; where a number is unknown it is left at None
rather than guessed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Capability(str, Enum):
    FAST = "fast"                    # classification, triage, short judgements
    REASONING = "reasoning"          # synthesis, verification, architecture
    CODING = "coding"                # implementation, review, tests
    LONG_CONTEXT = "long_context"    # repo analysis, many-source extraction
    VISION = "vision"                # screenshots, diagrams
    EMBEDDING = "embedding"          # retrieval, dedup, semantic search


CAPABILITIES = tuple(c.value for c in Capability)


@dataclass(frozen=True)
class ModelEntry:
    """One callable model.

    `latency_s` and `quality_note` record what was actually measured, so the
    ordering below can be re-derived rather than trusted. `cost_in`/`cost_out`
    are USD per 1M tokens from the provider's published rates.
    """
    id: str
    provider: str
    capabilities: tuple[str, ...]
    cost_in: float
    cost_out: float
    latency_s: float | None = None
    context: int = 0
    quality_note: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# NVIDIA NIM — the shipped cloud path. Re-probed 2026-08-16 (three runs each on
# a ~9k-character research prompt); `latency_s` is time to first CONTENT token.
#
# WITHDRAWN from this account and deliberately absent (re-probe before adding):
#   mistralai/mistral-nemotron               no content in 50s, twice
#   nvidia/llama-3.1-nemotron-nano-8b-v1     no content in 50s, twice
#   meta/llama-3.2-90b-vision-instruct       no content in 50s, twice
#   meta/llama-3.1-70b-instruct              answers, at a 35s median — removed
#                                            for being slow, not for being dead
#   mistralai/mistral-small-4-119b-2603      HTTP 410
#   meta/llama-4-maverick-17b-128e-instruct  HTTP 410
#   google/gemma-2-2b-it                     HTTP 410
#   openai/gpt-oss-120b                      no first token within 45s
# ---------------------------------------------------------------------------
REGISTRY: tuple[ModelEntry, ...] = (
    ModelEntry(
        "nvidia/nemotron-3-nano-30b-a3b", "nvidia",
        ("fast", "reasoning", "coding", "long_context"),
        0.10, 0.40, latency_s=2.5, context=128_000,
        quality_note="30B MoE with 3B active — large-model answers at small-model "
                     "latency; the general workhorse",
        tags=("workhorse", "extraction")),
    ModelEntry(
        "nvidia/nemotron-3-super-120b-a12b", "nvidia",
        ("reasoning", "coding", "long_context"),
        0.30, 1.20, latency_s=4.6, context=128_000,
        quality_note="best synthesis on this tier; holds citation indices across "
                     "a long source bundle",
        tags=("synthesis",)),
    ModelEntry(
        "openai/gpt-oss-20b", "nvidia",
        ("reasoning", "coding", "long_context"),
        0.10, 0.40, latency_s=3.9, context=128_000,
        quality_note="good prose, but reasons privately first — 17s median to a "
                     "finished answer, so it is a fallback rung now",
        tags=()),
    ModelEntry(
        "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning", "nvidia",
        ("reasoning",),
        0.20, 0.80, latency_s=2.0, context=128_000,
        quality_note="reasoning-tuned; earns its latency on entailment/verification",
        tags=("verifier",)),
    ModelEntry(
        "nvidia/nemotron-nano-12b-v2-vl", "nvidia",
        ("vision", "fast"),
        0.08, 0.30, latency_s=0.9, context=128_000,
        quality_note="vision + competent text"),
    ModelEntry(
        "nvidia/llama-3.1-nemotron-nano-vl-8b-v1", "nvidia",
        ("vision",),
        0.06, 0.20, latency_s=1.3, context=128_000,
        quality_note="second vision rung; replaced llama-3.2-90b-vision, which died"),
    ModelEntry(
        "meta/llama-3.2-11b-vision-instruct", "nvidia",
        ("vision",),
        0.06, 0.20, latency_s=1.4, context=128_000,
        quality_note="third vision rung"),
    ModelEntry(
        "meta/llama-3.1-8b-instruct", "nvidia",
        ("fast",),
        0.05, 0.10, latency_s=0.6, context=128_000,
        quality_note="quick but shallow — 1 of 3 claims; triage only"),
    ModelEntry(
        "nvidia/nemotron-mini-4b-instruct", "nvidia",
        ("fast",),
        # 4k, not the 8k the catalogue claims — the provider says so in its own
        # rejection. See `nvidia_client._CONTEXT`.
        0.03, 0.06, latency_s=0.7, context=4_096,
        quality_note="cheapest reliable rung; the always-warm anchor",
        tags=("anchor",)),
    # Registered for PRICING, not for routing. CHALLENGE seats it by name to get
    # a vendor that is not NVIDIA, Meta or OpenAI; it appears in no `_CLOUD_ORDER`
    # tuple, so the router will never select it on its own. Without an entry here
    # its calls would bill at the deliberately-high unknown-model rate and
    # overstate every panel run's cost in PULSE.
    ModelEntry(
        "minimaxai/minimax-m3", "nvidia", (),
        0.15, 0.60, latency_s=0.7, context=128_000,
        quality_note="CHALLENGE panel seat — independent vendor, verbose but "
                     "concrete",
        tags=("panel",)),
    # --- local Ollama (offline dev) ---
    ModelEntry("qwen2.5:7b-instruct", "ollama", ("reasoning", "long_context"),
               0.05, 0.05, context=32_000, tags=("local",)),
    ModelEntry("qwen2.5-coder:7b", "ollama", ("coding",),
               0.05, 0.05, context=32_000, tags=("local",)),
    ModelEntry("llama3.2:3b", "ollama", ("fast",),
               0.02, 0.02, context=8_000, tags=("local",)),
    ModelEntry("qwen3-vl:8b", "ollama", ("vision",),
               0.05, 0.05, context=32_000, tags=("local",)),
)

BY_ID = {m.id: m for m in REGISTRY}

# Preference order per capability, cloud path. Derived from the measurements
# above and from the ladders the agents already used, so behaviour does not
# regress when callers migrate onto the router.
_CLOUD_ORDER: dict[str, tuple[str, ...]] = {
    "fast": ("meta/llama-3.1-8b-instruct",
             "nvidia/nemotron-mini-4b-instruct",
             "nvidia/nemotron-3-nano-30b-a3b"),
    "reasoning": ("nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
                  "nvidia/nemotron-3-nano-30b-a3b",
                  "nvidia/nemotron-3-super-120b-a12b"),
    "coding": ("nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
               "nvidia/nemotron-3-nano-30b-a3b",
               "openai/gpt-oss-20b"),
    "long_context": ("nvidia/nemotron-3-nano-30b-a3b",
                     "nvidia/nemotron-3-super-120b-a12b",
                     "meta/llama-3.1-8b-instruct"),
    "vision": ("nvidia/nemotron-nano-12b-v2-vl",
               "nvidia/llama-3.1-nemotron-nano-vl-8b-v1",
               "meta/llama-3.2-11b-vision-instruct"),
    # No embedding model is wired on this account. Returning an empty ladder is
    # the honest answer — callers must degrade to keyword search rather than
    # receive a model that cannot embed.
    "embedding": (),
}

_LOCAL_ORDER: dict[str, tuple[str, ...]] = {
    "fast": ("llama3.2:3b",),
    "reasoning": ("qwen2.5:7b-instruct",),
    "coding": ("qwen2.5-coder:7b", "qwen2.5:7b-instruct"),
    "long_context": ("qwen2.5:7b-instruct",),
    "vision": ("qwen3-vl:8b",),
    "embedding": (),
}


def ladder(capability: str, *, local: bool = False,
           mode: str = "auto") -> list[str]:
    """Ordered model ids to try for a capability.

    Modes reorder the same candidate set rather than reaching outside it, so a
    cost-optimised run can never silently pick a model that cannot do the job.
    """
    order = (_LOCAL_ORDER if local else _CLOUD_ORDER).get(capability, ())
    ids = [m for m in order if m in BY_ID]
    if mode == "fastest":
        ids.sort(key=lambda i: (BY_ID[i].latency_s is None, BY_ID[i].latency_s or 1e9))
    elif mode == "lowest_cost":
        ids.sort(key=lambda i: BY_ID[i].cost_in + BY_ID[i].cost_out)
    elif mode == "best_quality":
        # Most expensive as a proxy for most capable. Stated plainly because it
        # is a heuristic, not a benchmark — OMNIX has no eval set, so it must
        # not claim one.
        ids.sort(key=lambda i: -(BY_ID[i].cost_in + BY_ID[i].cost_out))
    return ids


def describe() -> dict:
    """What the router would use right now, for the UI and PULSE."""
    return {
        cap: {
            "cloud": ladder(cap),
            "local": ladder(cap, local=True),
            "available": bool(ladder(cap)) or bool(ladder(cap, local=True)),
        }
        for cap in CAPABILITIES
    }
