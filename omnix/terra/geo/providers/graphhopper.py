"""GraphHopper — routing. Free tier with a key, self-hostable without one.

The middle option between OSRM and Google, and it earns its place for one
reason: it does bicycle and foot profiles properly on the hosted free tier,
which the public OSRM demo does not. When a user asks for a walking route and
no Google key is configured, this is what makes the answer correct rather than
a driving route wearing a walking label.

Sits between OSRM and Google in the chain: better coverage than OSRM, no
per-request cost like Google.
"""

from __future__ import annotations

from ..config import settings
from ..spatial import decode_polyline, simplify
from ..types import Coord, Mode, Route, Step

URL = "https://graphhopper.com/api/1/route"

_PROFILE = {
    Mode.DRIVING: "car",
    Mode.WALKING: "foot",
    Mode.CYCLING: "bike",
}


class GraphHopperProvider:
    name = "graphhopper"

    def available(self) -> bool:
        return settings().has_graphhopper()

    def supports(self, mode: Mode) -> bool:
        return mode in _PROFILE

    def route(self, origin: Coord, destination: Coord, *,
              mode: Mode = Mode.DRIVING, alternatives: int = 1,
              steps: bool = True) -> list[Route]:
        cfg = settings()
        params: list[tuple[str, str]] = [
            ("point", f"{origin.lat:.6f},{origin.lon:.6f}"),
            ("point", f"{destination.lat:.6f},{destination.lon:.6f}"),
            ("profile", _PROFILE.get(mode, "car")),
            ("instructions", "true" if steps else "false"),
            ("calc_points", "true"),
            # GraphHopper defaults to its own encoded polyline at precision 5,
            # same as OSRM. `points_encoded=false` would return raw GeoJSON and
            # triple the payload for no gain.
            ("points_encoded", "true"),
            ("locale", "en"),
            ("key", cfg.keys.graphhopper),
        ]
        if alternatives > 1:
            params += [("algorithm", "alternative_route"),
                       ("alternative_route.max_paths", str(min(alternatives, 3)))]

        data = _get_multi(URL, params)

        out: list[Route] = []
        for p in (data.get("paths") or [])[:max(1, alternatives)]:
            geometry = decode_polyline(p.get("points") or "", precision=5)
            out.append(Route(
                distance_m=float(p.get("distance") or 0.0),
                # GraphHopper reports milliseconds. Reading `time` as seconds
                # turns a 20-minute drive into a 20-day one, and it is exactly
                # the kind of unit error that looks plausible in a unit test.
                duration_s=float(p.get("time") or 0.0) / 1000.0,
                geometry=simplify(geometry, tolerance_m=8.0),
                steps=_steps(p) if steps else [],
                summary=_summary(p),
                mode=mode,
                source=self.name,
            ))
        return out


def _get_multi(url: str, params: list[tuple[str, str]]) -> dict:
    """GET with repeated query keys.

    GraphHopper takes two `point` parameters and a dict cannot hold both. httpx
    accepts a list of pairs, which is the only shape that expresses this — and
    the reason this helper exists rather than the shared `get_json`.
    """
    from .base import client
    resp = client().get(url, params=params)
    resp.raise_for_status()
    return resp.json()


def _steps(path: dict) -> list[Step]:
    points = decode_polyline(path.get("points") or "", precision=5)
    out: list[Step] = []
    for inst in (path.get("instructions") or []):
        interval = inst.get("interval") or [0, 0]
        idx = interval[0] if interval else 0
        out.append(Step(
            instruction=inst.get("text") or "",
            distance_m=float(inst.get("distance") or 0.0),
            duration_s=float(inst.get("time") or 0.0) / 1000.0,
            coord=points[idx] if 0 <= idx < len(points) else None,
        ))
    return out


def _summary(path: dict) -> str:
    by_road: dict[str, float] = {}
    for inst in (path.get("instructions") or []):
        name = inst.get("street_name") or ""
        if name:
            by_road[name] = by_road.get(name, 0.0) + float(inst.get("distance") or 0)
    top = sorted(by_road.items(), key=lambda kv: kv[1], reverse=True)[:2]
    return " and ".join(name for name, _ in top)
