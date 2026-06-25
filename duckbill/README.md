# duckbill

Live, query-backed dashboards declared as Python data. A dashboard is a Python
file that defines charts as dicts; the server runs each chart's SQL on every
request, so the page is live -- it re-queries on interaction and reflects the
current warehouse. No build step, two dependencies (`duckdb` + `sqlglot`); network
backends are opt-in extras. Single process.

> Part of **duckpond**, a two-part local-DuckDB toolkit. **duckbill** (this) serves
> and shares a warehouse as a live dashboard; its sibling **ducktail** pulls
> scattered sources into one. duckbill works against any DuckDB/SQLite store --
> ducktail-built or not.

![A duckbill dashboard over a local DuckDB warehouse](../assets/hero.png)

```
pip install -e .
python examples/gen_sample_db.py                 # build a synthetic warehouse
duckbill serve examples/web_service.py --db examples/sample.duckdb
```

## Backends

The `--db` flag accepts a DSN or a bare file path:

| DSN form | backend |
|---|---|
| `/path/to/file.duckdb` or `duckdb:///path/to/file.duckdb` | DuckDB (local file) |
| `sqlite:///path/to/file.db` | SQLite (local file) |
| `postgresql://user:pass@host/db` | Postgres |
| `mysql://user:pass@host/db` | MySQL |
| `snowflake://user@account/db/schema?warehouse=W&role=R` | Snowflake |

Secret values (passwords, tokens) should not be written into command lines or
dashboard files. Use `${VAR}` in the DSN; duckbill expands it from the
environment before connecting:

```
duckbill serve dash.py --db "postgresql://ro_user:${DB_PASS}@db-host:5432/warehouse"
```

Network backends (Postgres, MySQL, Snowflake) are opt-in extras; the base
install only pulls in `duckdb` and `sqlglot`:

```
pip install duckbill[postgres]    # psycopg
pip install duckbill[mysql]       # pymysql
pip install duckbill[snowflake]   # snowflake-connector-python
pip install duckbill[all]         # all three
```

All connections are read-only. For DuckDB and SQLite that is enforced at the
driver level. For network backends, use a read-only role or user -- Snowflake
has no session-level read-only toggle, so this is especially important there.

`--pool` sets the connection pool size for network backends (default 4); it has
no effect on DuckDB or SQLite.

`$name` parameter binding is uniform across all backends -- the server translates
the dashboard's `$name` placeholders to the native paramstyle before executing.
SQL dialect (functions, casts, date arithmetic) is the author's responsibility:
write SQL that matches the backend you deploy against.

Bundles (`duckbill bundle`) are DuckDB/SQLite-only. A bundle embeds the warehouse
tables as Parquet in a self-contained `uv run` server script; network backends are
serve-only (they can't export to Parquet). A bundle never contains credentials --
the DSN is used at build time to export data and is not written into the output.

## Declaring a dashboard

A dashboard module defines `charts`, and optionally `params`, `title`, and a
`readme` (see [Documentation](#documentation)):

```python
title = "my warehouse"

params = [
    {"name": "window", "control": "timespan", "default": "31d",
     "presets": ["6h", "24h", "7d", "31d"]},          # binds $start and $end
    {"name": "kind", "control": "select", "default": "all",
     "choices_sql": "SELECT DISTINCT kind FROM warehouse.t ORDER BY 1"},
    {"name": "id", "default": "", "control": "none"},  # set by a drill click
]

charts = [
    {"id": "volume", "section": "Overview", "title": "Volume", "type": "line",
     "brush": "timespan",                              # drag the x-axis to zoom
     "sql": """SELECT to_timestamp(ts) AS t, n FROM warehouse.t
               WHERE to_timestamp(ts) >= $start::TIMESTAMPTZ
                 AND to_timestamp(ts) <  $end::TIMESTAMPTZ
                 AND ($kind = 'all' OR kind = $kind) ORDER BY t""",
     "encoding": {"x": {"field": "t", "type": "temporal"},
                  "y": {"field": "n", "type": "quantitative"}}},
]
```

### Charts

| key        | meaning |
|------------|---------|
| `id`       | unique identifier (required) |
| `title`    | card heading (required) |
| `type`     | `line` / `bar` / `stacked-bar` / `area` / `point` / `table` / `metric` / `leaderboard` / `spec` (required) |
| (table col) | a `table` query column named `_*` is data-only: kept for drill values, not displayed |
| `sql`      | the query; may reference `$param` (required) |
| `section`  | groups cards under a heading (default `Overview`) |
| `encoding` | Vega-Lite encoding for the built-in types |
| `spec`     | raw Vega-Lite spec -- the escape hatch, used when `type` is `spec` |
| `drill`    | bars: `{"param": p, "field": col}` -- click a mark to open param `p`'s detail page. tables: `{column: param}` or `{column: {param, value}}` -- click a cell to drill (a `value` column, or one named `_*`, supplies the param value when it differs from the displayed text) |
| `brush`    | `"timespan"`: drag the x-axis to set the window |
| `markers`  | `true` (all marker sets) or `["id", ...]`: overlay marker rules on this chart |
| `span`     | `"full"` -- the card spans the whole row; an integer `N` -- it spans `N` columns (clamped to the columns that fit, so it degrades to full width on a narrow window) |

`sql` is bound, not interpolated -- the server passes `$param` values to DuckDB as
parameters, so control and drill input is safe. The dashboard module is your own
trusted code; its SQL runs as written.

### Metric cards

A `metric` chart is a strip of hero figures rather than a plot. Its SQL returns
**one row**; each column becomes a figure -- the value shown large and compacted
(`30.2k`, `1.2M`), the column name as the label. Use a quoted alias to control
the label, and pair it with `"span": "full"` for a hero row across the top:

```python
{"id": "summary", "section": "Overview", "title": "Traffic summary", "type": "metric", "span": "full",
 "sql": f"""SELECT count(*) AS "requests", round(quantile_cont(latency_ms, 0.95)) AS "p95 latency (ms)",
                   count(DISTINCT route) AS "routes"
            FROM warehouse.events WHERE {{window}}"""}
```

When the query references the timespan (`$start`/`$end`), each numeric figure
also shows its change versus the previous equal-length window, as a signed
percent colored by whether the move is good or bad. `good` declares the good
direction -- `"up"` (higher is better), `"down"` (lower is better), or
`"neutral"` (no judgment, gray) -- either for all figures or per figure:

```python
"good": {"requests": "neutral", "p95 latency (ms)": "down", "routes": "neutral"}
```

Figures not listed default to `"up"`. The delta is omitted when there's no prior
window or the previous value is zero/absent.

A metric may also carry a `spark` query -- SQL returning a temporal column plus
one column per figure (aliases matched by name) -- and each figure gets an inline
sparkline of that trend:

```python
"spark": f"""SELECT date_trunc('hour', t) AS hour, count(*) AS "requests", ...
             FROM warehouse.events WHERE {{window}} GROUP BY 1 ORDER BY 1"""
```

### Leaderboards

A `leaderboard` is a ranked list for top-N-by-dimension: SQL returns rows whose
first text column is the label and first numeric column the value, drawn with an
inline magnitude bar behind each value. It drills like a bar (`{"param", "field"}`,
clicking a row navigates to that param's detail page; a `_`-prefixed column can
carry a hidden drill value, e.g. show readable text but drill on a hash). Denser
and more scannable than a bar chart for a long ranking.

### Compare

The **Compare** toggle (in the timespan control) overlays the previous
equal-length window: a faded previous-period series on single-series time charts,
and a percent delta per row on windowed leaderboards. The prev/next arrows step the window
back/forward by its own length.

### Enlarge and explore

Every card has an expand icon (top-right, on hover). Clicking it opens the chart
in a large modal where you can flip between the **Chart** and the raw **Data**
table, and **Open in Ask** to drop the chart's query (with the current params
substituted) into the Ask workbench for ad-hoc exploration.

### Params

A param drives a control and binds into SQL by its `name`. Controls:

- `select` -- a dropdown; options come from `choices` (a list) or `choices_sql`.
- `timespan` -- a time-range picker (presets + custom from/to + brush-to-zoom).
  It binds `$start` and `$end` (ISO timestamps), not a param of its own name.
- `none` -- no control; the param is set only by a drill click.

`type` is `str` (default), `int`, or `float`.

### Markers

A `markers` list declares overlay queries -- the canonical case is deploy
markers, a recurring motif. Each marker is `{"id", "sql", "field"}` plus optional
`label` and `color`; the `sql` returns timestamps (referencing `$param` like any
query), and any chart with `markers: true` gets those timestamps drawn as rules.

```python
markers = [
    {"id": "deploys", "field": "t", "label": "label", "color": "#b9c2cc",
     "sql": "SELECT to_timestamp(build_time) AS t, version AS label FROM warehouse.deploys "
            "WHERE to_timestamp(build_time) >= $start::TIMESTAMPTZ "
            "  AND to_timestamp(build_time) <  $end::TIMESTAMPTZ"},
]
```

Window the marker query on `$start`/`$end` so rules stay inside the chart's time
axis. Markers re-run when the window changes.

## Documentation

A warehouse documents itself from two sources, both surfaced in the header's
**About** tab and by `duckbill docs`:

- the dashboard's `readme` -- a Markdown string for the narrative: what the
  warehouse is, how the pieces fit, how to read the dashboard.
- DuckDB `COMMENT`s -- per-table and per-column descriptions that live in the
  warehouse catalog, so the schema reference is generated, not hand-maintained.

```python
readme = """\
A synthetic HTTP access log: one row per request, with route, region, status, and
latency, plus a deploys table for markers.

Times are stored as epoch seconds (`ts`); the charts convert with
`to_timestamp`.
"""
```

Set the `COMMENT`s where the warehouse is built, so they survive a rebuild:

```sql
COMMENT ON TABLE warehouse.events IS 'One row per HTTP request to the service.';
COMMENT ON COLUMN warehouse.events.ts IS 'When the request completed, epoch seconds (UTC).';
```

The About view renders the `readme` and a schema reference (each table's comment
and its columns with types and comments); the Ask sidebar hangs the same comments
off tables and columns as tooltips. To emit a `WAREHOUSE.md` for the repo:

```
duckbill docs examples/web_service.py --db examples/sample.duckdb -o WAREHOUSE.md
```

## Ask (ad-hoc queries)

The header's **Ask** tab is a query workbench, like Metabase's native query: a
schema sidebar (click to insert), a CodeMirror SQL editor with schema-aware
autocomplete, and a Run button (Cmd/Ctrl+Enter). Results show as a table, or pick a
chart type + x/y/color to visualize through the same chart engine (tooltips,
hover crosshair, interactive legend included). The query is read-only -- the
connection is `read_only`, so it's SELECT-only -- and results are row-capped.

**Save** names a question and writes it to a file -- one JSON per question under
`questions/` next to the dashboard (override with `--questions <dir>`), so they're
git-friendly and hand-editable. The **Saved** dropdown reopens or deletes them,
and each has a stable link (`#q=<slug>`). **Copy link** is the no-save path: it
encodes the SQL and chart choice into the URL (`#ask=...`).

## Standalone bundle

Wrap a dashboard and its data into one self-contained file for sharing or
archiving:

```
duckbill bundle examples/web_service.py --db examples/sample.duckdb -o dashboard.py
# -> dashboard.py   (run it with: uv run dashboard.py)
```

`bundle` prunes the warehouse first -- only the tables and columns the charts
actually reference are included -- exports them to zstd Parquet, and embeds that
(b85) in a single `uv run`-able Python script. The recipient runs `uv run
dashboard.py`; uv resolves the deps from the PEP 723 header, the script extracts
its embedded Parquet to a content-keyed temp dir on first run (later runs reuse
it), an in-memory DuckDB exposes each table as a view `warehouse.<table>`, and a
browser renders the dashboard. No duckbill install, no static host, no sibling
files -- just one script and uv.

Queries run server-side (it's a tiny localhost http server), so it works in every
browser and the whole dashboard stays live: drill-down, the timespan brush, legend
filters, and the Ask view all work. The only degradation is that saved questions
are read-only -- the ones embedded at build time are loadable, but new ones can't
be persisted into the bundle.

Bundles are DuckDB/SQLite-only -- a bundle embeds the data as Parquet, which a
network backend can't export. A bundle never contains credentials; the DSN is used
only at build time.

### Static site (`--static`)

`duckbill bundle dash.py --db warehouse.duckdb --static -o site/` emits a multi-file static
site (`index.html` + `data/<table>.parquet`) that runs the dashboard entirely in the browser
via DuckDB-WASM -- no server. Serve it over http (not `file://`): `python -m http.server -d
site`, or host it (GitHub Pages, Netlify, ...). Read-only: the Ask view works (SQL runs
client-side); saving questions is disabled. Libraries load from CDN in this version.

## How it works

- **The dashboard module** is pure data -- charts and params, plus whatever Python
  you want for shared SQL fragments and computed defaults.
- **The server** holds one read-only DuckDB connection behind a lock and serves
  `/` (the page), `/meta` (params + chart metadata), `/q` (run one chart's SQL),
  and `/docs` (the readme + catalog comments). It binds only the params each
  query references.
- **The page** builds controls from the params, draws each chart with Vega-Lite,
  and re-queries only the charts that reference a changed param.

### Pages and drill-down

Every section is a page. Sections not driven by a drill are the home page; each
drill param has its own detail page -- the section whose charts all reference it.
Clicking a drill mark navigates to that detail page (the current page lives in
the URL hash, so the browser back button and shareable links work); the home
page's other charts stay put. A detail page populates from its drill value, or,
on a direct link with no value, from a SQL default like
`COALESCE(NULLIF($route, ''), (SELECT ... LIMIT 1))`.

A control appears only when a chart on the current page references its param, so
a home-only filter hides on a detail page that ignores it. The header is pinned.

A chart with a color series gets an interactive legend: click an entry to focus
that series (the rest dim), click again to clear, shift-click for several. This
filters within the chart and never drills.

## Not in scope

Client-side crossfilter (that's [Mosaic](https://github.com/uwdata/mosaic)),
multi-user/auth/sharing (loopback, single user), and static export (the point is
to stay live). Charts that need a layered/transformed view use the `spec` escape
hatch; reference overlays (deploys, incidents) use `markers`.
