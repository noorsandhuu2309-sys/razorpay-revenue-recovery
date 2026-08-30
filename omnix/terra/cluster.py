"""Articles -> events -> evolving timelines.

A "story" in the news is not an article; it is dozens of articles from a dozen
outlets over several days. Everything interesting downstream — the timeline, the
burst detector, cross-source verification, the situation room — needs that
grouping to exist first.

Clustering is single-pass agglomerative over a combined similarity: TF-IDF
cosine (what the articles say) plus shared-entity overlap (who they are about).
The entity term matters more than it looks: two headlines about completely
different Ukraine stories share vocabulary, and it is the entity set that keeps
"Zelenskyy meets Trump" from merging into "Russian strike on Kharkiv".

Clusters are stable across refreshes by construction — a cluster's id is derived
from its earliest member, so a story keeps its identity (and its timeline) as
new articles land on it rather than being renumbered every crawl.
"""

from __future__ import annotations

import time
from collections import Counter

from . import nlp

# Similarity weights. Text carries more than entities because entity extraction
# is noisier; both are needed because either alone over-merges.
W_TEXT = 0.62
W_ENTITY = 0.38
MERGE_THRESHOLD = 0.38
MIN_CLUSTER_FOR_EVENT = 2

# A floor on the TEXT half of the similarity, checked separately from the
# combined score. Without it, entity overlap alone can clear the threshold, and
# since every US political story shares {Donald Trump, United States} the result
# is one enormous cluster containing a drug-subsidy change, an approval-rating
# poll and an interest-rate remark. Two articles must be somewhat about the same
# WORDS before being called the same event, no matter who they both name.
MIN_TEXT_SIM = 0.14


def _entity_sim(a: dict, b: dict) -> float:
    ea = {e["id"] for e in (a.get("entities") or [])}
    eb = {e["id"] for e in (b.get("entities") or [])}
    return nlp.jaccard(ea, eb)


def build(articles: list[dict], index: nlp.TfIdf | None = None,
          threshold: float = MERGE_THRESHOLD) -> dict[str, dict]:
    """Group articles into clusters. Returns {cluster_id: cluster}.

    Articles are processed newest-first so the CANONICAL member of a cluster —
    the one whose title names the story — is the most recent framing of it,
    while the cluster's identity still comes from its oldest member.
    """
    if index is None:
        index = nlp.TfIdf()
        for art in articles:
            index.add(art["id"], art["title"] + " " + art.get("summary", ""))
    index.build()

    ordered = sorted(articles, key=lambda a: -a.get("published_ts", 0))
    clusters: list[dict] = []
    # Inverted entity index so a new article only compares against clusters it
    # shares an entity with; without it this is O(n^2) over ~1000 articles.
    by_entity: dict[str, set[int]] = {}

    for art in ordered:
        ent_ids = {e["id"] for e in (art.get("entities") or [])}
        candidates: set[int] = set()
        for eid in ent_ids:
            candidates |= by_entity.get(eid, set())

        best_i, best_score = -1, 0.0
        for ci in candidates:
            cluster = clusters[ci]
            # Compare against the cluster's exemplar, not every member: with a
            # tight threshold the exemplar is representative, and it keeps the
            # pass linear.
            ex = cluster["exemplar"]
            text_sim = nlp.cosine(index.docs.get(art["id"], {}),
                                  index.docs.get(ex["id"], {}))
            if text_sim < MIN_TEXT_SIM:
                continue
            score = W_TEXT * text_sim + W_ENTITY * _entity_sim(art, ex)
            if score > best_score:
                best_i, best_score = ci, score

        if best_i >= 0 and best_score >= threshold:
            cluster = clusters[best_i]
            cluster["members"].append(art)
            ci = best_i
        else:
            cluster = {"members": [art], "exemplar": art}
            clusters.append(cluster)
            ci = len(clusters) - 1
        for eid in ent_ids:
            by_entity.setdefault(eid, set()).add(ci)

    clusters = _second_pass(clusters, index)
    return {c["id"]: c for c in (_finalize(c, index) for c in clusters)}


def _second_pass(clusters: list[dict], index: nlp.TfIdf) -> list[dict]:
    """Rejoin clusters that describe the same event in different words.

    The exemplar comparison in the main pass is deliberately strict, which
    splits "Russia pounds Kyiv with missiles" from "Russian strikes on Kyiv kill
    nine" — same event, almost no shared 3-grams. At cluster level there is more
    evidence available, so a looser test is safe here: heavy entity agreement
    plus some shared vocabulary, on clusters that also overlap in time.
    """
    prepared = []
    for c in clusters:
        toks: set[str] = set()
        ents: set[str] = set()
        for m in c["members"]:
            toks |= set(nlp.tokens(m["title"]))
            ents |= {e["id"] for e in (m.get("entities") or [])}
        times = [m.get("published_ts", 0) for m in c["members"]]
        prepared.append({"c": c, "toks": toks, "ents": ents,
                         "lo": min(times), "hi": max(times), "dead": False})

    for i, a in enumerate(prepared):
        if a["dead"]:
            continue
        for b in prepared[i + 1:]:
            if b["dead"]:
                continue
            # Same event means overlapping or adjacent in time (36h grace for a
            # story that a slow outlet picks up the next day).
            if b["lo"] - a["hi"] > 129600 or a["lo"] - b["hi"] > 129600:
                continue
            if nlp.jaccard(a["ents"], b["ents"]) < 0.5:
                continue
            if nlp.jaccard(a["toks"], b["toks"]) < 0.22:
                continue
            a["c"]["members"].extend(b["c"]["members"])
            a["toks"] |= b["toks"]
            a["ents"] |= b["ents"]
            a["lo"], a["hi"] = min(a["lo"], b["lo"]), max(a["hi"], b["hi"])
            b["dead"] = True
    return [p["c"] for p in prepared if not p["dead"]]


def _finalize(cluster: dict, index: nlp.TfIdf) -> dict:
    members = sorted(cluster["members"], key=lambda a: a.get("published_ts", 0))
    ids = [m["id"] for m in members]
    first, last = members[0], members[-1]
    # Identity from the oldest member so the id survives new arrivals.
    cid = "ev_" + first["id"][:10]

    sources = {}
    for m in members:
        src = m.get("source") or "Unknown"
        sources.setdefault(src, 0)
        sources[src] += 1

    ent_counts: Counter = Counter()
    ent_meta: dict[str, dict] = {}
    for m in members:
        for e in m.get("entities") or []:
            ent_counts[e["id"]] += e.get("count", 1)
            ent_meta.setdefault(e["id"], e)
    top_entities = [{**ent_meta[eid], "mentions": n}
                    for eid, n in ent_counts.most_common(10)]

    countries: Counter = Counter()
    for m in members:
        for iso in m.get("countries") or []:
            countries[iso] += 1

    domains: Counter = Counter()
    for m in members:
        for d in m.get("domains") or []:
            domains[d] += 1

    # The headline shown for the event: the most-corroborated framing, which is
    # the highest-confidence source's most recent title.
    canonical = max(members, key=lambda m: (m.get("confidence", 0.5),
                                            m.get("published_ts", 0)))

    sentiments = [m.get("sentiment", 0.0) for m in members]
    severities = [m.get("severity", 0.0) for m in members]
    confidences = [m.get("confidence", 0.6) for m in members]

    span_h = max(0.01, (last.get("published_ts", 0) -
                        first.get("published_ts", 0)) / 3600.0)

    return {
        "id": cid,
        "title": canonical["title"],
        "url": canonical.get("url", ""),
        "size": len(members),
        "article_ids": ids,
        "sources": sources,
        "source_count": len(sources),
        "first_ts": first.get("published_ts", 0),
        "last_ts": last.get("published_ts", 0),
        "span_hours": round(span_h, 2),
        "velocity": round(len(members) / max(1.0, span_h), 2),
        "sentiment": round(sum(sentiments) / len(sentiments), 3),
        "severity": round(max(severities), 3),
        "confidence": round(max(confidences), 3),
        "corroboration": round(_corroboration(sources), 3),
        "entities": top_entities,
        "countries": [c for c, _ in countries.most_common(8)],
        "domains": [d for d, _ in domains.most_common(3)] or ["news"],
        "keywords": index.top_terms(ids, 8),
    }


def _corroboration(sources: dict[str, int]) -> float:
    """How independently attested a story is, in 0..1.

    Not just "how many outlets" — five outlets with a 0.4 confidence prior are
    weaker evidence than two wire services, so this is the summed confidence of
    the DISTINCT sources, saturating rather than growing without bound.
    """
    total = sum(nlp.source_confidence(s) for s in sources)
    return min(1.0, total / 2.6)


# ---------------------------------------------------------------------------
# Timelines
# ---------------------------------------------------------------------------
def timeline(cluster: dict, articles_by_id: dict[str, dict],
             max_beats: int = 14) -> list[dict]:
    """The event as an ordered sequence of distinct developments.

    Every article in a cluster is NOT a timeline beat — forty outlets rewriting
    one wire story is one beat. Beats are formed by walking the members in time
    order and starting a new one whenever the text diverges enough from the
    current beat, which is the same near-duplicate test used at ingest, applied
    along the time axis.
    """
    members = [articles_by_id[a] for a in cluster["article_ids"]
               if a in articles_by_id]
    members.sort(key=lambda a: a.get("published_ts", 0))
    if not members:
        return []

    beats: list[dict] = []
    for art in members:
        sh = nlp.shingles(art["title"], 3)
        placed = False
        for beat in reversed(beats[-3:]):     # only recent beats can absorb
            if nlp.jaccard(sh, beat["_sh"]) >= 0.42:
                beat["articles"].append(art)
                beat["sources"].add(art.get("source", ""))
                beat["_sh"] |= sh
                if art.get("confidence", 0) > beat["_conf"]:
                    beat["_conf"] = art.get("confidence", 0)
                    beat["title"] = art["title"]
                    beat["url"] = art.get("url", "")
                placed = True
                break
        if not placed:
            beats.append({
                "title": art["title"], "url": art.get("url", ""),
                "ts": art.get("published_ts", 0),
                "articles": [art], "sources": {art.get("source", "")},
                "_sh": sh, "_conf": art.get("confidence", 0.6),
            })

    out = []
    for beat in beats[-max_beats:]:
        out.append({
            "title": beat["title"],
            "url": beat["url"],
            "ts": beat["ts"],
            "when": _relative(beat["ts"]),
            "outlets": sorted(s for s in beat["sources"] if s),
            "count": len(beat["articles"]),
            "sentiment": round(sum(a.get("sentiment", 0) for a in beat["articles"])
                               / len(beat["articles"]), 3),
        })
    return out


def _relative(ts: float) -> str:
    delta = time.time() - (ts or 0)
    if delta < 3600:
        return f"{int(delta // 60)}m ago"
    if delta < 86400:
        return f"{int(delta // 3600)}h ago"
    return f"{int(delta // 86400)}d ago"


def status_of(cluster: dict) -> dict:
    """Where the story is in its life: still breaking, developing, or cooling.

    Derived from the gap since the last article measured against how fast the
    story was moving while it was live — a story that produced ten articles an
    hour and has been silent for three is cooling; one that produces two a day
    and was quiet for three hours is not.
    """
    now = time.time()
    quiet_h = max(0.0, (now - cluster["last_ts"]) / 3600.0)
    velocity = cluster.get("velocity", 0.0)
    expected_gap = 1.0 / velocity if velocity > 0 else 24.0

    if quiet_h < max(1.0, expected_gap * 1.5) and cluster["size"] >= 3:
        state, label = "breaking", "Breaking — still producing coverage"
    elif quiet_h < expected_gap * 6:
        state, label = "developing", "Developing — coverage continuing"
    elif quiet_h < 48:
        state, label = "cooling", "Cooling — coverage slowing"
    else:
        state, label = "dormant", "Dormant — no new coverage"
    return {"state": state, "label": label,
            "quiet_hours": round(quiet_h, 1),
            "expected_gap_hours": round(expected_gap, 2)}


def rank(clusters: dict[str, dict], limit: int = 40,
         min_size: int = MIN_CLUSTER_FOR_EVENT) -> list[dict]:
    """Order events by how much they should matter to a reader right now.

    Four factors, all of which are separately visible in the UI so the ranking
    is explainable rather than a magic number: how many outlets carry it, how
    severe the language is, how fast it is moving, and how fresh it is.
    """
    now = time.time()
    scored = []
    for cluster in clusters.values():
        if cluster["size"] < min_size:
            continue
        recency = 0.5 ** (max(0.0, (now - cluster["last_ts"]) / 3600.0) / 18.0)
        score = (cluster["corroboration"] * 2.2
                 + cluster["severity"] * 2.0
                 + min(2.0, cluster["velocity"] * 0.35)
                 + min(1.5, cluster["size"] * 0.08)) * (0.35 + 0.65 * recency)
        rec = dict(cluster)
        rec["score"] = round(score, 3)
        rec["status"] = status_of(cluster)
        rec["when"] = _relative(cluster["last_ts"])
        scored.append(rec)
    scored.sort(key=lambda c: -c["score"])
    return scored[:limit]
