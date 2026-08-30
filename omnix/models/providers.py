"""Provider implementations behind one interface.

Promoted from `avalon/gateway.py`, which already had the right shape — an ABC
with Ollama and NVIDIA implementations and a `Usage` type — but lived inside the
QA subsystem where nothing else could reach it.

One deliberate change from the AVALON version. Its NVIDIA backend went through
the hedged *streaming* ladder and then estimated tokens at ~4 chars each,
because an SSE stream returns no usage block. Every agent call in OMNIX
nevertheless joins the whole stream before using it — nobody actually renders
tokens as they arrive — so streaming bought hedging and cost exact accounting.

So `generate()` here uses the non-streaming endpoint and reads the provider's
own `usage` object. Real input/output counts, real cost. `stream()` keeps the
hedged path for callers that genuinely stream, and marks its usage estimated so
the difference is visible in the data rather than assumed away.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    # True when counts were derived from character length instead of reported.
    estimated: bool = False

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class LLMResponse:
    text: str = ""
    model: str = ""
    provider: str = ""
    usage: Usage = field(default_factory=Usage)
    latency_ms: int = 0
    raw: dict = field(default_factory=dict)


class ProviderError(RuntimeError):
    """Any provider failure. The router treats it as 'try the next rung'."""


class ModelProvider(ABC):
    """The interface application code is allowed to depend on.

    No provider-specific response shape may escape an implementation — that is
    the whole point, and it is what keeps a future OpenAI/Anthropic/local swap
    from touching agent code.
    """

    name: str = ""

    @abstractmethod
    def generate(self, model: str, messages: list[dict], *,
                 temperature: float = 0.2, max_tokens: int | None = None,
                 json_mode: bool = False, timeout: float = 120.0,
                 thinking: bool | None = None) -> LLMResponse:
        ...

    def stream(self, model: str, messages: list[dict], *,
               temperature: float = 0.2, max_tokens: int | None = None,
               timeout: float = 120.0, thinking: bool | None = None):
        """Yield text chunks. Default: no true streaming, emit one chunk."""
        yield self.generate(model, messages, temperature=temperature,
                            max_tokens=max_tokens, timeout=timeout,
                            thinking=thinking).text

    def tool_call(self, model: str, messages: list[dict], tools: list[dict],
                  **kw) -> LLMResponse:
        raise ProviderError(f"{self.name} does not implement tool calling")

    def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        raise ProviderError(f"{self.name} does not implement embeddings")

    @abstractmethod
    def health(self) -> tuple[bool, str]:
        ...


# ---------------------------------------------------------------------------
# NVIDIA NIM
# ---------------------------------------------------------------------------
class NvidiaProvider(ModelProvider):
    name = "nvidia"

    def generate(self, model, messages, *, temperature=0.2, max_tokens=None,
                 json_mode=False, timeout=120.0,
                 thinking: bool | None = None) -> LLMResponse:
        import httpx

        from .. import nvidia_client

        key = nvidia_client.api_key()
        if not key:
            raise ProviderError("no NVIDIA API key configured")

        if json_mode:
            messages = list(messages)
            if messages and messages[0].get("role") == "system":
                messages[0] = dict(messages[0])
                messages[0]["content"] += (
                    "\n\nReply with ONLY valid JSON — no prose, no code fences.")

        # A reasoning model bills its private thinking against `max_tokens`, so
        # a caller's small budget is spent before the answer starts and the
        # request returns HTTP 200 with an empty `content`. The streaming path
        # has enforced a floor for this since gpt-oss; this path never did,
        # which is why ORACLE's 256-token triage and 512-token planner calls
        # came back blank once a reasoning model led their ladder. Same rule,
        # same constant — the two paths must not disagree about it.
        budget = max_tokens or 2048
        if nvidia_client._is_reasoning_model(model):
            budget = max(budget, nvidia_client.REASONING_MIN_TOKENS)
        # Prompt + max_tokens over the model's window is HTTP 400, not a
        # truncated answer. Same clamp as the streaming path.
        budget = nvidia_client.fit_max_tokens(model, messages, budget)

        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": budget,
            "stream": False,
        }
        nvidia_client._apply_thinking(payload, model, thinking)
        t0 = time.time()
        try:
            with httpx.Client(timeout=timeout) as client:
                r = client.post(
                    nvidia_client.BASE_URL + "/chat/completions",
                    headers={"Authorization": f"Bearer {key}",
                             "Content-Type": "application/json"},
                    json=payload)
        except Exception as e:
            raise ProviderError(f"{model}: {type(e).__name__}: {e}") from e
        latency = int((time.time() - t0) * 1000)

        if r.status_code >= 400:
            raise ProviderError(f"{model}: HTTP {r.status_code} {r.text[:200]}")
        try:
            data = r.json()
        except Exception as e:
            raise ProviderError(f"{model}: unparseable response") from e

        choices = data.get("choices") or []
        msg = (choices[0].get("message") or {}) if choices else {}
        text = (msg.get("content") or "").strip()
        # Reasoning models may put the answer in a separate field and leave
        # content empty; treat that as the answer rather than as a failure.
        if not text:
            text = (msg.get("reasoning_content") or "").strip()

        u = data.get("usage") or {}
        usage = Usage(
            input_tokens=int(u.get("prompt_tokens") or 0),
            output_tokens=int(u.get("completion_tokens") or 0),
            estimated=not bool(u),
        )
        if usage.estimated:
            usage.input_tokens = sum(len(m.get("content") or "") for m in messages) // 4
            usage.output_tokens = len(text) // 4

        return LLMResponse(text=text, model=data.get("model") or model,
                           provider=self.name, usage=usage, latency_ms=latency,
                           raw={"finish_reason": (choices[0].get("finish_reason")
                                                  if choices else "")})

    def stream(self, model, messages, *, temperature=0.2, max_tokens=None,
               timeout=120.0, thinking: bool | None = None):
        from .. import nvidia_client
        yield from nvidia_client.stream_chat(
            messages, {"temperature": temperature,
                       "max_tokens": max_tokens or 2048,
                       "thinking": thinking}, model_id=model)

    def health(self) -> tuple[bool, str]:
        try:
            from .. import nvidia_client
            if nvidia_client.available():
                return True, ""
            return False, "no NVIDIA API key configured"
        except Exception as e:
            return False, f"{type(e).__name__}"


# ---------------------------------------------------------------------------
# Ollama (local / offline dev)
# ---------------------------------------------------------------------------
class OllamaProvider(ModelProvider):
    name = "ollama"

    def generate(self, model, messages, *, temperature=0.2, max_tokens=None,
                 json_mode=False, timeout=120.0,
                 thinking: bool | None = None) -> LLMResponse:
        # `thinking` is accepted and ignored: no local Ollama model here exposes
        # the switch, and refusing the argument would make the two providers
        # non-interchangeable, which is the one thing this interface exists for.
        import httpx

        options = {"temperature": temperature}
        if max_tokens:
            options["num_predict"] = max_tokens
        payload = {"model": model, "messages": messages, "stream": False,
                   "options": options}
        if json_mode:
            payload["format"] = "json"
        t0 = time.time()
        try:
            with httpx.Client(timeout=timeout) as client:
                r = client.post("http://127.0.0.1:11434/api/chat", json=payload)
                r.raise_for_status()
                data = r.json()
        except Exception as e:
            raise ProviderError(f"{model}: {type(e).__name__}: {e}") from e
        latency = int((time.time() - t0) * 1000)
        text = ((data.get("message") or {}).get("content") or "").strip()
        # Ollama reports exact counts; no estimation needed.
        usage = Usage(input_tokens=int(data.get("prompt_eval_count") or 0),
                      output_tokens=int(data.get("eval_count") or 0))
        return LLMResponse(text=text, model=model, provider=self.name,
                           usage=usage, latency_ms=latency)

    def health(self) -> tuple[bool, str]:
        try:
            import httpx
            with httpx.Client(timeout=3.0) as client:
                client.get("http://127.0.0.1:11434/api/tags").raise_for_status()
            return True, ""
        except Exception as e:
            return False, f"ollama unreachable ({type(e).__name__})"


_PROVIDERS: dict[str, ModelProvider] = {
    "nvidia": NvidiaProvider(),
    "ollama": OllamaProvider(),
}


def get(name: str) -> ModelProvider:
    p = _PROVIDERS.get(name)
    if p is None:
        raise ProviderError(f"unknown provider: {name}")
    return p


def health_all() -> dict[str, dict]:
    out = {}
    for name, p in _PROVIDERS.items():
        ok, detail = p.health()
        out[name] = {"available": ok, "detail": detail}
    return out
