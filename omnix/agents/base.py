"""Base Agent class: builds a message list and streams a response.

OMNIX is cloud-first (see omnix/cloud.py for the reasoning). The provider choice
lives here so every agent inherits identical behaviour.
"""

from collections.abc import Iterator

from .. import cloud, ollama_client
from ..config import (AGENTS, CLOUD_LADDER, local_fallback_enabled, local_only,
                      nvidia_enabled)
from .. import nvidia_client


def stream_reply(agent_name: str, model: str, messages: list[dict],
                 options: dict, on_model=None,
                 ladder: list[str] | None = None) -> Iterator[str]:
    """Stream a reply for `messages`.

    Walks this agent's cloud ladder, hedging onto the next rung when one stalls,
    and only drops to local Ollama when a developer has explicitly asked for that
    (OMNIX_LOCAL_FALLBACK=1) or is running offline (OMNIX_LOCAL_ONLY=1).

    `ladder` overrides the agent's default rungs. That is how the user's model
    picker takes effect: `model_catalog.ladder_for_agent()` builds a ladder from
    the chosen model plus its hedges, and it arrives here already filtered by
    the on/off toggles. Passing None keeps the historical behaviour, so every
    caller that does not know about the picker is unaffected.

    `on_model(name)` is called with the model that ACTUALLY produced the answer,
    so the UI can name the real backend instead of the one we hoped would win.
    """
    if ladder is None:
        ladder = CLOUD_LADDER.get(agent_name) or []

    if ladder and nvidia_enabled() and not local_only() and nvidia_client.available():
        produced = False

        def _report(mid: str) -> None:
            if on_model is not None:
                on_model(mid.split("/")[-1] + " · cloud")

        try:
            for chunk in cloud.stream_ladder(ladder, messages, options,
                                             on_winner=_report):
                produced = True
                yield chunk
            if produced:
                return
        except Exception:
            if produced:
                # The user already has text on screen; restarting anywhere else
                # would duplicate it. Surface the break rather than double-write.
                raise
            if not local_fallback_enabled():
                raise

        if not local_fallback_enabled():
            raise RuntimeError(
                "All cloud models are unavailable right now. Please try again in "
                "a moment."
            )

    if on_model is not None:
        on_model(model + " · local")
    yield from ollama_client.stream_chat(model=model, messages=messages,
                                         options=options)


class Agent:
    name: str = ""

    def __init__(self):
        self.spec = AGENTS[self.name]

    def build_messages(self, user_input: str, history: list[dict]) -> list[dict]:
        messages = [{"role": "system", "content": self.spec["system_prompt"]}]
        messages.extend(history)
        messages.append({"role": "user", "content": user_input})
        return messages

    def run(self, user_input: str, history: list[dict], on_model=None,
            ladder: list[str] | None = None, **kwargs) -> Iterator[str]:
        messages = self.build_messages(user_input, history)
        yield from stream_reply(self.name, self.spec["model"], messages,
                                self.spec["options"], on_model=on_model,
                                ladder=ladder)
