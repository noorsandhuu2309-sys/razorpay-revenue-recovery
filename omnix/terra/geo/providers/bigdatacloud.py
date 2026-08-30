"""BigDataCloud — reverse geocoding. No key, no rate limit worth worrying about.

Narrow on purpose: one capability, one endpoint. It is here because it is the
provider OMNIX already used (`omnix/tools/geo.py`) and because it answers the
one question Nominatim is slowest at under a hard 1 req/s budget — "roughly
where is this point" — with locality-level detail that is plenty for a status
line.

So the reverse-geocoding chain is BigDataCloud first for the cheap common case
and Nominatim behind it when a street-level answer is genuinely needed. It also
answers over open ocean, where Nominatim returns an error object.
"""

from __future__ import annotations

from ..config import settings
from ..types import Coord, Place
from .base import get_json

URL = "https://api.bigdatacloud.net/data/reverse-geocode-client"


class BigDataCloudProvider:
    name = "bigdatacloud"

    def available(self) -> bool:
        return not settings().offline

    def geocode(self, query: str, *, limit: int = 5,
                near: Coord | None = None) -> list[Place]:
        """Not supported — this provider is reverse-only."""
        return []

    def reverse(self, coord: Coord) -> Place | None:
        d = get_json(URL, {"latitude": coord.lat, "longitude": coord.lon,
                           "localityLanguage": "en"})
        if not d:
            return None
        city = (d.get("city") or d.get("locality")
                or d.get("principalSubdivision") or "")
        country = d.get("countryName") or ""
        # Empty everything means open water, which is a real answer rather than
        # a failure — a map click in the Pacific should say so, not error.
        label = ", ".join(p for p in (city, country) if p) or "Open water"
        return Place(
            name=city or country or "Open water",
            coord=coord,
            category="locality",
            address=label,
            source=self.name,
            tags={k: str(v) for k, v in (
                ("city", city),
                ("state", d.get("principalSubdivision")),
                ("country", country),
                ("countryCode", d.get("countryCode")),
                ("timezone", (d.get("localityInfo") or {}).get("timezone")),
            ) if v},
        )
