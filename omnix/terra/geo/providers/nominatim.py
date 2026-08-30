"""Nominatim — OpenStreetMap's geocoder. Forward, reverse, street-level. No key.

This is the provider that can resolve an actual address, which Open-Meteo's
gazetteer cannot, so it leads the geocoding chain for anything that looks like
one.

It comes with the strictest obligations of any provider here, and they are met
in code rather than in a comment:

  * **Absolute maximum one request per second.** Enforced by the rate limiter's
    `nominatim` bucket (config.rate_limits), which blocks rather than rejects.
  * **A genuine identifying User-Agent.** Set globally in `providers.base`;
    Nominatim blocks clients that send a library default, and rightly.
  * **No bulk or systematic querying.** TERRA only ever calls this for a
    question a user asked, and the 30-day geocode TTL means asking twice costs
    one call.

`TERRA_NOMINATIM_URL` points at a self-hosted or commercial instance, which is
the correct answer for anything beyond personal use — the public endpoint is
donated infrastructure, not a free tier.
"""

from __future__ import annotations

from ..config import settings
from ..types import Coord, Place
from .base import get_json


class NominatimProvider:
    name = "nominatim"

    @property
    def base(self) -> str:
        return settings().keys.nominatim_url.rstrip("/")

    def available(self) -> bool:
        return bool(self.base) and not settings().offline

    def geocode(self, query: str, *, limit: int = 5,
                near: Coord | None = None) -> list[Place]:
        params: dict = {
            "q": query, "format": "jsonv2", "limit": max(1, min(limit, 10)),
            "addressdetails": 1, "extratags": 1,
        }
        if near is not None:
            # A viewbox biases results without excluding them: `bounded=0` means
            # "prefer near here", so "MG Road" finds the local one first but a
            # search for somewhere genuinely distant still succeeds. Bounding it
            # hard would make TERRA unable to look anything up outside the city
            # the user happens to be in.
            from ..spatial import bbox_around
            south, west, north, east = bbox_around(near, 25_000)
            params["viewbox"] = f"{west},{north},{east},{south}"
            params["bounded"] = 0
        data = get_json(f"{self.base}/search", params)
        return [p for p in (self._to_place(r) for r in (data or [])) if p]

    def reverse(self, coord: Coord) -> Place | None:
        data = get_json(f"{self.base}/reverse", {
            "lat": coord.lat, "lon": coord.lon, "format": "jsonv2",
            "addressdetails": 1, "zoom": 18,
        })
        if not data or "error" in data:
            return None
        return self._to_place(data)

    # -- shaping ------------------------------------------------------------
    def _to_place(self, r: dict) -> Place | None:
        try:
            lat, lon = float(r["lat"]), float(r["lon"])
        except (KeyError, TypeError, ValueError):
            return None
        addr = r.get("address") or {}
        # `name` is populated for POIs and empty for plain addresses, so fall
        # back through the address parts rather than showing the 200-character
        # display_name as a title.
        name = (r.get("name")
                or addr.get("amenity") or addr.get("shop")
                or addr.get("building")
                or _street_line(addr)
                or (r.get("display_name") or "").split(",")[0]
                or "Unnamed place")
        extra = r.get("extratags") or {}
        return Place(
            name=name,
            coord=Coord(lat, lon),
            category=r.get("type") or r.get("class") or "",
            address=r.get("display_name") or "",
            opening_hours=extra.get("opening_hours"),
            phone=extra.get("phone") or extra.get("contact:phone"),
            website=extra.get("website") or extra.get("contact:website"),
            wheelchair=extra.get("wheelchair"),
            external_id=f"{r.get('osm_type', '')}/{r.get('osm_id', '')}",
            source=self.name,
            tags={k: str(v) for k, v in (
                ("city", addr.get("city") or addr.get("town")
                 or addr.get("village")),
                ("state", addr.get("state")),
                ("country", addr.get("country")),
                ("countryCode", (addr.get("country_code") or "").upper()),
                ("postcode", addr.get("postcode")),
                ("suburb", addr.get("suburb") or addr.get("neighbourhood")),
            ) if v},
        )


def _street_line(addr: dict) -> str:
    house, road = addr.get("house_number"), addr.get("road")
    if house and road:
        return f"{house} {road}"
    return road or ""
