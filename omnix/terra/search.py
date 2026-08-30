"""Semantic search over the corpus, plus a synthesized answer.

"Countries affected by Chinese chip export restrictions" must not be handled as
a keyword query — the answer lives in articles that never use most of those
words. Three things get it there:

  1. Query expansion through the ONTOLOGY. "Chinese" resolves to the China node,
     "chip" to the Semiconductors commodity; the graph then contributes their
     neighbors (TSMC, ASML, Nvidia, Taiwan, export controls) as extra query
     terms. This is the step that makes the retrieval semantic without an
     embedding model — the meaning comes from the graph rather than from a
     vector space, and it is auditable.
  2. TF-IDF retrieval over the expanded query.
  3. Graph-aware reranking: an article whose entities match the resolved query
     entities outranks one that merely shares vocabulary.

The synthesis step then answers from the retrieved articles only, with numbered
citations, so every sentence can be traced to a headline.
"""

from __future__ import annotations

import re

from . import nlp
from .extract import gazetteer

MAX_EXPANSION = 14


def resolve_query(query: str, kg) -> dict:
    """Map a natural-language query onto ontology objects and expansion terms."""
    matched = gazetteer().match(query)
    entity_ids = [e["id"] for e in matched]

    # Pull one hop of neighbors for each resolved entity, strongest first.
    # Deduplicated by node id: two objects can be connected by several relation
    # types, and without this the same neighbour lands in the expansion three
    # times and triples its weight in the retrieval query.
    expansion: list[str] = []
    related: list[dict] = []
    seen: set[str] = set(entity_ids)
    for eid in entity_ids[:4]:
        for edge in kg.neighbors(eid, limit=8):
            node = edge["node"]
            if node["id"] in seen or node["name"].lower() in query.lower():
                continue
            seen.add(node["id"])
            related.append({"id": node["id"], "name": node["name"],
                            "type": node["type"], "relation": edge["relation_label"],
                            "via": eid})
            expansion.append(node["name"])
            if len(expansion) >= MAX_EXPANSION:
                break
        if len(expansion) >= MAX_EXPANSION:
            break

    return {
        "entities": matched,
        "entity_ids": entity_ids,
        "expansion": expansion,
        "related": related[:MAX_EXPANSION],
        "expanded_query": query + " " + " ".join(expansion),
    }


def search(query: str, articles: list[dict], index: nlp.TfIdf, kg,
           limit: int = 14) -> dict:
    """Retrieve and rerank. Returns the resolution plus ranked hits."""
    query = (query or "").strip()
    if not query:
        return {"query": query, "results": [], "resolution": None}

    resolution = resolve_query(query, kg)
    by_id = {a["id"]: a for a in articles}

    # Score the plain query and the ontology-expanded query separately, then
    # blend. Blending rather than replacing matters: expansion can drift, and
    # the literal query is the user's actual words.
    literal = dict(index.search(query, k=limit * 4))
    expanded = dict(index.search(resolution["expanded_query"], k=limit * 4))

    want_entities = set(resolution["entity_ids"])
    want_related = {r["id"] for r in resolution["related"]}

    scored: list[tuple[float, dict, dict]] = []
    for aid in set(literal) | set(expanded):
        art = by_id.get(aid)
        if art is None:
            continue
        text_score = 0.65 * literal.get(aid, 0.0) + 0.35 * expanded.get(aid, 0.0)
        ents = {e["id"] for e in (art.get("entities") or [])}
        direct = len(ents & want_entities)
        indirect = len(ents & want_related)
        graph_boost = 0.14 * min(3, direct) + 0.05 * min(4, indirect)
        # A high-confidence source is a better answer to the same question.
        conf_boost = 0.05 * (art.get("confidence", 0.6) - 0.6)
        total = text_score + graph_boost + conf_boost
        scored.append((total, art, {"text": round(text_score, 4),
                                    "graph": round(graph_boost, 4),
                                    "direct_entities": direct,
                                    "related_entities": indirect}))
    scored.sort(key=lambda x: -x[0])

    results = []
    for rank, (score, art, why) in enumerate(scored[:limit], start=1):
        results.append({
            "n": rank,
            "id": art["id"],
            "title": art["title"],
            "url": art.get("url", ""),
            "source": art.get("source", ""),
            "confidence": art.get("confidence", 0.6),
            "ts": art.get("published_ts", 0),
            "sentiment": art.get("sentiment", 0.0),
            "countries": art.get("countries", []),
            "domains": art.get("domains", []),
            "score": round(score, 4),
            "why": why,
        })
    return {"query": query, "resolution": {
        "entities": resolution["entities"],
        "related": resolution["related"],
        "expansion": resolution["expansion"],
    }, "results": results}


_ANSWER_SYSTEM = (
    "You are a geopolitical intelligence analyst. Answer the question using "
    "ONLY the numbered sources provided.\n\n"
    "Rules:\n"
    "- Cite every claim inline as [n] matching the source numbers.\n"
    "- If the sources do not answer the question, say exactly what is missing "
    "instead of filling the gap from your own knowledge.\n"
    "- Lead with the direct answer in one or two sentences, then the supporting "
    "detail. Be concrete: name countries, organizations and numbers.\n"
    "- Note explicitly when sources disagree.\n"
    "- 250 words maximum."
)


def synthesize(query: str, results: list[dict]) -> dict:
    """Answer the question from the retrieved articles. '' if no model."""
    if not results:
        return {"answer": "", "citations": [], "grounded": False}
    try:
        from ..squad.base import MODEL_SMART, run_llm
    except Exception:
        return {"answer": "", "citations": [], "grounded": False}

    lines = []
    for r in results[:12]:
        lines.append(f"[{r['n']}] ({r['source']}, confidence {r['confidence']}) "
                     f"{r['title']}")
    answer = run_llm(MODEL_SMART, _ANSWER_SYSTEM,
                     f"QUESTION: {query}\n\nSOURCES:\n" + "\n".join(lines),
                     temperature=0.25)
    answer = (answer or "").strip()
    cited = sorted({int(n) for n in re.findall(r"\[(\d{1,2})\]", answer)
                    if 1 <= int(n) <= len(results)})
    return {
        "answer": answer,
        "citations": [results[n - 1] for n in cited],
        # An answer that cites nothing is an answer written from the model's own
        # memory, and is flagged rather than shown as grounded.
        "grounded": bool(cited),
        # ...except when the model correctly reports that the corpus does not
        # cover the question. That also has no citations but is the RIGHT
        # behaviour, and warning the user about it would train them to ignore
        # the warning that matters.
        "declined": bool(not cited and _declined(answer)),
    }


_DECLINE_MARKERS = (
    "do not contain", "don't contain", "do not cover", "don't cover",
    "no information about", "not provide information", "does not mention",
    "do not mention", "no sources", "sources do not", "sources don't",
    "unable to answer", "cannot answer", "insufficient information",
    "not addressed in", "none of the sources", "no relevant",
)


def _declined(answer: str) -> bool:
    low = (answer or "").lower()
    return any(m in low for m in _DECLINE_MARKERS)
