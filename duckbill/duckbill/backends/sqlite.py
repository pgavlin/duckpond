"""SQLite backend. Read-only via the URI immutable mode. No catalog comments
exist, so docs() returns names and types with empty comments. Bundleable: a
throwaway in-process DuckDB reads the file via sqlite_scanner to make Parquet."""

import os
import sqlite3
import tempfile
from collections.abc import Sequence

from .base import DBAPIBackend, DocsTable, Schema, parquet_codec, quote_ident


class SQLiteBackend(DBAPIBackend[sqlite3.Connection]):
    dialect = "sqlite"
    paramstyle = "sqlite"
    bundleable = True

    def __init__(self, path: str, read_only: bool = True, pool: int = 4):
        self._path = os.path.abspath(path)
        self._read_only = read_only
        super().__init__(pool=pool)

    def _connect(self) -> sqlite3.Connection:
        if self._read_only:
            uri = f"file:{self._path}?mode=ro&immutable=1"
            return sqlite3.connect(uri, uri=True, check_same_thread=False)
        return sqlite3.connect(self._path, check_same_thread=False)

    def _tables(self, con: sqlite3.Connection) -> list[str]:
        return [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view') "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()]

    def schema(self) -> Schema:
        with self._pool.borrow() as con:
            out: Schema = {}
            for tbl in self._tables(con):
                esc = tbl.replace("'", "''")
                cols = [r[1] for r in con.execute(f"PRAGMA table_info('{esc}')").fetchall()]
                out[f"main.{tbl}"] = cols
        return out

    def docs(self) -> list[DocsTable]:
        with self._pool.borrow() as con:
            tables: list[DocsTable] = []
            for tbl in self._tables(con):
                esc = tbl.replace("'", "''")
                cols = [{"name": r[1], "type": (r[2] or "").upper() or "ANY", "comment": None}
                        for r in con.execute(f"PRAGMA table_info('{esc}')").fetchall()]
                tables.append({"name": f"main.{tbl}", "comment": None, "columns": cols})
        return tables

    def table_columns(self) -> Schema:
        return self.schema()

    def export_parquet(
        self, qualified: str, columns: Sequence[str] | None = None, compression: str = "snappy"
    ) -> bytes:
        import duckdb
        table = qualified.split(".", 1)[-1]  # 'main.events' -> 'events'
        proj = ", ".join(f'"{c}"' for c in columns) if columns else "*"
        codec = parquet_codec(compression)
        con = duckdb.connect()
        try:
            con.execute("INSTALL sqlite; LOAD sqlite")
            escaped = self._path.replace("'", "''")
            con.execute(f"ATTACH '{escaped}' AS s (TYPE sqlite)")
            with tempfile.TemporaryDirectory() as d:
                out = os.path.join(d, "export.parquet")
                con.execute(
                    f"COPY (SELECT {proj} FROM s.{quote_ident(table)}) TO '{out}' "
                    f"(FORMAT parquet, COMPRESSION {codec})")
                with open(out, "rb") as f:
                    return f.read()
        finally:
            con.close()
