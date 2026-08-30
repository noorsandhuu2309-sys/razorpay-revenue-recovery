"""Free, key-less weather via Open-Meteo.

Open-Meteo needs no API key and has a generous free tier. We use its geocoding
endpoint to turn a city name into coordinates, then its forecast endpoint for
current conditions. WMO weather codes are mapped to a short label + emoji.
"""

from __future__ import annotations

from urllib.parse import quote_plus

import httpx

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# WMO weather interpretation codes -> (emoji, label).
_WMO: dict[int, tuple[str, str]] = {
    0: ("☀️", "Clear sky"),
    1: ("🌤️", "Mainly clear"),
    2: ("⛅", "Partly cloudy"),
    3: ("☁️", "Overcast"),
    45: ("🌫️", "Fog"),
    48: ("🌫️", "Rime fog"),
    51: ("🌦️", "Light drizzle"),
    53: ("🌦️", "Drizzle"),
    55: ("🌧️", "Dense drizzle"),
    56: ("🌧️", "Freezing drizzle"),
    57: ("🌧️", "Freezing drizzle"),
    61: ("🌦️", "Light rain"),
    63: ("🌧️", "Rain"),
    65: ("🌧️", "Heavy rain"),
    66: ("🌧️", "Freezing rain"),
    67: ("🌧️", "Freezing rain"),
    71: ("🌨️", "Light snow"),
    73: ("🌨️", "Snow"),
    75: ("❄️", "Heavy snow"),
    77: ("🌨️", "Snow grains"),
    80: ("🌦️", "Rain showers"),
    81: ("🌧️", "Rain showers"),
    82: ("⛈️", "Violent showers"),
    85: ("🌨️", "Snow showers"),
    86: ("❄️", "Snow showers"),
    95: ("⛈️", "Thunderstorm"),
    96: ("⛈️", "Thunderstorm + hail"),
    99: ("⛈️", "Thunderstorm + hail"),
}


def _describe(code: int) -> tuple[str, str]:
    return _WMO.get(int(code), ("🌡️", "Unknown"))


def geocode(city: str, timeout: float = 8.0) -> dict | None:
    """Resolve a city name to {name, country, lat, lon} or None."""
    resp = httpx.get(
        GEOCODE_URL,
        params={"name": city, "count": 1, "language": "en", "format": "json"},
        timeout=timeout,
    )
    resp.raise_for_status()
    results = resp.json().get("results") or []
    if not results:
        return None
    r = results[0]
    return {
        "name": r.get("name", city),
        "country": r.get("country", ""),
        "lat": r.get("latitude"),
        "lon": r.get("longitude"),
        "timezone": r.get("timezone", "auto"),
    }


def by_coords(lat: float, lon: float, name: str = "", timeout: float = 8.0) -> dict:
    """Current weather at coordinates. Returns a UI-ready dict."""
    resp = httpx.get(
        FORECAST_URL,
        params={
            "latitude": lat,
            "longitude": lon,
            "current": "temperature_2m,relative_humidity_2m,apparent_temperature,"
                       "weather_code,wind_speed_10m,is_day",
            "timezone": "auto",
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()
    cur = data.get("current", {})
    code = cur.get("weather_code", 0)
    emoji, label = _describe(code)
    return {
        "status": "success",
        "name": name,
        "temperature": round(cur.get("temperature_2m", 0)),
        "feels_like": round(cur.get("apparent_temperature", 0)),
        "humidity": cur.get("relative_humidity_2m"),
        "wind": cur.get("wind_speed_10m"),
        "is_day": bool(cur.get("is_day", 1)),
        "code": code,
        "emoji": emoji,
        "description": label,
        # Timezone info lets the UI show a live-ticking local clock for the spot.
        "timezone": data.get("timezone", ""),
        "timezone_abbr": data.get("timezone_abbreviation", ""),
        "utc_offset": data.get("utc_offset_seconds", 0),
        "lat": lat,
        "lon": lon,
    }


def get_weather(city: str | None = None, lat: float | None = None,
                lon: float | None = None) -> dict:
    """Weather by city name or explicit coordinates. Never raises."""
    try:
        if lat is not None and lon is not None:
            return by_coords(float(lat), float(lon), name=city or "Current location")
        if city:
            place = geocode(city)
            if not place:
                return {"status": "error", "error": f"Could not find '{city}'"}
            label = place["name"]
            if place.get("country"):
                label = f"{place['name']}, {place['country']}"
            return by_coords(place["lat"], place["lon"], name=label)
        return {"status": "error", "error": "Provide a city or coordinates"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


if __name__ == "__main__":
    import json

    print(json.dumps(get_weather(city="Bengaluru"), indent=2, ensure_ascii=False))
