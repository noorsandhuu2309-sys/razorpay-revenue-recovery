"""Configuration and credentials for the geospatial layer.

Two rules, both enforced here rather than trusted to callers:

  1. **No key ever appears in code, a log line, or an API response.** Keys are
     read from the environment only. `describe()` — the thing the UI and the
     health endpoint render — reports whether a key is *present*, never what it
     is. There is no accessor that returns a key to anything outside a
     provider.

  2. **A missing key is a configuration state, not an error.** TERRA is built
     so that every capability has a keyless path. Google is an upgrade, never a
     requirement, and a fresh clone with no `.env` at all must give a working
     map, working search, working routes and working weather. If that stops
     being true, something in this package has been written wrong.

Everything is read through `settings()`, which caches. `reload()` exists for
tests and for the settings UI; nothing else should call it.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

# Loaded once, lazily, so importing this module has no side effects.
_lock = threading.Lock()
_settings: "Settings | None" = None


def _load_dotenv() -> None:
    """Read `.env` into the environment if python-dotenv is not installed.

    OMNIX does not depend on python-dotenv and this package will not add a
    dependency for twelve lines. Existing environment variables always win, so
    a real deployment's configuration is never overridden by a stray file.
    """
    path = ROOT / ".env"
    if not path.exists():
        return
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except OSError:
        pass


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _flag(name: str, default: bool) -> bool:
    raw = _env(name).lower()
    if not raw:
        return default
    return raw not in ("0", "off", "false", "no")


def _num(name: str, default: float) -> float:
    try:
        return float(_env(name) or default)
    except ValueError:
        return default


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
# TTLs in seconds, per kind of data, chosen by how fast the underlying truth
# actually changes rather than by a single blanket number. Geocoding a street
# address is stable for months; a traffic-aware ETA is worthless in five
# minutes. Getting these wrong in either direction is the main way a geospatial
# system either burns money or lies.
DEFAULT_TTL: dict[str, float] = {
    "geocode": 30 * 24 * 3600.0,     # addresses do not move
    "reverse": 30 * 24 * 3600.0,
    "places": 6 * 3600.0,            # opening hours and closures drift
    "place_detail": 24 * 3600.0,
    "route": 6 * 3600.0,             # the ROADS, not the traffic
    "route_traffic": 180.0,          # the traffic
    "weather": 900.0,
    "air_quality": 1800.0,
    "elevation": 365 * 24 * 3600.0,  # terrain is not news
    "tiles": 7 * 24 * 3600.0,
}

# The basemap. Raster tiles on purpose: vector tiles need a key from every
# provider worth using, and TERRA must render on a fresh clone with no
# configuration. CARTO's basemap CDN is built for public use and — the reason
# it wins here — ships matched dark and light styles, so the map obeys OMNIX's
# theme instead of being the one surface that ignores it.
#
# NOT the openstreetmap.org tile servers. Those are a volunteer-funded service
# with an explicit tile usage policy that an application like this one does not
# qualify under, and pointing at them would be freeloading.
DEFAULT_TILES_DARK = ("https://{s}.basemaps.cartocdn.com/dark_all/"
                      "{z}/{x}/{y}{r}.png")
DEFAULT_TILES_LIGHT = ("https://{s}.basemaps.cartocdn.com/light_all/"
                       "{z}/{x}/{y}{r}.png")
TILE_ATTRIBUTION = ('© <a href="https://www.openstreetmap.org/copyright">'
                    'OpenStreetMap</a> contributors © '
                    '<a href="https://carto.com/attributions">CARTO</a>')

# Nominatim and Overpass both require a real identifying User-Agent and will
# (rightly) block a client that does not send one.
USER_AGENT = "OMNIX-TERRA/1.0 (+https://github.com/omnix; geospatial subsystem)"


@dataclass(frozen=True)
class ProviderKeys:
    """Credentials, read once. Nothing here is ever serialised."""

    google: str = ""
    graphhopper: str = ""
    maptiler: str = ""
    openweather: str = ""
    #: Self-hosted or commercial endpoints, when the operator has one.
    osrm_url: str = ""
    nominatim_url: str = ""
    overpass_url: str = ""


@dataclass
class Settings:
    keys: ProviderKeys = field(default_factory=ProviderKeys)
    ttl: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_TTL))

    #: Master switches. `offline` forces every provider call to fail so the
    #: degraded path can be exercised deliberately rather than by pulling a
    #: network cable.
    offline: bool = False
    google_enabled: bool = True
    cache_enabled: bool = True

    #: Politeness. Nominatim's policy is an absolute maximum of 1 request per
    #: second; Overpass asks for restraint and will return 429 rather than
    #: explain. These are per-provider ceilings the rate limiter enforces, and
    #: lowering them is always safe.
    rate_limits: dict[str, float] = field(default_factory=lambda: {
        "nominatim": 1.0,
        "overpass": 0.5,
        "osrm": 2.0,
        "openmeteo": 10.0,
        "bigdatacloud": 2.0,
        "google": 20.0,
        "graphhopper": 2.0,
    })

    timeout_s: float = 10.0
    max_retries: int = 2

    tiles_dark: str = DEFAULT_TILES_DARK
    tiles_light: str = DEFAULT_TILES_LIGHT
    tile_attribution: str = TILE_ATTRIBUTION

    #: Spatial memory retention. 0 means "keep until deleted"; privacy_mode
    #: stops anything being written at all.
    history_retention_days: float = 90.0
    privacy_mode: bool = False
    history_enabled: bool = True

    # -- capability questions the rest of the package asks -------------------
    def has_google(self) -> bool:
        return bool(self.keys.google) and self.google_enabled and not self.offline

    def has_graphhopper(self) -> bool:
        return bool(self.keys.graphhopper) and not self.offline

    def ttl_for(self, kind: str) -> float:
        return self.ttl.get(kind, 3600.0)

    def describe(self) -> dict:
        """What the UI is allowed to know about configuration.

        Presence booleans only. This is the function that must never grow a
        field containing an actual credential — the health panel renders it
        verbatim and the endpoint serving it is unauthenticated.
        """
        return {
            "offline": self.offline,
            "cacheEnabled": self.cache_enabled,
            "privacyMode": self.privacy_mode,
            "historyEnabled": self.history_enabled,
            "historyRetentionDays": self.history_retention_days,
            "providers": {
                "google": {"configured": bool(self.keys.google),
                           "enabled": self.google_enabled},
                "graphhopper": {"configured": bool(self.keys.graphhopper),
                                "enabled": True},
                "maptiler": {"configured": bool(self.keys.maptiler),
                             "enabled": True},
                "openmeteo": {"configured": True, "enabled": True},
                "nominatim": {"configured": True, "enabled": True},
                "overpass": {"configured": True, "enabled": True},
                "osrm": {"configured": True, "enabled": True},
            },
            "tiles": {
                "dark": self.tiles_dark,
                "light": self.tiles_light,
                "attribution": self.tile_attribution,
            },
            "ttl": self.ttl,
        }


def _build() -> Settings:
    _load_dotenv()
    keys = ProviderKeys(
        google=_env("GOOGLE_MAPS_API_KEY") or _env("TERRA_GOOGLE_API_KEY"),
        graphhopper=_env("GRAPHHOPPER_API_KEY"),
        maptiler=_env("MAPTILER_API_KEY"),
        openweather=_env("OPENWEATHER_API_KEY"),
        osrm_url=_env("TERRA_OSRM_URL", "https://router.project-osrm.org"),
        nominatim_url=_env("TERRA_NOMINATIM_URL",
                           "https://nominatim.openstreetmap.org"),
        overpass_url=_env("TERRA_OVERPASS_URL",
                          "https://overpass-api.de/api/interpreter"),
    )
    ttl = dict(DEFAULT_TTL)
    for kind in list(ttl):
        override = _env(f"TERRA_TTL_{kind.upper()}")
        if override:
            try:
                ttl[kind] = float(override)
            except ValueError:
                pass
    return Settings(
        keys=keys,
        ttl=ttl,
        offline=_flag("TERRA_OFFLINE", False),
        google_enabled=_flag("TERRA_GOOGLE", True),
        cache_enabled=_flag("TERRA_CACHE", True),
        timeout_s=_num("TERRA_TIMEOUT", 10.0),
        max_retries=int(_num("TERRA_MAX_RETRIES", 2)),
        tiles_dark=_env("TERRA_TILES_DARK", DEFAULT_TILES_DARK),
        tiles_light=_env("TERRA_TILES_LIGHT", DEFAULT_TILES_LIGHT),
        tile_attribution=_env("TERRA_TILE_ATTRIBUTION", TILE_ATTRIBUTION),
        history_retention_days=_num("TERRA_HISTORY_DAYS", 90.0),
        privacy_mode=_flag("TERRA_PRIVACY_MODE", False),
        history_enabled=_flag("TERRA_HISTORY", True),
    )


def settings() -> Settings:
    global _settings
    if _settings is not None:
        return _settings
    with _lock:
        if _settings is None:
            _settings = _build()
    return _settings


def reload() -> Settings:
    """Re-read the environment. For tests and the settings UI only."""
    global _settings
    with _lock:
        _settings = None
    return settings()
