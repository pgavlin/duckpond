"""Example duckbill dashboard over the weather warehouse (built by refresh.py).

    duckbill serve dash.py --db weather.duckdb

Reads warehouse.city_hourly (weather + air quality joined on city/hour) and the cities
dim. The timespan control windows ts; the city select narrows to one city.
"""
from typing import Any

title = "weather + air quality"

readme = """\
A duckpond end-to-end example: ducktail ingests hourly weather and air quality from
Open-Meteo (keyless) for a handful of cities, joined on (city, hour); duckbill serves it.

- **city_hourly** -- one row per city per hour: temperature, precipitation, wind, PM2.5, ozone.
- **cities** -- the static city dimension (country, coordinates).

Times are epoch seconds (`ts`); charts convert with `to_timestamp`.
"""

params: list[dict[str, Any]] = [
    {"name": "window", "control": "timespan", "default": "7d",
     "presets": ["24h", "72h", "7d"]},
    {"name": "city", "label": "city", "control": "select", "default": "all",
     "choices_sql": "SELECT 'all' UNION ALL SELECT DISTINCT city FROM warehouse.cities ORDER BY 1"},
]

markers: list[dict[str, Any]] = []

W = ("to_timestamp(ts) >= $start::TIMESTAMPTZ AND to_timestamp(ts) < $end::TIMESTAMPTZ "
     "AND ($city = 'all' OR city = $city)")
X_HOUR = {"field": "hour", "type": "temporal", "title": "hour (UTC)"}

charts: list[dict[str, Any]] = [
    {"id": "summary", "section": "Overview", "title": "Summary (current window)",
     "type": "metric", "span": "full",
     "good": {"avg temp (C)": "neutral", "peak PM2.5 (ug/m3)": "down",
              "cities": "neutral", "hours": "neutral"},
     "sql": f"""SELECT round(avg(temp_c), 1) AS "avg temp (C)",
                       round(max(pm2_5), 1) AS "peak PM2.5 (ug/m3)",
                       count(DISTINCT city) AS "cities",
                       count(DISTINCT ts) AS "hours"
                FROM warehouse.city_hourly WHERE {W}"""},

    {"id": "temp", "section": "Overview", "title": "Temperature by city", "type": "line",
     "brush": "timespan", "span": "full",
     "sql": f"""SELECT to_timestamp(ts) AS hour, city, temp_c
                FROM warehouse.city_hourly WHERE {W} ORDER BY ts""",
     "encoding": {"x": X_HOUR,
                  "y": {"field": "temp_c", "type": "quantitative", "title": "deg C"},
                  "color": {"field": "city", "type": "nominal"}}},

    {"id": "pm25", "section": "Overview", "title": "PM2.5 by city", "type": "line",
     "brush": "timespan", "span": "full",
     "sql": f"""SELECT to_timestamp(ts) AS hour, city, pm2_5
                FROM warehouse.city_hourly WHERE {W} ORDER BY ts""",
     "encoding": {"x": X_HOUR,
                  "y": {"field": "pm2_5", "type": "quantitative", "title": "ug/m3"},
                  "color": {"field": "city", "type": "nominal"}}},

    # A snapshot, not a time series: over the warehouse's ~7-day window each city's day
    # length barely moves, but the latitude spread (northern summer vs southern winter) is
    # the story. Ranked by the latest realized day; ignores the timespan, follows $city.
    {"id": "daylight", "section": "Overview", "title": "Daylight hours by city (latest day)",
     "type": "leaderboard",
     "sql": """SELECT city, round(day_length_s / 3600.0, 1) AS hours
               FROM warehouse.daylight
               WHERE ($city = 'all' OR city = $city)
               QUALIFY row_number() OVER (PARTITION BY city ORDER BY date DESC) = 1
               ORDER BY hours DESC"""},

    {"id": "precip", "section": "Overview", "title": "Precipitation / hour (by city)",
     "type": "stacked-bar", "brush": "timespan",
     "sql": f"""SELECT to_timestamp(ts) AS hour, city, sum(precip_mm) AS precip_mm
                FROM warehouse.city_hourly WHERE {W} GROUP BY 1, 2 ORDER BY 1""",
     "encoding": {"x": X_HOUR,
                  "y": {"field": "precip_mm", "type": "quantitative", "title": "mm", "stack": "zero"},
                  "color": {"field": "city", "type": "nominal"}}},

    {"id": "latest", "section": "Overview", "title": "Latest reading per city", "type": "table",
     "sql": f"""SELECT city AS "City", country AS "Country",
                       round(temp_c, 1) AS "Temp (C)",
                       round(pm2_5, 1) AS "PM2.5 (ug/m3)"
                FROM warehouse.city_hourly
                QUALIFY row_number() OVER (PARTITION BY city ORDER BY ts DESC) = 1
                ORDER BY "City\""""},

    {"id": "pageviews", "section": "City pulse", "title": "Wikipedia pageviews by city",
     "type": "line", "brush": "timespan", "span": "full",
     "sql": """SELECT to_timestamp(date) AS day, city, views
               FROM warehouse.pageviews
               WHERE to_timestamp(date) >= $start::TIMESTAMPTZ
                 AND to_timestamp(date) <  $end::TIMESTAMPTZ
                 AND ($city = 'all' OR city = $city)
               ORDER BY date""",
     "encoding": {"x": {"field": "day", "type": "temporal", "title": "day (UTC)"},
                  "y": {"field": "views", "type": "quantitative", "title": "views"},
                  "color": {"field": "city", "type": "nominal"}}},

    {"id": "quake_counts", "section": "City pulse", "title": "Quakes near each city (7d)",
     "type": "leaderboard",
     "sql": """SELECT city, count(*) AS quakes
               FROM warehouse.earthquakes
               WHERE to_timestamp(time) >= $start::TIMESTAMPTZ
                 AND to_timestamp(time) <  $end::TIMESTAMPTZ
                 AND ($city = 'all' OR city = $city)
               GROUP BY city ORDER BY quakes DESC"""},

    {"id": "quake_recent", "section": "City pulse", "title": "Recent quakes near each city",
     "type": "table",
     "sql": """SELECT city AS "City", round(mag, 1) AS "Mag",
                      round(depth_km, 1) AS "Depth (km)",
                      round(distance_km) AS "Distance (km)",
                      place AS "Place", to_timestamp(time) AS "When"
               FROM warehouse.earthquakes
               WHERE to_timestamp(time) >= $start::TIMESTAMPTZ
                 AND to_timestamp(time) <  $end::TIMESTAMPTZ
                 AND ($city = 'all' OR city = $city)
               ORDER BY time DESC LIMIT 50"""},
]
