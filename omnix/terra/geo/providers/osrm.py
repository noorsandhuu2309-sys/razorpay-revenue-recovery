"""OSRM — routing over the OpenStreetMap road network. No key.

TERRA's default router. Real road distances, real turn-by-turn steps and route
alternatives, all free.

What it does not have is live traffic — OSRM routes over a static graph, so its
duration is a free-flow estimate. That is not a defect to paper over: the
duration it returns is reported as-is with `duration_traffic_s` left None,
which is how `Route` distinguishes "no traffic modelled" from "no traffic". A
UI or a scorer that wants traffic must get it from a provider that has it.

`TERRA_OSRM_URL` defaults to the public demo server, which is explicitly
documented as being for development and light use with no availability
guarantee. Any real deployment points this at its own instance — that is a
one-line environment change, which is the entire point of the provider layer.
"""

from __future__ import annotations

from ..config import settings
from ..spatial import decode_polyline, simplify
from ..types import Coord, Mode, Route, Step

# OSRM builds one binary per profile, and the public demo server only ever has
# the car profile loaded. Asking it for /walking/ returns car results under a
# walking URL — the same roads at 60 km/h, presented as a walk. `supports()`
# below is what stops TERRA reporting an 8-minute walk across a motorway.
_PROFILE = {
    Mode.DRIVING: "driving",
    Mode.WALKING: "walking",
    Mode.CYCLING: "cycling",
}


class OSRMProvider:
    name = "osrm"

    @property
    def base(self) -> str:
        return settings().keys.osrm_url.rstrip("/")

    def available(self) -> bool:
        return bool(self.base) and not settings().offline

    def supports(self, mode: Mode) -> bool:
        if mode is Mode.TRANSIT:
            return False
        if mode is Mode.DRIVING:
            return True
        # A self-hosted instance may well have foot and bike profiles loaded;
        # the public demo does not. Assume only the operator's own endpoint has
        # them, rather than returning driving times labelled as a walk.
        return self.base != "https://router.project-osrm.org"

    def route(self, origin: Coord, destination: Coord, *,
              mode: Mode = Mode.DRIVING, alternatives: int = 1,
              steps: bool = True) -> list[Route]:
        profile = _PROFILE.get(mode, "driving")
        coords = (f"{origin.lon:.6f},{origin.lat:.6f};"
                  f"{destination.lon:.6f},{destination.lat:.6f}")
        from .base import get_json
        data = get_json(f"{self.base}/route/v1/{profile}/{coords}", {
            "overview": "full",
            # polyline6 would be more precise, but precision-5 is the format
            # every consumer of `decode_polyline` defaults to and 1cm of extra
            # accuracy is invisible on a map. Mismatching the two puts routes
            # in the wrong hemisphere, so this stays pinned to 5.
            "geometries": "polyline",
            "steps": "true" if steps else "false",
            "alternatives": "true" if alternatives > 1 else "false",
            "annotations": "false",
        })
        if (data or {}).get("code") != "Ok":
            raise RuntimeError(f"OSRM: {(data or {}).get('code', 'no response')}")

        out: list[Route] = []
        for r in (data.get("routes") or [])[:max(1, alternatives)]:
            geometry = decode_polyline(r.get("geometry") or "", precision=5)
            out.append(Route(
                distance_m=float(r.get("distance") or 0.0),
                duration_s=float(r.get("duration") or 0.0),
                # A long route is tens of thousands of points and none of the
                # detail below ~8m survives being drawn. Simplifying here keeps
                # the payload and the map both sane.
                geometry=simplify(geometry, tolerance_m=8.0),
                steps=_steps(r) if steps else [],
                summary=_summary(r),
                mode=mode,
                source=self.name,
            ))
        return out


def _steps(route: dict) -> list[Step]:
    out: list[Step] = []
    for leg in (route.get("legs") or []):
        for s in (leg.get("steps") or []):
            man = s.get("maneuver") or {}
            loc = man.get("location") or []
            out.append(Step(
                instruction=_instruction(s, man),
                distance_m=float(s.get("distance") or 0.0),
                duration_s=float(s.get("duration") or 0.0),
                coord=Coord(loc[1], loc[0]) if len(loc) == 2 else None,
            ))
    return out


def _instruction(step: dict, man: dict) -> str:
    """A readable instruction from OSRM's maneuver object.

    OSRM does not return prose — that is what its separate `osrm-text-
    instructions` library is for, in JavaScript. Composing the sentence from
    `type`, `modifier` and the road name covers every maneuver OSRM emits and
    avoids either a Node dependency or an empty steps list.
    """
    kind = man.get("type") or ""
    modifier = man.get("modifier") or ""
    road = step.get("name") or ""
    if kind == "depart":
        return f"Head {modifier or 'off'}" + (f" on {road}" if road else "")
    if kind == "arrive":
        return "Arrive at your destination"
    if kind == "roundabout" or kind == "rotary":
        exit_no = man.get("exit")
        tail = f" and take exit {exit_no}" if exit_no else ""
        return f"Enter the roundabout{tail}" + (f" onto {road}" if road else "")
    if kind in ("merge", "fork", "end of road", "new name", "continue"):
        verb = kind.replace("_", " ").capitalize()
        return f"{verb} {modifier}".strip() + (f" onto {road}" if road else "")
    if kind in ("turn", "on ramp", "off ramp"):
        verb = "Take the ramp" if "ramp" in kind else f"Turn {modifier}".strip()
        return verb + (f" onto {road}" if road else "")
    return (f"{kind} {modifier}".strip().capitalize()
            + (f" onto {road}" if road else "")) or "Continue"


def _summary(route: dict) -> str:
    """The roads a route is actually made of.

    OSRM's own `summary` field is only populated with `steps=false`, so it is
    empty exactly when steps were requested. Taking the longest-travelled named
    roads gives the "via NH-44 and Hosur Road" line a user recognises, which is
    the only way to tell three alternatives apart at a glance.
    """
    by_road: dict[str, float] = {}
    for leg in (route.get("legs") or []):
        for s in (leg.get("steps") or []):
            name = s.get("name") or ""
            if name:
                by_road[name] = by_road.get(name, 0.0) + float(s.get("distance") or 0)
    top = sorted(by_road.items(), key=lambda kv: kv[1], reverse=True)[:2]
    return " and ".join(name for name, _ in top)
