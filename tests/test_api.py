"""The graph HTTP surface — what the workspace frontend actually calls.

Mounted from the real router rather than the full `omnix.server`, so the suite
does not drag in ARENA, AVALON and Playwright to test twenty JSON routes. The
handlers, serialisation and status codes are the real ones.

Three conventions from the router's own docstring are what these assert:
`workspace` is a query parameter that falls back to the default, reads never
500 on missing data, and the ontology is served rather than duplicated.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from omnix.api import errors as api_errors
from omnix.api import objects as objects_api
from omnix.core import objects as objects_mod


@pytest.fixture(scope="module")
def client():
    app = FastAPI()
    app.include_router(objects_api.router)
    # Same handlers the real server installs, so a refusal is exercised as the
    # 404 a caller actually sees rather than as an unhandled 500.
    api_errors.install(app)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def populated(ws, obj):
    """Two linked countries and a person, in a workspace of their own."""
    a = obj("country", "Alphaland")
    b = obj("country", "Betaland")
    p = obj("person", "Dana Vance")
    objects_mod.link(ws, a["id"], b["id"], "in_conflict_with", sentiment=-0.4)
    objects_mod.link(ws, p["id"], a["id"], "member_of")
    return {"ws": ws, "a": a, "b": b, "p": p}


# ---------------------------------------------------------------------------
# Ontology
# ---------------------------------------------------------------------------
def test_ontology_route_serves_the_registry(client):
    r = client.get("/api/ontology")
    assert r.status_code == 200
    body = r.json()
    assert body["types"] and body["relations"] and body["provenance"]


# ---------------------------------------------------------------------------
# Objects
# ---------------------------------------------------------------------------
def test_objects_list_is_scoped_to_the_workspace(client, populated):
    r = client.get("/api/objects", params={"workspace": populated["ws"]})
    assert r.status_code == 200
    body = r.json()
    assert body["workspace"] == populated["ws"]
    assert {o["name"] for o in body["objects"]} == {
        "Alphaland", "Betaland", "Dana Vance"}


def test_objects_list_filters_by_type(client, populated):
    r = client.get("/api/objects",
                   params={"workspace": populated["ws"], "type": "person"})
    assert [o["name"] for o in r.json()["objects"]] == ["Dana Vance"]


def test_objects_lookup_by_external_id(client, populated):
    """How the Map resolves a clicked country to the Graph's object."""
    ext = populated["a"]["externalId"]
    r = client.get("/api/objects",
                   params={"workspace": populated["ws"], "externalId": ext})
    assert [o["id"] for o in r.json()["objects"]] == [populated["a"]["id"]]


def test_unknown_external_id_is_an_empty_list_not_an_error(client, populated):
    r = client.get("/api/objects",
                   params={"workspace": populated["ws"], "externalId": "nope:nope"})
    assert r.status_code == 200
    assert r.json()["objects"] == []


def test_get_object_returns_the_full_shape(client, populated):
    r = client.get(f"/api/objects/{populated['a']['id']}",
                   params={"workspace": populated["ws"]})
    assert r.status_code == 200
    body = r.json()
    for key in ("id", "type", "typeLabel", "family", "name",
                "provenance", "properties", "tags"):
        assert key in body, f"missing {key}"


def test_get_missing_object_is_404(client, populated):
    r = client.get("/api/objects/no-such-id",
                   params={"workspace": populated["ws"]})
    assert r.status_code == 404


def test_a_posted_object_is_recorded_as_user_created(client, ws):
    """A user POSTing an object is asserting it. Recording their own entry as
    an AI guess would be the wrong default — this is the one place provenance
    starts stronger than `ai_inferred`."""
    r = client.post("/api/objects", params={"workspace": ws},
                    json={"type": "company", "name": "Initech"})
    assert r.status_code in (200, 201)
    assert r.json()["object"]["provenance"] == "user_created"


def test_a_posted_object_cannot_claim_an_invented_provenance(client, ws):
    r = client.post("/api/objects", params={"workspace": ws},
                    json={"type": "company", "name": "Hooli",
                          "provenance": "extremely_true"})
    assert r.json()["object"]["provenance"] == "ai_inferred"


def test_create_object_without_a_name_is_rejected(client, ws):
    r = client.post("/api/objects", params={"workspace": ws},
                    json={"type": "company"})
    assert r.status_code == 400


def test_search_route_ranks_matches(client, populated):
    r = client.get("/api/objects/search",
                   params={"workspace": populated["ws"], "q": "alpha"})
    assert r.status_code == 200
    assert [o["name"] for o in r.json()["results"]] == ["Alphaland"]


def test_search_with_no_hits_is_empty_not_404(client, populated):
    r = client.get("/api/objects/search",
                   params={"workspace": populated["ws"], "q": "zzzzzz"})
    assert r.status_code == 200
    assert r.json()["results"] == []


def test_track_toggles_and_is_reflected_in_stats(client, populated):
    oid = populated["a"]["id"]
    r = client.post(f"/api/objects/{oid}/track",
                    params={"workspace": populated["ws"]}, json={"tracked": True})
    assert r.status_code == 200
    stats = client.get("/api/graph/stats",
                       params={"workspace": populated["ws"]}).json()
    assert stats["tracked"] == 1


def test_object_relationships_route(client, populated):
    r = client.get(f"/api/objects/{populated['a']['id']}/relationships",
                   params={"workspace": populated["ws"]})
    assert r.status_code == 200
    assert len(r.json()["relationships"]) == 2


def test_object_sources_is_empty_without_citations(client, populated):
    r = client.get(f"/api/objects/{populated['a']['id']}/sources",
                   params={"workspace": populated["ws"]})
    assert r.status_code == 200
    assert r.json()["sources"] == []


# ---------------------------------------------------------------------------
# Relationships
# ---------------------------------------------------------------------------
def test_create_relationship_coerces_an_invented_relation(client, populated):
    r = client.post("/api/relationships", params={"workspace": populated["ws"]},
                    json={"src": populated["a"]["id"], "dst": populated["p"]["id"],
                          "relation": "teleports_to"})
    assert r.status_code in (200, 201)
    assert r.json()["relationship"]["relation"] == "related_to"


def test_create_relationship_to_a_missing_object_is_404(client, populated):
    r = client.post("/api/relationships", params={"workspace": populated["ws"]},
                    json={"src": populated["a"]["id"], "dst": "no-such-id",
                          "relation": "allied_with"})
    assert r.status_code == 404


def test_create_relationship_without_endpoints_is_400(client, populated):
    r = client.post("/api/relationships", params={"workspace": populated["ws"]},
                    json={"relation": "allied_with"})
    assert r.status_code == 400


def test_delete_relationship_removes_it(client, populated):
    rels = client.get(f"/api/objects/{populated['a']['id']}/relationships",
                      params={"workspace": populated["ws"]}).json()["relationships"]
    rid = rels[0]["id"]
    assert client.delete(f"/api/relationships/{rid}",
                         params={"workspace": populated["ws"]}).status_code == 200
    after = client.get(f"/api/objects/{populated['a']['id']}/relationships",
                       params={"workspace": populated["ws"]}).json()["relationships"]
    assert rid not in [x["id"] for x in after]


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------
def test_graph_returns_nodes_and_edges(client, populated):
    r = client.get("/api/graph", params={"workspace": populated["ws"], "hops": 2})
    assert r.status_code == 200
    body = r.json()
    assert body["nodes"] and body["edges"]


def test_empty_graph_is_empty_lists_not_an_error(client, ws):
    r = client.get("/api/graph", params={"workspace": ws})
    assert r.status_code == 200
    assert r.json()["nodes"] == [] and r.json()["edges"] == []


def test_graph_expand_excludes_what_is_on_screen(client, populated):
    r = client.post("/api/graph/expand", params={"workspace": populated["ws"]},
                    json={"id": populated["a"]["id"],
                          "exclude": [populated["b"]["id"]]})
    assert r.status_code == 200
    assert populated["b"]["id"] not in [n["id"] for n in r.json()["nodes"]]


def test_graph_path_between_connected_objects(client, populated):
    r = client.get("/api/graph/path",
                   params={"workspace": populated["ws"],
                           "src": populated["p"]["id"], "dst": populated["b"]["id"]})
    assert r.status_code == 200
    assert len(r.json()["path"]) == 2


def test_graph_path_with_no_connection_is_an_empty_path(client, ws, obj):
    a, b = obj("country", "Lone A"), obj("country", "Lone B")
    r = client.get("/api/graph/path",
                   params={"workspace": ws, "src": a["id"], "dst": b["id"]})
    assert r.status_code == 200
    assert r.json()["path"] == []


def test_graph_stats_counts_by_type_and_provenance(client, populated):
    body = client.get("/api/graph/stats",
                      params={"workspace": populated["ws"]}).json()
    assert body["objects"] == 3
    assert body["relationships"] == 2
    assert body["byType"]["country"] == 2
    assert body["byProvenance"]["ai_inferred"] == 3


# ---------------------------------------------------------------------------
# Timeline and events
# ---------------------------------------------------------------------------
def test_timeline_is_empty_without_events(client, populated):
    r = client.get("/api/timeline", params={"workspace": populated["ws"]})
    assert r.status_code == 200
    assert r.json()["events"] == []


def test_created_event_appears_on_the_timeline(client, populated):
    r = client.post("/api/events", params={"workspace": populated["ws"]},
                    json={"title": "Border incident", "objectId": populated["a"]["id"]})
    assert r.status_code in (200, 201)
    events = client.get("/api/timeline",
                        params={"workspace": populated["ws"]}).json()["events"]
    assert [e["title"] for e in events] == ["Border incident"]


def test_event_without_a_title_is_rejected(client, populated):
    r = client.post("/api/events", params={"workspace": populated["ws"]}, json={})
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# Saved views
# ---------------------------------------------------------------------------
def test_saved_views_round_trip(client, ws):
    created = client.post("/api/views", params={"workspace": ws},
                          json={"name": "My graph", "view": "graph",
                                "state": {"hops": 2}})
    assert created.status_code in (200, 201)
    # Note: unlike POST /objects and POST /relationships, this route returns
    # the saved view unwrapped rather than under a key.
    vid = created.json()["id"]

    listed = client.get("/api/views", params={"workspace": ws}).json()["views"]
    assert vid in [v["id"] for v in listed]

    assert client.delete(f"/api/views/{vid}",
                         params={"workspace": ws}).status_code == 200
    after = client.get("/api/views", params={"workspace": ws}).json()["views"]
    assert vid not in [v["id"] for v in after]


# ---------------------------------------------------------------------------
# Workspace fallback
# ---------------------------------------------------------------------------
def test_omitting_workspace_falls_back_to_the_default(client):
    """Every legacy entry point calls these without knowing workspaces exist."""
    r = client.get("/api/objects")
    assert r.status_code == 200
    assert r.json()["workspace"], "a workspace id must always be resolved"


def test_an_unknown_workspace_is_refused_not_silently_swapped(client):
    """This assertion was inverted deliberately.

    It used to require that an unrecognised workspace id fall back to the
    default and answer 200. That is exactly what made the id parameter unsafe:
    a caller naming someone else's Space — or a Space that does not exist —
    got a successful answer about a Space they never asked for. Falling back is
    reasonable when the id is *absent* (the test above still covers that) and
    is a security hole when the id is *wrong*.
    """
    r = client.get("/api/graph/stats", params={"workspace": "no-such-workspace"})
    assert r.status_code == 404
    assert r.json()["error"] == "unknown workspace"
