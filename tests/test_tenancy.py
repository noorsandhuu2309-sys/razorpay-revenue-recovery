"""Tenant isolation — that one account cannot reach another's Spaces.

WHY THIS FILE EXISTS
--------------------
Authentication and authorisation were separate problems and only the first was
solved. `_require_session` proved who you were, and then the data layer asked
`workspace.default_user()` — a hardcoded local account — so every signed-in
user resolved to the same identity and saw the same Spaces. On top of that,
`resolve()` accepted any workspace id that merely *existed*, so naming someone
else's Space was enough to read and write it.

Both are now closed at one chokepoint, and these tests are what keep them
closed. They assert behaviour, not implementation: each one describes a thing
an attacker would try.

The suite drives `omnix.core.workspace` directly rather than through HTTP,
because the identity that matters is bound on a context variable and the
question under test is whether the data layer honours it. The HTTP rendering of
a refusal (a flat 404) is covered in `test_api.py`.
"""

from __future__ import annotations

import pytest

from omnix.core import identity, workspace as workspace_mod
from omnix.core.workspace import WorkspaceAccessError

ALICE = "alice@example.com"
BOB = "bob@example.com"


@pytest.fixture
def alice_space() -> str:
    with identity.acting_as(ALICE):
        return workspace_mod.create(
            workspace_mod.acting_user(), "Alice research", "")["id"]


@pytest.fixture
def bob_space() -> str:
    with identity.acting_as(BOB):
        return workspace_mod.create(
            workspace_mod.acting_user(), "Bob research", "")["id"]


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------
def test_two_emails_are_two_users():
    """The bug in one line: these used to be the same id."""
    with identity.acting_as(ALICE):
        a = workspace_mod.acting_user()
    with identity.acting_as(BOB):
        b = workspace_mod.acting_user()
    assert a != b


def test_the_same_email_is_the_same_user_across_requests():
    """The bridge must be find-or-create, not create-every-time — otherwise a
    user's second request lands on a new account with none of their Spaces."""
    with identity.acting_as(ALICE):
        first = workspace_mod.acting_user()
    with identity.acting_as(ALICE):
        second = workspace_mod.acting_user()
    assert first == second


def test_email_case_and_padding_do_not_fork_the_account():
    """`Alice@Example.com ` and `alice@example.com` are one person. Treating
    them as two silently strands the second one in an empty account."""
    with identity.acting_as(ALICE):
        canonical = workspace_mod.acting_user()
    with identity.acting_as("  Alice@Example.COM  "):
        assert workspace_mod.acting_user() == canonical


def test_no_identity_falls_back_to_the_local_account():
    """`OMNIX_AUTH=off`, the desktop story, and the rest of this test suite all
    depend on this staying true."""
    assert workspace_mod.acting_user() == workspace_mod.default_user()


# ---------------------------------------------------------------------------
# Isolation
# ---------------------------------------------------------------------------
def test_a_users_space_list_holds_only_their_own(alice_space, bob_space):
    with identity.acting_as(ALICE):
        mine = {w["id"] for w in workspace_mod.list_for(workspace_mod.acting_user())}
    assert alice_space in mine
    assert bob_space not in mine


def test_naming_another_users_space_is_refused(alice_space, bob_space):
    """The IDOR. Bob knows Alice's workspace id — from a shared link, a log, a
    screenshot — and passes it as `?workspace=`. It used to work."""
    with identity.acting_as(BOB):
        with pytest.raises(WorkspaceAccessError):
            workspace_mod.resolve(alice_space)


def test_owning_your_own_space_still_works(alice_space):
    with identity.acting_as(ALICE):
        assert workspace_mod.resolve(alice_space) == alice_space


def test_a_missing_space_and_someone_elses_fail_identically(alice_space):
    """Distinguishing the two would confirm which ids are real, which turns the
    error code into a membership oracle for anyone enumerating them."""
    with identity.acting_as(BOB):
        with pytest.raises(WorkspaceAccessError):
            workspace_mod.resolve(alice_space)
        with pytest.raises(WorkspaceAccessError):
            workspace_mod.resolve("definitely-not-a-real-id")


def test_omitting_the_space_gives_you_your_own_not_someone_elses(alice_space):
    """The fallback path. Bob asks for no Space in particular and must land in
    a Space of his, never in whichever one happened to be created first."""
    with identity.acting_as(BOB):
        got = workspace_mod.resolve(None)
        assert got != alice_space
        assert workspace_mod.owns(workspace_mod.acting_user(), got)


def test_a_new_user_gets_their_own_default_space():
    with identity.acting_as("carol@example.com"):
        uid = workspace_mod.acting_user()
        ws = workspace_mod.default_workspace()
        assert workspace_mod.owns(uid, ws)


# ---------------------------------------------------------------------------
# The context variable itself
# ---------------------------------------------------------------------------
def test_identity_does_not_leak_out_of_its_block():
    """These bind on a shared worker task. A value left behind would be
    inherited by whichever request ran next — a cross-tenant leak that only
    appears under concurrency, which is the worst kind to find in production."""
    with identity.acting_as(ALICE):
        assert identity.current_email() == ALICE
    assert identity.current_email() is None


def test_nested_identities_restore_the_outer_one():
    with identity.acting_as(ALICE):
        with identity.acting_as(BOB):
            assert identity.current_email() == BOB
        assert identity.current_email() == ALICE
