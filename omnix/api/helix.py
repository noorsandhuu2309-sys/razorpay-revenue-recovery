"""HELIX routes — the bioinformatics corpus, its index, and grounded answers.

    GET  /api/helix/status          corpus size, coverage, whether it is warm
    GET  /api/helix/topics          the subfield taxonomy, methods and tools
    GET  /api/helix/search          retrieval only: papers, no model, no wait
    GET  /api/helix/paper/{pmid}    one paper in full
    POST /api/helix/ask             SSE: sources, then a grounded answer

`search` and `ask` are separate on purpose. The UI calls `search` as the user
types and has papers on screen before they finish the question; `ask` is only
spent when they actually want prose. Fusing them would put a model call behind
every keystroke.
"""

from __future__ import annotations

import json
import time

from fastapi import APIRouter
from fastapi.responses import JSONResponse, StreamingResponse

from ..helix import answer as helix_answer
from ..helix import index as helix_index
from ..helix.topics import TOPICS

router = APIRouter(prefix="/api/helix", tags=["helix"])


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@router.get("/status")
def status():
    """Is the corpus there, how big is it, and is the index already built?

    `ready` is reported honestly rather than triggering a build: the caller is
    usually a status panel, and having a panel silently spend two seconds of CPU
    to make its own reading true is the kind of thing that makes a UI feel slow
    for no visible reason.
    """
    ok, reason = helix_index.available()
    if not ok:
        return JSONResponse({"ok": False, "error": reason, "papers": 0},
                            status_code=200)
    if not helix_index.ready():
        return {"ok": True, "ready": False, "papers": None,
                "hint": "index builds on the first question"}
    ix = helix_index.shared()
    return {"ok": True, "ready": True, **ix.stats()}


@router.get("/topics")
def topics():
    """The taxonomy. Drives the UI's topic rail and the tool index."""
    return {
        "topics": [
            {
                "key": t.key, "label": t.label, "summary": t.summary,
                "methods": list(t.methods), "tools": list(t.tools),
                "aliases": list(t.aliases),
            }
            for t in TOPICS
        ]
    }


@router.get("/search")
def search(q: str, limit: int = 10, topic: str | None = None):
    """Papers matching `q`. No model runs, so this answers in milliseconds."""
    query = (q or "").strip()
    if not query:
        return JSONResponse({"error": "q is required"}, status_code=400)

    ok, reason = helix_index.available()
    if not ok:
        return JSONResponse({"error": reason}, status_code=503)

    t0 = time.monotonic()
    ix = helix_index.shared()
    hits = ix.search(query, limit=max(1, min(limit, 50)), topic=topic)
    return {
        "query": query,
        "tookMs": round((time.monotonic() - t0) * 1000, 2),
        "count": len(hits),
        "results": [
            {
                "pmid": p["pmid"], "doi": p.get("doi", ""), "title": p["title"],
                "journal": p.get("journal", ""), "year": p.get("year", ""),
                "authors": p.get("authors", [])[:5], "topics": p["topics"],
                "score": score,
                "snippet": p["abstract"][:280].replace("\n", " "),
                "url": f"https://pubmed.ncbi.nlm.nih.gov/{p['pmid']}/",
            }
            for score, p in hits
        ],
    }


@router.get("/paper/{pmid}")
def paper(pmid: str):
    ok, reason = helix_index.available()
    if not ok:
        return JSONResponse({"error": reason}, status_code=503)
    p = helix_index.shared().by_pmid.get(pmid.strip())
    if p is None:
        return JSONResponse({"error": "unknown paper"}, status_code=404)
    return {**p, "url": f"https://pubmed.ncbi.nlm.nih.gov/{p['pmid']}/"}


@router.post("/ask")
def ask(payload: dict):
    """Body: { question, deep?: bool, topic?: str }

    Streams Server-Sent Events:
      meta     -> { kind, topics, deep, retrievalMs, model }
      sources  -> { sources: [...] }        (before any generation)
      delta    -> { text }
      done     -> { instant, tookMs }
      error    -> { message }

    Sources are emitted BEFORE the answer, always. The reader can start checking
    the papers while the prose is still arriving, and if generation fails they
    are still left with the retrieval — which on its own answers a good number
    of questions.

    Declared `def`, not `async def`: it returns a synchronous generator that
    Starlette iterates in a threadpool, and the retrieval before it is CPU work
    that would otherwise sit on the event loop.
    """
    question = (payload.get("question") or "").strip()
    if not question:
        return JSONResponse({"error": "question is required"}, status_code=400)
    if len(question) > 2000:
        return JSONResponse({"error": "question is too long"}, status_code=400)

    ok, reason = helix_index.available()
    if not ok:
        return JSONResponse({"error": reason}, status_code=503)

    deep = bool(payload.get("deep"))
    topic = payload.get("topic") or None

    t0 = time.monotonic()
    plan = helix_answer.plan(question, deep=deep, topic=topic)

    def gen():
        ladder = helix_answer.DEEP_LADDER if deep else helix_answer.QUICK_LADDER
        yield _sse("meta", {
            "kind": plan.kind,
            "topics": [t.key for t in plan.topics[:3]],
            "deep": deep,
            "retrievalMs": plan.retrieval_ms,
            "model": ladder[0],
        })
        yield _sse("sources", {"sources": [s.as_dict() for s in plan.sources]})

        # Structural questions are already answered. Sending it as one delta
        # keeps the client's rendering path identical for both routes.
        if plan.instant:
            yield _sse("delta", {"text": plan.instant})
            yield _sse("done", {
                "instant": True,
                "tookMs": round((time.monotonic() - t0) * 1000, 2)})
            return

        if not plan.sources:
            yield _sse("delta", {"text":
                "Nothing in the bioinformatics corpus matches that question. "
                "The corpus covers " + ", ".join(t.label for t in TOPICS[:5]) +
                " and ten other subfields — try naming a method, a tool or an "
                "assay."})
            yield _sse("done", {
                "instant": True,
                "tookMs": round((time.monotonic() - t0) * 1000, 2)})
            return

        try:
            for chunk in helix_answer.stream(plan, deep=deep):
                if chunk:
                    yield _sse("delta", {"text": chunk})
        except Exception as e:
            yield _sse("error", {"message": str(e)[:300]})
        yield _sse("done", {
            "instant": False,
            "tookMs": round((time.monotonic() - t0) * 1000, 2)})

    return StreamingResponse(gen(), media_type="text/event-stream")
