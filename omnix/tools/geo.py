"""Geocoding for the TERRA map — forward (name→places) and reverse (coords→place).

Forward search reuses Open-Meteo's geocoding API (keyless, same provider as
weather). Reverse geocoding uses BigDataCloud's free client endpoint (keyless).
Both are called server-side so the browser never needs a third-party key and we
avoid CORS surprises. Never raise — callers get {status:'error', ...} instead.
"""

from __future__ import annotations

import httpx

SEARCH_URL = "https://geocoding-api.open-meteo.com/v1/search"
REVERSE_URL = "https://api.bigdatacloud.net/data/reverse-geocode-client"


def search(query: str, count: int = 6, timeout: float = 8.0) -> dict:
    """Resolve a place name to up to `count` candidate locations."""
    try:
        resp = httpx.get(SEARCH_URL, params={
            "name": query, "count": max(1, min(count, 10)),
            "language": "en", "format": "json",
        }, timeout=timeout)
        resp.raise_for_status()
        results = resp.json().get("results") or []
        out = []
        for r in results:
            out.append({
                "name": r.get("name", ""),
                "country": r.get("country", ""),
                "country_code": r.get("country_code", ""),
                "admin1": r.get("admin1", ""),
                "lat": r.get("latitude"),
                "lon": r.get("longitude"),
                "timezone": r.get("timezone", ""),
                "population": r.get("population"),
                "elevation": r.get("elevation"),
            })
        return {"status": "success", "results": out}
    except Exception as e:
        return {"status": "error", "results": [], "error": str(e)}


def reverse(lat: float, lon: float, timeout: float = 8.0) -> dict:
    """Resolve coordinates to the nearest named place (city / region / country)."""
    try:
        resp = httpx.get(REVERSE_URL, params={
            "latitude": lat, "longitude": lon, "localityLanguage": "en",
        }, timeout=timeout, headers={"User-Agent": "OMNIX-TERRA/1.0"})
        resp.raise_for_status()
        d = resp.json()
        city = (d.get("city") or d.get("locality")
                or d.get("principalSubdivision") or "")
        country = d.get("countryName") or ""
        label = ", ".join([p for p in (city, country) if p]) or "Open water"
        return {
            "status": "success",
            "name": city or country or "Unknown",
            "label": label,
            "admin1": d.get("principalSubdivision", ""),
            "country": country,
            "country_code": d.get("countryCode", ""),
            "lat": lat, "lon": lon,
        }
    except Exception as e:
        # Over open ocean or offline — still return something usable.
        return {"status": "error", "name": "Unknown", "label": "Unknown location",
                "country": "", "lat": lat, "lon": lon, "error": str(e)}
