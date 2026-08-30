"""OMNIX web server — exposes the local multi-agent backend over HTTP/SSE.

Wraps the same agents, router, voice (STT/TTS) and web-search that the CLI uses,
and serves the React Intelligence Workspace built into omnix/webapp.

Run:  python -m omnix.server      (or use omnix.ps1 which does this)
"""

import base64
import io
import json
import tempfile
import threading
import wave
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, Form, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (
    StreamingResponse, JSONResponse, FileResponse, RedirectResponse, Response,
)
from fastapi.staticfiles import StaticFiles

from .agents import AGENT_CLASSES
from .agents.vision import extract_image_path  # noqa: F401  (kept for parity)
from .config import AGENTS
from .router import classify, VALID_AGENTS

WEB_DIR = Path(__file__).resolve().parent / "web"


@asynccontextmanager
async def _lifespan(app):
    # Platform database first: workspaces, artifacts, executions and the model
    # ledger all depend on it, and an agent run that starts before the tables
    # exist would lose its history silently.
    try:
        from .core import db as _db
        _db.init_db()
    except Exception as e:
        print(f"[omnix] platform database unavailable: {type(e).__name__}: {e}")
    # No execution survives the process that ran it, so anything still marked
    # running at boot was abandoned by a restart. Close those out before the
    # agent view can present them as live workers.
    try:
        from .core import executions as _executions
        _executions.reconcile_orphans()
    except Exception as e:
        print(f"[omnix] could not reconcile interrupted runs: "
              f"{type(e).__name__}: {e}")
    # Start the background news updater + the agent knowledge sync on boot, stop
    # them on shutdown. All are module-level singletons defined below.
    _updater.start()
    if _knowledge_sync is not None:
        _knowledge_sync.start()
    # TERRA's intelligence pipeline. Its own thread, guarded, and gated by env:
    # OMNIX_TERRA=off disables it entirely (the map and news tabs keep working —
    # only the graph/heatmap/agent layers go quiet), OMNIX_TERRA_INTERVAL sets
    # the refresh cadence in minutes.
    try:
        import os as _os
        if _os.environ.get("OMNIX_TERRA", "on").lower() not in ("0", "off", "false", "no"):
            from .terra import service as _terra
            try:
                _interval = int(_os.environ.get("OMNIX_TERRA_INTERVAL", "15"))
            except ValueError:
                _interval = 15
            _terra.shared().start(interval_minutes=_interval)
    except Exception:
        pass
    # Intent monitoring (§11). Its own thread, and gated the same way TERRA is:
    # OMNIX_INTENTS=off stops the sweep without touching the API, so Intents
    # can still be created and checked by hand.
    try:
        import os as _os
        if _os.environ.get("OMNIX_INTENTS", "on").lower() not in ("0", "off",
                                                                  "false", "no"):
            from .core import intents as _intents
            _intents.shared().start()
    except Exception as e:
        print(f"[omnix] intent scheduler unavailable: {type(e).__name__}: {e}")
    # Hold the hot cloud models resident. Cold start is the biggest latency risk
    # on the free tier (same model: ~0.5s warm vs 20s+ cold), so a cheap 1-token
    # ping every few minutes is what makes the app feel instant to a user who
    # opens it once an hour.
    try:
        from . import cloud, model_catalog, nvidia_client
        from .config import WARM_MODELS, local_only
        if not local_only() and nvidia_client.available():
            # The roster's own warm list is merged in. The MoE instances in
            # particular need it: `nemotron-3-super-120b-a12b` answered HTTP 502
            # cold and then 5/5 at 1.6-3.3s warm — without the keeper it looks
            # broken, with it it is the best model on the tier.
            cloud.start_keeper(
                list(dict.fromkeys([*WARM_MODELS, *model_catalog.WARM])))
    except Exception:
        pass
    # Load the Piper voice off the request path. Measured: the first synthesis
    # costs ~8.9s because it loads the ONNX model, and every one after it is
    # ~0.4s. Paying that on a background thread at boot means the first spoken
    # answer starts in under a second like all the others, instead of appearing
    # to hang. Daemon + silent: a machine without the voice extra installed must
    # still start normally, and this is the one place that would otherwise
    # surface the missing dependency as a scary traceback.
    def _warm_voice() -> None:
        try:
            from .voice import tts as _tts
            _tts.synthesize("ready")
        except Exception:
            pass

    try:
        threading.Thread(target=_warm_voice, name="omx-tts-warm",
                         daemon=True).start()
    except Exception:
        pass
    try:
        yield
    finally:
        _updater.stop()
        if _knowledge_sync is not None:
            _knowledge_sync.stop()
        try:
            from .terra import service as _terra
            _terra.shared().stop()
        except Exception:
            pass
        try:
            from .core import intents as _intents
            _intents.shared().stop()
        except Exception:
            pass
        try:
            from . import cloud
            cloud.stop_keeper()
        except Exception:
            pass


app = FastAPI(title="OMNIX", lifespan=_lifespan)

# Allow the frontend to call the API even when it's opened from a different
# origin (e.g. file:// or a different port).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# API responses are live state — executions change status, usage accumulates,
# artifacts appear. Browsers will otherwise heuristically cache these GETs and
# serve a stale execution or a stale timestamp indefinitely (which is exactly
# how a fixed timezone bug appeared to persist in testing).
@app.middleware("http")
async def _no_store_api(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------
# Port 8000 on loopback is open to every process and every web page on the
# machine, so "it only listens on 127.0.0.1" is not access control. This closes
# every API route behind a session cookie.
#
# Three things are deliberately still public, and each for a reason that would
# otherwise break the sign-in itself:
#
#   /api/auth/*   the gate cannot require the credential it exists to issue.
#   /api/health   a liveness probe that needs a login is not a liveness probe.
#   the SPA       HTML, JS, CSS and fonts must load so React can *draw* the
#                 login screen. Nothing in the bundle is user data; the data
#                 lives behind /api/*, which is shut.
#
# `OMNIX_AUTH=off` disables the gate for development. It is on by default,
# because a security control whose default is "off" is a comment.
from . import auth as _auth  # noqa: E402
from .api.auth import COOKIE as _SESSION_COOKIE  # noqa: E402
from .core import identity as _identity  # noqa: E402
from .core import ratelimit as _ratelimit  # noqa: E402
from .api import errors as _api_errors  # noqa: E402

_PUBLIC_API = ("/api/auth/", "/api/health")


@app.middleware("http")
async def _require_session(request, call_next):
    """Authenticate, then *bind* the identity for the rest of the request.

    The gate used to stop at "is there a valid session". That proved who you
    were and then threw the answer away, leaving the data layer to ask
    `default_user()` — which is why every signed-in account saw the same
    Spaces. The session user is now published on a context variable that
    :func:`omnix.core.workspace.resolve` reads, so ownership is enforced
    without threading a `Request` through sixty route signatures.

    The token is always reset in a `finally`: these run on a shared worker
    task, and a leaked value would be inherited by whoever ran next.
    """
    path = request.url.path
    user = None

    if path.startswith("/api/"):
        # Identify first, because an account is a better rate-limit key than an
        # address — several people behind one NAT share an address, and one
        # account moving between networks does not.
        if not path.startswith(_PUBLIC_API):
            user = _auth.session_user(request.cookies.get(_SESSION_COOKIE))

        # Rate limit BEFORE the auth check, not after. Limiting only
        # authenticated traffic leaves the 401 path itself unmetered, so an
        # attacker with no credentials at all is the one caller who can hammer
        # the server freely — and `/api/auth/login` is public, meaning
        # credential stuffing would be throttled per-email by auth.py but never
        # per-address.
        who = (user or {}).get("email") or (
            request.client.host if request.client else "anonymous")
        kind = _ratelimit.bucket_for(path, request.method)
        allowed, retry_after = _ratelimit.shared().check(who, kind)
        if not allowed:
            return JSONResponse(
                {"error": "Too many requests. Slow down and try again shortly.",
                 "rateLimited": True, "bucket": kind,
                 "retryAfterSeconds": round(retry_after, 1)},
                status_code=429,
                headers={"Retry-After": str(max(1, int(retry_after + 0.5)))})

        if (_auth.enabled() and user is None
                and not path.startswith(_PUBLIC_API)):
            # JSON, not a redirect: these are fetches, and a 302 to an HTML
            # page surfaces in the client as an unreadable parse error rather
            # than as "you are signed out".
            return JSONResponse(
                {"error": "Not signed in.", "authRequired": True},
                status_code=401)

    token = _identity.set_current_email(user["email"] if user else None)
    try:
        return await call_next(request)
    finally:
        _identity.reset_current_email(token)


_api_errors.install(app)


# Platform routes: workspaces, artifacts, executions, events, usage. Mounted
# before the legacy agent routes so the new surface is authoritative; the old
# /api/squad/* routes stay until their agents are rewritten.
from .api.platform import router as _platform_router  # noqa: E402
from .api.objects import router as _objects_router  # noqa: E402
from .api.nova import router as _nova_router  # noqa: E402
from .api.outputs import router as _outputs_router  # noqa: E402
from .api.agents import router as _agents_router  # noqa: E402
from .api.auth import router as _auth_router  # noqa: E402
from .api.models import router as _models_router  # noqa: E402
from .api.plugins import router as _plugins_router  # noqa: E402
from .api.helix import router as _helix_router  # noqa: E402
from .terra.geo.routes import router as _terra_geo_router  # noqa: E402

# Accounts and sessions. First, so nothing can shadow the routes the gate
# above lets through.
app.include_router(_auth_router)
app.include_router(_platform_router)
# Intelligence graph: objects, relationships, events, traversal, ontology.
# Mounted after the platform router; the two prefixes do not overlap.
app.include_router(_objects_router)
# NOVA command layer, research ingestion, claim ledger, intelligence brief.
app.include_router(_nova_router)
# Create-as-output-action (§12) and persistent Intents (§11).
app.include_router(_outputs_router)
# Agents as visible, inspectable, interruptible workers (§9).
app.include_router(_agents_router)
# TERRA's geospatial intelligence layer: /api/terra/geo/*. Additive — the
# existing /api/geo/search, /api/geo/reverse and /api/weather below still serve
# the world map and are untouched.
app.include_router(_terra_geo_router)
# The user-facing model roster and its on/off toggles (/api/roster/*). NOT
# /api/models — that path is taken by the live per-subsystem assignment report
# below, and mounting here shadowed it.
app.include_router(_models_router)
# The plugin manager and the tool bus (docs/OMNIX_PLUGIN_ARCHITECTURE.md).
app.include_router(_plugins_router)
# HELIX: the bioinformatics corpus and its grounded answer layer. Its index is
# built lazily on the first question, so mounting this costs nothing at boot.
app.include_router(_helix_router)

# One agent instance per type, reused across requests (models stay warm).
_agents = {name: cls() for name, cls in AGENT_CLASSES.items()}

# ---------------------------------------------------------------------------
# Intelligence services: news cache + background updater + persistent memory.
# These power the Intelligence panel (news / weather / reminders) and degrade
# gracefully if anything is unavailable.
# ---------------------------------------------------------------------------
import os

from .background_updater import BackgroundUpdater
from .knowledge_cache import KnowledgeCache
from .persistent_memory import shared as _shared_memory
from .agent_knowledge import shared_kb as _shared_kb, KnowledgeSync

_cache = KnowledgeCache()
_memory = _shared_memory()  # process-wide singleton (shared with ATLAS)
_updater = BackgroundUpdater(_cache, interval_minutes=30)

# Shared internet-knowledge base + background sync for the whole agent squad.
# Every unit and subagent reads from this cache (cache-first, offline-safe).
# Gated by env: OMNIX_SYNC=off disables the daemon; OMNIX_SYNC_INTERVAL sets the
# refresh cadence in minutes (default 20).
_knowledge = _shared_kb()
if os.environ.get("OMNIX_SYNC", "on").lower() not in ("0", "off", "false", "no"):
    try:
        _sync_interval = int(os.environ.get("OMNIX_SYNC_INTERVAL", "20"))
    except ValueError:
        _sync_interval = 20
    _knowledge_sync = KnowledgeSync(_knowledge, interval_minutes=_sync_interval)
else:
    _knowledge_sync = None


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------
@app.get("/api/models")
def models():
    """Which model every part of OMNIX is running on RIGHT NOW.

    One endpoint so the whole UI agrees — the agent picker, NEXUS cards, the
    consoles and their subagent rosters all read from here instead of each
    hard-coding a name that goes stale the moment the ladders change. `agents`
    keeps its original list shape for existing callers, but the model reported
    is the live one (cloud ladder top rung, or the local model when offline).
    """
    from .config import (active_agent_model, active_tier_model,
                         cloud_active)

    cloud = cloud_active()

    out = []
    for name, spec in AGENTS.items():
        info = active_agent_model(name)
        out.append({
            "id": name,
            "model": info["model"],            # live, display form
            "full": info["full"],
            "backend": info["backend"],
            "alternates": info["alternates"],  # the hedge rungs behind it
            "local_model": spec["model"],      # what offline mode would use
            "label": spec.get("label", name),
        })

    tiers = {t: active_tier_model(t) for t in ("smart", "fast", "code", "vision")}

    units = {}
    try:
        from .squad import units as _su
        units = {u["code"]: {"models": u.get("models", []),
                             "backend": u.get("backend"),
                             "subagents": u.get("subagents", [])}
                 for u in _su.catalog()}
    except Exception:
        pass

    return {
        "backend": "cloud" if cloud else "local",
        "agents": out,
        "tiers": tiers,
        "units": units,
        "router": active_tier_model("fast"),
    }


def _model_names(data):
    out = []
    models = getattr(data, "models", None)
    if models is None and isinstance(data, dict):
        models = data.get("models", [])
    for m in models or []:
        if isinstance(m, dict):
            name = m.get("model") or m.get("name")
        else:
            name = getattr(m, "model", None) or getattr(m, "name", None)
        if name:
            out.append(name)
    return out


@app.get("/api/health")
def health():
    """Reports whether the web server is up, Ollama is reachable, and which of
    the agent models are pulled. Handy for diagnosing setup issues.

    The Ollama probe is SKIPPED when the cloud ladders are the live path. It
    costs ~2.2s to fail when Ollama isn't running, which is the normal state for
    a cloud-first install — and omnix.ps1 polls this endpoint to decide when to
    open the browser, so that delay meant the browser never opened at all.
    """
    from .config import cloud_active

    status = {"server": "ok", "ollama": False, "models": [], "missing": []}

    if cloud_active():
        status["backend"] = "cloud"
        status["ollama_checked"] = False
        return status

    status["backend"] = "local"
    status["ollama_checked"] = True
    try:
        import ollama

        have = _model_names(ollama.list())
        status["ollama"] = True
        status["models"] = have
        have_set = set(have)
        needed = sorted({spec["model"] for spec in AGENTS.values()})
        status["missing"] = [
            n for n in needed if n not in have_set and (n + ":latest") not in have_set
        ]
    except Exception as e:
        status["error"] = str(e)
    return status


# ---------------------------------------------------------------------------
# Chat (SSE streaming)
# ---------------------------------------------------------------------------
@app.post("/api/chat")
async def chat(payload: dict):
    """Body: { messages, forced_agent:str|null, forced_model:str|null,
               image:dataURL|null }

    `forced_agent` picks the SPECIALTY (chat / coding / research / …).
    `forced_model` picks a specific model from the roster in
    `omnix/model_catalog.py`. They are independent: choosing a model does not
    change which agent's prompt and tools run, so "Research + Llama 70B" still
    searches the web, and "Research + Nemotron Vision" is simply refused by the
    catalogue rather than silently answering without eyes.

    Streams Server-Sent Events:
      event meta   -> { agent, model, ambiguous, researching }
      event delta  -> { text }
      event error  -> { message }
      event done   -> { agent }
    """
    messages = payload.get("messages", [])
    forced = payload.get("forced_agent")
    forced_model = payload.get("forced_model")
    image_data = payload.get("image")

    if not messages:
        return JSONResponse({"error": "no messages"}, status_code=400)

    user_text = messages[-1].get("content", "")
    history = [
        {"role": m["role"], "content": m["content"]}
        for m in messages[:-1]
        if m.get("role") in ("user", "assistant") and m.get("content")
    ]

    # Save an inline image (data URL) to a temp file for the vision agent.
    image_path = None
    if image_data and image_data.startswith("data:"):
        try:
            header, b64 = image_data.split(",", 1)
            ext = "png"
            if "jpeg" in header or "jpg" in header:
                ext = "jpg"
            elif "webp" in header:
                ext = "webp"
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix="." + ext)
            tmp.write(base64.b64decode(b64))
            tmp.close()
            image_path = tmp.name
        except Exception:
            image_path = None

    # Decide which agent handles this turn.
    #
    # An attached image forces the vision agent regardless of what was picked:
    # no text model can answer a question about a picture, so honouring a
    # contrary choice would mean confidently describing an image it never saw.
    if image_path:
        agent_name = "vision"
    elif forced in VALID_AGENTS:
        agent_name = forced
    else:
        agent_name = classify(user_text)

    ambiguous = forced not in VALID_AGENTS and not image_path
    researching = agent_name == "research"
    agent = _agents[agent_name]
    model = AGENTS[agent_name]["model"]

    # Which models this turn may run. The catalogue applies the user's on/off
    # toggles and the "Auto" setting; an env pin (OMNIX_CHAT_MODEL) outranks the
    # request so a demo machine cannot be knocked off its chosen model by a
    # stale browser tab.
    ladder: list[str] | None = None
    try:
        from . import model_catalog
        pin = model_catalog.env_override()
        choice = pin or (forced_model if isinstance(forced_model, str) else None)
        # A vision turn ignores a text model choice for the reason above.
        if image_path and choice and model_catalog.BY_ID.get(choice, {}).get("role") != "vision":
            choice = None
        ladder = model_catalog.ladder_for_agent(agent_name, choice)
    except Exception as e:  # the catalogue must never be able to break chat
        print(f"[omnix] model catalogue unavailable, using default ladder: {e}")
        ladder = None
    # The FIRST meta event can only state the model we intend to use — the
    # ladder may hedge onto a different rung. Naming it as fact here is what
    # made the UI credit a cloud model for an answer a local one had written,
    # so this is explicitly the intended model and a second meta event follows
    # with whichever model actually produced the text.
    try:
        from . import nvidia_client
        from .config import CLOUD_LADDER, local_only, nvidia_enabled
        # The resolved ladder, when there is one — its first rung is the model
        # the user's choice actually selected. Falling back to CLOUD_LADDER here
        # would announce the default while running the chosen one.
        _rungs = ladder or CLOUD_LADDER.get(agent_name) or []
        if _rungs and nvidia_enabled() and not local_only() and nvidia_client.available():
            model = _rungs[0].split("/")[-1] + " · cloud"
    except Exception:
        pass

    def sse(event: str, data: dict) -> str:
        return f"event: {event}\ndata: {json.dumps(data)}\n\n"

    def gen():
        yield sse("meta", {
            "agent": agent_name,
            "model": model,
            "ambiguous": ambiguous,
            "researching": researching,
        })
        # The agent calls this the moment a model wins the race. Queued rather
        # than yielded directly because it fires inside the streaming callback.
        resolved: list[str] = []
        try:
            if agent_name == "vision":
                stream = agent.run(user_text, history, image_path=image_path,
                                   on_model=resolved.append, ladder=ladder)
            elif agent_name == "research":
                # Search once here so we can surface the sources to the UI, then
                # hand the same results to the agent (avoids a second search).
                # search_deep also fetches the top pages for richer grounding.
                from .tools.websearch import search_deep as _search

                results = _search(user_text)
                srcs = [
                    {"n": i + 1, "title": r.get("title", ""), "url": r.get("url", "")}
                    for i, r in enumerate(results)
                    if r.get("url")
                ]
                yield sse("sources", {"sources": srcs})
                stream = agent.run(user_text, history, results=results,
                                   on_model=resolved.append, ladder=ladder)
            else:
                stream = agent.run(user_text, history,
                                   on_model=resolved.append, ladder=ladder)
            for chunk in stream:
                if resolved:
                    real = resolved.pop(0)
                    if real != model:
                        yield sse("meta", {
                            "agent": agent_name,
                            "model": real,
                            "ambiguous": ambiguous,
                            "researching": researching,
                        })
                if chunk:
                    yield sse("delta", {"text": chunk})
        except Exception as e:  # every cloud rung failed, or Ollama is down
            yield sse("error", {"message": str(e)})
        finally:
            # The uploaded image was written with `delete=False`, because it has
            # to outlive this request's setup and be readable by the vision
            # model. Nothing deleted it afterwards, so every picture a user sent
            # stayed in the system temp directory for good — unbounded disk
            # growth driven by ordinary use. `finally`, so it is cleaned up when
            # the client disconnects mid-stream too, which is the common case
            # for a long vision answer.
            if image_path:
                try:
                    os.unlink(image_path)
                except OSError:
                    pass
        yield sse("done", {"agent": agent_name})

    return StreamingResponse(gen(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# Export: a model answer as a real document
# ---------------------------------------------------------------------------
@app.post("/api/export")
def export_document(payload: dict):
    """Body: { markdown, format: docx|pdf|html|md|txt, title?, subtitle? }

    Returns the file as an attachment. Rendered server-side rather than in the
    browser because DOCX and PDF need real writers, and shipping those to the
    client would add megabytes to a bundle for a button most sessions never
    press.
    """
    from .tools import docgen

    # Same reason `tts_endpoint` is sync: rendering DOCX/PDF blocks, and blocking
    # inside a coroutine blocks the whole server, not just this request.
    md = (payload.get("markdown") or "").strip()
    if not md:
        return JSONResponse({"error": "nothing to export"}, status_code=400)

    fmt = (payload.get("format") or "docx").lower().strip()
    if fmt not in docgen.FORMATS:
        return JSONResponse(
            {"error": f"format must be one of {', '.join(docgen.FORMATS)}"},
            status_code=400)

    title = (payload.get("title") or "OMNIX answer").strip()[:120]
    subtitle = (payload.get("subtitle") or "").strip()[:200]

    try:
        blob = docgen.render(md, fmt, title, subtitle)
    except Exception as e:
        # A malformed table or an exotic character should not 500 silently —
        # the UI shows this string to the user.
        return JSONResponse({"error": f"could not build the {fmt}: {e}"},
                            status_code=500)

    name = docgen.filename(title, fmt)
    return StreamingResponse(
        io.BytesIO(blob),
        media_type=docgen.MIME[fmt],
        headers={"Content-Disposition": f'attachment; filename="{name}"',
                 "Content-Length": str(len(blob))},
    )


# ---------------------------------------------------------------------------
# Text-to-speech (Piper) -> WAV
# ---------------------------------------------------------------------------
@app.get("/api/voice/status")
def voice_status():
    """Is the voice stack installed? Answered without synthesising anything.

    The frontend needs this to decide whether to show the mic and read-aloud
    controls. It used to find out by POSTing to `/api/tts` with the word "ok"
    and reading the status code — which meant every page load ran a real Piper
    synthesis, on a route that (until this was fixed) also blocked the event
    loop. A capability question should not cost the capability.

    Reports the two halves separately because they fail independently: piper
    and faster-whisper are separate installs, and the voice model files are a
    separate download from the packages that read them.
    """
    tts_ok = False
    tts_why = ""
    try:
        from .config import PIPER_VOICE, VOICE_MODELS_DIR
        import importlib.util

        if importlib.util.find_spec("piper") is None:
            tts_why = "piper is not installed (see requirements-voice.txt)"
        else:
            model = (Path(__file__).resolve().parent.parent
                     / VOICE_MODELS_DIR / f"{PIPER_VOICE}.onnx")
            if model.exists():
                tts_ok = True
            else:
                tts_why = f"voice model missing at {model.name}"
    except Exception as e:
        tts_why = f"{type(e).__name__}: {e}"

    stt_ok = False
    stt_why = ""
    try:
        import importlib.util

        if importlib.util.find_spec("faster_whisper") is None:
            stt_why = "faster-whisper is not installed (see requirements-voice.txt)"
        else:
            stt_ok = True
    except Exception as e:
        stt_why = f"{type(e).__name__}: {e}"

    return {
        "tts": tts_ok, "stt": stt_ok,
        "available": tts_ok or stt_ok,
        "detail": {"tts": tts_why, "stt": stt_why},
    }


@app.post("/api/tts")
def tts_endpoint(payload: dict):
    # `def`, not `async def`: synthesis is seconds of blocking CPU, and on the
    # event loop those seconds freeze every other request in the process.
    text = (payload.get("text") or "").strip()
    if not text:
        return JSONResponse({"error": "no text"}, status_code=400)
    try:
        # Imported here, not at module scope: voice is an optional extra
        # (requirements-voice.txt) and the server must start without it.
        import numpy as np
        from .voice import tts as tts_mod

        audio, sample_rate = tts_mod.synthesize(text)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

    pcm16 = np.clip(audio, -1.0, 1.0)
    pcm16 = (pcm16 * 32767.0).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm16.tobytes())
    buf.seek(0)
    return StreamingResponse(buf, media_type="audio/wav")


# ---------------------------------------------------------------------------
# Speech-to-text (faster-whisper). Expects 16kHz mono WAV from the browser.
# ---------------------------------------------------------------------------
@app.post("/api/stt")
async def stt_endpoint(audio: UploadFile = File(...)):
    raw = await audio.read()
    try:
        import numpy as np
        with wave.open(io.BytesIO(raw), "rb") as w:
            n = w.getnframes()
            frames = w.readframes(n)
            sw = w.getsampwidth()
            ch = w.getnchannels()
        if sw != 2:
            return JSONResponse({"error": "expected 16-bit PCM WAV"}, status_code=400)
        data = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
        if ch > 1:
            data = data.reshape(-1, ch).mean(axis=1)
    except Exception as e:
        return JSONResponse({"error": f"bad audio: {e}"}, status_code=400)

    try:
        from .voice import stt as stt_mod

        text = stt_mod.transcribe(data)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    return {"text": text}


# ---------------------------------------------------------------------------
# Intelligence panel: News
# ---------------------------------------------------------------------------
@app.get("/api/news")
def api_news(q: str | None = None, refresh: bool = False):
    """Categorized headlines. Serves the background-refreshed cache when fresh,
    otherwise fetches live; falls back to any cached copy on network failure."""
    from .tools import news as news_mod

    query = q or news_mod.DEFAULT_QUERY
    cache_key = "__sidebar__" if not q else f"news::{query}"

    if not refresh:
        found, data, age = _cache.get(cache_key, category="news", max_age_hours=0.5)
        if found and data:
            return {**data, "cached": True, "age_minutes": round(age * 60, 1)}

    result = news_mod.get_headlines(query)
    if result.get("status") == "success":
        _cache.set(cache_key, result, category="news", source="Google News RSS")
        return {**result, "cached": False}

    # Live fetch failed — serve stale cache if we have any.
    found, data, age = _cache.get(cache_key, category="news")
    if found and data:
        return {**data, "cached": True, "stale": True, "age_minutes": round(age * 60, 1)}
    return JSONResponse(result, status_code=502)


# ---------------------------------------------------------------------------
# Intelligence panel: Weather
# ---------------------------------------------------------------------------
@app.get("/api/weather")
def api_weather(city: str | None = None, lat: float | None = None, lon: float | None = None):
    from .tools import weather as weather_mod

    # Nothing to look up is the caller's mistake, not the provider's. Mapping it
    # to 502 told the Intelligence panel the weather service was down whenever a
    # request simply arrived without arguments, and put an upstream-failure entry
    # in the log for a request that never reached upstream.
    if city is None and lat is None and lon is None:
        return JSONResponse(
            {"status": "error", "error": "Provide a city or coordinates"},
            status_code=400)

    result = weather_mod.get_weather(city=city, lat=lat, lon=lon)
    if result.get("status") == "success":
        return JSONResponse(result, status_code=200)
    # A place the geocoder does not know is a 404: the request was well formed
    # and upstream answered, it just has no such city.
    unknown = str(result.get("error", "")).startswith("Could not find")
    return JSONResponse(result, status_code=404 if unknown else 502)


# ---------------------------------------------------------------------------
# TERRA map: geocoding (search + reverse) — powers click-to-inspect on the map.
# ---------------------------------------------------------------------------
@app.get("/api/geo/search")
def api_geo_search(q: str, count: int = 6):
    from .tools import geo as geo_mod

    result = geo_mod.search(q, count=count)
    return JSONResponse(result, status_code=200 if result.get("status") == "success" else 502)


@app.get("/api/geo/reverse")
def api_geo_reverse(lat: float, lon: float):
    from .tools import geo as geo_mod

    # Always 200: over open ocean the reverse lookup 'fails' but still returns
    # usable coordinates, and the map should show them rather than error out.
    return JSONResponse(geo_mod.reverse(lat, lon), status_code=200)


# ---------------------------------------------------------------------------
# TERRA INTELLIGENCE — the ontology/graph layer under the map.
#
# Everything here reads state the background refresher already computed, so a
# request never triggers a crawl. The four products that need tens of seconds of
# model time (six-analyst analysis, situation reports, what-if, deep-event) run
# as jobs with the same SSE contract AVALON and the squad already use, so the
# frontend's existing event handling works on them unchanged.
# ---------------------------------------------------------------------------
from .terra import service as terra_service


@app.get("/api/terra/status")
def terra_status():
    return terra_service.shared().status()


@app.get("/api/terra/overview")
def terra_overview():
    """One call for the console landing view: events, alerts, risk, graph."""
    return terra_service.shared().overview()


@app.post("/api/terra/refresh")
def terra_refresh(payload: dict | None = None):
    """Force an ingestion pass. `llm=false` skips relationship extraction, which
    is the slow part, for when the caller just wants fresh headlines."""
    use_llm = bool((payload or {}).get("llm", True))
    return terra_service.shared().refresh(use_llm_extraction=use_llm)


@app.get("/api/terra/events")
def terra_events(limit: int = 30, domain: str | None = None,
                 country: str | None = None):
    svc = terra_service.shared()
    events = svc.ranked
    if domain:
        events = [e for e in events if domain in (e.get("domains") or [])]
    if country:
        iso = country.upper()
        events = [e for e in events if iso in (e.get("countries") or [])]
    return {"events": events[:max(1, min(limit, 100))],
            "total": len(events), "domains": terra_service.extract.DOMAINS}


@app.get("/api/terra/events/{cluster_id}")
def terra_event(cluster_id: str, deep: bool = False):
    result = terra_service.shared().event(cluster_id, deep=deep)
    return JSONResponse(result,
                        status_code=200 if result.get("status") == "ok" else 404)


@app.get("/api/terra/heatmap")
def terra_heatmap():
    return terra_service.shared().heatmap()


@app.get("/api/terra/graph")
def terra_graph(node: str | None = None, hops: int = 1):
    result = terra_service.shared().graph_view(node, hops=max(1, min(hops, 3)))
    return JSONResponse(result,
                        status_code=200 if result.get("status") == "ok" else 404)


@app.get("/api/terra/graph/seed")
def terra_graph_seed(node: str | None = None, degree: int = 8):
    """Opening state of the explorer: one focus plus its best relationships.

    Progressive exploration starts here rather than with the whole graph — see
    TerraService.seed_view for why that is the point rather than a limitation.
    """
    return terra_service.shared().seed_view(node, max(3, min(degree, 24)))


@app.post("/api/terra/graph/expand")
def terra_graph_expand(payload: dict):
    """Body: { node, have:[ids], degree?, relations?[], types?[] }"""
    node = str((payload or {}).get("node") or "").strip()
    if not node:
        return JSONResponse({"error": "node is required"}, status_code=400)
    have = [str(x) for x in ((payload or {}).get("have") or [])][:600]
    degree = (payload or {}).get("degree") or 6
    try:
        degree = max(1, min(int(degree), 20))
    except (TypeError, ValueError):
        degree = 6
    relations = [str(r) for r in ((payload or {}).get("relations") or [])] or None
    types = [str(t) for t in ((payload or {}).get("types") or [])] or None
    result = terra_service.shared().expand_node(node, have, degree,
                                                relations, types)
    return JSONResponse(result,
                        status_code=200 if result.get("status") == "ok" else 404)


@app.get("/api/terra/graph/communities")
def terra_graph_communities(limit: int = 10):
    return terra_service.shared().communities_view(limit=max(1, min(limit, 30)))


@app.get("/api/terra/entity/{entity_id:path}")
def terra_entity(entity_id: str):
    """Unified entity intelligence — the right-hand panel, for any entity kind.

    Path is `:path` because entity ids contain a colon (`country:IN`), which a
    plain path parameter would not carry.
    """
    result = terra_service.shared().entity(entity_id)
    return JSONResponse(result,
                        status_code=200 if result.get("status") == "ok" else 404)


@app.get("/api/terra/timeline")
def terra_timeline(hours: float = 72.0, limit: int = 60,
                   node: str | None = None):
    return terra_service.shared().timeline_view(
        hours=max(6.0, min(hours, 240.0)), limit=max(5, min(limit, 200)),
        node_id=node)


@app.get("/api/terra/relationships")
def terra_relationships(relations: str | None = None, types: str | None = None,
                        limit: int = 120):
    rels = [r.strip() for r in (relations or "").split(",") if r.strip()] or None
    kinds = [t.strip() for t in (types or "").split(",") if t.strip()] or None
    return terra_service.shared().relationship_view(
        relations=rels, types=kinds, limit=max(10, min(limit, 400)))


@app.get("/api/terra/graph/search")
def terra_graph_search(q: str, limit: int = 12):
    return {"query": q,
            "results": terra_service.shared().graph.find(q, limit=limit)}


@app.get("/api/terra/graph/path")
def terra_graph_path(src: str, dst: str):
    """How two objects are connected — 'how does Apple reach the Taiwan Strait'."""
    kg = terra_service.shared().graph
    chain = kg.path_between(src, dst)
    return {"from": src, "to": dst, "hops": len(chain), "path": chain,
            "connected": bool(chain)}


@app.get("/api/terra/place")
def terra_place(lat: float | None = None, lon: float | None = None,
                name: str = "", iso: str = "", region: str = "",
                limit: int = 40, workspace: str | None = None,
                resolve: bool = True):
    """What is happening at one point on Earth — the map's location briefing.

    `lat`/`lon` come from a map click; `name`/`iso` from a search result the
    client has already resolved, which lets it skip the reverse-geocode round
    trip. One of the two must be present.
    """
    if lat is None and lon is None and not (name or iso):
        return JSONResponse(
            {"error": "give lat/lon, or a name/iso"}, status_code=400)
    from .terra import place as terra_place_mod
    return terra_place_mod.brief(
        terra_service.shared(), lat=lat, lon=lon, name=name, iso=iso,
        region=region, limit=limit, workspace_id=workspace, resolve=resolve)


@app.get("/api/terra/country/{iso}")
def terra_country(iso: str):
    result = terra_service.shared().country_card(iso)
    return JSONResponse(result,
                        status_code=200 if result.get("status") == "ok" else 404)


@app.get("/api/terra/layers")
def terra_layers(keys: str | None = None):
    want = [k.strip() for k in (keys or "").split(",") if k.strip()] or None
    return terra_service.shared().layers(want)


@app.get("/api/terra/search")
def terra_search(q: str, synthesize: bool = True):
    if not (q or "").strip():
        return JSONResponse({"error": "no query"}, status_code=400)
    return terra_service.shared().search(q.strip(), synthesize=synthesize)


@app.get("/api/terra/theatres")
def terra_theatres():
    from .terra import reports as terra_reports
    return {"theatres": [{"key": k, "name": v["name"], "glyph": v["glyph"],
                          "countries": v["countries"], "extent": v["extent"]}
                         for k, v in terra_reports.THEATRES.items()],
            "brief_formats": [{"key": k, **{kk: vv for kk, vv in v.items()
                                            if kk != "instruction"}}
                              for k, v in terra_reports.BRIEF_FORMATS.items()]}


@app.get("/api/terra/analysis")
def terra_analysis():
    """The last completed six-analyst pass, if any. Start one via /jobs."""
    svc = terra_service.shared()
    if svc.analysis is None:
        return {"status": "none",
                "hint": "POST /api/terra/jobs/analysis to generate one"}
    return {"status": "ok", **svc.analysis}


_TERRA_JOBS = {
    "analysis": lambda p: terra_service.start_analysis(),
    "situation": lambda p: terra_service.start_situation(p["theatre"]),
    "whatif": lambda p: terra_service.start_whatif(p["scenario"]),
    "deep": lambda p: terra_service.start_deep_event(p["cluster"]),
    "brief": lambda p: terra_service.start_brief(p["format"]),
}


@app.post("/api/terra/jobs/{kind}")
def terra_job_start(kind: str, payload: dict | None = None):
    """Body depends on kind: situation{theatre}, whatif{scenario},
    deep{cluster}, brief{format}; analysis takes none."""
    from .terra import reports as terra_reports

    starter = _TERRA_JOBS.get(kind)
    if starter is None:
        return JSONResponse(
            {"error": f"unknown job kind: {kind}",
             "available": sorted(_TERRA_JOBS)}, status_code=404)
    body = dict(payload or {})

    if kind == "situation":
        theatre = str(body.get("theatre") or "").strip()
        if theatre not in terra_reports.THEATRES:
            return JSONResponse(
                {"error": "unknown theatre",
                 "available": sorted(terra_reports.THEATRES)}, status_code=400)
        body["theatre"] = theatre
    elif kind == "whatif":
        scenario = str(body.get("scenario") or "").strip()
        if not scenario:
            return JSONResponse({"error": "scenario is required"}, status_code=400)
        body["scenario"] = scenario[:600]
    elif kind == "deep":
        cluster = str(body.get("cluster") or "").strip()
        if cluster not in terra_service.shared().clusters:
            return JSONResponse({"error": "unknown event"}, status_code=404)
        body["cluster"] = cluster
    elif kind == "brief":
        fmt = str(body.get("format") or "standard").strip()
        if fmt not in terra_reports.BRIEF_FORMATS:
            return JSONResponse(
                {"error": "unknown brief format",
                 "available": sorted(terra_reports.BRIEF_FORMATS)},
                status_code=400)
        body["format"] = fmt

    return starter(body).public()


@app.get("/api/terra/jobs")
def terra_jobs():
    return {"jobs": terra_service.manager.list_jobs()}


@app.get("/api/terra/jobs/{job_id}")
def terra_job(job_id: str):
    job = terra_service.manager.get(job_id)
    if job is None:
        return JSONResponse({"error": "unknown job"}, status_code=404)
    return job.public(with_result=True)


@app.get("/api/terra/jobs/{job_id}/events")
def terra_job_events(job_id: str):
    """SSE stream: replays past progress events, then follows live ones."""
    return StreamingResponse(terra_service.manager.stream(job_id),
                             media_type="text/event-stream")


# ---------------------------------------------------------------------------
# Intelligence panel: Persistent memory (facts + reminders)
# ---------------------------------------------------------------------------
@app.get("/api/facts")
def api_get_facts():
    return {"facts": _memory.get_facts()}


@app.post("/api/facts")
def api_add_fact(payload: dict):
    text = (payload.get("text") or "").strip()
    if not text:
        return JSONResponse({"error": "no text"}, status_code=400)
    return {"fact": _memory.add_fact(text)}


@app.delete("/api/facts/{index}")
def api_delete_fact(index: int):
    return {"ok": _memory.delete_fact(index)}


@app.get("/api/reminders")
def api_get_reminders():
    # Surface any that came due so the UI can announce them.
    fired = _memory.due_reminders()
    return {"reminders": _memory.all_reminders(), "fired": fired}


@app.post("/api/reminders")
def api_add_reminder(payload: dict):
    from .persistent_memory import parse_reminder

    text = (payload.get("text") or "").strip()
    if not text:
        return JSONResponse({"error": "no text"}, status_code=400)

    # Accept either an explicit {text, seconds} or a natural phrase to parse.
    seconds = payload.get("seconds")
    if seconds is None:
        parsed = parse_reminder(text)
        if parsed:
            task, seconds = parsed
            return {"reminder": _memory.add_reminder_in(task, int(seconds))}
        return JSONResponse(
            {"error": "Could not parse a time. Try 'remind me in 10 minutes to ...' "
                      "or provide seconds."},
            status_code=400,
        )
    return {"reminder": _memory.add_reminder_in(text, int(seconds))}


@app.post("/api/reminders/{rem_id}/complete")
def api_complete_reminder(rem_id: int):
    return {"ok": _memory.complete_reminder(rem_id)}


@app.delete("/api/reminders/{rem_id}")
def api_delete_reminder(rem_id: int):
    return {"ok": _memory.delete_reminder(rem_id)}



# ---------------------------------------------------------------------------
# NEXUS — the agent squad (NOVA, ORACLE, SENTINEL, FORGE, ATLAS, WARDEN, MUSE,
# PULSE). One generic job engine drives every unit; the console renders them
# from /api/squad/units metadata.
# ---------------------------------------------------------------------------
from .squad import units as squad_units
from .squad.jobs import manager as squad_manager


@app.get("/api/squad/units")
def squad_catalog():
    return {"units": squad_units.catalog()}


@app.post("/api/squad/{code}/run")
def squad_run(code: str, payload: dict):
    """Body: { input?: str, ...unit-specific options }"""
    unit = squad_units.get_unit(code)
    if unit is None:
        return JSONResponse({"error": f"unknown unit: {code}"}, status_code=404)
    ctx = dict(payload or {})
    ctx["input"] = (ctx.get("input") or "").strip()
    # Never trust a client-supplied local path — it would let a caller feed any
    # file on this machine to the vision model. Only the temp path we write
    # below (from an uploaded data URL) is allowed.
    ctx.pop("image_path", None)
    if unit.needs_input and not ctx["input"]:
        return JSONResponse({"error": "this unit requires input"}, status_code=400)

    # Optional inline image (data URL) for MUSE grounding -> temp file.
    image_data = ctx.pop("image", None)
    if image_data and isinstance(image_data, str) and image_data.startswith("data:"):
        try:
            header, b64 = image_data.split(",", 1)
            ext = "jpg" if ("jpeg" in header or "jpg" in header) else "png"
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix="." + ext)
            tmp.write(base64.b64decode(b64))
            tmp.close()
            ctx["image_path"] = tmp.name
        except Exception:
            pass

    job = squad_manager.start(unit, ctx)
    return job.public()


@app.get("/api/squad/jobs")
def squad_jobs(unit: str | None = None):
    return {"jobs": squad_manager.list_jobs(unit=unit)}


@app.get("/api/squad/jobs/{job_id}")
def squad_job(job_id: str):
    job = squad_manager.get(job_id)
    if job is None:
        return JSONResponse({"error": "unknown job"}, status_code=404)
    return job.public(with_result=True)


@app.get("/api/squad/jobs/{job_id}/events")
def squad_events(job_id: str):
    """SSE stream: replays past progress events, then follows live ones."""
    def gen():
        for ev in squad_manager.events(job_id):
            yield f"event: {ev.get('stage', 'progress')}\ndata: {json.dumps(ev)}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream")



# ---------------------------------------------------------------------------
# Intelligence panel: system status
# ---------------------------------------------------------------------------
@app.get("/api/system")
def api_system():
    return {
        "cache": _cache.stats(),
        "updater": _updater.status(),
        "knowledge": (_knowledge_sync.status() if _knowledge_sync else _knowledge.status()),
        "facts": len(_memory.get_facts()),
        "reminders": len(_memory.all_reminders()),
    }


# ---------------------------------------------------------------------------
# Shared internet knowledge — status, recall probe, and manual refresh. Every
# squad unit/subagent reads this cache (cache-first, offline-safe).
# ---------------------------------------------------------------------------
@app.get("/api/knowledge/status")
def api_knowledge_status():
    return _knowledge_sync.status() if _knowledge_sync else _knowledge.status()


@app.get("/api/knowledge/recall")
def api_knowledge_recall(q: str, k: int = 5):
    from .squad.base import knowledge_recall
    return {"query": q, "docs": knowledge_recall(q, k)}


@app.post("/api/knowledge/sync")
def api_knowledge_sync():
    """Trigger one synchronous refresh pass (also runs automatically in the
    background). Returns the resulting knowledge status."""
    if _knowledge_sync is None:
        return JSONResponse({"error": "sync is disabled (OMNIX_SYNC=off)"}, status_code=400)
    return _knowledge_sync.sync_now()


# ---------------------------------------------------------------------------
# Frontend
# ---------------------------------------------------------------------------
# The React Intelligence Workspace is the only UI. The 2.65MB single-file
# vanilla bundle that used to serve `/` was retired on 2026-08-04 once the
# workspace reached parity; see WORKSPACE.md for the checklist and for the
# handful of legacy-only surfaces (voice, reminders, weather, the standalone
# SENTINEL/ATLAS consoles) that were deliberately dropped with it. Their API
# routes are untouched, so any of them can come back as a workspace view.
#
# `WEB_DIR` survives the retirement because it is not the bundle: it is where
# cosmos.min.js and the world/states/cities gazetteers live, and the React
# graph and map both load them from /static.


# A 1x1 transparent PNG, used to satisfy favicon and any placeholder image
# requests so they don't 404.
_TRANSPARENT_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


@app.get("/favicon.ico")
def favicon():
    return Response(content=_TRANSPARENT_PNG, media_type="image/png")


if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


# ---------------------------------------------------------------------------
# Intelligence Workspace (React) — the app
# ---------------------------------------------------------------------------
WEBAPP_DIR = Path(__file__).resolve().parent / "webapp"

if WEBAPP_DIR.exists():
    app.mount("/assets",
              StaticFiles(directory=str(WEBAPP_DIR / "assets")),
              name="workspace-assets")


def _spa() -> Response:
    """The SPA shell, or an actionable 503 when the build is missing."""
    index = WEBAPP_DIR / "index.html"
    if not index.exists():
        return JSONResponse(
            {"error": "workspace build missing",
             "hint": "cd frontend && npm run build"}, status_code=503)
    resp = FileResponse(index)
    resp.headers["Cache-Control"] = "no-store, max-age=0"
    return resp


@app.get("/")
def index():
    return _spa()


@app.get("/workspace")
@app.get("/workspace/{path:path}")
def workspace_redirect(path: str = ""):
    """The workspace lived here while it shared the server with the old bundle.
    Kept as a permanent redirect so saved links and bookmarks still land."""
    return RedirectResponse(f"/{path}" if path else "/", status_code=308)


# Catch-all (declared LAST so it never shadows real routes). Now that a SPA
# serves `/`, an unmatched path is far more likely to be a client-side route
# on a hard refresh than a genuine 404, so it falls through to the shell and
# React decides. Two exceptions keep that from hiding real failures:
# `/api/*` must still 404 as JSON — returning HTML to a fetch turns a missing
# endpoint into an unreadable parse error — and a concrete file in the build
# (favicon, icons) is served as itself.
@app.get("/{path:path}")
def _catch_all(path: str):
    if path.startswith("api/"):
        return JSONResponse({"error": "not found"}, status_code=404)
    if WEBAPP_DIR.exists() and path:
        candidate = (WEBAPP_DIR / path).resolve()
        if candidate.is_file() and str(candidate).startswith(str(WEBAPP_DIR)):
            return FileResponse(candidate)
    return _spa()


# Environment variables that only exist on a deployment taking money. Their
# presence is the most reliable available signal that this is not a laptop.
_BILLING_KEYS = (
    "STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET",
    "RAZORPAY_KEY_SECRET", "PADDLE_API_KEY", "LEMONSQUEEZY_API_KEY",
    "OMNIX_BILLING_ENABLED",
)


def _refuse_demo_with_billing() -> None:
    """Demo mode accepts any password. It must never run where money does.

    `OMNIX_AUTH=demo` exists so the gate can be *shown* in a walkthrough
    without a mistyped password ending the demo. That is a reasonable thing to
    want on a laptop and a catastrophe on a deployment with customers: it is
    not a weaker password policy, it is none.

    Refusing to start is deliberate. A warning printed to a log nobody is
    watching is how this survives to production; a process that will not boot
    is noticed within one deploy.
    """
    import os
    import sys

    if not _auth.demo_mode():
        return
    present = [k for k in _BILLING_KEYS if (os.environ.get(k) or "").strip()]
    if not present:
        return
    print("[auth] REFUSING TO START.\n"
          f"[auth] OMNIX_AUTH=demo accepts ANY password, and {', '.join(present)} "
          "is set — this looks like a deployment that takes money.\n"
          "[auth] Set OMNIX_AUTH=on, or unset the billing configuration.",
          file=sys.stderr)
    raise SystemExit(2)


def main():
    import uvicorn

    _refuse_demo_with_billing()

    print("OMNIX web running at http://127.0.0.1:8000")
    if _auth.demo_mode():
        print("[auth] DEMO MODE — the login screen accepts ANY password. "
              "Anyone who can reach this port can open every Space.\n"
              "[auth] Set OMNIX_AUTH=on before showing this to anyone you "
              "would not hand the database to.")
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")


if __name__ == "__main__":
    main()
