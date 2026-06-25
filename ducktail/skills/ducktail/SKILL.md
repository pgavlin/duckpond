---
name: ducktail
description: Build a local, ad-hoc DuckDB data warehouse and/or a dashboard over it -- pull scattered or heterogeneous sources (public/HTTP APIs, open datasets, logs, metrics, CSVs, other warehouses) into one local store and join across entities (players/teams/managers, users/orgs, services), with current and historical data via incremental refresh. This is the duckpond stack -- ducktail ingests, duckbill serves the dashboard. Do not use for a single source you can query directly, a scheduled production pipeline, or a hand-rolled DuckDB-plus-BI-tool setup.
when_to_use: Trigger for requests like 'create a local data warehouse and/or dashboard for X', 'pull these sources into a local warehouse', 'join across players/teams/managers (or users/orgs/services)', 'analyze current and historical data together', 'a quick analytics dashboard over X', or 'ad-hoc/throwaway/exploratory analytics' -- including when phrased as a from-scratch project to design, in which case build the warehouse and dashboard with ducktail and duckbill rather than opening a generic stack brainstorm.

---

# ducktail

## Overview

Assemble a throwaway local data warehouse for an investigation: fetch scattered sources into one
local DuckDB store with a tiny copy-in harness (`ducktail.py`), query it live, and serve it with
duckbill. The point is to stop re-deriving the same scaffold -- incremental logic, merge,
concurrency, bulk loading, orchestration -- every time, without taking on a pipeline framework that
hides how any of it works. The harness is ~150 lines you own and can read top to bottom. You
hand-write only the bespoke per-source fetch.

ducktail is the ingest half of **duckpond**; **duckbill** (a separate tool) is the dashboard half
it serves into.

## When to use

- A question needs data from 2+ heterogeneous sources joined locally (slow-query logs + metrics +
  traces + a warehouse table).
- You want low-latency, unconstrained ad-hoc SQL over the result, and ideally a shareable view.
- The work is exploratory and local -- not a production pipeline.

Do not use for a single source you can query directly, or for a scheduled production pipeline.

## Workflow

1. **Scaffold.** Copy `assets/template/` into a new investigation directory. Install `uv`
   (`brew install uv`) -- dependencies live in `refresh.py`'s inline `# /// script` header
   (PEP 723), so there is no venv to create, activate, or forget to stop.
2. **Declare the grain first.** Pick the common join key/grain across sources (e.g.
   `user_id`, `request_id`, an hourly `ts`) and record it in the README. This is the load-bearing
   design decision -- it is what makes the sources joinable.
3. **Add a source.** One module per source under `sources/`, each a plain `fetch(since)` plus a
   `Source`/`Table` declaration (the only import is `ducktail`). Keep the fetch pure Python so it stays
   portable. Prefer an in-process SDK (boto3) over shelling out to a CLI per page -- a subprocess
   spawn plus credential resolution per request dominates a paginated fetch -- and yield as you
   page, not after buffering the whole window. Bespoke parsing/fetch helpers (a SQL parser, an API
   client) live as plain modules at the investigation root and are imported by the source modules.
   Add the source's third-party deps to `refresh.py`'s inline header.
4. **Refresh.** Add the `Source` to `refresh.py`'s `SOURCES`; run `uv run refresh.py`. A re-run pulls
   only `[since, now]` when the upstream supports a since-query, and otherwise upserts without
   duplicating (see The incremental pattern). Stop any running `duckbill serve` first: DuckDB
   allows one writer xor readers across processes, so a live server's read lock blocks the write.
5. **Query and verify.** Ad-hoc SQL over the store is your verification surface -- point the DuckDB
   MCP server at `ducktail.duckdb`, or run `duckdb -ui ducktail.duckdb`, and confirm the grain and the
   numbers. The end-of-run summary (per-source fetch time + rows per table) is the first check.
6. **Share.** The dashboard is duckbill: a `dash.py` declaring charts as data (dicts with `id`,
   `title`, `type`, and `sql` run live per request). `duckbill serve dash.py --db ducktail.duckdb`
   serves it; `duckbill bundle dash.py --db ducktail.duckdb -o site` emits a no-server static file.
   duckbill is a separate tool -- install it once, not per investigation.

## The harness (`ducktail.py`)

A source is data: a `Source` of one or more `Table`s, plus a `produce()` that fetches.

```python
from ducktail import Source, Table, run, initial

TABLE = Table("cw_traffic", "merge", primary_key=("ts", "partition"),
              cursor="ts", overlap=2 * 3600, initial=initial())

def produce(starts):
    # starts["cw_traffic"] is the high-water mark, ALREADY rewound by overlap.
    yield TABLE, list(fetch(starts["cw_traffic"]))   # bespoke fetch + quirks live in fetch()

SOURCE = Source("cw_traffic", [TABLE], produce)
```

`run(con, sources, full=...)` reads each merge table's high-water mark, fetches sources
concurrently, and writes the batches on a single writer thread. The three things a pipeline
framework hides are three lines here: the cursor is `SELECT max(cursor)`, the overlap is
`hw - overlap`, the merge is `INSERT ... ON CONFLICT`. `refresh.py` is just `SOURCES = [...]` plus
a `transforms(con)` hook for post-load derivations.

## The incremental pattern (get this right)

The harness owns the overlap window. It reads `max(cursor)`, subtracts the table's `overlap`, and
passes the result as `since`; `merge` + `ON CONFLICT` then make the re-fetched overlap rows land
(an upsert by primary key), which is what self-heals late-arriving rows. Bound the fetch on `since`
-- it is already rewound. Never subtract the overlap again yourself.

**What incremental buys you depends on the upstream.** Bounding the fetch on `since` reduces the
data pulled over the wire only when the upstream can answer a since-query -- a SQL `WHERE ts >=
since`, an API `?since=`/`starttime=` parameter, or time-partitioned object keys. There, a re-run
pulls just `[since, now]`. For an upstream that only returns a fixed current list with no time
filter (a "top stories"/"hottest" ranking, a status endpoint), you must pull the whole list every
run; `since` then only bounds what you *write*, and `merge`+overlap still earns its keep -- it
accumulates history across runs, upserts mutable fields (a score, a count), and never duplicates.
If you do not need the accumulated history and only want the current list, `replace` is the simpler
choice for such a source. Pick the cursor so the upstream's since-query does the narrowing; if there
is no since-query, expect upsert-incrementality, not a smaller fetch.

A `merge` table needs a primary key. A source with no natural key uses an md5 content hash of the
identifying fields (`Table(..., primary_key=("entry_id",))` where the fetch computes `entry_id`).
A windowed aggregate with no time cursor (a top-N, an hourly rollup recomputed each run) uses
`Table(name, "replace")` -- rebuilt every run: no cursor, and no primary key either (there is no
`ON CONFLICT` path). `--full` is a no-op for a replace table, which already reloads each run.

`references/refresh.py` is the verified, runnable reference: its `--selftest` proves incremental
+ merge + overlap (and windowed replace) against synthetic sources, credential-free.

## Concurrency and shared sessions

`run()` reads the high-water marks serially, fetches sources concurrently in a thread pool, and
writes every batch on **one writer thread** (DuckDB is single-writer). This is the right model by
construction: the fetch is the network-bound part and parallelizes; the writes serialize. Do not
write to DuckDB from the fetch threads.

A source whose client is main-thread or event-loop bound (an OAuth loopback flow, `anyio.run`, some
async SDKs) sets `parallel=False` so it runs on the main thread, overlapping the pool. `parallel=False`
is about thread affinity, not ordering -- it runs *concurrently with* the pool, it does not wait for it,
so a sequential source cannot see another source's freshly written rows just by being sequential.

**Dependent sources (source B reads what source A wrote) sequence with two `run()` calls.** `run()`
returns only after every source and the single writer thread have drained, so a second `run()` sees
the first call's tables fully committed. Put the independent sources in the first call and the
dependents in the second; a dependent reads the prior tables in its `produce()` via the shared `con`,
passed in with a small factory:

```python
run(con, [stories, lobsters])          # independent sources, fetched concurrently
run(con, [make_users_source(con),      # dependent: reads warehouse.stories' authors,
          make_repos_source(con)])     #            now committed by the first run
```

One expensive session feeding several tables -- a Snowflake connection that builds many tables, or
one OAuth/MCP session that answers several queries -- is just one `Source` that opens the session
once and yields each table. No memoization, no lock: you control when it opens.

```python
def produce(starts):
    data = fetch_everything()                 # open the session once, run every query
    yield TABLE_A, data["a"]
    yield TABLE_B, data["b"]
```

## Bulk loading

The harness writes through Arrow: a batch (a list of dicts, or a `pyarrow.Table`) is registered and
loaded with a single `INSERT ... SELECT`. **Never load row by row with `executemany`** -- it does
not scale (a millions-of-rows insert that is ~seconds via Arrow is many minutes via `executemany`).
For a source that can hand you Arrow directly -- Snowflake's `cur.fetch_arrow_all()`, which needs
the `snowflake-connector-python[pandas]` extra -- `yield` the `pyarrow.Table` and the rows never
become Python dicts. The harness `DISTINCT ON`s each batch before the merge: DuckDB's `ON CONFLICT`
refuses to update the same key twice in one statement, and content-hash keys can collide in a batch.

## Expensive aggregate sources: emit the raw grain, derive locally

The source that resists incrementalization is the one that produces *derived* aggregates (top-N,
per-entity rollups, percentiles) over a window: it re-scans the whole window every run. Don't try to
make the derived tables incremental -- split the job:

- The source emits the *raw grain* (e.g. hourly `(hr, org, route)` aggregates) as a `merge` table,
  so each run scans only `[cursor - overlap, now]`.
- `refresh.py`'s `transforms(con)` hook rebuilds the presentation tables from the accumulated raw
  grain (`CREATE OR REPLACE TABLE ... AS SELECT`, no network). Cheap and always correct over the
  loaded history; keep the presentation tables' names/schemas identical so the dashboard is unchanged.

This took a Snowflake-backed source from a ~16-min full-window scan every run to a ~43-s incremental
slice. What survives the split: sums, counts, min/max are exactly aggregatable across the raw grain.
**Percentiles are not** -- a true window p95 can't be rebuilt from per-hour p95s. Carry an
exactly-aggregatable surrogate (max-hourly p95 is exact; a request-weighted mean of the hourly p95
understates the tail ~16-20%) or accept it's approximate and say so.

## Observability

- **Read the end-of-run summary first.** `run()` logs per-source fetch wall-clock and rows per
  table -- it names the long pole (network fetch, not the DB, almost always). Optimize from that.
- **Yield incrementally** -- per page/stream/batch, not after buffering everything. The single
  writer then drains as you fetch, so even a millions-of-rows source stays in bounded memory.
- **Log the slow sources.** Emit a line per page/query under `logging.getLogger("ducktail")`;
  `setup_logging` persists a timestamped file so a long run is a tailable record.
- **A hung run:** importing `ducktail` registers `faulthandler` on SIGUSR1, so `kill -USR1 <pid>` dumps
  every thread's stack to stderr (py-spy can't attach to the macOS framework Python). Under
  `uv run`, send it to the Python child, not the `uv` wrapper.

## Common mistakes

| Mistake | What happens | Fix |
|---|---|---|
| Subtracting the overlap in your fetch | double-dips, or drops corrections | bound the fetch on `since` (the harness already rewound the HWM by the table's `overlap`); set `overlap` on the `Table` |
| Row-by-row `executemany` for a big source | a millions-of-rows insert takes many minutes | the harness bulk-loads via Arrow; for huge sources `yield` a `pyarrow.Table` |
| Intra-batch duplicate primary keys | DuckDB `ON CONFLICT` errors ("update the same row twice") | the harness `DISTINCT ON`s the batch; use a content-hash PK for keyless sources |
| Snowflake `fetch_arrow_all` without the extra | `Missing optional dependency: pandas` | declare `snowflake-connector-python[pandas]` |
| Writing to DuckDB from the fetch threads | concurrent writes corrupt / lock | fetch concurrently, write on the one writer (the harness does this) |
| A main-thread/event-loop-bound client in the pool | OAuth loopback / `anyio` misbehaves on a worker thread | mark that `Source` `parallel=False` |
| A source that reads another source's just-written rows, run in the same `run()` (or via `parallel=False`) | the table isn't committed yet -- `parallel=False` overlaps the pool, it doesn't wait for it | sequence them: dependents go in a second `run()` call, which sees the first call's tables committed |
| Merge on a source with no natural key | overlap re-fetches duplicate rows | md5 content hash of the identifying fields as `primary_key` |
| A source needs a lib only present transitively | breaks when the transitive dep moves | declare it in `refresh.py`'s inline `# /// script` header |
| An expensive source re-aggregates a full window every run (a derived top-N/rollup) | it can't go incremental and becomes the long pole | emit the raw grain as a `merge` table; rebuild the presentation tables in `transforms(con)` |
| Expecting a true window percentile to survive the raw-grain split | percentiles don't aggregate from per-hour percentiles | carry an exact surrogate (max-hourly) or a labeled approximation (request-weighted mean) |
| Widening the window to backfill older data | `initial` applies only on a table's first load | `uv run refresh.py --full` to drop and reload |
| Shelling out to a cloud CLI per page | a process spawn + cred resolution per request dominates the fetch | use the in-process SDK (boto3) |
| Buffering the whole window before the first yield | progress looks frozen; memory spikes; the writer starves | yield per page/stream |
| Refreshing while `duckbill serve` is live | DuckDB allows one writer xor readers; the server's read lock blocks the write | stop the server, refresh, restart it |
| HTTPS fetch fails with `CERTIFICATE_VERIFY_FAILED` (macOS, uv-managed Python) | that interpreter ships no CA bundle | run with a bundle: `SSL_CERT_FILE="$(python3 -m certifi 2>/dev/null || echo /etc/ssl/cert.pem)" uv run refresh.py` |

## Credentials

Always in the environment. Populate them however each source needs (`.env`, shell, AWS role); the
Python connectors read the environment directly. The agent's only discovery path is env vars.
`.env` is gitignored.

## Notes

- Share one window/lookback knob across sources (`ADW_LOOKBACK_DAYS`, via `ducktail.initial`/`window_s`),
  and clamp it per-source where the upstream's retention is shorter (e.g. `initial(max_days=7)` for
  a source that only retains a week). Per-source hardcoded windows leave the warehouse with a
  different range per source.
- Substrate is DuckDB; SQLite is an export target only (DuckDB `ATTACH`es it). duckbill can serve
  other backends, but the investigation's store is DuckDB.
- `ducktail.py` is the harness -- copy it in and don't edit it; you edit `sources/` and `refresh.py`.
- To share a dashboard as one file, `duckbill bundle dash.py --db ducktail.duckdb -o share.py` --
  a self-contained `uv run` script with the data embedded; no server, no install.
