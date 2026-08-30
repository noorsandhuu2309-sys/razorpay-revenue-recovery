"""Turn an ORACLE research run into workspace intelligence.

This is the stage the research pipeline was missing. ORACLE already plans,
searches, extracts claims, verifies them against sources, hunts numeric
contradictions and synthesises — all deterministically, in
`squad/oracle_evidence.py`. What it did not do was *keep* any of it: the result
was rendered to markdown and the structure was thrown away.

Here that structure is persisted:

    sources          -> Source rows, deduped on URL
    claims           -> Claim rows + `claim` objects in the graph
    entities         -> objects, extracted once from the corpus
    relationships    -> edges between those objects
    mentions         -> `about` edges from each claim to what it is about
    conflicts        -> `conflict_of_evidence` objects, never silently resolved
    everything above -> ObjectSource provenance edges

Two guards, both load-bearing:

  * **The review gate.** Extracted entities below a relevance threshold are
    returned as *proposals* rather than written. An intelligence graph that
    accumulates every noun a model noticed is worse than no graph, because the
    junk is indistinguishable from the signal once it is in.
  * **Nothing here mints `verified`.** Objects land as `ai_inferred` and are
    promoted to `source_backed` only by an actual `ObjectSource` row pointing at
    a real retrieved page.
"""

from __future__ import annotations

import json
import re
from urllib.parse import urlparse

from sqlalchemy import select

from . import objects as objects_mod
from . import ontology as onto
from .db import session
from .schema import Claim, Source, utcnow

# Entities mentioned in fewer than this many sources are proposals, not facts.
# Two independent sources is the lowest bar that still excludes "a model said
# this once" — it is not a strong bar, which is why proposals are reviewed.
MIN_SOURCES_TO_COMMIT = 2
MAX_ENTITIES = 40
MAX_RELATIONS = 60

# A claim naming this many entities is a run-on sentence the extractor failed to
# split, not a statement about all of them. Linking every one buries the real
# subject in a fan of weak edges, so the longest names win and the rest are
# dropped — a claim's subject is nearly always its most specific noun.
MAX_MENTIONS_PER_CLAIM = 6

# Verdicts strong enough to ground an entity on their own. `verified` means two
# INDEPENDENT sources corroborated the sentence — exactly the bar
# `MIN_SOURCES_TO_COMMIT` sets, arrived at from the claim side instead of the
# entity side. Anything weaker would let a single page mint graph nodes.
_GROUNDING_VERDICTS = frozenset({"verified"})

_EXTRACT_SYSTEM = """You extract structured intelligence from research notes.

Return ONLY JSON matching:
{"entities":[{"name":"","type":"","description":"","sources":[1,2]}],
 "relations":[{"src":"","dst":"","relation":"","sources":[1]}]}

Rules:
- `type` MUST be one of: %(types)s
- `relation` MUST be one of: %(relations)s
- `name` is the entity's common name, no legal suffix, no description.
- `sources` are the [n] citation numbers in the notes that mention it.
- Only include entities that the notes actually discuss. Do not add general
  knowledge, do not infer entities that are merely implied, and never invent a
  citation number.
- `src` and `dst` in relations MUST exactly match an entity `name` you listed.
- If the notes do not support a relation, omit it. An empty list is correct."""


def _clean_json(text: str) -> dict:
    """Parse a model's JSON, tolerating fenced blocks and trailing prose."""
    t = (text or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-z]*\n?", "", t)
        t = re.sub(r"\n?```$", "", t.strip())
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", t, re.DOTALL)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}


def _mentions(haystack_key: str, entity_key: str) -> bool:
    """Does a claim name this entity?

    Both sides go through `normalize_name`, so case, punctuation and accents are
    already gone and the test is a whole-token containment rather than a
    substring one: padding both with spaces stops "Iran" matching "Irani" and
    stops "US" matching "trust". That precision is the point — a mention edge
    is an assertion that this claim is *about* this object, and a graph built
    from sloppy substring hits is worse than one with no edges, because the
    wrong edges are indistinguishable from the right ones.

    Two-character keys are refused outright: they collide with ordinary words
    far more often than they identify anything.
    """
    if not entity_key or not haystack_key or len(entity_key) < 3:
        return False
    return f" {entity_key} " in f" {haystack_key} "


def _mentioned_entities(text: str, by_key: dict[str, str]) -> list[str]:
    """Object ids for every entity named in `text`, most specific first.

    Sorted by key length descending so that when a claim names both "China" and
    "China Coast Guard", the more specific object is the one that survives the
    per-claim cap.
    """
    key = objects_mod.normalize_name(text)
    hits = [(k, oid) for k, oid in by_key.items() if _mentions(key, k)]
    hits.sort(key=lambda kv: -len(kv[0]))
    return [oid for _, oid in hits[:MAX_MENTIONS_PER_CLAIM]]


_SLUG_SPLIT = re.compile(r"[-_+]+")

# Capitalising a slug word by word turns the things these sources are actually
# about into `Eu`, `Lng` and `Nato`, which reads as a machine wrote it.
_SLUG_ACRONYMS = {
    "eu", "us", "usa", "uk", "un", "nato", "opec", "brics", "asean", "imf",
    "wto", "oecd", "gdp", "lng", "lpg", "ai", "ev", "api", "ceo", "cia", "fbi",
    "nasa", "who", "icc", "idf", "pla", "ccp", "sec", "ipo", "esg", "co2",
}


def _title_from_slug(url: str) -> str:
    """A title read off the URL's last path segment, or "" if it is not one.

    `en.wikipedia.org/wiki/World_War_III` names its subject perfectly well, and
    that costs no network call — which matters because it is the only lever
    available for sources already in the database, whose pages were retrieved
    long ago.

    This is deliberately timid. It is a *reading* of the URL, never a guess
    about the page: two alphabetic words minimum, and nothing that looks like
    an identifier (`/paper/2502.00072`, `/posts/richard-anani-789156241_the-t`),
    because a confident wrong title is worse than an honest hostname in a
    product whose entire claim is that it does not make things up.
    """
    try:
        path = urlparse(url).path
    except Exception:
        return ""
    seg = [p for p in (path or "").split("/") if p]
    if not seg:
        return ""
    last = seg[-1]
    if "." in last and not last.endswith((".html", ".htm", ".php")):
        return ""          # a file, not a slug
    last = re.sub(r"\.(html?|php)$", "", last)
    words = [w for w in _SLUG_SPLIT.split(last) if w]
    if len(words) < 2 or not all(w.isalnum() for w in words):
        return ""
    if re.search(r"\d{4,}", last):
        return ""          # identifier-shaped: a post id, a DOI, an accession
    if sum(c.isdigit() for c in last) > len(last) // 3:
        return ""
    alpha = [w for w in words if w.isalpha() and len(w) > 1]
    if len(alpha) < 2:
        return ""
    return " ".join(_slug_word(w) for w in words)


def _slug_word(w: str) -> str:
    if w.isupper():
        return w
    return w.upper() if w.lower() in _SLUG_ACRONYMS else w.capitalize()


def _source_title(entry: dict, url: str) -> str:
    """The best human-readable name for a source, host as the last resort.

    The host is a truthful fallback but it reads as a bug: the citation
    appendix prints `www.fpri.org` in both the Publisher and the Source column,
    and a reader cannot tell that row from a broken one. Anything the retrieval
    stage actually learned about the page beats it, and failing that the URL
    itself often does.
    """
    from ..tools.websearch import weak_title

    title = (entry.get("title") or "").strip()
    if title and not weak_title(title, url):
        return title
    return (_title_from_slug(url) or entry.get("host") or title or "").strip()


def persist_sources(workspace_id: str, source_scores: list[dict], *,
                    execution_id: str | None = None) -> dict[int, str]:
    """Write ORACLE's classified sources. Returns {citation number -> row id}.

    ORACLE's tier labels are carried through unchanged. They are derived from
    the URL by `classify_source()` — a real signal about what kind of page this
    is — and must not be embellished into a quality score.
    """
    out: dict[int, str] = {}
    with session() as s:
        for entry in source_scores or []:
            url = (entry.get("url") or "").strip()
            if not url:
                continue
            row = s.scalar(select(Source).where(
                Source.workspace_id == workspace_id, Source.url == url))
            if row is None:
                row = Source(
                    workspace_id=workspace_id, execution_id=execution_id,
                    url=url, title=_source_title(entry, url)[:2000],
                    publisher=(entry.get("host") or "")[:200],
                    tier=entry.get("tier") or "general",
                    tier_label=entry.get("tier_label") or "",
                    year=entry.get("year"),
                    credibility=int(entry.get("credibility") or 0),
                    snippet=(entry.get("snippet") or "")[:2000])
                s.add(row)
                s.flush()
            n = entry.get("n")
            if isinstance(n, int):
                out[n] = row.id
    return out


def persist_claims(workspace_id: str, execution_id: str, claims: list[dict],
                   source_ids: dict[int, str]) -> list[dict]:
    """Write the claim ledger, and mirror each claim into the graph.

    A claim becomes an object as well as a row so the Evidence Graph can draw
    `source -> claim -> conclusion` with the same renderer as everything else.
    """
    written: list[dict] = []
    for c in claims or []:
        text = (c.get("text") or "").strip()
        if not text:
            continue
        with session() as s:
            row = Claim(
                workspace_id=workspace_id, execution_id=execution_id or "",
                text=text, verdict=c.get("verdict") or "unverified",
                confidence=int(c.get("confidence") or 0),
                supported_by_json=list(c.get("supported_by") or []),
                contradicted_by_json=list(c.get("unsupported") or []))
            s.add(row)
            s.flush()
            claim_id = row.id

        obj, _ = objects_mod.upsert_object(
            workspace_id, "claim", text[:400],
            external_id=f"claim:{claim_id}",
            description=text,
            properties={"verdict": c.get("verdict"),
                        "independent": c.get("independent"),
                        "claimId": claim_id},
            # ORACLE's confidence is computed from real source agreement, so it
            # is a measured number and may be carried through.
            confidence=float(c.get("confidence") or 0) / 100.0 or None,
            provenance="ai_inferred", execution_id=execution_id)

        for n in (c.get("supported_by") or []):
            sid = source_ids.get(n)
            if sid:
                objects_mod.attach_source(workspace_id, sid, object_id=obj["id"],
                                          excerpt=text[:500])
        written.append({"claimId": claim_id, "objectId": obj["id"],
                        "text": text, "verdict": c.get("verdict"),
                        "confidence": c.get("confidence"),
                        # Carried so `ingest` can put the same citations behind
                        # the claim's `about` edges without re-reading the row.
                        "supportedBy": list(c.get("supported_by") or [])})
    return written


def persist_conflicts(workspace_id: str, conflicts: list[dict],
                      execution_id: str | None = None) -> list[dict]:
    """Record numeric contradictions as objects.

    The spec is explicit that a conflict is never silently resolved. Making it
    an object means it appears in the graph, can be selected, and has to be
    dealt with rather than quietly dropped from a summary.
    """
    out = []
    for i, c in enumerate(conflicts or []):
        label = (c.get("summary") or c.get("text") or
                 f"Conflicting values in research")[:300]
        obj, _ = objects_mod.upsert_object(
            workspace_id, "conflict_of_evidence", label,
            external_id=f"conflict:{execution_id or 'x'}:{i}",
            description=json.dumps(c)[:2000],
            properties={"raw": c}, provenance="ai_inferred",
            execution_id=execution_id)
        out.append(obj)
    return out


def extract_entities(question: str, notes: str, *, workspace_id: str,
                     execution_id: str | None = None) -> dict:
    """Ask a model for entities and relations, constrained to the ontology.

    Constrained rather than free-form: the type and relation vocabularies are
    injected into the prompt and re-validated on the way back, so an
    unrecognised type degrades to `thing` and an unrecognised relation to
    `related_to` instead of growing the ontology at runtime.
    """
    # See the note in `api/nova.py`: `from ..models import router` binds the
    # ModelRouter instance re-exported by the package, not the submodule, and
    # the instance has no `.shared()`. Import the singleton directly, as
    # `squad/base.py` and `squad/oracle_models.py` already do.
    from ..models.router import router as _router

    types = ", ".join(t.key for t in onto.types() if t.domain != "ai")
    relations = ", ".join(onto.EXTRACTABLE)
    system = _EXTRACT_SYSTEM % {"types": types, "relations": relations}

    result = _router.generate(
        "reasoning",
        system=system,
        user=f"Research question: {question}\n\nNotes:\n{notes[:14000]}",
        json_mode=True, temperature=0.1, max_tokens=2600,
        workspace_id=workspace_id, execution_id=execution_id, agent="oracle")

    if not result.ok:
        return {"entities": [], "relations": [], "error": result.error}

    data = _clean_json(result.text)
    ents, rels = [], []
    seen: set[str] = set()

    for e in (data.get("entities") or [])[:MAX_ENTITIES]:
        name = (e.get("name") or "").strip()
        if not name or len(name) > 200:
            continue
        key = objects_mod.normalize_name(name)
        if not key or key in seen:
            continue
        seen.add(key)
        srcs = [n for n in (e.get("sources") or []) if isinstance(n, int)]
        ents.append({
            "name": name,
            "type": onto.resolve(e.get("type") or "").key,
            "description": (e.get("description") or "").strip()[:1000],
            "sources": srcs,
        })

    names = {objects_mod.normalize_name(e["name"]): e["name"] for e in ents}
    for r in (data.get("relations") or [])[:MAX_RELATIONS]:
        src = names.get(objects_mod.normalize_name(r.get("src") or ""))
        dst = names.get(objects_mod.normalize_name(r.get("dst") or ""))
        if not src or not dst or src == dst:
            continue
        rels.append({
            "src": src, "dst": dst,
            "relation": onto.relation_ok(r.get("relation") or ""),
            "sources": [n for n in (r.get("sources") or []) if isinstance(n, int)],
        })

    return {"entities": ents, "relations": rels}


def ingest(workspace_id: str, question: str, meta: dict, notes: str, *,
           execution_id: str | None = None,
           auto_commit: bool = False) -> dict:
    """Full research -> workspace ingestion. The terminal stage of ORACLE.

    `auto_commit=False` (the default) writes only entities that clear the
    source threshold and returns the rest as proposals for review.
    """
    source_ids = persist_sources(workspace_id, meta.get("source_scores") or [],
                                 execution_id=execution_id)
    claims = persist_claims(workspace_id, execution_id or "",
                            meta.get("claims") or [], source_ids)
    conflicts = persist_conflicts(workspace_id,
                                  meta.get("numeric_conflicts") or [],
                                  execution_id)

    extracted = extract_entities(question, notes, workspace_id=workspace_id,
                                 execution_id=execution_id)

    committed: list[dict] = []
    proposed: list[dict] = []
    by_name: dict[str, str] = {}

    # Claim texts that carry enough corroboration to ground an entity. Computed
    # once: the alternative is re-normalising every claim inside the entity
    # loop, which is O(entities x claims) string folding for the same answer.
    grounding_keys = [objects_mod.normalize_name(c["text"]) for c in claims
                      if (c.get("verdict") or "") in _GROUNDING_VERDICTS]

    for e in extracted.get("entities", []):
        key = objects_mod.normalize_name(e["name"])
        enough = len(set(e["sources"])) >= MIN_SOURCES_TO_COMMIT
        # The second route past the review gate. The extractor cites the sources
        # it *noticed* an entity in, and it routinely cites one where the corpus
        # supports several — which is why a run could produce fifteen verified
        # claims and still commit nothing. An entity named by a claim that two
        # independent sources corroborated has already cleared the same bar from
        # the other direction, so holding it back as a proposal is not caution,
        # it is losing evidence the pipeline already established.
        grounded = any(_mentions(g, key) for g in grounding_keys)
        if not (enough or grounded or auto_commit):
            proposed.append(e)
            continue
        obj, created = objects_mod.upsert_object(
            workspace_id, e["type"], e["name"],
            description=e["description"], provenance="ai_inferred",
            execution_id=execution_id, tags=["research"])
        by_name[key] = obj["id"]
        for n in e["sources"]:
            sid = source_ids.get(n)
            if sid:
                objects_mod.attach_source(workspace_id, sid,
                                          object_id=obj["id"],
                                          excerpt=e["description"][:500])
        committed.append({**obj, "new": created, "grounding": (
            "sources" if enough else "claim" if grounded else "auto")})

    edges = 0
    for r in extracted.get("relations", []):
        src = by_name.get(objects_mod.normalize_name(r["src"]))
        dst = by_name.get(objects_mod.normalize_name(r["dst"]))
        if not src or not dst:
            continue          # one endpoint was only proposed, not committed
        linked = objects_mod.link(workspace_id, src, dst, r["relation"],
                                  provenance="ai_inferred",
                                  execution_id=execution_id)
        if linked is None:
            continue
        edges += 1
        for n in r["sources"]:
            sid = source_ids.get(n)
            if sid:
                objects_mod.attach_source(workspace_id, sid,
                                          relationship_id=linked[0]["id"])

    # -- the claims join the graph -------------------------------------------
    # Until this existed, `persist_claims` gave every claim an object and then
    # only ever called `attach_source`, which writes an ObjectSource provenance
    # row — not a Relationship. A claim was therefore structurally incapable of
    # having an edge, and every run added its claims to the Space as isolated
    # nodes: 91 claim objects, 0 relationships between any of them and anything.
    #
    # `about` is the honest relation. The claim is not evidence FOR the entity
    # and does not follow FROM it; it is a statement whose subject that entity
    # is. Linking through shared subjects is also what makes the Space
    # cumulative, which is the whole promise: two runs that both discuss Hormuz
    # now hang off one Hormuz node instead of forming two disconnected islands.
    # The pool is this run's entities PLUS what the Space already knows. Without
    # the second half a claim could only ever attach to nouns the extractor
    # happened to re-notice on this run, so asking a follow-up question built a
    # second island beside the first instead of extending it — and "the second
    # question builds on the first" is the reason the graph exists at all.
    # `upsert_object` dedupes on normalised name, so a returning entity is the
    # same node, not a twin.
    pool = dict(by_name)
    for o in objects_mod.list_objects(workspace_id, limit=1000):
        # Claims and generated documents are not what a claim is *about*, and
        # linking claim->claim by shared wording would assert a relationship the
        # extraction never established.
        if o.get("type") in ("claim", "document", "dataset"):
            continue
        pool.setdefault(objects_mod.normalize_name(o["name"]), o["id"])

    claim_edges = 0
    for c in claims:
        for oid in _mentioned_entities(c["text"], pool):
            linked = objects_mod.link(
                workspace_id, c["objectId"], oid, "about",
                # A mention is drawn from the claim's own text, so it is exactly
                # as well-attested as the claim. `attach_source` below promotes
                # it to source_backed when the claim carried real citations.
                provenance="ai_inferred", execution_id=execution_id)
            if linked is None:
                continue
            claim_edges += 1
            for n in (c.get("supportedBy") or []):
                sid = source_ids.get(n)
                if sid:
                    objects_mod.attach_source(workspace_id, sid,
                                              relationship_id=linked[0]["id"])

    # A contradiction is *about* the things it contradicts. Without this a
    # conflict object is as orphaned as the claims were, which defeats the
    # point of making it an object — it exists to be impossible to overlook.
    for cf in conflicts:
        for oid in _mentioned_entities(
                f"{cf.get('name') or ''} {cf.get('description') or ''}", pool):
            if objects_mod.link(workspace_id, cf["id"], oid, "about",
                                provenance="ai_inferred",
                                execution_id=execution_id) is not None:
                claim_edges += 1

    if committed or claims:
        objects_mod.recompute_salience(workspace_id)

    return {
        "workspace": workspace_id,
        "sources": len(source_ids),
        "claims": claims,
        "conflicts": conflicts,
        "objects": committed,
        "relationships": edges + claim_edges,
        "entityRelationships": edges,
        "claimRelationships": claim_edges,
        "proposed": proposed,
        "error": extracted.get("error", ""),
    }


def commit_proposals(workspace_id: str, proposals: list[dict], *,
                     execution_id: str | None = None) -> list[dict]:
    """Accept entities the review gate held back."""
    out = []
    for e in proposals or []:
        name = (e.get("name") or "").strip()
        if not name:
            continue
        obj, created = objects_mod.upsert_object(
            workspace_id, onto.resolve(e.get("type") or "").key, name,
            description=(e.get("description") or "")[:1000],
            # A user accepting a proposal is asserting it — a stronger and more
            # honest statement than the extraction that produced it.
            provenance="user_created", execution_id=execution_id,
            tags=["research"])
        out.append({**obj, "new": created})
    if out:
        objects_mod.recompute_salience(workspace_id)
    return out


def claim_ledger(workspace_id: str, *, execution_id: str | None = None,
                 limit: int = 200) -> list[dict]:
    """The Claim Ledger: every claim with the evidence behind it.

    Verdicts come from ORACLE's deterministic verifier, not from a model asked
    how confident it feels.
    """
    with session() as s:
        stmt = select(Claim).where(Claim.workspace_id == workspace_id)
        if execution_id:
            stmt = stmt.where(Claim.execution_id == execution_id)
        rows = s.scalars(stmt.order_by(Claim.created_at.desc()).limit(limit)).all()

        out = []
        for c in rows:
            out.append({
                "id": c.id,
                "text": c.text,
                "verdict": c.verdict,
                "confidence": c.confidence,
                "supportedBy": c.supported_by_json or [],
                "contradictedBy": c.contradicted_by_json or [],
                "executionId": c.execution_id,
                "status": _VERDICT_LABELS.get(c.verdict, "Unverified"),
            })
        return out


# ORACLE's verdict mapped onto the spec's status scale. "Mixed Evidence" is
# not faked: nothing currently produces it without contradicting sources, so it
# is derived rather than guessed.
#
# "Single Source" is the honest middle the ledger was missing. Claims are
# extracted FROM sources, so verifying one against its own source nearly always
# passes — every claim came back "Strongly Supported" and the column stopped
# carrying information. Corroboration by a second independent source is the
# part a reader cannot assume, so that is what the top label now means.
_VERDICT_LABELS = {
    "verified": "Corroborated",
    "single_source": "Single Source",
    "weak": "Weak Evidence",
    "unsupported": "Unsupported",
    "unverified": "Unverified",
}


def evidence_graph(workspace_id: str, claim_id: str) -> dict:
    """`source -> claim` with the real rows behind it, for one claim."""
    with session() as s:
        c = s.scalar(select(Claim).where(
            Claim.workspace_id == workspace_id, Claim.id == claim_id))
        if c is None:
            return {}
    obj = None
    for o in objects_mod.list_objects(workspace_id, type_key="claim", limit=1000):
        if (o.get("properties") or {}).get("claimId") == claim_id:
            obj = o
            break
    sources = objects_mod.sources_for(workspace_id, object_id=obj["id"]) if obj else []
    return {
        "claim": {"id": c.id, "text": c.text, "verdict": c.verdict,
                  "confidence": c.confidence,
                  "status": _VERDICT_LABELS.get(c.verdict, "Unverified")},
        "objectId": obj["id"] if obj else None,
        "sources": sources,
    }
