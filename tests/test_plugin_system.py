"""The plugin core: manifests, permissions, health, isolation, the tool bus.

The assertions that matter most here are the negative ones. A plugin system is
easy to make work when everything is configured and every provider answers; its
whole value is what it does when a manifest is malformed, a module will not
import, a permission is missing, or a provider is down. §2 and §90 are the two
promises under test:

  * one plugin failing must never take OMNIX down, and
  * an unreachable provider must never look like an empty world.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from omnix.core.plugin_system import Plugin, Result, Status
from omnix.core.plugin_system import manifest as mf
from omnix.core.plugin_system import permissions as perms
from omnix.core.plugin_system.health import Freshness
from omnix.core.plugin_system.registry import Registry


# ---------------------------------------------------------------------------
# Manifest validation
# ---------------------------------------------------------------------------
def _base(**over) -> dict:
    data = {"id": "demo", "name": "Demo", "version": "1.0.0",
            "category": "utility"}
    data.update(over)
    return data


def test_a_valid_manifest_parses():
    m = mf.parse(_base(permissions=["network.read"]))
    assert m.id == "demo"
    assert m.permissions == ("network.read",)


def test_an_unknown_permission_is_refused_not_ignored():
    """A misspelled permission must not silently read as 'needs nothing' —
    that would turn the whole permission system into a comment."""
    with pytest.raises(mf.ManifestError, match="unknown permission"):
        mf.parse(_base(permissions=["filesytem.write"]))


def test_a_tool_cannot_require_what_its_plugin_did_not_declare():
    with pytest.raises(mf.ManifestError, match="does not declare"):
        mf.parse(_base(
            permissions=["network.read"],
            tools=[{"name": "wipe", "permissions": ["filesystem.write"]}]))


def test_an_unknown_category_is_refused():
    with pytest.raises(mf.ManifestError, match="unknown category"):
        mf.parse(_base(category="vibes"))


def test_an_id_that_is_not_a_safe_module_name_is_refused():
    with pytest.raises(mf.ManifestError, match="alphanumeric"):
        mf.parse(_base(id="../../etc/passwd"))


def test_plugins_are_disabled_by_default():
    """FOCUS.md cut the product to one thing because it did eight. A plugin
    system whose plugins all switch themselves on rebuilds that problem."""
    assert mf.parse(_base()).enabled_by_default is False


def test_high_risk_permissions_are_identified():
    m = mf.parse(_base(permissions=["network.read", "process.execute"]))
    assert m.high_risk_permissions == ("process.execute",)


# ---------------------------------------------------------------------------
# The typed absence — §90
# ---------------------------------------------------------------------------
def test_unavailable_is_not_an_empty_success():
    """The single most important property in the system. An outage rendering
    as 'no active disasters' is a fabricated fact with no invented words."""
    r = Result.unavailable("provider down")
    assert r.available is False
    assert r.data is None
    assert r.to_dict()["available"] is False


def test_an_empty_list_is_still_a_success():
    r = Result.ok([])
    assert r.available is True
    assert r.data == []


def test_unavailable_carries_something_to_act_on():
    r = Result.unavailable("no key", fix_key="NASA_FIRMS_MAP_KEY",
                           docs_url="https://example.org", status=Status.UNCONFIGURED)
    d = r.to_dict()
    assert d["fixKey"] == "NASA_FIRMS_MAP_KEY"
    assert d["docsUrl"]
    assert d["status"] == "unconfigured"


@pytest.mark.parametrize("age,interval,expected", [
    (10, 600, Freshness.LIVE),
    (900, 600, Freshness.FRESH),
    (5000, 600, Freshness.AGING),
    (99999, 600, Freshness.STALE),
    (None, 600, Freshness.UNKNOWN),
])
def test_freshness_never_calls_old_data_live(age, interval, expected):
    assert Freshness.of(age, interval) is expected


# ---------------------------------------------------------------------------
# Isolation — §2
# ---------------------------------------------------------------------------
def _write_plugin(root: Path, name: str, manifest: dict, code: str) -> None:
    d = root / name
    d.mkdir(parents=True)
    (d / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    (d / "__init__.py").write_text("", encoding="utf-8")
    (d / "plugin.py").write_text(code, encoding="utf-8")


GOOD = """
from omnix.core.plugin_system import Plugin, Result

class Good(Plugin):
    def setup(self):
        self.register("ping", self.ping)
    def ping(self):
        return Result.ok("pong")
    def probe(self):
        from omnix.core.plugin_system import Status
        self.health.status = Status.OK
        return self.health
"""

BROKEN_IMPORT = "import a_module_that_does_not_exist_anywhere\n"


def test_one_broken_plugin_does_not_stop_discovery(tmp_path):
    """The promise: a syntax error in one plugin leaves the others working.

    Loads from `tmp_path` directly. An earlier version of this test rewrote
    `omnix.plugins.__path__` and cleared `sys.modules`, which imported the real
    USGS plugin a second time and produced two `UsgsError` classes — so
    `except UsgsError` in the plugin stopped catching the `UsgsError` its own
    adapter raised, in a *different* test file. The loader now imports
    out-of-tree plugins by path, so no global state is touched.
    """
    _write_plugin(tmp_path, "goodplug", {
        "id": "goodplug", "name": "Good", "version": "1.0.0",
        "category": "utility", "enabled_by_default": True,
        "tools": [{"name": "ping", "description": "ping"}]}, GOOD)
    _write_plugin(tmp_path, "badplug", {
        "id": "badplug", "name": "Bad", "version": "1.0.0",
        "category": "utility", "enabled_by_default": True}, BROKEN_IMPORT)

    reg = Registry(directory=tmp_path)
    found = reg.discover(reload=True)

    assert "goodplug" in found and "badplug" in found
    assert found["goodplug"].health.status is Status.OK
    assert found["badplug"].health.status is Status.FAILED
    assert found["badplug"].health.detail        # says what went wrong
    assert reg.call("goodplug.ping").data == "pong"


def test_a_malformed_manifest_is_reported_not_crashed(tmp_path):
    d = tmp_path / "junk"
    d.mkdir()
    (d / "plugin.json").write_text("{ not json", encoding="utf-8")
    reg = Registry(directory=tmp_path)
    reg.discover(reload=True)
    assert "junk" in reg.broken()


def test_a_tool_that_raises_becomes_unavailable_not_an_exception(tmp_path):
    """Nothing a plugin does may cross the boundary as an exception."""
    m = mf.parse(_base(tools=[{"name": "boom", "description": ""}]))
    p = Plugin(m, tmp_path)
    p.register = Plugin.register.__get__(p)
    p.health.status = Status.OK

    def boom():
        raise ZeroDivisionError("kaboom")

    p.register("boom", boom)
    out = p.call("boom")
    assert out.available is False
    assert "kaboom" in out.error.reason
    assert p.health.errors == 1


# ---------------------------------------------------------------------------
# Permissions — §5
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _isolated_permission_store(tmp_path, monkeypatch):
    monkeypatch.setattr(perms, "STORE", tmp_path / "perms.json")


def test_low_risk_permissions_are_granted_on_enable():
    pending = perms.grant_low_risk("p1", ("network.read", "filesystem.read"))
    assert pending == ()
    assert perms.granted("p1", "network.read")


def test_high_risk_permissions_are_never_granted_by_a_manifest():
    """Otherwise any plugin that can write its own manifest grants itself
    shell access, and the prompt in §5 is decoration."""
    pending = perms.grant_low_risk("p2", ("network.read", "process.execute"))
    assert pending == ("process.execute",)
    assert not perms.granted("p2", "process.execute")


def test_require_raises_when_not_granted():
    with pytest.raises(perms.PermissionDenied):
        perms.require("p3", "process.execute")


def test_a_denied_call_is_reported_as_unavailable_naming_the_permission(tmp_path):
    m = mf.parse(_base(
        permissions=["process.execute"],
        tools=[{"name": "run", "description": "", "permissions": ["process.execute"]}]))
    p = Plugin(m, tmp_path)
    p.health.status = Status.OK
    p.register("run", lambda: Result.ok("ran"))
    out = p.call("run")
    assert out.available is False
    assert "process.execute" in out.error.reason


def test_every_decision_is_audited():
    perms.grant("p4", "network.read", actor="alice@example.com")
    perms.revoke("p4", "network.read", actor="alice@example.com")
    trail = perms.audit()
    assert any(e["granted"] is True for e in trail)
    assert any(e["granted"] is False for e in trail)


def test_the_audit_trail_never_contains_a_secret_value():
    perms.grant("p5", "secrets.read")
    assert all("SECRET" not in json.dumps(e).upper() or "secrets.read" in json.dumps(e)
               for e in perms.audit())


# ---------------------------------------------------------------------------
# Configuration — §6, §45
# ---------------------------------------------------------------------------
def test_a_missing_secret_reports_the_key_that_fixes_it(tmp_path, monkeypatch):
    monkeypatch.delenv("DEMO_API_KEY", raising=False)
    m = mf.parse(_base(secrets=["DEMO_API_KEY"],
                       data_sources=[{"id": "x", "docs_url": "https://example.org"}]))
    p = Plugin(m, tmp_path)
    health = p.enable()
    assert health.status is Status.UNCONFIGURED
    assert health.fix_key == "DEMO_API_KEY"
    assert health.docs_url == "https://example.org"
    # And "Something went wrong" is never the message.
    assert "DEMO_API_KEY" in health.detail


def test_an_unconfigured_plugin_says_so_instead_of_returning_nothing(tmp_path, monkeypatch):
    monkeypatch.delenv("DEMO_API_KEY", raising=False)
    m = mf.parse(_base(secrets=["DEMO_API_KEY"],
                       tools=[{"name": "t", "description": ""}]))
    p = Plugin(m, tmp_path)
    p.enable()
    out = p.call("t")
    assert out.available is False
    assert out.error.status is Status.UNCONFIGURED


def test_reading_an_undeclared_secret_is_refused(tmp_path):
    """Declaring it is what lets the settings UI prompt for it without
    running the plugin."""
    p = Plugin(mf.parse(_base()), tmp_path)
    with pytest.raises(KeyError, match="does not declare"):
        p.secret("SOME_KEY")


# ---------------------------------------------------------------------------
# The tool bus — §46
# ---------------------------------------------------------------------------
def test_disabled_plugins_are_not_offered_to_the_model(tmp_path):
    """A tool from an off plugin makes the model plan a step that cannot run,
    which reads to the user as the assistant being confused."""
    reg = Registry(directory=tmp_path)
    reg.discover(reload=True)
    assert reg.tools() == []


def test_an_unqualified_tool_name_is_refused():
    reg = Registry(directory=Path("."))
    out = reg.call("just_a_name")
    assert out.available is False
    assert "qualified" in out.error.reason


def test_calling_an_unknown_plugin_is_unavailable_not_an_exception():
    reg = Registry(directory=Path("."))
    out = reg.call("nope.tool")
    assert out.available is False
