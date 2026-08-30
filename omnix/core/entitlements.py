"""What a plan allows, and whether this user has spent it.

WHY USAGE IS DERIVED RATHER THAN COUNTED
----------------------------------------
`UsageCounter` exists and is keyed by **workspace**, but a plan belongs to a
**user** and a user owns many workspaces — so a per-workspace counter cannot
answer "has this account used its 500 runs" without summing across Spaces
anyway. Worse, a counter is a second copy of a number: if an increment is
missed because a run crashed between the model call and the rollup, the
counter and the ledger disagree, and the ledger is the one that reflects money
actually spent.

So enforcement reads the ledger. `ModelCall` already records tokens, cost and
timestamp per request, and `Execution` records runs, both scoped by workspace.
Joining either to `Workspace.user_id` gives a per-user total that is true by
construction. `UsageCounter` is still maintained as a rollup for PULSE, where a
fast approximate number is fine and a join per dashboard tile is not — but
nothing is *enforced* from it.

THE TWO METERS
--------------
Runs and credits are counted separately because their costs differ by about
15×. A research run on open weights is roughly $0.02; the same run with
frontier extraction and verification is roughly $0.30. Folding them into one
allowance means either pricing every run as if it were frontier, or letting a
handful of frontier runs consume a month's margin.

THE CEILING IS NOT THE ALLOWANCE
--------------------------------
`max_spend_usd` is deliberately higher than what the allowance can legitimately
cost, and it is not a product promise — it is the backstop for the case the
allowance cannot model: a bug, a retry storm, an adversarial account. The
allowance is a promise to the customer; the ceiling is a promise to the person
paying the provider bill.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from .db import session
from .schema import Execution, ModelCall, User, Workspace


# ---------------------------------------------------------------------------
# Plans
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Plan:
    key: str
    label: str
    # Runs on open weights. None means unmetered.
    runs: int | None
    # Frontier credits. 1 credit == $0.05 of frontier inference at cost.
    credits: int
    # None means unlimited.
    spaces: int | None
    # The backstop, in dollars of measured provider cost per period.
    max_spend_usd: float
    # "month" resets on the 1st; "lifetime" never resets — the free trial is an
    # allowance for the whole trial, not a monthly gift, because a monthly one
    # is a free tier with extra steps.
    period: str = "month"
    # Days from signup before the plan stops granting. 0 means no expiry.
    trial_days: int = 0
    features: frozenset[str] = field(default_factory=frozenset)


_ALL = frozenset({"export", "tracking", "byok", "api", "deep_verify",
                  "documents", "daily_brief"})

PLANS: dict[str, Plan] = {
    "free": Plan(
        key="free", label="Free", runs=15, credits=3, spaces=1,
        max_spend_usd=1.00, period="lifetime", trial_days=30,
        features=frozenset({"deep_verify"}),
    ),
    "starter": Plan(
        key="starter", label="Starter", runs=100, credits=30, spaces=3,
        max_spend_usd=6.00,
        features=frozenset({"export", "tracking", "byok", "deep_verify"}),
    ),
    "pro": Plan(
        key="pro", label="Pro", runs=500, credits=120, spaces=None,
        max_spend_usd=25.00,
        features=_ALL - {"api", "documents"},
    ),
    "ultra": Plan(
        key="ultra", label="Ultra", runs=2000, credits=500, spaces=None,
        max_spend_usd=100.00, features=_ALL,
    ),
    # Not for sale. The local single-user install and the test suite run as
    # this, so a developer never trips a quota that only exists to protect a
    # hosted deployment's card.
    "unlimited": Plan(
        key="unlimited", label="Unlimited", runs=None, credits=10_000,
        spaces=None, max_spend_usd=1e9, features=_ALL,
    ),
}

# `User.plan` defaults to "free" and billing writes it. An unrecognised value
# must not fail open onto a paid tier.
DEFAULT_PLAN = "free"


class QuotaExceeded(RuntimeError):
    """The account has spent an allowance. Carries what, and what to do."""

    def __init__(self, reason: str, *, limit: float, used: float,
                 metric: str, plan: str):
        super().__init__(reason)
        self.reason = reason
        self.limit = limit
        self.used = used
        self.metric = metric
        self.plan = plan

    def payload(self) -> dict:
        return {"error": self.reason, "quotaExceeded": True,
                "metric": self.metric, "limit": self.limit,
                "used": self.used, "plan": self.plan}


# ---------------------------------------------------------------------------
# Reading the plan
# ---------------------------------------------------------------------------
def plan_for(user_id: str) -> Plan:
    """The user's plan, falling back to free for anything unrecognised.

    `local@omnix.local` is the single-user install and is deliberately
    unmetered — it was seeded with plan="pro" before these tiers existed, and
    quotas on a desktop build protect nobody.
    """
    from .workspace import LOCAL_EMAIL

    with session() as s:
        u = s.get(User, user_id)
        if u is None:
            return PLANS[DEFAULT_PLAN]
        if u.email == LOCAL_EMAIL:
            return PLANS["unlimited"]
        return PLANS.get((u.plan or "").strip().lower(), PLANS[DEFAULT_PLAN])


def period_start(plan: Plan, user_id: str) -> datetime:
    """The instant the current allowance window opened.

    Naive UTC, because SQLite stores these columns without an offset and a
    tz-aware bound value would be compared against naive strings — the same
    trap `core/schema.py` documents for `iso()`.
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if plan.period == "lifetime":
        with session() as s:
            u = s.get(User, user_id)
            created = getattr(u, "created_at", None) if u else None
        return created or (now - timedelta(days=365))
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def trial_expired(user_id: str) -> bool:
    """Whether a time-limited plan has run out of days."""
    plan = plan_for(user_id)
    if not plan.trial_days:
        return False
    with session() as s:
        u = s.get(User, user_id)
        created = getattr(u, "created_at", None) if u else None
    if created is None:
        return False
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    return (now - created) > timedelta(days=plan.trial_days)


# ---------------------------------------------------------------------------
# Reading usage — from the ledger, never from a counter
# ---------------------------------------------------------------------------
def usage(user_id: str) -> dict:
    """Runs, measured spend and Space count for the current window."""
    plan = plan_for(user_id)
    since = period_start(plan, user_id)

    with session() as s:
        ws_ids = [w for (w,) in s.execute(
            select(Workspace.id).where(Workspace.user_id == user_id))]
        if not ws_ids:
            return {"plan": plan.key, "runs": 0, "spendUsd": 0.0,
                    "spaces": 0, "since": since.isoformat()}

        runs = int(s.scalar(
            select(func.count()).select_from(Execution)
            .where(Execution.workspace_id.in_(ws_ids),
                   Execution.created_at >= since)) or 0)
        spend = float(s.scalar(
            select(func.coalesce(func.sum(ModelCall.cost_usd), 0.0))
            .where(ModelCall.workspace_id.in_(ws_ids),
                   ModelCall.ts >= since)) or 0.0)

    return {"plan": plan.key, "runs": runs, "spendUsd": round(spend, 6),
            "spaces": len(ws_ids), "since": since.isoformat()}


def report(user_id: str) -> dict:
    """Everything a settings screen or a paywall needs, in one call."""
    plan = plan_for(user_id)
    used = usage(user_id)
    return {
        "plan": plan.key,
        "planLabel": plan.label,
        "period": plan.period,
        "since": used["since"],
        "trialExpired": trial_expired(user_id),
        "features": sorted(plan.features),
        "runs": {"used": used["runs"], "limit": plan.runs},
        "spaces": {"used": used["spaces"], "limit": plan.spaces},
        "credits": {"limit": plan.credits},
        "spendUsd": {"used": used["spendUsd"], "limit": plan.max_spend_usd},
    }


# ---------------------------------------------------------------------------
# Enforcement
# ---------------------------------------------------------------------------
def check_run(user_id: str) -> None:
    """Raise :class:`QuotaExceeded` if this account may not start a run.

    Called before work begins rather than after, because the point is to avoid
    spending the provider's tokens — a check that runs afterwards has already
    paid for the thing it was meant to prevent.
    """
    plan = plan_for(user_id)
    if trial_expired(user_id):
        raise QuotaExceeded(
            "Your free trial has ended. Choose a plan to keep researching.",
            limit=float(plan.trial_days), used=float(plan.trial_days),
            metric="trial", plan=plan.key)

    used = usage(user_id)

    # The ceiling is checked first and reported separately: hitting it means
    # something is wrong, not that the customer used what they bought, and
    # telling them "you have used 12 of 500 runs" while refusing them would be
    # incomprehensible.
    if used["spendUsd"] >= plan.max_spend_usd:
        raise QuotaExceeded(
            "This account has reached its spending limit for the period. "
            "Contact support — this usually means something is retrying.",
            limit=plan.max_spend_usd, used=used["spendUsd"],
            metric="spend", plan=plan.key)

    if plan.runs is not None and used["runs"] >= plan.runs:
        window = "so far" if plan.period == "lifetime" else "this month"
        raise QuotaExceeded(
            f"You have used all {plan.runs} runs {window}. "
            f"Upgrade for more, or wait for the next period.",
            limit=float(plan.runs), used=float(used["runs"]),
            metric="runs", plan=plan.key)


def check_new_space(user_id: str) -> None:
    plan = plan_for(user_id)
    if plan.spaces is None:
        return
    used = usage(user_id)
    if used["spaces"] >= plan.spaces:
        raise QuotaExceeded(
            f"Your plan includes {plan.spaces} "
            f"Space{'s' if plan.spaces != 1 else ''}.",
            limit=float(plan.spaces), used=float(used["spaces"]),
            metric="spaces", plan=plan.key)


def has_feature(user_id: str, feature: str) -> bool:
    return feature in plan_for(user_id).features
