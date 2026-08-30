"""OpenRouter backend — OpenAI-compatible, and the intended production path.

WHY THIS EXISTS ALONGSIDE `nvidia_client`
-----------------------------------------
Everything currently runs against `build.nvidia.com`, whose own header in
`cloud.py` describes it as "a free tier that offers no capacity guarantee".
NVIDIA's production path is an AI Enterprise licence — a fixed annual cost per
GPU, payable before the first customer. OpenRouter is pure variable cost, which
is the only shape that works when the budget is $43 a month: cost of goods
scales *with* revenue instead of preceding it.

The swap is small because both speak the OpenAI chat-completions protocol. This
module is deliberately a near-mirror of `nvidia_client` rather than a clever
abstraction over both — the two differ in auth headers, error envelopes and
model naming, and a shared base class that hid those differences would have to
leak them back out at exactly the points where they matter.

RESELLING, AND WHY THE KEY NEVER LEAVES THE SERVER
--------------------------------------------------
OpenRouter's terms §7.4 prohibit "reselling API access to Models". OMNIX sells
research runs, not model access: the key is server-side, users receive no
credential, and credits are closed-loop. That distinction is the difference
between a normal SaaS cost of goods and a terms violation, and it is why there
is no code path anywhere in this module that returns a key to a caller.

NOT CONFIGURED IS A STATE, NOT AN ERROR
---------------------------------------
With no `OPENROUTER_API_KEY` this module reports itself unavailable and names
the variable. It never falls back to another provider silently — a system that
quietly answers from somewhere else is a system whose costs and quality cannot
be reasoned about.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterator
from pathlib import Path

import httpx

log = logging.getLogger("omnix.openrouter")

BASE_URL = "https://openrouter.ai/api/v1"
KEY_ENV = "OPENROUTER_API_KEY"

_ROOT = Path(__file__).resolve().parent.parent
_SECRETS = _ROOT / "omnix_secrets.json"


class OpenRouterError(RuntimeError):
    """A call failed in a way the caller may retry or hedge past."""


class FatalModelError(OpenRouterError):
    """This model will never work — do not retry it, move down the ladder.

    Separated from the retryable case because a hedged ladder that retries a
    model id the provider does not recognise burns the whole budget rediscovering
    the same 404.
    """


def api_key() -> str | None:
    """The key, from the environment or the git-ignored secrets file."""
    key = (os.environ.get(KEY_ENV) or "").strip()
    if key:
        return key
    try:
        data = json.loads(_SECRETS.read_text(encoding="utf-8"))
        key = (data.get("openrouter_api_key") or "").strip()
        return key or None
    except (OSError, json.JSONDecodeError, AttributeError):
        return None


def available() -> bool:
    return bool(api_key())


def status() -> dict:
    """What the health surface shows. Never includes the key itself."""
    key = api_key()
    if not key:
        return {"configured": False, "provider": "openrouter",
                "fixKey": KEY_ENV,
                "detail": f"Set {KEY_ENV} to use OpenRouter.",
                "docsUrl": "https://openrouter.ai/docs/api-reference/authentication"}
    return {"configured": True, "provider": "openrouter",
            # Enough to tell two keys apart in a log, not enough to use.
            "keyFingerprint": f"…{key[-4:]}",
            "baseUrl": BASE_URL}


def _headers() -> dict[str, str]:
    key = api_key()
    if not key:
        raise OpenRouterError(
            f"{KEY_ENV} is not set — OpenRouter is not configured.")
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    # OpenRouter uses these for its public app-ranking pages. They are optional
    # and both are the operator's own identity, never a user's.
    referer = (os.environ.get("OMNIX_PUBLIC_URL") or "").strip()
    if referer:
        headers["HTTP-Referer"] = referer
    headers["X-Title"] = os.environ.get("OMNIX_APP_NAME") or "OMNIX"
    return headers


def _raise_for_status(resp: httpx.Response, model: str) -> None:
    if resp.status_code == 200:
        return
    body = resp.text[:300]
    if resp.status_code in (400, 404):
        # Unknown model, or a request this model cannot accept. Retrying is
        # pointless; the ladder must move on.
        raise FatalModelError(
            f"OpenRouter rejected '{model}' (HTTP {resp.status_code}): {body}")
    if resp.status_code == 401:
        raise FatalModelError(
            f"OpenRouter rejected the API key (HTTP 401). Check {KEY_ENV}.")
    if resp.status_code == 402:
        raise FatalModelError(
            "OpenRouter reports insufficient credit (HTTP 402). "
            "Top up the account before retrying.")
    if resp.status_code == 429:
        raise OpenRouterError(f"OpenRouter rate-limited '{model}' (HTTP 429)")
    raise OpenRouterError(
        f"OpenRouter error for '{model}' (HTTP {resp.status_code}): {body}")


def stream_chat(model: str, messages: list[dict], *,
                temperature: float = 0.3, max_tokens: int = 2048,
                timeout: float = 120.0,
                extra: dict | None = None) -> Iterator[str]:
    """Yield content deltas. Reasoning deltas are consumed but not yielded.

    Some models emit `reasoning` before any `content`. Yielding it would print
    a private scratchpad into the answer; dropping it silently would make the
    model look hung. The caller sees nothing until real content arrives, which
    is what the first-token budget in `cloud.py` is measuring.
    """
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": True,
        **(extra or {}),
    }

    try:
        with httpx.stream("POST", f"{BASE_URL}/chat/completions",
                          headers=_headers(), json=payload,
                          timeout=timeout) as resp:
            if resp.status_code != 200:
                resp.read()
                _raise_for_status(resp, model)

            for line in resp.iter_lines():
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    return
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    # OpenRouter interleaves ": OPENROUTER PROCESSING" comment
                    # lines as keep-alives. They are not JSON and not an error.
                    continue
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                content = delta.get("content")
                if content:
                    yield content
    except httpx.TimeoutException as e:
        raise OpenRouterError(f"'{model}' timed out after {timeout:.0f}s") from e
    except httpx.HTTPError as e:
        raise OpenRouterError(f"could not reach OpenRouter: {e}") from e


def complete(model: str, messages: list[dict], *, temperature: float = 0.3,
             max_tokens: int = 2048, timeout: float = 120.0) -> str:
    """Non-streaming completion. Returns the text."""
    payload = {"model": model, "messages": messages,
               "temperature": temperature, "max_tokens": max_tokens}
    try:
        resp = httpx.post(f"{BASE_URL}/chat/completions", headers=_headers(),
                          json=payload, timeout=timeout)
    except httpx.TimeoutException as e:
        raise OpenRouterError(f"'{model}' timed out after {timeout:.0f}s") from e
    except httpx.HTTPError as e:
        raise OpenRouterError(f"could not reach OpenRouter: {e}") from e

    _raise_for_status(resp, model)
    data = resp.json()
    choices = data.get("choices") or []
    if not choices:
        raise OpenRouterError(f"'{model}' returned no choices")
    return (choices[0].get("message") or {}).get("content") or ""


def list_models(timeout: float = 30.0) -> list[dict]:
    """Every model OpenRouter currently serves, with its pricing.

    This is what the roster re-probe should read rather than a hardcoded list —
    `model_catalog.py` records three models that looked good on a one-line
    prompt and were unusable on a real one, and inheriting a list is exactly
    the mistake that file warns against.
    """
    try:
        resp = httpx.get(f"{BASE_URL}/models", headers=_headers(), timeout=timeout)
    except httpx.HTTPError as e:
        raise OpenRouterError(f"could not reach OpenRouter: {e}") from e
    _raise_for_status(resp, "<catalog>")
    return resp.json().get("data") or []


def check() -> dict:
    """A wiring check, mirroring `python -m omnix.nvidia_client`."""
    st = status()
    if not st["configured"]:
        return {**st, "ok": False}
    try:
        models = list_models()
    except OpenRouterError as e:
        return {**st, "ok": False, "detail": str(e)}
    return {**st, "ok": True, "modelCount": len(models)}


if __name__ == "__main__":
    result = check()
    print(json.dumps(result, indent=2))
    if not result.get("ok"):
        raise SystemExit(1)
