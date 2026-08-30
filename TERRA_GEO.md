# TERRA — Geospatial Intelligence

TERRA's spatial-awareness layer: where the user is, what is around them, what
the conditions are, how to get somewhere, and how any of that should change
what OMNIX does.

This document is the audit that preceded the build, the architecture that came
out of it, and the parts that are honestly still open.

---

## 1. What TERRA was

TERRA already existed and is good, but it answered a **different question**.
`omnix/terra/*.py` (~7k lines) is a geopolitical corpus: RSS ingest → TF-IDF →
entity extraction → knowledge graph → risk scoring → analyst agents. It is
world-scale and about *what is happening*.

The personal-geospatial surface was three endpoints and 200 lines:

| Endpoint | Backing | Notes |
|---|---|---|
| `/api/geo/search` | Open-Meteo geocoding | `omnix/tools/geo.py`, 68 lines |
| `/api/geo/reverse` | BigDataCloud | keyless |
| `/api/weather` | Open-Meteo forecast | current conditions only |

Both modules call `httpx` directly on every request. No cache, no rate limit,
no fallback, no key handling.

**Absent entirely:** routing, POIs, air quality, elevation, traffic,
geofencing, spatial memory, offline mode, tool-calling, usage accounting.

**Broken:** nothing, but `terra/__init__.py` still documented `build_terra.js`,
deleted in the 2026-08-04 bundle retirement.

**Orphaned:** `/api/weather` — no view had called it since the retirement.

---

## 2. What was preserved

Everything. This is additive.

- `omnix/terra/*.py` — untouched.
- `omnix/tools/geo.py`, `weather.py` — untouched, still serving `/api/geo/*`
  and `/api/weather`.
- `views/MapView.tsx` — the canvas world map, untouched. It selects **country
  objects** from the workspace graph; the new view selects nothing. Two
  renderers, two questions, and merging them would compromise both.

Nothing was removed. The new surface is namespaced `/api/terra/geo/*`
specifically so the existing `/api/geo/*` keeps working.

---

## 3. Architecture

```
omnix/terra/geo/
├── types.py          Coord, Place, Route, Weather, AirQuality, Result
│                     …and Freshness, which every payload carries
├── config.py         env-driven settings; keys never leave this module
├── cache.py          TTL cache · single-flight dedup · rate limits · usage
├── spatial.py        haversine, bbox, point-in-polygon, polyline, RDP
├── providers/        one vendor each — they know how to ask, nothing else
│   ├── base.py         protocols + the shared HTTP client + category map
│   ├── registry.py     WHICH provider answers WHAT, and in what order
│   ├── openmeteo.py    geocode · weather · air quality · elevation  (no key)
│   ├── nominatim.py    geocode · reverse, street-level                (no key)
│   ├── bigdatacloud.py reverse                                        (no key)
│   ├── overpass.py     POI search over raw OSM                        (no key)
│   ├── osrm.py         routing over the OSM road network              (no key)
│   ├── graphhopper.py  routing incl. real foot/bike profiles          (key)
│   └── google.py       Places · Routes · Geocoding · Elevation · AQ   (key)
├── core/             one capability each — they know what a GOOD answer is
│   ├── geocoding.py    memory → cache → providers
│   ├── places.py       search, nearest, quiet-workspace, ranking
│   ├── routing.py      chain, alternatives, degraded estimates
│   ├── scoring.py      configurable, explainable, learnable route scoring
│   ├── environment.py  weather, AQI, elevation, local sun times
│   ├── memory.py       saved places, visit history, retention, privacy
│   └── geofencing.py   fences, transition detection, events
├── intelligence/
│   └── context.py    the structured spatial context + its prose rendering
├── tools.py          the validated surface a model is allowed to reach
├── api.py            the `terra.*` facade everything outside this calls
└── routes.py         /api/terra/geo/*
```

**Three invariants hold throughout.** Breaking any is a bug:

1. **Nothing raises into a request.** Callers get a `Result` carrying an error.
2. **Nothing requires a key.** Every capability has a keyless provider. A fresh
   clone with no `.env` gives a working map, search, routes, weather and air
   quality.
3. **Nothing lies about freshness.** Cached is labelled cached, stale is
   labelled stale, and a locally computed estimate is never called live.

---

## 4. Providers, and why the order is what it is

Ordered by **cost per answer at equal usefulness** — not by quality in the
abstract.

| Capability | Chain |
|---|---|
| geocode | nominatim → openmeteo → google |
| reverse | bigdatacloud → nominatim → google |
| places | overpass → google *(inverted when ratings are required)* |
| routing | osrm → graphhopper → google *(filtered by mode support)* |
| weather | openmeteo |
| air quality | openmeteo → google |
| elevation | openmeteo → google |

Google is promoted to first in exactly one case: a request that genuinely needs
**ratings**, which do not exist in OSM at any ranking effort. Everything else
answers free-first.

### Recommended free / open

- **Open-Meteo** — four capabilities from one keyless vendor. No signup.
- **Nominatim (OSM)** — the only free street-level geocoder. Hard 1 req/s.
- **Overpass (OSM)** — excellent urban amenity coverage, plus tags Google will
  not sell you: wheelchair access, exact opening-hours expressions, wifi.
- **OSRM** — real road routing with turn-by-turn, free.
- **CARTO basemaps** — raster tiles built for public use, with matched dark and
  light styles so the map obeys the OMNIX theme.

**Deliberately not used: the openstreetmap.org tile servers.** Those are
volunteer-funded infrastructure with a usage policy this application does not
qualify under.

### Recommended Google (all optional)

Genuinely better at three things: **ratings/popularity**, **traffic-aware
ETAs**, and **address coverage** outside well-mapped areas. Add
`GOOGLE_MAPS_API_KEY` and the chains extend themselves.

---

## 5. Cost

Every Places and Routes call carries a **minimal field mask** — Google bills
Places by which fields you request, and the default set costs several times the
needed set. Every field in the masks is one the UI renders.

The controls, in descending order of what they actually save:

1. **Spatial key snapping** (`cache.spatial_key`) — weather and AQI are keyed
   on a ~1km grid. GPS jitters tens of metres per second; without snapping a
   stationary user generates a paid lookup per position update forever. A ±60m
   spread of 49 distinct fixes collapses to ≤4 cache keys.
2. **Saved places** — once "college" is in `geo_place` it never geocodes again,
   and it resolves offline.
3. **Per-kind TTLs** — a geocode is stable for 30 days; a traffic-aware ETA for
   180 seconds. One blanket TTL either burns money or lies.
4. **Single-flight dedup** — ten panels asking for the same weather during one
   render produce one network call, not ten.
5. **Batching** — a 300-point elevation profile is 3 requests, not 300.
6. **Local computation** — distance, bearing, containment, route/geofence
   intersection and sunrise/sunset never touch a provider.

`/api/terra/geo/usage` reports real numbers, including `callsAvoided`. The Data
panel in the UI renders it.

---

## 6. Storage

**Decision: the existing SQLite platform DB. No PostGIS, no SpatiaLite.**

The workload is a few thousand user-owned points and the query is "what is
within R metres of here". An indexed lat/lon bbox prefilter plus a haversine
refine in Python answers that in well under a millisecond at this scale, with
no extension to install.

Columns are plain floats precisely so migrating to PostGIS later is an `ALTER`
and a backfill, not a redesign — every spatial predicate lives behind a
function in `spatial.py`, so there is exactly one file to change.

Tables (migration `54e9d58d762a`): `geo_cache`, `geo_place`, `geo_visit`,
`geo_route`, `geofence`, `geofence_event`, `geo_preference`.

---

## 7. Security and privacy

- Keys are read from the environment only. `config.describe()` and
  `api.status()` report **presence booleans**, never values — there is a test
  asserting a planted secret cannot appear in either.
- `.env` is read without adding a dependency; existing environment wins.
- Rate limits are enforced per provider by a blocking token bucket, so
  exceeding Nominatim's 1 req/s is impossible rather than unlikely.
- A provider that fails three times is circuit-broken for 60s, so a dead
  provider costs one timeout, not one per request.
- **Saved places and visit history are separate tables with separate
  switches.** Privacy mode disables history while leaving saved places
  working, so "take me to college" still functions with tracking fully off.
- Retention is enforced **on write**, not by a sweeper — a policy that only
  holds while a scheduler is alive is not a policy.
- `DELETE /history` deletes rows, not flags. `GET /export` returns everything.
- The LLM never sees a URL, a provider name or a key. It selects a tool name
  from a fixed catalogue; unknown arguments are **dropped**, not forwarded.

---

## 8. Agent integration

Two dispatch paths, cheap one first:

1. **`tools.parse`** — deterministic patterns. "where am i", "find coffee near
   me", "nearest hospital", "take me to college", "somewhere quiet to work" all
   resolve with **no model call at all**, and work with every model offline. It
   returns `None` rather than guessing — a wrong tool produces a confident
   answer to a question nobody asked.
2. **`tools.select`** — a model picks one tool from the catalogue; its choice
   goes through the same validator. Any lat/lon it supplies is replaced with
   the real position.

Both converge on `tools.invoke`, the single choke point where validation
happens. NOVA routes to this via a `spatial` intent in `api/nova.py`, checked
*before* the research and query hints because "find the nearest hospital" would
otherwise search the object graph.

The model is handed **rendered context**, never raw provider JSON, and is
instructed to state staleness in the answer.

---

## 9. Degraded mode

```
provider live      → LIVE
provider cached    → CACHED   (inside TTL)
provider failed    → STALE    (past TTL, served deliberately, labelled)
computed locally   → ESTIMATED
nothing available  → OFFLINE  (empty payload + reason)
```

The cache's durable tier is what makes this work — an in-memory-only cache
empties on restart and can answer nothing.

`TERRA_OFFLINE=1` forces the whole degraded path so it can be exercised
deliberately. In that state: weather returns OFFLINE with a reason, routing
returns a labelled straight-line estimate, and distance/bearing/containment
still answer normally because they never needed the network.

`Freshness` is rendered on every panel that shows provider data.

---

## 10. What is honestly still open

- **Traffic requires Google.** OSRM routes over a static graph, so
  `durationTrafficS` is null on the free path. That is reported as "not
  modelled", never as "no traffic".
- **Walking and cycling on a default install are estimates.** The public OSRM
  demo ignores the profile in its URL — verified: driving, walking and cycling
  return byte-identical results. TERRA refuses to present car timings as a
  walk; it reuses the road *geometry* at walking speed and labels it ESTIMATED.
  Configure GraphHopper or a self-hosted OSRM with the foot profile and this
  disappears.
- **Ratings require Google.** OSM has none, and the ranker skips the rating
  term entirely rather than pretending the order means something.
- **"Quiet" is inferred, not measured.** OSM records no noise data. The signal
  comes from category and tags, and the API says so in `criteria.note`.
- **Overpass truncates spatially at very large radii.** `nearest` is immune —
  it searches outward in rings — but a >10km browse can return a partial set,
  and says so via `attempted`.
- **Transit routing is unimplemented.** No free provider covers it usefully;
  `route_chain` correctly returns nothing rather than substituting driving.
- **Preference learning is deliberately timid.** It takes many consistent
  choices to move a weight, and `/preferences` shows and resets the state.
- **Geofences evaluate on position updates, not continuously.** There is no
  background location service — the browser pushes fixes when the view is open.

---

## 11. Configuration

Everything optional. TERRA runs with no `.env` at all.

```bash
# Providers — all optional; each extends a chain rather than enabling it
GOOGLE_MAPS_API_KEY=          # ratings, traffic ETAs, wider address coverage
GRAPHHOPPER_API_KEY=          # real foot/bike routing

# Self-hosted or commercial endpoints
TERRA_OSRM_URL=https://router.project-osrm.org
TERRA_NOMINATIM_URL=https://nominatim.openstreetmap.org
TERRA_OVERPASS_URL=https://overpass-api.de/api/interpreter

# Basemap
TERRA_TILES_DARK= / TERRA_TILES_LIGHT= / TERRA_TILE_ATTRIBUTION=

# Behaviour
TERRA_OFFLINE=0               # force degraded mode
TERRA_CACHE=1
TERRA_GOOGLE=1                # disable Google without removing the key
TERRA_TIMEOUT=10
TERRA_TTL_<KIND>=             # geocode|places|route|weather|air_quality|…

# Privacy
TERRA_PRIVACY_MODE=0          # stops location history being written at all
TERRA_HISTORY=1
TERRA_HISTORY_DAYS=90
```

---

## 12. UI

Rail item **TERRA Live**, beside (not replacing) World Map. MapLibre GL with
raster tiles, seven panels: Search, Places, Route, Conditions, Memory,
Geofences, Data.

- Tile URL comes from the **server**, so changing basemap provider is an
  environment variable and the browser never holds a key.
- **Location is pull, never push** — the geolocation permission is requested on
  an explicit click, never on mount. Every panel accepts a map-clicked point
  instead, so the whole view works with permission denied.
- The **Data** panel shows provider health, capability chains and
  `callsAvoided`, which makes the cost discipline auditable rather than
  asserted.
- MapLibre is ~1MB, so this is the only lazily-loaded view; the main bundle is
  unaffected for users who never open it.

---

## 13. Tests

`tests/test_terra_geo.py` — 89 tests, **no network**. Provider chains are
driven with fakes, which is the practical argument for having a provider
abstraction: fallback, caching, stale-serving, circuit-breaking and offline
degradation are all testable without an HTTP request.
