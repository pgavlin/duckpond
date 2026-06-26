import re
from duckbill import bundle
from duckbill.page import PAGE


def test_page_prefers_injected_db():
    # The page must select an injected DB if present, else ServerDB.
    assert "window.__duckbillDB__ || ServerDB" in PAGE


def test_table_is_scrollable_and_resizable():
    # Tables render inside a capped, scrollable viewport with a sticky header...
    assert "<div class='table-scroll'>" in PAGE
    assert ".table-scroll { overflow: auto; max-height:" in PAGE
    assert ".table-scroll th { color: #666; font-weight: 600; white-space: nowrap; position: sticky; top: 0;" in PAGE
    # ...and freeze their auto widths into a fixed layout so columns can be dragged...
    assert "function freezeColumns(table)" in PAGE
    assert 'table.style.tableLayout = "fixed"' in PAGE
    assert 'grip.addEventListener("pointerdown"' in PAGE
    # ...or double-clicked to fit their content.
    assert "function autoFitColumn(table, i, col)" in PAGE
    assert 'grip.addEventListener("dblclick"' in PAGE


def test_pack_helpers_importable():
    # The static bundler reuses these; they must exist with the expected arity.
    # They're defined in server_bundle but imported via bundle to avoid circular imports.
    import duckbill.server_bundle as server_bundle
    assert callable(server_bundle._collect_pruned_parquet)
    assert callable(server_bundle._meta_payload)


import json
import os
import duckdb
from duckbill import static_bundle


def _make_db(path):
    con = duckdb.connect(path)
    con.execute("CREATE SCHEMA warehouse")
    con.execute("CREATE TABLE warehouse.events (ts BIGINT, city VARCHAR, n INTEGER)")
    con.execute("INSERT INTO warehouse.events VALUES (1700000000, 'london', 3), (1700003600, 'london', 5)")
    con.close()


def _make_dash(path):
    with open(path, "w") as f:
        f.write(
            "charts = [\n"
            "  {'id': 'c1', 'title': 'n by city', 'type': 'bar',\n"
            "   'sql': 'SELECT city, sum(n) AS n FROM warehouse.events GROUP BY 1',\n"
            "   'encoding': {'x': {'field': 'city'}, 'y': {'field': 'n'}}},\n"
            "]\n"
        )


def test_build_static_emits_site(tmp_path):
    db = str(tmp_path / "w.duckdb"); _make_db(db)
    dash = str(tmp_path / "dash.py"); _make_dash(dash)
    out = str(tmp_path / "site")
    static_bundle.build_static(dash, db, out)

    assert os.path.isfile(os.path.join(out, "index.html"))
    assert os.path.isfile(os.path.join(out, "data", "events.parquet"))

    html = open(os.path.join(out, "index.html")).read()
    # The injected static data block carries the embedded META with chart SQL inlined.
    assert "window.__duckbillStatic__" in html
    assert "window.__duckbillDB__" in html        # WasmDB sets this
    assert "savable" in html and "false" in html  # write UI disabled
    # Vendor assets were rewritten to absolute CDN URLs -- no root-relative /vendor/ left.
    assert 'src="/vendor/' not in html
    assert "/vendor/esm/" not in html
    # The Parquet fetch path is relative (survives a Pages subpath).
    assert "./data" in html
    # The loading overlay is injected into the body and wired to the boot phases.
    assert 'id="wasm-loading"' in html
    assert html.index('id="wasm-loading"') > html.index("<body>")
    assert "_wlDone()" in html and "_wlErr(" in html


def test_build_static_embeds_chart_sql(tmp_path):
    db = str(tmp_path / "w.duckdb"); _make_db(db)
    dash = str(tmp_path / "dash.py"); _make_dash(dash)
    out = str(tmp_path / "site")
    static_bundle.build_static(dash, db, out)
    html = open(os.path.join(out, "index.html")).read()
    # Extract the JSON META blob and confirm the chart SQL is inlined.
    m = re.search(r"window\.__duckbillStatic__\s*=\s*(\{.*?\});", html, re.S)
    assert m
    payload = json.loads(m.group(1))
    charts = {c["id"]: c for c in payload["meta"]["charts"]}
    assert "sql" in charts["c1"] and "FROM warehouse.events" in charts["c1"]["sql"]
    assert "events" in payload["tables"]
