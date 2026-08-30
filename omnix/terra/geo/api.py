"""`terra.*` — the internal API the rest of OMNIX calls.

This is the only surface anything outside this package should touch. Views,
routes, agents and tools all come through here, which is what makes the layers
underneath replaceable: `core/` can be refactored and providers swapped without
a single call site changing, because nobody outside imports them.

The shape follows the brief:

    terra.get_location()          terra.get_route(...)
    terra.geocode(...)            terra.get_weather(...)
    terra.reverse_geocode(...)    terra.get_air_quality(...)
    terra.search_places(...)      terra.get_elevation(...)
    terra.nearby(...)             terra.create_geofence(...)
    terra.get_spatial_context()   terra.get_environmental_context()

Everything returns plain JSON-ready dicts, never dataclasses and never
exceptions. Two consumers depend on that: FastAPI, which would otherwise need
encoders, and the tool layer, which hands results to a model.
"""

from __future__ import annotations

from typing import Any

from . import cache, spatial
from .config import settings
from .core import environment, geocoding, geofencing, memory, places as places_svc
from .core import routing, scoring
from .intelligence import context as context_mod
from .providers import registry
from .types import Coord, Mode


def _coord(lat: float | None, lon: float | None) -> Coord | None:
    """Build a Coord, or None if the pair is missing or out of range.

    Returning None rather than raising is what lets every entry point below
    treat "no usable position" as an ordinary branch — a validation error from
    deep inside a tool call is far harder for a caller to recover from.
    """
    if lat is None or lon is None:
        return None
    try:
        return Coord(float(lat), float(lon))
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Location
# ---------------------------------------------------------------------------
def get_location(workspace_id: str | None = None) -> dict[str, Any]:
    """The last known position, from spatial memory.

    TERRA never sources a position itself. The browser holds the geolocation
    permission and pushes fixes in via `observe`; a server that went looking
    for the user's location on its own would be doing something the user did
    not grant. With no history — or in privacy mode — this correctly returns
    `known: false`, and the UI asks for a fix.
    """
    if not workspace_id:
        return {"known": False, "reason": "no workspace"}
    recent = memory.history(workspace_id, limit=1)
    if not recent:
        return {"known": False,
                "reason": ("privacy mode is on" if settings().privacy_mode
                           else "no position recorded yet")}
    fix = recent[0]
    return {"known": True, "lat": fix["lat"], "lon": fix["lon"],
            "label": fix["label"], "accuracyM": fix["accuracyM"],
            "at": fix["arrivedAt"], "source": fix["source"],
            "dwellS": fix["dwellS"]}


def observe_location(workspace_id: str, lat: float, lon: float, *,
                     accuracy_m: float | None = None,
                     label: str = "", source: str = "browser") -> dict[str, Any]:
    """Record a position and evaluate geofences against it in one step.

    Coupled deliberately. Two separate calls means a client can record a
    position and forget to check fences, and a fence that only fires when the
    client remembers is not a fence.
    """
    coord = _coord(lat, lon)
    if coord is None:
        return {"recorded": False, "reason": "invalid coordinates"}
    recorded = memory.observe(workspace_id, coord, accuracy_m=accuracy_m,
                              label=label, source=source)
    # Fences are evaluated even when history is off. They are a feature the
    # user switched ON explicitly, and disabling history should not silently
    # disable the arrival reminder they set up.
    fired = geofencing.evaluate(workspace_id, coord, accuracy_m)
    return {**recorded, "geofenceEvents": fired}


# ---------------------------------------------------------------------------
# Geocoding
# ---------------------------------------------------------------------------
def geocode(query: str, *, lat: float | None = None, lon: float | None = None,
            limit: int = 5, workspace_id: str | None = None) -> dict[str, Any]:
    result = geocoding.geocode(query, near=_coord(lat, lon), limit=limit,
                               workspace_id=workspace_id)
    return result.as_dict("results")


def reverse_geocode(lat: float, lon: float, *,
                    workspace_id: str | None = None) -> dict[str, Any]:
    coord = _coord(lat, lon)
    if coord is None:
        return {"place": None, "freshness": "offline",
                "error": "invalid coordinates"}
    return geocoding.reverse(coord, workspace_id=workspace_id).as_dict("place")


# ---------------------------------------------------------------------------
# Places
# ---------------------------------------------------------------------------
def search_places(query: str = "", *, lat: float, lon: float,
                  category: str = "", radius_m: float = 2000,
                  limit: int = 20, open_now: bool | None = None,
                  require_ratings: bool = False) -> dict[str, Any]:
    coord = _coord(lat, lon)
    if coord is None:
        return {"places": [], "freshness": "offline",
                "error": "invalid coordinates"}
    result = places_svc.search(near=coord, query=query, category=category,
                               radius_m=radius_m, limit=limit,
                               open_now=open_now,
                               require_ratings=require_ratings)
    return result.as_dict("places")


def nearby(lat: float, lon: float, category: str = "",
           radius_m: float = 2000, limit: int = 20) -> dict[str, Any]:
    return search_places("", lat=lat, lon=lon, category=category,
                         radius_m=radius_m, limit=limit)


def nearest_poi(lat: float, lon: float, category: str,
                radius_m: float = 5000) -> dict[str, Any]:
    coord = _coord(lat, lon)
    if coord is None:
        return {"places": [], "freshness": "offline",
                "error": "invalid coordinates"}
    return places_svc.nearest(near=coord, category=category,
                              radius_m=radius_m).as_dict("places")


def find_quiet_place(lat: float, lon: float, *, radius_m: float = 5000,
                     hours: float = 2.0, limit: int = 8) -> dict[str, Any]:
    """Structured candidates for "somewhere quiet to work". Not a verdict."""
    coord = _coord(lat, lon)
    if coord is None:
        return {"places": [], "freshness": "offline",
                "error": "invalid coordinates"}
    result = places_svc.quiet_workspace(near=coord, radius_m=radius_m,
                                        hours=hours, limit=limit)
    payload = result.as_dict("places")
    payload["criteria"] = {
        "categories": list(places_svc.QUIET_WORK_CATEGORIES),
        "hoursNeeded": hours,
        "radiusM": radius_m,
        # Stated so the caller can qualify a recommendation honestly rather
        # than implying TERRA measured the noise.
        "note": ("Ambience is inferred from category and tags — OSM records no "
                 "noise data. Places with unknown opening hours are included."),
    }
    return payload


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------
def get_route(origin: dict | str, destination: dict | str, *,
              mode: str = "driving", alternatives: int = 3,
              workspace_id: str | None = None,
              avoid_weather: bool = False,
              prefer: str = "score") -> dict[str, Any]:
    """A route between two places, each given as coordinates or as text.

    Accepting text is what makes "route home from college" one call: both ends
    resolve through spatial memory first, so a route between two saved places
    costs zero geocoding requests.
    """
    travel = _mode(mode)
    o_coord, o_label = _resolve_endpoint(origin, workspace_id)
    d_coord, d_label = _resolve_endpoint(destination, workspace_id)
    if o_coord is None or d_coord is None:
        missing = "origin" if o_coord is None else "destination"
        return {"routes": [], "freshness": "offline",
                "error": f"could not resolve {missing}"}

    penalty = 0.0
    if avoid_weather and travel is not Mode.DRIVING:
        w = environment.weather(o_coord)
        penalty = environment.exposure_penalty(w.data if w.ok else None,
                                               travel.value)

    result = routing.route(o_coord, d_coord, mode=travel,
                           alternatives=alternatives,
                           workspace_id=workspace_id,
                           weather_penalty=penalty, prefer=prefer)
    payload = result.as_dict("routes")
    payload["origin"] = {"lat": o_coord.lat, "lon": o_coord.lon,
                         "label": o_label}
    payload["destination"] = {"lat": d_coord.lat, "lon": d_coord.lon,
                              "label": d_label}
    payload["mode"] = travel.value
    if result.ok and result.data:
        payload["explanations"] = [scoring.explain(r) for r in result.data]
        if workspace_id:
            payload["crossings"] = geofencing.route_crossings(
                workspace_id, result.data[0].geometry)
    return payload


def choose_route(workspace_id: str, origin: dict, destination: dict,
                 routes_payload: list[dict], chosen_index: int, *,
                 mode: str = "driving", origin_label: str = "",
                 dest_label: str = "") -> dict[str, Any]:
    """Record which alternative the user took, and learn from it.

    Takes the serialised routes back from the client rather than re-fetching:
    the user chose from what they were shown, and re-routing could return a
    different set of alternatives, which would attribute their choice to a
    route they never saw.
    """
    o = _coord(origin.get("lat"), origin.get("lon"))
    d = _coord(destination.get("lat"), destination.get("lon"))
    if o is None or d is None:
        return {"learned": {}, "error": "invalid coordinates"}

    from .types import Route
    rebuilt = [Route(distance_m=r.get("distanceM", 0.0),
                     duration_s=r.get("durationS", 0.0),
                     duration_traffic_s=r.get("durationTrafficS"),
                     summary=r.get("summary", ""),
                     source=r.get("source", ""),
                     tolls=r.get("tolls"),
                     score=r.get("score"),
                     score_parts=r.get("scoreParts") or {})
               for r in routes_payload]
    learned = routing.record_choice(workspace_id, o, d, rebuilt, chosen_index,
                                    origin_label=origin_label,
                                    dest_label=dest_label, mode=_mode(mode))
    return {"learned": learned, "weights": scoring.weights_for(workspace_id)}


def _resolve_endpoint(value: dict | str,
                      workspace_id: str | None) -> tuple[Coord | None, str]:
    if isinstance(value, dict):
        coord = _coord(value.get("lat"), value.get("lon"))
        return (coord, value.get("label") or (str(coord) if coord else ""))
    coord, label, _ = geocoding.resolve(str(value), workspace_id=workspace_id)
    return (coord, label)


def _mode(name: str) -> Mode:
    try:
        return Mode(str(name).lower())
    except ValueError:
        return Mode.DRIVING


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------
def get_weather(lat: float, lon: float) -> dict[str, Any]:
    coord = _coord(lat, lon)
    if coord is None:
        return {"weather": None, "freshness": "offline",
                "error": "invalid coordinates"}
    return environment.weather(coord).as_dict("weather")


def get_air_quality(lat: float, lon: float) -> dict[str, Any]:
    coord = _coord(lat, lon)
    if coord is None:
        return {"airQuality": None, "freshness": "offline",
                "error": "invalid coordinates"}
    return environment.air_quality(coord).as_dict("airQuality")


def get_hourly(lat: float, lon: float, hours: int = 24) -> dict[str, Any]:
    """The next N hours at a point — what the forecast strip renders."""
    coord = _coord(lat, lon)
    if coord is None:
        return {"forecast": None, "freshness": "offline",
                "error": "invalid coordinates"}
    return environment.hourly(coord, max(1, min(hours, 48))).as_dict("forecast")


def get_air_quality_grid(lat: float, lon: float, *, radius_m: float = 12_000,
                         steps: int = 5) -> dict[str, Any]:
    """Air quality sampled over a grid, for the overlay. One provider call."""
    coord = _coord(lat, lon)
    if coord is None:
        return {"grid": None, "freshness": "offline",
                "error": "invalid coordinates"}
    return environment.air_quality_grid(
        coord, radius_m=min(radius_m, 200_000), steps=steps).as_dict("grid")


def get_radar() -> dict[str, Any]:
    """Precipitation radar tile templates, oldest frame first."""
    return environment.radar().as_dict("radar")


def get_elevation(lat: float, lon: float) -> dict[str, Any]:
    coord = _coord(lat, lon)
    if coord is None:
        return {"elevation": None, "freshness": "offline",
                "error": "invalid coordinates"}
    result = environment.elevation([coord])
    heights = result.data if result.ok else []
    payload = result.as_dict("raw")
    payload.pop("raw", None)
    payload["elevation"] = {"metres": round(heights[0], 1)} if heights else None
    return payload


def get_elevation_profile(geometry: list[list[float]]) -> dict[str, Any]:
    coords = []
    for point in geometry or []:
        c = _coord(point[0] if len(point) > 0 else None,
                   point[1] if len(point) > 1 else None)
        if c is not None:
            coords.append(c)
    return environment.elevation_profile(coords).as_dict("profile")


def get_environmental_context(lat: float, lon: float) -> dict[str, Any]:
    """Everything about conditions at a point, with the outdoor signals.

    The signals are facts and constraints, never a verdict — see
    `environment.outdoor_signals`. "Should I go for a run" is answered by
    OMNIX, from this.
    """
    coord = _coord(lat, lon)
    if coord is None:
        return {"error": "invalid coordinates"}
    w = environment.weather(coord)
    aq = environment.air_quality(coord)
    return {
        "weather": w.data.as_dict() if w.ok and w.data else None,
        "airQuality": aq.data.as_dict() if aq.ok and aq.data else None,
        "sun": environment.sun_times(
            coord,
            utc_offset_s=(w.data.utc_offset_s if w.ok and w.data else None)),
        "signals": environment.outdoor_signals(w.data if w.ok else None,
                                               aq.data if aq.ok else None),
        "dataStatus": {"weather": context_mod._meta(w),
                       "airQuality": context_mod._meta(aq)},
    }


# ---------------------------------------------------------------------------
# Spatial memory
# ---------------------------------------------------------------------------
def save_place(workspace_id: str, label: str, lat: float, lon: float,
               **kw: Any) -> dict[str, Any] | None:
    coord = _coord(lat, lon)
    if coord is None:
        return None
    return memory.save_place(workspace_id, label, coord, **kw)


def known_locations(workspace_id: str) -> list[dict[str, Any]]:
    return memory.places(workspace_id)


def delete_place(workspace_id: str, place_id: str) -> bool:
    return memory.delete_place(workspace_id, place_id)


def location_history(workspace_id: str, limit: int = 50) -> list[dict[str, Any]]:
    return memory.history(workspace_id, limit)


def forget_history(workspace_id: str) -> int:
    return memory.forget_history(workspace_id)


def route_history(workspace_id: str, limit: int = 25) -> list[dict[str, Any]]:
    return routing.history(workspace_id, limit)


# ---------------------------------------------------------------------------
# Geofencing
# ---------------------------------------------------------------------------
def create_geofence(workspace_id: str, label: str, *,
                    lat: float | None = None, lon: float | None = None,
                    radius_m: float = 200.0,
                    polygon: list[list[float]] | None = None,
                    trigger: str = "both", action: str = "notify",
                    payload: dict | None = None) -> dict[str, Any] | None:
    return geofencing.create(workspace_id, label, coord=_coord(lat, lon),
                             radius_m=radius_m, polygon=polygon,
                             trigger=trigger, action=action, payload=payload)


def geofences(workspace_id: str) -> list[dict[str, Any]]:
    return geofencing.fences(workspace_id)


def delete_geofence(workspace_id: str, fence_id: str) -> bool:
    return geofencing.delete(workspace_id, fence_id)


def geofence_events(workspace_id: str, limit: int = 30) -> list[dict[str, Any]]:
    return geofencing.events(workspace_id, limit)


# ---------------------------------------------------------------------------
# Context
# ---------------------------------------------------------------------------
def get_spatial_context(lat: float | None = None, lon: float | None = None, *,
                        workspace_id: str | None = None,
                        **kw: Any) -> dict[str, Any]:
    """The structured geospatial context OMNIX's reasoning layer reads."""
    return context_mod.build(coord=_coord(lat, lon),
                             workspace_id=workspace_id, **kw)


def spatial_context_prompt(ctx: dict[str, Any]) -> str:
    return context_mod.as_prompt(ctx)


# ---------------------------------------------------------------------------
# Spatial queries — local, free, offline
# ---------------------------------------------------------------------------
def distance(a: dict, b: dict) -> dict[str, Any]:
    """Great-circle distance and bearing between two points. No API call."""
    ca, cb = _coord(a.get("lat"), a.get("lon")), _coord(b.get("lat"), b.get("lon"))
    if ca is None or cb is None:
        return {"error": "invalid coordinates"}
    metres = spatial.haversine_m(ca, cb)
    bearing = spatial.bearing_deg(ca, cb)
    return {"metres": round(metres, 1), "km": round(metres / 1000.0, 3),
            "human": spatial.human_distance(metres),
            "bearingDeg": round(bearing, 1), "compass": spatial.compass(bearing),
            "freshness": "live", "provider": "local"}


def inside(point: dict, polygon: list[list[float]]) -> dict[str, Any]:
    c = _coord(point.get("lat"), point.get("lon"))
    if c is None or len(polygon or []) < 3:
        return {"error": "need a point and a polygon of at least 3 vertices"}
    return {"inside": spatial.inside_polygon(c, [Coord(p[0], p[1])
                                                 for p in polygon]),
            "freshness": "live", "provider": "local"}


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------
def status() -> dict[str, Any]:
    """Configuration, provider health and usage. No credentials, ever."""
    return {
        "config": settings().describe(),
        "providers": registry.status(),
        "usage": cache.usage(),
        "capabilities": _capabilities(),
    }


def _capabilities() -> dict[str, list[str]]:
    """Which providers can currently answer each capability, in chain order.

    Rendered by the UI's data panel. It is the fastest way to see why an
    install behaves differently from another — a missing Google key shows up
    here as a shorter places chain rather than as mysteriously absent ratings.
    """
    return {
        "geocode": [p.name for p in registry.geocode_chain() if p.available()],
        "reverse": [p.name for p in registry.reverse_chain() if p.available()],
        "places": [p.name for p in registry.places_chain() if p.available()],
        "routing": [p.name for p in registry.route_chain(Mode.DRIVING)],
        "walking": [p.name for p in registry.route_chain(Mode.WALKING)],
        "cycling": [p.name for p in registry.route_chain(Mode.CYCLING)],
        "weather": [p.name for p in registry.weather_chain() if p.available()],
        "airQuality": [p.name for p in registry.air_quality_chain()
                       if p.available()],
        "elevation": [p.name for p in registry.elevation_chain()
                      if p.available()],
    }


def clear_cache(prefix: str = "") -> dict[str, Any]:
    return {"cleared": cache.invalidate(prefix)}


def preferences(workspace_id: str) -> dict[str, Any]:
    return {"weights": scoring.weights_for(workspace_id),
            "defaults": dict(scoring.DEFAULT_WEIGHTS),
            "factors": list(scoring.FACTORS)}


def set_preference(workspace_id: str, key: str, weight: float) -> bool:
    return scoring.set_weight(workspace_id, key, weight)


def reset_preferences(workspace_id: str) -> int:
    return scoring.reset(workspace_id)
