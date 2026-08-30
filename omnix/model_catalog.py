"""The user-facing model roster: open-weight models, one per job.

WHY THIS IS SEPARATE FROM `omnix/models/`
-----------------------------------------
`omnix/models/` is the INTERNAL capability router — subsystems ask it for
FAST or REASONING and never name a model, which is what keeps the squad from
being married to one provider. This module is the opposite thing on purpose: a
short, named, human-readable roster that a person picks from in the UI. Fusing
them would either drag provider plumbing into the settings screen or force the
squad through a list curated for humans. They are two different audiences.

THE ROSTER — re-probed 2026-08-16, and the leads changed
--------------------------------------------------------
The previous roster was ranked on a one-line question. That is not the workload:
a real turn carries a system prompt and history, and a research turn carries
five sources with two fetched pages inlined — around 9,000 characters. Prefill
dominates there, so the ranking was redone against a prompt of that size, three
runs each, measuring time to the first CONTENT token and then to a finished
answer:

    chat      nvidia/nemotron-3-nano-30b-a3b       2.5s ->  5.7s   3/3
    research  nvidia/nemotron-3-super-120b-a12b    4.6s ->  8.8s   3/3
    code      nvidia/nemotron-3-nano-omni-30b-...  2.0s ->  6.8s   3/3
    vision    nvidia/nemotron-nano-12b-v2-vl       (vision prompts; see below)
    fast      meta/llama-3.1-8b-instruct           0.6s ->  2.2s   3/3
    anchor    nvidia/nemotron-mini-4b-instruct     0.7s ->  3.6s   3/3

WHAT WAS DEMOTED, AND WHY IT MATTERS MORE THAN WHAT WAS ADDED
-------------------------------------------------------------
* `openai/gpt-oss-20b` was the chat default. It thinks privately before it
  writes, which cost 4-11s of empty bubble and a 17s median answer. It is kept
  as a selectable model — its prose is good — but it no longer leads anything.
* `meta/llama-3.1-70b-instruct` was the research default, on the strength of a
  1.68s reading taken on a one-line prompt. On a real source bundle it took a
  35s median, one run in three was still writing at 60s, and the answer was
  thinner than the 30B's. Being the biggest model on the tier is not the same
  as being the best one, and it is gone.
* `mistralai/mistral-nemotron` is simply DEAD on this account now — no content
  in 50s, twice. It led ORACLE's extractor and writer ladders, so research was
  paying a full timeout before it reached a model that could answer.

MEASUREMENTS THAT LOOK WRONG BUT ARE NOT
----------------------------------------
* A reasoning model with a small `max_tokens` looks dead: it emits
  `reasoning_content` deltas and spends the whole budget before any `content`.
  Give a probe ~400 tokens and count reasoning deltas.
* `nemotron-3-super-120b-a12b` answered one probe with HTTP 502 and then went
  5/5 warm at 1.6-3.3s. That 502 was a cold MoE instance, which is why it is in
  `config.WARM_MODELS`. Do not demote a model on one cold reading.
* `nemotron-3.5-lightning-30b-a3b` is fast and unusable: it returns the entire
  reply in ONE delta with its chain-of-thought copied into `content`. It cannot
  stream and it leaks its scratchpad.

TOGGLES
-------
Every model can be switched off. A role whose models are all off falls back to
its anchor rung rather than failing the turn — a settings screen that can leave
the product unable to answer is a trap, not a preference.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path

from .persistence import save_json

log = logging.getLogger("omnix.models")

_ROOT = Path(__file__).resolve().parents[1]
PREFS_PATH = _ROOT / "omnix_model_prefs.json"

# The tail of every ladder. Small, consistently warm, and never removable by a
# toggle: it is what guarantees SOMETHING answers.
ANCHOR = "nvidia/nemotron-mini-4b-instruct"

# --- The roster -------------------------------------------------------------
# `role` is the job this model is the DEFAULT for. `ladder` is what actually
# runs when it is chosen: the model itself, then the hedge rungs behind it.
CATALOG: list[dict] = [
    {
        "id": "nemotron-nano-30b",
        "model": "nvidia/nemotron-3-nano-30b-a3b",
        "label": "Nemotron Nano 30B",
        "vendor": "NVIDIA",
        "role": "chat",
        "license": "NVIDIA Open Model License",
        "params": "30B (A3B MoE)",
        "blurb": "General conversation, writing and explanation. Only 3B "
                 "parameters are active per token, so it answers like a small "
                 "model and writes like a large one.",
        "ttft": 2.5,
        "ladder": [
            "nvidia/nemotron-3-nano-30b-a3b",
            "meta/llama-3.1-8b-instruct",
            ANCHOR,
        ],
    },
    {
        "id": "nemotron-reasoning-30b",
        "model": "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
        "label": "Nemotron Reasoning 30B",
        "vendor": "NVIDIA",
        "role": "code",
        "license": "NVIDIA Open Model License",
        "params": "30B (A3B MoE)",
        "blurb": "Code, mathematics and step-by-step reasoning — one model for "
                 "all three, because they are one skill.",
        "ttft": 2.0,
        "ladder": [
            "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
            "nvidia/nemotron-3-nano-30b-a3b",
            "meta/llama-3.1-8b-instruct",
        ],
    },
    {
        "id": "nemotron-vl-12b",
        "model": "nvidia/nemotron-nano-12b-v2-vl",
        "label": "Nemotron Vision 12B",
        "vendor": "NVIDIA",
        "role": "vision",
        "license": "NVIDIA Open Model License",
        "params": "12B",
        "blurb": "Reads images, screenshots, charts and diagrams, and answers "
                 "questions about them.",
        "ttft": 0.9,
        "ladder": [
            "nvidia/nemotron-nano-12b-v2-vl",
            "nvidia/llama-3.1-nemotron-nano-vl-8b-v1",
            "meta/llama-3.2-11b-vision-instruct",
        ],
    },
    {
        "id": "nemotron-super-120b",
        "model": "nvidia/nemotron-3-super-120b-a12b",
        "label": "Nemotron Super 120B",
        "vendor": "NVIDIA",
        "role": "research",
        "license": "NVIDIA Open Model License",
        "params": "120B (A12B MoE)",
        "blurb": "Deep research and web search. The largest model that answers "
                 "promptly on this tier — it reads a bundle of sources without "
                 "losing track of which claim came from which.",
        "ttft": 4.6,
        "ladder": [
            "nvidia/nemotron-3-super-120b-a12b",
            "nvidia/nemotron-3-nano-30b-a3b",
            "meta/llama-3.1-8b-instruct",
        ],
    },
    # --- Extras. Not a role default, but selectable and toggleable. ---------
    {
        "id": "gpt-oss-20b",
        "model": "openai/gpt-oss-20b",
        "label": "GPT-OSS 20B",
        "vendor": "OpenAI",
        "role": "chat",
        "license": "Apache 2.0",
        "params": "20B (MoE)",
        "blurb": "Careful, well-structured prose. Thinks privately before it "
                 "writes, so the first words take a few seconds longer to "
                 "arrive than the default.",
        "ttft": 3.9,
        "ladder": [
            "openai/gpt-oss-20b",
            "nvidia/nemotron-3-nano-30b-a3b",
            ANCHOR,
        ],
    },
    {
        "id": "llama-8b",
        "model": "meta/llama-3.1-8b-instruct",
        "label": "Llama 3.1 8B",
        "vendor": "Meta",
        "role": "fast",
        "license": "Llama 3.1 Community",
        "params": "8B",
        "blurb": "The quick one — first token in well under a second. Good for "
                 "short back-and-forth.",
        "ttft": 0.6,
        "ladder": ["meta/llama-3.1-8b-instruct", ANCHOR],
    },
    {
        "id": "nemotron-mini-4b",
        "model": ANCHOR,
        "label": "Nemotron Mini 4B",
        "vendor": "NVIDIA",
        "role": "fast",
        "license": "NVIDIA Open Model License",
        "params": "4B",
        "blurb": "Smallest and warmest. This is the model that answers when "
                 "everything else is cold, so it can never be switched off.",
        "ttft": 0.7,
        "ladder": [ANCHOR],
        "locked": True,
    },
]

BY_ID = {m["id"]: m for m in CATALOG}

# Which catalogue role serves which core agent. Coding and reasoning both map to
# `code` — see the module docstring.
AGENT_ROLE = {
    "chat": "chat",
    "coding": "code",
    "reasoning": "code",
    "research": "research",
    "vision": "vision",
}

ROLE_LABEL = {
    "chat": "Chat",
    "code": "Code, maths & reasoning",
    "vision": "Vision",
    "research": "Deep research & web search",
    "fast": "Fast replies",
}

# Models held warm by the keeper thread. The MoE instances are here because
# their cold start is the difference between a 502 and 1.6s — same model, same
# prompt, minutes apart.
WARM = [
    "nvidia/nemotron-3-nano-30b-a3b",
    "nvidia/nemotron-3-super-120b-a12b",
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
    "meta/llama-3.1-8b-instruct",
    ANCHOR,
]


# --- Preferences ------------------------------------------------------------
_lock = threading.Lock()
_cache: dict | None = None

DEFAULT_PREFS = {"enabled": {}, "auto": True}


def _read() -> dict:
    global _cache
    if _cache is not None:
        return _cache
    data = dict(DEFAULT_PREFS)
    try:
        if PREFS_PATH.exists():
            raw = json.loads(PREFS_PATH.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                en = raw.get("enabled")
                data["enabled"] = {str(k): bool(v) for k, v in en.items()} \
                    if isinstance(en, dict) else {}
                data["auto"] = bool(raw.get("auto", True))
    except Exception as e:  # a corrupt prefs file must not stop the server
        log.warning("model prefs unreadable, using defaults: %s", e)
    _cache = data
    return data


def _write(data: dict) -> None:
    global _cache
    _cache = data
    # Atomic. A truncated prefs file is unreadable, and `_read` answers an
    # unreadable file with DEFAULT_PREFS — so a crash mid-save would quietly
    # switch every model back on and lose the user's choices.
    if not save_json(PREFS_PATH, data):
        log.warning("could not save model prefs to %s", PREFS_PATH)


def is_enabled(model_id: str) -> bool:
    """Enabled unless explicitly turned off. A model added to the catalogue in a
    later build is therefore ON for existing users, rather than invisible until
    someone finds the switch."""
    if BY_ID.get(model_id, {}).get("locked"):
        return True
    return _read()["enabled"].get(model_id, True)


def auto_enabled() -> bool:
    return _read()["auto"]


def set_enabled(model_id: str, on: bool) -> None:
    if model_id not in BY_ID or BY_ID[model_id].get("locked"):
        return
    with _lock:
        data = dict(_read())
        data["enabled"] = {**data["enabled"], model_id: bool(on)}
        _write(data)


def set_auto(on: bool) -> None:
    with _lock:
        data = dict(_read())
        data["auto"] = bool(on)
        _write(data)


# --- Resolution -------------------------------------------------------------
# nvapi model id -> catalogue id, for the rung filter below. Rungs that are not
# in the catalogue (the second vision model, for instance) have no toggle and
# are therefore always allowed.
_MODEL_TO_ID = {m["model"]: m["id"] for m in CATALOG}


def _dedupe(rungs: list[str]) -> list[str]:
    return list(dict.fromkeys(r for r in rungs if r))


def _allowed(rungs: list[str]) -> list[str]:
    """Drop switched-off models from a ladder.

    Applied to HEDGE rungs as well as the leading one. A model the user turned
    off appears inside several other ladders as a fallback, and "off" has to
    mean it is never called — otherwise disabling a model merely demotes it, and
    the user still sees its name credited on an answer.

    A rung that is not IN the catalogue has no switch and is therefore always
    allowed. That test has to come first: written as a second `if` clause it ran
    AFTER the lookup it was meant to guard, so the first hedge rung without a
    catalogue entry — the second vision model — raised KeyError instead of being
    kept. The order of the two conditions is the whole fix.
    """
    return _dedupe([r for r in rungs
                    if r not in _MODEL_TO_ID or is_enabled(_MODEL_TO_ID[r])])


# Where a role looks for a substitute once every model of its own is switched
# off. Ordered by how close the substitute is to the original job.
_SUBSTITUTES = {
    "chat": ["fast", "code", "research"],
    "code": ["chat", "research", "fast"],
    "research": ["chat", "code", "fast"],
    "fast": ["chat", "code", "research"],
    # Vision has none, deliberately. See below.
    "vision": [],
}


def ladder_for_role(role: str) -> list[str]:
    """The ladder for a role, honouring the on/off toggles.

    A disabled model is genuinely not run. When a role's own models are all
    switched off the turn moves to a NEIGHBOURING role's enabled model rather
    than quietly re-enabling the one the user turned off — a toggle that the
    product ignores when it finds the choice inconvenient is not a toggle.

    Vision is the one exception and it is a correctness exception, not a
    courtesy: no text model can answer a question about an image, so a vision
    ladder falls back to its own models and never to a text one. A text model
    on an image question does not fail — it invents a description, which is the
    worst possible outcome.
    """
    if role == "vision":
        picks = [m for m in CATALOG if m["role"] == "vision" and is_enabled(m["id"])] \
            or [m for m in CATALOG if m["role"] == "vision"]
        return _dedupe([r for m in picks for r in m["ladder"]])


    picks = [m for m in CATALOG if m["role"] == role and is_enabled(m["id"])]
    if not picks:
        for alt in _SUBSTITUTES.get(role, []):
            picks = [m for m in CATALOG if m["role"] == alt and is_enabled(m["id"])]
            if picks:
                log.info("every %s model is off; substituting %s",
                         role, picks[0]["id"])
                break

    rungs = _allowed([r for m in picks for r in m["ladder"]])
    # The anchor closes every text ladder. It is `locked` in the catalogue, so
    # this is always a model the user has left on — a settings screen must not
    # be able to leave the product unable to answer at all. Appended AFTER the
    # filter so it survives it.
    rungs.append(ANCHOR)
    return _dedupe(rungs)


def ladder_for_model(model_id: str) -> list[str] | None:
    """The ladder for one explicitly chosen model. None if unknown."""
    m = BY_ID.get(model_id)
    if not m:
        return None
    # The chosen model leads unconditionally — choosing it IS enabling it for
    # this turn. Only its hedge rungs are filtered.
    rungs = [m["model"], *_allowed(list(m["ladder"]))]
    if m["role"] != "vision":
        rungs.append(ANCHOR)
    return _dedupe(rungs)


def ladder_for_agent(agent: str, forced_model: str | None = None) -> list[str] | None:
    """What `/api/chat` should run for this turn.

    `forced_model` wins when it names a catalogue entry that is switched on. A
    model the user turned off is ignored rather than honoured — otherwise the
    picker could send a turn to something the settings screen says is disabled.
    """
    if forced_model:
        if is_enabled(forced_model):
            lad = ladder_for_model(forced_model)
            if lad:
                return lad
        else:
            log.info("forced model %s is disabled; routing by role", forced_model)
    role = AGENT_ROLE.get(agent)
    if not role:
        return None
    return ladder_for_role(role)


def default_for_role(role: str) -> dict | None:
    for m in CATALOG:
        if m["role"] == role and is_enabled(m["id"]):
            return m
    return next((m for m in CATALOG if m["role"] == role), None)


def public_catalog() -> list[dict]:
    """The roster as the UI sees it: no ladders (an implementation detail the
    settings screen has no use for), plus the live on/off state."""
    out = []
    for m in CATALOG:
        out.append({
            "id": m["id"],
            "model": m["model"],
            "label": m["label"],
            "vendor": m["vendor"],
            "role": m["role"],
            "roleLabel": ROLE_LABEL.get(m["role"], m["role"]),
            "license": m["license"],
            "params": m["params"],
            "blurb": m["blurb"],
            "ttft": m["ttft"],
            "locked": bool(m.get("locked")),
            "enabled": is_enabled(m["id"]),
            "isDefault": default_for_role(m["role"]) is not None
                         and default_for_role(m["role"])["id"] == m["id"],
        })
    return out


def env_override() -> str | None:
    """OMNIX_CHAT_MODEL pins one catalogue id for the whole run, for demos."""
    v = (os.environ.get("OMNIX_CHAT_MODEL") or "").strip()
    return v if v in BY_ID else None
