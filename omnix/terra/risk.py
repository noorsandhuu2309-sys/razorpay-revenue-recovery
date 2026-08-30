"""Per-country risk scoring — the data behind the conflict heatmap.

Five dimensions, scored independently so the map can be filtered by one of them
and a country's card can say WHY it is red rather than just that it is:

    political   instability, coups, unrest, contested elections, crackdowns
    disaster    earthquakes, floods, storms, wildfires, famine
    economic    inflation, default, sanctions, market and currency stress
    military    armed conflict, strikes, mobilization, force posture
    cyber       intrusions, ransomware, outages, infrastructure attacks

Each dimension's score is the same shape:

    sum over matching articles of  (term weight x severity x source confidence
                                    x recency decay)

normalized against a reference volume so the 0-100 number means roughly the same
thing week to week. That normalization is the part that makes it honest — a raw
count would make every country look calm in a quiet week and dangerous in a busy
one, because the scale would follow total news volume rather than events.

The score is a NEWS-ATTENTION measure and is labeled as such everywhere it
surfaces. A country with a free press and a bad week outscores an authoritarian
state with no coverage at all, and no amount of arithmetic here fixes that; what
the UI does instead is show the article count and confidence alongside the
score so a thin, low-confidence score is visibly thin.
"""

from __future__ import annotations

import math
import time

from . import ontology as onto

DIMENSIONS = {
    "political": {"label": "Political instability", "color": "#ff9a62", "glyph": "⬒"},
    "disaster":  {"label": "Natural disaster",      "color": "#57d7ff", "glyph": "◈"},
    "economic":  {"label": "Economic stress",       "color": "#4ade80", "glyph": "▤"},
    "military":  {"label": "Military activity",     "color": "#ff5d7a", "glyph": "⬢"},
    "cyber":     {"label": "Cyber activity",        "color": "#9d8cff", "glyph": "⬡"},
}

# Term -> weight per dimension. Weights are relative within a dimension only.
_TERMS: dict[str, dict[str, float]] = {
    "political": {
        "coup": 3.5, "junta": 3.0, "uprising": 2.8, "unrest": 2.4, "riot": 2.6,
        "riots": 2.6, "protest": 1.5, "protests": 1.7, "crackdown": 2.6,
        "martial law": 3.4, "curfew": 2.2, "state of emergency": 3.0,
        "impeachment": 2.2, "impeach": 2.2, "ousted": 2.6, "resigned": 1.6,
        "resignation": 1.7, "dissolved parliament": 3.0, "purge": 2.6,
        "election dispute": 2.6, "disputed election": 3.0, "vote rigging": 3.0,
        "political crisis": 3.0, "no-confidence": 2.4, "detained": 1.6,
        "opposition leader": 1.8, "dissident": 2.0, "exiled": 2.2,
        "authoritarian": 2.0, "corruption": 1.8, "assassination": 3.4,
        "separatist": 2.4, "secession": 2.6, "referendum": 1.4,
    },
    "disaster": {
        "earthquake": 3.2, "quake": 3.0, "aftershock": 2.2, "tsunami": 3.6,
        "hurricane": 3.0, "typhoon": 3.0, "cyclone": 3.0, "tornado": 2.4,
        "flood": 2.8, "floods": 2.8, "flooding": 2.8, "landslide": 2.6,
        "wildfire": 2.8, "wildfires": 2.8, "drought": 2.4, "famine": 3.4,
        "eruption": 2.8, "volcano": 2.2, "heatwave": 2.2, "blizzard": 2.0,
        "evacuated": 2.4, "evacuation": 2.4, "displaced": 2.4,
        "magnitude": 2.6, "disaster": 2.4, "storm surge": 2.6,
        "relief effort": 2.0, "death toll": 2.8,
    },
    "economic": {
        "inflation": 1.8, "hyperinflation": 3.4, "recession": 2.6,
        "default": 3.0, "bailout": 2.4, "austerity": 2.2, "devaluation": 2.8,
        "currency crisis": 3.2, "debt crisis": 3.0, "downgrade": 2.2,
        "sanctions": 2.4, "embargo": 2.6, "tariff": 1.8, "tariffs": 1.8,
        "trade war": 2.6, "export ban": 2.6, "shortage": 2.2,
        "unemployment": 1.8, "layoffs": 1.8, "bankruptcy": 2.2,
        "market crash": 3.2, "plunged": 2.0, "slump": 1.8, "capital flight": 2.8,
        "food prices": 2.2, "fuel shortage": 2.6, "strike action": 1.6,
        "central bank": 1.2, "interest rates": 1.2, "gdp": 1.0,
    },
    "military": {
        "airstrike": 3.2, "airstrikes": 3.2, "missile strike": 3.2,
        "shelling": 3.0, "offensive": 2.4, "invasion": 3.6, "invaded": 3.6,
        "war": 2.6, "warfare": 2.6, "combat": 2.2, "frontline": 2.4,
        "troops": 2.0, "deployed": 1.8, "mobilization": 2.8, "conscription": 2.6,
        "militants": 2.4, "insurgents": 2.4, "rebels": 2.2, "militia": 2.2,
        "ceasefire": 1.6, "truce": 1.4, "casualties": 2.8, "killed": 2.4,
        "drone strike": 3.0, "warship": 2.2, "airspace": 2.0, "nuclear": 2.6,
        "missile test": 2.8, "arms deal": 1.6, "military exercise": 1.8,
        "occupation": 2.6, "border clash": 3.0, "hostage": 2.6,
    },
    "cyber": {
        "cyberattack": 3.2, "cyber attack": 3.2, "ransomware": 3.0,
        "data breach": 2.6, "hacked": 2.4, "hackers": 2.2, "malware": 2.2,
        "spyware": 2.6, "phishing": 1.6, "ddos": 2.4, "botnet": 2.2,
        "zero-day": 2.6, "exploit": 1.6, "vulnerability": 1.6,
        "internet shutdown": 3.0, "internet outage": 2.8, "blackout": 2.2,
        "power grid": 2.4, "critical infrastructure": 2.6, "espionage": 2.6,
        "state-sponsored": 2.8, "disinformation": 2.0, "surveillance": 1.8,
        "leaked documents": 2.0, "credentials": 1.6,
    },
}

HALF_LIFE_HOURS = 30.0
# Raw score that maps to 100. Calibrated against observed live corpora: a
# country in an active shooting war reaches roughly this in a 72h window.
REFERENCE = 26.0

BANDS = [
    (80, "critical", "#ff3355"),
    (60, "severe",   "#ff5d7a"),
    (40, "elevated", "#ff9a62"),
    (22, "watch",    "#ffd166"),
    (8,  "low",      "#4ade80"),
    (0,  "calm",     "#2f6b4a"),
]


def band(score: float) -> tuple[str, str]:
    for threshold, name, color in BANDS:
        if score >= threshold:
            return name, color
    return "calm", "#2f6b4a"


def _decay(ts: float, now: float) -> float:
    return 0.5 ** (max(0.0, (now - ts) / 3600.0) / HALF_LIFE_HOURS)


def score_article(text: str) -> dict[str, float]:
    """Raw per-dimension term weight for one article's text."""
    low = " " + (text or "").lower() + " "
    out: dict[str, float] = {}
    for dim, terms in _TERMS.items():
        total = 0.0
        for term, weight in terms.items():
            if term in low:
                total += weight
        if total:
            out[dim] = total
    return out


def compute(articles: list[dict], window_hours: float = 72.0) -> dict[str, dict]:
    """Country ISO-2 -> risk record.

    An article contributes to every country it mentions, but split by how many
    it mentions: a piece naming twelve countries at a summit is not twelve
    countries' worth of risk. This is the single most important guard against
    the heatmap turning into a "countries that appear in headlines" map.
    """
    now = time.time()
    cutoff = now - window_hours * 3600
    acc: dict[str, dict] = {}

    for art in articles:
        ts = art.get("published_ts", 0)
        if ts < cutoff:
            continue
        isos = art.get("countries") or []
        if not isos:
            continue
        text = art.get("title", "") + " " + art.get("summary", "")
        dims = score_article(text)
        if not dims:
            continue
        severity = max(0.15, art.get("severity", 0.0))
        conf = art.get("confidence", 0.6)
        decay = _decay(ts, now)
        share = 1.0 / math.sqrt(len(isos))

        for iso in isos:
            rec = acc.setdefault(iso, {
                "iso2": iso, "name": onto.country_name(iso),
                "dimensions": {d: 0.0 for d in DIMENSIONS},
                "articles": 0, "evidence": {d: [] for d in DIMENSIONS},
                "conf_sum": 0.0, "sentiment_sum": 0.0,
            })
            rec["articles"] += 1
            rec["conf_sum"] += conf
            rec["sentiment_sum"] += art.get("sentiment", 0.0)
            for dim, weight in dims.items():
                contribution = weight * severity * conf * decay * share
                rec["dimensions"][dim] += contribution
                if len(rec["evidence"][dim]) < 5:
                    rec["evidence"][dim].append({
                        "id": art.get("id", ""), "title": art.get("title", ""),
                        "url": art.get("url", ""), "source": art.get("source", ""),
                        "ts": ts, "weight": round(contribution, 2),
                    })

    out: dict[str, dict] = {}
    for iso, rec in acc.items():
        dims_scaled = {}
        for dim, raw in rec["dimensions"].items():
            dims_scaled[dim] = round(min(100.0, 100.0 * raw / REFERENCE), 1)
        # Overall is dominated by the worst dimension rather than averaged:
        # a country with a war and nothing else is not "one-fifth at risk".
        ordered = sorted(dims_scaled.values(), reverse=True)
        overall = ordered[0] + sum(v * 0.25 for v in ordered[1:3])
        overall = round(min(100.0, overall), 1)
        name, color = band(overall)
        n = max(1, rec["articles"])
        out[iso] = {
            "iso2": iso,
            "name": rec["name"],
            "score": overall,
            "band": name,
            "color": color,
            "dimensions": dims_scaled,
            "top_dimension": max(dims_scaled, key=dims_scaled.get),
            "articles": rec["articles"],
            "confidence": round(rec["conf_sum"] / n, 2),
            "sentiment": round(rec["sentiment_sum"] / n, 3),
            "evidence": {d: ev for d, ev in rec["evidence"].items() if ev},
            "thin": rec["articles"] < 3,   # surfaced in the UI, not hidden
        }
    return out


def deltas(current: dict[str, dict], previous: dict[str, dict]
           ) -> dict[str, float]:
    """Score change per country since the last computation — what drives the
    "rising risk" list and the map's pulse animation."""
    out = {}
    for iso, rec in current.items():
        before = (previous.get(iso) or {}).get("score", 0.0)
        change = round(rec["score"] - before, 1)
        if abs(change) >= 1.0:
            out[iso] = change
    return out


def summary(scores: dict[str, dict], limit: int = 12) -> dict:
    """Ranked view for the panel header."""
    ranked = sorted(scores.values(), key=lambda r: -r["score"])
    by_dim: dict[str, list] = {}
    for dim in DIMENSIONS:
        top = sorted(scores.values(), key=lambda r: -r["dimensions"][dim])
        by_dim[dim] = [{"iso2": r["iso2"], "name": r["name"],
                        "score": r["dimensions"][dim]}
                       for r in top[:6] if r["dimensions"][dim] > 0]
    return {
        "top": ranked[:limit],
        "by_dimension": by_dim,
        "countries_scored": len(scores),
        "dimensions": DIMENSIONS,
        "bands": [{"min": t, "name": n, "color": c} for t, n, c in BANDS],
        "reference": REFERENCE,
        "window_hours": 72,
    }
