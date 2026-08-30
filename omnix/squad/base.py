"""Shared building blocks for the OMNIX agent squad.

Every agent in the squad (ORACLE, SENTINEL, FORGE, ...) is a `Unit` made of a
handful of `Subagent`s. A subagent is just a named role with a system prompt
that talks to a local Ollama model through `run_llm` / `run_llm_json`. Units
degrade gracefully: if Ollama is down or a model is missing, the LLM calls
return empty and the deterministic parts of each unit still produce output.

This mirrors the AVALON philosophy — deterministic checks are the backbone,
the LLM is the analyst on top — but generalized so all eight agents share one
tiny framework instead of each reinventing a gateway.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Callable, Iterable

# Model tiers. Kept aligned with omnix/config.py so the squad reuses models the
# user already has pulled for the core agents (no new downloads).
MODEL_SMART = "qwen2.5:7b-instruct"   # reasoning / synthesis
MODEL_FAST = "llama3.2:3b"            # quick classification / routing
MODEL_CODE = "qwen2.5-coder:7b"       # code generation / review
MODEL_VISION = "qwen3-vl:8b"          # image understanding

EmitFn = Callable[..., None]


# Every subagent in the squad resolves to one of the four tiers above, so this
# map is the single lever that moves all 31 of them onto the cloud at once.
_TIER_OF = {
    MODEL_SMART: "smart",
    MODEL_FAST: "fast",
    MODEL_CODE: "code",
    MODEL_VISION: "vision",
}


# Squad tiers -> router capabilities. The tier still picks the ladder (see
# below); this only tags the call so PULSE can group by what the work needed.
_CAPABILITY_OF_TIER = {"smart": "reasoning", "fast": "fast",
                       "code": "coding", "vision": "vision"}


def run_llm(model: str, system: str, user: str, *, temperature: float = 0.3,
            num_ctx: int = 8192) -> str:
    """Single-shot completion, cloud-first with the same hedged ladder the core
    agents use. Never raises — returns '' on any failure so a unit's
    deterministic path keeps working when every model is unavailable.

    Routed through omnix.models.router since the restructure. The ladder is
    still CLOUD_TIERS, so which models run is unchanged — what is new is that
    the call is metered (real provider token counts, real cost) and attributed
    to whatever execution is running. Previously this path went straight to
    cloud.stream_ladder, which discards the usage block, and so no agent
    outside AVALON could report what it spent.
    """
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]

    try:
        from .. import nvidia_client
        from ..config import CLOUD_TIERS, local_only, nvidia_enabled
        from ..models.router import router as _router
        tier = _TIER_OF.get(model)
        rungs = CLOUD_TIERS.get(tier) if tier else None
        if rungs and nvidia_enabled() and not local_only() and nvidia_client.available():
            res = _router.generate(
                _CAPABILITY_OF_TIER.get(tier or "", "reasoning"),
                messages=messages, models=list(rungs),
                temperature=temperature, max_tokens=2048,
                # Off across the squad. Nearly every subagent here asks for a
                # JSON shape and falls back to a deterministic path when it
                # cannot parse one, so a model that spends its budget thinking
                # and then flushes the scratchpad into `content` does not fail
                # loudly — it silently downgrades the unit to its fallback while
                # still costing the full latency.
                thinking=False)
            if res.ok and res.text.strip():
                return res.text.strip()
    except Exception:
        pass  # fall through to local

    try:
        from ..config import local_fallback_enabled
        if not local_fallback_enabled():
            # On a low-spec machine the local model usually isn't pulled at all;
            # pretending otherwise just stalls the unit. Its deterministic path
            # handles '' gracefully.
            from .. import nvidia_client as _nv
            from ..config import local_only as _lo
            if not _lo() and _nv.available():
                return ""
    except Exception:
        pass

    try:
        from ..ollama_client import chat
        return chat(model, messages, options={"temperature": temperature,
                                               "num_ctx": num_ctx}).strip()
    except Exception:
        return ""


def run_llm_json(model: str, system: str, user: str, *, temperature: float = 0.2,
                 default=None):
    """Like run_llm but coerces the reply into JSON. `default` on failure."""
    system = system + ("\n\nReply with ONLY valid JSON — no prose, no code "
                       "fences. Output must parse with json.loads.")
    text = run_llm(model, system, user, temperature=temperature)
    parsed = extract_json(text)
    return parsed if parsed is not None else default


def extract_json(text: str):
    """Best-effort JSON extraction, tolerant of code fences and surrounding prose."""
    if not text:
        return None
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, re.IGNORECASE)
    candidate = fence.group(1) if fence else text
    try:
        return json.loads(candidate.strip())
    except Exception:
        pass
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start = candidate.find(open_ch)
        if start == -1:
            continue
        depth = 0
        for i in range(start, len(candidate)):
            c = candidate[i]
            if c == open_ch:
                depth += 1
            elif c == close_ch:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(candidate[start:i + 1])
                    except Exception:
                        break
    return None


# ---------------------------------------------------------------------------
# Shared internet knowledge — every unit and subagent can read the web.
#
# Reads are served from the background-synced knowledge cache first (so they are
# instant and work offline); on a cold miss we fall back to a live search and
# opportunistically store the result. Every recall also registers the query as
# an interest, so the sync daemon keeps that topic fresh from then on. All calls
# are guarded: if the knowledge base or the network is unavailable, callers get
# an empty list / string and their deterministic paths keep working.
# ---------------------------------------------------------------------------
def knowledge_recall(query: str, k: int = 5) -> list[dict]:
    """Return up to k {title,url,snippet,content} docs relevant to query,
    cache-first with a live-search fallback. Never raises."""
    query = (query or "").strip()
    if not query:
        return []
    docs: list[dict] = []
    try:
        from ..agent_knowledge import shared_kb
        kb = shared_kb()
        kb.note_interest(query)          # daemon will keep this fresh
        docs = kb.recall(query, k)
    except Exception:
        docs = []
    if docs:
        return docs
    # Cold cache — try one live search and seed the cache for next time.
    try:
        from ..tools import websearch
        results = websearch.search(query, max_results=k)
        results = [r for r in results if r.get("url")]  # drop error rows
        try:
            from ..agent_knowledge import shared_kb
            shared_kb().remember(query, results)
        except Exception:
            pass
        return [{"title": r.get("title", ""), "url": r.get("url", ""),
                 "snippet": r.get("snippet", ""), "content": ""} for r in results]
    except Exception:
        return []


def research_context(query: str, k: int = 5) -> str:
    """Numbered, prompt-ready context block from the knowledge base. '' if none."""
    docs = knowledge_recall(query, k)
    if not docs:
        return ""
    lines = []
    for i, d in enumerate(docs, start=1):
        body = (d.get("content") or d.get("snippet") or "").strip()
        lines.append(f"[{i}] {d.get('title','')}\n{body}\nURL: {d.get('url','')}")
    return "\n\n".join(lines)


@dataclass
class Subagent:
    """A named role inside a unit. Holds a system prompt and a preferred model."""
    name: str
    role: str                       # one-line description shown in the UI roster
    system: str = ""
    model: str = MODEL_SMART

    def complete(self, user: str, *, temperature: float = 0.3) -> str:
        return run_llm(self.model, self.system or f"You are {self.name}.", user,
                       temperature=temperature)

    def complete_json(self, user: str, *, temperature: float = 0.2, default=None):
        return run_llm_json(self.model, self.system or f"You are {self.name}.",
                            user, temperature=temperature, default=default)

    # -- internet access (shared, cache-first, offline-safe) -------------------
    def recall(self, query: str, k: int = 5) -> list[dict]:
        """Read web-backed knowledge (cache-first). Available to every subagent."""
        return knowledge_recall(query, k)

    def research(self, query: str, k: int = 5) -> str:
        """Prompt-ready web context for grounding this subagent's reasoning."""
        return research_context(query, k)

    def public(self) -> dict:
        """Roster entry for the UI, including the model this subagent really
        runs on right now — which differs between cloud and offline mode, so it
        is resolved live rather than baked in."""
        info = self.model_info()
        return {"name": self.name, "role": self.role,
                "model": info["model"], "model_full": info["full"],
                "backend": info["backend"], "tier": _TIER_OF.get(self.model, "smart")}

    def model_info(self) -> dict:
        try:
            from ..config import active_tier_model
            return active_tier_model(_TIER_OF.get(self.model, "smart"))
        except Exception:
            return {"model": self.model, "full": self.model, "backend": "local",
                    "alternates": []}


# ---------------------------------------------------------------------------
# Result blocks — a tiny, uniform vocabulary the frontend renders generically.
# ---------------------------------------------------------------------------
def stats_block(stats: list[dict]) -> dict:
    """stats: [{'n': '12', 'label': 'Findings'}, ...]"""
    return {"type": "stats", "stats": stats}


def markdown_block(title: str, text: str) -> dict:
    return {"type": "markdown", "title": title, "text": text}


def list_block(title: str, items: list[str]) -> dict:
    return {"type": "list", "title": title, "items": items}


def cards_block(title: str, cards: list[dict]) -> dict:
    """cards: [{'title','badge','badge_color','body'}]"""
    return {"type": "cards", "title": title, "cards": cards}


def code_block(title: str, lang: str, code: str) -> dict:
    return {"type": "code", "title": title, "lang": lang, "code": code}


SEV_COLOR = {
    "critical": "#ff5d7a", "high": "#ff9a62", "medium": "#ffd166",
    "low": "#57d7ff", "info": "#8a92a6", "ok": "#4ade80", "pass": "#4ade80",
}


@dataclass
class UnitResult:
    """Standard output shape every unit returns; the console renders it."""
    summary: str = ""
    blocks: list[dict] = field(default_factory=list)
    meta: dict = field(default_factory=dict)

    def add(self, block: dict) -> None:
        self.blocks.append(block)

    def to_dict(self) -> dict:
        return {"summary": self.summary, "blocks": self.blocks, "meta": self.meta}


@dataclass
class Unit:
    """One squad agent: identity + roster + a run() orchestrator.

    Subclasses set the class attributes and implement `run`. `run` receives the
    input context (dict) and an `emit(stage, detail)` callback for live
    progress, and returns a `UnitResult`.
    """
    code: str = ""            # url-safe id, e.g. "oracle"
    name: str = ""            # display name, e.g. "ORACLE"
    glyph: str = "◆"          # tab/console glyph
    tagline: str = ""         # backronym expansion
    blurb: str = ""           # one-line function
    accent: str = "#57d7ff"   # theme color
    input_label: str = "Input"
    input_kind: str = "text"  # "text" | "textarea" | "url"
    placeholder: str = ""
    needs_input: bool = True
    subagents: list[Subagent] = field(default_factory=list)

    def run(self, ctx: dict, emit: EmitFn) -> UnitResult:  # pragma: no cover
        raise NotImplementedError

    # -- internet access (shared, cache-first, offline-safe) -------------------
    def recall(self, query: str, k: int = 5) -> list[dict]:
        """Read web-backed knowledge (cache-first). Available to every unit."""
        return knowledge_recall(query, k)

    def research(self, query: str, k: int = 5) -> str:
        """Prompt-ready web context for grounding this unit's run."""
        return research_context(query, k)

    # -- helpers ---------------------------------------------------------------
    def catalog(self) -> dict:
        subs = [s.public() for s in self.subagents]
        # Distinct models across this unit's subagents, in first-seen order, so
        # a card can say what it runs on without listing the same name twice.
        seen, models = set(), []
        for s in subs:
            if s.get("model") and s["model"] not in seen:
                seen.add(s["model"])
                models.append(s["model"])
        return {
            "code": self.code, "name": self.name, "glyph": self.glyph,
            "tagline": self.tagline, "blurb": self.blurb, "accent": self.accent,
            "input_label": self.input_label, "input_kind": self.input_kind,
            "placeholder": self.placeholder, "needs_input": self.needs_input,
            "subagents": subs,
            "models": models,
            "backend": subs[0]["backend"] if subs else "local",
        }


def clamp(text: str, n: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else text[: n - 1] + "…"


def str_list(value, *, item_max: int = 250, limit: int = 6) -> list[str]:
    """Coerce a JSON field that SHOULD be a list of strings into one.

    Models answer `"watch": "Monitor Russian retaliation"` about as often as
    they answer with a list, and the obvious comprehension —
    `[str(x) for x in payload.get("watch") or []]` — then iterates the string
    CHARACTER BY CHARACTER. Capping that to four items yields `['R','u','s','s']`,
    which is not an error anywhere: it is a well-formed list of strings that
    reaches the UI as four one-letter bullets. Measured on a live situation
    report, four of six analysts came back this way.

    A bare string therefore becomes one item — split on newlines first, since
    the other common shape is one bullet per line in a single string.
    """
    if value is None:
        return []
    if isinstance(value, str):
        parts = [p.strip().lstrip("-*•0123456789.) ").strip()
                 for p in value.splitlines()]
        items = [p for p in parts if p]
    elif isinstance(value, (list, tuple)):
        # Nested lists and dicts are flattened to their text rather than
        # dropped; a dropped item reads as the model having said nothing.
        items = []
        for x in value:
            if isinstance(x, dict):
                x = x.get("text") or x.get("item") or x.get("insight") or x
            items.append(str(x).strip())
        items = [x for x in items if x]
    else:
        items = [str(value).strip()]
    return [clamp(x, item_max) for x in items][:limit]


# Conversational scaffolding the cloud models wrap lists in. The old local
# llama3.2:3b answered bare, so every line WAS an item; the cloud fast tier is
# chattier, and "Sure, here are two search queries:" was being executed as an
# actual ORACLE web search. Stripped centrally because every unit uses bullets().
_PREAMBLE_RE = re.compile(
    r"^(sure|certainly|of course|absolutely|okay|ok|got it|happy to help|"
    r"here('s| is| are)|below (is|are)|these are|i'd be happy)\b", re.IGNORECASE)
# ...and the sign-off on the other end ("Let me know if you need more!").
_TRAILER_RE = re.compile(
    r"^(let me know|hope (this|that) helps|feel free|would you like|"
    r"if you (need|want|'d like)|anything else|i hope)\b", re.IGNORECASE)


def bullets(text: str, limit: int = 8) -> list[str]:
    """Turn an LLM reply into clean bullet strings, dropping chat scaffolding."""
    out: list[str] = []
    for raw in (text or "").splitlines():
        line = raw.strip().lstrip("-*•0123456789.) ").strip()
        if not line:
            continue
        # A lead-in ("Here are three angles:") is never itself an item.
        if (_PREAMBLE_RE.match(line) or _TRAILER_RE.match(line)
                or (line.endswith(":") and not out)):
            continue
        # Markdown emphasis around a whole line is decoration, not content.
        line = line.strip("*_` ").strip()
        if line:
            out.append(line)
        if len(out) >= limit:
            break
    return out
