"""Graph traversal — the `WorkspaceGraph` provider and the engine over it.

The engine was extracted out of TERRA behind a `NodeSource` protocol so the
algorithms are storage-agnostic. These tests drive it through the real SQL
provider, because the seam only pays off if both halves actually fit.
"""

from __future__ import annotations

import time

import pytest

from omnix.core import objects as objects_mod
from omnix.graph import engine, sql


@pytest.fixture
def chain(ws, obj):
    """A -> B -> C -> D, plus an isolated E. Linear so paths are unambiguous."""
    ids = {}
    for name in "ABCDE":
        ids[name] = obj("country", f"Country {name}")["id"]
    objects_mod.link(ws, ids["A"], ids["B"], "allied_with")
    objects_mod.link(ws, ids["B"], ids["C"], "allied_with")
    objects_mod.link(ws, ids["C"], ids["D"], "allied_with")
    return ids


# ---------------------------------------------------------------------------
# The provider
# ---------------------------------------------------------------------------
def test_graph_loads_nodes_and_edges(ws, chain):
    g = sql.load(ws)
    assert len(g) == 5
    assert g.edge_count == 3


def test_edges_are_traversable_from_both_endpoints(ws, chain):
    """Stored once, read both ways — otherwise "who supplies me" is unanswerable."""
    g = sql.load(ws)
    assert g.degree(chain["B"]) == 2
    assert g.degree(chain["A"]) == 1
    assert g.degree(chain["E"]) == 0


def test_type_filter_drops_dangling_edges(ws, obj):
    """An edge whose endpoint was filtered out must not survive, or traversal
    walks into a node that is not in the projection."""
    a = obj("country", "Alpha")["id"]
    p = obj("person", "Carol")["id"]
    objects_mod.link(ws, a, p, "member_of")

    g = sql.load(ws, types={"country"})
    assert len(g) == 1
    assert g.edge_count == 0
    assert list(g.edges_of(a)) == []


def test_relation_filter_is_applied(ws, obj):
    a = obj("country", "Alpha")["id"]
    b = obj("country", "Beta")["id"]
    objects_mod.link(ws, a, b, "allied_with")
    objects_mod.link(ws, a, b, "sanctions")

    assert sql.load(ws).edge_count == 2
    assert sql.load(ws, relations={"sanctions"}).edge_count == 1


def test_empty_workspace_loads_cleanly(ws):
    g = sql.load(ws)
    assert len(g) == 0 and g.edge_count == 0
    assert g.node("nope") is None
    assert list(g.edges_of("nope")) == []


# ---------------------------------------------------------------------------
# neighbors
# ---------------------------------------------------------------------------
def test_neighbors_returns_adjacent_nodes_only(ws, chain):
    g = sql.load(ws)
    got = {e["node"]["id"] for e in engine.neighbors(g, chain["B"])}
    assert got == {chain["A"], chain["C"]}


def test_neighbors_sorts_strongest_first(ws, obj):
    a = obj("country", "Alpha")["id"]
    weak = obj("country", "Weak")["id"]
    strong = obj("country", "Strong")["id"]
    objects_mod.link(ws, a, weak, "allied_with")
    for _ in range(5):
        objects_mod.link(ws, a, strong, "allied_with")

    g = sql.load(ws)
    order = [e["node"]["id"] for e in engine.neighbors(g, a)]
    assert order[0] == strong


def test_neighbors_respects_limit(ws, obj):
    a = obj("country", "Hub")["id"]
    for i in range(10):
        objects_mod.link(ws, a, obj("country", f"Spoke {i}")["id"], "allied_with")
    g = sql.load(ws)
    assert len(engine.neighbors(g, a, limit=3)) == 3


def test_neighbors_of_an_unknown_node_is_empty(ws, chain):
    assert engine.neighbors(sql.load(ws), "no-such-id") == []


# ---------------------------------------------------------------------------
# subgraph
# ---------------------------------------------------------------------------
def test_subgraph_depth_is_bounded_by_hops(ws, chain):
    g = sql.load(ws)
    one = engine.subgraph(g, [chain["A"]], hops=1)
    assert {n["id"] for n in one["nodes"]} == {chain["A"], chain["B"]}

    two = engine.subgraph(g, [chain["A"]], hops=2)
    assert {n["id"] for n in two["nodes"]} == {chain["A"], chain["B"], chain["C"]}


def test_subgraph_marks_roots_and_depths(ws, chain):
    out = engine.subgraph(sql.load(ws), [chain["A"]], hops=2)
    by_id = {n["id"]: n for n in out["nodes"]}
    assert by_id[chain["A"]]["root"] is True
    assert by_id[chain["A"]]["depth"] == 0
    assert by_id[chain["B"]]["depth"] == 1
    assert by_id[chain["C"]]["depth"] == 2


def test_subgraph_respects_max_nodes(ws, obj):
    a = obj("country", "Hub")["id"]
    for i in range(30):
        objects_mod.link(ws, a, obj("country", f"Spoke {i}")["id"], "allied_with")
    out = engine.subgraph(sql.load(ws), [a], hops=1, max_nodes=10, per_node=40)
    assert len(out["nodes"]) <= 10


def test_subgraph_never_emits_a_dangling_edge(ws, obj):
    """Every edge must reference nodes that are in the payload, or the renderer
    draws a line to nothing."""
    a = obj("country", "Hub")["id"]
    for i in range(30):
        objects_mod.link(ws, a, obj("country", f"Spoke {i}")["id"], "allied_with")
    out = engine.subgraph(sql.load(ws), [a], hops=2, max_nodes=12, per_node=40)
    ids = {n["id"] for n in out["nodes"]}
    for e in out["edges"]:
        assert e["source"] in ids and e["target"] in ids


def test_multi_root_subgraph_lists_a_shared_neighbour_once(ws, obj):
    hub = obj("country", "Hub")["id"]
    r1 = obj("country", "Root1")["id"]
    r2 = obj("country", "Root2")["id"]
    objects_mod.link(ws, r1, hub, "allied_with")
    objects_mod.link(ws, r2, hub, "allied_with")

    out = engine.subgraph(sql.load(ws), [r1, r2], hops=1)
    ids = [n["id"] for n in out["nodes"]]
    assert ids.count(hub) == 1


def test_subgraph_of_unknown_roots_is_empty(ws, chain):
    out = engine.subgraph(sql.load(ws), ["no-such-id"], hops=2)
    assert out["nodes"] == [] and out["edges"] == []


# ---------------------------------------------------------------------------
# expand
# ---------------------------------------------------------------------------
def test_expand_excludes_what_is_already_on_screen(ws, chain):
    g = sql.load(ws)
    out = engine.expand(g, chain["B"], exclude={chain["A"]})
    assert {n["id"] for n in out["nodes"]} == {chain["C"]}


# ---------------------------------------------------------------------------
# path_between
# ---------------------------------------------------------------------------
def test_path_between_finds_the_chain(ws, chain):
    path = engine.path_between(sql.load(ws), chain["A"], chain["D"])
    assert path, "A and D are connected through B and C"
    assert len(path) == 3


def test_path_between_returns_empty_when_unconnected(ws, chain):
    assert engine.path_between(sql.load(ws), chain["A"], chain["E"]) == []


def test_path_between_respects_max_hops(ws, chain):
    """Absence of a path within N hops is not evidence of no relationship."""
    assert engine.path_between(sql.load(ws), chain["A"], chain["D"], max_hops=2) == []


def test_path_between_identical_or_unknown_nodes_is_empty(ws, chain):
    g = sql.load(ws)
    assert engine.path_between(g, chain["A"], chain["A"]) == []
    assert engine.path_between(g, chain["A"], "no-such-id") == []


# ---------------------------------------------------------------------------
# importance and decay
# ---------------------------------------------------------------------------
def test_decay_factor_halves_over_the_half_life():
    now = time.time()
    assert engine.decay_factor(now, now) == pytest.approx(1.0, abs=1e-6)
    old = now - engine.HALF_LIFE_HOURS * 3600
    assert engine.decay_factor(old, now) == pytest.approx(0.5, abs=1e-3)


def test_importance_rises_with_connectedness(ws, chain):
    g = sql.load(ws)
    assert engine.importance(g, chain["B"]) > engine.importance(g, chain["E"])


def test_static_relations_do_not_decay(ws, obj):
    """A repository's own file tree must not fade out over three days."""
    parent = obj("repository", "repo")["id"]
    child = obj("file", "main.py")["id"]
    objects_mod.link(ws, parent, child, "contains")

    g = sql.load(ws)
    edge = list(g.edges_of(parent))[0]
    assert edge["static"] is True

    future = time.time() + engine.HALF_LIFE_HOURS * 3600 * 10
    fresh = engine.neighbors(g, parent, now=time.time())[0]["weight"]
    later = engine.neighbors(g, parent, now=future)[0]["weight"]
    assert later == pytest.approx(fresh)


def test_reported_relations_do_decay(ws, obj):
    a = obj("country", "Alpha")["id"]
    b = obj("country", "Beta")["id"]
    objects_mod.link(ws, a, b, "in_conflict_with")

    g = sql.load(ws)
    future = time.time() + engine.HALF_LIFE_HOURS * 3600 * 4
    fresh = engine.neighbors(g, a, now=time.time())[0]["weight"]
    later = engine.neighbors(g, a, now=future)[0]["weight"]
    assert later < fresh


# ---------------------------------------------------------------------------
# communities
# ---------------------------------------------------------------------------
def test_communities_separate_disconnected_clusters(ws, obj):
    left = [obj("country", f"L{i}")["id"] for i in range(3)]
    right = [obj("country", f"R{i}")["id"] for i in range(3)]
    objects_mod.link(ws, left[0], left[1], "allied_with")
    objects_mod.link(ws, left[1], left[2], "allied_with")
    objects_mod.link(ws, right[0], right[1], "allied_with")
    objects_mod.link(ws, right[1], right[2], "allied_with")

    labels = engine.communities(sql.load(ws))
    assert labels, "expected community labels"
    assert len({labels[n] for n in left}) == 1
    assert len({labels[n] for n in right}) == 1
    assert labels[left[0]] != labels[right[0]]
