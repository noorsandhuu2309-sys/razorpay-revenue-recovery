# OMNIX v1 — the focus pass

Written 2026-08-12, from evidence, not opinion.

## The diagnosis

OMNIX is not broken. 345 tests pass (231 Python, 114 frontend). The core
research loop runs end to end in ~100s and returns 8 independent sources and
31 claims with a confidence score.

**The problem is that it does eight things and none of them is a reason to
pay.** A workspace with 16 nav items, a model-comparison arena, a browser
test engine, a voice stack, a desktop screen-capture agent and a geopolitical
explorer is not a product. It is a portfolio of experiments sharing a database.

### The one thing worth selling

Run a research question and OMNIX returns something ChatGPT and Perplexity
structurally cannot: **an answer you can defend.** Every claim carries the
sources it came from, a verdict (`verified` / `weak` / `unsupported`), a count
of *independent* corroborating sources with near-duplicates collapsed, and a
confidence score derived from source credibility. Findings persist into a
graph, so the second question builds on the first, and tracked objects report
when the answer changes.

Perplexity answers and forgets. OMNIX answers, shows its work, and keeps watch.

That is the product. Everything that does not serve it is overhead.

### But the differentiator is currently fake

This is the finding that matters, and it is bad. A live run on "Strait of
Hormuz shipping risk" produced 31 claims, **all 31 marked `verified`**, at
confidence 57. Reading them:

- `"...the sea state wave height being at --."` — the extractor scraped an
  unpopulated dashboard widget and emitted `--` as a value.
- `"The Strait of Hormuz blockade has resulted in..."` — prefixed onto claim
  after claim. **No source established that a blockade occurred.** The
  extractor invented a causal premise and welded it to every finding.

The verification maths in `oracle_evidence.py` is genuinely good — the numeric
guard in `_support_score`, duplicate-collapsing before counting independence,
the diminishing-returns corroboration curve. It is let down by its input. It
scores lexical overlap, so an invented causal frame wrapped around a real
number still scores as `verified`.

**A product that stamps "verified" on a fabricated premise is worse than one
that says nothing.** It is the only feature anyone would pay for and it is
currently lying. Fixing this outranks every other item.

Also: the run created 31 claim objects and **0 relationships**. The
"intelligence graph" is a bag of orphan nodes.

## The cuts

Judged against one question: *does this help someone defend a conclusion?*

| Cut | Size | Why |
|---|---|---|
| ARENA | ~76k | A model-comparison arena is a developer toy. No researcher pays to benchmark LLMs. Biggest single maintenance load in the repo. |
| AVALON | ~4 modules | A Playwright web-app validation engine. Unrelated to research by any reading. |
| Voice (TTS/STT) | 5 deps | `faster-whisper`, `piper-tts`, `sounddevice`, `webrtcvad`, `voice_models/`. Nobody buys a research tool for dictation. Heavy install cost. |
| Desktop / BugHound | 4 deps | `pywebview`, `mss`, `pynput` screen-capture agent. Off-mission. |
| `atlas`, `warden`, `muse` | ~690 | Already marked DEPRECATED in the adapter. |
| `coding`, `vision` agents | — | Off-mission. Cursor exists; we are not competing there. |
| Legacy `OMNIX.html` | 1.4MB | Retired bundle, still shipped in the repo. |
| TERRA `predict.py`, `risk.py` | — | Fabricated forecasts and risk scores with no provenance — the exact behaviour the rest of the product exists to prevent. Actively contradicts the value prop. |

**Kept:** research → claims → verification → provenance → graph → brief →
tracking. Plus TERRA's map and graph as *views over the workspace graph*,
because seeing where your evidence sits is a real analytical affordance.

## Why anyone pays, and how much

Buyers are people who must **defend** a conclusion to someone else: analysts,
consultants, diligence teams, journalists, grad students, policy staff. For
them a wrong citation is a career event, so checking an LLM's work by hand is
already a line item in their week. OMNIX sells that hour back with a paper
trail attached.

Comparables: Perplexity Pro $20, Elicit $12–42, Consensus $9–12, Scite $20.

| Tier | Price | Contents |
|---|---|---|
| Free | $0 | 10 runs/mo, 1 space, no export. Enough to hit the "it caught a bad source" moment. |
| **Pro** | **$19 launch → $29** | 200 runs/mo, unlimited spaces, export with citation appendix, change tracking. |
| Team | $49/seat/mo | Shared spaces, audit log, review queue. |

Pro is the product. Free exists to reach the moment where the confidence score
disagrees with the user's assumption — that is the conversion event, not any
feature list. Charge from day one; a research tool that is free reads as a toy,
and the buyer is expensing it anyway.

## Order of work

1. ~~**Make `verified` mean something**~~ — **done.** Junk filter, premise
   guard, and `verified` now requires two independent sources. Same question
   re-run: 31 junk claims → 15 real ones, confidence 57 → 69. 19 tests on a
   module that previously had none.
2. ~~Research must build relationships, not orphan claims~~ — **done.** The
   cause was structural, not a tuning problem: `persist_claims` mirrored each
   claim into the graph and then only ever called `attach_source`, which writes
   an ObjectSource provenance row, never a Relationship. **A claim could not
   hold an edge at all** — the live Space had 91 claim objects and 0
   relationships touching any of them. Claims now link to the entities they
   name with `about`, matched on whole-token containment over normalised names
   so a mention edge is an assertion, not a substring accident.

   Two secondary fixes fell out of it. The mention pool includes entities the
   Space already knows, so a follow-up question extends the graph instead of
   building an island beside it. And a corroborated claim now grounds an entity
   the source gate held back: the extractor cites the sources it *noticed* an
   entity in, so a run could produce fifteen `verified` claims and still commit
   nothing. An entity named by a two-source claim has cleared the same bar from
   the other side.
3. ~~Execute the cuts~~ — **done.** Python 31,379 → 23,348 lines (−26%),
   frontend −4,422, JS bundle 430KB → 415KB even after adding a feature.
4. ~~Collapse the navigation to the spine~~ — **done.** 21 destinations were
   visible at once; the default rail is now ten. Research (Ask, Claims,
   Sources, Graph, Brief) and Work (Intents, Outputs, Agents) stay open; the
   three alternative lenses and TERRA's eight fold behind headers. Nothing was
   removed — every view keeps its route and its command-palette entry, and an
   opened fold is remembered.
5. ~~Export with a citation appendix~~ — **done.** A new `dossier` style:
   claims ordered by how well attested they are, each carrying `[n]` markers
   into a numbered appendix with publisher, tier, credibility and the date the
   page was actually retrieved. Numbering belongs to the document and covers
   only what it cites — a bibliography of everything the Space ever fetched
   lets an unsupported document look well-sourced by association.

   The join had to go through the graph. `Claim.supported_by_json` holds
   ORACLE's per-run citation numbers, and nothing maps those back to Source
   rows once sources are deduped on URL across runs; the ObjectSource edges are
   what survive. PDF is the print-ready page rather than a server-generated
   file — OMNIX ships no PDF engine, and neither candidate dependency is worth
   it when the browser already paginates correctly.

### Added on the way

**CHALLENGE** (`omnix/squad/challenge.py`) — the ARENA capability rebuilt at
~1/100th the size and pointed at the funnel. Four vendors' models attack an
idea independently; output is assumptions, the strongest counterargument, where
the panel split, and the questions that would settle it — one click from a
research run. No score, no verdict, no gauge: model agreement is not evidence,
and the interface refuses to imply it is.

**ViewBoundary** — a render error in one view used to unmount the entire
workspace. It is now contained to the view.

**The CHALLENGE panel, drawn live.** The view capped itself at 860px, so on a
wide window it sat in the left half with dead space beside it, and while running
it showed a spinner and a sentence. It now fills the pane, lays the result out
in an auto-fitting grid, and draws the panel as what it is: one idea fanned out
to four vendors and folded back into a consolidation step, each seat named with
its checkpoint and its state.

Every seat state comes from an `execution.progress` event the backend emits as
it happens — `seat.thinking`, `seat.answered`, `seat.silent`, `seat.unusable`,
`seat.retry` — polled incrementally by sequence number. Nothing is on a timer.
A client-side animation that advances on its own looks identical whether four
models are answering or the backend is unreachable, which is precisely the
failure the rest of this product exists to prevent. The strip collapses to
nothing when the run ends, so the answer gets the space; the final roll-call
comes from `meta.answered`, because a second count derived from progress events
disagreed with it in practice — polling stops the moment the execution reads
`completed`, and the last seat's event can still be in flight.

**Query TERRA** opened as a mode switcher over an empty input with nothing
below it, which reads as a page that failed to load. It now says what each of
the three modes does and what it returns, with examples that run on click —
the hardest part of searching a corpus you have not seen is guessing what it
can answer.

## Where this leaves the product

The spine works end to end and is honest about what it knows:

    Challenge an idea  →  questions worth researching  →  ORACLE researches
    →  claims with sources, verdicts and independent-corroboration counts
    →  a graph that persists  →  tracking that reports when it changes

562 tests pass (424 Python, 138 frontend). Every step above has been run live,
not just unit-tested.

The three gaps this document opened with are closed. What is honestly still
open:

- **The mention matcher is lexical.** It links a claim to an entity when the
  entity's name appears in the claim as whole tokens. That is precise but not
  clever: it will not connect "Tehran" to Iran, and a junk entity already in
  the graph (there is one literally called "tensions") collects edges it does
  not deserve. The review gate keeps new junk out; it cannot retract what is
  already in.
- **PDF depends on the browser.** Fine for a person exporting a document,
  useless for anything scheduled or server-side. A real engine is one
  dependency away if that becomes a requirement.

### Closed since

**Auth.** Real accounts, on by default — `omnix/auth.py` + `omnix/api/auth.py`
and a `_require_session` middleware. Every `/api/*` route 401s without the
`omnix_session` cookie except `/api/auth/*` and `/api/health`; the SPA bundle
stays public so React can draw the login screen. `OMNIX_AUTH=off` disables it.
This was "the largest gap" in the restructure plan.

**The lock screen is the original one again.** The workspace rewrite had
replaced it with a centred card, which reads as a form rather than as a gate.
The two-panel screen from the pre-workspace build is restored from
`omnix/web/login.html` on the unmerged `worktree-omnix-login-auth` branch:
card on the left, a WebGL dithered-Perlin field on the right under the
"One assistant. Many minds." plate. The shader is unchanged except that it now
reads `--omx-accent-rgb` instead of hard-coding gold, so the gate arrives in
whichever theme the user last chose, and it releases its GL context on unmount
— contexts are capped at ~16 per browser and this component mounts and
unmounts on every sign-out.

**`OMNIX_AUTH=demo`** — a third value beside `on`/`off`, added so the gate can
be *shown* without a mistyped password ending a walkthrough. Any password opens
the account named; an unrecognised address opens the oldest one. It is not a
weaker policy, it is none, so it is fenced: it must be named explicitly, an
invite code (the marker `/api/auth/forgot` already treats as "hosted") turns it
off, the server prints a warning at startup, and the screen says so in small
type. Two things it does *not* open — `change_password` still costs the current
password, and a bypassed sign-in never rehashes, which would otherwise store
the typed-in wrong password as the real credential and lock the account after
the demo. **This is deliberately temporary; the gate is only real under
`OMNIX_AUTH=on`.**

**Source titles.** The appendix printed `www.fpri.org` in both the Publisher
and Source columns, which read as a display bug and was three drops in a row:
`search_deep` parsed each fetched page's `<title>` and kept only the body text;
ORACLE's `source_scores` carried `host` but neither `title` nor `snippet`, so
even a good search-result title never reached persistence; and
`persist_sources` therefore always took its host fallback. Only the last was
visible, which is why it looked cosmetic.

Fixing the chain leaves the sources already in the database, whose pages were
retrieved long ago. Those get `_title_from_slug`, which *reads* a title off the
URL's last segment rather than guessing at the page — two alphabetic words
minimum, nothing identifier-shaped (`/paper/2502.00072`,
`/posts/richard-anani-789156241_the-t`), and a four-digit run refuses the whole
slug even when it is really a year. A confident wrong title is worse than an
honest hostname here of all places. It took host-titled sources from 13% to 9%
of the live corpus (52 → 35 of 379); the remainder are bare domains and opaque
IDs that only a refetch can name.
