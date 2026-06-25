"""Postgres backend (psycopg, an optional extra). Read-only session; catalog and
comments from information_schema plus obj_description/col_description. Serve-only.
"""

from contextlib import closing

from .base import DBAPIBackend, DBAPIConnection, DocsTable, Schema

# dlt bookkeeping tables/columns (_dlt_loads, _dlt_id, ...) are hidden, matching DuckDB.
SCHEMA_SQL = """
SELECT table_schema, table_name, column_name
FROM information_schema.columns
WHERE table_schema NOT IN ('information_schema', 'pg_catalog')
  AND table_name NOT LIKE '\\_dlt%' ESCAPE '\\'
  AND column_name NOT LIKE '\\_dlt%' ESCAPE '\\'
ORDER BY table_schema, table_name, ordinal_position
"""

DOCS_SQL = """
SELECT c.table_schema, c.table_name, c.column_name, c.data_type,
       col_description(format('%I.%I', c.table_schema, c.table_name)::regclass, c.ordinal_position) AS col_comment,
       obj_description(format('%I.%I', c.table_schema, c.table_name)::regclass) AS tbl_comment
FROM information_schema.columns c
WHERE c.table_schema NOT IN ('information_schema', 'pg_catalog')
  AND c.table_name NOT LIKE '\\_dlt%' ESCAPE '\\'
  AND c.column_name NOT LIKE '\\_dlt%' ESCAPE '\\'
ORDER BY c.table_schema, c.table_name, c.ordinal_position
"""


class PostgresBackend(DBAPIBackend[DBAPIConnection]):
    dialect = "postgres"
    paramstyle = "pyformat"
    bundleable = False

    def __init__(self, dsn: str, read_only: bool = True, pool: int = 4):
        self._dsn = dsn
        self._read_only = read_only
        super().__init__(pool=pool)

    def _connect(self) -> DBAPIConnection:
        import psycopg
        con = psycopg.connect(self._dsn, autocommit=True)
        if self._read_only:
            con.execute("SET default_transaction_read_only = on")
        # psycopg's Connection is untyped here (no stubs installed); it satisfies
        # the DBAPIConnection surface we use.
        return con  # type: ignore[no-any-return]

    def schema(self) -> Schema:
        with self._pool.borrow() as con, closing(con.cursor()) as cur:
            cur.execute(SCHEMA_SQL)
            rows = cur.fetchall()
        out: Schema = {}
        for sch, tbl, col in rows:
            out.setdefault(f"{sch}.{tbl}", []).append(col)
        return out

    def docs(self) -> list[DocsTable]:
        with self._pool.borrow() as con, closing(con.cursor()) as cur:
            cur.execute(DOCS_SQL)
            rows = cur.fetchall()
        tables: dict[str, DocsTable] = {}
        for sch, tbl, col, dtype, ccom, tcom in rows:
            q = f"{sch}.{tbl}"
            t = tables.setdefault(q, {"name": q, "comment": tcom, "columns": []})
            t["columns"].append({"name": col, "type": dtype, "comment": ccom})
        return list(tables.values())
