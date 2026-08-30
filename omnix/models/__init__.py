"""OMNIX model layer — one router, one provider interface, one ledger.

Before this package there were five routing tables (`config.CLOUD_LADDER`,
`config.CLOUD_TIERS`, `squad.oracle_models.ORACLE_LADDERS`,
`avalon.config.NVIDIA_LADDERS`, `NVIDIA_ROLE_LADDERS`) reached through two
unrelated call paths, and only AVALON's could report tokens. Adding a provider
meant editing five places, and cost was unknowable outside one subsystem.

The shape here:

    capabilities  what an operation NEEDS (FAST, REASONING, CODING, ...)
    registry      which models satisfy a capability, in measured order
    providers     how to actually call one, behind a stable interface
    router        pick a ladder, run it, meter it

Application code never names a model. It names a capability and a mode, and the
router resolves the rest — which is what keeps OMNIX from being permanently
married to NVIDIA.
"""

from __future__ import annotations

from .capabilities import CAPABILITIES, Capability
from .router import ModelRouter, RouterResult, router

__all__ = ["Capability", "CAPABILITIES", "ModelRouter", "RouterResult", "router"]
