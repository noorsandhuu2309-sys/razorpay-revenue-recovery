"""Weather, air quality, elevation, daylight — the environmental half of
spatial context.

Two things happen here that do not happen in the providers.

**Spatial key snapping.** Weather and air quality are looked up on a ~1km grid
(`cache.spatial_key`) rather than at the exact coordinate. Conditions do not
vary meaningfully across a kilometre, but a GPS fix varies across tens of
metres every few seconds — so without snapping, a user sitting still generates
a fresh lookup per position update forever. This single decision is the
difference between a handful of calls an hour and thousands.

**Local computation where it is exact.** Sunrise and sunset are astronomy, not
observations: they are computed from latitude and the day of the year rather
than fetched. It costs nothing, works offline, and is accurate to about a
minute — better than the round trip deserves.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone

from .. import cache, spatial
from ..providers import registry
from ..types import AirQuality, Coord, Freshness, Result, Weather


def weather(coord: Coord) -> Result:
    grid = cache.spatial_key(coord.lat, coord.lon, precision=2)
    return registry.first_ok(
        registry.weather_chain(), "weather",
        lambda p: p.weather(coord),
        {"lat": grid[0], "lon": grid[1]},
        empty_is_miss=True,
    )


def air_quality(coord: Coord) -> Result:
    grid = cache.spatial_key(coord.lat, coord.lon, precision=2)
    return registry.first_ok(
        registry.air_quality_chain(), "air_quality",
        lambda p: p.air_quality(coord),
        {"lat": grid[0], "lon": grid[1]},
        empty_is_miss=True,
    )


def hourly(coord: Coord, hours: int = 24) -> Result:
    """The next 24 hours at a point, for the forecast strip."""
    grid = cache.spatial_key(coord.lat, coord.lon, precision=2)
    return registry.first_ok(
        registry.weather_chain(), "weather",
        lambda p: p.hourly(coord, hours),
        {"lat": grid[0], "lon": grid[1], "hours": hours, "kind": "hourly"},
        empty_is_miss=True,
    )


def air_quality_grid(centre: Coord, *, radius_m: float = 12_000,
                     steps: int = 5) -> Result:
    """Air quality sampled over a grid, in one provider call.

    The overlay this feeds is only affordable because Open-Meteo answers a
    multi-coordinate request in a single response — see
    `openmeteo.air_quality_grid`. `steps` is capped at 7 (49 points) because
    beyond that the URL grows without the overlay looking meaningfully better;
    AQI fields are smooth at city scale and interpolation does the rest.

    Returns points with their coordinates attached so the client can build a
    heat layer without re-deriving the grid.
    """
    steps = max(2, min(int(steps), 7))
    south, west, north, east = spatial.bbox_around(centre, radius_m)
    coords: list[Coord] = []
    for i in range(steps):
        for j in range(steps):
            lat = south + (north - south) * i / (steps - 1)
            lon = west + (east - west) * j / (steps - 1)
            try:
                coords.append(Coord(lat, lon))
            except ValueError:
                continue

    result = registry.first_ok(
        registry.air_quality_chain(), "air_quality",
        lambda p: p.air_quality_grid(coords),
        {"lat": round(centre.lat, 2), "lon": round(centre.lon, 2),
         "r": int(radius_m), "steps": steps, "kind": "grid"},
        empty_is_miss=True,
    )
    if not result.ok:
        return result

    readings = list(result.data or [])
    points = [{"lat": c.lat, "lon": c.lon, "index": aq.index,
               "band": aq.band, "scale": aq.scale, "pm25": aq.pm2_5}
              for c, aq in zip(coords, readings) if aq.index is not None]
    values = [p["index"] for p in points]
    return Result(
        data={"points": points, "steps": steps,
              "min": min(values) if values else None,
              "max": max(values) if values else None,
              "scale": points[0]["scale"] if points else ""},
        freshness=result.freshness, provider=result.provider,
        age_s=result.age_s, attempted=result.attempted)


def radar() -> Result:
    """Precipitation radar frames. Keyless, via RainViewer.

    A short TTL on purpose: the index lists scan paths that age out of
    RainViewer's cache after a couple of hours, so a long-cached index serves
    tile URLs that 404 — the overlay silently renders nothing.
    """
    from ..providers.rainviewer import RainViewerProvider
    provider = RainViewerProvider()
    return registry.first_ok(
        [provider], "radar",
        lambda p: p.frames(),
        {"kind": "radar"},
        empty_is_miss=True,
        ttl_s=300.0,
    )


def elevation(coords: list[Coord]) -> Result:
    if not coords:
        return Result(data=[], freshness=Freshness.LIVE, provider="local")
    return registry.first_ok(
        registry.elevation_chain(), "elevation",
        lambda p: p.elevation(coords),
        # Terrain never changes, so the key is the exact points at 4dp and the
        # TTL is a year. This is the single most cacheable thing TERRA fetches.
        {"points": "|".join(str(c.rounded(4)) for c in coords[:100])},
        empty_is_miss=True,
    )


def elevation_profile(geometry: list[Coord], samples: int = 40) -> Result:
    """Elevation along a route, sampled rather than exhaustive.

    A route has thousands of points and an elevation chart needs about forty.
    Sampling before the request rather than after is what keeps this one API
    call — and on the batching providers, one call regardless of route length.
    """
    if not geometry:
        return Result(data={"points": [], "gainM": 0.0, "lossM": 0.0},
                      freshness=Freshness.LIVE, provider="local")
    step = max(1, len(geometry) // max(2, samples))
    sampled = geometry[::step][:samples]
    if sampled[-1] is not geometry[-1]:
        sampled.append(geometry[-1])

    result = elevation(sampled)
    if not result.ok:
        return result
    heights = list(result.data or [])
    gain = sum(max(0.0, heights[i + 1] - heights[i])
               for i in range(len(heights) - 1))
    loss = sum(max(0.0, heights[i] - heights[i + 1])
               for i in range(len(heights) - 1))
    return Result(
        data={
            "points": [{"lat": c.lat, "lon": c.lon, "elevationM": h}
                       for c, h in zip(sampled, heights)],
            "gainM": round(gain, 1), "lossM": round(loss, 1),
            "minM": round(min(heights), 1) if heights else None,
            "maxM": round(max(heights), 1) if heights else None,
        },
        freshness=result.freshness, provider=result.provider,
        age_s=result.age_s, attempted=result.attempted)


# ---------------------------------------------------------------------------
# Daylight, computed locally
# ---------------------------------------------------------------------------
def sun_times(coord: Coord, when: date | None = None,
              utc_offset_s: int | None = None) -> dict:
    """Sunrise, sunset and solar noon, in the local time of the QUERIED place.

    NOAA's low-precision algorithm — accurate to about a minute, which is far
    better than any use TERRA puts it to. Returns nulls above the Arctic and
    below the Antarctic circles on days when the sun does not rise or set,
    because that is the truth there and a fabricated 06:00 would be a lie in
    exactly the places it matters.

    `utc_offset_s` must be the offset at the COORDINATE, not at the server.
    Callers pass the one the weather provider reports (`Weather.utc_offset_s`);
    when that is unavailable the offset is derived from longitude, since solar
    events are a function of solar time and longitude is what determines it.
    That fallback is within half an hour nearly everywhere and is stated in
    `offsetSource` so a caller can qualify it — the alternative, defaulting to
    zero, printed a Bengaluru sunrise of 00:36 because those really are the
    UTC times.
    """
    day = when or date.today()
    offset_source = "given"
    if utc_offset_s is None:
        # Solar time from longitude: 15° of longitude is one hour.
        utc_offset_s = int(round(coord.lon / 15.0)) * 3600
        offset_source = "longitude"
    n = day.timetuple().tm_yday
    lat_rad = math.radians(coord.lat)

    # Solar declination.
    decl = math.radians(23.44) * math.sin(math.radians(360.0 / 365.0 * (n - 81)))
    # Hour angle at sunrise, with the standard -0.833° for refraction and the
    # solar disc's radius.
    cos_h = ((math.sin(math.radians(-0.833)) - math.sin(lat_rad) * math.sin(decl))
             / (math.cos(lat_rad) * math.cos(decl)))
    if cos_h > 1.0:
        return {"sunrise": None, "sunset": None, "solarNoon": None,
                "note": "polar night — the sun does not rise here today",
                "daylightHours": 0.0, "offsetSource": offset_source}
    if cos_h < -1.0:
        return {"sunrise": None, "sunset": None, "solarNoon": None,
                "note": "midnight sun — the sun does not set here today",
                "daylightHours": 24.0, "offsetSource": offset_source}

    hour_angle = math.degrees(math.acos(cos_h))
    # Equation of time, in minutes.
    b = math.radians(360.0 / 365.0 * (n - 81))
    eot = 9.87 * math.sin(2 * b) - 7.53 * math.cos(b) - 1.5 * math.sin(b)

    noon_utc_min = 720.0 - 4.0 * coord.lon - eot
    rise = noon_utc_min - 4.0 * hour_angle
    set_ = noon_utc_min + 4.0 * hour_angle

    def fmt(minutes_utc: float) -> str:
        base = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
        return (base + timedelta(minutes=minutes_utc + utc_offset_s / 60.0)
                ).strftime("%H:%M")

    return {"sunrise": fmt(rise), "sunset": fmt(set_),
            "solarNoon": fmt(noon_utc_min), "note": "",
            "daylightHours": round(hour_angle * 8.0 / 60.0, 2),
            "utcOffsetS": utc_offset_s, "offsetSource": offset_source}


# ---------------------------------------------------------------------------
# Interpretation
# ---------------------------------------------------------------------------
def exposure_penalty(w: Weather | None, mode: str = "walking") -> float:
    """How unpleasant the weather makes travelling, 0..1.

    Fed to the route scorer. Zero for driving by design — rain does not make a
    car journey worse in a way route choice can fix, and pretending otherwise
    would have the scorer reordering routes for no reason a user could follow.
    """
    if w is None or mode == "driving":
        return 0.0
    penalty = 0.0
    if w.precipitation_probability_pct:
        penalty += min(0.5, w.precipitation_probability_pct / 100.0 * 0.5)
    if w.precipitation_mm:
        penalty += min(0.2, w.precipitation_mm / 10.0 * 0.2)
    if w.temperature_c is not None:
        if w.temperature_c > 35 or w.temperature_c < 2:
            penalty += 0.25
        elif w.temperature_c > 30 or w.temperature_c < 8:
            penalty += 0.1
    if w.wind_kph and w.wind_kph > 30:
        penalty += 0.15 if mode == "cycling" else 0.08
    if w.uv_index and w.uv_index >= 8:
        penalty += 0.1
    return min(1.0, penalty)


def outdoor_signals(w: Weather | None, aq: AirQuality | None) -> dict:
    """Structured facts about being outside right now — NOT a recommendation.

    This is the shape the brief asks for under "Should I go for a run?": TERRA
    assembles the evidence and flags the constraints, and OMNIX's reasoning
    layer makes the call. Returning a verdict here would put the judgement in
    the wrong place and hide the inputs behind it.
    """
    concerns: list[str] = []
    favourable: list[str] = []

    if w is not None:
        if w.temperature_c is not None:
            if w.temperature_c >= 33:
                concerns.append(f"hot — {w.temperature_c:.0f}°C"
                                + (f", feels like {w.feels_like_c:.0f}°C"
                                   if w.feels_like_c else ""))
            elif w.temperature_c <= 3:
                concerns.append(f"cold — {w.temperature_c:.0f}°C")
            else:
                favourable.append(f"{w.temperature_c:.0f}°C")
        if (w.precipitation_probability_pct or 0) >= 50:
            concerns.append(f"{w.precipitation_probability_pct:.0f}% chance of rain")
        elif w.precipitation_probability_pct is not None:
            favourable.append(f"{w.precipitation_probability_pct:.0f}% chance of rain")
        if (w.uv_index or 0) >= 8:
            concerns.append(f"very high UV ({w.uv_index:.0f})")
        if (w.wind_kph or 0) >= 35:
            concerns.append(f"windy — {w.wind_kph:.0f} km/h")
        if w.is_day is False:
            concerns.append("after dark")

    if aq is not None and aq.band:
        if aq.band in ("good", "fair"):
            favourable.append(f"air quality {aq.band}")
        else:
            detail = f" (driven by {aq.dominant})" if aq.dominant else ""
            concerns.append(f"air quality {aq.band}{detail}")

    return {
        "concerns": concerns,
        "favourable": favourable,
        # No verdict field on purpose. If one appears here, the reasoning has
        # moved into the data layer and OMNIX's explanation becomes a
        # paraphrase of a rule it cannot see.
        "assessed": bool(w or aq),
    }


__all__ = ["weather", "hourly", "air_quality", "air_quality_grid", "radar",
           "elevation", "elevation_profile", "sun_times", "exposure_penalty",
           "outdoor_signals"]
