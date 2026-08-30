# OMNIX Restructure — decisions & build order

Working reference for the five-agent restructure. Full audit and rationale:
the published plan artifact (see `omnix-restructure` artifact, 2026-08-02).

## Status — phase 1 substrate is IN (2026-08-02)

Built and verified end-to-end. Nothing existing was removed or broken; the old
`/api/squad/*` routes and all eight units still work exactly as before.

| Added | What it does |
|---|---|
| `omnix/core/schema.py` | 15 tables — workspace, artifact, execution, event, model_call, finding, claim, source, task, … |
| `omnix/core/db.py` | SQLite engine, WAL + foreign keys, `OMNIX_DATABASE_URL` override |
| `omnix/core/workspace.py` | Projects, plus the local single-user fallback |
| `omnix/core/artifacts.py` | Typed envelope, reference edges, versioning-by-fork, lineage |
| `omnix/core/events.py` | Typed event bus (the table is the bus) + SSE replay/follow |
| `omnix/core/executions.py` | DAG steps, cooperative cancel, per-step status, artifact emission |
| `omnix/models/` | Capability registry, provider abstraction, router, ledger |
| `omnix/agents_v2/adapter.py` | Runs the existing squad units on the new engine |
| `omnix/api/platform.py` | `/api/workspaces`, `/api/artifacts`, `/api/executions`, `/api/agents/*/run`, `/api/usage` |

**Verified by running it:** SENTINEL scan → artifact → handoff to FORGE →
`derived_from` provenance edge → 6 model calls, 11,681 tokens, $0.0031 recorded,
`estimatedCalls: 0`. Cancellation, DAG dependency ordering, failure capture and
replay-of-finished-run all tested.

**Real token counts now exist.** `providers.NvidiaProvider.generate()` uses the
non-streaming endpoint and reads the provider's own `usage` object, so cost is
measured rather than estimated at 4 chars/token. `squad/base.run_llm` and
`oracle_models.research_llm` route through it while still using their own
measured ladders — model *selection* is unchanged, only metering was added.

Trade-off this created: dropping the streaming path also dropped its hedge
(abandon a model that produces no first token in ~8s). A stuck `gpt-oss-20b` was
measured burning 120s before failover. Mitigated by per-rung timeouts —
early rungs get `OMNIX_ROUTER_RUNG_TIMEOUT` (40s), only the last rung gets the
full `OMNIX_ROUTER_TIMEOUT` (90s). If cold-start latency becomes a complaint,
the proper fix is parallel hedging on the non-streaming path.

## Status — agent roster narrowed to five (2026-08-02)

**The interface is unchanged.** The existing OMNIX bundle and the NEXUS console
are the product surface; the restructure changes *which agents they offer*,
nothing else.

A replacement React shell at `/app` was built and then **reverted** — it was not
wanted, and rebuilding the interface is not part of this work. `frontend/` is
back to its original state (`main.tsx` renders the old `App`, vite outDir back
to `omnix/webapp`), the `/app` route is out of `server.py`, and the generated
files are deleted. Do not reintroduce a parallel UI; change the NEXUS console
in `build_frontend.py` instead.

### What changed
| File | Change |
|---|---|
| `omnix/squad/units.py` | `catalog()` now returns only NOVA · ORACLE · FORGE · SENTINEL · PULSE |
| `omnix/squad/nova.py` | `_ROUTES` / `_DISPATCHABLE` / Intent Parser prompt drop the retired units |
| `build_frontend.py` (NOVA console) | Hardcoded `AGENTS` array trimmed to the four dispatch targets |

NEXUS and CORTEX both render from `/api/squad/units`, so they picked the change
up with no frontend edit. NOVA's launcher was the only hardcoded list.

**ATLAS, WARDEN and MUSE are removed from the catalog but still resolvable by
`get_unit()`** — `/api/squad/atlas/run`, the standalone `/atlas` console and
stored job history keep working. Verified: all three still return HTTP 200.
Delete the modules and drop them from `_DEPRECATED_CLASSES` when nothing calls
them.

Routing was not simply deleted with the agents: privacy/secret keywords moved to
SENTINEL (where that work belongs now) and planning falls through to NOVA's own
direct answer. Dropping them outright would have sent "scan this config for
secrets" to a generic chat reply.

### Bugs fixed along the way (backend, still in)
- **Timestamps were off by the viewer's UTC offset.** `DateTime(timezone=True)`
  is a no-op on SQLite, so values came back naive, `.isoformat()` emitted no
  offset, and browsers parsed them as local time. Fixed with `schema.iso()`;
  `/api/usage` binds a naive-UTC window for the same reason.
- **API GETs were browser-cached**, serving stale state. Added a `no-store`
  middleware for `/api/*`.

### Still not done
- **Alembic.** `create_all()` only, so adding a column to an existing DB
  silently does nothing. Land migrations before any real data.
- **Auth / entitlements.** No user rows beyond the local fallback.
- **ATLAS→NOVA and WARDEN→SENTINEL capability merges.** Only the roster changed;
  task decomposition and the PII/secret detectors have not moved yet.

## Final product shape

| Agent | Role | Status |
|---|---|---|
| NOVA | DO — orchestration, workflow compiler, task planning | absorbs ATLAS |
| ORACLE | KNOW — evidence-first research | closest to done |
| FORGE | BUILD — architecture-first engineering over real repos | largest build |
| SENTINEL | PROTECT — web + repo security, closed-loop remediation | absorbs WARDEN |
| PULSE | OBSERVE — platform observability, not an agent | mostly assembly |

MUSE is removed from the primary product. ATLAS and WARDEN are removed as
standalone units. Deprecated modules stay on disk, unregistered, for one
release before deletion.

## Audit findings that drive everything

1. **No database, no auth, no workspace, no artifact.** Verified by grep — no
   `sqlite3`/`sqlalchemy`/`jwt`/`passlib` import anywhere. State is six JSON
   files in the repo root. Cross-agent handoff has nowhere to put the thing
   being handed off. This is the real starting point.
2. **The research differentiator already exists.** `squad/oracle_evidence.py`
   (467 ln) + `oracle.py` (502) do deterministic claim verification, source
   classification, near-duplicate detection, numeric contradiction hunting and
   citation auditing. `_find_gaps` already drives a recursive second research
   round. Surface it; do not rebuild it.
3. **The model router and cost ledger already exist — inside AVALON.**
   `avalon/gateway.py` is an `LLMBackend` ABC with Ollama/OpenRouter/NVIDIA
   backends and a `Usage` type; `avalon/credits.py` has a real pricing table,
   cost→credits conversion, a per-job ledger and budget enforcement. Both need
   promoting to `omnix/models/`, not writing.
4. **Five model-routing tables, two call paths.** `config.CLOUD_LADDER`,
   `config.CLOUD_TIERS`, `squad/oracle_models.ORACLE_LADDERS`,
   `avalon/config.NVIDIA_LADDERS` + `NVIDIA_ROLE_LADDERS`. Only AVALON's path
   reports tokens — `cloud.stream_ladder()` yields text and drops the usage
   block. That single fact is why PULSE cannot show real cost today.
5. **Three near-identical job managers** — `squad/jobs.py`,
   `avalon/service.py`, `terra/service.py`. None supports cancel, pause or
   retry; statuses are only `queued|running|done|error`.
6. **`build_frontend.py` is the highest-risk file** — 10,136 lines that regex-
   mutate a design export and inject ~20 vanilla-JS IIFEs into a 2.45 MB
   single-file bundle. It cannot carry a DAG editor, diff workbench or research
   notebook.
7. **There is no hosted product.** `api/index.py` is 33 lines serving static
   HTML; the real backend uses daemon threads and in-memory job dicts, which
   cannot run on Vercel. A SaaS launch needs a persistent host.

## Architecture decisions

- **DB:** SQLAlchemy 2.0 + Alembic over SQLite, written Postgres-compatibly
  (string UUID PKs, tz-aware timestamps, JSON columns). Swap by URL later.
- **Backend:** stay a modular monolith. New `omnix/core/` (workspace,
  artifacts, executions, events, memory, entitlements), `omnix/models/`
  (router + providers), `omnix/agents_v2/`. No queue broker, no containers
  beyond code sandboxing, no Kubernetes.
- **Frontend:** new Vite + React + TS app in `frontend/`, served at `/app`.
  The existing bundle keeps serving `/` untouched; agents cut over one at a
  time; `build_frontend.py` is deleted only when the last one lands.
- **Events:** the `event` table *is* the bus. Written in the same transaction
  as the state change, then streamed over SSE.
- **Model router:** capabilities `FAST | REASONING | CODING | LONG_CONTEXT |
  VISION | EMBEDDING`; modes `AUTO | FASTEST | BEST_QUALITY | LOWEST_COST |
  MANUAL`. Keep `cloud.stream_ladder`'s hedging — it works. Every call writes a
  `model_call` row.
- **Execution:** generalize `squad/jobs.py`. Steps are DAG nodes, so a linear
  agent run is a one-branch DAG and NOVA needs no separate engine. Cancel is
  cooperative (`threading.Event` checked between steps and inside streams).

> Preserve `JobManager._finish`'s atomic status+event transition when porting.
> Setting status before emitting let the SSE stream close early and clients
> hung forever — that bug is documented in the source and is easy to reintroduce.

## Build order

Phase 1 has no product-visible output, which makes it the one most likely to be
skipped and the most expensive to retrofit. Do it first.

1. **Platform foundation** — schema + Alembic + importer for existing job JSON;
   workspace & artifact services; execution engine with typed events, cancel,
   retry; model router with ledger; auth (local-mode escape hatch); Vite shell
   with `<AgentShell>`, `useExecution`, artifact renderer registry.
2. **ORACLE** — ported to the engine, sources & claims persisted, three-pane
   notebook, source library, claim ledger, QUICK/STANDARD/DEEP, MD+JSON export.
   (Deliberately before NOVA: closest to done, proves the platform soonest.)
3. **NOVA** — intent inspector, workflow compiler → step DAG, AUTO/GUIDED/
   MANUAL, execution center, artifact handoff, ATLAS planning merged, ⌘K,
   transparent memory.
4. **FORGE** — repo import, codebase map, architecture graph, Ask Codebase,
   then change plan → approval → worktree implementation → diff workbench →
   test lab → bounded failure loop. Ship read-only first.
5. **SENTINEL** — finding model, WARDEN detectors over repo walks, OSV.dev
   advisories, Fix with FORGE → rescan → security delta.
6. **PULSE** — traces, cost explorer, model health, error center, budgets.
7. **Differentiators** — research diff/monitor, COMPARE, datasets, ARENA
   second opinion, requirement traceability.

## Smallest set that beats a chatbot

All of phase 1, plus ORACLE, plus two P1 items (FORGE repo import, and the
SENTINEL fix→rescan loop). The thread that carries the whole value proposition:

    ORACLE researches, every claim linked to checked evidence
      -> report is an artifact
    FORGE implements it against a real repo as an inspectable diff
      -> diff is an artifact
    SENTINEL reviews it, files located evidence-backed findings
      -> Fix with FORGE -> rescan
    SENTINEL proves the finding is gone by re-running the check
    PULSE shows every model, token, second and failure in the chain

None of that is text generation, which is why a chatbot cannot do it.

**Cut from v1:** user-editable DAG graphs, Kanban/timeline views, COMPARE and
MONITOR modes, dataset extraction, PDF/DOCX, model benchmarking, compliance
mapping, and every remaining dashboard.

## Do not fake these

The spec forbids invented values. Current infrastructure cannot honestly
produce:

- **cost/tokens outside AVALON** — until the router lands (`stream_ladder`
  drops usage)
- **CVE/advisory data** — must come from OSV.dev, never an LLM
- **test coverage** — only when a coverage tool actually ran
- **model quality scores** — no eval dataset; report latency/TTFT/failure only
- **"peer reviewed"/"independent" badges** — currently a domain guess; needs
  Crossref/OpenAlex
- **semantic search** — no embedding provider wired; SQLite FTS5 is the honest
  interim
- **true pause mid-generation** — daemon threads can't suspend; offer cancel
  and between-step pause, and say so in the UI

## Migration notes

- **ATLAS → NOVA:** keep the four-role prompt chain from `squad/atlas.py`;
  output becomes `task` rows + a `task-plan` artifact instead of card text.
- **WARDEN → SENTINEL:** lift `_DETECTORS`, `_PROFILES`, `_LICENSES`, `_redact`
  unchanged into `agents_v2/sentinel/detectors.py`. Wrap matches in the Finding
  model (rule_id, file, line, evidence, remediation). Mask secrets centrally at
  the serializer. **Before the repo walk ships:** add Luhn validation to the
  `card` detector and a private-range filter to `ip`, or the first scan buries
  real findings in noise.
- **MUSE:** drop from `_UNIT_CLASSES` in `squad/units.py` and from NOVA's
  `_ROUTES`; delete its console block and `build_frontend.py.bak.musepulse`.
  Keep `agents/vision.py` and the VISION capability — the prompt-expansion
  product goes, not image understanding.

## Known stale state

`build_frontend.py`'s NOVA console hardcodes its agent roster instead of
reading `/api/squad/units` — it still advertises MUSE, ATLAS and WARDEN and
offers two local Ollama models that are not running. It will keep showing
removed agents until rewritten.
