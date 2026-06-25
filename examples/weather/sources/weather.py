"""Weather source: hourly temperature, precipitation, wind from Open-Meteo (keyless).

mode="merge" by (city, ts), cursor ts (epoch hour), 2h overlap. The HTTP+parse path is
shared with air_quality in sources/_http.py; this module declares only the endpoint
and its field mapping.
"""
from collections.abc import Iterator

from ducktail import Batch, Source, Table, initial
from sources import _http, _openmeteo
from sources.cities import CITIES

URL = "https://api.open-meteo.com/v1/forecast"
FIELDS = (("temp_c", "temperature_2m"), ("precip_mm", "precipitation"), ("wind_kph", "wind_speed_10m"))
HOURLY = tuple(key for _, key in FIELDS)
OVERLAP = 2 * 3600
LOOKBACK_DAYS = 7  # demo window; ADW_LOOKBACK_DAYS can narrow it further

WEATHER = Table("weather", "merge", ("city", "ts"), "ts", OVERLAP, initial(max_days=LOOKBACK_DAYS))


def produce(starts: dict[str, int]) -> Iterator[tuple[Table, Batch]]:
    since = starts["weather"]
    now = _http._now()
    past_days = max(1, (now - since) // 86400 + 1)
    rows: list[dict[str, object]] = []
    # All cities batch together; a fetch failure drops this run's batch -- the next run
    # self-heals from the unchanged high-water mark.
    for c in CITIES:
        url = _openmeteo.build_url(URL, c["lat"], c["lon"], HOURLY, past_days)
        rows.extend(_openmeteo.hourly_rows(c["city"], _http.fetch_json(url), FIELDS, since, now))
    yield WEATHER, rows


WEATHER_SOURCE = Source("weather", [WEATHER], produce)
