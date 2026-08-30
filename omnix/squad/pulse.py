"""PULSE — Performance & Usage Live System Evaluator.

The squad's introspection agent. Collects live OMNIX telemetry (Ollama health,
pulled models, knowledge-cache + memory stats, AVALON/squad job history),
measures real per-model latency + throughput, analyzes it deterministically,
and an Advisor turns it into plain-language guidance. Needs no user input — it
reports on the running system itself.

Everything the console shows is real, measured telemetry:
  • health / models / missing            — live `ollama.list()` vs config.AGENTS
  • latency + tokens/sec per model        — a real minimal probe (num_predict=1)
  • jobs / queue                          — live in-memory AVALON + squad jobs
  • log stream                            — real recent job events
  • rolling sparkline series              — a module ring buffer of past samples,
                                            one appended per run (accumulates as
                                            the console auto-refreshes)
This is a local Ollama deployment, so there is no per-token dollar cost — the
console reports real latency instead of a fabricated bill.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from .base import (Subagent, Unit, UnitResult, cards_block, clamp, list_block,
                   stats_block)

# Rolling telemetry ring buffer (real successive samples). Each PULSE run appends
# one sample; the console's auto-refresh re-runs PULSE, so the sparkline fills
# with genuine history over time rather than a simulated waveform.
_SAMPLES: list[dict] = []
_MAX_SAMPLES = 48

# Cache the last latency probe briefly so rapid auto-refreshes don't re-load
# every model on each tick (probing is the one heavy part of a run).
_PROBE_CACHE: dict = {"ts": 0.0, "latency": []}
_PROBE_TTL = 20.0  # seconds


def _probe_model(name: str, timeout: float = 8.0) -> dict:
    """Real latency/throughput probe: one minimal generation. Returns measured
    ms + tokens/sec, or off=True on timeout/error. Never raises."""
    try:
        import ollama
        t0 = time.perf_counter()
        # A short burst (not a single token) so tokens/sec is a real, stable
        # measurement — one-token timings are dominated by fixed overhead.
        r = ollama.generate(model=name, prompt="Reply with a short sentence.",
                            keep_alive="5m",
                            options={"num_predict": 24, "temperature": 0})
        ms = int((time.perf_counter() - t0) * 1000)
        # eval_count / eval_duration(ns) → real tokens/sec when present.
        get = (lambda k: r.get(k) if isinstance(r, dict) else getattr(r, k, None))
        ec, ed = get("eval_count") or 0, get("eval_duration") or 0
        tps = (ec / (ed / 1e9)) if (ec and ed and ed > 0) else 0.0
        tps = max(0.0, min(tps, 500.0))  # guard against tiny-duration overflow
        return {"name": name, "ms": ms, "tps": round(tps, 1), "off": False}
    except Exception:
        return {"name": name, "ms": 0, "tps": 0.0, "off": True}


class Pulse(Unit):
    code = "pulse"
    name = "PULSE"
    glyph = "◍"
    tagline = "Performance & Usage Live System Evaluator"
    blurb = "Live OMNIX health, models & job telemetry at a glance."
    accent = "#8b7bff"
    input_label = None
    input_kind = "none"
    needs_input = False
    placeholder = ""

    def __init__(self):
        self.subagents = [
            Subagent("Collector", "gathers live system telemetry"),
            Subagent("Analyzer", "computes health signals"),
            Subagent("Advisor", "turns metrics into guidance", system=(
                "You are PULSE's Advisor. Given OMNIX system telemetry, write a "
                "2-3 sentence health summary and up to 3 concrete suggestions "
                "(e.g. pull a missing model, warm a cache). Be practical.")),
        ]

    # -- real telemetry collectors -------------------------------------------
    def _loaded_models(self) -> set[str]:
        """Names of models currently resident in Ollama memory (fast to probe)."""
        try:
            import ollama
            ps = ollama.ps()
            raw = getattr(ps, "models", None) or (ps.get("models", []) if isinstance(ps, dict) else [])
            out = set()
            for m in raw:
                nm = getattr(m, "model", None) or (m.get("model") or m.get("name") if isinstance(m, dict) else None)
                if nm:
                    out.add(nm)
            return out
        except Exception:
            return set()

    def _live_jobs(self):
        """Real running/queued jobs + recent events across AVALON and squad,
        read from the in-memory managers. Returns (running, queued, logs)."""
        running, queued, events = [], [], []

        def note(mgr, agent_of):
            try:
                jobs = list(getattr(mgr, "_jobs", {}).values())
            except Exception:
                return
            for j in jobs:
                status = getattr(j, "status", "")
                created = getattr(j, "created", "") or ""
                elapsed = 0.0
                try:
                    elapsed = max(0.0, (datetime.now() -
                                        datetime.fromisoformat(created)).total_seconds())
                except Exception:
                    pass
                agent = agent_of(j)
                label = (getattr(j, "ctx", {}) or {}).get("input") if hasattr(j, "ctx") else None
                label = (label or getattr(j, "url", "") or agent.lower())
                row = {"id": getattr(j, "id", ""), "name": clamp(str(label), 28) or agent.lower(),
                       "agent": agent, "elapsed": round(elapsed, 1), "status": status,
                       "events": len(getattr(j, "events", []) or [])}
                if status == "running":
                    running.append(row)
                elif status == "queued":
                    queued.append(row)
                # collect this job's recent events for the log stream
                for ev in (getattr(j, "events", []) or [])[-4:]:
                    events.append({"ts": ev.get("ts", ""), "agent": agent,
                                   "stage": ev.get("stage", ""), "detail": ev.get("detail", "")})

        try:
            from .jobs import manager as sq
            note(sq, lambda j: (getattr(j, "unit", "") or "squad").upper())
        except Exception:
            pass

        # newest events first for the log stream
        events.sort(key=lambda e: e.get("ts", ""), reverse=True)
        logs = []
        for e in events[:14]:
            stage = (e.get("stage") or "").lower()
            level = "ERROR" if stage == "error" else ("WARN" if stage in ("heartbeat",) else "INFO")
            t = (e.get("ts", "") or "")[-8:]
            msg = f"{e['agent'].lower()}: {stage}" + (f" — {e['detail']}" if e.get("detail") else "")
            logs.append({"t": t, "level": level, "msg": clamp(msg, 90)})
        return running, queued, logs

    # -- orchestrator ---------------------------------------------------------
    def run(self, ctx, emit) -> UnitResult:
        res = UnitResult()

        # 1) Collect.
        emit("collect", "Collector gathering telemetry")
        ollama_up, models, missing = False, [], []
        needed = []
        try:
            import ollama
            from ..config import AGENTS
            listed = ollama.list()
            raw = getattr(listed, "models", None) or (listed.get("models", []) if isinstance(listed, dict) else [])
            for m in raw:
                nm = getattr(m, "model", None) or (m.get("model") or m.get("name") if isinstance(m, dict) else None)
                if nm:
                    models.append(nm)
            ollama_up = True
            have = set(models)
            needed = sorted({s["model"] for s in AGENTS.values()})
            missing = [n for n in needed if n not in have and (n + ":latest") not in have]
        except Exception as e:
            emit("collect", f"Ollama unreachable: {e}")

        # Real latency + throughput benchmark (cached briefly across refreshes).
        # We only *probe* models that are already resident in memory (ollama.ps)
        # so the benchmark is real and fast without force-loading cold 7B models
        # and stalling a health check. Pulled-but-cold models are shown as IDLE;
        # models needed by the core agents but not pulled are shown as MISSING.
        latency = []
        if ollama_up and models:
            now = time.time()
            if now - _PROBE_CACHE["ts"] < _PROBE_TTL and _PROBE_CACHE["latency"]:
                latency = _PROBE_CACHE["latency"]
            else:
                have = set(models)
                loaded = self._loaded_models()
                bench = [n for n in (needed or [])
                         if n in have or (n + ":latest") in have]
                # include any other resident models not in the core set
                for n in loaded:
                    if n not in bench:
                        bench.append(n)
                bench = bench[:8]
                to_probe = [n for n in bench
                            if n in loaded or (n.split(":")[0] in {l.split(":")[0] for l in loaded})]
                probed = {}
                if to_probe:
                    with ThreadPoolExecutor(max_workers=min(4, len(to_probe))) as ex:
                        futs = {ex.submit(_probe_model, n): n for n in to_probe}
                        try:
                            for f in as_completed(futs, timeout=15):
                                r = f.result()
                                probed[r["name"]] = r
                        except Exception:
                            pass
                        for fut, nm in futs.items():
                            if nm not in probed:
                                fut.cancel()
                for n in bench:
                    if n in probed:
                        latency.append(probed[n])
                    else:
                        # resident-but-slow → treat as off; cold → idle
                        latency.append({"name": n, "ms": 0, "tps": 0.0,
                                        "off": False, "cold": True})
                latency.sort(key=lambda m: (m.get("cold", False), m["off"], m["ms"]))
                _PROBE_CACHE["ts"] = now
                _PROBE_CACHE["latency"] = latency

        # Job history counts (persisted) + live jobs (in-memory).
        squad_jobs = 0
        try:
            from .jobs import manager as sq
            squad_jobs = len(sq.list_jobs())
        except Exception:
            pass
        running, queued, logs = self._live_jobs()

        # Cache + memory.
        facts = reminders = cache_entries = 0
        try:
            from ..persistent_memory import shared
            mem = shared()
            facts = len(mem.get_facts())
            reminders = len(mem.all_reminders())
        except Exception:
            pass
        try:
            from ..knowledge_cache import KnowledgeCache
            kstats = KnowledgeCache().stats()
            cache_entries = kstats.get("entries", 0) if isinstance(kstats, dict) else 0
        except Exception:
            pass

        # Cloud backend probe. OMNIX ships cloud-first, so on the machines it
        # targets Ollama is usually NOT running — judging health by Ollama alone
        # told a perfectly healthy user they were "offline". The active backend
        # is what health must reflect.
        cloud_up, cloud_models, cloud_ms = False, [], 0
        cloud_first = False
        try:
            from .. import nvidia_client
            from ..config import WARM_MODELS, local_only
            cloud_first = (not local_only()) and nvidia_client.available()
            if cloud_first:
                cloud_models = list(WARM_MODELS)
                t0 = time.time()
                cloud_up = nvidia_client.warm(cloud_models[0], timeout=15.0)
                cloud_ms = int((time.time() - t0) * 1000)
        except Exception as e:
            emit("collect", f"cloud probe failed: {type(e).__name__}")

        # 2) Analyze — derive real aggregates.
        emit("analyze", "Analyzer computing health signals")
        if cloud_first:
            # Local models are optional here, so a missing one is not a fault.
            health = "healthy" if cloud_up else "offline"
            backend = "cloud"
        else:
            health = "healthy" if (ollama_up and not missing) else \
                     ("degraded" if ollama_up else "offline")
            backend = "local"
        live_probes = [m for m in latency if not m["off"] and not m.get("cold")]
        avg_latency = int(sum(m["ms"] for m in live_probes) / len(live_probes)) if live_probes else 0
        tok_per_min = int(sum(m["tps"] for m in live_probes) * 60)
        # jobs-per-minute: real jobs started in the last 60s across managers
        jpm = self._jobs_last_minute()

        # append a real sample to the rolling series
        sample = {"t": datetime.now().strftime("%H:%M:%S"),
                  "tokens": tok_per_min, "rpm": jpm, "latency": avg_latency}
        _SAMPLES.append(sample)
        del _SAMPLES[:-_MAX_SAMPLES]
        series = {
            "tokens": [s["tokens"] for s in _SAMPLES],
            "rpm": [s["rpm"] for s in _SAMPLES],
            "latency": [s["latency"] for s in _SAMPLES],
            "t": [s["t"] for s in _SAMPLES],
        }

        # 3) Advise.
        emit("advise", "Advisor drafting guidance")
        telemetry = (f"backend={backend}; cloud_up={cloud_up}; "
                     f"cloud_models={len(cloud_models)}; cloud_probe_ms={cloud_ms}; "
                     f"ollama_up={ollama_up}; models={len(models)}; "
                     f"missing={missing if not cloud_first else 'n/a (cloud)'}; "
                                          f"squad_jobs={squad_jobs}; running={len(running)}; "
                     f"queued={len(queued)}; facts={facts}; reminders={reminders}; "
                     f"cache_entries={cache_entries}; avg_latency_ms={avg_latency}")
        advice = self.subagents[2].complete(
            f"OMNIX telemetry:\n{telemetry}\n\nHealth: {health}. Advise.")

        # Deterministic advisor + suggestions (offline backbone).
        det_advice, suggestions = self._advice(
            health, len(models), missing, avg_latency, len(running),
            backend=backend, cloud_ms=cloud_ms, n_cloud=len(cloud_models))
        summary = (advice or det_advice).strip()
        res.summary = clamp(summary, 1500)

        # -- classic blocks (generic console + other consumers) ---------------
        res.add(stats_block([
            {"n": health.upper(), "label": "Status"},
            {"n": backend.upper(), "label": "Backend"},
            {"n": str(len(cloud_models) if cloud_first else len(models)), "label": "Models"},
            {"n": str(len(running)), "label": "Running"},
            {"n": str(len(queued)), "label": "Queued"},
            {"n": str(facts), "label": "Facts"},
            {"n": str(reminders), "label": "Reminders"},
        ]))
        if cloud_first:
            res.add(cards_block("Backend", [{
                "title": "NVIDIA cloud", "badge": "up" if cloud_up else "down",
                "badge_color": "#4ade80" if cloud_up else "#ff5d7a",
                "body": (f"{len(cloud_models)} models kept warm · probe {cloud_ms} ms."
                         if cloud_up else "Cloud endpoint not responding.")}, {
                "title": "Local Ollama (optional)",
                "badge": "up" if ollama_up else "not running",
                "badge_color": "#4ade80" if ollama_up else "#8a8171",
                "body": (f"{len(models)} models resident." if ollama_up
                         else "Not required — OMNIX is running on cloud models.")}]))
            res.add(list_block("Cloud models in rotation", cloud_models))
        else:
            res.add(cards_block("Ollama", [{
                "title": "Ollama service", "badge": "up" if ollama_up else "down",
                "badge_color": "#4ade80" if ollama_up else "#ff5d7a",
                "body": f"{len(models)} models resident · avg {avg_latency} ms."
                        if ollama_up else "Not reachable at 127.0.0.1:11434."}]))
            if models:
                res.add(list_block("Pulled models", sorted(models)[:20]))
            if missing:
                res.add(list_block("Missing (needed by core agents)", missing))

        # -- rich meta (PULSE's purpose-built console) ------------------------
        res.meta = {
            "health": health,
            "backend": backend,
            "cloud_up": cloud_up,
            "cloud_models": cloud_models,
            "cloud_probe_ms": cloud_ms,
            "ollama_up": ollama_up,
            "models": sorted(models),
            "missing": missing,
            "used_llm": bool(advice),
            "advisor": summary,
            "suggestions": suggestions,
            "stats": {"models": len(models),
                      "squad": squad_jobs, "facts": facts,
                      "reminders": reminders, "cache": cache_entries},
            "latency": latency,
            "series": series,
            "tok_per_min": tok_per_min,
            "jobs_per_min": jpm,
            "avg_latency": avg_latency,
            "jobs": running,
            "queue": queued,
            "logs": logs,
            "timestamp": datetime.now().strftime("%H:%M:%S"),
        }
        return res

    def _jobs_last_minute(self) -> int:
        cutoff = time.time() - 60
        count = 0
        for getter in (self._squad_jobs_iter,):
            for created in getter():
                try:
                    if datetime.fromisoformat(created).timestamp() >= cutoff:
                        count += 1
                except Exception:
                    continue
        return count

    def _squad_jobs_iter(self):
        try:
            from .jobs import manager as sq
            return [getattr(j, "created", "") for j in getattr(sq, "_jobs", {}).values()]
        except Exception:
            return []

    def _advice(self, health, n_models, missing, avg_latency, running,
                backend="local", cloud_ms=0, n_cloud=0):
        # Cloud-first machines usually have no Ollama at all — telling that user
        # to "run ollama serve" would be advice for a problem they don't have.
        if backend == "cloud":
            if health == "healthy":
                adv = (f"All systems nominal. The NVIDIA cloud backend is "
                       f"responsive ({cloud_ms} ms probe) with {n_cloud} model(s) "
                       f"kept warm, and {running} job(s) processing normally.")
                sug = [{"n": "1", "text": "Warm the knowledge cache with trending topics to speed up web-grounded research."},
                       {"n": "2", "text": "No local GPU needed — OMNIX is running entirely on cloud models."}]
            else:
                adv = ("The NVIDIA cloud backend is not responding. Check network "
                       "connectivity and that the API key is still valid; OMNIX "
                       "falls back to local models only if you enable local mode.")
                sug = [{"n": "1", "text": "Verify connectivity, then re-run PULSE."},
                       {"n": "2", "text": "Run `python -m omnix.nvidia_client` to check the key."},
                       {"n": "3", "text": "Offline? Set OMNIX_LOCAL_ONLY=1 to use local models."}]
            return adv, sug

        if health == "healthy":
            adv = (f"All systems nominal. Ollama is responsive with {n_models} "
                   f"models resident" +
                   (f" (avg {avg_latency} ms/probe)" if avg_latency else "") +
                   f" and {running} job(s) processing within normal envelopes.")
            sug = [{"n": "1", "text": "Warm the knowledge cache with trending topics to speed up web-grounded research."},
                   {"n": "2", "text": "Pre-load heavier models before peak hours to cut cold-start latency."}]
        elif health == "degraded":
            miss = ", ".join(missing) or "a required model"
            adv = (f"Ollama is online but {len(missing)} model(s) needed by the core "
                   f"agents are not resident ({miss}). Pull the missing model(s) to "
                   "restore full capability.")
            sug = [{"n": "1", "text": f"Run: ollama pull {missing[0]}" if missing else "Pull the missing model."},
                   {"n": "2", "text": "Re-run PULSE after the pull completes to confirm HEALTHY status."}]
        else:
            adv = ("Ollama is unreachable at 127.0.0.1:11434. All squad agents are "
                   "without a backend. Start the service and re-run PULSE to regain access.")
            sug = [{"n": "1", "text": "Start Ollama: run `ollama serve` (or start the Ollama app)."},
                   {"n": "2", "text": "Verify port 11434 is not blocked by a firewall."}]
        return adv, sug
