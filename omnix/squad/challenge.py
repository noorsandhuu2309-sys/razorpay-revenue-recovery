"""CHALLENGE — stress-test an idea before you spend a research run on it.

The question this answers is "what would have to be true for this to hold, and
what am I not seeing?" — asked *before* committing ORACLE to a hundred seconds
of searching. Its output is meant to be consumed as the input to a research
run: the questions it produces are the ones worth actually researching.

WHY SEVERAL MODELS, AND WHAT THAT DOES NOT MEAN
-----------------------------------------------
Each panellist is a different vendor's model, called separately with no sight
of the others. That buys one specific thing: independence of failure. When
four models trained on different corpora independently raise the same
objection, that objection is unlikely to be an artifact of one model's
idiosyncrasy.

It buys nothing else. **Model agreement is not evidence.** Models share the
open web as training data, so consensus measures overlap in what they read, not
whether something is true — four models can be confidently, identically wrong.
This is the exact failure the rest of OMNIX exists to prevent, so CHALLENGE is
careful never to imply otherwise: it reports how many models raised a point as
a count, never as a score or a verdict, and every artifact it produces is
marked model opinion rather than sourced fact. The claim ledger's `verified`
badge means two independent *sources*; nothing here can earn it.

The useful output is therefore not "is my idea right" but the three things
models genuinely are good at: naming the assumptions a proposition rests on,
producing the strongest counterargument to it, and stating what evidence would
settle the matter.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

from .base import (Subagent, Unit, UnitResult, clamp, extract_json,
                   list_block, markdown_block, str_list)

# Four vendors, deliberately. Independence is the whole premise, and two
# checkpoints of the same base model would double-count one point of view while
# looking like corroboration.
#
# Re-probed 2026-08-16, and the seating changed. **Mistral has no callable model
# on this account at all** — `mistral-nemotron` returns no content in 50s, and
# `mistral-large`, `mistral-large-2`, `mixtral-8x22b` and `mistral-7b-v0.3` all
# 404. That seat was answering nothing, so the panel had been quietly running
# three-handed while reporting four. `minimax-m3` takes it: a separate vendor
# with a separate training lineage, 0.7s to first token and a specific,
# well-formed critique. (`z-ai/glm-5.2` is the standby if MiniMax goes the same
# way; it was probed live on the same day.)
#
# The NVIDIA seat moves up from the 4B anchor to the 30B: same vendor, same
# speed class, markedly better criticism — the 4B was the weakest voice on the
# panel and it was there for latency reasons that no longer apply.
PANEL: list[tuple[str, str]] = [
    ("openai/gpt-oss-20b", "OpenAI"),
    ("minimaxai/minimax-m3", "MiniMax"),
    ("meta/llama-3.1-8b-instruct", "Meta"),
    ("nvidia/nemotron-3-nano-30b-a3b", "NVIDIA"),
]

# Per-panellist ceiling. Generous enough for a slow-but-working vendor on a
# cold start, short enough that the worst case — every seat timing out, then
# every retry timing out — still returns inside the view's own 180s budget
# rather than leaving the user watching a spinner with nothing behind it.
_ASK_TIMEOUT = 45.0

# How many dead seats are worth a second attempt. See the retry block in run()
# for why this is bounded rather than "all of them".
_MAX_RETRIES = 2

_CRITIC_SYSTEM = (
    "You are one panellist on an independent review panel stress-testing an "
    "idea BEFORE the user spends real research effort on it. You are not "
    "deciding whether the idea is true — you have no sources and cannot know. "
    "You are finding what it rests on and what would break it.\n"
    "Return:\n"
    "(1) assumptions — the load-bearing things that must hold for the idea to "
    "work. Name the ones the author probably has not noticed. Be specific: "
    "'sulfide electrolyte yields survive scale-up', not 'technology must "
    "improve'.\n"
    "(2) counterargument — the single strongest case AGAINST, in two or three "
    "sentences. Argue it properly; a weak strawman is useless.\n"
    "(3) stance — exactly one of 'plausible', 'doubtful', or 'mixed'.\n"
    "(4) stance_note — one short clause saying what your stance hinges on.\n"
    "(5) research_questions — 2-4 questions whose ANSWERS would settle this. "
    "Each must be answerable from published evidence: 'what solid-state "
    "gigafactory dates have been announced?' not 'will it succeed?'\n"
    'Schema: {"assumptions":["..."],"counterargument":"...",'
    '"stance":"plausible|doubtful|mixed","stance_note":"...",'
    '"research_questions":["..."]}'
)


def _words(text: str) -> set[str]:
    return {w for w in "".join(
        c.lower() if c.isalnum() else " " for c in (text or "")).split()
        if len(w) > 3}


def _similar(a: str, b: str, threshold: float = 0.42) -> bool:
    """Do two panellists appear to be making the same point?

    Deliberately lexical and deliberately loose. Getting this wrong in the
    permissive direction merges two phrasings of one objection, which is the
    intended behaviour; getting it wrong the other way inflates the "N of M
    models" count, which would overstate independent agreement — the one number
    here a reader might lean on.
    """
    wa, wb = _words(a), _words(b)
    if not wa or not wb:
        return False
    return len(wa & wb) / float(min(len(wa), len(wb))) >= threshold


def _cluster(items: list[tuple[str, str]]) -> list[dict]:
    """Group (text, vendor) pairs into points, counting distinct vendors.

    Vendors rather than entries: if one model repeats itself the point is still
    raised by one model, and a count that says otherwise is the exact
    overstatement this module is written to avoid.
    """
    groups: list[dict] = []
    for text, vendor in items:
        text = (text or "").strip()
        if not text:
            continue
        for g in groups:
            if _similar(g["text"], text):
                g["vendors"].add(vendor)
                # Keep the fullest phrasing — the longest version of a point is
                # usually the one that spells out the mechanism.
                if len(text) > len(g["text"]):
                    g["text"] = text
                break
        else:
            groups.append({"text": text, "vendors": {vendor}})
    groups.sort(key=lambda g: (-len(g["vendors"]), -len(g["text"])))
    return groups


class Challenge(Unit):
    code = "challenge"
    name = "CHALLENGE"
    glyph = "◈"
    tagline = "Stress-test an idea before you research it"
    blurb = ("Independent models attack your idea, then hand you the questions "
             "worth researching.")
    accent = "#7cc4e8"
    input_label = "What idea should CHALLENGE stress-test?"
    input_kind = "textarea"
    placeholder = ("e.g. Solid-state batteries will be in mass-market EVs "
                   "by 2028.")

    def __init__(self):
        # Declared for the console's roster. The real calls are made per-model
        # in run() so each panellist is pinned to one vendor; going through the
        # tier ladder would let two seats land on the same model.
        self.subagents = [
            Subagent(vendor, "reviews the idea independently")
            for _, vendor in PANEL
        ]

    # -- panel ---------------------------------------------------------------
    @staticmethod
    def _seat(emit, stage: str, vendor: str) -> None:
        """Report one seat's state, without ever costing the panel that seat.

        The view draws a live panel from these events, so they are emitted from
        inside the worker threads. That makes them the one place a progress
        write could take down a panellist: `emit` reaches the database and also
        raises `Cancelled` on a cancelled run, and `_ask` catches broadly enough
        to turn either into a silent dropped seat. Swallowing here keeps a
        cosmetic failure cosmetic — the panel is the product, the animation is
        not.
        """
        try:
            emit(stage, vendor)
        except Exception:
            pass

    def _ask(self, model: str, vendor: str, idea: str, emit=None) -> dict | None:
        """One panellist. Returns None when that vendor did not answer.

        No ladder fallback on purpose: if Mistral is down, the honest outcome
        is three panellists, not four with one silently answered by someone
        else. The vendor label is a claim about who said it.
        """
        from ..models.router import router as _router

        if emit is not None:
            self._seat(emit, "seat.thinking", vendor)
        try:
            res = _router.generate(
                "reasoning", models=[model], json_mode=True,
                system=_CRITIC_SYSTEM, user=f"IDEA:\n{idea}",
                temperature=0.5, max_tokens=900, agent="challenge",
                # A seat has one attempt at a JSON shape and no ladder behind
                # it, so a model that thinks its 900 tokens away returns nothing
                # and the panel reports the vendor as silent — a wrong claim
                # about who was reachable, which is the one thing this unit
                # must not get wrong.
                thinking=False,
                # Without this a single unresponsive vendor pinned an execution
                # in `running` forever: the panel is fanned out in parallel, so
                # one hung socket held the pool open, the unit never returned,
                # and the run sat in the Agents view as permanently in-flight.
                # A panellist that cannot answer in 45s is a dropped seat,
                # which the aggregation already handles honestly.
                timeout=_ASK_TIMEOUT)
        except Exception:
            if emit is not None:
                self._seat(emit, "seat.silent", vendor)
            return None
        if not res.ok or not (res.text or "").strip():
            if emit is not None:
                self._seat(emit, "seat.silent", vendor)
            return None
        data = extract_json(res.text)
        if not isinstance(data, dict):
            # Answered, but not in a shape the panel can use. A distinct state
            # from silence: the vendor is up, its reply was unparseable, and
            # calling that "unavailable" would misattribute the failure.
            if emit is not None:
                self._seat(emit, "seat.unusable", vendor)
            return None

        stance = str(data.get("stance") or "").strip().lower()
        if stance not in ("plausible", "doubtful", "mixed"):
            stance = "mixed"
        if emit is not None:
            self._seat(emit, "seat.answered", vendor)
        return {
            "vendor": vendor,
            "model": model,
            "assumptions": str_list(data.get("assumptions"), limit=5),
            "counterargument": clamp(
                str(data.get("counterargument") or "").strip(), 700),
            "stance": stance,
            "stance_note": clamp(str(data.get("stance_note") or "").strip(), 220),
            "research_questions": str_list(data.get("research_questions"),
                                           limit=4),
        }

    def run(self, ctx, emit) -> UnitResult:
        idea = (ctx.get("input") or "").strip()
        res = UnitResult()
        if not idea:
            res.summary = "No idea provided."
            return res

        # The roster goes out before the calls do, so the view can draw all four
        # seats as pending immediately instead of having them pop in one by one
        # as each vendor happens to report. JSON rather than a delimited string
        # because the model ids contain slashes and hyphens, and the view names
        # the actual model on each seat — a panel that says "OpenAI" without
        # saying which checkpoint is not an independence claim anyone can check.
        emit("panel.seated", json.dumps(
            [{"vendor": v, "model": m} for m, v in PANEL]))
        emit("panel", f"Asking {len(PANEL)} independent models")
        with ThreadPoolExecutor(max_workers=len(PANEL)) as pool:
            replies = list(pool.map(
                lambda mv: self._ask(mv[0], mv[1], idea, emit), PANEL))
        panel = [r for r in replies if r]

        # Firing four vendors at once reliably trips the free-tier concurrency
        # limit, and it was never the same vendor twice — two consecutive runs
        # lost OpenAI and then Mistral. A dropped seat is expensive here: it is
        # not just a missing opinion, it lowers the denominator on every "N of
        # M models raised this" count. One sequential retry recovers it, and
        # costs a few seconds only in the case that actually needs it.
        missing_seats = [(m, v) for (m, v), r in zip(PANEL, replies) if not r]
        # Retried sequentially (re-firing them together would just trip the
        # same limit) and capped at two, which is what keeps the worst case
        # bounded: 45s for the parallel wave plus 2x45s of retries stays inside
        # the 180s the view is willing to wait. Retrying all four could exceed
        # it and leave the user staring at a spinner for a run that did finish.
        retries = missing_seats[:_MAX_RETRIES]
        if retries and panel:
            emit("retry", f"Retrying {len(retries)} model(s) that did not answer")
            for model, vendor in retries:
                self._seat(emit, "seat.retry", vendor)
                again = self._ask(model, vendor, idea, emit)
                if again:
                    panel.append(again)

        if not panel:
            # Every vendor failed. Say so rather than emitting an empty
            # critique that reads like "no objections found".
            res.summary = ("No model on the panel answered, so this idea has "
                           "not been stress-tested. Check the cloud backend "
                           "on the Agents view and try again.")
            res.meta = {"error": "panel_unavailable", "answered": 0,
                        "panel_size": len(PANEL)}
            return res

        answered = len(panel)
        emit("consolidate", "Consolidating what the panel agreed on")

        assumptions = _cluster([(a, p["vendor"])
                                for p in panel for a in p["assumptions"]])
        counters = _cluster([(p["counterargument"], p["vendor"])
                             for p in panel if p["counterargument"]])
        questions = _cluster([(q, p["vendor"])
                              for p in panel for q in p["research_questions"]])

        stances: dict[str, list[str]] = {}
        for p in panel:
            stances.setdefault(p["stance"], []).append(p["vendor"])

        # -- summary ---------------------------------------------------------
        top = counters[0] if counters else None
        if top:
            raised = len(top["vendors"])
            lead = (f"{raised} of {answered} models raised the same objection: "
                    if raised > 1 else "The strongest objection raised: ")
            res.summary = clamp(lead + top["text"], 1200)
        else:
            res.summary = clamp(
                f"{answered} models reviewed the idea without converging on a "
                "single objection.", 600)

        # -- blocks ----------------------------------------------------------
        if assumptions:
            res.add(list_block(
                "Assumptions your idea rests on",
                [self._label(a, answered) for a in assumptions[:6]]))

        if top:
            body = top["text"]
            others = counters[1:3]
            if others:
                body += "\n\nAlso raised:\n" + "\n".join(
                    f"- {self._label(c, answered)}" for c in others)
            res.add(markdown_block("Strongest counterargument", body))

        # Disagreement is the signal worth surfacing: a split panel means the
        # idea turns on something the models cannot settle, which is precisely
        # what is worth researching. Unanimity is reported plainly and NOT as
        # endorsement.
        if len(stances) > 1:
            split_lines = [f"{len(v)} say {k}: {', '.join(sorted(v))}"
                           for k, v in sorted(stances.items(),
                                              key=lambda kv: -len(kv[1]))]
        else:
            only = next(iter(stances))
            split_lines = [
                f"No split — all {answered} models call this {only}. "
                "Shared training data makes agreement cheap; treat it as the "
                "absence of an obvious objection, not as support."]
        res.add(list_block("Where the panel split — signal, not consensus",
                           split_lines))

        if questions:
            res.add(list_block(
                "Questions that would settle it",
                [q["text"] for q in questions[:5]]))

        missing = len(PANEL) - answered
        if missing:
            res.add(list_block("Panel", [
                f"{answered} of {len(PANEL)} models answered — "
                f"{missing} unavailable, so agreement counts are out of "
                f"{answered}."]))

        res.meta = {
            "kind": "challenge",
            # `summary` does not survive onto the execution's step output —
            # that carries only meta and a COUNT of blocks, with the prose
            # filed in the artifact. The view needs the headline immediately
            # to render, so it travels in meta rather than being recomputed
            # client-side, which would fork the "N of M raised it" wording.
            "headline": res.summary,
            "split": split_lines,
            # Consumed by the UI to render the research hand-off, and the
            # reason this unit exists: these go straight into a research run.
            "researchQuestions": [q["text"] for q in questions[:5]],
            "answered": answered,
            "panelSize": len(PANEL),
            "vendors": [p["vendor"] for p in panel],
            "stances": {k: sorted(v) for k, v in stances.items()},
            "assumptions": [{"text": a["text"],
                             "raisedBy": sorted(a["vendors"])}
                            for a in assumptions[:6]],
            "counterarguments": [{"text": c["text"],
                                  "raisedBy": sorted(c["vendors"])}
                                 for c in counters[:3]],
            # Every consumer of this artifact must be able to see, without
            # reading the prose, that none of it is sourced.
            "evidence": "model_opinion",
            "disclaimer": ("Model opinion, not evidence. Research these "
                           "questions to get sourced, verifiable claims."),
        }
        return res

    @staticmethod
    def _label(group: dict, answered: int) -> str:
        n = len(group["vendors"])
        if n <= 1:
            return f"{group['text']} ({sorted(group['vendors'])[0]})"
        return f"{group['text']} — raised by {n} of {answered}"
