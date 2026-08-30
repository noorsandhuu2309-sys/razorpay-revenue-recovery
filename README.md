# OMNIX

**A research workspace that returns an answer you can defend.**

Ask OMNIX a question and it searches the live web, reads the sources, and
writes a brief. So does everything else. The difference is what comes back
attached to it: every claim carries the sources it was drawn from, a verdict
(`verified` / `weak` / `unsupported`), a count of how many *independent*
sources corroborate it with near-duplicates collapsed, and a confidence score
derived from source credibility — none of it decided by a language model.
Findings persist into a graph, so the second question builds on the first, and
tracked objects report when the answer changes.

Perplexity answers and forgets. OMNIX answers, shows its work, and keeps watch.

---

## Run it

Requires Python 3.12 and an internet connection — the models are hosted on
NVIDIA NIM, the basemap tiles come from CARTO, and research does live web
search.

```powershell
git clone https://github.com/karthikeyachenchu/OMNIX.git
cd OMNIX
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# API keys. Copy the example and fill in NVIDIA_API_KEY (build.nvidia.com).
copy omnix_secrets.example.json omnix_secrets.json

# The UI is a Vite build; it is not committed.
cd frontend && npm install && npm run build && cd ..

.\.venv\Scripts\python.exe -m omnix.server
```

Then open **http://127.0.0.1:8000**. The first run creates the SQLite schema
and asks you to make an account; `/api/*` returns `{"authRequired": true}`
until you sign in.

A keeper thread warms the lead models on a loop. A cold mixture-of-experts
instance can take 20s to answer or return a 502 where a warm one answers in
under a second, so give the server five minutes before judging its speed.

Do **not** launch via `omnix.ps1` — it is a leftover from an earlier build and
shells out to a script that no longer exists.

---

## The engineering decision I would defend

The verification badge — the whole reason to use this over a chatbot — was
lying.

A live run on *"Strait of Hormuz shipping risk"* returned 31 claims. All 31
were marked `verified`, at confidence 57. Reading them:

- `"...the sea state wave height being at --."` — the extractor had scraped an
  unpopulated dashboard widget and emitted `--` as a value.
- `"The Strait of Hormuz blockade has resulted in..."` — welded onto claim
  after claim. **No source established that a blockade had occurred.** The
  extractor invented a causal premise and propagated it.

A demo would never have caught this. Every badge was green and the answer read
beautifully. The failure mode of a research agent is not being unhelpful, it is
being *fluent and wrong* — and that is indistinguishable from being right until
someone opens the link, which is precisely the moment trust is lost.

The fix was to stop letting the model grade its own homework.
`omnix/squad/oracle_evidence.py` is entirely deterministic — lexical and
numeric overlap between claim and retrieved source text, domain
classification, date extraction, near-duplicate detection, numeric conflict
detection across sources. No model is asked whether it was right. **The LLM
proposes; the evidence engine disposes.** Claims whose citations do not survive
the check are demoted to `weak` or `unsupported`, and `[n]` markers the
evidence cannot carry are stripped.

Confidence is then `independent corroboration × credibility × verification`,
where credibility is a transparent function of the domain — a peer-reviewed
journal, a `.gov`, a standards body and a Medium post are not the same
evidence, and three syndicated copies of one wire story are not three
confirmations.

The lesson generalised into a rule the codebase follows: **a signal a model
produces about its own output is not evidence.**

---

## Architecture

```
frontend/          Vite + React + TypeScript SPA — the product surface,
                   built into omnix/webapp/ and served as a SPA from /

omnix/
  server.py        FastAPI app: SSE streaming, auth gate, SPA mount
  router.py        Message -> agent. Regex heuristics first, tiny LLM
                   classifier only for the ambiguous remainder, so the
                   common case costs no model call
  agents/          chat, research, coding, reasoning, vision
  squad/           ORACLE (research) + oracle_evidence.py (verification),
                   CHALLENGE (multi-model panel), NOVA, FORGE, SENTINEL, PULSE
  models/          INTERNAL capability router — subsystems ask for FAST or
                   REASONING and never name a model
  model_catalog.py The human-facing roster in Settings. Deliberately NOT the
                   same module: two different audiences
  nvidia_client.py NVIDIA NIM transport — hedged ladders, timeouts, warmth
  core/            SQLite schema, objects, ontology, intents, executions,
                   conversation, artifacts, rate limits, tenancy, plugins
  graph/           The persistent finding graph
  terra/           World map: ingest, clustering, geocoding, risk, place search
  helix/           Bioinformatics corpus (PubMed) + BM25 retrieval
```

**Why the two model routers are separate** is the design question worth asking
about, and the answer is in `model_catalog.py`: fusing them would either drag
provider plumbing into the settings screen or force every subsystem through a
list curated for humans to read.

**Model selection is measured, not assumed.** The roster was re-probed against
a realistic 9,000-character prompt (a research turn carries five sources with
two pages inlined — prefill dominates, and ranking on one-line questions had
picked the three slowest models on the tier). Private "thinking" is off for
chat, research and vision, where it cost 3–18s per turn and broke the citation
format, and on for coding and reasoning, where it is the difference between a
right answer and a confidently wrong one. Working notes: `EXPO.md`.

---

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q     # 655 tests
cd frontend && npm test                            # 166 tests
```

Behaviour that was expensive to learn is pinned by a test, not by a comment:
`test_evidence.py` holds the verification rules, `test_model_ladders.py` the
routing and fallback, `test_terra_geo.py` the geocoding.

---

## Honest status

- **Scope was cut, deliberately.** An earlier version of this workspace did
  eight things — a model arena, a browser test engine, a voice stack, a
  desktop capture agent. It was a portfolio of experiments sharing a database,
  not a product. `FOCUS.md` is the record of that decision and what it cost.
- **Cloud-dependent.** There is no offline path; the local-model fallback was
  removed once the hosted ladders proved faster and the audience turned out to
  be on low-spec machines.
- **Single-tenant in practice.** Tenancy exists in the schema and is tested,
  but this has never been run for more than one account at a time.
