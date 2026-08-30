"""Thin wrapper over the ollama python package."""

from collections.abc import Iterator

import ollama

# Keys that live in an agent's `options` dict for the CLOUD path and mean
# nothing to Ollama. `thinking` is NVIDIA's chat-template switch; Ollama has its
# own top-level `think` argument with different semantics, and passing an
# unrecognised key inside `options` is at best ignored and at worst rejected.
# Stripped here, once, rather than at each of the four call sites.
_CLOUD_ONLY_OPTIONS = ("thinking",)


def _local_options(options: dict | None) -> dict:
    return {k: v for k, v in (options or {}).items()
            if k not in _CLOUD_ONLY_OPTIONS}


def stream_chat(
    model: str,
    messages: list[dict],
    options: dict | None = None,
) -> Iterator[str]:
    """Stream a chat completion from a local Ollama model, yielding text chunks."""
    stream = ollama.chat(
        model=model,
        messages=messages,
        options=_local_options(options),
        stream=True,
        keep_alive="30m",  # keep the model resident so later turns start fast
    )
    for chunk in stream:
        content = chunk.get("message", {}).get("content", "")
        if content:
            yield content


def chat(model: str, messages: list[dict], options: dict | None = None) -> str:
    """Non-streaming chat completion, returns the full response text."""
    response = ollama.chat(
        model=model,
        messages=messages,
        options=_local_options(options),
        stream=False,
        keep_alive="30m",
    )
    return response.get("message", {}).get("content", "")
