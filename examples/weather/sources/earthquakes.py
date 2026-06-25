"""Earthquakes source: recent quakes near each city from the USGS FDSN event API (keyless).

mode="merge" by (city, event_id), cursor the event time (epoch seconds), 1-day overlap. One radius
query per city: maxradiuskm=500, minmagnitude=2.5, [since, now]. USGS returns GeoJSON whose features
carry a stable id, magnitude, place, an epoch-millisecond time, and [lon, lat, depth] coordinates;
distance_km is the haversine from the city. Keyed by (city, event_id) so a quake in range of two
cities is recorded once per city. Sparse by nature -- many cities yield zero rows.
"""
import datetime
import math
import urllib.parse
from collections.abc import Iterator
from typing import Any

from ducktail import Batch, Source, Table, initial
from sources import _http
from sources.cities import CITIES

URL = "https://earthquake.usgs.gov/fdsnws/event/1/query"
RADIUS_KM = 500
MIN_MAG = 2.5
DAY = 86400
OVERLAP = DAY
LOOKBACK_DAYS = 7

EARTHQUAKES = Table("earthquakes", "merge", ("city", "event_id"), "time", OVERLAP,
                    initial(max_days=LOOKBACK_DAYS))


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km between two lat/lon points (rounded to 0.1 km)."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return round(2 * r * math.asin(math.sqrt(a)), 1)


def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) else None


def _rows(city: str, lat: float, lon: float, data: dict[str, Any]) -> Iterator[dict[str, object]]:
    """Parse USGS GeoJSON into {city, event_id, time, mag, depth_km, place, distance_km}."""
    features = data.get("features")
    if not isinstance(features, list):
        return
    for f in features:
        if not isinstance(f, dict):
            continue
        eid, props, geom = f.get("id"), f.get("properties"), f.get("geometry")
        if not isinstance(eid, str) or not isinstance(props, dict) or not isinstance(geom, dict):
            continue
        coords = geom.get("coordinates")
        t = props.get("time")
        if not isinstance(coords, list) or len(coords) < 3 or not isinstance(t, (int, float)):
            continue
        place = props.get("place")
        yield {"city": city, "event_id": eid, "time": int(t) // 1000,
               "mag": _num(props.get("mag")), "depth_km": _num(coords[2]),
               "place": place if isinstance(place, str) else None,
               "distance_km": _haversine_km(lat, lon, float(coords[1]), float(coords[0]))}


def produce(starts: dict[str, int]) -> Iterator[tuple[Table, Batch]]:
    since = starts["earthquakes"]
    now = _http._now()

    def iso(ts: int) -> str:
        return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

    rows: list[dict[str, object]] = []
    for c in CITIES:
        q = urllib.parse.urlencode({
            "format": "geojson", "latitude": c["lat"], "longitude": c["lon"],
            "maxradiuskm": RADIUS_KM, "minmagnitude": MIN_MAG,
            "starttime": iso(since), "endtime": iso(now), "orderby": "time",
        })
        rows.extend(_rows(c["city"], c["lat"], c["lon"], _http.fetch_json(f"{URL}?{q}")))
    yield EARTHQUAKES, rows


QUAKE_SOURCE = Source("earthquakes", [EARTHQUAKES], produce)
