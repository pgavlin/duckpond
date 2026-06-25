"""Wikipedia pageviews source: daily per-city article views from the Wikimedia REST API (keyless).

mode="merge" by (city, date), cursor the day's UTC-midnight epoch, 1-day overlap. One request per
city covering [since, today]; the per-article endpoint returns {"items": [...]} with a "YYYYMMDD00"
timestamp and an integer "views". Article titles differ from our ASCII city names (New York ->
New York City, Sao Paulo -> the diacritic title), so each city carries its Wikipedia title here;
the diacritics live only in the request URL -- the warehouse stores the ASCII city.
"""
import datetime
import urllib.parse
from collections.abc import Iterator
from typing import Any

from ducktail import Batch, Source, Table, initial
from sources import _http

URL = "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article"
PROJECT = "en.wikipedia.org/all-access/user"
DAY = 86400
OVERLAP = DAY
LOOKBACK_DAYS = 7

# city -> Wikipedia article title. The Sao Paulo article's canonical title carries U+00E3
# (a-tilde); build it with chr() so this file stays ASCII. The per-article endpoint does not
# follow the plain-ASCII redirect, so the canonical title is required.
ARTICLES = {
    "San Francisco": "San Francisco",
    "Seattle": "Seattle",
    "New York": "New York City",
    "London": "London",
    "Tokyo": "Tokyo",
    "Sydney": "Sydney",
    "Sao Paulo": "S" + chr(0xe3) + "o Paulo",  # a-tilde via chr() keeps the file ASCII
}

PAGEVIEWS = Table("pageviews", "merge", ("city", "date"), "date", OVERLAP,
                  initial(max_days=LOOKBACK_DAYS))


def _day_epoch(ts: int) -> int:
    """UTC-midnight epoch for the day containing ts."""
    return ts - ts % DAY


def _stamp_to_epoch(stamp: str) -> int:
    """Wikimedia 'YYYYMMDD00' -> UTC-midnight epoch seconds."""
    d = datetime.datetime.strptime(stamp[:8], "%Y%m%d").replace(tzinfo=datetime.timezone.utc)
    return int(d.timestamp())


def _rows(city: str, since: int, data: dict[str, Any]) -> Iterator[dict[str, object]]:
    """Parse the per-article response into {date, city, views}, dropping days before since's day."""
    items = data.get("items")
    if not isinstance(items, list):
        return
    floor = _day_epoch(since)
    for it in items:
        if not isinstance(it, dict):
            continue
        stamp, views = it.get("timestamp"), it.get("views")
        if not isinstance(stamp, str) or not isinstance(views, int):
            continue
        date = _stamp_to_epoch(stamp)
        if date < floor:
            continue
        yield {"date": date, "city": city, "views": views}


def produce(starts: dict[str, int]) -> Iterator[tuple[Table, Batch]]:
    since = starts["pageviews"]
    now = _http._now()

    def fmt(ts: int) -> str:
        return datetime.datetime.fromtimestamp(
            _day_epoch(ts), datetime.timezone.utc).strftime("%Y%m%d")

    start_d, end_d = fmt(since), fmt(now)
    rows: list[dict[str, object]] = []
    for city, article in ARTICLES.items():
        art = urllib.parse.quote(article.replace(" ", "_"), safe="")
        rows.extend(_rows(city, since, _http.fetch_json(f"{URL}/{PROJECT}/{art}/daily/{start_d}/{end_d}")))
    yield PAGEVIEWS, rows


PAGEVIEWS_SOURCE = Source("pageviews", [PAGEVIEWS], produce)
