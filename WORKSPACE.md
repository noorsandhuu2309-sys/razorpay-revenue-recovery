# OMNIX — Intelligence Workspace: audit & implementation plan

Companion to `RESTRUCTURE.md`. That document narrowed the agent roster and built
the platform substrate. This one turns OMNIX from an assistant into a persistent
intelligence workspace.

**Thesis:** chatbots generate answers; OMNIX builds and maintains an explorable
model of the user's problem. Every decision below is judged against that.

---

## 1. Existing architecture

| Layer | What is actually there | Verdict |
|---|---|---|
| Backend | FastAPI modular monolith, `omnix/server.py`, 75 routes | **Keep** |
| Platform core | `omnix/core/` — workspace, artifacts, executions, events | **Keep, extend** |
| Model layer | `omnix/models/` — capability registry, provider ABC, router, cost ledger | **Keep as-is** |
| Persistence | SQLAlchemy 2.0 over SQLite, 15 tables, Postgres-compatible | **Keep, extend** |
| Agents | `omnix/squad/` — 5 units on a `Unit`/`UnitResult` contract | **Keep, re-surface** |
| TERRA | `omnix/terra/` (18 modules) + 4 JS sidecars | **Generalize, don't rebuild** |
| Frontend | `build_frontend.py` → 2.65 MB single-file bundle | **Replaced, in stages** |
| State | Six JSON files in repo root + SQLite | Consolidating into SQLite |

### The frontend problem, stated precisely

`build_frontend.py` is not a build script. It reads `OMNIX.html` (a design
export), extracts a JSON-encoded template from a `<script type="__bundler/template">`
tag, and runs **21 `replace_once()` regex substitutions** against that string,
then concatenates ~20 vanilla-JS IIFEs. 10,700 lines.

It works, and it produced a genuinely good-looking product. But a context lens,
command palette, object inspector and synchronized split views need real
component state and a real reconciler. Extending this file is the wrong bet.

**Decision:** new Vite + React + TS app at `/workspace`. The existing bundle keeps
serving `/` untouched until the workspace wins on merit. *(Resolved 2026-08-04:
it won; the bundle is retired and the React app serves `/`. See §15.1.)*
`build_frontend.py` is
deleted at that point, not before.

---

## 2. TERRA components that can be reused

This is the highest-leverage section. TERRA is not geopolitics-specific by
accident of design — it is *nearly* general already.

| Component | Reusability | Why |
|---|---|---|
| `ontology.py` `TYPES` / `VISUAL` split | **Direct** | Already separates reasoning vocabulary from presentation vocabulary. New object types map onto the 9 visual families; the renderer never changes. This seam is why generalization is cheap. |
| `ontology.py` `RELATIONS` | **Direct** | `symmetric` + `weight` per relation, plus `relation_ok()` coercion so an LLM cannot invent edge types at runtime. Extend the dict; keep the discipline. |
| `graph.py` `KnowledgeGraph` | **Port, don't rewrite** | `subgraph()`, `expand()`, `neighbors()`, `path_between()`, `communities()`, `importance()` are all domain-agnostic graph algorithms. Only storage and provenance are TERRA-specific. |
| `graph.py` importance/decay | **Direct** | Weighted degree with 72h half-life. Correct for any live intelligence graph. |
| cosmos.gl renderer + overlay | **Direct** | Label budgeting, semantic zoom, hover isolation, cluster hulls. The hard part is done. |
| `build_terra_explorer.js` Bus | **Generalize** | Correct pattern, wrong cardinality — see §7. |
| Map / Timeline / Relationships views | **Generalize** | Views are already driven by the bus rather than owning state. |
| `store.py` | **Reference only** | Article-specific. The general layer is `Source` + `Object`. |

### What is TERRA-specific and must change

1. **Provenance is article-shaped.** Nodes carry `articles: [article_id]`, edges
   the same. The general form is a reference to a `Source` row, with the article
   store becoming one source *kind*.
2. **The graph is a global JSON file**, not workspace-scoped. Two users, or two
   projects, share one graph today.
3. **Ingestion assumes news.** `ingest_articles()` is the only writer. ORACLE
   research, repo import and document upload all need to write to the same graph.

---

## 3. Existing agent functionality worth surfacing

`oracle_evidence.py` (467 ln) is the most undervalued file in the repo. It
already implements, deterministically and without an LLM:

- `classify_source()` → tier + label. **§14's `PRIMARY SOURCE` / `OFFICIAL` /
  `ACADEMIC` indicators already exist.** No source-quality percentages are
  invented, which is exactly what §37 demands.
- `Claim` with `supported_by` / `contradicted_by` → **§15's Claim Ledger.**
- `verify_claims()` + `_confidence()` → the five-status verdict scale.
- `numeric_conflicts()` → **§17's Contradiction Engine, already built.**
- `mark_duplicates()` (Jaccard shingling) → §18's dedup requirement.
- `consolidate_claims()`, citation auditing.

`oracle.py::run()` already runs plan → search → extract → verify → gap-find →
skeptic → synthesize. **The pipeline in §13 exists.** What is missing is only the
final step: *writing the result into the workspace as objects and relationships*
instead of rendering it to markdown.

That is the single most important insight in this audit. ORACLE does not need to
be rebuilt. It needs an outlet.

---

## 4. Proposed unified architecture

```
                    ┌────────────────────────────────┐
                    │      /workspace  (React)       │
                    │  Graph · Map · Timeline ·      │
                    │  Table · Document · Board      │
                    │      ↕ shared selection ↕      │
                    │  Context Lens · Inspector ·    │
                    │  Command Palette · NOVA input  │
                    └───────────────┬────────────────┘
                                    │ REST + SSE
                    ┌───────────────┴────────────────┐
                    │        omnix/api/              │
                    ├────────────────────────────────┤
                    │  core/  objects · relationships│
                    │         artifacts · executions │
                    │         events · workspace     │
                    ├────────────────────────────────┤
                    │  graph/ engine (from terra)    │
                    ├────────────────────────────────┤
                    │  agents  NOVA ORACLE FORGE     │
                    │          SENTINEL  (PULSE=obs) │
                    ├────────────────────────────────┤
                    │  models/ router → providers    │
                    └────────────────┬───────────────┘
                                     │
                              SQLite → Postgres
```

Agents become **capabilities invoked against objects**, not destinations the user
navigates to. The user picks *what*; OMNIX picks *who*.

---

## 5. Object / relationship / event / source data model

New tables in `omnix/core/schema.py`. Names chosen to not collide with the
existing `Event` (execution event) — the object-level one is `ObjectEvent`.

```python
class ObjectNode:          # table: "object"
    id, workspace_id, type, name, description
    properties_json        # type-specific, schemaless by design
    tags_json
    external_id            # "country:US", "repo:omnix/file:auth.ts" — stable identity
    provenance             # verified | source_backed | ai_inferred | user_created
    confidence             # only when legitimately measurable, else NULL
    salience               # computed, for sizing/ranking
    tracked                # §21 live objects
    lat, lon               # nullable — drives Map eligibility
    first_seen, last_seen, created_at

class Relationship:        # first-class, per §3
    id, workspace_id, src_id, dst_id, relation
    weight, symmetric, provenance, confidence
    properties_json
    first_seen, last_seen

class ObjectEvent:         # temporal facts — drives Timeline
    id, workspace_id, object_id, type, title, body
    occurred_at, detected_at
    provenance, execution_id

class ObjectSource:        # provenance edge: what evidence backs this
    id, object_id | relationship_id | event_id  (exactly one)
    source_id → source.id
    excerpt, created_at
```

Design rules:

- **`type` is a string, not an enum.** §3 says do not hard-code the UI around a
  fixed type list. Types are registered in a Python registry that maps type →
  visual family → renderer, and unknown types degrade to a default family rather
  than breaking.
- **`provenance` is mandatory and never defaults to a strong value.** §31 requires
  users to distinguish verified from AI-inferred. An object created by an LLM
  extraction is `ai_inferred` until evidence attaches.
- **`confidence` is nullable on purpose.** §37 forbids decorative numbers. NULL
  renders as "not measured", never as 0% or 50%.
- **`external_id` gives cross-run identity.** Re-running research on NVIDIA must
  update the existing object, not create a second one. This is also what makes
  §20's Research Diff possible.
- Reuses the existing `Source` and `Claim` tables unchanged.

---

## 6. Workspace architecture

`Workspace` already exists and already scopes artifacts and executions. Extend it
to scope objects, relationships and events (all four new tables carry
`workspace_id`).

Added:
- `saved_view` — §4's saved views and layout state.
- `Workspace.settings_json` gains `layout`, `default_view`, `pinned_context`.

The local single-user fallback in `core/workspace.py` stays, so nothing requires
auth to work locally.

---

## 7. Context Lens implementation

The current bus is single-selection:

```js
var Bus = X.bus = { id: null, meta: null, subs: [], select: fn }
```

Generalizing to a set is the whole of §7 and §8:

```ts
interface SelectionState {
  primary: string | null          // focus — Inspector target, breadcrumb
  selected: string[]              // full set — Context Lens contents
  pinned: string[]                // survives selection changes (§8)
  origin: ViewId                  // which view initiated, to avoid feedback loops
}
```

- Plain click → replace `selected` with `[id]`, set `primary`.
- Ctrl/Cmd-click → toggle membership, `primary` unchanged.
- `pinned` merges into AI context but is not cleared by navigation.
- **`origin` matters:** without it, view A selects → view B reacts → B re-emits →
  infinite loop. The existing bus has this bug latent; it survives only because
  single selection converges. A set will not.

Context Lens is a thin renderer over `selected + pinned`. NOVA reads the same
state and hydrates object summaries into its prompt automatically — the user
never describes what they mean by "these".

Implementation: Zustand store with `subscribeWithSelector`. Small, no boilerplate,
correct for a solo maintainer.

---

## 8. Graph generalization strategy

Four steps, each independently shippable:

1. **Extract the algorithms.** Move `subgraph`, `expand`, `neighbors`,
   `path_between`, `communities`, `importance` from `terra/graph.py` into
   `omnix/graph/engine.py`, operating on an abstract node/edge provider rather
   than `self.nodes` / `self.adj`. Pure refactor, no behaviour change.
2. **Add a SQL-backed provider** reading `object` / `relationship`. TERRA's
   JSON-backed provider stays alongside it.
3. **Extend the ontology registry** with the new families (project, repository,
   file, document, task, claim, source, finding, technology, product, dataset…),
   each mapped to a visual family. Reuse the 9 existing families where honest —
   a Company is a Company whether it appears in news or in a competitor analysis.
4. **Bridge TERRA into the workspace.** A "Geopolitical Intelligence" workspace
   projects the existing TERRA graph through the same object API. TERRA keeps its
   own refresh loop and JSON store; nothing about it breaks.

**TERRA is never rewritten.** At the end it is one workspace among several, drawn
by the same engine.

---

## 9. NOVA orchestration strategy

NOVA stops being a chat destination and becomes the workspace's command layer.

```
input + selection context + workspace summary
        ↓
   classify: answer | query | single-agent | workflow
        ↓
 ┌──────────┬──────────┬───────────┬──────────────┐
 │ direct   │ workspace│ one agent │ compile DAG  │
 │ answer   │ query    │ execution │ (§10)        │
 └──────────┴──────────┴───────────┴──────────────┘
```

`nova.py`'s `_ROUTES` / intent parser already does classification. What it gains
is (a) selection as implicit context and (b) **workspace query** as a first-class
outcome — "find everything related to authentication" is a graph traversal, not a
model call, and answering it from the database is faster, free and correct.

The DAG compiler targets `ExecutionStep` with `depends_on_json`, which already
supports exactly this. §10 needs no new execution engine.

---

## 10. ORACLE research-to-graph pipeline

The existing pipeline gains one terminal stage:

```
question → plan → search → sources → claims → verify → conflicts → synthesize
                                                                        │
                                              ┌─────────────────────────┘
                                              ▼
                                    ENTITY EXTRACTION  (new)
                                              │
                                    ┌─────────┴──────────┐
                                    ▼                    ▼
                            objects + relationships   events
                                    │                    │
                                    └────────┬───────────┘
                                             ▼
                                   dedup vs existing objects
                                    (external_id + name similarity)
                                             ▼
                                   REVIEW GATE  — "12 new objects discovered"
                                             ▼
                                    workspace graph updated
```

Reuses `mark_duplicates()`'s Jaccard shingling for dedup. Every created object
carries `provenance = ai_inferred` plus `ObjectSource` rows pointing at the real
`Source` that justified it — §31 satisfied by construction, not by a later audit.

The review gate matters: §18 explicitly warns against blindly polluting the graph.
Below a relevance threshold, objects are proposed, not committed.

---

## 11. Model / provider strategy

**No change required.** `omnix/models/` already has the `ModelProvider` ABC, the
six capabilities from §35, NVIDIA + Ollama providers, a capability→ladder registry
and a `model_call` ledger with measured tokens. Adding OpenAI/Anthropic/Google is
a new `ModelProvider` subclass and registry entries.

One addition: `EMBEDDING` is declared but unwired. Semantic search stays
unavailable — SQLite FTS5 is the honest interim, per `RESTRUCTURE.md`.

---

## 12. Required backend changes

| Change | Effort | Phase |
|---|---|---|
| 4 new tables + `saved_view` | S | 1 |
| `core/objects.py` service (CRUD, dedup, merge, tracking) | M | 1 |
| `core/ontology.py` extensible type registry | S | 1 |
| `graph/engine.py` extracted from `terra/graph.py` | M | 1 |
| SQL node/edge provider | M | 1 |
| `/api/objects`, `/api/relationships`, `/api/graph/*` | M | 1 |
| TERRA → workspace bridge | M | 1 |
| **Alembic** (blocking — `create_all()` cannot add columns) | S | 1 |
| ORACLE entity-extraction stage | M | 3 |
| NOVA workspace-query route | M | 2 |
| Tracking scheduler + change detection | M | 4 |

## 13. Required frontend changes

New `frontend/` app (Vite + React + TS + Zustand), served at `/workspace`.

| Piece | Notes |
|---|---|
| App shell | Left rail (workspaces/views), centre canvas, right inspector, bottom NOVA bar |
| Design tokens | Extracted from TERRA: `--omx-gold: #d3ad55`, `--omx-ground: #060606`. Light + dark both first-class |
| Graph view | cosmos.gl, ported from `build_terra_explorer.js` including label budgeting |
| Inspector | §29's 8 sections, one component driven by type registry |
| Context Lens | Chip row over the selection store |
| Command palette | ⌘K, context-sensitive |
| Map / Timeline / Table / Document | Map + Timeline port from TERRA; Table and Document are new |
| Motion | 150–300ms, `prefers-reduced-motion` respected |

**Do not repeat the `installTabIndicator` bug** — see the observer-trap note. Any
MutationObserver-driven coupling is gone in React; this class of freeze
disappears with the architecture.

## 14. External APIs / services required

Nothing new for V1. Existing: NVIDIA NIM, DuckDuckGo (`ddgs`), the RSS/news
sources TERRA already ingests. Later and explicitly *not* now: OSV.dev
(SENTINEL — never invent CVEs), Crossref/OpenAlex (only if academic badges are
ever claimed), an embedding provider.

## 15. Migration strategy

1. Land Alembic **before** the new tables. `create_all()` silently no-ops on an
   existing DB; without migrations the first schema change corrupts dev state.
2. New tables are purely additive — no existing table is altered.
3. TERRA's JSON store and graph stay on disk, projected read-only into the
   workspace at first. No destructive migration.
4. `/` and `/workspace` coexist. Both hit the same API.
5. Cut `/` over only when the workspace is better. Delete `build_frontend.py`
   then, and not before.

**Every phase leaves the app working.** That is a hard constraint.

### 15.1 The cutover happened — 2026-08-04

Step 5 is done. `/` serves the React app; `/workspace/*` 308-redirects to it so
saved links still land. `build_frontend.py`, its two `.bak` copies, the four
`build_terra*.js` sidecars and the generated pages (`omnix/web/index.html`,
`atlas.html`, `sentinel.html`) are deleted. `OMNIX.html` is **kept** — it is the
design export, it is not served, and the unmerged `worktree-omnix-login-auth`
branch uses it as its React donor.

**`omnix/web/` survives, and must.** It is not the bundle — it is what `/static`
serves, and the React graph and map load `cosmos.min.js`, `world.json`,
`states.json` and `cities.json` from it. Deleting the directory along with the
page it used to hold breaks both views;  `test_the_gazetteers_survive_the_retirement`
exists to catch exactly that.

**Deliberately dropped with the bundle**, since only the old page called them:
voice (`/api/tts`, `/api/stt`), reminders, facts, personal knowledge recall,
weather, and the standalone SENTINEL and ATLAS consoles. **Every one of those
endpoints still exists** — nothing was deleted server-side, so any of them can
return as a workspace view without being rebuilt. They were assistant features
belonging to the product OMNIX no longer is.

Frontend tests landed at the same time (`frontend/`, Vitest + Testing Library,
`npm test`): 43 tests over the product invariants — trust-lens filter
semantics and monotonicity, the selection bus and its `origin` echo guard,
focus-trail push/pop, and ActionBar arity gating. Deliberately not snapshots.

## 16. Implementation phases

| Phase | Content | Ships |
|---|---|---|
| **1** | Alembic · object/relationship/event/provenance tables · ontology registry · graph engine extraction · SQL provider · object API · TERRA bridge | Backend only. No UI change. |
| **2** | Workspace shell · design tokens · Graph view · Inspector · selection store · Context Lens · ⌘K · NOVA input | First visible workspace |
| **3** | ORACLE → graph pipeline · Claim Ledger · Evidence Graph · Source Library · review gate · Table + Document views | **The V1 demo (§40)** |
| **4** | Tracking · research diff · change detection · Intelligence Brief · Timeline | Live intelligence |
| 5–7 | FORGE · SENTINEL · PULSE | Per `RESTRUCTURE.md` |

Phases 1–3 are the product. 4 makes it defensible. 5–7 come after it's proven.

## 17. Major risks

1. **Graph pollution.** The single biggest product risk. An intelligence graph
   full of junk entities is worse than no graph — it destroys trust permanently
   and silently. Mitigation: relevance thresholds, dedup on `external_id` +
   shingling, the review gate, and `provenance` visible on every node.
2. **Rebuilding TERRA by accident.** Generalization must be extraction, not
   reimplementation. Mitigation: phase 1 step 1 is a pure refactor with no
   behaviour change, verifiable by diffing API responses before/after.
3. **Two frontends diverging.** Mitigation: hard time-box. `/workspace` either
   overtakes `/` or the experiment is called off — no indefinite dual maintenance.
4. **Scope.** §§1–42 describe roughly two years of solo work. Mitigation: this
   plan implements phases 1–3 and stops.
5. **cosmos.gl outside TERRA.** Renderer constraints were learned the hard way and
   are documented; re-learning them in React is a real cost. Mitigation: port the
   overlay logic wholesale rather than rewriting it.
6. **No auth.** Fine locally, blocking for SaaS. Not in this plan.

## 18. What should NOT be built yet

Explicitly deferred: FORGE repo ingestion · SENTINEL scanning · the SENTINEL↔FORGE
loop · PULSE dashboards · billing · user-editable DAG graphs · Board view ·
split-view window manager · structured extraction to CSV · monitoring
infrastructure · semantic search · Kanban · PDF/DOCX export · model benchmarking ·
compliance mapping · mobile.

Per your instruction: **workspace → ORACLE research → objects → graph → timeline →
evidence → Context Lens → NOVA must feel exceptional first.**

---

## Feature classification

### P0 — required for V1
Object/relationship/event/source model · workspace scoping · ontology registry ·
graph engine generalization · object API · workspace shell · Graph view ·
Object Inspector · shared selection · Context Lens · NOVA global input · ⌘K ·
ORACLE → graph pipeline · Claim Ledger · Source Library · provenance display ·
global search · Table view · Document view · Alembic

### P1 — major differentiation
Evidence Graph · Contradiction surfacing (engine exists) · Research Diff ·
Live Objects / tracking · Intelligence Brief · Timeline view · Map view
generalization · workflow compiler + execution graph · context-sensitive actions ·
artifact cross-agent handoff · saved views

### P2 — valuable later
FORGE repo ingestion & architecture graph · SENTINEL findings as objects ·
SENTINEL↔FORGE loop · PULSE traces & cost · structured extraction/datasets ·
split views · Board · document upload for the student persona · export

### P3 — future
Auth & billing · collaboration · semantic search · user-editable DAGs ·
requirement traceability · ARENA second opinion · mobile · compliance mapping

---

## The question every feature answers

> Why pay $20–40/month for OMNIX instead of using ChatGPT?

Not "more agents". The answer this plan builds toward:

**OMNIX remembers what you're working on as structure, not as chat history —
and every claim in it can be traced back to the evidence that produced it.**

If a feature doesn't strengthen that, it doesn't belong in phases 1–3.
