"""Transactional email: password resets, receipts, alerts, digests.

Blocker 4 in the commercial plan. Without a mail path there is no account
recovery, no receipt, no dunning on a failed card, and none of the change
alerts the product's retention story depends on — `api/auth.py` printed reset
links to the server console, which is honest for a laptop and impossible with
customers.

THREE BACKENDS, AND THE DEFAULT IS NOT A NO-OP
----------------------------------------------
    console   prints the message. The developer default.
    file      writes .eml files to a directory. The TEST default, because it
              lets a test assert what was actually sent rather than trusting a
              mock, and because a human can open the result in a mail client
              and see what a customer would see.
    smtp      sends. Requires host and credentials.

There is deliberately no "silently discard" backend. A send that vanishes is
indistinguishable from a send that worked, and password reset is exactly the
feature where that difference is discovered by a locked-out customer.

WHAT THIS MODULE REFUSES TO DO
------------------------------
It does not retry forever, it does not queue to disk, and it does not pretend a
queued message was delivered. `send()` returns a typed result saying which
backend handled it and whether it left the machine. Anything that needs
guaranteed delivery needs a real queue, and claiming otherwise here would be a
worse lie than not having email at all.
"""

from __future__ import annotations

import os
import smtplib
import ssl
import uuid
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, formatdate
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


class MailError(RuntimeError):
    pass


@dataclass(frozen=True)
class Sent:
    ok: bool
    backend: str
    message_id: str
    detail: str = ""
    # False for console/file: the message was rendered, not delivered. The
    # distinction is the whole point — see the module docstring.
    delivered: bool = False

    def to_dict(self) -> dict:
        return {"ok": self.ok, "backend": self.backend,
                "messageId": self.message_id, "delivered": self.delivered,
                "detail": self.detail}


def backend() -> str:
    """`OMNIX_MAIL_BACKEND`, or inferred from whether SMTP is configured."""
    explicit = (os.environ.get("OMNIX_MAIL_BACKEND") or "").strip().lower()
    if explicit in ("console", "file", "smtp"):
        return explicit
    return "smtp" if os.environ.get("OMNIX_SMTP_HOST") else "console"


def configured() -> tuple[bool, str]:
    """Whether the active backend can actually run. (ok, what_to_fix)."""
    kind = backend()
    if kind == "smtp":
        missing = [k for k in ("OMNIX_SMTP_HOST", "OMNIX_SMTP_USER",
                               "OMNIX_SMTP_PASSWORD")
                   if not (os.environ.get(k) or "").strip()]
        if missing:
            return False, f"Set {', '.join(missing)} to send email."
    if kind == "file" and not (os.environ.get("OMNIX_MAIL_DIR") or "").strip():
        return False, "Set OMNIX_MAIL_DIR to a writable directory."
    return True, ""


def from_address() -> tuple[str, str]:
    return (os.environ.get("OMNIX_MAIL_FROM_NAME") or "OMNIX",
            os.environ.get("OMNIX_MAIL_FROM") or "omnix@localhost")


def build(to: str, subject: str, text: str, html: str = "") -> EmailMessage:
    msg = EmailMessage()
    name, addr = from_address()
    msg["From"] = formataddr((name, addr))
    msg["To"] = to
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = f"<{uuid.uuid4().hex}@omnix>"
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")
    return msg


def send(to: str, subject: str, text: str, html: str = "") -> Sent:
    """Send one message. Never raises; returns what happened."""
    to = (to or "").strip()
    if "@" not in to:
        return Sent(False, backend(), "", "not a valid address")

    ok, fix = configured()
    if not ok:
        return Sent(False, backend(), "", fix)

    msg = build(to, subject, text, html)
    message_id = msg["Message-ID"]
    kind = backend()

    try:
        if kind == "console":
            print(f"\n[mail:console] To: {to}\n[mail:console] Subject: {subject}\n"
                  f"{text}\n")
            return Sent(True, kind, message_id,
                        "printed to the console; not delivered", delivered=False)

        if kind == "file":
            outdir = Path(os.environ["OMNIX_MAIL_DIR"])
            outdir.mkdir(parents=True, exist_ok=True)
            path = outdir / f"{message_id.strip('<>').replace('@', '_')}.eml"
            path.write_text(msg.as_string(), encoding="utf-8")
            return Sent(True, kind, message_id, f"written to {path}",
                        delivered=False)

        host = os.environ["OMNIX_SMTP_HOST"]
        port = int(os.environ.get("OMNIX_SMTP_PORT") or 587)
        user = os.environ["OMNIX_SMTP_USER"]
        password = os.environ["OMNIX_SMTP_PASSWORD"]
        timeout = float(os.environ.get("OMNIX_SMTP_TIMEOUT") or 20)

        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=timeout,
                                  context=ssl.create_default_context()) as s:
                s.login(user, password)
                s.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=timeout) as s:
                s.starttls(context=ssl.create_default_context())
                s.login(user, password)
                s.send_message(msg)
        return Sent(True, "smtp", message_id, "", delivered=True)

    except (smtplib.SMTPException, OSError, ValueError, KeyError) as e:
        # A failed send must be visible. Callers decide whether to surface it
        # to the user — a reset that could not be mailed has to say so, or the
        # user waits for a message that is never coming.
        return Sent(False, kind, message_id, f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------
def send_password_reset(to: str, link: str) -> Sent:
    return send(
        to,
        "Reset your OMNIX password",
        "Someone asked to reset the password for this OMNIX account.\n\n"
        f"{link}\n\n"
        "This link expires shortly. If it wasn't you, no action is needed — "
        "your password has not changed.\n",
    )


def send_receipt(to: str, plan: str, amount: str, period: str) -> Sent:
    return send(
        to,
        f"Your OMNIX {plan} receipt",
        f"Thanks — your OMNIX {plan} plan is active.\n\n"
        f"Amount: {amount}\nPeriod: {period}\n\n"
        "You can see your usage and change your plan in Settings.\n",
    )


def send_quota_warning(to: str, metric: str, used: int, limit: int) -> Sent:
    return send(
        to,
        f"You have used {used} of {limit} {metric} this period",
        f"OMNIX is letting you know early rather than at the point it stops.\n\n"
        f"{metric.title()}: {used} of {limit} used.\n\n"
        "Nothing has been interrupted. If you expect to need more, you can "
        "change plan in Settings.\n",
    )
