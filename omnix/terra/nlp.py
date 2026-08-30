"""Text machinery for TERRA: tokens, TF-IDF vectors, sentiment, source trust.

Deliberately dependency-free. A sentence-transformer would give better vectors
than TF-IDF, but it is a ~90MB download and several seconds of cold start on a
machine that may not have the RAM — and for the job here (deduplicating and
clustering a few hundred news headlines that reuse the same proper nouns) the
lexical overlap TF-IDF measures is most of the signal anyway.

Everything is pure stdlib so it runs identically offline.
"""

from __future__ import annotations

import math
import re
from collections import Counter

# ---------------------------------------------------------------------------
# Tokenizing
# ---------------------------------------------------------------------------
_STOP = {
    "a", "an", "the", "and", "or", "but", "if", "then", "than", "that", "this",
    "these", "those", "of", "in", "on", "at", "to", "for", "from", "by", "with",
    "as", "is", "are", "was", "were", "be", "been", "being", "it", "its", "he",
    "she", "they", "them", "their", "his", "her", "we", "us", "our", "you",
    "your", "i", "me", "my", "has", "have", "had", "do", "does", "did", "will",
    "would", "can", "could", "should", "may", "might", "must", "not", "no",
    "yes", "up", "down", "out", "over", "under", "again", "more", "most",
    "other", "some", "such", "only", "own", "same", "so", "too", "very", "s",
    "t", "just", "now", "new", "says", "said", "say", "after", "before", "amid",
    "into", "about", "who", "what", "when", "where", "why", "how", "all", "any",
    "both", "each", "few", "one", "two", "first", "last", "also", "there",
    "here", "news", "report", "reports", "reported", "update", "updates",
    "live", "latest", "video", "watch", "read", "full", "top", "day", "days",
    "week", "year", "years", "vs", "via", "amp",
}

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'\-]*|\d{4}")


def tokens(text: str) -> list[str]:
    """Lowercased content words. Numbers are dropped except 4-digit years."""
    out = []
    for w in _WORD_RE.findall(text or ""):
        lw = w.lower().strip("'-")
        if len(lw) < 2 or lw in _STOP:
            continue
        out.append(lw)
    return out


def shingles(text: str, n: int = 3) -> set[str]:
    """Token n-grams — the near-duplicate signal that catches reworded titles."""
    tk = tokens(text)
    if len(tk) < n:
        return {" ".join(tk)} if tk else set()
    return {" ".join(tk[i:i + n]) for i in range(len(tk) - n + 1)}


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if not inter:
        return 0.0
    return inter / len(a | b)


# ---------------------------------------------------------------------------
# TF-IDF index — the "vector database", sized for a laptop.
#
# Sparse dicts rather than dense arrays: the vocabulary of a news corpus is tens
# of thousands of terms while any one article touches ~20 of them, so a dense
# representation would be 99.9% zeros and slower to score.
# ---------------------------------------------------------------------------
class TfIdf:
    def __init__(self):
        self.df: Counter = Counter()      # term -> document frequency
        self.docs: dict[str, dict] = {}   # doc id -> {term: tf-idf}, normalized
        self._raw: dict[str, Counter] = {}
        self._dirty = False

    def add(self, doc_id: str, text: str) -> None:
        tf = Counter(tokens(text))
        if not tf:
            return
        if doc_id in self._raw:                 # replacing: undo old df
            for term in self._raw[doc_id]:
                self.df[term] -= 1
                if self.df[term] <= 0:
                    del self.df[term]
        self._raw[doc_id] = tf
        for term in tf:
            self.df[term] += 1
        self._dirty = True

    def remove(self, doc_id: str) -> None:
        tf = self._raw.pop(doc_id, None)
        if not tf:
            return
        for term in tf:
            self.df[term] -= 1
            if self.df[term] <= 0:
                del self.df[term]
        self.docs.pop(doc_id, None)
        self._dirty = True

    def _weights(self, tf: Counter) -> dict[str, float]:
        n = max(1, len(self._raw))
        vec: dict[str, float] = {}
        for term, count in tf.items():
            idf = math.log((1 + n) / (1 + self.df.get(term, 0))) + 1.0
            vec[term] = (1.0 + math.log(count)) * idf
        norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        return {t: v / norm for t, v in vec.items()}

    def build(self) -> None:
        """Recompute every document vector. Cheap enough to just redo — idf
        shifts as documents arrive, so incremental updates would drift."""
        if not self._dirty:
            return
        self.docs = {d: self._weights(tf) for d, tf in self._raw.items()}
        self._dirty = False

    def vector(self, text: str) -> dict[str, float]:
        return self._weights(Counter(tokens(text)))

    def similarity(self, a: str, b: str) -> float:
        self.build()
        va, vb = self.docs.get(a), self.docs.get(b)
        if not va or not vb:
            return 0.0
        return cosine(va, vb)

    def search(self, query: str, k: int = 12, min_score: float = 0.02
               ) -> list[tuple[str, float]]:
        """Rank documents against a free-text query. Returns [(doc_id, score)]."""
        self.build()
        qv = self.vector(query)
        if not qv:
            return []
        # Only score documents that share at least one query term — for a sparse
        # corpus that skips almost everything without changing the result.
        candidates: set[str] = set()
        for term in qv:
            for doc_id, tf in self._raw.items():
                if term in tf:
                    candidates.add(doc_id)
        scored = []
        for doc_id in candidates:
            s = cosine(qv, self.docs.get(doc_id, {}))
            if s >= min_score:
                scored.append((doc_id, s))
        scored.sort(key=lambda x: -x[1])
        return scored[:k]

    def neighbors(self, doc_id: str, k: int = 8, min_score: float = 0.12
                  ) -> list[tuple[str, float]]:
        self.build()
        v = self.docs.get(doc_id)
        if not v:
            return []
        out = []
        for other, ov in self.docs.items():
            if other == doc_id:
                continue
            s = cosine(v, ov)
            if s >= min_score:
                out.append((other, s))
        out.sort(key=lambda x: -x[1])
        return out[:k]

    def top_terms(self, doc_ids: list[str], k: int = 8) -> list[str]:
        """The terms that most distinguish a set of documents — used to name
        an event cluster without asking a model."""
        self.build()
        agg: Counter = Counter()
        for d in doc_ids:
            for term, w in self.docs.get(d, {}).items():
                agg[term] += w
        return [t for t, _ in agg.most_common(k)]


def cosine(a: dict[str, float], b: dict[str, float]) -> float:
    if len(a) > len(b):
        a, b = b, a
    return sum(w * b.get(t, 0.0) for t, w in a.items())


# ---------------------------------------------------------------------------
# Sentiment / severity
#
# A general-purpose sentiment model is the wrong tool here: "killed" and
# "plunged" are strongly negative in a geopolitical risk sense but a product
# review model may treat a headline as neutral. This is a hand-built lexicon
# weighted for the five risk dimensions TERRA actually scores.
# ---------------------------------------------------------------------------
_NEGATIVE = {
    # violence / conflict
    "killed": 3.0, "kills": 3.0, "dead": 2.8, "deaths": 2.8, "massacre": 3.5,
    "war": 2.6, "warfare": 2.6, "attack": 2.4, "attacks": 2.4, "attacked": 2.4,
    "strike": 1.8, "strikes": 1.8, "airstrike": 3.0, "airstrikes": 3.0,
    "bomb": 3.0, "bombing": 3.2, "shelling": 3.0, "invasion": 3.4,
    "invade": 3.4, "offensive": 2.2, "militants": 2.4, "insurgents": 2.4,
    "terrorist": 3.0, "terror": 2.8, "gunmen": 2.8, "shooting": 2.6,
    "casualties": 2.8, "wounded": 2.0, "injured": 1.8, "hostage": 2.8,
    "clashes": 2.4, "clash": 2.4, "fighting": 2.2, "siege": 2.6,
    "genocide": 3.6, "atrocities": 3.4, "executed": 3.0, "assassination": 3.4,
    # instability
    "coup": 3.6, "uprising": 2.8, "protest": 1.6, "protests": 1.6,
    "riot": 2.6, "riots": 2.6, "unrest": 2.4, "crackdown": 2.6,
    "arrested": 1.6, "detained": 1.6, "impeach": 2.2, "impeachment": 2.2,
    "ousted": 2.6, "resign": 1.8, "resigns": 1.8, "resignation": 1.8,
    "martial": 3.0, "curfew": 2.4, "emergency": 2.2, "dissolved": 2.0,
    "junta": 3.0, "authoritarian": 2.0, "purge": 2.6, "exile": 2.2,
    # economic
    "crash": 2.8, "crashes": 2.8, "plunge": 2.4, "plunges": 2.4,
    "plummet": 2.4, "slump": 2.0, "recession": 2.6, "inflation": 1.6,
    "hyperinflation": 3.2, "default": 3.0, "bankrupt": 2.8,
    "bankruptcy": 2.8, "collapse": 2.8, "crisis": 2.6, "shortage": 2.2,
    "sanctions": 2.4, "tariff": 1.8, "tariffs": 1.8, "embargo": 2.6,
    "layoffs": 2.0, "unemployment": 1.8, "devaluation": 2.4, "debt": 1.4,
    "downgrade": 2.0, "bailout": 2.2, "austerity": 2.0,
    # disaster / climate
    "earthquake": 3.0, "quake": 3.0, "tsunami": 3.4, "hurricane": 3.0,
    "typhoon": 3.0, "cyclone": 3.0, "flood": 2.6, "flooding": 2.6,
    "floods": 2.6, "wildfire": 2.8, "wildfires": 2.8, "drought": 2.4,
    "famine": 3.4, "landslide": 2.6, "eruption": 2.8, "volcano": 2.4,
    "heatwave": 2.2, "blizzard": 2.0, "storm": 1.8, "evacuated": 2.4,
    "evacuation": 2.4, "displaced": 2.6, "devastated": 3.0,
    # cyber / infrastructure
    "hacked": 2.6, "hack": 2.4, "breach": 2.6, "ransomware": 3.0,
    "malware": 2.4, "cyberattack": 3.0, "cyberattacks": 3.0,
    "outage": 2.2, "blackout": 2.4, "leaked": 2.0, "espionage": 2.6,
    "spyware": 2.6, "phishing": 1.8, "compromised": 2.2, "exploit": 1.8,
    "disruption": 2.0, "disrupted": 2.0, "vulnerability": 1.8,
    # health
    "outbreak": 2.8, "epidemic": 3.0, "pandemic": 3.2, "virus": 1.8,
    "infection": 2.0, "infections": 2.0, "quarantine": 2.4, "cholera": 3.0,
    "ebola": 3.4, "measles": 2.2, "contamination": 2.4, "toxic": 2.2,
    # general negative
    "warns": 1.6, "warning": 1.6, "threat": 2.0, "threatens": 2.2,
    "fears": 1.8, "tensions": 2.0, "condemned": 1.8, "accused": 1.6,
    "scandal": 2.0, "corruption": 2.2, "fraud": 2.0, "banned": 1.8,
    "blocked": 1.4, "halted": 1.6, "suspended": 1.6, "failed": 1.8,
    "rejected": 1.4, "escalates": 2.4, "escalation": 2.4,
}

_POSITIVE = {
    "peace": 2.6, "ceasefire": 2.8, "truce": 2.6, "agreement": 1.8,
    "deal": 1.4, "treaty": 2.0, "accord": 2.0, "resolution": 1.6,
    "recovery": 2.0, "recovers": 1.8, "rebound": 1.8, "growth": 1.6,
    "surge": 1.2, "record": 1.0, "boost": 1.4, "gains": 1.4, "rally": 1.4,
    "breakthrough": 2.2, "cure": 2.2, "vaccine": 1.6, "aid": 1.6,
    "relief": 1.8, "rescued": 2.0, "freed": 2.0, "released": 1.4,
    "reopened": 1.6, "restored": 1.8, "stabilize": 1.8, "stabilized": 1.8,
    "cooperation": 1.8, "alliance": 1.4, "investment": 1.4, "signed": 1.2,
    "wins": 1.2, "elected": 1.2, "approved": 1.2, "eased": 1.6,
    "lifted": 1.6, "de-escalation": 2.6, "withdrawal": 1.4,
}


def sentiment(text: str) -> float:
    """Signed intensity in roughly [-1, 1]. Negative = bad news.

    Magnitude carries the useful part: -0.8 is an atrocity, -0.15 is a dull
    diplomatic spat. Scaled by a saturating curve so a headline stacking five
    grim words doesn't outrun one that stacks eight.
    """
    tk = tokens(text)
    if not tk:
        return 0.0
    score = 0.0
    for i, w in enumerate(tk):
        neg = _NEGATIVE.get(w, 0.0)
        pos = _POSITIVE.get(w, 0.0)
        if not neg and not pos:
            continue
        # Simple negation window: "no casualties", "avoids war".
        window = tk[max(0, i - 3):i]
        flipped = any(x in ("no", "not", "without", "avoid", "avoids",
                            "avoided", "prevent", "prevents", "prevented",
                            "denies", "denied") for x in window)
        val = pos - neg
        if flipped:
            val = -val * 0.6
        score += val
    # tanh-like saturation without importing math.tanh on every call
    return max(-1.0, min(1.0, score / (abs(score) + 4.0)))


def severity(text: str) -> float:
    """Unsigned 0..1 'how much does this matter' — negative OR positive extremes
    both register, because a ceasefire is as newsworthy as an invasion."""
    return abs(sentiment(text))


# ---------------------------------------------------------------------------
# Source confidence
#
# An explicit, editable prior on how much weight a single outlet's claim should
# carry before corroboration. It is a heuristic about editorial process and
# state independence, NOT a claim about any individual article being true —
# every consumer of this number shows it to the user alongside the outlet name
# so the judgement stays visible rather than buried in a score.
# ---------------------------------------------------------------------------
SOURCE_CONFIDENCE = {
    # wire services — the corroboration backbone
    "reuters": 0.95, "associated press": 0.95, "ap news": 0.95, "ap": 0.95,
    "agence france-presse": 0.92, "afp": 0.92,
    # major independent broadcasters / papers
    "bbc": 0.92, "bbc news": 0.92, "npr": 0.88, "pbs": 0.88,
    "the guardian": 0.86, "guardian": 0.86, "financial times": 0.90,
    "bloomberg": 0.90, "the wall street journal": 0.88, "wsj": 0.88,
    "the new york times": 0.88, "nyt": 0.88, "the washington post": 0.86,
    "the economist": 0.88, "deutsche welle": 0.88, "dw": 0.88,
    "france 24": 0.85, "cnbc": 0.82, "cnn": 0.80, "abc news": 0.82,
    "cbs news": 0.82, "nbc news": 0.82, "sky news": 0.80,
    "al jazeera": 0.82, "al jazeera english": 0.82, "the hindu": 0.84,
    "the times of india": 0.75, "hindustan times": 0.76, "ndtv": 0.76,
    "indian express": 0.80, "the indian express": 0.80, "scroll.in": 0.78,
    "nikkei": 0.86, "south china morning post": 0.76, "scmp": 0.76,
    "the straits times": 0.82, "haaretz": 0.82, "times of israel": 0.78,
    "the telegraph": 0.78, "politico": 0.80, "axios": 0.80, "reuters india": 0.95,
    # partisan or state-directed — usable as signal, discounted as evidence
    "fox news": 0.62, "msnbc": 0.62, "newsmax": 0.40, "the daily mail": 0.45,
    "daily mail": 0.45, "new york post": 0.55, "the sun": 0.40,
    "rt": 0.30, "russia today": 0.30, "sputnik": 0.28, "tass": 0.38,
    "xinhua": 0.42, "global times": 0.38, "cgtn": 0.40, "press tv": 0.32,
    "anadolu agency": 0.60, "anadolu": 0.60,
}

DEFAULT_CONFIDENCE = 0.60


def source_confidence(source: str) -> float:
    s = (source or "").strip().lower()
    if not s:
        return DEFAULT_CONFIDENCE
    if s in SOURCE_CONFIDENCE:
        return SOURCE_CONFIDENCE[s]
    # Fall back to a containment match so "BBC Sport" inherits from "bbc".
    for name, conf in SOURCE_CONFIDENCE.items():
        if name in s or s in name:
            return conf
    return DEFAULT_CONFIDENCE


def confidence_label(score: float) -> str:
    if score >= 0.88:
        return "wire-grade"
    if score >= 0.78:
        return "high"
    if score >= 0.60:
        return "moderate"
    if score >= 0.45:
        return "low"
    return "state-aligned"
