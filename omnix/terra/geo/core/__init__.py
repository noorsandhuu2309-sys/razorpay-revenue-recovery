"""TERRA's capability services.

One module per question TERRA can answer. Each owns its provider chain, its
cache policy and its fallback behaviour, and each returns a `Result` — never a
bare value and never an exception.

The division from `providers/` is strict and worth keeping strict: a provider
knows how to talk to one vendor and nothing else; a service knows what a good
answer looks like and which vendors to ask. Ranking, filtering, scoring and
memory all live here, because all four must survive a change of vendor.
"""

from __future__ import annotations

__all__ = ["geocoding", "places", "routing", "environment", "geofencing",
           "memory", "scoring"]
