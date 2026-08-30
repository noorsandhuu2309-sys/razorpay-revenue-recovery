"""The spatial context object, and its prose rendering for a model.

This is the interface between TERRA and OMNIX's reasoning layer, and the shape
is the one the brief specifies: current_location, weather, air_quality,
nearby_places, routes, elevation, time, known_locations, active_geofences.

Three rules govern what goes in it, all learned from what happens when they are
broken:

  1. **Assembled in parallel-ish, but never all of it.** A full context is five
     provider calls. Most questions need two. `build()` takes flags, and the
     callers in `tools.py` request only what the question needs — building the
     whole thing for "where am I" would make a reverse geocode cost five API
     calls.

  2. **Freshness travels with every section.** Each block carries its own
     `_meta` with provider and freshness, because a context can easily be half
     live and half stale, and a single flag at the top would have to lie about
     one half.

  3. **The prose rendering is separate and lossy on purpose.** `as_prompt()`
     produces the compact text a model reads. It omits ids, coordinates to five
     decimal places, score internals and provider names — none of which help a
     model reason and all of which cost tokens. The JSON keeps everything for
     the UI.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .. import spatial
from ..core import environment, geofencing, memory, places as places_svc
from ..core import geocoding
from ..types import Coord, Result


def build(*, coord: Coord | None = None, workspace_id: str | None = None,
          include_weather: bool = True, include_air: bool = True,
          include_places: bool = False, include_fences: bool = True,
          include_memory: bool = True, include_elevation: bool = False,
          place_category: str = "", radius_m: float = 1500,
          label: str = "") -> dict[str, Any]:
    """Assemble spatial context around a point.

    Every section is optional and every section is independently degradable: a
    weather provider being down produces a weather block marked offline, not a
    failed context. A caller can always render what came back.
    """
    now = datetime.now(timezone.utc).astimezone()
    ctx: dict[str, Any] = {
        "time": {
            "iso": now.isoformat(),
            "local": now.strftime("%A %d %B, %H:%M"),
            "hour": now.hour,
            # The part of day is here because it is what the reasoning actually
            # uses — "is it late" is a judgement a model makes badly from a
            # 24-hour clock and well from a word.
            "partOfDay": _part_of_day(now.hour),
            "weekday": now.strftime("%A"),
            "isWeekend": now.weekday() >= 5,
        },
        "currentLocation": None,
        "weather": None,
        "airQuality": None,
        "nearbyPlaces": [],
        "routes": [],
        "elevation": None,
        "knownLocations": [],
        "activeGeofences": [],
        "dataStatus": {},
    }

    if coord is None:
        # No position is a legitimate state, not an error. The context still
        # carries the time and the user's known locations, which is enough for
        # a model to ask a sensible follow-up instead of guessing.
        if include_memory and workspace_id:
            ctx["knownLocations"] = memory.places(workspace_id)[:12]
        ctx["dataStatus"]["location"] = {"freshness": "offline",
                                         "note": "no position provided"}
        return ctx

    # -- where -------------------------------------------------------------
    where: Result | None = None
    if label:
        ctx["currentLocation"] = {"lat": coord.lat, "lon": coord.lon,
                                  "label": label}
        ctx["dataStatus"]["location"] = {"freshness": "live", "provider": "given"}
    else:
        where = geocoding.reverse(coord, workspace_id=workspace_id)
        place = where.data if where.ok else None
        ctx["currentLocation"] = {
            "lat": coord.lat, "lon": coord.lon,
            "label": getattr(place, "name", "") or "Unknown location",
            "address": getattr(place, "address", ""),
            "country": (getattr(place, "tags", {}) or {}).get("country", ""),
        }
        ctx["dataStatus"]["location"] = _meta(where)

    # -- environment -------------------------------------------------------
    # The weather response carries the UTC offset AT THE COORDINATE, which is
    # the only thing that makes sun times right for somewhere the server is
    # not. Captured here and handed to `sun_times` below.
    utc_offset_s: int | None = None
    if include_weather:
        w = environment.weather(coord)
        ctx["weather"] = w.data.as_dict() if w.ok and w.data else None
        ctx["dataStatus"]["weather"] = _meta(w)
        if w.ok and w.data is not None:
            utc_offset_s = w.data.utc_offset_s

    if include_air:
        aq = environment.air_quality(coord)
        ctx["airQuality"] = aq.data.as_dict() if aq.ok and aq.data else None
        ctx["dataStatus"]["airQuality"] = _meta(aq)

    if include_elevation:
        el = environment.elevation([coord])
        heights = el.data if el.ok else []
        ctx["elevation"] = ({"metres": round(heights[0], 1)} if heights
                            else None)
        ctx["dataStatus"]["elevation"] = _meta(el)

    # Sun times are computed, never fetched — free, offline, and exact enough.
    ctx["time"]["sun"] = environment.sun_times(coord, utc_offset_s=utc_offset_s)

    # -- surroundings ------------------------------------------------------
    if include_places:
        found = places_svc.search(near=coord, category=place_category,
                                  radius_m=radius_m, limit=10)
        ctx["nearbyPlaces"] = [_place_brief(p) for p in (found.data or [])]
        ctx["dataStatus"]["nearbyPlaces"] = _meta(found)

    # -- what TERRA remembers ---------------------------------------------
    if include_memory and workspace_id:
        ctx["knownLocations"] = memory.nearby_saved(workspace_id, coord,
                                                    radius_m=50_000, limit=12)
        frequent = memory.frequent(workspace_id, limit=5)
        if frequent:
            ctx["frequentLocations"] = frequent

    if include_fences and workspace_id:
        active = geofencing.fences(workspace_id, active_only=True)
        ctx["activeGeofences"] = [{
            "id": f["id"], "label": f["label"], "trigger": f["trigger"],
            "inside": f["inside"],
            "distanceM": round(spatial.haversine_m(
                coord, Coord(f["lat"], f["lon"])), 1),
        } for f in active]

    # -- the honesty summary ----------------------------------------------
    ctx["dataStatus"]["overall"] = _overall(ctx["dataStatus"])
    return ctx


def _overall(status: dict[str, Any]) -> str:
    """The weakest freshness in the context.

    Deliberately pessimistic: a context that is 80% live and 20% stale is
    reported as stale, because the summary exists to stop a user trusting the
    whole thing when part of it should not be trusted. Optimism here would
    defeat the point of tracking freshness at all.
    """
    order = ["offline", "stale", "estimated", "cached", "live"]
    worst = "live"
    for section in status.values():
        f = (section or {}).get("freshness")
        if f and order.index(f) < order.index(worst):
            worst = f
    return worst


def _meta(result: Result | None) -> dict[str, Any]:
    if result is None:
        return {"freshness": "offline"}
    meta: dict[str, Any] = {"freshness": result.freshness.value,
                            "provider": result.provider}
    if result.age_s is not None:
        meta["ageS"] = round(result.age_s, 1)
    if result.error:
        meta["error"] = result.error
    return meta


def _place_brief(p) -> dict[str, Any]:
    """A place, trimmed to what a model or a list row needs."""
    out: dict[str, Any] = {
        "name": p.name,
        "category": p.category,
        "distanceM": round(p.distance_m) if p.distance_m is not None else None,
        "lat": p.coord.lat, "lon": p.coord.lon,
    }
    for key, value in (("rating", p.rating), ("ratingCount", p.rating_count),
                       ("openNow", p.open_now), ("hours", p.opening_hours),
                       ("address", p.address), ("website", p.website),
                       ("phone", p.phone), ("wheelchair", p.wheelchair),
                       ("priceLevel", p.price_level)):
        if value not in (None, ""):
            out[key] = value
    return out


def _part_of_day(hour: int) -> str:
    if hour < 5:
        return "night"
    if hour < 12:
        return "morning"
    if hour < 17:
        return "afternoon"
    if hour < 21:
        return "evening"
    return "night"


# ---------------------------------------------------------------------------
# Rendering for a model
# ---------------------------------------------------------------------------
def as_prompt(ctx: dict[str, Any], *, max_places: int = 8) -> str:
    """Compact prose for a system prompt.

    Written as short labelled lines rather than JSON. Models follow both, but
    JSON in a prompt invites the model to answer in JSON, and this context is
    meant to inform a sentence the user reads.

    The staleness warning is the important line and it is stated in words the
    model is instructed to pass on — a freshness enum in a JSON blob gets
    silently dropped from the answer; "this is 40 minutes old" does not.
    """
    lines: list[str] = []
    t = ctx.get("time") or {}
    if t:
        lines.append(f"Time: {t.get('local', '')} ({t.get('partOfDay', '')})")
        sun = t.get("sun") or {}
        if sun.get("sunset"):
            lines.append(f"Daylight: sunrise {sun.get('sunrise')}, "
                         f"sunset {sun.get('sunset')}")

    loc = ctx.get("currentLocation")
    if loc:
        label = loc.get("label") or "unknown"
        addr = f" ({loc['address']})" if loc.get("address") else ""
        lines.append(f"Location: {label}{addr} "
                     f"at {loc['lat']:.4f}, {loc['lon']:.4f}")

    w = ctx.get("weather")
    if w:
        bits = [f"{w.get('description', '')}".strip()]
        if w.get("temperatureC") is not None:
            feels = (f" (feels {w['feelsLikeC']:.0f}°C)"
                     if w.get("feelsLikeC") is not None else "")
            bits.append(f"{w['temperatureC']:.0f}°C{feels}")
        if w.get("precipitationProbabilityPct") is not None:
            bits.append(f"{w['precipitationProbabilityPct']:.0f}% rain chance")
        if w.get("windKph") is not None:
            bits.append(f"wind {w['windKph']:.0f} km/h")
        if w.get("uvIndex") is not None:
            bits.append(f"UV {w['uvIndex']:.0f}")
        if w.get("humidityPct") is not None:
            bits.append(f"humidity {w['humidityPct']:.0f}%")
        lines.append("Weather: " + ", ".join(b for b in bits if b))

    aq = ctx.get("airQuality")
    if aq and aq.get("band"):
        detail = f", driven by {aq['dominant']}" if aq.get("dominant") else ""
        index = f" (index {aq['index']:.0f} on {aq['scale']})" if aq.get("index") else ""
        lines.append(f"Air quality: {aq['band']}{index}{detail}")

    if ctx.get("elevation"):
        lines.append(f"Elevation: {ctx['elevation']['metres']} m")

    nearby = ctx.get("nearbyPlaces") or []
    if nearby:
        lines.append(f"Nearby ({len(nearby)} found):")
        for p in nearby[:max_places]:
            bits = [p["name"]]
            if p.get("category"):
                bits.append(p["category"])
            if p.get("distanceM") is not None:
                bits.append(spatial.human_distance(p["distanceM"]))
            if p.get("rating"):
                bits.append(f"rated {p['rating']}"
                            + (f" ({p['ratingCount']})" if p.get("ratingCount")
                               else ""))
            if p.get("openNow") is True:
                bits.append("open now")
            elif p.get("openNow") is False:
                bits.append("CLOSED")
            lines.append("  - " + " · ".join(bits))

    known = ctx.get("knownLocations") or []
    if known:
        lines.append("Known locations: " + ", ".join(
            f"{k['label']}"
            + (f" ({spatial.human_distance(k['distanceM'])} away)"
               if k.get("distanceM") is not None else "")
            for k in known[:8]))

    fences = ctx.get("activeGeofences") or []
    if fences:
        lines.append("Active geofences: " + ", ".join(
            f"{f['label']}" + (" [inside]" if f.get("inside") else "")
            for f in fences[:8]))

    routes = ctx.get("routes") or []
    for i, r in enumerate(routes):
        traffic = (f", {spatial.human_duration(r['durationTrafficS'])} "
                   "with traffic" if r.get("durationTrafficS") else "")
        lines.append(
            f"Route {i + 1}: {spatial.human_distance(r['distanceM'])}, "
            f"{spatial.human_duration(r['durationS'])}{traffic}"
            + (f" via {r['summary']}" if r.get("summary") else ""))

    status = (ctx.get("dataStatus") or {}).get("overall")
    if status and status != "live":
        ages = [f"{k} {v.get('ageS', 0):.0f}s old"
                for k, v in (ctx.get("dataStatus") or {}).items()
                if isinstance(v, dict) and v.get("freshness") in ("cached", "stale")
                and v.get("ageS")]
        detail = f" ({'; '.join(ages)})" if ages else ""
        lines.append(f"DATA FRESHNESS: {status}{detail} — say so if you rely "
                     "on it; do not present it as current.")

    return "\n".join(lines)


__all__ = ["build", "as_prompt"]
