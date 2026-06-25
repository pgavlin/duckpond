"""Tests for the bundler's column pruner and column-projected Parquet export."""

import duckdb
import pytest

from duckbill.backends.duckdb import DuckDBBackend
from duckbill.core import Dashboard
from duckbill.prune import referenced


@pytest.fixture
def warehouse(tmp_path):
    """A warehouse with three tables: t_used(a,b,c), t_unused(x,y), t_star(p,q),
    all under the `warehouse` schema dashboards reference."""
    path = str(tmp_path / "w.duckdb")
    con = duckdb.connect(path)
    con.execute("CREATE SCHEMA warehouse")
    con.execute("CREATE TABLE warehouse.t_used (a INT, b INT, c INT)")
    con.execute("INSERT INTO warehouse.t_used VALUES (1, 2, 3), (4, 5, 6)")
    con.execute("CREATE TABLE warehouse.t_unused (x INT, y INT)")
    con.execute("CREATE TABLE warehouse.t_star (p INT, q INT)")
    con.close()
    wh = DuckDBBackend(path)
    yield wh
    wh.close()


def _dashboard(charts, markers=None, params=None):
    full = [{"id": f"c{i}", "title": "t", "type": "table", **c}
            for i, c in enumerate(charts)]
    return Dashboard(full, params=params or [], markers=markers or [])


def test_referenced_prunes_columns_and_tables(warehouse):
    dash = _dashboard(
        [{"sql": "SELECT a, b FROM warehouse.t_used"},
         {"sql": "SELECT * FROM warehouse.t_star"}],
        markers=[{"id": "m", "field": "a", "sql": "SELECT a FROM warehouse.t_used"}],
        params=[{"name": "p",
                 "choices_sql": "SELECT DISTINCT b FROM warehouse.t_used"}],
    )
    ref = referenced(dash, warehouse)

    assert ref["warehouse.t_used"] == {"a", "b"}   # not c
    assert ref["warehouse.t_star"] is None          # star -> all columns
    assert "warehouse.t_unused" not in ref          # never queried


def test_referenced_keys_match_schema(warehouse):
    dash = _dashboard([{"sql": "SELECT a FROM warehouse.t_used"}])
    ref = referenced(dash, warehouse)
    assert set(ref) <= set(warehouse.schema())


def test_referenced_handles_aliases_and_joins(warehouse):
    dash = _dashboard([
        {"sql": "SELECT u.a, s.p FROM warehouse.t_used u "
                "JOIN warehouse.t_star s ON u.a = s.p"},
    ])
    ref = referenced(dash, warehouse)
    assert ref["warehouse.t_used"] == {"a"}
    assert ref["warehouse.t_star"] == {"p"}


def test_referenced_unparseable_sql_is_skipped(warehouse):
    dash = _dashboard([{"sql": "this is not sql"}])
    assert referenced(dash, warehouse) == {}


def test_referenced_unresolvable_column_is_conservative(warehouse):
    # A column that doesn't exist makes qualify raise; we widen to all columns.
    dash = _dashboard([{"sql": "SELECT nonesuch FROM warehouse.t_used"}])
    ref = referenced(dash, warehouse)
    assert ref["warehouse.t_used"] is None


def test_referenced_cte_alias_is_not_a_warehouse_table(warehouse):
    dash = _dashboard([
        {"sql": "WITH cte AS (SELECT a, b FROM warehouse.t_used) "
                "SELECT a FROM cte"},
    ])
    ref = referenced(dash, warehouse)
    # Only the real warehouse table appears, with the columns the CTE reads.
    assert set(ref) == {"warehouse.t_used"}
    assert ref["warehouse.t_used"] == {"a", "b"}


def test_referenced_prefers_warehouse_over_staging_collision(tmp_path):
    # A leftover `warehouse_staging` schema shares bare table names with the real
    # `warehouse` schema. The dashboard says `warehouse.events`; the pruner must
    # resolve to warehouse.events, never let warehouse_staging.events shadow it.
    path = str(tmp_path / "collide.duckdb")
    con = duckdb.connect(path)
    con.execute("CREATE SCHEMA warehouse")
    con.execute("CREATE SCHEMA warehouse_staging")
    con.execute("CREATE TABLE warehouse.events (a INT, b INT)")
    con.execute("CREATE TABLE warehouse_staging.events (a INT, b INT)")
    con.close()
    wh = DuckDBBackend(path)
    try:
        dash = _dashboard([{"sql": "SELECT a FROM warehouse.events"}])
        ref = referenced(dash, wh)
        assert set(ref) == {"warehouse.events"}
        assert "warehouse_staging.events" not in ref
    finally:
        wh.close()


def test_export_parquet_projects_columns(warehouse, tmp_path):
    data = warehouse.export_parquet("warehouse.t_used", ["a", "b"])
    out = tmp_path / "proj.parquet"
    out.write_bytes(data)
    cols = duckdb.connect().execute(
        f"DESCRIBE SELECT * FROM read_parquet('{out}')").fetchall()
    assert [r[0] for r in cols] == ["a", "b"]


def test_export_parquet_all_columns_by_default(warehouse, tmp_path):
    data = warehouse.export_parquet("warehouse.t_used")
    out = tmp_path / "all.parquet"
    out.write_bytes(data)
    cols = duckdb.connect().execute(
        f"DESCRIBE SELECT * FROM read_parquet('{out}')").fetchall()
    assert [r[0] for r in cols] == ["a", "b", "c"]


def test_export_parquet_rejects_unknown_compression(warehouse):
    # `compression` is interpolated into a COPY statement (it can't be a bind
    # param), so it must be allowlisted -- guard the one injection boundary.
    with pytest.raises(ValueError, match="compression"):
        warehouse.export_parquet("warehouse.t_used", compression="snappy; DROP TABLE x")


def test_parquet_codec_allowlist():
    from duckbill.backends.base import parquet_codec
    assert parquet_codec("ZSTD") == "zstd"   # normalized
    with pytest.raises(ValueError):
        parquet_codec("lz4")                 # real codec, but not on the allowlist


def test_table_columns_matches_schema(warehouse):
    assert warehouse.table_columns() == warehouse.schema()
