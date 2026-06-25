# <investigation name>

<one line: what question this warehouse answers>

## Grain

Join key / grain shared across sources: `<e.g. user_id>`. Pick this first -- it is the
design decision that makes the sources joinable.

## Sources

One module per source under `sources/`, each a plain `fetch(since)` plus a `Source`/`Table`
declaration (see `ducktail.py`). `ducktail.py` is the harness; you edit `sources/` and `refresh.py`, not it.

- `sources/example_hourly.py` -- synthetic placeholder; replace with real sources.

## Credentials

Live in the environment. Populate them however each source needs (`.env`, shell, AWS role); the
Python connectors read the environment directly. `.env` is gitignored.

## Run

Install [uv](https://docs.astral.sh/uv/) once (`brew install uv`). Dependencies are declared in
`refresh.py`'s inline `# /// script` header -- there's no venv to create or activate:

    uv run refresh.py            # build / incrementally refresh ducktail.duckdb
    uv run refresh.py --full     # drop tables and reload from each source's initial window

Tables land in the `warehouse` schema (e.g. `warehouse.example_hourly`). Verify and explore with
ad-hoc SQL: point the DuckDB MCP server at `ducktail.duckdb`, or run `duckdb -ui ducktail.duckdb`.

## Dashboard

The dashboard is `dash.py` -- charts declared as data, served live by duckbill (a separate tool;
install it once with `pip install -e <duckbill checkout>`). Stop it before a refresh: DuckDB allows
one writer xor readers, so the server's read lock blocks the write.

    duckbill serve dash.py --db ducktail.duckdb              # live, re-queries on every request
    duckbill bundle dash.py --db ducktail.duckdb -o share.py # one self-contained uv-run script to share
