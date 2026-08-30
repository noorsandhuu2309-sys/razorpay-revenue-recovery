"""Shared internet-knowledge base + background sync for the OMNIX agent squad.

A silent daemon periodically fetches web results for a set of tracked topics and
stores them in a size-capped local cache. Every squad unit and subagent reads
from this cache (through the helpers in ``squad/base.py``), so their answers stay
current AND keep working offline — the cache is served when the network is down.

Design notes
------------
* Keyword-scored recall (token overlap + title/topic/recency boosts). No
  embedding model is required, so it works on any OMNIX install.
* The daemon "learns" what the agents care about: whenever an agent recalls a
  query, ``note_interest`` records it as a topic, and the next sync keeps it
  fresh. Seed topics cover broadly-useful current events out of the box.
* Everything degrades gracefully — every network / disk op is guarded, and the
  store is a plain JSON file written atomically via ``persistence.save_json``.
"""

from __future__ import annotations

import re
import threading
import time
from datetime import datetime, timezone

from .persistence import resolve, load_json, save_json
from .tools import websearch

_STORE = "omnix_agent_knowledge.json"

_MAX_DOCS = 400        # hard cap on cached documents (size control)
_MAX_TOPICS = 60       # hard cap on tracked topics
_DOC_TTL_H = 72.0      # a topic older than this is due for a refresh
_CONTENT_CHARS = 1400  # trim stored page text to keep the store small

# Broadly-useful current-events topics kept warm out of the box.
DEFAULT_TOPICS = [
    "latest technology news",
    "artificial intelligence breakthroughs",
    "world news today",
    "latest cybersecurity threats and CVEs",
    "stock market news today",
    "software development best practices trends",
    "recent science discoveries",
]

_STOP = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "is",
    "are", "be", "by", "from", "at", "as", "it", "this", "that", "these", "those",
    "was", "were", "will", "would", "can", "could", "should", "has", "have", "had",
    "but", "not", "you", "your", "our", "their", "its", "about", "into", "over",
    "what", "which", "who", "how", "why", "when", "where", "latest", "today", "new",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso() -> str:
    return _now().isoformat()


def _tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]{3,}", (text or "").lower()) if t not in _STOP]


def _age_hours(iso_ts: str) -> float:
    try:
        dt = datetime.fromisoformat(iso_ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (_now() - dt).total_seconds() / 3600.0
    except Exception:
        return 1e9


class KnowledgeBase:
    """Thread-safe, size-capped store of web documents with offline recall."""

    def __init__(self, store: str = _STORE):
        self.path = resolve(store)
        self._lock = threading.RLock()
        data = load_json(self.path, None)
        if not isinstance(data, dict):
            data = {}
        data.setdefault("docs", [])
        data.setdefault("topics", [])
        data.setdefault("meta", {"created": _iso(), "syncs": 0, "last_sync": None})
        self.data = data
        self._seed_topics()
        self._save()

    # -- topics ---------------------------------------------------------------
    def _seed_topics(self) -> None:
        have = {t.get("q") for t in self.data["topics"]}
        for q in DEFAULT_TOPICS:
            if q not in have:
                self.data["topics"].append(
                    {"q": q, "added": _iso(), "hits": 0, "seed": True, "synced": None}
                )

    def note_interest(self, query: str) -> None:
        """Record a query the agents care about so the daemon keeps it fresh."""
        q = (query or "").strip()
        if len(q) < 3:
            return
        with self._lock:
            for t in self.data["topics"]:
                if t.get("q", "").lower() == q.lower():
                    t["hits"] = t.get("hits", 0) + 1
                    return
            self.data["topics"].append(
                {"q": q, "added": _iso(), "hits": 1, "seed": False, "synced": None}
            )
            self._evict_topics()
            self._save()

    def _evict_topics(self) -> None:
        if len(self.data["topics"]) <= _MAX_TOPICS:
            return
        # Keep all seeds; drop the least-used learned topics.
        learned = [t for t in self.data["topics"] if not t.get("seed")]
        learned.sort(key=lambda t: (t.get("hits", 0), t.get("added", "")))
        drop = len(self.data["topics"]) - _MAX_TOPICS
        victims = set(id(t) for t in learned[:drop])
        self.data["topics"] = [t for t in self.data["topics"] if id(t) not in victims]

    def topics_to_refresh(self, force_all: bool = False) -> list[dict]:
        with self._lock:
            if force_all:
                return list(self.data["topics"])
            due = []
            for t in self.data["topics"]:
                synced = t.get("synced")
                if synced is None or _age_hours(synced) >= _DOC_TTL_H:
                    due.append(t)
            # If nothing is due, still refresh the few most-wanted topics so the
            # cache never goes fully stale.
            if not due:
                ranked = sorted(self.data["topics"], key=lambda t: t.get("hits", 0), reverse=True)
                due = ranked[:3]
            return due

    # -- writes ---------------------------------------------------------------
    def remember(self, topic: str, results: list[dict]) -> int:
        """Store search results (each: title/snippet/url, optional content)."""
        stored = 0
        with self._lock:
            by_url = {d.get("url"): d for d in self.data["docs"] if d.get("url")}
            for r in results or []:
                url = (r.get("url") or "").strip()
                title = (r.get("title") or "").strip()
                if not url and not title:
                    continue
                content = (r.get("content") or r.get("snippet") or "").strip()
                doc = by_url.get(url)
                blob = f"{title} {r.get('snippet','')} {content} {topic}"
                fields = {
                    "topic": topic,
                    "title": title,
                    "url": url,
                    "snippet": (r.get("snippet") or "").strip()[:400],
                    "content": content[:_CONTENT_CHARS],
                    "fetched": _iso(),
                    "tok": _tokens(blob)[:60],
                }
                if doc:
                    doc.update(fields)
                else:
                    fields["hits"] = 0
                    self.data["docs"].append(fields)
                    by_url[url] = fields
                stored += 1
            # stamp the topic as synced
            for t in self.data["topics"]:
                if t.get("q") == topic:
                    t["synced"] = _iso()
                    break
            self._evict_docs()
            self._save()
        return stored

    def _evict_docs(self) -> None:
        if len(self.data["docs"]) <= _MAX_DOCS:
            return
        # Importance = hits, tie-broken by freshness. Drop the weakest.
        self.data["docs"].sort(
            key=lambda d: (d.get("hits", 0), d.get("fetched", "")), reverse=True
        )
        self.data["docs"] = self.data["docs"][:_MAX_DOCS]

    def mark_sync(self) -> None:
        with self._lock:
            self.data["meta"]["syncs"] = self.data["meta"].get("syncs", 0) + 1
            self.data["meta"]["last_sync"] = _iso()
            self._save()

    # -- read (offline) -------------------------------------------------------
    def recall(self, query: str, k: int = 5) -> list[dict]:
        qtok = set(_tokens(query))
        with self._lock:
            docs = self.data["docs"]
            if not qtok:
                ranked = sorted(docs, key=lambda d: d.get("fetched", ""), reverse=True)[:k]
                return [self._public(d) for d in ranked]
            scored = []
            for d in docs:
                s = self._score(d, qtok)
                if s > 0:
                    scored.append((s, d))
            scored.sort(key=lambda x: x[0], reverse=True)
            out = []
            for _s, d in scored[:k]:
                d["hits"] = d.get("hits", 0) + 1
                out.append(self._public(d))
            if out:
                self._save()
            return out

    @staticmethod
    def _score(doc: dict, qtok: set) -> float:
        dtok = set(doc.get("tok", []))
        overlap = len(qtok & dtok)
        if overlap == 0:
            return 0.0
        title_tok = set(_tokens(doc.get("title", "")))
        topic_tok = set(_tokens(doc.get("topic", "")))
        boost = 0.6 * len(qtok & title_tok) + 0.35 * len(qtok & topic_tok)
        recency = max(0.0, 1.0 - _age_hours(doc.get("fetched", "")) / (_DOC_TTL_H * 4))
        return overlap + boost + recency

    @staticmethod
    def _public(doc: dict) -> dict:
        return {
            "title": doc.get("title", ""),
            "url": doc.get("url", ""),
            "snippet": doc.get("snippet", ""),
            "content": doc.get("content", ""),
            "topic": doc.get("topic", ""),
            "fetched": doc.get("fetched", ""),
        }

    def _save(self) -> None:
        save_json(self.path, self.data)

    def status(self) -> dict:
        with self._lock:
            return {
                "docs": len(self.data["docs"]),
                "topics": len(self.data["topics"]),
                "syncs": self.data["meta"].get("syncs", 0),
                "last_sync": self.data["meta"].get("last_sync"),
            }


class KnowledgeSync:
    """Background thread that keeps the knowledge base warm (mirrors the news
    BackgroundUpdater pattern). Silent, resilient, and stop-responsive."""

    def __init__(self, kb: KnowledgeBase, interval_minutes: int = 20,
                 per_topic: int = 4, fetch_top: int = 2):
        self.kb = kb
        self.interval = max(120, interval_minutes * 60)
        self.per_topic = per_topic
        self.fetch_top = fetch_top
        self.running = False
        self.thread: threading.Thread | None = None
        self.last_run: str | None = None
        self.last_error: str | None = None

    def start(self) -> None:
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.running = False

    def _loop(self) -> None:
        time.sleep(20)  # let the server settle before the first fetch
        while self.running:
            try:
                self._sync_once()
                self.last_run = _iso()
                self.last_error = None
            except Exception as e:  # never let the thread die
                self.last_error = str(e)
                print(f"[knowledge_sync] error: {e}")
            slept = 0
            while self.running and slept < self.interval:
                time.sleep(2)
                slept += 2

    def _sync_once(self) -> None:
        topics = self.kb.topics_to_refresh()
        for t in topics:
            if not self.running:
                break
            q = t.get("q")
            if not q:
                continue
            try:
                results = websearch.search_deep(q, max_results=self.per_topic,
                                                 fetch_top=self.fetch_top)
                self.kb.remember(q, results)
            except Exception as e:
                self.last_error = f"{q}: {e}"
            time.sleep(1)  # be gentle to the network
        self.kb.mark_sync()

    def sync_now(self) -> dict:
        """Run one synchronous sync pass (used for verification / manual refresh).

        Temporarily marks the engine running so the per-topic stop-check inside
        ``_sync_once`` doesn't short-circuit the pass when the daemon isn't
        already looping."""
        was_running = self.running
        self.running = True
        try:
            self._sync_once()
        finally:
            self.running = was_running
        self.last_run = _iso()
        return self.status()

    def status(self) -> dict:
        s = self.kb.status()
        s.update({
            "running": self.running,
            "interval_minutes": self.interval // 60,
            "last_run": self.last_run,
            "last_error": self.last_error,
        })
        return s


# ---------------------------------------------------------------------------
# Process-wide singletons (shared across the server, squad units and subagents).
# ---------------------------------------------------------------------------
_KB: KnowledgeBase | None = None
_KB_LOCK = threading.Lock()


def shared_kb() -> KnowledgeBase:
    global _KB
    with _KB_LOCK:
        if _KB is None:
            _KB = KnowledgeBase()
        return _KB
