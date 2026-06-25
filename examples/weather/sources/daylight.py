"""Daylight source: per-city sunrise/sunset/day-length from sunrise-sunset.org (keyless).

A second provider (not Open-Meteo), joined to the weather grain on (city, date). mode="merge"
by (city, date), cursor the day's UTC-midnight epoch, 1-day overlap. One request per
(city, date); the `results` object carries ISO8601 sunrise/sunset (UTC offset) and
day_length in seconds (formatted=0). day < now caps it so the cursor never runs ahead.
"""
import datetime
import urllib.parse
from collections.abc import Iterator
from typing import Any

from ducktail import Batch, Source, Table, initial
from sources import _http
from sources.cities import CITIES

URL = "https://api.sunrise-sunset.org/json"
DAY = 86400
OVERLAP = DAY          # re-fetch the last day
LOOKBACK_DAYS = 7

DAYLIGHT = Table("daylight", "merge", ("city", "date"), "date", OVERLAP, initial(max_days=LOOKBACK_DAYS))


def _day_epoch(ts: int) -> int:
    """UTC-midnight epoch for the day containing ts."""
    return ts - ts % DAY


def _to_epoch(iso: str) -> int:
    """Parse an ISO8601 timestamp (UTC offset) to epoch seconds."""
    return int(datetime.datetime.fromisoformat(iso).timestamp())


def _row(city: str, day: int, results: dict[str, Any]) -> dict[str, object] | None:
    sunrise = results.get("sunrise")
    sunset = results.get("sunset")
    day_length = results.get("day_length")
    if not isinstance(sunrise, str) or not isinstance(sunset, str):
        return None
    return {"date": day, "city": city,
            "sunrise": _to_epoch(sunrise), "sunset": _to_epoch(sunset),
            "day_length_s": int(day_length) if day_length is not None else 0}


def produce(starts: dict[str, int]) -> Iterator[tuple[Table, Batch]]:
    since = starts["daylight"]
    now = _http._now()
    rows: list[dict[str, object]] = []
    for c in CITIES:
        day = _day_epoch(since)
        while day < now:                                  # realized days only; never run ahead
            d = datetime.datetime.fromtimestamp(day, datetime.timezone.utc).strftime("%Y-%m-%d")
            q = urllib.parse.urlencode({"lat": c["lat"], "lng": c["lon"], "date": d, "formatted": 0})
            data = _http.fetch_json(f"{URL}?{q}")
            results = data.get("results")
            if isinstance(results, dict):
                row = _row(c["city"], day, results)
                if row is not None:
                    rows.append(row)
            day += DAY
    yield DAYLIGHT, rows


DAYLIGHT_SOURCE = Source("daylight", [DAYLIGHT], produce)
