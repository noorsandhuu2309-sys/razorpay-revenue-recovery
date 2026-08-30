"""POI search, and the ranking that makes it intelligence rather than a list.

The brief's distinction — "TERRA should understand places rather than simply
display markers" — comes down to what happens AFTER the provider returns. A
provider hands back whatever is inside a radius, in an order that is either
arbitrary or optimised for the vendor. Turning that into an answer to "find a
quiet place to work for two hours" is this module's job, and it is done with an
explicit, inspectable score rather than an opaque one.

The scoring rules, and why each exists:

  * **Distance dominates, but with diminishing returns.** A place 200m away is
    much better than one 2km away; 4km versus 4.2km is noise. So distance
    enters as a decay curve, not a linear penalty.
  * **Closed is not a tiebreak, it is a filter.** A closed restaurant is not a
    slightly worse restaurant. When opening hours are known and the place is
    shut, it drops to the bottom regardless of everything else — and when
    hours are UNKNOWN it is not penalised, because absence of data is not
    evidence of closure.
  * **Ratings count only where they exist.** OSM has none. A ranking that
    weights ratings would silently rank every OSM result identically and then
    claim the order meant something. So the rating term is skipped entirely
    when no candidate in the set has one.
  * **The score is returned.** `score_parts` travels to the UI and to the LLM,
    so a recommendation can be explained instead of asserted.
"""

from __future__ import annotations

import math
import re
from datetime import datetime

from .. import spatial
from ..providers import registry
from ..providers.base import CATEGORIES, canonical_category
from ..types import Coord, Place, Result

#: Categories a "somewhere quiet to work" request should consider, in the order
#: they are usually right. Named here rather than inferred, because inferring
#: "quiet" from OSM tags is guesswork and this list is at least honest about
#: being a judgement call.
QUIET_WORK_CATEGORIES = ("library", "cafe", "coworking", "park")


def search(*, near: Coord, query: str = "", category: str = "",
           radius_m: float = 2000, limit: int = 20,
           open_now: bool | None = None,
           require_ratings: bool = False) -> Result:
    """Find places near a point.

    `category` is canonicalised, so callers may pass "chemist" or "pharmacy" or
    "medical store" and get the same OSM tag. An unrecognised category falls
    back to free-text search rather than returning nothing.
    """
    cat = canonical_category(category or query)
    text = query if not cat else ""
    if cat and cat not in CATEGORIES:
        cat, text = "", (query or category)

    result = registry.first_ok(
        registry.places_chain(require_ratings=require_ratings), "places",
        lambda p: p.search_places(near=near, query=text, category=cat,
                                  radius_m=radius_m, limit=max(limit, 20)),
        {"lat": round(near.lat, 3), "lon": round(near.lon, 3),
         "q": text.lower(), "cat": cat, "r": int(radius_m)},
        # An empty list here is a real answer — "no pharmacies within 1km" —
        # so it must NOT cascade to the next provider and must not read as a
        # failure. This is the flag's whole reason for existing.
        empty_is_miss=False,
    )
    if not result.ok:
        return result

    places: list[Place] = list(result.data or [])
    for p in places:
        p.distance_m = spatial.haversine_m(near, p.coord)
        p.distance_kind = "straight"

    # Providers honour a radius loosely — Overpass's `around` is exact but
    # Google's locationBias is a hint, and a text search can return something
    # in the next city. Enforcing it here means "within 5 km" means it.
    places = [p for p in places if (p.distance_m or 0) <= radius_m * 1.05]

    if open_now is True:
        places = [p for p in places if _open_state(p) is not False]

    # A provider that hit its element cap returned a spatially biased sample,
    # not everything in the radius — so the result is incomplete in a way the
    # caller cannot detect from the data. Reported through `attempted`, which
    # already exists to carry "what actually happened" up to the UI, rather
    # than through `error`, which would wrongly mark a usable answer as failed.
    attempted = list(result.attempted)
    if any((p.tags or {}).get("_truncated") for p in places):
        attempted.append("overpass:truncated — narrow the radius for "
                         "complete coverage")

    ranked = rank(places, near=near)
    return Result(data=ranked[:limit], freshness=result.freshness,
                  provider=result.provider, age_s=result.age_s,
                  attempted=attempted)


#: Radii `nearest` tries in order. Small first, and that ordering is the whole
#: correctness argument — see below.
_NEAREST_LADDER = (750.0, 2000.0, 5000.0, 15000.0)


def nearest(*, near: Coord, category: str, radius_m: float = 5000) -> Result:
    """The single closest place of a category.

    Distance only, no blended score — "the nearest hospital" is a question
    about distance, and answering it with a better-rated one 3km further away
    is a different question. This is the emergency path; it means what it says.

    **It searches outward in rings, and must.** The obvious implementation —
    one query at the full radius, then sort by distance — is wrong, and wrong
    in the worst possible direction. Overpass returns elements in `qt`
    (quadtile) order and the query carries an element cap; at a 5km radius in a
    dense city the cap is reached and the truncation is SPATIAL, keeping one
    corner of the bounding box. Measured against real data at 12.9716,77.5946:
    a 1km search finds a pharmacy at 360m, and the same search at 5km returns
    nothing closer than 693m — the near ones are simply not in the response.
    Sorting a truncated set by distance cannot recover them, and the answer
    looks perfectly plausible.

    Rings fix it because a small radius returns everything inside it, so the
    first ring with any result contains the true nearest. It is also cheaper in
    the common case: the first ring usually hits, and that is the smallest
    query Overpass can be asked.
    """
    ladder = [r for r in _NEAREST_LADDER if r <= radius_m] or [radius_m]
    if ladder[-1] < radius_m:
        ladder.append(radius_m)

    last: Result | None = None
    attempted: list[str] = []
    for ring in ladder:
        result = search(near=near, category=category, radius_m=ring, limit=40)
        attempted.extend(result.attempted)
        last = result
        if not result.ok:
            continue
        found = sorted(result.data or [], key=lambda p: p.distance_m or 1e9)
        if found:
            return Result(data=found[:1], freshness=result.freshness,
                          provider=result.provider, age_s=result.age_s,
                          attempted=attempted)

    if last is None:
        return Result.offline("no provider could answer", attempted=attempted)
    return Result(data=[], freshness=last.freshness, provider=last.provider,
                  age_s=last.age_s, error=last.error, attempted=attempted)


def quiet_workspace(*, near: Coord, radius_m: float = 5000,
                    hours: float = 2.0, limit: int = 8) -> Result:
    """Candidates for "somewhere quiet I can work for a couple of hours".

    Searches several categories, merges, then re-ranks with the weights that
    matter for this request rather than the defaults: staying open long enough
    is the binding constraint, so a place closing in an hour is dropped even if
    it is the closest.

    Returns CANDIDATES, deliberately. The final recommendation is OMNIX's to
    make and explain — this returns the structured shortlist and the reasons,
    which is the division the brief asks for.
    """
    merged: dict[str, Place] = {}
    freshness = None
    provider_names: list[str] = []
    attempted: list[str] = []

    for cat in QUIET_WORK_CATEGORIES:
        r = search(near=near, category=cat, radius_m=radius_m, limit=12)
        attempted.extend(r.attempted)
        if not r.ok:
            continue
        if freshness is None or r.freshness.value != "live":
            freshness = r.freshness if freshness is None else freshness
        if r.provider and r.provider not in provider_names:
            provider_names.append(r.provider)
        for p in (r.data or []):
            # Dedupe across categories by identity, then by position — a
            # library tagged as both amenity=library and a POI comes back from
            # two queries as two rows with different ids.
            key = p.external_id or f"{p.coord.lat:.5f},{p.coord.lon:.5f}"
            if key not in merged:
                merged[key] = p

    if not merged:
        from ..types import Freshness
        return Result(data=[], freshness=freshness or Freshness.LIVE,
                      provider=",".join(provider_names), attempted=attempted)

    candidates = [p for p in merged.values() if _stays_open(p, hours)]
    ranked = rank(candidates or list(merged.values()), near=near,
                  weights={"distance": 0.9, "rating": 0.5, "open": 1.4,
                           "quiet": 1.2, "amenity": 0.8})
    from ..types import Freshness
    return Result(data=ranked[:limit], freshness=freshness or Freshness.LIVE,
                  provider=",".join(provider_names) or "overpass",
                  attempted=attempted)


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------
DEFAULT_WEIGHTS: dict[str, float] = {
    "distance": 1.0,
    "rating": 0.6,
    "open": 1.0,
    "quiet": 0.0,
    "amenity": 0.3,
}


def rank(places: list[Place], *, near: Coord,
         weights: dict[str, float] | None = None) -> list[Place]:
    """Score and sort, writing the reasoning onto each place.

    The score lands in `Place.tags['_score']` and its components in
    `_score_*`, so the UI can show why something is first and the LLM can
    explain it. Underscore-prefixed because they are TERRA's annotations, not
    the provider's tags.
    """
    if not places:
        return []
    w = {**DEFAULT_WEIGHTS, **(weights or {})}

    # Ratings only participate if the set actually has them — otherwise every
    # candidate gets the same neutral value and the term is pure noise that
    # dilutes the terms that do carry signal.
    has_ratings = any(p.rating is not None for p in places)

    scored: list[tuple[float, Place]] = []
    for p in places:
        parts: dict[str, float] = {}

        d = p.distance_m if p.distance_m is not None else spatial.haversine_m(near, p.coord)
        # Exponential decay with a 1.2km half-life: near things separate
        # sharply, far things compress. A linear penalty made everything beyond
        # 3km indistinguishable and everything within 300m indistinguishable
        # too, which is exactly backwards.
        parts["distance"] = math.exp(-d / 1200.0) * w["distance"]

        if has_ratings and w["rating"]:
            if p.rating is not None:
                # Confidence-weighted: 4.9 from 3 reviews is not better than
                # 4.4 from 900. The log term saturates quickly, so a place with
                # 50 reviews counts nearly as much as one with 5000.
                confidence = min(1.0, math.log10((p.rating_count or 1) + 1) / 2.5)
                parts["rating"] = ((p.rating - 3.0) / 2.0) * confidence * w["rating"]
            else:
                parts["rating"] = 0.0

        state = _open_state(p)
        if state is True:
            parts["open"] = 0.5 * w["open"]
        elif state is False:
            # Not a small penalty. A closed place is the wrong answer to
            # "where can I go now", and it should never outrank an open one.
            parts["open"] = -3.0 * w["open"]

        if w["quiet"]:
            parts["quiet"] = _quiet_signal(p) * w["quiet"]
        if w["amenity"]:
            parts["amenity"] = _amenity_signal(p) * w["amenity"]

        total = sum(parts.values())
        p.tags = dict(p.tags or {})
        p.tags["_score"] = f"{total:.3f}"
        for name, value in parts.items():
            p.tags[f"_score_{name}"] = f"{value:.3f}"
        scored.append((total, p))

    scored.sort(key=lambda kv: kv[0], reverse=True)
    return [p for _, p in scored]


def _quiet_signal(p: Place) -> float:
    """A rough, honest guess at how workable somewhere is.

    OSM does not record noise. What it does record is tags that correlate with
    it — a library is quiet by definition, a bar is not, wifi and power sockets
    mean people work there. This is a heuristic and is labelled as one in the
    score breakdown; it is not presented to the user as measured ambience.
    """
    score = 0.0
    cat = (p.category or "").lower()
    if cat in ("library", "coworking_space", "coworking"):
        score += 1.0
    elif cat in ("cafe", "garden", "park"):
        score += 0.4
    elif cat in ("bar", "pub", "nightclub", "fast_food"):
        score -= 0.8
    tags = p.tags or {}
    if (tags.get("internet_access") or "").lower() in ("wlan", "yes", "wifi"):
        score += 0.3
    if (tags.get("outdoor_seating") or "").lower() == "yes":
        score += 0.1
    if (tags.get("smoking") or "").lower() in ("yes", "outside"):
        score -= 0.1
    return score


def _amenity_signal(p: Place) -> float:
    """Completeness of the record. A place with hours, a phone and a website is
    more likely to be a real, operating business than a bare node someone
    dropped on a map five years ago."""
    score = 0.0
    if p.opening_hours:
        score += 0.3
    if p.phone:
        score += 0.2
    if p.website:
        score += 0.2
    if p.address:
        score += 0.2
    if p.wheelchair in ("yes", "limited"):
        score += 0.1
    return score


# ---------------------------------------------------------------------------
# Opening hours
# ---------------------------------------------------------------------------
_DAYS = ("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su")
_HOURS_RE = re.compile(r"(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})")


def _open_state(p: Place, when: datetime | None = None) -> bool | None:
    """True / False / None-for-unknown.

    Tri-state on purpose. Collapsing "unknown" into "closed" hides places that
    are open, and collapsing it into "open" sends someone to a shut door.
    Google answers this directly; for OSM the expression is parsed, and only
    the common cases are attempted — a partial parser that says "I don't know"
    is far better than one that guesses at `Mo-Fr 09:00-12:00,13:00-17:00; PH
    off` and gets it wrong.
    """
    if p.open_now is not None:
        return p.open_now
    expr = (p.opening_hours or "").strip()
    if not expr:
        return None
    if expr in ("24/7", "24 hours"):
        return True

    now = when or datetime.now()
    today = _DAYS[now.weekday()]
    minutes_now = now.hour * 60 + now.minute

    # Two separate facts, and conflating them was a bug. `parsed_any` means
    # the expression was understood at all; `covers_today` means one of its
    # rules applies to today. A place open "Mo-Fr 09:00-17:00" is CLOSED on a
    # Sunday — OSM's semantics are that days not listed are closed — but with a
    # single flag that case came back "unknown", so weekend searches quietly
    # included shut offices with nothing marking them.
    parsed_any = False
    covers_today = False
    for rule in expr.split(";"):
        rule = rule.strip()
        if not rule or "off" in rule.lower() or "closed" in rule.lower():
            continue
        spans = _HOURS_RE.findall(rule)
        if not spans:
            continue
        parsed_any = True
        if not _rule_covers_day(rule, today):
            continue
        covers_today = True
        for h1, m1, h2, m2 in spans:
            start = int(h1) * 60 + int(m1)
            end = int(h2) * 60 + int(m2)
            # A closing time before the opening time means it runs past
            # midnight — "22:00-02:00". Treating it literally makes every late
            # bar permanently closed.
            if end <= start:
                if minutes_now >= start or minutes_now < end:
                    return True
            elif start <= minutes_now < end:
                return True
    if covers_today:
        return False        # a rule applies today and we are outside its hours
    if parsed_any:
        return False        # the week is specified and today is not in it
    return None             # nothing parseable — genuinely unknown


def _rule_covers_day(rule: str, today: str) -> bool:
    """Whether an OSM hours rule applies today. Handles "Mo-Fr", "Sa,Su" and a
    bare time range (which means every day)."""
    head = rule.split(" ")[0] if " " in rule else ""
    if not head or _HOURS_RE.search(head):
        return True
    idx_today = _DAYS.index(today)
    for token in head.split(","):
        token = token.strip()
        if "-" in token:
            a, _, b = token.partition("-")
            if a in _DAYS and b in _DAYS:
                start, end = _DAYS.index(a), _DAYS.index(b)
                if start <= end:
                    if start <= idx_today <= end:
                        return True
                elif idx_today >= start or idx_today <= end:
                    return True
        elif token == today:
            return True
    return False


def _stays_open(p: Place, hours: float) -> bool:
    """Whether a place is open now and plausibly still open in `hours`.

    Unknown hours pass. That is the deliberate choice: excluding every OSM
    place without an `opening_hours` tag would empty the result set in most of
    the world, and the shortlist says so via the score breakdown rather than
    pretending to certainty.
    """
    state = _open_state(p)
    if state is None:
        return True
    if state is False:
        return False
    later = datetime.now().timestamp() + hours * 3600
    return _open_state(p, datetime.fromtimestamp(later)) is not False


__all__ = ["search", "nearest", "quiet_workspace", "rank"]
