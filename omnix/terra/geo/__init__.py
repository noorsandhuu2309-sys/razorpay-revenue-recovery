"""TERRA Geospatial Intelligence — OMNIX's spatial awareness layer.

The rest of `omnix/terra/` answers "what is happening in the world": a
geopolitical corpus, entity extraction, risk scoring, a knowledge graph. This
package answers a different question — "where is the user, what is around them,
what are the conditions, and how should that change what OMNIX does" — and the
two share a name because they share a purpose: geography as something to reason
over rather than something to display.

    from omnix.terra.geo import terra

    terra.get_spatial_context(lat, lon, workspace_id=ws)
    terra.get_route("home", "college", mode="cycling")
    terra.nearest_poi(lat, lon, "pharmacy")

The layout, and the rule each layer obeys:

    types.py            the vocabulary — and Freshness, which every payload
                        carries so nothing stale is ever shown as current
    config.py           environment-driven settings; keys never leave it
    cache.py            TTL cache, single-flight dedup, rate limits, usage
    spatial.py          local geometry — free, exact, works offline
    providers/          one vendor each; they know how to ask, nothing else
    core/               one capability each; they know what a good answer is
    intelligence/       context assembly and prose rendering for a model
    tools.py            the validated surface a model is allowed to reach
    api.py              the facade everything outside this package calls
    routes.py           /api/terra/geo/*

Three invariants hold throughout, and breaking any of them is a bug:

  * **Nothing raises into a request.** Callers get a `Result` carrying an
    error, never an exception.
  * **Nothing requires a key.** Every capability has a keyless provider.
    Google is an upgrade; a fresh clone must work.
  * **Nothing lies about freshness.** Cached is labelled cached, stale is
    labelled stale, and a locally computed estimate is never called live.
"""

from __future__ import annotations

from . import api as terra

__all__ = ["terra", "api", "tools", "routes"]
