"""SENTINEL — Security & ENdpoint Threat INtelligence Evaluation Layer.

Points at a URL and runs a defensive, non-intrusive security review: fetches
the page + headers, audits security headers, flags exposed info / mixed
content / insecure transport, then an LLM threat-models the surface. Read-only:
it never sends attack payloads — it inspects what the server already returns.
"""

from __future__ import annotations

import re
import socket
import ssl
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx

from .base import (MODEL_SMART, Subagent, Unit, UnitResult, cards_block, clamp,
                   list_block, markdown_block, stats_block)

# Header -> (severity, human explanation, remediation) when MISSING.
_SECURITY_HEADERS = {
    "content-security-policy": (
        "high", "No CSP — XSS and injection have no baseline mitigation.",
        "Add a Content-Security-Policy starting in report-only mode, then enforce a locked-down default-src."),
    "strict-transport-security": (
        "high", "No HSTS — connections can be downgraded to HTTP.",
        "Send Strict-Transport-Security: max-age=63072000; includeSubDomains; preload."),
    "x-content-type-options": (
        "medium", "Missing nosniff — MIME-sniffing risks.",
        "Add X-Content-Type-Options: nosniff."),
    "x-frame-options": (
        "medium", "No frame protection — clickjacking possible (unless CSP frame-ancestors).",
        "Add X-Frame-Options: DENY, or a CSP frame-ancestors 'none' directive."),
    "referrer-policy": (
        "low", "No referrer policy — URLs may leak to third parties.",
        "Add Referrer-Policy: strict-origin-when-cross-origin."),
    "permissions-policy": (
        "low", "No permissions policy — powerful browser features left open.",
        "Add a Permissions-Policy that denies the browser features the app does not use."),
}

# Informational / positive response headers surfaced in the "Headers present"
# panel when the origin actually sends them.
_POSITIVE_HEADERS = [
    "content-type", "x-xss-protection", "cache-control", "vary",
    "content-encoding", "x-content-type-options",
]

_SEV_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


def _tls_info(final_url: str) -> list[dict]:
    """Best-effort TLS/transport posture for the SSL panel. Never raises —
    returns rows describing the live connection (or cleartext for http://)."""
    p = urlparse(final_url)
    if p.scheme != "https":
        return [
            {"k": "transport", "v": "HTTP · cleartext", "state": "bad"},
            {"k": "tls", "v": "none", "state": "bad"},
            {"k": "hsts", "v": "n/a over http", "state": "bad"},
        ]
    host, port = p.hostname or "", p.port or 443
    rows: list[dict] = [{"k": "transport", "v": "HTTPS · encrypted", "state": "ok"}]
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((host, port), timeout=6) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as ss:
                version = ss.version() or "?"
                cert = ss.getpeercert() or {}
        rows.append({"k": "tls", "v": version,
                     "state": "ok" if version in ("TLSv1.3", "TLSv1.2") else "warn"})
        issuer = ""
        for part in cert.get("issuer", ()):  # tuple of RDNs
            for k, v in part:
                if k == "organizationName":
                    issuer = v
        if issuer:
            rows.append({"k": "issuer", "v": clamp(issuer, 28), "state": "ok"})
        not_after = cert.get("notAfter")
        if not_after:
            try:
                exp = datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
                days = (exp - datetime.now(timezone.utc)).days
                st = "ok" if days > 30 else ("warn" if days > 7 else "bad")
                rows.append({"k": "expiry", "v": f"in {days} days", "state": st})
            except Exception:
                rows.append({"k": "expiry", "v": clamp(not_after, 22), "state": "warn"})
    except Exception as e:
        rows.append({"k": "tls", "v": f"handshake failed: {clamp(str(e), 24)}",
                     "state": "warn"})
    return rows


def _cors_info(headers: dict) -> list[dict]:
    """CORS posture from the response headers actually returned."""
    origin = headers.get("access-control-allow-origin")
    creds = headers.get("access-control-allow-credentials")
    methods = headers.get("access-control-allow-methods")
    rows: list[dict] = []
    if origin is None and creds is None and methods is None:
        return [{"k": "allow-origin", "v": "not sent", "state": "ok"}]
    if origin is not None:
        wildcard = origin.strip() == "*"
        bad = wildcard and (creds or "").lower() == "true"
        rows.append({"k": "allow-origin", "v": clamp(origin, 24),
                     "state": "bad" if bad else ("warn" if wildcard else "ok")})
    if creds is not None:
        rows.append({"k": "allow-credentials", "v": creds,
                     "state": "bad" if creds.lower() == "true" and (origin or "").strip() == "*" else "ok"})
    if methods is not None:
        rows.append({"k": "allow-methods", "v": clamp(methods, 22), "state": "ok"})
    return rows

_URL_IN_TEXT = re.compile(r"https?://[^\s\"'<>)]+", re.IGNORECASE)
# A bare hostname like "example.com" or "sub.example.co.uk/path" (no scheme).
_BARE_DOMAIN = re.compile(
    r"\b((?:[a-z0-9-]+\.)+[a-z]{2,}(?:/[^\s\"'<>)]*)?)\b", re.IGNORECASE)


def _extract_url(text: str) -> str:
    """Pull a scannable URL out of free-form input.

    Handles NOVA routing a whole sentence ('scan https://x for issues'), a bare
    URL, or a bare domain ('example.com' -> 'https://example.com'). Returns ''
    if nothing URL-like is present.
    """
    text = (text or "").strip()
    if not text:
        return ""
    if re.match(r"^https?://", text, re.IGNORECASE):
        return text.split()[0]
    m = _URL_IN_TEXT.search(text)
    if m:
        return m.group(0)
    m = _BARE_DOMAIN.search(text)
    if m:
        return "https://" + m.group(1)
    return ""


_SECRET_PATTERNS = [
    ("AWS access key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("Google API key", re.compile(r"AIza[0-9A-Za-z\-_]{35}")),
    ("Bearer/JWT token", re.compile(r"eyJ[A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+")),
    ("Private key block", re.compile(r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----")),
    ("Generic api_key=", re.compile(r"(?i)api[_-]?key['\"]?\s*[:=]\s*['\"][A-Za-z0-9\-_]{16,}")),
]


class Sentinel(Unit):
    code = "sentinel"
    name = "SENTINEL"
    glyph = "⬡"
    tagline = "Security & ENdpoint Threat INtelligence Evaluation Layer"
    blurb = "Non-intrusive security posture scan of a live URL."
    accent = "#ff5d7a"
    input_label = "Target URL"
    input_kind = "url"
    placeholder = "https://example.com"

    def __init__(self):
        self.subagents = [
            Subagent("Recon", "captures the target's response surface"),
            Subagent("Header Auditor", "checks security response headers"),
            Subagent("Exposure Scanner", "flags leaked secrets & insecure content"),
            Subagent("Threat Modeler", "reasons about likely attack surface",
                     model=MODEL_SMART, system=(
                         "You are SENTINEL's Threat Modeler, a defensive security "
                         "analyst. Given a target's headers and deterministic "
                         "findings, summarize the realistic risk posture and the "
                         "top hardening priorities. Be practical and non-alarmist. "
                         "Never suggest offensive exploitation steps.")),
            Subagent("Reporter", "compiles the security brief", model=MODEL_SMART,
                     system="You are SENTINEL's Reporter."),
        ]

    def run(self, ctx, emit) -> UnitResult:
        url = _extract_url(ctx.get("input") or "")
        res = UnitResult()
        if not url:
            res.summary = ("No URL found in the request. Give SENTINEL a target "
                           "like https://example.com.")
            return res

        # Scan profile: 'full' (all 5 subagents), 'quick' (deterministic, no LLM),
        # 'headers' (recon + header audit only — no content/exposure scan, no LLM).
        profile = (ctx.get("profile") or "full").strip().lower()
        if profile not in ("full", "quick", "headers"):
            profile = "full"

        findings: list[dict] = []

        # 1) Recon — fetch headers + body (read-only GET).
        emit("recon", f"Recon fetching {url}")
        headers, body, status_code, err = {}, "", None, ""
        try:
            with httpx.Client(follow_redirects=True, timeout=12.0,
                              headers={"User-Agent": "OMNIX-SENTINEL/1.0"}) as c:
                r = c.get(url)
            status_code = r.status_code
            headers = {k.lower(): v for k, v in r.headers.items()}
            body = r.text[:200_000]
            final_url = str(r.url)
        except Exception as e:
            res.summary = f"Could not reach target: {e}"
            return res

        # Transport check.
        if final_url.lower().startswith("http://"):
            findings.append(dict(severity="high", source="transport", title="Served over plain HTTP",
                                 detail="Traffic is unencrypted and open to interception/MITM.",
                                 remediation="Redirect all HTTP to HTTPS (301) and enable HSTS so browsers refuse the downgrade."))

        # 2) Header audit.
        emit("headers", "Header Auditor reviewing response headers")
        present = []
        for h, (sev, why, fix) in _SECURITY_HEADERS.items():
            if h in headers:
                present.append(h)
            else:
                findings.append(dict(severity=sev, source="headers",
                                     title=f"Missing header: {h}", detail=why, remediation=fix))
        server_banner = headers.get("server") or headers.get("x-powered-by")
        if server_banner:
            findings.append(dict(severity="low", source="headers", title="Server version disclosed",
                                 detail=f"`{clamp(server_banner,80)}` in response headers aids fingerprinting.",
                                 remediation="Strip or generalize the Server / X-Powered-By header at the edge."))

        # 3) Exposure scan of body (skipped for the headers-only profile).
        if profile != "headers":
            emit("exposure", "Exposure Scanner checking content")
            for label, pat in _SECRET_PATTERNS:
                if pat.search(body):
                    findings.append(dict(severity="critical", source="exposure",
                                         title=f"Possible {label} in page source",
                                         detail="A secret-looking string is exposed in the returned HTML/JS.",
                                         remediation="Rotate the credential immediately, purge it from the bundle and git history, and move privileged calls server-side."))
            if final_url.lower().startswith("https://"):
                mixed = re.findall(r'(?:src|href)=["\']http://[^"\']+', body)
                if mixed:
                    findings.append(dict(severity="medium", source="transport", title="Mixed content",
                                         detail=f"{len(mixed)} resource(s) loaded over http:// on an https page.",
                                         remediation="Load every subresource over https:// and send Content-Security-Policy: upgrade-insecure-requests."))
            if "<form" in body.lower() and 'type="password"' in body.lower() \
                    and final_url.lower().startswith("http://"):
                findings.append(dict(severity="critical", source="transport", title="Password form on insecure page",
                                     detail="Credentials would be submitted without transport encryption.",
                                     remediation="Serve the whole origin over HTTPS and point the form action at an https:// endpoint."))

        # 4) LLM threat model (best-effort; full profile only).
        model_txt = ""
        if profile == "full":
            emit("threat", "Threat Modeler assessing posture")
            hdr_summary = "\n".join(f"{k}: {clamp(v,120)}" for k, v in list(headers.items())[:25])
            find_summary = "\n".join(f"- [{f['severity']}] {f['title']}" for f in findings) or "- none"
            model_txt = self.subagents[3].complete(
                f"URL: {final_url}\nHTTP status: {status_code}\n\nHEADERS:\n{hdr_summary}\n\n"
                f"DETERMINISTIC FINDINGS:\n{find_summary}\n\n"
                "Give a short risk posture summary and the top 3 hardening priorities.")

        # Assemble.
        emit("report", "Reporter compiling brief")
        findings.sort(key=lambda f: _SEV_ORDER.get(f["severity"], 9))
        counts: dict[str, int] = {}
        for f in findings:
            counts[f["severity"]] = counts.get(f["severity"], 0) + 1
        high_plus = counts.get("critical", 0) + counts.get("high", 0)

        res.summary = clamp(model_txt or
                            (f"Scanned {final_url}: {len(findings)} posture findings, "
                             f"{len(present)} security headers present."), 3000)
        res.add(stats_block([
            {"n": str(len(findings)), "label": "Findings"},
            {"n": str(high_plus), "label": "High+"},
            {"n": str(len(present)), "label": "Headers OK"},
            {"n": str(status_code), "label": "HTTP"},
        ]))
        res.add(cards_block("Findings", [
            {"title": f["title"], "badge": f["severity"],
             "badge_color": {"critical": "#ff5d7a", "high": "#ff9a62",
                             "medium": "#ffd166", "low": "#57d7ff"}.get(f["severity"], "#8a92a6"),
             "body": f["detail"]}
            for f in findings
        ] or [{"title": "No posture issues detected", "badge": "ok",
               "badge_color": "#4ade80", "body": "All checked headers present, no exposures found."}]))
        if present:
            res.add(list_block("Security headers present", present))

        # --- Rich structured meta for the SENTINEL console UI ------------------
        # Letter grade from the worst severity present.
        if counts.get("critical"):
            grade, grade_label = "F", "Critical exposure"
        elif counts.get("high"):
            grade, grade_label = "D", "High risk"
        elif counts.get("medium"):
            grade, grade_label = "C", "Needs work"
        elif counts.get("low"):
            grade, grade_label = "B", "Moderate"
        else:
            grade, grade_label = "A", "Solid posture"

        # Actual audited-header values (present -> value, missing -> not set).
        headers_grid = []
        for h, (sev, _why, _fix) in _SECURITY_HEADERS.items():
            if h in headers:
                headers_grid.append({"name": h, "value": clamp(headers[h], 46), "state": "ok"})
            else:
                headers_grid.append({"name": h, "value": "— not set",
                                     "state": "bad" if sev == "high" else "warn"})
        if server_banner:
            headers_grid.append({"name": "server", "value": clamp(server_banner, 46), "state": "warn"})

        # Notable positive/informational headers the origin actually sends.
        headers_present = [f"{h}: {clamp(headers[h], 60)}"
                           for h in _POSITIVE_HEADERS if h in headers]
        if not headers_present:
            headers_present = [f"{h}: {clamp(headers[h], 60)}"
                               for h in list(headers)[:4]]

        # Top hardening priorities — the remediation of the worst findings.
        priorities = [f["remediation"] for f in findings
                      if f.get("remediation")][:3]

        res.meta = {
            "severity_counts": counts,
            "url": final_url,
            "profile": profile,
            "status_code": status_code,
            "high_plus": high_plus,
            "headers_ok": len(present),
            "grade": grade,
            "grade_label": grade_label,
            "findings": findings,
            "headers_grid": headers_grid,
            "headers_present": headers_present,
            "ssl": _tls_info(final_url),
            "cors": _cors_info(headers),
            "priorities": priorities,
        }
        return res
