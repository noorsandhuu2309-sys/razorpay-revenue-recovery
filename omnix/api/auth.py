"""HTTP surface over :mod:`omnix.auth` — sign up, sign in, sign out, profile.

The module underneath does the security work (scrypt, opaque session tokens,
throttling, decoy hashes). This layer only translates it into requests and
cookies, and it has exactly two jobs of its own:

1. **Never leak which half of a credential was wrong.** `AuthError` carries a
   message the module already vetted as safe, so handlers re-raise it verbatim
   and never add detail of their own.

2. **Set the cookie correctly.** `SameSite=Lax` is what makes the local port
   safe to leave open: a page on another origin cannot make the browser attach
   this cookie to a POST, so a cross-site request meets the gate as an
   anonymous one. `HttpOnly` keeps it out of reach of any script on the page.
   `Secure` is deliberately NOT set, because OMNIX runs on plain http over
   loopback and a Secure cookie would simply never be stored.

The gate itself lives in `server.py` as middleware, not here — a route module
that also decided which routes were public would be the wrong place to look
when something is unexpectedly reachable.
"""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from .. import auth
from ..core import mail

log = logging.getLogger("omnix.auth")

router = APIRouter(prefix="/api/auth", tags=["auth"])

COOKIE = "omnix_session"


def _fail(err: auth.AuthError) -> JSONResponse:
    return JSONResponse(
        {"error": err.message, "field": err.field}, status_code=err.status)


def _set_cookie(response: Response, token: str, max_age: int) -> None:
    """Attach the session. `max_age=0` means a browser-session cookie.

    The distinction is the whole of "keep me signed in": with a max-age the
    cookie survives closing the window, without one it does not. Passing
    `max_age=0` to Starlette would expire the cookie immediately, so the
    argument is omitted entirely in that case.
    """
    response.set_cookie(
        COOKIE, token,
        max_age=max_age or None,
        httponly=True,
        samesite="lax",
        path="/",
    )


def _state(request: Request) -> dict:
    """Everything the client needs to decide whether to show the gate."""
    user = auth.session_user(request.cookies.get(COOKIE))
    return {
        "authenticated": user is not None,
        "user": user,
        # Drives first-run: with no account at all the screen opens on signup
        # rather than asking for credentials that cannot exist yet.
        "hasAccounts": auth.count_users() > 0,
        "required": auth.enabled(),
        "inviteRequired": bool(auth.invite_code()),
        # The gate is on and accepting anything. The screen says so in small
        # type: a lock that opens for any key is worth demonstrating, but not
        # worth letting someone mistake for a real one.
        "demo": auth.demo_mode(),
    }


@router.get("/me")
def me(request: Request):
    return _state(request)


@router.post("/signup")
def signup(request: Request, payload: dict):
    p = payload or {}
    try:
        user = auth.create_user(
            p.get("name") or "", p.get("email") or "",
            p.get("password") or "", invite=p.get("invite") or "")
    except auth.AuthError as err:
        return _fail(err)
    token, max_age = auth.create_session(user["email"], keep=bool(p.get("keep")))
    resp = JSONResponse({"authenticated": True, "user": user})
    _set_cookie(resp, token, max_age)
    return resp


@router.post("/login")
def login(payload: dict):
    p = payload or {}
    try:
        user = auth.verify_password(p.get("email") or "", p.get("password") or "")
    except auth.AuthError as err:
        return _fail(err)
    token, max_age = auth.create_session(user["email"], keep=bool(p.get("keep")))
    resp = JSONResponse({"authenticated": True, "user": user})
    _set_cookie(resp, token, max_age)
    return resp


@router.post("/logout")
def logout(request: Request):
    auth.destroy_session(request.cookies.get(COOKIE))
    resp = JSONResponse({"authenticated": False, "user": None})
    # Delete with the same path the cookie was set on, or the browser keeps a
    # second copy and the next request signs the user straight back in.
    resp.delete_cookie(COOKIE, path="/")
    return resp


@router.post("/forgot")
def forgot(payload: dict):
    """Always answers identically, so this cannot enumerate accounts.

    The reply is deliberately the same whether or not the address exists, and
    deliberately the same whether or not the mail actually went out. Reporting
    a send failure here would leak which addresses have accounts — the one
    thing this endpoint exists to hide — so a failure is logged for the
    operator instead, where it is visible without being an oracle.

    `devToken` is a loopback convenience for the local install, suppressed the
    moment an invite code is set, which is the marker for a hosted deployment.
    """
    email = ((payload or {}).get("email") or "").strip()
    token = auth.create_reset_token(email)

    if token:
        base = (os.environ.get("OMNIX_PUBLIC_URL") or "").rstrip("/")
        link = f"{base}/login?reset={token}" if base else f"/login?reset={token}"
        sent = mail.send_password_reset(email, link)
        if not sent.ok:
            log.warning("password reset for %s could not be mailed (%s): %s",
                        email, sent.backend, sent.detail)
        elif not sent.delivered:
            # console/file backend — say so, so nobody assumes it was sent.
            log.info("password reset for %s rendered via %s: %s",
                     email, sent.backend, sent.detail)

    return {"sent": True,
            "note": "If that account exists, a reset link has been issued.",
            "devToken": token if token and not auth.invite_code() else None}


@router.post("/reset")
def reset(payload: dict):
    p = payload or {}
    try:
        user = auth.consume_reset_token(p.get("token") or "",
                                        p.get("password") or "")
    except auth.AuthError as err:
        return _fail(err)
    token, max_age = auth.create_session(user["email"])
    resp = JSONResponse({"authenticated": True, "user": user})
    _set_cookie(resp, token, max_age)
    return resp


@router.patch("/profile")
def profile(request: Request, payload: dict):
    user = auth.session_user(request.cookies.get(COOKIE))
    if user is None:
        return JSONResponse({"error": "Not signed in."}, status_code=401)
    try:
        return {"user": auth.set_name(user["email"],
                                      (payload or {}).get("name") or "")}
    except auth.AuthError as err:
        return _fail(err)


@router.post("/password")
def password(request: Request, payload: dict):
    """Change the password. Every other session for the account dies with it,
    which is the point — a password change that leaves old sessions alive does
    not actually revoke anything."""
    user = auth.session_user(request.cookies.get(COOKIE))
    if user is None:
        return JSONResponse({"error": "Not signed in."}, status_code=401)
    p = payload or {}
    try:
        auth.change_password(user["email"], p.get("current") or "",
                             p.get("new") or "")
    except auth.AuthError as err:
        return _fail(err)
    # The caller's own session was dropped with the rest; mint a fresh one so
    # changing a password does not sign you out of the tab you did it in.
    token, max_age = auth.create_session(user["email"])
    resp = JSONResponse({"changed": True, "user": user})
    _set_cookie(resp, token, max_age)
    return resp
