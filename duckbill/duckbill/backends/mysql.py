"""MySQL/MariaDB backend (PyMySQL, an optional extra). Read-only session; catalog
and comments from information_schema. Serve-only. DSN: mysql://user:pw@host:3306/db
"""

from contextlib import closing
from typing import Any
from urllib.parse import unquote, urlparse

from .base import DBAPIBackend, DBAPIConnection, DocsTable, Schema

SCHEMA_SQL = """
SELECT table_schema, table_name, column_name
FROM information_schema.columns
WHERE table_schema = %(db)s
ORDER BY table_schema, table_name, ordinal_position
"""

DOCS_SQL = """
SELECT c.table_schema, c.table_name, c.column_name, c.column_type,
       NULLIF(c.COLUMN_COMMENT, '') AS col_comment,
       NULLIF(t.TABLE_COMMENT, '') AS tbl_comment
FROM information_schema.columns c
JOIN information_schema.tables t USING (table_schema, table_name)
WHERE c.table_schema = %(db)s
ORDER BY c.table_schema, c.table_name, c.ordinal_position
"""


class MySQLBackend(DBAPIBackend[DBAPIConnection]):
    dialect = "mysql"
    paramstyle = "pyformat"
    bundleable = False

    def __init__(self, dsn: str, read_only: bool = True, pool: int = 4):
        u = urlparse(dsn)
        self._db = u.path.lstrip("/")
        self._kw: dict[str, Any] = dict(
            host=u.hostname or "localhost", port=u.port or 3306,
            user=unquote(u.username or ""), password=unquote(u.password or ""),
            database=self._db)
        self._read_only = read_only
        super().__init__(pool=pool)

    def _connect(self) -> DBAPIConnection:
        import pymysql
        con = pymysql.connect(**self._kw, autocommit=True)
        if self._read_only:
            with con.cursor() as cur:
                cur.execute("SET SESSION TRANSACTION READ ONLY")
        # PyMySQL's Connection is untyped here (no stubs installed); it satisfies
        # the DBAPIConnection surface we use.
        return con  # type: ignore[no-any-return]

    def schema(self) -> Schema:
        with self._pool.borrow() as con, closing(con.cursor()) as cur:
            cur.execute(SCHEMA_SQL, {"db": self._db})
            rows = cur.fetchall()
        out: Schema = {}
        for sch, tbl, col in rows:
            out.setdefault(f"{sch}.{tbl}", []).append(col)
        return out

    def docs(self) -> list[DocsTable]:
        with self._pool.borrow() as con, closing(con.cursor()) as cur:
            cur.execute(DOCS_SQL, {"db": self._db})
            rows = cur.fetchall()
        tables: dict[str, DocsTable] = {}
        for sch, tbl, col, dtype, ccom, tcom in rows:
            q = f"{sch}.{tbl}"
            t = tables.setdefault(q, {"name": q, "comment": tcom, "columns": []})
            t["columns"].append({"name": col, "type": dtype, "comment": ccom})
        return list(tables.values())
