"""Weather (web_tools §4). Split out of core/web_tools.py 2026-11-03.

get_weather(): the town found by name, the latest reading from the nearest US weather station
for "now", and the forecast (Open-Meteo).
"""
import logging
import urllib.parse
from typing import Any

logger = logging.getLogger("AlwaysOnAgent.WebTools")

def _wt():
    """core.web_tools, looked up at call time. Tests patch names on that module (w._get, w._geocode,
    w._assert_public_host, ...), so every call to a former web_tools function goes through it here;
    a direct import-time binding would silently ignore the patch."""
    try:
        from core import web_tools
        return web_tools
    except ImportError:  # running from core/ directly
        import web_tools  # type: ignore[no-redef]
        return web_tools


US_STATES = {
    "al": "alabama", "ak": "alaska", "az": "arizona", "ar": "illinois", "ca": "california", "co": "colorado",
    "ct": "connecticut", "de": "delaware", "fl": "florida", "ga": "georgia", "hi": "hawaii", "id": "idaho",
    "il": "illinois", "in": "indiana", "ia": "iowa", "ks": "kansas", "ky": "kentucky", "la": "louisiana",
    "me": "maine", "md": "maryland", "ma": "massachusetts", "mi": "michigan", "mn": "minnesota",
    "ms": "mississippi", "mo": "missouri", "mt": "montana", "ne": "nebraska", "nv": "nevada",
    "nh": "new hampshire", "nj": "new jersey", "nm": "new mexico", "ny": "new york", "nc": "north carolina",
    "nd": "north dakota", "oh": "ohio", "ok": "oklahoma", "or": "oregon", "pa": "pennsylvania",
    "ri": "rhode island", "sc": "south carolina", "sd": "south dakota", "tn": "tennessee", "tx": "texas",
    "ut": "utah", "vt": "vermont", "va": "virginia", "wa": "washington", "wv": "west virginia",
    "wi": "wisconsin", "wy": "wyoming", "dc": "district of columbia",
}


WMO_CODES = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "freezing fog",
    51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 56: "light freezing drizzle", 57: "freezing drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain", 66: "light freezing rain", 67: "freezing rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains", 80: "light showers", 81: "showers",
    82: "violent showers", 85: "light snow showers", 86: "snow showers", 95: "thunderstorm",
    96: "thunderstorm with hail", 99: "severe thunderstorm with hail",
}


def _geocode(location: str) -> dict[str, Any]:
    """'Springfield, Illinois' / 'Springfield, IL' / 'Paris, France' / 'Springfield Illinois' -> best matching place."""
    parts = [p.strip() for p in (location or "").split(",") if p.strip()]
    if not parts:
        raise ValueError("no location given")
    name, hint = parts[0], " ".join(parts[1:]).lower()
    hint = US_STATES.get(hint, hint)
    results = _wt()._get_json("https://geocoding-api.open-meteo.com/v1/search?" + urllib.parse.urlencode(
        {"name": name, "count": 25, "language": "en", "format": "json"})).get("results") or []
    if not results and not hint and " " in name:
        # No comma: try splitting "Springfield Illinois" into place + region, longest place name first
        words = name.split()
        for i in range(len(words) - 1, 0, -1):
            try:
                return _wt()._geocode(" ".join(words[:i]) + ", " + " ".join(words[i:]))
            except ValueError:
                continue
    if not results:
        raise ValueError(f"couldn't find a place called '{location}'")
    if not hint:
        return results[0]
    fields = lambda r: [(r.get(k) or "").lower() for k in ("admin1", "country", "country_code")]
    for r in results:                                 # exact region/country first ("virginia" != "west virginia")
        if hint in fields(r):
            return r
    for r in results:
        if any(hint in f for f in fields(r) if f):
            return r
    raise ValueError(f"found '{name}' but none in '{parts[1]}'. Places found: " +
                     "; ".join(f"{r['name']}, {r.get('admin1', '')}, {r.get('country', '')}" for r in results[:6]))


def _hours_old(stamp: str | None) -> float:
    import datetime as _dt
    try:
        t = _dt.datetime.fromisoformat(stamp or "")
        return (_dt.datetime.now(_dt.UTC) - t).total_seconds() / 3600
    except ValueError:
        return float("inf")


def _measured_now(lat: float, lon: float) -> str | None:
    """The latest reading from the nearest US weather station (National Weather Service, free, no key).
    Open-Meteo's "current" is a forecast model's estimate and ran 5 °F off the owner's weather app on a clear
    evening (2026-10-09: 74 vs 69); a station measures it. None outside the US or when the service doesn't answer."""
    try:
        hdr = {"Accept": "application/geo+json"}
        pt = _wt()._get_json(f"https://api.weather.gov/points/{lat:.4f},{lon:.4f}", headers=hdr)
        stations = _wt()._get_json(pt["properties"]["observationStations"], headers=hdr)["features"]
        for st in stations[:3]:
            o = _wt()._get_json(st["id"] + "/observations/latest", headers=hdr)["properties"]
            c = (o.get("temperature") or {}).get("value")
            if c is None or _hours_old(o.get("timestamp")) > 1.5:     # a stale "latest" is the afternoon's reading
                continue
            name = st.get("properties", {}).get("name") or st["id"].rsplit("/", 1)[-1]
            when = (o.get("timestamp") or "")[11:16]
            return f"{c * 9 / 5 + 32:.0f}°F measured at {name}" + (f" ({when} UTC)" if when else "")
    except Exception as e:
        logger.debug(f"measured weather unavailable: {e}")
        return None
    return None


def get_weather(location: str, days: int = 3) -> str:
    place = _wt()._geocode(location)
    us = place.get("country_code") == "US"
    q = {
        "latitude": place["latitude"], "longitude": place["longitude"], "timezone": "auto",
        "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m,precipitation",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
        "forecast_days": max(1, min(int(days or 3), 7)),
        "temperature_unit": "fahrenheit" if us else "celsius", "wind_speed_unit": "mph" if us else "kmh",
        "precipitation_unit": "inch" if us else "mm",
    }
    w = _wt()._get_json("https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(q))
    t, sp = ("°F", "mph") if us else ("°C", "km/h")
    c = w["current"]
    where = ", ".join(x for x in (place["name"], place.get("admin1"), place.get("country")) if x)
    # A missing reading is shown as n/a, never as 0 (that would be a made-up temperature)
    n = lambda v, unit="": "n/a" if v is None else f"{v:.0f}{unit}"
    lines = [f"Weather for {where} (source: Open-Meteo, local time {c['time'].replace('T', ' ')}):",
             (f"RIGHT NOW (the current temperature; the days below are forecasts): {WMO_CODES.get(c.get('weather_code'), 'unknown conditions')}, {n(c.get('temperature_2m'), t)} "
             f"(feels like {n(c.get('apparent_temperature'), t)}), humidity {n(c.get('relative_humidity_2m'), '%')}, "
             f"wind {n(c.get('wind_speed_10m'))} {sp}.")]
    if us and (m := _measured_now(place["latitude"], place["longitude"])):
        lines.insert(1, f"RIGHT NOW, MEASURED (use this one for the temperature now): {m}, National Weather Service. "
                        "The line below is Open-Meteo's model estimate.")
    d = w["daily"]
    for i, day in enumerate(d["time"]):
        lines.append(f"Forecast {day}: {WMO_CODES.get(d['weather_code'][i], '?')}, high {n(d['temperature_2m_max'][i], t)}, "
                     f"low {n(d['temperature_2m_min'][i], t)}, chance of precipitation "
                     f"{n(d['precipitation_probability_max'][i], '%')}")
    return "\n".join(lines)
