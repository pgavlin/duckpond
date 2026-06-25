# AGENTS.md

duckpond is a monorepo of two independent local-DuckDB tools. They share no code; work within the
one you're changing.

- `duckbill/` -- live, query-backed dashboards declared as Python data. A pip-installable package.
- `ducktail/` -- ingest: a skill + project template that scaffolds a local DuckDB warehouse with a
  small incremental harness. Not packaged -- it's copied into an investigation and run with `uv`.
- `examples/weather/` -- an end-to-end example exercising both tools (ducktail ingest ->
  duckbill dashboard) over keyless Open-Meteo data. Has its own pytest + strict mypy
  (`examples/weather/mypy.ini`); CI runs them as a separate job.

ducktail builds a warehouse; duckbill serves it. duckbill is general -- it runs against any
DuckDB/SQLite store, ducktail-built or not.

## Build, test, type-check

Every change must keep both `pytest` and strict `mypy` green for the subproject you touched. Run
both before you consider the change done. CI enforces this across Python 3.10-3.13 on every push and PR.

duckbill (from `duckbill/`):

    pip install -e . pytest uv mypy
    pytest        # `uv` is needed for the standalone-bundle serve-integration test
    mypy          # strict; config in pyproject.toml

ducktail (from `ducktail/`):

    pip install -r requirements-dev.txt mypy
    pytest
    mypy          # strict; config in mypy.ini (checks the skill's template + reference, not tests/)

## Conventions

- Match the surrounding code. Prose -- comments, docs, commit messages -- uses ASCII punctuation:
  `--` for an em-dash, `->` for an arrow, `...` for an ellipsis. No Unicode dashes/arrows/ellipses.
- Type annotations are required and checked strict. Don't reach for `Any`, `# type: ignore`, or
  `cast()` to silence mypy when a real type exists -- both packages currently have zero casts and
  only a few justified, error-code-specific ignores. Keep it that way.
- Commit subjects are imperative ("Add X", "Fix Y"); the body explains why before what.

## Gotchas

- **duckbill `page.py`** is almost entirely the dashboard's HTML/JS as one `PAGE` string.
  **`server_bundle.py`'s `TEMPLATE`** is the generated standalone `uv run` server, lifted
  near-verbatim from `server.py`/`base.py`/`core.py`. If you change the live server's request
  handling or `$param` bind logic, mirror it in `TEMPLATE`.
- **ducktail's harness** (`skills/ducktail/assets/template/ducktail.py`) is copied verbatim into
  investigations, and the reference `skills/ducktail/references/refresh.py` runs via `uv run`. Keep
  them runnable and dependency-light: deps live in a PEP 723 `# /// script` header, not a
  requirements file. Don't add heavy dependencies to the harness.
- DuckDB allows one writer xor readers. A running `duckbill serve` holds a read lock that blocks a
  `refresh` write -- stop the server before refreshing.

## Releasing

duckbill publishes to PyPI on a `duckbill-v*` tag via Trusted Publishing. See CONTRIBUTING.md.
