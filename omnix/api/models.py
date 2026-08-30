"""The model roster API: what OMNIX can run, and which of them are switched on.

Three routes, deliberately small:

    GET  /api/roster          the roster + the live on/off state
    PUT  /api/roster/prefs    toggle a model, or turn Auto off
    GET  /api/roster/health   which of them answered a probe, and how fast

**Why `/api/roster` and not `/api/models`.** `/api/models` already exists in
`server.py` and answers a different question — "which model is every subsystem
running right now", read by the agent picker, the NEXUS cards and the consoles.
Mounting this router at that prefix shadowed it and would have broken all three.
The two are genuinely different resources: that one reports live assignment
across the whole product, this one is the short human roster with its switches.

`health` is a separate route and NOT folded into `GET /api/roster` because it
costs a real inference call per model. The settings screen opens far more often
than anyone needs a latency reading, and making the roster slow to load in order
to decorate it with numbers is the wrong trade. It is fetched on demand.
"""

from __future__ import annotations

import concurrent.futures
import time

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from .. import model_catalog as mc

router = APIRouter(prefix="/api/roster", tags=["roster"])


@router.get("")
async def list_models():
    """The roster, grouped by the job each model does."""
    cat = mc.public_catalog()
    roles = []
    for role in ("chat", "code", "vision", "research", "fast"):
        members = [m for m in cat if m["role"] == role]
        if not members:
            continue
        roles.append({
            "id": role,
            "label": mc.ROLE_LABEL.get(role, role),
            "models": members,
        })
    return {
        "models": cat,
        "roles": roles,
        "auto": mc.auto_enabled(),
        # A run-wide pin set by OMNIX_CHAT_MODEL. Surfaced so the UI can say the
        # picker is being overridden rather than appear to be ignored.
        "pinned": mc.env_override(),
    }


@router.put("/prefs")
async def set_prefs(payload: dict):
    """Body: { enabled: {id: bool}, auto: bool } — both optional.

    Unknown ids are ignored rather than 400'd: a stale tab holding a model that
    a later build dropped should not have its whole save rejected.
    """
    enabled = payload.get("enabled")
    if isinstance(enabled, dict):
        for mid, on in enabled.items():
            mc.set_enabled(str(mid), bool(on))
    if "auto" in payload:
        mc.set_auto(bool(payload["auto"]))
    return await list_models()


@router.get("/health")
def health():
    """Probe every model in the roster concurrently and report first-token time.

    Deliberately `def`, not `async def`. The body blocks — it waits on a
    ThreadPoolExecutor for up to 75s — and a blocking wait inside a coroutine
    holds the whole event loop, not just this request. It did: while this route
    ran, every other endpoint (the map, the graph, the plugin list, chat) stalled
    for the full probe and timed out. FastAPI runs a sync handler in its
    threadpool, so the loop stays free and the probe only costs its own request.

    Concurrent because serial probing of six models takes as long as the sum of
    their cold starts — about a minute — and nobody waits that long for a
    settings panel. Each probe is capped so one dead model cannot hold the
    response open.
    """
    from .. import nvidia_client

    if not nvidia_client.available():
        return JSONResponse(
            {"error": "no NVIDIA key configured", "results": {}}, status_code=200)

    def probe(entry: dict) -> tuple[str, dict]:
        t0 = time.monotonic()
        try:
            # One token is enough to prove the model answers. `warm` already
            # exists for the keeper thread and does exactly this.
            #
            # Its RESULT has to be read. `warm` swallows every failure and
            # returns False — it is written for a background thread that has
            # nobody to tell — so ignoring the bool and reporting `ok: True`
            # because no exception escaped painted a green tick next to models
            # that were answering 404 and 410. That is precisely the reading
            # this panel exists to give, so it must come from the status code.
            # 45s, not `warm`'s 20s default. This panel probes models that may
            # be genuinely cold, and a cold instance here measured 43s to first
            # token — under the default the vision model reported as DEAD every
            # time it had not been used recently, which is the most alarming
            # possible way to say "not warmed up yet".
            live = nvidia_client.warm(entry["model"], timeout=45.0)
            ms = int((time.monotonic() - t0) * 1000)
            if live:
                return entry["id"], {"ok": True, "ms": ms}
            return entry["id"], {"ok": False, "ms": ms,
                                 "error": "no answer from the provider"}
        except Exception as e:
            return entry["id"], {"ok": False, "error": str(e)[:120],
                                 "ms": int((time.monotonic() - t0) * 1000)}

    results: dict[str, dict] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(probe, m) for m in mc.CATALOG]
        for f in concurrent.futures.as_completed(futures, timeout=75):
            try:
                mid, res = f.result()
                results[mid] = res
            except Exception:
                continue

    # Anything that never reported inside the window is unknown, not dead — a
    # timeout here is about this probe, not about the model.
    for m in mc.CATALOG:
        results.setdefault(m["id"], {"ok": None, "error": "probe timed out"})
    return {"results": results}
