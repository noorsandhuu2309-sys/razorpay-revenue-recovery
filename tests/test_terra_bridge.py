"""The TERRA -> workspace projection.

TERRA is *projected*, not migrated: the bridge is one-way and idempotent, and
TERRA's JSON store and refresh loop are untouched by it. The rule worth
protecting is the provenance one — `verified` is reserved for gazetteer seeds
(real countries out of `world.json`) and no extracted entity can ever earn it.
That is the difference between an honest graph and provenance inflation.

These run against whatever TERRA data the repo has. On a fresh clone there is
none, so they skip rather than fail.
"""

from __future__ import annotations

import pytest

from omnix.core import objects as objects_mod
from omnix.core import terra_bridge


# ---------------------------------------------------------------------------
# Contract: never raise into a caller
# ---------------------------------------------------------------------------
def test_status_never_raises():
    st = terra_bridge.status()
    assert isinstance(st, dict)


def test_sync_reports_failure_rather_than_raising():
    """TERRA may be mid-refresh or absent; the endpoint must still answer."""
    out = terra_bridge.sync(workspace_id="no-such-workspace", max_nodes=1,
                            max_edges=1, attach_sources=False)
    assert isinstance(out, dict)
    assert "ok" in out


# ---------------------------------------------------------------------------
# Projection
# ---------------------------------------------------------------------------
@pytest.fixture
def projected(ws):
    out = terra_bridge.sync(workspace_id=ws, max_nodes=40, max_edges=60,
                            attach_sources=False)
    if not out.get("ok"):
        pytest.skip(f"TERRA data unavailable: {out.get('error')}")
    if not objects_mod.list_objects(ws, limit=1):
        pytest.skip("TERRA graph is empty")
    return {"ws": ws, "out": out}


def test_projection_creates_objects(projected):
    objs = objects_mod.list_objects(projected["ws"], limit=200)
    assert objs, "sync reported ok but wrote nothing"


def test_only_seeded_nodes_are_verified(projected):
    """The invariant: an extracted entity must never read as verified.

    `verified` tracks TERRA's `seed` flag exactly — a curated gazetteer entry
    (countries out of world.json, but also the curated commodity and
    organisation vocabularies), never something an extractor produced. Checked
    against TERRA's own graph rather than by guessing which types can be
    seeded, because that list is longer than it looks: "Crude oil" is a seeded
    commodity, not a country.
    """
    from omnix.terra import graph as terra_graph
    nodes = terra_graph.shared().nodes

    for o in objects_mod.list_objects(projected["ws"], limit=500):
        tid = (o.get("properties") or {}).get("terraId")
        if not tid or tid not in nodes:
            continue
        seeded = bool(nodes[tid].get("seed"))
        if o["provenance"] == "verified":
            assert seeded, (
                f"{o['name']} ({o['type']}) is verified but TERRA did not seed "
                "it — that is provenance being minted from an extraction")
        elif seeded:
            assert o["provenance"] == "verified", (
                f"{o['name']} is a TERRA seed but projected as "
                f"{o['provenance']}")


def test_projected_objects_carry_their_terra_id(projected):
    objs = objects_mod.list_objects(projected["ws"], limit=200)
    tagged = [o for o in objs if "terra" in (o.get("tags") or [])]
    assert tagged, "projected objects must be tagged so they can be re-synced"
    assert any(o["properties"].get("terraId") for o in tagged), \
        "terraId is how the Map resolves a clicked country to this object"


def test_sync_is_idempotent(ws):
    """Running the bridge twice must not double the graph — that is what
    'one-way and idempotent' has to mean in practice."""
    first = terra_bridge.sync(workspace_id=ws, max_nodes=40, max_edges=60,
                              attach_sources=False)
    if not first.get("ok"):
        pytest.skip(f"TERRA data unavailable: {first.get('error')}")

    count_after_first = len(objects_mod.list_objects(ws, limit=500))
    if not count_after_first:
        pytest.skip("TERRA graph is empty")

    terra_bridge.sync(workspace_id=ws, max_nodes=40, max_edges=60,
                      attach_sources=False)
    count_after_second = len(objects_mod.list_objects(ws, limit=500))
    assert count_after_second == count_after_first


def test_confidence_is_never_decorative(projected):
    """`confidence` is nullable and means 'not measured'. The bridge measures
    nothing, so it must not invent a number."""
    for o in objects_mod.list_objects(projected["ws"], limit=500):
        assert o.get("confidence") is None, \
            f"{o['name']} carries a confidence the bridge did not measure"
