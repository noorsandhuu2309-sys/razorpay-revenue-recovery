"""Structured tools: the only way a model reaches TERRA.

The brief's rule — "do not let the LLM directly construct arbitrary API
requests without validation" — is enforced structurally rather than by
instruction. A model never sees a URL, a provider name or a key. It selects a
tool name from a fixed list and supplies arguments that are validated against a
schema before anything runs. An unknown tool is refused; an out-of-range radius
is clamped; an unknown category falls back to free text rather than being
passed through.

There are two dispatch paths and the cheap one runs first:

  **`parse()` — deterministic.** Most spatial requests are formulaic. "where am
  i", "find coffee near me", "take me to college", "weather here" are all
  matchable with patterns, and matching them costs nothing, works with every
  model unavailable, and cannot hallucinate a tool. This handles the common
  cases outright.

  **`select()` — model-driven.** Everything `parse` declines. The model is
  given the tool list and the user's sentence and returns one JSON object. Its
  output is then run through the same validator as everything else, so a
  malformed or malicious selection fails closed.

Both paths converge on `invoke()`, which is the single point where a tool
actually runs.
"""

from __future__ import annotations

import re
from typing import Any, Callable

from . import api
from .providers.base import CATEGORIES, canonical_category

#: The catalogue. `args` describes each parameter as (type, required, default),
#: which is enough to validate against and enough to render as a schema for a
#: model. Keeping one definition for both is what stops the prompt and the
#: validator drifting apart — the classic way a tool layer starts accepting
#: things it documents as forbidden.
TOOLS: dict[str, dict[str, Any]] = {
    "get_location": {
        "description": "The user's last known position, from TERRA's memory. "
                       "Use before any tool needing a location the user has "
                       "not stated.",
        "args": {},
    },
    "geocode": {
        "description": "Turn a place name or address into coordinates.",
        "args": {"query": ("str", True, None),
                 "limit": ("int", False, 5)},
    },
    "reverse_geocode": {
        "description": "Turn coordinates into a place name and address.",
        "args": {"lat": ("float", True, None), "lon": ("float", True, None)},
    },
    "search_places": {
        "description": "Find places near a point by category or free text. "
                       "Categories: " + ", ".join(sorted(CATEGORIES)) + ".",
        "args": {"lat": ("float", True, None), "lon": ("float", True, None),
                 "category": ("str", False, ""), "query": ("str", False, ""),
                 "radius_m": ("float", False, 2000.0),
                 "limit": ("int", False, 12),
                 "open_now": ("bool", False, None)},
    },
    "nearest_poi": {
        "description": "The single closest place of a category. Use for "
                       "'nearest hospital' and other urgent lookups.",
        "args": {"lat": ("float", True, None), "lon": ("float", True, None),
                 "category": ("str", True, None),
                 "radius_m": ("float", False, 5000.0)},
    },
    "find_quiet_place": {
        "description": "Ranked candidates for somewhere quiet to work or "
                       "study, filtered by how long they stay open.",
        "args": {"lat": ("float", True, None), "lon": ("float", True, None),
                 "radius_m": ("float", False, 5000.0),
                 "hours": ("float", False, 2.0)},
    },
    "get_route": {
        "description": "Route between two places. Each end may be coordinates "
                       "or a name TERRA knows, such as 'home' or 'college'.",
        "args": {"origin": ("place", True, None),
                 "destination": ("place", True, None),
                 "mode": ("str", False, "driving"),
                 "alternatives": ("int", False, 3),
                 "prefer": ("str", False, "score")},
    },
    "get_weather": {
        "description": "Current weather at a point.",
        "args": {"lat": ("float", True, None), "lon": ("float", True, None)},
    },
    "get_air_quality": {
        "description": "Current air quality at a point.",
        "args": {"lat": ("float", True, None), "lon": ("float", True, None)},
    },
    "get_elevation": {
        "description": "Height above sea level at a point.",
        "args": {"lat": ("float", True, None), "lon": ("float", True, None)},
    },
    "get_environmental_context": {
        "description": "Weather, air quality, daylight and the outdoor "
                       "signals together. Use for 'should I go outside/run'.",
        "args": {"lat": ("float", True, None), "lon": ("float", True, None)},
    },
    "get_spatial_context": {
        "description": "The full structured picture around a point: place, "
                       "conditions, nearby, known locations, geofences.",
        "args": {"lat": ("float", True, None), "lon": ("float", True, None),
                 "include_places": ("bool", False, False),
                 "place_category": ("str", False, "")},
    },
    "save_place": {
        "description": "Remember a named location so it never needs looking "
                       "up again. Use when the user says 'this is my X'.",
        "args": {"label": ("str", True, None),
                 "lat": ("float", True, None), "lon": ("float", True, None),
                 "kind": ("str", False, "saved")},
    },
    "known_locations": {
        "description": "Every location the user has saved.",
        "args": {},
    },
    "location_history": {
        "description": "Recently visited places, if history is enabled.",
        "args": {"limit": ("int", False, 20)},
    },
    "create_geofence": {
        "description": "Watch an area and raise an event on entering or "
                       "leaving it. Use for 'remind me when I get to X'.",
        "args": {"label": ("str", True, None),
                 "lat": ("float", True, None), "lon": ("float", True, None),
                 "radius_m": ("float", False, 200.0),
                 "trigger": ("str", False, "both"),
                 "action": ("str", False, "notify")},
    },
    "geofences": {
        "description": "Every geofence and whether the user is inside it.",
        "args": {},
    },
    "distance": {
        "description": "Straight-line distance and bearing between two "
                       "points. Free and offline — no provider is called.",
        "args": {"a": ("place", True, None), "b": ("place", True, None)},
    },
}

#: Tools that need a workspace to mean anything. Injected by `invoke`, never
#: accepted from the model — a model that could name a workspace could read
#: another one's saved places.
_WORKSPACE_TOOLS = frozenset((
    "get_location", "save_place", "known_locations", "location_history",
    "create_geofence", "geofences", "get_route", "get_spatial_context",
    "geocode",
))

#: Bounds. Applied by clamping rather than rejection, because a model asking
#: for a 900km radius means "far", and failing the call teaches it nothing
#: while answering with 50km answers the question.
_LIMITS = {
    "radius_m": (10.0, 50_000.0),
    "limit": (1, 50),
    "alternatives": (1, 3),
    "hours": (0.0, 24.0),
}

_MODES = ("driving", "walking", "cycling", "transit")
_PREFER = ("score", "fastest", "shortest")
_TRIGGERS = ("enter", "exit", "both")


def schema() -> list[dict[str, Any]]:
    """The tool list as JSON schema, for a model's tool-calling interface."""
    types = {"str": "string", "float": "number", "int": "integer",
             "bool": "boolean", "place": "object"}
    out = []
    for name, spec in TOOLS.items():
        properties, required = {}, []
        for arg, (kind, is_required, default) in spec["args"].items():
            prop: dict[str, Any] = {"type": types.get(kind, "string")}
            if kind == "place":
                prop = {"type": ["object", "string"],
                        "description": "Either {lat, lon} or a place name "
                                       "TERRA knows."}
            if default is not None:
                prop["default"] = default
            if arg == "category":
                prop["enum"] = sorted(CATEGORIES)
            if arg == "mode":
                prop["enum"] = list(_MODES)
            if arg == "prefer":
                prop["enum"] = list(_PREFER)
            if arg == "trigger":
                prop["enum"] = list(_TRIGGERS)
            properties[arg] = prop
            if is_required:
                required.append(arg)
        out.append({"name": name, "description": spec["description"],
                    "parameters": {"type": "object", "properties": properties,
                                   "required": required}})
    return out


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def validate(name: str, args: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """Coerce and bound arguments. Returns (clean_args, error).

    Unknown keys are DROPPED rather than passed through. That is the line that
    matters: without it a model could add `key=...` or `url=...` to a call and
    the kwargs would reach a provider function.
    """
    spec = TOOLS.get(name)
    if spec is None:
        return ({}, f"unknown tool '{name}'")

    clean: dict[str, Any] = {}
    for arg, (kind, is_required, default) in spec["args"].items():
        if arg not in args or args[arg] is None:
            if is_required:
                return ({}, f"{name}: missing required argument '{arg}'")
            if default is not None:
                clean[arg] = default
            continue

        raw = args[arg]
        try:
            if kind == "float":
                value: Any = float(raw)
            elif kind == "int":
                value = int(float(raw))
            elif kind == "bool":
                value = (raw if isinstance(raw, bool)
                         else str(raw).lower() in ("1", "true", "yes"))
            elif kind == "place":
                value = _clean_place(raw)
                if value is None:
                    return ({}, f"{name}: '{arg}' must be a name or "
                                "{lat, lon}")
            else:
                value = str(raw)
        except (TypeError, ValueError):
            return ({}, f"{name}: '{arg}' is not a valid {kind}")

        if arg in _LIMITS:
            low, high = _LIMITS[arg]
            value = max(low, min(value, high))
        if arg in ("lat", "lon"):
            bound = 90.0 if arg == "lat" else 180.0
            if not (-bound <= value <= bound):
                return ({}, f"{name}: '{arg}' out of range")
        if arg == "category" and value:
            # Canonicalise rather than reject. A model saying "coffee shop"
            # should get cafés, not an error it then apologises for.
            value = canonical_category(value) or ""
        if arg == "mode" and value not in _MODES:
            value = "driving"
        if arg == "prefer" and value not in _PREFER:
            value = "score"
        if arg == "trigger" and value not in _TRIGGERS:
            value = "both"
        if kind == "str":
            value = value[:200]

        clean[arg] = value
    return (clean, "")


def _clean_place(raw: Any) -> dict | str | None:
    if isinstance(raw, str):
        return raw[:200] or None
    if isinstance(raw, dict):
        lat, lon = raw.get("lat"), raw.get("lon")
        if lat is None or lon is None:
            return (raw.get("label") or raw.get("name") or None)
        try:
            return {"lat": float(lat), "lon": float(lon),
                    "label": str(raw.get("label") or "")[:120]}
        except (TypeError, ValueError):
            return None
    return None


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------
def _handlers() -> dict[str, Callable[..., Any]]:
    return {
        "get_location": api.get_location,
        "geocode": api.geocode,
        "reverse_geocode": api.reverse_geocode,
        "search_places": api.search_places,
        "nearest_poi": api.nearest_poi,
        "find_quiet_place": api.find_quiet_place,
        "get_route": api.get_route,
        "get_weather": api.get_weather,
        "get_air_quality": api.get_air_quality,
        "get_elevation": api.get_elevation,
        "get_environmental_context": api.get_environmental_context,
        "get_spatial_context": api.get_spatial_context,
        "save_place": api.save_place,
        "known_locations": api.known_locations,
        "location_history": api.location_history,
        "create_geofence": api.create_geofence,
        "geofences": api.geofences,
        "distance": api.distance,
    }


def invoke(name: str, args: dict[str, Any],
           workspace_id: str | None = None) -> dict[str, Any]:
    """Run one validated tool. Never raises; failures come back as data.

    The single choke point. Everything — the deterministic parser, the model
    selector, the HTTP endpoint — arrives here, so validation cannot be
    bypassed by adding a new caller.
    """
    clean, error = validate(name, args)
    if error:
        return {"tool": name, "ok": False, "error": error}

    handler = _handlers().get(name)
    if handler is None:
        return {"tool": name, "ok": False, "error": f"unknown tool '{name}'"}

    call: dict[str, Any] = dict(clean)
    if name in _WORKSPACE_TOOLS:
        if not workspace_id:
            return {"tool": name, "ok": False,
                    "error": "this tool needs a workspace"}
        call["workspace_id"] = workspace_id

    # Positional-first signatures. Keeping this mapping explicit rather than
    # blanket **kwargs means adding an api function does not silently create a
    # model-reachable tool.
    try:
        if name == "geocode":
            result = handler(call.pop("query"), **call)
        elif name == "save_place":
            result = handler(call.pop("workspace_id"), call.pop("label"),
                             call.pop("lat"), call.pop("lon"), **call)
        elif name == "create_geofence":
            result = handler(call.pop("workspace_id"), call.pop("label"), **call)
        elif name in ("known_locations", "geofences", "location_history",
                      "get_location"):
            result = handler(**call)
        elif name == "distance":
            result = handler(_as_point(call["a"]), _as_point(call["b"]))
        elif name == "get_route":
            result = handler(call.pop("origin"), call.pop("destination"), **call)
        else:
            result = handler(**call)
    except Exception as exc:  # noqa: BLE001 — a tool must not break a turn
        return {"tool": name, "ok": False, "error": str(exc), "args": clean}

    return {"tool": name, "ok": True, "args": clean, "result": result}


def _as_point(value: Any) -> dict:
    """`distance` needs two coordinate pairs; a name has to resolve first."""
    if isinstance(value, dict):
        return value
    from .core import geocoding
    coord, label, _ = geocoding.resolve(str(value))
    return ({"lat": coord.lat, "lon": coord.lon, "label": label} if coord
            else {})


# ---------------------------------------------------------------------------
# Deterministic parsing
# ---------------------------------------------------------------------------
_HERE = ("here", "near me", "nearby", "around me", "around here", "close by",
         "my location", "where i am", "current location", "near by")

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(where am i|my location|current location)\b"), "where"),
    (re.compile(r"\b(what'?s|whats|what is) (around|near) (me|here)\b"), "around"),
    (re.compile(r"\bwhat'?s? nearby\b"), "around"),
    (re.compile(r"\b(take me to|navigate to|directions? to|route to|"
                r"how do i get to|drive to|walk to|cycle to)\s+(.+)"), "route"),
    (re.compile(r"\b(fastest|quickest|shortest) route\b"), "route_pref"),
    (re.compile(r"\b(nearest|closest)\s+(.+)"), "nearest"),
    (re.compile(r"\b(find|show|search for|any|where can i (?:find|get|buy))\s+"
                r"(.+)"), "places"),
    (re.compile(r"\b(weather|temperature|forecast|raining|hot|cold)\b"), "weather"),
    (re.compile(r"\b(air quality|aqi|pollution|smog)\b"), "air"),
    (re.compile(r"\b(elevation|altitude|how high)\b"), "elevation"),
    (re.compile(r"\b(should i|can i|is it ok to)\b.*\b(run|walk|jog|cycle|"
                r"go outside|go out)\b"), "outdoor"),
    (re.compile(r"\b(quiet|peaceful|calm)\b.*\b(place|spot|somewhere|work|"
                r"study)\b"), "quiet"),
    (re.compile(r"\bsomewhere (quiet|to work|to study)\b"), "quiet"),
    (re.compile(r"\b(remind me when i|notify me when i|tell me when i)\s+"
                r"(?:reach|arrive at|get to|leave|exit)\s+(.+)"), "geofence"),
    (re.compile(r"\b(where have i been|places i(?:'ve| have) visited|"
                r"location history|recent(?:ly)? visited)\b"), "history"),
    (re.compile(r"\b(my (?:saved )?places|known locations|saved locations)\b"),
     "known"),
]


def parse(text: str, *, lat: float | None = None,
          lon: float | None = None) -> dict[str, Any] | None:
    """Match a request to a tool call without a model.

    Returns `{"tool": ..., "args": ...}` or None when nothing matches
    confidently. Returning None is the important half — a parser that guesses
    is worse than one that defers, because a wrong tool produces a confident
    answer to a question nobody asked.
    """
    t = (text or "").strip().lower()
    if not t:
        return None
    has_here = lat is not None and lon is not None
    point = {"lat": lat, "lon": lon} if has_here else {}

    for pattern, kind in _PATTERNS:
        m = pattern.search(t)
        if not m:
            continue

        if kind == "where" and has_here:
            return {"tool": "reverse_geocode", "args": point}
        if kind == "around" and has_here:
            return {"tool": "get_spatial_context",
                    "args": {**point, "include_places": True}}
        if kind == "weather" and has_here:
            return {"tool": "get_weather", "args": point}
        if kind == "air" and has_here:
            return {"tool": "get_air_quality", "args": point}
        if kind == "elevation" and has_here:
            return {"tool": "get_elevation", "args": point}
        if kind == "outdoor" and has_here:
            return {"tool": "get_environmental_context", "args": point}
        if kind == "quiet" and has_here:
            return {"tool": "find_quiet_place",
                    "args": {**point, "hours": _hours_in(t) or 2.0}}
        if kind == "history":
            return {"tool": "location_history", "args": {"limit": 20}}
        if kind == "known":
            return {"tool": "known_locations", "args": {}}

        if kind == "route":
            destination = _strip_place(m.group(2))
            if not destination:
                return None
            args: dict[str, Any] = {
                "destination": destination,
                "mode": _mode_in(t),
                "prefer": "fastest" if "fastest" in t or "quickest" in t
                          else ("shortest" if "shortest" in t else "score"),
            }
            args["origin"] = point if has_here else "home"
            return {"tool": "get_route", "args": args}

        if kind == "nearest" and has_here:
            category = canonical_category(m.group(2))
            if not category:
                return None
            return {"tool": "nearest_poi",
                    "args": {**point, "category": category,
                             "radius_m": _radius_in(t) or 5000.0}}

        if kind == "places" and has_here:
            phrase = _strip_place(m.group(2))
            category = canonical_category(phrase)
            if not category:
                # A free-text search is still a valid, validated tool call —
                # but only when the phrase is plausibly a place name rather
                # than the rest of an arbitrary sentence.
                if len(phrase.split()) > 4:
                    return None
                return {"tool": "search_places",
                        "args": {**point, "query": phrase,
                                 "radius_m": _radius_in(t) or 2000.0}}
            return {"tool": "search_places",
                    "args": {**point, "category": category,
                             "radius_m": _radius_in(t) or 2000.0,
                             "open_now": "open" in t or "now" in t}}

        if kind == "geofence":
            label = _strip_place(m.group(2))
            if not label or not has_here:
                return None
            # The fence is created at the NAMED place, not at the user — so the
            # place has to resolve first. Left to `invoke`? No: the tool takes
            # coordinates, and handing it the user's current position would
            # create a fence around wherever they happened to be standing.
            from .core import geocoding
            coord, resolved, _ = geocoding.resolve(label)
            if coord is None:
                return None
            return {"tool": "create_geofence",
                    "args": {"label": resolved or label,
                             "lat": coord.lat, "lon": coord.lon,
                             "trigger": "exit" if "leave" in t or "exit" in t
                                        else "enter",
                             "action": "notify"}}
    return None


def _strip_place(text: str) -> str:
    """Trim a captured phrase down to the place it names."""
    t = (text or "").strip().strip("?.!,")
    for phrase in _HERE:
        t = t.replace(phrase, " ")
    t = re.sub(r"\b(please|now|right now|for me|asap|quickly|the|a|an)\b",
               " ", t)
    t = re.sub(r"\s+(within|in|under)\s+\d+\s*(km|kilometres|kilometers|m|"
               r"metres|meters|miles)\b.*", "", t)
    return " ".join(t.split())[:120]


def _radius_in(text: str) -> float | None:
    """"within 5 km" -> 5000. Units matter: bare numbers are ignored, since
    "find 3 cafes" is a count, not a distance."""
    m = re.search(r"(\d+(?:\.\d+)?)\s*(km|kilometres|kilometers|m\b|metres|"
                  r"meters|miles|mi\b)", text)
    if not m:
        return None
    value, unit = float(m.group(1)), m.group(2)
    if unit.startswith("k"):
        return value * 1000.0
    if unit.startswith("mi"):
        return value * 1609.34
    return value


def _hours_in(text: str) -> float | None:
    m = re.search(r"(\d+(?:\.\d+)?)\s*(hours?|hrs?|h\b)", text)
    if m:
        return float(m.group(1))
    return 2.0 if "couple of hours" in text else None


def _mode_in(text: str) -> str:
    if any(w in text for w in ("walk", "walking", "on foot")):
        return "walking"
    if any(w in text for w in ("cycle", "cycling", "bike", "bicycle")):
        return "cycling"
    if any(w in text for w in ("transit", "bus", "metro", "train", "public")):
        return "transit"
    return "driving"


# ---------------------------------------------------------------------------
# Model-driven selection
# ---------------------------------------------------------------------------
_SELECT_SYSTEM = """You route a user's request to exactly one TERRA geospatial \
tool, or to none.

Reply with ONE JSON object and nothing else:
  {"tool": "<name>", "args": {...}}
or, if no tool fits the request:
  {"tool": null, "reason": "<short reason>"}

Rules:
- Use only the tools listed. Never invent a tool or an argument.
- Omit arguments you do not know. Do not guess coordinates — if a tool needs
  lat/lon and none were supplied, either use a tool that does not, or answer
  with tool: null.
- Prefer the most specific tool. "nearest hospital" is nearest_poi, not
  search_places.
"""


def select(text: str, *, lat: float | None = None, lon: float | None = None,
           workspace_id: str | None = None) -> dict[str, Any] | None:
    """Ask a model which tool to call. Validated exactly like any other path.

    Only reached when `parse` declines, so the cheap path stays cheap. Returns
    None on any failure — no model, bad JSON, unknown tool — and the caller
    then answers without TERRA rather than failing the whole turn.
    """
    try:
        from ...models.router import router as _router
    except Exception:
        return None

    catalogue = "\n".join(
        f"- {t['name']}({', '.join(t['parameters']['properties'])}): "
        f"{t['description']}"
        for t in schema())
    where = (f"\nThe user's current position is lat={lat}, lon={lon}."
             if lat is not None and lon is not None
             else "\nThe user's current position is unknown.")

    result = _router.generate_json(
        "fast",
        system=_SELECT_SYSTEM + "\n\nTools:\n" + catalogue + where,
        messages=[{"role": "user", "content": text}],
        temperature=0.0, max_tokens=300,
        workspace_id=workspace_id, agent="terra",
        default=None)
    if not isinstance(result, dict) or not result.get("tool"):
        return None

    name = str(result["tool"])
    if name not in TOOLS:
        return None
    args = result.get("args") if isinstance(result.get("args"), dict) else {}

    # The model is told not to guess coordinates, but "told not to" is not a
    # control. Any lat/lon it supplies is replaced with the real position when
    # we have one, and the call is refused when we do not.
    if "lat" in TOOLS[name]["args"]:
        if lat is None or lon is None:
            return None
        args["lat"], args["lon"] = lat, lon

    clean, error = validate(name, args)
    if error:
        return None
    return {"tool": name, "args": clean}


__all__ = ["TOOLS", "schema", "validate", "invoke", "parse", "select"]
