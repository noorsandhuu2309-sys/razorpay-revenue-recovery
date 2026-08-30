"""NOVA's conversation memory.

The bug these cover: `/api/nova/command` was stateless, so answering "yes" to
"shall I go into more detail?" reached the model with nothing to attach to and
it asked for context that had already been given. A thread that is not replayed
into the prompt is not memory, so both halves are tested — that turns are
stored, and that they actually reach the model.
"""

from __future__ import annotations

import pytest

from omnix.core import conversation


# ---------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------
def test_a_new_space_has_an_empty_thread(ws):
    assert conversation.thread(ws) == []
    assert conversation.history_for_prompt(ws) == []


def test_turns_come_back_in_the_order_they_happened(ws):
    conversation.add(ws, "user", "first")
    conversation.add(ws, "assistant", "second")
    conversation.add(ws, "user", "third")
    assert [t["text"] for t in conversation.thread(ws)] == \
        ["first", "second", "third"]


def test_roles_are_constrained_to_user_and_assistant(ws):
    t = conversation.add(ws, "system", "should not be trusted")
    assert t["role"] == "user", "an unknown role must not become 'system'"


def test_threads_do_not_leak_between_spaces(ws):
    from omnix.core import workspace as workspace_mod
    other = workspace_mod.create(workspace_mod.default_user(), "Other", "")["id"]
    conversation.add(ws, "user", "mine")
    assert conversation.thread(other) == []


def test_clear_empties_only_the_one_space(ws):
    from omnix.core import workspace as workspace_mod
    other = workspace_mod.create(workspace_mod.default_user(), "Other", "")["id"]
    conversation.add(ws, "user", "a")
    conversation.add(other, "user", "b")

    assert conversation.clear(ws) == 1
    assert conversation.thread(ws) == []
    assert len(conversation.thread(other)) == 1


def test_context_is_recorded_with_the_turn(ws):
    """"compare these two" is unreadable a week later without the objects."""
    t = conversation.add(ws, "user", "compare these", context=["a", "b"])
    assert t["context"] == ["a", "b"]


# ---------------------------------------------------------------------------
# What actually reaches the model
# ---------------------------------------------------------------------------
def test_history_is_chronological_and_shaped_for_the_model(ws):
    conversation.add(ws, "user", "name three risks")
    conversation.add(ws, "assistant", "one, two, three")
    conversation.add(ws, "user", "yes, in detail")

    hist = conversation.history_for_prompt(ws)
    assert [h["role"] for h in hist] == ["user", "assistant", "user"]
    assert [h["content"] for h in hist] == \
        ["name three risks", "one, two, three", "yes, in detail"]


def test_history_is_capped_by_turns_and_drops_the_oldest(ws):
    for i in range(20):
        conversation.add(ws, "user", f"turn {i}")
    hist = conversation.history_for_prompt(ws, max_turns=5)
    assert len(hist) == 5
    assert hist[-1]["content"] == "turn 19", "the newest turn must survive"
    assert "turn 0" not in [h["content"] for h in hist]


def test_history_respects_a_character_budget(ws):
    conversation.add(ws, "user", "x" * 500)
    conversation.add(ws, "user", "y" * 500)
    conversation.add(ws, "user", "recent")
    hist = conversation.history_for_prompt(ws, max_chars=600)
    assert hist[-1]["content"] == "recent"
    assert sum(len(h["content"]) for h in hist) <= 600 + len("recent")


def test_empty_turns_are_not_replayed(ws):
    """A failed generation stores an empty assistant turn; sending it as an
    empty message confuses the next call for no benefit."""
    conversation.add(ws, "user", "question")
    conversation.add(ws, "assistant", "")
    assert [h["content"] for h in conversation.history_for_prompt(ws)] == \
        ["question"]


# ---------------------------------------------------------------------------
# The endpoint
# ---------------------------------------------------------------------------
def test_command_records_both_sides_of_the_exchange(monkeypatch, ws):
    from omnix.api import nova
    from omnix.models.router import router as real

    class _Answer:
        ok = True
        text = "a real answer"
        error = ""
        model = "test/model"

    monkeypatch.setattr(real, "generate", lambda *a, **k: _Answer())

    nova.command({"workspace": ws, "input": "a question", "selection": []})

    turns = conversation.thread(ws)
    assert [t["role"] for t in turns] == ["user", "assistant"]
    assert turns[0]["text"] == "a question"
    assert turns[1]["text"] == "a real answer"
    assert turns[1]["model"] == "test/model"


def test_a_follow_up_is_answered_with_the_prior_turns(monkeypatch, ws):
    """The regression itself: the second call must see the first exchange."""
    from omnix.api import nova
    from omnix.models.router import router as real

    seen: list[list[dict]] = []

    class _Answer:
        ok = True
        text = "answer"
        error = ""
        model = "test/model"

    def _capture(*_a, **kw):
        seen.append(list(kw.get("messages") or []))
        return _Answer()

    monkeypatch.setattr(real, "generate", _capture)

    nova.command({"workspace": ws, "input": "name three risks", "selection": []})
    nova.command({"workspace": ws, "input": "yes", "selection": []})

    assert len(seen) == 2
    contents = [m["content"] for m in seen[1]]
    assert any("name three risks" in c for c in contents), \
        "the follow-up was sent without the question it follows"
    assert any("answer" == c for c in contents), \
        "the follow-up was sent without NOVA's previous reply"


@pytest.mark.parametrize("text", ["yes", "Yes.", "go on", "ok", "tell me more",
                                  "elaborate", "in detail"])
def test_short_continuations_are_recognised(text):
    from omnix.api import nova
    assert nova._is_followup(text)


@pytest.mark.parametrize("text", [
    "research the semiconductor supply chain",
    "which countries are in conflict",
])
def test_real_questions_are_not_mistaken_for_continuations(text):
    from omnix.api import nova
    assert not nova._is_followup(text)
