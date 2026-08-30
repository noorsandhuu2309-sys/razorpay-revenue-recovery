"""Provider protocols and the shared HTTP client.

A provider's whole job is to answer one question in TERRA's vocabulary. It does
NOT decide whether to cache, whether it is allowed to run, what to do when it
fails, or which provider runs next — all four belong to the core services, and
a provider that took any of them on would be impossible to swap out, which is
the one property this layer exists to have.

So a provider is: a name, a `available()`, and one or more capability methods
returning TERRA types. Every one of them may raise; the core catches.

The protocols are `typing.Protocol` rather than ABCs deliberately. A provider
implementing three of the seven capabilities is normal — Overpass does places
and nothing else, OSRM does routes and nothing else — and inheritance would
force either seven near-empty base classes or one fat base with five methods
that raise. Structural typing lets each provider declare exactly what it can
do, and `registry.py` asks by capability.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

import httpx

from ..config import USER_AGENT, settings
from ..types import AirQuality, Coord, Mode, Place, Route, Weather

# One client, reused. A new httpx.Client per call means a new TCP and TLS
# handshake per call — on a chatty layer like this it doubled latency, and
# connection pooling is most of what makes the fallback chains fast enough to
# be worth having.
_client: httpx.Client | None = None


def client() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(
            timeout=settings().timeout_s,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            limits=httpx.Limits(max_connections=16, max_keepalive_connections=8),
        )
    return _client


def get_json(url: str, params: dict[str, Any] | None = None,
             *, headers: dict[str, str] | None = None,
             retries: int | None = None) -> Any:
    """GET returning parsed JSON, with bounded retry on transient failure.

    Retries 5xx, 429 and connection errors, and never retries a 4xx — a 400 or
    a 403 is a bad request or a bad key, and repeating it just spends quota to
    be told the same thing. Backoff is exponential from 400ms, which is enough
    for a public endpoint's momentary overload without making a user wait.
    """
    cfg = settings()
    attempts = (cfg.max_retries if retries is None else retries) + 1
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            resp = client().get(url, params=params, headers=headers)
            if resp.status_code in (429, 502, 503, 504):
                raise httpx.HTTPStatusError(
                    f"{resp.status_code} from {resp.url.host}",
                    request=resp.request, response=resp)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as exc:
            last = exc
            code = exc.response.status_code if exc.response is not None else 0
            if code and code not in (429, 502, 503, 504):
                raise
        except (httpx.TransportError, ValueError) as exc:
            last = exc
        if attempt < attempts - 1:
            import time
            time.sleep(0.4 * (2 ** attempt))
    raise last or RuntimeError(f"request to {url} failed")


def post_json(url: str, body: dict[str, Any],
              *, headers: dict[str, str] | None = None) -> Any:
    resp = client().post(url, json=body, headers=headers)
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Capability protocols
# ---------------------------------------------------------------------------
@runtime_checkable
class Provider(Protocol):
    #: Stable identifier. Used as the rate-limit bucket, the usage-accounting
    #: key, the health-circuit key and the attribution string, so it must not
    #: change once shipped.
    name: str

    def available(self) -> bool:
        """Whether this provider can be called right now.

        Configuration only — a missing key, a disabled flag, offline mode. It
        must not make a network call: this is consulted while building a
        fallback chain, potentially several times per request.
        """
        ...


@runtime_checkable
class GeocodeProvider(Provider, Protocol):
    def geocode(self, query: str, *, limit: int = 5,
                near: Coord | None = None) -> list[Place]: ...

    def reverse(self, coord: Coord) -> Place | None: ...


@runtime_checkable
class PlacesProvider(Provider, Protocol):
    def search_places(self, *, near: Coord, query: str = "",
                      category: str = "", radius_m: float = 2000,
                      limit: int = 20) -> list[Place]: ...


@runtime_checkable
class RouteProvider(Provider, Protocol):
    def route(self, origin: Coord, destination: Coord, *,
              mode: Mode = Mode.DRIVING, alternatives: int = 1,
              steps: bool = True) -> list[Route]: ...

    def supports(self, mode: Mode) -> bool: ...


@runtime_checkable
class WeatherProvider(Provider, Protocol):
    def weather(self, coord: Coord) -> Weather | None: ...


@runtime_checkable
class AirQualityProvider(Provider, Protocol):
    def air_quality(self, coord: Coord) -> AirQuality | None: ...


@runtime_checkable
class ElevationProvider(Provider, Protocol):
    def elevation(self, coords: list[Coord]) -> list[float]: ...


# ---------------------------------------------------------------------------
# Shared vocabulary
# ---------------------------------------------------------------------------
# TERRA's category names, and how each provider spells them. Keeping the
# mapping here rather than in each provider is what lets a caller ask for
# "hospital" without knowing whether the answer comes from OSM tags or Google
# place types — and it is the list the LLM tool schema validates against, so a
# model cannot invent a category that reaches a provider.
CATEGORIES: dict[str, dict[str, list[str]]] = {
    "cafe":       {"osm": ["amenity=cafe"], "google": ["cafe"]},
    "restaurant": {"osm": ["amenity=restaurant"], "google": ["restaurant"]},
    "bar":        {"osm": ["amenity=bar", "amenity=pub"], "google": ["bar"]},
    "hospital":   {"osm": ["amenity=hospital"], "google": ["hospital"]},
    "clinic":     {"osm": ["amenity=clinic", "amenity=doctors"],
                   "google": ["doctor"]},
    "pharmacy":   {"osm": ["amenity=pharmacy"], "google": ["pharmacy"]},
    "police":     {"osm": ["amenity=police"], "google": ["police"]},
    "fuel":       {"osm": ["amenity=fuel"], "google": ["gas_station"]},
    "charging":   {"osm": ["amenity=charging_station"],
                   "google": ["electric_vehicle_charging_station"]},
    "atm":        {"osm": ["amenity=atm"], "google": ["atm"]},
    "bank":       {"osm": ["amenity=bank"], "google": ["bank"]},
    "school":     {"osm": ["amenity=school"], "google": ["school"]},
    "university": {"osm": ["amenity=university", "amenity=college"],
                   "google": ["university"]},
    "library":    {"osm": ["amenity=library"], "google": ["library"]},
    "coworking":  {"osm": ["amenity=coworking_space", "office=coworking"],
                   "google": ["coworking_space"]},
    "park":       {"osm": ["leisure=park", "leisure=garden"], "google": ["park"]},
    "gym":        {"osm": ["leisure=fitness_centre"], "google": ["gym"]},
    "supermarket": {"osm": ["shop=supermarket"], "google": ["supermarket"]},
    "shop":       {"osm": ["shop"], "google": ["store"]},
    "hotel":      {"osm": ["tourism=hotel", "tourism=guest_house"],
                   "google": ["lodging"]},
    "museum":     {"osm": ["tourism=museum"], "google": ["museum"]},
    "attraction": {"osm": ["tourism=attraction"], "google": ["tourist_attraction"]},
    "transit":    {"osm": ["public_transport=station", "railway=station"],
                   "google": ["transit_station"]},
    "bus":        {"osm": ["highway=bus_stop"], "google": ["bus_station"]},
    "airport":    {"osm": ["aeroway=aerodrome"], "google": ["airport"]},
    "parking":    {"osm": ["amenity=parking"], "google": ["parking"]},
    "toilets":    {"osm": ["amenity=toilets"], "google": ["public_bathroom"]},
    "worship":    {"osm": ["amenity=place_of_worship"], "google": ["place_of_worship"]},
    "post":       {"osm": ["amenity=post_office"], "google": ["post_office"]},
    "veterinary": {"osm": ["amenity=veterinary"], "google": ["veterinary_care"]},
}

#: Words a user might reasonably say, mapped onto the canonical category. This
#: is what makes "find me a chemist" work in Indian English and "gas station"
#: work in American English against the same OSM tag.
CATEGORY_ALIASES: dict[str, str] = {
    "coffee": "cafe", "coffee shop": "cafe", "coffee shops": "cafe",
    "cafes": "cafe", "café": "cafe", "tea": "cafe",
    "restaurants": "restaurant", "food": "restaurant", "eat": "restaurant",
    "dinner": "restaurant", "lunch": "restaurant", "breakfast": "cafe",
    "pub": "bar", "pubs": "bar", "bars": "bar", "drinks": "bar",
    "hospitals": "hospital", "emergency": "hospital", "er": "hospital",
    "doctor": "clinic", "doctors": "clinic", "clinics": "clinic",
    "chemist": "pharmacy", "chemists": "pharmacy", "medical store": "pharmacy",
    "medicine": "pharmacy", "drugstore": "pharmacy", "pharmacies": "pharmacy",
    "petrol": "fuel", "petrol pump": "fuel", "gas": "fuel",
    "gas station": "fuel", "petrol station": "fuel", "fuel station": "fuel",
    "ev charging": "charging", "charger": "charging",
    "atms": "atm", "cash": "atm", "banks": "bank",
    "college": "university", "colleges": "university", "campus": "university",
    "schools": "school", "libraries": "library",
    "co-working": "coworking", "coworking space": "coworking",
    "work": "coworking", "workspace": "coworking",
    "parks": "park", "garden": "park", "gardens": "park",
    "gyms": "gym", "fitness": "gym",
    "grocery": "supermarket", "groceries": "supermarket",
    "market": "supermarket", "supermarkets": "supermarket",
    "shops": "shop", "store": "shop", "stores": "shop", "shopping": "shop",
    "hotels": "hotel", "stay": "hotel", "accommodation": "hotel",
    "museums": "museum", "attractions": "attraction",
    "sightseeing": "attraction",
    "metro": "transit", "train": "transit", "station": "transit",
    "train station": "transit", "subway": "transit",
    "bus stop": "bus", "airports": "airport",
    "parking lot": "parking", "car park": "parking",
    "restroom": "toilets", "bathroom": "toilets", "washroom": "toilets",
    "temple": "worship", "church": "worship", "mosque": "worship",
    "post office": "post", "vet": "veterinary",
}


def canonical_category(text: str) -> str:
    """Best canonical category for free text, or "" if it names none.

    Longest alias first, so "coffee shop" is not shadowed by "coffee" matching
    a different mapping, and substring matching so "find a good coffee shop"
    resolves without the caller stripping the sentence down first.
    """
    t = (text or "").strip().lower()
    if not t:
        return ""
    if t in CATEGORIES:
        return t
    if t in CATEGORY_ALIASES:
        return CATEGORY_ALIASES[t]
    for alias in sorted(CATEGORY_ALIASES, key=len, reverse=True):
        if alias in t:
            return CATEGORY_ALIASES[alias]
    for cat in sorted(CATEGORIES, key=len, reverse=True):
        if cat in t:
            return cat
    return ""
