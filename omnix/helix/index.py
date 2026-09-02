"""In-memory BM25 retrieval over the HELIX corpus.

WHY BM25 AND NOT EMBEDDINGS
---------------------------
An embedding index would need a model call to embed the query, which is the one
thing this feature exists to avoid on the fast path — it would put a network
round trip in front of every question and make "instant" impossible offline.
BM25 over 4,000 abstracts scores in single-digit milliseconds on one core, needs
no model, no GPU and no extra dependency, and for the queries people actually
type here — tool names, method names, gene names, acronyms — exact lexical
matching is *better* than semantic similarity, because "Is minimap2 or BWA
faster" is a question about those literal strings.

The one thing lexical matching loses is synonymy, and that is handled where it
belongs: `topics.py` carries the alias table, and `expand()` rewrites a query
through it before scoring.

THE INDEX IS BUILT ONCE
-----------------------
Tokenising 5MB of abstracts takes a couple of seconds, which is fine once and
unacceptable per request. `shared()` builds it on first use behind a lock and
every later call reuses it. It is deliberately not built at import: a server
that has never been asked a bioinformatics question should not pay for one.
"""

from __future__ import annotations

import json
import math
import re
import threading
import time
from collections import Counter, defaultdict
from pathlib import Path

from .topics import BY_KEY, find as find_topics

CORPUS_PATH = Path(__file__).resolve().parent.parent.parent / "omnix_helix_corpus.json"

# Split on anything that is not alphanumeric, and keep digits: version numbers
# and tool names carry them (BWA-MEM2, Kraken2, ESM-2, scVI, Hi-C).
_TOKEN = re.compile(r"[a-z0-9]+")

# Words that appear in nearly every abstract in this corpus and therefore
# separate nothing. A generic English stop list would keep "sequence" and
# "analysis", which here are pure noise.
_STOP = frozenset("""
a an and are as at be been but by can for from has have here how if in into is
it its of on or our that the their there these they this to was we were what
when where which who will with within without study studies result results
method methods approach approaches using use used based show shows shown
propose proposed present presented paper we
""".split())


def _tokens(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower())
            if t not in _STOP and len(t) > 1]


class Index:
    """BM25 over title + abstract, with a title boost."""

    # Standard BM25 constants. k1 controls term-frequency saturation, b how
    # hard length normalisation bites. Abstracts are uniform in length, so b is
    # a little below the usual 0.75.
    K1 = 1.4
    B = 0.6
    # A term in the title is worth this many occurrences in the abstract. The
    # title of a methods paper names the method, which is usually the query.
    TITLE_WEIGHT = 3

    def __init__(self, papers: list[dict]):
        t0 = time.monotonic()
        self.papers = papers
        self.by_pmid = {p["pmid"]: p for p in papers}
        self.postings: dict[str, list[tuple[int, int]]] = defaultdict(list)
        self.lengths: list[int] = []

        for i, p in enumerate(papers):
            counts = Counter(_tokens(p["abstract"]))
            for tok in _tokens(p["title"]):
                counts[tok] += self.TITLE_WEIGHT
            self.lengths.append(sum(counts.values()) or 1)
            for tok, n in counts.items():
                self.postings[tok].append((i, n))

        self.n = len(papers)
        self.avg_len = (sum(self.lengths) / self.n) if self.n else 1.0
        # Precomputed IDF: it depends only on the corpus, and recomputing it per
        # query was 30% of query time.
        self.idf = {
            tok: math.log(1 + (self.n - len(post) + 0.5) / (len(post) + 0.5))
            for tok, post in self.postings.items()
        }
        self.build_seconds = round(time.monotonic() - t0, 2)

    # -- querying ----------------------------------------------------------
    # Expansion terms are worth a quarter of a term the user actually typed.
    # At parity they outvote the query: "Is minimap2 or BWA better?" expands to
    # the whole alignment toolbox, and the dozens of MAFFT and MUSCLE papers
    # then outrank the thirteen that mention minimap2 — the exact question the
    # user asked gets buried by its own topic. Expansion is a tie-breaker.
    EXPANSION_WEIGHT = 0.25

    def expand(self, query: str) -> dict[str, float]:
        """Query terms with weights: what the user typed, plus topic vocabulary.

        Without expansion, "how do I cluster cells" retrieves nothing useful,
        because no abstract says "cluster cells" in those words while many say
        "Leiden", "Seurat" or "cell type annotation". The alias table is the
        cheap stand-in for a synonym model — but it is a hint, not a rewrite.
        """
        weights: dict[str, float] = {}
        for tok in _tokens(query):
            weights[tok] = 1.0

        for topic in find_topics(query)[:2]:
            extra = _tokens(topic.label)
            for tool in topic.tools[:6]:
                extra += _tokens(tool)
            for tok in extra:
                # Never downgrade a term the user typed.
                if tok not in weights:
                    weights[tok] = self.EXPANSION_WEIGHT
        return weights

    def search(self, query: str, limit: int = 8,
               topic: str | None = None) -> list[tuple[float, dict]]:
        """Top `limit` papers for `query`, best first."""
        weights = self.expand(query)
        if not weights:
            return []

        scores: dict[int, float] = defaultdict(float)
        for tok, weight in weights.items():
            post = self.postings.get(tok)
            if not post:
                continue
            idf = self.idf[tok]
            # A term matching almost everything adds noise and costs the most
            # time; skip it rather than let it dominate by sheer posting length.
            if len(post) > self.n * 0.5:
                continue
            for i, tf in post:
                norm = 1 - self.B + self.B * (self.lengths[i] / self.avg_len)
                scores[i] += weight * idf * (tf * (self.K1 + 1)) / (tf + self.K1 * norm)

        ranked = sorted(scores.items(), key=lambda kv: -kv[1])

        out: list[tuple[float, dict]] = []
        for i, score in ranked:
            paper = self.papers[i]
            if topic and topic not in paper["topics"]:
                continue
            out.append((round(score, 3), paper))
            if len(out) >= limit:
                break
        return out

    def stats(self) -> dict:
        years = Counter(p.get("year", "") for p in self.papers)
        topics = Counter(t for p in self.papers for t in p["topics"])
        return {
            "papers": self.n,
            "vocabulary": len(self.postings),
            "buildSeconds": self.build_seconds,
            "byTopic": {k: topics.get(k, 0) for k in BY_KEY},
            "byYear": {y: n for y, n in sorted(years.items()) if y},
            "journals": dict(Counter(p["journal"] for p in self.papers).most_common(15)),
            "medianAbstract": (
                sorted(len(p["abstract"]) for p in self.papers)[self.n // 2]
                if self.n else 0),
        }


# -- process-wide singleton --------------------------------------------------
_index: Index | None = None
_lock = threading.Lock()
_load_error: str = ""


def load_corpus() -> list[dict]:
    if not CORPUS_PATH.exists():
        raise FileNotFoundError(
            f"no HELIX corpus at {CORPUS_PATH}. Build it with: "
            "python -m omnix.helix.ingest")
    data = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    return data.get("papers", [])


def shared() -> Index:
    """The one index, built on first use.

    Double-checked under a lock: two requests arriving together on a cold
    process would otherwise each tokenise the whole corpus, and the loser would
    throw its work away after several seconds of CPU.
    """
    global _index, _load_error
    if _index is not None:
        return _index
    with _lock:
        if _index is None:
            _index = Index(load_corpus())
            _load_error = ""
    return _index


def ready() -> bool:
    """Whether the index is already built — used to report honest latency."""
    return _index is not None


def available() -> tuple[bool, str]:
    """Can this feature answer at all? Returns (ok, reason-if-not)."""
    if _index is not None:
        return True, ""
    if not CORPUS_PATH.exists():
        return False, ("The bioinformatics corpus has not been built yet. "
                       "Run: python -m omni.helix.ingest")
    return True, ""
