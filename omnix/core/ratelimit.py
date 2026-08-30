"""Request throttling for the routes that cost money.

WHY LOGIN THROTTLING WAS NOT ENOUGH
-----------------------------------
`auth.py` already throttles failed sign-ins, which protects the password. It
does nothing for the routes that matter financially: a signed-in account can
call the research endpoints as fast as the network allows, and every one of
those spends provider tokens. On a variable-cost provider that lands directly
on the operator's card, and the plan this was built for has a $43/month
ceiling — roughly two thousand open-weight runs. A loop could exhaust a month
in minutes.

Entitlements (core/entitlements.py) cap what an account may spend over a
period. This caps the *rate*, which is a different failure: a runaway client, a
retry storm, or a page that mounts the same request three times. Both are
needed — a monthly ceiling does not stop a bad afternoon, and a per-minute
limit does not stop steady overuse.

TOKEN BUCKET, NOT A FIXED WINDOW
--------------------------------
A fixed window lets a caller spend the whole allowance in the last second of
one window and again in the first second of the next — twice the intended rate
at the boundary. A bucket refills continuously, so the limit means what it
says. It also allows a genuine short burst, which is what a dashboard mounting
four panels actually looks like.

IN-MEMORY, AND HONEST ABOUT IT
------------------------------
State lives in this process. That is correct for the single-instance
deployment this ships as, and it is wrong the moment there are two instances
behind a load balancer — each would enforce the full limit independently. When
that day comes the bucket store moves to Redis; the call sites do not change.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field


@dataclass
class Bucket:
    capacity: float
    refill_per_s: float
    tokens: float = field(default=0.0)
    updated: float = field(default_factory=time.monotonic)

    def level(self, now: float | None = None) -> float:
        """Tokens held *right now*, including refill since the last `take`.

        `self.tokens` is only recomputed when a request arrives, so on an idle
        bucket the stored figure is stale and always below capacity. Anything
        asking "is this bucket full?" has to ask here — reading the field gave
        the sweep below a permanently false answer.
        """
        now = time.monotonic() if now is None else now
        return min(self.capacity,
                   self.tokens + (now - self.updated) * self.refill_per_s)

    def take(self, n: float = 1.0) -> tuple[bool, float]:
        """Spend `n` tokens. Returns (allowed, seconds_until_next_allowed)."""
        now = time.monotonic()
        self.tokens = self.level(now)
        self.updated = now
        if self.tokens >= n:
            self.tokens -= n
            return True, 0.0
        deficit = n - self.tokens
        return False, deficit / self.refill_per_s if self.refill_per_s else 3600.0


@dataclass(frozen=True)
class Limit:
    """`count` requests per `per_s`, allowing a burst up to `count`."""
    count: int
    per_s: float

    @property
    def refill_per_s(self) -> float:
        return self.count / self.per_s if self.per_s else 0.0


# Tuned to what a human plus a normal UI actually does, not to what feels safe.
#
#   research  A run takes ~100s. Six a minute is already far beyond hand use
#             and still lets someone queue a handful deliberately.
#   agents    Cheaper and more interactive; CHALLENGE fans out to four models.
#   write     Creating Spaces, objects, intents. Cheap, but unbounded creation
#             is a disk-fill vector.
#   read      Generous. The workspace mounts many panels at once and a limit
#             that fires on normal navigation trains people to distrust it.
#   auth      Sign-in and reset are public, so this is the only per-address
#             brake on credential stuffing — `auth.py` throttles per *email*,
#             which an attacker walking a list of addresses never trips.
LIMITS: dict[str, Limit] = {
    "research": Limit(6, 60),
    "agents": Limit(20, 60),
    "write": Limit(60, 60),
    "read": Limit(600, 60),
    "auth": Limit(20, 60),
}

# Which bucket a path belongs to. Longest prefix wins, so a more specific rule
# can sit inside a broader one.
_ROUTES: tuple[tuple[str, str], ...] = (
    ("/api/research", "research"),
    ("/api/nova/research", "research"),
    ("/api/agents", "agents"),
    ("/api/plugins", "agents"),
    ("/api/nova", "agents"),
    ("/api/auth", "auth"),
    # Deliberately generous, and deliberately not exempt: a liveness probe
    # should never be refused in normal operation, but an unmetered public
    # endpoint is a free amplifier.
    ("/api/health", "read"),
)


def bucket_for(path: str, method: str) -> str:
    for prefix, name in sorted(_ROUTES, key=lambda r: -len(r[0])):
        if path.startswith(prefix):
            return name
    return "read" if method.upper() in ("GET", "HEAD", "OPTIONS") else "write"


class Limiter:
    def __init__(self, limits: dict[str, Limit] | None = None):
        self._limits = limits or LIMITS
        self._buckets: dict[tuple[str, str], Bucket] = {}
        self._lock = threading.Lock()
        self._last_sweep = time.monotonic()

    def check(self, identity: str, kind: str) -> tuple[bool, float]:
        limit = self._limits.get(kind)
        if limit is None:
            return True, 0.0
        key = (identity, kind)
        with self._lock:
            b = self._buckets.get(key)
            if b is None:
                # Start full: a first request must never be refused, or a cold
                # start looks like an outage.
                b = Bucket(capacity=float(limit.count),
                           refill_per_s=limit.refill_per_s,
                           tokens=float(limit.count))
                self._buckets[key] = b
            allowed, retry_after = b.take()
            self._maybe_sweep()
        return allowed, retry_after

    def _maybe_sweep(self) -> None:
        """Drop buckets that have been full and idle. Called under the lock.

        Without this the dict grows one entry per (identity, kind) forever,
        which on a public deployment is an unbounded allocation driven by
        whoever wants to send requests.
        """
        now = time.monotonic()
        if now - self._last_sweep < 300:
            return
        self._last_sweep = now
        # `b.level(now)`, not `b.tokens`: the stored count is whatever was left
        # after the caller's last request and is never recomputed while they are
        # away. Comparing it to capacity meant a bucket that had been idle for a
        # day still looked drained, nothing was ever collected, and the dict grew
        # one permanent entry per caller — the exact leak this sweep is for.
        stale = [k for k, b in self._buckets.items()
                 if now - b.updated > 600 and b.level(now) >= b.capacity]
        for k in stale:
            del self._buckets[k]

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()


_shared = Limiter()


def shared() -> Limiter:
    return _shared
