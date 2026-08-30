"""Open-Meteo — geocoding, weather, air quality and elevation. No key.

Four of TERRA's seven capabilities from one keyless vendor, which is why this
is the default everywhere. Open-Meteo's free tier is generous, it publishes no
per-request charge, and — the thing that matters most for a system that must
work on a fresh clone — it needs no signup at all.

It is also the vendor OMNIX already used: `omnix/tools/weather.py` and
`omnix/tools/geo.py` call these same endpoints. Those two modules are left
alone and still serve `/api/weather` and `/api/geo/*`; this is the same data
behind the caching, rate-limiting and fallback machinery the rest of TERRA's
spatial layer runs on.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ..types import AirQuality, Coord, Place, Weather
from .base import get_json

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
AIR_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
ELEVATION_URL = "https://api.open-meteo.com/v1/elevation"

# WMO weather interpretation codes -> (emoji, label). Kept in sync with
# omnix/tools/weather.py deliberately: two different labels for code 61 in one
# product is the kind of inconsistency users notice and cannot explain.
_WMO: dict[int, tuple[str, str]] = {
    0: ("☀️", "Clear sky"), 1: ("🌤️", "Mainly clear"), 2: ("⛅", "Partly cloudy"),
    3: ("☁️", "Overcast"), 45: ("🌫️", "Fog"), 48: ("🌫️", "Rime fog"),
    51: ("🌦️", "Light drizzle"), 53: ("🌦️", "Drizzle"),
    55: ("🌧️", "Dense drizzle"), 56: ("🌧️", "Freezing drizzle"),
    57: ("🌧️", "Freezing drizzle"), 61: ("🌦️", "Light rain"),
    63: ("🌧️", "Rain"), 65: ("🌧️", "Heavy rain"),
    66: ("🌧️", "Freezing rain"), 67: ("🌧️", "Freezing rain"),
    71: ("🌨️", "Light snow"), 73: ("🌨️", "Snow"), 75: ("❄️", "Heavy snow"),
    77: ("🌨️", "Snow grains"), 80: ("🌦️", "Rain showers"),
    81: ("🌧️", "Rain showers"), 82: ("⛈️", "Violent showers"),
    85: ("🌨️", "Snow showers"), 86: ("❄️", "Snow showers"),
    95: ("⛈️", "Thunderstorm"), 96: ("⛈️", "Thunderstorm + hail"),
    99: ("⛈️", "Thunderstorm + hail"),
}


class OpenMeteoProvider:
    name = "openmeteo"

    def available(self) -> bool:
        from ..config import settings
        return not settings().offline

    # -- geocoding ----------------------------------------------------------
    def geocode(self, query: str, *, limit: int = 5,
                near: Coord | None = None) -> list[Place]:
        """Place-name search. Cities and towns, not street addresses.

        This is the honest limit of the provider and the reason Nominatim sits
        beside it in the chain: Open-Meteo's index is a populated-places
        gazetteer, so "Bengaluru" resolves instantly and "14 MG Road" resolves
        to nothing at all. `core.geocoding` reads an empty list as a miss and
        moves on rather than reporting failure.
        """
        data = get_json(GEOCODE_URL, {
            "name": query, "count": max(1, min(limit, 10)),
            "language": "en", "format": "json",
        })
        out: list[Place] = []
        for r in (data.get("results") or []):
            lat, lon = r.get("latitude"), r.get("longitude")
            if lat is None or lon is None:
                continue
            parts = [p for p in (r.get("name"), r.get("admin1"), r.get("country"))
                     if p]
            out.append(Place(
                name=r.get("name") or query,
                coord=Coord(float(lat), float(lon)),
                category="place",
                address=", ".join(parts),
                external_id=str(r.get("id") or ""),
                source=self.name,
                tags={k: str(v) for k, v in (
                    ("country", r.get("country")),
                    ("countryCode", r.get("country_code")),
                    ("admin1", r.get("admin1")),
                    ("timezone", r.get("timezone")),
                    ("population", r.get("population")),
                    ("elevation", r.get("elevation")),
                ) if v is not None},
            ))
        return out

    def reverse(self, coord: Coord) -> Place | None:
        """Not supported. Open-Meteo's gazetteer is forward-only.

        Returning None rather than omitting the method keeps the provider a
        structural `GeocodeProvider`, so the chain can hold it for forward
        lookups without a second registry for half-implementations.
        """
        return None

    # -- weather ------------------------------------------------------------
    def weather(self, coord: Coord) -> Weather:
        data = get_json(FORECAST_URL, {
            "latitude": coord.lat, "longitude": coord.lon,
            "current": ("temperature_2m,relative_humidity_2m,apparent_temperature,"
                        "precipitation,weather_code,cloud_cover,visibility,"
                        "wind_speed_10m,wind_direction_10m,is_day"),
            # Hourly is requested for exactly two fields the `current` block
            # does not carry: rain probability and UV. "Should I go for a run"
            # is unanswerable without both, and one call for all of it beats
            # three.
            "hourly": "precipitation_probability,uv_index",
            "daily": "sunrise,sunset,uv_index_max",
            "forecast_days": 1,
            "timezone": "auto",
        })
        cur = data.get("current") or {}
        code = int(cur.get("weather_code") or 0)
        emoji, label = _WMO.get(code, ("🌡️", "Unknown"))

        # Pick the hour we are actually in, not hourly[0]. With timezone=auto
        # the series starts at midnight *at the queried location*, so index 0
        # is the small hours — reading UV from it reports 0 at noon, which
        # looks like "no sun" rather than "wrong index".
        #
        # The hour must come from the TARGET's clock, not the server's. Using
        # the server's local time works by accident when the user asks about
        # where they are and is silently wrong the moment they ask about
        # anywhere else — checking the weather in London from Bengaluru would
        # read the UV for a time 5½ hours off. `utc_offset_seconds` comes back
        # in the response precisely so this can be done right.
        pop = uv = None
        hourly = data.get("hourly") or {}
        times = hourly.get("time") or []
        if times:
            offset = int(data.get("utc_offset_seconds") or 0)
            local_now = datetime.now(timezone.utc) + timedelta(seconds=offset)
            stamp = local_now.strftime("%Y-%m-%dT%H:00")
            idx = (times.index(stamp) if stamp in times
                   else min(len(times) - 1, local_now.hour))
            probs = hourly.get("precipitation_probability") or []
            uvs = hourly.get("uv_index") or []
            if idx < len(probs):
                pop = probs[idx]
            if idx < len(uvs):
                uv = uvs[idx]

        daily = data.get("daily") or {}
        sunrise = (daily.get("sunrise") or [None])[0]
        sunset = (daily.get("sunset") or [None])[0]

        wind_ms = cur.get("wind_speed_10m")
        return Weather(
            temperature_c=cur.get("temperature_2m"),
            feels_like_c=cur.get("apparent_temperature"),
            humidity_pct=cur.get("relative_humidity_2m"),
            precipitation_mm=cur.get("precipitation"),
            precipitation_probability_pct=pop,
            # Open-Meteo reports km/h by default for wind_speed_10m.
            wind_kph=wind_ms,
            wind_direction_deg=cur.get("wind_direction_10m"),
            uv_index=uv,
            cloud_cover_pct=cur.get("cloud_cover"),
            visibility_m=cur.get("visibility"),
            code=code, description=label, emoji=emoji,
            is_day=bool(cur.get("is_day", 1)),
            sunrise=sunrise, sunset=sunset,
            timezone=data.get("timezone") or "",
            utc_offset_s=int(data.get("utc_offset_seconds") or 0),
            source=self.name,
        )

    # -- air quality --------------------------------------------------------
    def air_quality(self, coord: Coord) -> AirQuality:
        data = get_json(AIR_URL, {
            "latitude": coord.lat, "longitude": coord.lon,
            "current": ("european_aqi,us_aqi,pm10,pm2_5,carbon_monoxide,"
                        "nitrogen_dioxide,sulphur_dioxide,ozone"),
            "timezone": "auto",
        })
        cur = data.get("current") or {}
        # US AQI preferred when present because its bands are the ones most
        # people have seen; `scale` records which one was actually used so the
        # number is never compared against a differently-scaled one.
        index, scale = cur.get("us_aqi"), "us_aqi"
        if index is None:
            index, scale = cur.get("european_aqi"), "european_aqi"
        return AirQuality(
            index=index, scale=scale if index is not None else "",
            band=_aqi_band(index, scale),
            pm2_5=cur.get("pm2_5"), pm10=cur.get("pm10"),
            ozone=cur.get("ozone"), no2=cur.get("nitrogen_dioxide"),
            so2=cur.get("sulphur_dioxide"), co=cur.get("carbon_monoxide"),
            dominant=_dominant(cur), source=self.name,
        )

    # -- hourly forecast ----------------------------------------------------
    def hourly(self, coord: Coord, hours: int = 24) -> dict:
        """The next `hours` of conditions, for the forecast strip.

        Two days are requested and then sliced from "now", because a single
        `forecast_days=1` run ends at local midnight — ask at 22:00 and you get
        a two-hour strip. Slicing a two-day series gives a full 24 hours at any
        time of day for the same one request.
        """
        data = get_json(FORECAST_URL, {
            "latitude": coord.lat, "longitude": coord.lon,
            "hourly": ("temperature_2m,precipitation_probability,precipitation,"
                       "weather_code,wind_speed_10m,uv_index,relative_humidity_2m"),
            "forecast_days": 2,
            "timezone": "auto",
        })
        h = data.get("hourly") or {}
        times = h.get("time") or []
        offset = int(data.get("utc_offset_seconds") or 0)
        local_now = datetime.now(timezone.utc) + timedelta(seconds=offset)
        stamp = local_now.strftime("%Y-%m-%dT%H:00")
        start = times.index(stamp) if stamp in times else 0

        out: list[dict] = []
        for i in range(start, min(start + hours, len(times))):
            code = int((h.get("weather_code") or [0] * len(times))[i] or 0)
            emoji, label = _WMO.get(code, ("🌡️", "Unknown"))
            out.append({
                "time": times[i],
                "hour": int(times[i][11:13]),
                "temperatureC": _at(h, "temperature_2m", i),
                "precipitationProbabilityPct": _at(h, "precipitation_probability", i),
                "precipitationMm": _at(h, "precipitation", i),
                "windKph": _at(h, "wind_speed_10m", i),
                "uvIndex": _at(h, "uv_index", i),
                "humidityPct": _at(h, "relative_humidity_2m", i),
                "code": code, "emoji": emoji, "description": label,
            })
        return {"hours": out, "timezone": data.get("timezone") or "",
                "utcOffsetS": offset, "source": self.name}

    # -- air quality over a grid -------------------------------------------
    def air_quality_grid(self, coords: list[Coord]) -> list[AirQuality]:
        """Air quality at many points in ONE request.

        Open-Meteo accepts comma-separated coordinate lists and answers with a
        JSON ARRAY, one object per point — which is what makes a real
        air-quality overlay affordable. A 5x5 grid over a city is one API call,
        not twenty-five; doing it per point would have made the overlay the
        single most expensive thing in TERRA and it would have been switched
        off by default and never used.

        Order is preserved, so the caller can zip the results back onto the
        coordinates it sent.
        """
        if not coords:
            return []
        out: list[AirQuality] = []
        # 100 per request keeps the URL comfortably inside every proxy's limit.
        for start in range(0, len(coords), 100):
            chunk = coords[start:start + 100]
            data = get_json(AIR_URL, {
                "latitude": ",".join(f"{c.lat:.4f}" for c in chunk),
                "longitude": ",".join(f"{c.lon:.4f}" for c in chunk),
                "current": "us_aqi,european_aqi,pm2_5,pm10,ozone",
            })
            # A single-point request returns an object, not a list. Normalising
            # here means callers never branch on how many points they asked for.
            rows = data if isinstance(data, list) else [data]
            for row in rows:
                cur = (row or {}).get("current") or {}
                index, scale = cur.get("us_aqi"), "us_aqi"
                if index is None:
                    index, scale = cur.get("european_aqi"), "european_aqi"
                out.append(AirQuality(
                    index=index, scale=scale if index is not None else "",
                    band=_aqi_band(index, scale),
                    pm2_5=cur.get("pm2_5"), pm10=cur.get("pm10"),
                    ozone=cur.get("ozone"), source=self.name,
                ))
        return out

    # -- elevation ----------------------------------------------------------
    def elevation(self, coords: list[Coord]) -> list[float]:
        """Batched: one request for up to 100 points.

        Open-Meteo accepts comma-separated coordinate lists, so a route's
        elevation profile is a single call rather than one per point. Batching
        here is worth more than caching — a 300-point profile is 3 requests
        instead of 300.
        """
        if not coords:
            return []
        out: list[float] = []
        for start in range(0, len(coords), 100):
            chunk = coords[start:start + 100]
            data = get_json(ELEVATION_URL, {
                "latitude": ",".join(f"{c.lat:.6f}" for c in chunk),
                "longitude": ",".join(f"{c.lon:.6f}" for c in chunk),
            })
            out.extend(float(e) for e in (data.get("elevation") or []))
        return out


def _at(series: dict, key: str, i: int):
    """One value from an Open-Meteo hourly series, or None.

    The arrays are parallel to `time` but a requested variable can be missing
    entirely for a location, in which case the key is absent rather than a list
    of nulls — indexing it directly raises where a missing reading should just
    be unknown.
    """
    values = series.get(key)
    if not isinstance(values, list) or i >= len(values):
        return None
    return values[i]


def _aqi_band(index: float | None, scale: str) -> str:
    """The word for a number, which is what the reasoning layer should read.

    Two scales with different breakpoints, kept apart on purpose: EAQI 55 is
    "poor" and US AQI 55 is "moderate", and collapsing them into one ladder
    would make TERRA confidently wrong about whether to go outside.
    """
    if index is None:
        return ""
    v = float(index)
    if scale == "us_aqi":
        for limit, band in ((50, "good"), (100, "moderate"),
                            (150, "unhealthy for sensitive groups"),
                            (200, "unhealthy"), (300, "very unhealthy")):
            if v <= limit:
                return band
        return "hazardous"
    for limit, band in ((20, "good"), (40, "fair"), (60, "moderate"),
                        (80, "poor"), (100, "very poor")):
        if v <= limit:
            return band
    return "extremely poor"


def _dominant(cur: dict) -> str:
    """Which pollutant is driving the reading.

    Concentrations are compared against WHO 24-hour guideline values rather
    than against each other — 40 µg/m³ of PM2.5 and 40 of ozone are not
    comparably bad, and ranking the raw numbers would name the wrong culprit
    almost every time.
    """
    guidelines = {"pm2_5": 15.0, "pm10": 45.0, "ozone": 100.0,
                  "nitrogen_dioxide": 25.0, "sulphur_dioxide": 40.0}
    labels = {"pm2_5": "PM2.5", "pm10": "PM10", "ozone": "ozone",
              "nitrogen_dioxide": "NO₂", "sulphur_dioxide": "SO₂"}
    worst, worst_ratio = "", 0.0
    for key, guideline in guidelines.items():
        value = cur.get(key)
        if value is None:
            continue
        ratio = float(value) / guideline
        if ratio > worst_ratio:
            worst, worst_ratio = labels[key], ratio
    return worst if worst_ratio >= 1.0 else ""
