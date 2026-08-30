"""Squad registry — instantiates every unit once and exposes lookup helpers.

Order here is the order the units appear in the NEXUS console.

The restructure narrows the product to five agents:

    NOVA (do) · ORACLE (know) · FORGE (build) · SENTINEL (protect) · PULSE (observe)

ATLAS, WARDEN and MUSE are no longer part of it. They are removed from the
*catalog* — so NEXUS, CORTEX and NOVA's launcher stop offering them — but they
are still resolvable by `get_unit()`, so anything already pointing at
a stored job history
keeps working through the deprecation window. Delete the modules and drop them
from `_DEPRECATED_CLASSES` once nothing calls them.
"""

from __future__ import annotations

from .challenge import Challenge
from .forge import Forge
from .nova import Nova
from .oracle import Oracle
from .pulse import Pulse
from .sentinel import Sentinel

# The product. NOVA first (the conductor), then the specialists, then the
# platform's own observability surface.
_PRIMARY_CLASSES = [Nova, Oracle, Challenge, Forge, Sentinel, Pulse]

# Nothing is retired-but-callable any more: ATLAS, WARDEN and MUSE were
# deleted in the v1 focus pass rather than left dangling behind the UI.
_DEPRECATED_CLASSES = []

UNITS = {}
for _cls in _PRIMARY_CLASSES + _DEPRECATED_CLASSES:
    _inst = _cls()
    UNITS[_inst.code] = _inst

PRIMARY_CODES = [c().code for c in _PRIMARY_CLASSES]
DEPRECATED_CODES = [c().code for c in _DEPRECATED_CLASSES]


def get_unit(code: str):
    """Resolve any unit, including deprecated ones (see module docstring)."""
    return UNITS.get((code or "").lower())


def catalog() -> list[dict]:
    """What the UI offers. Deprecated units are deliberately absent."""
    return [UNITS[code].catalog() for code in PRIMARY_CODES]


def is_deprecated(code: str) -> bool:
    return (code or "").lower() in DEPRECATED_CODES
