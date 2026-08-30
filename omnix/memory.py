"""Simple in-memory conversation history for a CLI session."""

from .config import MAX_HISTORY_TURNS


class Memory:
    def __init__(self, max_turns: int = MAX_HISTORY_TURNS):
        self.max_turns = max_turns
        self._history: list[dict] = []

    def get(self) -> list[dict]:
        return list(self._history)

    def add_turn(self, user_input: str, assistant_response: str) -> None:
        self._history.append({"role": "user", "content": user_input})
        self._history.append({"role": "assistant", "content": assistant_response})
        max_messages = self.max_turns * 2
        if len(self._history) > max_messages:
            self._history = self._history[-max_messages:]

    def clear(self) -> None:
        self._history = []
