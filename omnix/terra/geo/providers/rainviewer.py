"""RainViewer — global precipitation radar tiles. No key.

The one genuinely free weather *raster* source. Everything else that renders
weather as map tiles (OpenWeatherMap, Tomorrow.io, AerisWeather) requires a key
even for a hobby tier, and TERRA's rule is that a fresh clone with no `.env`
gets a working product.

RainViewer's model is two-step and worth understanding, because it is what makes
an animated radar loop possible without a key:

  1. `weather-maps.json` is an INDEX. It lists the last ~2 hours of radar scans
     as timestamped path fragments, plus a short nowcast into the future.
  2. Each path is then a normal `{z}/{x}/{y}` tile template.

So the index has to be re-fetched every ten minutes or so (scans age out and
the paths 404), while the tiles themselves are immutable and cache forever.
That is why `frames()` is cached for 5 minutes and the tile URLs are not cached
by TERRA at all — the browser's own HTTP cache handles those.

The colour scheme and smoothing are parameters on the tile path rather than
headers: `/256/{z}/{x}/{y}/{scheme}/{smooth}_{snow}.png`.
"""

from __future__ import annotations

from ..config import settings
from .base import get_json

INDEX_URL = "https://api.rainviewer.com/public/weather-maps.json"

#: RainViewer's palette ids. 4 ("Universal Blue") reads best over a dark
#: basemap — the default green/yellow scheme fights OMNIX's accent and makes
#: light rain nearly invisible on black.
DEFAULT_SCHEME = 4


class RainViewerProvider:
    name = "rainviewer"

    def available(self) -> bool:
        return not settings().offline

    def frames(self, *, scheme: int = DEFAULT_SCHEME, smooth: bool = True,
               snow: bool = True) -> dict:
        """Radar frames as ready-to-use tile templates, oldest first.

        Returns past scans and nowcast separately. The distinction matters and
        is passed through to the UI: a past frame is an OBSERVATION and a
        nowcast frame is a PREDICTION, and animating them as one indistinct
        loop would present a forecast as a measurement — the same honesty rule
        `Freshness` exists to enforce everywhere else in this package.
        """
        data = get_json(INDEX_URL)
        host = (data or {}).get("host") or "https://tilecache.rainviewer.com"
        radar = (data or {}).get("radar") or {}
        options = f"{DEFAULT_SCHEME if scheme is None else scheme}/" \
                  f"{1 if smooth else 0}_{1 if snow else 0}.png"

        def build(entries: list, kind: str) -> list[dict]:
            out = []
            for e in entries or []:
                path = e.get("path")
                if not path:
                    continue
                out.append({
                    "time": e.get("time"),
                    "kind": kind,
                    "url": f"{host}{path}/256/{{z}}/{{x}}/{{y}}/{options}",
                })
            return out

        past = build(radar.get("past") or [], "observed")
        nowcast = build(radar.get("nowcast") or [], "forecast")
        return {
            "frames": past + nowcast,
            "pastCount": len(past),
            "nowcastCount": len(nowcast),
            "attribution": '<a href="https://www.rainviewer.com/">RainViewer</a>',
            "generated": (data or {}).get("generated"),
        }
