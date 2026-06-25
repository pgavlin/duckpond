# /// script
# requires-python = ">=3.10"
# dependencies = ["duckdb>=1.0"]
# ///
"""A minimal DuckDB-native incremental-load harness.

Run it with no venv:

    uv run refresh.py            # one incremental load into ./ducktail.duckdb
    uv run refresh.py --selftest # prove incremental + merge + overlap, no creds

The inline `# /// script` block above is PEP 723 metadata; uv builds a cached ephemeral
env from it -- nothing to create, activate, or forget to stop. The deps travel with the file.

The whole harness is `load()` below -- ~15 lines. Incremental loading is three visible steps:
the high-water mark (`max(cursor)`), the overlap rewind (`hw - overlap`), and the merge
(`INSERT ... ON CONFLICT`). A source is a table DDL + primary key + cursor column + overlap +
a `fetch(since)`.
"""
import argparse
import os
import sys
from collections.abc import Callable, Iterator
from typing import TypedDict

import duckdb


# The fields of a source spread into load() as keyword arguments (everything but `fetch`,
# which is passed separately so the same source dict can be reused with different fetches).
class SourceSpec(TypedDict):
    table: str
    ddl: str
    primary_key: tuple[str, ...]
    cursor: str
    overlap: int
    initial: int


# The harness -- the whole "framework".
def load(
    con: duckdb.DuckDBPyConnection,
    *,
    table: str,
    ddl: str,
    primary_key: tuple[str, ...],
    cursor: str,
    overlap: int,
    initial: int,
    fetch: Callable[[int], Iterator[dict[str, object]]],
) -> int:
    """Incrementally load one source into `table`, returning the rows fetched this run.

    Idempotent over the overlap window: re-fetched rows upsert by primary key, so a
    correction (a late-arriving or changed row) lands instead of duplicating."""
    con.execute(f"CREATE TABLE IF NOT EXISTS {ddl}")
    row = con.execute(f"SELECT max({cursor}) FROM {table}").fetchone()
    # fetchone() is typed Any at the duckdb boundary; the cursor column is an int.
    hw: int | None = row[0] if row is not None else None
    since = hw - overlap if hw is not None else initial   # the overlap rewind, in the open
    rows = list(fetch(since))                              # source bounds its fetch on `since`
    if not rows:
        return 0
    cols = list(rows[0])
    sets = ", ".join(f"{c} = excluded.{c}" for c in cols if c not in primary_key)
    con.executemany(
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))}) "
        f"ON CONFLICT ({', '.join(primary_key)}) DO UPDATE SET {sets}",  # native merge/upsert
        [[r[c] for c in cols] for r in rows],
    )
    return len(rows)


# A source, ported from template/sources/example_hourly.py: a synthetic hourly metric
# whose clock and revision are env-driven so the load loop is testable with no credentials.
# Swap `fetch` for a real fetch (boto3, a Snowflake query, an observability API) for a real source.
HOUR = 3600
EPOCH0 = 1_700_000_000 // HOUR * HOUR
PARTITIONS = ["us-east", "eu-west", "ap-south"]

EXAMPLE: SourceSpec = dict(
    table="example_hourly",
    ddl="example_hourly (ts BIGINT, partition VARCHAR, requests BIGINT, loaded_rev BIGINT, "
        "PRIMARY KEY (ts, partition))",
    primary_key=("ts", "partition"),
    cursor="ts",
    overlap=2 * HOUR,
    initial=EPOCH0,
)


def example_fetch(since: int) -> Iterator[dict[str, object]]:
    now_ts = EPOCH0 + int(os.environ.get("NOW_HOURS", "6")) * HOUR
    revision = int(os.environ.get("REVISION", "0"))
    ts = (since // HOUR) * HOUR  # align to the hour
    while ts < now_ts:
        for i, partition in enumerate(PARTITIONS):
            yield {"ts": ts, "partition": partition,
                   "requests": ((ts // HOUR) % 24) * 100 + i * 10, "loaded_rev": revision}
        ts += HOUR


def refresh(db_path: str) -> None:
    con = duckdb.connect(db_path)
    try:
        n = load(con, fetch=example_fetch, **EXAMPLE)
        row = con.execute("SELECT count(DISTINCT ts) FROM example_hourly").fetchone()
        hours = row[0] if row is not None else 0
        print(f"example_hourly: fetched {n} rows; warehouse now holds {hours} distinct hours")
    finally:
        con.close()


def selftest() -> bool:
    """Prove the three incremental properties (incremental, overlap, merge), credential-free."""
    import tempfile

    ok = True

    def check(label: str, cond: bool) -> None:
        nonlocal ok
        ok = ok and cond
        print(f"  [{'PASS' if cond else 'FAIL'}] {label}")

    with tempfile.TemporaryDirectory() as d:
        db = os.path.join(d, "t.duckdb")

        # Run 1: 6 hours of data, revision 0.
        os.environ["NOW_HOURS"], os.environ["REVISION"] = "6", "0"
        con = duckdb.connect(db)
        n1 = load(con, fetch=example_fetch, **EXAMPLE)
        rows1 = con.execute("SELECT count(*), count(DISTINCT ts) FROM example_hourly").fetchone()
        con.close()
        print(f"run 1 (NOW_HOURS=6, REVISION=0): fetched {n1}")
        check("run 1 loads 6 hours x 3 partitions = 18 rows", n1 == 18 and rows1 == (18, 6))

        # Run 2: clock advances to 9 hours, revision 1.
        os.environ["NOW_HOURS"], os.environ["REVISION"] = "9", "1"
        con = duckdb.connect(db)
        n2 = load(con, fetch=example_fetch, **EXAMPLE)
        totals = con.execute(
            "SELECT count(*), count(DISTINCT ts) FROM example_hourly").fetchone()
        assert totals is not None  # a count query always returns one row
        total, hours = totals
        # hours are EPOCH0 + k*HOUR for k in 0..8; check loaded_rev per hour-index.
        rev_by_k = dict(con.execute(
            "SELECT (ts - ?) / ? AS k, max(loaded_rev) FROM example_hourly GROUP BY 1",
            [EPOCH0, HOUR]).fetchall())
        con.close()
        print(f"run 2 (NOW_HOURS=9, REVISION=1): fetched {n2}")

        # Incremental: run 2 fetched only the new+overlap slice (hours 3..8 = 18 rows),
        # not all 9 hours from scratch.
        check("run 2 is incremental (fetched 18 = hours 3..8, not 27 from scratch)", n2 == 18)
        # Overlap + merge: hours 3,4,5 existed at rev 0 and were re-fetched and upserted to rev 1.
        check("overlap re-fetch + merge upserted hours 3-5 to rev 1",
              rev_by_k.get(3) == 1 and rev_by_k.get(4) == 1 and rev_by_k.get(5) == 1)
        # Untouched history stays put: hours 0,1,2 were outside the overlap, still rev 0.
        check("hours 0-2 (outside overlap) untouched at rev 0",
              rev_by_k.get(0) == 0 and rev_by_k.get(1) == 0 and rev_by_k.get(2) == 0)
        # New hours appended without rebuilding: 9 distinct hours, 27 rows, no duplicates.
        check("merge upsert (no dupes): 9 distinct hours, 27 rows", (total, hours) == (27, 9))

    print("\nSELFTEST:", "PASS" if ok else "FAIL")
    return ok


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--selftest", action="store_true",
                    help="prove incremental + merge + overlap against a temp store, no creds")
    args = ap.parse_args()
    if args.selftest:
        sys.exit(0 if selftest() else 1)
    here = os.path.dirname(os.path.abspath(__file__))
    refresh(os.path.join(here, "ducktail.duckdb"))


if __name__ == "__main__":
    main()
