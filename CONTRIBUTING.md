# Contributing

duckpond is a monorepo of two tools, each self-contained in its own directory:

- `duckbill/` -- the dashboard server; a pip-installable Python package.
- `ducktail/` -- the ingest skill + project template; not packaged (copied in, run with `uv`).
- `examples/weather/` -- end-to-end example; its own tests + mypy run from that directory
  (`pip install -e ../../duckbill pyarrow pytest && pytest && mypy`).

## Development

Work in the subproject's directory.

**duckbill** (`uv` is needed for the standalone-bundle serve-integration test):

    cd duckbill
    python -m venv .venv && . .venv/bin/activate
    pip install -e . pytest uv mypy
    pytest        # tests
    mypy          # strict type checking (config in pyproject.toml)

**ducktail**:

    cd ducktail
    python -m venv .venv && . .venv/bin/activate
    pip install -r requirements-dev.txt mypy
    pytest        # tests
    mypy          # strict type checking (config in mypy.ini)

## CI

`.github/workflows/ci.yml` runs both suites on every push to `main` and every pull
request, across Python 3.10-3.13.

## Releasing duckbill

duckbill publishes to PyPI through GitHub Actions Trusted Publishing (OIDC -- no stored
token; the trusted publisher is configured on PyPI for project `duckbill`, repo
`pgavlin/duckpond`, workflow `release-duckbill.yml`, environment `pypi`). ducktail is not
published.

1. Bump the version in `duckbill/pyproject.toml`.
2. Commit it, then tag and push -- the tag must match the version:

       git tag duckbill-v0.2.0        # == duckbill/pyproject.toml
       git push origin duckbill-v0.2.0

The `duckbill-v*` tag triggers `.github/workflows/release-duckbill.yml`, which checks the
tag against the package version, runs the tests, builds the sdist + wheel, and publishes.
A tag that doesn't match the version fails the release before anything ships.
