"""ORACLE — Operational Research, Analysis & Contextual Learning Engine.

Evidence-first deep research. A squad of specialist subagents plans search
angles, retrieves and reads live sources, extracts claims WITH citations, then —
the part that matters — checks those citations against what the sources actually
say before anything reaches the reader.

Pipeline (each stage emits progress so the console can animate the roster):

    plan      Planner       question -> diverse search angles, multi-domain
    retrieve  Retriever     parallel search + page reads, deduped
    appraise  Appraiser     deterministic source credibility + near-duplicates
    extract   Extractor     sources -> structured claims with [n] citations
    verify    Fact-Checker  every claim re-checked against its cited source
    gaps      Planner       what is still unanswered -> a second targeted round
    conflict  Statistician  numeric disagreements across sources
    skeptic   Skeptic       red-teams the surviving evidence
    write     Synthesizer   cited briefing from VERIFIED claims only
    audit     Judge         every [n] in the prose re-checked; overall confidence

Design principle inherited from AVALON: the deterministic layer is the backbone.
Source scoring, deduplication, claim/citation verification, numeric conflict
detection and the final citation audit are all computed, not asked. If every
model is unavailable, ORACLE still returns scored, deduplicated, conflict-checked
sources instead of nothing.
"""

from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor

from .base import (Subagent, Unit, UnitResult, bullets, cards_block, clamp,
                   list_block, markdown_block, stats_block)
from .oracle_evidence import (Claim, audit_citations, build_sources,
                              consolidate_claims, is_junk_claim, is_meta_claim,
                              mark_duplicates, numeric_conflicts,
                              overall_confidence, verify_claims)
from .oracle_models import ORACLE_LADDERS, research_json, research_llm

# Domain-targeted query shaping. The blueprint asks for parallel search across
# academic / news / government / code; DuckDuckGo has no vertical API, but site
# and filetype operators reach the same corpora without another API key.
_DOMAIN_HINTS = {
    "academic": "(site:arxiv.org OR site:pubmed.ncbi.nlm.nih.gov OR site:nature.com)",
    "official": "(site:.gov OR site:.edu OR site:who.int OR site:europa.eu)",
    "news": "(site:reuters.com OR site:apnews.com OR site:bbc.com)",
    "code": "(site:github.com OR site:stackoverflow.com)",
}

_TIME_SENSITIVE = re.compile(
    r"\b(latest|current|today|now|2026|2025|recent|newest|state of|"
    r"this year|right now|up to date|trend)\b", re.I)


class Oracle(Unit):
    code = "oracle"
    name = "ORACLE"
    glyph = "◉"
    tagline = "Operational Research, Analysis & Contextual Learning Engine"
    blurb = "Evidence-first research → a cited briefing every claim is checked against."
    accent = "#57d7ff"
    input_label = "Research question"
    input_kind = "textarea"
    placeholder = "e.g. What are the tradeoffs of RAG vs fine-tuning in 2026?"

    def __init__(self):
        # Declared for the console roster. Execution routes through
        # oracle_models.research_llm so each role gets its own measured ladder.
        self.subagents = [
            Subagent("Planner", "decomposes the question into search angles",
                     model=ORACLE_LADDERS["planner"][0], system=""),
            Subagent("Retriever", "searches multiple domains and reads pages",
                     model=ORACLE_LADDERS["triage"][0], system=""),
            Subagent("Appraiser", "scores source credibility, flags duplicates",
                     model="deterministic", system=""),
            Subagent("Extractor", "pulls structured claims with citations",
                     model=ORACLE_LADDERS["extractor"][0], system=""),
            Subagent("Fact-Checker", "verifies each claim against its source",
                     model=ORACLE_LADDERS["verifier"][0], system=""),
            Subagent("Statistician", "cross-checks numbers between sources",
                     model="deterministic", system=""),
            Subagent("Skeptic", "red-teams the surviving evidence",
                     model=ORACLE_LADDERS["verifier"][0], system=""),
            Subagent("Synthesizer", "writes the cited briefing",
                     model=ORACLE_LADDERS["writer"][0], system=""),
            Subagent("Judge", "audits citations and scores confidence",
                     model="deterministic", system=""),
        ]

    # depth -> (angles, sources, read_top, gap_round, skeptic)
    DEPTHS = {
        "quick":    (2, 5, 2, False, False),
        "standard": (3, 8, 3, False, True),
        "deep":     (5, 14, 4, True, True),
    }

    # ---------------------------------------------------------------- helpers
    def _plan(self, question: str, n: int) -> list[str]:
        sys = ("You are ORACLE's research Planner. Given a question, produce "
               f"{n} focused web-search queries that together cover it well. "
               "Vary the angle: definitions, evidence/data, counter-arguments, "
               "recent developments, practical tradeoffs. Avoid near-duplicates. "
               "Output ONLY the queries, one per line — no numbering, no preamble.")
        out = research_llm("planner", sys, f"Question: {question}", temperature=0.3)
        qs = [q for q in bullets(out, limit=n * 2) if len(q) > 6][:n]
        return qs or [question]

    def _shape(self, queries: list[str], question: str) -> list[tuple[str, str]]:
        """Attach a domain slant to some queries so one job reaches several
        corpora instead of hammering general web search N times."""
        shaped: list[tuple[str, str]] = [(q, "web") for q in queries]
        technical = re.search(r"\b(algorithms?|models?|protocols?|apis?|librar(y|ies)|"
                              r"frameworks?|code|benchmarks?|software|databases?)\b",
                              question, re.I)
        # Plurals matter: the singular-only pattern missed "drugs", "studies",
        # "materials", so a pharmacology question never reached PubMed/arXiv and
        # the run silently stayed on general web search.
        scientific = re.search(r"\b(stud(y|ies)|trials?|efficacy|clinical|physics|"
                               r"biolog\w*|chemi\w*|climate|energy|materials?|"
                               r"medicines?|drugs?|vaccines?|therap\w*|patients?)\b",
                               question, re.I)
        if scientific:
            shaped.append((f"{queries[0]} {_DOMAIN_HINTS['academic']}", "academic"))
        if technical:
            shaped.append((f"{queries[0]} {_DOMAIN_HINTS['code']}", "code"))
        if _TIME_SENSITIVE.search(question):
            shaped.append((f"{queries[0]} {_DOMAIN_HINTS['news']}", "news"))
        return shaped

    def _search_all(self, shaped, per_query, read_top):
        """Parallel retrieval. Search is IO-bound and the slowest stage, so the
        angles run concurrently rather than one after another."""
        from ..tools.websearch import search_deep

        def one(item):
            q, kind = item
            try:
                return kind, search_deep(q, max_results=per_query, fetch_top=read_top)
            except Exception:
                return kind, []

        if not shaped:
            return []
        with ThreadPoolExecutor(max_workers=min(5, len(shaped))) as pool:
            return list(pool.map(one, shaped))

    # Sources per extraction call. One giant call is a single point of failure:
    # on a 14-source deep run the model's reply ran past its token budget, the
    # truncated JSON failed to parse, and the whole stage returned ZERO claims
    # after a minute of retrieval. Small batches keep each reply parseable, run
    # in parallel, and let one bad batch cost a few claims instead of all of them.
    _EXTRACT_BATCH = 4

    def _extract_claims(self, question, sources) -> list[Claim]:
        """Sources -> structured claims with citation indices, batched."""
        readable = [s for s in sources if not s.duplicate_of]
        if not readable:
            return []
        batches = [readable[i:i + self._EXTRACT_BATCH]
                   for i in range(0, len(readable), self._EXTRACT_BATCH)]
        claims: list[Claim] = []
        with ThreadPoolExecutor(max_workers=min(4, len(batches))) as pool:
            for got in pool.map(
                    lambda b: self._extract_batch(question, b, sources), batches):
                claims.extend(got)
        return claims[:60]

    def _extract_batch(self, question, batch, all_sources) -> list[Claim]:
        blocks = [f"[{s.n}] {s.title}\n{clamp(s.content or s.snippet, 1400)}"
                  for s in batch]
        sources = all_sources
        if not blocks:
            return []
        sys = ("You are ORACLE's Extractor. From the numbered sources, extract "
               "the specific factual claims that bear on the question.\n"
               "Rules:\n"
               "(1) A claim is an ASSERTION about the world that could be true "
               "or false — 'RAG retrieves documents at inference time'. It is "
               "NOT a description of the page ('this guide explains when to "
               "fine-tune'). Never describe the source; state what it says.\n"
               "(2) Every claim MUST cite the source numbers it came from. If "
               "SEVERAL sources state the same thing, emit ONE claim citing all "
               "of them — that agreement is the most valuable signal here.\n"
               "(3) Copy figures exactly as stated — never round or infer.\n"
               "(4) If sources genuinely DISAGREE, emit both versions as "
               "separate claims so the conflict stays visible.\n"
               "(5) Keep each claim to one sentence, self-contained.\n"
               "(6) NEVER import a premise. Do not open a claim with a cause, "
               "event or condition unless THAT source states it happened — no "
               "'The X blockade has resulted in...' unless the source says a "
               "blockade occurred. State only what is on the page; the "
               "question is not evidence for its own assumptions.\n"
               "(7) Skip any figure shown as a placeholder — '--', 'N/A', "
               "'TBD', 'Loading', blank. Those are unrendered widgets on a "
               "live dashboard, not values. A claim needs a real quantity.\n"
               'Schema: {"claims":[{"text":"...","sources":[1,2]}]}')
        data = research_json(
            "extractor", sys,
            f"QUESTION: {question}\n\nSOURCES:\n" + "\n\n".join(blocks)[:9000],
            default={}, max_tokens=1600) or {}
        claims: list[Claim] = []
        valid = {s.n for s in sources}
        for c in (data.get("claims") or [])[:40]:
            if not isinstance(c, dict):
                continue
            text = str(c.get("text") or "").strip()
            if len(text) < 12 or is_meta_claim(text):
                continue   # commentary about the sources is not a finding
            if is_junk_claim(text):
                continue   # a placeholder where the value should be asserts nothing
            raw = c.get("sources") or []
            if isinstance(raw, (int, str)):
                raw = [raw]
            nums = []
            for x in raw:
                try:
                    n = int(x)
                except (TypeError, ValueError):
                    continue
                if n in valid and n not in nums:
                    nums.append(n)
            claims.append(Claim(text=clamp(text, 320), sources=nums))
        return claims

    def _find_gaps(self, question, claims) -> list[str]:
        sys = ("You are ORACLE's Planner reviewing partial research. Given the "
               "question and the claims gathered so far, name what is still "
               "MISSING to answer it well. Output ONLY 1-3 web-search queries "
               "that would close those gaps, one per line. If nothing important "
               "is missing, output the single word NONE.")
        found = "\n".join(f"- {c.text}" for c in claims[:20]) or "(nothing yet)"
        out = research_llm("planner", sys,
                           f"QUESTION: {question}\n\nCLAIMS SO FAR:\n{found}",
                           temperature=0.3)
        if not out or "NONE" in out.upper()[:40]:
            return []
        return [q for q in bullets(out, limit=3) if len(q) > 6][:3]

    def _skeptic(self, question, claims) -> str:
        sys = ("You are ORACLE's Skeptic. Red-team the evidence below. Name only "
               "concrete weaknesses: unsupported leaps, over-generalisation from "
               "one source, stale data, selection bias, conflicts of interest, "
               "and what a careful reader should NOT conclude. Be specific and "
               "brief — 3-5 bullets. Do not invent facts.")
        ev = "\n".join(
            f"- [{c.verdict}, conf {c.confidence}] {c.text} (sources {c.sources})"
            for c in claims[:25])
        return research_llm("verifier", sys,
                            f"QUESTION: {question}\n\nEVIDENCE:\n{ev}",
                            temperature=0.3, max_tokens=700)

    def _synthesize(self, question, claims, conflicts, skeptic) -> str:
        ev = "\n".join(
            f"- {c.text}  [cite: {', '.join('[' + str(n) + ']' for n in c.supported_by)}]"
            f"  (confidence {c.confidence}, {c.independent} independent source(s))"
            for c in claims[:30])
        conflict_txt = ""
        if conflicts:
            conflict_txt = ("\n\nUNRESOLVED NUMERIC DISAGREEMENTS (say so explicitly):\n"
                            + "\n".join(f"- {c['unit']}: sources range {c['low']:g}–{c['high']:g} "
                                        f"({c['spread_pct']}% spread)"
                                        for c in conflicts[:4]))
        sys = ("You are ORACLE's Synthesizer. Write a decision-useful briefing "
               "that answers the question using ONLY the verified evidence given. "
               "Rules: (1) cite inline as [n], using ONLY the citation numbers "
               "attached to each claim; (2) never assert anything not present in "
               "the evidence; (3) where confidence is low or sources disagree, "
               "SAY SO in the sentence rather than smoothing it over; (4) open "
               "with a direct 2-3 sentence answer, then short sections with "
               "bullets. Truth over fluency — an honest 'the evidence is thin on "
               "X' is worth more than a confident guess.\n"
               # Every sentence is re-checked against the sources it names, and
               # each one that does not contain it is printed as a flagged
               # citation. Piling six numbers onto a sentence therefore does not
               # make it better supported — it produces five failed checks and
               # drags the confidence score down, which is exactly how a sound
               # briefing came to be reported at 37/100 with seven flags.
               "CITE NARROWLY. Attach the ONE citation whose claim that "
               "sentence is drawn from. Only cite several when the sentence "
               "genuinely combines several claims, and never restate one claim "
               "with every number you have seen — each citation you add is "
               "checked against that source, and a number the source does not "
               "support is reported as a citation failure.\n"
               "Use `##` headings with one leading emoji for each section, "
               "bold the key figures, and keep bullets short.")
        out = research_llm(
            "writer", sys,
            f"QUESTION: {question}\n\nVERIFIED EVIDENCE:\n"
            f"{ev or '(none survived verification)'}{conflict_txt}\n\n"
            f"SKEPTIC'S NOTES:\n{clamp(skeptic, 900) or '(none)'}\n\n"
            "Write the briefing now.",
            temperature=0.4, max_tokens=2600)
        if out:
            return out
        # A deep run can gather 30+ claims and the writer occasionally returns
        # nothing on the resulting prompt — which threw away a minute of good
        # retrieval and printed "AI synthesis was unavailable" under a full
        # evidence ledger. Retry once, leaner, before conceding.
        lean = "\n".join(
            f"- {c.text} [{', '.join('[' + str(n) + ']' for n in c.supported_by)}]"
            for c in sorted(claims, key=lambda x: -x.confidence)[:12])
        return research_llm(
            "writer", sys,
            f"QUESTION: {question}\n\nVERIFIED EVIDENCE:\n{lean}\n\n"
            "Write the briefing now.",
            temperature=0.4, max_tokens=1800)

    # -------------------------------------------------------------------- run
    def run(self, ctx, emit) -> UnitResult:
        question = (ctx.get("input") or "").strip()
        res = UnitResult()
        if not question:
            res.summary = "No question provided."
            return res

        started = time.time()
        depth = str(ctx.get("depth") or "deep").lower()
        n_angles, n_sources, read_top, do_gaps, do_skeptic = self.DEPTHS.get(
            depth, self.DEPTHS["deep"])

        # 1) PLAN -----------------------------------------------------------
        emit("plan", f"Planner drafting {n_angles} search angles [{depth}]")
        queries = self._plan(question, n_angles)
        shaped = self._shape(queries, question)
        emit("plan", f"{len(shaped)} angles across "
                     f"{len({k for _, k in shaped})} corpora")

        # 2) RETRIEVE -------------------------------------------------------
        emit("retrieve", f"Retriever searching {len(shaped)} angles in parallel")
        try:
            batches = self._search_all(shaped, per_query=4, read_top=read_top)
        except Exception as e:
            res.summary = (f"Web search tooling is unavailable ({type(e).__name__}); "
                           "ORACLE cannot research right now.")
            return res

        raw, seen = [], set()
        for kind, hits in batches:
            for r in hits:
                url = (r.get("url") or "").split("#")[0]
                if not url or url in seen:
                    continue
                seen.add(url)
                r["kind"] = kind
                raw.append(r)
        raw = raw[:n_sources]
        if not raw:
            res.summary = ("No web sources could be retrieved (search or network "
                           "unavailable). Try again when online.")
            return res
        emit("retrieve", f"{len(raw)} unique sources retrieved")

        # 3) APPRAISE (deterministic) ---------------------------------------
        emit("appraise", "Appraiser scoring credibility and de-duplicating")
        sources = build_sources(raw)
        dupes = mark_duplicates(sources)
        independent = len([s for s in sources if not s.duplicate_of])
        emit("appraise", f"{independent} independent sources"
                         + (f", {dupes} near-duplicates flagged" if dupes else ""))

        # 4) EXTRACT --------------------------------------------------------
        emit("extract", "Extractor pulling claims with citations")
        claims = self._extract_claims(question, sources)
        raw_n = len(claims)
        claims = consolidate_claims(claims)
        emit("extract", f"{len(claims)} claims"
             + (f" (merged from {raw_n} — agreeing sources combined)"
                if raw_n > len(claims) else ""))

        # 5) VERIFY (deterministic) -----------------------------------------
        emit("verify", f"Fact-Checker verifying {len(claims)} claims against sources")
        verify_claims(claims, sources)
        kept = [c for c in claims if c.verdict != "unsupported"]
        dropped = len(claims) - len(kept)
        emit("verify", f"{len(kept)} claims corroborated"
                       + (f", {dropped} rejected as unsupported" if dropped else ""))

        # 6) GAPS -> second retrieval round ---------------------------------
        if do_gaps and kept:
            emit("gaps", "Planner checking for unanswered angles")
            gap_qs = self._find_gaps(question, kept)
            if gap_qs:
                emit("gaps", f"{len(gap_qs)} gap(s) found — running a second round")
                extra = self._search_all([(q, "web") for q in gap_qs],
                                         per_query=3, read_top=2)
                new_raw = []
                for _, hits in extra:
                    for r in hits:
                        url = (r.get("url") or "").split("#")[0]
                        if url and url not in seen:
                            seen.add(url)
                            new_raw.append(r)
                if new_raw:
                    offset = len(sources)
                    more = build_sources(new_raw[:6])
                    for s in more:
                        s.n += offset
                    sources.extend(more)
                    mark_duplicates(sources)
                    more_claims = self._extract_claims(question, sources)
                    known = {c.text.lower()[:80] for c in claims}
                    added = [c for c in more_claims
                             if c.text.lower()[:80] not in known]
                    claims.extend(added)
                    claims = consolidate_claims(claims)
                    verify_claims(claims, sources)
                    kept = [c for c in claims if c.verdict != "unsupported"]
                    emit("gaps", f"second round added {len(added)} claims "
                                 f"from {len(more)} sources")
                else:
                    emit("gaps", "second round found no new sources")
            else:
                emit("gaps", "no material gaps found")

        # 7) NUMERIC CONFLICTS (deterministic) ------------------------------
        emit("conflict", "Statistician cross-checking figures")
        conflicts = numeric_conflicts(kept)
        emit("conflict", (f"{len(conflicts)} numeric disagreement(s)" if conflicts
                          else "figures are consistent across sources"))

        # 8) SKEPTIC --------------------------------------------------------
        skeptic = ""
        if do_skeptic and kept:
            emit("skeptic", "Skeptic red-teaming the evidence")
            skeptic = self._skeptic(question, kept)

        # 9) SYNTHESIZE -----------------------------------------------------
        emit("write", "Synthesizer writing the cited briefing")
        answer = self._synthesize(question, kept, conflicts, skeptic)

        # 10) CITATION AUDIT (deterministic) --------------------------------
        emit("audit", "Judge auditing every citation in the briefing")
        answer, cite_problems = audit_citations(answer, sources)
        conf = overall_confidence(kept, sources)
        emit("audit", f"confidence {conf['score']}/100 ({conf['label']}), "
                      f"{len(cite_problems)} citation(s) flagged")

        # Deterministic fallback — the sources are the value even with no LLM.
        if not answer:
            answer = self._fallback(question, sources)

        res.summary = clamp(answer, 6000)
        res.add(list_block("Search plan", [q for q, _ in shaped]))
        res.add(stats_block([
            {"n": str(len(shaped)), "label": "Angles"},
            {"n": str(independent), "label": "Independent sources"},
            {"n": str(len(kept)), "label": "Claims verified"},
            {"n": str(conf["score"]), "label": f"Confidence ({conf['label']})"},
        ]))
        self._add_evidence_blocks(res, kept, claims, conflicts, cite_problems,
                                  skeptic, conf)
        res.add(cards_block("Sources", [
            {"title": f"[{s.n}] {clamp(s.title, 90)}",
             "badge": s.tier_label.split(" /")[0][:14],
             "badge_color": _tier_colour(s.tier),
             "body": (f"{clamp(s.snippet, 200)}\n\n"
                      f"credibility {s.credibility}/100 · {s.tier_label}"
                      + (f" · {s.year}" if s.year else "")
                      + (f" · {s.note}" if s.note else "")
                      + f"\n\n{s.url}")}
            for s in sources
        ]))
        res.meta = {
            "sources": len(sources),
            "independent_sources": independent,
            "confidence": conf,
            "claims": [
                {"text": c.text, "sources": c.sources,
                 "supported_by": c.supported_by, "unsupported": c.unsupported,
                 "verdict": c.verdict, "confidence": c.confidence,
                 "independent": c.independent, "note": c.note}
                for c in claims
            ],
            "source_scores": [
                {"n": s.n, "url": s.url, "host": s.host, "title": s.title,
                 "tier": s.tier, "tier_label": s.tier_label,
                 "credibility": s.credibility, "year": s.year,
                 "snippet": s.snippet, "duplicate_of": s.duplicate_of}
                for s in sources
            ],
            "numeric_conflicts": conflicts,
            "citation_problems": cite_problems,
            "models": {r: ORACLE_LADDERS[r][0] for r in ORACLE_LADDERS},
            "depth": depth,
            "elapsed_s": round(time.time() - started, 1),
        }
        return res

    # ------------------------------------------------------------- rendering
    def _add_evidence_blocks(self, res, kept, claims, conflicts, cite_problems,
                             skeptic, conf) -> None:
        verified = [c for c in kept if c.verdict == "verified"]
        weak = [c for c in kept if c.verdict == "weak"]
        # `single_source` is a FOURTH verdict (see oracle_evidence.Claim), and
        # counting only the other three is how the ledger came to announce
        # "0 verified, 0 weakly supported" directly above a list of eight
        # claims. It is also the commonest verdict in practice — most facts on
        # the open web are stated by one page and repeated by others — so
        # leaving it out of the tally silently zeroed the whole summary.
        single = [c for c in kept if c.verdict == "single_source"]
        rejected = [c for c in claims if c.verdict == "unsupported"]

        if kept:
            lines = []
            for c in sorted(kept, key=lambda x: -x.confidence)[:24]:
                cites = ", ".join(f"[{n}]" for n in c.supported_by) or "—"
                tag = "✓" if c.verdict == "verified" else "~"
                lines.append(f"{tag} **{c.confidence}/100** {c.text} — {cites}"
                             + (f"  _({c.note})_" if c.note else ""))
            counts = [f"{len(verified)} corroborated by two or more sources"]
            if single:
                counts.append(f"{len(single)} from a single source")
            if weak:
                counts.append(f"{len(weak)} weakly supported")
            res.add(markdown_block(
                "Evidence ledger",
                f"_{', '.join(counts)}. "
                "✓ = two or more independent sources state this; ~ = only one "
                "does, or the source is related rather than explicit._\n\n"
                + "\n\n".join(lines)))

        if rejected:
            res.add(markdown_block(
                "Rejected claims",
                f"_{len(rejected)} extracted claim(s) were dropped because the "
                "source they cited does not contain them. They are listed so the "
                "filtering is visible rather than silent._\n\n"
                + "\n\n".join(f"✗ {c.text} — cited {c.sources or '—'} "
                              f"_({c.note})_" for c in rejected[:12])))

        if conflicts:
            lines = []
            for c in conflicts:
                vals = "; ".join(
                    f"{d['value']:g} from {', '.join('[' + str(s) + ']' for s in d['sources']) or '—'}"
                    for d in c["claims"][:4])
                lines.append(f"- **{c['unit']}** — sources span {c['low']:g} to "
                             f"{c['high']:g} ({c['spread_pct']}% spread): {vals}")
            res.add(markdown_block(
                "Numeric disagreements",
                "_The same quantity reported differently across sources. "
                "Resolve these before relying on any single figure._\n\n"
                + "\n".join(lines)))

        if cite_problems:
            res.add(markdown_block(
                "Citation audit",
                f"_{len(cite_problems)} sentence(s) in the briefing carry a "
                "citation the cited source does not support; each is marked ⚠ "
                "in the briefing above._\n\n"
                + "\n\n".join(
                    f"⚠ “{p['sentence']}”\n"
                    + "\n".join(f"  · [{c['n']}] — {c['reason']}"
                                for c in p["citations"])
                    for p in cite_problems[:8])))

        if skeptic:
            res.add(markdown_block("Skeptic's notes", clamp(skeptic, 2200)))

        res.add(markdown_block(
            "How to read this",
            f"Overall confidence **{conf['score']}/100 ({conf['label']})** from "
            f"{conf['independent_sources']} independent source(s): "
            f"{conf['claims']} claim(s) — {conf['verified']} corroborated, "
            f"{conf.get('single_source', 0)} single-source, "
            f"{conf['weak']} weak, {conf['unsupported']} rejected.\n\n"
            "Confidence is computed, not asserted — it combines how many "
            "*independent* sources support each claim (near-duplicates are "
            "collapsed so syndicated copies cannot fake consensus), how credible "
            "those sources are, and whether the cited text actually contains the "
            "claim. A single-source answer is capped at 45."))

    def _fallback(self, question, sources) -> str:
        digest = "\n".join(
            f"- **[{s.n}] {clamp(s.title, 100)}** ({s.credibility}/100, "
            f"{s.tier_label}) — {clamp(s.snippet, 180)} {s.url}"
            for s in sources if not s.duplicate_of)
        return (f"_AI synthesis was unavailable, so here are the scored, "
                f"de-duplicated sources gathered for “{clamp(question, 120)}”:_"
                f"\n\n{digest}")


def _tier_colour(tier: str) -> str:
    return {
        "primary": "#3fd68c", "official": "#3fd68c", "standards": "#3fd68c",
        "news": "#57d7ff", "docs": "#57d7ff",
        "general": "#8a92a6", "community": "#f5a623", "aggregator": "#f5a623",
    }.get(tier, "#8a92a6")
