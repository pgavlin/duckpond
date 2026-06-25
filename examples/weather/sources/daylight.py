"""Daylight source: per-city sunrise/sunset/day-length, computed locally (no network).

A second "provider" joined to the weather grain on (city, date) -- but unlike the Open-Meteo
sources it makes no HTTP requests: sunrise, sunset, and day length are a deterministic
function of latitude, longitude, and date (see sources/_solar.py). Computing them avoids the
rate limits that a per-(city, date) API call invites. mode="merge" by (city, date), cursor the
day's UTC-midnight epoch, 1-day overlap. day < now caps it so the cursor never runs ahead.
"""
from collections.abc import Iterator

from ducktail import Batch, Source, Table, initial
from sources import _http, _solar
from sources.cities import CITIES

DAY = 86400
OVERLAP = DAY          # re-fetch the last day
LOOKBACK_DAYS = 7

DAYLIGHT = Table("daylight", "merge", ("city", "date"), "date", OVERLAP, initial(max_days=LOOKBACK_DAYS))


def _day_epoch(ts: int) -> int:
    """UTC-midnight epoch for the day containing ts."""
    return ts - ts % DAY


def produce(starts: dict[str, int]) -> Iterator[tuple[Table, Batch]]:
    since = starts["daylight"]
    now = _http._now()
    rows: list[dict[str, object]] = []
    for c in CITIES:
        day = _day_epoch(since)
        while day < now:                                  # realized days only; never run ahead
            t = _solar.sun_times(c["lat"], c["lon"], day)
            if t is not None:
                sunrise, sunset, day_length_s = t
                rows.append({"date": day, "city": c["city"], "sunrise": sunrise,
                             "sunset": sunset, "day_length_s": day_length_s})
            day += DAY
    yield DAYLIGHT, rows


DAYLIGHT_SOURCE = Source("daylight", [DAYLIGHT], produce)
