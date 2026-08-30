"""TERRA Intelligence — the ontology-driven analysis layer under the world map.

The existing TERRA map (omnix/tools/geo.py, weather.py, news.py + build_terra.js)
stays exactly as it is: a fast 2D world you can pan, click and read headlines on.
This package is the layer UNDERNEATH it — the part that turns those headlines
into connected objects and reasons over them.

The shape of it, in one pass:

    ingest   multi-source RSS crawl -> deduplicated article corpus
    nlp      tokenizing, TF-IDF vectors, sentiment, source confidence
    extract  articles -> typed entities (gazetteer first, LLM to deepen)
    graph    entities + relationships, with provenance on every edge
    cluster  near-duplicate articles -> events -> evolving timelines
    risk     per-country risk across five dimensions -> the heatmap
    agents   six domain analysts + a master synthesizer
    predict  second-order effects, economic impact, what-if simulation
    country  live country intelligence cards (World Bank / REST Countries)
    verify   the same story across outlets: agreement, conflict, omission
    detect   article-velocity bursts -> "something is happening in X"
    search   semantic retrieval over the corpus, synthesized by an LLM
    brief    30s / 2min / executive / research briefings
    situation named theatre reports (Middle East, Taiwan, ...)
    history  historical analogues for a live event
    layers   earthquakes, disasters, and news-derived map overlays
    service  in-memory state, background refresh, and the job runner

Design rules this package sticks to, because they are what make it an addon
rather than a rewrite:

  * No new infrastructure. Palantir-style architectures reach for Neo4j, Kafka
    and a vector DB; none of those can be assumed on the laptop this ships to.
    The graph is an in-process adjacency structure with JSON persistence and the
    "vector DB" is a TF-IDF index over a few hundred articles. Same operations,
    no daemons to install. Every module is written so the storage layer could be
    swapped for the real thing later without touching its callers.
  * Deterministic backbone, LLM on top. Extraction, scoring, clustering and
    verification all produce useful output with the network down and every model
    unavailable — the LLM deepens the analysis, it is never load-bearing.
  * Never raise into a request. Callers get empty/partial structures instead.
"""

from __future__ import annotations

__all__ = ["service"]
