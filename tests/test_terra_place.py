"""Location intelligence — the World Map's "what is happening here" briefing.

Nothing here touches the network. `place.brief` is driven with a fake service
carrying a handful of hand-written articles, because every behaviour worth
protecting is a decision about *which* of those articles counts as local and
why — and that decision must be legible without a live corpus.
"""

from __future__ import annotations

import time

from omnix.terra import place


NOW = time.time()


def _article(aid: str, title: str, *, summary: str = "", countries=(),
             hours_ago: float = 2.0, entities=(), source: str = "Reuters",
             domains=("news",)):
    return {
        "id": aid, "title": title, "url": f"https://example.test/{aid}",
        "source": source, "summary": summary,
        "published_ts": NOW - hours_ago * 3600,
        "domains": list(domains), "sentiment": -0.2, "severity": 0.4,
        "confidence": 0.8, "countries": list(countries),
        "entities": list(entities), "cluster": "",
    }


class FakeStore:
    def __init__(self, articles):
        self._articles = articles

    def all(self):
        return list(self._articles)


class FakeGraph:
    def __init__(self, nodes=()):
        self._nodes = list(nodes)

    def node(self, nid):
        return next((n for n in self._nodes if n["id"] == nid), None)

    def find(self, query, limit=12):
        q = query.lower()
        return [n for n in self._nodes if q in n["name"].lower()][:limit]

    def _public(self, node):
        return {**node, "glyph": "o", "color": "#fff",
                "type_label": node["type"].title(), "mentions": 10}


class FakeService:
    """Only the surface `place.brief` reads: store, ranked, graph, risk."""

    def __init__(self, articles, ranked=(), nodes=(), risk=None):
        self.store = FakeStore(articles)
        self.ranked = list(ranked)
        self.graph = FakeGraph(nodes)
        self.risk = risk or {}
        self.risk_deltas = {}


ARTICLES = [
    _article("a1", "Explosion reported in central Hyderabad",
             countries=["IN"], hours_ago=1,
             entities=[{"id": "country:IN", "type": "country", "name": "India",
                        "count": 2},
                       {"id": "person:x", "type": "person", "name": "X",
                        "count": 1}]),
    _article("a2", "India signs trade deal with Singapore",
             countries=["IN", "SG"], hours_ago=3),
    _article("a3", "Flooding closes roads across Telangana",
             countries=["IN"], hours_ago=6),
    _article("a4", "Tokyo markets open higher", countries=["JP"], hours_ago=2),
    _article("a5", "Old Hyderabad story", countries=["IN"], hours_ago=400),
]


def _svc():
    return FakeService(
        ARTICLES,
        ranked=[{"id": "ev1", "title": "Explosion in Hyderabad", "url": "",
                 "size": 3, "source_count": 2, "first_ts": NOW - 7200,
                 "last_ts": NOW - 3600, "when": "1h ago", "severity": 0.6,
                 "sentiment": -0.3, "corroboration": 0.9, "domains": ["news"],
                 "countries": ["IN"], "keywords": ["blast"], "status": {}},
                {"id": "ev2", "title": "Tokyo markets rally", "url": "",
                 "size": 2, "source_count": 2, "first_ts": NOW - 7200,
                 "last_ts": NOW - 1800, "when": "30m ago", "severity": 0.1,
                 "sentiment": 0.2, "corroboration": 0.5, "domains": ["economic"],
                 "countries": ["JP"], "keywords": [], "status": {}}],
        nodes=[{"id": "location:hyderabad", "type": "location",
                "name": "Hyderabad"},
               {"id": "news_story:s1", "type": "news_story",
                "name": "Hyderabad blast latest"}],
        risk={"IN": {"score": 12.0, "band": "low"}},
    )


def _brief(**kw):
    return place.brief(_svc(), name="Hyderabad", iso="IN", region="Telangana",
                       resolve=False, **kw)


# ---------------------------------------------------------------------------
# Scope: what counts as "here"
# ---------------------------------------------------------------------------
def test_articles_naming_the_place_are_local_and_carry_the_matched_term():
    result = _brief()
    local = [n for n in result["news"] if n["scope"] == "local"]
    assert {n["id"] for n in local} == {"a1", "a3"}
    assert local[0]["matched"] == ["hyderabad"]


def test_country_articles_are_kept_but_never_labelled_local():
    result = _brief()
    by_id = {n["id"]: n for n in result["news"]}
    assert by_id["a2"]["scope"] == "country"
    assert by_id["a2"]["matched"] == []


def test_other_countries_are_excluded_entirely():
    assert "a4" not in {n["id"] for n in _brief()["news"]}


def test_local_articles_lead_the_list_regardless_of_recency():
    # a2 is more recent than a3, and both are in the country; a3 names the
    # place. A busy country must never push the local rows off the top.
    news = _brief()["news"]
    assert [n["scope"] for n in news[:2]] == ["local", "local"]


def test_the_window_excludes_stale_articles():
    assert "a5" not in {n["id"] for n in _brief()["news"]}
    assert "a5" in {n["id"] for n in _brief(window_hours=1000)["news"]}


# ---------------------------------------------------------------------------
# The locality/country boundary
# ---------------------------------------------------------------------------
def test_the_country_name_is_not_a_locality_term():
    # Clicking anywhere in India must not make every story about India "local".
    result = place.brief(_svc(), name="India", iso="IN", resolve=False)
    assert result["terms"] == []
    assert all(n["scope"] == "country" for n in result["news"])


def test_a_city_that_doubles_as_a_country_metonym_survives():
    # `surface_forms` resolves "dubai" to AE, which would delete the only term
    # the headlines actually use. See `_country_names`.
    result = place.brief(FakeService([]), name="Dubai", iso="AE", resolve=False)
    assert "dubai" in result["terms"]


def test_word_boundaries_are_respected():
    svc = FakeService([_article("b1", "Hyderabadi biryani wins award",
                                countries=["IN"])])
    result = place.brief(svc, name="Hyderabad", iso="IN", resolve=False)
    assert result["news"][0]["scope"] == "country"


# ---------------------------------------------------------------------------
# The rest of the briefing
# ---------------------------------------------------------------------------
def test_stories_are_scoped_the_same_way_and_local_ones_lead():
    stories = _brief()["stories"]
    assert stories[0]["id"] == "ev1"
    assert stories[0]["scope"] == "local"
    assert "ev2" not in {s["id"] for s in stories}


def test_entities_are_counted_over_the_matched_articles_minus_the_country():
    ids = {e["id"] for e in _brief()["entities"]}
    assert "person:x" in ids
    assert "country:IN" not in ids


def test_graph_hits_drop_headlines_and_people():
    hits = {g["id"] for g in _brief()["graph_hits"]}
    assert hits == {"location:hyderabad"}


def test_summary_counts_and_risk_are_reported():
    result = _brief()
    assert result["summary"]["local"] == 2
    assert result["summary"]["country"] == 1
    assert result["country"]["risk"]["band"] == "low"
    assert result["summary"]["domains"]["news"] == 3


def test_a_point_in_no_country_returns_an_honest_empty_brief():
    result = place.brief(_svc(), lat=0.0, lon=-140.0, resolve=False)
    assert result["status"] == "ok"
    assert result["place"]["iso2"] == ""
    assert result["news"] == []
    assert result["country"]["risk"] is None


def test_a_summary_that_merely_restates_the_headline_is_dropped():
    svc = FakeService([_article("c1", "Blast in Hyderabad",
                                summary="Blast in Hyderabad - Reuters",
                                countries=["IN"])])
    result = place.brief(svc, name="Hyderabad", iso="IN", resolve=False)
    assert result["news"][0]["summary"] == ""


def test_a_real_summary_is_kept():
    svc = FakeService([_article("c2", "Blast in Hyderabad",
                                summary="Police said the cause is unknown.",
                                countries=["IN"])])
    result = place.brief(svc, name="Hyderabad", iso="IN", resolve=False)
    assert result["news"][0]["summary"].startswith("Police said")
