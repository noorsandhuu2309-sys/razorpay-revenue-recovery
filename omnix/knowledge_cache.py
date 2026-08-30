"""Lightweight JSON knowledge cache with TTL and fuzzy lookup.

Ported from the external OMNIX KnowledgeCache but with stdlib difflib instead of
thefuzz (no extra dependency). Used to keep the news feed responsive/offline by
serving the last good result when the network is down.
"""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

from .persistence import resolve, save_json

_CATEGORIES = ("news", "facts", "weather", "general")


class KnowledgeCache:
    def __init__(self, cache_file: str | Path = "omnix_knowledge_cache.json"):
        self.cache_file = resolve(cache_file)
        self._lock = threading.Lock()
        self.cache = self._load()

    def _empty(self) -> dict:
        return {c: {} for c in _CATEGORIES} | {
            "metadata": {"created": datetime.now().isoformat(), "hits": 0, "misses": 0}
        }

    def _load(self) -> dict:
        try:
            if self.cache_file.exists():
                with self.cache_file.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                for c in _CATEGORIES:
                    data.setdefault(c, {})
                data.setdefault("metadata", {"hits": 0, "misses": 0})
                return data
        except Exception:
            pass
        return self._empty()

    def _save(self) -> None:
        # Atomic: `open("w")` truncates first, so a crash or a kill mid-write
        # leaves a half-written file that `_load` cannot parse — and `_load`
        # swallows that and returns an empty cache, silently discarding
        # everything the background updater has collected. Compact, because
        # this store runs to hundreds of KB and indentation is dead weight.
        save_json(self.cache_file, self.cache, indent=None)

    @staticmethod
    def _key(query: str) -> str:
        return hashlib.md5(query.lower().strip().encode()).hexdigest()

    def get(self, query: str, category: str = "general",
            max_age_hours: float | None = None) -> tuple[bool, object, float]:
        """Return (found, data, age_hours)."""
        with self._lock:
            if category not in self.cache:
                category = "general"
            entry = self.cache[category].get(self._key(query))
            if not entry:
                entry = self._find_similar(query, category)
            if entry:
                age = (datetime.now() - datetime.fromisoformat(entry["cached_at"])).total_seconds() / 3600
                if max_age_hours is not None and age > max_age_hours:
                    self.cache["metadata"]["misses"] += 1
                    return False, None, age
                self.cache["metadata"]["hits"] += 1
                return True, entry["data"], age
            self.cache["metadata"]["misses"] += 1
            return False, None, 0.0

    def _find_similar(self, query: str, category: str, min_ratio: float = 0.85):
        ql = query.lower()
        best, best_score = None, 0.0
        for entry in self.cache[category].values():
            orig = entry.get("original_query", "").lower()
            score = SequenceMatcher(None, ql, orig).ratio()
            if score > best_score and score >= min_ratio:
                best, best_score = entry, score
        return best

    def set(self, query: str, data: object, category: str = "general",
            source: str = "unknown") -> None:
        with self._lock:
            if category not in self.cache:
                category = "general"
            self.cache[category][self._key(query)] = {
                "original_query": query,
                "data": data,
                "source": source,
                "cached_at": datetime.now().isoformat(),
            }
            self._save()

    def stats(self) -> dict:
        meta = self.cache.get("metadata", {})
        hits, misses = meta.get("hits", 0), meta.get("misses", 0)
        total = hits + misses
        return {
            "hits": hits,
            "misses": misses,
            "hit_rate": round(hits / total * 100, 1) if total else 0.0,
            "entries": {c: len(self.cache.get(c, {})) for c in _CATEGORIES},
        }
