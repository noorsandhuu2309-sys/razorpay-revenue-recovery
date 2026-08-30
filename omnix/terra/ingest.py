"""Multi-source news ingestion for TERRA.

The existing /api/news path fetches one Google News RSS query and is perfect for
what it does — a headline sidebar. Cross-source verification needs the opposite:
the SAME story from several named outlets, so the outlet attribution has to
survive the fetch. So this module reads outlet feeds directly where they exist,
and falls back to Google News `site:` scoping for the outlets that killed their
public RSS (Reuters and AP both did).

Feeds are fetched concurrently — thirty sequential RSS round-trips is 20+
seconds, concurrently it is under three. Every fetch is individually guarded so
one dead feed never stalls a crawl.
"""

from __future__ import annotations

import html
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote_plus
from xml.etree import ElementTree as ET

import httpx

from . import nlp
from .store import article_id, parse_published

UA = "Mozilla/5.0 (compatible; OMNIX-TERRA/1.0; +local intelligence map)"

# ---------------------------------------------------------------------------
# Sources
#
# `kind` is how the URL is built:
#   'rss'  — a real outlet feed; the outlet name is authoritative
#   'gnews'— a Google News query; the outlet comes from the <source> element
# `domains` pre-tags every article from that feed for the domain agents, which
# saves the classifier work on feeds that are single-subject by construction.
# ---------------------------------------------------------------------------
OUTLET_FEEDS: list[dict] = [
    {"name": "BBC",          "kind": "rss", "url": "https://feeds.bbci.co.uk/news/world/rss.xml"},
    {"name": "Al Jazeera",   "kind": "rss", "url": "https://www.aljazeera.com/xml/rss/all.xml"},
    {"name": "The Guardian", "kind": "rss", "url": "https://www.theguardian.com/world/rss"},
    {"name": "NPR",          "kind": "rss", "url": "https://feeds.npr.org/1004/rss.xml"},
    {"name": "Deutsche Welle","kind": "rss","url": "https://rss.dw.com/rdf/rss-en-world"},
    {"name": "France 24",    "kind": "rss", "url": "https://www.france24.com/en/rss"},
    {"name": "CNN",          "kind": "rss", "url": "http://rss.cnn.com/rss/edition_world.rss"},
    {"name": "The Hindu",    "kind": "rss", "url": "https://www.thehindu.com/news/international/feeder/default.rss"},
    {"name": "Times of India","kind": "rss","url": "https://timesofindia.indiatimes.com/rssfeeds/296589292.cms"},
    {"name": "Sky News",     "kind": "rss", "url": "https://feeds.skynews.com/feeds/rss/world.xml"},
    {"name": "CBS News",     "kind": "rss", "url": "https://www.cbsnews.com/latest/rss/world"},
    {"name": "CNBC",         "kind": "rss", "url": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100727362",
     "domains": ["economic"]},
    {"name": "The Straits Times", "kind": "rss", "url": "https://www.straitstimes.com/news/world/rss.xml"},
    # Wire services — no usable public feed any more, so scoped via Google News.
    {"name": "Reuters",           "kind": "gnews", "q": "site:reuters.com when:2d"},
    {"name": "Associated Press",  "kind": "gnews", "q": "site:apnews.com when:2d"},
    {"name": "Bloomberg",         "kind": "gnews", "q": "site:bloomberg.com when:2d", "domains": ["economic"]},
]

# Topic sweeps. These are what give each domain agent something to read even on
# a day when the general world feeds are dominated by one story.
TOPIC_QUERIES: list[dict] = [
    {"q": "world news when:1d",                    "domains": ["news"]},
    {"q": "breaking news when:1d",                 "domains": ["news"]},
    {"q": "conflict OR military OR troops OR airstrike when:2d", "domains": ["military"]},
    {"q": "sanctions OR embargo OR tariffs when:2d", "domains": ["economic", "military"]},
    {"q": "inflation OR recession OR central bank OR interest rates when:2d", "domains": ["economic"]},
    {"q": "oil prices OR OPEC OR natural gas when:2d", "domains": ["economic"]},
    {"q": "supply chain OR semiconductor OR chip exports when:2d", "domains": ["economic"]},
    {"q": "election OR coup OR protest OR parliament when:2d", "domains": ["news"]},
    {"q": "earthquake OR flood OR wildfire OR hurricane OR cyclone when:2d", "domains": ["climate"]},
    {"q": "climate OR drought OR heatwave OR emissions when:2d", "domains": ["climate"]},
    {"q": "cyberattack OR ransomware OR data breach OR hackers when:2d", "domains": ["cyber"]},
    {"q": "outbreak OR epidemic OR public health OR WHO when:2d", "domains": ["health"]},
    {"q": "shipping OR port OR strait OR trade route when:2d", "domains": ["economic"]},
    {"q": "nuclear OR missile OR defense deal when:2d", "domains": ["military"]},
]

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


# Feeds that declare ISO-8859-1 but actually emit CP1252 leave curly quotes and
# dashes sitting in the C1 control range. Left alone they end up inside entity
# names ("Yemen\x92s Houthis") and there is no way to recover the intent later,
# so the repair happens once, here, before anything downstream sees the text.
_C1_RE = re.compile(r"[\x80-\x9f]")


def _repair_mojibake(text: str) -> str:
    # Class 2: UTF-8 bytes decoded as Latin-1, which turns "António" into
    # "AntÃ³nio". Distinctive enough to detect on the marker sequences.
    if "Ã" in text or "â€" in text or "Å" in text:
        try:
            fixed = text.encode("latin-1", "strict").decode("utf-8", "strict")
            text = fixed
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    # Some feeds arrive already lossy — the curly apostrophe was replaced with
    # U+FFFD upstream and the original byte is gone. Between a letter and an "s"
    # the intent is never ambiguous, so restoring the apostrophe there is safe
    # and stops "Morocco?s border" reaching the UI.
    if "�" in text:
        text = re.sub(r"(?<=[A-Za-z])�(?=[a-z])", "'", text)
    if not _C1_RE.search(text):
        return text
    try:
        return text.encode("latin-1", "ignore").decode("cp1252", "ignore")
    except Exception:
        return _C1_RE.sub("'", text)


def _clean(text: str, limit: int = 400) -> str:
    text = html.unescape(_TAG_RE.sub(" ", text or ""))
    text = _repair_mojibake(text)
    text = _WS_RE.sub(" ", text).strip()
    return text[:limit]


def _strip_source_suffix(title: str) -> str:
    """Google News renders titles as 'Headline - The Publisher'."""
    if " - " in title:
        head, tail = title.rsplit(" - ", 1)
        if len(tail) < 45 and head.strip():
            return head.strip()
    return title.strip()


def _fetch(url: str, timeout: float = 12.0) -> bytes | None:
    try:
        r = httpx.get(url, timeout=timeout, follow_redirects=True,
                      headers={"User-Agent": UA,
                               "Accept": "application/rss+xml, application/xml, text/xml, */*"})
        r.raise_for_status()
        return r.content
    except Exception:
        return None


def _parse_feed(content: bytes, default_source: str,
                trust_item_source: bool) -> list[dict]:
    """RSS 2.0 and RDF/Atom, without a feed-parsing dependency."""
    try:
        root = ET.fromstring(content)
    except Exception:
        return []
    out: list[dict] = []

    # RSS/RDF <item> and Atom <entry> — namespace-agnostic tag matching.
    for node in root.iter():
        tag = node.tag.rsplit("}", 1)[-1].lower()
        if tag not in ("item", "entry"):
            continue
        title = link = desc = pub = source = ""
        for child in node:
            ctag = child.tag.rsplit("}", 1)[-1].lower()
            if ctag == "title" and not title:
                title = (child.text or "").strip()
            elif ctag == "link" and not link:
                link = (child.text or "").strip() or (child.get("href") or "").strip()
            elif ctag in ("description", "summary", "content") and not desc:
                desc = (child.text or "").strip()
            elif ctag in ("pubdate", "published", "updated", "date") and not pub:
                pub = (child.text or "").strip()
            elif ctag == "source" and not source:
                source = (child.text or "").strip()
        title = _clean(title, 300)
        if not title or not link:
            continue
        out.append({
            "title": title,
            "url": link,
            "summary": _clean(desc, 400),
            "published": pub,
            "source": (source if (trust_item_source and source) else default_source),
        })
    return out


def fetch_source(spec: dict, max_items: int = 40) -> list[dict]:
    """One feed -> normalized articles. Never raises."""
    if spec.get("kind") == "gnews" or spec.get("q"):
        q = spec.get("q") or ""
        url = (f"https://news.google.com/rss/search?q={quote_plus(q)}"
               f"&hl=en-US&gl=US&ceid=US:en")
        # For a site:-scoped query the outlet is known; for a topic sweep it is
        # whatever Google reports per item.
        pinned = spec.get("name") if spec.get("kind") == "gnews" else ""
        content = _fetch(url)
        if content is None:
            return []
        items = _parse_feed(content, pinned or "Google News",
                            trust_item_source=not pinned)
        for it in items:
            it["title"] = _strip_source_suffix(it["title"])
    else:
        content = _fetch(spec["url"])
        if content is None:
            return []
        items = _parse_feed(content, spec["name"], trust_item_source=False)

    feed_domains = spec.get("domains") or []
    feed_name = spec.get("name") or spec.get("q", "")[:40]
    out = []
    for it in items[:max_items]:
        if len(it["title"]) < 18:      # nav junk, "Live updates", section links
            continue
        out.append({
            "id": article_id(it["url"], it["title"]),
            "title": it["title"],
            "url": it["url"],
            "source": it["source"] or "Unknown",
            "summary": it["summary"],
            "published_ts": parse_published(it["published"]),
            "feed": feed_name,
            "domains": list(feed_domains),
        })
    return out


def crawl(sources: list[dict] | None = None, workers: int = 10,
          per_source: int = 40) -> list[dict]:
    """Fetch every configured source concurrently. Returns raw articles."""
    specs = sources if sources is not None else (OUTLET_FEEDS + TOPIC_QUERIES)
    collected: list[dict] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fetch_source, s, per_source): s for s in specs}
        for fut in as_completed(futures):
            try:
                collected.extend(fut.result() or [])
            except Exception:
                continue
    return collected


# ---------------------------------------------------------------------------
# Deduplication
#
# Two levels, because they solve different problems:
#   * exact  — the same URL seen twice in one crawl (a feed listing a story in
#              two sections). Collapsed silently.
#   * near   — the same story from different outlets. NOT collapsed: those are
#              the records cross-source verification compares. Instead they are
#              linked via `dupe_of` so clustering starts with a head start and
#              the UI can show "reported by 6 outlets" without re-deriving it.
# ---------------------------------------------------------------------------
def dedupe(articles: list[dict], near_threshold: float = 0.55) -> list[dict]:
    by_id: dict[str, dict] = {}
    for art in articles:
        prior = by_id.get(art["id"])
        if prior is None:
            by_id[art["id"]] = art
        else:
            # Same URL twice: keep the richer record.
            if len(art.get("summary", "")) > len(prior.get("summary", "")):
                prior["summary"] = art["summary"]
            prior["domains"] = sorted(set(prior.get("domains", [])) |
                                      set(art.get("domains", [])))

    items = list(by_id.values())
    # Bucket by a cheap key first so near-dupe comparison stays roughly linear
    # instead of comparing every pair of ~600 articles.
    buckets: dict[str, list[dict]] = {}
    for art in items:
        art["_sh"] = nlp.shingles(art["title"], 3)
        tk = nlp.tokens(art["title"])
        key = tk[0][:4] if tk else "_"
        buckets.setdefault(key, []).append(art)
        # A second bucket on the rarest-looking token catches reordered titles.
        if len(tk) > 1:
            buckets.setdefault(max(tk, key=len)[:4], []).append(art)

    for group in buckets.values():
        for i, a in enumerate(group):
            if a.get("dupe_of"):
                continue
            for b in group[i + 1:]:
                if b.get("dupe_of") or b["id"] == a["id"]:
                    continue
                if nlp.jaccard(a["_sh"], b["_sh"]) >= near_threshold:
                    # Point the later/lower-confidence one at the earlier one.
                    head, tail = a, b
                    if nlp.source_confidence(b["source"]) > nlp.source_confidence(a["source"]):
                        head, tail = b, a
                    tail["dupe_of"] = head["id"]
                    head["dupe_count"] = head.get("dupe_count", 0) + 1

    for art in items:
        art.pop("_sh", None)
    return items


def enrich(articles: list[dict]) -> list[dict]:
    """Attach the cheap derived signals every downstream stage expects."""
    now = time.time()
    for art in articles:
        text = art["title"] + " " + art.get("summary", "")
        art["sentiment"] = round(nlp.sentiment(text), 3)
        art["severity"] = round(nlp.severity(text), 3)
        art["confidence"] = round(nlp.source_confidence(art.get("source", "")), 2)
        art["conf_label"] = nlp.confidence_label(art["confidence"])
        # A future-dated or missing timestamp would poison velocity detection.
        ts = art.get("published_ts") or now
        art["published_ts"] = min(float(ts), now + 3600)
    return articles


def run(sources: list[dict] | None = None) -> list[dict]:
    """Full ingest pass: crawl -> dedupe -> enrich. Returns ready articles."""
    return enrich(dedupe(crawl(sources)))
