"""The dialect-aware parameter scan: discovery and paramstyle translation.

$name placeholders are found and rewritten only in code positions -- never
inside string literals, quoted identifiers, comments, or dollar-quoted bodies,
whose lexical rules differ per dialect.
"""

import pytest

from duckbill.backends.base import bind, jsonable, jsonable_row, referenced_params


@pytest.mark.parametrize("sql,dialect,want", [
    ("SELECT $foo, $$bar $baz$$ FROM t WHERE x='$qux'", "postgres", {"foo"}),
    ("SELECT $a, $tag$ hi $n $tag$ FROM t", "postgres", {"a"}),
    ('SELECT $a, "lit $b" FROM t -- $c', "duckdb", {"a"}),
    ("SELECT $a, x # $d\nFROM t WHERE y=$e", "mysql", {"a", "e"}),
    ("SELECT $start, $end FROM t WHERE label='$x'", "duckdb", {"start", "end"}),
    ("SELECT 1", "duckdb", set()),
])
def test_referenced_params_skips_noncode(sql, dialect, want):
    assert referenced_params(sql, dialect) == want


def test_bind_duckdb_passthrough_filters_params():
    sql, params = bind("SELECT $start FROM t", {"start": 1, "z": 9}, "duckdb", "duckdb")
    assert sql == "SELECT $start FROM t"
    assert params == {"start": 1}  # unreferenced 'z' dropped


def test_bind_sqlite_named():
    sql, params = bind("SELECT $start FROM t WHERE k=$kind",
                       {"start": 1, "kind": "a", "z": 9}, "sqlite", "sqlite")
    assert sql == "SELECT :start FROM t WHERE k=:kind"
    assert params == {"start": 1, "kind": "a"}


def test_bind_pyformat_escapes_literal_percent():
    sql, params = bind("SELECT $a FROM t WHERE s LIKE '%x%' AND b=$a",
                       {"a": 1}, "postgres", "pyformat")
    assert sql == "SELECT %(a)s FROM t WHERE s LIKE '%%x%%' AND b=%(a)s"
    assert params == {"a": 1}


def test_bind_leaves_placeholders_in_strings_untouched():
    sql, params = bind("SELECT $a FROM t WHERE note='$b'", {"a": 1, "b": 2},
                       "postgres", "pyformat")
    assert sql == "SELECT %(a)s FROM t WHERE note='$b'"  # $b stays literal
    assert params == {"a": 1}  # 'b' is not a real param


def test_jsonable_coercions():
    from datetime import date
    from decimal import Decimal
    assert jsonable(Decimal("3.5")) == 3.5 and isinstance(jsonable(Decimal("3.5")), float)
    assert jsonable(date(2026, 1, 2)) == "2026-01-02"
    assert jsonable(b"\x00\xff") == "00ff"
    assert jsonable("x") == "x" and jsonable(7) == 7
    assert jsonable_row((Decimal("1"), "a")) == [1.0, "a"]


def test_bind_pyformat_no_params_does_not_escape_percent():
    sql, params = bind("SELECT * FROM t WHERE s LIKE '%x%'", {}, "postgres", "pyformat")
    assert sql == "SELECT * FROM t WHERE s LIKE '%x%'"  # NOT '%%x%%'
    assert params == {}


def test_pool_discards_connection_on_borrow_error():
    from duckbill.backends.base import Pool
    closed = []
    made = []
    class _Conn:
        def close(self):
            closed.append(self)
    def factory():
        c = _Conn(); made.append(c); return c
    pool = Pool(factory, size=1)
    with pytest.raises(ValueError):
        with pool.borrow():
            raise ValueError("boom")
    assert len(closed) == 1            # the errored connection was closed
    # the slot was freed, so a fresh borrow succeeds with a NEW connection
    with pool.borrow() as con:
        assert con is made[-1] and len(made) == 2


def test_pool_recovers_from_factory_failure():
    from duckbill.backends.base import Pool
    calls = {"n": 0}
    def factory():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return object()
    pool = Pool(factory, size=1)
    with pytest.raises(RuntimeError):
        with pool.borrow():
            pass
    # the failed slot must not leak: a second borrow can still acquire
    with pool.borrow() as con:
        assert con is not None
