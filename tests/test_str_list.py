"""`str_list` — coercing a JSON field that should be a list of strings.

This exists because of a failure that produced no error anywhere. Models answer
`"watch": "Monitor Russian retaliation"` about as often as they answer with a
list, and the obvious comprehension iterates a bare string CHARACTER BY
CHARACTER. Capping the result at four items yields `['R','u','s','s']` — a
well-formed list of strings that passes every type check and reaches the UI as
four one-letter bullets.

Measured on a live TERRA situation report, four of six analysts came back that
way. These tests pin the coercion so it cannot regress silently again.
"""

from __future__ import annotations

from omnix.squad.base import str_list


def test_a_bare_string_becomes_one_item_not_one_per_character():
    """The exact bug: `['R','u','s','s']` instead of the sentence."""
    out = str_list("Russian retaliation would confirm escalation", limit=4)
    assert out == ["Russian retaliation would confirm escalation"]
    assert not all(len(x) == 1 for x in out)


def test_a_proper_list_is_preserved():
    assert str_list(["one", "two", "three"]) == ["one", "two", "three"]


def test_a_multiline_string_splits_per_line():
    """The other shape models use: one bullet per line inside a single string.
    Bullet and numbering marks are scaffolding, not content."""
    out = str_list("Watch oil prices\n- Watch Hormuz\n2. Watch sanctions")
    assert out == ["Watch oil prices", "Watch Hormuz", "Watch sanctions"]


def test_empty_inputs_stay_empty():
    """An empty answer is a real answer and must not become `['']`."""
    assert str_list(None) == []
    assert str_list([]) == []
    assert str_list("") == []
    assert str_list(["", "   "]) == []


def test_limit_and_item_length_are_honoured():
    out = str_list([f"item {i}" for i in range(20)], limit=5)
    assert len(out) == 5
    long_item = str_list(["x" * 500], item_max=50)[0]
    assert len(long_item) <= 50


def test_dicts_are_flattened_to_their_text_rather_than_dropped():
    """A dropped item reads as the model having said nothing, which is a
    stronger claim than the data supports."""
    assert str_list([{"insight": "nested"}, {"text": "other"}]) == ["nested", "other"]


def test_a_scalar_is_still_one_item():
    assert str_list(42) == ["42"]


def test_terra_analyst_watch_field_survives_a_string_answer():
    """End-to-end shape check against the field that actually broke: whatever
    the model returns, `watch` must never be a list of single characters."""
    for answer in ("Monitor Russian oil production",
                   ["Monitor Russian oil production"],
                   "Iran",
                   None):
        out = str_list(answer, item_max=220, limit=4)
        assert all(len(x) > 1 for x in out), f"char-splat for {answer!r}: {out}"
