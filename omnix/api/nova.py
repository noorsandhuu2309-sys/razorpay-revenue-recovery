"""NOVA command layer, research, evidence and the intelligence brief.

NOVA stops being a chat destination here. The workspace has one input, and what
the user typed plus what they have *selected* decides where it goes:

    direct        a plain answer, no workspace involvement
    query         answered from the graph — a traversal, not a model call
    research      ORACLE, whose output is written back into the graph
    agent         a single existing unit
    workflow      a compiled multi-step DAG

The `query` branch matters more than it looks. "What connects these?" and "find
everything related to authentication" are graph operations: answering them from
the database is faster, free, and — unlike a model — actually correct about
what the workspace contains.
"""

from __future__ import annotations

import re
import threading

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ..core import conversation
from ..core import objects as objects_mod
from ..core import ontology as onto
from ..core import research_ingest, tracking
from ..core import workspace as workspace_mod
from ..graph import engine, sql

router = APIRouter(prefix="/api", tags=["nova"])


def _ws(workspace: str | None) -> str:
    return workspace_mod.resolve(workspace)


# ---------------------------------------------------------------------------
# Implicit grounding
# ---------------------------------------------------------------------------
# The workspace's worst first impression: a new user asks "what is driving the
# tension between the United States and Iran?", nothing is selected, so
# `_context_summary` returns "" and the system prompt's "never invent facts
# about objects you were not given" correctly produces a refusal — while the
# Space holds 644 objects including both countries.
#
# Selecting first is a step nobody arriving from a chat app knows to take, and
# teaching it costs more than resolving it here. So when the user selects
# nothing, find what they are plainly talking about.
#
# This is name matching against objects that actually exist, NOT semantic
# search: `search_objects` takes a substring, and a whole question is never a
# substring of anything. Matching the other way round — do this Space's object
# names appear in the sentence — is exact, cheap over a few hundred nodes, and
# cannot invent an entity the workspace does not hold.

_STOP_NAMES = {
    # Names common enough to match nearly any sentence and ground the answer in
    # noise rather than in its subject.
    "news", "report", "reports", "update", "updates", "world", "other",
    "new", "data", "time", "state", "states", "group", "market", "markets",
}

_MIN_NAME_LEN = 3


def _norm_words(text: str) -> str:
    """Lowercase, every non-alphanumeric run collapsed to one space, padded at
    both ends — so `" iran "` can be tested with `in` and cannot match inside
    `"iranian"`."""
    cleaned = "".join(ch if ch.isalnum() else " " for ch in (text or "").lower())
    return " " + " ".join(cleaned.split()) + " "


def _aliases(name: str) -> list[str]:
    """Surface forms a user might actually type for this object.

    Formal names in the graph are rarely what people write: the object is
    "United States of America" and the question says "the United States". So a
    name also matches on the head of an "X of Y" construction and on whatever
    sits outside a parenthetical. Anything shorter than four characters is
    dropped — two-letter heads match everything and ground the answer in noise.
    """
    out = [name]
    base = name.split("(")[0].strip()
    if base and base != name:
        out.append(base)
    low = base.lower()
    for sep in (" of ", " of the "):
        if sep in low:
            head = base[:low.index(sep)].strip()
            if head:
                out.append(head)
            break
    seen: list[str] = []
    for a in out:
        if len(a) >= 4 and a.lower() not in {s.lower() for s in seen}:
            seen.append(a)
    return seen


def resolve_mentions(workspace_id: str, text: str, limit: int = 6) -> list[dict]:
    """Objects from this Space whose names appear in `text`.

    Longest match wins: with "United States of America" and "America" both in
    the graph, grounding on the specific one and dropping the substring keeps
    the context from carrying two rows for the same subject.
    """
    hay = _norm_words(text)
    if not hay.strip():
        return []

    g = sql.load(workspace_id)
    hits: list[tuple[int, float, str, dict]] = []
    for nid in g.all_ids():
        node = g.node(nid)
        if not node:
            continue
        name = (node.get("name") or "").strip()
        if len(name) < _MIN_NAME_LEN or name.lower() in _STOP_NAMES:
            continue
        # Score on the longest alias that hit, so a full-name match still
        # outranks a head-of-phrase match on a different object.
        best = 0
        for alias in _aliases(name):
            if _norm_words(alias).rstrip() + " " in hay and len(alias) > best:
                best = len(alias)
        if best:
            hits.append((best, float(node.get("salience") or 0.0), name, node))

    # Longest name first, then most salient; then drop any hit whose name is
    # contained in one already kept.
    hits.sort(key=lambda h: (-h[0], -h[1]))
    kept: list[dict] = []
    kept_names: list[str] = []
    for _, _, name, node in hits:
        low = name.lower()
        if any(low in k for k in kept_names):
            continue
        kept_names.append(low)
        kept.append(node)
        if len(kept) >= limit:
            break
    return kept


# ---------------------------------------------------------------------------
# Intent
# ---------------------------------------------------------------------------
# Hints match on WORD BOUNDARIES, never as bare substrings.
#
# Substring matching is the obvious first implementation and it is quietly
# wrong. Every one of these was a real misroute on the product's main path:
#
#     "What is Meta's latest AI model?"      -> spatial   ("eta" in "Meta")
#     "Research the beta launch in Japan"    -> spatial   ("eta" in "beta")
#     "Who is the Secretary of State?"       -> spatial   ("eta" in "Secretary")
#     "Summarise the report on exports"      -> forge     ("repo" in "report")
#     "What caused the Volkswagen scandal?"  -> sentinel  ("scan" in "scandal")
#     "Latest on India UPI expansion"        -> forge     ("test" in "latest")
#
# A misroute does not read as a routing bug to the user. It reads as the
# product being broken: you ask a research question and a code agent answers.
#
# `stems` are the deliberate prefix matches — "vulnerab" must still catch
# "vulnerable" and "vulnerability". Anything that would collide as a prefix
# belongs in `words` instead: "secret" as a stem fires on "Secretary of State",
# and "scan" as a stem fires on "scandal".


def _hints(words: tuple = (), stems: tuple = ()) -> "re.Pattern[str]":
    """Compile hint phrases into one word-boundary matcher."""
    alts = [re.escape(w) + r"\b" for w in words] + [re.escape(s) for s in stems]
    return re.compile(r"\b(?:" + "|".join(alts) + r")", re.I)


# "latest" and "current state" are here because the demo question -- "what is
# the current state of India UPI international expansion?" -- fell through
# every hint and was answered from the graph instead of researched.
_RESEARCH_RE = _hints(
    words=("find out", "look into", "latest", "current state", "compare",
           "who are", "market", "landscape"),
    stems=("research", "investigat", "analys", "analyz", "competitor"))
_QUERY_RE = _hints(
    words=("what connects", "how are", "related to", "connected",
           "show me everything", "find everything", "what do i have",
           "in my workspace", "path between", "relationship between"))
_SECURITY_RE = _hints(
    words=("scan", "scans", "scanning", "secret", "secrets", "security", "cve"),
    stems=("audit", "vulnerab"))

# Scaffolding words that carry no search signal. Everything here is either
# question furniture ("what", "do", "i") or a verb of asking ("show", "find") —
# never the subject of the question.
_QUERY_STOPWORDS = frozenset((
    "a", "an", "the", "about", "on", "of", "for", "in", "into", "my", "our",
    "me", "us", "i", "we", "you", "do", "does", "did", "is", "are", "was",
    "were", "be", "have", "has", "had", "know", "tell", "show", "give",
    "find", "get", "list", "see", "all", "everything", "anything", "any",
    "something", "what", "which", "who", "whom", "whose", "where", "when",
    "why", "how", "and", "or", "to", "from", "with", "please", "there",
    "workspace", "space", "graph", "related", "relating", "connected",
))
_CODE_RE = _hints(
    words=("repo", "repos", "codebase", "function", "functions",
           "bug", "bugs", "debug", "test", "tests"),
    stems=("repositor", "refactor", "implement"))

# TERRA's spatial layer. These are checked FIRST, before the research and query
# hints, because several of them collide: "compare" is a research hint, but
# "what's the fastest route" contains none of the graph vocabulary and "find the
# nearest hospital" would otherwise route to `query` on "find" and search the
# object graph for a hospital that was never researched.
#
# Two-stage on purpose. A hint word alone is not enough — "what is the weather
# like in the Taiwan Strait for shipping" is a geopolitical question about the
# corpus, not a request for a forecast at the user's position. So a spatial
# route requires a hint AND (a position, or an explicitly spatial verb).
_SPATIAL_HINTS = (
    "near me", "nearby", "around me", "around here", "close by", "closest",
    "nearest", "where am i", "my location", "current location",
    "take me to", "navigate to", "directions to", "route to", "how do i get to",
    "drive to", "walk to", "cycle to", "fastest route", "quickest route",
    "shortest route", "is there traffic", "eta", "how far is",
    "air quality", "aqi", "pollution",
    "remind me when i", "notify me when i", "tell me when i",
    "location history", "places i've visited", "places i have visited",
    "where have i been", "my saved places", "known locations",
    "geofence", "somewhere quiet", "quiet place", "place to work",
    "place to study", "elevation", "altitude",
)

#: Weather and "what's around" are spatial ONLY with a position. Without one
#: they are ordinary questions the model answers better than a provider.
_SPATIAL_NEEDS_POSITION = ("weather", "temperature", "raining", "forecast",
                           "what's around", "whats around", "what is around",
                           "should i go for a run", "should i go outside")

_SPATIAL_RE = _hints(words=_SPATIAL_HINTS)
_SPATIAL_NEEDS_POSITION_RE = _hints(words=_SPATIAL_NEEDS_POSITION)
_RELATIONAL_RE = _hints(words=("between", "difference", "differences"),
                        stems=("connect", "relat", "compar"))


def classify(text: str, selection: list[str],
             has_position: bool = False) -> str:
    t = (text or "").strip().lower()
    if not t:
        return "direct"
    if _SPATIAL_RE.search(t):
        return "spatial"
    if has_position and _SPATIAL_NEEDS_POSITION_RE.search(t):
        return "spatial"
    if _QUERY_RE.search(t):
        return "query"
    # With objects selected, a relational question is almost always about them.
    if selection and _RELATIONAL_RE.search(t):
        return "query"
    if _RESEARCH_RE.search(t):
        return "research"
    if _SECURITY_RE.search(t):
        return "agent:sentinel"
    if _CODE_RE.search(t):
        return "agent:forge"
    return "direct"


def _search_terms(text: str) -> str:
    """Reduce a natural-language request to the thing being asked about.

    `search_objects` is a substring match, so handing it the raw sentence is
    worse than useless: "find everything on Russia" becomes
    `name ILIKE '%find everything on Russia%'` and matches nothing while the
    Space holds a Russia object. The phrases that routed us here are COMMANDS,
    not subjects, so they come out first, then the ordinary question furniture.

    Falls back to the original words when stripping would leave nothing, so a
    question made entirely of stopwords still searches for something.
    """
    t = (text or "").strip().lower()
    for hint in _QUERY_HINTS:
        t = t.replace(hint, " ")
    words = [w for w in re.split(r"[^\w'-]+", t) if w]
    kept = [w for w in words if w not in _QUERY_STOPWORDS]
    return " ".join(kept or words)


def _search_objects_loosely(workspace_id: str, text: str) -> list[dict]:
    """Search for `text`, then for its individual words if that found nothing.

    A multi-word subject is still one substring to SQL, so "taiwan
    semiconductors" misses both objects that exist. Widening to per-word
    matches is what makes a two-noun question answerable at all; results keep
    the order the words were typed in, deduplicated by id.
    """
    terms = _search_terms(text)
    results = objects_mod.search_objects(workspace_id, terms, limit=25)
    if results or " " not in terms:
        return results
    seen: set[str] = set()
    merged: list[dict] = []
    for word in terms.split():
        for r in objects_mod.search_objects(workspace_id, word, limit=10):
            if r["id"] not in seen:
                seen.add(r["id"])
                merged.append(r)
    return merged[:25]


def _context_summary(workspace_id: str, ids: list[str]) -> str:
    """Hydrate selected object ids into text a model can actually use.

    This is what makes the Context Lens work: the user says "these", and the
    prompt receives names, types and relationships without them typing any of
    it.
    """
    if not ids:
        return ""
    parts = []
    g = sql.load(workspace_id)
    for oid in ids[:12]:
        obj = g.node(oid)
        if obj is None:
            continue
        rels = objects_mod.relationships_of(workspace_id, oid, limit=8)
        rel_txt = []
        for r in rels:
            other = g.node(r["dst"] if r["src"] == oid else r["src"])
            if other:
                rel_txt.append(f"{r['label']} {other['name']}")
        line = f"- {obj['name']} ({obj['typeLabel']})"
        if obj.get("description"):
            line += f": {obj['description'][:200]}"
        if rel_txt:
            line += f"\n  related: {'; '.join(rel_txt[:6])}"
        parts.append(line)
    return "Objects from the user's workspace:\n" + "\n".join(parts) if parts else ""


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------
@router.post("/nova/command")
def command(payload: dict):
    """Route one workspace command. Returns immediately.

    Long-running work (research, agents) returns an `executionId` the client
    follows over the existing SSE stream; nothing here blocks the request.
    """
    p = payload or {}
    text = (p.get("input") or "").strip()
    if not text:
        return JSONResponse({"error": "input is required"}, status_code=400)
    ws = _ws(p.get("workspace"))
    selection = [s for s in (p.get("selection") or []) if s]

    # Read the thread BEFORE recording this turn, so the history handed to the
    # model is what came before the question rather than including it twice.
    history = conversation.history_for_prompt(ws)
    conversation.add(ws, "user", text, context=selection)

    # A bare follow-up ("yes", "go on") classifies as nonsense on its own, so
    # intent is resolved against the conversation: if the last thing NOVA did
    # was answer directly, a short reply continues that answer rather than
    # being routed off to a search.
    #
    # Classification runs on what the USER actually selected, deliberately
    # BEFORE the implicit grounding below. Grounding first inverted the
    # routing: "what is driving the tension between the US and Iran?" resolved
    # both countries, `classify` then saw "between" with a non-empty selection,
    # and a plain question was answered with a graph traversal — "Traced 1
    # connection(s)" instead of an answer. Implicit context is there to inform
    # the reply, never to change where the request goes.
    # The client sends the browser's position when it has one and the user has
    # granted it. Absent is the normal case and must stay fully functional —
    # TERRA never sources a position server-side.
    position = p.get("position") or {}
    lat, lon = position.get("lat"), position.get("lon")

    intent = p.get("intent") or classify(text, selection,
                                         has_position=lat is not None)
    if history and _is_followup(text):
        intent = "direct"

    if intent == "spatial":
        out = _answer_spatial(ws, text, lat, lon, history)
        conversation.add(ws, "assistant", out.get("text") or "",
                         intent="spatial", model=out.get("model") or "",
                         context=selection)
        return {"intent": "spatial", "workspace": ws, **out}

    if intent == "query":
        out = _answer_query(ws, text, selection)
        conversation.add(ws, "assistant", _query_summary(out),
                         intent="query", context=selection)
        return {"intent": "query", "workspace": ws, **out}

    if intent == "research":
        execution_id = _start_research(ws, text, selection,
                                       depth=p.get("depth") or "standard")
        conversation.add(ws, "assistant",
                         "Started a research run. Findings will appear in this "
                         "Space as objects, sources and relationships.",
                         intent="research", execution_id=execution_id,
                         context=selection)
        return {"intent": "research", "workspace": ws,
                "executionId": execution_id}

    if intent.startswith("agent:"):
        code = intent.split(":", 1)[1]
        execution_id = _start_agent(ws, code, text, selection)
        if execution_id is None:
            return JSONResponse({"error": f"unknown agent {code}"}, status_code=400)
        conversation.add(ws, "assistant", f"Started {code.upper()}.",
                         intent=intent, execution_id=execution_id,
                         context=selection)
        return {"intent": intent, "workspace": ws, "executionId": execution_id}

    # Plain question, nothing held: ground it on whatever the question names.
    # This runs only on the direct path and only after routing, so it can
    # inform the answer without ever redirecting the request.
    #
    # The ids come back as `resolved` so the client can show what was used.
    # An implicit context the user cannot see is worse than none: a wrong
    # grounding then reads as a wrong model, with nothing to correct.
    resolved: list[dict] = []
    if not selection:
        resolved = resolve_mentions(ws, text)
        selection = [o["id"] for o in resolved]

    out = _direct_answer(ws, text, selection, history)
    conversation.add(ws, "assistant", out.get("text") or "",
                     intent="direct", model=out.get("model") or "",
                     context=selection)
    return {"intent": "direct", "workspace": ws,
            "resolved": [{"id": o["id"], "name": o["name"],
                          "type": o.get("typeLabel") or o.get("type"),
                          "glyph": o.get("glyph"), "color": o.get("color")}
                         for o in resolved],
            **out}


# Short continuations that only mean something relative to the previous turn.
_FOLLOWUP = {
    "yes", "yeah", "yep", "yup", "y", "ok", "okay", "sure", "please", "go on",
    "continue", "more", "tell me more", "go ahead", "do it", "expand",
    "elaborate", "details", "detail", "in detail", "and", "why", "why?",
    "how", "how?", "no", "nope", "next", "explain", "summarise", "summarize",
}


def _is_followup(text: str) -> bool:
    t = text.strip().lower().rstrip(".!?")
    return t in _FOLLOWUP or (len(t) <= 24 and t.startswith(
        ("yes", "no", "ok", "sure", "go on", "tell me more", "and what",
         "what about", "why", "how about")))


def _query_summary(out: dict) -> str:
    """A sentence for the thread describing what a graph answer actually did.

    The thread has to stay readable on its own; "answered from the graph" with
    no numbers is a row the user cannot learn anything from later.
    """
    kind = out.get("answer")
    if kind == "search":
        return f"Found {len(out.get('results') or [])} matching objects."
    nodes = (out.get("graph") or {}).get("nodes") or []
    if out.get("paths"):
        return (f"Traced {len(out['paths'])} connection(s); "
                f"{len(nodes)} objects in view.")
    if out.get("found") is False:
        return "No path found between the selected objects within 4 hops."
    return f"Answered from the graph — {len(nodes)} objects in view."


@router.get("/nova/thread")
def get_thread(workspace: str | None = None, limit: int = 100):
    """The conversation for a Space. This is what the Ask view renders."""
    ws = _ws(workspace)
    return {"workspace": ws, "turns": conversation.thread(ws, limit)}


@router.delete("/nova/thread")
def clear_thread(workspace: str | None = None):
    ws = _ws(workspace)
    return {"workspace": ws, "cleared": conversation.clear(ws)}


def _answer_query(workspace_id: str, text: str, selection: list[str]) -> dict:
    """Answer from the graph. No model call unless one is genuinely needed."""
    g = sql.load(workspace_id)

    # Two or more selected objects: the question is almost certainly "how are
    # these connected", and a path is a better answer than a paragraph.
    if len(selection) >= 2:
        paths = []
        for i in range(len(selection) - 1):
            chain = engine.path_between(g, selection[i], selection[i + 1],
                                        max_hops=4)
            if chain:
                paths.append({"from": selection[i], "to": selection[i + 1],
                              "path": chain})
        sub = engine.subgraph(g, selection, hops=1, max_nodes=60)
        return {"answer": "graph", "paths": paths, "graph": sub,
                "found": bool(paths)}

    if len(selection) == 1:
        sub = engine.subgraph(g, selection, hops=1, max_nodes=40)
        return {"answer": "graph", "graph": sub,
                "relationships": objects_mod.relationships_of(
                    workspace_id, selection[0], limit=40)}

    results = _search_objects_loosely(workspace_id, text)
    roots = [r["id"] for r in results[:5]]
    sub = engine.subgraph(g, roots, hops=1, max_nodes=50) if roots else \
        {"nodes": [], "edges": []}
    return {"answer": "search", "results": results, "graph": sub}


def _direct_answer(workspace_id: str, text: str, selection: list[str],
                   history: list[dict] | None = None) -> dict:
    # Import the singleton, not the package attribute. `omnix.models.__init__`
    # re-exports the ModelRouter INSTANCE under the name `router`, which
    # shadows the `omnix.models.router` submodule — so `from ..models import
    # router` binds the instance, and there has never been a `.shared()` on it.
    from ..models.router import router as _router

    ctx = _context_summary(workspace_id, selection)

    # Two different instructions, because the failure mode differs.
    #
    # WITH context the risk is embroidery — inventing properties for objects
    # the workspace holds. That stays forbidden.
    #
    # WITHOUT context the old prompt produced a refusal: it told the model to
    # ground every answer in selected objects, so with none selected the only
    # compliant reply was "I cannot answer based on the selected objects".
    # That is a wrong answer to a reasonable question. Answer from general
    # knowledge instead and be explicit that it is not drawn from this Space —
    # honest and useful beats correct-but-useless.
    base = ("You are NOVA, the command layer of the OMNIX intelligence "
            "workspace. Answer concisely and concretely. "
            "The conversation so far is provided: if the user replies with a "
            "short continuation such as 'yes' or 'go on', continue your "
            "previous answer directly. Never ask the user to repeat context "
            "they have already given.")
    if ctx:
        system = base + (
            " Objects from the user's workspace are provided below; ground "
            "your answer in them and cite them by name. Never invent facts "
            "about those objects beyond what you were given.")
    else:
        system = base + (
            " This workspace holds nothing matching the question, so answer "
            "from your own general knowledge. Do NOT refuse, and do not tell "
            "the user to select something first. Add one short closing line "
            "noting the answer is not drawn from this workspace's own "
            "sources, and suggest researching the topic to build it out.")

    # Prior turns go in as real messages rather than being flattened into the
    # prompt, so the model sees who said what. This is what makes "yes" resolve
    # to the offer NOVA made instead of arriving as a one-word question.
    msgs: list[dict] = [{"role": "system", "content": system}]
    msgs.extend(history or [])
    msgs.append({"role": "user", "content": f"{ctx}\n\n{text}" if ctx else text})

    result = _router.generate(
        "fast", messages=msgs,
        temperature=0.3, max_tokens=900,
        workspace_id=workspace_id, agent="nova")
    return {"answer": "text", "text": result.text if result.ok else "",
            "ok": result.ok, "error": result.error,
            "model": result.model, "contextUsed": bool(ctx),
            "historyUsed": len(history or [])}


def _answer_spatial(workspace_id: str, text: str,
                    lat: float | None, lon: float | None,
                    history: list[dict] | None = None) -> dict:
    """Answer a spatial question through TERRA's validated tool layer.

    The shape here is the one the brief asks for and it is worth being precise
    about the ordering, because it is what keeps this cheap:

      1. `tools.parse` — deterministic. Most spatial requests are formulaic and
         this catches them with no model call at all.
      2. `tools.select` — a model picks ONE tool from the catalogue and its
         choice is validated against the same schema. It never sees a URL, a
         provider or a key, and it cannot pass an argument that is not in the
         schema.
      3. The tool result is rendered as structured context and a second model
         call turns it into a sentence.

    The model is given the DATA, never the ability to fetch it. That is the
    whole design: an LLM that can call `search_places(category='pharmacy')`
    cannot ask for anything TERRA would not have offered a button for.
    """
    from ..terra.geo import tools as terra_tools
    from ..terra.geo import api as terra_api
    from ..models.router import router as _router

    # A position the user did not supply is worth recovering from memory before
    # giving up — TERRA already knows where they were, and "find coffee near
    # me" should not fail because this particular request had no GPS attached.
    if lat is None or lon is None:
        known = terra_api.get_location(workspace_id)
        if known.get("known"):
            lat, lon = known["lat"], known["lon"]

    call = terra_tools.parse(text, lat=lat, lon=lon)
    path = "parsed"
    if call is None:
        call = terra_tools.select(text, lat=lat, lon=lon,
                                  workspace_id=workspace_id)
        path = "model"

    if call is None:
        # No tool fits. Say what is missing rather than refusing blankly — with
        # no position, that is almost always the actual problem, and the UI
        # turns this into a "share my location" prompt.
        note = ("I need your location to answer that. Turn on location in the "
                "TERRA map and ask again."
                if lat is None else
                "I could not map that to a TERRA capability.")
        return {"answer": "spatial", "text": note, "ok": True,
                "spatial": {"matched": False, "path": "none"},
                "model": "", "error": ""}

    invoked = terra_tools.invoke(call["tool"], call["args"],
                                 workspace_id=workspace_id)

    # The reply is written from a rendered context, not from the raw payload.
    # Handing a model a provider's JSON invites it to quote ids and read
    # `freshness: "stale"` as a value rather than a warning.
    rendered = _render_tool_result(invoked, lat, lon, workspace_id)
    system = (
        "You are NOVA, answering a location question using data TERRA has "
        "already gathered. Answer in one or two short sentences, concretely, "
        "using the numbers you were given. Never invent a place, a distance, "
        "a time or a condition that is not below. "
        "If the data is marked cached, stale or estimated, say so plainly in "
        "the answer — never present it as current. "
        "If the data is empty, say nothing was found rather than guessing.")
    msgs: list[dict] = [{"role": "system", "content": system}]
    msgs.extend(history or [])
    msgs.append({"role": "user",
                 "content": f"{rendered}\n\nThe user asked: {text}"})

    result = _router.generate("fast", messages=msgs, temperature=0.2,
                              max_tokens=400, workspace_id=workspace_id,
                              agent="terra")

    return {
        "answer": "spatial",
        # A model being unavailable must not lose the answer. The rendered
        # context is already readable prose, so it is the fallback rather than
        # an error — TERRA's value here is the data, not the sentence.
        "text": result.text if result.ok and result.text else rendered,
        "ok": True,
        "error": result.error if not result.ok else "",
        "model": result.model,
        "spatial": {
            "matched": True,
            "path": path,
            "tool": invoked.get("tool"),
            "args": invoked.get("args"),
            "result": invoked.get("result"),
            "toolOk": invoked.get("ok"),
            "toolError": invoked.get("error", ""),
        },
    }


def _render_tool_result(invoked: dict, lat: float | None, lon: float | None,
                        workspace_id: str) -> str:
    """Turn a tool result into the prose a model should reason over.

    Routes through `context.as_prompt` wherever the payload fits its shape, so
    there is one renderer rather than a per-tool formatter that drifts.
    """
    from ..terra.geo.intelligence import context as ctx_mod
    from ..terra.geo import spatial

    if not invoked.get("ok"):
        return f"TERRA could not answer: {invoked.get('error', 'unknown error')}"

    tool = invoked.get("tool") or ""
    data = invoked.get("result") or {}

    if tool == "get_spatial_context":
        return ctx_mod.as_prompt(data)

    lines = [f"TERRA ran `{tool}`."]

    # Freshness first, so it is never buried under the data it qualifies.
    fresh = data.get("freshness") if isinstance(data, dict) else None
    if fresh and fresh != "live":
        age = data.get("ageS")
        lines.append(f"DATA FRESHNESS: {fresh}"
                     + (f", {age:.0f}s old" if age else "")
                     + " — say so in the answer.")

    if tool in ("search_places", "nearest_poi", "find_quiet_place"):
        found = data.get("places") or []
        if not found:
            lines.append("No matching places were found.")
        for p in found[:10]:
            bits = [p.get("name", "?")]
            if p.get("category"):
                bits.append(p["category"])
            if p.get("distanceM") is not None:
                bits.append(spatial.human_distance(p["distanceM"]))
            if p.get("rating"):
                bits.append(f"rated {p['rating']}")
            if p.get("openNow") is True:
                bits.append("open now")
            elif p.get("openNow") is False:
                bits.append("closed")
            if p.get("openingHours"):
                bits.append(f"hours: {p['openingHours'][:60]}")
            if p.get("address"):
                bits.append(p["address"][:80])
            lines.append("  - " + " · ".join(bits))
        if data.get("criteria", {}).get("note"):
            lines.append("Caveat: " + data["criteria"]["note"])

    elif tool == "get_route":
        origin = (data.get("origin") or {}).get("label") or "origin"
        dest = (data.get("destination") or {}).get("label") or "destination"
        lines.append(f"From {origin} to {dest} by {data.get('mode', 'driving')}:")
        for i, r in enumerate(data.get("routes") or []):
            traffic = (f", {spatial.human_duration(r['durationTrafficS'])} "
                       "in current traffic" if r.get("durationTrafficS") else "")
            lines.append(
                f"  {i + 1}. {spatial.human_distance(r['distanceM'])}, "
                f"{spatial.human_duration(r['durationS'])}{traffic}"
                + (f" via {r['summary']}" if r.get("summary") else ""))
        for note in (data.get("explanations") or [])[:3]:
            lines.append(f"     ({note})")
        crossings = data.get("crossings") or []
        if crossings:
            lines.append("Passes through geofences: "
                         + ", ".join(c["label"] for c in crossings))

    elif tool in ("get_weather", "get_air_quality", "get_elevation",
                  "get_environmental_context"):
        if tool == "get_environmental_context":
            w, aq = data.get("weather"), data.get("airQuality")
            sig = data.get("signals") or {}
            if w:
                lines.append(f"Weather: {w.get('description', '')}, "
                             f"{w.get('temperatureC')}°C, "
                             f"{w.get('precipitationProbabilityPct')}% rain, "
                             f"UV {w.get('uvIndex')}, "
                             f"wind {w.get('windKph')} km/h")
            if aq:
                lines.append(f"Air quality: {aq.get('band')} "
                             f"(index {aq.get('index')} on {aq.get('scale')})")
            sun = data.get("sun") or {}
            if sun.get("sunset"):
                lines.append(f"Sunrise {sun['sunrise']}, sunset {sun['sunset']}")
            if sig.get("concerns"):
                lines.append("Concerns: " + "; ".join(sig["concerns"]))
            if sig.get("favourable"):
                lines.append("In favour: " + "; ".join(sig["favourable"]))
            lines.append("Weigh these and give the user a clear "
                         "recommendation with the reason.")
        else:
            payload = (data.get("weather") or data.get("airQuality")
                       or data.get("elevation") or {})
            lines.append(str({k: v for k, v in payload.items()
                              if v is not None and k != "source"}))

    elif tool == "reverse_geocode":
        place = data.get("place") or {}
        lines.append(f"The user is at {place.get('name', 'an unknown place')}"
                     + (f", {place['address']}" if place.get("address") else ""))

    elif tool == "geocode":
        for p in (data.get("results") or [])[:5]:
            lines.append(f"  - {p.get('name')} ({p.get('address', '')}) "
                         f"at {p.get('lat'):.4f}, {p.get('lon'):.4f}")

    elif tool in ("known_locations", "geofences"):
        items = data if isinstance(data, list) else []
        if not items:
            lines.append("Nothing saved yet.")
        for item in items[:15]:
            lines.append(f"  - {item.get('label')}"
                         + (f" ({item.get('kind')})" if item.get("kind") else "")
                         + (" [inside]" if item.get("inside") else ""))

    elif tool == "location_history":
        items = data if isinstance(data, list) else []
        if not items:
            lines.append("No location history — it may be disabled.")
        for item in items[:12]:
            lines.append(f"  - {item.get('label') or 'unnamed'} at "
                         f"{item.get('arrivedAt')}")

    elif tool == "create_geofence":
        if data:
            lines.append(f"Created a geofence '{data.get('label')}' with a "
                         f"{data.get('radiusM')}m radius, firing on "
                         f"{data.get('trigger')}.")
        else:
            lines.append("The geofence could not be created.")

    elif tool == "distance":
        lines.append(f"{data.get('human')} ({data.get('km')} km), bearing "
                     f"{data.get('compass')}.")

    else:
        lines.append(str(data)[:1500])

    return "\n".join(lines)


def _start_agent(workspace_id: str, code: str, text: str,
                 selection: list[str]) -> str | None:
    from ..agents_v2 import adapter
    from ..squad import units as units_mod

    try:
        unit = units_mod.get_unit(code)
    except Exception:
        return None
    if unit is None:
        return None
    ctx = {"input": text}
    ctxt = _context_summary(workspace_id, selection)
    if ctxt:
        ctx["input"] = f"{ctxt}\n\n{text}"
    return adapter.run_unit(unit, ctx, workspace_id=workspace_id)


def _start_research(workspace_id: str, question: str, selection: list[str],
                    depth: str = "standard") -> str:
    """Run ORACLE, then write its findings into the graph.

    The ingestion runs in a follow-on thread rather than inside the unit so
    that a failure to extract entities can never fail the research itself —
    the report is valuable on its own, and losing it because a JSON parse went
    wrong would be a bad trade.
    """
    from ..agents_v2 import adapter
    from ..core import executions
    from ..squad import units as units_mod

    ctxt = _context_summary(workspace_id, selection)
    full_q = f"{ctxt}\n\n{question}" if ctxt else question

    unit = units_mod.get_unit("oracle")
    execution_id = adapter.run_unit(
        unit, {"input": full_q, "depth": depth},
        workspace_id=workspace_id, title=question[:120])

    def _ingest_when_done() -> None:
        import time
        from ..core import artifacts as artifacts_mod
        deadline = time.time() + 900
        while time.time() < deadline:
            ex = executions.get(execution_id, with_steps=False)
            if ex is None:
                return
            if ex["status"] in ("completed", "failed", "cancelled"):
                break
            time.sleep(2.0)
        else:
            return
        try:
            arts = artifacts_mod.list_for(workspace_id, execution_id=execution_id)
            if not arts:
                return
            full = artifacts_mod.get(arts[0]["id"]) or {}
            content = full.get("content") or {}
            research_ingest.ingest(
                workspace_id, question, content.get("meta") or {},
                content.get("summary") or "", execution_id=execution_id)
        except Exception:
            # Never let ingestion take down the run that produced the report.
            pass

    threading.Thread(target=_ingest_when_done, daemon=True,
                     name=f"ingest-{execution_id[:8]}").start()
    return execution_id


# ---------------------------------------------------------------------------
# Research surfaces
# ---------------------------------------------------------------------------
@router.post("/research/run")
def research_run(payload: dict):
    p = payload or {}
    q = (p.get("question") or "").strip()
    if not q:
        return JSONResponse({"error": "question is required"}, status_code=400)
    ws = _ws(p.get("workspace"))
    return {"executionId": _start_research(
        ws, q, p.get("selection") or [], depth=p.get("depth") or "standard"),
        "workspace": ws}


@router.post("/research/ingest")
def research_ingest_now(payload: dict):
    """Ingest a finished research execution's artifact into the graph.

    Exposed separately so a run that completed before ingestion existed — or
    one whose ingestion failed — can be folded in without re-researching.
    """
    from ..core import artifacts as artifacts_mod

    p = payload or {}
    execution_id = p.get("executionId")
    if not execution_id:
        return JSONResponse({"error": "executionId is required"}, status_code=400)
    ws = _ws(p.get("workspace"))
    arts = artifacts_mod.list_for(ws, execution_id=execution_id)
    if not arts:
        return JSONResponse({"error": "no artifact for that execution"},
                            status_code=404)
    full = artifacts_mod.get(arts[0]["id"]) or {}
    content = full.get("content") or {}
    return research_ingest.ingest(
        ws, content.get("input") or "", content.get("meta") or {},
        content.get("summary") or "", execution_id=execution_id,
        auto_commit=bool(p.get("autoCommit")))


@router.post("/research/proposals/commit")
def commit_proposals(payload: dict):
    p = payload or {}
    ws = _ws(p.get("workspace"))
    return {"committed": research_ingest.commit_proposals(
        ws, p.get("proposals") or [], execution_id=p.get("executionId"))}


@router.get("/claims")
def claims(workspace: str | None = None, execution: str | None = None,
           limit: int = 200):
    """The Claim Ledger. Verdicts come from ORACLE's deterministic verifier."""
    ws = _ws(workspace)
    return {"claims": research_ingest.claim_ledger(
        ws, execution_id=execution, limit=limit)}


@router.get("/claims/{claim_id}/evidence")
def claim_evidence(claim_id: str, workspace: str | None = None):
    ws = _ws(workspace)
    data = research_ingest.evidence_graph(ws, claim_id)
    if not data:
        return JSONResponse({"error": "unknown claim"}, status_code=404)
    return data


@router.get("/sources")
def sources(workspace: str | None = None, limit: int = 200):
    """Source Library. Tier labels are ORACLE's URL classification, not a
    quality score — see oracle_evidence.classify_source."""
    from sqlalchemy import select

    from ..core.db import session
    from ..core.schema import Source, iso

    ws = _ws(workspace)
    with session() as s:
        rows = s.scalars(select(Source).where(Source.workspace_id == ws)
                         .order_by(Source.retrieved_at.desc())
                         .limit(max(1, min(limit, 1000)))).all()
        return {"sources": [{
            "id": r.id, "url": r.url, "title": r.title,
            "publisher": r.publisher, "tier": r.tier, "tierLabel": r.tier_label,
            "year": r.year, "credibility": r.credibility,
            "duplicateOf": r.duplicate_of, "snippet": r.snippet,
            "retrievedAt": iso(r.retrieved_at),
        } for r in rows]}


# ---------------------------------------------------------------------------
# Live intelligence
# ---------------------------------------------------------------------------
@router.get("/brief")
def brief(workspace: str | None = None, hours: float = 168.0, limit: int = 30):
    return tracking.brief(_ws(workspace), hours=hours, limit=limit)


@router.get("/summary")
def summary(workspace: str | None = None):
    return tracking.workspace_summary(_ws(workspace))


@router.post("/tracking/sync")
def tracking_sync(payload: dict | None = None):
    """Pull new coverage for tracked objects into events."""
    ws = _ws((payload or {}).get("workspace"))
    return tracking.sync_tracked_from_terra(ws)


@router.post("/research/diff")
def diff(payload: dict):
    p = payload or {}
    prev, cur = p.get("previousExecution"), p.get("currentExecution")
    if not prev or not cur:
        return JSONResponse(
            {"error": "previousExecution and currentExecution are required"},
            status_code=400)
    return tracking.research_diff(_ws(p.get("workspace")),
                                  p.get("question") or "", prev, cur)
