"""Live event detection — noticing that something started, without being asked.

The signal is article velocity against that subject's own baseline. Absolute
volume is useless for this: forty articles about US politics is a Tuesday, four
about Gabon is a coup. So every cluster and every country is compared against
what IT normally produces, and the alert fires on the ratio.

Two detectors:

    cluster bursts   a story whose article rate jumped over its own early rate
    country bursts   a country producing far more coverage than its baseline

Both emit the same alert shape so the UI renders one list. Every alert carries
the articles that triggered it — an unexplained "possible coup detected" banner
is worse than no banner, because the user cannot check it.
"""

from __future__ import annotations

import math
import time
from collections import defaultdict

from . import ontology as onto
from .nlp import source_confidence

# A burst needs BOTH a rate multiple and a floor of absolute articles — without
# the floor, one article where there were normally none is an infinite ratio.
MIN_ARTICLES = 3
MIN_SOURCES = 2
RATE_MULTIPLE = 2.5
RECENT_HOURS = 6.0
BASELINE_HOURS = 72.0

SEVERITY_WORDS = {
    "coup": ("Possible coup", 0.95), "military takeover": ("Military takeover", 0.95),
    "state of emergency": ("State of emergency", 0.85),
    "martial law": ("Martial law", 0.9),
    "assassination": ("Assassination reported", 0.9),
    "invasion": ("Invasion reported", 0.95), "invaded": ("Invasion reported", 0.95),
    "earthquake": ("Major earthquake", 0.85), "tsunami": ("Tsunami", 0.95),
    "eruption": ("Volcanic eruption", 0.8),
    "airstrike": ("Airstrikes reported", 0.8), "airstrikes": ("Airstrikes reported", 0.8),
    "ceasefire": ("Ceasefire", 0.7), "truce": ("Truce", 0.7),
    "resigned": ("Leadership change", 0.7), "ousted": ("Leadership change", 0.8),
    "default": ("Sovereign default", 0.85),
    "cyberattack": ("Major cyber incident", 0.75),
    "outbreak": ("Disease outbreak", 0.8), "pandemic": ("Pandemic escalation", 0.9),
    "nuclear": ("Nuclear development", 0.8),
    "evacuation": ("Mass evacuation", 0.75),
    "shot down": ("Aircraft downed", 0.85),
    "explosion": ("Major explosion", 0.75),
}


def _headline_kind(text: str) -> tuple[str, float]:
    low = " " + (text or "").lower() + " "
    best, best_w = "", 0.0
    for term, (label, weight) in SEVERITY_WORDS.items():
        if term in low and weight > best_w:
            best, best_w = label, weight
    return best, best_w


def cluster_bursts(clusters: list[dict], articles_by_id: dict[str, dict],
                   now: float | None = None) -> list[dict]:
    """Stories accelerating right now."""
    now = now or time.time()
    out = []
    for cluster in clusters:
        members = [articles_by_id[a] for a in cluster["article_ids"]
                   if a in articles_by_id]
        if len(members) < MIN_ARTICLES:
            continue
        recent = [m for m in members if now - m.get("published_ts", 0) <= RECENT_HOURS * 3600]
        if len(recent) < MIN_ARTICLES:
            continue
        sources = {m.get("source", "") for m in recent if m.get("source")}
        if len(sources) < MIN_SOURCES:
            continue

        older = [m for m in members if m not in recent]
        recent_rate = len(recent) / RECENT_HOURS
        older_span = max(RECENT_HOURS, (now - cluster["first_ts"]) / 3600.0 - RECENT_HOURS)
        older_rate = len(older) / older_span if older else 0.0
        # A brand-new story has no baseline; treat "nothing before" as the
        # weakest baseline that still clears the bar, so genuinely new events
        # can fire but a slow trickle cannot.
        ratio = recent_rate / older_rate if older_rate > 0 else RATE_MULTIPLE
        if ratio < RATE_MULTIPLE:
            continue

        kind, kind_weight = _headline_kind(
            " ".join(m["title"] for m in recent[:6]))
        corroboration = min(1.0, sum(source_confidence(s) for s in sources) / 2.6)
        # Confidence in the DETECTION, distinct from confidence in the claim.
        confidence = round(min(0.97, 0.35 + 0.35 * corroboration
                               + 0.2 * min(1.0, ratio / 5.0)
                               + 0.1 * kind_weight), 2)

        out.append({
            "kind": "cluster",
            "id": "burst_" + cluster["id"],
            "cluster": cluster["id"],
            "label": kind or "Developing story",
            "title": cluster["title"],
            "url": cluster.get("url", ""),
            "countries": cluster.get("countries", []),
            "country_names": [onto.country_name(c) for c in cluster.get("countries", [])],
            "domains": cluster.get("domains", []),
            "articles_recent": len(recent),
            "articles_total": len(members),
            "sources": sorted(sources),
            "source_count": len(sources),
            "rate_ratio": round(ratio, 2),
            "severity": cluster.get("severity", 0.0),
            "sentiment": cluster.get("sentiment", 0.0),
            "corroboration": round(corroboration, 2),
            "confidence": confidence,
            "detected_at": now,
            "evidence": [{"title": m["title"], "url": m.get("url", ""),
                          "source": m.get("source", ""),
                          "ts": m.get("published_ts", 0)}
                         for m in sorted(recent, key=lambda m: -m.get("published_ts", 0))[:6]],
        })
    out.sort(key=lambda a: -(a["confidence"] * (1 + a["severity"])))
    return out


def country_bursts(articles: list[dict], now: float | None = None,
                   min_ratio: float = 3.0) -> list[dict]:
    """Countries whose coverage volume spiked against their own baseline."""
    now = now or time.time()
    recent: dict[str, list] = defaultdict(list)
    baseline: dict[str, int] = defaultdict(int)

    for art in articles:
        age_h = (now - art.get("published_ts", 0)) / 3600.0
        if age_h < 0 or age_h > BASELINE_HOURS:
            continue
        for iso in art.get("countries") or []:
            if age_h <= RECENT_HOURS:
                recent[iso].append(art)
            else:
                baseline[iso] += 1

    out = []
    for iso, arts in recent.items():
        if len(arts) < MIN_ARTICLES:
            continue
        sources = {a.get("source", "") for a in arts if a.get("source")}
        if len(sources) < MIN_SOURCES:
            continue
        recent_rate = len(arts) / RECENT_HOURS
        base_rate = baseline[iso] / (BASELINE_HOURS - RECENT_HOURS)
        ratio = recent_rate / base_rate if base_rate > 0 else min_ratio
        if ratio < min_ratio:
            continue
        worst = min(arts, key=lambda a: a.get("sentiment", 0))
        kind, kind_weight = _headline_kind(" ".join(a["title"] for a in arts[:8]))
        corroboration = min(1.0, sum(source_confidence(s) for s in sources) / 2.6)
        out.append({
            "kind": "country",
            "id": "burst_c_" + iso,
            "label": kind or "Coverage surge",
            "title": f"{onto.country_name(iso)}: {len(arts)} stories in {int(RECENT_HOURS)}h",
            "url": worst.get("url", ""),
            "countries": [iso],
            "country_names": [onto.country_name(iso)],
            "domains": sorted({d for a in arts for d in a.get("domains", [])})[:3],
            "articles_recent": len(arts),
            "articles_total": len(arts) + baseline[iso],
            "sources": sorted(sources),
            "source_count": len(sources),
            "rate_ratio": round(ratio, 2),
            "severity": round(max(a.get("severity", 0) for a in arts), 3),
            "sentiment": round(sum(a.get("sentiment", 0) for a in arts) / len(arts), 3),
            "corroboration": round(corroboration, 2),
            "confidence": round(min(0.95, 0.3 + 0.35 * corroboration
                                    + 0.2 * min(1.0, ratio / 6.0)
                                    + 0.1 * kind_weight), 2),
            "detected_at": now,
            "evidence": [{"title": a["title"], "url": a.get("url", ""),
                          "source": a.get("source", ""),
                          "ts": a.get("published_ts", 0)}
                         for a in sorted(arts, key=lambda a: a.get("sentiment", 0))[:6]],
        })
    out.sort(key=lambda a: -(a["confidence"] * (1 + a["severity"])))
    return out


def detect(clusters: list[dict], articles: list[dict],
           limit: int = 16) -> list[dict]:
    """Both detectors, merged and de-overlapped.

    A country burst caused entirely by one story is that story's alert, not a
    second one — so country alerts are dropped when a cluster alert already
    covers the same country.
    """
    by_id = {a["id"]: a for a in articles}
    alerts = cluster_bursts(clusters, by_id)
    covered = {iso for a in alerts for iso in a["countries"]}
    for alert in country_bursts(articles):
        if alert["countries"][0] in covered:
            continue
        alerts.append(alert)
    alerts.sort(key=lambda a: -(a["confidence"] * (1 + a["severity"])))
    return alerts[:limit]


def alert_line(alert: dict) -> str:
    """One-sentence phrasing for the ticker, hedged to the evidence.

    The wording matters: this is a statistical anomaly in news volume, not a
    confirmed event, and the copy says so at low confidence instead of
    announcing a coup on the strength of three articles.
    """
    where = ", ".join(alert["country_names"][:2]) or "multiple countries"
    n, s = alert["articles_recent"], alert["source_count"]
    if alert["confidence"] >= 0.8:
        return f"{alert['label']} — {where}: {n} reports across {s} outlets"
    if alert["confidence"] >= 0.6:
        return f"Possible {alert['label'].lower()} — {where}: {n} reports, {s} outlets"
    return (f"Unverified signal — {where}: coverage up "
            f"{alert['rate_ratio']}x ({n} reports, {s} outlets)")
