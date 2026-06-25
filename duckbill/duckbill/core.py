"""The data layer: a Dashboard and the bind-value coercion it provides.

Nothing here knows about HTTP. `Dashboard` holds the declared charts and params
and turns a request's query string into bind values. The warehouse is a `Backend`
opened by `open_backend`; `Warehouse` is an alias for the DuckDB backend.
"""

import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from .backends.base import Row, referenced_params
from .backends.duckdb import DuckDBBackend as Warehouse  # `Warehouse` is the default backend

if TYPE_CHECKING:
    from .backends.base import Backend

# Author-supplied declared shapes, validated at runtime in `_validate`. These are
# heterogeneous data dicts (many optional keys, mixed value types) read by both
# the server and the page, so a flat `dict[str, Any]` stays out of the way; the
# validation lives in code, not the type.
Chart = dict[str, Any]
Param = dict[str, Any]
Marker = dict[str, Any]
# A parsed query string (name -> list of values, as urllib's parse_qs returns, or
# a bare value for hand-built calls).
QueryString = Mapping[str, "str | Sequence[str]"]

_WINDOW_RE = re.compile(r"^(\d+)([hd])$")

# A timespan control binds these two params; SQL references them directly.
TIMESPAN_BINDS = ("start", "end")


# params_in is the dialect-aware param scan (DuckDB dialect).
def params_in(sql: str) -> set[str]:
    """The named parameters a SQL string references, e.g. {'start', 'route'}."""
    return referenced_params(sql, "duckdb")


def window_delta(preset: str) -> timedelta:
    """The timedelta for a relative window preset like '24h' or '31d'."""
    m = _WINDOW_RE.match(preset)
    if not m:
        raise ValueError(f"bad window preset {preset!r} (want e.g. '24h', '7d')")
    n, unit = int(m.group(1)), m.group(2)
    return timedelta(hours=n * (24 if unit == "d" else 1))


class Dashboard:
    """Validated charts + params, plus request->bind-value coercion.

    `charts` is a list of dicts; see the README for the schema. `params` is a
    list of declared parameters that drive the page's controls. One param may
    use `control: "timespan"`, which binds `$start` and `$end` instead of a
    param of its own name.
    """

    KNOWN_TYPES = {"line", "bar", "stacked-bar", "area", "point", "table",
                   "metric", "leaderboard", "spec"}

    def __init__(
        self,
        charts: Sequence[Chart],
        params: Sequence[Param] | None = None,
        title: str = "duckbill",
        markers: Sequence[Marker] | None = None,
        readme: str = "",
    ):
        self.charts: list[Chart] = list(charts)
        self.params: list[Param] = list(params or [])
        self.markers: list[Marker] = list(markers or [])
        self.title = title
        self.readme = readme or ""
        self.by_id: dict[str, Chart] = {c["id"]: c for c in self.charts}
        self.markers_by_id: dict[str, Marker] = {m["id"]: m for m in self.markers}
        self.timespan: Param | None = next(
            (p for p in self.params if p.get("control") == "timespan"), None)
        self._validate()

    def _validate(self) -> None:
        seen: set[str] = set()
        for c in self.charts:
            for key in ("id", "title", "type", "sql"):
                if key not in c:
                    raise ValueError(f"chart {c.get('id', c)!r} missing {key!r}")
            if c["id"] in seen:
                raise ValueError(f"duplicate chart id {c['id']!r}")
            seen.add(c["id"])
            if c["type"] not in self.KNOWN_TYPES:
                raise ValueError(
                    f"chart {c['id']!r} has unknown type {c['type']!r}")
            if c["type"] == "spec" and "spec" not in c:
                raise ValueError(f"chart {c['id']!r} is type 'spec' but has no `spec`")
            if "drill" in c:
                if c["type"] == "table":
                    if not isinstance(c["drill"], dict):
                        raise ValueError(
                            f"chart {c['id']!r} table drill must be a column->param map")
                elif not {"param", "field"} <= set(c["drill"]):
                    raise ValueError(
                        f"chart {c['id']!r} drill needs both 'param' and 'field'")
            if "span" in c and c["span"] != "full" and not (
                    isinstance(c["span"], int) and not isinstance(c["span"], bool) and c["span"] >= 1):
                raise ValueError(
                    f"chart {c['id']!r} span must be a positive int or 'full'")
            if "good" in c:
                dirs = c["good"].values() if isinstance(c["good"], dict) else [c["good"]]
                if not all(d in ("up", "down", "neutral") for d in dirs):
                    raise ValueError(
                        f"chart {c['id']!r} good must be 'up'/'down'/'neutral' "
                        "or a map of figure label -> one of those")
            if "spark" in c and not isinstance(c["spark"], str):
                raise ValueError(f"chart {c['id']!r} spark must be a SQL string")
        for p in self.params:
            if "name" not in p:
                raise ValueError(f"param {p!r} missing 'name'")
        for m in self.markers:
            for key in ("id", "sql", "field"):
                if key not in m:
                    raise ValueError(f"marker {m.get('id', m)!r} missing {key!r}")

    def defaults(self) -> dict[str, object]:
        """Default bind values, before the request overrides any."""
        out: dict[str, object] = {}
        for p in self.params:
            if p.get("control") == "timespan":
                delta = window_delta(p.get("default", "24h"))
                end = datetime.now(timezone.utc)
                out["start"] = (end - delta).isoformat()
                out["end"] = end.isoformat()
            else:
                out[p["name"]] = p.get("default", "")
        return out

    def coerce(self, qs: QueryString) -> dict[str, object]:
        """Turn a parsed query string into typed bind values.

        Starts from `defaults()` so a missing param (or a hand-issued curl) still
        binds; then applies whatever the request sent, typed per the param's
        declared `type` ('int'/'float', else string).
        """
        types = {p["name"]: p.get("type", "str") for p in self.params}
        args = self.defaults()
        for k, vals in qs.items():
            if k == "chart":
                continue
            val: str = vals[0] if isinstance(vals, (list, tuple)) else str(vals)
            t = types.get(k, "str")
            try:
                args[k] = int(val) if t == "int" else float(val) if t == "float" else val
            except (TypeError, ValueError):
                args[k] = val
        return args

    def chart_meta(self, dialect: str = "duckdb") -> list[dict[str, Any]]:
        """Per-chart metadata for the page (no SQL leaves the server)."""
        return [{
            "id": c["id"],
            "title": c["title"],
            "type": c["type"],
            "section": c.get("section", "Overview"),
            "encoding": c.get("encoding", {}),
            "spec": c.get("spec"),
            "drill": c.get("drill"),
            "brush": c.get("brush"),
            "markers": c.get("markers"),
            "span": c.get("span"),
            "good": c.get("good"),
            "spark": bool(c.get("spark")),  # the SQL stays server-side; the page just needs to know it exists
            "params": sorted(referenced_params(c["sql"], dialect)),
        } for c in self.charts]

    def marker_meta(self) -> list[dict[str, Any]]:
        """Marker metadata for the page (field/label/color; SQL stays server-side)."""
        return [{
            "id": m["id"],
            "field": m["field"],
            "label": m.get("label"),
            "color": m.get("color", "#b9c2cc"),
        } for m in self.markers]

    def marker_rows(self, warehouse: "Backend", qs: QueryString) -> dict[str, list[Row]]:
        """Run every marker's SQL with the request's params -> {id: rows}."""
        out: dict[str, list[Row]] = {}
        for m in self.markers:
            rows: list[Row]
            try:
                _, rows = warehouse.run(m["sql"], self.coerce(qs))
            except Exception:
                rows = []
            out[m["id"]] = rows
        return out

    def param_meta(self, warehouse: "Backend") -> list[dict[str, Any]]:
        """Param metadata for the page, resolving any `choices_sql` to choices."""
        out: list[dict[str, Any]] = []
        for p in self.params:
            pm: dict[str, Any] = {k: v for k, v in p.items() if k != "choices_sql"}
            if p.get("choices_sql"):
                try:
                    _, rows = warehouse.run(p["choices_sql"], {})
                    pm["choices"] = [next(iter(r.values())) for r in rows]
                except Exception as e:  # surface, don't crash the page
                    pm["choices"] = []
                    pm["error"] = str(e)
            out.append(pm)
        return out


__all__ = ["Dashboard", "Warehouse", "params_in", "window_delta", "TIMESPAN_BINDS"]
