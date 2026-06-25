"""Snowflake backend (snowflake-connector-python, an optional extra). Serve-only.

DSN: snowflake://user:password@account/database/schema?warehouse=WH&role=ROLE
Connect read-only by using a role with only SELECT grants (Snowflake has no
session read-only toggle).
"""

from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from .base import DBAPIBackend, DBAPIConnection, DocsTable, Schema

SCHEMA_SQL = """
SELECT table_schema, table_name, column_name
FROM information_schema.columns
WHERE table_schema NOT IN ('INFORMATION_SCHEMA')
ORDER BY table_schema, table_name, ordinal_position
"""

DOCS_SQL = """
SELECT c.table_schema, c.table_name, c.column_name, c.data_type,
       c.comment AS col_comment, t.comment AS tbl_comment
FROM information_schema.columns c
JOIN information_schema.tables t
  ON c.table_schema = t.table_schema AND c.table_name = t.table_name
WHERE c.table_schema NOT IN ('INFORMATION_SCHEMA')
ORDER BY c.table_schema, c.table_name, c.ordinal_position
"""


def _connect_kwargs(dsn: str) -> dict[str, str]:
    u = urlparse(dsn)
    parts = [p for p in u.path.split("/") if p]
    q = parse_qs(u.query)
    kw = {
        "account": u.hostname or "",
        "user": unquote(u.username or ""),
        "password": unquote(u.password or ""),
    }
    if len(parts) >= 1:
        kw["database"] = parts[0]
    if len(parts) >= 2:
        kw["schema"] = parts[1]
    for opt in ("warehouse", "role"):
        if opt in q:
            kw[opt] = q[opt][0]
    return kw


class SnowflakeBackend(DBAPIBackend[DBAPIConnection]):
    dialect = "snowflake"
    paramstyle = "pyformat"
    bundleable = False

    def __init__(self, dsn: str, read_only: bool = True, pool: int = 4):
        self._kw = _connect_kwargs(dsn)
        super().__init__(pool=pool)

    def _connect(self) -> DBAPIConnection:
        import snowflake.connector
        # snowflake-connector is untyped here (no stubs installed); the returned
        # connection satisfies the DBAPIConnection surface we use.
        con: Any = snowflake.connector.connect(paramstyle="pyformat", **self._kw)
        return con  # type: ignore[no-any-return]

    def schema(self) -> Schema:
        with self._pool.borrow() as con:
            cur = con.cursor()
            cur.execute(SCHEMA_SQL)
            rows = cur.fetchall()
        out: Schema = {}
        for sch, tbl, col in rows:
            out.setdefault(f"{sch}.{tbl}", []).append(col)
        return out

    def docs(self) -> list[DocsTable]:
        with self._pool.borrow() as con:
            cur = con.cursor()
            cur.execute(DOCS_SQL)
            rows = cur.fetchall()
        tables: dict[str, DocsTable] = {}
        for sch, tbl, col, dtype, ccom, tcom in rows:
            q = f"{sch}.{tbl}"
            t = tables.setdefault(q, {"name": q, "comment": tcom, "columns": []})
            t["columns"].append({"name": col, "type": dtype, "comment": ccom})
        return list(tables.values())
