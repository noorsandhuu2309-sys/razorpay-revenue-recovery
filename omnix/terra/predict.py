"""Second-order reasoning: predictive alerts, economic impact, what-if.

The distinction this module exists to make: "an earthquake happened" is
retrieval; "this earthquake may disrupt semiconductor exports within 5 days" is
inference over a causal structure. The causal structure is the knowledge graph,
which is why this module is downstream of it rather than a standalone prompt.

Each of the three engines follows the same pattern:

    seed        an event, or a hypothetical
        |
    graph walk  who and what is connected to it, and how
        |
    exposure    deterministic scoring of which nodes are exposed, via which path
        |
    LLM         phrases the consequence, constrained to the exposed nodes

The deterministic exposure step is what keeps the output from being generic. A
model asked "what happens if Hormuz closes" writes a plausible essay from
memory; a model given "these are the nodes with a dependency path to Hormuz,
here are their path lengths and the live articles attached to them" writes about
this world. And when no model is reachable, the exposure paths ARE the answer —
less fluent, still correct.

Everything here is explicitly labeled as a projection, carries a confidence, and
shows the path it reasoned along, because an unfalsifiable prediction with no
visible chain is worse than none.
"""

from __future__ import annotations

import time

from . import ontology as onto

# ---------------------------------------------------------------------------
# Exposure — graph reachability with decay by hop and edge type
# ---------------------------------------------------------------------------
# How strongly each relation transmits an effect. A dependency transmits almost
# fully; a co-mention barely transmits at all, which is what stops exposure from
# flooding the entire graph through incidental article co-occurrence.
TRANSMISSION = {
    "depends_on": 0.85, "supplies": 0.80, "produces": 0.70,
    "located_in": 0.55, "trades_with": 0.60, "member_of": 0.50,
    "in_conflict": 0.65, "sanctions": 0.70, "allied_with": 0.45,
    "affected_by": 0.75, "invests_in": 0.50, "leads": 0.40,
    "negotiating": 0.35, "supports": 0.35, "accuses": 0.25,
    "involved_in": 0.45, "co_mentioned": 0.15,
}
MIN_EXPOSURE = 0.06


def exposure(kg, seeds: list[str], max_hops: int = 3,
             limit: int = 40) -> list[dict]:
    """Nodes reachable from the seeds, scored by how strongly effects transmit.

    Best-path search rather than shortest-path: a two-hop dependency chain
    transmits more than a one-hop co-mention, so the strongest route wins even
    when it is longer.
    """
    best: dict[str, dict] = {}
    frontier = [(s, 1.0, [], []) for s in seeds if kg.node(s)]
    for s, _, _, _ in frontier:
        best[s] = {"strength": 1.0, "path": [], "relations": [], "hops": 0}

    for hop in range(max_hops):
        nxt = []
        for nid, strength, path, rels in frontier:
            for edge in kg.neighbors(nid, limit=14):
                other = edge["node"]["id"]
                transmit = TRANSMISSION.get(edge["relation"], 0.2)
                # Edge weight modulates transmission: a heavily-attested
                # relationship carries more than one seen once.
                attest = min(1.0, 0.4 + 0.6 * min(1.0, edge["weight"] / 3.0))
                new_strength = strength * transmit * attest
                if new_strength < MIN_EXPOSURE:
                    continue
                prior = best.get(other)
                if prior and prior["strength"] >= new_strength:
                    continue
                best[other] = {
                    "strength": new_strength,
                    "path": path + [edge["node"]["name"]],
                    "relations": rels + [edge["relation_label"]],
                    "hops": hop + 1,
                    "articles": edge.get("articles", []),
                }
                nxt.append((other, new_strength, best[other]["path"],
                            best[other]["relations"]))
        frontier = nxt
        if not frontier:
            break

    out = []
    for nid, rec in best.items():
        if nid in seeds or rec["strength"] < MIN_EXPOSURE:
            continue
        node = kg.node(nid)
        if node is None:
            continue
        pub = kg._public(node)
        out.append({
            "id": nid, "name": pub["name"], "type": pub["type"],
            "glyph": pub["glyph"], "color": pub["color"],
            "iso2": pub.get("iso2", ""), "sector": pub.get("sector", ""),
            "exposure": round(rec["strength"], 3),
            "hops": rec["hops"],
            "path": rec["path"],
            "relations": rec["relations"],
            "chain": _chain_text(kg, seeds, rec),
            "articles": rec.get("articles", [])[:4],
        })
    out.sort(key=lambda x: -x["exposure"])
    return out[:limit]


def _chain_text(kg, seeds: list[str], rec: dict) -> str:
    root = kg.node(seeds[0]) if seeds else None
    start = root["name"] if root else "seed"
    parts = [start]
    for name, rel in zip(rec["path"], rec["relations"]):
        parts.append(f"—{rel}→ {name}")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# 1. Predictive alerts
# ---------------------------------------------------------------------------
_PREDICT_SYSTEM = (
    "You are a geopolitical forecaster. You are given ONE event and the objects "
    "that are causally connected to it in a knowledge graph, with the "
    "connection path for each.\n\n"
    "Produce concrete, checkable second-order predictions — what may happen "
    "NEXT as a consequence, on what timescale.\n\n"
    "Return JSON:\n"
    "{\"predictions\":[{\"claim\":\"specific, falsifiable prediction\","
    "\"horizon\":\"e.g. 48 hours / 5 days / 3 weeks\","
    "\"mechanism\":\"the causal chain in one sentence\","
    "\"affected\":[\"object names from the exposure list\"],"
    "\"confidence\":0.0,"
    "\"falsifier\":\"what observation would show this is wrong\"}]}\n\n"
    "Rules:\n"
    "- Only involve objects from the exposure list. Do not invent actors.\n"
    "- A prediction must be checkable. \"Tensions may rise\" is not acceptable; "
    "\"Brent closes above $X within 5 trading days\" is.\n"
    "- Be honest with confidence. Most second-order predictions deserve 0.3-0.6.\n"
    "- 3 to 5 predictions."
)


def predict_event(kg, cluster: dict, articles_by_id: dict[str, dict],
                  use_llm: bool = True) -> dict:
    """Second-order predictions for one event cluster."""
    seeds = [e["id"] for e in cluster.get("entities", [])[:4]]
    exposed = exposure(kg, seeds, max_hops=3, limit=30)

    base = {
        "cluster": cluster.get("id", ""),
        "title": cluster.get("title", ""),
        "seeds": [{"id": e["id"], "name": e["name"], "type": e["type"]}
                  for e in cluster.get("entities", [])[:4]],
        "exposure": exposed,
        "predictions": [],
        "generated_at": time.time(),
        "mode": "deterministic",
        "note": ("Projections, not reported facts. Each carries the causal path "
                 "it was derived from and a falsifier."),
    }
    if not exposed:
        return base

    # Deterministic baseline: the strongest exposure paths, phrased as exposure
    # rather than as prediction — an honest statement of what is connected,
    # which is all the graph alone can support.
    base["predictions"] = [{
        "claim": f"{node['name']} is exposed to this event",
        "horizon": "unspecified",
        "mechanism": node["chain"],
        "affected": [node["name"]],
        "confidence": round(min(0.55, node["exposure"]), 2),
        "falsifier": f"No coverage linking {node['name']} to this event within a week",
        "kind": "exposure",
    } for node in exposed[:5]]

    if not use_llm:
        return base
    try:
        from ..squad.base import MODEL_SMART, run_llm_json
    except Exception:
        return base

    headlines = []
    for aid in cluster.get("article_ids", [])[:8]:
        art = articles_by_id.get(aid)
        if art:
            headlines.append(f"- ({art.get('source', '')}) {art['title']}")

    exposure_lines = [
        f"- {n['name']} [{n['type']}] exposure {n['exposure']} via {n['chain']}"
        for n in exposed[:18]]

    payload = run_llm_json(
        MODEL_SMART, _PREDICT_SYSTEM,
        f"EVENT: {cluster.get('title', '')}\n"
        f"Corroboration: {cluster.get('corroboration', 0):.2f} across "
        f"{cluster.get('source_count', 0)} outlets\n\n"
        f"REPORTING:\n" + "\n".join(headlines) +
        "\n\nCAUSALLY CONNECTED OBJECTS:\n" + "\n".join(exposure_lines),
        temperature=0.4, default=None)

    preds = _parse_predictions(payload, {n["name"] for n in exposed})
    if preds:
        base["predictions"] = preds
        base["mode"] = "llm"
    return base


def _parse_predictions(payload, valid_names: set[str]) -> list[dict]:
    if not isinstance(payload, dict):
        return []
    out = []
    for item in (payload.get("predictions") or [])[:6]:
        if not isinstance(item, dict) or not item.get("claim"):
            continue
        try:
            conf = float(item.get("confidence", 0.4))
        except (TypeError, ValueError):
            conf = 0.4
        # Keep only affected objects that actually exist in the exposure set —
        # the guard against a model adding an actor it liked the sound of.
        affected = [str(a) for a in (item.get("affected") or [])
                    if str(a) in valid_names]
        out.append({
            "claim": str(item["claim"])[:400],
            "horizon": str(item.get("horizon", "unspecified"))[:60],
            "mechanism": str(item.get("mechanism", ""))[:400],
            "affected": affected[:6],
            "confidence": round(max(0.0, min(0.95, conf)), 2),
            "falsifier": str(item.get("falsifier", ""))[:300],
            "kind": "prediction",
        })
    return out


# ---------------------------------------------------------------------------
# 2. Economic impact engine
# ---------------------------------------------------------------------------
# The market channels every event is scored against. Keyword weights are the
# deterministic prior; the LLM refines direction and magnitude.
CHANNELS = {
    "oil":       {"label": "Crude oil", "glyph": "🛢", "color": "#f0b429",
                  "terms": {"oil": 2.0, "crude": 2.2, "opec": 2.4, "barrel": 2.0,
                            "refinery": 2.0, "pipeline": 1.8, "hormuz": 3.0,
                            "energy": 1.2, "gas": 1.4, "lng": 1.8}},
    "gold":      {"label": "Gold", "glyph": "🥇", "color": "#c9a45c",
                  "terms": {"gold": 2.0, "safe haven": 2.4, "bullion": 2.0,
                            "inflation": 1.4, "war": 1.6, "crisis": 1.4}},
    "crypto":    {"label": "Crypto", "glyph": "₿", "color": "#f7931a",
                  "terms": {"bitcoin": 2.4, "crypto": 2.2, "ethereum": 2.0,
                            "stablecoin": 1.8, "sanctions": 1.2, "capital controls": 2.0}},
    "equities":  {"label": "Equities", "glyph": "📈", "color": "#4ade80",
                  "terms": {"stocks": 2.0, "market": 1.4, "shares": 1.8,
                            "index": 1.4, "wall street": 2.0, "earnings": 1.6,
                            "recession": 2.2, "rates": 1.6}},
    "supply":    {"label": "Supply chains", "glyph": "⛓", "color": "#57d7ff",
                  "terms": {"supply chain": 2.6, "semiconductor": 2.4,
                            "chips": 2.2, "export controls": 2.6, "factory": 1.6,
                            "manufacturing": 1.6, "rare earth": 2.4,
                            "shortage": 2.2, "tariff": 2.0}},
    "shipping":  {"label": "Shipping & freight", "glyph": "🚢", "color": "#9d8cff",
                  "terms": {"shipping": 2.4, "freight": 2.2, "port": 1.8,
                            "canal": 2.4, "strait": 2.4, "vessel": 2.0,
                            "tanker": 2.2, "container": 2.0, "red sea": 2.6}},
}


def economic_impact(cluster: dict, kg, use_llm: bool = True) -> dict:
    """Estimated effect of one event on six market channels."""
    text = (cluster.get("title", "") + " " +
            " ".join(cluster.get("keywords", []))).lower()
    entity_names = " ".join(e["name"].lower() for e in cluster.get("entities", []))
    haystack = text + " " + entity_names

    channels = []
    for key, spec in CHANNELS.items():
        raw = sum(w for term, w in spec["terms"].items() if term in haystack)
        if raw <= 0:
            continue
        # Severity and corroboration scale the magnitude: a well-attested severe
        # event moves more than a thinly-sourced mild one.
        magnitude = min(1.0, raw / 6.0) * (0.4 + 0.6 * cluster.get("severity", 0.3))
        magnitude *= (0.5 + 0.5 * cluster.get("corroboration", 0.5))
        direction = "up" if cluster.get("sentiment", 0) < -0.1 else "down"
        # Only the safe-haven channels rise on bad news; risk assets fall.
        if key in ("equities", "crypto", "supply", "shipping"):
            direction = "down" if cluster.get("sentiment", 0) < -0.1 else "up"
            if key in ("supply", "shipping"):
                direction = "stress" if cluster.get("sentiment", 0) < -0.1 else "ease"
        channels.append({
            "key": key, "label": spec["label"], "glyph": spec["glyph"],
            "color": spec["color"],
            "magnitude": round(magnitude, 3),
            "direction": direction,
            "band": ("high" if magnitude > 0.55 else
                     "moderate" if magnitude > 0.28 else "low"),
            "drivers": [t for t in spec["terms"] if t in haystack][:5],
        })
    channels.sort(key=lambda c: -c["magnitude"])

    result = {
        "cluster": cluster.get("id", ""),
        "title": cluster.get("title", ""),
        "channels": channels,
        "commentary": "",
        "mode": "deterministic",
        "note": ("Directional estimates from event characteristics and graph "
                 "exposure. Not price forecasts and not investment advice."),
    }
    if not channels or not use_llm:
        return result

    try:
        from ..squad.base import MODEL_SMART, run_llm
    except Exception:
        return result

    lines = [f"- {c['label']}: estimated {c['band']} impact, direction "
             f"{c['direction']} (drivers: {', '.join(c['drivers'])})"
             for c in channels]
    commentary = run_llm(
        MODEL_SMART,
        ("You are a markets analyst. Given an event and a deterministic "
         "estimate of which market channels it touches, write 3-4 sentences on "
         "the transmission mechanism — HOW the event reaches each channel and "
         "over what timescale. Be specific about the mechanism. Do not give "
         "investment advice and do not predict price levels."),
        f"EVENT: {cluster.get('title', '')}\n"
        f"Severity {cluster.get('severity', 0):.2f}, corroboration "
        f"{cluster.get('corroboration', 0):.2f}\n\nCHANNELS:\n" + "\n".join(lines),
        temperature=0.3)
    if commentary.strip():
        result["commentary"] = commentary.strip()[:1200]
        result["mode"] = "llm"
    return result


# ---------------------------------------------------------------------------
# 3. What-if simulator
# ---------------------------------------------------------------------------
_WHATIF_SYSTEM = (
    "You are running a geopolitical scenario simulation. You are given a "
    "hypothetical, the objects in a knowledge graph that are causally exposed "
    "to it (with connection paths), and live reporting on the objects involved.\n\n"
    "Return JSON:\n"
    "{\"summary\":\"3-5 sentences on what happens\","
    "\"timeline\":[{\"when\":\"first 48 hours\",\"effects\":[\"...\"]}],"
    "\"markets\":[{\"asset\":\"...\",\"direction\":\"up|down\","
    "\"magnitude\":\"small|moderate|severe\",\"reasoning\":\"...\"}],"
    "\"countries\":[{\"name\":\"...\",\"iso2\":\"XX\",\"impact\":\"...\","
    "\"severity\":0.0}],"
    "\"companies\":[{\"name\":\"...\",\"impact\":\"...\"}],"
    "\"mitigations\":[\"what would blunt this\"],"
    "\"confidence\":0.0,"
    "\"assumptions\":[\"what this scenario assumes\"]}\n\n"
    "Rules: prefer objects from the exposure list. State assumptions "
    "explicitly. This is a simulation of a hypothetical — never present it as "
    "a forecast of what will happen."
)


def what_if(kg, scenario: str, articles: list[dict] | None = None,
            use_llm: bool = True) -> dict:
    """Simulate a hypothetical against the live graph."""
    from .extract import gazetteer

    matched = gazetteer().match(scenario)
    seeds = [e["id"] for e in matched][:5]
    exposed = exposure(kg, seeds, max_hops=3, limit=45) if seeds else []

    result = {
        "scenario": scenario,
        "seeds": [{"id": e["id"], "name": e["name"], "type": e["type"]}
                  for e in matched[:6]],
        "exposure": exposed,
        "summary": "", "timeline": [], "markets": [], "countries": [],
        "companies": [], "mitigations": [], "assumptions": [],
        "confidence": 0.0,
        "mode": "deterministic",
        "note": "Hypothetical simulation. Not a forecast.",
        "generated_at": time.time(),
    }
    if not seeds:
        result["summary"] = ("No known objects in that scenario could be "
                             "resolved against the ontology. Try naming a "
                             "country, company, commodity or waterway.")
        return result

    # Deterministic answer: who is exposed, grouped by type.
    by_type: dict[str, list] = {}
    for node in exposed:
        by_type.setdefault(node["type"], []).append(node)
    result["countries"] = [
        {"name": n["name"], "iso2": n.get("iso2", ""),
         "impact": n["chain"], "severity": n["exposure"]}
        for n in by_type.get("country", [])[:12]]
    result["companies"] = [
        {"name": n["name"], "impact": n["chain"]}
        for n in by_type.get("organization", [])[:12]]
    result["summary"] = (
        f"{len(exposed)} objects have a causal path to this scenario: "
        f"{len(by_type.get('country', []))} countries, "
        f"{len(by_type.get('organization', []))} organizations, "
        f"{len(by_type.get('commodity', []))} commodities. "
        f"Strongest exposure: " + ", ".join(n["name"] for n in exposed[:4]) + ".")

    if not use_llm:
        return result
    try:
        from ..squad.base import MODEL_SMART, run_llm_json
    except Exception:
        return result

    context = []
    if articles:
        seed_names = {e["name"].lower() for e in matched}
        relevant = [a for a in articles
                    if any(s in a["title"].lower() for s in seed_names)][:10]
        context = [f"- ({a.get('source', '')}) {a['title']}" for a in relevant]

    exposure_lines = [
        f"- {n['name']} [{n['type']}] exposure {n['exposure']} via {n['chain']}"
        for n in exposed[:24]]

    payload = run_llm_json(
        MODEL_SMART, _WHATIF_SYSTEM,
        f"SCENARIO: {scenario}\n\nCAUSALLY EXPOSED OBJECTS:\n"
        + "\n".join(exposure_lines)
        + ("\n\nCURRENT REPORTING ON THESE OBJECTS:\n" + "\n".join(context)
           if context else ""),
        temperature=0.45, default=None)

    if not isinstance(payload, dict) or not payload.get("summary"):
        return result

    try:
        conf = float(payload.get("confidence", 0.4))
    except (TypeError, ValueError):
        conf = 0.4
    result.update({
        "summary": str(payload["summary"])[:1600],
        "timeline": [
            {"when": str(t.get("when", ""))[:60],
             "effects": [str(e)[:250] for e in (t.get("effects") or [])][:5]}
            for t in (payload.get("timeline") or [])[:6]
            if isinstance(t, dict)],
        "markets": [
            {"asset": str(m.get("asset", ""))[:60],
             "direction": str(m.get("direction", ""))[:12],
             "magnitude": str(m.get("magnitude", ""))[:20],
             "reasoning": str(m.get("reasoning", ""))[:300]}
            for m in (payload.get("markets") or [])[:8]
            if isinstance(m, dict)],
        "countries": [
            {"name": str(c.get("name", ""))[:60],
             "iso2": (str(c.get("iso2", "")).upper()[:2]
                      or onto.iso_for(str(c.get("name", "")))),
             "impact": str(c.get("impact", ""))[:300],
             "severity": _num(c.get("severity"), 0.5)}
            for c in (payload.get("countries") or [])[:14]
            if isinstance(c, dict)] or result["countries"],
        "companies": [
            {"name": str(c.get("name", ""))[:60],
             "impact": str(c.get("impact", ""))[:300]}
            for c in (payload.get("companies") or [])[:12]
            if isinstance(c, dict)] or result["companies"],
        "mitigations": [str(x)[:250] for x in (payload.get("mitigations") or [])][:6],
        "assumptions": [str(x)[:250] for x in (payload.get("assumptions") or [])][:6],
        "confidence": round(max(0.0, min(0.9, conf)), 2),
        "mode": "llm",
    })
    return result


def _num(value, default: float) -> float:
    try:
        return round(max(0.0, min(1.0, float(value))), 2)
    except (TypeError, ValueError):
        return default
