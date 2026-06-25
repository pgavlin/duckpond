"""ducktail investigation dashboard (duckbill).

A live, query-backed view over ducktail.duckdb. Each chart is data -- a dict with an id,
title, type, and sql that duckbill runs on every request, so the page reflects the
current warehouse. No build step. Serve it (duckbill is a separate tool; install it
once with `pip install -e <duckbill checkout>`):

    duckbill serve dash.py --db ducktail.duckdb

Replace the example charts as you wire real sources. The timespan control binds $start
and $end (ISO timestamps); window an epoch-seconds column with
`to_timestamp(ts) >= $start::TIMESTAMPTZ AND to_timestamp(ts) < $end::TIMESTAMPTZ`.
Write SQL against the `warehouse` schema; `$name` placeholders bind params.
"""

title = "<investigation name>"

# The timespan control windows the whole page; it binds $start / $end.
params: list[dict[str, object]] = [
    {"name": "window", "control": "timespan", "default": "7d",
     "presets": ["24h", "7d", "31d"]},
]

# Each chart is data: id/title/type/sql are required; non-table charts add an `encoding`
# mapping result columns to x/y/color (Vega-Lite). type is one of line/bar/area/point/
# table/metric/leaderboard/spec.
charts: list[dict[str, object]] = [
    {"id": "requests_hourly", "section": "Overview", "title": "Requests / hour", "type": "bar",
     "sql": """SELECT to_timestamp(ts) AS hour, partition, requests
               FROM warehouse.example_hourly
               WHERE to_timestamp(ts) >= $start::TIMESTAMPTZ
                 AND to_timestamp(ts) <  $end::TIMESTAMPTZ
               ORDER BY ts""",
     "encoding": {"x": {"field": "hour", "type": "temporal", "title": "hour (UTC)"},
                  "y": {"field": "requests", "type": "quantitative", "title": "requests/hr"},
                  "color": {"field": "partition", "type": "nominal"}}},
    {"id": "by_partition", "section": "Overview", "title": "Total requests by partition",
     "type": "table",
     "sql": """SELECT partition, count(*) AS hours, sum(requests) AS total_requests
               FROM warehouse.example_hourly
               WHERE to_timestamp(ts) >= $start::TIMESTAMPTZ
                 AND to_timestamp(ts) <  $end::TIMESTAMPTZ
               GROUP BY partition ORDER BY total_requests DESC"""},
]
