"""Six domain analysts and a master synthesizer.

One model reading everything produces one flat summary. Six specialists reading
the same corpus through different lenses produce six readings that disagree with
each other, and the disagreement is the useful part — the economic analyst and
the military analyst looking at the same Hormuz story write different second
sentences, and the master agent's job is to reconcile them rather than average
them away.

Each analyst:
  * gets only the articles tagged for its domain (extract.classify_domains)
  * gets them pre-clustered, so it reads ~15 events rather than 300 headlines
  * answers in a fixed JSON shape, so the console renders it without parsing prose
  * has a deterministic fallback that is genuinely useful on its own — with no
    model at all the panel still shows each domain's top events, the countries
    involved, and the direction of the tone

The master runs last and sees the analysts' outputs, not the raw corpus.
"""

from __future__ import annotations

import time
from collections import Counter

from . import ontology as onto
from .extract import DOMAINS

ANALYSTS = {
    "news": {
        "name": "NEWS ANALYST",
        "focus": "what actually happened in the last day and what it means",
        "system": (
            "You are the general news analyst on an intelligence desk. From the "
            "event digest, identify what genuinely matters and why. Prioritize "
            "events with real second-order consequences over events that are "
            "merely loud."),
    },
    "economic": {
        "name": "ECONOMIC ANALYST",
        "focus": "markets, trade, supply chains, currencies, commodities",
        "system": (
            "You are the economic analyst on an intelligence desk. Read the "
            "event digest for effects on prices, trade flows, supply chains, "
            "currencies and growth. Name the specific commodity, market or "
            "corridor affected. Distinguish an actual price move from an "
            "expectation of one."),
    },
    "military": {
        "name": "MILITARY ANALYST",
        "focus": "armed conflict, force posture, escalation and de-escalation",
        "system": (
            "You are the military analyst on an intelligence desk. Read the "
            "event digest for changes in force posture, escalation ladders and "
            "conflict intensity. Distinguish rhetoric from movement of forces. "
            "State escalation risk plainly and say what would confirm it."),
    },
    "climate": {
        "name": "CLIMATE ANALYST",
        "focus": "natural disasters, climate stress and their human impact",
        "system": (
            "You are the climate and disaster analyst on an intelligence desk. "
            "Read the event digest for hazards, their population exposure and "
            "their knock-on effects on agriculture, energy and displacement."),
    },
    "cyber": {
        "name": "CYBER ANALYST",
        "focus": "intrusions, infrastructure attacks, information operations",
        "system": (
            "You are the cyber analyst on an intelligence desk. Read the event "
            "digest for intrusions, infrastructure targeting and information "
            "operations. Be careful with attribution: say who is ALLEGED to be "
            "responsible and by whom, never assert attribution as fact."),
    },
    "health": {
        "name": "HEALTH ANALYST",
        "focus": "outbreaks, health systems, food and water security",
        "system": (
            "You are the health analyst on an intelligence desk. Read the event "
            "digest for outbreaks, health-system stress and food or water "
            "security. Note transmission and containment status where stated."),
    },
}

_SHAPE = (
    "\n\nReturn JSON exactly in this shape:\n"
    "{\"headline\":\"one sentence, the single most important thing in your "
    "domain right now\","
    "\"assessment\":\"2-4 sentences of analysis\","
    "\"key_points\":[\"...\",\"...\"],"
    "\"watch\":[\"specific thing to watch next\"],"
    "\"countries\":[\"ISO2\",\"ISO2\"],"
    "\"confidence\":0.0}\n\n"
    "Base everything ONLY on the digest given. Do not add events that are not "
    "in it. `confidence` is your confidence in the assessment given how much "
    "the digest actually supports it."
)


def _digest(clusters: list[dict], limit: int = 14) -> str:
    """Compact, model-readable rendering of the top events for a domain."""
    lines = []
    for i, c in enumerate(clusters[:limit], start=1):
        countries = ", ".join(onto.country_name(x) for x in c.get("countries", [])[:3])
        lines.append(
            f"{i}. {c['title']}\n"
            f"   {c['size']} articles / {c['source_count']} outlets · "
            f"corroboration {c['corroboration']:.2f} · tone {c['sentiment']:+.2f} · "
            f"{c.get('status', {}).get('state', 'n/a')}"
            + (f" · {countries}" if countries else ""))
    return "\n".join(lines)


def _fallback(domain: str, clusters: list[dict]) -> dict:
    """What the analyst reports with no model available.

    Deliberately descriptive rather than analytical — it states what the corpus
    contains and lets the reader draw the inference, instead of faking an
    assessment a model did not produce.
    """
    if not clusters:
        return {
            "headline": f"No significant {DOMAINS[domain]['label'].lower()} activity in the window.",
            "assessment": "", "key_points": [], "watch": [],
            "countries": [], "confidence": 0.0, "mode": "deterministic",
        }
    top = clusters[0]
    counts: Counter = Counter()
    for c in clusters:
        for iso in c.get("countries", []):
            counts[iso] += c["size"]
    tone = sum(c["sentiment"] for c in clusters) / len(clusters)
    direction = "negative" if tone < -0.1 else "positive" if tone > 0.1 else "mixed"
    return {
        "headline": top["title"],
        "assessment": (
            f"{len(clusters)} distinct {DOMAINS[domain]['label'].lower()} events "
            f"in the window across {sum(c['source_count'] for c in clusters)} "
            f"outlet reports. Overall tone is {direction} "
            f"({tone:+.2f}). Leading story is corroborated by "
            f"{top['source_count']} outlets."),
        "key_points": [c["title"] for c in clusters[1:5]],
        "watch": [f"{c['title']} — {c.get('status', {}).get('label', '')}"
                  for c in clusters[:2]],
        "countries": [iso for iso, _ in counts.most_common(5)],
        "confidence": round(min(0.6, 0.2 + 0.05 * len(clusters)), 2),
        "mode": "deterministic",
    }


def run_analyst(domain: str, clusters: list[dict], use_llm: bool = True) -> dict:
    """One domain analyst's reading of the corpus."""
    spec = ANALYSTS[domain]
    domain_clusters = [c for c in clusters if domain in (c.get("domains") or [])]
    domain_clusters.sort(key=lambda c: -c.get("score", 0))

    result = _fallback(domain, domain_clusters)
    result.update({
        "domain": domain,
        "name": spec["name"],
        "label": DOMAINS[domain]["label"],
        "glyph": DOMAINS[domain]["glyph"],
        "color": DOMAINS[domain]["color"],
        "events": len(domain_clusters),
        "articles": sum(c["size"] for c in domain_clusters),
        "top_events": [{"id": c["id"], "title": c["title"],
                        "size": c["size"], "sources": c["source_count"],
                        "url": c.get("url", "")}
                       for c in domain_clusters[:6]],
    })
    if not use_llm or not domain_clusters:
        return result

    try:
        from ..squad.base import MODEL_SMART, run_llm_json, str_list
    except Exception:
        return result

    payload = run_llm_json(
        MODEL_SMART, spec["system"] + _SHAPE,
        f"EVENT DIGEST — {spec['focus']}\n\n" + _digest(domain_clusters),
        temperature=0.3, default=None)
    if not isinstance(payload, dict) or not payload.get("headline"):
        return result

    isos = []
    for raw in (payload.get("countries") or [])[:8]:
        code = str(raw).strip().upper()
        if len(code) == 2 and onto.country_name(code) != code:
            isos.append(code)
        else:
            resolved = onto.iso_for(str(raw))
            if resolved:
                isos.append(resolved)
    try:
        conf = float(payload.get("confidence", 0.5))
    except (TypeError, ValueError):
        conf = 0.5

    result.update({
        "headline": str(payload["headline"])[:300],
        "assessment": str(payload.get("assessment", ""))[:1200],
        "key_points": str_list(payload.get("key_points"), item_max=220, limit=6),
        "watch": str_list(payload.get("watch"), item_max=220, limit=4),
        "countries": isos or result["countries"],
        "confidence": round(max(0.0, min(1.0, conf)), 2),
        "mode": "llm",
    })
    return result


_MASTER_SYSTEM = (
    "You are the chief of an intelligence desk. Six domain analysts have each "
    "reported on the same 24-hour window from their own perspective. Produce "
    "the combined assessment.\n\n"
    "Your value is in the CONNECTIONS between domains that no single analyst "
    "could see — where the economic reading and the military reading are about "
    "the same underlying event, where one analyst's watch item is another's "
    "leading story. Say so explicitly.\n\n"
    "Return JSON:\n"
    "{\"headline\":\"the single most consequential thing happening globally\","
    "\"assessment\":\"4-6 sentences\","
    "\"cross_domain\":[{\"insight\":\"...\",\"domains\":[\"economic\",\"military\"]}],"
    "\"priorities\":[\"ranked, most important first\"],"
    "\"watch\":[\"what would change this assessment\"],"
    "\"confidence\":0.0}\n\n"
    "Use ONLY what the analysts reported. Do not introduce events they did not "
    "mention."
)


def run_master(analyst_results: list[dict], use_llm: bool = True) -> dict:
    """Combine the six analyst readings into one desk assessment."""
    active = [a for a in analyst_results if a.get("events")]
    fallback = {
        "headline": (active[0]["headline"] if active
                     else "No significant global activity detected in the window."),
        "assessment": (
            f"{sum(a['events'] for a in analyst_results)} distinct events across "
            f"{len([a for a in analyst_results if a['events']])} domains. "
            f"Most active: " + ", ".join(
                f"{a['label'].lower()} ({a['events']})"
                for a in sorted(analyst_results, key=lambda a: -a["events"])[:3])
            + "."),
        "cross_domain": [],
        "priorities": [a["headline"] for a in
                       sorted(active, key=lambda a: -a["events"])[:5]],
        "watch": [w for a in active for w in a.get("watch", [])][:5],
        "confidence": round(sum(a.get("confidence", 0) for a in analyst_results)
                            / max(1, len(analyst_results)), 2),
        "mode": "deterministic",
    }
    if not use_llm or not active:
        return fallback

    try:
        from ..squad.base import MODEL_SMART, run_llm_json, str_list
    except Exception:
        return fallback

    lines = []
    for a in analyst_results:
        if not a.get("events"):
            continue
        lines.append(
            f"== {a['name']} ({a['events']} events, confidence {a['confidence']}) ==\n"
            f"HEADLINE: {a['headline']}\n"
            f"ASSESSMENT: {a.get('assessment', '')}\n"
            f"KEY POINTS: " + " | ".join(a.get("key_points", [])[:4]) + "\n"
            f"WATCHING: " + " | ".join(a.get("watch", [])[:3]))

    payload = run_llm_json(MODEL_SMART, _MASTER_SYSTEM, "\n\n".join(lines),
                           temperature=0.3, default=None)
    if not isinstance(payload, dict) or not payload.get("headline"):
        return fallback

    try:
        conf = float(payload.get("confidence", 0.5))
    except (TypeError, ValueError):
        conf = 0.5
    cross = []
    for item in (payload.get("cross_domain") or [])[:6]:
        if isinstance(item, dict) and item.get("insight"):
            cross.append({
                "insight": str(item["insight"])[:400],
                "domains": [d for d in (item.get("domains") or [])
                            if d in DOMAINS][:4],
            })
    return {
        "headline": str(payload["headline"])[:300],
        "assessment": str(payload.get("assessment", ""))[:2000],
        "cross_domain": cross,
        "priorities": str_list(payload.get("priorities"), limit=6),
        "watch": str_list(payload.get("watch"), limit=5),
        "confidence": round(max(0.0, min(1.0, conf)), 2),
        "mode": "llm",
    }


def run_all(clusters: list[dict], use_llm: bool = True,
            emit=None) -> dict:
    """The full six-analyst + master pass."""
    started = time.time()
    results = []
    for domain in ANALYSTS:
        if emit:
            emit("analyst", f"{ANALYSTS[domain]['name']} reading the corpus…")
        results.append(run_analyst(domain, clusters, use_llm=use_llm))
    if emit:
        emit("master", "MASTER combining six analyst readings…")
    master = run_master(results, use_llm=use_llm)
    return {
        "analysts": results,
        "master": master,
        "generated_at": time.time(),
        "elapsed": round(time.time() - started, 1),
        "events_considered": len(clusters),
    }
