"""Articles -> typed ontology objects and relationships.

Three passes, in increasing cost and decreasing precision:

  1. Gazetteer   Countries and seed objects, matched by longest surface form.
                 Near-perfect precision, and the only pass that runs on every
                 article, every crawl. Free and offline.
  2. Heuristic   Capitalized noun phrases that survive a stoplist become
                 candidate people/organizations. Catches the long tail the
                 gazetteer can't know about (a minister who took office
                 yesterday). Also free.
  3. LLM         The strongest articles get read by a model that returns typed
                 SUBJECT-RELATION-OBJECT triples constrained to the ontology's
                 relation vocabulary. This is the pass that turns co-occurrence
                 into actual relationships ("sanctions" vs "negotiating with"),
                 and it is the only one that needs the network.

Pass 3 failing is a normal condition, not an error — the graph is fully usable
with passes 1 and 2 alone, which is what makes the whole feature work offline.
"""

from __future__ import annotations

import re
from collections import Counter

from . import ontology as onto
from .nlp import tokens

# ---------------------------------------------------------------------------
# Domain classification — which of the six analyst agents should read this.
# ---------------------------------------------------------------------------
DOMAINS = {
    "news":     {"label": "General",       "glyph": "◍", "color": "#c9a45c"},
    "economic": {"label": "Economic",      "glyph": "▤", "color": "#4ade80"},
    "military": {"label": "Military",      "glyph": "⬢", "color": "#ff5d7a"},
    "climate":  {"label": "Climate",       "glyph": "◈", "color": "#57d7ff"},
    "cyber":    {"label": "Cyber",         "glyph": "⬡", "color": "#9d8cff"},
    "health":   {"label": "Health",        "glyph": "✚", "color": "#ff9a62"},
}

_DOMAIN_TERMS: dict[str, tuple[str, ...]] = {
    "economic": ("market", "markets", "stock", "stocks", "shares", "economy",
                 "economic", "inflation", "gdp", "recession", "tariff",
                 "tariffs", "trade", "export", "exports", "import", "imports",
                 "currency", "dollar", "euro", "rupee", "yuan", "oil", "gas",
                 "opec", "barrel", "commodity", "commodities", "supply",
                 "chain", "shipping", "port", "freight", "central", "bank",
                 "rates", "bond", "bonds", "debt", "default", "investment",
                 "investors", "earnings", "profit", "revenue", "layoffs",
                 "unemployment", "sanctions", "embargo", "semiconductor",
                 "chips", "manufacturing", "factory", "crypto", "bitcoin",
                 "budget", "fiscal", "imf", "subsidy", "prices", "price"),
    "military": ("military", "troops", "army", "navy", "air force", "soldier",
                 "soldiers", "missile", "missiles", "drone", "drones",
                 "airstrike", "airstrikes", "strike", "strikes", "war",
                 "warfare", "combat", "offensive", "defense", "defence",
                 "nato", "nuclear", "warhead", "artillery", "tank", "tanks",
                 "frontline", "ceasefire", "truce", "militants", "insurgents",
                 "rebels", "militia", "weapons", "arms", "invasion",
                 "occupation", "battalion", "airspace", "warship", "submarine",
                 "coup", "junta", "conscription", "mobilization"),
    "climate":  ("climate", "earthquake", "quake", "tsunami", "hurricane",
                 "typhoon", "cyclone", "flood", "floods", "flooding",
                 "wildfire", "wildfires", "drought", "famine", "heatwave",
                 "storm", "blizzard", "landslide", "volcano", "eruption",
                 "emissions", "warming", "carbon", "glacier", "monsoon",
                 "rainfall", "temperatures", "disaster", "evacuated",
                 "evacuation", "magnitude", "aftershock", "erosion",
                 "deforestation", "renewable", "solar", "wind"),
    "cyber":    ("cyber", "cyberattack", "cyberattacks", "hack", "hacked",
                 "hackers", "hacking", "ransomware", "malware", "phishing",
                 "breach", "data", "leak", "leaked", "spyware", "botnet",
                 "ddos", "vulnerability", "exploit", "zero-day", "encryption",
                 "outage", "internet", "network", "server", "servers",
                 "credentials", "espionage", "surveillance", "firewall",
                 "disinformation", "deepfake", "misinformation"),
    "health":   ("health", "outbreak", "epidemic", "pandemic", "virus",
                 "disease", "infection", "infections", "vaccine", "vaccines",
                 "hospital", "hospitals", "patients", "who", "cholera",
                 "measles", "malaria", "ebola", "influenza", "flu",
                 "quarantine", "mortality", "cases", "medicine", "drug",
                 "clinical", "contamination", "sanitation", "malnutrition"),
}


def classify_domains(text: str, preset: list[str] | None = None) -> list[str]:
    """Which analyst domains this article belongs to. Multi-label on purpose —
    "sanctions on Russian oil" is genuinely both economic and military, and
    forcing a single label is what makes each agent's view incomplete."""
    low = " " + (text or "").lower() + " "
    hits: list[tuple[str, int]] = []
    for domain, terms in _DOMAIN_TERMS.items():
        n = sum(1 for t in terms if (" " + t + " ") in low or (" " + t + ",") in low)
        if n:
            hits.append((domain, n))
    hits.sort(key=lambda x: -x[1])
    # Two independent term hits, not one. A single match is usually a word doing
    # double duty — "breach of the border fence" is not a cyber story, and
    # "drone delivery" is not a military one.
    out = [d for d, n in hits if n >= 2][:3]
    for d in preset or []:
        if d not in out:
            out.append(d)
    return out or ["news"]


# ---------------------------------------------------------------------------
# Pass 1 — gazetteer
# ---------------------------------------------------------------------------
def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")[:60]


def entity_id(etype: str, name: str) -> str:
    if etype == "country":
        return f"country:{(name or '').upper()}"
    return f"{etype}:{slug(name)}"


class Gazetteer:
    """Longest-match surface-form matcher over the ontology's known objects."""

    def __init__(self):
        self.surface: dict[str, tuple[str, str, str]] = {}  # form -> (id,type,name)
        for iso, meta in onto.countries().items():
            self._add(meta["name"], "country", iso, iso)
        for form, iso in onto.country_lookup().items():
            self._add(form, "country", iso, iso)
        for name, meta in onto.seed_objects().items():
            etype = meta.get("type", "organization")
            self._add(name, etype, name, name)
            for alias in meta.get("aliases", []):
                self._add(alias, etype, name, name)
        # Longest forms first so "United States of America" wins over "America".
        self._forms = sorted(self.surface, key=len, reverse=True)

    def _add(self, form: str, etype: str, key: str, display: str) -> None:
        form = (form or "").strip().lower()
        if len(form) < 2:
            return
        name = onto.country_name(key) if etype == "country" else display
        self.surface.setdefault(form, (entity_id(etype, key), etype, name))

    def match(self, text: str) -> list[dict]:
        """Entities present in `text`, each with its match count."""
        low = " " + re.sub(r"\s+", " ", (text or "").lower()) + " "
        found: dict[str, dict] = {}
        consumed: list[tuple[int, int]] = []
        for form in self._forms:
            # Word-boundary search that tolerates the trailing-space aliases
            # ("un ", "eu ") used to keep two-letter forms from matching inside
            # ordinary words.
            pattern = form if form.endswith(" ") else form + " "
            start = 0
            while True:
                idx = low.find(" " + pattern, start)
                if idx == -1:
                    break
                # The span is the MATCHED WORDS only — not the delimiting
                # spaces. Including them made two adjacent entities overlap by
                # the single space between them, so in "Chinese chip export"
                # the longer form won and "Chinese" was silently dropped.
                span = (idx + 1, idx + len(pattern))
                start = idx + 1
                if any(s <= span[0] < e or s < span[1] <= e for s, e in consumed):
                    continue  # already claimed by a longer form
                consumed.append(span)
                eid, etype, name = self.surface[form]
                rec = found.setdefault(eid, {"id": eid, "type": etype,
                                             "name": name, "count": 0})
                rec["count"] += 1
        return list(found.values())


_gaz: Gazetteer | None = None


def gazetteer() -> Gazetteer:
    global _gaz
    if _gaz is None:
        _gaz = Gazetteer()
    return _gaz


# ---------------------------------------------------------------------------
# Pass 2 — heuristic proper-noun candidates
# ---------------------------------------------------------------------------
_ROLE_WORDS = {
    "president", "prime", "minister", "chancellor", "premier", "king", "queen",
    "sheikh", "emir", "pope", "senator", "governor", "mayor", "chief", "ceo",
    "chairman", "chairwoman", "secretary", "ambassador", "general", "admiral",
    "commander", "spokesman", "spokeswoman", "leader", "envoy", "director",
}
_ORG_SUFFIX = {
    "inc", "corp", "corporation", "company", "co", "ltd", "plc", "group",
    "holdings", "bank", "agency", "ministry", "department", "commission",
    "council", "authority", "university", "institute", "foundation", "party",
    "airlines", "motors", "technologies", "systems", "energy", "capital",
    # armed-service and state-body heads: "Marine Corps", "Central Command",
    # "Revolutionary Guard" are institutions, and typing them as people is the
    # single most misleading thing the heuristic can do to a conflict graph.
    "corps", "command", "forces", "force", "guard", "army", "navy", "police",
    "ministry", "bureau", "service", "agency", "administration", "directorate",
    "cabinet", "parliament", "senate", "assembly", "court", "tribunal",
    "office", "finance", "council", "board", "union", "association", "federation",
}
# Geographic head nouns. "Middle East", "Camp David" and "San Diego" are places
# an event happens at, and typing them as people makes the graph nonsense.
_GEO_WORDS = {
    "east", "west", "north", "south", "eastern", "western", "northern",
    "southern", "sea", "ocean", "gulf", "bay", "strait", "canal", "river",
    "lake", "valley", "mountain", "mountains", "peak", "desert", "island",
    "islands", "coast", "peninsula", "province", "region", "territory",
    "district", "county", "city", "town", "village", "port", "harbour",
    "harbor", "beach", "border", "plateau", "delta", "basin", "camp", "base",
    "airport", "station", "bridge", "dam", "park", "square", "street",
    "avenue", "road", "highway", "san", "los", "las", "new", "fort", "saint",
    "st", "cape", "bank", "hills", "heights", "plains", "sur", "del",
}
# News outlets. They are the SOURCE of an article, not an actor inside it —
# leaving them in makes every outlet a top-ranked graph node purely because
# other outlets cite it. Built from the confidence table so the two never drift.
_OUTLET_WORDS = {
    "post", "times", "journal", "herald", "tribune", "gazette", "chronicle",
    "observer", "telegraph", "guardian", "mirror", "express", "standard",
    "review", "press", "wire", "news", "daily", "weekly", "magazine",
    "broadcasting", "network", "channel", "media", "reuters", "bloomberg",
    "jazeera", "cnn", "bbc", "npr", "cnbc", "msnbc", "afp", "pti", "ani",
}
# Words that make a capitalized phrase an occurrence, not an actor. Without
# this "World Cup" and "Winter Olympics" get typed as people.
_EVENT_WORDS = {
    "cup", "games", "olympics", "olympic", "championship", "championships",
    "summit", "election", "elections", "conference", "forum", "assembly",
    "congress", "war", "crisis", "accord", "accords", "treaty", "protocol",
    "agreement", "day", "festival", "open", "series", "final", "finals",
    "league", "tournament", "expo", "referendum", "census",
}
_CAP_SEQ = re.compile(r"\b([A-Z][a-z’'\-]{1,}(?:\s+(?:of|the|and|for|al|bin|de|van|von)\s+|\s+)?){1,4}")
_STOP_CAP = {
    "The", "A", "An", "This", "That", "These", "Those", "But", "And", "Or",
    "If", "When", "Where", "What", "Why", "How", "Who", "After", "Before",
    "During", "Amid", "Live", "Breaking", "Update", "Updates", "Exclusive",
    "Analysis", "Opinion", "Watch", "Video", "New", "Latest", "Report",
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday",
    "Sunday", "January", "February", "March", "April", "May", "June", "July",
    "August", "September", "October", "November", "December", "US", "UK", "EU",
}


def _is_title_case(text: str) -> bool:
    words = [w for w in re.findall(r"[A-Za-z']+", text or "") if len(w) > 3]
    if len(words) < 4:
        return False
    caps = sum(1 for w in words if w[0].isupper())
    return caps / len(words) > 0.65


def heuristic_entities(text: str, known: set[str]) -> list[dict]:
    """Capitalized phrases that look like a person or organization.

    Skipped entirely on Title Case headlines, where capitalization carries no
    information and this would return every noun in the sentence.
    """
    if _is_title_case(text):
        return []
    out: dict[str, dict] = {}
    for match in _CAP_SEQ.finditer(text or ""):
        phrase = match.group(0).strip(" ,.;:—-")
        # "Infantino's" and "Infantino" must land on the same node, so the
        # possessive is stripped before the id is derived rather than after.
        words = [re.sub(r"[’']s?$", "", w) for w in phrase.split()]
        words = [w for w in words if w]
        while words and words[0] in _STOP_CAP:
            words = words[1:]
        while words and words[-1] in _STOP_CAP:
            words = words[:-1]
        if len(words) < 2 or len(words) > 5:
            continue
        phrase = " ".join(words)
        if len(phrase) < 6 or phrase.lower() in known:
            continue
        # A demonym or country alias in first position means this is a modified
        # noun phrase ("Iranian Revolutionary Guard", "Illicit Iranian"), not a
        # name starting there — the gazetteer already recorded the country, and
        # what follows is rarely a clean entity.
        if onto.iso_for(words[0]):
            continue
        # Residual mojibake, and grammatical fragments the capitalization
        # heuristic can't distinguish from names: gerund modifiers ("Reflecting
        # Pool") and possessives ("Spain's Ceuta", where the real entity is the
        # second word and the gazetteer already has the first).
        if any(c in phrase for c in ("�", "", "", "")):
            continue
        if words[0].lower().endswith("ing") or "'" in words[0] or "’" in words[0]:
            continue
        low = phrase.lower()
        tail = words[-1].lower().strip(".")
        lower_words = {w.lower().strip(".") for w in words}
        if lower_words & _OUTLET_WORDS:
            continue
        if lower_words & _EVENT_WORDS:
            etype = "event"
        elif lower_words & _GEO_WORDS:
            etype = "location"
        elif tail in _ORG_SUFFIX or (lower_words & _ORG_SUFFIX):
            etype = "organization"
        elif any(w.lower() in _ROLE_WORDS for w in words):
            continue  # "Prime Minister" alone is a role, not an entity
        elif len(words) in (2, 3) and all(w[0].isupper() for w in words):
            etype = "person"
        else:
            continue
        eid = entity_id(etype, phrase)
        rec = out.setdefault(eid, {"id": eid, "type": etype, "name": phrase,
                                   "count": 0, "provisional": True})
        rec["count"] += 1
        known.add(low)
    return list(out.values())


# ---------------------------------------------------------------------------
# Deterministic relationship patterns
#
# A small set of verb frames that carry a specific ontology relation. Weak
# individually; useful because they run on every article for free and give the
# graph typed edges even when no model is reachable.
# ---------------------------------------------------------------------------
_REL_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bsanction(?:s|ed|ing)?\b", re.I), "sanctions"),
    (re.compile(r"\b(?:strike[sd]?|attack(?:s|ed)?|bomb(?:s|ed|ing)?|shell(?:s|ed|ing)?|invad(?:e|es|ed|ing))\b", re.I), "in_conflict"),
    (re.compile(r"\b(?:accus(?:e|es|ed)|blame[sd]?|condemn(?:s|ed)?)\b", re.I), "accuses"),
    (re.compile(r"\b(?:meet[s]?|met|talks with|negotiat(?:e|es|ed|ing)|summit)\b", re.I), "negotiating"),
    (re.compile(r"\b(?:back[s]?|backed|support[s]?|supported|aid(?:s|ed)? to|arms? to)\b", re.I), "supports"),
    (re.compile(r"\b(?:suppl(?:y|ies|ied)|ship(?:s|ped)? to|export(?:s|ed)? to)\b", re.I), "supplies"),
    (re.compile(r"\b(?:ally|allies|alliance|joint|partnership|pact)\b", re.I), "allied_with"),
    (re.compile(r"\b(?:invest(?:s|ed|ment)?|stake in|acquir(?:e|es|ed))\b", re.I), "invests_in"),
]


def infer_relation(text: str) -> str:
    for pattern, rel in _REL_PATTERNS:
        if pattern.search(text or ""):
            return rel
    return "co_mentioned"


# ---------------------------------------------------------------------------
# The deterministic pipeline stage
# ---------------------------------------------------------------------------
def analyze(article: dict) -> dict:
    """Attach entities, countries, domains and the inferred relation to one
    article, in place. Returns the article."""
    text = (article.get("title") or "") + ". " + (article.get("summary") or "")
    ents = gazetteer().match(text)
    known = {e["name"].lower() for e in ents}
    ents.extend(heuristic_entities(text, known))
    # Rank by mention count then ontology rank so the UI shows the anchor
    # entities first rather than whichever matched earliest.
    ents.sort(key=lambda e: (-e["count"],
                             onto.TYPES.get(e["type"], {}).get("rank", 9)))
    article["entities"] = ents[:14]
    article["countries"] = [e["id"].split(":", 1)[1] for e in ents
                            if e["type"] == "country"][:8]
    article["domains"] = classify_domains(text, article.get("domains"))
    article["relation"] = infer_relation(text)
    return article


def analyze_all(articles: list[dict], corroborate_min: int = 2) -> list[dict]:
    for art in articles:
        try:
            analyze(art)
        except Exception:
            art.setdefault("entities", [])
            art.setdefault("countries", [])
            art.setdefault("domains", ["news"])
    return corroborate(articles, corroborate_min)


def corroborate(articles: list[dict], min_articles: int = 2) -> list[dict]:
    """Drop heuristic (provisional) entities that only one article supports.

    The capitalization heuristic has to be permissive to catch a minister nobody
    had heard of yesterday, which means it also proposes phrases like "Illicit
    Iranian" and "Northern Command Base". Requiring a candidate to show up in at
    least two independent articles is what separates the two, and it needs the
    whole batch to decide — hence a second pass rather than a smarter regex.

    Gazetteer entities are never touched: those are known objects, and a country
    mentioned once is still that country.
    """
    support: dict[str, set[str]] = {}
    for art in articles:
        for ent in art.get("entities") or []:
            if ent.get("provisional"):
                support.setdefault(ent["id"], set()).add(art.get("id", ""))
    doomed = {eid for eid, arts in support.items() if len(arts) < min_articles}
    if not doomed:
        return articles
    for art in articles:
        ents = art.get("entities") or []
        if any(e["id"] in doomed for e in ents):
            art["entities"] = [e for e in ents if e["id"] not in doomed]
    return articles


# ---------------------------------------------------------------------------
# Pass 3 — LLM relationship extraction
# ---------------------------------------------------------------------------
_EXTRACT_SYSTEM = (
    "You are an intelligence analyst building a knowledge graph from news "
    "headlines. For each numbered headline, extract the real-world "
    "relationships it asserts.\n\n"
    "Rules:\n"
    "- Use ONLY these relation types: " + ", ".join(onto.EXTRACTABLE) + "\n"
    "- Use ONLY these entity types: country, organization, government, person, "
    "location, infrastructure, commodity, asset, event\n"
    "- Use the full formal name of each entity (\"United States\", not \"US\"; "
    "\"Vladimir Putin\", not \"Putin\").\n"
    "- Extract only what the headline actually asserts. Do NOT add background "
    "knowledge, and do NOT invent entities that are not referenced.\n"
    "- If a headline asserts no relationship between two entities, skip it.\n\n"
    "Return JSON: {\"triples\":[{\"n\":<headline number>,\"s\":\"subject\","
    "\"st\":\"subject type\",\"r\":\"relation\",\"o\":\"object\","
    "\"ot\":\"object type\"}]}"
)


def llm_relations(articles: list[dict], batch: int = 12,
                  max_batches: int = 4) -> list[dict]:
    """Typed triples for the strongest articles. [] if no model is reachable.

    Only the highest-severity recent articles are sent: the marginal value of
    graphing a routine sports headline does not justify the tokens, and the
    deterministic pass has already covered every article anyway.
    """
    from ..squad.base import MODEL_SMART, run_llm_json

    ranked = sorted(articles, key=lambda a: -(a.get("severity", 0) or 0))
    ranked = [a for a in ranked if len(a.get("entities") or []) >= 2]
    ranked = ranked[: batch * max_batches]
    if not ranked:
        return []

    chunks = [ranked[i:i + batch] for i in range(0, len(ranked), batch)]

    def extract_chunk(chunk: list[dict]):
        lines = [f"{i}. {art['title']}" for i, art in enumerate(chunk, start=1)]
        return chunk, run_llm_json(MODEL_SMART, _EXTRACT_SYSTEM,
                                   "\n".join(lines), temperature=0.1,
                                   default=None)

    # Batches are independent, so they go out concurrently. Sequentially this
    # was ~22s per batch and dominated the whole refresh; the cloud ladder
    # handles four at once without contention.
    from concurrent.futures import ThreadPoolExecutor

    out: list[dict] = []
    with ThreadPoolExecutor(max_workers=min(4, len(chunks))) as pool:
        results = list(pool.map(extract_chunk, chunks))

    for chunk, payload in results:
        # The prompt asks for {"triples":[...]} and the model frequently returns
        # the bare array instead. Both are accepted rather than tightening the
        # prompt further: a stricter instruction does not stop it, and rejecting
        # the array shape silently discarded every relationship the pass found.
        if isinstance(payload, list):
            triples = payload
        elif isinstance(payload, dict):
            triples = payload.get("triples") or payload.get("relationships") or []
        else:
            continue
        if not isinstance(triples, list):
            continue
        for tri in triples[: batch * 4]:
            if not isinstance(tri, dict):
                continue
            try:
                n = int(tri.get("n", 0))
            except (TypeError, ValueError):
                continue
            if not (1 <= n <= len(chunk)):
                continue
            subj, obj = str(tri.get("s", "")).strip(), str(tri.get("o", "")).strip()
            if not subj or not obj or subj.lower() == obj.lower():
                continue
            out.append({
                "article": chunk[n - 1]["id"],
                "subject": subj,
                "subject_type": _coerce_type(tri.get("st"), subj),
                "relation": onto.relation_ok(tri.get("r")),
                "object": obj,
                "object_type": _coerce_type(tri.get("ot"), obj),
            })
    return out


def _coerce_type(raw, name: str) -> str:
    t = str(raw or "").strip().lower()
    if t in onto.TYPES:
        # A model calling something a country when the gazetteer knows it isn't
        # is a common failure; the gazetteer wins.
        if t == "country" and not onto.iso_for(name):
            return "organization"
        return t
    return "country" if onto.iso_for(name) else "organization"


def summarize_entities(articles: list[dict], limit: int = 40) -> list[dict]:
    """Most-mentioned entities across a set of articles, for panel headers."""
    counts: Counter = Counter()
    meta: dict[str, dict] = {}
    for art in articles:
        for ent in art.get("entities") or []:
            counts[ent["id"]] += ent.get("count", 1)
            meta.setdefault(ent["id"], ent)
    out = []
    for eid, n in counts.most_common(limit):
        rec = dict(meta[eid])
        rec["mentions"] = n
        out.append(rec)
    return out


__all__ = ["analyze", "analyze_all", "llm_relations", "classify_domains",
           "gazetteer", "entity_id", "slug", "DOMAINS", "summarize_entities",
           "tokens"]
