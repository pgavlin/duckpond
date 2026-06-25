"""SQLite backend: run/query/docs/schema and a bundleable export."""

import sqlite3

import pytest

from duckbill.backends import open_backend
from duckbill.backends.sqlite import SQLiteBackend


@pytest.fixture
def sqlite_path(tmp_path):
    path = str(tmp_path / "w.sqlite")
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE events (n INTEGER, kind TEXT)")
    con.executemany("INSERT INTO events VALUES (?, ?)", [(10, "a"), (20, "a"), (5, "b")])
    con.commit()
    con.close()
    return path


def test_open_and_run(sqlite_path):
    be = open_backend("sqlite:///" + sqlite_path.lstrip("/"))
    assert isinstance(be, SQLiteBackend) and be.bundleable is True
    cols, rows = be.run("SELECT sum(n) AS total FROM events WHERE kind=$kind",
                        {"kind": "a", "unused": "x"})
    assert rows == [{"total": 30}]


def test_query_and_cap(sqlite_path):
    be = SQLiteBackend(sqlite_path)
    cols, rows, trunc = be.query("SELECT n, kind FROM events ORDER BY n")
    assert cols == ["n", "kind"] and len(rows) == 3 and not trunc
    cols, rows, trunc = be.query("SELECT * FROM events", limit=2)
    assert len(rows) == 2 and trunc


def test_schema_and_docs(sqlite_path):
    be = SQLiteBackend(sqlite_path)
    sch = be.schema()
    assert "main.events" in sch and "kind" in sch["main.events"]
    tables = be.docs()
    events = next(t for t in tables if t["name"] == "main.events")
    assert events["comment"] is None  # SQLite has no comments
    assert {c["name"] for c in events["columns"]} == {"n", "kind"}


def test_export_parquet(sqlite_path):
    data = SQLiteBackend(sqlite_path).export_parquet("main.events")
    assert data[:4] == b"PAR1" and data[-4:] == b"PAR1"


def test_quoted_table_name(tmp_path):
    path = str(tmp_path / "q.sqlite")
    con = sqlite3.connect(path)
    con.execute('CREATE TABLE "o\'clock" (n INTEGER)')
    con.execute('INSERT INTO "o\'clock" VALUES (1)')
    con.commit()
    con.close()
    be = SQLiteBackend(path)
    assert "main.o'clock" in be.schema()
    assert any(t["name"] == "main.o'clock" for t in be.docs())
