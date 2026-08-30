"""Vision agent: answers questions about a supplied image.

Cloud and local expect different message shapes for images — the OpenAI-compatible
cloud API wants a content ARRAY with an inline `image_url` data URI, while Ollama
wants a plain string plus a sibling `images` list of file paths. Both are built
here so vision behaves like every other agent.
"""

import base64
import mimetypes
import re
from collections.abc import Iterator
from pathlib import Path

from .. import cloud, nvidia_client, ollama_client
from ..config import CLOUD_LADDER, local_fallback_enabled, local_only, nvidia_enabled
from .base import Agent

_IMAGE_PATH_RE = re.compile(r"[^\s\"']+\.(?:png|jpe?g|gif|bmp|webp)\b", re.IGNORECASE)

# Cloud vision sends the image inline, so a huge upload becomes a huge request.
MAX_INLINE_BYTES = 12 * 1024 * 1024


def extract_image_path(user_input: str) -> str | None:
    match = _IMAGE_PATH_RE.search(user_input)
    return match.group(0) if match else None


def _data_uri(path: str) -> str | None:
    try:
        raw = Path(path).read_bytes()
    except Exception:
        return None
    if len(raw) > MAX_INLINE_BYTES:
        return None
    mime = mimetypes.guess_type(path)[0] or "image/png"
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


class VisionAgent(Agent):
    name = "vision"

    def run(self, user_input: str, history: list[dict],
            image_path: str | None = None, on_model=None,
            ladder: list[str] | None = None, **kwargs) -> Iterator[str]:
        image_path = image_path or extract_image_path(user_input)

        if ladder is None:
            ladder = CLOUD_LADDER.get("vision") or []
        use_cloud = (ladder and nvidia_enabled() and not local_only()
                     and nvidia_client.available())

        if use_cloud:
            uri = _data_uri(image_path) if image_path else None
            content: list | str
            if uri:
                content = [{"type": "text", "text": user_input},
                           {"type": "image_url", "image_url": {"url": uri}}]
            else:
                content = user_input
            messages = [{"role": "system", "content": self.spec["system_prompt"]}]
            messages.extend(history)
            messages.append({"role": "user", "content": content})

            produced = False

            def _report(mid: str) -> None:
                if on_model is not None:
                    on_model(mid.split("/")[-1] + " · cloud")

            try:
                for chunk in cloud.stream_ladder(ladder, messages,
                                                 self.spec["options"],
                                                 on_winner=_report):
                    produced = True
                    yield chunk
                if produced:
                    return
            except Exception:
                if produced or not local_fallback_enabled():
                    raise

            if not local_fallback_enabled():
                raise RuntimeError(
                    "All vision models are unavailable right now. Please try "
                    "again in a moment."
                )

        # Local Ollama path (offline / development).
        messages = [{"role": "system", "content": self.spec["system_prompt"]}]
        messages.extend(history)
        user_message: dict = {"role": "user", "content": user_input}
        if image_path:
            user_message["images"] = [image_path]
        messages.append(user_message)

        if on_model is not None:
            on_model(self.spec["model"] + " · local")
        yield from ollama_client.stream_chat(
            model=self.spec["model"],
            messages=messages,
            options=self.spec["options"],
        )
