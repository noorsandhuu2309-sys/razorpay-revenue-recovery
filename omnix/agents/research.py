"""Research agent: runs a web search, then answers grounded in the results."""

from collections.abc import Iterator

from ..tools.websearch import format_deep_context, search_deep
from .base import Agent, stream_reply


class ResearchAgent(Agent):
    name = "research"

    def run(self, user_input: str, history: list[dict], results=None,
            on_model=None, ladder: list[str] | None = None,
            **kwargs) -> Iterator[str]:
        if results is None:
            results = search_deep(user_input)
        context = format_deep_context(results)

        augmented_input = (
            f"Web search results for the user's question:\n\n{context}\n\n"
            f"User's question: {user_input}"
        )

        messages = self.build_messages(augmented_input, history)
        yield from stream_reply(self.name, self.spec["model"], messages,
                                self.spec["options"], on_model=on_model,
                                ladder=ladder)
