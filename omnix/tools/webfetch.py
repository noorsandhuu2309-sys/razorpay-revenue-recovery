"""Safe, read-only web page fetching for the research agent.

A trimmed port of the external OMNIX WebFetcher: GET-only, SSRF-protected,
size- and rate-limited. Uses httpx (already a dependency) and a stdlib
HTMLParser for text extraction, so no requests/bs4 needed.
"""

from __future__ import annotations

import ipaddress
import threading
import time
from html.parser import HTMLParser
from urllib.parse import urlparse

import httpx

_MAX_PAGE = 3 * 1024 * 1024  # 3 MB
_TIMEOUT = 10.0
_MAX_PER_MIN = 15
_BLOCKED_SCHEMES = {"file", "ftp", "data", "javascript"}
_SKIP_TAGS = {"script", "style", "noscript", "iframe", "embed", "svg", "head"}

_PRIVATE_RANGES = [
    ipaddress.ip_network(n) for n in (
        "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8",
        "169.254.0.0/16", "::1/128", "fc00::/7", "fe80::/10",
    )
]

_request_times: list[float] = []
_rate_lock = threading.Lock()


class _TextExtractor(HTMLParser):
    """Collect visible text, skipping scripts/styles/etc."""

    def __init__(self) -> None:
        super().__init__()
        self._skip = 0
        self.parts: list[str] = []
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip += 1
        if tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS and self._skip:
            self._skip -= 1
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if self._skip == 0:
            text = data.strip()
            if text:
                self.parts.append(text)


def _check_rate_limit() -> bool:
    # Locked because callers are genuinely concurrent now — `search_deep`
    # fetches its pages in parallel, and several chat streams can be in flight
    # at once. Read-modify-write on a shared list without one lets the limit be
    # overshot, which is the one thing this function exists to prevent.
    with _rate_lock:
        now = time.time()
        _request_times[:] = [t for t in _request_times if now - t < 60]
        if len(_request_times) >= _MAX_PER_MIN:
            return False
        _request_times.append(now)
        return True


def _validate_url(url: str) -> tuple[bool, str]:
    try:
        p = urlparse(url)
        if p.scheme.lower() in _BLOCKED_SCHEMES:
            return False, f"blocked scheme: {p.scheme}"
        if p.scheme.lower() not in ("http", "https"):
            return False, f"only http/https allowed: {p.scheme}"
        host = (p.netloc.split(":")[0] or "").lower()
        if not host:
            return False, "no hostname"
        if any(x in host for x in ("localhost", "0.0.0.0")):
            return False, "localhost blocked"
        try:
            ip = ipaddress.ip_address(host)
            if any(ip in r for r in _PRIVATE_RANGES):
                return False, "private IP blocked"
        except ValueError:
            pass  # a domain name, fine
        return True, ""
    except Exception as e:
        return False, str(e)


def fetch_url(url: str, max_chars: int = 6000) -> dict:
    """Fetch a page and return {status, url, title, text, error?}. Never raises."""
    if not _check_rate_limit():
        return {"status": "error", "url": url, "error": "rate limit exceeded"}
    ok, err = _validate_url(url)
    if not ok:
        return {"status": "error", "url": url, "error": err}
    try:
        with httpx.Client(follow_redirects=True, timeout=_TIMEOUT,
                          headers={"User-Agent": "Mozilla/5.0 (OMNIX research)"}) as client:
            resp = client.get(url)
        clen = resp.headers.get("content-length")
        if clen and int(clen) > _MAX_PAGE:
            return {"status": "error", "url": url, "error": "page too large"}
        content = resp.content[:_MAX_PAGE]
        if resp.status_code != 200:
            return {"status": "error", "url": url, "error": f"HTTP {resp.status_code}"}
        ctype = resp.headers.get("content-type", "").lower()
        raw = content.decode("utf-8", errors="ignore")
        if "html" in ctype or raw.lstrip()[:6].lower().startswith("<html") or "<body" in raw.lower():
            parser = _TextExtractor()
            try:
                parser.feed(raw)
            except Exception:
                pass
            text = " ".join(parser.parts)
            title = parser.title.strip()
        else:
            text, title = raw, ""
        return {
            "status": "success",
            "url": url,
            "title": title,
            "text": text[:max_chars],
        }
    except httpx.TimeoutException:
        return {"status": "error", "url": url, "error": "request timed out"}
    except Exception as e:
        return {"status": "error", "url": url, "error": str(e)}
