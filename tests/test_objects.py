"""Objects, relationships and the two invariants the module exists to enforce.

`core/objects.py` states them in its docstring:

  1. Identity before insertion.
  2. Provenance is never upgraded for free.

Both are the kind of rule that degrades silently — the graph keeps working, it
just becomes confidently wrong. So they are tested directly rather than through
the API.
"""

from __future__ import annotations

import pytest

from omnix.core import objects as objects_mod
from omnix.core import ontology as onto


# ---------------------------------------------------------------------------
# Name normalisation
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("a,b", [
    ("NVIDIA", "Nvidia"),
    ("NVIDIA", "NVIDIA Corporation"),
    ("NVIDIA Corp.", "nvidia"),
    ("Apple Inc", "Apple"),
    ("The Pentagon", "Pentagon"),
    ("Nestlé", "Nestle"),
    ("Foo  Bar", "foo bar"),
])
def test_names_that_must_normalise_together(a, b):
    assert objects_mod.normalize_name(a) == objects_mod.normalize_name(b)


@pytest.mark.parametrize("a,b", [
    ("Iran", "Iraq"),
    ("Sudan", "South Sudan"),
    ("Congo", "Democratic Republic of the Congo"),
    ("Boeing 737", "Boeing 747"),
])
def test_names_that_must_stay_distinct(a, b):
    """Normalisation is lossy on purpose, but must not collapse real entities."""
    assert objects_mod.normalize_name(a) != objects_mod.normalize_name(b)


def test_normalize_name_survives_empty_and_punctuation_only():
    assert objects_mod.normalize_name("") == ""
    assert objects_mod.normalize_name(None) == ""
    # A name that is nothing BUT a suffix must not normalise away to nothing,
    # or every such object would collide on the empty key.
    assert objects_mod.normalize_name("Group") != ""


def test_make_external_id_is_deterministic_and_scoped():
    a = objects_mod.make_external_id("company", "NVIDIA Corporation")
    b = objects_mod.make_external_id("company", "nvidia")
    assert a == b
    scoped = objects_mod.make_external_id("company", "nvidia", scope="repo1")
    assert scoped != a and "repo1" in scoped


# ---------------------------------------------------------------------------
# Invariant 1 — identity before insertion
# ---------------------------------------------------------------------------
def test_same_entity_under_three_surface_forms_is_one_object(ws):
    """The NVIDIA / Nvidia / NVIDIA Corporation failure, tested directly."""
    first, created1 = objects_mod.upsert_object(ws, "company", "NVIDIA")
    second, created2 = objects_mod.upsert_object(ws, "company", "Nvidia")
    third, created3 = objects_mod.upsert_object(ws, "company", "NVIDIA Corporation")

    assert created1 is True
    assert created2 is False and created3 is False
    assert first["id"] == second["id"] == third["id"]
    assert len(objects_mod.list_objects(ws, type_key="company")) == 1


def test_the_fuller_surface_form_wins_the_display_name(ws):
    objects_mod.upsert_object(ws, "company", "NVIDIA")
    final, _ = objects_mod.upsert_object(ws, "company", "NVIDIA Corporation")
    assert final["name"] == "NVIDIA Corporation"


def test_same_name_in_different_types_stays_separate(ws):
    """Deduplication is *within* a type — a person and a company may share a
    name and are not the same thing."""
    a, _ = objects_mod.upsert_object(ws, "company", "Bloomberg")
    b, _ = objects_mod.upsert_object(ws, "person", "Bloomberg")
    assert a["id"] != b["id"]


def test_workspaces_do_not_share_objects(ws, obj):
    from omnix.core import workspace as workspace_mod
    other = workspace_mod.create(workspace_mod.default_user(), "Other", "")["id"]
    a, _ = objects_mod.upsert_object(ws, "company", "Acme")
    b, created = objects_mod.upsert_object(other, "company", "Acme")
    assert created is True
    assert a["id"] != b["id"]


def test_name_matching_merges_across_different_external_ids(ws):
    """Documented behaviour: match on `external_id` first, then on normalised
    name within the type — so the same entity arriving from two sources under
    two different natural keys becomes one object.

    This is what makes cross-source entity resolution work (TERRA's
    `terra:company:apple` and a research run's `company:apple` are one Apple).
    The sharp edge is that the first external_id wins, so a later lookup by the
    second key finds nothing; callers that need both must record the alternate
    key as a property.
    """
    a, _ = objects_mod.upsert_object(ws, "company", "Apple",
                                     external_id="terra:company:apple")
    b, created = objects_mod.upsert_object(ws, "company", "Apple",
                                           external_id="research:company:apple")
    assert created is False
    assert a["id"] == b["id"]
    assert b["externalId"] == "terra:company:apple", "first key wins"


def test_upsert_requires_a_name(ws):
    with pytest.raises(ValueError):
        objects_mod.upsert_object(ws, "company", "   ")


# ---------------------------------------------------------------------------
# Invariant 2 — provenance never strengthens for free
# ---------------------------------------------------------------------------
def test_objects_default_to_the_weakest_provenance(ws):
    o, _ = objects_mod.upsert_object(ws, "company", "Acme")
    assert o["provenance"] == onto.DEFAULT_PROVENANCE == "ai_inferred"


def test_a_later_weaker_write_cannot_downgrade_provenance(ws):
    objects_mod.upsert_object(ws, "company", "Acme", provenance="verified")
    o, _ = objects_mod.upsert_object(ws, "company", "Acme", provenance="ai_inferred")
    assert o["provenance"] == "verified"


def test_provenance_strengthens_when_justified(ws):
    objects_mod.upsert_object(ws, "company", "Acme", provenance="ai_inferred")
    o, _ = objects_mod.upsert_object(ws, "company", "Acme", provenance="source_backed")
    assert o["provenance"] == "source_backed"


def test_an_invented_provenance_falls_back_rather_than_being_stored(ws):
    o, _ = objects_mod.upsert_object(ws, "company", "Acme",
                                     provenance="definitely_true")
    assert o["provenance"] == onto.DEFAULT_PROVENANCE


def test_confidence_is_null_until_measured(ws):
    o, _ = objects_mod.upsert_object(ws, "company", "Acme")
    assert o.get("confidence") is None, "confidence must mean 'not measured'"


# ---------------------------------------------------------------------------
# Additive merging
# ---------------------------------------------------------------------------
def test_merging_never_overwrites_an_existing_property(ws):
    objects_mod.upsert_object(ws, "company", "Acme", properties={"ceo": "Ada"})
    o, _ = objects_mod.upsert_object(ws, "company", "Acme",
                                     properties={"ceo": "Bob", "hq": "Zurich"})
    assert o["properties"]["ceo"] == "Ada", "first value must win"
    assert o["properties"]["hq"] == "Zurich", "new keys must still be added"


def test_a_real_description_replaces_a_stub(ws):
    objects_mod.upsert_object(ws, "company", "Acme", description="A company.")
    long = ("Acme is a diversified manufacturer of anvils, rockets and other "
            "mail-order hardware, best known for its enthusiastic customers.")
    o, _ = objects_mod.upsert_object(ws, "company", "Acme", description=long)
    assert o["description"] == long


def test_a_shorter_description_does_not_replace_a_real_one(ws):
    long = ("Acme is a diversified manufacturer of anvils, rockets and other "
            "mail-order hardware, best known for its enthusiastic customers.")
    objects_mod.upsert_object(ws, "company", "Acme", description=long)
    o, _ = objects_mod.upsert_object(ws, "company", "Acme", description="A company.")
    assert o["description"] == long


def test_tags_accumulate_without_duplicates(ws):
    objects_mod.upsert_object(ws, "company", "Acme", tags=["terra"])
    o, _ = objects_mod.upsert_object(ws, "company", "Acme", tags=["oracle", "terra"])
    assert sorted(o["tags"]) == ["oracle", "terra"]


# ---------------------------------------------------------------------------
# Relationships
# ---------------------------------------------------------------------------
def test_symmetric_relations_collapse_to_one_row(ws, obj):
    a = obj("country", "Alpha")
    b = obj("country", "Beta")
    r1, created1 = objects_mod.link(ws, a["id"], b["id"], "allied_with")
    r2, created2 = objects_mod.link(ws, b["id"], a["id"], "allied_with")

    assert onto.is_symmetric("allied_with")
    assert created1 is True and created2 is False
    assert r1["id"] == r2["id"]
    assert r2["observations"] == 2


def test_asymmetric_relations_keep_their_direction(ws, obj):
    a = obj("country", "Alpha")
    b = obj("country", "Beta")
    _, created1 = objects_mod.link(ws, a["id"], b["id"], "sanctions")
    _, created2 = objects_mod.link(ws, b["id"], a["id"], "sanctions")
    assert not onto.is_symmetric("sanctions")
    assert created1 is True and created2 is True, "direction carries meaning"


def test_repeat_observations_accumulate_weight_and_average_sentiment(ws, obj):
    a = obj("country", "Alpha")
    b = obj("country", "Beta")
    objects_mod.link(ws, a["id"], b["id"], "in_conflict", sentiment=-1.0)
    r, _ = objects_mod.link(ws, a["id"], b["id"], "in_conflict", sentiment=0.0)
    assert r["observations"] == 2
    assert r["sentiment"] == pytest.approx(-0.5)
    assert r["weight"] > onto.relation_weight("in_conflict")


def test_self_links_are_refused(ws, obj):
    a = obj("country", "Alpha")
    assert objects_mod.link(ws, a["id"], a["id"], "allied_with") is None


def test_links_to_a_missing_object_are_refused(ws, obj):
    a = obj("country", "Alpha")
    assert objects_mod.link(ws, a["id"], "no-such-id", "allied_with") is None


def test_an_invented_relation_is_coerced_not_stored(ws, obj):
    a = obj("country", "Alpha")
    b = obj("country", "Beta")
    r, _ = objects_mod.link(ws, a["id"], b["id"], "teleports_to")
    assert r["relation"] == "related_to"


def test_relationship_provenance_also_only_strengthens(ws, obj):
    a = obj("country", "Alpha")
    b = obj("country", "Beta")
    objects_mod.link(ws, a["id"], b["id"], "allied_with", provenance="source_backed")
    r, _ = objects_mod.link(ws, a["id"], b["id"], "allied_with",
                            provenance="ai_inferred")
    assert r["provenance"] == "source_backed"


# ---------------------------------------------------------------------------
# Reads never raise
# ---------------------------------------------------------------------------
def test_reads_on_an_empty_workspace_return_empty_not_errors(ws):
    assert objects_mod.list_objects(ws) == []
    assert objects_mod.search_objects(ws, "anything") == []
    assert objects_mod.get_object(ws, "no-such-id") is None
    assert objects_mod.by_external_id(ws, "no-such-ext") == []
    assert objects_mod.relationships_of(ws, "no-such-id") == []
    s = objects_mod.stats(ws)
    assert s["objects"] == 0 and s["relationships"] == 0


def test_search_finds_by_substring(ws, obj):
    obj("company", "Northrop Grumman")
    obj("company", "Lockheed Martin")
    hits = objects_mod.search_objects(ws, "lockheed")
    assert [h["name"] for h in hits] == ["Lockheed Martin"]


def test_stats_counts_by_type_and_provenance(ws, obj):
    obj("country", "Alpha", provenance="verified")
    obj("country", "Beta")
    obj("person", "Carol")
    s = objects_mod.stats(ws)
    assert s["objects"] == 3
    assert s["byType"]["country"] == 2
    assert s["byProvenance"]["verified"] == 1
    assert s["byProvenance"]["ai_inferred"] == 2
