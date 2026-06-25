"""MySQL backend. Translation path unit-tested with a fake connection; live test
behind @pytest.mark.integration."""

import os

import pytest

from duckbill.backends.mysql import MySQLBackend


class _FakeCursor:
    def __init__(self, sink):
        self._sink = sink
        self.description = [("one",)]

    def execute(self, sql, params=None):
        self._sink["sql"] = sql
        self._sink["params"] = params

    def fetchall(self):
        return [(1,)]

    def fetchmany(self, n):
        return [(1,)][:n]

    def close(self):
        pass


class _FakeConn:
    def __init__(self, sink):
        self._sink = sink

    def cursor(self):
        return _FakeCursor(self._sink)

    def close(self):
        pass


def test_run_translates_and_filters():
    from duckbill.backends.base import Pool
    sink = {}
    be = MySQLBackend.__new__(MySQLBackend)
    be.dialect, be.paramstyle = "mysql", "pyformat"
    be._pool = Pool(lambda: _FakeConn(sink), size=1)
    be.run("SELECT 1 AS one FROM t WHERE k=$k # $ignored\nAND j=$k", {"k": 5, "z": 1})
    assert "%(k)s" in sink["sql"] and "$ignored" in sink["sql"]  # comment text untouched, not bound
    assert sink["params"] == {"k": 5}


def test_catalog_sql_shapes():
    from duckbill.backends import mysql
    assert "COLUMN_COMMENT" in mysql.DOCS_SQL and "TABLE_COMMENT" in mysql.DOCS_SQL
    assert "information_schema.columns" in mysql.SCHEMA_SQL.lower()


@pytest.mark.integration
def test_live_roundtrip():
    dsn = os.environ.get("DUCKBILL_MYSQL_DSN")
    if not dsn:
        pytest.skip("set DUCKBILL_MYSQL_DSN to run the live MySQL test")
    be = MySQLBackend(dsn)
    cols, rows = be.run("SELECT 1 AS one", {})
    assert rows == [{"one": 1}]
