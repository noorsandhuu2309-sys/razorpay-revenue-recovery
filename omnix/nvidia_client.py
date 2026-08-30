"""Optional NVIDIA NIM cloud backend (build.nvidia.com) — OpenAI-compatible.

This is OMNIX's ONE non-local escape hatch: a higher-quality cloud model, used
only when the user deliberately opts in (env OMNIX_NVIDIA=1) AND an API key is
configured. It never runs by default, so OMNIX stays local-first unless asked.

SECURITY: the API key is NEVER hard-coded here. It is read at call time from
either the NVIDIA_API_KEY environment variable or a git-ignored secrets file
(omnix_secrets.json at the project root). If no key is present, `available()`
returns False and callers transparently fall back to the local Ollama models.
"""

from __future__ import annotations

import json
import os
import random
import re
import time
from collections.abc import Iterator
from pathlib import Path

# OpenAI-compatible endpoint for NVIDIA-hosted models.
BASE_URL = "https://integrate.api.nvidia.com/v1"

# Default cloud model, used only when a caller doesn't name one (the agent path
# always passes its own from config.NVIDIA_MODELS). Override with the
# OMNIX_NVIDIA_MODEL env var. Must be a model id offered on build.nvidia.com.
#
# This has to be a model that actually answers promptly on the free tier: the
# big instances (llama-3.3-70b, deepseek-v4-pro, qwen3-next-80b) cold-start for
# 100s+ or never stream at all, which made the self-test below report a FAILURE
# for a perfectly valid key.
#
# Changed 2026-08-16 from `openai/gpt-oss-20b`, which reasons privately before
# it writes: 3.9-11.4s of silence and a 17s median answer. This one measured
# 2.5s to first token and 5.7s to a finished one on the same prompt.
DEFAULT_MODEL = "nvidia/nemotron-3-nano-30b-a3b"

# The key file is git-ignored (see .gitignore, which matches the name in any
# directory). We look in a few sensible spots so it works whether it sits at the
# project root or inside the omnix/ package folder.
_ROOT = Path(__file__).resolve().parents[1]   # OMNIX/
_PKG = Path(__file__).resolve().parent        # OMNIX/omnix/
_SECRETS_CANDIDATES = [
    _ROOT / "omnix_secrets.json",
    _PKG / "omnix_secrets.json",
    Path.cwd() / "omnix_secrets.json",
]


def _secrets_path() -> Path | None:
    for p in _SECRETS_CANDIDATES:
        try:
            if p.exists():
                return p
        except Exception:
            continue
    return None


def _extract_key(raw: str) -> str | None:
    """Pull an NVIDIA key out of a secrets file, tolerant of how it was saved:
    JSON ({"NVIDIA_API_KEY": "..."}), a bare key, or a shell/PowerShell style
    assignment (NVIDIA_API_KEY=..., $env:NVIDIA_API_KEY = "...", export ...).
    Never logs the value."""
    if not raw or not raw.strip():
        return None
    # 1) Proper JSON with the expected field.
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            key = data.get("NVIDIA_API_KEY")
            if key and str(key).strip():
                return str(key).strip()
    except Exception:
        pass
    # 2) Any nvapi- token anywhere (covers raw keys and KEY="..." snippets).
    m = re.search(r"nvapi-[A-Za-z0-9_\-]{20,}", raw)
    if m:
        return m.group(0)
    return None


def api_key() -> str | None:
    """Return the NVIDIA API key from the environment or the git-ignored secrets
    file, or None. The raw value is never logged or persisted by OMNIX."""
    env = os.environ.get("NVIDIA_API_KEY")
    if env and env.strip():
        return env.strip()
    path = _secrets_path()
    if path is not None:
        try:
            return _extract_key(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return None


def model() -> str:
    return os.environ.get("OMNIX_NVIDIA_MODEL", "").strip() or DEFAULT_MODEL


def available() -> bool:
    """True only when a key is configured — callers use this to decide whether
    the cloud path is even possible before trying it."""
    return bool(api_key())


class CloudError(RuntimeError):
    """Any failure on the cloud path. Callers treat it as 'try the next model'."""


class FirstTokenTimeout(CloudError):
    """The model accepted the request but produced no token inside the budget.
    This is the free tier's characteristic failure — a cold instance that may
    eventually answer, long after the user has given up."""


class CapacityError(CloudError):
    """429/5xx — the shared free-tier pool is momentarily out of room. Worth
    retrying on the SAME model briefly, unlike a 404 (wrong model) or 401."""


class FatalModelError(CloudError):
    """404/401/400 — retrying this model will never help."""


# How long a model gets to produce its FIRST token before we give up on it.
# Everything downstream is built around this number: the whole point is that a
# stalled model costs the user a couple of seconds, not half a minute.
FIRST_TOKEN_BUDGET = float(os.environ.get("OMNIX_TTFT_BUDGET", "8"))
# Once tokens are flowing, a gap this long means the stream died mid-answer.
STREAM_GAP_TIMEOUT = 45.0

# A flat first-token budget is wrong for a long prompt. The model has to read
# the prompt before it can write anything, and research turns arrive with five
# search results plus two fetched pages inlined — 10-15k characters against the
# ~300 of a chat turn. Judging both by the same 8s stopwatch declared every
# research rung dead before it had finished reading the question, which is how a
# perfectly healthy ladder reported "all cloud models are unavailable".
#
# So the budget grows with the prompt, at roughly the prefill rate these models
# actually manage, and stops at a ceiling — past which the model really is stuck
# rather than busy.
PREFILL_CHARS_PER_S = 1500.0
MAX_FIRST_TOKEN_BUDGET = 30.0


def first_token_budget_for(messages: list[dict], base: float | None = None) -> float:
    """The first-token budget this prompt has earned.

    Exported because `cloud.stream_ladder` sizes its own deadline from the same
    number; the two drifting apart is what lets a ladder abandon a rung it had
    just granted more time to.
    """
    base = FIRST_TOKEN_BUDGET if base is None else base
    chars = sum(len(m.get("content") or "") for m in messages
                if isinstance(m, dict) and isinstance(m.get("content"), str))
    return min(base + chars / PREFILL_CHARS_PER_S, MAX_FIRST_TOKEN_BUDGET)

# Floor on max_tokens for models that emit chain-of-thought, so the reasoning
# can't consume the entire budget and leave no room for the answer.
#
# `nemotron-3` matches the whole current generation — nano-30b, nano-omni and
# super-120b all emit `reasoning_content`, and all three were measured spending
# 700-1700 characters on it before writing a word. Listing only the `-reasoning`
# checkpoint missed the two that became defaults, and a caller asking those for
# 256 tokens (squad triage does) gets a successful, completely empty response.
REASONING_MIN_TOKENS = 1024
_REASONING_HINTS = ("gpt-oss", "-reasoning", "deepseek-r1", "nemotron-3")


def _is_reasoning_model(model_id: str) -> bool:
    mid = (model_id or "").lower()
    return any(h in mid for h in _REASONING_HINTS)


# Models whose chat template exposes a thinking switch, and the request field
# that operates it.
#
# WHY THIS EXISTS. OMNIX's system prompts are ~7,700 characters of instruction,
# and a reasoning model deliberates over all of it before writing a word. On
# "in 60 words, what is compound interest?" that measured:
#
#     nemotron-3-nano-30b-a3b        2.81s thinking  ->  0.84s not
#     nemotron-3-super-120b-a12b    17.95s thinking  ->  0.92s not
#     nemotron-3-nano-omni-...      18.53s thinking  ->  0.70s not
#
# and the answers with thinking OFF were not worse — they were longer and
# better formatted, because the thinking had been eating the token budget. Two
# of those runs also flushed the scratchpad into `content` when the budget ran
# out mid-deliberation, putting "We need to answer in 60 words exactly?" on
# screen as the answer.
#
# Only `chat_template_kwargs` works. A `/no_think` line in the system prompt and
# the OpenAI `reasoning_effort` field were both probed on the same models and
# both were ignored — do not reach for them.
_THINKING_SWITCH = ("nemotron-3",)


def _supports_thinking_switch(model_id: str) -> bool:
    mid = (model_id or "").lower()
    return any(h in mid for h in _THINKING_SWITCH)


def _apply_thinking(payload: dict, model_id: str, thinking) -> None:
    """Add the thinking switch to `payload`, when it is asked for and supported.

    Sent ONLY to models known to read it. It is an unrecognised extra field
    everywhere else, and a provider is entitled to reject those — quietly
    attaching it to every request would be a 400 waiting for the next model
    that is stricter than today's.
    """
    if thinking is None or not _supports_thinking_switch(model_id):
        return
    payload["chat_template_kwargs"] = {"thinking": bool(thinking)}


# Context windows, for the models whose window is small enough to matter. NIM
# rejects a request whose prompt plus `max_tokens` exceeds the model's context
# with HTTP 400, and `stream_chat` classifies a 400 as FatalModelError — so the
# rung is not retried, it is struck off.
#
# That is how the ANCHOR broke. `nemotron-mini-4b` holds 8k tokens; OMNIX's chat
# system prompt is ~2k and the chat agent asks for 4096 output. Fine on its own,
# fatal together, and the anchor exists precisely to be the rung that cannot
# fail. Everything else here holds 128k and never comes close, so only the small
# windows are listed and the default is generous.
# Measured, not looked up. The provider states the real number in the rejection
# itself: "This model's maximum context length is 4096 tokens. However, you
# requested 6174 (2078 in the messages, 4096 in the completion)." The published
# figure for this model is 8k; on this endpoint it is 4k, and trusting the
# documentation is what left the anchor rejecting every chat turn.
_CONTEXT = {
    "nvidia/nemotron-mini-4b-instruct": 4_096,
}
_DEFAULT_CONTEXT = 128_000
# Characters per token, for sizing the prompt. Deliberately low — the rejection
# above puts the real ratio at 4.35 for OMNIX's own system prompt — so the
# estimate errs towards reserving too much room rather than too little. Being
# wrong in the other direction is the HTTP 400 this exists to prevent.
_CHARS_PER_TOKEN = 3.0


def fit_max_tokens(model_id: str, messages: list[dict], want: int) -> int:
    """`want`, reduced to what this model can actually still emit.

    A short answer is a far better outcome than the alternative: `stream_chat`
    classifies HTTP 400 as `FatalModelError`, so an over-long request does not
    merely fail, it strikes the rung off the ladder — and the rung this hits
    hardest is the anchor, whose entire job is to be the one that never fails.
    """
    context = _CONTEXT.get(model_id, _DEFAULT_CONTEXT)
    chars = sum(len(m.get("content") or "") for m in messages
                if isinstance(m, dict) and isinstance(m.get("content"), str))
    # 5% headroom for the chat template's own scaffolding, which counts against
    # the window too and is not in `messages`.
    room = int(context * 0.95) - int(chars / _CHARS_PER_TOKEN)
    # No floor. A floor would be a number we assert fits regardless of what is
    # left, which is the same mistake in a smaller font.
    return max(1, min(want, room))


def stream_chat(messages: list[dict], options: dict | None = None,
                model_id: str | None = None,
                first_token_budget: float | None = None,
                should_stop=None,
                on_alive=None,
                retries: int = 2) -> Iterator[str]:
    """Stream a chat completion from NVIDIA, yielding text chunks — same shape as
    ollama_client.stream_chat, so the rest of OMNIX treats it as a drop-in.

    Two behaviours matter for a live demo on the free tier:

    * `first_token_budget` is enforced as WALL-CLOCK time to the first token,
      separately from the inter-chunk read timeout. An httpx read timeout alone
      can't express "answer within N seconds" — a trickling cold model resets it
      on every keep-alive byte.
    * 429/5xx are retried with jittered backoff, but ONLY before the first token
      (re-sending after partial output would duplicate text). 404/401 raise
      immediately — no amount of retrying fixes a model your account can't call.

    `should_stop` lets a caller abandon this stream (used when a hedged sibling
    model has already won the race).
    """
    key = api_key()
    if not key:
        raise CloudError("NVIDIA_API_KEY is not configured")

    import httpx  # local import: only needed on the cloud path

    budget = first_token_budget_for(messages, first_token_budget)
    opts = options or {}
    mid = model_id or model()

    # Reasoning models spend tokens THINKING before they write a word, and that
    # thinking is billed against max_tokens. Ask gpt-oss-20b for 60 tokens and it
    # burns all 60 reasoning, then stops — returning a perfectly successful,
    # completely empty response. Give reasoning models room for the answer too.
    max_tokens = opts.get("max_tokens") or 2048
    if _is_reasoning_model(mid):
        max_tokens = max(max_tokens, REASONING_MIN_TOKENS)
    # ...then cut back to what the window can hold. The floor above and this
    # ceiling can disagree on a small-context model, and the ceiling has to win:
    # asking for more than fits is a hard 400, while asking for less is merely a
    # shorter answer.
    max_tokens = fit_max_tokens(mid, messages, max_tokens)

    payload = {
        "model": mid,
        "messages": messages,
        "stream": True,
        "temperature": opts.get("temperature", 0.6),
        "top_p": opts.get("top_p", 0.95),
        "max_tokens": max_tokens,
    }
    _apply_thinking(payload, mid, opts.get("thinking"))
    headers = {
        "Authorization": f"Bearer {key}",
        "Accept": "text/event-stream",
        "Content-Type": "application/json",
    }

    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        if should_stop is not None and should_stop():
            return
        started = time.monotonic()
        got_token = False   # real answer text was emitted (blocks retry)
        alive = False       # model produced anything at all, incl. reasoning
        try:
            # `read` is the gap allowed between two chunks, so it governs the
            # WHOLE stream, not just the wait for the first one. Setting it to
            # `budget + 2` therefore capped every answer at ~10s of silence:
            # any model that paused longer than that mid-sentence — routine for
            # a 70B writing a long research answer — raised ReadTimeout with
            # `got_token` already true, which surfaces as "stream broke
            # mid-answer" and leaves a half-written reply on screen. That is the
            # research failure; the first-token budget is enforced by the
            # wall-clock check in the loop below, which is the right instrument
            # for it because it can tell reasoning from silence.
            timeout = httpx.Timeout(STREAM_GAP_TIMEOUT, connect=10.0,
                                    read=STREAM_GAP_TIMEOUT, write=30.0)
            with httpx.stream("POST", BASE_URL + "/chat/completions",
                              json=payload, headers=headers, timeout=timeout) as resp:
                if resp.status_code in (401, 403):
                    raise FatalModelError(f"auth rejected (HTTP {resp.status_code})")
                if resp.status_code in (404, 400):
                    raise FatalModelError(f"model unavailable (HTTP {resp.status_code})")
                if resp.status_code == 429 or resp.status_code >= 500:
                    raise CapacityError(f"HTTP {resp.status_code}")
                resp.raise_for_status()

                for line in resp.iter_lines():
                    if should_stop is not None and should_stop():
                        return
                    # `alive` counts private reasoning too: a thinking model is
                    # working, not stalled, and shouldn't be hedged away for it.
                    if not alive and (time.monotonic() - started) > budget:
                        raise FirstTokenTimeout(
                            f"{mid} produced no token in {budget:.0f}s")
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[len("data:"):].strip()
                    if data == "[DONE]":
                        return
                    try:
                        obj = json.loads(data)
                        delta = obj["choices"][0]["delta"]
                    except Exception:
                        continue
                    # Reasoning models (gpt-oss-*, nemotron-*-reasoning) stream
                    # their chain-of-thought in `reasoning_content`. That is NOT
                    # the answer — emitting it dumps "User wants: ..." into the
                    # chat and breaks every JSON-parsing squad agent. Use it only
                    # as a liveness signal; never yield it.
                    if delta.get("reasoning_content"):
                        if not alive and on_alive is not None:
                            on_alive()   # "I'm thinking" — don't hedge me away
                        alive = True
                    piece = delta.get("content")
                    if piece:
                        if not alive and on_alive is not None:
                            on_alive()
                        alive = True
                        got_token = True
                        yield piece
                return
        except FatalModelError:
            raise
        except FirstTokenTimeout:
            # A cold model rarely warms up within a retry, so don't burn the
            # user's time here — let the caller move down the ladder instead.
            raise
        except httpx.TimeoutException as e:
            if got_token:
                raise CloudError(
                    f"{mid} went quiet for {STREAM_GAP_TIMEOUT:.0f}s "
                    f"mid-answer: {e}") from e
            # Silence, not rejection: the model accepted the request and then
            # sent nothing. Retrying just multiplies the wait, which is exactly
            # the stall the ladder exists to route around. Fail now so it can.
            raise FirstTokenTimeout(
                f"{mid} sent nothing within {STREAM_GAP_TIMEOUT:.0f}s") from e
        except (CapacityError, httpx.TransportError) as e:
            if got_token:
                # Never re-send once the user has seen text — that duplicates it.
                raise CloudError(f"{mid} stream broke mid-answer: {e}") from e
            last_exc = e
            if attempt < retries:
                # Jitter so concurrent OMNIX users don't retry in lockstep and
                # re-create the very capacity spike they're backing off from.
                time.sleep((0.4 * (2 ** attempt)) + random.uniform(0, 0.3))
                continue
        except Exception as e:
            if got_token:
                raise CloudError(f"{mid} stream broke mid-answer: {e}") from e
            last_exc = e
            break

    raise CloudError(f"{mid} failed: {last_exc}")


def warm(model_id: str, timeout: float = 20.0) -> bool:
    """Poke a model with a 1-token request so the instance stays resident.

    Cold start is the single largest latency risk on the free tier — a model
    that answers in 0.5s warm can take 20s+ cold. Called periodically by the
    keeper thread (see omnix/cloud.py)."""
    key = api_key()
    if not key:
        return False
    try:
        import httpx
        r = httpx.post(BASE_URL + "/chat/completions",
                       json={"model": model_id,
                             "messages": [{"role": "user", "content": "hi"}],
                             "max_tokens": 1, "stream": False},
                       headers={"Authorization": f"Bearer {key}",
                                "Content-Type": "application/json"},
                       timeout=timeout)
        return r.status_code == 200
    except Exception:
        return False


def chat(messages: list[dict], options: dict | None = None,
         model_id: str | None = None) -> str:
    """Non-streaming convenience wrapper."""
    return "".join(stream_chat(messages, options, model_id))


def check_auth() -> tuple[bool, str]:
    """Verify the key itself, independently of any model. Hitting the catalog
    costs no inference, so it cleanly separates 'the key is bad' from 'that
    model is cold' — the two failures look identical on a chat call."""
    key = api_key()
    if not key:
        return False, "no key configured"
    try:
        import httpx
        r = httpx.get(BASE_URL + "/models",
                      headers={"Authorization": f"Bearer {key}"}, timeout=30.0)
    except Exception as e:
        return False, f"could not reach NVIDIA: {type(e).__name__}: {e}"
    if r.status_code == 200:
        return True, f"key valid — {len(r.json().get('data', []))} models available"
    if r.status_code in (401, 403):
        return False, f"key REJECTED (HTTP {r.status_code}) — rotate it and update the secrets file"
    return False, f"unexpected HTTP {r.status_code}: {r.text[:200]}"


if __name__ == "__main__":
    # Wiring check the USER runs after pasting their key:
    #   python -m omnix.nvidia_client
    if not available():
        print("No NVIDIA key found. Set NVIDIA_API_KEY or fill omnix_secrets.json.")
        raise SystemExit(1)

    ok, detail = check_auth()
    print(f"1. Auth: {'OK' if ok else 'FAILED'} — {detail}")
    if not ok:
        raise SystemExit(1)

    # Then prove the models the app will really use can answer. A cold model is
    # a model-availability problem, not a key problem, so it's reported as WARN.
    from .config import NVIDIA_MODELS

    msgs = [{"role": "user", "content": "In one sentence, say hello and name yourself."}]
    failures = 0
    for agent, model_id in sorted(set((a, m) for a, m in NVIDIA_MODELS.items())):
        import time
        t0 = time.time()
        try:
            got = "".join(stream_chat(msgs, model_id=model_id))
        except Exception as e:
            print(f"2. {agent:<9} {model_id}: WARN — {type(e).__name__} "
                  f"(cold/unavailable; OMNIX falls back to the local model)")
            failures += 1
            continue
        if not got.strip():
            print(f"2. {agent:<9} {model_id}: WARN — empty response "
                  f"(OMNIX falls back to the local model)")
            failures += 1
            continue
        print(f"2. {agent:<9} {model_id}: OK in {time.time() - t0:.1f}s")

    if failures:
        print(f"\nKey is wired correctly, but {failures} configured model(s) did not "
              f"answer. OMNIX still works — it falls back to local Ollama.")
    else:
        print("\nNVIDIA cloud backend is working. ✅")
