"""Persistent Intents (§11).

The point of these tests is to prove the monitor is not decorative. It has to
actually catch something, refuse to catch it twice, and be honest about when it
last looked.
"""

from __future__ import annotations

import time

import pytest

from omnix.core import intents
from omnix.core import objects as objects_mod


@pytest.fixture
def watched(ws, obj):
    return obj("company", "Initech", provenance="source_backed")


def test_intent_needs_something_to_watch(ws):
    with pytest.raises(ValueError):
        intents.create(ws, "Watch nothing")


def test_created_intent_has_never_been_checked(ws, watched):
    it = intents.create(ws, "Monitor Initech", object_ids=[watched["id"]])
    assert it["lastCheckedAt"] is None, "an unchecked Intent must not look checked"
    assert it["status"] == "active"
    assert it["hitCount"] == 0


def test_evaluation_catches_a_new_event_then_dedupes(ws, watched):
    it = intents.create(ws, "Monitor Initech", object_ids=[watched["id"]])

    # The window opens at creation, so the event has to be newer than it.
    time.sleep(0.01)
    objects_mod.add_event(ws, "Initech acquires a competitor",
                          object_id=watched["id"], relevance="high")

    first = intents.evaluate(ws, it["id"])
    assert first["newHits"] == 1
    assert "acquires" in first["hits"][0]["title"]
    assert first["hits"][0]["kind"] == "event"

    second = intents.evaluate(ws, it["id"])
    assert second["newHits"] == 0, "the same event fired twice"

    after = intents.get(ws, it["id"])
    assert after["hitCount"] == 1
    assert after["lastCheckedAt"] is not None
    assert after["lastHitAt"] is not None


def test_events_from_before_the_intent_are_not_backfilled(ws, watched):
    objects_mod.add_event(ws, "Old news nobody asked about",
                          object_id=watched["id"], relevance="high")
    # Longer than Windows' ~15ms clock resolution: the window's lower bound is
    # inclusive, so the old event must be strictly earlier to prove the point.
    time.sleep(0.05)
    it = intents.create(ws, "Monitor Initech", object_ids=[watched["id"]])
    assert intents.evaluate(ws, it["id"])["newHits"] == 0


def test_relevance_floor_filters_noise(ws, watched):
    it = intents.create(ws, "Only the important things",
                        object_ids=[watched["id"]], relevance_floor="high")
    time.sleep(0.01)
    objects_mod.add_event(ws, "Trivial mention", object_id=watched["id"],
                          relevance="low")
    assert intents.evaluate(ws, it["id"])["newHits"] == 0

    objects_mod.add_event(ws, "Major announcement", object_id=watched["id"],
                          relevance="high")
    out = intents.evaluate(ws, it["id"])
    assert out["newHits"] == 1
    assert "Major" in out["hits"][0]["title"]


def test_keywords_filter_events_on_a_watched_object(ws, watched):
    it = intents.create(ws, "Only lawsuits", object_ids=[watched["id"]],
                        keywords=["lawsuit"])
    time.sleep(0.01)
    objects_mod.add_event(ws, "Initech launches a product",
                          object_id=watched["id"], relevance="high")
    assert intents.evaluate(ws, it["id"])["newHits"] == 0

    objects_mod.add_event(ws, "Initech faces a lawsuit",
                          object_id=watched["id"], relevance="high")
    assert intents.evaluate(ws, it["id"])["newHits"] == 1


def test_a_new_relationship_is_a_hit(ws, obj, watched):
    """"X now partners with Y" is exactly what a monitor exists to catch."""
    it = intents.create(ws, "Monitor Initech", object_ids=[watched["id"]])
    time.sleep(0.01)
    other = obj("company", "Globex")
    objects_mod.link(ws, watched["id"], other["id"], "partners_with")

    out = intents.evaluate(ws, it["id"])
    assert out["newHits"] == 1
    hit = out["hits"][0]
    assert hit["kind"] == "relationship"
    assert "Globex" in hit["title"]


def test_hits_become_activity_events(ws, watched):
    """§13: autonomous work has to be visible in the Activity layer."""
    it = intents.create(ws, "Monitor Initech", object_ids=[watched["id"]])
    time.sleep(0.01)
    objects_mod.add_event(ws, "Initech ships something",
                          object_id=watched["id"], relevance="high")
    intents.evaluate(ws, it["id"])

    activity = objects_mod.timeline(ws, limit=50)
    assert any(e["title"].startswith("Intent") for e in activity)


def test_intent_events_do_not_feed_themselves(ws, watched):
    """The activity event an Intent writes must not be caught by the next
    check, or every poll would echo the previous one forever."""
    it = intents.create(ws, "Monitor Initech", object_ids=[watched["id"]])
    time.sleep(0.01)
    objects_mod.add_event(ws, "Initech does a thing",
                          object_id=watched["id"], relevance="high")
    assert intents.evaluate(ws, it["id"])["newHits"] == 1
    assert intents.evaluate(ws, it["id"])["newHits"] == 0
    assert intents.evaluate(ws, it["id"])["newHits"] == 0


def test_paused_intents_are_skipped_by_the_workspace_sweep(ws, watched):
    it = intents.create(ws, "Monitor Initech", object_ids=[watched["id"]])
    intents.update(ws, it["id"], status="paused")
    time.sleep(0.01)
    objects_mod.add_event(ws, "Something happened", object_id=watched["id"],
                          relevance="high")

    swept = intents.evaluate_workspace(ws)
    assert swept["checked"] == 0
    assert intents.get(ws, it["id"])["hitCount"] == 0

    # Resuming picks the event up: pausing suppressed it, it did not lose it.
    intents.update(ws, it["id"], status="active")
    assert intents.evaluate_workspace(ws)["newHits"] == 1


def test_update_and_delete(ws, watched):
    it = intents.create(ws, "First name", object_ids=[watched["id"]])
    updated = intents.update(ws, it["id"], title="Second name",
                             cadenceMinutes=5, relevanceFloor="high")
    assert updated["title"] == "Second name"
    assert updated["cadenceMinutes"] == 5
    assert updated["relevanceFloor"] == "high"

    assert intents.delete(ws, it["id"]) is True
    assert intents.get(ws, it["id"]) is None
    assert intents.delete(ws, it["id"]) is False


def test_cadence_is_clamped(ws, watched):
    it = intents.create(ws, "Too eager", object_ids=[watched["id"]],
                        cadence_minutes=1)
    assert it["cadenceMinutes"] == 5
