"""Phase 0 hardening: rate limits, mail, the OpenRouter backend, demo fencing.

Each of these protects something that only fails in production: a provider bill,
a locked-out customer, a silent fallback, and a demo login on a paying
deployment. None of them is exercised by ordinary use, which is exactly why
they need tests.
"""

from __future__ import annotations

import os

import httpx
import pytest

from omnix import openrouter_client as orc
from omnix.core import mail
from omnix.core.ratelimit import Limit, Limiter, bucket_for


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------
def test_research_routes_are_limited_harder_than_reads():
    """A read is cheap; a research run spends provider tokens. One limit for
    both is either uselessly loose or breaks normal navigation."""
    from omnix.core.ratelimit import LIMITS
    assert LIMITS["research"].count < LIMITS["read"].count


@pytest.mark.parametrize("path,method,expected", [
    ("/api/research/run", "POST", "research"),
    ("/api/nova/research", "POST", "research"),
    ("/api/agents/x/run", "POST", "agents"),
    ("/api/objects", "GET", "read"),
    ("/api/objects", "POST", "write"),
    ("/api/workspaces", "GET", "read"),
])
def test_routes_map_to_the_right_bucket(path, method, expected):
    assert bucket_for(path, method) == expected


def test_the_first_request_is_never_refused():
    """A cold start that answers 429 looks like an outage."""
    lim = Limiter({"k": Limit(3, 60)})
    allowed, _ = lim.check("someone", "k")
    assert allowed is True


def test_a_burst_beyond_the_limit_is_refused():
    lim = Limiter({"k": Limit(3, 60)})
    results = [lim.check("someone", "k")[0] for _ in range(5)]
    assert results[:3] == [True, True, True]
    assert results[3:] == [False, False]


def test_a_refusal_says_how_long_to_wait():
    """Without this the client can only guess, and guessing means retrying
    immediately — which is the behaviour the limit exists to stop."""
    lim = Limiter({"k": Limit(1, 60)})
    lim.check("someone", "k")
    allowed, retry_after = lim.check("someone", "k")
    assert allowed is False
    assert retry_after > 0


def test_one_account_cannot_exhaust_anothers_allowance():
    lim = Limiter({"k": Limit(2, 60)})
    for _ in range(5):
        lim.check("noisy@example.com", "k")
    assert lim.check("quiet@example.com", "k")[0] is True


def test_buckets_refill_over_time():
    """A fixed window would let a caller spend the whole allowance either side
    of the boundary — twice the intended rate."""
    lim = Limiter({"k": Limit(60, 60)})   # one per second
    lim.check("someone", "k")
    b = lim._buckets[("someone", "k")]
    b.tokens = 0.0
    b.updated -= 5                        # pretend five seconds passed
    assert lim.check("someone", "k")[0] is True


def test_abandoned_buckets_are_actually_collected():
    """The sweep exists to stop the bucket dict growing one entry per caller.

    It did not work. `_maybe_sweep` asked `b.tokens >= b.capacity`, and
    `tokens` is only recomputed inside `take()` — so for anyone who sent a
    request and left, the stored figure stayed one token below capacity for
    ever and the bucket was never eligible. On a public deployment that is an
    unbounded allocation controlled by whoever wants to send requests, which is
    precisely what the docstring claims to prevent.
    """
    lim = Limiter({"k": Limit(600, 60)})
    for i in range(500):
        lim.check("caller-%d" % i, "k")
    assert len(lim._buckets) == 500

    # Idle long past both thresholds (300s sweep cadence, 600s idle) — every
    # one of these has refilled to capacity many times over in real terms.
    for b in lim._buckets.values():
        b.updated -= 100_000
    lim._last_sweep -= 100_000

    lim.check("someone-new", "k")           # drives the sweep
    assert len(lim._buckets) == 1, (
        "abandoned buckets survived a sweep: %d left" % len(lim._buckets))


def test_a_bucket_still_draining_survives_the_sweep():
    """The sweep must not hand back an allowance the caller has not earned."""
    lim = Limiter({"k": Limit(6, 3600)})    # slow refill: 6 per hour
    for _ in range(6):
        lim.check("heavy", "k")
    assert lim.check("heavy", "k")[0] is False

    b = lim._buckets[("heavy", "k")]
    b.updated -= 700                        # idle past the threshold...
    lim._last_sweep -= 400
    lim.check("someone-else", "k")          # ...and sweep

    # 700s at 6/3600 per second is ~1.2 tokens: nowhere near full, so the
    # bucket must still be there holding the caller to their limit.
    assert ("heavy", "k") in lim._buckets


def test_an_unknown_bucket_is_not_silently_unlimited():
    """A route that maps to no bucket must still land somewhere real."""
    assert bucket_for("/api/something/new", "POST") == "write"
    assert bucket_for("/api/something/new", "GET") == "read"


# ---------------------------------------------------------------------------
# Mail
# ---------------------------------------------------------------------------
def test_the_file_backend_actually_writes_a_message(tmp_path, monkeypatch):
    monkeypatch.setenv("OMNIX_MAIL_BACKEND", "file")
    monkeypatch.setenv("OMNIX_MAIL_DIR", str(tmp_path))
    sent = mail.send("someone@example.com", "Subject here", "Body here")
    assert sent.ok
    files = list(tmp_path.glob("*.eml"))
    assert len(files) == 1
    body = files[0].read_text(encoding="utf-8")
    assert "Subject here" in body and "someone@example.com" in body


def test_console_and_file_report_that_nothing_was_delivered(tmp_path, monkeypatch):
    """The distinction that matters. A rendered message is not a sent one, and
    conflating them is how a locked-out customer waits for mail that never
    existed."""
    monkeypatch.setenv("OMNIX_MAIL_BACKEND", "file")
    monkeypatch.setenv("OMNIX_MAIL_DIR", str(tmp_path))
    assert mail.send("a@b.com", "s", "t").delivered is False

    monkeypatch.setenv("OMNIX_MAIL_BACKEND", "console")
    assert mail.send("a@b.com", "s", "t").delivered is False


def test_an_invalid_address_is_refused_rather_than_sent():
    assert mail.send("not-an-address", "s", "t").ok is False


def test_smtp_without_credentials_says_which_are_missing(monkeypatch):
    monkeypatch.setenv("OMNIX_MAIL_BACKEND", "smtp")
    for k in ("OMNIX_SMTP_HOST", "OMNIX_SMTP_USER", "OMNIX_SMTP_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    ok, fix = mail.configured()
    assert ok is False
    assert "OMNIX_SMTP_HOST" in fix


def test_the_reset_email_contains_the_link(tmp_path, monkeypatch):
    """Parsed rather than string-matched.

    The raw .eml is quoted-printable, so the link appears as `reset=3Dabc` —
    grepping the file would fail on a message that is perfectly correct, and
    would equally pass on one whose MIME a mail client could not read. Decoding
    it asserts what the recipient actually sees.
    """
    import email
    import email.policy

    monkeypatch.setenv("OMNIX_MAIL_BACKEND", "file")
    monkeypatch.setenv("OMNIX_MAIL_DIR", str(tmp_path))
    mail.send_password_reset("user@example.com", "https://omnix.app/login?reset=abc")

    raw = next(tmp_path.glob("*.eml")).read_text(encoding="utf-8")
    msg = email.message_from_string(raw, policy=email.policy.default)
    assert msg["To"] == "user@example.com"
    assert "reset" in msg["Subject"].lower()
    assert "https://omnix.app/login?reset=abc" in msg.get_content()


def test_there_is_no_backend_that_silently_discards(monkeypatch):
    """A send that vanishes is indistinguishable from one that worked."""
    monkeypatch.setenv("OMNIX_MAIL_BACKEND", "nonsense")
    # Unknown values fall back to a real backend, never to a no-op.
    assert mail.backend() in ("console", "smtp")


# ---------------------------------------------------------------------------
# OpenRouter
# ---------------------------------------------------------------------------
def test_unconfigured_reports_the_variable_to_set(monkeypatch, tmp_path):
    monkeypatch.delenv(orc.KEY_ENV, raising=False)
    monkeypatch.setattr(orc, "_SECRETS", tmp_path / "none.json")
    st = orc.status()
    assert st["configured"] is False
    assert st["fixKey"] == "OPENROUTER_API_KEY"
    assert st["docsUrl"]


def test_status_never_exposes_the_key(monkeypatch):
    monkeypatch.setenv(orc.KEY_ENV, "sk-or-v1-supersecretvalue")
    st = orc.status()
    assert "supersecretvalue" not in repr(st)
    assert st["keyFingerprint"].endswith("alue")


def test_calling_without_a_key_raises_rather_than_falling_back(monkeypatch, tmp_path):
    """A silent fallback to another provider makes cost and quality
    unreasonable-about."""
    monkeypatch.delenv(orc.KEY_ENV, raising=False)
    monkeypatch.setattr(orc, "_SECRETS", tmp_path / "none.json")
    with pytest.raises(orc.OpenRouterError, match="not configured"):
        orc._headers()


@pytest.mark.parametrize("code,fatal", [
    (400, True),    # unknown model — retrying re-discovers the same 404
    (404, True),
    (401, True),    # bad key — every model will fail identically
    (402, True),    # out of credit — same
    (429, False),   # rate limited — a later rung or a retry may work
    (503, False),
])
def test_errors_are_classified_as_retryable_or_terminal(code, fatal, monkeypatch):
    monkeypatch.setenv(orc.KEY_ENV, "sk-test")
    resp = httpx.Response(code, text="nope", request=httpx.Request("POST", "http://x"))
    with pytest.raises(orc.OpenRouterError) as e:
        orc._raise_for_status(resp, "some/model")
    assert isinstance(e.value, orc.FatalModelError) is fatal


def test_the_referer_header_is_the_operators_identity_not_a_users(monkeypatch):
    monkeypatch.setenv(orc.KEY_ENV, "sk-test")
    monkeypatch.setenv("OMNIX_PUBLIC_URL", "https://omnix.example")
    h = orc._headers()
    assert h["HTTP-Referer"] == "https://omnix.example"
    assert h["Authorization"].startswith("Bearer ")


# ---------------------------------------------------------------------------
# Demo mode fencing
# ---------------------------------------------------------------------------
def test_demo_mode_refuses_to_start_when_billing_is_configured(monkeypatch):
    """Demo accepts any password. A warning in a log nobody reads is how that
    reaches production; a process that will not boot is noticed in one deploy."""
    from omnix import server

    monkeypatch.setenv("OMNIX_AUTH", "demo")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_live_x")
    with pytest.raises(SystemExit):
        server._refuse_demo_with_billing()


def test_demo_mode_is_allowed_without_billing(monkeypatch):
    from omnix import server

    monkeypatch.setenv("OMNIX_AUTH", "demo")
    for k in server._BILLING_KEYS:
        monkeypatch.delenv(k, raising=False)
    server._refuse_demo_with_billing()      # must not raise


def test_normal_auth_is_unaffected_by_billing_keys(monkeypatch):
    from omnix import server

    monkeypatch.setenv("OMNIX_AUTH", "on")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_live_x")
    server._refuse_demo_with_billing()      # must not raise


def test_auth_defaults_to_on(monkeypatch):
    """A security control whose default is off is a comment."""
    from omnix import auth
    monkeypatch.delenv("OMNIX_AUTH", raising=False)
    assert auth.enabled() is True


def test_auth_routes_are_rate_limited_per_address():
    """`auth.py` throttles per email, which an attacker walking a list of
    addresses never trips. This is the only per-address brake on stuffing."""
    assert bucket_for("/api/auth/login", "POST") == "auth"
    from omnix.core.ratelimit import LIMITS
    assert "auth" in LIMITS


def test_unauthenticated_requests_are_still_metered():
    """The gap this closes: limiting only authenticated traffic leaves the 401
    path unmetered, so a caller with no credentials is the one who can hammer
    the server freely."""
    import inspect

    from omnix import server

    src = inspect.getsource(server._require_session)
    limit_at = src.index("_ratelimit.shared().check")
    unauth_401 = src.index('"authRequired": True')
    assert limit_at < unauth_401, (
        "the rate-limit check must run before the 401 return, or "
        "unauthenticated traffic bypasses throttling entirely")
