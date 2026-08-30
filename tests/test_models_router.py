"""The model router singleton, and the import trap that broke two call sites.

`omnix/models/__init__.py` does `from .router import ModelRouter, RouterResult,
router`. That binds the name `router` on the package to the ModelRouter
*instance*, shadowing the `omnix.models.router` *submodule*. So:

    from omnix.models import router as router_mod   # the INSTANCE
    from omnix.models.router import router          # also the instance
    import omnix.models.router                      # the module

Two call sites read the first form as if it were the module and called
`router_mod.shared().generate(...)`. There has never been a `shared()`, so both
raised AttributeError at runtime: `/api/nova/command` returned 500 for every
question, and research ingestion could not extract entities — which is why the
Claim Ledger stayed empty.

Nothing type-checks Python imports, so these tests are the guard.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from omnix.models import ModelRouter
from omnix.models.router import router as singleton

SRC = pathlib.Path(__file__).resolve().parent.parent / "omnix"


# ---------------------------------------------------------------------------
# The singleton
# ---------------------------------------------------------------------------
def test_the_package_attribute_is_the_instance_not_the_module():
    """Documents the shadowing rather than pretending it is not there."""
    from omnix import models
    assert isinstance(models.router, ModelRouter)


def test_the_singleton_exposes_the_methods_callers_use():
    for name in ("generate", "generate_json", "select"):
        assert callable(getattr(singleton, name, None)), \
            f"router.{name} is missing"


def test_router_has_never_had_a_shared_factory():
    """If a `shared()` is ever added, this test should be deleted deliberately
    rather than the broken call sites being resurrected by accident."""
    assert not hasattr(singleton, "shared")


# ---------------------------------------------------------------------------
# Static guard over every call site
# ---------------------------------------------------------------------------
def _py_files():
    return [p for p in SRC.rglob("*.py") if "__pycache__" not in str(p)]


def _code_lines(path: pathlib.Path):
    """Numbered lines with whole-line comments dropped.

    The comments explaining this very trap quote the broken forms, so a naive
    scan flags the documentation as the defect.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    for i, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue
        yield i, line


def test_no_source_file_calls_shared_on_the_router():
    bad = []
    pattern = re.compile(r"router\w*\s*\.\s*shared\s*\(")
    for path in _py_files():
        for i, line in _code_lines(path):
            if pattern.search(line):
                bad.append(f"{path.relative_to(SRC.parent)}:{i}: {line.strip()}")
    assert not bad, "router.shared() does not exist:\n" + "\n".join(bad)


def test_router_imports_use_the_submodule_path():
    """`from ..models import router` is legal but reads as a module and is what
    misled both broken call sites. Require the unambiguous form."""
    bad = []
    pattern = re.compile(r"from\s+\.*models\s+import\s+router\b")
    for path in _py_files():
        if path.name == "__init__.py":
            continue
        for i, line in _code_lines(path):
            if pattern.search(line):
                bad.append(f"{path.relative_to(SRC.parent)}:{i}: {line.strip()}")
    assert not bad, (
        "import the singleton explicitly — `from ..models.router import router`"
        " — so it cannot be mistaken for the module:\n" + "\n".join(bad))


# ---------------------------------------------------------------------------
# The endpoint that broke
# ---------------------------------------------------------------------------
def test_direct_answer_does_not_raise_without_a_provider(monkeypatch, ws):
    """The 500 was an AttributeError before any provider was contacted. A
    provider that is merely unavailable must degrade to `ok: false`, never to
    an exception — the command bar has to answer something."""
    from omnix.api import nova
    from omnix.models.router import router as real

    class _Down:
        ok = False
        text = ""
        error = "no provider configured"
        model = ""

    monkeypatch.setattr(real, "generate", lambda *a, **k: _Down())

    out = nova._direct_answer(ws, "hello", [])
    assert out["answer"] == "text"
    assert out["ok"] is False
    assert out["error"]


def test_research_extraction_degrades_without_a_provider(monkeypatch, ws):
    """The same bug silently disabled entity extraction, which is why no
    research run ever produced claims."""
    from omnix.core import research_ingest
    from omnix.models.router import router as real

    class _Down:
        ok = False
        text = ""
        error = "no provider configured"
        model = ""

    monkeypatch.setattr(real, "generate", lambda *a, **k: _Down())

    out = research_ingest.extract_entities(
        "question", "notes", workspace_id=ws, execution_id=None)
    assert out["entities"] == [] and out["relations"] == []
    assert out["error"]
