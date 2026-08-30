"""Routes a user message to the agent best suited to handle it.

Order of decision:
1. An image path was supplied -> vision.
2. Fast keyword/regex heuristics for coding / research / reasoning.
3. Ambiguous cases fall back to a tiny LLM classification call.
4. Default to chat.
"""

import re

from . import ollama_client
from .config import AGENTS, CLASSIFIER_MODEL, DEFAULT_AGENT

_CODE_HINTS = re.compile(
    r"```|\bdef \b|\bclass \b|\bfunction\b|\bimport \b|\bimplement\b|"
    r"\bdebug\b|\bwrite (a|an|some) (code|script|function|program)\b|"
    r"\b(python|javascript|typescript|java|c\+\+|rust|golang|sql|html|css)\b",
    re.IGNORECASE,
)

_RESEARCH_HINTS = re.compile(
    r"\bsearch\b|\blatest\b|\bnews\b|\bcurrent\b|\btoday\b|\bwho is\b|"
    r"\bwhat is happening\b|\brecent\b|\blookup\b|\block up\b",
    re.IGNORECASE,
)

_REASONING_HINTS = re.compile(
    r"\bwhy\b|\bprove\b|\bstep by step\b|\bexplain the logic\b|"
    r"\bsolve\b|\bpuzzle\b|\breasoning\b|\bwork(ed)? out\b",
    re.IGNORECASE,
)

_IMAGE_PATH_HINT = re.compile(
    r"\.(png|jpe?g|gif|bmp|webp)\b", re.IGNORECASE
)

# Greetings / acknowledgements — obvious chit-chat that should answer instantly
# as "chat" instead of waiting on the LLM classifier (which adds latency and can
# occasionally misfire on trivial messages).
_CHITCHAT = re.compile(
    r"^\s*(hi+|hey+|hello+|yo|sup|hiya|howdy|heya|"
    r"thanks?|thank you|thx|ty|ok(ay)?|k|cool|nice|great|awesome|"
    r"good\s?(morning|afternoon|evening|night|day)|gm|gn|"
    r"lol|lmao|haha+|hmm+|yeah?|yep|nope|no|yes|"
    r"bye|goodbye|see ya|cya|good bye)"
    r"[\s!.?,]*$",
    re.IGNORECASE,
)

VALID_AGENTS = set(AGENTS.keys())


def classify(user_input: str, image_path: str | None = None) -> str:
    if image_path or _IMAGE_PATH_HINT.search(user_input):
        return "vision"

    if _CODE_HINTS.search(user_input):
        return "coding"

    if _RESEARCH_HINTS.search(user_input):
        return "research"

    if _REASONING_HINTS.search(user_input):
        return "reasoning"

    # Fast-path: greetings and ultra-short small talk -> chat, no LLM round-trip.
    stripped = (user_input or "").strip()
    if _CHITCHAT.match(stripped) or (0 < len(stripped.split()) <= 2 and "?" not in stripped):
        return "chat"

    return _classify_with_llm(user_input)


def _classify_with_llm(user_input: str) -> str:
    """Last-resort classification when the heuristics above don't fire.

    This used to call local Ollama unconditionally. On a cloud-first install
    Ollama isn't running, so the call spent ~2.2s failing to connect and then
    silently returned "chat" for EVERY ambiguous message — the LLM tier of the
    router was effectively dead in production while /api/models still advertised
    a cloud router model. Now it uses the same cloud ladder as everything else
    and only falls back to Ollama when running local-only.
    """
    prompt = (
        "Classify the user's message into exactly one category. "
        "Respond with only one word, nothing else: "
        "chat, coding, reasoning, research, or vision.\n\n"
        f"Message: {user_input}"
    )
    messages = [{"role": "user", "content": prompt}]

    try:
        from .config import CLOUD_TIERS, cloud_active
    except Exception:
        cloud_active = None

    if cloud_active is not None and cloud_active():
        try:
            from . import cloud

            # Routing sits in front of every turn, so it gets a tight budget:
            # a slow classification is worse than a slightly wrong one, and the
            # keyword heuristics already caught the clear-cut cases.
            result = "".join(cloud.stream_ladder(
                CLOUD_TIERS.get("fast") or [],
                messages,
                options={"temperature": 0.0, "max_tokens": 4},
                hedge_after=1.0,
                first_token_budget=4.0,
            ))
            return _pick(result)
        except Exception:
            return DEFAULT_AGENT

    try:
        result = ollama_client.chat(
            model=CLASSIFIER_MODEL,
            messages=messages,
            options={"temperature": 0.0, "num_ctx": 1024},
        )
    except Exception:
        return DEFAULT_AGENT

    return _pick(result)


def _pick(result: str) -> str:
    """Pull an agent name out of a one-word classification reply."""
    text = (result or "").strip().lower()
    if not text:
        return DEFAULT_AGENT
    guess = text.split()[0].strip(".,!:\"'")
    if guess in VALID_AGENTS:
        return guess
    # Reasoning models can wrap the answer in prose; take any agent name present.
    for name in VALID_AGENTS:
        if name in text:
            return name
    return DEFAULT_AGENT
