#!/usr/bin/env python3
"""Deterministic gate for the ducktail skill eval: prove an investigation is genuinely
incremental, independent of how it was built.

It wipes any existing store, runs `refresh.py` twice, and asserts:

  1. run 1 (cold, full initial window) fetched some rows;
  2. run 2 (warm, only the since/overlap window) fetched strictly fewer -- the upstream's
     since-query narrowed the re-fetch, the whole point of incremental loading;
  3. no warehouse table has exact-duplicate rows (merge upsert, not append);
  4. at least one non-empty warehouse table exists.

Exit code 0 on PASS, non-zero on any failure. Inherits the environment (the workflow sets
SSL_CERT_FILE); needs `uv` on PATH and `duckdb` importable.

Usage: check_incremental.py <investigation_dir>
"""
import glob
import os
import re
import subprocess
import sys

import duckdb

# The harness summary logs `<ts>  rows <table> <comma-padded count>` (see ducktail.run());
# the logger prefixes a timestamp, so match `rows ...` anywhere on the line, not at the start.
ROWS_RE = re.compile(r"rows\s+(\S+)\s+([\d,]+)\s*$", re.M)


def _refresh(investigation: str) -> dict[str, int]:
    p = subprocess.run(["uv", "run", "refresh.py"], cwd=investigation,
                       capture_output=True, text=True)
    out = p.stdout + p.stderr
    if p.returncode != 0:
        sys.stderr.write(out)
        sys.exit(f"FAIL: refresh.py exited {p.returncode}")
    return {t: int(n.replace(",", "")) for t, n in ROWS_RE.findall(out)}


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("usage: check_incremental.py <investigation_dir>")
    inv = sys.argv[1]

    for db in glob.glob(os.path.join(inv, "*.duckdb")):
        os.remove(db)                                   # force run 1 to be a cold, full load

    run1 = _refresh(inv)
    run2 = _refresh(inv)
    t1, t2 = sum(run1.values()), sum(run2.values())
    print(f"run 1 (cold) fetched {t1} rows: {run1}")
    print(f"run 2 (warm) fetched {t2} rows: {run2}")

    if t1 <= 0:
        sys.exit("FAIL: run 1 fetched no rows -- the pipeline produced no data")
    if t2 >= t1:
        sys.exit(f"FAIL: run 2 ({t2}) did not fetch fewer than run 1 ({t1}) -- not incremental "
                 "(the upstream must support a since-query so the re-fetch narrows server-side)")

    dbs = glob.glob(os.path.join(inv, "*.duckdb"))
    if not dbs:
        sys.exit("FAIL: no .duckdb store was created")
    con = duckdb.connect(dbs[0], read_only=True)
    tables = [r[0] for r in con.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'warehouse'"
    ).fetchall()]
    if not tables:
        sys.exit("FAIL: no warehouse tables were created")
    for t in tables:
        n = con.execute(f'SELECT count(*) FROM warehouse."{t}"').fetchone()[0]
        distinct = con.execute(
            f'SELECT count(*) FROM (SELECT DISTINCT * FROM warehouse."{t}")').fetchone()[0]
        if n != distinct:
            sys.exit(f"FAIL: table {t} has {n - distinct} duplicate rows (merge should upsert)")
        print(f"  {t}: {n} rows, no duplicates")
    con.close()

    reduction = 100 * (1 - t2 / t1)
    print(f"INCREMENTAL CHECK: PASS  (run 2 fetched {reduction:.0f}% fewer rows than run 1)")


if __name__ == "__main__":
    main()
