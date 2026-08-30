"""NOVA — Natural-language Orchestration & Virtual Assistant.

The conductor. NOVA reads a plain-language request, figures out which squad
unit should handle it (or answers directly), dispatches to that unit, and
narrates what it did. It's the JARVIS-style front door to the whole squad.
"""

from __future__ import annotations

from dataclasses import replace

from .base import (MODEL_FAST, MODEL_SMART, Subagent, Unit, UnitResult, clamp,
                   markdown_block)

# Intent keyword -> unit code, used as a deterministic fast path / fallback.
#
# ATLAS, WARDEN and MUSE were removed from the product, so NOVA no longer routes
# to them. Their keywords are not simply deleted: planning goes to NOVA itself
# (it answers directly rather than dispatching), and the privacy/secret keywords
# move to SENTINEL, which is where that work belongs now. Dropping them outright
# would have sent "scan this config for secrets" to a general chat answer.
_ROUTES = {
    "oracle": ["research", "find out", "look up", "what is", "who is", "explain", "sources", "latest"],
    "sentinel": ["security", "vulnerab", "headers", "https", "scan the site", "is it safe", "secure",
                 "pii", "compliance", "secret", "leak", "redact", "sensitive", "gdpr", "audit this"],
    "forge": ["code", "write a function", "implement", "refactor", "script", "program", "bug in"],
    "pulse": ["health", "status", "system", "how are you", "diagnostics", "telemetry"],
}


class Nova(Unit):
    code = "nova"
    name = "NOVA"
    glyph = "✧"
    tagline = "Natural-language Orchestration & Virtual Assistant"
    blurb = "Say what you want — NOVA routes it to the right agent."
    accent = "#a0f0e0"
    input_label = "Ask NOVA anything"
    input_kind = "textarea"
    placeholder = "e.g. Scan https://example.com for security issues"

    def __init__(self):
        self.subagents = [
            Subagent("Intent Parser", "classifies the request", model=MODEL_FAST,
                     system=("You are NOVA's Intent Parser. Route a user request to "
                             "ONE agent. Options: oracle (web research), sentinel "
                             "(security review of a site, repo or text — including "
                             "PII, leaked secrets and privacy exposure), forge "
                             "(write or change code), pulse (system health and "
                             "telemetry), chat (general answer, including planning "
                             "and creative requests). Reply as JSON: "
                             '{"unit":"<code>","reason":"<short>"}.')),
            Subagent("Dispatcher", "hands off to the chosen unit"),
            Subagent("Responder", "answers directly when no specialist fits",
                     model=MODEL_SMART, system=(
                         "You are NOVA, OMNIX's assistant. Answer the user's "
                         "request helpfully and concisely in Markdown.")),
        ]

    # Valid dispatch targets. Deliberately excludes "nova" so NOVA can never
    # route to itself (which would recurse into run() forever), and the retired
    # units so a stale model reply cannot resurrect one.
    _DISPATCHABLE = {"oracle", "sentinel", "forge", "pulse"}

    # Models the console may pick for the Intent Parser. Whitelisted because the
    # value arrives from the client and is passed straight to Ollama.
    PARSER_MODELS = {MODEL_FAST, MODEL_SMART}

    def _parser(self, model: str | None) -> Subagent:
        """Intent Parser, optionally swapped to a caller-chosen model."""
        base = self.subagents[0]
        if model and model in self.PARSER_MODELS and model != base.model:
            return replace(base, model=model)
        return base

    def _classify(self, text: str, *, model: str | None = None,
                  temperature: float | None = None) -> tuple[str, str]:
        low = text.lower()
        for code, kws in _ROUTES.items():
            if any(k in low for k in kws):
                return code, "matched routing keywords"
        kw = {} if temperature is None else {"temperature": temperature}
        parsed = self._parser(model).complete_json(
            f"Request: {text}", default={"unit": "chat", "reason": "default"}, **kw)
        if not isinstance(parsed, dict):
            return "chat", "unparseable routing reply"
        unit = str(parsed.get("unit", "chat") or "chat").lower()
        # Anything that isn't a real specialist falls back to a direct answer.
        if unit not in self._DISPATCHABLE:
            unit = "chat"
        return unit, str(parsed.get("reason", ""))

    def run(self, ctx, emit) -> UnitResult:
        text = (ctx.get("input") or "").strip()
        res = UnitResult()
        if not text:
            res.summary = "Nothing to do — give NOVA a request."
            return res

        # Console-supplied routing controls (both optional, both clamped).
        parser_model = ctx.get("parser_model") or None
        temp = ctx.get("temperature")
        try:
            temp = None if temp is None else min(1.0, max(0.0, float(temp)))
        except (TypeError, ValueError):
            temp = None

        emit("route", "Intent Parser classifying the request")
        unit_code, reason = self._classify(text, model=parser_model, temperature=temp)
        emit("route", f"→ routing to {unit_code.upper()} ({reason})")

        from .units import get_unit  # deferred: avoids circular import
        target = get_unit(unit_code) if unit_code in self._DISPATCHABLE else None

        if target is not None:
            emit("dispatch", f"Dispatcher engaging {target.name}")
            try:
                sub = target.run({"input": text,
                                  **{k: v for k, v in ctx.items() if k != "input"}}, emit)
                sub.summary = (f"**NOVA → {target.name}** — {reason}\n\n" + (sub.summary or ""))
                sub.meta = {**(sub.meta or {}), "routed_to": unit_code, "reason": reason}
                return sub
            except Exception as e:
                # A specialist choking must not hard-fail the front door — fall
                # through to a direct answer, but record what happened.
                emit("dispatch", f"{target.name} failed ({type(e).__name__}); answering directly")
                res.meta = {"dispatch_error": f"{type(e).__name__}: {str(e)[:160]}"}

        emit("respond", "Responder answering directly")
        answer = self.subagents[2].complete(
            text, temperature=0.6 if temp is None else temp)
        res.summary = clamp(answer or "I couldn't generate a response.", 3000)
        res.add(markdown_block("Routing", f"Handled directly (general chat). "
                               f"Reason: {reason or 'no specialist matched'}."))
        res.meta = {**res.meta, "routed_to": "chat", "reason": reason}
        return res
