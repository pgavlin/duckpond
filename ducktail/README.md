# ducktail

Scaffolding for ad-hoc local data warehouses: pull scattered sources into a local DuckDB store
with a small DuckDB-native incremental harness, then serve it as a live dashboard with duckbill.
Driven by the `ducktail` skill.

> Part of **duckpond**, a two-part local-DuckDB toolkit. **ducktail** (this) builds the
> warehouse; its sibling **duckbill** serves and shares it as a live dashboard.

- `skills/ducktail/` -- the self-contained, installable skill that drives the workflow. It carries
  its own scaffold in `assets/template/` (the per-investigation template) and a verified
  DuckDB-native incremental-load reference in `references/refresh.py`.

## Develop

    python3 -m venv .venv && ./.venv/bin/pip install -r requirements-dev.txt
    ./.venv/bin/pytest -q

## Acceptance test: build a multi-source warehouse

The automated tests use a synthetic, credential-free source. The real end-to-end validation is to
build a warehouse from two or more heterogeneous sources (e.g. a log API, a metrics store, and a
warehouse table) joined on a common grain, using the `ducktail` skill and template:

1. Scaffold an investigation from `skills/ducktail/assets/template/`; pick and declare the join
   grain first.
2. Write one `sources/<name>.py` per source, keeping the incremental pattern; export each source's
   credentials into the environment.
3. `uv run refresh.py` twice; confirm the second run only fetches new data.
4. `duckbill serve dash.py --db ducktail.duckdb`; confirm the breakdowns render and line up across
   sources.
