"""USGS FDSN event service adapter.

Endpoint and parameter names are taken from the service's own documentation at
https://earthquake.usgs.gov/fdsnws/event/1/ and were verified against a live
response before this file was written — §82 forbids coding against an API whose
shape has been assumed.

WHY THE PARSER IS DEFENSIVE ABOUT `properties`
----------------------------------------------
The live response carries `mag: null` for some events, `place: null` for others,
and a `time` in **milliseconds** since the epoch rather than seconds. Each of
those, taken at face value, produces a plausible wrong answer: a magnitude of
0.0, an empty location, or a timestamp in 1970. A parser that coerces silently
is how a system ends up confidently reporting a magnitude-0 earthquake off the
coast of nowhere, so missing values stay `None` and the caller decides.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import httpx

BASE_URL = "https://earthquake.usgs.gov/fdsnws/event/1/query"

# The service documents a hard ceiling of 20000 events for a single query and
# returns HTTP 400 above it. Clamping here turns a provider error into a
# well-formed request.
MAX_LIMIT = 500


class UsgsError(RuntimeError):
    """The service could not be reached, or answered something unusable."""


@dataclass(frozen=True)
class Quake:
    id: str
    magnitude: float | None
    magnitude_type: str
    place: str
    time: float | None          # epoch seconds, UTC
    latitude: float | None
    longitude: float | None
    depth_km: float | None
    tsunami: bool
    felt_reports: int | None
    alert: str
    url: str
    raw: dict = field(default_factory=dict, repr=False)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "magnitude": self.magnitude,
            "magnitudeType": self.magnitude_type,
            "place": self.place,
            "time": self.time,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "depthKm": self.depth_km,
            "tsunami": self.tsunami,
            "feltReports": self.felt_reports,
            "alert": self.alert,
            "url": self.url,
        }


def _num(value: Any) -> float | None:
    """A number, or None. Never 0.0 as a stand-in for 'not reported'."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_feature(feature: dict) -> Quake | None:
    """One GeoJSON feature into a `Quake`, or None if it is unusable."""
    if not isinstance(feature, dict):
        return None
    props = feature.get("properties") or {}
    geom = feature.get("geometry") or {}
    coords = geom.get("coordinates") or []
    if not isinstance(coords, list):
        coords = []

    event_id = feature.get("id")
    if not event_id:
        # Without an id there is no way to dedupe or link the event, and an
        # event that cannot be cited is not evidence.
        return None

    # USGS reports epoch MILLISECONDS. Treating it as seconds dates every
    # earthquake to January 1970, which looks like a data bug rather than a
    # unit bug and is correspondingly hard to notice.
    raw_time = _num(props.get("time"))
    when = raw_time / 1000.0 if raw_time is not None else None

    return Quake(
        id=str(event_id),
        magnitude=_num(props.get("mag")),
        magnitude_type=str(props.get("magType") or ""),
        place=str(props.get("place") or ""),
        time=when,
        longitude=_num(coords[0]) if len(coords) > 0 else None,
        latitude=_num(coords[1]) if len(coords) > 1 else None,
        depth_km=_num(coords[2]) if len(coords) > 2 else None,
        tsunami=bool(props.get("tsunami")),
        felt_reports=int(props["felt"]) if isinstance(props.get("felt"), (int, float)) else None,
        alert=str(props.get("alert") or ""),
        url=str(props.get("url") or ""),
        raw=props,
    )


def parse_collection(payload: dict) -> list[Quake]:
    if not isinstance(payload, dict):
        raise UsgsError("response was not a JSON object")
    if payload.get("type") != "FeatureCollection":
        raise UsgsError(
            f"expected a GeoJSON FeatureCollection, got {payload.get('type')!r} "
            "— the provider's response shape may have changed")
    features = payload.get("features")
    if not isinstance(features, list):
        raise UsgsError("response had no 'features' list")
    return [q for q in (parse_feature(f) for f in features) if q is not None]


class UsgsAdapter:
    """Thin, typed access to the FDSN event service."""

    def __init__(self, *, timeout_s: float = 20.0,
                 client: httpx.Client | None = None):
        self.timeout_s = timeout_s
        self._client = client

    def _get(self, params: dict) -> dict:
        query = {"format": "geojson", **params}
        try:
            if self._client is not None:
                resp = self._client.get(BASE_URL, params=query,
                                        timeout=self.timeout_s)
            else:
                resp = httpx.get(BASE_URL, params=query, timeout=self.timeout_s,
                                 headers={"User-Agent": "OMNIX/1.0 (research tool)"})
        except httpx.TimeoutException as e:
            raise UsgsError(
                f"USGS did not respond within {self.timeout_s:.0f}s") from e
        except httpx.HTTPError as e:
            raise UsgsError(f"could not reach USGS: {e}") from e

        if resp.status_code == 400:
            # FDSN returns 400 with a plain-text explanation for bad parameters.
            raise UsgsError(
                f"USGS rejected the query: {resp.text.strip()[:200]}")
        if resp.status_code == 429:
            raise UsgsError("USGS rate-limited this request; try again shortly")
        if resp.status_code >= 500:
            raise UsgsError(f"USGS service error (HTTP {resp.status_code})")
        if resp.status_code != 200:
            raise UsgsError(f"unexpected HTTP {resp.status_code} from USGS")

        try:
            return resp.json()
        except ValueError as e:
            raise UsgsError("USGS returned a body that was not JSON") from e

    def health_check(self) -> tuple[bool, str]:
        """Cheapest possible real query. Returns (ok, detail)."""
        try:
            self._get({"limit": 1, "orderby": "time"})
        except UsgsError as e:
            return False, str(e)
        return True, ""

    def search(self, *, min_magnitude: float | None = None,
               starttime: float | None = None, endtime: float | None = None,
               latitude: float | None = None, longitude: float | None = None,
               radius_km: float | None = None, limit: int = 50) -> list[Quake]:
        params: dict[str, Any] = {
            "orderby": "time",
            "limit": max(1, min(MAX_LIMIT, int(limit))),
        }
        if min_magnitude is not None:
            params["minmagnitude"] = float(min_magnitude)
        if starttime is not None:
            params["starttime"] = _iso(starttime)
        if endtime is not None:
            params["endtime"] = _iso(endtime)
        if latitude is not None and longitude is not None:
            params["latitude"] = float(latitude)
            params["longitude"] = float(longitude)
            # FDSN takes a radius in degrees or kilometres; the km parameter is
            # explicit and avoids a silent 111x error.
            params["maxradiuskm"] = float(radius_km if radius_km else 500.0)
        return parse_collection(self._get(params))


def _iso(epoch_s: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(epoch_s))
