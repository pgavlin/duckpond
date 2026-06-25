"""Shared Open-Meteo URL builder and hourly-array parser for both feeds.

Both sources hit the same upstream, so the hourly-array unzip lives here; each source
declares only its URL and field mapping. forecast_days=0 plus the `ts < now` cap keep
forecast hours out, so max(ts) -- the incremental cursor -- never runs ahead of realized
data. The GET and the now-cap live in sources._http.
"""
import urllib.parse
from collections.abc import Iterator, Sequence
from typing import Any


def build_url(base: str, lat: float, lon: float, hourly: Sequence[str], past_days: int) -> str:
    q = urllib.parse.urlencode({
        "latitude": lat, "longitude": lon, "hourly": ",".join(hourly),
        "past_days": past_days, "forecast_days": 0,
        "timezone": "UTC", "timeformat": "unixtime",
    })
    return f"{base}?{q}"


def hourly_rows(city: str, data: dict[str, Any], fields: Sequence[tuple[str, str]],
                since: int, now: int) -> Iterator[dict[str, object]]:
    """Unzip Open-Meteo's parallel hourly arrays into rows, capped to since <= ts < now.

    `fields` maps each output column to its response key, e.g.
    (("temp_c", "temperature_2m"), ("precip_mm", "precipitation")).
    """
    hourly = data.get("hourly")
    if not isinstance(hourly, dict):
        return
    times = hourly.get("time")
    if not isinstance(times, list):
        return
    cols: dict[str, list[Any]] = {}
    for out, key in fields:
        arr = hourly.get(key)
        if not isinstance(arr, list):
            return  # a declared field is missing or malformed -- skip the whole response
        cols[out] = arr
    n = min([len(times), *(len(a) for a in cols.values())])  # ragged -> stop at the shortest
    for i in range(n):
        ts = times[i]
        if ts is None or ts < since or ts >= now:
            continue
        row: dict[str, object] = {"ts": int(ts), "city": city}
        for out in cols:
            row[out] = cols[out][i]
        yield row
