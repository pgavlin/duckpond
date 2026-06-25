"""The harness must not let one source's write failure wedge or truncate the run."""

import pathlib
import sys

import duckdb

TEMPLATE = pathlib.Path(__file__).resolve().parent.parent / "skills" / "ducktail" / "assets" / "template"
sys.path.insert(0, str(TEMPLATE))
import ducktail  # noqa: E402


def test_write_failure_is_skipped_and_later_batches_still_land(tmp_path):
    con = duckdb.connect(str(tmp_path / "w.duckdb"))
    good = ducktail.Table("good", "replace")
    # A merge table whose primary key names a column the batch doesn't have:
    # CREATE TABLE ... PRIMARY KEY (missing) raises inside _write.
    bad = ducktail.Table("bad", "merge", primary_key=("missing",), cursor="ts")

    def produce(starts):
        yield good, [{"x": 1}]
        yield bad, [{"x": 2}]      # this write fails
        yield good, [{"x": 3}]     # ... and must not be lost

    src = ducktail.Source("s", [good, bad], produce, parallel=False)
    counts = ducktail.run(con, [src])

    # The writer survived the failure: both good batches landed (pre-fix the writer
    # thread died on the bad batch, dropping x=3 and, with a full queue, deadlocking).
    assert counts["good"] == 2
    assert {r[0] for r in con.execute("SELECT x FROM warehouse.good").fetchall()} == {1, 3}
    assert "bad" not in counts
    assert con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name='bad'"
    ).fetchone()[0] == 0
    con.close()


def test_reserved_word_column_names_load_and_merge(tmp_path):
    # `by` and `order` are SQL reserved words; the harness must quote identifiers so they
    # work as column names across the generated DDL, INSERT, and the merge's ON CONFLICT/SET.
    con = duckdb.connect(str(tmp_path / "rw.duckdb"))
    posts = ducktail.Table("posts", "merge", primary_key=("id",), cursor="ts")

    def first(starts):
        yield posts, [{"id": 1, "by": "alice", "order": 5, "ts": 100},
                      {"id": 2, "by": "bob", "order": 9, "ts": 101}]
    ducktail.run(con, [ducktail.Source("s", [posts], first, parallel=False)])

    def again(starts):
        yield posts, [{"id": 1, "by": "alice", "order": 7, "ts": 100}]  # re-fetch: must upsert
    ducktail.run(con, [ducktail.Source("s", [posts], again, parallel=False)])

    rows = con.execute('SELECT id, "by", "order" FROM warehouse.posts ORDER BY id').fetchall()
    assert rows == [(1, "alice", 7), (2, "bob", 9)]   # id=1 upserted (order 5 -> 7), no duplicate
    con.close()


def test_reserved_word_table_and_cursor_survive_reads_and_full_reload(tmp_path):
    # The high-water-mark read and the --full DROP interpolate the table name and cursor column,
    # so a reserved word there must be quoted too -- not only in the write path. `order` (table)
    # and `by` (cursor) are both reserved.
    con = duckdb.connect(str(tmp_path / "rw2.duckdb"))
    order = ducktail.Table("order", "merge", primary_key=("id",), cursor="by")

    def produce(starts):
        since = starts["order"]                       # keyed by table name; the rewound cursor
        yield order, [{"id": 1, "by": since + 1}, {"id": 2, "by": since + 2}]

    src = ducktail.Source("s", [order], produce, parallel=False)
    ducktail.run(con, [src])                          # run 1: table absent, no HW read yet
    ducktail.run(con, [src])                          # run 2: SELECT max("by") FROM warehouse."order"
    n = con.execute('SELECT count(*) FROM warehouse."order"').fetchone()[0]
    assert n == 2                                     # id 1,2 upserted, no duplicates
    ducktail.run(con, [src], full=True)               # --full: DROP TABLE warehouse."order"
    assert con.execute('SELECT count(*) FROM warehouse."order"').fetchone()[0] == 2
    con.close()


def test_dependent_source_sees_prior_run_via_second_run(tmp_path):
    # The skill's pattern for a source that reads another's just-written rows: two run()
    # calls. run() returns only after the writer drains, so the second call's source sees
    # the first call's table committed. (parallel=False overlaps the pool; it does NOT wait.)
    con = duckdb.connect(str(tmp_path / "dep.duckdb"))
    a = ducktail.Table("a", "replace")
    b = ducktail.Table("b", "replace")

    def produce_a(starts):
        yield a, [{"k": 1}, {"k": 2}, {"k": 3}]

    def make_b(conn):
        def produce_b(starts):
            ks = [r[0] for r in conn.execute("SELECT k FROM warehouse.a").fetchall()]
            yield b, [{"k": k, "doubled": k * 2} for k in ks]
        return ducktail.Source("b", [b], produce_b, parallel=False)

    ducktail.run(con, [ducktail.Source("a", [a], produce_a)])   # commits warehouse.a
    ducktail.run(con, [make_b(con)])                            # b reads warehouse.a
    rows = con.execute("SELECT k, doubled FROM warehouse.b ORDER BY k").fetchall()
    assert rows == [(1, 2), (2, 4), (3, 6)]                     # b saw a's three rows
    con.close()
