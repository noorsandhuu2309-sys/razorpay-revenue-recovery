"""USGS adapter — parsing, and every way the provider can let us down.

§74 requires mocked responses rather than live-API tests, for a reason worth
stating: a suite that only passes when USGS is up tests the internet, and it
goes red on a morning when nothing in this repository changed. Every test here
runs offline.

The parsing assertions target the three traps in the real payload, all of which
produce a *plausible wrong answer* rather than an error:

  * `time` is epoch MILLISECONDS — read as seconds, every quake dates to 1970
  * `mag` is sometimes null — coerced to 0.0, that is a magnitude-0 earthquake
  * `place` is sometimes null — coerced to "", that is an event nowhere
"""

from __future__ import annotations

import json

import httpx
import pytest

from omnix.plugins.usgs_earthquake.adapters.usgs import (
    UsgsAdapter, UsgsError, parse_collection, parse_feature)


def _feature(**props) -> dict:
    base = {"mag": 5.2, "place": "100 km W of Somewhere", "magType": "mb",
            "time": 1787088988032, "tsunami": 0, "felt": None, "alert": "",
            "url": "https://earthquake.usgs.gov/x"}
    base.update(props)
    return {"type": "Feature", "id": "us1234",
            "properties": base,
            "geometry": {"type": "Point", "coordinates": [95.6, 37.8, 6.0]}}


def _collection(*features) -> dict:
    return {"type": "FeatureCollection", "metadata": {}, "features": list(features)}


def _adapter(handler) -> UsgsAdapter:
    transport = httpx.MockTransport(handler)
    return UsgsAdapter(timeout_s=5, client=httpx.Client(transport=transport))


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------
def test_time_is_converted_from_milliseconds():
    """Read as seconds this is 1970-01-21. The bug looks like corrupt data
    rather than a unit error, which is what makes it hard to spot."""
    q = parse_feature(_feature(time=1787088988032))
    assert 1_780_000_000 < q.time < 1_800_000_000


def test_a_null_magnitude_stays_none_and_does_not_become_zero():
    assert parse_feature(_feature(mag=None)).magnitude is None


def test_a_null_place_stays_empty_rather_than_inventing_a_location():
    assert parse_feature(_feature(place=None)).place == ""


def test_coordinates_are_read_in_geojson_order():
    """GeoJSON is [lon, lat]; swapping them silently relocates every event."""
    q = parse_feature(_feature())
    assert (round(q.longitude, 1), round(q.latitude, 1)) == (95.6, 37.8)
    assert q.depth_km == 6.0


def test_a_feature_without_an_id_is_dropped():
    """An event that cannot be cited or deduped is not evidence."""
    bad = _feature()
    del bad["id"]
    assert parse_feature(bad) is None


def test_missing_geometry_does_not_raise():
    bad = _feature()
    bad["geometry"] = None
    q = parse_feature(bad)
    assert q is not None and q.latitude is None


def test_a_response_that_is_not_a_feature_collection_is_rejected():
    """§82 — the adapter must report a changed provider shape, not guess."""
    with pytest.raises(UsgsError, match="FeatureCollection"):
        parse_collection({"type": "Something", "features": []})


def test_a_collection_with_no_features_list_is_rejected():
    with pytest.raises(UsgsError, match="features"):
        parse_collection({"type": "FeatureCollection"})


def test_an_empty_collection_parses_to_an_empty_list():
    """Genuinely no earthquakes is a real answer, and different from an error."""
    assert parse_collection(_collection()) == []


# ---------------------------------------------------------------------------
# Transport failures — §74
# ---------------------------------------------------------------------------
def test_a_timeout_is_reported_as_a_timeout():
    def handler(request):
        raise httpx.TimeoutException("too slow", request=request)
    with pytest.raises(UsgsError, match="did not respond"):
        _adapter(handler).search()


def test_a_network_failure_is_reported():
    def handler(request):
        raise httpx.ConnectError("no route to host", request=request)
    with pytest.raises(UsgsError, match="could not reach"):
        _adapter(handler).search()


def test_rate_limiting_is_named_explicitly():
    with pytest.raises(UsgsError, match="rate-limited"):
        _adapter(lambda r: httpx.Response(429)).search()


def test_a_server_error_is_distinguished_from_a_bad_query():
    with pytest.raises(UsgsError, match="service error"):
        _adapter(lambda r: httpx.Response(503)).search()


def test_a_rejected_query_surfaces_the_providers_own_explanation():
    """FDSN answers 400 with plain text saying which parameter was wrong;
    swallowing it leaves the operator with nothing to fix."""
    handler = lambda r: httpx.Response(400, text="Bad request: minmagnitude")
    with pytest.raises(UsgsError, match="minmagnitude"):
        _adapter(handler).search()


def test_malformed_json_is_reported_as_such():
    handler = lambda r: httpx.Response(200, text="<html>maintenance</html>")
    with pytest.raises(UsgsError, match="not JSON"):
        _adapter(handler).search()


# ---------------------------------------------------------------------------
# Query construction
# ---------------------------------------------------------------------------
def test_the_limit_is_clamped_to_what_the_service_accepts():
    seen = {}

    def handler(request):
        seen.update(dict(request.url.params))
        return httpx.Response(200, json=_collection())

    _adapter(handler).search(limit=99999)
    assert int(seen["limit"]) <= 500


def test_a_radius_search_sends_kilometres_not_degrees():
    """maxradius is degrees; maxradiuskm is kilometres. Confusing them is a
    111x error that returns a plausible, entirely wrong, set of events."""
    seen = {}

    def handler(request):
        seen.update(dict(request.url.params))
        return httpx.Response(200, json=_collection())

    _adapter(handler).search(latitude=12.9, longitude=77.6, radius_km=300)
    assert "maxradiuskm" in seen and float(seen["maxradiuskm"]) == 300.0
    assert "maxradius" not in seen


def test_health_check_reports_failure_without_raising():
    ok, detail = _adapter(lambda r: httpx.Response(503)).health_check()
    assert ok is False and detail


def test_health_check_reports_success():
    handler = lambda r: httpx.Response(200, json=_collection(_feature()))
    ok, detail = _adapter(handler).health_check()
    assert ok is True and detail == ""


# ---------------------------------------------------------------------------
# The plugin on top of the adapter
# ---------------------------------------------------------------------------
def _plugin(handler):
    """The real plugin with a mocked transport, and no live call anywhere.

    Deliberately not `enable()`: that probes the provider, so every test here
    would open a socket to USGS and the suite would go red whenever the service
    or the network was having a bad morning. `setup()` builds the adapter and
    cache; the adapter is then swapped for the mock before anything runs.
    """
    from pathlib import Path

    from omnix.core.plugin_system import Status
    from omnix.core.plugin_system.manifest import load as load_manifest
    from omnix.plugins.usgs_earthquake.plugin import UsgsEarthquakePlugin

    root = Path(__file__).resolve().parents[1] / "omnix" / "plugins" / "usgs_earthquake"
    p = UsgsEarthquakePlugin(load_manifest(root / "plugin.json"), root)
    p.setup()
    p._setup_done = True
    p.adapter = _adapter(handler)
    p.health.status = Status.OK
    return p


def test_a_provider_outage_is_unavailable_never_an_empty_world():
    """The property the whole plugin system exists to guarantee."""
    p = _plugin(lambda r: httpx.Response(503))
    out = p.call("recent_earthquakes")
    assert out.available is False
    assert out.data is None
    assert "unavailable" in out.error.reason.lower()


def test_a_successful_call_carries_attribution():
    """§79 — USGS is public domain but still asks to be credited."""
    p = _plugin(lambda r: httpx.Response(200, json=_collection(_feature())))
    out = p.call("recent_earthquakes")
    assert out.available
    assert "U.S. Geological Survey" in out.attribution
    assert out.source == "usgs_fdsn"


def test_the_summary_separates_measured_from_unmeasured_events():
    """A total that quietly counts unrated events beside rated ones is a
    number nobody can check."""
    p = _plugin(lambda r: httpx.Response(200, json=_collection(
        _feature(mag=6.4), _feature(mag=4.2), _feature(mag=None))))
    out = p.call("earthquake_summary", hours=24)
    assert out.data["total"] == 3
    assert out.data["rated"] == 2
    assert out.data["unrated"] == 1
    assert out.data["largest"]["magnitude"] == 6.4
    assert out.data["buckets"]["m6_plus"] == 1


def test_the_summary_passes_an_outage_through_rather_than_reporting_zero():
    p = _plugin(lambda r: httpx.Response(503))
    out = p.call("earthquake_summary")
    assert out.available is False


def test_repeated_identical_calls_hit_the_cache():
    """Three dashboard tiles must not make three requests to a free service."""
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(200, json=_collection(_feature()))

    p = _plugin(handler)
    p.call("recent_earthquakes", hours=6)
    p.call("recent_earthquakes", hours=6)
    second = p.call("recent_earthquakes", hours=6)
    assert calls["n"] == 1
    assert second.cached is True
