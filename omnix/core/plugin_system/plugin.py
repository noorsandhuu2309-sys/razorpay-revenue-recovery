"""The base class every OMNIX plugin implements.

A plugin owns three things and nothing else: a manifest, a health record, and a
set of tools. It does not own its own configuration file, its own cache, its own
logger or its own database connection — those come from the platform, so that
turning a plugin off actually stops it and turning it on does not require it to
have got any of that right.

WHY `call` WRAPS EVERY TOOL
---------------------------
Health, permissions, timing and the no-fabrication rule are cross-cutting: if
each tool implemented them, each tool would implement them slightly differently
and one of them would forget. `call()` is the single path through which a tool
runs, so a plugin author writes the part that is actually specific to their
provider and inherits the rest.

That is also what makes §2's promise real — "never allow one plugin failure to
crash OMNIX". A tool that raises anything at all becomes an `Unavailable`
result and a recorded failure, because the alternative is an exception crossing
a plugin boundary into the orchestrator.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Callable

from . import permissions as perms
from .health import Freshness, Health, Result, Status
from .manifest import Manifest


class Plugin:
    """Subclass this. Override `probe` and register tools in `setup`."""

    #: Set by the loader from the plugin's directory.
    manifest: Manifest
    directory: Path

    def __init__(self, manifest: Manifest, directory: Path | None = None):
        self.manifest = manifest
        self.directory = directory or Path(".")
        self.health = Health(status=Status.DISABLED)
        self._tools: dict[str, Callable[..., Any]] = {}
        self._setup_done = False

    # -- identity ---------------------------------------------------------
    @property
    def id(self) -> str:
        return self.manifest.id

    def __repr__(self) -> str:
        return f"<Plugin {self.id} {self.health.status.value}>"

    # -- lifecycle --------------------------------------------------------
    def setup(self) -> None:
        """Register tools and build adapters. Override in a subclass.

        Called once, on enable. Anything expensive belongs here rather than in
        `__init__`, because the registry constructs every discovered plugin in
        order to read its health — including ones that are switched off.
        """

    def teardown(self) -> None:
        """Release anything `setup` acquired. Must be safe to call twice."""

    def enable(self, *, actor: str = "") -> Health:
        """Grant low-risk permissions, run setup, then probe."""
        pending = perms.grant_low_risk(
            self.id, self.manifest.permissions, actor=actor)

        missing = self.missing_secrets()
        if missing:
            self.health.status = Status.UNCONFIGURED
            self.health.detail = (
                f"Set {', '.join(missing)} to enable {self.manifest.name}.")
            self.health.fix_key = missing[0]
            self.health.docs_url = self._primary_docs_url()
            return self.health

        try:
            if not self._setup_done:
                self.setup()
                self._setup_done = True
        except Exception as e:                       # noqa: BLE001
            # A plugin that cannot construct is quarantined, never fatal.
            self.health.status = Status.FAILED
            self.health.detail = f"{type(e).__name__}: {e}"
            self.health.last_error = str(e)
            return self.health

        self.health.status = Status.OK
        self.health.detail = ""
        if pending:
            self.health.status = Status.DEGRADED
            self.health.detail = (
                "Awaiting permission: " + ", ".join(pending))
        return self.probe()

    def disable(self) -> Health:
        try:
            self.teardown()
        except Exception:                            # noqa: BLE001
            pass
        self._setup_done = False
        self.health.status = Status.DISABLED
        self.health.detail = ""
        return self.health

    # -- configuration ----------------------------------------------------
    def missing_secrets(self) -> tuple[str, ...]:
        """Declared secrets with no value in the environment.

        Names only — this never reads a value into a log, a health payload or
        an error message.
        """
        return tuple(k for k in self.manifest.secrets
                     if not (os.environ.get(k) or "").strip())

    def secret(self, key: str) -> str:
        """Read a declared secret. Undeclared keys are refused.

        Forcing the manifest to name it is what makes the plugin manager able
        to say "this plugin needs NASA_FIRMS_MAP_KEY" without running the
        plugin.
        """
        if key not in self.manifest.secrets:
            raise KeyError(
                f"{self.id} reads '{key}' but does not declare it in "
                "plugin.json — declare it so the settings UI can prompt for it")
        perms.require(self.id, "secrets.read") if "secrets.read" in \
            self.manifest.permissions else None
        return (os.environ.get(key) or "").strip()

    def config(self, key: str, default: Any = None) -> Any:
        return self.manifest.configuration.get(key, default)

    def _primary_docs_url(self) -> str:
        for src in self.manifest.data_sources:
            if src.docs_url:
                return src.docs_url
        return ""

    # -- tools ------------------------------------------------------------
    def register(self, name: str, fn: Callable[..., Any]) -> None:
        declared = {t.name for t in self.manifest.tools}
        if name not in declared:
            raise KeyError(
                f"{self.id} registers tool '{name}' which plugin.json does not "
                "declare. The manifest is what the orchestrator sees, so an "
                "undeclared tool is unreachable anyway.")
        self._tools[name] = fn

    def tool_names(self) -> tuple[str, ...]:
        return tuple(sorted(self._tools))

    def call(self, name: str, /, **kwargs) -> Result:
        """Run one tool. Always returns a `Result`; never raises.

        The ordering matters: disabled and unconfigured are checked before
        permissions, because "this plugin is off" is a more useful answer than
        "this plugin lacks a permission it would need if it were on".
        """
        spec = next((t for t in self.manifest.tools if t.name == name), None)
        if spec is None:
            return Result.unavailable(
                f"{self.manifest.name} has no tool '{name}'.",
                status=Status.FAILED)

        if self.health.status is Status.DISABLED:
            return Result.unavailable(
                f"{self.manifest.name} is switched off.",
                status=Status.DISABLED)

        if self.health.status is Status.UNCONFIGURED:
            return Result.unavailable(
                self.health.detail or f"{self.manifest.name} is not configured.",
                status=Status.UNCONFIGURED,
                fix_key=self.health.fix_key, docs_url=self.health.docs_url)

        fn = self._tools.get(name)
        if fn is None:
            return Result.unavailable(
                f"{self.manifest.name} declares '{name}' but never registered "
                "it. This is a bug in the plugin, not a configuration problem.",
                status=Status.FAILED)

        try:
            for p in spec.permissions:
                perms.require(self.id, p)
        except perms.PermissionDenied as e:
            return Result.unavailable(
                f"{self.manifest.name} needs permission '{e.permission}'.",
                status=Status.DEGRADED, fix_key=f"permission:{e.permission}")

        started = time.time()
        try:
            out = fn(**kwargs)
        except Exception as e:                       # noqa: BLE001
            # The boundary. Nothing a plugin does reaches the orchestrator as
            # an exception — §2, and the reason one bad adapter cannot take the
            # product down.
            self.health.record_failure(f"{type(e).__name__}: {e}")
            return Result.unavailable(
                f"{self.manifest.name} could not answer: {type(e).__name__}: {e}",
                status=Status.DEGRADED, docs_url=self._primary_docs_url())

        latency = int((time.time() - started) * 1000)
        if isinstance(out, Result):
            if out.available:
                self.health.record_success(latency_ms=latency, source=out.source)
            else:
                self.health.record_failure(out.error.reason, degrade=False)
            return out

        # A plugin that returns raw data still gets the platform's envelope, so
        # provenance is never optional.
        self.health.record_success(latency_ms=latency)
        return Result.ok(out, source=self.id,
                         freshness=Freshness.LIVE,
                         attribution=self._attribution())

    def _attribution(self) -> str:
        parts = [s.attribution for s in self.manifest.data_sources if s.attribution]
        return " · ".join(parts)

    # -- health -----------------------------------------------------------
    def probe(self) -> Health:
        """Check the provider is answering. Override where there is one.

        The default is honest rather than optimistic: a plugin with no probe
        reports the status it already had instead of claiming OK.
        """
        return self.health

    def describe(self) -> dict:
        """What the plugin manager renders (§53, §78)."""
        return {
            "id": self.id,
            "name": self.manifest.name,
            "version": self.manifest.version,
            "description": self.manifest.description,
            "category": self.manifest.category,
            "enabled": self.health.status is not Status.DISABLED,
            "health": self.health.to_dict(),
            "permissions": {
                "declared": list(self.manifest.permissions),
                "granted": list(perms.held(self.id)),
                "highRisk": list(self.manifest.high_risk_permissions),
            },
            "secrets": {
                "declared": list(self.manifest.secrets),
                "missing": list(self.missing_secrets()),
            },
            "dataSources": [
                {"id": s.id, "name": s.name, "provider": s.provider,
                 "free": s.free, "auth": s.auth, "license": s.license,
                 "attribution": s.attribution, "docsUrl": s.docs_url,
                 "rateLimit": s.rate_limit}
                for s in self.manifest.data_sources],
            "tools": [
                {"name": t.name, "description": t.description,
                 "inputSchema": t.input_schema, "permissions": list(t.permissions)}
                for t in self.manifest.tools],
            "requiresPlan": self.manifest.requires_plan,
            "resources": {"pollSeconds": self.manifest.poll_seconds,
                          "memoryMb": self.manifest.estimated_memory_mb},
        }
