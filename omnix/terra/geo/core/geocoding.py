"""Address <-> coordinates, and the memory that stops us asking twice.

The cheapest API call is the one not made, and geocoding is where that pays off
most: a user's questions are dominated by a handful of places they name over
and over. So the resolution order is

    1. saved places   — "college" is in the spatial memory, cost 0, offline-safe
    2. cache          — asked before, still inside a 30-day TTL, cost 0
    3. providers      — Nominatim, then Open-Meteo, then Google

Step 1 is the one the brief calls out by name, and it is what makes "take me to
college" a database lookup rather than a geocode. It runs before the cache
because a saved place is not merely cached, it is *authoritative*: the user
told us where their college is, and a geocoder's opinion does not override it.
"""

from __future__ import annotations

from ..providers import registry
from ..types import Coord, Freshness, Place, Result


def geocode(query: str, *, near: Coord | None = None, limit: int = 5,
            workspace_id: str | None = None,
            use_memory: bool = True) -> Result:
    """Resolve text to candidate places, best first."""
    q = (query or "").strip()
    if not q:
        return Result.offline("empty query", data=[])

    if use_memory and workspace_id:
        from . import memory
        saved = memory.match_place(workspace_id, q)
        if saved is not None:
            # ESTIMATED, not LIVE: this is the user's own record, not a live
            # lookup. Labelling it live would claim a currency it does not
            # have, and labelling it cached would imply it expires.
            return Result(data=[saved], freshness=Freshness.ESTIMATED,
                          provider="memory", attempted=["memory:hit"])

    return registry.first_ok(
        registry.geocode_chain(), "geocode",
        lambda p: p.geocode(q, limit=limit, near=near),
        {"q": q.lower(), "limit": limit,
         # Bias is part of the question: "MG Road" near Bengaluru and "MG Road"
         # near Delhi are different answers and must not share a cache entry.
         "near": str(near.rounded(2)) if near else ""},
        empty_is_miss=True,
    )


def reverse(coord: Coord, *, workspace_id: str | None = None,
            use_memory: bool = True) -> Result:
    """Resolve coordinates to a place.

    Checks saved places first within 150m — arriving at your own front door
    should say "Home", not "42 Some Street", and it should say it offline.
    """
    if use_memory and workspace_id:
        from . import memory
        saved = memory.nearest_saved(workspace_id, coord, radius_m=150.0)
        if saved is not None:
            return Result(data=saved, freshness=Freshness.ESTIMATED,
                          provider="memory", attempted=["memory:hit"])

    return registry.first_ok(
        registry.reverse_chain(), "reverse",
        lambda p: p.reverse(coord),
        # 4dp (~11m) rather than the full precision: two clicks in the same
        # room are the same question, and paying twice for them is waste.
        {"lat": round(coord.lat, 4), "lon": round(coord.lon, 4)},
        empty_is_miss=True,
    )


def resolve(text: str, *, near: Coord | None = None,
            workspace_id: str | None = None) -> tuple[Coord | None, str, Result]:
    """Best-effort "turn whatever the user typed into one point".

    Returns (coord, label, result) so a caller can act on the coordinate and
    still report where it came from. This is what the routing and places tools
    call, and it accepts the three things a user actually types: a saved place
    name, a raw "lat,lon" pair, or an address.
    """
    t = (text or "").strip()
    if not t:
        return (None, "", Result.offline("empty location"))

    # A literal coordinate pair. Worth handling before anything else because
    # it is exact, free, and the format the map itself produces when the user
    # clicks — round-tripping that through a geocoder would be absurd.
    pair = _parse_coord(t)
    if pair is not None:
        return (pair, str(pair), Result(data=pair, freshness=Freshness.LIVE,
                                        provider="literal"))

    result = geocode(t, near=near, limit=1, workspace_id=workspace_id)
    places = result.data if isinstance(result.data, list) else []
    if not places:
        return (None, t, result)
    place: Place = places[0]
    return (place.coord, place.name or t, result)


def _parse_coord(text: str) -> Coord | None:
    """"12.97, 77.59" -> Coord. Anything else -> None.

    Deliberately strict: exactly two comma-separated numbers and nothing more,
    so a place genuinely called "42, 7" cannot be silently read as a
    coordinate. Out-of-range values fall through to the geocoder rather than
    raising, since `Coord` validates.
    """
    parts = [p.strip() for p in text.split(",")]
    if len(parts) != 2:
        return None
    try:
        lat, lon = float(parts[0]), float(parts[1])
    except ValueError:
        return None
    try:
        return Coord(lat, lon)
    except ValueError:
        return None


__all__ = ["geocode", "reverse", "resolve"]
