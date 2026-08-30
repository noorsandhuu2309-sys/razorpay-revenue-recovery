"""Routing: provider chain, alternatives, scoring, and a straight line when
everything is down.

The degraded path is the interesting part of this module. When no router is
reachable, TERRA does not return an error — it returns a great-circle distance
and a speed-model duration, marked `ESTIMATED`, with a summary that says so in
words. That is genuinely useful ("is this 5 minutes away or 2 hours?") and it
is impossible to mistake for a real route: there is no geometry beyond two
points, no steps, and the freshness label is on every surface that renders it.
"""

from __future__ import annotations

from .. import spatial
from ..providers import registry
from ..types import Coord, Freshness, Mode, Result, Route
from . import scoring


def route(origin: Coord, destination: Coord, *,
          mode: Mode = Mode.DRIVING, alternatives: int = 3,
          steps: bool = True, workspace_id: str | None = None,
          weather_penalty: float = 0.0,
          prefer: str = "score") -> Result:
    """Routes from origin to destination, best first.

    `prefer` picks the final ordering:
        score     the configurable scorer, weights from this workspace
        fastest   least duration, traffic-aware where the provider models it
        shortest  least distance
    """
    chain = registry.route_chain(mode)
    if not chain:
        return _road_estimate(origin, destination, mode,
                              workspace_id=workspace_id)

    # Traffic-aware answers expire fast. Splitting the TTL by whether traffic
    # is involved is the difference between a 6-hour cached road geometry
    # (correct — roads do not move) and a 6-hour cached ETA (useless).
    wants_traffic = mode is Mode.DRIVING and any(
        p.name == "google" for p in chain)
    kind = "route_traffic" if wants_traffic else "route"

    result = registry.first_ok(
        chain, kind,
        lambda p: p.route(origin, destination, mode=mode,
                          alternatives=alternatives, steps=steps),
        {"o": str(origin.rounded(4)), "d": str(destination.rounded(4)),
         "mode": mode.value, "alt": alternatives, "steps": steps},
        empty_is_miss=True,
    )

    if not result.ok or not result.data:
        return _estimate(origin, destination, mode,
                         result.error or "no route found",
                         attempted=result.attempted)

    routes: list[Route] = list(result.data)
    scoring.score(routes, workspace_id=workspace_id,
                  weather_penalty=weather_penalty)

    if prefer == "fastest":
        routes.sort(key=lambda r: r.duration_traffic_s or r.duration_s)
    elif prefer == "shortest":
        routes.sort(key=lambda r: r.distance_m)

    return Result(data=routes, freshness=result.freshness,
                  provider=result.provider, age_s=result.age_s,
                  error=result.error, attempted=result.attempted)


#: Crude average speeds including stops and junctions, not free-flow maxima.
#: The point of an estimate is an order of magnitude; an optimistic one shown
#: beside a real route would be the worst of both.
SPEEDS_KPH = {Mode.DRIVING: 35.0, Mode.WALKING: 4.5,
              Mode.CYCLING: 14.0, Mode.TRANSIT: 20.0}


def _road_estimate(origin: Coord, destination: Coord, mode: Mode,
                   workspace_id: str | None = None) -> Result:
    """A walking or cycling answer when no router serves that mode.

    This is the common case on a default install, and it is worth doing
    properly. The public OSRM demo ignores the profile in the URL — verified:
    driving, walking and cycling return byte-identical distance and duration —
    so TERRA refuses to present its car timings as a walk. But the road
    GEOMETRY it returns is still real, and a path that follows streets is
    enormously more useful than a line through buildings.

    So: take the driving geometry, keep it as the path, and recompute the
    duration from its measured length at the mode's speed. Labelled ESTIMATED,
    with a summary saying exactly what was assumed.

    What this is still wrong about, and why that is acceptable: it follows the
    road network, so it misses footpaths and pedestrian shortcuts, and it obeys
    one-way restrictions that do not apply on foot. Both make it CONSERVATIVE —
    the real walk is usually a little shorter — which is the right direction
    for an estimate to err. Configure GraphHopper or a self-hosted OSRM with
    the foot profile and this path is never taken.
    """
    driving = registry.route_chain(Mode.DRIVING)
    if driving:
        result = registry.first_ok(
            driving, "route",
            lambda p: p.route(origin, destination, mode=Mode.DRIVING,
                              alternatives=1, steps=False),
            {"o": str(origin.rounded(4)), "d": str(destination.rounded(4)),
             "mode": "driving", "alt": 1, "steps": False},
            empty_is_miss=True,
        )
        if result.ok and result.data:
            base: Route = result.data[0]
            length = spatial.route_length_m(base.geometry) or base.distance_m
            speed = SPEEDS_KPH.get(mode, 5.0)
            estimate = Route(
                distance_m=length,
                duration_s=(length / 1000.0) / speed * 3600.0,
                geometry=base.geometry,
                steps=[],
                summary=(f"Follows the road network at {speed:g} km/h — no "
                         f"{mode.value} router is configured, so footpaths and "
                         "shortcuts are not included"),
                mode=mode,
                source=f"{base.source}+estimate",
            )
            estimate.score = 0.0
            return Result(data=[estimate], freshness=Freshness.ESTIMATED,
                          provider=f"{result.provider}+estimate",
                          error=f"no {mode.value} routing provider configured",
                          attempted=result.attempted)

    return _estimate(origin, destination, mode,
                     f"no routing provider supports {mode.value}")


def _estimate(origin: Coord, destination: Coord, mode: Mode,
              reason: str, attempted: list[str] | None = None) -> Result:
    """A straight-line fallback, clearly labelled as one.

    The speeds are crude averages including stops, not free-flow maxima — the
    point is an order of magnitude, and an optimistic estimate presented
    alongside a real one from yesterday would be the worst of both.

    The 1.25 detour factor is the standard road-network circuity ratio: actual
    road distance runs roughly a quarter longer than the crow flies in typical
    street grids. Omitting it made every estimate cheerfully short.
    """
    straight = spatial.haversine_m(origin, destination)
    distance = straight * 1.25
    speed = SPEEDS_KPH.get(mode, 30.0)
    duration = (distance / 1000.0) / speed * 3600.0

    estimate = Route(
        distance_m=distance,
        duration_s=duration,
        geometry=[origin, destination],
        steps=[],
        summary=(f"Straight-line estimate — {spatial.human_distance(straight)} "
                 f"{spatial.compass(spatial.bearing_deg(origin, destination))}, "
                 "not a road route"),
        mode=mode,
        source="estimate",
    )
    estimate.score = 0.0
    return Result(data=[estimate], freshness=Freshness.ESTIMATED,
                  provider="estimate", error=reason,
                  attempted=attempted or [])


def eta(origin: Coord, destination: Coord, *,
        mode: Mode = Mode.DRIVING) -> Result:
    """Just the number, for a status line. One alternative, no steps — the
    cheapest shape of a routing request."""
    return route(origin, destination, mode=mode, alternatives=1, steps=False)


def record_choice(workspace_id: str, origin: Coord, destination: Coord,
                  routes: list[Route], chosen_index: int, *,
                  origin_label: str = "", dest_label: str = "",
                  mode: Mode = Mode.DRIVING) -> dict:
    """Log which alternative the user took, and learn from it.

    Writes to `geo_route` and feeds `scoring.learn_from_choice`. This is the
    only place preferences change from use rather than from an explicit
    setting, and it is called only when the user actively picks a route — never
    on merely viewing one, which would learn from a scroll.
    """
    if not (0 <= chosen_index < len(routes)):
        return {}
    chosen = routes[chosen_index]
    try:
        from ....core import db
        from ....core.schema import GeoRoute
        with db.session() as s:
            s.add(GeoRoute(
                workspace_id=workspace_id,
                origin_lat=origin.lat, origin_lon=origin.lon,
                dest_lat=destination.lat, dest_lon=destination.lon,
                origin_label=origin_label, dest_label=dest_label,
                mode=mode.value, provider=chosen.source,
                distance_m=chosen.distance_m, duration_s=chosen.duration_s,
                alternatives=len(routes), chosen_index=chosen_index,
                factors_json=chosen.score_parts or {},
            ))
    except Exception:
        pass
    return scoring.learn_from_choice(workspace_id, routes, chosen_index)


def history(workspace_id: str, limit: int = 25) -> list[dict]:
    try:
        from ....core import db
        from ....core.schema import GeoRoute, iso
        with db.session() as s:
            rows = (s.query(GeoRoute)
                    .filter(GeoRoute.workspace_id == workspace_id)
                    .order_by(GeoRoute.created_at.desc()).limit(limit).all())
            return [{
                "id": r.id,
                "origin": {"lat": r.origin_lat, "lon": r.origin_lon,
                           "label": r.origin_label},
                "destination": {"lat": r.dest_lat, "lon": r.dest_lon,
                                "label": r.dest_label},
                "mode": r.mode, "provider": r.provider,
                "distanceM": r.distance_m, "durationS": r.duration_s,
                "alternatives": r.alternatives, "chosenIndex": r.chosen_index,
                "createdAt": iso(r.created_at),
            } for r in rows]
    except Exception:
        return []


__all__ = ["route", "eta", "record_choice", "history"]
