"""Local geometry. Everything here runs with the network down.

This module exists for a cost reason as much as a correctness one. A great many
"geospatial" questions do not need a provider at all — how far apart are these
two points, is this point inside that circle, does this route cross that fence,
what is the bounding box to search in. Answering them locally is free,
instantaneous, and works offline, and every one of them answered here is an API
call not made.

The projections are the standard cheap ones and their limits are stated where
they bite: haversine assumes a sphere (~0.5% error at worst, irrelevant at the
scales TERRA works at), and the bbox helpers assume small spans and break at
the poles and the antimeridian — both guarded.
"""

from __future__ import annotations

import math

from .types import Coord

EARTH_RADIUS_M = 6_371_008.8


# ---------------------------------------------------------------------------
# Distance and bearing
# ---------------------------------------------------------------------------
def haversine_m(a: Coord, b: Coord) -> float:
    """Great-circle distance in metres."""
    lat1, lat2 = math.radians(a.lat), math.radians(b.lat)
    dlat = lat2 - lat1
    dlon = math.radians(b.lon - a.lon)
    h = (math.sin(dlat / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2)
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(h)))


def bearing_deg(a: Coord, b: Coord) -> float:
    """Initial compass bearing from a to b, 0-360 clockwise from north."""
    lat1, lat2 = math.radians(a.lat), math.radians(b.lat)
    dlon = math.radians(b.lon - a.lon)
    y = math.sin(dlon) * math.cos(lat2)
    x = (math.cos(lat1) * math.sin(lat2)
         - math.sin(lat1) * math.cos(lat2) * math.cos(dlon))
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


_COMPASS = ("N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
            "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW")


def compass(bearing: float) -> str:
    """A bearing as a word. `bearing_deg` is the number; this is the thing a
    human or an LLM should actually be handed."""
    return _COMPASS[int((bearing % 360.0) / 22.5 + 0.5) % 16]


def destination(origin: Coord, bearing: float, distance_m: float) -> Coord:
    """The point `distance_m` from `origin` along `bearing`. Used to build
    search boxes and to place synthetic markers."""
    d = distance_m / EARTH_RADIUS_M
    br = math.radians(bearing)
    lat1, lon1 = math.radians(origin.lat), math.radians(origin.lon)
    lat2 = math.asin(math.sin(lat1) * math.cos(d)
                     + math.cos(lat1) * math.sin(d) * math.cos(br))
    lon2 = lon1 + math.atan2(math.sin(br) * math.sin(d) * math.cos(lat1),
                             math.cos(d) - math.sin(lat1) * math.sin(lat2))
    lat = math.degrees(lat2)
    lon = (math.degrees(lon2) + 540.0) % 360.0 - 180.0
    return Coord(max(-90.0, min(90.0, lat)), lon)


# ---------------------------------------------------------------------------
# Bounding boxes
# ---------------------------------------------------------------------------
def bbox_around(centre: Coord, radius_m: float) -> tuple[float, float, float, float]:
    """(south, west, north, east) covering a circle of `radius_m`.

    This is the SQL pre-filter for every nearby() query: an indexed
    `lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?` throws away 99% of rows
    before any trigonometry runs. The box over-selects at the corners, which is
    why callers must still refine with `haversine_m` — a bbox is a filter, not
    an answer.

    Degenerates deliberately near the poles, where a metre of longitude spans
    an unbounded number of degrees: the longitude span is clamped to the whole
    world rather than producing a nonsense box.
    """
    dlat = math.degrees(radius_m / EARTH_RADIUS_M)
    coslat = math.cos(math.radians(centre.lat))
    if abs(coslat) < 1e-6:
        dlon = 180.0
    else:
        dlon = min(180.0, math.degrees(radius_m / (EARTH_RADIUS_M * coslat)))
    return (max(-90.0, centre.lat - dlat), max(-180.0, centre.lon - dlon),
            min(90.0, centre.lat + dlat), min(180.0, centre.lon + dlon))


def bbox_of(points: list[Coord]) -> tuple[float, float, float, float]:
    """(south, west, north, east) enclosing every point. Empty list -> world."""
    if not points:
        return (-90.0, -180.0, 90.0, 180.0)
    lats = [p.lat for p in points]
    lons = [p.lon for p in points]
    return (min(lats), min(lons), max(lats), max(lons))


# ---------------------------------------------------------------------------
# Containment
# ---------------------------------------------------------------------------
def inside_circle(point: Coord, centre: Coord, radius_m: float) -> bool:
    return haversine_m(point, centre) <= radius_m


def inside_polygon(point: Coord, polygon: list[Coord]) -> bool:
    """Ray-casting point-in-polygon, in degrees.

    Working in raw lat/lon rather than a projection is correct here for the
    same reason it is wrong for area: the crossing test only cares about
    topology, and any monotonic transform of the coordinates preserves it. The
    real constraint is the antimeridian — a polygon spanning ±180 is scored
    wrong — so callers with such a polygon must split it first. TERRA's fences
    are neighbourhood-sized, so this has never come up.
    """
    n = len(polygon)
    if n < 3:
        return False
    inside = False
    j = n - 1
    for i in range(n):
        yi, xi = polygon[i].lat, polygon[i].lon
        yj, xj = polygon[j].lat, polygon[j].lon
        if (yi > point.lat) != (yj > point.lat):
            x_cross = (xj - xi) * (point.lat - yi) / (yj - yi) + xi
            if point.lon < x_cross:
                inside = not inside
        j = i
    return inside


# ---------------------------------------------------------------------------
# Routes against geometry
# ---------------------------------------------------------------------------
def point_to_segment_m(p: Coord, a: Coord, b: Coord) -> float:
    """Shortest distance from p to the segment ab.

    Projects onto a local tangent plane, scaling longitude by cos(lat) so a
    degree of longitude is worth what it is actually worth at this latitude.
    Over a single route segment — tens of metres to a few km — the flat-earth
    error is far below the precision anyone cares about, and it avoids an
    iterative great-circle solve per segment on a route with 2000 of them.
    """
    lat0 = math.radians((a.lat + b.lat) / 2.0)
    kx = math.cos(lat0) * (math.pi / 180.0) * EARTH_RADIUS_M
    ky = (math.pi / 180.0) * EARTH_RADIUS_M
    px, py = p.lon * kx, p.lat * ky
    ax, ay = a.lon * kx, a.lat * ky
    bx, by = b.lon * kx, b.lat * ky
    dx, dy = bx - ax, by - ay
    if dx == 0.0 and dy == 0.0:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def route_length_m(geometry: list[Coord]) -> float:
    """Summed great-circle length of a polyline. Used to sanity-check what a
    provider claims and to measure a user-drawn line."""
    return sum(haversine_m(geometry[i], geometry[i + 1])
               for i in range(len(geometry) - 1))


def route_intersects_circle(geometry: list[Coord], centre: Coord,
                            radius_m: float) -> bool:
    """Does the route pass within `radius_m` of `centre`?

    Testing the VERTICES alone is the obvious implementation and it is wrong:
    a motorway is encoded as a handful of points kilometres apart, so a route
    straight through the middle of a 200m fence has no vertex inside it and
    reads as a miss. Segments, not points.
    """
    if not geometry:
        return False
    if len(geometry) == 1:
        return haversine_m(geometry[0], centre) <= radius_m
    for i in range(len(geometry) - 1):
        if point_to_segment_m(centre, geometry[i], geometry[i + 1]) <= radius_m:
            return True
    return False


def route_intersects_polygon(geometry: list[Coord], polygon: list[Coord]) -> bool:
    """Whether any vertex of the route falls inside the polygon.

    Honest about its limit: a route that clips a corner between two distant
    vertices is missed. Densify the geometry before calling if that matters.
    For TERRA's fences — arrival and departure areas around buildings — the
    route always has vertices inside.
    """
    return any(inside_polygon(p, polygon) for p in geometry)


def nearest_point_on_route(geometry: list[Coord], p: Coord) -> tuple[int, float]:
    """(index of closest segment start, distance in metres). Used to answer
    "how far off my route is this?" without another API call."""
    if not geometry:
        return (-1, float("inf"))
    if len(geometry) == 1:
        return (0, haversine_m(geometry[0], p))
    best_i, best_d = 0, float("inf")
    for i in range(len(geometry) - 1):
        d = point_to_segment_m(p, geometry[i], geometry[i + 1])
        if d < best_d:
            best_i, best_d = i, d
    return (best_i, best_d)


# ---------------------------------------------------------------------------
# Polyline codecs
# ---------------------------------------------------------------------------
def decode_polyline(encoded: str, precision: int = 5) -> list[Coord]:
    """Google's encoded-polyline format, which OSRM also speaks.

    `precision` is the trap: Google and OSRM's default use 5 decimal places,
    OSRM's `geometries=polyline6` uses 6, and decoding one as the other puts
    the route in the wrong hemisphere rather than merely off by a bit. The
    caller passes what it asked the provider for.

    Never raises — a truncated polyline yields the points decoded so far.
    """
    coords: list[Coord] = []
    if not encoded:
        return coords
    factor = float(10 ** precision)
    index = lat = lon = 0
    length = len(encoded)
    try:
        while index < length:
            for is_lat in (True, False):
                shift = result = 0
                while index < length:
                    b = ord(encoded[index]) - 63
                    index += 1
                    result |= (b & 0x1F) << shift
                    shift += 5
                    if b < 0x20:
                        break
                delta = ~(result >> 1) if (result & 1) else (result >> 1)
                if is_lat:
                    lat += delta
                else:
                    lon += delta
            coords.append(Coord(lat / factor, lon / factor))
    except (ValueError, IndexError):
        pass
    return coords


def simplify(geometry: list[Coord], tolerance_m: float = 8.0) -> list[Coord]:
    """Ramer-Douglas-Peucker, iterative.

    A cross-country route from OSRM is tens of thousands of points; sending
    that to the browser makes the map stutter and the JSON enormous, while at
    any zoom the user can actually see, 8m of detail is invisible. Iterative
    rather than recursive because Python's recursion limit is ~1000 and a long
    route genuinely reaches it.
    """
    n = len(geometry)
    if n < 3:
        return list(geometry)
    keep = [False] * n
    keep[0] = keep[n - 1] = True
    stack = [(0, n - 1)]
    while stack:
        start, end = stack.pop()
        if end <= start + 1:
            continue
        a, b = geometry[start], geometry[end]
        far_i, far_d = -1, 0.0
        for i in range(start + 1, end):
            d = point_to_segment_m(geometry[i], a, b)
            if d > far_d:
                far_i, far_d = i, d
        if far_d > tolerance_m and far_i > 0:
            keep[far_i] = True
            stack.append((start, far_i))
            stack.append((far_i, end))
    return [c for c, k in zip(geometry, keep) if k]


# ---------------------------------------------------------------------------
# Presentation
# ---------------------------------------------------------------------------
def human_distance(metres: float) -> str:
    if metres < 1000:
        return f"{int(round(metres / 10.0) * 10)} m"
    if metres < 10_000:
        return f"{metres / 1000.0:.1f} km"
    return f"{int(round(metres / 1000.0))} km"


def human_duration(seconds: float) -> str:
    mins = int(round(seconds / 60.0))
    if mins < 1:
        return "under a minute"
    if mins < 60:
        return f"{mins} min"
    hours, rem = divmod(mins, 60)
    return f"{hours} h" if rem == 0 else f"{hours} h {rem} min"
