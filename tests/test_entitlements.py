"""Plan limits — that an allowance is real, and that it is spent honestly.

`User.plan` was a dead column: declared `free | pro` with the comment
"Entitlements read this; billing writes it", and nothing did either. These
tests are what make it load-bearing.

The important design property under test is that usage is **derived from the
ledger**, not incremented into a counter. A counter drifts — a run that dies
between the model call and the rollup leaves the two disagreeing, and the one
that reflects money actually spent is the ledger. So the assertions here write
real `Execution` and `ModelCall` rows and expect the limit to notice.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from omnix.core import entitlements, identity, workspace as workspace_mod
from omnix.core.db import session
from omnix.core.entitlements import PLANS, QuotaExceeded
from omnix.core.schema import Execution, ModelCall, User


def _user(email: str, plan: str) -> str:
    with identity.acting_as(email):
        uid = workspace_mod.acting_user()
    with session() as s:
        s.get(User, uid).plan = plan
    return uid


def _space(uid: str, name: str = "S") -> str:
    return workspace_mod.create(uid, name, "")["id"]


def _runs(ws: str, n: int) -> None:
    with session() as s:
        for _ in range(n):
            s.add(Execution(workspace_id=ws, agent="oracle", status="completed"))


def _spend(ws: str, usd: float) -> None:
    with session() as s:
        s.add(ModelCall(workspace_id=ws, agent="oracle", provider="test",
                        model="test", cost_usd=usd, status="ok"))


# ---------------------------------------------------------------------------
# Reading the plan
# ---------------------------------------------------------------------------
def test_an_unknown_plan_falls_back_to_free_not_open():
    """A typo in a billing webhook must not hand someone Ultra."""
    uid = _user("typo@example.com", "PROO")
    assert entitlements.plan_for(uid).key == "free"


def test_the_local_account_is_never_metered():
    """The desktop install and this suite run as it; a quota there protects
    nobody and would only break the single-user story."""
    assert entitlements.plan_for(workspace_mod.default_user()).key == "unlimited"


def test_plan_is_read_from_the_column_billing_writes():
    uid = _user("payer@example.com", "pro")
    assert entitlements.plan_for(uid).key == "pro"


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------
def test_runs_under_the_limit_are_allowed():
    uid = _user("light@example.com", "starter")
    _runs(_space(uid), 5)
    entitlements.check_run(uid)          # must not raise


def test_runs_at_the_limit_are_refused():
    uid = _user("heavy@example.com", "starter")
    _runs(_space(uid), PLANS["starter"].runs)
    with pytest.raises(QuotaExceeded) as e:
        entitlements.check_run(uid)
    assert e.value.metric == "runs"


def test_runs_are_counted_across_all_of_a_users_spaces():
    """The hole a per-workspace counter leaves: split the work over three
    Spaces and each one is under the limit while the account is over it."""
    uid = _user("spread@example.com", "starter")
    half = PLANS["starter"].runs // 2
    _runs(_space(uid, "A"), half)
    _runs(_space(uid, "B"), half + 1)
    with pytest.raises(QuotaExceeded):
        entitlements.check_run(uid)


def test_one_users_runs_do_not_count_against_another():
    a = _user("mine@example.com", "starter")
    b = _user("theirs@example.com", "starter")
    _runs(_space(b), PLANS["starter"].runs + 10)
    entitlements.check_run(a)            # must not raise


# ---------------------------------------------------------------------------
# The spending ceiling
# ---------------------------------------------------------------------------
def test_the_ceiling_stops_an_account_that_is_under_its_run_limit():
    """The case the run allowance cannot model — a retry storm or a bug burns
    money without burning runs. This is the backstop that catches it."""
    uid = _user("runaway@example.com", "starter")
    ws = _space(uid)
    _runs(ws, 2)
    _spend(ws, PLANS["starter"].max_spend_usd + 0.01)
    with pytest.raises(QuotaExceeded) as e:
        entitlements.check_run(uid)
    assert e.value.metric == "spend"


def test_the_ceiling_sits_above_what_the_allowance_can_legitimately_cost():
    """If a customer could exhaust their runs and trip the ceiling, the ceiling
    would be refusing people for using what they paid for."""
    for key in ("starter", "pro", "ultra"):
        p = PLANS[key]
        legitimate = p.runs * 0.02 + p.credits * 0.05
        assert p.max_spend_usd > legitimate, f"{key} ceiling is below its own allowance"


# ---------------------------------------------------------------------------
# Trial expiry
# ---------------------------------------------------------------------------
def test_an_expired_trial_is_refused_before_any_other_check():
    uid = _user("lapsed@example.com", "free")
    with session() as s:
        s.get(User, uid).created_at = (
            datetime.now(timezone.utc).replace(tzinfo=None)
            - timedelta(days=PLANS["free"].trial_days + 1))
    with pytest.raises(QuotaExceeded) as e:
        entitlements.check_run(uid)
    assert e.value.metric == "trial"


def test_a_fresh_trial_is_not_expired():
    assert not entitlements.trial_expired(_user("new@example.com", "free"))


def test_a_paid_plan_never_expires():
    uid = _user("subscriber@example.com", "pro")
    with session() as s:
        s.get(User, uid).created_at = (
            datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=900))
    assert not entitlements.trial_expired(uid)


# ---------------------------------------------------------------------------
# Spaces
# ---------------------------------------------------------------------------
def test_the_space_limit_is_enforced():
    uid = _user("hoarder@example.com", "free")
    _space(uid, "only one")
    with pytest.raises(QuotaExceeded) as e:
        entitlements.check_new_space(uid)
    assert e.value.metric == "spaces"


def test_an_unlimited_plan_has_no_space_limit():
    uid = _user("bigco@example.com", "ultra")
    for i in range(6):
        _space(uid, f"S{i}")
    entitlements.check_new_space(uid)     # must not raise


# ---------------------------------------------------------------------------
# The report the paywall reads
# ---------------------------------------------------------------------------
def test_the_report_is_readable_before_anything_is_refused():
    """A plan screen that only works once you are blocked means the user finds
    the limit by hitting it."""
    uid = _user("curious@example.com", "pro")
    r = entitlements.report(uid)
    assert r["plan"] == "pro"
    assert r["runs"]["limit"] == PLANS["pro"].runs
    assert r["runs"]["used"] == 0
    assert "deep_verify" in r["features"]


def test_the_refusal_says_which_allowance_ran_out():
    """A generic 'upgrade' prompt cannot tell the user what to change."""
    uid = _user("informative@example.com", "starter")
    _runs(_space(uid), PLANS["starter"].runs)
    with pytest.raises(QuotaExceeded) as e:
        entitlements.check_run(uid)
    p = e.value.payload()
    assert p["quotaExceeded"] is True
    assert p["metric"] == "runs"
    assert p["limit"] == float(PLANS["starter"].runs)
    assert p["plan"] == "starter"
