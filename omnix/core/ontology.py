"""The OMNIX object ontology — extensible, and deliberately two-layered.

This is `terra/ontology.py` generalised. TERRA proved the important idea: keep
the *reasoning* vocabulary (what a thing is) separate from the *presentation*
vocabulary (how it draws). Types are written into every persisted row and so
must stay stable forever; families are a rendering concern and are free to group
several types together or split one apart.

That split is why adding an object type is cheap. A new type declares which
family it draws as, and the graph renderer, the inspector and the legend all
pick it up with no change. Nothing in the UI switches on a hard-coded type list,
which is the explicit requirement in the workspace spec.

Three intelligence domains coexist in one graph, and the domain is recorded so
views can filter without guessing from the type name:

    external   the world  — countries, companies, people, events, markets
    internal   the user's — projects, repositories, files, documents, tasks
    ai         derived    — claims, findings, hypotheses, recommendations

An unknown type is never an error. It degrades to the `thing` family so a
mis-typed extraction shows up as a plain node rather than crashing a view.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Visual families
# ---------------------------------------------------------------------------
# The first nine are TERRA's, reproduced with identical glyph/colour/shape/
# weight so a geopolitical workspace drawn through this registry is pixel-wise
# indistinguishable from TERRA today. They must stay in step with
# `terra/ontology.VISUAL` until TERRA is migrated to read from here; the
# `families_match_terra()` helper at the bottom asserts that cheaply.
#
# `shape` maps to the renderer's point shapes. `weight` is a size multiplier so
# a country reads as more massive than a single news story at equal degree.
FAMILIES: dict[str, dict] = {
    # -- TERRA's nine ------------------------------------------------------
    "country":  {"label": "Countries",             "glyph": "◈", "color": "#c9a45c",
                 "shape": "square",   "weight": 1.45, "ring": True},
    "person":   {"label": "People",                "glyph": "◉", "color": "#ff9a62",
                 "shape": "circle",   "weight": 1.00, "ring": False},
    "org":      {"label": "Organizations",         "glyph": "▣", "color": "#57d7ff",
                 "shape": "diamond",  "weight": 1.15, "ring": False},
    "company":  {"label": "Companies",             "glyph": "▰", "color": "#5eead4",
                 "shape": "diamond",  "weight": 1.10, "ring": False},
    "event":    {"label": "Events",                "glyph": "✦", "color": "#ff5d7a",
                 "shape": "triangle", "weight": 0.95, "ring": False},
    "conflict": {"label": "Conflicts",             "glyph": "⚔", "color": "#ff3355",
                 "shape": "triangle", "weight": 1.25, "ring": True},
    "place":    {"label": "Locations",             "glyph": "◇", "color": "#4ade80",
                 "shape": "circle",   "weight": 0.90, "ring": False},
    "story":    {"label": "News Stories",          "glyph": "▤", "color": "#e8cd8b",
                 "shape": "square",   "weight": 0.85, "ring": False},
    "economic": {"label": "Economic Indicators",   "glyph": "◫", "color": "#a3e635",
                 "shape": "square",   "weight": 1.00, "ring": False},

    # -- new: the workspace's own material --------------------------------
    # Hues are chosen to stay legible against #060606 next to the gold accent,
    # and to keep the three domains distinguishable at a glance: internal work
    # trends violet/blue, evidence trends warm-neutral, risk stays red-adjacent.
    "tech":     {"label": "Technologies",          "glyph": "◈", "color": "#c084fc",
                 "shape": "diamond",  "weight": 1.05, "ring": False},
    "product":  {"label": "Products",              "glyph": "▮", "color": "#f0abfc",
                 "shape": "square",   "weight": 1.00, "ring": False},
    "work":     {"label": "Projects & Tasks",      "glyph": "◐", "color": "#8b9dff",
                 "shape": "circle",   "weight": 1.10, "ring": False},
    "code":     {"label": "Code",                  "glyph": "⬡", "color": "#7dd3fc",
                 "shape": "diamond",  "weight": 0.95, "ring": False},
    "doc":      {"label": "Documents",             "glyph": "▥", "color": "#d6d3d1",
                 "shape": "square",   "weight": 0.90, "ring": False},
    "evidence": {"label": "Evidence",              "glyph": "❖", "color": "#fbbf24",
                 "shape": "triangle", "weight": 0.85, "ring": False},
    "risk":     {"label": "Findings",              "glyph": "⬢", "color": "#fb7185",
                 "shape": "triangle", "weight": 1.05, "ring": True},
    "dataset":  {"label": "Datasets",              "glyph": "▦", "color": "#67e8f9",
                 "shape": "square",   "weight": 0.95, "ring": False},

    # Fallback. Never remove: unknown types resolve here.
    "thing":    {"label": "Other",                 "glyph": "○", "color": "#8b8578",
                 "shape": "circle",   "weight": 0.85, "ring": False},
}

DOMAINS = ("external", "internal", "ai")


@dataclass(frozen=True)
class ObjectType:
    """One registered object type.

    `geo` marks types that can legitimately carry coordinates, which is what
    decides Map eligibility. Marking a type geo does not promise every instance
    has a location — only that a location would be meaningful.
    """
    key: str
    label: str
    family: str
    domain: str = "external"
    geo: bool = False
    temporal: bool = False          # belongs on the Timeline in its own right
    rank: int = 50                  # lower sorts first in legends and pickers
    aliases: tuple[str, ...] = field(default_factory=tuple)

    @property
    def visual(self) -> dict:
        return FAMILIES.get(self.family, FAMILIES["thing"])


def _t(key, label, family, domain="external", **kw) -> ObjectType:
    return ObjectType(key=key, label=label, family=family, domain=domain, **kw)


# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------
# Seeded with the spec's initial types plus TERRA's, which must keep resolving
# because existing persisted nodes carry them. Extend with `register()`.
_TYPES: dict[str, ObjectType] = {}


def register(t: ObjectType) -> ObjectType:
    """Add or replace a type. Idempotent, so modules can register at import."""
    if t.family not in FAMILIES:
        raise ValueError(f"unknown visual family {t.family!r} for type {t.key!r}")
    if t.domain not in DOMAINS:
        raise ValueError(f"unknown domain {t.domain!r} for type {t.key!r}")
    _TYPES[t.key] = t
    for alias in t.aliases:
        _ALIASES[alias] = t.key
    return t


_ALIASES: dict[str, str] = {}

for _t_ in (
    # -- external: TERRA's vocabulary, unchanged ---------------------------
    _t("country", "Country", "country", geo=True, rank=1),
    _t("government", "Government Body", "org", rank=2),
    _t("organization", "Organization", "org", rank=3),
    _t("company", "Company", "company", rank=3),
    _t("person", "Person", "person", rank=4),
    _t("event", "Event", "event", temporal=True, rank=5),
    _t("conflict", "Conflict", "conflict", geo=True, temporal=True, rank=5),
    _t("news_story", "News Story", "story", temporal=True, rank=5),
    _t("location", "Location", "place", geo=True, rank=6),
    _t("infrastructure", "Infrastructure", "place", geo=True, rank=7),
    _t("commodity", "Commodity", "economic", rank=8),
    _t("asset", "Market Asset", "economic", rank=9),
    _t("economic_indicator", "Economic Indicator", "economic", rank=9),

    # -- external: the rest of the world the spec names --------------------
    _t("technology", "Technology", "tech", rank=10),
    _t("product", "Product", "product", rank=11),
    _t("website", "Website", "doc", rank=12, aliases=("site", "url")),
    _t("market", "Market", "economic", rank=13),
    _t("regulation", "Regulation", "doc", temporal=True, rank=14),

    # -- internal: the user's own material ---------------------------------
    _t("project", "Project", "work", domain="internal", rank=20),
    _t("repository", "Repository", "code", domain="internal", rank=21),
    _t("service", "Service", "code", domain="internal", rank=22),
    _t("module", "Module", "code", domain="internal", rank=23),
    _t("file", "File", "code", domain="internal", rank=24),
    _t("code_component", "Code Component", "code", domain="internal", rank=24),
    _t("api", "API", "code", domain="internal", rank=25),
    _t("test", "Test", "code", domain="internal", rank=26),
    _t("dependency", "Dependency", "code", domain="internal", rank=27),
    _t("document", "Document", "doc", domain="internal", rank=30),
    _t("article", "Article", "doc", temporal=True, rank=31),
    _t("note", "Note", "doc", domain="internal", rank=32),
    _t("dataset", "Dataset", "dataset", domain="internal", rank=33),
    _t("task", "Task", "work", domain="internal", temporal=True, rank=34),
    _t("decision", "Decision", "work", domain="internal", temporal=True, rank=35),
    _t("requirement", "Requirement", "work", domain="internal", rank=36),
    _t("model", "Model", "tech", domain="internal", rank=37),

    # -- ai: derived intelligence ------------------------------------------
    # These are the types that make provenance non-negotiable. A claim is not a
    # fact; a finding is not a vulnerability until something reproduces it.
    _t("claim", "Claim", "evidence", domain="ai", rank=40),
    _t("source", "Source", "evidence", domain="ai", rank=41),
    _t("research", "Research", "doc", domain="ai", rank=42),
    _t("security_finding", "Security Finding", "risk", domain="ai",
       rank=43, aliases=("finding",)),
    _t("hypothesis", "Hypothesis", "evidence", domain="ai", rank=44),
    _t("recommendation", "Recommendation", "evidence", domain="ai", rank=45),
    _t("conflict_of_evidence", "Evidence Conflict", "risk", domain="ai", rank=46),
):
    register(_t_)


UNKNOWN = ObjectType(key="thing", label="Other", family="thing", domain="external",
                     rank=99)


def resolve(type_key: str) -> ObjectType:
    """Type for a key, tolerating aliases and unknowns.

    Never raises. An extraction that invents a type must not be able to break a
    view — it becomes a plain node instead.
    """
    key = (type_key or "").strip().lower().replace(" ", "_").replace("-", "_")
    if key in _TYPES:
        return _TYPES[key]
    if key in _ALIASES:
        return _TYPES[_ALIASES[key]]
    return UNKNOWN


def known(type_key: str) -> bool:
    key = (type_key or "").strip().lower().replace(" ", "_").replace("-", "_")
    return key in _TYPES or key in _ALIASES


def types(domain: str | None = None) -> list[ObjectType]:
    out = [t for t in _TYPES.values() if domain is None or t.domain == domain]
    return sorted(out, key=lambda t: (t.rank, t.label))


def visual_of(type_key: str) -> dict:
    return resolve(type_key).visual


# ---------------------------------------------------------------------------
# Relations
# ---------------------------------------------------------------------------
# TERRA's seventeen, plus the workspace's own. The discipline that matters is
# `relation_ok()`: an extractor that emits something unrecognised gets it
# coerced to `related_to` rather than minting a new edge type. An ontology that
# grows itself at runtime has stopped being an ontology.
RELATIONS: dict[str, dict] = {
    # TERRA's — values identical to terra/ontology.RELATIONS
    "located_in":    {"label": "located in",        "symmetric": False, "weight": 1.0},
    "leads":         {"label": "leads",             "symmetric": False, "weight": 1.4},
    "member_of":     {"label": "member of",         "symmetric": False, "weight": 1.0},
    "allied_with":   {"label": "allied with",       "symmetric": True,  "weight": 1.2},
    "in_conflict":   {"label": "in conflict with",  "symmetric": True,  "weight": 1.8},
    "sanctions":     {"label": "sanctions",         "symmetric": False, "weight": 1.6},
    "trades_with":   {"label": "trades with",       "symmetric": True,  "weight": 1.0},
    "supplies":      {"label": "supplies",          "symmetric": False, "weight": 1.2},
    "invests_in":    {"label": "invests in",        "symmetric": False, "weight": 1.0},
    "negotiating":   {"label": "negotiating with",  "symmetric": True,  "weight": 1.2},
    "accuses":       {"label": "accuses",           "symmetric": False, "weight": 1.2},
    "supports":      {"label": "supports",          "symmetric": False, "weight": 1.1},
    "affected_by":   {"label": "affected by",       "symmetric": False, "weight": 1.3},
    "involved_in":   {"label": "involved in",       "symmetric": False, "weight": 1.0},
    "produces":      {"label": "produces",          "symmetric": False, "weight": 1.0},
    "depends_on":    {"label": "depends on",        "symmetric": False, "weight": 1.3},
    "co_mentioned":  {"label": "co-mentioned",      "symmetric": True,  "weight": 0.4},

    # Commercial / competitive — the analyst persona
    "competes_with": {"label": "competes with",     "symmetric": True,  "weight": 1.3},
    "partners_with": {"label": "partners with",     "symmetric": True,  "weight": 1.2},
    "customer_of":   {"label": "customer of",       "symmetric": False, "weight": 1.2},
    "acquired":      {"label": "acquired",          "symmetric": False, "weight": 1.5},
    "subsidiary_of": {"label": "subsidiary of",     "symmetric": False, "weight": 1.4},
    "uses":          {"label": "uses",              "symmetric": False, "weight": 1.0},

    # Evidence — the spine of the Claim Ledger and Evidence Graph
    "supported_by":    {"label": "supported by",    "symmetric": False, "weight": 1.5},
    "contradicted_by": {"label": "contradicted by", "symmetric": False, "weight": 1.5},
    "cites":           {"label": "cites",           "symmetric": False, "weight": 1.0},
    "derived_from":    {"label": "derived from",    "symmetric": False, "weight": 1.2},
    "about":           {"label": "about",           "symmetric": False, "weight": 1.1},

    # Software — FORGE's vocabulary, registered now so the graph is ready
    "imports":       {"label": "imports",           "symmetric": False, "weight": 1.0},
    "calls":         {"label": "calls",             "symmetric": False, "weight": 1.0},
    "implements":    {"label": "implements",        "symmetric": False, "weight": 1.3},
    "verifies":      {"label": "verifies",          "symmetric": False, "weight": 1.3},
    "affects":       {"label": "affects",           "symmetric": False, "weight": 1.3},
    "contains":      {"label": "contains",          "symmetric": False, "weight": 0.9},

    # Work
    "blocks":        {"label": "blocks",            "symmetric": False, "weight": 1.2},
    "assigned_to":   {"label": "assigned to",       "symmetric": False, "weight": 1.0},

    # Catch-all. The coercion target, so it must always exist.
    "related_to":    {"label": "related to",        "symmetric": True,  "weight": 0.5},
}

# What an LLM extractor may emit. `co_mentioned` and `related_to` are excluded:
# they are what we fall back *to*, and letting a model choose them directly
# means it stops trying to name the actual relationship.
EXTRACTABLE = tuple(r for r in RELATIONS if r not in ("co_mentioned", "related_to"))


def relation_ok(rel: str) -> str:
    r = (rel or "").strip().lower().replace(" ", "_").replace("-", "_")
    return r if r in RELATIONS else "related_to"


def relation_label(rel: str) -> str:
    return RELATIONS.get(rel, RELATIONS["related_to"])["label"]


def is_symmetric(rel: str) -> bool:
    return bool(RELATIONS.get(rel, RELATIONS["related_to"])["symmetric"])


def relation_weight(rel: str) -> float:
    return float(RELATIONS.get(rel, RELATIONS["related_to"])["weight"])


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------
# The spec is explicit: a user must be able to tell where OMNIX got something,
# and AI-inferred relationships must never look like verified facts. Provenance
# is therefore mandatory on every object, relationship and event, and the
# default is the *weakest* value rather than the most convenient one.
PROVENANCE = {
    "user_created":  {"label": "User created",  "rank": 0, "trust": "asserted"},
    "verified":      {"label": "Verified",      "rank": 1, "trust": "checked"},
    "source_backed": {"label": "Source-backed", "rank": 2, "trust": "cited"},
    "ai_inferred":   {"label": "AI-inferred",   "rank": 3, "trust": "unverified"},
}
DEFAULT_PROVENANCE = "ai_inferred"


def provenance_ok(value: str) -> str:
    v = (value or "").strip().lower()
    return v if v in PROVENANCE else DEFAULT_PROVENANCE


def describe() -> dict:
    """Everything a client needs to render the ontology without hard-coding it.

    The workspace frontend builds its legend, type filters, relation filters and
    inspector sections from this one payload — which is what keeps the UI from
    growing a duplicate copy of the type list.
    """
    return {
        "families": FAMILIES,
        "domains": list(DOMAINS),
        "types": [
            {"key": t.key, "label": t.label, "family": t.family,
             "domain": t.domain, "geo": t.geo, "temporal": t.temporal,
             "rank": t.rank, **{k: v for k, v in t.visual.items() if k != "label"}}
            for t in types()
        ],
        "relations": [
            {"key": k, "label": v["label"], "symmetric": v["symmetric"],
             "weight": v["weight"], "extractable": k in EXTRACTABLE}
            for k, v in RELATIONS.items()
        ],
        "provenance": PROVENANCE,
    }


def families_match_terra() -> list[str]:
    """Report families that have drifted from TERRA's originals.

    Not called at import — TERRA is a heavier import than core wants, and the
    dependency runs the wrong way. Used by the smoke check, so drift surfaces
    when someone edits one copy and not the other.
    """
    try:
        from ..terra import ontology as terra_onto
    except Exception:
        return []
    drift = []
    for key, want in terra_onto.VISUAL.items():
        got = FAMILIES.get(key)
        if got is None:
            drift.append(f"{key}: missing from core")
            continue
        for prop in ("glyph", "color", "shape", "weight", "ring"):
            if got.get(prop) != want.get(prop):
                drift.append(f"{key}.{prop}: core={got.get(prop)!r} terra={want.get(prop)!r}")
    return drift
