# /// script
# requires-python = ">=3.10"
# dependencies = ["duckdb>=1.0", "pyarrow"]
# ///
"""Refresh the local warehouse: fetch every source into ducktail.duckdb.

Add a source by writing sources/<name>.py with a fetch + a Source/Table, importing it here, and
adding it to SOURCES. Incremental by construction -- re-runs only fetch new data.

    uv run refresh.py            # build / incrementally refresh ducktail.duckdb
    uv run refresh.py --full     # drop tables and reload from each source's initial window

Add each source's third-party deps to the inline `# /// script` header above (e.g. boto3,
snowflake-connector-python[pandas], mcp). Set ADW_LOOKBACK_DAYS to change the window depth.

At the end of each run a per-source fetch-time + row-count summary is logged. The post-load
`transforms(con)` hook runs pure-DuckDB transforms over the loaded data (joins, derived tables) --
empty here; fill it in with CREATE TABLE ... AS SELECT statements that build derived tables from the
loaded sources. Stop any running `duckbill serve` first: DuckDB allows one writer xor readers.
"""
import argparse
import os
import threading

import duckdb

from ducktail import Source, heartbeat, run, setup_logging
from sources.example_hourly import EXAMPLE

HERE = os.path.dirname(os.path.abspath(__file__))

SOURCES: list[Source] = [
    EXAMPLE,
]


def transforms(con: duckdb.DuckDBPyConnection) -> None:
    """Post-load, pure-DuckDB transforms over the warehouse. Runs after every refresh. Empty in
    the template."""
    pass


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--full", action="store_true",
                    help="drop tables and reload from each source's initial window")
    args = ap.parse_args()

    log_path = setup_logging(os.path.join(HERE, "logs"))
    print(f"logging to {log_path}")
    con = duckdb.connect(os.path.join(HERE, "ducktail.duckdb"))
    stop = threading.Event()
    threading.Thread(target=heartbeat, args=(stop,), daemon=True).start()
    try:
        run(con, SOURCES, full=args.full)
        transforms(con)
    finally:
        stop.set()
        con.close()


if __name__ == "__main__":
    main()
