"""Backend surface plus the dialect-aware parameter scan shared by every backend.

`$name` is the one author-facing bind placeholder. Discovery and translation are
the same scan: sqlglot tokenizes the SQL for the backend's dialect so we know the
source spans of string literals, quoted identifiers, and dollar-quoted bodies;
comment spans we add ourselves (guarded by those string spans). A `$name` counts
only when it falls outside every protected span. We use sqlglot to find non-code
regions, not to interpret `$name` -- which is a duckbill convention, not native
to each dialect.
"""

import queue
import re
import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, Generic, Protocol, TypeVar

import sqlglot

# A result row, column name -> JSON-safe value (see jsonable()).
Row = dict[str, object]
# A column's name and the table it belongs to (per schema()/table_columns()).
Schema = dict[str, list[str]]
# A table's docs: {"name", "comment", "columns": [{"name","type","comment"}]}.
DocsTable = dict[str, Any]


class DBAPIConnection(Protocol):
    """The slice of a PEP-249 connection the pool needs: a cursor and close."""

    def cursor(self) -> "DBAPICursor": ...
    def close(self) -> None: ...


class DBAPICursor(Protocol):
    """The slice of a PEP-249 cursor the backends use. `description` is a property
    (read-only) so concrete cursor types -- whose own `description` is a tuple --
    satisfy it covariantly."""

    @property
    def description(self) -> Any: ...
    def execute(self, sql: str, *args: Any) -> Any: ...
    def fetchall(self) -> Sequence[Sequence[Any]]: ...
    def fetchmany(self, size: int = ...) -> Sequence[Sequence[Any]]: ...


# A pooled connection: bounded above by DBAPIConnection so the pool can close it.
ConnT = TypeVar("ConnT", bound=DBAPIConnection)


_PARAM = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")

# sqlglot token types whose source span is non-code: string literals (incl.
# Postgres/Snowflake dollar-quoting -> HEREDOC/RAW) and quoted identifiers.
_PROTECTED_TOKENS = {
    "STRING", "HEREDOC_STRING", "RAW_STRING", "NATIONAL_STRING",
    "BYTE_STRING", "HEX_STRING", "BIT_STRING", "IDENTIFIER",
}
# Line-comment markers; only MySQL adds '#'. Block comments /* */ are universal.
_LINE_COMMENTS = {"mysql": ("--", "#")}

_STYLE: dict[str, Callable[[str], str]] = {
    "duckdb": lambda n: f"${n}",      # native; passthrough
    "sqlite": lambda n: f":{n}",      # sqlite3 named paramstyle
    "pyformat": lambda n: f"%({n})s",  # psycopg / PyMySQL / snowflake-connector
}


def _in(spans: Sequence[tuple[int, int]], i: int) -> bool:
    return any(a <= i <= b for a, b in spans)


def _comment_spans(sql: str, dialect: str, str_spans: Sequence[tuple[int, int]]) -> list[tuple[int, int]]:
    markers = _LINE_COMMENTS.get(dialect, ("--",))
    spans, i, n = [], 0, len(sql)
    while i < n:
        if _in(str_spans, i):  # a marker inside a string is not a comment
            i += 1
            continue
        if sql.startswith("/*", i):
            j = sql.find("*/", i + 2)
            j = n - 1 if j < 0 else j + 1
            spans.append((i, j))
            i = j + 1
            continue
        if any(sql.startswith(m, i) for m in markers):
            j = sql.find("\n", i)
            j = n - 1 if j < 0 else j - 1
            spans.append((i, j))
            i = j + 1
            continue
        i += 1
    return spans


def _protected(sql: str, dialect: str) -> list[tuple[int, int]]:
    try:
        toks = sqlglot.tokenize(sql, dialect=dialect)
    except Exception:  # a tokenize failure must not blank the chart -- protect nothing
        toks = []
    str_spans = [(t.start, t.end) for t in toks if t.token_type.name in _PROTECTED_TOKENS]
    return str_spans + _comment_spans(sql, dialect, str_spans)


def referenced_params(sql: str, dialect: str = "duckdb") -> set[str]:
    """The set of $name placeholders a query references, ignoring those inside
    strings, quoted identifiers, comments, or dollar-quoted bodies."""
    spans = _protected(sql, dialect)
    return {m.group(1) for m in _PARAM.finditer(sql) if not _in(spans, m.start())}


def bind(
    sql: str, args: Mapping[str, object], dialect: str, paramstyle: str
) -> tuple[str, dict[str, object]]:
    """Translate $name to the driver's paramstyle and bind only referenced params.

    Returns (translated_sql, params). For 'pyformat' backends, literal '%' in the
    SQL is escaped to '%%' so LIKE patterns survive the driver's own substitution.
    """
    spans = _protected(sql, dialect)
    fmt = _STYLE[paramstyle]
    esc: Callable[[str], str] = (
        (lambda s: s.replace("%", "%%")) if paramstyle == "pyformat" else (lambda s: s))
    out: list[str] = []
    last = 0
    used: set[str] = set()
    for m in _PARAM.finditer(sql):
        if _in(spans, m.start()):
            continue
        name = m.group(1)
        out.append(esc(sql[last:m.start()]))
        out.append(fmt(name))
        last = m.end()
        used.add(name)
    if not used:
        return sql, {}          # no binds -> run() calls execute(q) with no driver
                                # %-substitution, so the SQL must stay verbatim
    out.append(esc(sql[last:]))
    return "".join(out), {k: v for k, v in args.items() if k in used}


def jsonable(v: object) -> object:
    """Coerce a driver value to something the JSON encoder and Vega accept."""
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (datetime, date, time)):
        return v.isoformat()
    if isinstance(v, (bytes, bytearray, memoryview)):
        return bytes(v).hex()
    return v


def jsonable_row(row: Sequence[object]) -> list[object]:
    return [jsonable(v) for v in row]


class Backend:
    """The surface the server and bundler speak. Subclasses implement these.

    dialect:    sqlglot dialect name for the scan
    paramstyle: key into _STYLE for bind()
    bundleable: can `duckbill bundle` embed this backend's data?
    """

    dialect: str = "duckdb"
    paramstyle: str = "duckdb"
    bundleable: bool = False

    def run(self, sql: str, args: Mapping[str, object]) -> tuple[list[str], list[Row]]:
        raise NotImplementedError

    def query(self, sql: str, limit: int = 2000) -> tuple[list[str], list[Row], bool]:
        raise NotImplementedError

    def docs(self) -> list[DocsTable]:
        raise NotImplementedError

    def schema(self) -> Schema:
        raise NotImplementedError

    def table_columns(self) -> Schema:
        """Columns per table, keyed by the same qualified names as `schema()`:
        `{<schema>.<name>: [col, ...]}`. The bundler's column pruner feeds this to
        sqlglot as a schema map. Serve-only backends don't implement it."""
        raise NotImplementedError(f"{type(self).__name__} is serve-only (not bundleable)")

    def export_parquet(
        self, qualified: str, columns: Sequence[str] | None = None, compression: str = "snappy"
    ) -> bytes:
        raise NotImplementedError(f"{type(self).__name__} is serve-only (not bundleable)")

    def close(self) -> None:
        pass


# Parquet codecs DuckDB writes and reads back. Restricted to an allowlist because
# the value is interpolated into a COPY statement (it can't be a bind parameter).
_PARQUET_CODECS = frozenset({"snappy", "zstd", "gzip", "uncompressed"})


def parquet_codec(compression: str) -> str:
    """Validate and normalize a Parquet compression name to a COPY keyword."""
    c = compression.lower()
    if c not in _PARQUET_CODECS:
        raise ValueError(
            f"unsupported Parquet compression {compression!r}; "
            f"expected one of {sorted(_PARQUET_CODECS)}")
    return c


class Pool(Generic[ConnT]):
    """A tiny bounded connection pool for network backends. Connections are made
    lazily up to `size`, then borrowers block for a free one. LIFO so a small
    working set stays warm."""

    def __init__(self, factory: Callable[[], ConnT], size: int = 4):
        self._factory = factory
        self._free: queue.LifoQueue[ConnT] = queue.LifoQueue()
        self._made = 0
        self._size = max(1, size)
        self._lock = threading.Lock()

    @contextmanager
    def borrow(self) -> Iterator[ConnT]:
        con = self._acquire()
        ok = False
        try:
            yield con
            ok = True
        finally:
            if ok:
                self._free.put(con)
            else:
                try:
                    con.close()
                except Exception:
                    pass
                with self._lock:
                    self._made -= 1

    def _acquire(self) -> ConnT:
        try:
            return self._free.get_nowait()
        except queue.Empty:
            pass
        with self._lock:
            make = self._made < self._size
            if make:
                self._made += 1
        if make:
            try:
                return self._factory()
            except Exception:
                with self._lock:
                    self._made -= 1
                raise
        return self._free.get()  # all in use -- block for one

    def close(self) -> None:
        while not self._free.empty():
            try:
                self._free.get_nowait().close()
            except Exception:
                pass


class DBAPIBackend(Backend, Generic[ConnT]):
    """Shared run/query for PEP-249 drivers: translate $name via bind(), borrow a
    pooled connection, coerce rows. Subclasses set dialect/paramstyle and
    implement `_connect` (returns a new read-only DBAPI connection), `docs`,
    `schema`."""

    def __init__(self, *, pool: int = 4):
        self._pool: Pool[ConnT] = Pool(self._connect, size=pool)

    def _connect(self) -> ConnT:
        raise NotImplementedError

    def run(self, sql: str, args: Mapping[str, object]) -> tuple[list[str], list[Row]]:
        q, p = bind(sql, args, self.dialect, self.paramstyle)
        with self._pool.borrow() as con:
            cur = con.cursor()
            cur.execute(q, p) if p else cur.execute(q)
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
        return cols, [dict(zip(cols, jsonable_row(r))) for r in rows]

    def query(self, sql: str, limit: int = 2000) -> tuple[list[str], list[Row], bool]:
        with self._pool.borrow() as con:
            cur = con.cursor()
            cur.execute(sql)
            cols = [d[0] for d in cur.description]
            rows = cur.fetchmany(limit + 1)
        truncated = len(rows) > limit
        rows = rows[:limit]
        return cols, [dict(zip(cols, jsonable_row(r))) for r in rows], truncated

    def close(self) -> None:
        self._pool.close()
