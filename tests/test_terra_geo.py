"""TERRA geospatial layer.

Nothing here touches the network. Every test either exercises pure local logic
(the geometry, the scorer, the opening-hours parser, the validator) or drives
the provider chain with a fake provider — which is the whole argument for
having a provider abstraction in the first place: the fallback, caching and
degradation behaviour is testable without a single HTTP request.
"""

from __future__ import annotations

import math

import pytest

from omnix.terra.geo import cache, spatial, tools
from omnix.terra.geo.config import reload as reload_settings, settings
from omnix.terra.geo.core import places as places_svc
from omnix.terra.geo.core import scoring
from omnix.terra.geo.providers import registry
from omnix.terra.geo.providers.base import canonical_category
from omnix.terra.geo.types import (Coord, Freshness, Mode, Place, Result,
                                   Route)


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------
BENGALURU = Coord(12.9716, 77.5946)
CHENNAI = Coord(13.0827, 80.2707)


def test_haversine_matches_known_distance():
    km = spatial.haversine_m(BENGALURU, CHENNAI) / 1000.0
    # Published great-circle distance is ~290km.
    assert 288 < km < 292


def test_coord_rejects_out_of_range():
    with pytest.raises(ValueError):
        Coord(91.0, 0.0)
    with pytest.raises(ValueError):
        Coord(0.0, 181.0)


def test_bearing_and_compass():
    assert spatial.compass(spatial.bearing_deg(BENGALURU, CHENNAI)) in ("E", "ENE")
    assert spatial.compass(0) == "N"
    assert spatial.compass(180) == "S"


def test_destination_round_trips():
    target = spatial.destination(BENGALURU, 90.0, 10_000)
    assert abs(spatial.haversine_m(BENGALURU, target) - 10_000) < 50


def test_bbox_around_contains_the_circle():
    south, west, north, east = spatial.bbox_around(BENGALURU, 1000)
    assert south < BENGALURU.lat < north
    assert west < BENGALURU.lon < east
    # A point due east at the radius must fall inside the box.
    east_point = spatial.destination(BENGALURU, 90.0, 990)
    assert west <= east_point.lon <= east


def test_bbox_degenerates_safely_at_the_pole():
    """Longitude spans without bound at the pole; the box must clamp rather
    than produce nonsense."""
    south, west, north, east = spatial.bbox_around(Coord(89.999, 0.0), 5000)
    assert west == -180.0 and east == 180.0
    assert north <= 90.0 and south >= -90.0


def test_point_in_polygon():
    square = [Coord(0, 0), Coord(0, 2), Coord(2, 2), Coord(2, 0)]
    assert spatial.inside_polygon(Coord(1, 1), square)
    assert not spatial.inside_polygon(Coord(3, 1), square)
    # Degenerate input is False, never an exception.
    assert not spatial.inside_polygon(Coord(1, 1), [Coord(0, 0)])


def test_route_circle_intersection_uses_segments_not_vertices():
    """The bug this guards: a motorway is two vertices kilometres apart, so a
    fence in the middle of it has no vertex inside and reads as a miss."""
    route = [Coord(12.90, 77.50), Coord(13.05, 77.70)]
    midpoint = Coord(12.975, 77.60)
    assert not any(spatial.haversine_m(v, midpoint) <= 300 for v in route), \
        "fixture must have no vertex near the fence, or it proves nothing"
    assert spatial.route_intersects_circle(route, midpoint, 400)


def test_simplify_keeps_the_ends_and_drops_noise():
    line = [Coord(12.0 + i * 0.0001, 77.0) for i in range(200)]
    out = spatial.simplify(line, tolerance_m=20.0)
    assert out[0] == line[0] and out[-1] == line[-1]
    assert len(out) < len(line)


def test_simplify_survives_a_long_route():
    """Iterative, not recursive: a long route genuinely exceeds Python's
    recursion limit and the recursive form crashed on it."""
    line = [Coord(12.0 + i * 0.00005, 77.0 + math.sin(i / 30.0) * 0.001)
            for i in range(5000)]
    assert len(spatial.simplify(line, tolerance_m=5.0)) >= 2


def test_decode_polyline_round_trip():
    """The reference string from Google's polyline documentation."""
    points = spatial.decode_polyline("_p~iF~ps|U_ulLnnqC_mqNvxq`@")
    assert len(points) == 3
    assert points[0].lat == pytest.approx(38.5, abs=0.01)
    assert points[0].lon == pytest.approx(-120.2, abs=0.01)


def test_decode_polyline_never_raises_on_garbage():
    assert spatial.decode_polyline("!!!not a polyline!!!") is not None
    assert spatial.decode_polyline("") == []


# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text,expected", [
    ("coffee", "cafe"),
    ("coffee shop", "cafe"),
    ("chemist", "pharmacy"),
    ("medical store", "pharmacy"),
    ("petrol pump", "fuel"),
    ("gas station", "fuel"),
    ("find me a good coffee shop", "cafe"),
    ("college", "university"),
    ("hospital", "hospital"),
    ("", ""),
    ("xyzzy", ""),
])
def test_canonical_category(text, expected):
    assert canonical_category(text) == expected


# ---------------------------------------------------------------------------
# Opening hours
# ---------------------------------------------------------------------------
def test_opening_hours_is_tristate():
    """Unknown must stay unknown. Collapsing it either hides open places or
    sends someone to a shut door."""
    assert places_svc._open_state(Place("x", BENGALURU)) is None
    assert places_svc._open_state(
        Place("x", BENGALURU, opening_hours="24/7")) is True
    assert places_svc._open_state(
        Place("x", BENGALURU, open_now=False)) is False


def test_opening_hours_handles_past_midnight():
    """A bar open 22:00-02:00 is open at 23:00, not permanently closed."""
    from datetime import datetime
    late = datetime(2026, 8, 12, 23, 30)
    assert places_svc._open_state(
        Place("bar", BENGALURU, opening_hours="Mo-Su 22:00-02:00"), late) is True


def test_opening_hours_day_ranges():
    from datetime import datetime
    wednesday = datetime(2026, 8, 12, 10, 0)
    sunday = datetime(2026, 8, 16, 10, 0)
    weekday = Place("x", BENGALURU, opening_hours="Mo-Fr 09:00-17:00")
    assert places_svc._open_state(weekday, wednesday) is True
    assert places_svc._open_state(weekday, sunday) is False


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------
def test_ranking_puts_an_open_place_above_a_closed_nearer_one():
    near_closed = Place("Near but shut", spatial.destination(BENGALURU, 0, 100),
                        open_now=False, distance_m=100)
    far_open = Place("Further but open", spatial.destination(BENGALURU, 0, 900),
                     open_now=True, distance_m=900)
    ranked = places_svc.rank([near_closed, far_open], near=BENGALURU)
    assert ranked[0].name == "Further but open"


def test_ranking_ignores_ratings_when_nothing_has_one():
    """OSM has no ratings. A rating term over a set with none would rank every
    result identically and imply the order meant something."""
    a = Place("A", spatial.destination(BENGALURU, 0, 100), distance_m=100)
    b = Place("B", spatial.destination(BENGALURU, 0, 500), distance_m=500)
    ranked = places_svc.rank([b, a], near=BENGALURU)
    assert ranked[0].name == "A"
    assert "_score_rating" not in ranked[0].tags


def test_nearest_searches_outward_in_rings(monkeypatch):
    """The regression this guards is the worst bug found building TERRA.

    Overpass returns elements in quadtile order under an element cap, so a
    large-radius query truncates SPATIALLY — it keeps one corner of the search
    area. Measured live: a 5km pharmacy search returned nothing closer than
    693m while a 1km search found one at 360m. Sorting the truncated set by
    distance cannot recover what was never sent, and the answer looks
    perfectly plausible. On the "nearest hospital" path that is dangerous.

    So `nearest` must issue a SMALL query first and stop at the first ring
    with a hit, never one big query it then sorts.
    """
    calls: list[float] = []

    def fake_search(*, near, category="", query="", radius_m=2000, limit=20,
                    open_now=None, require_ratings=False):
        calls.append(radius_m)
        # Only the smallest ring has the true nearest — exactly the real
        # failure mode, inverted so a single big query would get it wrong.
        if radius_m <= 750:
            return Result(data=[Place("Close one", BENGALURU, distance_m=360)],
                          freshness=Freshness.LIVE, provider="fake")
        return Result(data=[Place("Far one", CHENNAI, distance_m=3331)],
                      freshness=Freshness.LIVE, provider="fake")

    monkeypatch.setattr(places_svc, "search", fake_search)
    out = places_svc.nearest(near=BENGALURU, category="pharmacy",
                             radius_m=5000)
    assert out.data[0].name == "Close one"
    assert calls[0] <= 750, "the first query must be the smallest ring"
    assert len(calls) == 1, "a ring that hits must stop the ladder"


def test_nearest_expands_when_a_ring_is_empty(monkeypatch):
    calls: list[float] = []

    def fake_search(*, near, category="", query="", radius_m=2000, limit=20,
                    open_now=None, require_ratings=False):
        calls.append(radius_m)
        if radius_m < 5000:
            return Result(data=[], freshness=Freshness.LIVE, provider="fake")
        return Result(data=[Place("Found", BENGALURU, distance_m=4800)],
                      freshness=Freshness.LIVE, provider="fake")

    monkeypatch.setattr(places_svc, "search", fake_search)
    out = places_svc.nearest(near=BENGALURU, category="hospital",
                             radius_m=5000)
    assert out.data[0].name == "Found"
    assert len(calls) == 3, "empty rings must expand outward"


def test_overpass_does_not_trim_before_ranking(monkeypatch):
    """Trimming inside the provider re-creates the same bug one layer up.

    Overpass hands back elements in quadtile order, so cutting the list to
    `limit` in the provider discards candidates by POSITION before anything has
    measured their distance — the nearest result can be dropped before ranking
    ever sees it. The provider must return everything it parsed and let
    `core.places` apply the limit after sorting.
    """
    from omnix.terra.geo.providers import overpass

    # 50 elements returned for a request whose limit is 5.
    elements = [{"type": "node", "id": i, "lat": 12.97 + i * 0.001,
                 "lon": 77.59, "tags": {"name": f"P{i}", "amenity": "cafe"}}
                for i in range(50)]

    class _Resp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"elements": elements}

    class _Client:
        def post(self, *a, **kw):
            return _Resp()

    monkeypatch.setattr(overpass, "client", lambda: _Client())
    out = overpass.OverpassProvider().search_places(
        near=BENGALURU, category="cafe", radius_m=2000, limit=5)
    assert len(out) == 50, "the provider must not trim before ranking"


def test_ranking_writes_an_explainable_breakdown():
    p = Place("A", BENGALURU, distance_m=100, rating=4.5, rating_count=200)
    ranked = places_svc.rank([p, Place("B", BENGALURU, distance_m=800,
                                       rating=3.0, rating_count=10)],
                             near=BENGALURU)
    assert "_score" in ranked[0].tags
    assert "_score_distance" in ranked[0].tags


# ---------------------------------------------------------------------------
# Route scoring
# ---------------------------------------------------------------------------
def _route(distance_m: float, duration_s: float, traffic: float | None = None,
           steps: int = 5) -> Route:
    from omnix.terra.geo.types import Step
    return Route(distance_m=distance_m, duration_s=duration_s,
                 duration_traffic_s=traffic,
                 steps=[Step("go", 1, 1) for _ in range(steps)])


def test_scoring_prefers_the_faster_route():
    routes = [_route(10_000, 1200), _route(9_000, 1800)]
    scored = scoring.score(routes, workspace_id=None)
    assert scored[0].duration_s == 1200


def test_scoring_keeps_zero_terms_so_the_winner_is_explainable():
    """The regression this guards: filtering zero-valued terms left the winner
    with an empty breakdown, and `explain` then reported 'only one route was
    available' while three were on screen."""
    routes = [_route(9_000, 1000), _route(12_000, 1500)]
    scored = scoring.score(routes, workspace_id=None)
    assert scored[0].score_parts, "the best route must still have a breakdown"
    assert "only one route" not in scoring.explain(scored[0])


def test_scoring_a_single_route_makes_no_claim():
    scored = scoring.score([_route(9_000, 1000)], workspace_id=None)
    assert scored[0].score_parts == {}
    assert "one route" in scoring.explain(scored[0])


def test_scoring_is_relative_not_absolute():
    """Two near-identical routes must still separate; the range normalisation
    is what makes the scorer useful when the options are similar."""
    routes = [_route(10_000, 1000), _route(10_100, 1010)]
    scored = scoring.score(routes, workspace_id=None)
    assert scored[0].score != scored[1].score


def test_weather_penalty_applies_only_when_passed():
    plain = scoring.score([_route(1, 100), _route(2, 200)], workspace_id=None)
    assert "weather" not in plain[0].score_parts
    wet = scoring.score([_route(1, 100), _route(2, 200)], workspace_id=None,
                        weather_penalty=0.8)
    assert wet[0].score_parts.get("weather", 0) > 0


# ---------------------------------------------------------------------------
# Environment interpretation
# ---------------------------------------------------------------------------
def test_sun_times_are_local_not_utc():
    """The bug this guards: defaulting the offset to zero printed a Bengaluru
    sunrise of 00:36, which are the real UTC times."""
    from omnix.terra.geo.core import environment
    from datetime import date
    times = environment.sun_times(BENGALURU, date(2026, 8, 12),
                                  utc_offset_s=19800)
    assert times["sunrise"] is not None
    hour = int(times["sunrise"].split(":")[0])
    assert 5 <= hour <= 7, f"sunrise {times['sunrise']} is not a morning time"
    assert times["offsetSource"] == "given"


def test_sun_times_fall_back_to_longitude():
    from omnix.terra.geo.core import environment
    times = environment.sun_times(BENGALURU)
    assert times["offsetSource"] == "longitude"
    assert 4 <= int(times["sunrise"].split(":")[0]) <= 7


def test_sun_times_report_polar_day_honestly():
    from omnix.terra.geo.core import environment
    from datetime import date
    times = environment.sun_times(Coord(80.0, 20.0), date(2026, 6, 21))
    assert times["sunrise"] is None
    assert "midnight sun" in times["note"]


def test_exposure_penalty_is_zero_for_driving():
    from omnix.terra.geo.core import environment
    from omnix.terra.geo.types import Weather
    wet = Weather(precipitation_probability_pct=90, temperature_c=38)
    assert environment.exposure_penalty(wet, "driving") == 0.0
    assert environment.exposure_penalty(wet, "walking") > 0.0


def test_outdoor_signals_never_return_a_verdict():
    """The reasoning belongs to OMNIX. A verdict field here would move it into
    the data layer and make the explanation a paraphrase of a hidden rule."""
    from omnix.terra.geo.core import environment
    from omnix.terra.geo.types import AirQuality, Weather
    out = environment.outdoor_signals(
        Weather(temperature_c=36, uv_index=9),
        AirQuality(band="unhealthy", dominant="PM2.5"))
    assert "verdict" not in out
    assert "recommendation" not in out
    assert out["concerns"]


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------
def test_cache_key_is_order_independent():
    assert cache.key_for("x", a=1, b=2) == cache.key_for("x", b=2, a=1)
    assert cache.key_for("x", a=1) != cache.key_for("x", a=2)


def test_spatial_key_snaps_gps_jitter_into_one_key():
    """The single biggest cost saving: GPS jitter must not be a cache miss.

    Grid snapping is not distance-based, so two points either side of a cell
    boundary DO get different keys however close they are — that is inherent
    and acceptable, since the win is a stationary user producing one key rather
    than every nearby pair sharing one. This asserts the property that
    actually holds: a spread of fixes around a point collapses to a single key.
    """
    base_lat, base_lon = 12.9716, 77.5946
    raw = {(base_lat + dx * 0.0002, base_lon + dy * 0.0002)
           for dx in range(-3, 4) for dy in range(-3, 4)}
    keys = {cache.spatial_key(lat, lon) for lat, lon in raw}
    assert len(raw) == 49
    # 49 distinct coordinates — 49 paid lookups without snapping — collapse to
    # a handful. Not always exactly one: this base sits near a cell edge, and
    # a spread straddling one lands in two or four cells. The saving is the
    # order of magnitude, not the exact count.
    assert len(keys) <= 4

    assert (cache.spatial_key(12.97, 77.59)
            != cache.spatial_key(13.50, 77.59))


def test_spatial_key_away_from_a_boundary_is_exactly_one_key():
    """At a cell CENTRE the collapse is total. Cells at precision 2 span
    [x.xx5, x.xx5+0.01), so 12.9700 / 77.5900 are centres and 12.9750 /
    77.5950 are edges — which is why the test above allows up to four."""
    keys = {cache.spatial_key(12.9700 + dx * 0.0002, 77.5900 + dy * 0.0002)
            for dx in range(-3, 4) for dy in range(-3, 4)}
    assert len(keys) == 1


def test_spatial_key_cell_is_about_a_kilometre():
    """Documents the grid size the TTLs were chosen against."""
    a = cache.spatial_key(12.9716, 77.5946)
    b = cache.spatial_key(12.9716 + 0.05, 77.5946)
    assert a != b
    assert spatial.haversine_m(Coord(a[0], a[1]), Coord(b[0], b[1])) > 3000


def test_cache_round_trip_and_expiry():
    key = cache.key_for("test", n=1)
    cache.invalidate("test")
    assert cache.get(key) is None
    cache.put(key, {"v": 1}, ttl_s=100)
    assert cache.get(key).value == {"v": 1}

    cache.put(key, {"v": 2}, ttl_s=-1)          # already expired
    assert cache.get(key) is None               # not served as fresh
    assert cache.get(key, allow_stale=True).value == {"v": 2}
    cache.invalidate("test")


def test_fetch_serves_stale_when_the_producer_fails():
    """The core degradation rule: a stale answer beats no answer, but it must
    come back LABELLED stale."""
    cache.invalidate("stale-test")
    key = cache.key_for("stale-test", n=1)
    cache.put(key, {"v": "old"}, ttl_s=-1, provider="fake")

    def boom():
        raise RuntimeError("provider down")

    result = cache.fetch(key, "stale-test", "fake", boom)
    assert result.data == {"v": "old"}
    assert result.freshness is Freshness.STALE
    assert result.freshness is not Freshness.LIVE
    cache.invalidate("stale-test")


def test_fetch_returns_offline_with_no_cache_at_all():
    cache.invalidate("offline-test")
    key = cache.key_for("offline-test", n=1)

    def boom():
        raise RuntimeError("provider down")

    result = cache.fetch(key, "offline-test", "fake", boom)
    assert result.freshness is Freshness.OFFLINE
    assert not result.ok
    assert "provider down" in result.error


def test_rate_limit_bucket_blocks_beyond_its_rate():
    bucket = cache._Bucket(rate_per_s=1.0, burst=1.0)
    assert bucket.acquire(max_wait_s=0.1)
    assert not bucket.acquire(max_wait_s=0.05)


def test_health_circuit_opens_after_repeated_failure():
    cache.mark_ok("probe")
    assert cache.healthy("probe")
    for _ in range(3):
        cache.mark_failed("probe")
    assert not cache.healthy("probe")
    cache.mark_ok("probe")
    assert cache.healthy("probe")


# ---------------------------------------------------------------------------
# Provider chain
# ---------------------------------------------------------------------------
class _Fake:
    """A provider that does exactly what the test tells it to."""

    def __init__(self, name, result=None, fail=False, available=True):
        self.name = name
        self.result = result
        self.fail = fail
        self._available = available
        self.calls = 0

    def available(self):
        return self._available

    def go(self):
        self.calls += 1
        if self.fail:
            raise RuntimeError(f"{self.name} exploded")
        return self.result


def test_chain_falls_through_a_failing_provider():
    cache.invalidate("chaintest")
    bad = _Fake("bad-a", fail=True)
    good = _Fake("good-a", result=["answer"])
    result = registry.first_ok([bad, good], "chaintest", lambda p: p.go(),
                               {"k": "fallthrough"})
    assert result.data == ["answer"]
    assert result.provider == "good-a"
    assert any("bad-a" in a for a in result.attempted)
    cache.invalidate("chaintest")


def test_chain_skips_an_unavailable_provider_without_calling_it():
    cache.invalidate("chaintest")
    off = _Fake("off-a", result=["no"], available=False)
    on = _Fake("on-a", result=["yes"])
    result = registry.first_ok([off, on], "chaintest", lambda p: p.go(),
                               {"k": "skip"})
    assert result.data == ["yes"]
    assert off.calls == 0
    cache.invalidate("chaintest")


def test_empty_is_a_miss_for_geocoding_but_an_answer_for_places():
    """The flag that stops "no cafés within 2km" cascading to a paid provider
    while still letting a geocoder that does not index addresses fall through."""
    cache.invalidate("emptytest")
    empty = _Fake("empty-a", result=[])
    backup = _Fake("backup-a", result=["found"])

    miss = registry.first_ok([empty, backup], "emptytest", lambda p: p.go(),
                             {"k": "as-miss"}, empty_is_miss=True)
    assert miss.data == ["found"]

    cache.invalidate("emptytest")
    empty2 = _Fake("empty-b", result=[])
    backup2 = _Fake("backup-b", result=["found"])
    answer = registry.first_ok([empty2, backup2], "emptytest", lambda p: p.go(),
                               {"k": "as-answer"}, empty_is_miss=False)
    assert answer.data == []
    assert backup2.calls == 0, "a real empty answer must not cost a second call"
    cache.invalidate("emptytest")


def test_exhausted_chain_returns_offline_rather_than_raising():
    cache.invalidate("chaintest")
    result = registry.first_ok([_Fake("x1", fail=True), _Fake("x2", fail=True)],
                               "chaintest", lambda p: p.go(), {"k": "dead"})
    assert result.freshness is Freshness.OFFLINE
    assert not result.ok
    cache.invalidate("chaintest")


def test_osrm_refuses_walking_on_the_public_demo():
    """Verified against the live endpoint: driving, walking and cycling return
    byte-identical results, so the profile in the URL is ignored. Answering a
    walking request with car timings is silent corruption, not degradation."""
    from omnix.terra.geo.providers.osrm import OSRMProvider
    provider = OSRMProvider()
    assert settings().keys.osrm_url == "https://router.project-osrm.org"
    assert provider.supports(Mode.DRIVING)
    assert not provider.supports(Mode.WALKING)
    assert not provider.supports(Mode.TRANSIT)


# ---------------------------------------------------------------------------
# Configuration and secrets
# ---------------------------------------------------------------------------
def test_no_capability_requires_a_key():
    """A fresh clone with no .env must have a provider for everything."""
    caps = registry
    assert [p for p in caps.geocode_chain() if p.available()]
    assert [p for p in caps.reverse_chain() if p.available()]
    assert [p for p in caps.places_chain() if p.available()]
    assert caps.route_chain(Mode.DRIVING)
    assert [p for p in caps.weather_chain() if p.available()]
    assert [p for p in caps.air_quality_chain() if p.available()]
    assert [p for p in caps.elevation_chain() if p.available()]


def test_describe_never_leaks_a_credential(monkeypatch):
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "SUPER-SECRET-KEY-VALUE")
    reload_settings()
    try:
        blob = repr(settings().describe())
        assert "SUPER-SECRET" not in blob
        assert settings().describe()["providers"]["google"]["configured"] is True
    finally:
        monkeypatch.delenv("GOOGLE_MAPS_API_KEY", raising=False)
        reload_settings()


def test_status_never_leaks_a_credential(monkeypatch):
    from omnix.terra.geo import api as terra
    monkeypatch.setenv("GRAPHHOPPER_API_KEY", "GH-SECRET-VALUE")
    reload_settings()
    try:
        assert "GH-SECRET" not in repr(terra.status())
    finally:
        monkeypatch.delenv("GRAPHHOPPER_API_KEY", raising=False)
        reload_settings()


# ---------------------------------------------------------------------------
# Tool layer
# ---------------------------------------------------------------------------
def test_unknown_tool_is_refused():
    assert not tools.invoke("rm_rf", {})["ok"]
    assert "unknown tool" in tools.invoke("rm_rf", {})["error"]


def test_unknown_arguments_are_dropped_not_passed_through():
    """The line that matters: without it a model could append `key=` or `url=`
    and have it reach a provider function as a kwarg."""
    clean, error = tools.validate("get_weather",
                                  {"lat": 12.9, "lon": 77.5,
                                   "api_key": "leak", "url": "http://evil"})
    assert not error
    assert clean == {"lat": 12.9, "lon": 77.5}


def test_out_of_range_values_are_clamped_not_rejected():
    clean, error = tools.validate("search_places",
                                  {"lat": 12.9, "lon": 77.5, "radius_m": 999_999})
    assert not error
    assert clean["radius_m"] == 50_000


def test_invalid_coordinates_are_rejected():
    _, error = tools.validate("get_weather", {"lat": 200, "lon": 0})
    assert "out of range" in error


def test_missing_required_argument_is_rejected():
    _, error = tools.validate("get_weather", {"lat": 12.9})
    assert "missing required" in error


def test_categories_are_canonicalised_not_rejected():
    clean, error = tools.validate("search_places",
                                  {"lat": 12.9, "lon": 77.5,
                                   "category": "coffee shop"})
    assert not error
    assert clean["category"] == "cafe"


def test_bad_mode_falls_back_rather_than_failing():
    clean, error = tools.validate("get_route",
                                  {"origin": "a", "destination": "b",
                                   "mode": "teleport"})
    assert not error
    assert clean["mode"] == "driving"


def test_schema_is_complete_and_matches_the_handlers():
    schema = tools.schema()
    assert len(schema) == len(tools.TOOLS)
    handlers = tools._handlers()
    for name in tools.TOOLS:
        assert name in handlers, f"{name} has a schema but no handler"


@pytest.mark.parametrize("text,tool", [
    ("where am i", "reverse_geocode"),
    ("what's around me", "get_spatial_context"),
    ("find coffee shops near me", "search_places"),
    ("nearest hospital", "nearest_poi"),
    ("what's the weather", "get_weather"),
    ("air quality here", "get_air_quality"),
    ("should i go for a run", "get_environmental_context"),
    ("somewhere quiet to work", "find_quiet_place"),
    ("take me to college", "get_route"),
    ("my saved places", "known_locations"),
    ("where have i been", "location_history"),
])
def test_deterministic_parse_covers_the_common_requests(text, tool):
    """Every one of these is answered with no model call at all."""
    call = tools.parse(text, lat=12.97, lon=77.59)
    assert call is not None, f"{text!r} did not parse"
    assert call["tool"] == tool


def test_parse_defers_rather_than_guessing():
    """Returning None is the important half — a wrong tool produces a confident
    answer to a question nobody asked."""
    assert tools.parse("summarise the last research run", lat=12.9, lon=77.5) is None
    assert tools.parse("", lat=12.9, lon=77.5) is None


def test_parse_extracts_a_radius_only_with_a_unit():
    call = tools.parse("find cafes near me within 5 km", lat=12.97, lon=77.59)
    assert call["args"]["radius_m"] == 5000.0
    # A bare number is a count, not a distance.
    plain = tools.parse("find 3 cafes near me", lat=12.97, lon=77.59)
    assert plain["args"]["radius_m"] == 2000.0


def test_parse_reads_the_travel_mode():
    assert tools.parse("walk to college")["args"]["mode"] == "walking"
    assert tools.parse("cycle to college")["args"]["mode"] == "cycling"
    assert tools.parse("take me to college")["args"]["mode"] == "driving"


def test_workspace_is_injected_never_accepted_from_the_caller():
    """A tool that could be told which workspace to read would let one Space's
    saved places be read from another."""
    out = tools.invoke("known_locations", {"workspace_id": "someone-else"},
                       workspace_id=None)
    assert not out["ok"]
    assert "workspace" in out["error"]


# ---------------------------------------------------------------------------
# Offline mode
# ---------------------------------------------------------------------------
def test_offline_mode_degrades_rather_than_erroring(monkeypatch):
    from omnix.terra.geo import api as terra
    monkeypatch.setenv("TERRA_OFFLINE", "1")
    reload_settings()
    cache.invalidate()
    try:
        out = terra.get_weather(12.97, 77.59)
        assert out["weather"] is None
        assert out["freshness"] == "offline"
        assert out["error"]

        # A straight-line estimate is still produced, and is labelled.
        route = terra.get_route({"lat": 12.97, "lon": 77.59},
                                {"lat": 13.08, "lon": 80.27})
        assert route["freshness"] == "estimated"
        assert route["routes"]
        assert "not a road route" in route["routes"][0]["summary"]

        # Local geometry never needs the network.
        d = terra.distance({"lat": 12.97, "lon": 77.59},
                           {"lat": 13.08, "lon": 80.27})
        assert d["freshness"] == "live"
        assert 288 < d["km"] < 292
    finally:
        monkeypatch.delenv("TERRA_OFFLINE", raising=False)
        reload_settings()


# ---------------------------------------------------------------------------
# Spatial memory and geofencing
# ---------------------------------------------------------------------------
@pytest.fixture()
def ws():
    from omnix.core import workspace as workspace_mod
    return workspace_mod.resolve(None)


def test_saved_place_round_trip_and_name_matching(ws):
    from omnix.terra.geo.core import memory
    memory.save_place(ws, "Test College", Coord(12.9784, 77.6408), kind="study")
    try:
        assert memory.match_place(ws, "take me to test college") is not None
        assert memory.match_place(ws, "TEST COLLEGE") is not None
        assert memory.match_place(ws, "somewhere else entirely") is None
    finally:
        for p in memory.places(ws):
            if p["slug"] == "test college":
                memory.delete_place(ws, p["id"])


def test_name_matching_uses_words_not_substrings(ws):
    """"work" must not match "network" — substring matching did exactly that."""
    from omnix.terra.geo.core import memory
    memory.save_place(ws, "Work", Coord(12.97, 77.59))
    try:
        assert memory.match_place(ws, "go to work") is not None
        assert memory.match_place(ws, "show me the network diagram") is None
    finally:
        for p in memory.places(ws):
            if p["slug"] == "work":
                memory.delete_place(ws, p["id"])


def test_privacy_mode_blocks_history_but_not_saved_places(ws, monkeypatch):
    from omnix.terra.geo.core import memory
    monkeypatch.setenv("TERRA_PRIVACY_MODE", "1")
    reload_settings()
    try:
        out = memory.observe(ws, Coord(12.97, 77.59))
        assert out["recorded"] is False
        assert "privacy" in out["reason"]
        # Saved places still work — "take me to college" must not break because
        # tracking is off.
        assert memory.save_place(ws, "Privacy Test", Coord(12.97, 77.59))
    finally:
        for p in memory.places(ws):
            if p["slug"] == "privacy test":
                memory.delete_place(ws, p["id"])
        monkeypatch.delenv("TERRA_PRIVACY_MODE", raising=False)
        reload_settings()


def test_geofence_fires_on_transition_only(ws):
    from omnix.terra.geo.core import geofencing
    fence = geofencing.create(ws, "Test Fence", coord=Coord(12.9784, 77.6408),
                              radius_m=200)
    assert fence is not None
    try:
        assert geofencing.evaluate(ws, Coord(12.9716, 77.5946)) == []
        entering = geofencing.evaluate(ws, Coord(12.9784, 77.6408))
        assert [e["transition"] for e in entering] == ["enter"]
        # Still inside: no second event. This is what stops an arrival alert
        # re-firing every few seconds for the rest of the day.
        assert geofencing.evaluate(ws, Coord(12.97841, 77.64081)) == []
        leaving = geofencing.evaluate(ws, Coord(12.9716, 77.5946))
        assert [e["transition"] for e in leaving] == ["exit"]
    finally:
        geofencing.delete(ws, fence["id"])


def test_geofence_radius_has_a_floor(ws):
    """Below consumer GPS error a fence flaps between states while the user
    stands still."""
    from omnix.terra.geo.core import geofencing
    fence = geofencing.create(ws, "Tiny", coord=Coord(12.97, 77.59), radius_m=5)
    try:
        assert fence["radiusM"] >= 50
    finally:
        geofencing.delete(ws, fence["id"])


def test_route_crossings_detect_a_fence_between_vertices(ws):
    from omnix.terra.geo.core import geofencing
    fence = geofencing.create(ws, "Midway", coord=Coord(12.975, 77.60),
                              radius_m=400)
    try:
        crossings = geofencing.route_crossings(
            ws, [Coord(12.90, 77.50), Coord(13.05, 77.70)])
        assert any(c["label"] == "Midway" for c in crossings)
    finally:
        geofencing.delete(ws, fence["id"])
