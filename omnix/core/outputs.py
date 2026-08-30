"""Create as an output action — the blueprint's §12.

The interaction this exists for: hold some objects, choose Create > Report, and
get a real document back. Not a "PDF Creator" tab you navigate to and then have
to re-explain your context inside; the selection *is* the input, which is the
whole point of §6.

Three rules shape the design, and each one is why a piece of this file looks the
way it does:

  * **One content shape, many styles.** Every builder emits the same envelope —
    a list of typed `sections` — so the Markdown, HTML and CSV renderers are
    written once and adding a new output style means adding a builder, never
    touching a renderer. This mirrors `artifacts.py`: content is opaque to the
    transport, structured for the reader.
  * **The data is real or the section is absent.** Sections are assembled from
    objects, relationships, events, claims and sources that exist in the Space.
    The one model-written section is explicitly flagged `generated: True`, and
    when no model is reachable it is *omitted* rather than filled with an
    apology. An empty Findings heading teaches the user the feature is fake.
  * **An output can never be more trustworthy than its inputs.** The artifact
    object lands in the graph carrying the WEAKEST provenance of everything it
    was built from. A report derived from `ai_inferred` entities is
    `ai_inferred`, however confident its prose reads.

The artifact is written twice on purpose: as an `Artifact` row (the addressable
handoff, with `derived_from` lineage) and as an `ObjectNode` in the graph (so it
becomes selectable context that can be fed back into an agent, which §12 asks
for in its last line).
"""

from __future__ import annotations

import csv
import io
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from html import escape

from sqlalchemy import select

from . import artifacts as artifacts_mod
from . import objects as objects_mod
from . import ontology as onto
from .db import session
from .schema import Claim, ObjectNode, ObjectSource, Source, iso, utcnow


# ---------------------------------------------------------------------------
# Style registry
# ---------------------------------------------------------------------------
class OutputStyle:
    """One thing the Create verb can produce.

    `artifact_type` is what the artifact row is tagged with; `object_type` is
    the ontology type the graph node takes, which decides how it draws. They
    differ because "a chart" is a dataset in the graph but a chart to a reader.
    """

    def __init__(self, key: str, label: str, glyph: str, artifact_type: str,
                 object_type: str, builder: str, formats: tuple[str, ...],
                 hint: str, min_objects: int = 1):
        self.key = key
        self.label = label
        self.glyph = glyph
        self.artifact_type = artifact_type
        self.object_type = object_type
        self.builder = builder
        self.formats = formats
        self.hint = hint
        self.min_objects = min_objects

    def public(self) -> dict:
        return {"key": self.key, "label": self.label, "glyph": self.glyph,
                "artifactType": self.artifact_type, "objectType": self.object_type,
                "formats": list(self.formats), "hint": self.hint,
                "minObjects": self.min_objects}


STYLES: dict[str, OutputStyle] = {}


def _style(*args, **kw) -> OutputStyle:
    st = OutputStyle(*args, **kw)
    STYLES[st.key] = st
    return st


# Order here is the order the Create menu renders in. The dossier leads because
# it is the one output that carries a citation appendix, and handing a sourced
# document to someone who has to be convinced is the job this product is bought
# for. `min_objects=0`: a dossier is about the CLAIMS in a Space, which exist
# whether or not anything is selected, so requiring a selection would put the
# main export behind a step the user has no reason to expect.
_style("dossier", "Dossier", "❖", "research-dossier", "document", "dossier",
       ("pdf", "html", "md"),
       "Claims, verdicts and a numbered citation appendix — the handoff",
       min_objects=0)
_style("report", "Report", "▤", "research-report", "document", "report",
       ("md", "html"), "Full written report over everything held")
_style("brief", "Brief", "◈", "brief", "document", "brief",
       ("md", "html"), "One-page executive summary")
_style("dashboard", "Dashboard", "▩", "dashboard", "document", "dashboard",
       ("html", "md"), "Measured tiles — counts, provenance, coverage")
_style("timeline", "Timeline", "▬", "timeline", "document", "timeline",
       ("md", "html", "csv"), "Everything that happened, in order")
_style("chart", "Chart", "◫", "chart", "dataset", "chart",
       ("html", "csv"), "A series drawn from the graph")
_style("spreadsheet", "Spreadsheet", "▦", "table", "dataset", "spreadsheet",
       ("csv", "html", "md"), "Rows you can open in Excel")
_style("presentation", "Presentation", "▭", "presentation", "document",
       "presentation", ("html", "md"), "Slides, one idea each")
_style("page", "Website", "⬒", "page", "document", "page",
       ("html",), "A standalone shareable page")
_style("note", "Note", "✎", "note", "note", "note",
       ("md",), "A short note pinned into the Space")


def styles() -> list[dict]:
    return [s.public() for s in STYLES.values()]


# ---------------------------------------------------------------------------
# Gathering — what the builders draw from
# ---------------------------------------------------------------------------
class Material:
    """Everything the Space knows about the held objects, fetched once.

    Builders receive this rather than a workspace id so that no builder can
    quietly issue its own query and produce a document inconsistent with its
    siblings — every style describes the same material, differently.
    """

    def __init__(self, workspace_id: str, objects: list[dict],
                 relationships: list[dict], events: list[dict],
                 claims: list[dict], sources: list[dict], question: str = ""):
        self.workspace_id = workspace_id
        self.objects = objects
        self.relationships = relationships
        self.events = events
        self.claims = claims
        self.sources = sources
        self.question = question
        self.by_id = {o["id"]: o for o in objects}

    def name(self, object_id: str) -> str:
        o = self.by_id.get(object_id)
        return o["name"] if o else "—"

    @property
    def provenance_floor(self) -> str:
        """The weakest provenance among the inputs.

        `PROVENANCE[*].rank` is weakest-LAST (user_created 0 … ai_inferred 3),
        so the floor is the maximum rank. With no objects held the honest
        answer is the weakest value, not the strongest.
        """
        if not self.objects:
            return onto.DEFAULT_PROVENANCE
        worst = max(self.objects,
                    key=lambda o: onto.PROVENANCE.get(
                        o.get("provenance") or onto.DEFAULT_PROVENANCE,
                        {"rank": 3})["rank"])
        return worst.get("provenance") or onto.DEFAULT_PROVENANCE

    @property
    def counts(self) -> dict:
        return {"objects": len(self.objects), "relationships": len(self.relationships),
                "events": len(self.events), "claims": len(self.claims),
                "sources": len(self.sources)}

    @property
    def citation_numbers(self) -> dict[str, int]:
        """`{source id -> [n]}`, numbered in order of first citation.

        The numbering belongs to the DOCUMENT, not to the source: a Space with
        380 sources produces a dossier citing eleven of them, and an appendix
        that numbered all 380 — or that reused the research run's own numbering
        — would send a reader hunting for [147] in a list of eleven. So the
        order of first appearance in the claim list is the order in the
        appendix, which is also the convention every reader already knows.
        """
        order: dict[str, int] = {}
        for c in self.claims:
            for sid in c.get("sourceIds") or []:
                if sid not in order:
                    order[sid] = len(order) + 1
        return order

    @property
    def cited_sources(self) -> list[dict]:
        """The sources this document actually cites, in citation order.

        Only the cited ones. An appendix listing everything the Space has ever
        retrieved is a bibliography of the tool, not of the argument, and it
        lets an unsupported document look well-sourced by association.
        """
        numbers = self.citation_numbers
        by_id = {s["id"]: s for s in self.sources}
        out = []
        for sid, n in sorted(numbers.items(), key=lambda kv: kv[1]):
            src = by_id.get(sid)
            if src:
                out.append({**src, "n": n})
        return out

    def cite(self, claim: dict) -> str:
        """A claim's citation markers, e.g. "[1][4]"."""
        numbers = self.citation_numbers
        ns = sorted(numbers[s] for s in (claim.get("sourceIds") or [])
                    if s in numbers)
        return "".join(f"[{n}]" for n in ns)


def _claim_citations(workspace_id: str, claim_ids: list[str]) -> dict[str, list[str]]:
    """`{claim id -> [source id, ...]}`, from the provenance edges.

    The join has to go through the graph rather than through `Claim`. The row's
    `supported_by_json` holds ORACLE's per-run citation numbers — `[1, 3]` means
    "the first and third source *of that run*" — and nothing maps those back to
    Source rows afterwards, because sources are deduped on URL across runs and a
    page cited as [1] in June can be [7] in July. What survives is the
    ObjectSource edge `persist_claims` wrote from the claim's graph object to
    the real Source row, so that is the spine the appendix is built on.
    """
    if not claim_ids:
        return {}
    keys = {f"claim:{cid}": cid for cid in claim_ids}
    out: dict[str, list[str]] = {}
    with session() as s:
        rows = s.execute(
            select(ObjectNode.external_id, ObjectSource.source_id)
            .join(ObjectSource, ObjectSource.object_id == ObjectNode.id)
            .where(ObjectNode.workspace_id == workspace_id,
                   ObjectNode.external_id.in_(list(keys)))).all()
    for external_id, source_id in rows:
        cid = keys.get(external_id)
        if cid and source_id:
            out.setdefault(cid, [])
            if source_id not in out[cid]:
                out[cid].append(source_id)
    return out


def gather(workspace_id: str, object_ids: list[str], *,
           question: str = "", hours: float = 720.0) -> Material:
    """Hydrate a selection into everything attached to it."""
    ids = [i for i in (object_ids or []) if i]
    objects: list[dict] = []
    for oid in ids[:60]:
        o = objects_mod.get_object(workspace_id, oid)
        if o:
            objects.append(o)

    held = {o["id"] for o in objects}
    rels: list[dict] = []
    seen_rel: set[str] = set()
    for oid in held:
        for r in objects_mod.relationships_of(workspace_id, oid, limit=60):
            if r["id"] not in seen_rel:
                seen_rel.add(r["id"])
                rels.append(r)

    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    events = objects_mod.timeline(workspace_id, object_ids=list(held) or None,
                                  since=since, limit=200) if held else []

    # Claims and sources are workspace-scoped rather than object-scoped: the
    # evidence base behind a report is the Space's, and narrowing it to objects
    # would silently drop the claim that motivated the selection.
    with session() as s:
        claim_rows = s.scalars(
            select(Claim).where(Claim.workspace_id == workspace_id)
            .order_by(Claim.created_at.desc()).limit(60)).all()
        claims = [{"id": c.id, "text": c.text, "verdict": c.verdict,
                   "confidence": c.confidence,
                   "supportedBy": len(c.supported_by_json or []),
                   "contradictedBy": len(c.contradicted_by_json or [])}
                  for c in claim_rows]

        source_rows = s.scalars(
            select(Source).where(Source.workspace_id == workspace_id)
            .order_by(Source.retrieved_at.desc()).limit(80)).all()
        sources = [{"id": r.id, "url": r.url, "title": r.title,
                    "publisher": r.publisher, "tier": r.tier,
                    "tierLabel": r.tier_label, "credibility": r.credibility,
                    "retrievedAt": iso(r.retrieved_at)} for r in source_rows]

    # Which source backs which claim, so the dossier can number its citations.
    # Outside the session above because it opens its own.
    cited = _claim_citations(workspace_id, [c["id"] for c in claims])
    for c in claims:
        c["sourceIds"] = cited.get(c["id"], [])

    return Material(workspace_id, objects, rels, events, claims, sources,
                    question=question)


# ---------------------------------------------------------------------------
# Section helpers — the one shape every renderer understands
# ---------------------------------------------------------------------------
def _text(heading: str, body: str, *, generated: bool = False) -> dict:
    return {"kind": "text", "heading": heading, "body": body,
            "generated": generated}


def _list(heading: str, items: list[str]) -> dict:
    return {"kind": "list", "heading": heading, "items": items}


def _table(heading: str, columns: list[str], rows: list[list]) -> dict:
    return {"kind": "table", "heading": heading, "columns": columns, "rows": rows}


def _metrics(heading: str, metrics: list[dict]) -> dict:
    return {"kind": "metrics", "heading": heading, "metrics": metrics}


def _chart(heading: str, series: list[dict], *, unit: str = "") -> dict:
    """A bar series. Deliberately one chart kind: the renderer draws it with
    CSS widths, so a published page needs no charting library and no network."""
    return {"kind": "chart", "heading": heading, "series": series, "unit": unit}


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------
def _object_rows(m: Material) -> list[list]:
    return [[o["name"], o.get("typeLabel") or o.get("type") or "",
             onto.PROVENANCE.get(o.get("provenance") or "", {}).get("label", ""),
             len(objects_mod.relationships_of(m.workspace_id, o["id"], limit=200)),
             (o.get("description") or "")[:180]]
            for o in m.objects]


def _relationship_rows(m: Material) -> list[list]:
    rows = []
    for r in sorted(m.relationships, key=lambda x: -(x.get("weight") or 0))[:60]:
        rows.append([m.name(r["src"]), r.get("label") or r.get("relation"),
                     m.name(r["dst"]), round(r.get("weight") or 0, 2),
                     onto.PROVENANCE.get(r.get("provenance") or "",
                                         {}).get("label", "")])
    return rows


def _event_rows(m: Material) -> list[list]:
    return [[(e.get("occurredAt") or "")[:10], m.name(e.get("objectId") or ""),
             e.get("relevance") or "", (e.get("title") or "")[:200]]
            for e in m.events[:120]]


def _provenance_metrics(m: Material) -> list[dict]:
    by_prov: dict[str, int] = {}
    for o in m.objects:
        key = o.get("provenance") or onto.DEFAULT_PROVENANCE
        by_prov[key] = by_prov.get(key, 0) + 1
    return [{"label": onto.PROVENANCE.get(k, {}).get("label", k), "value": v}
            for k, v in sorted(by_prov.items(),
                               key=lambda kv: onto.PROVENANCE.get(
                                   kv[0], {"rank": 9})["rank"])]


def _synthesis(m: Material, instruction: str, *, max_tokens: int = 700) -> dict | None:
    """The one model-written section. Returns None when no model answers.

    Omitting beats apologising: a heading followed by "the model was
    unavailable" is worse than no heading, and a reader who sees only measured
    sections has been told the truth about what this document is.
    """
    facts = []
    for o in m.objects[:20]:
        line = f"- {o['name']} ({o.get('typeLabel') or o.get('type')})"
        if o.get("description"):
            line += f": {o['description'][:240]}"
        facts.append(line)
    rels = [f"- {m.name(r['src'])} {r.get('label')} {m.name(r['dst'])}"
            for r in m.relationships[:25]]
    claims = [f"- [{c['verdict']}] {c['text'][:220]}" for c in m.claims[:15]]
    events = [f"- {(e.get('occurredAt') or '')[:10]} {e.get('title', '')[:160]}"
              for e in m.events[:20]]

    body = "\n".join(filter(None, [
        "OBJECTS:\n" + "\n".join(facts) if facts else "",
        "\nRELATIONSHIPS:\n" + "\n".join(rels) if rels else "",
        "\nVERIFIED CLAIMS:\n" + "\n".join(claims) if claims else "",
        "\nRECENT EVENTS:\n" + "\n".join(events) if events else "",
    ]))
    if not body.strip():
        return None

    system = ("You are writing one section of an OMNIX intelligence document. "
              "Use ONLY the workspace material given — never introduce facts, "
              "figures, dates or entities that do not appear in it. If the "
              "material does not support a conclusion, say what is missing "
              "instead of guessing. No preamble, no restating the question, no "
              "bullet-point padding. Write prose an analyst would sign.")
    try:
        from ..models.router import router as _router
        result = _router.generate(
            "fast",
            messages=[{"role": "system", "content": system},
                      {"role": "user",
                       "content": f"{instruction}\n\n{body}"}],
            temperature=0.3, max_tokens=max_tokens,
            workspace_id=m.workspace_id, agent="nova")
    except Exception:
        return None
    if not getattr(result, "ok", False) or not (result.text or "").strip():
        return None
    return _text("Synthesis", result.text.strip(), generated=True)


def build_report(m: Material, title: str) -> list[dict]:
    sections: list[dict] = []
    syn = _synthesis(m, (
        "Write the opening analysis for a report titled "
        f"{title!r}. Two to four paragraphs: what these objects are, how they "
        "relate, and what the evidence supports. Name the gaps explicitly."))
    if syn:
        sections.append(syn)
    if m.objects:
        sections.append(_table("Objects",
                               ["Name", "Type", "Provenance", "Links", "Description"],
                               _object_rows(m)))
    if m.relationships:
        sections.append(_table("Relationships",
                               ["From", "Relation", "To", "Weight", "Provenance"],
                               _relationship_rows(m)))
    if m.claims:
        sections.append(_table("Claims",
                               ["Verdict", "Confidence", "Supporting", "Claim"],
                               [[c["verdict"], c["confidence"], c["supportedBy"],
                                 c["text"][:300]] for c in m.claims[:40]]))
    if m.events:
        sections.append(_table("Recent activity",
                               ["Date", "Object", "Relevance", "Event"],
                               _event_rows(m)))
    if m.sources:
        sections.append(_table("Sources",
                               ["Publisher", "Tier", "Title", "URL"],
                               [[s.get("publisher") or "", s.get("tierLabel") or "",
                                 (s.get("title") or "")[:160], s.get("url") or ""]
                                for s in m.sources[:50]]))
    return sections


def build_brief(m: Material, title: str) -> list[dict]:
    sections: list[dict] = []
    syn = _synthesis(m, (
        "Write a single executive paragraph — at most 120 words — stating what "
        "matters right now about this material and why. No hedging, no list."),
        max_tokens=320)
    if syn:
        sections.append(syn)
    sections.append(_metrics("At a glance", [
        {"label": "Objects", "value": len(m.objects)},
        {"label": "Relationships", "value": len(m.relationships)},
        {"label": "Events", "value": len(m.events)},
        {"label": "Claims", "value": len(m.claims)},
    ]))
    high = [e for e in m.events if e.get("relevance") == "high"][:12]
    if high:
        sections.append(_list("Highest relevance",
                              [f"{(e.get('occurredAt') or '')[:10]} — "
                               f"{m.name(e.get('objectId') or '')}: {e.get('title')}"
                               for e in high]))
    top = sorted(m.objects, key=lambda o: -(o.get("salience") or 0))[:10]
    if top:
        sections.append(_list("Principal objects",
                              [f"{o['name']} — {o.get('typeLabel') or o.get('type')}"
                               for o in top]))
    return sections


def build_dashboard(m: Material, title: str) -> list[dict]:
    by_type: dict[str, int] = {}
    for o in m.objects:
        key = o.get("typeLabel") or o.get("type") or "Other"
        by_type[key] = by_type.get(key, 0) + 1
    by_rel: dict[str, int] = {}
    for r in m.relationships:
        key = r.get("label") or r.get("relation") or "related to"
        by_rel[key] = by_rel.get(key, 0) + 1
    verdicts: dict[str, int] = {}
    for c in m.claims:
        verdicts[c["verdict"] or "unrated"] = verdicts.get(c["verdict"] or "unrated", 0) + 1

    sections = [
        _metrics("Totals", [
            {"label": "Objects", "value": len(m.objects)},
            {"label": "Relationships", "value": len(m.relationships)},
            {"label": "Events", "value": len(m.events)},
            {"label": "Claims", "value": len(m.claims)},
            {"label": "Sources", "value": len(m.sources)},
        ]),
        _metrics("Provenance", _provenance_metrics(m)),
    ]
    if by_type:
        sections.append(_chart("By type", [{"label": k, "value": v} for k, v in
                                           sorted(by_type.items(), key=lambda kv: -kv[1])]))
    if by_rel:
        sections.append(_chart("By relation", [{"label": k, "value": v} for k, v in
                                               sorted(by_rel.items(),
                                                      key=lambda kv: -kv[1])[:12]]))
    if verdicts:
        sections.append(_chart("Claim verdicts",
                               [{"label": k, "value": v} for k, v in verdicts.items()]))
    return sections


def build_timeline(m: Material, title: str) -> list[dict]:
    if not m.events:
        return [_text("No events",
                      "Nothing has happened to these objects in the window "
                      "covered by this Space. Track an object and OMNIX will "
                      "record what moves.")]
    return [_table("Timeline", ["Date", "Object", "Relevance", "Event"],
                   _event_rows(m))]


def build_chart(m: Material, title: str) -> list[dict]:
    """Events per day, which is the one series every Space genuinely has."""
    per_day: dict[str, int] = {}
    for e in m.events:
        day = (e.get("occurredAt") or "")[:10]
        if day:
            per_day[day] = per_day.get(day, 0) + 1
    if per_day:
        series = [{"label": d, "value": v} for d, v in sorted(per_day.items())]
        return [_chart("Events per day", series, unit="events"),
                _table("Data", ["Date", "Events"], [[d, v] for d, v in sorted(per_day.items())])]
    by_type: dict[str, int] = {}
    for o in m.objects:
        key = o.get("typeLabel") or o.get("type") or "Other"
        by_type[key] = by_type.get(key, 0) + 1
    series = [{"label": k, "value": v} for k, v in
              sorted(by_type.items(), key=lambda kv: -kv[1])]
    return [_chart("Objects by type", series, unit="objects"),
            _table("Data", ["Type", "Objects"], [[s["label"], s["value"]] for s in series])]


def build_spreadsheet(m: Material, title: str) -> list[dict]:
    sections = [_table("Objects",
                       ["Name", "Type", "Provenance", "Links", "Description"],
                       _object_rows(m))]
    if m.relationships:
        sections.append(_table("Relationships",
                               ["From", "Relation", "To", "Weight", "Provenance"],
                               _relationship_rows(m)))
    if m.events:
        sections.append(_table("Events", ["Date", "Object", "Relevance", "Event"],
                               _event_rows(m)))
    return sections


def build_presentation(m: Material, title: str) -> list[dict]:
    """Slides are sections too — the HTML renderer paginates on headings."""
    sections: list[dict] = [_metrics("Overview", [
        {"label": "Objects", "value": len(m.objects)},
        {"label": "Relationships", "value": len(m.relationships)},
        {"label": "Claims", "value": len(m.claims)},
    ])]
    syn = _synthesis(m, (
        "Write 3 to 5 slide bodies for a deck. Each slide is a heading line "
        "followed by at most 3 short bullets. Separate slides with a blank "
        "line. Ground every bullet in the material."), max_tokens=600)
    if syn:
        sections.append(syn)
    top = sorted(m.objects, key=lambda o: -(o.get("salience") or 0))[:8]
    if top:
        sections.append(_list("Principal objects",
                              [f"{o['name']} — {o.get('typeLabel') or o.get('type')}"
                               for o in top]))
    if m.claims:
        sections.append(_list("Evidence",
                              [f"[{c['verdict']}] {c['text'][:160]}"
                               for c in m.claims[:6]]))
    return sections


def _citation_appendix(m: Material) -> dict | None:
    """The numbered source list a reader can check the claims against.

    Every column here is a recorded fact about the retrieval — publisher, tier,
    credibility as scored at fetch time, and the date OMNIX actually fetched it.
    The retrieval date matters more than it looks: a citation to a page that
    changed after it was read is the specific failure this product exists to
    prevent, and it is the first thing a reviewer asks about.
    """
    cited = m.cited_sources
    if not cited:
        return None
    return _table(
        "Citations",
        ["#", "Publisher", "Source", "Tier", "Credibility", "Retrieved", "URL"],
        [[f"[{s['n']}]", s.get("publisher") or "—",
          (s.get("title") or "")[:160] or "—",
          s.get("tierLabel") or s.get("tier") or "—",
          s.get("credibility") or "—",
          (s.get("retrievedAt") or "")[:10] or "—",
          s.get("url") or ""]
         for s in cited])


def build_dossier(m: Material, title: str) -> list[dict]:
    """A defensible answer, formatted to be handed to someone else.

    This is the export the product is actually sold on: not "here is what the
    model said" but "here is each claim, what verdict the verifier reached, and
    which numbered source backs it". The claims table and the appendix share one
    numbering, so a reader can go from any line to the page it came from without
    trusting a word of the prose.

    Claims are ordered by how well attested they are, not by when they were
    extracted. A reader who stops after the first screen should have read the
    best-supported material, and a document that opens with its weakest claim
    invites the reader to distrust the rest of it.
    """
    order = {"verified": 0, "single_source": 1, "weak": 2,
             "unverified": 3, "unsupported": 4}
    claims = sorted(m.claims, key=lambda c: (order.get(c.get("verdict") or "", 3),
                                             -(c.get("confidence") or 0)))

    sections: list[dict] = []
    syn = _synthesis(m, (
        f"Write the opening summary for a research dossier titled {title!r}. "
        "Two or three paragraphs stating what the evidence supports and, "
        "explicitly, where it is thin. Do not restate the claim list."))
    if syn:
        sections.append(syn)

    # The honest headline number: how much of this document is corroborated.
    corroborated = sum(1 for c in m.claims if c.get("verdict") == "verified")
    sections.append(_metrics("Evidence base", [
        {"label": "Claims", "value": len(m.claims)},
        {"label": "Corroborated", "value": corroborated},
        {"label": "Sources cited", "value": len(m.cited_sources)},
        {"label": "Sources held", "value": len(m.sources)},
    ]))

    if claims:
        sections.append(_table(
            "Claims and their evidence",
            ["Verdict", "Confidence", "Claim", "Citations"],
            [[c.get("verdict") or "unverified", c.get("confidence") or 0,
              c["text"][:400], m.cite(c) or "— none on file"]
             for c in claims[:60]]))

    if m.objects:
        sections.append(_table("Subjects of this dossier",
                               ["Name", "Type", "Provenance", "Links", "Description"],
                               _object_rows(m)))
    if m.relationships:
        sections.append(_table("How they connect",
                               ["From", "Relation", "To", "Weight", "Provenance"],
                               _relationship_rows(m)))

    appendix = _citation_appendix(m)
    if appendix:
        sections.append(appendix)
    else:
        # Said plainly rather than omitted. A dossier whose claims carry no
        # traceable sources is exactly the document this product exists to stop
        # someone circulating by accident, so it has to say so on its face.
        sections.append(_text(
            "Citations",
            "No claim in this Space carries a source edge, so this document "
            "has no citation appendix and nothing in it can be checked against "
            "an original. Run a research question to produce sourced claims."))
    return sections


def build_page(m: Material, title: str) -> list[dict]:
    return build_report(m, title)


def build_note(m: Material, title: str) -> list[dict]:
    names = ", ".join(o["name"] for o in m.objects[:12]) or "nothing selected"
    return [_text("Note", f"{title}\n\nAbout: {names}"),
            _list("Held", [f"{o['name']} ({o.get('typeLabel') or o.get('type')})"
                           for o in m.objects[:30]])]


BUILDERS: dict[str, Callable[[Material, str], list[dict]]] = {
    "dossier": build_dossier,
    "report": build_report,
    "brief": build_brief,
    "dashboard": build_dashboard,
    "timeline": build_timeline,
    "chart": build_chart,
    "spreadsheet": build_spreadsheet,
    "presentation": build_presentation,
    "page": build_page,
    "note": build_note,
}


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------
def create(workspace_id: str, style_key: str, object_ids: list[str], *,
           title: str = "", question: str = "",
           execution_id: str | None = None) -> dict:
    """Produce an output from a selection. Raises ValueError on a bad style."""
    style = STYLES.get((style_key or "").strip().lower())
    if style is None:
        raise ValueError(f"unknown output style {style_key!r}")

    m = gather(workspace_id, object_ids, question=question)
    if len(m.objects) < style.min_objects:
        raise ValueError(
            f"{style.label} needs at least {style.min_objects} object"
            f"{'s' if style.min_objects > 1 else ''} — "
            f"{len(m.objects)} of the {len(object_ids or [])} ids given exist "
            "in this Space")

    heading = (title or "").strip() or _default_title(style, m)
    sections = BUILDERS[style.builder](m, heading)

    content = {
        "style": style.key,
        "styleLabel": style.label,
        "title": heading,
        "question": question,
        "generatedAt": iso(utcnow()),
        "sections": sections,
        "inputs": {
            "objectIds": [o["id"] for o in m.objects],
            "objects": [{"id": o["id"], "name": o["name"], "type": o.get("type"),
                         "provenance": o.get("provenance")} for o in m.objects],
        },
        "counts": m.counts,
        # The claim the document makes about itself. The UI renders this rather
        # than deciding on its own how much to trust the page.
        "provenance": {
            "floor": m.provenance_floor,
            "floorLabel": onto.PROVENANCE.get(m.provenance_floor, {}).get("label", ""),
            "breakdown": _provenance_metrics(m),
            "synthesised": any(s.get("generated") for s in sections),
        },
    }

    # Lineage: every artifact the input objects came out of. This is what makes
    # "which research produced this report" answerable later.
    refs: list[tuple[str, str]] = []
    seen_exec: set[str] = set()
    for o in m.objects:
        eid = o.get("executionId")
        if eid and eid not in seen_exec:
            seen_exec.add(eid)
            for a in artifacts_mod.list_for(workspace_id, execution_id=eid, limit=4):
                refs.append((a["id"], "derived_from"))

    artifact = artifacts_mod.create(
        workspace_id, style.artifact_type, heading, content,
        source_agent="nova", execution_id=execution_id,
        tags=["output", style.key], references=refs)

    node = _land_in_graph(workspace_id, style, artifact, m, execution_id)
    artifact["objectId"] = node["id"] if node else None
    artifact["provenance"] = m.provenance_floor
    return artifact


def _default_title(style: OutputStyle, m: Material) -> str:
    names = [o["name"] for o in m.objects[:3]]
    subject = ", ".join(names) if names else "this Space"
    if len(m.objects) > 3:
        subject += f" +{len(m.objects) - 3}"
    return f"{style.label}: {subject}"


def _land_in_graph(workspace_id: str, style: OutputStyle, artifact: dict,
                   m: Material, execution_id: str | None) -> dict | None:
    """Make the artifact an object, linked to what it was built from.

    §12's closing requirement: generated artifacts become objects inside the
    Space and can be passed back into agents. Without this step Create produces
    a dead end — a file you can read but never select.
    """
    try:
        node, _ = objects_mod.upsert_object(
            workspace_id, style.object_type, artifact["title"],
            external_id=f"artifact:{artifact['id']}",
            description=f"{style.label} generated from "
                        f"{len(m.objects)} object(s) in this Space.",
            properties={"artifactId": artifact["id"], "outputStyle": style.key,
                        "formats": list(style.formats),
                        "counts": m.counts},
            tags=["output", style.key],
            # Never stronger than its inputs — see the module docstring.
            provenance=m.provenance_floor,
            execution_id=execution_id)
    except Exception:
        return None

    for o in m.objects:
        try:
            objects_mod.link(workspace_id, node["id"], o["id"], "derived_from",
                             provenance=m.provenance_floor,
                             execution_id=execution_id)
        except Exception:
            continue

    try:
        objects_mod.add_event(
            workspace_id, f"Created {style.label.lower()}: {artifact['title']}",
            object_id=node["id"], type_key="artifact",
            body=f"Built from {len(m.objects)} object(s), "
                 f"{len(m.relationships)} relationship(s).",
            relevance="medium", provenance=m.provenance_floor,
            properties={"artifactId": artifact["id"], "outputStyle": style.key},
            execution_id=execution_id)
    except Exception:
        pass
    return node


def list_outputs(workspace_id: str, *, style: str | None = None,
                 limit: int = 100) -> list[dict]:
    """Artifacts produced by the Create verb, newest first."""
    out = []
    for a in artifacts_mod.list_for(workspace_id, limit=400):
        tags = a.get("tags") or []
        if "output" not in tags:
            continue
        if style and style not in tags:
            continue
        out.append(a)
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------------------
# Renderers — one per format, all styles
# ---------------------------------------------------------------------------
def render(artifact: dict, fmt: str) -> tuple[str, str, str]:
    """Return `(body, media_type, filename)` for a stored output artifact.

    `pdf` is the print-ready page, not a server-generated PDF file. The browser
    writes the actual PDF, which is why this returns `text/html`: OMNIX ships no
    PDF engine, and the two candidates both cost more than the feature is worth
    here — WeasyPrint needs a GTK stack that is genuinely painful to install on
    Windows, and ReportLab means hand-laying every table for output the print
    pipeline already paginates correctly. The file the user ends up with is a
    real PDF either way; this route just declines to reimplement pagination.
    """
    content = artifact.get("content") or {}
    fmt = (fmt or "md").lower()
    slug = _slug(content.get("title") or artifact.get("title") or "output")
    if fmt == "pdf":
        return (_render_html(artifact, content, auto_print=True),
                "text/html; charset=utf-8", f"{slug}.html")
    if fmt == "html":
        return _render_html(artifact, content), "text/html; charset=utf-8", f"{slug}.html"
    if fmt == "csv":
        return _render_csv(content), "text/csv; charset=utf-8", f"{slug}.csv"
    return _render_md(artifact, content), "text/markdown; charset=utf-8", f"{slug}.md"


def _slug(text: str) -> str:
    keep = [c.lower() if c.isalnum() else "-" for c in (text or "output")]
    s = "".join(keep)
    while "--" in s:
        s = s.replace("--", "-")
    return s.strip("-")[:60] or "output"


def _provenance_line(content: dict) -> str:
    p = content.get("provenance") or {}
    bits = [f"Trust floor: {p.get('floorLabel') or p.get('floor') or 'unknown'}"]
    if p.get("synthesised"):
        bits.append("contains one AI-written section (marked)")
    counts = content.get("counts") or {}
    if counts:
        bits.append(", ".join(f"{v} {k}" for k, v in counts.items() if v))
    return " · ".join(bits)


def _render_md(artifact: dict, content: dict) -> str:
    out = [f"# {content.get('title') or artifact.get('title') or 'Output'}", ""]
    out.append(f"*{content.get('styleLabel') or ''} — generated "
               f"{content.get('generatedAt') or ''}*")
    out.append("")
    out.append(f"> {_provenance_line(content)}")
    out.append("")
    for sec in content.get("sections") or []:
        out.append(f"## {sec.get('heading') or ''}")
        if sec.get("generated"):
            out.append("")
            out.append("*Written by a model from the material below. "
                       "Every other section is measured.*")
        out.append("")
        kind = sec.get("kind")
        if kind == "text":
            out.append(sec.get("body") or "")
        elif kind == "list":
            out.extend(f"- {i}" for i in sec.get("items") or [])
        elif kind == "table":
            cols = sec.get("columns") or []
            out.append("| " + " | ".join(str(c) for c in cols) + " |")
            out.append("|" + "|".join([" --- "] * len(cols)) + "|")
            for row in sec.get("rows") or []:
                cells = [str(c).replace("|", "\\|").replace("\n", " ") for c in row]
                out.append("| " + " | ".join(cells) + " |")
        elif kind == "metrics":
            for mt in sec.get("metrics") or []:
                out.append(f"- **{mt.get('label')}**: {mt.get('value')}")
        elif kind == "chart":
            for pt in sec.get("series") or []:
                out.append(f"- {pt.get('label')}: {pt.get('value')}")
        out.append("")
    return "\n".join(out).strip() + "\n"


def _render_csv(content: dict) -> str:
    """First table wins. CSV has one shape, and a file that concatenates three
    tables under one header row is not openable in a spreadsheet."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    for sec in content.get("sections") or []:
        if sec.get("kind") == "table":
            w.writerow(sec.get("columns") or [])
            for row in sec.get("rows") or []:
                w.writerow(row)
            return buf.getvalue()
        if sec.get("kind") == "chart":
            w.writerow(["Label", "Value"])
            for pt in sec.get("series") or []:
                w.writerow([pt.get("label"), pt.get("value")])
            return buf.getvalue()
        if sec.get("kind") == "metrics":
            w.writerow(["Metric", "Value"])
            for mt in sec.get("metrics") or []:
                w.writerow([mt.get("label"), mt.get("value")])
            return buf.getvalue()
    w.writerow(["Title"])
    w.writerow([content.get("title") or ""])
    return buf.getvalue()


_CSS = """
:root{--bg:#060606;--fg:#efe9dc;--dim:#8b8578;--gold:#d3ad55;--line:#241f18;
--card:#0d0b08}
@media(prefers-color-scheme:light){:root{--bg:#faf7f0;--fg:#1a1712;--dim:#6b6555;
--gold:#8a6d20;--line:#e3dcc9;--card:#fff}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.65 ui-sans-serif,-apple-system,"Segoe UI",Inter,sans-serif;
padding:48px 24px}
main{max-width:900px;margin:0 auto}
h1{font-size:30px;letter-spacing:-.02em;margin:0 0 6px}
h2{font-size:13px;text-transform:uppercase;letter-spacing:.16em;color:var(--gold);
margin:40px 0 12px;font-weight:600}
.meta{color:var(--dim);font-size:13px;margin-bottom:28px}
.prov{border:1px solid var(--line);background:var(--card);border-radius:8px;
padding:10px 14px;font-size:12.5px;color:var(--dim);margin-bottom:8px}
.gen{font-size:12px;color:var(--gold);margin:-4px 0 10px}
table{width:100%;border-collapse:collapse;font-size:13.5px;display:block;
overflow-x:auto;white-space:nowrap}
th{text-align:left;color:var(--dim);font-weight:500;font-size:11px;
text-transform:uppercase;letter-spacing:.1em;padding:8px 12px 8px 0;
border-bottom:1px solid var(--line)}
td{padding:9px 12px 9px 0;border-bottom:1px solid var(--line);vertical-align:top}
ul{padding-left:18px;margin:0}li{margin:4px 0}
.tiles{display:flex;flex-wrap:wrap;gap:12px}
.tile{border:1px solid var(--line);background:var(--card);border-radius:10px;
padding:14px 18px;min-width:120px}
.tile .v{font-size:26px;font-weight:600;color:var(--gold)}
.tile .l{font-size:11px;text-transform:uppercase;letter-spacing:.1em;
color:var(--dim);margin-top:2px}
.bar{display:grid;grid-template-columns:160px 1fr 48px;gap:10px;align-items:center;
margin:6px 0;font-size:13px}
.bar .t{height:8px;background:var(--gold);border-radius:4px;min-width:2px}
.bar .n{text-align:right;color:var(--dim);font-variant-numeric:tabular-nums}
p{white-space:pre-wrap;margin:0 0 12px}

/* ---- print / save-as-PDF -------------------------------------------------
   The screen rules actively break on paper: tables are `display:block` with
   `nowrap` and a horizontal scrollbar, which prints as one clipped line per
   row and silently loses the right-hand columns — including the URL column of
   the citation appendix, which is the one thing a reader needs to check. So
   print gets real table layout, wrapping cells, and a header row repeated on
   every page. Colours are forced light because a dark background prints as a
   solid ink block or, more often, as nothing at all. */
@media print{
:root{--bg:#fff;--fg:#111;--dim:#555;--gold:#6b5310;--line:#ccc;--card:#fff}
@page{size:A4;margin:16mm 14mm}
body{padding:0;font-size:10.5pt}
main{max-width:none}
h1{font-size:20pt}
h2{font-size:9pt;margin:16pt 0 6pt;break-after:avoid}
table{display:table;white-space:normal;overflow:visible;font-size:8.5pt;
table-layout:auto}
thead{display:table-header-group}
tr{break-inside:avoid}
td,th{padding:4pt 6pt 4pt 0}
/* Long URLs must break rather than force the table wider than the page. */
td:last-child{word-break:break-all}
.tile{border:1px solid var(--line)}
.prov{border:1px solid var(--line)}
a{color:inherit;text-decoration:none}
}
"""


def _render_html(artifact: dict, content: dict, *, auto_print: bool = False) -> str:
    parts = [
        "<!doctype html><html><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width,initial-scale=1'>",
        f"<title>{escape(content.get('title') or 'OMNIX output')}</title>",
        f"<style>{_CSS}</style></head><body><main>",
        f"<h1>{escape(content.get('title') or '')}</h1>",
        f"<div class='meta'>{escape(content.get('styleLabel') or '')} · "
        f"{escape(content.get('generatedAt') or '')} · OMNIX</div>",
        f"<div class='prov'>{escape(_provenance_line(content))}</div>",
    ]
    for sec in content.get("sections") or []:
        parts.append(f"<h2>{escape(str(sec.get('heading') or ''))}</h2>")
        if sec.get("generated"):
            parts.append("<div class='gen'>Written by a model from the material "
                         "in this document. Every other section is measured.</div>")
        kind = sec.get("kind")
        if kind == "text":
            for para in (sec.get("body") or "").split("\n\n"):
                if para.strip():
                    parts.append(f"<p>{escape(para.strip())}</p>")
        elif kind == "list":
            parts.append("<ul>" + "".join(
                f"<li>{escape(str(i))}</li>" for i in sec.get("items") or []) + "</ul>")
        elif kind == "table":
            head = "".join(f"<th>{escape(str(c))}</th>" for c in sec.get("columns") or [])
            body = "".join(
                "<tr>" + "".join(f"<td>{escape(str(c))}</td>" for c in row) + "</tr>"
                for row in sec.get("rows") or [])
            parts.append(f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>")
        elif kind == "metrics":
            tiles = "".join(
                f"<div class='tile'><div class='v'>{escape(str(mt.get('value')))}</div>"
                f"<div class='l'>{escape(str(mt.get('label')))}</div></div>"
                for mt in sec.get("metrics") or [])
            parts.append(f"<div class='tiles'>{tiles}</div>")
        elif kind == "chart":
            series = sec.get("series") or []
            top = max([float(p.get("value") or 0) for p in series] or [1]) or 1
            for pt in series:
                val = float(pt.get("value") or 0)
                pct = max(1.0, val / top * 100.0)
                parts.append(
                    f"<div class='bar'><span>{escape(str(pt.get('label')))}</span>"
                    f"<span class='t' style='width:{pct:.1f}%'></span>"
                    f"<span class='n'>{escape(str(pt.get('value')))}</span></div>")
    parts.append("</main>")
    if auto_print:
        # `onafterprint` is not used to close the tab: the user may cancel the
        # dialog and want to read the page, and a document that vanishes when
        # you decline to print it is hostile. Fired on load rather than inline
        # so webfonts and layout have settled before the dialog measures pages.
        parts.append(
            "<script>window.addEventListener('load',function(){"
            "setTimeout(function(){window.print()},250)})</script>")
    parts.append("</body></html>")
    return "".join(parts)
