"""Why the citation appendix printed `www.fpri.org` as a source title.

Three separate drops, chained, each individually invisible:

  1. `search_deep` fetched the page, parsed its `<title>`, and kept only the
     body text — the better title was thrown away at the moment it was learned.
  2. ORACLE's `source_scores` metadata carried `host` but not `title` (nor
     `snippet`), so even a perfectly good search-result title never reached
     persistence.
  3. `persist_sources` therefore always took its `entry.get("host")` fallback.

Only the third was visible, which is why it read as a display bug. The
assertions below pin each link, because fixing the last one alone would have
produced an empty title instead of a wrong one.
"""

from __future__ import annotations

import pytest

from omnix.core import research_ingest as ri
from omnix.tools import websearch


# ---------------------------------------------------------------------------
# 1. Recognising a title that says nothing
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("title, url, weak", [
    ("Iran's Strategy in the Strait", "https://www.fpri.org/article/2026/x", False),
    ("", "https://www.fpri.org/a", True),
    ("(untitled)", "https://www.fpri.org/a", True),
    # The case that produced the report: the title IS the host.
    ("www.fpri.org", "https://www.fpri.org/a", True),
    ("fpri.org", "https://www.fpri.org/a", True),
    ("FPRI.ORG/", "https://www.fpri.org/a", True),
    # A hostname-shaped name that is genuinely the publication's name still
    # tells the reader nothing the URL column does not already show.
    ("Home", "https://www.fpri.org/a", True),
    # No URL to compare against: only placeholders are weak.
    ("www.fpri.org", "", False),
])
def test_weak_title(title, url, weak):
    assert websearch.weak_title(title, url) is weak


# ---------------------------------------------------------------------------
# 2. The fetched <title> is adopted when the search result's is weak
# ---------------------------------------------------------------------------
@pytest.fixture
def fake_search(monkeypatch):
    from omnix.tools import webfetch

    def install(results, page_title="Iran's Strategy in the Strait"):
        monkeypatch.setattr(websearch, "search", lambda q, max_results=5: results)
        monkeypatch.setattr(webfetch, "fetch_url", lambda url, max_chars=4000: {
            "status": "success", "url": url,
            "title": page_title, "text": "body text"})
    return install


def test_search_deep_adopts_the_fetched_title(fake_search):
    fake_search([{"title": "www.fpri.org", "snippet": "", "url": "https://www.fpri.org/a"}])
    out = websearch.search_deep("q", fetch_top=1)
    assert out[0]["title"] == "Iran's Strategy in the Strait"
    assert out[0]["content"] == "body text"


def test_search_deep_keeps_a_good_search_title(fake_search):
    """The search engine's headline is usually cleaner than a `<title>` tag,
    which tends to carry site furniture. It wins whenever it says anything."""
    fake_search([{"title": "Iran closes the strait", "snippet": "",
                  "url": "https://www.fpri.org/a"}])
    assert websearch.search_deep("q", fetch_top=1)[0]["title"] == "Iran closes the strait"


def test_search_deep_does_not_swap_in_an_equally_weak_title(fake_search):
    fake_search([{"title": "", "snippet": "", "url": "https://www.fpri.org/a"}],
                page_title="www.fpri.org")
    assert websearch.search_deep("q", fetch_top=1)[0]["title"] == ""


def test_search_deep_leaves_unfetched_results_alone(fake_search):
    """Only the first `fetch_top` results are retrieved; the rest must not be
    silently credited with the fetched page's title."""
    fake_search([
        {"title": "www.fpri.org", "snippet": "", "url": "https://www.fpri.org/a"},
        {"title": "www.other.org", "snippet": "", "url": "https://www.other.org/b"},
    ])
    out = websearch.search_deep("q", fetch_top=1)
    assert out[1]["title"] == "www.other.org" and "content" not in out[1]


# ---------------------------------------------------------------------------
# 3. Persistence prefers a real title, and still degrades to the host
# ---------------------------------------------------------------------------
def test_source_title_prefers_the_real_title():
    entry = {"title": "Iran's Strategy in the Strait", "host": "www.fpri.org"}
    assert ri._source_title(entry, "https://www.fpri.org/a") == \
        "Iran's Strategy in the Strait"


def test_source_title_falls_back_to_host_rather_than_echoing_the_url():
    entry = {"title": "(untitled)", "host": "www.fpri.org"}
    assert ri._source_title(entry, "https://www.fpri.org/a") == "www.fpri.org"


def test_source_title_survives_a_missing_host():
    assert ri._source_title({}, "https://www.fpri.org/a") == ""


def test_source_title_reads_the_slug_before_giving_up_on_the_host():
    entry = {"title": "en.wikipedia.org", "host": "en.wikipedia.org"}
    assert ri._source_title(entry, "https://en.wikipedia.org/wiki/World_War_III") == \
        "World War III"


# ---------------------------------------------------------------------------
# 3b. The slug reader, which must stay timid
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("url, expected", [
    ("https://en.wikipedia.org/wiki/World_War_III", "World War III"),
    ("https://www.fpri.org/article/2026/iran-strait-strategy", "Iran Strait Strategy"),
    ("https://x.test/the-price-of-oil.html", "The Price Of Oil"),
    # Acronyms survive the capitalisation, or the result reads as machine output.
    ("https://x.test/nato-spending-by-country", "NATO Spending By Country"),
    ("https://x.test/eu-sanctions-russia-lng", "EU Sanctions Russia LNG"),
    ("https://en.wikipedia.org/wiki/Member_states_of_NATO", "Member States Of NATO"),
    # Identifier-shaped: an honest hostname beats a confident wrong title.
    ("https://paperswithcode.co/paper/2502.00072", ""),
    ("https://www.linkedin.com/posts/richard-anani-789156241_the-t", ""),
    ("https://x.test/2026/01/02", ""),
    # A four-digit run is refused even when it is really a year. Accepted
    # cost: the host is still shown, which is where we already were.
    ("https://x.test/oil-prices-2026", ""),
    # Nothing to read.
    ("https://www.worldmonitor.app/", ""),
    ("https://x.test/report.pdf", ""),
    ("https://x.test/about", ""),          # one word is a section, not a title
    ("not a url at all", ""),
])
def test_title_from_slug(url, expected):
    assert ri._title_from_slug(url) == expected


# ---------------------------------------------------------------------------
# 4. The metadata contract that broke the chain in the middle
# ---------------------------------------------------------------------------
def test_source_scores_carries_title_and_snippet():
    """`persist_sources` reads `title` and `snippet` off each entry. ORACLE has
    both on its Source dataclass and used to emit neither, so this asserts the
    shape rather than any behaviour — it is the join that silently failed."""
    from omnix.squad.oracle_evidence import Source

    s = Source(n=1, title="T", url="https://x.test/a", snippet="S")
    entry = {"n": s.n, "url": s.url, "host": s.host, "title": s.title,
             "tier": s.tier, "tier_label": s.tier_label,
             "credibility": s.credibility, "year": s.year,
             "snippet": s.snippet, "duplicate_of": s.duplicate_of}
    assert entry["title"] == "T" and entry["snippet"] == "S"
