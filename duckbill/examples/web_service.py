"""Example duckbill dashboard: a synthetic web-service access log.

    python examples/gen_sample_db.py            # build the sample warehouse first
    duckbill serve examples/web_service.py --db examples/sample.duckdb

An overview of request rate, errors, and latency over a fabricated HTTP service,
plus a per-route detail section you reach by clicking a bar or a leaderboard row.
Deploy markers (gray dashed rules) overlay every time series, and a timespan
control (presets + custom + brush-to-zoom) windows the whole page.

The module is plain Python, so the repetitive parts -- the per-route detail grid
and the latency band -- are built by helpers rather than copied. Only the
resulting `charts`/`params`/`markers` data matters to duckbill. The warehouse is
two tables, `warehouse.events` and `warehouse.deploys`; see gen_sample_db.py.
"""

from typing import Any

title = "web service traffic"

readme = """\
A synthetic HTTP access log, for demonstrating duckbill against data anyone can
generate (`python examples/gen_sample_db.py`).

- **events** -- one row per request: route, region, status, latency, bytes.
- **deploys** -- one row per release, overlaid on every time series as a marker.

The overview answers "how much traffic, how many errors, how slow"; click a route
to drill into its detail section. Times are stored as epoch seconds (`ts`); the
charts convert with `to_timestamp`.
"""

# --------------------------------------------------------------- params --
params: list[dict[str, Any]] = [
    {"name": "window", "control": "timespan", "default": "7d",
     "presets": ["24h", "72h", "7d", "14d"]},                 # binds $start / $end
    {"name": "region", "label": "region", "control": "select", "default": "all",
     "choices_sql": "SELECT 'all' UNION ALL "
                    "SELECT DISTINCT region FROM warehouse.events ORDER BY 1"},
    {"name": "route", "default": "", "control": "none"},      # set by drill
]

# Deploy markers: gray dashed rules at each release, windowed to the selected range.
markers: list[dict[str, Any]] = [
    {"id": "deploys", "field": "t", "label": "label", "color": "#b9c2cc",
     "sql": "SELECT to_timestamp(build_time) AS t, version AS label "
            "FROM warehouse.deploys "
            "WHERE to_timestamp(build_time) >= $start::TIMESTAMPTZ "
            "  AND to_timestamp(build_time) <  $end::TIMESTAMPTZ"},
]

# ----------------------------------------------------------- SQL fragments --
# Absolute window the timespan control sets; the region select narrows it further.
W = ("to_timestamp(ts) >= $start::TIMESTAMPTZ AND to_timestamp(ts) < $end::TIMESTAMPTZ "
     "AND ($region = 'all' OR region = $region)")
HOUR = "to_timestamp(ts - ts % 3600) AS hour"
X_HOUR = {"field": "hour", "type": "temporal", "title": "hour (UTC)"}

# The route detail section defaults to the busiest route, so it is populated before
# anything is clicked.
ROUTE_VALUE = ("COALESCE(NULLIF($route, ''), "
               "(SELECT route FROM warehouse.events GROUP BY route "
               "ORDER BY count(*) DESC LIMIT 1))")


def latency_band(chart_id: str, section: str, where: str) -> dict[str, Any]:
    """Layered latency band: shaded p50->p99 area plus a p95 line."""
    return {
        "id": chart_id, "section": section,
        "title": "Latency (p50-p99 shaded, p95 line)",
        "type": "spec", "brush": "timespan", "markers": True,
        "sql": f"""SELECT {HOUR},
                          quantile_cont(latency_ms, 0.50) AS p50_ms,
                          quantile_cont(latency_ms, 0.95) AS p95_ms,
                          quantile_cont(latency_ms, 0.99) AS p99_ms
                   FROM warehouse.events WHERE {where} AND {W}
                   GROUP BY 1 ORDER BY 1""",
        "spec": {"layer": [
            {"mark": {"type": "area", "opacity": 0.25, "color": "#ffc000"},
             "encoding": {"x": X_HOUR,
                          "y": {"field": "p50_ms", "type": "quantitative", "title": "latency (ms)"},
                          "y2": {"field": "p99_ms"}}},
            {"mark": {"type": "line", "color": "#c00", "strokeWidth": 1.5},
             "encoding": {"x": {"field": "hour", "type": "temporal"},
                          "y": {"field": "p95_ms", "type": "quantitative"}}},
        ]},
    }


def route_detail() -> list[dict[str, Any]]:
    """The requests / errors / latency grid for the drilled-into route."""
    where = f"route = {ROUTE_VALUE}"
    sec = "Route detail"
    return [
        {"id": "rd_requests", "section": sec, "title": "Requests / hour", "type": "bar",
         "brush": "timespan", "markers": True,
         "sql": f"SELECT {HOUR}, count(*) AS requests FROM warehouse.events "
                f"WHERE {where} AND {W} GROUP BY 1 ORDER BY 1",
         "encoding": {"x": X_HOUR, "y": {"field": "requests", "type": "quantitative",
                                         "title": "requests/hr"}}},
        {"id": "rd_5xx", "section": sec, "title": "5xx / hour", "type": "bar",
         "brush": "timespan", "markers": True,
         "sql": f"SELECT {HOUR}, count(*) FILTER (WHERE status >= 500) AS errors_5xx "
                f"FROM warehouse.events WHERE {where} AND {W} GROUP BY 1 ORDER BY 1",
         "encoding": {"x": X_HOUR, "y": {"field": "errors_5xx", "type": "quantitative",
                                         "title": "5xx"}}},
        latency_band("rd_latency", sec, where),
    ]


# ----------------------------------------------------------------- overview --
charts: list[dict[str, Any]] = [
    {"id": "summary", "section": "Overview", "title": "Traffic summary (current window)",
     "type": "metric", "span": "full",
     "good": {"requests": "neutral", "error rate (%)": "down",
              "p95 latency (ms)": "down", "routes": "neutral"},
     "sql": f"""SELECT count(*) AS "requests",
                       round(100.0 * count(*) FILTER (WHERE status >= 500) / count(*), 2)
                         AS "error rate (%)",
                       round(quantile_cont(latency_ms, 0.95)) AS "p95 latency (ms)",
                       count(DISTINCT route) AS "routes"
                FROM warehouse.events WHERE {W}""",
     # per-hour trend behind each figure; columns match the metric aliases by name
     "spark": f"""SELECT {HOUR}, count(*) AS "requests",
                         round(100.0 * count(*) FILTER (WHERE status >= 500) / count(*), 2)
                           AS "error rate (%)",
                         round(quantile_cont(latency_ms, 0.95)) AS "p95 latency (ms)",
                         count(DISTINCT route) AS "routes"
                  FROM warehouse.events WHERE {W} GROUP BY 1 ORDER BY 1"""},

    {"id": "requests_by_status", "section": "Overview",
     "title": "Requests / hour (by status class)", "type": "stacked-bar",
     "brush": "timespan", "markers": True, "span": "full",
     "sql": f"""SELECT {HOUR}, (status // 100) || 'xx' AS class, count(*) AS n
                FROM warehouse.events WHERE {W} GROUP BY 1, 2 ORDER BY 1""",
     "encoding": {"x": X_HOUR,
                  "y": {"field": "n", "type": "quantitative", "title": "requests", "stack": "zero"},
                  "color": {"field": "class", "type": "nominal", "title": None,
                            "scale": {"domain": ["2xx", "3xx", "4xx", "5xx"],
                                      "range": ["#4472c4", "#70ad47", "#ffc000", "#c00"]}}}},

    {"id": "top_routes", "section": "Overview",
     "title": "Top routes by requests  (click to drill)", "type": "leaderboard",
     "drill": {"param": "route", "field": "_route"},
     "sql": f"""SELECT route AS _route, route, count(*) AS requests
                FROM warehouse.events WHERE {W}
                GROUP BY route ORDER BY requests DESC LIMIT 12"""},

    latency_band("ov_latency", "Overview", "TRUE"),

    {"id": "slowest_routes", "section": "Overview", "title": "Slowest routes (p95)",
     "type": "table",
     "sql": f"""SELECT route, count(*) AS requests,
                       round(quantile_cont(latency_ms, 0.95)) AS p95_ms,
                       round(quantile_cont(latency_ms, 0.99)) AS p99_ms
                FROM warehouse.events WHERE {W}
                GROUP BY route ORDER BY p95_ms DESC LIMIT 12"""},

    *route_detail(),
]
