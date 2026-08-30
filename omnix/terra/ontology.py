"""The TERRA ontology: what kinds of objects exist and how they may relate.

This is the piece the whole upgrade hangs off. Without it, "entity extraction"
produces a bag of strings and the graph is a word-association toy. With it, every
extracted mention resolves to a TYPED object with a stable id, and every edge
between two objects has a declared relationship type that an agent can reason
over ("who supplies whom" is a different question from "who is fighting whom").

Object types form a containment hierarchy — Country > Government > Organization
> Person, plus the cross-cutting Event, Location, Infrastructure, Commodity and
Asset types that everything else attaches to.

The country gazetteer is built from the map's own world.json so the graph and
the map agree on names and ISO codes by construction rather than by a second
hand-maintained list that drifts.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parents[1] / "web"

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------
TYPES = {
    "country":        {"label": "Country",        "glyph": "◈", "color": "#c9a45c", "rank": 1},
    "organization":   {"label": "Organization",   "glyph": "▣", "color": "#57d7ff", "rank": 3},
    "company":        {"label": "Company",        "glyph": "▰", "color": "#5eead4", "rank": 3},
    "government":     {"label": "Government Body","glyph": "⬒", "color": "#9d8cff", "rank": 2},
    "person":         {"label": "Person",         "glyph": "◉", "color": "#ff9a62", "rank": 4},
    "event":          {"label": "Event",          "glyph": "✦", "color": "#ff5d7a", "rank": 5},
    "conflict":       {"label": "Conflict",       "glyph": "⚔", "color": "#ff3355", "rank": 5},
    "news_story":     {"label": "News Story",     "glyph": "▤", "color": "#e8cd8b", "rank": 5},
    "location":       {"label": "Location",       "glyph": "◇", "color": "#4ade80", "rank": 6},
    "infrastructure": {"label": "Infrastructure", "glyph": "⬢", "color": "#ffd166", "rank": 7},
    "commodity":      {"label": "Commodity",      "glyph": "◆", "color": "#f0b429", "rank": 8},
    "asset":          {"label": "Market Asset",   "glyph": "◧", "color": "#7dd3fc", "rank": 9},
    "economic_indicator": {"label": "Economic Indicator", "glyph": "◫", "color": "#a3e635", "rank": 9},
}

# ---------------------------------------------------------------------------
# Visual classes — the nine families the explorer draws distinctly.
#
# Separate from TYPES on purpose. TYPES is the reasoning vocabulary and has to
# stay stable because it is written into every persisted node; VISUAL is the
# presentation vocabulary and is free to group several types into one family
# (infrastructure and location are both "places" to a reader) or to split one
# (a commercial organization is drawn as a Company, a multilateral one is not).
#
# `shape` maps to the renderer's point shapes; `weight` is a size multiplier so
# a country reads as more massive than a single news story at the same degree.
# ---------------------------------------------------------------------------
VISUAL = {
    "country":   {"label": "Countries",           "glyph": "◈", "color": "#c9a45c",
                  "shape": "square",  "weight": 1.45, "ring": True},
    "person":    {"label": "People",              "glyph": "◉", "color": "#ff9a62",
                  "shape": "circle",  "weight": 1.00, "ring": False},
    "org":       {"label": "Organizations",       "glyph": "▣", "color": "#57d7ff",
                  "shape": "diamond", "weight": 1.15, "ring": False},
    "company":   {"label": "Companies",           "glyph": "▰", "color": "#5eead4",
                  "shape": "diamond", "weight": 1.10, "ring": False},
    "event":     {"label": "Events",              "glyph": "✦", "color": "#ff5d7a",
                  "shape": "triangle","weight": 0.95, "ring": False},
    "conflict":  {"label": "Conflicts",           "glyph": "⚔", "color": "#ff3355",
                  "shape": "triangle","weight": 1.25, "ring": True},
    "place":     {"label": "Locations",           "glyph": "◇", "color": "#4ade80",
                  "shape": "circle",  "weight": 0.90, "ring": False},
    "story":     {"label": "News Stories",        "glyph": "▤", "color": "#e8cd8b",
                  "shape": "square",  "weight": 0.85, "ring": False},
    "economic":  {"label": "Economic Indicators", "glyph": "◫", "color": "#a3e635",
                  "shape": "square",  "weight": 1.00, "ring": False},
}

_TYPE_TO_VISUAL = {
    "country": "country",
    "person": "person",
    "organization": "org",
    "government": "org",
    "company": "company",
    "event": "event",
    "conflict": "conflict",
    "news_story": "story",
    "location": "place",
    "infrastructure": "place",
    "commodity": "economic",
    "asset": "economic",
    "economic_indicator": "economic",
}

# Sectors that make an organization a Company rather than an institution. The
# distinction a reader cares about is "does this entity answer to shareholders",
# which is what separates Nvidia from the IAEA even though both are "orgs".
_COMMERCIAL_SECTORS = {
    "semiconductors", "electronics", "consumer tech", "software", "commerce",
    "artificial intelligence", "aerospace", "automotive", "energy", "shipping",
    "asset management", "banking", "manufacturing", "telecom", "defense",
}


def visual_class(ntype: str, sector: str = "") -> str:
    """Which of the nine visual families an object belongs to."""
    if ntype == "organization" and (sector or "").strip().lower() in _COMMERCIAL_SECTORS:
        return "company"
    return _TYPE_TO_VISUAL.get(ntype, "org")


def visual_of(ntype: str, sector: str = "") -> dict:
    return VISUAL[visual_class(ntype, sector)]

# The containment spine — "Palantir's ontology" in the sense that matters: an
# agent can walk UP from a person to the country that contains them, and DOWN
# from a country to the infrastructure inside it, without special-casing.
HIERARCHY = [
    ("country", "government"),
    ("government", "organization"),
    ("country", "organization"),
    ("organization", "person"),
    ("government", "person"),
    ("country", "location"),
    ("country", "infrastructure"),
    ("location", "infrastructure"),
    ("country", "event"),
    ("location", "event"),
]

# Relationship types. `symmetric` edges are stored once and read both ways;
# `weight` is the default salience contributed by a single observation.
RELATIONS = {
    "located_in":    {"label": "located in",     "symmetric": False, "weight": 1.0},
    "leads":         {"label": "leads",          "symmetric": False, "weight": 1.4},
    "member_of":     {"label": "member of",      "symmetric": False, "weight": 1.0},
    "allied_with":   {"label": "allied with",    "symmetric": True,  "weight": 1.2},
    "in_conflict":   {"label": "in conflict with","symmetric": True, "weight": 1.8},
    "sanctions":     {"label": "sanctions",      "symmetric": False, "weight": 1.6},
    "trades_with":   {"label": "trades with",    "symmetric": True,  "weight": 1.0},
    "supplies":      {"label": "supplies",       "symmetric": False, "weight": 1.2},
    "invests_in":    {"label": "invests in",     "symmetric": False, "weight": 1.0},
    "negotiating":   {"label": "negotiating with","symmetric": True, "weight": 1.2},
    "accuses":       {"label": "accuses",        "symmetric": False, "weight": 1.2},
    "supports":      {"label": "supports",       "symmetric": False, "weight": 1.1},
    "affected_by":   {"label": "affected by",    "symmetric": False, "weight": 1.3},
    "involved_in":   {"label": "involved in",    "symmetric": False, "weight": 1.0},
    "produces":      {"label": "produces",       "symmetric": False, "weight": 1.0},
    "depends_on":    {"label": "depends on",     "symmetric": False, "weight": 1.3},
    "co_mentioned":  {"label": "co-mentioned",   "symmetric": True,  "weight": 0.4},
}

# Which relations an LLM extractor is allowed to emit. Anything else is coerced
# to co_mentioned rather than silently inventing a new edge type — an ontology
# that grows itself at runtime stops being an ontology.
EXTRACTABLE = [r for r in RELATIONS if r != "co_mentioned"]


def relation_ok(rel: str) -> str:
    rel = (rel or "").strip().lower().replace(" ", "_").replace("-", "_")
    return rel if rel in RELATIONS else "co_mentioned"


# ---------------------------------------------------------------------------
# Country gazetteer, derived from the map data
# ---------------------------------------------------------------------------
# Common forms that will never appear verbatim in world.json but dominate news
# copy. Maps alias -> ISO-2.
COUNTRY_ALIASES = {
    "us": "US", "u.s.": "US", "usa": "US", "u.s.a.": "US", "america": "US",
    "american": "US", "united states": "US", "washington": "US",
    "uk": "GB", "u.k.": "GB", "britain": "GB", "british": "GB",
    "england": "GB", "great britain": "GB", "london": "GB", "scotland": "GB",
    "wales": "GB", "northern ireland": "GB",
    "uae": "AE", "emirates": "AE", "abu dhabi": "AE", "dubai": "AE",
    "russia": "RU", "russian": "RU", "moscow": "RU", "kremlin": "RU",
    "china": "CN", "chinese": "CN", "beijing": "CN", "prc": "CN",
    "taiwan": "TW", "taipei": "TW",
    "india": "IN", "indian": "IN", "delhi": "IN", "new delhi": "IN",
    "mumbai": "IN", "bengaluru": "IN", "bangalore": "IN",
    "japan": "JP", "japanese": "JP", "tokyo": "JP",
    "korea": "KR", "south korea": "KR", "seoul": "KR",
    "north korea": "KP", "pyongyang": "KP", "dprk": "KP",
    "germany": "DE", "german": "DE", "berlin": "DE",
    "france": "FR", "french": "FR", "paris": "FR",
    "iran": "IR", "iranian": "IR", "tehran": "IR",
    "israel": "IL", "israeli": "IL", "jerusalem": "IL", "tel aviv": "IL",
    "palestine": "PS", "palestinian": "PS", "gaza": "PS", "west bank": "PS",
    "ukraine": "UA", "ukrainian": "UA", "kyiv": "UA", "kiev": "UA",
    "saudi": "SA", "saudi arabia": "SA", "riyadh": "SA",
    "turkey": "TR", "turkish": "TR", "ankara": "TR", "istanbul": "TR",
    "türkiye": "TR",
    "egypt": "EG", "egyptian": "EG", "cairo": "EG",
    "pakistan": "PK", "pakistani": "PK", "islamabad": "PK",
    "afghanistan": "AF", "afghan": "AF", "kabul": "AF",
    "syria": "SY", "syrian": "SY", "damascus": "SY",
    "iraq": "IQ", "iraqi": "IQ", "baghdad": "IQ",
    "yemen": "YE", "yemeni": "YE", "houthi": "YE", "houthis": "YE",
    "lebanon": "LB", "lebanese": "LB", "beirut": "LB",
    "venezuela": "VE", "venezuelan": "VE", "caracas": "VE",
    "brazil": "BR", "brazilian": "BR", "brasilia": "BR",
    "mexico": "MX", "mexican": "MX",
    "canada": "CA", "canadian": "CA", "ottawa": "CA",
    "australia": "AU", "australian": "AU", "canberra": "AU",
    "spain": "ES", "spanish": "ES", "madrid": "ES",
    "italy": "IT", "italian": "IT", "rome": "IT",
    "netherlands": "NL", "dutch": "NL", "amsterdam": "NL", "hague": "NL",
    "poland": "PL", "polish": "PL", "warsaw": "PL",
    "sweden": "SE", "swedish": "SE", "norway": "NO", "norwegian": "NO",
    "finland": "FI", "finnish": "FI", "denmark": "DK", "danish": "DK",
    "switzerland": "CH", "swiss": "CH", "greece": "GR", "greek": "GR",
    "nigeria": "NG", "nigerian": "NG", "kenya": "KE", "kenyan": "KE",
    "ethiopia": "ET", "ethiopian": "ET", "sudan": "SD", "sudanese": "SD",
    "south africa": "ZA", "libya": "LY", "libyan": "LY",
    "myanmar": "MM", "burma": "MM", "burmese": "MM",
    "thailand": "TH", "thai": "TH", "bangkok": "TH",
    "vietnam": "VN", "vietnamese": "VN", "hanoi": "VN",
    "indonesia": "ID", "indonesian": "ID", "jakarta": "ID",
    "philippines": "PH", "filipino": "PH", "manila": "PH",
    "malaysia": "MY", "singapore": "SG", "bangladesh": "BD", "dhaka": "BD",
    "sri lanka": "LK", "nepal": "NP", "argentina": "AR", "chile": "CL",
    "colombia": "CO", "peru": "PE", "cuba": "CU", "haiti": "HT",
    "belarus": "BY", "kazakhstan": "KZ", "azerbaijan": "AZ", "armenia": "AM",
    "georgia": "GE", "serbia": "RS", "kosovo": "XK", "bosnia": "BA",
    "croatia": "HR", "hungary": "HU", "romania": "RO", "bulgaria": "BG",
    "czech": "CZ", "czechia": "CZ", "slovakia": "SK", "austria": "AT",
    "belgium": "BE", "brussels": "BE", "ireland": "IE", "portugal": "PT",
    "morocco": "MA", "algeria": "DZ", "tunisia": "TN", "qatar": "QA",
    "kuwait": "KW", "oman": "OM", "bahrain": "BH", "jordan": "JO",
    "new zealand": "NZ", "congo": "CD", "drc": "CD", "mali": "ML",
    "niger": "NE", "chad": "TD", "somalia": "SO", "eritrea": "ER",
    "uganda": "UG", "tanzania": "TZ", "ghana": "GH", "senegal": "SN",
    "zimbabwe": "ZW", "zambia": "ZM", "mozambique": "MZ", "angola": "AO",
    "uzbekistan": "UZ", "mongolia": "MN", "cambodia": "KH", "laos": "LA",
}

# Deliberately NOT auto-matched: short/ambiguous country names that collide with
# ordinary English or with people's names.
_AMBIGUOUS = {"chad", "jordan", "niger", "guinea", "georgia", "turkey", "mali"}


@lru_cache(maxsize=1)
def countries() -> dict[str, dict]:
    """ISO-2 -> {name, iso2, lat, lon} built from the map's own world.json.

    Centroid is the area-weighted mean of the largest ring, which for map
    purposes lands the marker somewhere sensible inside the country instead of
    in the ocean between two islands.
    """
    out: dict[str, dict] = {}
    path = WEB_DIR / "world.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return out
    for feat in data.get("features", []):
        iso = (feat.get("i") or "").strip().upper()
        name = (feat.get("n") or "").strip()
        if not iso or len(iso) != 2 or not name:
            continue
        best_ring, best_area = None, -1.0
        for poly in feat.get("g", []):
            ring = poly[0] if poly else None
            if not ring or len(ring) < 4:
                continue
            area = abs(_ring_area(ring))
            if area > best_area:
                best_area, best_ring = area, ring
        if best_ring:
            lon = sum(p[0] for p in best_ring) / len(best_ring)
            lat = sum(p[1] for p in best_ring) / len(best_ring)
        else:
            lon = lat = 0.0
        out[iso] = {"iso2": iso, "name": name, "lat": round(lat, 4),
                    "lon": round(lon, 4)}
    return out


def _ring_area(ring) -> float:
    s = 0.0
    for i in range(len(ring) - 1):
        s += ring[i][0] * ring[i + 1][1] - ring[i + 1][0] * ring[i][1]
    return s / 2.0


@lru_cache(maxsize=1)
def country_lookup() -> dict[str, str]:
    """Every recognizable surface form -> ISO-2. Longest match wins downstream."""
    table: dict[str, str] = {}
    for iso, meta in countries().items():
        name = meta["name"].lower()
        if name not in _AMBIGUOUS:
            table[name] = iso
        # "Federated States of Micronesia" is also just "Micronesia" in copy.
        stripped = re.sub(r"^(the\s+)?(republic|kingdom|state|federated states|"
                          r"islamic republic|people's republic|united)\s+of\s+",
                          "", name)
        if stripped != name and len(stripped) > 4 and stripped not in _AMBIGUOUS:
            table[stripped] = iso
    table.update(COUNTRY_ALIASES)
    return table


def iso_for(name: str) -> str:
    return country_lookup().get((name or "").strip().lower(), "")


def country_name(iso: str) -> str:
    return (countries().get((iso or "").upper()) or {}).get("name", iso or "")


def country_point(iso: str) -> tuple[float, float] | None:
    meta = countries().get((iso or "").upper())
    return (meta["lat"], meta["lon"]) if meta else None


# ---------------------------------------------------------------------------
# Seed objects for the non-country types.
#
# These are the high-precision anchors: matching them needs no model and no
# guessing. Everything beyond this list is discovered at runtime by the
# heuristic + LLM extractors in extract.py, which is where the long tail lives.
# ---------------------------------------------------------------------------
SEED_ORGS: dict[str, dict] = {
    # multilateral / governmental
    "United Nations": {"type": "organization", "aliases": ["un ", "u.n.", "unsc", "security council", "unhcr", "unicef"], "sector": "multilateral"},
    "NATO": {"type": "organization", "aliases": ["nato", "north atlantic treaty"], "sector": "military alliance"},
    "European Union": {"type": "organization", "aliases": ["eu ", "e.u.", "european commission", "brussels"], "sector": "multilateral"},
    "OPEC": {"type": "organization", "aliases": ["opec", "opec+"], "sector": "energy cartel"},
    # NOTE: no bare "who" alias — it matches the English pronoun in roughly
    # every third headline. The full name and the dotted form are unambiguous.
    "World Health Organization": {"type": "organization", "aliases": ["w.h.o."], "sector": "health"},
    "International Monetary Fund": {"type": "organization", "aliases": ["imf"], "sector": "finance"},
    "World Bank": {"type": "organization", "aliases": ["world bank"], "sector": "finance"},
    "World Trade Organization": {"type": "organization", "aliases": ["wto"], "sector": "trade"},
    "BRICS": {"type": "organization", "aliases": ["brics"], "sector": "bloc"},
    "G7": {"type": "organization", "aliases": ["g7", "g-7"], "sector": "bloc"},
    "G20": {"type": "organization", "aliases": ["g20", "g-20"], "sector": "bloc"},
    "ASEAN": {"type": "organization", "aliases": ["asean"], "sector": "bloc"},
    "African Union": {"type": "organization", "aliases": ["african union"], "sector": "bloc"},
    "IAEA": {"type": "organization", "aliases": ["iaea", "atomic energy agency"], "sector": "nuclear"},
    # central banks / regulators
    "Federal Reserve": {"type": "government", "aliases": ["federal reserve", "the fed", "fomc"], "sector": "central bank"},
    "European Central Bank": {"type": "government", "aliases": ["ecb", "european central bank"], "sector": "central bank"},
    "Bank of Japan": {"type": "government", "aliases": ["bank of japan", "boj"], "sector": "central bank"},
    "Reserve Bank of India": {"type": "government", "aliases": ["reserve bank of india", "rbi"], "sector": "central bank"},
    "People's Bank of China": {"type": "government", "aliases": ["people's bank of china", "pboc"], "sector": "central bank"},
    "Pentagon": {"type": "government", "aliases": ["pentagon", "department of defense", "dod"], "sector": "defense"},
    "White House": {"type": "government", "aliases": ["white house"], "sector": "executive"},
    "State Department": {"type": "government", "aliases": ["state department"], "sector": "diplomacy"},
    "CIA": {"type": "government", "aliases": ["cia"], "sector": "intelligence"},
    "FBI": {"type": "government", "aliases": ["fbi"], "sector": "law enforcement"},
    "SEC": {"type": "government", "aliases": ["sec ", "securities and exchange"], "sector": "regulator"},
    # technology / semiconductors — the supply-chain spine
    "TSMC": {"type": "organization", "aliases": ["tsmc", "taiwan semiconductor"], "sector": "semiconductors"},
    "ASML": {"type": "organization", "aliases": ["asml"], "sector": "semiconductors"},
    "Nvidia": {"type": "organization", "aliases": ["nvidia"], "sector": "semiconductors"},
    "Intel": {"type": "organization", "aliases": ["intel corp", "intel "], "sector": "semiconductors"},
    "Samsung": {"type": "organization", "aliases": ["samsung"], "sector": "electronics"},
    "SK Hynix": {"type": "organization", "aliases": ["sk hynix"], "sector": "semiconductors"},
    "Apple": {"type": "organization", "aliases": ["apple inc", "apple "], "sector": "consumer tech"},
    "Foxconn": {"type": "organization", "aliases": ["foxconn", "hon hai"], "sector": "manufacturing"},
    "Huawei": {"type": "organization", "aliases": ["huawei"], "sector": "telecom"},
    "Microsoft": {"type": "organization", "aliases": ["microsoft"], "sector": "software"},
    "Google": {"type": "organization", "aliases": ["google", "alphabet"], "sector": "software"},
    "Amazon": {"type": "organization", "aliases": ["amazon"], "sector": "commerce"},
    "Meta": {"type": "organization", "aliases": ["meta platforms", "facebook"], "sector": "software"},
    "OpenAI": {"type": "organization", "aliases": ["openai"], "sector": "artificial intelligence"},
    "Anthropic": {"type": "organization", "aliases": ["anthropic"], "sector": "artificial intelligence"},
    "SpaceX": {"type": "organization", "aliases": ["spacex"], "sector": "aerospace"},
    "Tesla": {"type": "organization", "aliases": ["tesla"], "sector": "automotive"},
    "Boeing": {"type": "organization", "aliases": ["boeing"], "sector": "aerospace"},
    "Airbus": {"type": "organization", "aliases": ["airbus"], "sector": "aerospace"},
    "Lockheed Martin": {"type": "organization", "aliases": ["lockheed"], "sector": "defense"},
    # energy / commodities / shipping
    "Saudi Aramco": {"type": "organization", "aliases": ["aramco"], "sector": "energy"},
    "Gazprom": {"type": "organization", "aliases": ["gazprom"], "sector": "energy"},
    "ExxonMobil": {"type": "organization", "aliases": ["exxon"], "sector": "energy"},
    "Shell": {"type": "organization", "aliases": ["shell plc", "royal dutch shell"], "sector": "energy"},
    "BP": {"type": "organization", "aliases": ["bp plc"], "sector": "energy"},
    "Maersk": {"type": "organization", "aliases": ["maersk"], "sector": "shipping"},
    "BlackRock": {"type": "organization", "aliases": ["blackrock"], "sector": "asset management"},
    "Goldman Sachs": {"type": "organization", "aliases": ["goldman sachs"], "sector": "banking"},
    "JPMorgan": {"type": "organization", "aliases": ["jpmorgan", "jp morgan"], "sector": "banking"},
    # armed / non-state actors
    "Hamas": {"type": "organization", "aliases": ["hamas"], "sector": "armed group"},
    "Hezbollah": {"type": "organization", "aliases": ["hezbollah", "hizbollah"], "sector": "armed group"},
    "Wagner Group": {"type": "organization", "aliases": ["wagner group"], "sector": "armed group"},
    "Islamic State": {"type": "organization", "aliases": ["islamic state", "isis", "isil"], "sector": "armed group"},
    "Taliban": {"type": "organization", "aliases": ["taliban"], "sector": "armed group"},
    "Houthi movement": {"type": "organization", "aliases": ["houthi", "houthis", "ansar allah"], "sector": "armed group"},
}

SEED_INFRASTRUCTURE: dict[str, dict] = {
    "Strait of Hormuz": {"type": "infrastructure", "aliases": ["strait of hormuz", "hormuz"], "lat": 26.57, "lon": 56.25, "sector": "shipping chokepoint"},
    "Suez Canal": {"type": "infrastructure", "aliases": ["suez canal", "suez"], "lat": 30.02, "lon": 32.58, "sector": "shipping chokepoint"},
    "Panama Canal": {"type": "infrastructure", "aliases": ["panama canal"], "lat": 9.08, "lon": -79.68, "sector": "shipping chokepoint"},
    "Bab el-Mandeb": {"type": "infrastructure", "aliases": ["bab el-mandeb", "bab al-mandab", "red sea strait"], "lat": 12.58, "lon": 43.33, "sector": "shipping chokepoint"},
    "Strait of Malacca": {"type": "infrastructure", "aliases": ["strait of malacca", "malacca strait"], "lat": 2.5, "lon": 101.0, "sector": "shipping chokepoint"},
    "Taiwan Strait": {"type": "infrastructure", "aliases": ["taiwan strait"], "lat": 24.5, "lon": 119.5, "sector": "contested waterway"},
    "South China Sea": {"type": "infrastructure", "aliases": ["south china sea"], "lat": 13.0, "lon": 114.0, "sector": "contested waterway"},
    "Red Sea": {"type": "infrastructure", "aliases": ["red sea"], "lat": 20.0, "lon": 38.5, "sector": "shipping lane"},
    "Black Sea": {"type": "infrastructure", "aliases": ["black sea"], "lat": 43.4, "lon": 34.3, "sector": "shipping lane"},
    "Nord Stream": {"type": "infrastructure", "aliases": ["nord stream"], "lat": 55.5, "lon": 15.5, "sector": "pipeline"},
    "Druzhba pipeline": {"type": "infrastructure", "aliases": ["druzhba"], "lat": 52.0, "lon": 27.0, "sector": "pipeline"},
    "Zaporizhzhia NPP": {"type": "infrastructure", "aliases": ["zaporizhzhia"], "lat": 47.51, "lon": 34.59, "sector": "nuclear plant"},
    "Natanz facility": {"type": "infrastructure", "aliases": ["natanz"], "lat": 33.72, "lon": 51.73, "sector": "nuclear facility"},
    "Fordow facility": {"type": "infrastructure", "aliases": ["fordow", "fordo"], "lat": 34.88, "lon": 50.99, "sector": "nuclear facility"},
    "Port of Shanghai": {"type": "infrastructure", "aliases": ["port of shanghai"], "lat": 31.23, "lon": 121.47, "sector": "port"},
    "Port of Rotterdam": {"type": "infrastructure", "aliases": ["port of rotterdam"], "lat": 51.95, "lon": 4.14, "sector": "port"},
    "Hsinchu Science Park": {"type": "infrastructure", "aliases": ["hsinchu"], "lat": 24.78, "lon": 121.0, "sector": "semiconductor cluster"},
}

SEED_COMMODITIES: dict[str, dict] = {
    "Crude oil": {"type": "commodity", "aliases": ["crude oil", "brent", "wti", "oil prices", "barrel"], "unit": "USD/bbl"},
    "Natural gas": {"type": "commodity", "aliases": ["natural gas", "lng", "gas prices"], "unit": "USD/MMBtu"},
    # "gold" alone is mostly Olympic medals in a news corpus.
    "Gold": {"type": "commodity", "aliases": ["gold price", "gold prices", "bullion"], "unit": "USD/oz"},
    "Wheat": {"type": "commodity", "aliases": ["wheat", "grain deal", "grain exports"], "unit": "USD/bu"},
    "Semiconductors": {"type": "commodity", "aliases": ["semiconductor", "semiconductors", "chip", "chips", "chipmaking", "advanced chips", "chip export", "export controls"], "unit": "index"},
    "Rare earths": {"type": "commodity", "aliases": ["rare earth", "rare earths", "critical minerals"], "unit": "index"},
    "Lithium": {"type": "commodity", "aliases": ["lithium"], "unit": "USD/t"},
    "Copper": {"type": "commodity", "aliases": ["copper"], "unit": "USD/lb"},
    "Uranium": {"type": "commodity", "aliases": ["uranium", "enriched uranium"], "unit": "USD/lb"},
}

SEED_ASSETS: dict[str, dict] = {
    "Bitcoin": {"type": "asset", "aliases": ["bitcoin", "btc"], "class": "crypto"},
    "Ethereum": {"type": "asset", "aliases": ["ethereum", "ether "], "class": "crypto"},
    "S&P 500": {"type": "asset", "aliases": ["s&p 500", "s&p500", "wall street"], "class": "equity index"},
    "Nasdaq": {"type": "asset", "aliases": ["nasdaq"], "class": "equity index"},
    "Nifty 50": {"type": "asset", "aliases": ["nifty", "nifty 50"], "class": "equity index"},
    "Sensex": {"type": "asset", "aliases": ["sensex", "bse sensex"], "class": "equity index"},
    "Nikkei 225": {"type": "asset", "aliases": ["nikkei"], "class": "equity index"},
    "Hang Seng": {"type": "asset", "aliases": ["hang seng"], "class": "equity index"},
    "US dollar": {"type": "asset", "aliases": ["dollar index", "greenback", "us dollar"], "class": "currency"},
    "Euro": {"type": "asset", "aliases": ["the euro", "euro zone currency"], "class": "currency"},
}

# Heads of state / government and other figures that dominate world copy.
#
# The VALUE here is the alias->canonical-name mapping: news copy says "Putin"
# and the graph needs one node, not one per surname form. `role` is a display
# hint only — offices change and this list does not, so nothing downstream
# treats it as authoritative. The live country cards and the LLM passes are what
# report who currently holds an office.
SEED_PEOPLE: dict[str, dict] = {
    "Donald Trump": {"type": "person", "aliases": ["donald trump", "trump"], "country": "US", "role": "President of the United States"},
    "Vladimir Putin": {"type": "person", "aliases": ["putin"], "country": "RU", "role": "President of Russia"},
    "Xi Jinping": {"type": "person", "aliases": ["xi jinping", "president xi"], "country": "CN", "role": "President of China"},
    "Narendra Modi": {"type": "person", "aliases": ["modi"], "country": "IN", "role": "Prime Minister of India"},
    "Volodymyr Zelenskyy": {"type": "person", "aliases": ["zelensky", "zelenskyy", "zelenskiy"], "country": "UA", "role": "President of Ukraine"},
    "Benjamin Netanyahu": {"type": "person", "aliases": ["netanyahu"], "country": "IL", "role": "Prime Minister of Israel"},
    "Ali Khamenei": {"type": "person", "aliases": ["khamenei"], "country": "IR", "role": "Supreme Leader of Iran"},
    "Kim Jong Un": {"type": "person", "aliases": ["kim jong un", "kim jong-un"], "country": "KP", "role": "Leader of North Korea"},
    "Emmanuel Macron": {"type": "person", "aliases": ["macron"], "country": "FR", "role": "President of France"},
    "Ursula von der Leyen": {"type": "person", "aliases": ["von der leyen"], "country": "", "role": "President of the European Commission"},
    "António Guterres": {"type": "person", "aliases": ["guterres"], "country": "", "role": "UN Secretary-General"},
    "Mohammed bin Salman": {"type": "person", "aliases": ["bin salman", "mbs "], "country": "SA", "role": "Crown Prince of Saudi Arabia"},
    "Recep Tayyip Erdogan": {"type": "person", "aliases": ["erdogan", "erdoğan"], "country": "TR", "role": "President of Türkiye"},
    "Jerome Powell": {"type": "person", "aliases": ["jerome powell", "powell"], "country": "US", "role": "Chair of the Federal Reserve"},
    "Christine Lagarde": {"type": "person", "aliases": ["lagarde"], "country": "", "role": "President of the ECB"},
    "Elon Musk": {"type": "person", "aliases": ["elon musk", "musk"], "country": "US", "role": "CEO, Tesla / SpaceX"},
    "Jensen Huang": {"type": "person", "aliases": ["jensen huang"], "country": "US", "role": "CEO, Nvidia"},
    "Tim Cook": {"type": "person", "aliases": ["tim cook"], "country": "US", "role": "CEO, Apple"},
    "Keir Starmer": {"type": "person", "aliases": ["keir starmer", "starmer"], "country": "GB", "role": "Prime Minister of the United Kingdom"},
    "Friedrich Merz": {"type": "person", "aliases": ["friedrich merz", "merz"], "country": "DE", "role": "Chancellor of Germany"},
    "Anthony Albanese": {"type": "person", "aliases": ["albanese"], "country": "AU", "role": "Prime Minister of Australia"},
    "Luiz Inácio Lula da Silva": {"type": "person", "aliases": ["lula"], "country": "BR", "role": "President of Brazil"},
    "Cyril Ramaphosa": {"type": "person", "aliases": ["ramaphosa"], "country": "ZA", "role": "President of South Africa"},
    "Justin Trudeau": {"type": "person", "aliases": ["trudeau"], "country": "CA", "role": "Canadian political leader"},
    "Mark Rutte": {"type": "person", "aliases": ["mark rutte", "rutte"], "country": "", "role": "NATO Secretary General"},
    "Sam Altman": {"type": "person", "aliases": ["sam altman"], "country": "US", "role": "CEO, OpenAI"},
    "Satya Nadella": {"type": "person", "aliases": ["satya nadella", "nadella"], "country": "US", "role": "CEO, Microsoft"},
    "Sundar Pichai": {"type": "person", "aliases": ["sundar pichai", "pichai"], "country": "US", "role": "CEO, Alphabet"},
}


def seed_objects() -> dict[str, dict]:
    """All non-country seed objects, keyed by canonical name."""
    out: dict[str, dict] = {}
    for table in (SEED_ORGS, SEED_INFRASTRUCTURE, SEED_COMMODITIES,
                  SEED_ASSETS, SEED_PEOPLE):
        for name, meta in table.items():
            out[name] = {**meta, "name": name}
    return out


def surface_forms(name: str, ntype: str = "", iso2: str = "") -> list[str]:
    """Every written form this object plausibly appears as in news copy.

    The canonical name is often the form nobody writes: no headline says
    "United States of America". Callers that have to recognise an entity in raw
    text — rather than in our own graph — need the aliases the extractor matches
    on, so they are published here rather than kept private to extraction.
    """
    forms: list[str] = []
    if name:
        forms.append(name)
    iso = (iso2 or "").upper()
    if iso:
        canon = country_name(iso)
        if canon:
            forms.append(canon)
        # Reverse the alias table: every spelling that resolves to this country.
        for alias, target in COUNTRY_ALIASES.items():
            if target == iso:
                forms.append(alias)
        # "Republic of X" -> "X", the form the gazetteer strips.
        stripped = re.sub(r"^(the\s+)?(republic|kingdom|state|federated states|"
                          r"islamic republic|people's republic|united)\s+of\s+",
                          "", canon or name, flags=re.I)
        if stripped and stripped.lower() != (canon or name).lower():
            forms.append(stripped)
    meta = seed_objects().get(name)
    if meta:
        forms.extend(meta.get("aliases", []))
    # A person is usually written by surname alone after first mention.
    if ntype == "person" and name and " " in name:
        forms.append(name.rsplit(" ", 1)[-1])

    seen: set[str] = set()
    out: list[str] = []
    for f in forms:
        f = (f or "").strip()
        key = f.lower()
        # Two characters match far too much to be worth emphasising on.
        if len(key) < 3 or key in seen:
            continue
        seen.add(key)
        out.append(f)
    return out


# ---------------------------------------------------------------------------
# Static relationships that are true independent of today's news. They give the
# graph a skeleton on first run, before any article has been ingested.
# ---------------------------------------------------------------------------
BASE_EDGES: list[tuple[str, str, str]] = [
    ("TSMC", "supplies", "Nvidia"),
    ("TSMC", "supplies", "Apple"),
    ("ASML", "supplies", "TSMC"),
    ("ASML", "supplies", "Samsung"),
    ("ASML", "supplies", "Intel"),
    ("Foxconn", "supplies", "Apple"),
    ("SK Hynix", "supplies", "Nvidia"),
    ("Samsung", "produces", "Semiconductors"),
    ("TSMC", "produces", "Semiconductors"),
    ("Nvidia", "depends_on", "Semiconductors"),
    ("Apple", "depends_on", "Semiconductors"),
    ("Saudi Aramco", "produces", "Crude oil"),
    ("Gazprom", "produces", "Natural gas"),
    ("OPEC", "produces", "Crude oil"),
    ("Maersk", "depends_on", "Suez Canal"),
    ("Maersk", "depends_on", "Panama Canal"),
    ("Crude oil", "depends_on", "Strait of Hormuz"),
    ("Semiconductors", "depends_on", "Taiwan Strait"),
    ("Jensen Huang", "leads", "Nvidia"),
    ("Tim Cook", "leads", "Apple"),
    ("Elon Musk", "leads", "Tesla"),
    ("Elon Musk", "leads", "SpaceX"),
    ("Jerome Powell", "leads", "Federal Reserve"),
    ("Christine Lagarde", "leads", "European Central Bank"),
    ("Ursula von der Leyen", "leads", "European Union"),
    ("António Guterres", "leads", "United Nations"),
]

# Country attachments for seed objects that have an obvious home state.
BASE_COUNTRY_EDGES: list[tuple[str, str]] = [
    ("TSMC", "TW"), ("Hsinchu Science Park", "TW"),
    ("ASML", "NL"), ("Port of Rotterdam", "NL"),
    ("Samsung", "KR"), ("SK Hynix", "KR"),
    ("Nvidia", "US"), ("Intel", "US"), ("Apple", "US"), ("Microsoft", "US"),
    ("Google", "US"), ("Amazon", "US"), ("Meta", "US"), ("OpenAI", "US"),
    ("Anthropic", "US"), ("SpaceX", "US"), ("Tesla", "US"), ("Boeing", "US"),
    ("Lockheed Martin", "US"), ("ExxonMobil", "US"), ("BlackRock", "US"),
    ("Goldman Sachs", "US"), ("JPMorgan", "US"), ("Federal Reserve", "US"),
    ("Pentagon", "US"), ("White House", "US"), ("State Department", "US"),
    ("CIA", "US"), ("FBI", "US"), ("SEC", "US"),
    ("Foxconn", "TW"), ("Huawei", "CN"), ("Port of Shanghai", "CN"),
    ("People's Bank of China", "CN"),
    ("Airbus", "FR"), ("Shell", "GB"), ("BP", "GB"),
    ("Saudi Aramco", "SA"), ("Gazprom", "RU"), ("Maersk", "DK"),
    ("Bank of Japan", "JP"), ("Reserve Bank of India", "IN"),
    ("Hamas", "PS"), ("Hezbollah", "LB"), ("Houthi movement", "YE"),
    ("Taliban", "AF"), ("Wagner Group", "RU"),
    ("Strait of Hormuz", "IR"), ("Suez Canal", "EG"), ("Panama Canal", "PA"),
    ("Bab el-Mandeb", "YE"), ("Taiwan Strait", "TW"),
    ("Zaporizhzhia NPP", "UA"), ("Natanz facility", "IR"),
    ("Fordow facility", "IR"), ("Nord Stream", "RU"),
]
