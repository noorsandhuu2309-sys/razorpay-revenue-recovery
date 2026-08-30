"""HTTP routers for the platform layer.

Split out of `omnix/server.py`, which had grown to ~90 routes in one module.
New surfaces go here; the legacy agent routes stay where they are until their
agents are rewritten.
"""

from __future__ import annotations

__all__ = ["platform", "objects"]
