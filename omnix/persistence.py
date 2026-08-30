"""Tiny helpers for atomic-ish JSON persistence used by memory and cache modules.

Paths are resolved relative to the project root (the OMNIX/ folder) unless an
absolute path is given, so data files sit alongside the code regardless of the
current working directory.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

# omnix/persistence.py -> project root is one level above the package.
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def resolve(path: str | Path) -> Path:
    p = Path(path)
    return p if p.is_absolute() else PROJECT_ROOT / p


def load_json(path: str | Path, default):
    p = resolve(path)
    try:
        if p.exists():
            with p.open("r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return default


def save_json(path: str | Path, data, indent: int | None = 2) -> bool:
    """Write JSON, using a temp file + replace so a crash can't truncate it.

    `indent=None` writes compact output, for stores big enough that pretty
    printing is a real cost. The temp file is created in the destination's own
    directory so `os.replace` is a rename within one filesystem, which is the
    part that makes it atomic.
    """
    p = resolve(path)
    tmp = None
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(p.parent), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=indent)
        os.replace(tmp, p)
        return True
    except Exception:
        # Leaving the temp file behind would litter the project root with one
        # `.tmp` per failed write.
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        return False
