# Spike: the DuckDB-native harness (reference)

A runnable reference for the `ducktail` harness -- the shape every investigation's `refresh.py` uses.
`refresh.py` is self-contained (a synthetic, credential-free source) so the mechanics run end to
end against DuckDB with no setup.

## Run it

```sh
uv run refresh.py --selftest   # proves incremental + merge + overlap + replace, no creds
uv run refresh.py              # one incremental load into ./ducktail.duckdb
```

Dependencies are declared in the file's PEP 723 `# /// script` header; `uv` builds the env -- no
venv.

## What it shows

The whole "framework" is `load()`/`run()`: the incremental cursor is `SELECT max(cursor)`, the
overlap is `hw - overlap`, the merge is `INSERT ... ON CONFLICT`, and the load is one vectorized
Arrow `INSERT ... SELECT` (never a row-by-row `executemany`). `--selftest` advances a synthetic
clock across two runs and asserts the overlap window re-fetches and upserts (merge), older rows
stay untouched, there are no duplicates, and a windowed `replace` table rebuilds clean -- the same
contract the template's `test_template_incremental` enforces.
