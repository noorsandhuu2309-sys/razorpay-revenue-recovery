"""Research ingestion, and the one promise it used to break.

The bug these tests exist for: `persist_claims` mirrored every claim into the
graph and then only ever called `attach_source`, which writes an ObjectSource
provenance row rather than a Relationship. A claim was therefore structurally
incapable of holding an edge, and a live Space accumulated 91 claim objects with
0 relationships between them and anything — an "intelligence graph" that was a
bag of orphans.

So the assertions here are about edges existing and about *which* edges: a
mention matcher that is too eager is worse than none, because a wrong `about`
edge is indistinguishable from a right one once it is in the graph.
"""

from __future__ import annotations

import pytest

from omnix.core import objects as objects_mod
from omnix.core import research_ingest as ri


# ---------------------------------------------------------------------------
# The matcher
# ---------------------------------------------------------------------------
n = objects_mod.normalize_name


@pytest.mark.parametrize("text, entity, expected", [
    ("Iran closed the strait to traffic", "Iran", True),
    ("The Strait of Hormuz reopened", "Strait of Hormuz", True),
    ("China Coast Guard vessels shadowed it", "China", True),
    # Whole-token containment, not substring: these are the failures that would
    # quietly fill the graph with edges nobody can audit.
    ("Irani officials commented", "Iran", False),
    ("Shipping rates rose sharply", "Iran", False),
    ("We must trust the figures", "US", False),   # 2-char keys are refused
])
def test_mentions_is_whole_token(text, entity, expected):
    assert ri._mentions(n(text), n(entity)) is expected


def test_mentions_prefers_the_more_specific_entity():
    """A claim naming both "China" and "China Coast Guard" is about the latter
    first — the cap must not spend its budget on the vaguer node."""
    pool = {n("China"): "china", n("China Coast Guard"): "ccg"}
    assert ri._mentioned_entities("China Coast Guard seized a tanker",
                                  pool)[0] == "ccg"


def test_mentions_are_capped_per_claim():
    pool = {n(f"Country {i}"): f"id{i}" for i in range(20)}
    text = " ".join(f"Country {i}" for i in range(20))
    assert len(ri._mentioned_entities(text, pool)) == ri.MAX_MENTIONS_PER_CLAIM


# ---------------------------------------------------------------------------
# Ingestion end to end, with the model call stubbed
# ---------------------------------------------------------------------------
META = {
    "source_scores": [
        {"n": 1, "url": "https://lloydslist.com/a", "host": "lloydslist.com",
         "title": "Hormuz transits fall", "tier": "trade", "credibility": 80},
        {"n": 2, "url": "https://reuters.com/b", "host": "reuters.com",
         "title": "Traffic down", "tier": "news", "credibility": 85},
    ],
    "claims": [
        # Corroborated by two independent sources, and it names Iran — which is
        # what lets Iran through the gate below on the grounding route.
        {"text": "Tanker transits through the Strait of Hormuz fell 12 percent "
                 "after Iran began exercises.",
         "verdict": "verified", "confidence": 71, "supported_by": [1, 2]},
        {"text": "Iran conducted naval exercises near the strait.",
         "verdict": "single_source", "confidence": 44, "supported_by": [1]},
    ],
}

EXTRACTED = {
    "entities": [
        # Cited by two sources — clears the original gate.
        {"name": "Strait of Hormuz", "type": "location", "description": "A strait.",
         "sources": [1, 2]},
        # Cited once. Held back as a proposal before, but named by a `verified`
        # claim, so the grounding route should now commit it.
        {"name": "Iran", "type": "country", "description": "A country.",
         "sources": [1]},
        # Cited once and named by nothing corroborated: still a proposal.
        {"name": "Lloyds List", "type": "organization", "description": "A journal.",
         "sources": [2]},
    ],
    "relations": [
        {"src": "Strait of Hormuz", "dst": "Iran", "relation": "located_in",
         "sources": [1]},
    ],
}


@pytest.fixture
def ingested(ws, monkeypatch):
    monkeypatch.setattr(ri, "extract_entities",
                        lambda *a, **k: dict(EXTRACTED))
    return ri.ingest(ws, "Hormuz shipping risk", META, "notes",
                     execution_id="exec1")


def test_claims_are_no_longer_orphans(ws, ingested):
    """The regression this module exists for."""
    assert ingested["claimRelationships"] > 0
    for c in ingested["claims"]:
        edges = objects_mod.relationships_of(ws, c["objectId"])
        assert edges, f"claim left orphaned: {c['text']!r}"
        assert all(e["relation"] == "about" for e in edges)


def test_claim_links_to_what_it_is_actually_about(ws, ingested):
    hormuz = next(o for o in ingested["objects"]
                  if o["name"] == "Strait of Hormuz")
    transits = next(c for c in ingested["claims"]
                    if "transits" in c["text"])
    targets = {e["dst"] for e in
               objects_mod.relationships_of(ws, transits["objectId"])}
    assert hormuz["id"] in targets


def test_a_corroborated_claim_grounds_an_entity_the_source_gate_held_back(ingested):
    """`verified` means two independent sources backed the sentence naming it —
    the same bar MIN_SOURCES_TO_COMMIT sets, reached from the claim side."""
    committed = {o["name"]: o for o in ingested["objects"]}
    assert "Iran" in committed
    assert committed["Iran"]["grounding"] == "claim"
    assert committed["Strait of Hormuz"]["grounding"] == "sources"


def test_the_review_gate_still_holds_back_the_ungrounded(ingested):
    """The gate is load-bearing: an entity with one citation and no corroborated
    claim naming it must stay a proposal."""
    assert [p["name"] for p in ingested["proposed"]] == ["Lloyds List"]


def test_entity_relations_still_land(ws, ingested):
    assert ingested["entityRelationships"] == 1


def test_mention_edges_carry_the_claims_citations(ws, ingested):
    """An `about` edge drawn from a claim with two sources is evidence-backed,
    and must be able to say so when asked."""
    transits = next(c for c in ingested["claims"] if "transits" in c["text"])
    edges = objects_mod.relationships_of(ws, transits["objectId"])
    assert any(objects_mod.sources_for(ws, relationship_id=e["id"])
               for e in edges)


def test_a_later_run_attaches_to_what_the_space_already_knew(ws, monkeypatch):
    """The cumulative promise: a follow-up question must extend the graph, not
    build a second island beside it."""
    monkeypatch.setattr(ri, "extract_entities", lambda *a, **k: dict(EXTRACTED))
    ri.ingest(ws, "first question", META, "notes", execution_id="exec1")

    # The second run extracts nothing at all, but its claim names an entity the
    # first run committed.
    monkeypatch.setattr(ri, "extract_entities",
                        lambda *a, **k: {"entities": [], "relations": []})
    second = ri.ingest(ws, "follow-up", {
        "source_scores": META["source_scores"],
        "claims": [{"text": "Iran signalled it would not close the strait.",
                    "verdict": "verified", "confidence": 60,
                    "supported_by": [2]}],
    }, "notes", execution_id="exec2")

    assert second["claimRelationships"] > 0
    claim = second["claims"][0]
    targets = {e["dst"] for e in
               objects_mod.relationships_of(ws, claim["objectId"])}
    iran = objects_mod.list_objects(ws, type_key="country")
    assert any(o["id"] in targets for o in iran)


def test_claims_never_link_to_other_claims(ws, ingested):
    """Two claims sharing wording is not a relationship anyone established."""
    claim_ids = {c["objectId"] for c in ingested["claims"]}
    for cid in claim_ids:
        for e in objects_mod.relationships_of(ws, cid):
            assert e["dst"] not in claim_ids
