"""Route scoring — configurable, explainable, and learnable.

The brief is explicit that the weights must not be hard-coded, and there is a
real reason beyond preference: the right weights are not knowable in advance.
Someone commuting in Bengaluru traffic and someone driving across Rajasthan
want opposite things from the same scorer, and neither of them wants whatever a
developer guessed.

So the model is:

    score = Σ  weight[factor] × normalised_penalty[factor]

with three properties that matter:

  * **Penalties are normalised across the alternatives being compared**, not
    against absolute constants. "20 minutes" is not inherently good or bad —
    it is good if the other option is 35 and bad if the other is 12. Comparing
    against absolutes made the scorer rank a set of three similar routes almost
    identically, which is the one situation where a user needs a
    recommendation.

  * **Weights come from the database**, seeded with defaults and updated by
    `learn_from_choice`. A weight the user has locked is never touched.

  * **Every term is returned.** `Route.score_parts` carries the breakdown so
    the UI can say "chosen for less traffic despite being 1.2 km longer",
    which is the difference between a recommendation and an assertion.

Lower penalty is better; the final score is negated so that higher score is
better and sorting is the obvious direction.
"""

from __future__ import annotations

from ..types import Route

#: Seed weights. Deliberately mild — the scorer should barely disagree with
#: "fastest" until it has learned something, because a strong opinion held on
#: no evidence is worse than no opinion.
DEFAULT_WEIGHTS: dict[str, float] = {
    "time": 1.0,          # travel time, the baseline everyone expects
    "traffic": 0.6,       # how much of that time is congestion
    "distance": 0.25,     # fuel and wear, not speed
    "weather": 0.2,       # exposure, and it only bites for walk/cycle
    "tolls": 0.15,
    "complexity": 0.1,    # turn count — a stressful route is a real cost
}

FACTORS = tuple(DEFAULT_WEIGHTS)


def weights_for(workspace_id: str | None) -> dict[str, float]:
    """Effective weights: defaults overlaid with anything stored."""
    w = dict(DEFAULT_WEIGHTS)
    if not workspace_id:
        return w
    try:
        from ....core import db
        from ....core.schema import GeoPreference
        with db.session() as s:
            rows = (s.query(GeoPreference)
                    .filter(GeoPreference.workspace_id == workspace_id).all())
            for row in rows:
                if row.key in w:
                    w[row.key] = float(row.weight)
    except Exception:
        pass
    return w


def set_weight(workspace_id: str, key: str, weight: float,
               *, locked: bool = True) -> bool:
    """Set one weight explicitly. `locked` protects it from learning."""
    if key not in DEFAULT_WEIGHTS:
        return False
    try:
        from ....core import db
        from ....core.schema import GeoPreference
        with db.session() as s:
            row = (s.query(GeoPreference)
                   .filter(GeoPreference.workspace_id == workspace_id,
                           GeoPreference.key == key).one_or_none())
            if row is None:
                row = GeoPreference(workspace_id=workspace_id, key=key)
                s.add(row)
            row.weight = max(0.0, min(3.0, float(weight)))
            row.locked = locked
        return True
    except Exception:
        return False


def score(routes: list[Route], *, workspace_id: str | None = None,
          weights: dict[str, float] | None = None,
          weather_penalty: float = 0.0) -> list[Route]:
    """Score routes against each other and sort best-first.

    `weather_penalty` is 0..1, supplied by the caller because only the caller
    knows the mode and the conditions — rain matters enormously on a bicycle
    and not at all in a car, and the scorer should not be reaching for a
    weather provider on its own.

    Mutates and returns the same Route objects; a single route is scored 0 with
    an empty breakdown, since there is nothing to compare it against and an
    invented number would imply otherwise.
    """
    if not routes:
        return []
    w = weights or weights_for(workspace_id)

    if len(routes) == 1:
        routes[0].score = 0.0
        routes[0].score_parts = {}
        return routes

    times = [r.duration_traffic_s or r.duration_s for r in routes]
    distances = [r.distance_m for r in routes]
    # Congestion delay: how much slower than free-flow. Only meaningful when
    # the provider modelled traffic at all; otherwise every route scores 0 on
    # this term and it drops out of the comparison, which is correct.
    delays = [max(0.0, (r.duration_traffic_s or r.duration_s) - r.duration_s)
              for r in routes]
    turns = [float(len(r.steps)) for r in routes]

    for i, route in enumerate(routes):
        parts: dict[str, float] = {
            "time": w["time"] * _norm(times[i], times),
            "traffic": w["traffic"] * _norm(delays[i], delays),
            "distance": w["distance"] * _norm(distances[i], distances),
            "complexity": w["complexity"] * _norm(turns[i], turns),
        }
        if route.tolls is not None:
            parts["tolls"] = w["tolls"] * (1.0 if route.tolls else 0.0)
        if weather_penalty:
            parts["weather"] = w["weather"] * weather_penalty

        penalty = sum(parts.values())
        route.score = round(-penalty, 4)
        # Zero-valued terms are KEPT. Filtering them out looked tidy and made
        # the winner — which by definition scores 0 on every factor it leads on
        # — come back with an empty breakdown, indistinguishable from a route
        # that was never compared to anything. `explain` then told the user
        # "only one route was available" while showing them three.
        route.score_parts = {k: round(v, 4) for k, v in parts.items()}

    routes.sort(key=lambda r: r.score or 0.0, reverse=True)
    return routes


def _norm(value: float, values: list[float]) -> float:
    """Position within the range of the alternatives, 0 (best) to 1 (worst).

    Range-relative rather than absolute so the scorer stays discriminating when
    routes are similar. When every option is identical the spread is zero and
    the term contributes nothing — which is right: a factor that does not
    distinguish the options should not influence the choice.
    """
    lo, hi = min(values), max(values)
    if hi - lo < 1e-9:
        return 0.0
    return (value - lo) / (hi - lo)


def explain(route: Route) -> str:
    """One sentence naming what drove this route's score.

    Reports the two largest penalties, because those are the terms the user
    would argue with. A route with no breakdown says so rather than inventing
    a justification.
    """
    if not route.score_parts:
        return "Only one route was available, so nothing was traded off."
    ranked = sorted(route.score_parts.items(), key=lambda kv: kv[1],
                    reverse=True)
    worst = [k for k, v in ranked if v > 0.01][:2]
    if not worst:
        return "Best on every factor considered."
    labels = {"time": "travel time", "traffic": "congestion",
              "distance": "distance", "weather": "exposure to weather",
              "tolls": "tolls", "complexity": "number of turns"}
    return "Costs the most on " + " and ".join(labels.get(k, k) for k in worst)


# ---------------------------------------------------------------------------
# Learning
# ---------------------------------------------------------------------------
def learn_from_choice(workspace_id: str, routes: list[Route],
                      chosen_index: int, *, rate: float = 0.08) -> dict:
    """Nudge weights toward whatever made the chosen route attractive.

    The rule: if the user took a route that was WORSE than average on a factor,
    they evidently do not care much about that factor, so its weight goes down;
    if they took one that was better, it goes up. Small steps, bounded range,
    and locked weights are skipped entirely.

    This is deliberately timid. Preference learning that moves fast is
    indistinguishable from a system that has developed opinions the user never
    expressed and cannot find the switch for — so it takes many consistent
    choices to shift anything, and `/api/terra/geo/preferences` shows the
    current state and can reset it.
    """
    if not (0 <= chosen_index < len(routes)) or len(routes) < 2:
        return {}
    chosen = routes[chosen_index]
    if not chosen.score_parts:
        return {}

    updated: dict[str, float] = {}
    try:
        from ....core import db
        from ....core.schema import GeoPreference
        current = weights_for(workspace_id)
        with db.session() as s:
            for factor, penalty in chosen.score_parts.items():
                others = [r.score_parts.get(factor, 0.0)
                          for i, r in enumerate(routes) if i != chosen_index]
                if not others:
                    continue
                average = sum(others) / len(others)
                # Positive delta = the chosen route was worse on this factor.
                delta = penalty - average
                if abs(delta) < 0.02:
                    continue

                row = (s.query(GeoPreference)
                       .filter(GeoPreference.workspace_id == workspace_id,
                               GeoPreference.key == factor).one_or_none())
                if row is not None and row.locked:
                    continue
                if row is None:
                    row = GeoPreference(workspace_id=workspace_id, key=factor,
                                        weight=current.get(factor, 0.5))
                    s.add(row)
                new = max(0.0, min(3.0, float(row.weight) - delta * rate))
                row.weight = new
                row.observations = int(row.observations or 0) + 1
                updated[factor] = round(new, 4)
    except Exception:
        return {}
    return updated


def reset(workspace_id: str) -> int:
    """Forget everything learned. The switch that has to exist."""
    try:
        from ....core import db
        from ....core.schema import GeoPreference
        with db.session() as s:
            return (s.query(GeoPreference)
                    .filter(GeoPreference.workspace_id == workspace_id)
                    .delete(synchronize_session=False))
    except Exception:
        return 0


__all__ = ["score", "explain", "weights_for", "set_weight",
           "learn_from_choice", "reset", "DEFAULT_WEIGHTS", "FACTORS"]
