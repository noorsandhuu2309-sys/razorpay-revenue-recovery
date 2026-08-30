"""Google Maps Platform — Places, Routes, Geocoding, Elevation, Air Quality.

Five capabilities from one key, and genuinely better than the open stack at
three things TERRA cares about: **ratings and popularity** (which do not exist
in OSM at all), **traffic-aware ETAs** (which OSRM's static graph cannot
produce), and **address coverage** outside well-mapped areas.

It is also the only provider here that costs money per request, so this module
is written defensively about that in ways the free ones are not:

  * **Field masks on every Places and Routes call.** Google bills Places by
    SKU tier based on WHICH fields you ask for, so requesting the default set
    costs several times what requesting the needed set costs. The masks below
    are deliberately minimal and every field in them is one the UI renders.
  * **Never called speculatively.** `registry` places Google LAST in every
    chain except the two where it is uniquely better, so a keyed install still
    answers most questions for free.
  * **`available()` is false without a key**, so an unconfigured install never
    even builds a request.

Cost, at the published list prices as of this writing, for the SKUs used here:
Places Nearby (Pro) ~$32/1000, Routes (Basic) ~$5/1000, Geocoding ~$5/1000,
Elevation ~$5/1000, Air Quality ~$5/1000 — against a monthly free allowance.
With the TTLs in `config.DEFAULT_TTL` and the spatial key snapping in
`cache.spatial_key`, ordinary personal use stays inside that allowance; the
usage endpoint reports the real numbers rather than these estimates.
"""

from __future__ import annotations

from ..config import settings
from ..spatial import decode_polyline, simplify
from ..types import AirQuality, Coord, Mode, Place, Route, Step
from .base import get_json, post_json

GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
ELEVATION_URL = "https://maps.googleapis.com/maps/api/elevation/json"
PLACES_NEARBY_URL = "https://places.googleapis.com/v1/places:searchNearby"
PLACES_TEXT_URL = "https://places.googleapis.com/v1/places:searchText"
ROUTES_URL = ("https://routes.googleapis.com/directions/v2:computeRoutes")
AIR_URL = "https://airquality.googleapis.com/v1/currentConditions:lookup"

# Exactly the fields the UI shows. Adding one here changes the bill, so the
# list is short on purpose and anything speculative stays out.
PLACES_MASK = ",".join((
    "places.id", "places.displayName", "places.formattedAddress",
    "places.location", "places.types", "places.rating",
    "places.userRatingCount", "places.priceLevel",
    "places.currentOpeningHours.openNow",
    "places.regularOpeningHours.weekdayDescriptions",
    "places.nationalPhoneNumber", "places.websiteUri",
    "places.accessibilityOptions.wheelchairAccessibleEntrance",
))

ROUTES_MASK = ",".join((
    "routes.duration", "routes.staticDuration", "routes.distanceMeters",
    "routes.polyline.encodedPolyline", "routes.description",
    "routes.travelAdvisory.tollInfo",
    "routes.legs.steps.navigationInstruction",
    "routes.legs.steps.distanceMeters", "routes.legs.steps.staticDuration",
    "routes.legs.steps.startLocation",
))

_TRAVEL_MODE = {
    Mode.DRIVING: "DRIVE",
    Mode.WALKING: "WALK",
    Mode.CYCLING: "BICYCLE",
    Mode.TRANSIT: "TRANSIT",
}


class GoogleProvider:
    """One class for all five capabilities because they share one key and one
    availability rule. `registry` still registers it per capability, so it can
    be preferred for Places and skipped for geocoding independently."""

    name = "google"

    def available(self) -> bool:
        return settings().has_google()

    def _key(self) -> str:
        return settings().keys.google

    def supports(self, mode: Mode) -> bool:
        return mode in _TRAVEL_MODE

    # -- geocoding ----------------------------------------------------------
    def geocode(self, query: str, *, limit: int = 5,
                near: Coord | None = None) -> list[Place]:
        params: dict = {"address": query, "key": self._key()}
        if near is not None:
            # Bias, not restriction — same reasoning as Nominatim's viewbox.
            params["bounds"] = _bounds(near, 25_000)
        data = get_json(GEOCODE_URL, params)
        _raise_on_status(data)
        return [p for p in (self._geocode_place(r)
                            for r in (data.get("results") or [])[:limit]) if p]

    def reverse(self, coord: Coord) -> Place | None:
        data = get_json(GEOCODE_URL, {
            "latlng": f"{coord.lat},{coord.lon}", "key": self._key(),
        })
        _raise_on_status(data)
        results = data.get("results") or []
        return self._geocode_place(results[0]) if results else None

    def _geocode_place(self, r: dict) -> Place | None:
        loc = ((r.get("geometry") or {}).get("location") or {})
        lat, lon = loc.get("lat"), loc.get("lng")
        if lat is None or lon is None:
            return None
        types = r.get("types") or []
        return Place(
            name=_component(r, "point_of_interest")
                 or _component(r, "premise")
                 or (r.get("formatted_address") or "").split(",")[0],
            coord=Coord(float(lat), float(lon)),
            category=types[0] if types else "",
            address=r.get("formatted_address") or "",
            external_id=r.get("place_id") or "",
            source=self.name,
        )

    # -- places -------------------------------------------------------------
    def search_places(self, *, near: Coord, query: str = "",
                      category: str = "", radius_m: float = 2000,
                      limit: int = 20) -> list[Place]:
        from .base import CATEGORIES
        headers = {"X-Goog-Api-Key": self._key(),
                   "X-Goog-FieldMask": PLACES_MASK,
                   "Content-Type": "application/json"}
        bias = {"circle": {"center": {"latitude": near.lat,
                                      "longitude": near.lon},
                           "radius": float(max(1.0, min(radius_m, 50_000)))}}

        if query and not category:
            body: dict = {"textQuery": query, "locationBias": bias,
                          "maxResultCount": min(limit, 20)}
            data = post_json(PLACES_TEXT_URL, body, headers=headers)
        else:
            types = CATEGORIES.get(category, {}).get("google") or []
            body = {"locationRestriction": bias,
                    "maxResultCount": min(limit, 20),
                    # By distance, not by Google's relevance. TERRA's own
                    # ranking in core.places blends distance, rating and
                    # opening hours with weights the user can see; taking
                    # Google's opaque order first would make that ranking a
                    # re-shuffle of an unknown one.
                    "rankPreference": "DISTANCE"}
            if types:
                body["includedTypes"] = types
            data = post_json(PLACES_NEARBY_URL, body, headers=headers)

        return [p for p in (self._place(r) for r in (data.get("places") or []))
                if p][:limit]

    def _place(self, r: dict) -> Place | None:
        loc = r.get("location") or {}
        lat, lon = loc.get("latitude"), loc.get("longitude")
        if lat is None or lon is None:
            return None
        types = r.get("types") or []
        hours = r.get("regularOpeningHours") or {}
        access = r.get("accessibilityOptions") or {}
        wheelchair = access.get("wheelchairAccessibleEntrance")
        return Place(
            name=(r.get("displayName") or {}).get("text") or "Unnamed place",
            coord=Coord(float(lat), float(lon)),
            category=types[0] if types else "",
            address=r.get("formattedAddress") or "",
            opening_hours="; ".join(hours.get("weekdayDescriptions") or []) or None,
            open_now=(r.get("currentOpeningHours") or {}).get("openNow"),
            rating=r.get("rating"),
            rating_count=r.get("userRatingCount"),
            price_level=_price_level(r.get("priceLevel")),
            phone=r.get("nationalPhoneNumber"),
            website=r.get("websiteUri"),
            wheelchair=("yes" if wheelchair else "no") if wheelchair is not None
                       else None,
            external_id=r.get("id") or "",
            source=self.name,
            tags={"googleTypes": ",".join(types[:4])} if types else {},
        )

    # -- routing ------------------------------------------------------------
    def route(self, origin: Coord, destination: Coord, *,
              mode: Mode = Mode.DRIVING, alternatives: int = 1,
              steps: bool = True) -> list[Route]:
        travel = _TRAVEL_MODE.get(mode, "DRIVE")
        body: dict = {
            "origin": {"location": {"latLng": {"latitude": origin.lat,
                                               "longitude": origin.lon}}},
            "destination": {"location": {"latLng": {"latitude": destination.lat,
                                                    "longitude": destination.lon}}},
            "travelMode": travel,
            "computeAlternativeRoutes": alternatives > 1,
            "polylineQuality": "OVERVIEW",
        }
        if travel == "DRIVE":
            # The reason to pay for Google at all: a duration that knows about
            # traffic. Only valid for DRIVE — sending it with WALK is a 400.
            body["routingPreference"] = "TRAFFIC_AWARE"
        data = post_json(ROUTES_URL, body, headers={
            "X-Goog-Api-Key": self._key(),
            "X-Goog-FieldMask": ROUTES_MASK,
            "Content-Type": "application/json",
        })

        out: list[Route] = []
        for r in (data.get("routes") or [])[:max(1, alternatives)]:
            encoded = ((r.get("polyline") or {}).get("encodedPolyline") or "")
            geometry = decode_polyline(encoded, precision=5)
            duration = _seconds(r.get("duration"))
            static = _seconds(r.get("staticDuration"))
            toll = (r.get("travelAdvisory") or {}).get("tollInfo")
            out.append(Route(
                distance_m=float(r.get("distanceMeters") or 0.0),
                # `staticDuration` is free-flow and `duration` includes
                # traffic. Reporting them in the right slots is what lets the
                # UI say "8 min slower than usual" instead of showing one
                # number with no baseline.
                duration_s=static or duration,
                duration_traffic_s=duration if travel == "DRIVE" else None,
                geometry=simplify(geometry, tolerance_m=8.0),
                steps=_google_steps(r) if steps else [],
                summary=r.get("description") or "",
                mode=mode,
                tolls=bool(toll) if toll is not None else None,
                source=self.name,
            ))
        return out

    # -- elevation ----------------------------------------------------------
    def elevation(self, coords: list[Coord]) -> list[float]:
        if not coords:
            return []
        out: list[float] = []
        # 512 locations per request is Google's documented ceiling.
        for start in range(0, len(coords), 300):
            chunk = coords[start:start + 300]
            data = get_json(ELEVATION_URL, {
                "locations": "|".join(f"{c.lat:.6f},{c.lon:.6f}" for c in chunk),
                "key": self._key(),
            })
            _raise_on_status(data)
            out.extend(float(r.get("elevation") or 0.0)
                       for r in (data.get("results") or []))
        return out

    # -- air quality --------------------------------------------------------
    def air_quality(self, coord: Coord) -> AirQuality:
        data = post_json(f"{AIR_URL}?key={self._key()}", {
            "location": {"latitude": coord.lat, "longitude": coord.lon},
            "extraComputations": ["POLLUTANT_CONCENTRATION",
                                  "DOMINANT_POLLUTANT_CONCENTRATION"],
        })
        indexes = data.get("indexes") or []
        # Google returns a universal AQI plus, where available, the local
        # regulatory one. The local index is the number people around the user
        # actually see quoted, so it wins when present.
        chosen = next((i for i in indexes if i.get("code") != "uaqi"),
                      indexes[0] if indexes else {})
        pollutants = {p.get("code"): p for p in (data.get("pollutants") or [])}

        def conc(code: str) -> float | None:
            p = pollutants.get(code) or {}
            return (p.get("concentration") or {}).get("value")

        return AirQuality(
            index=chosen.get("aqi"),
            scale=chosen.get("code") or "",
            band=(chosen.get("category") or "").replace(" air quality", "").lower(),
            pm2_5=conc("pm25"), pm10=conc("pm10"), ozone=conc("o3"),
            no2=conc("no2"), so2=conc("so2"), co=conc("co"),
            dominant=(pollutants.get(chosen.get("dominantPollutant") or "", {})
                      .get("displayName") or ""),
            source=self.name,
        )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _raise_on_status(data: dict) -> None:
    """Turn Google's in-body error status into an exception.

    The legacy Maps APIs answer 200 OK with `{"status": "REQUEST_DENIED"}`, so
    `raise_for_status` sees success and the caller gets an empty result list.
    That failure mode is indistinguishable from "nothing found" — a bad key
    would silently read as an empty world. ZERO_RESULTS genuinely is an empty
    world and is allowed through.
    """
    status = (data or {}).get("status")
    if status and status not in ("OK", "ZERO_RESULTS"):
        raise RuntimeError(f"google: {status} "
                           f"{(data or {}).get('error_message', '')}".strip())


def _seconds(value: str | None) -> float:
    """Google durations are protobuf strings like "1234s"."""
    if not value:
        return 0.0
    try:
        return float(str(value).rstrip("s"))
    except ValueError:
        return 0.0


def _price_level(value: str | None) -> int | None:
    return {"PRICE_LEVEL_FREE": 0, "PRICE_LEVEL_INEXPENSIVE": 1,
            "PRICE_LEVEL_MODERATE": 2, "PRICE_LEVEL_EXPENSIVE": 3,
            "PRICE_LEVEL_VERY_EXPENSIVE": 4}.get(value or "")


def _component(result: dict, kind: str) -> str:
    for c in (result.get("address_components") or []):
        if kind in (c.get("types") or []):
            return c.get("long_name") or ""
    return ""


def _bounds(centre: Coord, radius_m: float) -> str:
    from ..spatial import bbox_around
    south, west, north, east = bbox_around(centre, radius_m)
    return f"{south},{west}|{north},{east}"


def _google_steps(route: dict) -> list[Step]:
    out: list[Step] = []
    for leg in (route.get("legs") or []):
        for s in (leg.get("steps") or []):
            nav = s.get("navigationInstruction") or {}
            latlng = ((s.get("startLocation") or {}).get("latLng") or {})
            lat, lon = latlng.get("latitude"), latlng.get("longitude")
            out.append(Step(
                instruction=nav.get("instructions") or nav.get("maneuver") or "",
                distance_m=float(s.get("distanceMeters") or 0.0),
                duration_s=_seconds(s.get("staticDuration")),
                coord=Coord(float(lat), float(lon)) if lat is not None else None,
            ))
    return out
