"""Geographic overlays for the map — the "Google Earth for intelligence" layers.

Two kinds, and the difference is worth stating because it changes how much the
user should trust a marker:

  MEASURED layers come from instrument networks and official feeds. An
  earthquake marker is at the epicenter USGS computed, to three decimals. These
  are ground truth.

  DERIVED layers are placed by TERRA from news geocoding — a cyber-incident
  marker sits at the centroid of the country the story is about, not at the
  server that was breached. They are honest about what they are (`precision:
  "country"`), and the UI draws them differently so a reader never mistakes a
  derived marker for a measured one.

Every layer returns the same envelope so the client renders them uniformly:

    {key, label, glyph, color, kind, precision, points: [...], source, error?}

Upstreams are all keyless: USGS for seismicity, GDACS for multi-hazard disaster
alerts. Wildfire perimeters (NASA FIRMS) and flight/AIS tracking need keys or
commercial licences, so the corresponding layers are derived from news rather
than pretending to have sensor data — and shipping routes / chokepoints come
from the ontology, which is real static geography.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from xml.etree import ElementTree as ET

import httpx

from . import ontology as onto

UA = "OMNIX-TERRA/1.0"

USGS_URL = ("https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/"
            "2.5_day.geojson")
GDACS_URL = "https://www.gdacs.org/xml/rss.xml"

LAYERS = {
    "earthquakes": {"label": "Earthquakes", "glyph": "◎", "color": "#ff9a62",
                    "kind": "measured", "precision": "epicenter",
                    "source": "USGS (M2.5+, last 24h)"},
    "disasters":   {"label": "Disaster alerts", "glyph": "▲", "color": "#ff5d7a",
                    "kind": "measured", "precision": "event",
                    "source": "GDACS multi-hazard"},
    "conflict":    {"label": "Armed conflict", "glyph": "⬢", "color": "#ff3355",
                    "kind": "derived", "precision": "country",
                    "source": "TERRA corpus (military domain)"},
    "wildfires":   {"label": "Wildfires", "glyph": "🔥", "color": "#f0b429",
                    "kind": "derived", "precision": "country",
                    "source": "TERRA corpus (fire reporting)"},
    "floods":      {"label": "Floods & storms", "glyph": "≈", "color": "#57d7ff",
                    "kind": "derived", "precision": "country",
                    "source": "TERRA corpus (flood/storm reporting)"},
    "elections":   {"label": "Elections & politics", "glyph": "⬒", "color": "#9d8cff",
                    "kind": "derived", "precision": "country",
                    "source": "TERRA corpus (electoral reporting)"},
    "cyber":       {"label": "Cyber incidents", "glyph": "⬡", "color": "#a78bfa",
                    "kind": "derived", "precision": "country",
                    "source": "TERRA corpus (cyber domain)"},
    "outages":     {"label": "Infrastructure & outages", "glyph": "⊘", "color": "#94a3b8",
                    "kind": "derived", "precision": "country",
                    "source": "TERRA corpus (outage reporting)"},
    "health":      {"label": "Health emergencies", "glyph": "✚", "color": "#fb7185",
                    "kind": "derived", "precision": "country",
                    "source": "TERRA corpus (health domain)"},
    "chokepoints": {"label": "Shipping chokepoints", "glyph": "⬗", "color": "#22d3ee",
                    "kind": "static", "precision": "exact",
                    "source": "TERRA ontology"},
    "facilities":  {"label": "Strategic facilities", "glyph": "⬣", "color": "#fbbf24",
                    "kind": "static", "precision": "exact",
                    "source": "TERRA ontology"},
}

# Term sets for the news-derived layers. Kept separate from risk.py's term
# weights on purpose: those score intensity, these decide membership.
_DERIVED_TERMS = {
    "conflict": ("airstrike", "airstrikes", "shelling", "offensive", "troops",
                 "missile", "drone strike", "militants", "insurgents",
                 "ceasefire", "frontline", "invasion", "combat", "gunmen",
                 "clashes", "artillery", "warplanes", "rebels"),
    "wildfires": ("wildfire", "wildfires", "bushfire", "forest fire",
                  "blaze", "firefighters", "burned acres", "fire season"),
    "floods": ("flood", "floods", "flooding", "storm", "hurricane", "typhoon",
               "cyclone", "monsoon", "torrential", "landslide", "mudslide",
               "storm surge", "tornado"),
    "elections": ("election", "elections", "ballot", "vote count", "polls",
                  "referendum", "parliament", "coalition", "impeachment",
                  "inaugurated", "sworn in", "coup", "protests", "resign"),
    "cyber": ("cyberattack", "cyber attack", "ransomware", "data breach",
              "hackers", "hacked", "malware", "spyware", "ddos", "phishing"),
    "outages": ("power outage", "blackout", "internet shutdown",
                "internet outage", "grid failure", "pipeline shut",
                "port closed", "airport closed", "rail disruption",
                "supply disruption", "service disruption"),
    "health": ("outbreak", "epidemic", "pandemic", "cholera", "measles",
               "ebola", "quarantine", "infections", "who declares",
               "health emergency", "contamination"),
}


def _get(url: str, timeout: float = 12.0):
    try:
        r = httpx.get(url, timeout=timeout, follow_redirects=True,
                      headers={"User-Agent": UA})
        r.raise_for_status()
        return r
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Measured layers
# ---------------------------------------------------------------------------
def earthquakes() -> dict:
    spec = LAYERS["earthquakes"]
    resp = _get(USGS_URL)
    if resp is None:
        return {**spec, "key": "earthquakes", "points": [],
                "error": "USGS feed unreachable"}
    try:
        data = resp.json()
    except Exception:
        return {**spec, "key": "earthquakes", "points": [],
                "error": "USGS feed unparseable"}

    points = []
    for feat in (data.get("features") or [])[:250]:
        props = feat.get("properties") or {}
        coords = (feat.get("geometry") or {}).get("coordinates") or []
        if len(coords) < 2:
            continue
        mag = props.get("mag")
        if mag is None:
            continue
        points.append({
            "lat": coords[1], "lon": coords[0],
            "depth_km": coords[2] if len(coords) > 2 else None,
            "magnitude": round(float(mag), 1),
            "label": props.get("place", ""),
            "url": props.get("url", ""),
            "ts": (props.get("time") or 0) / 1000.0,
            "tsunami": bool(props.get("tsunami")),
            # Marker weight: M5 is a different object from M2.6 and the map
            # should say so without the user reading each label.
            "weight": round(min(1.0, max(0.1, (float(mag) - 2.0) / 5.0)), 2),
        })
    points.sort(key=lambda p: -p["magnitude"])
    return {**spec, "key": "earthquakes", "points": points,
            "count": len(points)}


_GDACS_TYPES = {
    "EQ": ("Earthquake", "◎"), "TC": ("Tropical cyclone", "🌀"),
    "FL": ("Flood", "≈"), "VO": ("Volcano", "▲"), "DR": ("Drought", "☀"),
    "WF": ("Wildfire", "🔥"), "TS": ("Tsunami", "≋"),
}
_GDACS_ALERT = {"green": 0.3, "orange": 0.65, "red": 1.0}


def _rfc822(value: str) -> float:
    from .store import parse_published
    return parse_published(value)


def disasters() -> dict:
    spec = LAYERS["disasters"]
    resp = _get(GDACS_URL, timeout=15.0)
    if resp is None:
        return {**spec, "key": "disasters", "points": [],
                "error": "GDACS feed unreachable"}
    try:
        root = ET.fromstring(resp.content)
    except Exception:
        return {**spec, "key": "disasters", "points": [],
                "error": "GDACS feed unparseable"}

    points = []
    for item in root.iter():
        if item.tag.rsplit("}", 1)[-1].lower() != "item":
            continue
        rec: dict = {}
        # Descendants, not direct children: GDACS puts the coordinates inside a
        # nested <geo:Point> wrapper, so a flat scan of the item's children
        # finds every field EXCEPT the two that decide whether a marker can be
        # placed at all — and the layer silently comes back empty.
        for child in item.iter():
            tag = child.tag.rsplit("}", 1)[-1].lower()
            text = (child.text or "").strip()
            if not text:
                continue
            if tag in ("title", "link", "pubdate", "description"):
                rec.setdefault(tag, text)
            elif tag == "lat":
                rec.setdefault("_lat", text)
            elif tag == "long":
                rec.setdefault("_lon", text)
            elif tag == "point":
                rec.setdefault("_point", text)
            elif tag == "eventtype":
                rec.setdefault("_type", text)
            elif tag == "alertlevel":
                rec.setdefault("_alert", text.lower())
            elif tag == "country":
                rec.setdefault("_country", text)
            elif tag == "severity":
                rec.setdefault("_severity", text)
            elif tag == "population":
                rec.setdefault("_population", text)
        lat = lon = None
        if rec.get("_lat") and rec.get("_lon"):
            try:
                lat, lon = float(rec["_lat"]), float(rec["_lon"])
            except (TypeError, ValueError):
                lat = lon = None
        if lat is None and rec.get("_point") and " " in rec["_point"]:
            try:
                parts = rec["_point"].split()
                lat, lon = float(parts[0]), float(parts[1])
            except (TypeError, ValueError, IndexError):
                lat = lon = None
        if lat is None or lon is None:
            continue
        kind, glyph = _GDACS_TYPES.get(rec.get("_type", ""),
                                       ("Hazard", "▲"))
        alert = rec.get("_alert", "green")
        iso = onto.iso_for((rec.get("_country") or "").split(",")[0])
        points.append({
            "lat": lat, "lon": lon,
            "label": rec.get("title", "")[:160],
            "url": rec.get("link", ""),
            "hazard": kind, "glyph": glyph,
            "alert": alert,
            "severity": rec.get("_severity", "")[:120],
            "population": rec.get("_population", "")[:80],
            "country": rec.get("_country", "")[:80],
            "iso2": iso,
            "ts": _rfc822(rec.get("pubdate", "")),
            "weight": _GDACS_ALERT.get(alert, 0.3),
        })
    points.sort(key=lambda p: -p["weight"])
    return {**spec, "key": "disasters", "points": points[:200],
            "count": len(points)}


# ---------------------------------------------------------------------------
# Static layers, straight from the ontology
# ---------------------------------------------------------------------------
def _static_layer(key: str, sectors: tuple[str, ...]) -> dict:
    spec = LAYERS[key]
    points = []
    for name, meta in onto.SEED_INFRASTRUCTURE.items():
        if meta.get("lat") is None:
            continue
        sector = meta.get("sector", "")
        if not any(s in sector for s in sectors):
            continue
        points.append({
            "lat": meta["lat"], "lon": meta["lon"],
            "label": name, "sector": sector,
            "id": f"infrastructure:{name.lower().replace(' ', '-')}",
            "weight": 0.8,
        })
    return {**spec, "key": key, "points": points, "count": len(points)}


def chokepoints() -> dict:
    return _static_layer("chokepoints", ("chokepoint", "waterway", "lane"))


def facilities() -> dict:
    return _static_layer("facilities", ("nuclear", "pipeline", "port",
                                        "semiconductor"))


# ---------------------------------------------------------------------------
# Derived layers — placed from the news corpus
# ---------------------------------------------------------------------------
def derived(key: str, articles: list[dict], hours: float = 48.0) -> dict:
    """Aggregate matching articles to country centroids.

    One marker per country, not per article: fifty stories about the same war
    should be one heavy marker, not fifty overlapping ones. Marker weight is the
    count, and the articles ride along so clicking it shows the evidence.
    """
    spec = LAYERS[key]
    terms = _DERIVED_TERMS.get(key, ())
    cutoff = time.time() - hours * 3600
    by_country: dict[str, dict] = {}

    for art in articles:
        if art.get("published_ts", 0) < cutoff:
            continue
        hay = " " + (art.get("title", "") + " " +
                     art.get("summary", "")).lower() + " "
        matched = [t for t in terms if t in hay]
        # Domain tags corroborate keyword matching: an article the classifier
        # already put in the cyber domain needs only one keyword, one that
        # didn't needs two.
        domain_hint = {"conflict": "military", "cyber": "cyber",
                       "health": "health", "wildfires": "climate",
                       "floods": "climate"}.get(key)
        need = 1 if (domain_hint and domain_hint in (art.get("domains") or [])) else 2
        if len(matched) < need:
            continue
        for iso in (art.get("countries") or [])[:3]:
            point = onto.country_point(iso)
            if not point:
                continue
            rec = by_country.setdefault(iso, {
                "lat": point[0], "lon": point[1], "iso2": iso,
                "label": onto.country_name(iso), "count": 0,
                "articles": [], "severity": 0.0, "terms": set(),
            })
            rec["count"] += 1
            rec["severity"] = max(rec["severity"], art.get("severity", 0.0))
            rec["terms"].update(matched[:3])
            if len(rec["articles"]) < 6:
                rec["articles"].append({
                    "title": art.get("title", ""), "url": art.get("url", ""),
                    "source": art.get("source", ""),
                    "ts": art.get("published_ts", 0)})

    points = []
    max_count = max((r["count"] for r in by_country.values()), default=1)
    for rec in by_country.values():
        rec["terms"] = sorted(rec["terms"])[:5]
        rec["weight"] = round(0.25 + 0.75 * (rec["count"] / max_count), 2)
        points.append(rec)
    points.sort(key=lambda p: -p["count"])
    return {**spec, "key": key, "points": points, "count": len(points),
            "window_hours": hours}


# ---------------------------------------------------------------------------
# Bulk fetch
# ---------------------------------------------------------------------------
_MEASURED = {"earthquakes": earthquakes, "disasters": disasters}
_STATIC = {"chokepoints": chokepoints, "facilities": facilities}

_CACHE: dict[str, tuple[float, dict]] = {}
_TTL = 600.0


def _cached_measured(key: str) -> dict:
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < _TTL:
        return hit[1]
    data = _MEASURED[key]()
    if not data.get("error"):
        _CACHE[key] = (time.time(), data)
    elif hit:
        return {**hit[1], "stale": True}
    return data


def all_layers(articles: list[dict], keys: list[str] | None = None) -> dict:
    """Every requested layer. Measured feeds are fetched concurrently."""
    want = [k for k in (keys or list(LAYERS)) if k in LAYERS]
    out: dict[str, dict] = {}

    measured = [k for k in want if k in _MEASURED]
    if measured:
        with ThreadPoolExecutor(max_workers=len(measured)) as pool:
            for key, data in zip(measured, pool.map(_cached_measured, measured)):
                out[key] = data
    for key in want:
        if key in _STATIC:
            out[key] = _STATIC[key]()
        elif key in _DERIVED_TERMS:
            out[key] = derived(key, articles)
    return {"layers": out, "catalog": LAYERS, "generated_at": time.time()}
