"""USGS earthquakes — the reference plugin.

This is the plugin the plugin system was tested against, and it was chosen for
that job because it needs no credentials: everything it exercises — manifest,
discovery, permission check, adapter, cache, health, degraded state, tool bus —
can be verified end to end without an account anywhere. A core proven only
against plugins that cannot run is a core that has not been proven.

WHAT IT DELIBERATELY DOES NOT DO
--------------------------------
It does not interpret. An earthquake near a border is an earthquake, not an
explosion; a swarm of small events is a swarm, not an imminent eruption. §15
draws this line for satellite data and it applies just as hard here: the plugin
reports observations, and anything downstream that infers from them has to say
that it is inferring.
"""

from __future__ import annotations

import threading
import time

from ...core.plugin_system import Plugin, Result, Status
from ...core.plugin_system.health import Freshness
from .adapters.usgs import UsgsAdapter, UsgsError

ATTRIBUTION = "Data from the U.S. Geological Survey Earthquake Hazards Program"
DOCS = "https://earthquake.usgs.gov/fdsnws/event/1/"


class _Cache:
    """Tiny TTL cache with request de-duplication (§42).

    De-duplication matters more than the hit rate here: a dashboard that
    renders three earthquake tiles will otherwise make three identical
    requests on mount, which is the behaviour that gets a free public service
    to rate-limit an application.
    """

    def __init__(self, ttl_s: float):
        self.ttl_s = ttl_s
        self._entries: dict[str, tuple[float, object]] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    def _lock_for(self, key: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(key, threading.Lock())

    def get_or_call(self, key: str, fn):
        now = time.time()
        hit = self._entries.get(key)
        if hit and now - hit[0] < self.ttl_s:
            return hit[1], True, now - hit[0]

        with self._lock_for(key):
            # Re-check: another thread may have filled it while we waited.
            hit = self._entries.get(key)
            now = time.time()
            if hit and now - hit[0] < self.ttl_s:
                return hit[1], True, now - hit[0]
            value = fn()
            self._entries[key] = (time.time(), value)
            return value, False, 0.0

    def clear(self) -> None:
        self._entries.clear()


class UsgsEarthquakePlugin(Plugin):

    def setup(self) -> None:
        self.adapter = UsgsAdapter(
            timeout_s=float(self.config("timeout_s", 20)))
        self._cache = _Cache(float(self.config("cache_ttl_s", 300)))
        self.register("recent_earthquakes", self.recent_earthquakes)
        self.register("earthquakes_near", self.earthquakes_near)
        self.register("earthquake_summary", self.earthquake_summary)

    def teardown(self) -> None:
        cache = getattr(self, "_cache", None)
        if cache is not None:
            cache.clear()

    def probe(self):
        ok, detail = self.adapter.health_check()
        if ok:
            self.health.status = Status.OK
            self.health.detail = ""
            self.health.active_source = "usgs_fdsn"
            self.health.last_success = time.time()
        else:
            # DEGRADED, not FAILED: the plugin is fine, the network or the
            # provider is not, and that distinction is what tells the user
            # whether to check their wifi or wait.
            self.health.status = Status.DEGRADED
            self.health.detail = detail
            self.health.docs_url = DOCS
            self.health.last_failure = time.time()
            self.health.last_error = detail
        return self.health

    # -- tools ------------------------------------------------------------
    def _result(self, quakes, *, cached: bool, age_s: float) -> Result:
        return Result.ok(
            [q.to_dict() for q in quakes],
            source="usgs_fdsn",
            cached=cached,
            freshness=Freshness.of(age_s, self.manifest.poll_seconds or 600),
            attribution=ATTRIBUTION,
        )

    def _fetch(self, key: str, call) -> Result:
        try:
            value, cached, age = self._cache.get_or_call(key, call)
        except UsgsError as e:
            # The typed absence. Never an empty list — a dashboard that renders
            # "no earthquakes" during an outage has stated a fact nobody checked.
            return Result.unavailable(
                f"USGS earthquake data is unavailable: {e}",
                status=Status.DEGRADED, docs_url=DOCS)
        return self._result(value, cached=cached, age_s=age)

    def recent_earthquakes(self, *, min_magnitude: float | None = None,
                           hours: float = 24.0, limit: int = 0) -> Result:
        mag = (min_magnitude if min_magnitude is not None
               else float(self.config("default_min_magnitude", 4.0)))
        lim = int(limit or self.config("default_limit", 50))
        hours = max(0.1, float(hours))
        # Bucketed to the minute so that three tiles rendering within the same
        # second share one cache key rather than missing by microseconds.
        start = time.time() - hours * 3600
        key = f"recent:{mag}:{int(start // 60)}:{lim}"
        return self._fetch(key, lambda: self.adapter.search(
            min_magnitude=mag, starttime=start, limit=lim))

    def earthquakes_near(self, *, latitude: float, longitude: float,
                         radius_km: float = 500.0,
                         min_magnitude: float | None = None,
                         days: float = 30.0, limit: int = 0) -> Result:
        lim = int(limit or self.config("default_limit", 50))
        start = time.time() - max(0.1, float(days)) * 86400
        key = (f"near:{round(float(latitude), 3)}:{round(float(longitude), 3)}"
               f":{radius_km}:{min_magnitude}:{int(start // 60)}:{lim}")
        return self._fetch(key, lambda: self.adapter.search(
            latitude=latitude, longitude=longitude, radius_km=radius_km,
            min_magnitude=min_magnitude, starttime=start, limit=lim))

    def earthquake_summary(self, *, hours: float = 24.0,
                           min_magnitude: float | None = None) -> Result:
        inner = self.recent_earthquakes(
            min_magnitude=min_magnitude, hours=hours, limit=500)
        if not inner.available:
            return inner

        quakes = inner.data
        rated = [q for q in quakes if q.get("magnitude") is not None]
        largest = max(rated, key=lambda q: q["magnitude"], default=None)

        # Buckets are reported as counts of events whose magnitude is KNOWN.
        # `unrated` is surfaced rather than folded into the total, because a
        # summary that quietly counts unmeasured events alongside measured ones
        # is a number nobody can check.
        buckets = {"m3_4": 0, "m4_5": 0, "m5_6": 0, "m6_plus": 0}
        for q in rated:
            m = q["magnitude"]
            if m < 4:
                buckets["m3_4"] += 1
            elif m < 5:
                buckets["m4_5"] += 1
            elif m < 6:
                buckets["m5_6"] += 1
            else:
                buckets["m6_plus"] += 1

        return Result.ok(
            {
                "windowHours": hours,
                "total": len(quakes),
                "rated": len(rated),
                "unrated": len(quakes) - len(rated),
                "buckets": buckets,
                "tsunamiFlagged": sum(1 for q in quakes if q.get("tsunami")),
                "largest": largest,
            },
            source="usgs_fdsn", cached=inner.cached,
            freshness=inner.freshness, attribution=ATTRIBUTION)
