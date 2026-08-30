"""Generated intelligence products: briefings, situation reports, analogues.

Three things a reader asks for that all consume the same upstream state (corpus,
clusters, graph, risk) and differ only in framing and depth:

    brief()      the same picture at four lengths, from 30 seconds to research
    situation()  one named theatre, in depth, with players and a map extent
    analogues()  historical precedents for a live event, and how those ended

The historical layer is the one worth being careful about. Its analogues come
from the model's own knowledge, not from the corpus, so unlike everything else
in TERRA they are NOT citable to a source the user can click. That distinction
is carried in the payload (`grounded: false`) and rendered as an explicit
caveat, rather than being quietly mixed in with sourced material.
"""

from __future__ import annotations

import time

from . import ontology as onto

# ---------------------------------------------------------------------------
# 1. Briefings
# ---------------------------------------------------------------------------
BRIEF_FORMATS = {
    "flash": {
        "label": "30-second flash", "words": 70, "events": 4,
        "instruction": ("Three or four sentences. Only what a decision-maker "
                        "must know before their next meeting. No preamble."),
    },
    "standard": {
        "label": "2-minute brief", "words": 260, "events": 8,
        "instruction": ("Around 250 words in short paragraphs. Lead with the "
                        "single most important development, then the other "
                        "significant ones, then what to watch."),
    },
    "executive": {
        "label": "Executive report", "words": 650, "events": 14,
        "instruction": ("Around 600 words. Structure with short bolded section "
                        "headers: Situation, Assessment, Implications, Watch "
                        "items. Written for a reader who will act on it."),
    },
    "research": {
        "label": "Research report", "words": 1200, "events": 20,
        "instruction": ("Around 1100 words. Full analytical treatment: "
                        "background, current state, competing interpretations, "
                        "second-order effects, confidence and gaps. Say "
                        "explicitly where the reporting is thin or where "
                        "outlets disagree."),
    },
}

_BRIEF_SYSTEM = (
    "You are the duty officer writing an intelligence briefing from a live "
    "event digest.\n\n"
    "Rules that matter more than style:\n"
    "- Use ONLY events from the digest. Never add an event from memory.\n"
    "- Every event in the digest has a corroboration score and an outlet count. "
    "Weight your treatment by them, and say when something is thinly sourced.\n"
    "- Distinguish what HAPPENED from what is CLAIMED or EXPECTED.\n"
    "- No filler, no 'in today's rapidly changing world'. Start with substance."
)


def brief(fmt: str, clusters: list[dict], risk_scores: dict,
          alerts: list[dict], analysis: dict | None = None,
          use_llm: bool = True) -> dict:
    """Generate a briefing at one of four lengths."""
    spec = BRIEF_FORMATS.get(fmt) or BRIEF_FORMATS["standard"]
    top = clusters[:spec["events"]]

    result = {
        "format": fmt,
        "label": spec["label"],
        "text": "",
        "events": [{"id": c["id"], "title": c["title"], "url": c.get("url", ""),
                    "sources": c["source_count"],
                    "corroboration": c["corroboration"]} for c in top],
        "alerts": [{"label": a["label"], "title": a["title"],
                    "confidence": a["confidence"]} for a in alerts[:4]],
        "risk_top": [{"iso2": r["iso2"], "name": r["name"], "score": r["score"],
                      "band": r["band"]}
                     for r in sorted(risk_scores.values(),
                                     key=lambda r: -r["score"])[:6]],
        "generated_at": time.time(),
        "mode": "deterministic",
        "grounded": True,
    }
    if not top:
        result["text"] = "No significant events in the current window."
        return result

    # Deterministic briefing: a real, readable digest with no model involved.
    lines = [f"{len(clusters)} events tracked. Leading developments:"]
    for i, c in enumerate(top[:6], start=1):
        where = ", ".join(onto.country_name(x) for x in c.get("countries", [])[:2])
        lines.append(f"{i}. {c['title']}"
                     + (f" ({where})" if where else "")
                     + f" — {c['source_count']} outlets, corroboration "
                       f"{c['corroboration']:.2f}.")
    if alerts:
        lines.append("Active signals: " +
                     "; ".join(a["title"] for a in alerts[:3]) + ".")
    result["text"] = "\n".join(lines)

    if not use_llm:
        return result
    try:
        from ..squad.base import MODEL_SMART, run_llm
    except Exception:
        return result

    digest = []
    for i, c in enumerate(top, start=1):
        where = ", ".join(onto.country_name(x) for x in c.get("countries", [])[:3])
        digest.append(
            f"{i}. {c['title']}\n"
            f"   {c['size']} articles / {c['source_count']} outlets · "
            f"corroboration {c['corroboration']:.2f} · tone {c['sentiment']:+.2f}"
            + (f" · {where}" if where else "")
            + (f" · {c.get('status', {}).get('state', '')}" if c.get("status") else ""))

    extra = []
    if alerts:
        extra.append("ACTIVE DETECTION SIGNALS:\n" + "\n".join(
            f"- {a['label']}: {a['title']} (detection confidence {a['confidence']})"
            for a in alerts[:5]))
    if risk_scores:
        hot = sorted(risk_scores.values(), key=lambda r: -r["score"])[:6]
        extra.append("HIGHEST RISK SCORES (news-attention based, 0-100):\n"
                     + "\n".join(f"- {r['name']}: {r['score']} ({r['band']}, "
                                 f"driven by {r['top_dimension']})" for r in hot))
    if analysis and analysis.get("master", {}).get("headline"):
        m = analysis["master"]
        extra.append(f"DESK ASSESSMENT:\n{m['headline']}\n{m.get('assessment', '')}")

    text = run_llm(
        MODEL_SMART, _BRIEF_SYSTEM + "\n\nFORMAT: " + spec["instruction"],
        "EVENT DIGEST:\n" + "\n".join(digest) + "\n\n" + "\n\n".join(extra),
        temperature=0.35)
    if text.strip():
        result["text"] = text.strip()
        result["mode"] = "llm"
    return result


# ---------------------------------------------------------------------------
# 2. Situation room
# ---------------------------------------------------------------------------
# Named theatres.
#
# `core` is the distinction that makes selection work. `countries` is the full
# cast that can appear in a theatre, but most of them appear in everything —
# the United States is in the cast of five of these eight, so admitting an event
# because it mentions the US put a FIFA governance story into the Taiwan Strait
# report. An event joins a theatre only via a CORE country or a theatre term;
# the wider `countries` list is used for the risk panel and for ranking.
THEATRES = {
    "middle_east": {
        "name": "Middle East", "glyph": "◈",
        "core": ["IL", "PS", "IR", "LB", "SY", "IQ", "YE"],
        "countries": ["IL", "PS", "IR", "LB", "SY", "IQ", "YE", "SA", "EG",
                      "JO", "AE", "QA", "TR"],
        "terms": ["gaza", "israel", "iran", "hezbollah", "hamas", "houthi",
                  "red sea", "hormuz", "west bank", "idf", "tehran"],
        "extent": {"lon": 42, "lat": 29, "scale": 11},
    },
    "russia_ukraine": {
        "name": "Russia–Ukraine", "glyph": "⬢",
        "core": ["UA", "RU", "BY"],
        "countries": ["UA", "RU", "BY", "PL", "MD", "LT", "LV", "EE"],
        "terms": ["ukraine", "russia", "kyiv", "moscow", "donetsk", "kharkiv",
                  "zaporizhzhia", "crimea", "black sea", "nato"],
        "extent": {"lon": 33, "lat": 49, "scale": 12},
    },
    "taiwan": {
        "name": "Taiwan & the Strait", "glyph": "⬡",
        "core": ["TW"],
        "countries": ["TW", "CN", "JP", "PH", "US", "KR"],
        "terms": ["taiwan", "taipei", "taiwan strait", "south china sea",
                  "people's liberation army", "tsmc", "chip export"],
        "extent": {"lon": 120, "lat": 24, "scale": 14},
    },
    "us_politics": {
        "name": "United States", "glyph": "▣",
        "core": ["US"],
        "countries": ["US"],
        "terms": ["white house", "congress", "senate", "supreme court",
                  "election", "federal reserve", "tariff"],
        "extent": {"lon": -96, "lat": 39, "scale": 8},
    },
    "ai_industry": {
        "name": "AI Industry", "glyph": "✦",
        # Purely topical — no core country, because the story is the industry
        # rather than a place.
        "core": [],
        "countries": ["US", "CN", "TW", "KR", "NL", "GB"],
        "terms": ["artificial intelligence", " ai ", "openai", "anthropic",
                  "nvidia", "chipmaking", "semiconductor", "data center",
                  "language model", "compute", "gpu", "deepmind"],
        "extent": {"lon": -60, "lat": 32, "scale": 3},
    },
    "indo_pacific": {
        "name": "Indo-Pacific", "glyph": "◉",
        "core": ["IN", "PK", "PH", "VN", "TW", "MM", "BD"],
        "countries": ["IN", "CN", "PK", "JP", "AU", "PH", "VN", "ID", "KR",
                      "TW", "MM", "BD"],
        "terms": ["indo-pacific", "south china sea", "quad ", "aukus",
                  "line of actual control", "malacca"],
        "extent": {"lon": 100, "lat": 15, "scale": 5},
    },
    "africa_sahel": {
        "name": "Africa & Sahel", "glyph": "◇",
        "core": ["NG", "ML", "NE", "TD", "SD", "ET", "SO", "LY", "CD",
                 "BF", "KE", "ZA"],
        "countries": ["NG", "ML", "NE", "TD", "SD", "ET", "SO", "LY", "CD",
                      "BF", "KE", "ZA"],
        "terms": ["sahel", "ecowas", "african union", "junta", "jihadist"],
        "extent": {"lon": 18, "lat": 10, "scale": 5},
    },
    "energy_markets": {
        "name": "Energy & Commodities", "glyph": "◆",
        "core": [],
        "countries": ["SA", "RU", "US", "IR", "VE", "AE", "QA", "NO"],
        "terms": ["oil price", "opec", "brent", "crude", "natural gas", "lng",
                  "pipeline", "refinery", "barrel", "energy prices",
                  "gas prices", "fuel prices"],
        "extent": {"lon": 30, "lat": 25, "scale": 3},
    },
}


def _theatre_clusters(theatre: dict, clusters: list[dict]) -> list[dict]:
    """Events belonging to a theatre: a CORE country or a theatre term.

    Peripheral countries (the US in the Taiwan cast, Poland in the Ukraine cast)
    raise an event's relevance once it already qualifies, but never admit one on
    their own — that is what kept unrelated superpower stories out.
    """
    core = set(theatre.get("core") or [])
    wider = set(theatre["countries"]) - core
    terms = theatre["terms"]
    out = []
    for c in clusters:
        hay = " " + (c["title"] + " " + " ".join(c.get("keywords", []))).lower() + " "
        countries = set(c.get("countries", []))
        by_core = bool(core & countries)
        by_term = any(t in hay for t in terms)
        if not (by_core or by_term):
            continue
        rec = dict(c)
        rec["_relevance"] = ((2 if by_core else 0) + (2 if by_term else 0)
                             + (1 if wider & countries else 0))
        out.append(rec)
    out.sort(key=lambda c: (-c["_relevance"], -c.get("score", 0)))
    return out


_SITREP_SYSTEM = (
    "You are writing a situation report on one theatre for an intelligence "
    "desk.\n\n"
    "Return JSON:\n"
    "{\"summary\":\"4-6 sentences on the current state\","
    "\"timeline\":[{\"when\":\"...\",\"what\":\"...\"}],"
    "\"players\":[{\"name\":\"...\",\"type\":\"country|organization|person\","
    "\"role\":\"what they are doing in this theatre\","
    "\"posture\":\"escalating|holding|de-escalating|unclear\"}],"
    "\"predictions\":[{\"claim\":\"...\",\"horizon\":\"...\",\"confidence\":0.0}],"
    "\"watch\":[\"indicator that would change the assessment\"],"
    "\"confidence\":0.0,"
    "\"gaps\":[\"what the reporting does not cover\"]}\n\n"
    "Use ONLY the given events. Where outlets are few or corroboration is low, "
    "say so in `gaps` instead of writing around it."
)


def situation(key: str, clusters: list[dict], articles_by_id: dict,
              kg, risk_scores: dict, use_llm: bool = True) -> dict:
    """A full situation report for one named theatre."""
    theatre = THEATRES.get(key)
    if not theatre:
        return {"status": "unknown", "error": f"unknown theatre: {key}",
                "available": list(THEATRES)}

    relevant = _theatre_clusters(theatre, clusters)
    articles = [articles_by_id[a] for c in relevant[:25]
                for a in c.get("article_ids", []) if a in articles_by_id]

    # Graph players: the most connected objects across the theatre's events.
    node_scores: dict[str, float] = {}
    node_meta: dict[str, dict] = {}
    for c in relevant[:20]:
        for ent in c.get("entities", []):
            node_scores[ent["id"]] = node_scores.get(ent["id"], 0) + \
                ent.get("mentions", 1) * (1 + c.get("score", 0) * 0.1)
            node_meta[ent["id"]] = ent
    players = [
        {**node_meta[nid], "weight": round(score, 1)}
        for nid, score in sorted(node_scores.items(), key=lambda x: -x[1])[:14]]

    theatre_risk = [risk_scores[iso] for iso in theatre["countries"]
                    if iso in risk_scores]
    theatre_risk.sort(key=lambda r: -r["score"])

    report = {
        "status": "ok",
        "key": key,
        "name": theatre["name"],
        "glyph": theatre["glyph"],
        "extent": theatre["extent"],
        "countries": theatre["countries"],
        "events": [{"id": c["id"], "title": c["title"], "url": c.get("url", ""),
                    "size": c["size"], "sources": c["source_count"],
                    "corroboration": c["corroboration"],
                    "sentiment": c["sentiment"],
                    "when": c.get("when", ""),
                    "status": c.get("status", {}).get("state", "")}
                   for c in relevant[:16]],
        "event_count": len(relevant),
        "article_count": len(articles),
        "players": players,
        "risk": theatre_risk[:10],
        "summary": "", "timeline": [], "predictions": [], "watch": [],
        "gaps": [], "confidence": 0.0,
        "mode": "deterministic",
        "generated_at": time.time(),
    }
    if not relevant:
        report["summary"] = (f"No events matched {theatre['name']} in the "
                             f"current corpus window.")
        return report

    # Deterministic timeline from the events themselves.
    ordered = sorted(relevant[:12], key=lambda c: c["last_ts"], reverse=True)
    report["timeline"] = [{"when": c.get("when", ""), "what": c["title"]}
                          for c in ordered]
    report["summary"] = (
        f"{len(relevant)} events across {len(articles)} articles in this "
        f"theatre. Leading: {relevant[0]['title']} "
        f"({relevant[0]['source_count']} outlets). "
        + (f"Highest risk score: {theatre_risk[0]['name']} "
           f"{theatre_risk[0]['score']}/100 ({theatre_risk[0]['band']})."
           if theatre_risk else ""))

    if not use_llm:
        return report
    try:
        from ..squad.base import MODEL_SMART, run_llm_json, str_list
    except Exception:
        return report

    digest = [
        f"- [{c.get('when', '')}] {c['title']} ({c['source_count']} outlets, "
        f"corroboration {c['corroboration']:.2f}, tone {c['sentiment']:+.2f})"
        for c in relevant[:22]]
    player_lines = [f"- {p['name']} [{p['type']}] mentioned {p.get('mentions', 0)}x"
                    for p in players]
    risk_lines = [f"- {r['name']}: {r['score']}/100 ({r['band']}, "
                  f"top driver {r['top_dimension']})" for r in theatre_risk[:8]]

    payload = run_llm_json(
        MODEL_SMART, _SITREP_SYSTEM,
        f"THEATRE: {theatre['name']}\n\nEVENTS (newest first):\n"
        + "\n".join(digest)
        + "\n\nMOST-MENTIONED ACTORS:\n" + "\n".join(player_lines)
        + ("\n\nRISK SCORES:\n" + "\n".join(risk_lines) if risk_lines else ""),
        temperature=0.35, default=None)

    if not isinstance(payload, dict) or not payload.get("summary"):
        return report

    try:
        conf = float(payload.get("confidence", 0.5))
    except (TypeError, ValueError):
        conf = 0.5
    known = {p["name"].lower() for p in players}
    report.update({
        "summary": str(payload["summary"])[:2000],
        "timeline": [{"when": str(t.get("when", ""))[:60],
                      "what": str(t.get("what", ""))[:300]}
                     for t in (payload.get("timeline") or [])[:12]
                     if isinstance(t, dict)] or report["timeline"],
        "players": [{"name": str(p.get("name", ""))[:60],
                     "type": str(p.get("type", ""))[:20],
                     "role": str(p.get("role", ""))[:250],
                     "posture": str(p.get("posture", "unclear"))[:20],
                     "in_graph": str(p.get("name", "")).lower() in known}
                    for p in (payload.get("players") or [])[:14]
                    if isinstance(p, dict)] or players,
        "predictions": [{"claim": str(p.get("claim", ""))[:350],
                         "horizon": str(p.get("horizon", ""))[:60],
                         "confidence": _clamp(p.get("confidence"))}
                        for p in (payload.get("predictions") or [])[:6]
                        if isinstance(p, dict)],
        "watch": str_list(payload.get("watch"), limit=6),
        "gaps": str_list(payload.get("gaps"), limit=6),
        "confidence": round(max(0.0, min(1.0, conf)), 2),
        "mode": "llm",
    })
    return report


def _clamp(value, default: float = 0.4) -> float:
    try:
        return round(max(0.0, min(0.95, float(value))), 2)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# 3. Historical analogues
# ---------------------------------------------------------------------------
_HISTORY_SYSTEM = (
    "You are a historian advising an intelligence desk. Given a current event, "
    "identify genuine historical precedents.\n\n"
    "Return JSON:\n"
    "{\"analogues\":[{\"event\":\"name\",\"year\":\"YYYY or range\","
    "\"what_happened\":\"2-3 sentences\","
    "\"similarity\":\"why this is comparable to the current event\","
    "\"difference\":\"the most important way it is NOT comparable\","
    "\"outcome\":\"how it actually resolved\","
    "\"lesson\":\"what it suggests for the current case\","
    "\"strength\":0.0}],"
    "\"pattern\":\"one sentence on what these cases have in common\"}\n\n"
    "Rules:\n"
    "- Real, verifiable historical events only. If you are unsure a detail is "
    "correct, leave it out.\n"
    "- `difference` is mandatory and must be substantive. An analogy presented "
    "without its limits is misleading.\n"
    "- `strength` is how good the analogy is, 0-1. Be conservative.\n"
    "- 2 to 4 analogues."
)


def analogues(cluster: dict, use_llm: bool = True) -> dict:
    """Historical precedents for a live event.

    Explicitly NOT grounded in the corpus — these come from the model's training
    data, so the payload says so and the UI labels it. Every other TERRA panel
    can be traced to a headline; this one cannot, and pretending otherwise would
    put unverifiable claims next to verifiable ones with the same styling.
    """
    result = {
        "cluster": cluster.get("id", ""),
        "title": cluster.get("title", ""),
        "analogues": [],
        "pattern": "",
        "grounded": False,
        "mode": "none",
        "caveat": ("Historical analogues come from the model's own knowledge, "
                   "not from the live news corpus. They are not citable to a "
                   "source in TERRA and should be verified independently."),
        "generated_at": time.time(),
    }
    if not use_llm:
        return result
    try:
        from ..squad.base import MODEL_SMART, run_llm_json
    except Exception:
        return result

    countries = ", ".join(onto.country_name(c)
                          for c in cluster.get("countries", [])[:4])
    entities = ", ".join(e["name"] for e in cluster.get("entities", [])[:6])
    payload = run_llm_json(
        MODEL_SMART, _HISTORY_SYSTEM,
        f"CURRENT EVENT: {cluster.get('title', '')}\n"
        f"Countries involved: {countries or 'unspecified'}\n"
        f"Actors: {entities or 'unspecified'}\n"
        f"Themes: {', '.join(cluster.get('keywords', [])[:8])}",
        temperature=0.4, default=None)

    if not isinstance(payload, dict):
        return result
    items = []
    for a in (payload.get("analogues") or [])[:5]:
        if not isinstance(a, dict) or not a.get("event"):
            continue
        items.append({
            "event": str(a["event"])[:120],
            "year": str(a.get("year", ""))[:24],
            "what_happened": str(a.get("what_happened", ""))[:600],
            "similarity": str(a.get("similarity", ""))[:400],
            "difference": str(a.get("difference", ""))[:400],
            "outcome": str(a.get("outcome", ""))[:400],
            "lesson": str(a.get("lesson", ""))[:400],
            "strength": _clamp(a.get("strength"), 0.5),
        })
    if items:
        result["analogues"] = items
        result["pattern"] = str(payload.get("pattern", ""))[:400]
        result["mode"] = "llm"
    return result
