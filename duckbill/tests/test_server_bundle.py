"""Tests for the single-file server bundle: a self-contained `uv run` script with
the pruned Parquet embedded as b85.

Structural tests (the script compiles, embeds the right things; the embedded data
holds one Parquet per referenced table, projected to its columns) always run. The
serve integration test (`uv run <out>.py` -> GET /meta, /q) is skipped when uv is
unavailable or a loopback port can't bind.
"""

import http.client
import json
import os
import py_compile
import shutil
import socket
import subprocess
import time

import duckdb
import pytest

from duckbill import bundle
from duckbill.server_bundle import build_server


@pytest.fixture
def warehouse_path(tmp_path):
    """A warehouse with main referenced + unreferenced tables, in a `warehouse` schema."""
    path = str(tmp_path / "w.duckdb")
    con = duckdb.connect(path)
    con.execute("CREATE SCHEMA warehouse")
    con.execute("CREATE TABLE warehouse.sales (id INT, amount INT, region TEXT)")
    con.execute("INSERT INTO warehouse.sales VALUES (1, 100, 'north'), (2, 200, 'south'), (3, 50, 'north')")
    con.execute("CREATE TABLE warehouse.raw_events (ts BIGINT, payload TEXT)")
    con.execute("INSERT INTO warehouse.raw_events VALUES (1700000000, 'e1'), (1700003600, 'e2')")
    con.execute("CREATE TABLE warehouse.unused (x INT)")
    con.close()
    return path


@pytest.fixture
def dashboard_path(tmp_path):
    """A dashboard with two charts: one over warehouse.sales (two of three columns,
    bound via $region), and one over warehouse.raw_events that materializes a
    TIMESTAMPTZ via `to_timestamp(ts)` -- the column type whose Python conversion
    needs pytz, which the bundle must declare. warehouse.unused is never queried."""
    mod = tmp_path / "dash.py"
    mod.write_text(
        'title = "Sales"\n'
        'params = [{"name": "region", "default": "north"}]\n'
        'charts = [\n'
        '  {"id": "by_region", "title": "Sales by Region", "type": "bar",'
        ' "sql": "SELECT region, sum(amount) AS total FROM warehouse.sales'
        " WHERE region = $region GROUP BY region\"},\n"
        '  {"id": "events_over_time", "title": "Events", "type": "line",'
        ' "sql": "SELECT to_timestamp(ts) AS hour, payload FROM warehouse.raw_events'
        " ORDER BY hour\"},\n"
        ']\n'
    )
    return str(mod)


def _embedded_b85data(script_text):
    """Pull the B85DATA mapping back out of a generated script."""
    import ast
    import base64
    import json
    import re
    m = re.search(r"B85DATA = json\.loads\((.*)\)\n", script_text)
    assert m, "no B85DATA in generated script"
    blob = json.loads(ast.literal_eval(m.group(1)))
    return {name: base64.b85decode(b) for name, b in blob.items()}


def test_build_server_prunes_and_projects(tmp_path, warehouse_path, dashboard_path):
    out = str(tmp_path / "out.py")
    build_server(dashboard_path, warehouse_path, out)

    # Self-contained: no sibling data directory.
    assert not (tmp_path / "out_data").exists()

    data = _embedded_b85data(open(out).read())
    # Only referenced tables are embedded.
    assert "sales" in data
    assert "raw_events" in data
    assert "unused" not in data

    con = duckdb.connect()
    try:
        sales = str(tmp_path / "sales.parquet")
        with open(sales, "wb") as f:
            f.write(data["sales"])
        cols = {r[0] for r in con.execute(
            f"DESCRIBE SELECT * FROM read_parquet('{sales}')").fetchall()}
        # The SQL references region and amount; id is never referenced.
        assert cols == {"region", "amount"}, cols

        n = con.execute(f"SELECT count(*) FROM read_parquet('{sales}')").fetchone()[0]
        assert n == 3
    finally:
        con.close()


def test_generated_script_structure(tmp_path, warehouse_path, dashboard_path):
    out = str(tmp_path / "out.py")
    build_server(dashboard_path, warehouse_path, out)

    assert os.path.exists(out)
    # Compiles cleanly.
    py_compile.compile(out, doraise=True)

    text = open(out).read()
    # PEP 723 header with the runtime deps. pytz is required: DuckDB's Python
    # client imports it to convert a TIMESTAMPTZ result to a datetime, and it is
    # not a dependency of the duckdb wheel.
    assert "# /// script" in text
    assert "duckdb>=1.0" in text
    assert "sqlglot>=20" in text
    assert "pytz" in text
    # The chart SQL is embedded (the standalone server runs it).
    assert "SELECT region, sum(amount) AS total FROM warehouse.sales" in text
    # The data is embedded (b85) and extracted to a content-keyed temp dir, then
    # exposed as views over the Parquet.
    assert "B85DATA" in text
    assert "DATA_KEY" in text
    assert "b85decode" in text
    assert "tempfile.gettempdir()" in text
    assert "read_parquet" in text
    assert "CREATE VIEW" in text


def test_build_server_exposed_from_bundle(tmp_path, warehouse_path, dashboard_path):
    # Task 4 imports it from bundle; keep that surface working.
    assert bundle.build_server is build_server


def _free_port():
    try:
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        return port
    except OSError:
        return None


def _get(port, path):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request("GET", path)
        r = conn.getresponse()
        return r.status, r.read()
    finally:
        conn.close()


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv not available")
def test_serve_integration(tmp_path, warehouse_path, dashboard_path):
    port = _free_port()
    if port is None:
        pytest.skip("could not bind a loopback port")

    out = str(tmp_path / "out.py")
    build_server(dashboard_path, warehouse_path, out)

    # Pin the port + suppress the browser open so the subprocess is headless.
    text = open(out).read().replace("PORT = 8799", f"PORT = {port}")
    text = text.replace("webbrowser.open(url)", "None")
    open(out, "w").write(text)

    proc = subprocess.Popen(
        ["uv", "run", out],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        # Poll the port: uv may resolve+download deps on first run.
        deadline = time.time() + 90
        ready = False
        while time.time() < deadline:
            if proc.poll() is not None:
                out_text = proc.stdout.read() if proc.stdout else ""
                pytest.fail(f"server exited early ({proc.returncode}):\n{out_text}")
            try:
                status, _ = _get(port, "/meta")
                if status == 200:
                    ready = True
                    break
            except (ConnectionRefusedError, OSError):
                time.sleep(0.25)
        if not ready:
            pytest.skip("server did not come up within the timeout (uv resolve slow?)")

        # /meta carries the title, params, and the chart (without SQL).
        status, body = _get(port, "/meta")
        assert status == 200
        meta = json.loads(body)
        assert meta["title"] == "Sales"
        cids = {c["id"] for c in meta["charts"]}
        assert "by_region" in cids
        assert all("sql" not in c for c in meta["charts"]), "SQL must not leak through /meta"

        # /q runs the chart SQL server-side; rows match a direct DuckDB query.
        status, body = _get(port, "/q?chart=by_region&region=north")
        assert status == 200
        got = json.loads(body)["rows"]

        con = duckdb.connect(warehouse_path, read_only=True)
        try:
            cur = con.execute(
                "SELECT region, sum(amount) AS total FROM warehouse.sales "
                "WHERE region = 'north' GROUP BY region")
            cols = [d[0] for d in cur.description]
            expected = [dict(zip(cols, r)) for r in cur.fetchall()]
        finally:
            con.close()
        assert got == expected, (got, expected)

        # The TIMESTAMPTZ-materializing chart succeeds under the isolated uv env.
        # Without pytz declared this 500s with "No module named 'pytz'".
        status, body = _get(port, "/q?chart=events_over_time")
        assert status == 200, body
        payload = json.loads(body)
        assert "error" not in payload, payload
        assert len(payload["rows"]) == 2, payload
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
