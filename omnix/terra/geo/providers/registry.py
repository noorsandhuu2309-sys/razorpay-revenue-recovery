"""Which provider answers which question, and in what order.

This is the file you edit to change vendors. Nothing else in TERRA names a
provider, so replacing Overpass with Foursquare, or putting a self-hosted OSRM
in front of everything, is a change to one list here.

**How the order is chosen.** Not by quality in the abstract — by cost per
answer at equal usefulness. A free provider that answers the question well goes
first, and a paid one goes first only where it is the ONLY provider that can
answer at all:

    geocode    nominatim -> openmeteo -> google
               Nominatim does street addresses; Open-Meteo only does populated
               places but is faster and never rate-limited, so it catches
               "Bengaluru" if Nominatim's bucket is saturated.

    reverse    bigdatacloud -> nominatim -> google
               Cheap locality answer first; the precise one behind it.

    places     overpass -> google
               OSM's amenity coverage is excellent and free. Google goes second
               EXCEPT when the caller explicitly needs ratings — see
               `places_chain(require_ratings=...)`, which is the one place a
               paid provider is deliberately promoted, because OSM has no
               ratings at all and no ordering of free data can substitute.

    route      osrm -> graphhopper -> google
               ...with one inversion: for a mode OSRM's endpoint cannot
               actually serve, it is dropped rather than allowed to answer
               wrongly. See `route_chain`.

    weather    openmeteo               (Google's Weather API is regional)
    air        openmeteo -> google
    elevation  openmeteo -> google

**Fallback is not retry.** `first_ok` moves to the next provider on failure,
skipping any whose health circuit is open, and returns the first real answer
with `attempted` recording who was passed over. A chain that exhausts itself
returns a `Result.offline`, never an exception.
"""

from __future__ import annotations

from typing import Any, Callable, TypeVar

from .. import cache
from ..config import settings
from ..types import Freshness, Mode, Result
from .bigdatacloud import BigDataCloudProvider
from .google import GoogleProvider
from .graphhopper import GraphHopperProvider
from .nominatim import NominatimProvider
from .openmeteo import OpenMeteoProvider
from .osrm import OSRMProvider
from .overpass import OverpassProvider

T = TypeVar("T")

# Singletons. They hold no per-request state — only configuration read through
# `settings()` — so one instance each is correct and avoids rebuilding them on
# every call.
OPENMETEO = OpenMeteoProvider()
NOMINATIM = NominatimProvider()
BIGDATACLOUD = BigDataCloudProvider()
OVERPASS = OverpassProvider()
OSRM = OSRMProvider()
GRAPHHOPPER = GraphHopperProvider()
GOOGLE = GoogleProvider()

ALL = {p.name: p for p in (OPENMETEO, NOMINATIM, BIGDATACLOUD, OVERPASS,
                           OSRM, GRAPHHOPPER, GOOGLE)}


def get(name: str) -> Any | None:
    return ALL.get(name)


# ---------------------------------------------------------------------------
# Chains
# ---------------------------------------------------------------------------
def geocode_chain() -> list[Any]:
    return [NOMINATIM, OPENMETEO, GOOGLE]


def reverse_chain() -> list[Any]:
    return [BIGDATACLOUD, NOMINATIM, GOOGLE]


def places_chain(*, require_ratings: bool = False) -> list[Any]:
    """Providers for POI search.

    `require_ratings` is the deliberate exception to free-first. A request that
    genuinely needs ratings — "the best-rated coffee nearby" — cannot be
    answered from OSM at any ranking effort, so Google is promoted rather than
    the caller being handed unrated results that quietly ignore the word
    "best". With no key configured the chain falls back to Overpass and the
    answer is honestly returned without ratings.
    """
    if require_ratings and GOOGLE.available():
        return [GOOGLE, OVERPASS]
    return [OVERPASS, GOOGLE]


def route_chain(mode: Mode = Mode.DRIVING) -> list[Any]:
    """Routers that can actually serve this mode.

    The filter matters more than the order. OSRM's public endpoint answers a
    `/walking/` request with car-profile results, so leaving it in the chain
    for a walking request produces a confident, wrong, 60 km/h "walk". Dropping
    a provider that cannot serve the mode is the difference between graceful
    degradation and silent corruption.
    """
    return [p for p in (OSRM, GRAPHHOPPER, GOOGLE)
            if p.available() and p.supports(mode)]


def weather_chain() -> list[Any]:
    return [OPENMETEO]


def air_quality_chain() -> list[Any]:
    return [OPENMETEO, GOOGLE]


def elevation_chain() -> list[Any]:
    return [OPENMETEO, GOOGLE]


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------
def first_ok(chain: list[Any], kind: str,
             call: Callable[[Any], T],
             key_parts: dict[str, Any],
             *, empty_is_miss: bool = True,
             ttl_s: float | None = None) -> Result:
    """Run a chain until one provider gives a usable answer.

    `empty_is_miss` is the subtle one. An empty list from Overpass means "no
    cafés within 2km", which is a real and correct answer — but an empty list
    from a geocoder means "I do not index street addresses", which is a miss
    that the next provider can fix. Callers say which they mean; places pass
    False, geocoding passes True.

    Each provider gets its own cache key, so a fallback answer is cached under
    the provider that produced it and a later request that reaches the healthy
    provider first still hits.
    """
    attempted: list[str] = []
    last_error = ""
    stale_fallback: Result | None = None

    for provider in chain:
        name = getattr(provider, "name", "?")
        if not provider.available():
            attempted.append(f"{name}:unavailable")
            continue
        if not cache.healthy(name):
            attempted.append(f"{name}:circuit-open")
            continue

        key = cache.key_for(kind, provider=name, **key_parts)
        result = cache.fetch(key, kind, name, lambda p=provider: call(p),
                             ttl_s=ttl_s)

        if result.ok:
            empty = result.data in (None, [], {})
            if empty and empty_is_miss:
                attempted.append(f"{name}:empty")
                continue
            # A stale answer is real, but a LIVE answer from the next provider
            # is better. Hold it and keep going; if nobody else answers, this
            # is what the caller gets — labelled stale, never as live.
            if result.freshness is Freshness.STALE and stale_fallback is None:
                stale_fallback = result
                attempted.append(f"{name}:stale")
                continue
            cache.mark_ok(name)
            result.attempted = attempted
            return result

        cache.mark_failed(name)
        last_error = result.error or last_error
        attempted.append(f"{name}:error")

    if stale_fallback is not None:
        stale_fallback.attempted = attempted
        return stale_fallback

    if settings().offline:
        return Result.offline("TERRA is in offline mode and nothing is cached",
                              attempted=attempted)
    return Result.offline(last_error or "no provider could answer",
                          attempted=attempted)


def status() -> dict:
    """Per-provider configuration, health and usage, for the UI's data panel.

    Never includes a credential — `configured` is a boolean derived from
    whether a key is non-empty, and nothing here can reach the key itself.
    """
    stats = cache.usage()["providers"]
    health = cache.health()
    return {
        name: {
            "available": provider.available(),
            "circuitOpen": health.get(name, {}).get("circuitOpen", False),
            "failures": health.get(name, {}).get("failures", 0),
            "usage": stats.get(name, {}),
        }
        for name, provider in ALL.items()
    }
