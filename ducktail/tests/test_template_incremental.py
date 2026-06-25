import duckdb
from conftest import run_script

EPOCH0 = 1699999200  # 1700000000 floored to the hour (1700000000 // 3600 * 3600)


def _rev_by_hour(con):
    rows = con.execute(
        f"SELECT CAST((ts - {EPOCH0}) / 3600 AS INTEGER) AS hour, loaded_rev "
        "FROM warehouse.example_hourly"
    ).fetchall()
    out = {}
    for hour, rev in rows:
        out.setdefault(int(hour), set()).add(rev)
    return out


def test_incremental_overlap_and_merge(investigation):
    # run 1: 6 hours of data exist, stamped revision 0
    r1 = run_script("refresh.py", investigation, {"NOW_HOURS": "6", "REVISION": "0"})
    assert r1.returncode == 0, r1.stderr
    # run 2: time advanced to 8 hours, revision 1 -- overlap hours must re-fetch and upsert
    r2 = run_script("refresh.py", investigation, {"NOW_HOURS": "8", "REVISION": "1"})
    assert r2.returncode == 0, r2.stderr

    con = duckdb.connect(str(investigation / "ducktail.duckdb"), read_only=True)
    total = con.execute("SELECT count(*) FROM warehouse.example_hourly").fetchone()[0]
    distinct = con.execute(
        "SELECT count(*) FROM (SELECT DISTINCT ts, partition FROM warehouse.example_hourly)"
    ).fetchone()[0]
    assert total == distinct == 24, "8 hours x 3 partitions, no duplicates"

    rev = _rev_by_hour(con)
    # lag = 2h: run 2's last_value rewinds to hour 3, so hours 3..7 reload at rev 1
    for h in (0, 1, 2):
        assert rev[h] == {0}, f"hour {h} should be untouched: {rev[h]}"
    for h in (3, 4, 5, 6, 7):
        assert rev[h] == {1}, f"hour {h} should reload at rev 1: {rev[h]}"
