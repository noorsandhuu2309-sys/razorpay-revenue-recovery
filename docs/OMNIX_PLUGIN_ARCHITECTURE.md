# OMNIX plugin architecture

Written 2026-08-19, after inspecting the repository as §0 and §106 require.
This is the report that precedes implementation.

## The three findings that shape this plan

### 1. Roughly 60% of the specification already exists — under different names

OMNIX is not a blank slate with a chat box. Most of what §3–§92 describe is
built, tested and running; it is simply not organised as "plugins". Building it
again as plugins would be the single most expensive mistake available here, so
the plan is **migrate onto a contract, not rewrite**.

| Spec section | Already in the repo | Where |
|---|---|---|
| §9 Evidence object | Source with credibility, tier, retrieved_at, content hash | `squad/oracle_evidence.py`, `core/schema.py` |
| §29 Source verification | Independent corroboration with duplicate collapsing, verdicts | `squad/oracle_evidence.py` |
| §30 Claim database | `Claim` table with verdict and supporting evidence | `core/schema.py` |
| §91 Source quality tiers | Deterministic domain/TLD/recency classing | `squad/oracle_evidence.py` |
| §27 Knowledge graph | Objects, relationships, ontology, traversal | `core/objects.py`, `graph/` |
| §26 Entity intelligence | Upsert with alias handling, canonical ids | `core/objects.py` |
| §83 Provider fallback | Ordered chains with per-capability ordering | `terra/geo/providers/registry.py` |
| §42 Caching | TTL cache with request dedup | `terra/geo/cache.py`, `knowledge_cache.py` |
| §43 Scheduler | Daemon-thread sweep with configurable interval | `core/intents.py`, `terra/service.py` |
| §46 Tool bus | Typed units, one execution per call, metered | `agents_v2/adapter.py`, `squad/base.py` |
| §54 Database | SQLAlchemy + Alembic, ~25 tables | `core/schema.py`, `migrations/` |
| §66 Audit log | Typed event stream per execution | `core/events.py` |
| §69/70 Model abstraction and routing | Capability router + named roster + hedged ladders | `models/`, `model_catalog.py`, `cloud.py` |
| §39 Memory | Persistent + per-workspace memory | `core/memory.py` |
| §44 Health | Live probes, latency, error rate | `squad/pulse.py` |

**Genuinely missing, and therefore what Phase 1 builds:** a formal plugin
manifest, automatic discovery, a permission system with an audit trail,
per-plugin health and lifecycle, resource governance, and a `.env`-backed
secret abstraction. Those are real gaps and they are worth closing.

### 2. The specification lists features to "preserve" that this repo does not have

§102 says to preserve Telegram, `pyttsx3`, screen vision, computer control and
`pywebview`/`mss`/`pynput`. Measured against the tree:

```
telegram        0 files        pyttsx3         0 files
screen vision   0 files        pywebview/mss   0 files
pynput          0 files        ollama         16 files
whisper         3 files        omnix/voice     4 files
```

Most of those were deleted deliberately in commit `d6dba2b`
("cut: retire voice deps to an extra, delete desktop/capture and the deprecated
squad") during the v1 focus pass. This specification appears to have been
written against a different OMNIX build — most likely the pre-focus one, or the
NARC project, which does have screen vision and automation.

**Consequence:** §51's computer-control, terminal and screen-vision plugins are
not "preserve and integrate" work. They are net-new builds of things that were
intentionally removed three commits ago. They are still buildable; they are just
not free, and they should be a decision rather than an assumption.

### 3. This specification reverses the v1 focus pass

`FOCUS.md`, written 2026-08-12, diagnosed the product as follows:

> **The problem is that it does eight things and none of them is a reason to
> pay.** A workspace with 16 nav items, a model-comparison arena, a browser test
> engine, a voice stack, a desktop screen-capture agent and a geopolitical
> explorer is not a product. It is a portfolio of experiments sharing a
> database.

That pass cut ARENA, AVALON, voice, desktop capture, three squad units, and the
coding and vision agents, taking Python from 31,379 to 23,348 lines. §51 of this
specification asks for the coding agent, the vision agent, the browser engine
and the desktop control agent back, plus roughly thirty more plugins.

This is not an argument against the plugin system. It is an argument about
**what ships inside the product versus what the product can be extended with** —
and that distinction is exactly what a plugin architecture is for. The
resolution below takes it seriously rather than picking a side.

## The resolution: infrastructure now, plugins on demand

Three rules that let both documents be right:

1. **The plugin core is product infrastructure and ships.** A registry with
   manifests, permissions, health and resource governance makes OMNIX
   extensible without widening the paid product's surface. It is the mechanism
   FOCUS.md would have wanted for the things it cut.

2. **Nothing is enabled by default that does not serve "an answer you can
   defend."** `enabled_by_default` is false for everything outside the research
   spine. A plugin that ships disabled costs the product nothing — no nav item,
   no support burden, no maintenance claim — and costs the user one toggle.

3. **Plugins are tier-gated, which makes them revenue rather than scope.** The
   entitlement layer built in `6b469b3` already gates by feature. A plugin
   declares the tier it requires; Ultra unlocks more. That turns §51's long list
   from a distraction into a pricing ladder.

## The plugin contract

```
omnix/core/plugin_system/
    manifest.py      Typed manifest, loaded from plugin.json, validated
    plugin.py        The base class every plugin implements
    permissions.py   Grants, prompts, audit trail
    health.py        Status, probes, degraded states
    registry.py      Discovery, enable/disable, lookup
    loader.py        Import, instantiate, isolate failures
    tools.py         Tool declaration and the bus adapter
```

### Status model (§2, §45)

A plugin is never "broken". It is in one of:

```
UNCONFIGURED   a required secret is absent — actionable, names the env var
DEGRADED       a dependency or provider is failing — partial service
DISABLED       switched off by the user or by tier
OK             probed and answering
```

A failing plugin must never take down OMNIX, and the UI must never render
"Something went wrong." Every non-OK state carries what is wrong, which
configuration key fixes it, and a link to the provider's own documentation.

### Permissions (§5)

Declared in the manifest, granted per-plugin, checked at the call. Every grant,
denial and use is written to the audit trail. Dangerous permissions
(`process.execute`, `filesystem.write`, `browser.write`) cannot be granted
silently by a manifest — they require an explicit user decision.

### The no-fabrication rule (§90) is enforced structurally

A plugin that cannot reach its provider returns a typed `Unavailable` result. It
does not return an empty list that reads as "nothing is happening in the world",
and it does not return sample data. This is the single most important property
in the whole system, because the product's entire thesis is that its answers can
be defended, and a plugin that quietly invents a quiet day destroys that
thesis more thoroughly than an outage would.

## Implementation order

Phase 1 is the core plus **one real plugin end to end**, chosen to prove the
whole chain honestly:

**USGS earthquakes.** No API key, a documented public GeoJSON endpoint, real
data, and a real failure mode to exercise. It proves manifest → discovery →
permission → adapter → cache → health → degraded → tool bus without needing a
single credential. A core with no plugin through it is a core that has not been
tested.

Subsequent phases follow the specification's own ordering (§81), with the
`enabled_by_default: false` rule applied throughout, and with the plugins that
need credentials built complete-but-unconfigured rather than faked.

## What this plan does not do

- It does not ship thirty plugins in one commit. §81 explicitly forbids that,
  and it is the right instruction.
- It does not rebuild the evidence, claim, graph, routing or caching layers that
  already work. It adapts them.
- It does not implement offensive security, military targeting, or credential
  theft (§80, §25, §52).
- It does not re-add the desktop, voice and coding agents by default. They are
  buildable as plugins when asked for; they are not assumed.
