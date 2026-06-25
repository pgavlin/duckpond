"""The DuckDB backend: a read-only DuckDB connection behind a lock.

DuckDB connections are not safe for concurrent use, so requests serialize on
`_lock`. Local queries are sub-millisecond, so one connection is plenty.
"""

import os
import tempfile
import threading
from collections.abc import Mapping, Sequence

import duckdb

from .base import Backend, DocsTable, Row, Schema, bind, jsonable_row, parquet_codec, quote_ident


class DuckDBBackend(Backend):
    dialect = "duckdb"
    paramstyle = "duckdb"
    bundleable = True

    def __init__(self, path: str, read_only: bool = True):
        self._con = duckdb.connect(path, read_only=read_only)
        self._lock = threading.Lock()

    def run(self, sql: str, args: Mapping[str, object]) -> tuple[list[str], list[Row]]:
        q, p = bind(sql, args, self.dialect, self.paramstyle)
        with self._lock:
            cur = self._con.execute(q, p) if p else self._con.execute(q)
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
        return cols, [dict(zip(cols, jsonable_row(r))) for r in rows]

    def query(self, sql: str, limit: int = 2000) -> tuple[list[str], list[Row], bool]:
        with self._lock:
            cur = self._con.execute(sql)
            cols = [d[0] for d in cur.description]
            rows = cur.fetchmany(limit + 1)
        truncated = len(rows) > limit
        rows = rows[:limit]
        return cols, [dict(zip(cols, jsonable_row(r))) for r in rows], truncated

    def export_parquet(
        self, qualified: str, columns: Sequence[str] | None = None, compression: str = "snappy"
    ) -> bytes:
        proj = ", ".join(f'"{c}"' for c in columns) if columns else "*"
        codec = parquet_codec(compression)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "export.parquet")
            with self._lock:
                # ROW_GROUP_SIZE: a large, bounded row group compresses text-heavy
                # tables better than the default ~122k-row groups (and export memory
                # stays reasonable). COMPRESSION: the server bundle passes 'zstd',
                # which roughly halves the shipped data vs DuckDB's default snappy
                # (379k-row `entries`: 161 MB -> 76 MB) and reads as fast or faster.
                self._con.execute(
                    f"COPY (SELECT {proj} FROM {quote_ident(qualified)}) TO '{path}' "
                    f"(FORMAT parquet, ROW_GROUP_SIZE 1048576, COMPRESSION {codec})")
            with open(path, "rb") as f:
                return f.read()

    def docs(self) -> list[DocsTable]:
        with self._lock:
            rows = self._con.execute(
                "SELECT c.schema_name, c.table_name, c.column_name, c.data_type, "
                "       c.comment AS col_comment, t.comment AS tbl_comment "
                "FROM duckdb_columns() c "
                "LEFT JOIN duckdb_tables() t USING (database_name, schema_name, table_name) "
                "WHERE NOT c.internal "
                "  AND c.schema_name NOT IN ('information_schema', 'pg_catalog') "
                "  AND c.table_name NOT LIKE '\\_dlt%' ESCAPE '\\' "
                "  AND c.column_name NOT LIKE '\\_dlt%' ESCAPE '\\' "
                "ORDER BY c.schema_name, c.table_name, c.column_index").fetchall()
        tables: dict[str, DocsTable] = {}
        for sch, tbl, col, dtype, ccom, tcom in rows:
            q = f"{sch}.{tbl}"
            t = tables.setdefault(q, {"name": q, "comment": tcom, "columns": []})
            t["columns"].append({"name": col, "type": dtype, "comment": ccom})
        return list(tables.values())

    def schema(self) -> Schema:
        with self._lock:
            rows = self._con.execute(
                "SELECT table_schema, table_name, column_name "
                "FROM information_schema.columns "
                "WHERE table_schema NOT IN ('information_schema', 'pg_catalog') "
                "  AND table_name NOT LIKE '\\_dlt%' ESCAPE '\\' "
                "  AND column_name NOT LIKE '\\_dlt%' ESCAPE '\\' "
                "ORDER BY table_schema, table_name, ordinal_position").fetchall()
        out: Schema = {}
        for sch, tbl, col in rows:
            out.setdefault(f"{sch}.{tbl}", []).append(col)
        return out

    def table_columns(self) -> Schema:
        return self.schema()

    def close(self) -> None:
        self._con.close()
