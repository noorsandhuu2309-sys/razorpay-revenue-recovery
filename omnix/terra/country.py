"""Country intelligence cards — a live dossier per country.

Assembles, in one call, what a reader clicking a country actually wants:

    identity    capital, region, income level, coordinates       (World Bank)
    economy     GDP, GDP/capita, inflation, unemployment,
                military spend, trade openness                   (World Bank)
    currency    code and current rate against USD    (static ISO-4217 + Frankfurter)
    live        local time and weather                           (existing tools)
    news        recent headlines about this country               (TERRA corpus)
    risk        the five-dimension risk profile                   (risk.py)
    graph       the country's strongest relationships             (graph.py)
    summary     an LLM reading of all of the above

Every upstream is keyless and every one is fetched concurrently and
independently — a card with the economy panel missing because the World Bank
timed out is still a useful card, so nothing here fails as a unit.

World Bank data is authoritative but LAGGED, often by a year or two. The card
reports the observation year with every figure rather than presenting a 2023
inflation number as today's, because a stale number shown as current is worse
than no number.
"""

from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx

from . import ontology as onto

UA = "OMNIX-TERRA/1.0"
# REST Countries moved behind an API key in v5 and its v3 endpoints now answer
# with a deprecation notice instead of data, so country identity comes from the
# World Bank's own country endpoint — already a dependency here, keyless, and
# consistent with the economic figures by construction.
WB_COUNTRY_URL = "https://api.worldbank.org/v2/country/{iso}"
WB_URL = "https://api.worldbank.org/v2/country/{iso}/indicator/{ind}"
FX_URL = "https://api.frankfurter.app/latest"

# ISO-3166-2 -> ISO-4217. Static because it is stable, and because the only
# keyless services that carry it are the ones that just went behind a key.
CURRENCY_OF = {
    "US": "USD", "GB": "GBP", "EU": "EUR", "DE": "EUR", "FR": "EUR",
    "IT": "EUR", "ES": "EUR", "NL": "EUR", "BE": "EUR", "AT": "EUR",
    "PT": "EUR", "IE": "EUR", "FI": "EUR", "GR": "EUR", "SK": "EUR",
    "SI": "EUR", "LT": "EUR", "LV": "EUR", "EE": "EUR", "CY": "EUR",
    "MT": "EUR", "LU": "EUR", "HR": "EUR", "JP": "JPY", "CN": "CNY",
    "IN": "INR", "RU": "RUB", "BR": "BRL", "CA": "CAD", "AU": "AUD",
    "NZ": "NZD", "CH": "CHF", "SE": "SEK", "NO": "NOK", "DK": "DKK",
    "PL": "PLN", "CZ": "CZK", "HU": "HUF", "RO": "RON", "BG": "BGN",
    "TR": "TRY", "ZA": "ZAR", "MX": "MXN", "AR": "ARS", "CL": "CLP",
    "CO": "COP", "PE": "PEN", "KR": "KRW", "ID": "IDR", "MY": "MYR",
    "SG": "SGD", "TH": "THB", "PH": "PHP", "VN": "VND", "TW": "TWD",
    "HK": "HKD", "PK": "PKR", "BD": "BDT", "LK": "LKR", "NP": "NPR",
    "IL": "ILS", "SA": "SAR", "AE": "AED", "QA": "QAR", "KW": "KWD",
    "BH": "BHD", "OM": "OMR", "JO": "JOD", "EG": "EGP", "MA": "MAD",
    "DZ": "DZD", "TN": "TND", "NG": "NGN", "KE": "KES", "GH": "GHS",
    "ET": "ETB", "TZ": "TZS", "UG": "UGX", "ZW": "ZWL", "ZM": "ZMW",
    "AO": "AOA", "MZ": "MZN", "IR": "IRR", "IQ": "IQD", "SY": "SYP",
    "LB": "LBP", "YE": "YER", "AF": "AFN", "UA": "UAH", "BY": "BYN",
    "KZ": "KZT", "UZ": "UZS", "AZ": "AZN", "AM": "AMD", "GE": "GEL",
    "RS": "RSD", "BA": "BAM", "MK": "MKD", "AL": "ALL", "IS": "ISK",
    "MM": "MMK", "KH": "KHR", "LA": "LAK", "MN": "MNT", "KP": "KPW",
    "VE": "VES", "CU": "CUP", "HT": "HTG", "DO": "DOP", "GT": "GTQ",
    "CR": "CRC", "PA": "PAB", "UY": "UYU", "PY": "PYG", "BO": "BOB",
    "EC": "USD", "SV": "USD", "CD": "CDF", "SD": "SDG", "SO": "SOS",
    "LY": "LYD", "SN": "XOF", "ML": "XOF", "NE": "XOF", "TD": "XAF",
    "CM": "XAF", "RW": "RWF", "MW": "MWK", "BW": "BWP", "NA": "NAD",
}

INDICATORS = {
    "gdp":            ("NY.GDP.MKTP.CD",    "GDP",                 "USD"),
    "gdp_per_capita": ("NY.GDP.PCAP.CD",    "GDP per capita",      "USD"),
    "inflation":      ("FP.CPI.TOTL.ZG",    "Inflation (CPI)",     "%"),
    "population":     ("SP.POP.TOTL",       "Population",          "people"),
    "unemployment":   ("SL.UEM.TOTL.ZS",    "Unemployment",        "%"),
    "military_spend": ("MS.MIL.XPND.GD.ZS", "Military spend",      "% of GDP"),
    "trade_openness": ("NE.TRD.GNFS.ZS",    "Trade (exp+imp)",     "% of GDP"),
    "gdp_growth":     ("NY.GDP.MKTP.KD.ZG", "GDP growth",          "%"),
}

# Cards are expensive (up to 11 upstream calls) and country facts move slowly.
_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_TTL = 6 * 3600
_CACHE_LOCK = threading.Lock()


def _cached(key: str):
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < _CACHE_TTL:
        return hit[1]
    return None


def _store(key: str, value: dict) -> None:
    with _CACHE_LOCK:
        _CACHE[key] = (time.time(), value)


def _get_json(url: str, params: dict | None = None, timeout: float = 10.0):
    """GET + parse, tolerant of the World Bank's inconsistent encoding.

    Its responses carry a UTF-8 BOM on some indicators and not others, which
    makes the standard json parser raise on exactly those indicators — the
    symptom is a card where GDP and unemployment are silently missing while
    inflation is present. Decoding as utf-8-sig removes the BOM when there is
    one and changes nothing when there isn't.
    """
    try:
        r = httpx.get(url, params=params, timeout=timeout,
                      follow_redirects=True, headers={"User-Agent": UA})
        r.raise_for_status()
        return json.loads(r.content.decode("utf-8-sig", "replace"))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Upstream fetchers — each returns a partial dict or {}
# ---------------------------------------------------------------------------
def fetch_profile(iso: str) -> dict:
    data = _get_json(WB_COUNTRY_URL.format(iso=iso), {"format": "json"})
    if not isinstance(data, list) or len(data) < 2 or not data[1]:
        return {}
    rec = data[1][0]
    code = CURRENCY_OF.get(iso.upper(), "")
    lat, lon = rec.get("latitude"), rec.get("longitude")
    return {
        "official_name": rec.get("name", ""),
        "capital": rec.get("capitalCity", ""),
        "region": (rec.get("region") or {}).get("value", ""),
        "subregion": (rec.get("adminregion") or {}).get("value", ""),
        "income_level": (rec.get("incomeLevel") or {}).get("value", ""),
        "lending_type": (rec.get("lendingType") or {}).get("value", ""),
        "iso3": rec.get("id", ""),
        "latlng": [float(lat), float(lon)] if lat and lon else [],
        "flag": _flag_emoji(iso),
        "currency_code": code,
    }


def _flag_emoji(iso: str) -> str:
    iso = (iso or "").upper()
    if len(iso) != 2 or not iso.isalpha():
        return "🌐"
    return chr(0x1F1E6 + ord(iso[0]) - 65) + chr(0x1F1E6 + ord(iso[1]) - 65)


def fetch_indicator(iso: str, key: str) -> dict:
    """Latest available value for one indicator, plus a short history.

    Queried as a DATE RANGE rather than with `mrnev=1`. Semantically mrnev is
    exactly right ("most recent non-empty value") but on this API it is
    pathologically slow for several of these series — GDP, GDP per capita and
    trade openness each took 15s+ and timed out, while the same series over an
    explicit year range return in three. The range also gives a sparkline for
    free, which mrnev cannot.
    """
    ind, label, unit = INDICATORS[key]
    data = _get_json(WB_URL.format(iso=iso, ind=ind),
                     {"format": "json", "date": "2016:2026", "per_page": 60},
                     timeout=18.0)
    if not isinstance(data, list) or len(data) < 2 or not data[1]:
        return {}
    rows = [r for r in data[1] if r.get("value") is not None]
    if not rows:
        return {}
    rows.sort(key=lambda r: r.get("date", ""), reverse=True)
    latest = rows[0]
    history = [{"year": r.get("date", ""), "value": r.get("value")}
               for r in reversed(rows[:10])]
    prior = rows[1]["value"] if len(rows) > 1 else None
    change = None
    if prior not in (None, 0):
        try:
            change = round((latest["value"] - prior) / abs(prior) * 100, 1)
        except (TypeError, ZeroDivisionError):
            change = None
    return {key: {"value": latest["value"], "year": latest.get("date", ""),
                  "label": label, "unit": unit,
                  "display": _fmt(latest["value"], unit),
                  "history": history, "change_pct": change}}


def _fmt(value: float, unit: str) -> str:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return str(value)
    if unit == "USD":
        for div, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
            if abs(v) >= div:
                return f"${v / div:.2f}{suffix}"
        return f"${v:,.0f}"
    if unit == "people":
        for div, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "k")):
            if abs(v) >= div:
                return f"{v / div:.2f}{suffix}"
        return f"{v:,.0f}"
    if unit in ("%", "% of GDP"):
        return f"{v:.1f}%"
    return f"{v:,.2f}"


def fetch_fx(code: str) -> dict:
    if not code or code == "USD":
        return {}
    data = _get_json(FX_URL, {"from": "USD", "to": code})
    if not data or "rates" not in data:
        return {}
    rate = (data.get("rates") or {}).get(code)
    if rate is None:
        return {}
    return {"usd_rate": rate, "fx_date": data.get("date", ""),
            "fx_display": f"1 USD = {rate:,.2f} {code}"}


def fetch_weather(lat: float, lon: float) -> dict:
    try:
        from ..tools import weather as weather_mod
        result = weather_mod.get_weather(lat=lat, lon=lon)
    except Exception:
        return {}
    if result.get("status") != "success":
        return {}
    return {"weather": result}


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------
def economy(iso: str) -> dict:
    """All World Bank indicators for one country.

    Concurrency is capped at 3 deliberately. Firing all eight at once looks
    faster and is not: the API throttles a burst from one client, and the
    observed result was fewer indicators returned, not more — eight parallel
    requests resolved 0-3 series while three at a time resolve nearly all of
    them. Slower and complete beats faster and half-empty on a data card.
    """
    cache_key = f"econ:{iso}"
    hit = _cached(cache_key)
    if hit is not None:
        return hit
    out: dict = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        for partial in pool.map(lambda k: fetch_indicator(iso, k), INDICATORS):
            out.update(partial or {})
    # Never cache a mostly-failed fetch for six hours — a transient throttle
    # would otherwise pin an empty economy panel onto the country for the rest
    # of the session.
    if len(out) >= len(INDICATORS) // 2:
        _store(cache_key, out)
    return out


def profile(iso: str) -> dict:
    cache_key = f"profile:{iso}"
    hit = _cached(cache_key)
    if hit is not None:
        return hit
    data = fetch_profile(iso)
    if data:
        fx = fetch_fx(data.get("currency_code", ""))
        data.update(fx)
        _store(cache_key, data)
    return data


def card(iso: str, articles: list[dict], risk_scores: dict, kg,
         use_llm: bool = True) -> dict:
    """The full country intelligence card."""
    iso = (iso or "").upper()
    name = onto.country_name(iso)
    if not name or name == iso:
        return {"status": "unknown", "iso2": iso,
                "error": f"'{iso}' is not a country in the ontology."}

    point = onto.country_point(iso) or (0.0, 0.0)
    prof: dict = {}
    econ: dict = {}
    wx: dict = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        f_prof = pool.submit(profile, iso)
        f_econ = pool.submit(economy, iso)
        f_wx = pool.submit(fetch_weather, point[0], point[1])
        prof = f_prof.result() or {}
        econ = f_econ.result() or {}
        wx = f_wx.result() or {}

    # Weather is nicer from the capital than from the polygon centroid, which
    # for a large country can be an empty plain.
    if prof.get("latlng") and len(prof["latlng"]) == 2:
        capital_wx = fetch_weather(prof["latlng"][0], prof["latlng"][1])
        if capital_wx:
            wx = capital_wx

    country_articles = [a for a in articles if iso in (a.get("countries") or [])]
    country_articles.sort(key=lambda a: -a.get("published_ts", 0))
    risk = risk_scores.get(iso) or {
        "score": 0.0, "band": "calm", "color": "#2f6b4a",
        "dimensions": {}, "articles": 0, "thin": True, "evidence": {},
    }

    node_id = f"country:{iso}"
    relations = kg.neighbors(node_id, limit=16) if kg else []

    card_data = {
        "status": "ok",
        "iso2": iso,
        "name": name,
        "flag": prof.get("flag", ""),
        "profile": prof,
        "economy": econ,
        "weather": wx.get("weather"),
        "risk": risk,
        "articles": [{"title": a["title"], "url": a.get("url", ""),
                      "source": a.get("source", ""), "ts": a.get("published_ts", 0),
                      "sentiment": a.get("sentiment", 0),
                      "confidence": a.get("confidence", 0.6)}
                     for a in country_articles[:12]],
        "article_count": len(country_articles),
        "relations": [{"name": e["node"]["name"], "id": e["node"]["id"],
                       "type": e["node"]["type"], "glyph": e["node"]["glyph"],
                       "color": e["node"]["color"],
                       "relation": e["relation_label"],
                       "weight": e["weight"], "sentiment": e["sentiment"],
                       "articles": e["articles"]}
                      for e in relations],
        "point": {"lat": point[0], "lon": point[1]},
        "summary": "",
        "summary_mode": "none",
        "generated_at": time.time(),
        "sources": ["World Bank", "Frankfurter (FX)", "Open-Meteo",
                    "TERRA news corpus"],
    }
    if use_llm:
        card_data["summary"] = _summarize(card_data)
        card_data["summary_mode"] = "llm" if card_data["summary"] else "none"
    return card_data


_SUMMARY_SYSTEM = (
    "You are an intelligence analyst writing a country brief. You are given "
    "structured live data about one country. Write 4-6 sentences covering: the "
    "current situation, the economic picture, and the main risk.\n\n"
    "Rules:\n"
    "- Use ONLY the data provided. Every economic figure has an observation "
    "year attached — if a figure is from an earlier year, say so rather than "
    "implying it is current.\n"
    "- Note explicitly when the news sample is thin.\n"
    "- No preamble, no bullet points, no headings. Plain prose."
)


def _summarize(card_data: dict) -> str:
    try:
        from ..squad.base import MODEL_SMART, run_llm
    except Exception:
        return ""
    prof = card_data.get("profile") or {}
    econ = card_data.get("economy") or {}
    risk = card_data.get("risk") or {}

    econ_lines = [f"- {v['label']}: {v['display']} ({v['year']})"
                  for v in econ.values() if isinstance(v, dict)]
    news_lines = [f"- ({a['source']}) {a['title']}"
                  for a in card_data.get("articles", [])[:8]]
    rel_lines = [f"- {r['relation']} {r['name']}"
                 for r in card_data.get("relations", [])[:8]]
    dims = ", ".join(f"{k} {v}" for k, v in (risk.get("dimensions") or {}).items()
                     if v > 0) or "no scored risk"

    prompt = (
        f"COUNTRY: {card_data['name']} ({card_data['iso2']})\n"
        f"Capital: {prof.get('capital', 'unknown')} · Region: "
        f"{prof.get('region', '')}/{prof.get('subregion', '')}\n"
        f"Currency: {prof.get('currency_code', '')} "
        f"{prof.get('fx_display', '')}\n\n"
        f"ECONOMY (World Bank, year shown per figure):\n"
        + ("\n".join(econ_lines) or "- no data available") + "\n\n"
        f"RISK PROFILE: overall {risk.get('score', 0)}/100 "
        f"({risk.get('band', 'unknown')}) from {risk.get('articles', 0)} "
        f"articles. Dimensions: {dims}\n\n"
        f"RECENT COVERAGE ({card_data.get('article_count', 0)} articles in "
        f"corpus):\n" + ("\n".join(news_lines) or "- none in the window") + "\n\n"
        f"GRAPH RELATIONSHIPS:\n" + ("\n".join(rel_lines) or "- none"))

    return (run_llm(MODEL_SMART, _SUMMARY_SYSTEM, prompt,
                    temperature=0.3) or "").strip()[:1600]
