"""Caching, deduplication, rate limiting and usage accounting.

Everything that costs money or goodwill passes through this module. It is the
single most important file in the geospatial layer for the stated goal of
minimising API usage, and it does four separable jobs:

  **Cache.** Two tiers. An in-process dict for the hot path, and the platform
  database underneath it so the cache survives a restart — which is what makes
  degraded mode work at all. A cache that empties on reboot cannot answer
  anything when the network is down.

  **Deduplication (single-flight).** Ten map panels asking for the weather at
  the same point during one render must produce ONE network call, not ten. The
  naive cache does not achieve this: all ten miss simultaneously, all ten
  fetch, and nine of the answers are thrown away. So concurrent callers for the
  same key block on the first one's result.

  **Rate limiting.** Per provider, token-bucket. Nominatim's usage policy is a
  hard 1 req/s and being blocked is permanent-ish; Overpass returns 429 and
  sulks. This makes exceeding those limits impossible rather than unlikely.

  **Usage accounting.** Per provider counters — calls, hits, errors, bytes,
  latency — so the cost of a feature is a number someone can look at instead of
  a surprise at the end of the month.

The stale-serving rule is deliberate and is the reason `Freshness.STALE`
exists: when a provider fails, an expired cache entry is better than nothing,
but it is returned *labelled*, and the label travels all the way to the pixel.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable

from .config import settings
from .types import Freshness, Result

# ---------------------------------------------------------------------------
# Keys
# ---------------------------------------------------------------------------
def key_for(kind: str, /, **parts: Any) -> str:
    """A stable cache key.

    Sorted, JSON-serialised and hashed so that argument order cannot produce
    two keys for one question, and so a key is safe to use as a database
    primary key regardless of what went into it. The `kind` prefix stays
    readable because it is what usage reporting groups by.

    `kind` is POSITIONAL-ONLY. Without the `/` any caller whose key parts
    happened to include a field called `kind` — a perfectly natural name for
    "which variant of this query" — hit `TypeError: got multiple values for
    argument 'kind'` from inside the cache, several layers below where the
    mistake was made. Positional-only makes the parameter name unreservable.
    """
    blob = json.dumps(parts, sort_keys=True, default=str, separators=(",", ":"))
    digest = hashlib.sha1(blob.encode("utf-8")).hexdigest()[:20]
    return f"{kind}:{digest}"


def spatial_key(lat: float, lon: float, precision: int = 2) -> tuple[float, float]:
    """Snap a coordinate to a grid so that nearby requests share a cache entry.

    This is the single biggest cost saving in the package and it is worth being
    precise about why. Weather and air quality do not vary meaningfully across
    a kilometre, but GPS jitters by tens of metres every second — so an
    un-snapped key means a fresh paid lookup every time the user's phone
    breathes. At precision=2 (~1.1 km) an afternoon at one desk is one call.

    Precision is per data type, not global: routing and geocoding need the real
    coordinate and must not use this.
    """
    return (round(lat, precision), round(lon, precision))


# ---------------------------------------------------------------------------
# Usage accounting
# ---------------------------------------------------------------------------
@dataclass
class ProviderUsage:
    calls: int = 0
    hits: int = 0
    misses: int = 0
    errors: int = 0
    stale_served: int = 0
    rate_limited: int = 0
    total_ms: float = 0.0
    last_error: str = ""
    last_call_at: float = 0.0

    @property
    def avg_ms(self) -> float:
        return self.total_ms / self.calls if self.calls else 0.0

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "calls": self.calls, "hits": self.hits, "misses": self.misses,
            "errors": self.errors, "staleServed": self.stale_served,
            "rateLimited": self.rate_limited,
            "avgMs": round(self.avg_ms, 1),
            "hitRate": round(self.hit_rate, 3),
            "lastError": self.last_error,
            "lastCallAt": self.last_call_at or None,
        }


_usage: dict[str, ProviderUsage] = defaultdict(ProviderUsage)
_usage_lock = threading.Lock()


def record(provider: str, *, hit: bool = False, miss: bool = False,
           call: bool = False, error: str = "", ms: float = 0.0,
           stale: bool = False, limited: bool = False) -> None:
    with _usage_lock:
        u = _usage[provider]
        if hit:
            u.hits += 1
        if miss:
            u.misses += 1
        if call:
            u.calls += 1
            u.total_ms += ms
            u.last_call_at = time.time()
        if stale:
            u.stale_served += 1
        if limited:
            u.rate_limited += 1
        if error:
            u.errors += 1
            u.last_error = error[:200]


def usage() -> dict[str, Any]:
    with _usage_lock:
        by_provider = {name: u.as_dict() for name, u in _usage.items()}
    totals = {
        "calls": sum(p["calls"] for p in by_provider.values()),
        "hits": sum(p["hits"] for p in by_provider.values()),
        "misses": sum(p["misses"] for p in by_provider.values()),
        "errors": sum(p["errors"] for p in by_provider.values()),
    }
    served = totals["hits"] + totals["misses"]
    totals["hitRate"] = round(totals["hits"] / served, 3) if served else 0.0
    #: The number the whole design exists to produce: requests answered without
    #: touching a provider.
    totals["callsAvoided"] = totals["hits"]
    return {"providers": by_provider, "totals": totals,
            "memoryEntries": len(_mem)}


def reset_usage() -> None:
    with _usage_lock:
        _usage.clear()


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------
class _Bucket:
    """Token bucket, one per provider.

    `acquire` BLOCKS rather than rejecting. For a background enrichment that is
    exactly right; for a user-facing request it trades latency for never being
    banned, which is the correct trade against Nominatim in particular. The
    wait is capped so a request cannot hang forever behind a saturated bucket.
    """

    def __init__(self, rate_per_s: float, burst: float | None = None) -> None:
        self.rate = max(0.01, rate_per_s)
        self.capacity = burst if burst is not None else max(1.0, rate_per_s)
        self.tokens = self.capacity
        self.updated = time.monotonic()
        self.lock = threading.Lock()

    def acquire(self, max_wait_s: float = 5.0) -> bool:
        deadline = time.monotonic() + max_wait_s
        while True:
            with self.lock:
                now = time.monotonic()
                self.tokens = min(self.capacity,
                                  self.tokens + (now - self.updated) * self.rate)
                self.updated = now
                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    return True
                need = (1.0 - self.tokens) / self.rate
            if time.monotonic() + need > deadline:
                return False
            time.sleep(min(need, 0.25))


_buckets: dict[str, _Bucket] = {}
_buckets_lock = threading.Lock()


def bucket(provider: str) -> _Bucket:
    with _buckets_lock:
        b = _buckets.get(provider)
        if b is None:
            rate = settings().rate_limits.get(provider, 5.0)
            b = _Bucket(rate)
            _buckets[provider] = b
        return b


# ---------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------
@dataclass
class Entry:
    value: Any
    stored_at: float
    ttl_s: float
    provider: str = ""

    @property
    def age(self) -> float:
        return time.time() - self.stored_at

    @property
    def expired(self) -> bool:
        return self.age > self.ttl_s


_mem: dict[str, Entry] = {}
_mem_lock = threading.Lock()
#: Bounded so a long-running server cannot grow without limit. Eviction is
#: oldest-first, which is right for a cache whose entries are equally sized.
_MEM_MAX = 4000

#: One lock per in-flight key. This is the single-flight machinery.
_inflight: dict[str, threading.Event] = {}
_inflight_lock = threading.Lock()


def _evict_if_needed() -> None:
    if len(_mem) <= _MEM_MAX:
        return
    victims = sorted(_mem.items(), key=lambda kv: kv[1].stored_at)
    for k, _ in victims[: len(_mem) - int(_MEM_MAX * 0.8)]:
        _mem.pop(k, None)


def _db_get(key: str) -> Entry | None:
    """Read the durable tier. Never raises — the cache failing must degrade to
    'no cache', never to a broken request."""
    try:
        from ...core import db
        from ...core.schema import GeoCache
        with db.session() as s:
            row = s.get(GeoCache, key)
            if row is None:
                return None
            return Entry(value=row.value_json, stored_at=row.stored_at,
                         ttl_s=row.ttl_s, provider=row.provider or "")
    except Exception:
        return None


def _db_put(key: str, entry: Entry) -> None:
    try:
        from ...core import db
        from ...core.schema import GeoCache
        with db.session() as s:
            row = s.get(GeoCache, key)
            if row is None:
                row = GeoCache(key=key)
                s.add(row)
            row.kind = key.split(":", 1)[0]
            row.value_json = entry.value
            row.stored_at = entry.stored_at
            row.ttl_s = entry.ttl_s
            row.provider = entry.provider
    except Exception:
        pass


def get(key: str, *, allow_stale: bool = False) -> Entry | None:
    """Fetch an entry. Returns expired entries only when `allow_stale`."""
    if not settings().cache_enabled:
        return None
    with _mem_lock:
        entry = _mem.get(key)
    if entry is None:
        entry = _db_get(key)
        if entry is not None:
            with _mem_lock:
                _mem[key] = entry
                _evict_if_needed()
    if entry is None:
        return None
    if entry.expired and not allow_stale:
        return None
    return entry


def put(key: str, value: Any, ttl_s: float, provider: str = "") -> None:
    if not settings().cache_enabled:
        return
    entry = Entry(value=value, stored_at=time.time(), ttl_s=ttl_s,
                  provider=provider)
    with _mem_lock:
        _mem[key] = entry
        _evict_if_needed()
    _db_put(key, entry)


def invalidate(prefix: str = "") -> int:
    """Drop cached entries, optionally by kind prefix. Returns the count."""
    with _mem_lock:
        keys = [k for k in _mem if not prefix or k.startswith(prefix)]
        for k in keys:
            _mem.pop(k, None)
    try:
        from ...core import db
        from ...core.schema import GeoCache
        with db.session() as s:
            q = s.query(GeoCache)
            if prefix:
                q = q.filter(GeoCache.key.like(f"{prefix}%"))
            q.delete(synchronize_session=False)
    except Exception:
        pass
    return len(keys)


def prune() -> int:
    """Remove entries past twice their TTL.

    Twice, not once: an expired entry is still valuable as the offline
    fallback, and throwing it away the moment it goes stale would delete
    exactly the data degraded mode depends on.
    """
    now = time.time()
    removed = 0
    with _mem_lock:
        for k in [k for k, e in _mem.items() if now - e.stored_at > e.ttl_s * 2]:
            _mem.pop(k, None)
            removed += 1
    try:
        from ...core import db
        from ...core.schema import GeoCache
        from sqlalchemy import text
        with db.session() as s:
            s.execute(text("DELETE FROM geo_cache "
                           "WHERE :now - stored_at > ttl_s * 2"), {"now": now})
    except Exception:
        pass
    return removed


# ---------------------------------------------------------------------------
# The fetch wrapper everything actually uses
# ---------------------------------------------------------------------------
def fetch(key: str, kind: str, provider: str,
          producer: Callable[[], Any],
          *, ttl_s: float | None = None,
          serve_stale_on_error: bool = True) -> Result:
    """Cache-and-dedup around one provider call.

    The order of operations is the whole contract:

      1. Fresh cache hit -> return it, no network, no rate-limit token spent.
      2. Someone else is already fetching this key -> wait for them, then take
         their answer from the cache. This is what stops a render storm from
         becoming a request storm.
      3. Rate-limit token, then the call.
      4. On failure, fall back to a STALE entry if one exists, labelled as
         stale. Only if there is none does the caller get OFFLINE.

    `producer` returns the value to cache, or raises. It must not itself cache.
    """
    cfg = settings()
    ttl = ttl_s if ttl_s is not None else cfg.ttl_for(kind)

    entry = get(key)
    if entry is not None:
        record(provider, hit=True)
        return Result(data=entry.value, freshness=Freshness.CACHED,
                      provider=entry.provider or provider, age_s=entry.age)

    # -- single flight ------------------------------------------------------
    with _inflight_lock:
        event = _inflight.get(key)
        leader = event is None
        if leader:
            event = threading.Event()
            _inflight[key] = event

    if not leader:
        # Someone else is fetching. Wait, then read what they wrote. The
        # timeout is a safety net: if the leader dies without setting the
        # event we fetch ourselves rather than hanging.
        assert event is not None
        event.wait(timeout=cfg.timeout_s + 2.0)
        follower = get(key, allow_stale=True)
        if follower is not None:
            record(provider, hit=True)
            fresh = (Freshness.CACHED if not follower.expired
                     else Freshness.STALE)
            if fresh is Freshness.STALE:
                record(provider, stale=True)
            return Result(data=follower.value, freshness=fresh,
                          provider=follower.provider or provider,
                          age_s=follower.age)

    record(provider, miss=True)
    try:
        if cfg.offline:
            raise RuntimeError("TERRA is in offline mode")
        if not bucket(provider).acquire():
            record(provider, limited=True)
            raise RuntimeError(f"{provider} rate limit exceeded")

        started = time.monotonic()
        value = producer()
        elapsed = (time.monotonic() - started) * 1000.0
        record(provider, call=True, ms=elapsed)
        put(key, value, ttl, provider)
        return Result(data=value, freshness=Freshness.LIVE, provider=provider)

    except Exception as exc:  # noqa: BLE001 — never raise into a request
        record(provider, error=str(exc))
        if serve_stale_on_error:
            stale = get(key, allow_stale=True)
            if stale is not None:
                record(provider, stale=True)
                return Result(data=stale.value, freshness=Freshness.STALE,
                              provider=stale.provider or provider,
                              age_s=stale.age, error=str(exc))
        return Result.offline(str(exc), attempted=[provider])

    finally:
        if leader:
            with _inflight_lock:
                _inflight.pop(key, None)
            assert event is not None
            event.set()


# ---------------------------------------------------------------------------
# Provider health
# ---------------------------------------------------------------------------
@dataclass
class Health:
    """Per-provider circuit state.

    A provider that has failed three times running is not tried again for a
    minute. Without this, a dead provider costs every request its full timeout
    before falling back — with three providers in a chain that is thirty
    seconds of a user staring at a spinner to reach an answer that was
    available immediately.
    """

    failures: int = 0
    opened_at: float = 0.0
    threshold: int = 3
    cooldown_s: float = 60.0

    @property
    def open(self) -> bool:
        if self.failures < self.threshold:
            return False
        if time.time() - self.opened_at > self.cooldown_s:
            return False
        return True


_health: dict[str, Health] = defaultdict(Health)
_health_lock = threading.Lock()


def healthy(provider: str) -> bool:
    with _health_lock:
        return not _health[provider].open


def mark_ok(provider: str) -> None:
    with _health_lock:
        _health[provider] = Health()


def mark_failed(provider: str) -> None:
    with _health_lock:
        h = _health[provider]
        h.failures += 1
        if h.failures >= h.threshold:
            h.opened_at = time.time()


def health() -> dict[str, Any]:
    with _health_lock:
        return {name: {"failures": h.failures, "circuitOpen": h.open}
                for name, h in _health.items()}
