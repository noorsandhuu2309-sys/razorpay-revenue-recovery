"""The typed manifest a plugin declares itself with.

WHY A FILE AND NOT A CLASS ATTRIBUTE
------------------------------------
`plugin.json` sits beside the code and is readable without importing it. That
matters for two things the registry has to do before a plugin is trustworthy
enough to import: show it in the plugin manager (name, permissions, data
sources) and decide whether the user has granted what it asks for. Importing a
module to find out what permissions it wants means the module has already run.

WHY VALIDATION IS STRICT
------------------------
A manifest that half-parses is worse than one that fails: the plugin loads,
declares no permissions, and is therefore allowed to do anything the code
happens to call. Unknown permission strings are rejected rather than ignored
for the same reason — a typo in `filesytem.write` must not silently become "no
filesystem permission required".
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


class ManifestError(ValueError):
    """A manifest that cannot be trusted. Never downgraded to a warning."""


# Every permission OMNIX recognises (§5). A manifest naming anything outside
# this set is refused — see the module docstring.
PERMISSIONS: frozenset[str] = frozenset({
    "network.read", "network.write",
    "filesystem.read", "filesystem.write",
    "process.execute",
    "browser.read", "browser.write",
    "microphone.read", "camera.read", "screen.read",
    "notification.send", "telegram.send",
    "email.read", "email.send",
    "calendar.read", "calendar.write",
    "financial.read", "secrets.read",
})

# Permissions that can never be granted by a manifest alone, however the plugin
# was installed. Each one lets a plugin act on the machine rather than read from
# it, and the blast radius of a mistake is the user's filesystem or shell.
HIGH_RISK: frozenset[str] = frozenset({
    "process.execute", "filesystem.write", "browser.write",
    "microphone.read", "camera.read", "screen.read",
    "email.send", "telegram.send",
})

CATEGORIES: frozenset[str] = frozenset({
    "intelligence", "geospatial", "economics", "markets", "earth",
    "research", "knowledge", "action", "system", "productivity",
    "security", "developer", "utility",
})


@dataclass(frozen=True)
class DataSource:
    """A provider this plugin reads from (§8, §79).

    `free` is not a marketing claim — it must be verifiable from the provider's
    own documentation, and `docs_url` is where that verification lives. A source
    whose terms have not been read gets `free=False`, because assuming a free
    tier that does not exist is how a hobby project acquires an invoice.
    """
    id: str
    name: str
    provider: str
    docs_url: str = ""
    auth: str = "none"            # none | api_key | oauth | account
    free: bool = False
    rate_limit: str = ""
    license: str = ""
    attribution: str = ""


@dataclass(frozen=True)
class ToolSpec:
    """One callable the plugin exposes on the tool bus (§46)."""
    name: str
    description: str
    input_schema: dict = field(default_factory=dict)
    output_schema: dict = field(default_factory=dict)
    permissions: tuple[str, ...] = ()
    timeout_s: float = 30.0


@dataclass(frozen=True)
class Manifest:
    id: str
    name: str
    version: str
    description: str
    category: str
    author: str = "OMNIX"
    # False by default and deliberately so. FOCUS.md's diagnosis was that
    # OMNIX did eight things and none was a reason to pay; a plugin system whose
    # plugins all switch themselves on rebuilds that problem with extra steps.
    enabled_by_default: bool = False
    permissions: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    optional_dependencies: tuple[str, ...] = ()
    # Environment variable names, never values.
    secrets: tuple[str, ...] = ()
    configuration: dict = field(default_factory=dict)
    data_sources: tuple[DataSource, ...] = ()
    tools: tuple[ToolSpec, ...] = ()
    # Minimum plan required (see core/entitlements.py). None means any plan.
    requires_plan: str | None = None
    # Rough cost of running this plugin, for §104 resource governance.
    poll_seconds: int = 0
    estimated_memory_mb: int = 16

    @property
    def high_risk_permissions(self) -> tuple[str, ...]:
        return tuple(sorted(set(self.permissions) & HIGH_RISK))


def _require(data: dict, key: str, path: Path) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"{path}: '{key}' is required and must be a non-empty string")
    return value.strip()


def parse(data: dict, *, path: Path | None = None) -> Manifest:
    """Validate a manifest dict into a `Manifest`. Raises `ManifestError`."""
    path = path or Path("<memory>")

    plugin_id = _require(data, "id", path)
    if not plugin_id.replace("_", "").isalnum():
        raise ManifestError(
            f"{path}: id '{plugin_id}' must be alphanumeric with underscores — "
            "it becomes a module path and a database key")

    category = _require(data, "category", path)
    if category not in CATEGORIES:
        raise ManifestError(
            f"{path}: unknown category '{category}'. "
            f"Known: {', '.join(sorted(CATEGORIES))}")

    perms = tuple(data.get("permissions") or ())
    unknown = set(perms) - PERMISSIONS
    if unknown:
        raise ManifestError(
            f"{path}: unknown permission(s) {sorted(unknown)}. "
            "A misspelled permission would otherwise read as 'needs nothing'.")

    sources = []
    for raw in data.get("data_sources") or ():
        if not isinstance(raw, dict) or not raw.get("id"):
            raise ManifestError(f"{path}: each data_source needs an 'id'")
        sources.append(DataSource(
            id=raw["id"], name=raw.get("name", raw["id"]),
            provider=raw.get("provider", ""), docs_url=raw.get("docs_url", ""),
            auth=raw.get("auth", "none"), free=bool(raw.get("free", False)),
            rate_limit=raw.get("rate_limit", ""), license=raw.get("license", ""),
            attribution=raw.get("attribution", "")))

    tools = []
    for raw in data.get("tools") or ():
        if not isinstance(raw, dict) or not raw.get("name"):
            raise ManifestError(f"{path}: each tool needs a 'name'")
        t_perms = tuple(raw.get("permissions") or ())
        stray = set(t_perms) - set(perms)
        if stray:
            raise ManifestError(
                f"{path}: tool '{raw['name']}' requires {sorted(stray)}, which "
                "the plugin itself does not declare. A tool cannot hold a "
                "permission its plugin was never granted.")
        tools.append(ToolSpec(
            name=raw["name"], description=raw.get("description", ""),
            input_schema=raw.get("input_schema") or {},
            output_schema=raw.get("output_schema") or {},
            permissions=t_perms,
            timeout_s=float(raw.get("timeout_s", 30.0))))

    return Manifest(
        id=plugin_id,
        name=_require(data, "name", path),
        version=_require(data, "version", path),
        description=data.get("description", ""),
        category=category,
        author=data.get("author", "OMNIX"),
        enabled_by_default=bool(data.get("enabled_by_default", False)),
        permissions=perms,
        dependencies=tuple(data.get("dependencies") or ()),
        optional_dependencies=tuple(data.get("optional_dependencies") or ()),
        secrets=tuple(data.get("secrets") or ()),
        configuration=data.get("configuration") or {},
        data_sources=tuple(sources),
        tools=tuple(tools),
        requires_plan=data.get("requires_plan"),
        poll_seconds=int(data.get("poll_seconds", 0)),
        estimated_memory_mb=int(data.get("estimated_memory_mb", 16)),
    )


def load(path: Path) -> Manifest:
    """Read and validate a `plugin.json`."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ManifestError(f"{path}: invalid JSON — {e}") from e
    if not isinstance(data, dict):
        raise ManifestError(f"{path}: manifest must be a JSON object")
    return parse(data, path=path)
