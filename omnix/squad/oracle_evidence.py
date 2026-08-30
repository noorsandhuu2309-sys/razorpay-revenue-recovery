"""ORACLE's evidence engine — the part that decides what is allowed to be said.

A research agent's failure mode is not being unhelpful, it is being fluent and
wrong: a confident briefing with [3] after a sentence source 3 never supported.
That is indistinguishable from a correct answer until someone opens the link,
which is exactly when trust is lost.

So citations here are checked, not decorated. Everything in this module is
deterministic — lexical and numeric overlap, domain classification, date
extraction, near-duplicate detection. No model is asked whether it was right.
The LLM proposes; this module disposes.

What it computes:

    credibility(source)   domain class, TLD, recency, depth of retrieved text
    dedupe(sources)       near-duplicate detection, so three syndicated copies
                          of one wire story do not read as three confirmations
    verify(claim)         does the cited source actually contain support?
    numeric_conflicts()   same quantity, different numbers, across sources
    confidence(claim)     independent corroboration x credibility x verification
    audit_citations()     strip/flag [n] markers the evidence does not carry
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# Source credibility. Deterministic, transparent, and deliberately conservative:
# a high score never asserts truth, only that the source is the kind a reviewer
# would accept without argument.
# ---------------------------------------------------------------------------
_PRIMARY_HOSTS = re.compile(
    r"(^|\.)(nature|science|sciencedirect|springer|wiley|arxiv|biorxiv|medrxiv|"
    r"pubmed|ncbi\.nlm\.nih|acm|ieee|jstor|ssrn|plos|cell|thelancet|bmj|nejm)\.",
    re.I)
_GOV_EDU = re.compile(r"\.(gov|gov\.[a-z]{2}|edu|edu\.[a-z]{2}|int|mil)($|/|:)", re.I)
_STANDARDS = re.compile(
    r"(^|\.)(w3|ietf|iso|nist|iec|itu|rfc-editor|whatwg|unicode|oasis-open)\.", re.I)
_MAJOR_NEWS = re.compile(
    r"(^|\.)(reuters|apnews|bbc|ft|economist|wsj|nytimes|washingtonpost|"
    r"bloomberg|npr|guardian|theatlantic|nikkei|dw)\.", re.I)
_VENDOR_DOCS = re.compile(
    r"(^|\.)(docs?|developer|developers|learn|support)\.", re.I)
_LOW_TRUST = re.compile(
    r"(^|\.)(medium|substack|blogspot|wordpress|quora|reddit|pinterest|"
    r"answers|ezinearticles|hubpages|scribd|slideshare|coursehero)\.", re.I)
_AGGREGATOR = re.compile(
    r"(^|\.)(news\.google|bing|yahoo|msn|flipboard|feedly|techmeme)\.", re.I)

# Tier -> (label, base score out of 100)
_TIERS = {
    "primary":   ("Peer-reviewed / preprint", 92),
    "official":  ("Government / academic", 88),
    "standards": ("Standards body", 90),
    "news":      ("Established news", 74),
    "docs":      ("Vendor documentation", 72),
    "community": ("Community / blog", 45),
    "aggregator": ("Aggregator", 38),
    "general":   ("General web", 58),
}

_DATE_PATTERNS = [
    re.compile(r"\b(20[0-2]\d|19[89]\d)-(\d{2})-(\d{2})\b"),
    re.compile(r"\b(\d{1,2})\s+(January|February|March|April|May|June|July|"
               r"August|September|October|November|December)\s+(20[0-2]\d)\b", re.I),
    re.compile(r"\b(January|February|March|April|May|June|July|August|"
               r"September|October|November|December)\s+\d{1,2},?\s+(20[0-2]\d)\b", re.I),
    re.compile(r"\b(20[0-2]\d)\b"),
]


def classify_source(url: str) -> tuple[str, str]:
    """(tier_key, human label) for a URL, from its host alone."""
    host = (urlparse(url or "").netloc or "").lower()
    if not host:
        return "general", _TIERS["general"][0]
    for rx, key in ((_PRIMARY_HOSTS, "primary"), (_STANDARDS, "standards"),
                    (_MAJOR_NEWS, "news"), (_AGGREGATOR, "aggregator"),
                    (_LOW_TRUST, "community")):
        if rx.search(host):
            return key, _TIERS[key][0]
    if _GOV_EDU.search(host):
        return "official", _TIERS["official"][0]
    if _VENDOR_DOCS.search(host):
        return "docs", _TIERS["docs"][0]
    return "general", _TIERS["general"][0]


def extract_year(text: str) -> int | None:
    """Most likely publication year mentioned in a page's text."""
    if not text:
        return None
    now = datetime.now(timezone.utc).year
    for rx in _DATE_PATTERNS:
        for m in rx.finditer(text[:4000]):
            for g in m.groups():
                if g and g.isdigit() and len(g) == 4:
                    y = int(g)
                    if 1990 <= y <= now:
                        return y
    return None


@dataclass
class Source:
    n: int
    title: str
    url: str
    snippet: str = ""
    content: str = ""
    tier: str = "general"
    tier_label: str = ""
    year: int | None = None
    credibility: int = 0
    duplicate_of: int | None = None
    note: str = ""

    @property
    def text(self) -> str:
        return f"{self.title}\n{self.snippet}\n{self.content}"

    @property
    def host(self) -> str:
        return (urlparse(self.url or "").netloc or "").lower()


def build_sources(raw: list[dict]) -> list[Source]:
    """Score and classify retrieved results."""
    now = datetime.now(timezone.utc).year
    out: list[Source] = []
    for i, r in enumerate(raw, 1):
        tier, label = classify_source(r.get("url", ""))
        content = r.get("content") or ""
        s = Source(n=i, title=(r.get("title") or "(untitled)").strip(),
                   url=r.get("url", ""), snippet=(r.get("snippet") or "").strip(),
                   content=content, tier=tier, tier_label=label)
        s.year = extract_year(content or s.snippet)
        score = _TIERS[tier][1]
        # Recency: research decays. Unknown dates are not punished as if old.
        if s.year:
            age = max(0, now - s.year)
            score -= min(22, age * 3)
        else:
            score -= 4
        # We only actually read some pages; a snippet is weaker evidence.
        if len(content) > 1200:
            score += 6
        elif not content:
            score -= 10
        if s.url.startswith("http://"):
            score -= 5
        s.credibility = max(5, min(99, score))
        out.append(s)
    return out


# ---------------------------------------------------------------------------
# Near-duplicate detection. Syndicated copies of one story must not be counted
# as independent corroboration — that is how an agent manufactures false
# consensus and then reports high confidence in it.
# ---------------------------------------------------------------------------
_WORD = re.compile(r"[a-z0-9]+")
_STOP = {
    "the", "a", "an", "and", "or", "of", "to", "in", "is", "are", "was", "were",
    "for", "on", "with", "that", "this", "it", "as", "at", "by", "be", "from",
    "has", "have", "had", "but", "not", "they", "their", "its", "we", "you",
}


def _tokens(text: str) -> list[str]:
    return [w for w in _WORD.findall((text or "").lower()) if w not in _STOP]


def _shingles(text: str, k: int = 5) -> set[str]:
    t = _tokens(text)
    if len(t) < k:
        return {" ".join(t)} if t else set()
    return {" ".join(t[i:i + k]) for i in range(len(t) - k + 1)}


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / float(len(a) + len(b) - inter)


def _dupe_text(s: "Source") -> str:
    """Body text only. Syndicated copies of one story keep the prose and change
    the headline, so including the title in the comparison is what stops the
    match — exactly the case this check exists to catch."""
    return (s.content or s.snippet or s.title)[:4000]


def mark_duplicates(sources: list[Source], threshold: float = 0.5) -> int:
    """Flag near-duplicate sources in place. Returns how many were flagged."""
    shingles = {s.n: _shingles(_dupe_text(s)) for s in sources}
    bodies = {s.n: " ".join(_tokens(_dupe_text(s))) for s in sources}
    flagged = 0
    for i, a in enumerate(sources):
        if a.duplicate_of:
            continue
        for b in sources[i + 1:]:
            if b.duplicate_of:
                continue
            sim = jaccard(shingles[a.n], shingles[b.n])
            # Short bodies produce few shingles, so Jaccard is noisy on them;
            # an identical normalised body is decisive regardless of length.
            ta, tb = bodies[a.n], bodies[b.n]
            identical = bool(ta) and (ta == tb or (len(ta) > 80 and (
                ta in tb or tb in ta)))
            if identical or sim >= threshold:
                b.duplicate_of = a.n
                b.note = (f"near-duplicate of [{a.n}]"
                          + (" (identical body text)" if identical
                             else f" ({int(sim * 100)}% overlap)"))
                flagged += 1
    return flagged


# ---------------------------------------------------------------------------
# Claim verification. A claim cites sources; we check the cited source text
# actually carries the claim's distinctive content.
# ---------------------------------------------------------------------------
_NUM = re.compile(r"(?<![\w.])(\d[\d,]*(?:\.\d+)?)\s*(%|percent|bn|billion|"
                  r"million|m\b|k\b|trillion|wh/kg|kw|mw|gw|gb|tb|ms|s\b|"
                  r"years?|months?|days?|x\b|°c|°f|usd|\$|€|£)?", re.I)


def numbers_in(text: str) -> list[tuple[float, str]]:
    """(value, unit) pairs, normalised enough to compare across sources."""
    out = []
    for m in _NUM.finditer(text or ""):
        raw = m.group(1).replace(",", "")
        try:
            v = float(raw)
        except ValueError:
            continue
        unit = (m.group(2) or "").lower().strip()
        unit = {"percent": "%", "bn": "billion", "m": "million"}.get(unit, unit)
        out.append((v, unit))
    return out


@dataclass
class Claim:
    text: str
    sources: list[int] = field(default_factory=list)
    supported_by: list[int] = field(default_factory=list)
    unsupported: list[int] = field(default_factory=list)
    independent: int = 0          # distinct non-duplicate sources supporting it
    confidence: int = 0           # 0-100
    # verified | single_source | weak | unsupported. `single_source` is easy to
    # forget — it is the commonest verdict in practice, and a renderer that
    # tallies only the other three reports a full run as having found nothing.
    verdict: str = "unverified"
    note: str = ""

    @property
    def has_numbers(self) -> bool:
        return bool(numbers_in(self.text))


def _support_score(claim: str, source: Source) -> float:
    """How much of the claim's distinctive content appears in the source.

    Content words plus, crucially, numbers: a claim asserting "500 Wh/kg" is
    NOT supported by a source that only says "high energy density", and lexical
    overlap alone would happily call that a match.
    """
    ctoks = set(_tokens(claim))
    if not ctoks:
        return 0.0
    stext = source.text.lower()
    stoks = set(_tokens(stext))
    lexical = len(ctoks & stoks) / float(len(ctoks))

    cnums = numbers_in(claim)
    if cnums:
        snums = {round(v, 4) for v, _ in numbers_in(stext)}
        hit = sum(1 for v, _ in cnums if round(v, 4) in snums)
        # For a quantitative claim the NUMBER is the claim. "Cells reached 900
        # Wh/kg" shares almost every word with a source saying 500 Wh/kg, so
        # lexical overlap alone scores it as supported — the one detail that
        # matters is the one that differs. If the source carries none of the
        # claim's figures, it does not support the claim, however similar the
        # prose.
        if hit == 0:
            return 0.0
        numeric = hit / float(len(cnums))
        return 0.45 * lexical + 0.55 * numeric
    return lexical


def verify_claims(claims: list[Claim], sources: list[Source],
                  strong: float = 0.55, weak: float = 0.32) -> None:
    """Check each claim against the sources it cites. Mutates in place."""
    by_n = {s.n: s for s in sources}
    for c in claims:
        c.supported_by, c.unsupported = [], []
        for n in c.sources:
            src = by_n.get(n)
            if src is None:
                c.unsupported.append(n)
                continue
            score = _support_score(c.text, src)
            (c.supported_by if score >= weak else c.unsupported).append(n)
        # Independent corroboration ignores near-duplicates: two copies of the
        # same article are one witness.
        roots = set()
        for n in c.supported_by:
            src = by_n.get(n)
            roots.add(src.duplicate_of or n if src else n)
        c.independent = len(roots)

        if not c.supported_by:
            c.verdict = "unsupported"
            c.note = ("no cited source contains this claim's key terms"
                      if c.sources else "no citation given")
        else:
            best = max(_support_score(c.text, by_n[n]) for n in c.supported_by)
            if best < strong:
                c.verdict = "weak"
                c.note = "cited source is topically related but does not state this directly"
            elif c.independent >= 2:
                c.verdict = "verified"
            else:
                # Claims are extracted FROM a source, so scoring one against
                # that same source is close to circular — it nearly always
                # passes, which is why a shipped run marked 15 of 15 claims
                # `verified` and told the reader nothing. Corroboration by a
                # second, non-duplicate source is the only part of this that
                # a reader could not have assumed. Reserve the badge for it.
                c.verdict = "single_source"
                c.note = ("only one source states this — no independent "
                          "corroboration found")
            # A claim can overlap its source almost perfectly and still assert
            # a cause the source never mentions; the invented frame is the one
            # part lexical scoring cannot see. Cap it at `weak` so the audit
            # trail stays honest — the figure may well be right, but the
            # causal story around it is the extractor's, not the source's.
            cited = [by_n[n] for n in c.supported_by if n in by_n]
            if imported_premise(c.text, cited):
                c.verdict = "weak"
                c.note = ("states a premise no cited source mentions — the "
                          "figure may hold but the causal framing is unsourced")
        c.confidence = _confidence(c, by_n)


def _confidence(c: Claim, by_n: dict[int, Source]) -> int:
    """Independent corroboration x source quality x verification strength."""
    if not c.supported_by:
        return 0
    cred = [by_n[n].credibility for n in c.supported_by if n in by_n] or [40]
    base = sum(cred) / float(len(cred))
    # Corroboration has diminishing returns; the second source matters most.
    corro = 1.0 + 0.28 * math.log(max(1, c.independent), 2)
    strength = {"verified": 1.0, "single_source": 0.88,
                "weak": 0.72, "unsupported": 0.0}.get(c.verdict, 0.72)
    return max(0, min(99, int(base * corro * strength)))


# Extractor output that talks ABOUT the sources instead of asserting anything.
# "The main risks are not explicitly stated in the sources" is a statement about
# the retrieval, not a finding, and it scored 99/100 with four citations because
# every source trivially "contains" it. A research report must not headline the
# absence of evidence as its best-corroborated claim.
META_CLAIM = re.compile(
    r"\b(not (explicitly |clearly |directly )?(stated|mentioned|specified|"
    r"provided|available|discussed|addressed|covered))\b|"
    r"\bthe (sources?|documents?|articles?|excerpts?|texts?|passages?)\b|"
    r"\b(this|the) (study|guide|article|page|post|review|document|paper|report) "
    r"(compares|explains|describes|discusses|outlines|covers|provides|"
    r"aims?|seeks?|intends?|attempts?|examines)\b|"
    r"\bprovides? an overview\b|\bis intended to\b|"
    r"\bno (information|data|details|mention|evidence) (is |was |)"
    r"(given|provided|found|available)\b|"
    r"\b(further|more) research is (needed|required)\b",
    re.IGNORECASE)


def is_meta_claim(text: str) -> bool:
    return bool(META_CLAIM.search(text or ""))


# Extractor output scraped from a live dashboard whose widgets had not loaded.
# A shipped run asserted "the sea state wave height being at --", because the
# page really did say `--` and nothing downstream distinguished a value from a
# placeholder. Every token here is matched only in isolation: `--` needs
# non-word neighbours so "state-owned" and "12-15" survive, and the bare words
# need boundaries so "annulled" is not read as "null".
PLACEHOLDER = re.compile(
    r"(?<![\w-])(--+|–|—)(?![\w-])|"
    r"\.\.\.|…|\[\s*\]|\{\s*\}|"
    r"\bn/?a\b|\btbd\b|\btba\b|\bnull\b|\bundefined\b|\bnan\b|"
    r"\bloading\b|\bplease wait\b|\bno data\b|\bpending\b\s*$",
    re.IGNORECASE)


def is_junk_claim(text: str) -> bool:
    """True when the 'claim' carries a placeholder where its value should be.

    This is not a quality judgement — it is a check that an assertion was made
    at all. A sentence whose object is `--` asserts nothing, and letting one
    through means the confidence score is computed over noise.
    """
    return bool(PLACEHOLDER.search(text or ""))


# A cause stated before the effect ("the blockade HAS RESULTED IN x") versus
# after it ("x DUE TO the blockade"). The premise sits on the opposite side of
# the connective in each case, so they cannot share one pattern.
_CAUSE_FIRST = re.compile(
    r"\b(has |have |had )?(resulted in|caused|led to|triggered|prompted|"
    r"forced|drove|pushed)\b", re.IGNORECASE)
_CAUSE_LAST = re.compile(
    r"\b(due to|because of|as a result of|owing to|on account of|"
    r"in the wake of|following)\b", re.IGNORECASE)


def _singular(w: str) -> str:
    return w[:-1] if len(w) > 4 and w.endswith("s") and not w.endswith("ss") else w


def imported_premise(text: str, sources: list["Source"]) -> bool:
    """True when a claim asserts a cause that none of its sources mention.

    The failure this exists to stop: an extractor prefixed
    "The Strait of Hormuz blockade has resulted in..." onto claim after claim
    when no source said a blockade had happened. Lexical overlap cannot catch
    it — the invented frame wraps a real number from a real source, so the
    claim scores as supported and gets stamped `verified`. The fabricated part
    is precisely the part no source contains.

    So: isolate the asserted cause and require the sources to actually contain
    it. Every distinctive word of the premise must appear somewhere in the
    cited text, because a premise is only as sound as its rarest term — the
    background words ("strait", "hormuz") are shared by every page on the
    topic, and "blockade" is the whole of what was invented.
    """
    if not text or not sources:
        return False

    m = _CAUSE_FIRST.search(text)
    if m:
        premise = text[:m.start()]
    else:
        m = _CAUSE_LAST.search(text)
        if not m:
            return False        # no causal assertion to audit
        premise = text[m.end():]

    # Short words carry no premise on their own; requiring them invites false
    # positives on articles and prepositions the tokeniser keeps.
    ptoks = {_singular(t) for t in _tokens(premise) if len(t) >= 4}
    if not ptoks:
        return False

    corpus = " ".join((s.text or "") for s in sources).lower()
    stoks = {_singular(t) for t in _tokens(corpus)}
    return not ptoks.issubset(stoks)


def consolidate_claims(claims: list[Claim], threshold: float = 0.55) -> list[Claim]:
    """Merge claims that say the same thing, unioning their citations.

    Extraction runs source-by-source, so two sources agreeing on a point yield
    two separate one-citation claims. Left alone, every claim looks
    single-sourced and the corroboration machinery — the whole reason to read
    more than one page — never fires. Merging equivalent claims is what turns
    "seven sources said something" into "three sources agree on this".

    Claims carrying different numbers are never merged: those are a
    disagreement to surface, not a duplicate to collapse.
    """
    merged: list[Claim] = []
    sigs: list[set] = []
    for c in claims:
        # Claims are single sentences; 3-word shingles are too brittle for a
        # reworded restatement ("X is effective for Y" vs "X can be effective
        # for Y"), which is exactly the case worth merging. Compare content
        # words instead.
        sig = set(_tokens(c.text))
        cnums = {round(v, 4) for v, _ in numbers_in(c.text)}
        placed = False
        for i, other in enumerate(merged):
            onums = {round(v, 4) for v, _ in numbers_in(other.text)}
            if cnums and onums and cnums != onums:
                continue          # same topic, different figures -> keep apart
            if jaccard(sig, sigs[i]) >= threshold:
                for n in c.sources:
                    if n not in other.sources:
                        other.sources.append(n)
                # Keep the more specific wording (longer text usually carries
                # the figure or qualifier that makes the claim checkable).
                if len(c.text) > len(other.text):
                    other.text = c.text
                    sigs[i] = sig
                placed = True
                break
        if not placed:
            merged.append(c)
            sigs.append(sig)
    return merged


# ---------------------------------------------------------------------------
# Cross-source contradiction on numbers (the Statistician role, done exactly).
# ---------------------------------------------------------------------------
def numeric_conflicts(claims: list[Claim], tolerance: float = 0.10) -> list[dict]:
    """Claims that quantify the same thing with materially different numbers.

    Grouped by their non-numeric wording so 'X reached 500 Wh/kg' and 'X reached
    420 Wh/kg' collide, while unrelated quantities do not.
    """
    buckets: dict[str, list[tuple[Claim, float, str]]] = {}
    for c in claims:
        nums = numbers_in(c.text)
        if not nums:
            continue
        key_tokens = [w for w in _tokens(c.text) if not w.isdigit()]
        if len(key_tokens) < 3:
            continue
        key = " ".join(sorted(key_tokens)[:8])
        for v, unit in nums[:2]:
            # A bare number carries no comparable meaning: "phase 1" vs "2 doses"
            # are not a disagreement, yet unitless bucketing reported them as a
            # 300% spread. Only quantities with a unit are comparable.
            if not unit:
                continue
            buckets.setdefault(f"{key}|{unit}", []).append((c, v, unit))

    out = []
    for key, entries in buckets.items():
        if len(entries) < 2:
            continue
        # The bucket key is a loose token bag, so confirm the claims really are
        # about the same thing before calling their numbers a contradiction.
        if jaccard(_shingles(entries[0][0].text, k=3),
                   _shingles(entries[1][0].text, k=3)) < 0.18:
            continue
        vals = [v for _, v, _ in entries]
        lo, hi = min(vals), max(vals)
        if lo <= 0:
            continue
        if (hi - lo) / lo > tolerance:
            unit = entries[0][2]
            out.append({
                "unit": unit or "(unitless)",
                "low": lo, "high": hi,
                "spread_pct": round((hi - lo) / lo * 100),
                "claims": [{"text": c.text[:180],
                            "value": v,
                            "sources": c.sources} for c, v, _ in entries[:4]],
            })
    return sorted(out, key=lambda d: -d["spread_pct"])[:6]


# ---------------------------------------------------------------------------
# Citation audit on the final prose. The last line of defence: whatever the
# writer produced, every [n] it emitted must be a real source, and the sentence
# carrying it must have support.
# ---------------------------------------------------------------------------
_CITE = re.compile(r"\[(\d{1,2})\]")
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")


def audit_citations(text: str, sources: list[Source],
                    weak: float = 0.30) -> tuple[str, list[dict]]:
    """Check every [n] in the briefing. Returns (annotated_text, problems).

    A citation is a problem when it points at a source that does not exist, or
    when the sentence carrying it shares almost nothing with that source. Rather
    than silently deleting the marker — which would hide the failure — we mark
    it so the reader can see exactly which sentence outran its evidence.
    """
    by_n = {s.n: s for s in sources}
    problems: list[dict] = []
    if not text:
        return text, problems

    out_sentences = []
    for sent in _SENT_SPLIT.split(text):
        cites = [int(m) for m in _CITE.findall(sent)]
        # Structural markdown (headings, rules, bare list bullets) is not a
        # factual assertion, so auditing it produces confusing findings about
        # "sentences" like "### Uncertainties". Only prose is audited.
        prose = _CITE.sub("", sent).strip(" \t\n-*#>_|").strip()
        if not cites or len(prose) < 25:
            out_sentences.append(sent)
            continue
        bad = []
        for n in set(cites):
            src = by_n.get(n)
            if src is None:
                bad.append((n, "no such source"))
                continue
            if _support_score(_CITE.sub("", sent), src) < weak:
                bad.append((n, "sentence not supported by this source"))
        if bad:
            problems.append({
                "sentence": _CITE.sub("", sent).strip()[:220],
                "citations": [{"n": n, "reason": why} for n, why in bad],
            })
            sent = sent.rstrip() + " ⚠"
        out_sentences.append(sent)
    return " ".join(out_sentences), problems


def overall_confidence(claims: list[Claim], sources: list[Source]) -> dict:
    """Headline trust figure, with the arithmetic shown rather than asserted."""
    verified = [c for c in claims if c.verdict == "verified"]
    single = [c for c in claims if c.verdict == "single_source"]
    weak = [c for c in claims if c.verdict == "weak"]
    unsupported = [c for c in claims if c.verdict == "unsupported"]
    independent = len({s.duplicate_of or s.n for s in sources})
    if claims:
        score = int(sum(c.confidence for c in claims) / float(len(claims)))
    else:
        score = 0
    # Breadth of the evidence base caps how confident a single-source answer
    # is allowed to look.
    if independent <= 1:
        score = min(score, 45)
    elif independent == 2:
        score = min(score, 70)
    label = ("high" if score >= 72 else "moderate" if score >= 50
             else "low" if score >= 28 else "very low")
    return {
        "score": score, "label": label,
        "claims": len(claims), "verified": len(verified),
        "single_source": len(single),
        "weak": len(weak), "unsupported": len(unsupported),
        "independent_sources": independent,
    }
