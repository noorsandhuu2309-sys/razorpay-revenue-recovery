"""Location intelligence — everything TERRA knows about one point on Earth.

The map could already answer "where is this" (geocoding, conditions, routes)
and the graph could already answer "what is happening to this country" (the
entity panel, risk, stories). Neither answered the question a user actually
asks of a world map, which is **"what is going on *here*"** — where *here* is
wherever they just clicked or searched, at whatever granularity that point
happens to be.

That question is spatial on the way in and lexical on the way out:

    point -> reverse geocode -> {city, region, country, ISO}
          -> country match  (article["countries"] carries ISO codes)
          -> locality match (the corpus has no coordinates, so a city is
                             recognised by NAME in the headline and summary)

The locality half is the part that did not exist. TERRA's extractor places
articles on countries, and the 344 `location:` nodes in the graph carry no
coordinates at all, so there is no spatial index to query for "news near
17.4N, 78.5E". What there *is* is the text: a story about Hyderabad says
"Hyderabad". So a locality is matched on word boundaries against title and
summary, and the result is labelled `local` — a claim about the wording, which
is honest, rather than a claim about geography, which would not be.

Two rules the payload keeps:

  * **Local and country news are never merged silently.** Each item carries
    `scope`, and local items carry `matched` — the exact terms that put them
    there. A reader can always see why an article is in front of them.
  * **Nothing is invented for an empty place.** Click the middle of the
    Pacific and you get a place with no country, no risk and no news, said
    plainly. A map that fabricates a briefing for open ocean is worse than one
    that admits the corpus is silent.
"""

from __future__ import annotations

import re
import time
from collections import Counter

from . import ontology as onto

# The corpus retains more than the "what is happening now" window, and a city
# is thin: Hyderabad may get three mentions a week where India gets three an
# hour. Reading a week back is what makes a locality answer non-empty without
# making a country answer stale — country stories are ranked by recency anyway.
WINDOW_HOURS = 168.0

# Terms shorter than this match inside other words often enough to be useless
# even with word boundaries ("Ur", "Po"), and reverse geocoders return them as
# region codes.
MIN_TERM = 4

# Region names that are also common words would drag in unrelated copy. These
# are dropped from locality matching; the country match still covers them.
STOP_TERMS = {
    "north", "south", "east", "west", "central", "district", "county",
    "province", "state", "region", "city", "town", "village", "area",
    "capital", "territory", "union", "federal", "national", "new", "old",
}


def _terms(*values: str) -> list[str]:
    """De-duplicated, lowercased, usable match terms from raw place fields."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in values:
        term = (raw or "").strip().lower()
        if len(term) < MIN_TERM or term in STOP_TERMS or term in seen:
            continue
        seen.add(term)
        out.append(term)
    return out


def _matcher(terms: list[str]) -> re.Pattern | None:
    """One word-boundary regex for every term, or None when there are none.

    One alternation rather than a loop of `in` tests: `"delhi" in text` is true
    of "New Delhi" (wanted) and of "Delhicious" (not), and running N substring
    scans over 1500 articles is slower than one compiled pass.
    """
    if not terms:
        return None
    return re.compile(
        r"\b(" + "|".join(re.escape(t) for t in terms) + r")\b", re.I)


def _reverse(lat: float, lon: float, workspace_id: str | None) -> dict:
    """Coordinates -> {name, city, region, country, iso2}, or {} if offline.

    Wrapped in a try because this is the one network call in the whole brief
    and a dead geocoder must degrade to "we know the coordinates and nothing
    else", not to a 500. The caller can also supply `iso` from the country
    polygon it already drew, which is why this failing is survivable.
    """
    try:
        from .geo.core import geocoding
        from .geo.types import Coord
        result = geocoding.reverse(Coord(lat=lat, lon=lon),
                                   workspace_id=workspace_id)
        place = result.data
        if place is None:
            return {}
        tags = getattr(place, "tags", None) or {}
        return {
            "name": getattr(place, "name", "") or "",
            "address": getattr(place, "address", "") or "",
            "city": tags.get("city") or tags.get("locality") or "",
            "region": tags.get("state") or tags.get("region") or "",
            "country": tags.get("country") or "",
            "iso2": (tags.get("countryCode") or "").upper(),
            "provider": result.provider,
            "freshness": getattr(result.freshness, "value", str(result.freshness)),
        }
    except Exception:
        return {}


def _country_names(country_name: str, iso2: str) -> set[str]:
    """The forms of a COUNTRY's own name, which must not count as a locality.

    Deliberately *not* `onto.surface_forms`. That table is built for entity
    extraction, so it resolves metonyms — "washington", "dubai", "jerusalem"
    all map to a country there, which is right when you are tagging a headline
    and catastrophic here: it would delete the very term the user clicked on
    and leave the panel matching "Dubayy" and "Yerushalayim", the two spellings
    no newsroom uses. Only the country name itself is excluded, because "India"
    in a story about India is already caught by the ISO match.
    """
    out: set[str] = set()
    if iso2:
        out.add(iso2.lower())
    for raw in (country_name, onto.country_name(iso2) if iso2 else ""):
        base = (raw or "").strip().lower()
        if not base:
            continue
        # Gazetteer names arrive as "United States of America (the)".
        base = re.sub(r"\s*\(the\)$", "", base)
        base = re.sub(r"^the\s+", "", base)
        out.add(base)
        stripped = re.sub(r"^(republic|kingdom|state|federated states|islamic "
                          r"republic|people's republic|united)\s+of\s+", "", base)
        out.add(stripped)
    return {o for o in out if o}


def brief(svc, *, lat: float | None = None, lon: float | None = None,
          name: str = "", iso: str = "", region: str = "",
          limit: int = 40, window_hours: float = WINDOW_HOURS,
          workspace_id: str | None = None, resolve: bool = True) -> dict:
    """Everything happening at one place: news, stories, risk, who is involved.

    `svc` is the live TerraService — passed in rather than imported, so this
    module stays a pure reader over whatever state the refresh loop produced.
    """
    now = time.time()

    # -- 1. resolve the place ------------------------------------------------
    rev = _reverse(lat, lon, workspace_id) if (
        resolve and lat is not None and lon is not None) else {}
    iso2 = (iso or rev.get("iso2") or "").upper()
    city = rev.get("city") or ""
    # A searched place arrives with a label and no reverse lookup behind it
    # ("Hyderabad", picked from the geocoder). That label IS the locality.
    label = name or rev.get("name") or city or ""
    region_name = region or rev.get("region") or ""
    country_name = rev.get("country") or (onto.country_name(iso2) if iso2 else "")

    place = {
        "label": label or (f"{lat:.4f}, {lon:.4f}" if lat is not None else "Unknown"),
        "name": rev.get("name") or name,
        "address": rev.get("address", ""),
        "city": city,
        "region": region_name,
        "country": country_name,
        "iso2": iso2,
        "lat": lat,
        "lon": lon,
        "resolved_by": rev.get("provider", "" if rev else "client"),
        "freshness": rev.get("freshness", ""),
    }

    # -- 2. what counts as "here" -------------------------------------------
    # The label is included as a term because a searched place ("Strait of
    # Hormuz", "Camp David") is frequently a feature the reverse geocoder has
    # no city for, and it is exactly the string the headlines use.
    local_terms = [t for t in _terms(city, label, region_name)
                   if t not in _country_names(country_name, iso2)]
    rx = _matcher(local_terms)

    # -- 3. read the corpus --------------------------------------------------
    articles = [a for a in svc.store.all()
                if now - a.get("published_ts", 0) <= window_hours * 3600]
    articles.sort(key=lambda a: -a.get("published_ts", 0))

    local: list[dict] = []
    national: list[dict] = []
    # The source article is kept beside each item so the entity tally below can
    # read `entities` off it. Re-looking those up would mean rebuilding the
    # corpus index once per article, which is quadratic on 1500 articles.
    raw: dict[str, dict] = {}
    for art in articles:
        hits: list[str] = []
        if rx is not None:
            text = f"{art.get('title', '')} {art.get('summary', '')}"
            hits = sorted({m.group(1).lower() for m in rx.finditer(text)})
        in_country = bool(iso2) and iso2 in (art.get("countries") or [])
        if not hits and not in_country:
            continue
        item = {
            "id": art.get("id", ""),
            "title": art.get("title", ""),
            "url": art.get("url", ""),
            "source": art.get("source", ""),
            "summary": _summary(art),
            "published_ts": art.get("published_ts", 0),
            "when": _ago(now - art.get("published_ts", 0)),
            "domains": art.get("domains", []),
            "sentiment": art.get("sentiment", 0.0),
            "severity": art.get("severity", 0.0),
            "confidence": art.get("confidence", 0.0),
            "countries": art.get("countries", []),
            "cluster": art.get("cluster", ""),
            "scope": "local" if hits else "country",
            "matched": hits,
        }
        raw[item["id"]] = art
        (local if hits else national).append(item)

    # Local first, then national, each already in recency order. The cap is
    # applied AFTER that ordering so a busy country can never push the three
    # articles that actually name the place off the end of the list.
    news = (local + national)[:max(1, min(limit, 120))]

    # -- 4. clustered stories ------------------------------------------------
    stories = []
    for c in svc.ranked:
        hits: list[str] = []
        if rx is not None:
            text = f"{c.get('title', '')} {' '.join(c.get('keywords', []))}"
            hits = sorted({m.group(1).lower() for m in rx.finditer(text)})
        if not hits and not (iso2 and iso2 in (c.get("countries") or [])):
            continue
        stories.append({
            "id": c["id"], "title": c["title"], "url": c.get("url", ""),
            "size": c.get("size", 0), "sources": c.get("source_count", 0),
            "first_ts": c.get("first_ts", 0), "last_ts": c.get("last_ts", 0),
            "when": c.get("when", ""),
            "severity": c.get("severity", 0.0),
            "sentiment": c.get("sentiment", 0.0),
            "corroboration": c.get("corroboration", 0.0),
            "domains": c.get("domains", []),
            "countries": c.get("countries", []),
            "status": (c.get("status") or {}).get("state", ""),
            "scope": "local" if hits else "country",
            "matched": hits,
        })
    stories.sort(key=lambda s: (s["scope"] != "local", -s["last_ts"]))
    stories = stories[:16]

    # -- 5. who and what is involved ----------------------------------------
    # Counted over the matched articles rather than looked up in the graph, so
    # the names on screen are the names in the stories on screen. The country
    # itself is dropped: "India appears in news about India" is not a finding.
    tally: Counter = Counter()
    meta: dict[str, dict] = {}
    for item in local + national:
        for e in (raw.get(item["id"], {}).get("entities") or []):
            eid = e.get("id", "")
            if not eid or eid == f"country:{iso2}":
                continue
            tally[eid] += int(e.get("count", 1) or 1)
            meta.setdefault(eid, {"id": eid, "name": e.get("name", ""),
                                  "type": e.get("type", "")})
    entities = []
    for eid, count in tally.most_common(14):
        node = svc.graph.node(eid)
        row = dict(meta[eid], count=count)
        if node:
            pub = svc.graph._public(node)
            row.update({"name": pub["name"], "type": pub["type"],
                        "glyph": pub["glyph"], "color": pub["color"],
                        "type_label": pub["type_label"],
                        "mentions": pub["mentions"]})
        entities.append(row)

    # Graph objects whose NAME is the place — "Strait of Hormuz" as an object
    # in its own right, with everything TERRA has attached to it.
    graph_hits = []
    seen_hits: set[str] = set()
    for term in local_terms[:3]:
        for hit in svc.graph.find(term, limit=6):
            # `news_story` nodes are headlines, which the news list already
            # shows in full; `person` matches surnames that merely coincide
            # with the place ("George Washington"). Neither is a thing that is
            # happening *at* the location.
            if hit["type"] in ("news_story", "person"):
                continue
            if hit["id"] in seen_hits:
                continue
            seen_hits.add(hit["id"])
            graph_hits.append(hit)

    # -- 6. summary ----------------------------------------------------------
    domains: Counter = Counter()
    for item in local + national:
        for d in item["domains"]:
            domains[d] += 1
    considered = local + national
    sentiment = (round(sum(i["sentiment"] for i in considered) / len(considered), 3)
                 if considered else None)

    return {
        "status": "ok",
        "place": place,
        "country": {
            "id": f"country:{iso2}" if iso2 else "",
            "iso2": iso2,
            "name": country_name,
            "risk": svc.risk.get(iso2) if iso2 else None,
            "risk_delta": svc.risk_deltas.get(iso2) if iso2 else None,
        },
        "terms": local_terms,
        "summary": {
            "local": len(local),
            "country": len(national),
            "stories": len(stories),
            "sources": len({i["source"] for i in considered if i["source"]}),
            "domains": dict(domains.most_common()),
            "sentiment": sentiment,
            "window_hours": window_hours,
            "corpus_articles": len(articles),
        },
        "news": news,
        "stories": stories,
        "entities": entities,
        "graph_hits": graph_hits[:8],
        "generated_ts": now,
    }


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _summary(article: dict) -> str:
    """The article's summary, unless it is just the headline again.

    A large share of the RSS sources in the corpus emit `<description>` as the
    title with the outlet appended, so the row would print the same sentence
    twice — once as the link and once as the grey line under it.
    """
    summary = (article.get("summary", "") or "").strip()
    title = (article.get("title", "") or "").strip()
    if not summary or not title:
        return summary[:400]
    if _norm(summary).startswith(_norm(title)):
        return ""
    return summary[:400]


def _ago(seconds: float) -> str:
    if seconds < 90:
        return "just now"
    if seconds < 5400:
        return f"{int(seconds // 60)}m ago"
    if seconds < 172800:
        return f"{int(seconds // 3600)}h ago"
    return f"{int(seconds // 86400)}d ago"
