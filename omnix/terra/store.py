"""The article corpus — TERRA's system of record.

Every other module reads from here. An Article is kept as a flat dict rather
than a dataclass because it round-trips to JSON on every save and gains derived
fields (entities, cluster id, domain tags) as the pipeline stages run over it;
a rigid schema would mean touching this file for every new derived field.

Retention is by count, oldest first. A rolling window of ~1500 articles is about
three days of multi-source world coverage — enough for velocity/burst detection
and event timelines, small enough that a full TF-IDF rebuild is milliseconds.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STORE_PATH = ROOT / "omnix_terra_store.json"

MAX_ARTICLES = 1500
SCHEMA = 1


def article_id(url: str, title: str = "") -> str:
    """Stable id per (outlet, story). Keyed on URL so the same wire story
    published by five outlets stays five records — cross-source verification
    needs them separate; clustering is what groups them back together."""
    basis = (url or "").strip() or (title or "").strip()
    return hashlib.sha1(basis.encode("utf-8", "replace")).hexdigest()[:16]


def parse_published(value: str) -> float:
    """RFC-822 (RSS) or ISO date -> epoch seconds. Now, if unparseable."""
    value = (value or "").strip()
    if not value:
        return time.time()
    try:
        return parsedate_to_datetime(value).timestamp()
    except Exception:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d"):
        try:
            import datetime as _dt
            return _dt.datetime.strptime(value, fmt).timestamp()
        except Exception:
            continue
    return time.time()


class Store:
    """Thread-safe article corpus with JSON persistence.

    Writes are debounced: the ingest loop adds a few hundred articles in a burst
    and there is no reason to serialize 1500 records after each one.
    """

    def __init__(self, path: Path | str = STORE_PATH):
        self.path = Path(path)
        self._lock = threading.RLock()
        self.articles: dict[str, dict] = {}
        self.meta: dict = {"last_ingest": 0.0, "ingest_count": 0,
                           "sources_seen": {}, "schema": SCHEMA}
        self._dirty = False
        self._last_save = 0.0
        self._load()

    # -- persistence ---------------------------------------------------------
    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return
        if raw.get("meta", {}).get("schema") != SCHEMA:
            return  # shape changed — start clean rather than half-migrate
        arts = raw.get("articles") or {}
        if isinstance(arts, dict):
            self.articles = arts
        self.meta.update(raw.get("meta") or {})

    def save(self, force: bool = False) -> None:
        with self._lock:
            if not self._dirty and not force:
                return
            if not force and time.time() - self._last_save < 20:
                return
            payload = {"meta": self.meta, "articles": self.articles}
            self._last_save = time.time()
            self._dirty = False
        try:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False),
                           encoding="utf-8")
            tmp.replace(self.path)
        except Exception:
            pass  # a corpus we can't persist is still a corpus we can use

    # -- writes --------------------------------------------------------------
    def upsert(self, art: dict) -> tuple[str, bool]:
        """Add or refresh one article. Returns (id, is_new).

        Existing records keep their derived fields — re-seeing a headline in the
        next crawl must not throw away the entities we already extracted from it.
        """
        aid = art.get("id") or article_id(art.get("url", ""), art.get("title", ""))
        art["id"] = aid
        with self._lock:
            prior = self.articles.get(aid)
            if prior:
                prior["seen_count"] = prior.get("seen_count", 1) + 1
                prior["last_seen"] = time.time()
                for key in ("title", "summary", "source", "url"):
                    if art.get(key) and not prior.get(key):
                        prior[key] = art[key]
                self._dirty = True
                return aid, False
            art.setdefault("first_seen", time.time())
            art.setdefault("last_seen", time.time())
            art.setdefault("seen_count", 1)
            art.setdefault("entities", [])
            art.setdefault("countries", [])
            art.setdefault("domains", [])
            art.setdefault("cluster", "")
            self.articles[aid] = art
            src = (art.get("source") or "unknown").strip()
            self.meta.setdefault("sources_seen", {})
            self.meta["sources_seen"][src] = self.meta["sources_seen"].get(src, 0) + 1
            self.meta["ingest_count"] = self.meta.get("ingest_count", 0) + 1
            self._dirty = True
            return aid, True

    def patch(self, aid: str, **fields) -> None:
        with self._lock:
            art = self.articles.get(aid)
            if art is None:
                return
            art.update(fields)
            self._dirty = True

    def prune(self, max_articles: int = MAX_ARTICLES) -> list[str]:
        """Drop the oldest records past the cap. Returns the ids removed so
        callers can evict them from their own indexes."""
        with self._lock:
            if len(self.articles) <= max_articles:
                return []
            ordered = sorted(self.articles.values(),
                             key=lambda a: a.get("published_ts", 0))
            drop = ordered[: len(self.articles) - max_articles]
            ids = [a["id"] for a in drop]
            for aid in ids:
                self.articles.pop(aid, None)
            self._dirty = True
            return ids

    # -- reads ---------------------------------------------------------------
    def get(self, aid: str) -> dict | None:
        return self.articles.get(aid)

    def all(self) -> list[dict]:
        with self._lock:
            return list(self.articles.values())

    def recent(self, hours: float = 48.0, limit: int = 0) -> list[dict]:
        cutoff = time.time() - hours * 3600
        out = [a for a in self.all() if a.get("published_ts", 0) >= cutoff]
        out.sort(key=lambda a: -a.get("published_ts", 0))
        return out[:limit] if limit else out

    def by_country(self, iso: str, hours: float = 72.0) -> list[dict]:
        iso = (iso or "").upper()
        return [a for a in self.recent(hours) if iso in (a.get("countries") or [])]

    def by_entity(self, entity_id: str, hours: float = 168.0) -> list[dict]:
        return [a for a in self.recent(hours)
                if any(e.get("id") == entity_id for e in (a.get("entities") or []))]

    def by_domain(self, domain: str, hours: float = 48.0) -> list[dict]:
        return [a for a in self.recent(hours) if domain in (a.get("domains") or [])]

    def stats(self) -> dict:
        arts = self.all()
        now = time.time()
        return {
            "articles": len(arts),
            "last_24h": sum(1 for a in arts if now - a.get("published_ts", 0) < 86400),
            "sources": len({a.get("source") for a in arts if a.get("source")}),
            "last_ingest": self.meta.get("last_ingest", 0),
            "ingest_count": self.meta.get("ingest_count", 0),
            "oldest": min((a.get("published_ts", now) for a in arts), default=now),
            "path": str(self.path),
        }


_shared: Store | None = None
_shared_lock = threading.Lock()


def shared() -> Store:
    global _shared
    with _shared_lock:
        if _shared is None:
            _shared = Store()
        return _shared
