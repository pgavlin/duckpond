import json as _json
import os

import duckdb
import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(os.path.dirname(HERE))


def _load_fixture(name):
    path = os.path.join(HERE, "tests", "fixtures", name)
    with open(path) as f:
        return _json.load(f)


def test_harness_copy_matches_template():
    """examples/weather/ducktail.py must be a verbatim copy of the template's."""
    example = os.path.join(HERE, "ducktail.py")
    template = os.path.join(
        REPO, "ducktail", "skills", "ducktail", "assets", "template", "ducktail.py")
    with open(example, "rb") as f:
        a = f.read()
    with open(template, "rb") as f:
        b = f.read()
    assert a == b, "examples/weather/ducktail.py has drifted from the template harness"


def test_cities_source_yields_dim_rows():
    from sources.cities import CITIES, CITIES_SOURCE, CITIES_TABLE
    batches = list(CITIES_SOURCE.produce({}))
    assert len(batches) == 1
    table, rows = batches[0]
    assert table is CITIES_TABLE and table.mode == "replace"
    assert isinstance(rows, list) and len(rows) == len(CITIES)
    assert set(rows[0]) == {"city", "country", "lat", "lon"}


def test_hourly_rows_parses_and_caps():
    from sources._openmeteo import hourly_rows
    from sources.weather import FIELDS
    data = _load_fixture("openmeteo_weather.json")
    rows = list(hourly_rows("Testville", data, FIELDS, since=0, now=10**12))
    assert len(rows) == 5
    assert rows[0] == {"ts": 1699999200, "city": "Testville",
                       "temp_c": 12.1, "precip_mm": 0.0, "wind_kph": 9.0}
    # now caps the upper bound (drop ts >= now); since caps the lower bound.
    capped = list(hourly_rows("T", data, FIELDS, since=1700002800, now=1700010000))
    assert [r["ts"] for r in capped] == [1700002800, 1700006400]


def test_hourly_rows_handles_ragged_and_missing_fields():
    from sources._openmeteo import hourly_rows
    fields = (("temp_c", "temperature_2m"), ("precip_mm", "precipitation"))
    # Ragged: precipitation shorter than time -> truncate to the shortest, no IndexError.
    ragged = {"hourly": {"time": [0, 3600, 7200], "temperature_2m": [1.0, 2.0, 3.0],
                         "precipitation": [0.1, 0.2]}}
    rows = list(hourly_rows("T", ragged, fields, since=0, now=10**12))
    assert [r["ts"] for r in rows] == [0, 3600]
    # Missing declared field -> no rows (schema stays consistent).
    missing = {"hourly": {"time": [0, 3600], "temperature_2m": [1.0, 2.0]}}
    assert list(hourly_rows("T", missing, fields, since=0, now=10**12)) == []


def test_air_quality_fields_parse():
    from sources._openmeteo import hourly_rows
    from sources.air_quality import FIELDS
    data = _load_fixture("openmeteo_air_quality.json")
    rows = list(hourly_rows("Testville", data, FIELDS, since=0, now=10**12))
    assert len(rows) == 5
    assert rows[0] == {"ts": 1699999200, "city": "Testville", "pm2_5": 8.4, "ozone": 40.0}


def _payload(times, value):
    """An Open-Meteo-shaped weather response: every hourly value set to `value`."""
    n = len(times)
    return {"hourly": {"time": list(times), "temperature_2m": [value] * n,
                       "precipitation": [0.0] * n, "wind_speed_10m": [value] * n}}


def _air_payload(times, value):
    n = len(times)
    return {"hourly": {"time": list(times), "pm2_5": [value] * n, "ozone": [value] * n}}


BASE = 1699999200  # an exact hour


def _patch(monkeypatch, weather_data, air_data, now, pageviews_data=None, quake_data=None):
    import sources._http as http
    import sources.air_quality as aq
    import sources.daylight as dl
    import sources.earthquakes as eq
    import sources.pageviews as pv
    import sources.weather as w
    pageviews_data = {"items": []} if pageviews_data is None else pageviews_data
    quake_data = {"features": []} if quake_data is None else quake_data

    def fetch(url):
        if "wikimedia.org" in url:
            return pageviews_data
        if "earthquake.usgs.gov" in url:
            return quake_data
        return air_data if "air-quality" in url else weather_data

    monkeypatch.setattr(http, "fetch_json", fetch)
    monkeypatch.setattr(http, "_now", lambda: now)
    # First-run window starts at BASE (override the import-time wall-clock value).
    monkeypatch.setattr(w.WEATHER, "initial", BASE)
    monkeypatch.setattr(aq.AIR, "initial", BASE)
    monkeypatch.setattr(dl.DAYLIGHT, "initial", BASE)
    monkeypatch.setattr(pv.PAGEVIEWS, "initial", BASE)
    monkeypatch.setattr(eq.EARTHQUAKES, "initial", BASE)


def test_dashboard_sql_runs_against_warehouse(monkeypatch, tmp_path):
    pytest.importorskip("duckbill")
    import datetime
    import ducktail
    import refresh
    from duckbill.backends import open_backend
    from duckbill.loader import load_dashboard

    hrs = lambda a, b: [BASE + k * 3600 for k in range(a, b)]
    db = str(tmp_path / "weather.duckdb")
    pv = {"items": [{"timestamp": "2023111500", "views": 4200}]}
    eq = {"features": [{"id": "evt1",
                        "properties": {"mag": 4.5, "place": "near T", "time": 1700006400000},
                        "geometry": {"coordinates": [-122.0, 37.5, 7.0]}}]}
    _patch(monkeypatch, _payload(hrs(0, 6), 15.0), _air_payload(hrs(0, 6), 12.0),
           now=BASE + 6 * 3600, pageviews_data=pv, quake_data=eq)
    con = duckdb.connect(db)
    ducktail.run(con, refresh.SOURCES)
    refresh.transforms(con)
    con.close()

    dash = load_dashboard(os.path.join(HERE, "dash.py"))
    wh = open_backend(db)
    try:
        iso = lambda ts: datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).isoformat()
        qs = {"start": [iso(BASE)], "end": [iso(BASE + 6 * 3600)], "city": ["all"]}
        binds = dash.coerce(qs)
        for c in dash.charts:
            for key in ("sql", "spark"):
                sql = c.get(key)
                if sql:
                    cols, rows = wh.run(sql, binds)  # raises if the SQL is invalid
                    assert isinstance(cols, list) and len(rows) > 0, \
                        f"chart {c['id']} {key} returned no rows"
        # the city choices_sql resolves
        params = dash.param_meta(wh)
        city = next(p for p in params if p["name"] == "city")
        assert "all" in city["choices"]
    finally:
        wh.close()


def test_pipeline_incremental_and_transform(monkeypatch, tmp_path):
    import ducktail
    import refresh

    hrs = lambda a, b: [BASE + k * 3600 for k in range(a, b)]
    db = str(tmp_path / "weather.duckdb")

    # Run 1: hours 0..4 at value 20, clock just past hour 4.
    _patch(monkeypatch, _payload(hrs(0, 5), 20.0), _air_payload(hrs(0, 5), 20.0), now=BASE + 5 * 3600)
    con = duckdb.connect(db)
    ducktail.run(con, refresh.SOURCES)
    refresh.transforms(con)
    con.close()

    # Run 2: hours 2..7 at value 99 (overlap 2,3,4 changed), clock just past hour 7.
    _patch(monkeypatch, _payload(hrs(2, 8), 99.0), _air_payload(hrs(2, 8), 99.0), now=BASE + 8 * 3600)
    con = duckdb.connect(db)
    ducktail.run(con, refresh.SOURCES)
    refresh.transforms(con)

    ncity = len(__import__("sources.cities", fromlist=["CITIES"]).CITIES)
    total, hours = con.execute(
        "SELECT count(*), count(DISTINCT ts) FROM warehouse.weather").fetchone()
    # 8 distinct hours (0..7), one row per city, no duplicates.
    assert (total, hours) == (8 * ncity, 8)
    # Overlap upserted (hour 2 -> 99), pre-overlap untouched (hour 0 still 20).
    h2 = con.execute("SELECT DISTINCT temp_c FROM warehouse.weather WHERE ts = ?",
                     [BASE + 2 * 3600]).fetchall()
    h0 = con.execute("SELECT DISTINCT temp_c FROM warehouse.weather WHERE ts = ?",
                     [BASE]).fetchall()
    assert h2 == [(99.0,)] and h0 == [(20.0,)]
    # The transform join populated city_hourly with the country dim.
    joined = con.execute(
        "SELECT count(*) FROM warehouse.city_hourly WHERE country IS NOT NULL").fetchone()[0]
    assert joined == 8 * ncity
    lit = con.execute(
        "SELECT count(*) FROM warehouse.city_hourly WHERE day_length_s IS NOT NULL").fetchone()[0]
    assert lit == 8 * ncity   # every hour joined a daylight row
    con.close()


def test_pageviews_rows_parse():
    from sources.pageviews import _rows, _stamp_to_epoch
    data = _load_fixture("wikimedia_pageviews.json")
    rows = list(_rows("Testville", 0, data))
    assert len(rows) == 3
    assert rows[0] == {"date": _stamp_to_epoch("2023111500"), "city": "Testville", "views": 5400}
    assert _stamp_to_epoch("2023111500") == 1700006400  # 2023-11-15T00:00:00Z
    # `since` floors out earlier days (kept: the 16th and 17th).
    floored = list(_rows("Testville", _stamp_to_epoch("2023111600"), data))
    assert [r["date"] for r in floored] == [_stamp_to_epoch("2023111600"), _stamp_to_epoch("2023111700")]
    # malformed / empty -> no rows
    assert list(_rows("x", 0, {})) == []
    assert list(_rows("x", 0, {"items": [{"timestamp": "2023111500"}]})) == []  # no views key


def test_solar_sun_times_matches_almanac():
    import datetime
    from sources import _solar

    def day_epoch(y: int, m: int, d: int) -> int:
        return int(datetime.datetime(y, m, d, tzinfo=datetime.timezone.utc).timestamp())

    def minutes(epoch: int) -> int:
        t = datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc)
        return t.hour * 60 + t.minute

    # London, summer solstice: almanac sunrise ~03:43 UTC, sunset ~20:21 UTC, day ~16h38m.
    london = _solar.sun_times(51.51, -0.13, day_epoch(2026, 6, 21))
    assert london is not None
    rise, sett, length = london
    assert abs(minutes(rise) - (3 * 60 + 43)) <= 2
    assert abs(minutes(sett) - (20 * 60 + 21)) <= 2
    assert abs(length - (16 * 3600 + 38 * 60)) <= 120
    assert sett - rise == length

    # New York, winter solstice: almanac sunrise ~12:16 UTC, day ~9h15m.
    nyc = _solar.sun_times(40.71, -74.01, day_epoch(2026, 12, 21))
    assert nyc is not None
    rise, sett, length = nyc
    assert abs(minutes(rise) - (12 * 60 + 16)) <= 2
    assert abs(length - (9 * 3600 + 15 * 60)) <= 120

    # Polar night: the sun never rises at the North Pole in December.
    assert _solar.sun_times(89.9, 0.0, day_epoch(2026, 12, 21)) is None


def test_daylight_produce_schema():
    from sources import _http
    from sources.daylight import produce
    batches = list(produce({"daylight": _http._now() - 2 * 86400}))
    assert len(batches) == 1
    _table, rows = batches[0]
    assert rows, "expected at least one daylight row"
    for r in rows:
        assert set(r) == {"date", "city", "sunrise", "sunset", "day_length_s"}
        sunrise, sunset = r["sunrise"], r["sunset"]
        assert isinstance(sunrise, int) and isinstance(sunset, int)
        assert sunset > sunrise


def test_quake_rows_parse():
    from sources.earthquakes import _rows
    data = _load_fixture("usgs_earthquakes.json")
    rows = list(_rows("Testville", 37.0, -122.0, data))
    assert len(rows) == 2
    r = rows[0]
    assert r["event_id"] == "us7000abcd" and r["city"] == "Testville"
    assert r["time"] == 1700006400          # epoch ms -> s
    assert r["mag"] == 4.2 and r["depth_km"] == 8.5
    assert r["place"] == "10km N of Testville"
    assert isinstance(r["distance_km"], float)
    # empty / malformed -> no rows
    assert list(_rows("x", 0.0, 0.0, {})) == []
    assert list(_rows("x", 0.0, 0.0, {"features": [{"id": "z"}]})) == []  # no geometry/properties


def test_haversine_known_distance():
    from sources.earthquakes import _haversine_km
    # San Francisco (37.77, -122.42) to New York (40.71, -74.01) is ~4130 km.
    d = _haversine_km(37.77, -122.42, 40.71, -74.01)
    assert 4100 < d < 4160


def test_pageviews_and_quakes_land(monkeypatch, tmp_path):
    import ducktail
    import refresh

    hrs = lambda a, b: [BASE + k * 3600 for k in range(a, b)]
    db = str(tmp_path / "weather.duckdb")
    ncity = len(__import__("sources.cities", fromlist=["CITIES"]).CITIES)
    pv = {"items": [{"timestamp": "2023111500", "views": 1234}]}
    eq = {"features": [{"id": "evt1",
                        "properties": {"mag": 4.5, "place": "near T", "time": 1700006400000},
                        "geometry": {"coordinates": [-122.0, 37.5, 7.0]}}]}
    _patch(monkeypatch, _payload(hrs(0, 6), 15.0), _air_payload(hrs(0, 6), 12.0),
           now=BASE + 6 * 3600, pageviews_data=pv, quake_data=eq)
    con = duckdb.connect(db)
    ducktail.run(con, refresh.SOURCES)
    refresh.transforms(con)
    # One pageviews row per city (the fetch returns the same item for each city's URL).
    assert con.execute("SELECT count(*) FROM warehouse.pageviews").fetchone()[0] == ncity
    assert con.execute("SELECT DISTINCT views FROM warehouse.pageviews").fetchall() == [(1234,)]
    # One quake row per city, keyed (city, event_id); time stored as epoch seconds.
    assert con.execute("SELECT count(*) FROM warehouse.earthquakes").fetchone()[0] == ncity
    assert con.execute("SELECT DISTINCT time FROM warehouse.earthquakes").fetchall() == [(1700006400,)]
    # city_hourly is unchanged by the new sources (still has its weather columns, no quake cols).
    cols = [r[1] for r in con.execute("PRAGMA table_info('warehouse.city_hourly')").fetchall()]
    assert "event_id" not in cols and "views" not in cols
    con.close()
