"""Tests for duckbill's data layer and a live end-to-end server check.

The data layer (params_in, window_delta, Dashboard.coerce, Warehouse.run) is pure
and tested directly. The server is exercised through one real request cycle.
"""

import json
import os
import tempfile
import threading
import urllib.request
from datetime import timedelta
from http.server import ThreadingHTTPServer

import duckdb
import pytest

from duckbill.core import Dashboard, Warehouse, params_in, window_delta
from duckbill.loader import DashboardError, load_dashboard
from duckbill.questions import QuestionStore, slugify
from duckbill.server import make_handler


@pytest.fixture
def db_path(tmp_path):
    """A tiny warehouse: hourly events with an epoch `ts` and a `kind`."""
    path = str(tmp_path / "w.duckdb")
    con = duckdb.connect(path)
    con.execute("CREATE SCHEMA warehouse")
    con.execute("""
        CREATE TABLE warehouse.events AS
        SELECT * FROM (VALUES
            (1700000000, 'a', 10),
            (1700003600, 'a', 20),
            (1700007200, 'b', 5)
        ) AS t(ts, kind, n)
    """)
    con.execute("COMMENT ON TABLE warehouse.events IS 'hourly events'")
    con.execute("COMMENT ON COLUMN warehouse.events.ts IS 'epoch seconds'")
    con.close()
    return path


def test_params_in():
    assert params_in("SELECT * WHERE ts >= $start AND kind = $kind") == {"start", "kind"}
    assert params_in("SELECT 1") == set()


def test_window_delta():
    assert window_delta("24h") == timedelta(hours=24)
    assert window_delta("7d") == timedelta(days=7)
    with pytest.raises(ValueError):
        window_delta("oops")


def test_dashboard_validation():
    with pytest.raises(ValueError):
        Dashboard([{"id": "x", "title": "x", "type": "nope", "sql": "SELECT 1"}])
    with pytest.raises(ValueError):  # duplicate id
        Dashboard([
            {"id": "x", "title": "x", "type": "bar", "sql": "SELECT 1"},
            {"id": "x", "title": "y", "type": "bar", "sql": "SELECT 1"},
        ])
    with pytest.raises(ValueError):  # spec type without a spec
        Dashboard([{"id": "x", "title": "x", "type": "spec", "sql": "SELECT 1"}])
    for bad in (0, -1, 1.5, "wide", True):  # span must be a positive int or 'full'
        with pytest.raises(ValueError):
            Dashboard([{"id": "x", "title": "x", "type": "bar", "sql": "SELECT 1", "span": bad}])


def test_metric_type_validates():
    d = Dashboard([{"id": "m", "title": "M", "type": "metric",
                    "sql": 'SELECT count(*) AS "n" FROM warehouse.events'}])
    assert d.chart_meta()[0]["type"] == "metric"


def test_leaderboard_and_spark():
    d = Dashboard([
        {"id": "lb", "title": "LB", "type": "leaderboard",
         "drill": {"param": "k", "field": "kind"}, "sql": "SELECT kind, n FROM warehouse.events"},
        {"id": "m", "title": "M", "type": "metric", "spark": "SELECT ts, n FROM warehouse.events",
         "sql": "SELECT sum(n) AS n FROM warehouse.events"},
    ])
    meta = {c["id"]: c for c in d.chart_meta()}
    assert meta["lb"]["type"] == "leaderboard"
    assert meta["m"]["spark"] is True and meta["lb"]["spark"] is False  # a flag; the SQL stays server-side
    with pytest.raises(ValueError):  # spark must be a SQL string
        Dashboard([{"id": "m", "title": "M", "type": "metric", "spark": 1, "sql": "SELECT 1"}])


def test_metric_good_direction():
    good = {"n": "down", "x": "neutral"}
    d = Dashboard([{"id": "m", "title": "M", "type": "metric", "good": good,
                    "sql": 'SELECT 1 AS "n", 2 AS "x"'}])
    assert d.chart_meta()[0]["good"] == good
    Dashboard([{"id": "m", "title": "M", "type": "metric", "good": "down",
                "sql": "SELECT 1"}])  # string form is fine
    for bad in ("higher", {"n": "less"}, "true"):
        with pytest.raises(ValueError):
            Dashboard([{"id": "m", "title": "M", "type": "metric", "good": bad,
                        "sql": "SELECT 1"}])


def test_span_passthrough():
    d = Dashboard([
        {"id": "a", "title": "A", "type": "bar", "sql": "SELECT 1", "span": "full"},
        {"id": "b", "title": "B", "type": "bar", "sql": "SELECT 1", "span": 2},
        {"id": "c", "title": "C", "type": "bar", "sql": "SELECT 1"},
    ])
    meta = {m["id"]: m["span"] for m in d.chart_meta()}
    assert meta == {"a": "full", "b": 2, "c": None}


def test_timespan_defaults_bind_start_end():
    d = Dashboard([{"id": "c", "title": "c", "type": "line", "sql": "SELECT $start, $end"}],
                  params=[{"name": "w", "control": "timespan", "default": "24h"}])
    defaults = d.defaults()
    assert "start" in defaults and "end" in defaults
    assert "w" not in defaults  # the timespan control doesn't bind its own name


def test_coerce_types_and_overrides():
    d = Dashboard([{"id": "c", "title": "c", "type": "bar", "sql": "SELECT $hours"}],
                  params=[{"name": "hours", "type": "int", "default": 744},
                          {"name": "kind", "default": "all"}])
    args = d.coerce({"hours": ["6"], "chart": ["c"]})
    assert args["hours"] == 6 and isinstance(args["hours"], int)
    assert args["kind"] == "all"  # default preserved


def test_warehouse_binds_only_referenced(db_path):
    wh = Warehouse(db_path)
    cols, rows = wh.run(
        "SELECT sum(n) AS total FROM warehouse.events WHERE kind = $kind",
        {"kind": "a", "unused": "x"})
    assert rows == [{"total": 30}]


def test_warehouse_query_and_cap(db_path):
    wh = Warehouse(db_path)
    cols, rows, truncated = wh.query("SELECT kind, n FROM warehouse.events ORDER BY n")
    assert cols == ["kind", "n"] and len(rows) == 3 and not truncated
    cols, rows, truncated = wh.query("SELECT * FROM warehouse.events", limit=2)
    assert len(rows) == 2 and truncated


def test_warehouse_schema(db_path):
    sch = Warehouse(db_path).schema()
    assert "warehouse.events" in sch
    assert "ts" in sch["warehouse.events"]


def test_warehouse_docs(db_path):
    tables = Warehouse(db_path).docs()
    events = next(t for t in tables if t["name"] == "warehouse.events")
    assert events["comment"] == "hourly events"
    ts = next(c for c in events["columns"] if c["name"] == "ts")
    assert ts["type"] == "INTEGER" and ts["comment"] == "epoch seconds"
    kind = next(c for c in events["columns"] if c["name"] == "kind")
    assert kind["comment"] is None  # uncommented columns still appear
    # system catalog views (duckdb_columns, etc.) must not leak in
    assert all(not t["name"].endswith(".duckdb_columns") for t in tables)


def test_to_markdown():
    from duckbill.docs import to_markdown
    md = to_markdown("My WH", "Some _readme_.", [
        {"name": "warehouse.events", "comment": "hourly events",
         "columns": [{"name": "ts", "type": "BIGINT", "comment": "epoch | seconds"},
                     {"name": "n", "type": "INTEGER", "comment": None}]},
    ])
    assert md.startswith("# My WH\n")
    assert "Some _readme_." in md
    assert "### `warehouse.events`" in md
    assert "hourly events" in md
    assert "| `ts` | BIGINT | epoch \\| seconds |" in md   # pipe escaped
    assert "| `n` | INTEGER |  |" in md


def test_load_dashboard_reads_readme(tmp_path):
    mod = tmp_path / "dash.py"
    mod.write_text(
        'readme = "the docs"\n'
        'charts = [{"id": "c", "title": "C", "type": "bar", "sql": "SELECT 1"}]\n')
    assert load_dashboard(str(mod)).readme == "the docs"


def test_export_parquet(db_path):
    data = Warehouse(db_path).export_parquet("warehouse.events")
    assert data[:4] == b"PAR1" and data[-4:] == b"PAR1"  # parquet magic


def test_build_bundle(db_path, tmp_path):
    import py_compile
    from duckbill import bundle
    mod = tmp_path / "dash.py"
    mod.write_text('readme="bundled docs"\n'
                   'charts=[{"id":"c","title":"C","type":"bar",'
                   ' "sql":"SELECT kind, n FROM warehouse.events"}]\n')
    out = tmp_path / "out.py"
    bundle.build_server(str(mod), db_path, str(out), questions_dir=str(tmp_path / "q"))
    # One self-contained script, no sibling files.
    assert not (tmp_path / "out_data").exists()
    py_compile.compile(str(out), doraise=True)
    script = out.read_text()
    assert "B85DATA" in script                       # data embedded as b85
    assert "events" in script                        # the referenced table
    assert "bundled docs" in script                  # readme embedded for the About view


def test_question_store(tmp_path):
    store = QuestionStore(str(tmp_path / "questions"))
    assert store.list() == []
    rec = store.save("Slow by user!", "SELECT 1", {"type": "bar", "x": "u"})
    assert rec["slug"] == "slow-by-user"
    got = store.list()
    assert len(got) == 1 and got[0]["name"] == "Slow by user!" and got[0]["sql"] == "SELECT 1"
    store.save("Slow by user!", "SELECT 2", {})  # same name upserts
    assert len(store.list()) == 1 and store.list()[0]["sql"] == "SELECT 2"
    store.delete("slow-by-user")
    assert store.list() == []


def test_slugify_no_traversal():
    assert slugify("../../etc/passwd") == "etc-passwd"
    assert slugify("") == "untitled"


def test_load_dashboard_missing(tmp_path):
    with pytest.raises(DashboardError):
        load_dashboard(str(tmp_path / "nope.py"))


def test_load_dashboard_roundtrip(tmp_path):
    mod = tmp_path / "dash.py"
    mod.write_text(
        'title = "t"\n'
        'params = [{"name": "kind", "default": "a"}]\n'
        'charts = [{"id": "c", "title": "C", "type": "bar",'
        ' "sql": "SELECT n FROM warehouse.events WHERE kind = $kind"}]\n')
    d = load_dashboard(str(mod))
    assert d.title == "t" and len(d.charts) == 1


def test_markers_validation_and_meta(db_path):
    d = Dashboard(
        [{"id": "c", "title": "C", "type": "line", "sql": "SELECT to_timestamp(ts) AS t, n FROM warehouse.events",
          "markers": True}],
        markers=[{"id": "ev", "field": "t", "color": "#999",
                  "sql": "SELECT to_timestamp(ts) AS t FROM warehouse.events WHERE kind = 'a'"}])
    assert d.chart_meta()[0]["markers"] is True
    assert d.marker_meta() == [{"id": "ev", "field": "t", "label": None, "color": "#999"}]
    rows = d.marker_rows(Warehouse(db_path), {})
    assert len(rows["ev"]) == 2  # two 'a' events

    with pytest.raises(ValueError):  # marker missing 'field'
        Dashboard([{"id": "c", "title": "C", "type": "line", "sql": "SELECT 1"}],
                  markers=[{"id": "ev", "sql": "SELECT 1"}])


def _free_port():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_server_end_to_end(db_path, tmp_path):
    mod = tmp_path / "dash.py"
    mod.write_text(
        'title = "live"\n'
        'readme = "live warehouse docs"\n'
        'params = [{"name": "kind", "control": "select", "default": "a",'
        ' "choices_sql": "SELECT DISTINCT kind FROM warehouse.events ORDER BY 1"}]\n'
        'charts = [{"id": "byhour", "title": "By hour", "type": "bar",'
        ' "spark": "SELECT kind, count(*) AS c FROM warehouse.events GROUP BY 1",'
        ' "sql": "SELECT to_timestamp(ts) AS hour, n FROM warehouse.events'
        ' WHERE kind = $kind ORDER BY 1"}]\n')
    d = load_dashboard(str(mod))
    wh = Warehouse(db_path)
    port = _free_port()
    srv = ThreadingHTTPServer(("127.0.0.1", port),
                              make_handler(d, wh, QuestionStore(str(tmp_path / "questions"))))
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        base = f"http://127.0.0.1:{port}"
        meta = json.load(urllib.request.urlopen(base + "/meta"))
        assert meta["title"] == "live"
        assert meta["params"][0]["choices"] == ["a", "b"]
        assert meta["charts"][0]["params"] == ["kind"]

        q = json.load(urllib.request.urlopen(base + "/q?chart=byhour&kind=a"))
        assert len(q["rows"]) == 2  # two 'a' events

        sp = json.load(urllib.request.urlopen(base + "/q?chart=byhour&spark=1&kind=a"))
        assert {r["kind"] for r in sp["rows"]} == {"a", "b"}  # spark runs the companion query, not the main SQL

        sql = json.load(urllib.request.urlopen(base + "/sql?chart=byhour"))  # for "open in Ask"
        assert "$kind" in sql["sql"]  # the raw SQL with its params intact

        docs = json.load(urllib.request.urlopen(base + "/docs"))
        assert docs["readme"] == "live warehouse docs"
        assert any(t["name"] == "warehouse.events" for t in docs["tables"])

        page = urllib.request.urlopen(base + "/").read()
        assert b"duckbill" in page
    finally:
        srv.shutdown()
