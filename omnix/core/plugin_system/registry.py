"""Discovery, enable/disable, and the tool bus the orchestrator sees.

DISCOVERY IS FILESYSTEM-FIRST
-----------------------------
A plugin is a directory under `omnix/plugins/` containing `plugin.json`. The
manifest is read *before* the module is imported, which is what lets the plugin
manager list a plugin's permissions and data sources without executing it — and
what lets a plugin whose code is broken still appear in the UI as FAILED rather
than vanishing.

ONE BAD PLUGIN CANNOT BREAK DISCOVERY
-------------------------------------
Every step — read manifest, import module, construct, enable — is caught per
plugin. A syntax error in one plugin leaves the other twenty working and shows
the broken one with the traceback in its health detail. That is §2's "never
allow one plugin failure to crash OMNIX" applied to load time, which is where
it is most often violated.

ENABLED STATE IS PERSISTED, DEFAULTS ARE NOT ASSUMED
----------------------------------------------------
`enabled_by_default` only decides what happens the first time a plugin is seen.
After that the user's choice is stored and wins, so upgrading a plugin never
silently switches it back on.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import logging
import threading
import traceback
from pathlib import Path

from . import permissions as perms
from .health import Health, Result, Status
from .manifest import Manifest, ManifestError, load as load_manifest
from .plugin import Plugin
from ...persistence import save_json

log = logging.getLogger("omnix.plugins")

_ROOT = Path(__file__).resolve().parents[3]
PLUGIN_DIR = _ROOT / "omnix" / "plugins"
STATE = _ROOT / "omnix_plugin_state.json"

_lock = threading.RLock()


def _read_state() -> dict:
    if not STATE.exists():
        return {}
    try:
        data = json.loads(STATE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _write_state(state: dict) -> None:
    # Atomic: a truncated state file reads back as {}, which re-enables every
    # plugin the user had switched off.
    save_json(STATE, state)


class Registry:
    """Every discovered plugin, by id."""

    def __init__(self, directory: Path | None = None):
        self.directory = directory or PLUGIN_DIR
        self._plugins: dict[str, Plugin] = {}
        self._broken: dict[str, str] = {}
        self._loaded = False

    # -- discovery --------------------------------------------------------
    def discover(self, *, reload: bool = False) -> dict[str, Plugin]:
        with _lock:
            if self._loaded and not reload:
                return dict(self._plugins)
            self._plugins.clear()
            self._broken.clear()

            if not self.directory.exists():
                self._loaded = True
                return {}

            for entry in sorted(self.directory.iterdir()):
                if not entry.is_dir() or entry.name.startswith((".", "_")):
                    continue
                manifest_path = entry / "plugin.json"
                if not manifest_path.exists():
                    continue
                self._load_one(entry, manifest_path)

            self._loaded = True
            return dict(self._plugins)

    def _load_one(self, directory: Path, manifest_path: Path) -> None:
        try:
            manifest = load_manifest(manifest_path)
        except ManifestError as e:
            # A manifest that will not parse is the one case where nothing can
            # be shown in the UI, because the id itself is untrusted.
            self._broken[directory.name] = str(e)
            log.warning("plugin %s: %s", directory.name, e)
            return

        try:
            plugin = self._construct(manifest, directory)
        except Exception as e:                       # noqa: BLE001
            plugin = Plugin(manifest, directory)
            plugin.health.status = Status.FAILED
            plugin.health.detail = f"{type(e).__name__}: {e}"
            plugin.health.last_error = traceback.format_exc(limit=3)
            log.warning("plugin %s failed to construct: %s", manifest.id, e)

        self._plugins[manifest.id] = plugin

        # Apply persisted state, falling back to the manifest's default only
        # for a plugin nobody has decided about yet.
        state = _read_state()
        want = state.get(manifest.id, {}).get(
            "enabled", manifest.enabled_by_default)
        if want and plugin.health.status is not Status.FAILED:
            try:
                plugin.enable()
            except Exception as e:                   # noqa: BLE001
                plugin.health.status = Status.FAILED
                plugin.health.detail = f"{type(e).__name__}: {e}"

    def _construct(self, manifest: Manifest, directory: Path) -> Plugin:
        """Import `plugin.py` and instantiate its `Plugin` subclass.

        Two import paths, and the distinction is not cosmetic. A plugin living
        under `omnix/plugins/` is imported as a package module, so its relative
        imports (`from .adapters.usgs import ...`) resolve and — critically —
        it is imported ONCE. Loading the same file twice under two names
        produces two distinct classes with the same qualified name, and then
        `except UsgsError` silently stops catching the `UsgsError` that was
        actually raised.

        A plugin from anywhere else (a test fixture, a future third-party
        directory) is loaded from its file, registered in `sys.modules` under a
        name derived from its absolute path so repeated discovery reuses it.
        """
        if directory.resolve().parent == PLUGIN_DIR.resolve():
            module_name = f"omnix.plugins.{directory.name}.plugin"
            module = importlib.import_module(module_name)
        else:
            module = self._import_from_path(directory)

        candidates = [
            obj for obj in vars(module).values()
            if isinstance(obj, type) and issubclass(obj, Plugin) and obj is not Plugin
        ]
        if not candidates:
            raise TypeError(
                f"{module_name} defines no Plugin subclass — a plugin module "
                "must export exactly one")
        if len(candidates) > 1:
            # Ambiguity here would make which class runs depend on dict order.
            named = [c.__name__ for c in candidates]
            raise TypeError(
                f"{module.__name__} defines several Plugin subclasses {named}; "
                "export one")
        return candidates[0](manifest, directory)

    @staticmethod
    def _import_from_path(directory: Path):
        """Load `<directory>/plugin.py` as a module, once per path."""
        import sys

        source = directory / "plugin.py"
        if not source.exists():
            raise FileNotFoundError(f"{directory} has no plugin.py")

        # Keyed by absolute path, so two plugins with the same directory name
        # in different trees do not collide, and re-discovery reuses the
        # existing module rather than duplicating its classes.
        key = "omnix_ext_plugin_" + str(directory.resolve()).replace("\\", "_").replace(
            "/", "_").replace(":", "")
        cached = sys.modules.get(key)
        if cached is not None:
            return cached

        spec = importlib.util.spec_from_file_location(key, source)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load {source}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[key] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            # Do not leave a half-executed module behind for the next import
            # to find and treat as complete.
            sys.modules.pop(key, None)
            raise
        return module

    # -- access -----------------------------------------------------------
    def all(self) -> list[Plugin]:
        self.discover()
        return [self._plugins[k] for k in sorted(self._plugins)]

    def get(self, plugin_id: str) -> Plugin | None:
        self.discover()
        return self._plugins.get(plugin_id)

    def broken(self) -> dict[str, str]:
        self.discover()
        return dict(self._broken)

    # -- control ----------------------------------------------------------
    def enable(self, plugin_id: str, *, actor: str = "") -> Health:
        p = self.get(plugin_id)
        if p is None:
            raise KeyError(plugin_id)
        health = p.enable(actor=actor)
        state = _read_state()
        state.setdefault(plugin_id, {})["enabled"] = True
        _write_state(state)
        return health

    def disable(self, plugin_id: str) -> Health:
        p = self.get(plugin_id)
        if p is None:
            raise KeyError(plugin_id)
        health = p.disable()
        state = _read_state()
        state.setdefault(plugin_id, {})["enabled"] = False
        _write_state(state)
        return health

    # -- the tool bus (§46) -----------------------------------------------
    def tools(self) -> list[dict]:
        """Every callable tool, for the orchestrator's tool list.

        Only from plugins that are actually usable. Offering the model a tool
        belonging to a disabled or unconfigured plugin means it plans a step
        that cannot run, then has to recover — which reads to the user as the
        assistant being confused rather than the plugin being off.
        """
        out = []
        for p in self.all():
            if p.health.status not in (Status.OK, Status.DEGRADED):
                continue
            for t in p.manifest.tools:
                out.append({
                    "plugin": p.id,
                    "name": t.name,
                    "qualified": f"{p.id}.{t.name}",
                    "description": t.description,
                    "input_schema": t.input_schema,
                    "output_schema": t.output_schema,
                    "permissions": list(t.permissions),
                    "timeout_s": t.timeout_s,
                })
        return out

    def call(self, qualified: str, /, **kwargs) -> Result:
        """Invoke `plugin_id.tool_name`. Always returns a `Result`."""
        plugin_id, _, tool = qualified.partition(".")
        if not tool:
            return Result.unavailable(
                f"'{qualified}' is not a qualified tool name "
                "(expected 'plugin.tool').", status=Status.FAILED)
        p = self.get(plugin_id)
        if p is None:
            return Result.unavailable(
                f"No plugin '{plugin_id}' is installed.", status=Status.FAILED)
        return p.call(tool, **kwargs)

    # -- reporting --------------------------------------------------------
    def report(self) -> dict:
        """The plugin manager's data (§44, §53)."""
        plugins = [p.describe() for p in self.all()]
        counts: dict[str, int] = {}
        for p in plugins:
            counts[p["health"]["status"]] = counts.get(p["health"]["status"], 0) + 1
        return {
            "plugins": plugins,
            "broken": [{"directory": d, "error": e}
                       for d, e in self.broken().items()],
            "counts": counts,
            "permissionAudit": perms.audit(limit=50),
        }


_shared: Registry | None = None


def shared() -> Registry:
    global _shared
    if _shared is None:
        _shared = Registry()
    return _shared
