"""The ontology registry.

These tests exist because the ontology is the one thing both the backend and
the frontend reason about, and the frontend builds its legend and filters from
`describe()` rather than a second copy. A silent change here is a silent change
to the UI.
"""

from __future__ import annotations

import pytest

from omnix.core import ontology as onto


# ---------------------------------------------------------------------------
# Registry integrity
# ---------------------------------------------------------------------------
def test_every_type_resolves_to_a_declared_family():
    """A type pointing at a missing family renders with no colour or glyph."""
    for t in onto.types():
        assert t.family in onto.FAMILIES, f"{t.key} -> unknown family {t.family}"


def test_every_type_has_a_visual():
    for t in onto.types():
        vis = onto.visual_of(t.key)
        assert vis, f"{t.key} has no visual"
        assert vis.get("label"), f"{t.key} visual has no label"


def test_type_keys_are_unique_and_normalised():
    keys = [t.key for t in onto.types()]
    assert len(keys) == len(set(keys)), "duplicate type keys"
    for k in keys:
        assert k == k.strip().lower(), f"{k!r} is not normalised"


def test_resolve_is_total():
    """`resolve` must never raise — an unknown type from an LLM is expected.

    The fallback is the `thing` sentinel, which is deliberately NOT in the
    registry: it is a type you can land on, never one you can choose. What
    matters is that it still renders, so it needs a declared family and a
    visual like any other type.
    """
    fallback = onto.resolve("definitely-not-a-real-type")
    assert fallback is onto.UNKNOWN
    assert fallback.family in onto.FAMILIES
    assert fallback.visual.get("label")
    assert fallback.key not in {t.key for t in onto.types()}


def test_known_distinguishes_real_types():
    assert onto.known(onto.types()[0].key)
    assert not onto.known("definitely-not-a-real-type")


# ---------------------------------------------------------------------------
# Relations
# ---------------------------------------------------------------------------
def test_relation_ok_coerces_unknown_to_related_to():
    """The point of the coercion: a model cannot invent an edge type."""
    assert onto.relation_ok("teleports_to") == "related_to"
    assert onto.relation_ok("") == "related_to"
    assert onto.relation_ok(None) == "related_to"


@pytest.mark.parametrize("raw", ["In Conflict", "in-conflict",
                                 "IN_CONFLICT", " in conflict "])
def test_relation_ok_normalises_surface_forms(raw):
    assert onto.relation_ok(raw) == "in_conflict"


def test_every_relation_declares_symmetry_and_weight():
    for key, spec in onto.RELATIONS.items():
        assert "symmetric" in spec, f"{key} missing symmetric"
        assert isinstance(spec["weight"], (int, float)), f"{key} weight not numeric"
        assert spec["weight"] > 0, f"{key} weight must be positive"
        assert spec.get("label"), f"{key} missing label"


def test_extractable_excludes_the_fallback_relations():
    """`co_mentioned` and `related_to` are what extraction falls back TO;
    offering them as choices stops a model naming the real relation."""
    assert "related_to" not in onto.EXTRACTABLE
    assert "co_mentioned" not in onto.EXTRACTABLE
    assert set(onto.EXTRACTABLE) < set(onto.RELATIONS)


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------
def test_default_provenance_is_the_weakest_level():
    ranks = {k: v["rank"] for k, v in onto.PROVENANCE.items()}
    assert ranks[onto.DEFAULT_PROVENANCE] == max(ranks.values())


def test_provenance_ok_rejects_invented_levels():
    assert onto.provenance_ok("extremely_true") == onto.DEFAULT_PROVENANCE
    assert onto.provenance_ok("") == onto.DEFAULT_PROVENANCE
    assert onto.provenance_ok("VERIFIED") == "verified"


def test_provenance_ranks_are_unique():
    ranks = [v["rank"] for v in onto.PROVENANCE.values()]
    assert len(ranks) == len(set(ranks)), "two provenance levels share a rank"


# ---------------------------------------------------------------------------
# The client contract
# ---------------------------------------------------------------------------
def test_describe_carries_everything_the_frontend_needs():
    d = onto.describe()
    for key in ("families", "domains", "types", "relations", "provenance"):
        assert key in d, f"describe() missing {key}"
    assert d["types"] and d["relations"]
    for t in d["types"]:
        assert {"key", "label", "family", "domain"} <= set(t)
    for r in d["relations"]:
        assert {"key", "label", "symmetric", "weight", "extractable"} <= set(r)


def test_families_have_not_drifted_from_terra():
    """TERRA and core hold parallel family tables; this reports drift."""
    drift = onto.families_match_terra()
    assert drift == [], f"family drift vs TERRA: {drift}"
