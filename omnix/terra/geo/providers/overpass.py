"""Overpass — POI search over raw OpenStreetMap data. No key.

This is TERRA's default answer to "what is around me", and it is genuinely good
at it: OSM's amenity coverage in cities is excellent, the data is free of
per-request charges, and the tags carry things Google will not sell you at all
— wheelchair access, exact opening-hours expressions, whether the café has
power sockets.

What it cannot do is ratings and popularity. Those do not exist in OSM. Rather
than fabricate a proxy, `Place.rating` is left None and the ranking in
`core.places` is built to produce a sensible order without it — which is also
what makes the Google path an upgrade rather than a different product.

Overpass is a shared, donated, CPU-bound service. The obligations are real:

  * The query is bounded by a radius, always, and capped by `[out:json]
    [timeout:N]` so a runaway query dies on their side rather than pinning a
    core.
  * `qt` sorting and an explicit `out` limit keep responses small.
  * The rate bucket is 0.5 req/s and the results cache for 6 hours.
"""

from __future__ import annotations

from ..config import settings
from ..types import Coord, Place
from .base import CATEGORIES, client


class OverpassProvider:
    name = "overpass"

    @property
    def url(self) -> str:
        return settings().keys.overpass_url

    def available(self) -> bool:
        return bool(self.url) and not settings().offline

    def search_places(self, *, near: Coord, query: str = "",
                      category: str = "", radius_m: float = 2000,
                      limit: int = 20) -> list[Place]:
        ql = self._build_query(near, query, category, radius_m, limit)
        # POST, not GET: Overpass QL for several tags exceeds what is
        # comfortable in a query string, and their servers prefer it.
        resp = client().post(self.url, data={"data": ql},
                             headers={"Accept": "application/json"})
        resp.raise_for_status()
        elements = (resp.json() or {}).get("elements") or []
        places = [p for p in (self._to_place(e) for e in elements) if p]

        # Flag a capped response so the caller knows the set is a spatially
        # biased sample rather than everything in the radius. Carried on the
        # first place's tags because `search_places` returns a plain list —
        # the alternative was a second return value on every provider in the
        # protocol, for a condition only this one can report.
        cap = int(min(max(limit * 6, 200), 400))
        if len(elements) >= cap and places:
            places[0].tags = dict(places[0].tags or {})
            places[0].tags["_truncated"] = str(cap)

        # Everything parsed is returned — NOT trimmed to `limit` here.
        #
        # Trimming in the provider re-introduced the exact bug the raised cap
        # was meant to fix, one layer up: the list is still in quadtile order,
        # so `places[:limit]` keeps a spatial corner and throws away the
        # nearest results before `core.places` has had a chance to measure the
        # distances. Ranking must see every candidate; `limit` is applied after
        # sorting, where it means what the caller intended.
        return places

    # -- query construction -------------------------------------------------
    def _build_query(self, near: Coord, query: str, category: str,
                     radius_m: float, limit: int) -> str:
        radius = int(max(50, min(radius_m, 50_000)))
        around = f"(around:{radius},{near.lat:.6f},{near.lon:.6f})"

        selectors: list[str] = []
        if category and category in CATEGORIES:
            for tag in CATEGORIES[category]["osm"]:
                if "=" in tag:
                    key, value = tag.split("=", 1)
                    selectors.append(f'["{key}"="{value}"]')
                else:
                    selectors.append(f'["{tag}"]')
        elif query:
            # Free-text against the name tag. Case-insensitive regex, with the
            # user's text escaped — an unescaped '(' or '[' from a search box
            # is a syntax error that returns 400 for the whole request, and a
            # crafted one is an injection into the query language.
            safe = _escape_regex(query)
            selectors.append(f'["name"~"{safe}",i]')
        else:
            # No category and no text: the useful default is "notable things",
            # not "every node in a 2km circle", which is tens of thousands of
            # street lamps and postboxes.
            selectors.append('["amenity"]')

        # nwr = nodes, ways and relations. A hospital is usually a way (a
        # building outline) and a bus stop is always a node; querying only
        # nodes silently omits most large POIs, which reads as "OSM has no
        # hospitals here".
        body = "".join(f"  nwr{sel}{around};\n" for sel in selectors)

        # The element cap, and why it is generous rather than tight.
        #
        # `out ... qt N` truncates in QUADTILE order, which is spatial — so
        # hitting the cap does not drop a random sample, it drops one region of
        # the search area, and that region can easily be the one containing the
        # nearest result. Measured in central Bengaluru: a 5km pharmacy search
        # capped at 60 returned nothing closer than 693m while a 1km search
        # found one at 360m.
        #
        # `core.places.nearest` solves this properly by searching outward in
        # rings. For an ordinary browse the cap is simply raised so that
        # typical radii do not reach it at all, and `_truncated` below tells
        # the caller when one did.
        cap = int(min(max(limit * 6, 200), 400))
        return (f"[out:json][timeout:25];\n(\n{body});\n"
                f"out center tags qt {cap};")

    # -- shaping ------------------------------------------------------------
    def _to_place(self, el: dict) -> Place | None:
        tags = el.get("tags") or {}
        # Ways and relations have no lat/lon of their own; `out center` adds a
        # `center` object. Reading only lat/lon drops every building-shaped
        # POI, which is most of the interesting ones.
        lat = el.get("lat", (el.get("center") or {}).get("lat"))
        lon = el.get("lon", (el.get("center") or {}).get("lon"))
        if lat is None or lon is None:
            return None

        name = tags.get("name") or tags.get("brand") or ""
        if not name:
            # An unnamed node is real data but useless in a list. Label it by
            # what it is rather than dropping it — "Pharmacy" beats a blank row
            # and beats hiding a pharmacy that is genuinely there.
            kind = (tags.get("amenity") or tags.get("shop")
                    or tags.get("tourism") or tags.get("leisure") or "")
            if not kind:
                return None
            name = kind.replace("_", " ").title()

        addr = ", ".join(p for p in (
            " ".join(x for x in (tags.get("addr:housenumber"),
                                 tags.get("addr:street")) if x),
            tags.get("addr:suburb"), tags.get("addr:city"),
            tags.get("addr:postcode"),
        ) if p)

        return Place(
            name=name,
            coord=Coord(float(lat), float(lon)),
            category=(tags.get("amenity") or tags.get("shop")
                      or tags.get("tourism") or tags.get("leisure")
                      or tags.get("office") or ""),
            address=addr,
            opening_hours=tags.get("opening_hours"),
            phone=tags.get("phone") or tags.get("contact:phone"),
            website=tags.get("website") or tags.get("contact:website"),
            wheelchair=tags.get("wheelchair"),
            external_id=f"{el.get('type', 'node')}/{el.get('id', '')}",
            source=self.name,
            # A curated subset, not the whole tag soup. These are the ones
            # `core.places` ranks on and the UI shows; passing 60 raw OSM tags
            # to the frontend and the LLM is noise that costs tokens.
            tags={k: v for k, v in tags.items() if k in _KEPT_TAGS},
        )


_KEPT_TAGS = frozenset((
    "cuisine", "brand", "operator", "internet_access", "outdoor_seating",
    "takeaway", "delivery", "drive_through", "air_conditioning", "smoking",
    "wheelchair", "toilets", "capacity", "fee", "opening_hours:covid19",
    "diet:vegetarian", "diet:vegan", "level", "indoor",
))


def _escape_regex(text: str) -> str:
    """Neutralise regex metacharacters and quotes for embedding in Overpass QL.

    Two layers of escaping matter here because there are two layers of syntax:
    the double quote would terminate the QL string literal, and the
    metacharacters would change what the regex matches. Both are handled, and
    the length is capped so a pasted paragraph cannot become a pathological
    pattern on someone else's CPU.
    """
    out = []
    for ch in text.strip()[:80]:
        if ch in '\\"':
            out.append("\\" + ch)
        elif ch in ".^$*+?()[]{}|/":
            out.append("\\" + ch)
        else:
            out.append(ch)
    return "".join(out)
