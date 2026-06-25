# weather + air quality (duckpond end-to-end example)

The full duckpond pipeline over real, keyless public data from two providers: **ducktail**
ingests hourly weather and air quality from [Open-Meteo](https://open-meteo.com) and daily
sunrise/sunset from [sunrise-sunset.org](https://sunrise-sunset.org) for a handful of cities,
joins them on `(city, hour)` and `(city, date)`, and **duckbill** serves the result as a
live dashboard.

This directory is laid out as a real scaffolded ducktail investigation -- a verbatim
copy of the harness (`ducktail.py`), `refresh.py`, `sources/`, and a duckbill `dash.py`.

## Run it

Ingest (no venv -- deps live in `refresh.py`'s PEP 723 header):

```
uv run refresh.py            # build / incrementally refresh weather.duckdb
uv run refresh.py --full     # reload from each source's initial window
```

Dashboard:

```
pip install duckbill
duckbill serve dash.py --db weather.duckdb
```

A static, in-browser build of this dashboard is published to GitHub Pages by
`.github/workflows/pages.yml` (rebuilt daily). Build it yourself with
`duckbill bundle dash.py --db weather.duckdb --static -o site/`.

## Layout

- `sources/cities.py` -- a static city dimension (`mode="replace"`).
- `sources/weather.py`, `sources/air_quality.py` -- two `mode="merge"` Open-Meteo sources,
  each incremental by an hourly `ts` cursor with a 2h overlap.
- `sources/daylight.py` -- a `mode="merge"` sunrise-sunset.org source (a second provider),
  incremental by a daily `date` cursor.
- `sources/pageviews.py` -- a `mode="merge"` Wikimedia pageviews source, daily per-city views
  by a `date` cursor.
- `sources/earthquakes.py` -- a `mode="merge"` USGS source, recent quakes within 500km of each
  city, keyed `(city, event_id)` and incremental by event `time`.
- `sources/_http.py`, `sources/_openmeteo.py` -- the shared keyless-GET helper and the
  Open-Meteo response parser.
- `refresh.py` -- wires the sources and, post-load, joins them into `warehouse.city_hourly`
  (adding `day_length_s` and a derived `is_daylight` per hour).
- `dash.py` -- the duckbill dashboard.

No credentials are needed; Open-Meteo, sunrise-sunset.org, Wikimedia, and USGS are all keyless.
`ADW_LOOKBACK_DAYS` narrows the window (default 7).

## Troubleshooting

On macOS under uv's managed Python, the live fetch can fail with `CERTIFICATE_VERIFY_FAILED`
-- the interpreter ships no CA bundle. Point `SSL_CERT_FILE` at one:

    SSL_CERT_FILE="$(python3 -m certifi 2>/dev/null || echo /etc/ssl/cert.pem)" uv run refresh.py
