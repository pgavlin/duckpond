"""Snowflake backend. No container -- DSN parsing and the run/docs SQL shape are
unit-tested with a recorded cursor; a live test sits behind @pytest.mark.integration."""

import os

import pytest

from duckbill.backends.snowflake import SnowflakeBackend, _connect_kwargs


def test_dsn_parse():
    kw = _connect_kwargs("snowflake://alice:secret@acme-prod/ANALYTICS/PUBLIC"
                         "?warehouse=WH_RO&role=READER")
    assert kw["user"] == "alice" and kw["password"] == "secret"
    assert kw["account"] == "acme-prod"
    assert kw["database"] == "ANALYTICS" and kw["schema"] == "PUBLIC"
    assert kw["warehouse"] == "WH_RO" and kw["role"] == "READER"


class _RecCursor:
    def __init__(self, sink):
        self._sink = sink
        self.description = [("total",)]

    def execute(self, sql, params=None):
        self._sink.append((sql, params))

    def fetchall(self):
        return [(7,)]

    def fetchmany(self, n):
        return [(7,)][:n]


class _RecConn:
    def __init__(self, sink):
        self._sink = sink

    def cursor(self):
        return _RecCursor(self._sink)

    def close(self):
        pass


def test_run_translates():
    from duckbill.backends.base import Pool
    sink = []
    be = SnowflakeBackend.__new__(SnowflakeBackend)
    be.dialect, be.paramstyle = "snowflake", "pyformat"
    be._pool = Pool(lambda: _RecConn(sink), size=1)
    cols, rows = be.run("SELECT sum(n) AS total FROM t WHERE k=$k", {"k": "a"})
    assert sink[-1][0] == "SELECT sum(n) AS total FROM t WHERE k=%(k)s"
    assert sink[-1][1] == {"k": "a"} and rows == [{"total": 7}]


def test_catalog_sql_shapes():
    from duckbill.backends import snowflake
    assert "information_schema.columns" in snowflake.SCHEMA_SQL.lower()
    assert "comment" in snowflake.DOCS_SQL.lower()


@pytest.mark.integration
def test_live_roundtrip():
    dsn = os.environ.get("DUCKBILL_SNOWFLAKE_DSN")
    if not dsn:
        pytest.skip("set DUCKBILL_SNOWFLAKE_DSN to run the live Snowflake test")
    be = SnowflakeBackend(dsn)
    cols, rows = be.run("SELECT 1 AS one", {})
    assert rows == [{"one": 1}]
