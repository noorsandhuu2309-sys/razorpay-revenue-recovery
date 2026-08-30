"""Local account store + session auth for OMNIX.

OMNIX runs as a single-user-ish desktop app on 127.0.0.1, but the login screen
is a real gate, not decoration: passwords are salted + stretched, sessions are
opaque random tokens stored only as hashes, and every non-public route is
closed until a session cookie proves otherwise.

Everything lives in one JSON file (``omnix_auth.json`` at the repo root, which
is gitignored). No database, no network, no third party.

Threat model — what this defends against:

* Someone who opens http://127.0.0.1:8000 from another app/tab on the machine
  (the port is otherwise wide open to every process and every web page).
* A website trying a cross-site request at the local port: the session cookie
  is ``SameSite=Lax``, so it never rides along on a cross-site POST, and the
  gate then answers 401.
* Someone who reads ``omnix_auth.json``: passwords are scrypt hashes and the
  session tokens are stored as SHA-256 digests, so the file alone grants
  neither the password nor a usable session.

What it does *not* claim: this is not multi-tenant server auth. There is no
TLS (loopback only), no email delivery, and password reset hands the link back
through the local console.

Public API
----------
``create_user`` / ``verify_password`` / ``set_name`` / ``change_password``
``create_session`` / ``session_user`` / ``destroy_session``
``create_reset_token`` / ``consume_reset_token``
``count_users`` / ``enabled``
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent

# Where the account store lives. Beside the code for a local run; on a hosted
# deployment OMNIX_AUTH_DIR points at a mounted volume, because a container
# filesystem is thrown away on every redeploy and that would silently delete
# everyone's account.
_STORE_DIR = Path(os.environ.get("OMNIX_AUTH_DIR", "").strip() or ROOT)
try:
    _STORE_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    _STORE_DIR = ROOT
STORE = _STORE_DIR / "omnix_auth.json"

# Session lifetimes, *within* one run of the app. "Keep me signed in" gets a
# long sliding window and a persistent cookie, so closing and reopening the
# browser doesn't sign you out; without it you get a short window in a
# browser-session cookie.
KEEP_TTL = 30 * 24 * 3600      # 30 days
SESSION_TTL = 12 * 3600        # 12 hours
RESET_TTL = 30 * 60            # 30 minutes

# scrypt cost. n=2**15 with r=8 costs ~32 MB and ~100 ms per hash on a laptop —
# slow enough to make offline guessing expensive, fast enough that sign-in still
# feels instant.
SCRYPT_N = 2 ** 15
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 64
PBKDF2_ROUNDS = 600_000        # fallback if the OpenSSL build has no scrypt

MIN_PASSWORD = 12
MAX_PASSWORD = 1024            # refuse absurd inputs before paying for a hash
MAX_NAME = 64

# Failed-attempt throttle (in-memory; resets when the server restarts, which is
# fine — an attacker cannot restart it for us).
LOCK_THRESHOLD = 5             # failures before the lockout starts
LOCK_BASE = 5.0                # seconds, doubled per extra failure
LOCK_MAX = 15 * 60.0

EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]{2,}$")

_lock = threading.RLock()
_data: dict[str, Any] | None = None
_fails: dict[str, tuple[int, float]] = {}   # email -> (count, locked_until)


class AuthError(Exception):
    """A failure the user should see verbatim (safe, non-leaky messages only)."""

    def __init__(self, message: str, status: int = 400, field: str | None = None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.field = field


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------
def enabled() -> bool:
    """False when OMNIX_AUTH is explicitly switched off (dev escape hatch)."""
    return os.environ.get("OMNIX_AUTH", "on").strip().lower() not in (
        "0", "off", "false", "no",
    )


def demo_mode() -> bool:
    """True when the lock screen is theatre: any password opens the app.

    `OMNIX_AUTH=demo` exists so the gate can be *shown* — in a walkthrough, a
    screen recording, a pitch — without a wrong keystroke ending the demo. It
    is not a weaker password policy; it is no password policy at all. Every
    account is reachable by anyone who can reach the port.

    Two catches keep that from escaping the laptop it is meant for:

    * It must be asked for by name. `on`/`off` are unchanged, so nothing
      already deployed acquires this by upgrading.
    * **An invite code turns it off.** That is the same marker `/api/auth/forgot`
      already uses for "this deployment looks hosted", and reusing it means a
      real deployment cannot be in demo mode by forgetting an env var.

    Sign-in is the only door this opens. Changing a password still requires the
    current one — see `verify_password(allow_demo=...)`.
    """
    if not enabled() or invite_code():
        return False
    return os.environ.get("OMNIX_AUTH", "on").strip().lower() == "demo"


def persist_sessions() -> bool:
    """Whether sessions may outlive the app. Off by default — see new_run()."""
    return os.environ.get("OMNIX_AUTH_PERSIST", "off").strip().lower() in (
        "1", "on", "true", "yes",
    )


def new_run() -> int:
    """Drop every session, so launching OMNIX always lands on the login screen.

    The gate is the lock on the app, and a lock you only meet once is not doing
    much. Starting the server is the event that should demand credentials
    again, so sessions are scoped to a single run of the process rather than to
    wall-clock time. "Keep me signed in" still does its job inside that run: it
    survives closing the browser, refreshing, and navigating away and back.

    Set OMNIX_AUTH_PERSIST=on to keep the old behaviour, where a session
    outlives a restart until its own expiry.
    """
    if persist_sessions():
        return 0
    with _lock:
        data = _load()
        dropped = len(data["sessions"])
        if dropped:
            data["sessions"] = {}
            _save()
        return dropped


def _blank() -> dict[str, Any]:
    return {"version": 1, "users": {}, "sessions": {}, "resets": {}}


def _load() -> dict[str, Any]:
    global _data
    if _data is not None:
        return _data
    try:
        raw = json.loads(STORE.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or "users" not in raw:
            raise ValueError("malformed store")
        for key in ("users", "sessions", "resets"):
            raw.setdefault(key, {})
        _data = raw
    except FileNotFoundError:
        _data = _blank()
    except Exception:
        # A corrupt store must not brick the app. Keep the bad file around for
        # forensics and start clean — the user re-creates their account.
        try:
            STORE.replace(STORE.with_suffix(".corrupt.json"))
        except Exception:
            pass
        _data = _blank()
    return _data


def _save() -> None:
    data = _load()
    tmp = STORE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(STORE)
    # Credentials file: owner-only where the platform honours it.
    try:
        os.chmod(STORE, 0o600)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------
def _hash_password(password: str, salt: bytes | None = None) -> dict[str, Any]:
    salt = salt or secrets.token_bytes(16)
    pw = password.encode("utf-8")
    try:
        digest = hashlib.scrypt(
            pw, salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=SCRYPT_DKLEN,
            maxmem=128 * SCRYPT_N * SCRYPT_R * 2,
        )
        return {
            "algo": "scrypt", "n": SCRYPT_N, "r": SCRYPT_R, "p": SCRYPT_P,
            "salt": salt.hex(), "hash": digest.hex(),
        }
    except (ValueError, AttributeError):
        digest = hashlib.pbkdf2_hmac("sha256", pw, salt, PBKDF2_ROUNDS, dklen=64)
        return {
            "algo": "pbkdf2_sha256", "rounds": PBKDF2_ROUNDS,
            "salt": salt.hex(), "hash": digest.hex(),
        }


def _check_password(password: str, rec: dict[str, Any]) -> bool:
    try:
        salt = bytes.fromhex(rec["salt"])
        expected = bytes.fromhex(rec["hash"])
        pw = password.encode("utf-8")
        if rec.get("algo") == "scrypt":
            got = hashlib.scrypt(
                pw, salt=salt, n=int(rec["n"]), r=int(rec["r"]), p=int(rec["p"]),
                dklen=len(expected),
                maxmem=128 * int(rec["n"]) * int(rec["r"]) * 2,
            )
        else:
            got = hashlib.pbkdf2_hmac(
                "sha256", pw, salt, int(rec.get("rounds", PBKDF2_ROUNDS)),
                dklen=len(expected),
            )
    except Exception:
        return False
    return hmac.compare_digest(got, expected)


# A throwaway hash used when the email is unknown, so a wrong email and a wrong
# password take the same wall-clock time and cannot be told apart.
_DECOY = _hash_password(secrets.token_urlsafe(24))


def _token_key(token: str) -> str:
    """Sessions and reset tokens are stored as digests, never in the clear."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def normalise_email(email: str) -> str:
    return (email or "").strip().lower()


def clean_name(name: str) -> str:
    """Collapse whitespace, strip control characters, cap the length."""
    name = re.sub(r"[\x00-\x1f\x7f]", "", (name or ""))
    return re.sub(r"\s+", " ", name).strip()[:MAX_NAME]


def initials(name: str) -> str:
    parts = [p for p in clean_name(name).split(" ") if p]
    if not parts:
        return "??"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def _require_email(email: str) -> str:
    email = normalise_email(email)
    if not email:
        raise AuthError("Enter your email address.", field="email")
    if len(email) > 254 or not EMAIL_RE.match(email):
        raise AuthError("That doesn't look like an email address.", field="email")
    return email


def _require_name(name: str) -> str:
    name = clean_name(name)
    if not name:
        raise AuthError("Enter your name.", field="name")
    return name


def _require_password(password: str) -> str:
    password = password or ""
    if not password:
        raise AuthError("Enter your password.", field="password")
    if len(password) < MIN_PASSWORD:
        raise AuthError(f"Use at least {MIN_PASSWORD} characters.", field="password")
    if len(password) > MAX_PASSWORD:
        raise AuthError("That password is too long.", field="password")
    return password


# ---------------------------------------------------------------------------
# Throttle
# ---------------------------------------------------------------------------
def _throttle_check(email: str) -> None:
    count, until = _fails.get(email, (0, 0.0))
    if until > time.time():
        wait = int(until - time.time()) + 1
        raise AuthError(
            f"Too many failed attempts. Try again in {wait}s.", status=429,
        )


def _throttle_fail(email: str) -> None:
    count, _ = _fails.get(email, (0, 0.0))
    count += 1
    until = 0.0
    if count >= LOCK_THRESHOLD:
        delay = min(LOCK_BASE * (2 ** (count - LOCK_THRESHOLD)), LOCK_MAX)
        until = time.time() + delay
    _fails[email] = (count, until)


def _throttle_clear(email: str) -> None:
    _fails.pop(email, None)


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------
def _public(user: dict[str, Any]) -> dict[str, Any]:
    """The shape the frontend gets — never includes the password record."""
    return {
        "name": user["name"],
        "email": user["email"],
        "initials": initials(user["name"]),
        "created": user.get("created"),
        "last_login": user.get("last_login"),
    }


def count_users() -> int:
    with _lock:
        return len(_load()["users"])


def get_user(email: str) -> dict[str, Any] | None:
    with _lock:
        return _load()["users"].get(normalise_email(email))


def invite_code() -> str:
    """Invite code required to sign up, or "" when signup is open.

    Read straight from the environment. On a hosted run the operator's API key
    pays for whatever a new account spends, so signup is gated by a code and an
    account cap; on a local run neither variable is set and both checks in
    `create_user` are no-ops.
    """
    return os.environ.get("OMNIX_INVITE_CODE", "").strip()


def max_users() -> int:
    """Account cap, or 0 for unlimited."""
    try:
        return max(0, int(os.environ.get("OMNIX_MAX_USERS", "0").strip() or "0"))
    except ValueError:
        return 0


def create_user(name: str, email: str, password: str,
                invite: str = "") -> dict[str, Any]:
    """Create an account."""
    required = invite_code()
    if required and not hmac.compare_digest((invite or "").strip(), required):
        raise AuthError(
            "That invite code isn't right." if invite else
            "OMNIX is invite-only right now. Enter your invite code.",
            status=403, field="invite",
        )

    name = _require_name(name)
    email = _require_email(email)
    password = _require_password(password)
    with _lock:
        users = _load()["users"]
        cap = max_users()
        if cap and email not in users and len(users) >= cap:
            raise AuthError(
                "OMNIX isn't accepting new accounts right now.", status=403,
            )
        if email in users:
            raise AuthError(
                "An account already exists for that email.", status=409, field="email",
            )
        users[email] = {
            "name": name,
            "email": email,
            "pw": _hash_password(password),
            "created": time.time(),
            "last_login": None,
        }
        _save()
        return _public(users[email])


def verify_password(email: str, password: str, *,
                    allow_demo: bool = True) -> dict[str, Any]:
    """Return the user on success. Raises AuthError otherwise.

    Unknown email and wrong password produce the *same* message and roughly the
    same timing, so the endpoint can't be used to enumerate accounts.

    `allow_demo=False` opts a caller out of :func:`demo_mode`. Re-authentication
    before a destructive change is not the door demo mode exists to prop open.
    """
    email = normalise_email(email)
    password = password or ""
    demo = allow_demo and demo_mode()
    if len(password) > MAX_PASSWORD and not demo:
        raise AuthError("Email or password is incorrect.", status=401)
    with _lock:
        _throttle_check(email)
        users = _load()["users"]
        user = users.get(email)
        if user is None:
            # In demo mode an unfamiliar address is a typo on stage, not an
            # intruder: fall through to the only account that could be meant.
            stand_in = _demo_stand_in(users) if demo else None
            if stand_in is None:
                _check_password(password, _DECOY)   # burn the same time
                _throttle_fail(email)
                raise AuthError("Email or password is incorrect.", status=401)
            user, matched = stand_in, False
        else:
            matched = _check_password(password, user["pw"])
            if not matched and not demo:
                _throttle_fail(email)
                raise AuthError("Email or password is incorrect.", status=401)
        if not matched:
            print(f"[auth] DEMO MODE: opened {user.get('email')!r} without a "
                  f"valid password. Set OMNIX_AUTH=on to enforce the gate.")
        _throttle_clear(email)
        # Opportunistic upgrade if the stored hash predates the current params.
        # Guarded on `matched`: rehashing here in demo mode would silently
        # replace the real password with whatever was typed to get past it.
        if matched and (user["pw"].get("algo") != "scrypt"
                        or int(user["pw"].get("n", 0)) < SCRYPT_N):
            user["pw"] = _hash_password(password)
        user["last_login"] = time.time()
        _save()
        return _public(user)


def _demo_stand_in(users: dict[str, Any]) -> dict[str, Any] | None:
    """The account an unrecognised demo-mode sign-in should land on.

    Oldest first, so it is the same account every time rather than whichever
    one a dict happened to yield. Returns None when there is no account at
    all — demo mode relaxes the check on credentials, and inventing a user out
    of nothing is a different and much larger thing.
    """
    if not users:
        return None
    return min(users.values(), key=lambda u: (u.get("created") or 0,
                                              u.get("email") or ""))


def set_name(email: str, name: str) -> dict[str, Any]:
    """Rename the account. This is what the app shows everywhere."""
    name = _require_name(name)
    with _lock:
        user = _load()["users"].get(normalise_email(email))
        if user is None:
            raise AuthError("No such account.", status=404)
        user["name"] = name
        _save()
        return _public(user)


def change_password(email: str, current: str, new: str) -> dict[str, Any]:
    # Not `allow_demo`: demo mode is there to let the gate be demonstrated, not
    # to let a bystander take an account over while it is on screen.
    user = verify_password(email, current, allow_demo=False)
    new = _require_password(new)
    with _lock:
        rec = _load()["users"][normalise_email(email)]
        rec["pw"] = _hash_password(new)
        _save()
        # Every other session for this account dies with the old password.
        _drop_sessions_for(normalise_email(email))
        return user


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------
def _prune(now: float | None = None) -> None:
    now = now or time.time()
    data = _load()
    for bucket in ("sessions", "resets"):
        dead = [k for k, v in data[bucket].items() if v.get("expires", 0) <= now]
        for k in dead:
            del data[bucket][k]


def _drop_sessions_for(email: str) -> None:
    data = _load()
    dead = [k for k, v in data["sessions"].items() if v.get("email") == email]
    for k in dead:
        del data["sessions"][k]
    _save()


def create_session(email: str, keep: bool = False) -> tuple[str, int]:
    """Mint a session token. Returns ``(token, max_age_seconds)``.

    ``max_age`` is 0 for a non-persistent session, which the caller turns into a
    browser-session cookie (dropped when the window closes).
    """
    email = normalise_email(email)
    token = secrets.token_urlsafe(32)
    ttl = KEEP_TTL if keep else SESSION_TTL
    with _lock:
        _prune()
        _load()["sessions"][_token_key(token)] = {
            "email": email,
            "created": time.time(),
            "expires": time.time() + ttl,
            "keep": bool(keep),
        }
        _save()
    return token, (ttl if keep else 0)


def session_user(token: str | None) -> dict[str, Any] | None:
    """Resolve a cookie value to a user, sliding the expiry forward."""
    if not token:
        return None
    with _lock:
        data = _load()
        sess = data["sessions"].get(_token_key(token))
        if sess is None:
            return None
        now = time.time()
        if sess.get("expires", 0) <= now:
            del data["sessions"][_token_key(token)]
            _save()
            return None
        user = data["users"].get(sess.get("email", ""))
        if user is None:                       # account deleted underneath us
            del data["sessions"][_token_key(token)]
            _save()
            return None
        # Slide the window, but only write when it moves meaningfully — this is
        # on the hot path for every request.
        ttl = KEEP_TTL if sess.get("keep") else SESSION_TTL
        if sess["expires"] - now < ttl - 300:
            sess["expires"] = now + ttl
            _save()
        return _public(user)


def destroy_session(token: str | None) -> None:
    if not token:
        return
    with _lock:
        data = _load()
        if data["sessions"].pop(_token_key(token), None) is not None:
            _save()


# ---------------------------------------------------------------------------
# Password reset
# ---------------------------------------------------------------------------
def create_reset_token(email: str) -> str | None:
    """Mint a reset token, or None if there's no such account.

    Callers must answer the user identically either way — the None is for the
    server's own logging, not for the response body.
    """
    email = normalise_email(email)
    with _lock:
        _prune()
        if email not in _load()["users"]:
            return None
        token = secrets.token_urlsafe(32)
        _load()["resets"][_token_key(token)] = {
            "email": email,
            "expires": time.time() + RESET_TTL,
        }
        _save()
        return token


def consume_reset_token(token: str, new_password: str) -> dict[str, Any]:
    new_password = _require_password(new_password)
    with _lock:
        data = _load()
        key = _token_key(token or "")
        rec = data["resets"].get(key)
        if rec is None or rec.get("expires", 0) <= time.time():
            data["resets"].pop(key, None)
            _save()
            raise AuthError(
                "That reset link has expired. Request a new one.", status=400,
            )
        email = rec["email"]
        user = data["users"].get(email)
        if user is None:
            del data["resets"][key]
            _save()
            raise AuthError("That reset link is no longer valid.", status=400)
        user["pw"] = _hash_password(new_password)
        del data["resets"][key]
        _save()
        _drop_sessions_for(email)              # a reset logs out everywhere
        _throttle_clear(email)
        return _public(user)
