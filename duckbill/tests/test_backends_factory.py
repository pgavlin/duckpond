"""open_backend dispatch, ${VAR} expansion, and the DuckDBBackend behind the seam."""

import duckdb
import pytest

from duckbill.backends import open_backend
from duckbill.backends.base import Backend
from duckbill.backends.duckdb import DuckDBBackend


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "w.duckdb")
    con = duckdb.connect(path)
    con.execute("CREATE SCHEMA warehouse")
    con.execute("CREATE TABLE warehouse.events AS SELECT * FROM (VALUES (1, 'a'), (2, 'b')) t(n, kind)")
    con.close()
    return path


def test_bare_path_is_duckdb(db_path):
    be = open_backend(db_path)
    assert isinstance(be, DuckDBBackend) and isinstance(be, Backend)
    assert be.dialect == "duckdb" and be.bundleable is True


def test_duckdb_scheme(db_path):
    be = open_backend("duckdb:///" + db_path.lstrip("/"))
    cols, rows = be.run("SELECT count(*) AS n FROM warehouse.events", {})
    assert rows == [{"n": 2}]


def test_env_expansion(db_path, monkeypatch):
    monkeypatch.setenv("DBFILE", db_path)  # db_path is absolute, so "duckdb://" + "/abs" -> "duckdb:///abs"
    be = open_backend("duckdb://${DBFILE}")
    cols, rows = be.run("SELECT count(*) AS n FROM warehouse.events", {})
    assert rows == [{"n": 2}]


def test_unknown_scheme_errors():
    with pytest.raises(ValueError):
        open_backend("oracle://host/db")
