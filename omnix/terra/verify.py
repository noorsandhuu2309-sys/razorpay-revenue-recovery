"""Cross-source verification — the same story read across outlets.

Given an event cluster, this answers four questions:

    agreement    which factual claims every outlet carries
    conflict     where outlets state incompatible things (numbers, attribution)
    omission     what one outlet reports that the others do not
    reliability  who is carrying it, and how independent those outlets are

The deterministic layer does the parts arithmetic is good at: extracting the
numbers, dates and named actors each outlet asserts, then comparing them. Numeric
disagreement is the highest-value automatic signal in news — "at least 9 killed"
against "at least 20 killed" is a real conflict a reader should see, and it needs
no model to find.

The LLM layer, when reachable, reads the headlines and characterizes the framing
differences that arithmetic cannot see. It is additive: with no model, this
module still returns claims, conflicts, omissions and reliability.
"""

from __future__ import annotations

import re
from collections import defaultdict

from . import nlp

_NUM_RE = re.compile(
    r"\b(?:at least\s+|more than\s+|over\s+|nearly\s+|about\s+|some\s+|up to\s+)?"
    r"(\d[\d,]*(?:\.\d+)?)\s*"
    r"(killed|dead|deaths?|injured|wounded|missing|casualties|people|civilians|"
    r"soldiers|troops|percent|%|million|billion|trillion|barrels|tonnes|tons|"
    r"days?|weeks?|months?|years?|hostages?|prisoners?|migrants?)\b",
    re.IGNORECASE)

_HEDGE_RE = re.compile(
    r"\b(allegedly|reportedly|claims?|claimed|accused|denies|denied|disputed|"
    r"unconfirmed|according to|said to be|purported|apparent(?:ly)?|"
    r"suspected|believed to)\b", re.IGNORECASE)

_ATTRIB_RE = re.compile(
    r"\b(?:according to|says?|said|told|per)\s+([A-Z][A-Za-z’'\-]*(?:\s+[A-Z][A-Za-z’'\-]*){0,3})")


def numeric_claims(text: str) -> list[dict]:
    """Quantities an article asserts, normalized for comparison."""
    out = []
    for match in _NUM_RE.finditer(text or ""):
        raw = match.group(1).replace(",", "")
        try:
            value = float(raw)
        except ValueError:
            continue
        unit = match.group(2).lower().rstrip("s")
        if unit in ("death", "dead"):
            unit = "killed"
        elif unit == "%":
            unit = "percent"
        out.append({"value": value, "unit": unit, "text": match.group(0).strip()})
    return out


def verify(cluster: dict, articles_by_id: dict[str, dict],
           use_llm: bool = True) -> dict:
    """Full cross-source report for one event cluster."""
    members = [articles_by_id[a] for a in cluster.get("article_ids", [])
               if a in articles_by_id]
    if not members:
        return {"status": "empty", "outlets": [], "agreement": [],
                "conflicts": [], "omissions": [], "reliability": {}}

    # -- one row per outlet, keeping its own framing --------------------------
    by_source: dict[str, list[dict]] = defaultdict(list)
    for art in members:
        by_source[art.get("source") or "Unknown"].append(art)

    outlets = []
    for source, arts in by_source.items():
        arts.sort(key=lambda a: -a.get("published_ts", 0))
        lead = arts[0]
        text = " ".join(a["title"] + " " + a.get("summary", "") for a in arts)
        conf = nlp.source_confidence(source)
        outlets.append({
            "source": source,
            "confidence": round(conf, 2),
            "confidence_label": nlp.confidence_label(conf),
            "headline": lead["title"],
            "url": lead.get("url", ""),
            "ts": lead.get("published_ts", 0),
            "articles": len(arts),
            "sentiment": round(sum(a.get("sentiment", 0) for a in arts) / len(arts), 3),
            "hedged": bool(_HEDGE_RE.search(text)),
            "numbers": numeric_claims(text)[:6],
            "attributions": sorted({m.group(1).strip()
                                    for m in _ATTRIB_RE.finditer(text)})[:4],
            "terms": set(nlp.tokens(text)),
        })
    outlets.sort(key=lambda o: (-o["confidence"], o["source"]))

    # -- agreement: content terms carried by most outlets ---------------------
    n_outlets = len(outlets)
    term_carriers: dict[str, set[str]] = defaultdict(set)
    for outlet in outlets:
        for term in outlet["terms"]:
            term_carriers[term].add(outlet["source"])

    agreement = []
    if n_outlets >= 2:
        for term, carriers in term_carriers.items():
            share = len(carriers) / n_outlets
            if share >= 0.6 and len(carriers) >= 2 and len(term) > 3:
                agreement.append({
                    "claim": term,
                    "carriers": sorted(carriers),
                    "share": round(share, 2),
                    "weight": round(sum(nlp.source_confidence(c) for c in carriers), 2),
                })
        agreement.sort(key=lambda c: (-c["share"], -c["weight"]))

    # -- conflicts: incompatible numbers for the same unit --------------------
    conflicts = []
    by_unit: dict[str, list[tuple[str, dict]]] = defaultdict(list)
    for outlet in outlets:
        for num in outlet["numbers"]:
            by_unit[num["unit"]].append((outlet["source"], num))
    for unit, entries in by_unit.items():
        if len(entries) < 2:
            continue
        values = [n["value"] for _, n in entries]
        lo, hi = min(values), max(values)
        if lo <= 0:
            continue
        # A 25% spread is normal reporting lag on a rising toll; 2x is a real
        # disagreement worth surfacing.
        if hi / lo >= 2.0:
            conflicts.append({
                "type": "numeric",
                "unit": unit,
                "low": lo, "high": hi,
                "spread": round(hi / lo, 2),
                "positions": [{"source": s, "value": n["value"], "text": n["text"],
                               "confidence": round(nlp.source_confidence(s), 2)}
                              for s, n in sorted(entries, key=lambda e: e[1]["value"])],
            })

    # -- conflicts: sentiment polarity split ----------------------------------
    positives = [o for o in outlets if o["sentiment"] > 0.15]
    negatives = [o for o in outlets if o["sentiment"] < -0.15]
    if positives and negatives and n_outlets >= 3:
        conflicts.append({
            "type": "framing",
            "unit": "tone",
            "positions": (
                [{"source": o["source"], "value": o["sentiment"],
                  "text": o["headline"],
                  "confidence": o["confidence"]} for o in negatives[:3]] +
                [{"source": o["source"], "value": o["sentiment"],
                  "text": o["headline"],
                  "confidence": o["confidence"]} for o in positives[:3]]),
            "note": (f"{len(negatives)} outlet(s) frame this negatively while "
                     f"{len(positives)} frame it positively."),
        })

    # -- omissions: distinctive content only one outlet carries ---------------
    omissions = []
    for outlet in outlets:
        unique = [t for t in outlet["terms"]
                  if len(term_carriers[t]) == 1 and len(t) > 4]
        # Rank by how unusual the term is in this cluster, not alphabetically.
        unique.sort(key=lambda t: -len(t))
        if unique and n_outlets >= 3:
            omissions.append({
                "source": outlet["source"],
                "confidence": outlet["confidence"],
                "unique_terms": unique[:6],
                "headline": outlet["headline"],
                "url": outlet["url"],
            })
    omissions.sort(key=lambda o: -len(o["unique_terms"]))

    # -- reliability roll-up --------------------------------------------------
    weights = [o["confidence"] for o in outlets]
    independent = sum(1 for o in outlets if o["confidence"] >= 0.78)
    state_aligned = [o["source"] for o in outlets if o["confidence"] < 0.45]
    reliability = {
        "outlets": n_outlets,
        "mean_confidence": round(sum(weights) / len(weights), 2),
        "max_confidence": round(max(weights), 2),
        "independent_outlets": independent,
        "state_aligned": state_aligned,
        "corroboration": round(min(1.0, sum(weights) / 2.6), 2),
        "verdict": _verdict(n_outlets, independent, conflicts),
    }

    for outlet in outlets:
        outlet.pop("terms", None)

    report = {
        "status": "ok",
        "cluster": cluster.get("id", ""),
        "title": cluster.get("title", ""),
        "outlets": outlets,
        "agreement": agreement[:14],
        "conflicts": conflicts[:8],
        "omissions": omissions[:6],
        "reliability": reliability,
        "llm": None,
    }
    if use_llm and n_outlets >= 2:
        report["llm"] = _llm_compare(outlets, cluster.get("title", ""))
    return report


def _verdict(n_outlets: int, independent: int, conflicts: list) -> str:
    numeric_conflicts = [c for c in conflicts if c["type"] == "numeric"]
    if independent >= 3 and not numeric_conflicts:
        return "Well corroborated — multiple independent outlets agree"
    if independent >= 3:
        return "Corroborated, but outlets disagree on specifics"
    if n_outlets >= 3 and independent >= 1:
        return "Partially corroborated — check the independent outlet"
    if n_outlets >= 2:
        return "Thinly sourced — two or fewer independent outlets"
    return "Single source — treat as unconfirmed"


_COMPARE_SYSTEM = (
    "You are a news verification analyst. You are given how several outlets "
    "headlined the SAME story. Compare them.\n\n"
    "Return JSON:\n"
    "{\"common_facts\":[\"...\"],"
    "\"conflicting_claims\":[{\"issue\":\"...\",\"positions\":[{\"source\":\"...\","
    "\"claim\":\"...\"}]}],"
    "\"missing\":[{\"source\":\"...\",\"omits\":\"...\"}],"
    "\"framing\":\"one sentence on how framing differs\"}\n\n"
    "Rules: use ONLY what the given headlines say. Do not add background "
    "knowledge. If outlets do not actually conflict, return an empty "
    "conflicting_claims list rather than inventing a disagreement."
)


def _llm_compare(outlets: list[dict], title: str) -> dict | None:
    try:
        from ..squad.base import MODEL_SMART, run_llm_json
    except Exception:
        return None
    lines = [f"STORY: {title}", ""]
    for outlet in outlets[:10]:
        lines.append(f"- {outlet['source']} (source confidence "
                     f"{outlet['confidence']}): {outlet['headline']}")
    result = run_llm_json(MODEL_SMART, _COMPARE_SYSTEM, "\n".join(lines),
                          temperature=0.15, default=None)
    if not isinstance(result, dict):
        return None
    return {
        "common_facts": [str(x) for x in (result.get("common_facts") or [])][:8],
        "conflicting_claims": (result.get("conflicting_claims") or [])[:6],
        "missing": (result.get("missing") or [])[:6],
        "framing": str(result.get("framing") or "")[:400],
    }
