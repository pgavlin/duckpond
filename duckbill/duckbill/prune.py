"""Work out which warehouse tables -- and which of their columns -- a dashboard
actually queries, so the bundler embeds only that data instead of the whole
warehouse.

The dashboard references tables as `warehouse.<name>`; `warehouse.schema()` keys
them as `<schema>.<name>` (e.g. `main.t_used`). `referenced()` resolves columns
to their tables with sqlglot's qualifier and reports, per `schema()` key, the set
of referenced columns -- or `None`, meaning "embed every column", whenever
pruning would risk dropping a column a chart needs (a `SELECT *`, an
unresolvable column, or a query sqlglot can't qualify).
"""

from collections.abc import Iterable, Iterator
from typing import TYPE_CHECKING

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.qualify import qualify

from .backends.base import Schema

if TYPE_CHECKING:
    from .backends.base import Backend
    from .core import Dashboard

# The catalog/db prefix dashboards use to name warehouse tables.
_PREFIX = "warehouse"


def _dashboard_sql(dashboard: "Dashboard") -> Iterator[str | None]:
    """Every SQL string a dashboard runs against the warehouse."""
    for c in dashboard.charts:
        yield c.get("sql")
        yield c.get("spark")
    for m in dashboard.markers:
        yield m.get("sql")
    for p in dashboard.params:
        yield p.get("choices_sql")


def _schema_map(
    table_columns: Schema,
) -> tuple[dict[str, object], dict[str, str]]:
    """sqlglot schema map keyed by the `warehouse` prefix, plus a name->key map
    back to the qualified names `schema()` produces.

    `table_columns` is `{<schema>.<name>: [col, ...]}`. Several schemas can share a
    bare table name -- a leftover `warehouse_staging` from a prior dlt load, `_dlt_*`
    bookkeeping, a SQLite `main`. The dashboard only ever says `warehouse.<name>`, so per
    bare name we prefer the key in the `warehouse` schema and never let a non-warehouse
    copy shadow it; absent a warehouse copy (e.g. SQLite's `main.<name>`) the first key
    seen wins. (Keying by bare name with last-one-wins, as this once did, would bundle
    `warehouse_staging.events` for a chart that queries `warehouse.events`.)
    """
    by_name: dict[str, str] = {}
    for key in table_columns:
        schema_name, _, name = key.partition(".")
        cur = by_name.get(name)
        if cur is None or (schema_name == _PREFIX and cur.partition(".")[0] != _PREFIX):
            by_name[name] = key
    schema = {name: {c: "UNKNOWN" for c in table_columns[key]} for name, key in by_name.items()}
    return {_PREFIX: schema}, by_name


def _warehouse_tables(expr: exp.Expr, name_to_key: dict[str, str]) -> set[str]:
    """The set of qualified `schema()` keys referenced by warehouse.<name>
    tables in `expr`."""
    out: set[str] = set()
    for t in expr.find_all(exp.Table):
        if t.db == _PREFIX and t.name in name_to_key:
            out.add(name_to_key[t.name])
    return out


def _alias_to_key(expr: exp.Expr, name_to_key: dict[str, str]) -> dict[str, str]:
    """Map each warehouse table's in-query qualifier (its alias, or its bare name
    when unaliased) to the qualified `schema()` key. Built from an expression
    sqlglot has already qualified, so every column carries one of these
    qualifiers. CTE and subquery aliases never appear here, so columns that
    resolve to them are simply not warehouse columns."""
    out: dict[str, str] = {}
    for t in expr.find_all(exp.Table):
        if t.db == _PREFIX and t.name in name_to_key:
            out[t.alias_or_name] = name_to_key[t.name]
    return out


def _has_star(expr: exp.Expr) -> bool:
    """Whether any select projects a bare `*` or a `<table>.*`. `count(*)` and
    other function-arg stars don't count -- only projection stars widen the set
    of columns we'd have to embed."""
    for sel in expr.find_all(exp.Select):
        for proj in sel.expressions:
            if isinstance(proj, exp.Star):
                return True
            if isinstance(proj, exp.Column) and isinstance(proj.this, exp.Star):
                return True
    return False


def referenced(dashboard: "Dashboard", warehouse: "Backend") -> dict[str, set[str] | None]:
    """The warehouse tables a dashboard queries, mapped to their referenced
    columns.

    Returns `{<schema>.<name>: set[str] | None}` for each warehouse table the
    dashboard actually touches. `None` means "embed all columns" -- used whenever
    pruning isn't safe (a projection star, a column that can't be resolved to one
    table, or SQL sqlglot can't qualify). Tables the dashboard never queries are
    absent. Keys match `warehouse.schema()` exactly.
    """
    table_columns = warehouse.table_columns()
    schema_map, name_to_key = _schema_map(table_columns)
    dialect = warehouse.dialect

    result: dict[str, set[str] | None] = {}

    def mark_all(keys: Iterable[str]) -> None:
        for k in keys:
            result[k] = None

    for sql in _dashboard_sql(dashboard):
        if not sql:
            continue
        try:
            expr = sqlglot.parse_one(sql, dialect=dialect)
        except Exception:
            continue  # not a query we can read -- it touches no resolvable table
        if expr is None:
            continue

        # Tables this statement references, before qualify rewrites anything.
        tables = _warehouse_tables(expr, name_to_key)
        if not tables:
            continue

        # A projection star could pull in any column; be conservative and embed
        # every column of every warehouse table in the statement.
        if _has_star(expr):
            mark_all(tables)
            continue

        try:
            q = qualify(expr, schema=schema_map, dialect=dialect)
        except Exception:
            # Can't resolve columns to tables -- don't risk dropping one.
            mark_all(tables)
            continue

        for t in tables:
            result.setdefault(t, set())

        alias_to_key = _alias_to_key(q, name_to_key)
        for col in q.find_all(exp.Column):
            if isinstance(col.this, exp.Star):
                continue
            tbl = col.table
            if not tbl:
                # qualify normally tags every column with a source; an untagged
                # one is ambiguous -- widen every warehouse table here.
                mark_all(tables)
                break
            key = alias_to_key.get(tbl)
            if key is None:
                continue  # a CTE / subquery / derived column, not a warehouse one
            cols = result.get(key)
            if cols is not None:
                cols.add(col.name)

    return result
