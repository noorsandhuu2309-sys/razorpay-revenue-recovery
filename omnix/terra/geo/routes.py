"""HTTP surface: `/api/terra/geo/*`.

A thin translation of `api.py` into FastAPI. Deliberately thin — anything with
a decision in it belongs one layer down, where it is testable without a client
and reachable from the agent layer without an HTTP round trip.

Namespaced under `/api/terra/geo/` rather than `/api/geo/` because
`/api/geo/search` and `/api/geo/reverse` already exist and still serve the
world map. Those keep working, unchanged; this is a separate, additive surface.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from . import api, tools
from .config import reload as reload_settings, settings

router = APIRouter(prefix="/api/terra/geo", tags=["terra-geo"])


def _ws(workspace: str | None) -> str:
    from ...core import workspace as workspace_mod
    return workspace_mod.resolve(workspace)


def _bad(message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=400)


# ---------------------------------------------------------------------------
# Configuration and health
# ---------------------------------------------------------------------------
@router.get("/config")
def config() -> dict[str, Any]:
    """Everything the client needs to render a map, and nothing secret.

    The tile URLs live here rather than in the frontend so that changing a
    basemap provider is an environment variable, not a rebuild — and so the
    client never holds a key even when a keyed provider is configured.
    """
    return api.status()


@router.post("/config/reload")
def config_reload() -> dict[str, Any]:
    """Re-read the environment. For picking up a newly added key without a
    restart."""
    reload_settings()
    return api.status()


@router.get("/usage")
def usage() -> dict[str, Any]:
    from . import cache
    return {"usage": cache.usage(), "health": cache.health()}


@router.post("/cache/clear")
def cache_clear(payload: dict | None = None) -> dict[str, Any]:
    return api.clear_cache((payload or {}).get("prefix") or "")


# ---------------------------------------------------------------------------
# Location
# ---------------------------------------------------------------------------
@router.get("/location")
def location(workspace: str | None = None) -> dict[str, Any]:
    return api.get_location(_ws(workspace))


@router.post("/location")
def observe(payload: dict) -> Any:
    """Record a position fix from the browser and evaluate geofences.

    The only endpoint that accepts a user's coordinates for storage, so it is
    the one that has to respect the privacy switches — which it does one layer
    down in `memory.observe`, returning `recorded: false` with a reason rather
    than pretending to have stored something.
    """
    lat, lon = payload.get("lat"), payload.get("lon")
    if lat is None or lon is None:
        return _bad("lat and lon are required")
    return api.observe_location(
        _ws(payload.get("workspace")), lat, lon,
        accuracy_m=payload.get("accuracyM"),
        label=payload.get("label") or "",
        source=payload.get("source") or "browser")


# ---------------------------------------------------------------------------
# Geocoding
# ---------------------------------------------------------------------------
@router.get("/geocode")
def geocode(q: str, lat: float | None = None, lon: float | None = None,
            limit: int = 5, workspace: str | None = None) -> Any:
    if not q.strip():
        return _bad("q is required")
    return api.geocode(q, lat=lat, lon=lon, limit=min(limit, 10),
                       workspace_id=_ws(workspace))


@router.get("/reverse")
def reverse(lat: float, lon: float, workspace: str | None = None) -> dict[str, Any]:
    return api.reverse_geocode(lat, lon, workspace_id=_ws(workspace))


# ---------------------------------------------------------------------------
# Places
# ---------------------------------------------------------------------------
@router.get("/places")
def places(lat: float, lon: float, q: str = "", category: str = "",
           radius: float = 2000, limit: int = 20,
           open_now: bool | None = None,
           ratings: bool = False) -> dict[str, Any]:
    return api.search_places(q, lat=lat, lon=lon, category=category,
                             radius_m=min(radius, 50_000),
                             limit=min(limit, 50), open_now=open_now,
                             require_ratings=ratings)


@router.get("/places/nearest")
def nearest(lat: float, lon: float, category: str,
            radius: float = 5000) -> dict[str, Any]:
    return api.nearest_poi(lat, lon, category, radius_m=min(radius, 50_000))


@router.get("/places/quiet")
def quiet(lat: float, lon: float, radius: float = 5000,
          hours: float = 2.0) -> dict[str, Any]:
    return api.find_quiet_place(lat, lon, radius_m=min(radius, 50_000),
                                hours=hours)


@router.get("/categories")
def categories() -> dict[str, Any]:
    """The canonical category list. The UI builds its filter chips from this so
    a category cannot appear in the interface that no provider can serve."""
    from .providers.base import CATEGORIES
    return {"categories": sorted(CATEGORIES)}


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------
@router.post("/route")
def route(payload: dict) -> Any:
    origin, destination = payload.get("origin"), payload.get("destination")
    if not origin or not destination:
        return _bad("origin and destination are required")
    return api.get_route(origin, destination,
                         mode=payload.get("mode") or "driving",
                         alternatives=int(payload.get("alternatives") or 3),
                         workspace_id=_ws(payload.get("workspace")),
                         avoid_weather=bool(payload.get("avoidWeather")),
                         prefer=payload.get("prefer") or "score")


@router.post("/route/choose")
def route_choose(payload: dict) -> Any:
    routes = payload.get("routes")
    if not isinstance(routes, list) or not routes:
        return _bad("routes are required")
    index = int(payload.get("chosenIndex") or 0)
    return api.choose_route(_ws(payload.get("workspace")),
                            payload.get("origin") or {},
                            payload.get("destination") or {},
                            routes, index,
                            mode=payload.get("mode") or "driving",
                            origin_label=payload.get("originLabel") or "",
                            dest_label=payload.get("destinationLabel") or "")


@router.get("/route/history")
def route_history(workspace: str | None = None, limit: int = 25) -> dict[str, Any]:
    return {"routes": api.route_history(_ws(workspace), min(limit, 200))}


@router.get("/preferences")
def preferences(workspace: str | None = None) -> dict[str, Any]:
    return api.preferences(_ws(workspace))


@router.post("/preferences")
def set_preference(payload: dict) -> Any:
    key, weight = payload.get("key"), payload.get("weight")
    if not key or weight is None:
        return _bad("key and weight are required")
    ws = _ws(payload.get("workspace"))
    if not api.set_preference(ws, str(key), float(weight)):
        return _bad(f"unknown preference '{key}'")
    return api.preferences(ws)


@router.delete("/preferences")
def reset_preferences(workspace: str | None = None) -> dict[str, Any]:
    ws = _ws(workspace)
    return {"reset": api.reset_preferences(ws), **api.preferences(ws)}


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------
@router.get("/weather")
def weather(lat: float, lon: float) -> dict[str, Any]:
    return api.get_weather(lat, lon)


@router.get("/air")
def air(lat: float, lon: float) -> dict[str, Any]:
    return api.get_air_quality(lat, lon)


@router.get("/forecast")
def forecast(lat: float, lon: float, hours: int = 24) -> dict[str, Any]:
    return api.get_hourly(lat, lon, hours)


@router.get("/air/grid")
def air_grid(lat: float, lon: float, radius: float = 12000,
             steps: int = 5) -> dict[str, Any]:
    """Air quality over a grid, in one upstream call — see
    `core.environment.air_quality_grid` for why that matters."""
    return api.get_air_quality_grid(lat, lon, radius_m=radius, steps=steps)


@router.get("/overlays/radar")
def radar() -> dict[str, Any]:
    """Precipitation radar frames.

    Proxied rather than fetched from the browser so the index is cached once
    for every client and the provider stays swappable — the frontend receives
    tile templates and never learns which vendor produced them.
    """
    return api.get_radar()


@router.get("/elevation")
def elevation(lat: float, lon: float) -> dict[str, Any]:
    return api.get_elevation(lat, lon)


@router.post("/elevation/profile")
def elevation_profile(payload: dict) -> Any:
    geometry = payload.get("geometry")
    if not isinstance(geometry, list) or not geometry:
        return _bad("geometry is required")
    return api.get_elevation_profile(geometry[:400])


@router.get("/environment")
def environment(lat: float, lon: float) -> dict[str, Any]:
    return api.get_environmental_context(lat, lon)


# ---------------------------------------------------------------------------
# Spatial memory
# ---------------------------------------------------------------------------
@router.get("/places/saved")
def saved_places(workspace: str | None = None) -> dict[str, Any]:
    return {"places": api.known_locations(_ws(workspace))}


@router.post("/places/saved")
def save_place(payload: dict) -> Any:
    label = (payload.get("label") or "").strip()
    lat, lon = payload.get("lat"), payload.get("lon")
    if not label or lat is None or lon is None:
        return _bad("label, lat and lon are required")
    saved = api.save_place(_ws(payload.get("workspace")), label, lat, lon,
                           kind=payload.get("kind") or "saved",
                           address=payload.get("address") or "",
                           category=payload.get("category") or "",
                           notes=payload.get("notes") or "",
                           tags=payload.get("tags") or [])
    return saved or _bad("could not save that place")


@router.delete("/places/saved/{place_id}")
def delete_place(place_id: str, workspace: str | None = None) -> dict[str, Any]:
    return {"deleted": api.delete_place(_ws(workspace), place_id)}


@router.get("/history")
def history(workspace: str | None = None, limit: int = 50) -> dict[str, Any]:
    return {"history": api.location_history(_ws(workspace), min(limit, 500)),
            "enabled": settings().history_enabled,
            "privacyMode": settings().privacy_mode,
            "retentionDays": settings().history_retention_days}


@router.delete("/history")
def forget_history(workspace: str | None = None) -> dict[str, Any]:
    """Delete location history. Rows, not a flag — see `memory.forget_history`."""
    return {"deleted": api.forget_history(_ws(workspace))}


@router.get("/export")
def export(workspace: str | None = None) -> dict[str, Any]:
    """Everything TERRA holds about this workspace's locations."""
    from .core import memory
    return memory.export(_ws(workspace))


@router.post("/privacy")
def privacy(payload: dict) -> dict[str, Any]:
    """Toggle privacy mode and history for the running process.

    In-process only, on purpose. A setting that silently rewrote `.env` would
    make the file stop describing the deployment; this changes behaviour now
    and the environment remains the source of truth across restarts. The
    response says so, and the UI shows it.
    """
    cfg = settings()
    if "privacyMode" in payload:
        cfg.privacy_mode = bool(payload["privacyMode"])
    if "historyEnabled" in payload:
        cfg.history_enabled = bool(payload["historyEnabled"])
    if "retentionDays" in payload:
        cfg.history_retention_days = max(0.0, float(payload["retentionDays"]))
    if "offline" in payload:
        cfg.offline = bool(payload["offline"])
    return {"privacyMode": cfg.privacy_mode,
            "historyEnabled": cfg.history_enabled,
            "retentionDays": cfg.history_retention_days,
            "offline": cfg.offline,
            "note": "Applies to this running process. Set TERRA_PRIVACY_MODE, "
                    "TERRA_HISTORY, TERRA_HISTORY_DAYS or TERRA_OFFLINE in "
                    ".env to make it permanent."}


# ---------------------------------------------------------------------------
# Geofencing
# ---------------------------------------------------------------------------
@router.get("/geofences")
def geofences(workspace: str | None = None) -> dict[str, Any]:
    return {"geofences": api.geofences(_ws(workspace))}


@router.post("/geofences")
def create_geofence(payload: dict) -> Any:
    label = (payload.get("label") or "").strip()
    if not label:
        return _bad("label is required")
    fence = api.create_geofence(
        _ws(payload.get("workspace")), label,
        lat=payload.get("lat"), lon=payload.get("lon"),
        radius_m=float(payload.get("radiusM") or 200.0),
        polygon=payload.get("polygon"),
        trigger=payload.get("trigger") or "both",
        action=payload.get("action") or "notify",
        payload=payload.get("payload") or {})
    return fence or _bad("a geofence needs coordinates or a polygon")


@router.delete("/geofences/{fence_id}")
def delete_geofence(fence_id: str, workspace: str | None = None) -> dict[str, Any]:
    return {"deleted": api.delete_geofence(_ws(workspace), fence_id)}


@router.post("/geofences/{fence_id}/active")
def toggle_geofence(fence_id: str, payload: dict) -> dict[str, Any]:
    from .core import geofencing
    ws = _ws(payload.get("workspace"))
    active = bool(payload.get("active", True))
    return {"updated": geofencing.set_active(ws, fence_id, active)}


@router.get("/geofences/events")
def geofence_events(workspace: str | None = None,
                    limit: int = 30) -> dict[str, Any]:
    return {"events": api.geofence_events(_ws(workspace), min(limit, 200))}


# ---------------------------------------------------------------------------
# Context and tools
# ---------------------------------------------------------------------------
@router.get("/context")
def context(lat: float | None = None, lon: float | None = None,
            workspace: str | None = None, places: bool = False,
            category: str = "", radius: float = 1500) -> dict[str, Any]:
    return api.get_spatial_context(lat, lon, workspace_id=_ws(workspace),
                                   include_places=places,
                                   place_category=category,
                                   radius_m=min(radius, 20_000))


@router.get("/tools")
def tool_schema() -> dict[str, Any]:
    """The tool catalogue. Exposed so the UI can show what TERRA can be asked
    and so the schema has exactly one definition."""
    return {"tools": tools.schema()}


@router.post("/tools/invoke")
def tool_invoke(payload: dict) -> Any:
    """Run one validated tool.

    Reachable over HTTP because the frontend uses it too, and because a tool
    layer that can only be exercised through a model is a tool layer that
    cannot be tested. Validation is identical on every path — see
    `tools.invoke`.
    """
    name = payload.get("tool")
    if not name:
        return _bad("tool is required")
    return tools.invoke(str(name), payload.get("args") or {},
                        workspace_id=_ws(payload.get("workspace")))


@router.post("/ask")
def ask(payload: dict) -> Any:
    """Natural language in, a tool result out.

    Deterministic parse first, model selection second — the ordering that keeps
    the common questions free. `route` reports which path answered, so the cost
    of the natural-language layer is visible rather than assumed.
    """
    text = (payload.get("text") or "").strip()
    if not text:
        return _bad("text is required")
    ws = _ws(payload.get("workspace"))
    lat, lon = payload.get("lat"), payload.get("lon")

    call = tools.parse(text, lat=lat, lon=lon)
    path = "parsed"
    if call is None:
        call = tools.select(text, lat=lat, lon=lon, workspace_id=ws)
        path = "model"
    if call is None:
        return {"matched": False, "path": "none", "text": text,
                "note": "No TERRA tool fits that request."}

    result = tools.invoke(call["tool"], call["args"], workspace_id=ws)
    return {"matched": True, "path": path, **result}
