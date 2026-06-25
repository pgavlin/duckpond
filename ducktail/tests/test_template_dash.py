import importlib.util

import duckdb

from conftest import run_script


def _load_dash(path):
    spec = importlib.util.spec_from_file_location("dash", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_dash_charts_execute_against_store(investigation):
    # build the store, then run every chart's SQL against it -- a broken chart (bad SQL,
    # wrong column, wrong table) fails here. $start/$end are bound the way duckbill binds
    # the timespan control (ISO strings cast to TIMESTAMPTZ in the chart SQL).
    assert run_script("refresh.py", investigation, {"NOW_HOURS": "6"}).returncode == 0
    mod = _load_dash(investigation / "dash.py")
    assert isinstance(mod.charts, list) and mod.charts, "dash.py must define a non-empty charts list"

    con = duckdb.connect(str(investigation / "ducktail.duckdb"), read_only=True)
    try:
        params = {"start": "1970-01-01T00:00:00+00:00", "end": "2100-01-01T00:00:00+00:00"}
        for c in mod.charts:
            for key in ("id", "title", "type", "sql"):
                assert key in c, f"chart {c.get('id', c)!r} missing {key!r}"
            con.execute(c["sql"], params).fetchall()
    finally:
        con.close()
