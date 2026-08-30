"""Workspaces — the container everything else is scoped by.

Also owns identity resolution, because "which Space" and "whose Space" are the
same question and answering them apart is how tenants leak.

TWO IDENTITY STORES, ONE BRIDGE
-------------------------------
Accounts, passwords and sessions live in `omnix_auth.json`, keyed by email and
owned by :mod:`omnix.auth`. Workspaces point at a row in the `user` table via
`Workspace.user_id`, and that row is where `plan` lives. Nothing connected the
two: signing in proved who you were and then the data layer asked
`default_user()`, which is a hardcoded local account. Every signed-in user
therefore saw the same Spaces.

:func:`user_for_email` is the bridge — it materialises the database row for an
authenticated email on first use. :func:`acting_user` is what the request path
calls, and :func:`resolve` is the chokepoint that every `/api/*` route already
funnels through, so ownership is enforced in exactly one place.

LOCAL MODE STILL WORKS
----------------------
With `OMNIX_AUTH=off` there is no authenticated email, and `acting_user()`
falls back to the local account exactly as before. That is what keeps the
single-user desktop story — and the test suite — working unchanged.
"""

from __future__ import annotations

from sqlalchemy import or_, select

from . import identity
from .db import session
from .schema import Workspace, User, iso

LOCAL_EMAIL = "local@omnix.local"
DEFAULT_WORKSPACE_NAME = "Default workspace"


class WorkspaceAccessError(LookupError):
    """The caller asked for a Space that is not theirs, or does not exist.

    One exception for both cases on purpose. Distinguishing "not yours" from
    "no such Space" tells an attacker which ids are real, so the HTTP layer
    renders this as a flat 404 either way.
    """


def default_user() -> str:
    """Id of the local single-user account, creating it on first call.

    **Local mode only.** This is the identity used when authentication is
    switched off. It is no longer a general-purpose "current user" — reaching
    for it on the request path is what made every tenant share one account.
    Use :func:`acting_user` instead.
    """
    with session() as s:
        u = s.scalar(select(User).where(User.email == LOCAL_EMAIL))
        if u is None:
            u = User(email=LOCAL_EMAIL, display_name="Local", plan="pro",
                     is_admin=True)
            s.add(u)
            s.flush()
        return u.id


def user_for_email(email: str, display_name: str = "") -> str:
    """Id of the `user` row for an authenticated email, creating it on demand.

    Created lazily rather than at signup because the auth store is the system
    of record for accounts and it predates this table — a user who registered
    before this bridge existed still needs a row the first time they load a
    Space. `plan` takes its column default of `free`; billing writes it later.
    """
    key = (email or "").strip().lower()
    if not key:
        return default_user()
    with session() as s:
        u = s.scalar(select(User).where(User.email == key))
        if u is None:
            u = User(email=key, display_name=display_name or key.split("@")[0])
            s.add(u)
            s.flush()
        elif display_name and not u.display_name:
            u.display_name = display_name
        return u.id


def acting_user() -> str:
    """The database user id for whoever is making this request.

    Falls back to the local account when no authenticated identity is bound —
    which happens with `OMNIX_AUTH=off`, in the test suite, and in background
    work that did not re-bind an identity. Background work that touches another
    tenant's data must establish one with :func:`omnix.core.identity.acting_as`.
    """
    email = identity.current_email()
    return user_for_email(email) if email else default_user()


def default_workspace() -> str:
    """Id of the *acting* user's first workspace, creating it on first call.

    Every legacy entry point (the old /api/squad routes, the desktop app) funnels
    through here so runs made without an explicit workspace are still recorded
    somewhere real rather than being dropped. It used to resolve the local
    account unconditionally, which is what handed every signed-in user the same
    Space; it now follows :func:`acting_user`, so a new account lands in a new
    Space of its own.
    """
    uid = acting_user()
    with session() as s:
        ws = s.scalar(
            select(Workspace).where(Workspace.user_id == uid)
            .order_by(Workspace.created_at).limit(1))
        if ws is None:
            ws = Workspace(user_id=uid, name=DEFAULT_WORKSPACE_NAME,
                           description="Runs not attached to a specific project.")
            s.add(ws)
            s.flush()
        return ws.id


def create(user_id: str, name: str, description: str = "") -> dict:
    with session() as s:
        ws = Workspace(user_id=user_id, name=name.strip() or "Untitled",
                       description=description.strip())
        s.add(ws)
        s.flush()
        return _public(ws)


def get(workspace_id: str) -> dict | None:
    with session() as s:
        ws = s.get(Workspace, workspace_id)
        return _public(ws) if ws else None


def list_for(user_id: str) -> list[dict]:
    with session() as s:
        rows = s.scalars(
            select(Workspace).where(Workspace.user_id == user_id)
            .order_by(Workspace.updated_at.desc())).all()
        return [_public(w) for w in rows]


def update(workspace_id: str, **fields) -> dict | None:
    allowed = {"name", "description", "settings_json"}
    with session() as s:
        ws = s.get(Workspace, workspace_id)
        if ws is None:
            return None
        for k, v in fields.items():
            if k in allowed and v is not None:
                setattr(ws, k, v)
        s.flush()
        return _public(ws)


def delete(workspace_id: str) -> bool:
    """Delete a Space and everything scoped to it.

    Thirteen tables carry a `workspace_id` foreign key and none of them
    cascade, so deleting the row on its own raises `FOREIGN KEY constraint
    failed` under `PRAGMA foreign_keys=ON` — which surfaced as a 500 with no
    explanation. Children are cleared explicitly, deepest first, because the
    dependents have dependents: an `object_source` references both an object
    and a source, and a `claim` references an execution.

    Ordering is the whole correctness argument here, so it is written out
    rather than left to SQLAlchemy's relationship graph, which does not know
    about the tables that reference objects rather than the workspace.
    """
    from .schema import (Artifact, ArtifactRef, Claim, Event, Execution,
                         ExecutionStep, Finding, MemoryItem, ModelCall,
                         ObjectEvent, ObjectNode, ObjectSource, Relationship,
                         Repo, SavedView, Source, Task, UsageCounter)

    with session() as s:
        ws = s.get(Workspace, workspace_id)
        if ws is None:
            return False

        exec_ids = [e.id for e in s.scalars(
            select(Execution).where(Execution.workspace_id == workspace_id)).all()]
        art_ids = [a.id for a in s.scalars(
            select(Artifact).where(Artifact.workspace_id == workspace_id)).all()]

        # Leaves that hang off executions/artifacts rather than the workspace.
        if exec_ids:
            s.query(ExecutionStep).filter(
                ExecutionStep.execution_id.in_(exec_ids)).delete(
                    synchronize_session=False)
            s.query(ModelCall).filter(
                ModelCall.execution_id.in_(exec_ids)).delete(
                    synchronize_session=False)
        if art_ids:
            # An edge is dead if EITHER endpoint is going, so both directions
            # have to be cleared — dropping only one leaves a ref pointing at a
            # deleted artifact and the constraint fires anyway.
            s.query(ArtifactRef).filter(
                or_(ArtifactRef.from_artifact_id.in_(art_ids),
                    ArtifactRef.to_artifact_id.in_(art_ids))).delete(
                        synchronize_session=False)

        # Then everything scoped directly by workspace, join tables first.
        for model in (ObjectSource, ObjectEvent, Relationship, Claim, Finding,
                      Source, ObjectNode, Event, Task, Repo, MemoryItem,
                      SavedView, UsageCounter, Artifact, Execution):
            s.query(model).filter(
                model.workspace_id == workspace_id).delete(
                    synchronize_session=False)

        s.delete(ws)
        return True


def exists(workspace_id: str) -> bool:
    with session() as s:
        return s.get(Workspace, workspace_id) is not None


def owns(user_id: str, workspace_id: str) -> bool:
    """Whether `user_id` is the owner of `workspace_id`."""
    if not user_id or not workspace_id:
        return False
    with session() as s:
        ws = s.get(Workspace, workspace_id)
        return ws is not None and ws.user_id == user_id


def resolve(workspace_id: str | None) -> str:
    """Given an optional id, return a usable one **that the caller owns**.

    This is the security chokepoint. Every `/api/*` route that accepts a
    `workspace` parameter reaches it through `api/*.py::_ws()`, so enforcing
    ownership here covers the whole surface at once rather than sixty times.

    It used to accept any id that merely *existed*, which meant a signed-in
    user could read and write any other tenant's Space by guessing or
    observing its id — an IDOR sitting behind a login form that looked like it
    was protecting something.

    Raises :class:`WorkspaceAccessError` for an id that is missing or owned by
    someone else; the two are deliberately indistinguishable to the caller.
    """
    uid = acting_user()
    if workspace_id:
        if not owns(uid, workspace_id):
            raise WorkspaceAccessError(workspace_id)
        return workspace_id
    return default_workspace()


def _public(ws: Workspace) -> dict:
    return {
        "id": ws.id,
        "user_id": ws.user_id,
        "name": ws.name,
        "description": ws.description,
        "settings": ws.settings_json or {},
        "created_at": iso(ws.created_at),
        "updated_at": iso(ws.updated_at),
    }
