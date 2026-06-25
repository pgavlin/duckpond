"""Air-quality source: hourly PM2.5 and ozone from Open-Meteo (keyless).

Same incremental shape and shared HTTP path (sources/_http.py) as the weather source;
joined to it on (city, ts).
"""
from collections.abc import Iterator

from ducktail import Batch, Source, Table, initial
from sources import _http, _openmeteo
from sources.cities import CITIES

URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
FIELDS = (("pm2_5", "pm2_5"), ("ozone", "ozone"))
HOURLY = tuple(key for _, key in FIELDS)
OVERLAP = 2 * 3600
LOOKBACK_DAYS = 7

AIR = Table("air_quality", "merge", ("city", "ts"), "ts", OVERLAP, initial(max_days=LOOKBACK_DAYS))


def produce(starts: dict[str, int]) -> Iterator[tuple[Table, Batch]]:
    since = starts["air_quality"]
    now = _http._now()
    past_days = max(1, (now - since) // 86400 + 1)
    rows: list[dict[str, object]] = []
    # All cities batch together; a fetch failure drops this run's batch -- the next run
    # self-heals from the unchanged high-water mark.
    for c in CITIES:
        url = _openmeteo.build_url(URL, c["lat"], c["lon"], HOURLY, past_days)
        rows.extend(_openmeteo.hourly_rows(c["city"], _http.fetch_json(url), FIELDS, since, now))
    yield AIR, rows


AIR_SOURCE = Source("air_quality", [AIR], produce)
