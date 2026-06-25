"""ducktail -- a small DuckDB-native incremental-load harness.

Copy this file into an investigation alongside refresh.py. It is the whole "framework": declare
each source as a Source of one or more Tables with a fetch, and run() reads the high-water marks,
fetches sources concurrently, and writes batches through a single writer thread that bulk-loads via
Arrow and merges with INSERT ... ON CONFLICT. No hidden engine -- the incremental cursor is
SELECT max(cursor), the overlap is hw - overlap, the merge is ON CONFLICT. See skills/ducktail.
"""
import faulthandler
import logging
import os
import signal
import threading
import time
from collections import Counter
from collections.abc import Callable, Iterable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from queue import Queue
from typing import Literal, TypeAlias

import duckdb
import pyarrow as pa

_log = logging.getLogger("ducktail")

# A fetched batch: a list of row dicts or, for bulk fetches, an Arrow table.
Batch: TypeAlias = list[dict[str, object]] | pa.Table

# One lookback window across all sources, env-overridable. Clamp per source where the upstream's
# retention is shorter (e.g. initial(max_days=7) for a 7-day-retention source).
LOOKBACK_DAYS = int(os.environ.get("ADW_LOOKBACK_DAYS", "31"))
# Fetch independent sources concurrently. Set ADW_PARALLEL=0 to extract one at a time (the
# known-good path when debugging a stuck run).
PARALLEL = os.environ.get("ADW_PARALLEL", "1") not in ("0", "false", "no", "off")


def _days(max_days: int | None) -> int:
    return LOOKBACK_DAYS if max_days is None else min(LOOKBACK_DAYS, max_days)


def initial(max_days: int | None = None) -> int:
    """Epoch seconds for a source's first-run window start (now - the lookback), clamped to
    max_days when the upstream's retention is shorter."""
    return int(time.time()) - _days(max_days) * 86400


def window_s(max_days: int | None = None) -> int:
    """The lookback as a duration in seconds, for sources that fetch a fixed window."""
    return _days(max_days) * 86400


@dataclass
class Table:
    """One output table. mode="merge" is an incremental upsert by primary_key on the cursor column
    with an overlap re-fetch; mode="replace" is rebuilt every run (a windowed aggregate with no
    time cursor)."""
    name: str
    mode: Literal["merge", "replace"]  # incremental upsert by cursor, or rebuilt every run
    primary_key: tuple[str, ...] = ()
    cursor: str | None = None          # merge: the incremental column
    overlap: int = 0                   # merge: re-fetch window, seconds
    initial: int = 0                   # merge: first-run window start, epoch seconds


@dataclass
class Source:
    """A fetch unit producing one or more tables. produce(starts) reads its merge tables' rewound
    start cursors from `starts` (table name -> epoch) and yields (Table, batch) pairs, where batch
    is a list of dicts or a pyarrow.Table. parallel=False runs it on the main thread, for clients
    that are main-thread / event-loop bound (an OAuth loopback, anyio, some async SDKs)."""
    label: str
    tables: list[Table]
    produce: Callable[[dict[str, int]], Iterable[tuple[Table, Batch]]]
    parallel: bool = True


def setup_logging(log_dir: str, prefix: str = "refresh") -> str:
    """Send ducktail log events to a timestamped file and the console, and silence sqlglot's
    Command-fallback warnings. Returns the log path."""
    os.makedirs(log_dir, exist_ok=True)
    path = os.path.join(log_dir, datetime.now().strftime(f"{prefix}-%Y%m%d-%H%M%S.log"))
    fmt = logging.Formatter("%(asctime)s  %(message)s", "%H:%M:%S")
    _log.setLevel(logging.INFO)
    _log.propagate = False
    _log.handlers.clear()  # idempotent: a second call replaces handlers, doesn't double them
    for h in (logging.FileHandler(path), logging.StreamHandler()):
        h.setFormatter(fmt)
        _log.addHandler(h)
    logging.getLogger("sqlglot").setLevel(logging.ERROR)
    return path


def heartbeat(stop: threading.Event, period: float = 30) -> None:
    """Thread target: print a liveness line every `period`s so a long quiet fetch doesn't read as
    hung. Run as a daemon for the duration of run()."""
    t0 = time.monotonic()
    while not stop.wait(period):
        print(f"[refresh] still working... {int(time.monotonic() - t0)}s elapsed", flush=True)


# Dump every thread's stack on SIGUSR1 (kill -USR1 <pid>) -- py-spy can't attach to the macOS
# framework Python. The dump goes to stderr.
faulthandler.enable()
if hasattr(signal, "SIGUSR1"):
    faulthandler.register(signal.SIGUSR1, all_threads=True)


def _exists(con: duckdb.DuckDBPyConnection, name: str) -> bool:
    return con.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_schema='warehouse' AND table_name=?",
        [name]).fetchone() is not None


def _q(name: str) -> str:
    """Double-quote a SQL identifier so a reserved word (e.g. `by`, `order`) works as a name."""
    return '"' + name.replace('"', '""') + '"'


def _write(con: duckdb.DuckDBPyConnection, table: Table, batch: Batch, seen: set[str]) -> None:
    """Write one batch -- a list of dicts or a pyarrow.Table -- in a single vectorized statement.
    First batch of a table creates it (replace clears, merge preserves; types inferred by DuckDB
    from the Arrow schema). Merge upserts by primary key; DISTINCT ON dedups within the batch
    (DuckDB's ON CONFLICT refuses to update the same key twice in one statement). Bulk-loading
    through Arrow is what scales -- a row-by-row executemany does not."""
    tbl = batch if isinstance(batch, pa.Table) else pa.Table.from_pylist(batch)
    if tbl.num_rows == 0:
        return
    con.register("_b", tbl)
    try:
        collist = ", ".join(_q(c) for c in tbl.column_names)
        tname = f"warehouse.{_q(table.name)}"           # quote identifiers so reserved words work
        if table.name not in seen:
            defs = ", ".join(f"{_q(c)} {t}" for c, t, *_ in con.execute("DESCRIBE SELECT * FROM _b").fetchall())
            pk = f", PRIMARY KEY ({', '.join(_q(c) for c in table.primary_key)})" if table.primary_key else ""
            verb = "CREATE OR REPLACE TABLE" if table.mode == "replace" else "CREATE TABLE IF NOT EXISTS"
            con.execute(f"{verb} {tname} ({defs}{pk})")
            seen.add(table.name)
        if table.mode == "merge":
            pkc = ", ".join(_q(c) for c in table.primary_key)
            sets = ", ".join(f"{_q(c)} = excluded.{_q(c)}" for c in tbl.column_names if c not in table.primary_key)
            # DISTINCT ON keeps one row per key; ORDER BY the cursor DESC makes that the latest
            # one (last-write-wins) instead of an arbitrary pick when keys repeat within a batch.
            tiebreak = f", {_q(table.cursor)} DESC" if table.cursor else ""
            con.execute(f"INSERT INTO {tname} ({collist}) "
                        f"SELECT {collist} FROM (SELECT DISTINCT ON ({pkc}) {collist} FROM _b "
                        f"ORDER BY {pkc}{tiebreak}) "
                        f"ON CONFLICT ({pkc}) DO UPDATE SET {sets}")
        else:
            con.execute(f"INSERT INTO {tname} ({collist}) SELECT {collist} FROM _b")
    finally:
        con.unregister("_b")


def run(con: duckdb.DuckDBPyConnection, sources: list[Source], *, full: bool = False) -> Counter[str]:
    """Refresh every source into the `warehouse` schema. Reads high-water marks serially, fetches
    sources concurrently (network-bound), and writes batches on a single writer thread (DuckDB is
    one writer). full=True drops the tables first so merge cursors reset to each source's initial
    window. Returns a Counter of rows written per table."""
    con.execute("CREATE SCHEMA IF NOT EXISTS warehouse")
    if full:
        for s in sources:
            for t in s.tables:
                con.execute(f"DROP TABLE IF EXISTS warehouse.{_q(t.name)}")

    starts: dict[str, int] = {}
    for s in sources:
        for t in s.tables:
            if t.mode == "merge":
                assert t.cursor is not None  # a merge table is incremental on its cursor column
                row = (con.execute(f"SELECT max({_q(t.cursor)}) FROM warehouse.{_q(t.name)}").fetchone()
                       if _exists(con, t.name) else None)
                # max(cursor) over the cursor column (epoch seconds), or None on an empty/missing
                # table. fetchone() is typed Any at the duckdb boundary; the cursor is an int.
                hw: int | None = row[0] if row is not None else None
                starts[t.name] = (hw - t.overlap) if hw is not None else t.initial

    # The writer drains (Table, Batch) items; None signals end-of-stream.
    q: Queue[tuple[Table, Batch] | None] = Queue(maxsize=8)
    counts: Counter[str] = Counter()
    durations: dict[str, float] = {}
    seen: set[str] = set()

    def writer() -> None:
        while True:
            item = q.get()
            if item is None:
                return
            table, batch = item
            # Keep draining even if a write fails: a dead writer would block the
            # producers on the bounded queue forever. Degrade like the fetch side.
            try:
                _write(con, table, batch, seen)
                counts[table.name] += batch.num_rows if isinstance(batch, pa.Table) else len(batch)
            except Exception as e:  # noqa: BLE001 -- a write failure must not wedge the run
                _log.warning("write to %s FAILED: %s", table.name, str(e)[:300])

    def produce(s: Source) -> None:
        t0 = time.monotonic()
        try:
            for table, batch in s.produce(starts):
                q.put((table, batch))
        except Exception as e:  # noqa: BLE001 -- one source failing must not abort the rest
            _log.warning("source %s FAILED: %s", s.label, str(e)[:300])
        finally:
            durations[s.label] = time.monotonic() - t0

    wt = threading.Thread(target=writer, daemon=True)
    wt.start()
    par = [s for s in sources if s.parallel] if PARALLEL else []
    seq = [s for s in sources if not (PARALLEL and s.parallel)]
    with ThreadPoolExecutor(max_workers=max(1, len(par)) if par else 1) as ex:
        futs: list[Future[None]] = [ex.submit(produce, s) for s in par]
        for s in seq:              # main-thread sources (anyio / one shared session), overlapping the pool
            produce(s)
        for f in futs:
            f.result()
    q.put(None)
    wt.join()

    _log.info("==== refresh summary ====")
    for label, dt in sorted(durations.items(), key=lambda kv: -kv[1]):
        _log.info(f"  {label:<16} fetched in {dt:7.1f}s")
    for tbl, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        _log.info(f"  rows {tbl:<24} {n:>12,}")
    return counts
