"""Free DuckDuckGo web search, used by the research agent."""

from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

from ddgs import DDGS

_PLACEHOLDER_TITLES = {"", "(untitled)", "untitled", "no title", "home", "homepage"}


def weak_title(title: str, url: str = "") -> bool:
    """True when `title` carries no more information than the URL already does.

    A search result whose title is missing, a placeholder, or just the hostname
    tells the reader nothing — and it is the reason a citation appendix prints
    `www.fpri.org` in both the Publisher and Source columns. Detecting it is
    what lets the fetched `<title>` take over.
    """
    t = (title or "").strip()
    if t.lower() in _PLACEHOLDER_TITLES:
        return True
    host = ""
    try:
        host = (urlparse(url).netloc or "").lower().split(":")[0]
    except Exception:
        pass
    if not host:
        return False
    bare = t.lower().rstrip("/")
    return bare in (host, host.removeprefix("www."))


def search(query: str, max_results: int = 5) -> list[dict]:
    """Return up to max_results {title, snippet, url} dicts for a query."""
    results = []
    try:
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=max_results):
                results.append(
                    {
                        "title": r.get("title", ""),
                        "snippet": r.get("body", ""),
                        "url": r.get("href", ""),
                    }
                )
    except Exception as e:
        return [{"title": "search error", "snippet": str(e), "url": ""}]
    return results


def format_results_as_context(results: list[dict]) -> str:
    """Format search results as numbered context for the LLM prompt."""
    if not results:
        return "No search results found."
    lines = []
    for i, r in enumerate(results, start=1):
        lines.append(f"[{i}] {r['title']}\n{r['snippet']}\nURL: {r['url']}")
    return "\n\n".join(lines)


def search_deep(query: str, max_results: int = 5, fetch_top: int = 2) -> list[dict]:
    """Search, then fetch the top few pages for fuller context.

    Same shape as `search()` but each of the first `fetch_top` results gains a
    `content` field with extracted page text (best-effort). Used by the research
    agent for the "intelligent auto-search" pipeline. Falls back gracefully to a
    plain search if page fetching is unavailable.

    A fetched page also carries its `<title>`, which is adopted when the search
    result's own title is weak. The page has already been retrieved at this
    point, so the better title costs nothing extra.
    """
    results = search(query, max_results=max_results)
    try:
        from . import webfetch
    except Exception:
        return results

    targets = [r for r in results[:fetch_top] if r.get("url")]
    if not targets:
        return results

    # Fetched CONCURRENTLY. These are independent network round-trips to
    # unrelated hosts, and `webfetch` allows each 10s, so doing them in
    # sequence put up to 20s of dead air in front of a research answer before
    # the model had even been asked the question — the single largest
    # contributor to research feeling slow. In parallel the cost is the slowest
    # page, not their sum.
    with ThreadPoolExecutor(max_workers=len(targets)) as pool:
        pages = list(pool.map(
            lambda r: webfetch.fetch_url(r["url"], max_chars=4000), targets))

    for r, page in zip(targets, pages):
        if page.get("status") != "success":
            continue
        if page.get("text"):
            r["content"] = page["text"]
        fetched = (page.get("title") or "").strip()
        url = r["url"]
        if fetched and weak_title(r.get("title", ""), url) and not weak_title(fetched, url):
            r["title"] = fetched
    return results


def format_deep_context(results: list[dict]) -> str:
    """Like format_results_as_context but includes fetched page text when present."""
    if not results:
        return "No search results found."
    lines = []
    for i, r in enumerate(results, start=1):
        block = f"[{i}] {r['title']}\n{r['snippet']}\nURL: {r['url']}"
        if r.get("content"):
            block += f"\nEXCERPT: {r['content']}"
        lines.append(block)
    return "\n\n".join(lines)
