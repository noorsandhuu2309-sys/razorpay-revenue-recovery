"""Plugin status, and the typed absence of data.

THE MOST IMPORTANT TYPE IN THE PLUGIN SYSTEM IS `Unavailable`
------------------------------------------------------------
§90 forbids fabrication, and the usual way a system violates that rule is not
by inventing a statistic — it is by returning an empty list. A disaster plugin
whose provider is unreachable returns `[]`, the dashboard renders "no active
disasters", and the user reads an outage as a quiet day. That is a fabricated
fact produced without a single invented word, and it is worse than an error
because it is plausible.

So a plugin call returns `Result`, which is either data *or* a reason there is
none, and the two are different types. Rendering code cannot accidentally treat
the second as the first.

STATUS IS ACTIONABLE OR IT IS NOISE (§45)
-----------------------------------------
"Something went wrong" is banned. Every non-OK status carries what is wrong,
the configuration key that fixes it where one exists, and the provider's own
documentation URL. A user who sees a red dot must be able to act on it without
reading the source.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Status(str, Enum):
    OK = "ok"
    # A required secret is missing. Actionable: names the env var.
    UNCONFIGURED = "unconfigured"
    # Reachable but impaired — a provider is erroring, or a fallback is serving.
    DEGRADED = "degraded"
    # Switched off by the user, by tier, or by resource governance.
    DISABLED = "disabled"
    # Import or construction failed. The plugin is quarantined, not fatal.
    FAILED = "failed"


@dataclass
class Health:
    status: Status = Status.DISABLED
    detail: str = ""
    # The configuration key that would fix an UNCONFIGURED/DEGRADED state.
    fix_key: str = ""
    docs_url: str = ""
    last_success: float | None = None
    last_failure: float | None = None
    last_error: str = ""
    calls: int = 0
    errors: int = 0
    latency_ms_last: int = 0
    # Which provider actually answered, when a fallback chain is in play (§83).
    active_source: str = ""

    @property
    def error_rate(self) -> float:
        return round(self.errors / self.calls, 4) if self.calls else 0.0

    def record_success(self, *, latency_ms: int = 0, source: str = "") -> None:
        self.calls += 1
        self.last_success = time.time()
        self.latency_ms_last = latency_ms
        if source:
            self.active_source = source
        # A success clears a DEGRADED state but must not override a deliberate
        # DISABLED one — a working provider is not a reason to switch a plugin
        # the user turned off back on.
        if self.status is Status.DEGRADED:
            self.status = Status.OK

    def record_failure(self, error: str, *, degrade: bool = True) -> None:
        self.calls += 1
        self.errors += 1
        self.last_failure = time.time()
        self.last_error = error
        if degrade and self.status is Status.OK:
            self.status = Status.DEGRADED

    def to_dict(self) -> dict:
        return {
            "status": self.status.value,
            "detail": self.detail,
            "fixKey": self.fix_key,
            "docsUrl": self.docs_url,
            "lastSuccess": self.last_success,
            "lastFailure": self.last_failure,
            "lastError": self.last_error,
            "calls": self.calls,
            "errors": self.errors,
            "errorRate": self.error_rate,
            "latencyMs": self.latency_ms_last,
            "activeSource": self.active_source,
        }


class Freshness(str, Enum):
    """§41. Never show stale data as live."""
    LIVE = "live"          # under one refresh interval
    FRESH = "fresh"        # under 3x
    AGING = "aging"        # under 12x
    STALE = "stale"        # older than that
    UNKNOWN = "unknown"

    @staticmethod
    def of(age_s: float | None, interval_s: float) -> "Freshness":
        if age_s is None or interval_s <= 0:
            return Freshness.UNKNOWN
        if age_s <= interval_s:
            return Freshness.LIVE
        if age_s <= interval_s * 3:
            return Freshness.FRESH
        if age_s <= interval_s * 12:
            return Freshness.AGING
        return Freshness.STALE


@dataclass
class Unavailable:
    """Why there is no data. Never rendered as an empty result."""
    reason: str
    status: Status = Status.DEGRADED
    fix_key: str = ""
    docs_url: str = ""

    def to_dict(self) -> dict:
        return {"available": False, "reason": self.reason,
                "status": self.status.value, "fixKey": self.fix_key,
                "docsUrl": self.docs_url}


@dataclass
class Result:
    """Data, or a typed reason there is none.

    Construct through :meth:`ok` or :meth:`unavailable` rather than directly, so
    that a result can never carry both and never carry neither.
    """
    data: Any = None
    error: Unavailable | None = None
    source: str = ""
    retrieved_at: float = field(default_factory=time.time)
    published_at: float | None = None
    cached: bool = False
    # How old the underlying observation is, against the plugin's own interval.
    freshness: Freshness = Freshness.UNKNOWN
    attribution: str = ""

    @property
    def available(self) -> bool:
        return self.error is None

    @classmethod
    def ok(cls, data: Any, **kw) -> "Result":
        return cls(data=data, **kw)

    @classmethod
    def unavailable(cls, reason: str, *, status: Status = Status.DEGRADED,
                    fix_key: str = "", docs_url: str = "", **kw) -> "Result":
        return cls(error=Unavailable(reason, status, fix_key, docs_url), **kw)

    def to_dict(self) -> dict:
        if self.error is not None:
            return self.error.to_dict()
        return {
            "available": True,
            "data": self.data,
            "source": self.source,
            "retrievedAt": self.retrieved_at,
            "publishedAt": self.published_at,
            "cached": self.cached,
            "freshness": self.freshness.value,
            "attribution": self.attribution,
        }
