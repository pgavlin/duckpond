"""Postgres backend. The translation + row-coercion path is unit-tested with a
fake DBAPI connection so it runs without a server. A live round-trip is behind
@pytest.mark.integration."""

import os

import pytest

from duckbill.backends.postgres import PostgresBackend


class _FakeCursor:
    def __init__(self, sink):
        self._sink = sink
        self.description = None
        self._rows = []

    def execute(self, sql, params=None):
        self._sink["sql"] = sql
        self._sink["params"] = params
        self.description = [("total",)]
        self._rows = [(30,)]

    def fetchall(self):
        return self._rows

    def fetchmany(self, n):
        return self._rows[:n]


class _FakeConn:
    def __init__(self, sink):
        self._sink = sink

    def cursor(self):
        return _FakeCursor(self._sink)

    def close(self):
        pass


def test_run_translates_to_pyformat(monkeypatch):
    sink = {}
    be = PostgresBackend.__new__(PostgresBackend)  # bypass real connect
    be.dialect, be.paramstyle = "postgres", "pyformat"
    from duckbill.backends.base import Pool
    be._pool = Pool(lambda: _FakeConn(sink), size=1)
    cols, rows = be.run("SELECT sum(n) AS total FROM t WHERE kind=$kind",
                        {"kind": "a", "z": 9})
    assert sink["sql"] == "SELECT sum(n) AS total FROM t WHERE kind=%(kind)s"
    assert sink["params"] == {"kind": "a"}  # only referenced
    assert rows == [{"total": 30}]


def test_catalog_sql_shapes():
    from duckbill.backends import postgres
    assert "information_schema.columns" in postgres.SCHEMA_SQL
    assert "col_description" in postgres.DOCS_SQL and "obj_description" in postgres.DOCS_SQL


@pytest.mark.integration
def test_live_roundtrip():
    dsn = os.environ.get("DUCKBILL_PG_DSN")
    if not dsn:
        pytest.skip("set DUCKBILL_PG_DSN to run the live Postgres test")
    be = PostgresBackend(dsn)
    cols, rows = be.run("SELECT 1 AS one", {})
    assert rows == [{"one": 1}]
