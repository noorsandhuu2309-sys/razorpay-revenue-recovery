"""OMNIX plugin system — manifest, permissions, health, registry, tool bus.

See `docs/OMNIX_PLUGIN_ARCHITECTURE.md` for why this exists and what of the
specification was already built elsewhere in the tree.
"""

from .health import Freshness, Health, Result, Status, Unavailable
from .manifest import HIGH_RISK, PERMISSIONS, DataSource, Manifest, ManifestError, ToolSpec
from .plugin import Plugin
from .registry import Registry, shared

__all__ = [
    "Freshness", "Health", "Result", "Status", "Unavailable",
    "DataSource", "Manifest", "ManifestError", "ToolSpec",
    "PERMISSIONS", "HIGH_RISK",
    "Plugin", "Registry", "shared",
]
