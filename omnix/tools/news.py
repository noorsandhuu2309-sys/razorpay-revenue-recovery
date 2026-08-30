"""Free, key-less news headlines via Google News RSS.

Fetches Google News RSS with httpx (already a dependency) and parses the feed
with the standard library's xml.etree — no feedparser needed. Headlines are
grouped into friendly categories for the Intelligence panel's news feed.
"""

from __future__ import annotations

import re
from urllib.parse import quote_plus
from xml.etree import ElementTree as ET

import httpx

# Category label -> keywords that route a headline into it. Order matters: the
# first matching category wins, so more specific buckets come first.
_CATEGORIES: list[tuple[str, tuple[str, ...]]] = [
    ("🔴 Breaking", ("breaking", "alert", "urgent", "crisis", "dead", "killed")),
    ("🇮🇳 India", ("india", "delhi", "mumbai", "kolkata", "chennai", "bengaluru", "bangalore", "modi", "imd")),
    ("💼 Business", ("stock", "market", "business", "economy", "sensex", "nifty", "rupee", "fdi", "startup", "ipo")),
    ("⚽ Sports", ("cricket", "football", "sports", "ipl", "medal", "match", "fifa", "olympic", "tennis")),
    ("🔬 Tech", ("ai", "tech", "technology", "science", "chip", "software", "google", "apple", "openai", "space")),
    ("🌡️ Weather", ("weather", "temperature", "heat", "rain", "cyclone", "monsoon", "storm", "flood")),
    ("🌍 World", ("us", "china", "russia", "world", "global", "ukraine", "gaza", "israel", "un ")),
    ("🎬 Entertainment", ("film", "movie", "actor", "actress", "celebrity", "box office", "bollywood", "music")),
]

DEFAULT_QUERY = "top stories"

# Region detection for the RSS locale parameters.
_REGIONS = {
    ("us", "usa", "america", "american"): ("US", "en-US"),
    ("uk", "britain", "british", "england"): ("GB", "en-GB"),
}


def _locale_for(query: str) -> tuple[str, str]:
    q = query.lower()
    for words, (region, lang) in _REGIONS.items():
        if any(w in q for w in words):
            return region, lang
    return "IN", "en-IN"  # default to India, matching the original app


def _strip_source_suffix(title: str) -> str:
    """Google News titles look like 'Headline text - The Publisher'."""
    if " - " in title:
        return title.rsplit(" - ", 1)[0].strip()
    return title.strip()


def _categorize(title: str) -> str:
    low = title.lower()
    for label, keywords in _CATEGORIES:
        if any(k in low for k in keywords):
            return label
    return "📰 General"


def fetch_raw(query: str = DEFAULT_QUERY, max_results: int = 30, timeout: float = 10.0) -> list[dict]:
    """Return raw articles [{title, url, source, published}] from Google News RSS."""
    region, lang = _locale_for(query)
    q = query
    if any(w in query.lower() for w in ("today", "latest", "recent")):
        q = f"{query} when:1d"  # last 24h for time-sensitive queries
    url = (
        f"https://news.google.com/rss/search?q={quote_plus(q)}"
        f"&hl={lang}&gl={region}&ceid={region}:en"
    )

    resp = httpx.get(url, timeout=timeout, follow_redirects=True,
                     headers={"User-Agent": "Mozilla/5.0 (OMNIX news)"})
    resp.raise_for_status()

    root = ET.fromstring(resp.content)
    articles: list[dict] = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        if not title:
            continue
        link = (item.findtext("link") or "").strip()
        source_el = item.find("source")
        source = (source_el.text if source_el is not None else "") or "Google News"
        published = (item.findtext("pubDate") or "").strip()
        articles.append(
            {"title": title, "url": link, "source": source.strip(), "published": published}
        )
        if len(articles) >= max_results:
            break
    return articles


def get_headlines(query: str = DEFAULT_QUERY, max_results: int = 24,
                  per_category: int = 5) -> dict:
    """Categorized headlines for the news sidebar.

    Returns {status, headlines:[{category,title,url,source}], sources, error?}.
    Never raises — failures come back as status='error' so the UI can show a
    friendly message (and fall back to cache).
    """
    try:
        raw = fetch_raw(query, max_results=max(max_results * 2, 30))
    except Exception as e:  # network down, parse error, etc.
        return {"status": "error", "headlines": [], "sources": [], "error": str(e)}

    if not raw:
        return {"status": "error", "headlines": [], "sources": [], "error": "no articles"}

    buckets: dict[str, list[dict]] = {}
    seen: set[str] = set()
    for art in raw:
        clean = _strip_source_suffix(art["title"])
        key = re.sub(r"\W+", "", clean.lower())[:60]
        if key in seen:
            continue
        seen.add(key)
        cat = _categorize(clean)
        buckets.setdefault(cat, []).append(
            {"category": cat, "title": clean, "url": art["url"], "source": art["source"]}
        )

    # Preserve category priority order, cap per category, then overall.
    ordered_labels = [label for label, _ in _CATEGORIES] + ["📰 General"]
    headlines: list[dict] = []
    for label in ordered_labels:
        for item in buckets.get(label, [])[:per_category]:
            headlines.append(item)

    return {
        "status": "success",
        "headlines": headlines[:max_results],
        "sources": ["Google News RSS"],
    }


if __name__ == "__main__":  # quick manual check
    import json

    print(json.dumps(get_headlines(), indent=2, ensure_ascii=False))
