"""The real `omnix.server` app, not a router assembled for a test.

`test_api.py` mounts the graph router on a bare FastAPI app so the suite does
not import ARENA, AVALON and Playwright to exercise JSON routes. That leaves
one thing untested: whether the routers are actually wired into the app the
user runs. A router that works perfectly and is never included is a 404.

Route registration is asserted through the OpenAPI schema rather than
`app.routes`. As of FastAPI 0.139 `include_router` appends a single
`_IncludedRouter` marker and resolves the real paths lazily, so the route list
is no longer flat and reads as empty — the schema is the supported view.
"""

from __future__ import annotations

import pytest

server = pytest.importorskip("omnix.server")


@pytest.fixture(scope="module")
def paths() -> set[str]:
    return set(server.app.openapi().get("paths", {}))


# ---------------------------------------------------------------------------
# The surfaces the workspace frontend calls
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("path", [
    "/api/ontology",
    "/api/objects",
    "/api/objects/search",
    "/api/objects/{object_id}",
    "/api/objects/{object_id}/relationships",
    "/api/relationships",
    "/api/graph",
    "/api/graph/expand",
    "/api/graph/path",
    "/api/graph/stats",
    "/api/timeline",
    "/api/events",
    "/api/views",
])
def test_graph_router_is_mounted(paths, path):
    assert path in paths, f"{path} is not registered on the server app"


@pytest.mark.parametrize("path", [
    "/api/workspaces",
    "/api/workspaces/{workspace_id}",
    # Artifacts are listed per workspace and fetched by id — there is no bare
    # /api/artifacts collection, because an artifact outside a workspace has
    # nothing to be scoped by.
    "/api/workspaces/{workspace_id}/artifacts",
    "/api/artifacts/{artifact_id}",
    "/api/artifacts/{artifact_id}/lineage",
    "/api/executions",
    "/api/executions/active",
    "/api/usage",
])
def test_platform_router_is_mounted(paths, path):
    assert path in paths, f"{path} is not registered on the server app"


@pytest.mark.parametrize("path", ["/api/nova/command", "/api/research/run",
                                  "/api/claims", "/api/brief", "/api/summary"])
def test_nova_router_is_mounted(paths, path):
    assert path in paths, f"{path} is not registered on the server app"


def test_terra_routes_still_exist(paths):
    """TERRA is projected into the workspace, not replaced — its own API stays."""
    for path in ("/api/terra/overview", "/api/terra/theatres",
                 "/api/terra/graph", "/api/terra/search"):
        assert path in paths, f"{path} disappeared"


def test_squad_routes_outlive_the_bundle_that_called_them(paths):
    """The legacy bundle was retired without deleting the endpoints it used.
    Keeping them means a dropped surface can return as a workspace view rather
    than needing to be rebuilt from scratch."""
    assert "/api/squad/units" in paths


# ---------------------------------------------------------------------------
# Static surfaces
# ---------------------------------------------------------------------------
def test_the_react_app_is_served_at_the_root():
    route_paths = {getattr(r, "path", "") for r in server.app.routes}
    assert "/" in route_paths


def test_the_workspace_build_exists_and_is_the_react_app():
    """A missing build makes `/` return a 503 with a hint, which is a much
    worse thing to discover in a browser than in a test."""
    index = server.WEBAPP_DIR / "index.html"
    assert index.exists(), "run `cd frontend && npm run build`"
    html = index.read_text(encoding="utf-8")
    assert "/assets/" in html, "asset URLs must be based at the root"
    assert "/workspace/assets/" not in html, (
        "stale build: rebuild after the base changed from /workspace/ to /")
    assert "<div id=\"root\">" in html


def test_workspace_urls_still_resolve_after_the_move():
    """Bookmarks and saved links point at /workspace. They must not 404."""
    route_paths = {getattr(r, "path", "") for r in server.app.routes}
    assert "/workspace" in route_paths
    assert "/workspace/{path:path}" in route_paths


def test_the_gazetteers_survive_the_retirement():
    """`/static` is mounted from the retired bundle's OWN directory. Deleting
    that directory with it would break the React graph and map, which load
    cosmos.gl and the world/states gazetteers from there."""
    assert (server.WEB_DIR / "cosmos.min.js").exists()
    assert (server.WEB_DIR / "world.json").exists()
    assert (server.WEB_DIR / "states.json").exists()


def test_the_legacy_bundle_is_gone():
    """The 2.65MB single-file page is retired. If it reappears, something has
    re-run a build pipeline that no longer exists."""
    assert not (server.WEB_DIR / "index.html").exists()


def test_the_omnix_mark_is_bundled():
    """The rail must not silently lose its logo.

    The wireframe-Möbius mark is a PNG imported by Workspace.tsx, so Vite emits
    it into assets/ with a content hash. If the import is dropped the rail
    loses its brand with nothing failing anywhere else.
    """
    assets = server.WEBAPP_DIR / "assets"
    marks = list(assets.glob("omnix-mark-*.png"))
    assert marks, f"no omnix-mark-*.png emitted into {assets}"
    assert marks[0].stat().st_size > 1024


def test_the_favicon_is_the_omnix_one():
    favicon = server.WEBAPP_DIR / "favicon.png"
    assert favicon.exists(), "favicon.png missing from the workspace build"
    # The scaffold favicon that shipped with the Vite template was an SVG.
    assert not (server.WEBAPP_DIR / "favicon.svg").exists(), \
        "the purple scaffold favicon is back in the build output"


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
def test_database_is_reachable():
    from omnix.core import db
    ok, err = db.healthy()
    assert ok, err


# ---------------------------------------------------------------------------
# Handlers that block must not be coroutines
# ---------------------------------------------------------------------------
# A route declared `async def` runs ON the event loop. If its body blocks — a
# ThreadPoolExecutor wait, speech synthesis, a DOCX render — it holds the loop
# for that whole duration and every other request in the process stalls behind
# it. This was real: `/api/roster/health` waits up to 75s on a model probe, and
# while it ran the map, the graph, the plugin list and chat all timed out.
# FastAPI runs a plain `def` handler in its threadpool instead, so the cost
# stays inside the one request that asked for it.
#
# The guard is deliberately about the *declaration*, not the behaviour: there is
# no cheap runtime assertion for "this coroutine blocks", and the failure mode
# is invisible until something else is slow at the same time.
@pytest.mark.parametrize("module_path, name", [
    ("omnix.api.models", "health"),      # probes every model, up to 75s
    ("omnix.server", "tts_endpoint"),    # speech synthesis, seconds of CPU
    ("omnix.server", "export_document"), # DOCX/PDF rendering
])
def test_blocking_handlers_are_not_coroutines(module_path, name):
    import asyncio
    import importlib

    mod = importlib.import_module(module_path)
    fn = getattr(mod, name)
    assert not asyncio.iscoroutinefunction(fn), (
        f"{module_path}.{name} is `async def` but its body blocks; "
        "declare it `def` so FastAPI runs it off the event loop"
    )


# ---------------------------------------------------------------------------
# A missing argument is the caller's fault, not the provider's
# ---------------------------------------------------------------------------
def test_weather_without_arguments_is_400_not_502(monkeypatch):
    """`/api/weather` used to answer 502 for every non-success, including this.

    502 means "the upstream service failed". A request that names neither a city
    nor coordinates never reaches upstream, so reporting it that way told the
    Intelligence panel the weather service was down and logged an outage that
    had not happened. Only the no-argument path is asserted: it is the one that
    resolves without touching the network, so the test stays offline-safe.
    """
    from fastapi.testclient import TestClient

    # The gate is on by default and would answer 401 before the handler runs;
    # this test is about the handler's own status code, not about auth.
    monkeypatch.setenv("OMNIX_AUTH", "off")
    with TestClient(server.app) as client:
        r = client.get("/api/weather")
    assert r.status_code == 400, r.text
    assert "city or coordinates" in r.json().get("error", "")


# ---------------------------------------------------------------------------
# Uploaded images must not outlive the turn that used them
# ---------------------------------------------------------------------------
def test_an_uploaded_image_is_deleted_when_the_turn_ends(monkeypatch, tmp_path):
    """`/api/chat` writes an inline data-URL image to a temp file for the vision
    agent, with `delete=False` because it has to outlive the request setup.

    Nothing removed it afterwards, so every picture a user sent stayed in the
    system temp directory permanently — disk growth driven by ordinary use. The
    delete belongs in the streaming generator's `finally`, which also covers the
    client disconnecting part-way through a long vision answer.
    """
    import base64
    import tempfile
    from fastapi.testclient import TestClient

    # Point tempfile at a directory we can count, so the assertion cannot be
    # confused by anything else on the machine.
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.setenv("OMNIX_AUTH", "off")

    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYPhfDwACh"
        "wGA60e6kgAAAABJRU5ErkJggg==")
    payload = {
        "messages": [{"role": "user", "content": "what is this?"}],
        "image": "data:image/png;base64," + base64.b64encode(png).decode(),
    }

    with TestClient(server.app) as client:
        with client.stream("POST", "/api/chat", json=payload) as r:
            for _ in r.iter_bytes():   # drain, so the generator reaches `finally`
                pass

    left = list(tmp_path.glob("*.png"))
    assert not left, "uploaded image survived the turn: %s" % [p.name for p in left]
