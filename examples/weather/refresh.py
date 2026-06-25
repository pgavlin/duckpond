# /// script
# requires-python = ">=3.10"
# dependencies = ["duckdb>=1.0", "pyarrow"]
# ///
"""Refresh the weather warehouse: ducktail ingests Open-Meteo weather + air quality.

    uv run refresh.py            # incremental refresh into ./weather.duckdb
    uv run refresh.py --full     # drop tables and reload from each source's initial window

Then serve it with duckbill (a separate tool):

    pip install duckbill
    duckbill serve dash.py --db weather.duckdb

Open-Meteo needs no credentials. ADW_LOOKBACK_DAYS narrows the window (default clamps to 7).
"""
import argparse
import os
import threading

import duckdb

from ducktail import Source, heartbeat, run, setup_logging
from sources.air_quality import AIR_SOURCE
from sources.cities import CITIES_SOURCE
from sources.daylight import DAYLIGHT_SOURCE
from sources.earthquakes import QUAKE_SOURCE
from sources.pageviews import PAGEVIEWS_SOURCE
from sources.weather import WEATHER_SOURCE

HERE = os.path.dirname(os.path.abspath(__file__))

SOURCES: list[Source] = [CITIES_SOURCE, WEATHER_SOURCE, AIR_SOURCE, DAYLIGHT_SOURCE,
                         PAGEVIEWS_SOURCE, QUAKE_SOURCE]


def _exists(con: duckdb.DuckDBPyConnection, name: str) -> bool:
    return con.execute(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_schema = 'warehouse' AND table_name = ?", [name]).fetchone() is not None


def transforms(con: duckdb.DuckDBPyConnection) -> None:
    """Post-load: join weather + air_quality + cities + daylight into warehouse.city_hourly."""
    if not all(_exists(con, t) for t in ("weather", "air_quality", "cities", "daylight")):
        return
    con.execute("""
        CREATE OR REPLACE TABLE warehouse.city_hourly AS
        SELECT w.ts, w.city, c.country, w.temp_c, w.precip_mm, w.wind_kph, a.pm2_5, a.ozone,
               d.day_length_s,
               (w.ts >= d.sunrise AND w.ts < d.sunset) AS is_daylight
        FROM warehouse.weather w
        LEFT JOIN warehouse.air_quality a USING (ts, city)
        LEFT JOIN warehouse.cities c USING (city)
        LEFT JOIN warehouse.daylight d ON d.city = w.city AND d.date = w.ts - w.ts % 86400
    """)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--full", action="store_true",
                    help="drop tables and reload from each source's initial window")
    args = ap.parse_args()

    log_path = setup_logging(os.path.join(HERE, "logs"))
    print(f"logging to {log_path}")
    con = duckdb.connect(os.path.join(HERE, "weather.duckdb"))
    stop = threading.Event()
    threading.Thread(target=heartbeat, args=(stop,), daemon=True).start()
    try:
        run(con, SOURCES, full=args.full)
        transforms(con)
    finally:
        stop.set()
        con.close()


if __name__ == "__main__":
    main()
