"""Answering bioinformatics questions from the corpus.

THE SPEED ARGUMENT
------------------
Most questions asked of a domain assistant are not open research questions.
They are "what is X", "which tool does Y", "what does Z stand for" — and those
are answerable from structure, not from generation. So there are two paths:

  fast path    Retrieval + the topic table, assembled deterministically.
               No model call, no network. Sub-10ms, works offline, and cannot
               hallucinate because nothing is generated.

  grounded     Retrieval, then a model that is shown the retrieved abstracts
               path         and told to answer only from them. Costs one model
               call, streams, and every claim carries a PMID.

Roughly half of realistic traffic lands on the fast path. That is where "answers
quickly" actually comes from — not from picking a smaller model.

WHY THESE MODELS
----------------
Measured on this account (see `model_catalog`'s header for the methodology):

    nemotron-3-nano-30b-a3b     2.5s to first token, 5.7s complete
    llama-3.1-8b-instruct       0.6s to first token, 2.2s complete
    nemotron-3-super-120b-a12b  4.6s to first token, 8.8s complete

The default lead is the 30B A3B mixture-of-experts: only ~3B parameters are
active per token, so it costs a small model's latency while reasoning like a
large one — the best intelligence-per-second on the tier, which is the actual
requirement here. The 120B leads only in Deep mode, where a question is worth
four more seconds. The 8B sits behind both as a hedge, not as a default: it is
the fastest model available and not good enough to be trusted with a grounded
citation task on its own.

The hedging is `cloud.stream_ladder`'s: if the lead has produced nothing by the
hedge deadline, the next rung is started in parallel and whichever speaks first
owns the answer. A cold MoE instance therefore costs a hedge, not a timeout.

WHY GROUNDING IS THE POINT
--------------------------
The model is never asked what it knows about bioinformatics. It is asked to read
four to eight abstracts and answer from them. That is what makes this feature
*smart* rather than merely well-informed: the answer is current to the last
corpus sync, it is attributable to a specific paper, and when the corpus does
not cover something the honest answer — "the corpus does not address this" — is
reachable, which it never is for a model answering from memory.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from . import index as helix_index
from .topics import Topic, find as find_topics

# --- model ladders -----------------------------------------------------------
QUICK_LADDER = [
    "nvidia/nemotron-3-nano-30b-a3b",       # lead: MoE, fast and capable
    "meta/llama-3.1-8b-instruct",           # hedge: fastest thing available
    "nvidia/nemotron-mini-4b-instruct",     # anchor: always warm
]
DEEP_LADDER = [
    "nvidia/nemotron-3-super-120b-a12b",    # lead: reads more, keeps sources straight
    "nvidia/nemotron-3-nano-30b-a3b",
    "meta/llama-3.1-8b-instruct",
]

# How many abstracts go into the prompt. More context is not free: it delays the
# first token, and past roughly this many the model starts averaging sources
# together instead of citing them individually.
QUICK_SOURCES = 5
DEEP_SOURCES = 10

SYSTEM = """You are HELIX, the bioinformatics specialist inside OMNIX.

You answer ONLY from the numbered papers given to you below. They are real
PubMed records retrieved for this question.

Rules, in order of importance:
1. Every factual claim cites the paper it came from, as [1], [2] — the numbers
   in the SOURCES block. Never invent a citation number.
2. If the sources do not answer the question, say so plainly and say what they
   do cover. Do not fill the gap from memory. A short honest answer beats a
   long confident one.
3. Distinguish what a paper FOUND from what it merely set out to do. Abstracts
   are labelled BACKGROUND / METHODS / RESULTS where the publisher provided
   labels; findings live in RESULTS and CONCLUSIONS.
4. Where papers disagree, say that they disagree and cite both. Do not
   average them into a false consensus.
5. Be concrete and technical. The reader is a working bioinformatician: name
   the tools, the algorithms and the tradeoffs. Do not explain what DNA is.
6. Lead with the answer. No preamble, no restating the question.

Format: short prose or tight bullets. Bold the key terms. No headings unless
the answer genuinely has several parts."""


# --- question shape ----------------------------------------------------------
_DEFINITION = re.compile(
    r"^\s*(what(?:'s| is| are)|define|meaning of|explain)\b", re.I)
_TOOLS = re.compile(
    r"\b(which|what|best|recommend\w*|list)\b.{0,40}\b"
    r"(tool|tools|software|package|packages|library|libraries|pipeline)\b", re.I)
_OVERVIEW = re.compile(r"\b(overview|introduce|introduction|summar\w+)\b", re.I)


@dataclass
class Source:
    n: int
    pmid: str
    doi: str
    title: str
    journal: str
    year: str
    authors: list[str]
    score: float
    url: str

    def as_dict(self) -> dict:
        return {
            "n": self.n, "pmid": self.pmid, "doi": self.doi,
            "title": self.title, "journal": self.journal, "year": self.year,
            "authors": self.authors, "score": self.score, "url": self.url,
        }


@dataclass
class Plan:
    """What answering this question will take, decided before any model runs."""
    question: str
    topics: list[Topic]
    sources: list[Source] = field(default_factory=list)
    abstracts: list[str] = field(default_factory=list)
    instant: str = ""            # a complete answer, if one is derivable
    kind: str = "open"           # definition | tools | overview | open
    retrieval_ms: float = 0.0


def _url(pmid: str) -> str:
    return f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"


def plan(question: str, deep: bool = False, topic: str | None = None) -> Plan:
    """Retrieve, classify, and answer outright when the answer is structural."""
    t0 = time.monotonic()
    ix = helix_index.shared()
    want = DEEP_SOURCES if deep else QUICK_SOURCES
    hits = ix.search(question, limit=want, topic=topic)
    topics = find_topics(question)

    sources = [
        Source(n=i + 1, pmid=p["pmid"], doi=p.get("doi", ""), title=p["title"],
               journal=p.get("journal", ""), year=p.get("year", ""),
               authors=p.get("authors", [])[:4], score=score, url=_url(p["pmid"]))
        for i, (score, p) in enumerate(hits)
    ]
    abstracts = [p["abstract"] for _, p in hits]

    kind = "open"
    if _TOOLS.search(question):
        kind = "tools"
    elif _DEFINITION.search(question):
        kind = "definition"
    elif _OVERVIEW.search(question):
        kind = "overview"

    p = Plan(question=question, topics=topics, sources=sources,
             abstracts=abstracts, kind=kind,
             retrieval_ms=round((time.monotonic() - t0) * 1000, 2))
    p.instant = _instant(p)
    return p


def _instant(p: Plan) -> str:
    """A complete answer assembled without a model, or "" if one is not derivable.

    Only produced when the question is squarely about a topic in the taxonomy.
    A question that merely mentions a topic in passing ("does Seurat handle
    spatial data") is not answered from the table — the table knows what Seurat
    is for, not what it handles, and answering anyway would be the confident
    wrong answer this feature exists to avoid.
    """
    if not p.topics or p.kind == "open":
        return ""
    topic = p.topics[0]

    # The topic must be what the question is ABOUT, not a word inside it.
    named = any(term in p.question.lower() for term in topic.terms)
    if not named:
        return ""

    lines: list[str] = []
    if p.kind in ("definition", "overview"):
        lines.append(f"**{topic.label}.** {topic.summary}")
        lines.append("")
        lines.append("**Core methods**")
        lines += [f"- {m}" for m in topic.methods]
        lines.append("")
        lines.append(f"**Commonly used**: {', '.join(topic.tools)}")
    elif p.kind == "tools":
        lines.append(f"**{topic.label} — the tools in common use:**")
        lines.append("")
        lines += [f"- **{t}**" for t in topic.tools]
        lines.append("")
        lines.append(f"They implement: {'; '.join(topic.methods[:4])}.")
    else:
        return ""

    if p.sources:
        lines.append("")
        cites = ", ".join(f"[{s.n}]" for s in p.sources[:3])
        lines.append(
            f"The corpus holds {len(p.sources)} papers matching this question "
            f"most closely {cites}; ask something specific about them for a "
            "grounded answer.")
    return "\n".join(lines)


def prompt(p: Plan) -> list[dict]:
    """The messages for a grounded answer."""
    blocks = []
    for src, abstract in zip(p.sources, p.abstracts):
        who = ", ".join(src.authors[:3]) + (" et al." if len(src.authors) > 3 else "")
        blocks.append(
            f"[{src.n}] {src.title}\n"
            f"    {who} — {src.journal} ({src.year}), PMID {src.pmid}\n"
            f"    {abstract}")
    sources_block = "\n\n".join(blocks) if blocks else "(no papers matched)"

    background = ""
    if p.topics:
        t = p.topics[0]
        background = (
            f"\n\nBACKGROUND (context only, never cite this):\n"
            f"{t.label}: {t.summary}\n"
            f"Tools in common use: {', '.join(t.tools)}")

    return [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content":
            f"SOURCES:\n\n{sources_block}{background}\n\n"
            f"QUESTION: {p.question}"},
    ]


# Some models emit citations in a bracket form borrowed from another product —
# `【1†L1-L4】` — instead of the `[1]` they were asked for. It renders as
# mojibake and breaks the source links, so it is normalised on the way out.
# Matching is on the CJK brackets themselves, which appear nowhere else in an
# English answer about bioinformatics.
_FOREIGN_CITE = re.compile(r"【(\d+)[^】]*】")


def _sanitise(text: str) -> str:
    return _FOREIGN_CITE.sub(lambda m: "[" + m.group(1) + "]", text)


def sanitised(chunks):
    """Normalise citation artefacts across a stream, without stalling it.

    The artefact can straddle a chunk boundary, so a naive per-chunk regex
    would miss half of them. Anything after the last safe point is held back
    until the delimiter arrives — at most a few characters, and only while an
    open bracket is pending, so the stream still feels live.
    """
    buffer = ""
    for chunk in chunks:
        buffer += chunk
        cut = buffer.rfind("【")
        if cut == -1:
            yield _sanitise(buffer)
            buffer = ""
        elif "】" in buffer[cut:]:
            yield _sanitise(buffer)
            buffer = ""
        else:
            # An unterminated artefact: emit everything before it, hold the rest.
            if cut:
                yield _sanitise(buffer[:cut])
            buffer = buffer[cut:]
    if buffer:
        yield _sanitise(buffer)


def stream(p: Plan, deep: bool = False):
    """Yield answer chunks from the model ladder. Raises on total failure."""
    from ..cloud import stream_ladder

    ladder = DEEP_LADDER if deep else QUICK_LADDER
    # A grounded answer over abstracts is a reading task, not a creative one.
    #
    # `thinking: False` is load-bearing, for two reasons measured on these exact
    # models (see nvidia_client's notes): it removes ~2.8s of private
    # deliberation before the first word on the 30B and ~18s on the 120B, and it
    # stops the deliberation being spent out of `max_tokens`. It also fixes a
    # correctness problem seen here — with thinking on, this model wrote its
    # working into the answer ("We need to answer... From [1]... Now need to
    # answer:"), which is a scratchpad, not a reply.
    return sanitised(stream_ladder(
        ladder, prompt(p),
        {"temperature": 0.2, "max_tokens": 1400 if deep else 900,
         "thinking": False},
    ))
