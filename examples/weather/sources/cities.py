"""Static city dimension -- the join key shared by weather and air_quality.

mode="replace": rebuilt verbatim each run from the list below. ASCII names keep the
warehouse ASCII-clean.
"""
from collections.abc import Iterator
from typing import TypedDict

from ducktail import Batch, Source, Table


class City(TypedDict):
    city: str
    country: str
    lat: float
    lon: float


CITIES: list[City] = [
    {"city": "San Francisco", "country": "US", "lat": 37.77, "lon": -122.42},
    {"city": "Seattle", "country": "US", "lat": 47.61, "lon": -122.33},
    {"city": "New York", "country": "US", "lat": 40.71, "lon": -74.01},
    {"city": "London", "country": "GB", "lat": 51.51, "lon": -0.13},
    {"city": "Tokyo", "country": "JP", "lat": 35.68, "lon": 139.69},
    {"city": "Sydney", "country": "AU", "lat": -33.87, "lon": 151.21},
    {"city": "Sao Paulo", "country": "BR", "lat": -23.55, "lon": -46.63},
]

CITIES_TABLE = Table("cities", "replace")


def produce(starts: dict[str, int]) -> Iterator[tuple[Table, Batch]]:
    rows: list[dict[str, object]] = [
        {"city": c["city"], "country": c["country"], "lat": c["lat"], "lon": c["lon"]}
        for c in CITIES
    ]
    yield CITIES_TABLE, rows


CITIES_SOURCE = Source("cities", [CITIES_TABLE], produce, parallel=False)
