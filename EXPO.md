# Running OMNIX at the expo

Written 2026-08-17, after the model re-probe and the streaming fixes.

## Start it

```powershell
cd C:\Users\karth\Downloads\OMNIX
.\.venv\Scripts\python.exe -m omnix.server
```

Then open **http://127.0.0.1:8000/**. Sign in with the demo account
(`karthikeyachunchu08@gmail.com`). Do **not** launch via `omnix.ps1` — it still
shells out to a deleted build script and prints an error on every start.

**Start it 5–10 minutes before you demo.** A keeper thread warms every lead
model on a loop; a cold MoE instance answers a 502 or takes 20s, and the same
model answers in under a second once warm. This is the single biggest
difference between a good demo and a bad one.

The laptop needs internet: the models are hosted on NVIDIA NIM, the basemap
tiles come from CARTO, and research does live web search.

## What to show, and what it should look like

| Surface | Expect |
|---|---|
| **Home — chat** | First words in **under a second**. Headings with emoji, bold key terms, tables for comparisons. |
| **Home — research** | Sources listed within ~5s, then a cited brief. ~6–10s to first word — it is searching the live web first. |
| **Home — reasoning** | ~6–8s to first word. It deliberates before answering; this is deliberate and it is what makes it right. |
| **Challenge** | Four seats — OpenAI, MiniMax, Meta, NVIDIA — filling in live, then "4 of 4 models answered". |
| **World Map** | "Drawing the world…" for a few seconds, then the world with live event clusters. |
| **Graph** | ~170 objects, ~380 links, force layout with labels. |
| **Settings → Models** | Seven models, each switchable. "Test all models" probes them live. |

## If something looks wrong

- **A model shows red in Settings** — it is cold, not dead. Press "Test all
  models" again; the probe allows 45s.
- **The map is blank** — give it ten seconds; it is fetching ~120 tiles. If it
  is still blank, the machine has no internet.
- **"All cloud models are unavailable"** — check the NVIDIA key
  (`python -m omnix.nvidia_client` prints a wiring check).
- **An answer stops halfway** — this was a real bug and is fixed. If it recurs,
  it is a network drop, not the model.

## What changed on 2026-08-16/17, in one paragraph

Every model lead was re-probed against a realistic prompt and replaced; the
previous leads were the three slowest models on the tier. Private "thinking" is
now switched off for chat, research and vision (it cost 3–18s per turn and, in
research, broke the citation format) and kept on for coding and reasoning,
where it is the difference between a right and a confidently wrong answer. Four
latency bugs were fixed: a 10s read timeout that killed long answers
mid-sentence, a flat first-token budget that declared research dead before it
had read the question, the anchor model's real 4k context window, and a
front-end animation loop that only advanced the text *between* chunks. Full
details are in `omnix/config.py`, `omnix/model_catalog.py` and
`omnix/nvidia_client.py`; the behaviour is pinned by `tests/test_model_ladders.py`
and `tests/test_oracle_ledger.py`.
