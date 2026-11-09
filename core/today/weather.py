"""Today §3: the weather — the weather now at your town, for the header.

Moved into core/today/weather.py on 2026-11-04 (was core/today_weather.py, split out of
core/today.py §3 on 2026-11-03); core/today_weather.py is now a re-export shim.
"""
import urllib.parse
from typing import Any

# open-meteo weather codes -> a few words (https://open-meteo.com/en/docs, WMO 4677)
WEATHER_WORDS = {0: "Clear", 1: "Mostly clear", 2: "Partly cloudy", 3: "Cloudy", 45: "Fog", 48: "Fog",
                 51: "Drizzle", 53: "Drizzle", 55: "Drizzle", 56: "Freezing drizzle", 57: "Freezing drizzle",
                 61: "Light rain", 63: "Rain", 65: "Heavy rain", 66: "Freezing rain", 67: "Freezing rain",
                 71: "Light snow", 73: "Snow", 75: "Heavy snow", 77: "Snow", 80: "Showers", 81: "Showers",
                 82: "Heavy showers", 85: "Snow showers", 86: "Snow showers", 95: "Thunderstorms",
                 96: "Thunderstorms", 99: "Thunderstorms"}


def current_weather(location: str) -> dict[str, Any] | None:
    """Now, at the owner's town (Proactive settings "location"): {"temp_f", "summary", "place", "is_day"}, plus what
    the app's sky is drawn from (2026-10-28: a real sun, moon, clouds and rain): "code" (WMO), "cloud_cover" (%),
    "rain_mm" and "snow_cm" (the last 15 min), "wind_mph", "wind_dir" (degrees it blows from), "sunrise" and "sunset"
    (today's, unix seconds)."""
    if not (location or "").strip():
        return None
    from core import web_tools
    place = web_tools._geocode(location)
    w = web_tools._get_json("https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode({
        "latitude": place["latitude"], "longitude": place["longitude"], "timezone": "auto", "timeformat": "unixtime",
        "current": "temperature_2m,weather_code,is_day,cloud_cover,rain,showers,snowfall,wind_speed_10m,"
                   "wind_direction_10m",
        "daily": "sunrise,sunset", "forecast_days": 1, "temperature_unit": "fahrenheit", "wind_speed_unit": "mph"}))
    cur, daily = w.get("current") or {}, w.get("daily") or {}
    if cur.get("temperature_2m") is None:
        return None
    num = lambda k: float(cur.get(k) or 0)
    first = lambda k: int((daily.get(k) or [0])[0] or 0)
    code = int(cur.get("weather_code") or 0)
    return {"temp_f": round(float(cur["temperature_2m"])), "summary": WEATHER_WORDS.get(code, ""),
            "place": place.get("name", ""), "is_day": bool(cur.get("is_day", 1)), "code": code,
            "cloud_cover": round(num("cloud_cover")), "rain_mm": round(num("rain") + num("showers"), 2),
            "snow_cm": round(num("snowfall"), 2), "wind_mph": round(num("wind_speed_10m"), 1),
            "wind_dir": round(num("wind_direction_10m")), "sunrise": first("sunrise"), "sunset": first("sunset")}
