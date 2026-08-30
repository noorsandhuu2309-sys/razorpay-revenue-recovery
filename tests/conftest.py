"""Shared fixtures.

Every test runs against a throwaway SQLite file, never the real
`omnix_platform.db`. The database URL has to be in the environment *before*
`omnix.core.db` is imported, because that module caches its engine in a global
on first use — so the env var is set at import time here, and conftest.py is
the first thing pytest loads.
"""

from __future__ import annotations

import os
import tempfile
import pathlib

_TMP = pathlib.Path(tempfile.mkdtemp(prefix="omnix-tests-"))
os.environ["OMNIX_DATABASE_URL"] = f"sqlite:///{(_TMP / 'test.db').as_posix()}"

import pytest  # noqa: E402

from omnix.core import db  # noqa: E402
from omnix.core import objects as objects_mod  # noqa: E402
from omnix.core import workspace as workspace_mod  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _schema():
    """Create the schema once for the whole session."""
    db.init_db()
    yield


@pytest.fixture
def ws() -> str:
    """A fresh, empty workspace per test.

    Tests must not share a workspace: almost everything here is scoped by
    workspace id, so a per-test one gives isolation without truncating tables
    between tests.
    """
    uid = workspace_mod.default_user()
    return workspace_mod.create(uid, "Test workspace", "pytest")["id"]


@pytest.fixture
def obj(ws):
    """Factory: make an object in the test workspace and return its dict."""
    def _make(type_key: str, name: str, **kw) -> dict:
        o, _created = objects_mod.upsert_object(ws, type_key, name, **kw)
        return o
    return _make
