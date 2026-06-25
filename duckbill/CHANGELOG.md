# Changelog

All notable changes to duckbill are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.2] - 2026-06-25

### Fixed

- Temporal bar charts (e.g. an hourly histogram) now get the same readable
  day-boundary date axis as line charts, instead of Vega's default labels.
- Mobile: the page renders at device width, the card grid and the enlarge modal
  fit a phone, a vertical swipe scrolls the page instead of scrubbing a chart,
  and legends move below the plot and wrap into columns so the chart keeps the
  full width.
- `bundle` quotes table identifiers when exporting Parquet, so a warehouse with a
  reserved-word or otherwise special table name bundles correctly.
- Rendered error text and chart titles are HTML-escaped.
- The `uv run` bundle reports itself read-only, so the page hides the save
  control instead of showing one that errors.
- DB-API cursors are closed after use, so a pooled Postgres / MySQL / Snowflake
  connection no longer accumulates server-side cursor state.
- A malformed `Content-Length` is treated as an empty body, not a 500.
- A leaderboard over a result with no numeric column shows a message instead of
  empty bars, and `duckbill docs` closes its backend connection.

### Changed

- The Postgres and Snowflake schema and docs views hide dlt bookkeeping tables
  and columns (`_dlt_*`), matching the DuckDB backend.

## [0.1.1] - 2026-06-24

Initial release.

### Added

- Serve a dashboard declared as Python data, running each chart's SQL per
  request against a local DuckDB or SQLite store, with opt-in
  Postgres / MySQL / Snowflake backends.
- `$name` parameter binding translated to each backend's paramstyle, a timespan
  control, chart drill-down, deploy markers, and an Ask query workbench.
- `duckbill bundle` to wrap a dashboard and its pruned data into one
  self-contained `uv run` server script, `duckbill bundle --static` to emit a
  DuckDB-WASM site that runs the queries in the browser, and `duckbill docs` to
  emit a Markdown schema reference from the warehouse's own `COMMENT`s.
- `examples/web_service.py` and `examples/gen_sample_db.py` -- a self-contained
  example dashboard over a synthetic warehouse you can generate locally.
- `py.typed` marker, so downstream type checkers see duckbill's types (PEP 561).
- PyPI metadata: long description (`readme`), license, author, and classifiers.
- Minimum Python is 3.10 (only 3.10-3.13 are tested).

[0.1.2]: https://github.com/pgavlin/duckpond/releases/tag/duckbill-v0.1.2
[0.1.1]: https://github.com/pgavlin/duckpond/releases/tag/duckbill-v0.1.1
