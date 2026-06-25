"""Tests for `duckbill bundle` CLI dispatch (single-file server bundle)."""

import os
import py_compile

import duckdb
import pytest

from duckbill.cli import main as cli_main


@pytest.fixture
def warehouse_path(tmp_path):
    path = str(tmp_path / "w.duckdb")
    con = duckdb.connect(path)
    con.execute("CREATE SCHEMA warehouse")
    con.execute("CREATE TABLE warehouse.sales (id INT, amount INT, region TEXT)")
    con.execute("INSERT INTO warehouse.sales VALUES (1, 100, 'north'), (2, 200, 'south')")
    con.close()
    return path


@pytest.fixture
def dashboard_path(tmp_path):
    mod = tmp_path / "dash.py"
    mod.write_text(
        'title = "Test"\n'
        'charts = [{"id": "c1", "title": "Sales", "type": "bar",'
        ' "sql": "SELECT region, sum(amount) AS total FROM warehouse.sales GROUP BY region"}]\n'
    )
    return str(mod)


def test_bundle_writes_single_file(tmp_path, warehouse_path, dashboard_path):
    out_py = str(tmp_path / "out.py")
    cli_main(["bundle", dashboard_path, "--db", warehouse_path, "-o", out_py])
    assert os.path.exists(out_py)
    py_compile.compile(out_py, doraise=True)
    # Self-contained: no sibling data directory or file.
    assert not os.path.exists(str(tmp_path / "out_data"))
    assert "B85DATA" in open(out_py).read()


def test_bundle_appends_py_extension(tmp_path, warehouse_path, dashboard_path):
    """When -o has no .py extension, it is appended."""
    out = str(tmp_path / "out")
    cli_main(["bundle", dashboard_path, "--db", warehouse_path, "-o", out])
    assert os.path.exists(out + ".py")


def test_bundle_normalizes_non_py_extension(tmp_path, warehouse_path, dashboard_path):
    """A non-.py extension is replaced with .py."""
    out = str(tmp_path / "out.html")
    cli_main(["bundle", dashboard_path, "--db", warehouse_path, "-o", out])
    assert os.path.exists(str(tmp_path / "out.py"))
    assert not os.path.exists(out)
