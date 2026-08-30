"""Demo mode: the gate on screen, unlocked underneath.

`OMNIX_AUTH=demo` lets the lock screen be *shown* — in a walkthrough, a
recording, a pitch — without a mistyped password ending the demo. It is not a
weaker policy, it is no policy, so the tests that matter here are the ones
about how far the hole goes:

  * it cannot be reached by accident (`on`/`off` are unchanged, and an invite
    code — the same marker `/api/auth/forgot` uses for "this looks hosted" —
    turns it off);
  * it opens sign-in and nothing else (changing a password still costs the
    current one);
  * and it must never write. A wrong password that got rehashed into the store
    would replace the real credential with whatever was typed to bypass it,
    which would turn a demo switch into permanent account damage.
"""

from __future__ import annotations

import pytest

from omnix import auth


@pytest.fixture
def store(tmp_path, monkeypatch):
    """An account store isolated from the developer's real `omnix_auth.json`."""
    monkeypatch.setattr(auth, "STORE", tmp_path / "auth.json")
    monkeypatch.setattr(auth, "_data", None)
    monkeypatch.setattr(auth, "_fails", {}, raising=False)
    monkeypatch.delenv("OMNIX_INVITE_CODE", raising=False)
    monkeypatch.setenv("OMNIX_AUTH", "on")
    auth.create_user("Ada Lovelace", "ada@example.com", "correct-horse-42!")
    return tmp_path


def demo(monkeypatch):
    monkeypatch.setenv("OMNIX_AUTH", "demo")


# ---------------------------------------------------------------------------
# Reaching it
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("value, on", [
    ("on", False), ("", False), ("off", False), ("demo", True), ("DEMO", True),
])
def test_demo_mode_must_be_asked_for_by_name(monkeypatch, value, on):
    monkeypatch.delenv("OMNIX_INVITE_CODE", raising=False)
    monkeypatch.setenv("OMNIX_AUTH", value)
    assert auth.demo_mode() is on


def test_an_invite_code_turns_demo_mode_off(monkeypatch):
    """The marker for "this deployment looks hosted". A real deployment must
    not end up in demo mode by inheriting a stray env var."""
    monkeypatch.setenv("OMNIX_AUTH", "demo")
    monkeypatch.setenv("OMNIX_INVITE_CODE", "let-me-in")
    assert auth.demo_mode() is False


# ---------------------------------------------------------------------------
# What it opens
# ---------------------------------------------------------------------------
def test_wrong_password_is_refused_normally(store):
    with pytest.raises(auth.AuthError):
        auth.verify_password("ada@example.com", "nope")


def test_wrong_password_opens_the_account_in_demo_mode(store, monkeypatch):
    demo(monkeypatch)
    assert auth.verify_password("ada@example.com", "nope")["email"] == \
        "ada@example.com"


def test_an_unknown_address_lands_on_the_stand_in(store, monkeypatch):
    """A typo on stage should still open something rather than reading as a
    broken product."""
    demo(monkeypatch)
    assert auth.verify_password("typo@example.com", "x")["email"] == \
        "ada@example.com"


def test_demo_mode_cannot_invent_a_user(store, monkeypatch):
    """Relaxing the credential check is one thing; conjuring an account out of
    an empty store is a different and much larger one."""
    demo(monkeypatch)
    auth._data = auth._blank()
    with pytest.raises(auth.AuthError):
        auth.verify_password("anyone@example.com", "x")


def test_the_stand_in_is_the_oldest_account(store, monkeypatch):
    auth.create_user("Grace Hopper", "grace@example.com", "another-good-one-9!")
    demo(monkeypatch)
    assert auth.verify_password("nobody@example.com", "x")["email"] == \
        "ada@example.com"


# ---------------------------------------------------------------------------
# What it must not open
# ---------------------------------------------------------------------------
def test_a_bypassed_login_does_not_overwrite_the_password(store, monkeypatch):
    """The regression this file exists for. `verify_password` opportunistically
    rehashes on success; if that ran on a demo-mode bypass it would store the
    wrong password as the real one and lock the account after the demo."""
    demo(monkeypatch)
    auth.verify_password("ada@example.com", "whatever-i-typed")

    monkeypatch.setenv("OMNIX_AUTH", "on")
    auth._data = None                      # force a re-read from disk
    with pytest.raises(auth.AuthError):
        auth.verify_password("ada@example.com", "whatever-i-typed")
    assert auth.verify_password("ada@example.com", "correct-horse-42!")


def test_changing_a_password_still_costs_the_current_one(store, monkeypatch):
    demo(monkeypatch)
    with pytest.raises(auth.AuthError):
        auth.change_password("ada@example.com", "not-it", "a-brand-new-one-7!")
    # And still works with the real one.
    assert auth.change_password(
        "ada@example.com", "correct-horse-42!", "a-brand-new-one-7!")
