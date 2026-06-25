# Changelog

All notable changes to duckbill are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

[0.1.1]: https://github.com/pgavlin/duckpond/releases/tag/duckbill-v0.1.1
