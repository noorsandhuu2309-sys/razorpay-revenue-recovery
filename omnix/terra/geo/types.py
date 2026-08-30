"""The vocabulary of TERRA's geospatial layer.

Everything crossing a provider boundary is normalised into these shapes before
anything else sees it. That is the whole point of the layer: a Place from
Overpass and a Place from Google Places differ in every field name and half
their semantics, and if that difference reaches the reasoning engine then
swapping providers means rewriting the reasoning engine.

One field deserves explanation, because it is the honesty rule of this
subsystem made into a type.

`Freshness` travels on every payload that leaves a provider. TERRA is designed
to keep working when the network is down, which means it will happily serve a
route it computed an hour ago — and a routing answer that is quietly an hour
old is worse than no answer, because the user cannot tell. So nothing in this
package returns a bare value. It returns a value plus how much that value
should be trusted as *current*, and the UI is built to render the difference.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Freshness(str, Enum):
    """How current a piece of geographic information actually is.

    LIVE       fetched from a provider during this request
    CACHED     served from TERRA's cache, still inside its TTL
    STALE      served from cache PAST its TTL, because the provider failed.
               This is the one that must never be shown as live.
    ESTIMATED  computed locally rather than fetched — a straight-line distance
               standing in for a road route, a sunrise from an almanac formula
    OFFLINE    nothing was available; the payload is empty and `error` says why
    """

    LIVE = "live"
    CACHED = "cached"
    STALE = "stale"
    ESTIMATED = "estimated"
    OFFLINE = "offline"


class Mode(str, Enum):
    """Travel modes. Providers that cannot serve one say so rather than
    silently substituting another — an OSRM instance built with the car profile
    answering a walking request with driving times is a wrong ETA, not a
    graceful degradation."""

    DRIVING = "driving"
    WALKING = "walking"
    CYCLING = "cycling"
    TRANSIT = "transit"


@dataclass(frozen=True)
class Coord:
    """A WGS84 point. Frozen because coordinates are identity here — they are
    used as cache keys, and a mutable cache key is a bug waiting to happen."""

    lat: float
    lon: float

    def __post_init__(self) -> None:
        if not (-90.0 <= self.lat <= 90.0):
            raise ValueError(f"latitude out of range: {self.lat}")
        if not (-180.0 <= self.lon <= 180.0):
            raise ValueError(f"longitude out of range: {self.lon}")

    @property
    def valid(self) -> bool:
        return not (math.isnan(self.lat) or math.isnan(self.lon))

    def rounded(self, places: int = 4) -> "Coord":
        """Snap to a grid for cache keying.

        4 decimal places is ~11m. Two requests from the same room should share
        a cached weather lookup; without this every GPS jitter of a few metres
        is a cache miss and a paid API call. Weather and air quality use a
        coarser grid still — see `cache.spatial_key`.
        """
        return Coord(round(self.lat, places), round(self.lon, places))

    def as_dict(self) -> dict[str, float]:
        return {"lat": self.lat, "lon": self.lon}

    def __str__(self) -> str:
        return f"{self.lat:.5f},{self.lon:.5f}"


@dataclass
class Place:
    """A location that means something — a POI, a geocoding result, a saved
    place. The union of what the providers can tell us, with `None` meaning
    "not known" and never a placeholder value.

    `source` names the provider so the UI can attribute it, which several
    provider licences require and all of them deserve.
    """

    name: str
    coord: Coord
    category: str = ""
    address: str = ""
    distance_m: float | None = None
    #: Straight-line vs along a route. A "500m away" that is across a river is
    #: a lie of omission, so which one this is gets recorded.
    distance_kind: str = "straight"
    opening_hours: str | None = None
    open_now: bool | None = None
    rating: float | None = None
    rating_count: int | None = None
    price_level: int | None = None
    phone: str | None = None
    website: str | None = None
    wheelchair: str | None = None
    tags: dict[str, str] = field(default_factory=dict)
    external_id: str = ""
    source: str = ""

    def as_dict(self) -> dict[str, Any]:
        # Spelled out rather than `asdict()` so the keys are camelCase like
        # every other type here. The generated version emitted `distance_m` and
        # `opening_hours` into a payload where Route and Weather emit
        # `distanceM` and `openingHours`, which left the frontend guessing per
        # field which convention applied.
        return {
            "name": self.name,
            "coord": self.coord.as_dict(),
            "lat": self.coord.lat, "lon": self.coord.lon,
            "category": self.category,
            "address": self.address,
            "distanceM": self.distance_m,
            "distanceKind": self.distance_kind,
            "openingHours": self.opening_hours,
            "openNow": self.open_now,
            "rating": self.rating,
            "ratingCount": self.rating_count,
            "priceLevel": self.price_level,
            "phone": self.phone,
            "website": self.website,
            "wheelchair": self.wheelchair,
            "tags": self.tags,
            "externalId": self.external_id,
            "source": self.source,
        }


@dataclass
class Step:
    """One turn-by-turn instruction. Optional everywhere: OSRM's default
    profile returns them, Overpass has no concept of them, and a route with no
    steps is still a useful route."""

    instruction: str
    distance_m: float
    duration_s: float
    coord: Coord | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "instruction": self.instruction,
            "distanceM": self.distance_m,
            "durationS": self.duration_s,
            "coord": self.coord.as_dict() if self.coord else None,
        }


@dataclass
class Route:
    """One option for getting from A to B.

    `geometry` is a list of points, already decoded. Providers hand back
    polylines in three different encodings and the frontend should not have to
    know which one it is looking at.

    `score` and `score_parts` are filled in by `intelligence.route_scoring`,
    not by the provider — the provider reports facts, the scorer applies
    preferences, and keeping those separable is what lets preferences be
    user-configurable.
    """

    distance_m: float
    duration_s: float
    geometry: list[Coord] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)
    summary: str = ""
    mode: Mode = Mode.DRIVING
    #: Duration including live traffic, when the provider models it. None means
    #: "not modelled", which is different from "no traffic".
    duration_traffic_s: float | None = None
    tolls: bool | None = None
    source: str = ""
    score: float | None = None
    score_parts: dict[str, float] = field(default_factory=dict)

    @property
    def distance_km(self) -> float:
        return self.distance_m / 1000.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "distanceM": self.distance_m,
            "distanceKm": round(self.distance_km, 2),
            "durationS": self.duration_s,
            "durationTrafficS": self.duration_traffic_s,
            "geometry": [[c.lat, c.lon] for c in self.geometry],
            "steps": [s.as_dict() for s in self.steps],
            "summary": self.summary,
            "mode": self.mode.value,
            "tolls": self.tolls,
            "source": self.source,
            "score": self.score,
            "scoreParts": self.score_parts,
        }


@dataclass
class Weather:
    """Current conditions. Every field optional because provider coverage
    genuinely differs and a fabricated zero reads as real data."""

    temperature_c: float | None = None
    feels_like_c: float | None = None
    humidity_pct: float | None = None
    precipitation_mm: float | None = None
    precipitation_probability_pct: float | None = None
    wind_kph: float | None = None
    wind_direction_deg: float | None = None
    uv_index: float | None = None
    cloud_cover_pct: float | None = None
    visibility_m: float | None = None
    code: int | None = None
    description: str = ""
    emoji: str = ""
    is_day: bool | None = None
    sunrise: str | None = None
    sunset: str | None = None
    timezone: str = ""
    #: Offset at the QUERIED coordinate, not at the server. Carried because it
    #: is the only reliable way to compute local sun times for somewhere the
    #: server is not — see `core.environment.sun_times`.
    utc_offset_s: int | None = None
    source: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "temperatureC": self.temperature_c,
            "feelsLikeC": self.feels_like_c,
            "humidityPct": self.humidity_pct,
            "precipitationMm": self.precipitation_mm,
            "precipitationProbabilityPct": self.precipitation_probability_pct,
            "windKph": self.wind_kph,
            "windDirectionDeg": self.wind_direction_deg,
            "uvIndex": self.uv_index,
            "cloudCoverPct": self.cloud_cover_pct,
            "visibilityM": self.visibility_m,
            "code": self.code,
            "description": self.description,
            "emoji": self.emoji,
            "isDay": self.is_day,
            "sunrise": self.sunrise,
            "sunset": self.sunset,
            "timezone": self.timezone,
            "utcOffsetS": self.utc_offset_s,
            "source": self.source,
        }


@dataclass
class AirQuality:
    """Air quality, normalised to a band the reasoning layer can act on.

    `index` is deliberately NOT called "AQI": providers disagree about which
    scale they mean (US EPA vs European EAQI vs a raw concentration), and a
    number whose scale is unknown cannot be compared or reasoned over. `scale`
    names it, and `band` is the interpretation — that is what the LLM should
    read, not the number.
    """

    index: float | None = None
    scale: str = ""
    band: str = ""
    pm2_5: float | None = None
    pm10: float | None = None
    ozone: float | None = None
    no2: float | None = None
    so2: float | None = None
    co: float | None = None
    dominant: str = ""
    source: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index, "scale": self.scale, "band": self.band,
            "pm25": self.pm2_5, "pm10": self.pm10, "ozone": self.ozone,
            "no2": self.no2, "so2": self.so2, "co": self.co,
            "dominant": self.dominant, "source": self.source,
        }


@dataclass
class Result:
    """A provider answer plus everything needed to judge it.

    Nothing in this package returns a bare payload. The three things a caller
    always needs — is it current, who said so, and did it fail — travel with
    the data rather than in a side channel, because a side channel is a thing
    a caller can forget to check.
    """

    data: Any
    freshness: Freshness = Freshness.LIVE
    provider: str = ""
    #: Seconds since the underlying fetch. Only meaningful when cached/stale.
    age_s: float | None = None
    error: str = ""
    #: Providers tried and rejected, in order. This is what makes a fallback
    #: chain debuggable instead of a mystery.
    attempted: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.data is not None and self.freshness is not Freshness.OFFLINE

    @property
    def live(self) -> bool:
        return self.freshness is Freshness.LIVE

    def as_dict(self, key: str = "data") -> dict[str, Any]:
        payload = self.data
        if hasattr(payload, "as_dict"):
            payload = payload.as_dict()
        elif isinstance(payload, list):
            payload = [p.as_dict() if hasattr(p, "as_dict") else p for p in payload]
        return {
            key: payload,
            "freshness": self.freshness.value,
            "provider": self.provider,
            "ageS": round(self.age_s, 1) if self.age_s is not None else None,
            "error": self.error,
            "attempted": self.attempted,
        }

    @classmethod
    def offline(cls, error: str, attempted: list[str] | None = None,
                data: Any = None) -> "Result":
        return cls(data=data, freshness=Freshness.OFFLINE, error=error,
                   attempted=attempted or [])
