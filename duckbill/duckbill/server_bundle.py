"""The bundler: emit one self-contained `uv run` server script for a dashboard.

`build_server` prunes the warehouse to the tables and columns the dashboard
references, exports each to zstd Parquet, and embeds them -- b85-encoded -- into a
single generated `<out>.py`. The recipient runs `uv run <out>.py`; uv resolves the
deps from the PEP 723 header, the script decodes its embedded Parquet once into a
content-keyed temp dir (reused across runs), an in-memory DuckDB exposes each as a
view `warehouse.<table>`, a tiny http.server runs each chart's SQL server-side, and
a browser renders the dashboard. No sibling files, no duckbill install -- just uv.

b85 (stdlib, RFC 1924) rather than raw bytes: a `uv run` script must be valid
UTF-8 text for uv to read its PEP 723 deps, so the data has to be text-encoded;
b85's alphabet has no quote or backslash, so it embeds with no escaping and no
custom decoder. Parquet rather than a native DuckDB file: it keeps the (often
text-heavy) columns dictionary+RLE encoded, so the embedded data is several times
smaller, and DuckDB reads it with predicate pushdown.

The generated script reproduces, minimally, the subset of duckbill the page needs
at runtime (/, /meta, /q, /markers, /schema, /docs, /sql, /vendor, /questions)
plus the `$param` bind machinery. It can't import duckbill, so that logic is
embedded in TEMPLATE below; it's lifted verbatim from server.py/base.py/core.py so
the two stay in sync by reading.
"""

import base64
import hashlib
import json
import os
from typing import Any

from .backends import open_backend
from .backends.base import Backend
from .core import Dashboard
from .loader import load_dashboard
from .page import PAGE
from .prune import referenced
from .questions import QuestionStore

# CDN constants, mirrored from bundle.py so the generated /vendor proxy matches
# the page's pinned asset versions. Kept here (rather than imported at runtime)
# so the standalone script is self-contained.
from .bundle import (
    CDN,
    ESM_CDN,
    VEGA_VERSION,
    VEGALITE_VERSION,
    VEGAEMBED_VERSION,
)


def _collect_pruned_parquet(
    warehouse: Backend, ref: dict[str, set[str] | None]
) -> tuple[list[str], dict[str, bytes]]:
    """Export the referenced tables, projected to their columns, to zstd Parquet
    in memory. Reuses export_parquet's projection, so it stays backend-agnostic.

    Returns `(names, raw)`: the bare table names in `ref` order, and
    `{name: parquet_bytes}`. The caller b85-encodes these into the script and the
    generated server exposes each as a view `warehouse.<name>`.
    """
    names: list[str] = []
    raw: dict[str, bytes] = {}
    for qualified, cols in ref.items():
        name = qualified.split(".", 1)[-1]  # warehouse.<name> -> <name>
        columns = sorted(cols) if cols else None
        raw[name] = warehouse.export_parquet(qualified, columns=columns, compression="zstd")
        names.append(name)
    return names, raw


def _data_key(names: list[str], raw: dict[str, bytes]) -> str:
    """A short content hash identifying this dataset, so a re-run of the same
    bundle reuses its already-extracted temp copy instead of rewriting it."""
    h = hashlib.sha256()
    for name in names:
        h.update(name.encode("utf-8"))
        h.update(b"\0")
        h.update(raw[name])
    return h.hexdigest()[:16]


def _meta_payload(dashboard: Dashboard, warehouse: Backend) -> dict[str, Any]:
    """The meta the standalone server embeds: mirrors server.py's /meta and the
    /docs and /sql payloads, but with each chart's (and marker's) SQL inlined --
    the standalone server runs it server-side."""
    charts: list[dict[str, Any]] = []
    for m, c in zip(dashboard.chart_meta(warehouse.dialect), dashboard.charts):
        m = dict(m)
        m["sql"] = c["sql"]
        if c.get("spark"):
            m["spark"] = c["spark"]
        charts.append(m)
    markers = []
    for m, src in zip(dashboard.marker_meta(), dashboard.markers):
        m = dict(m)
        m["sql"] = src["sql"]
        markers.append(m)
    return {
        "title": dashboard.title,
        "params": dashboard.param_meta(warehouse),
        "charts": charts,
        "markers": markers,
        "docs": {"readme": dashboard.readme, "tables": warehouse.docs()},
    }


def build_server(
    dashboard_path: str, db_path: str, out_path: str, questions_dir: str | None = None
) -> None:
    """Emit a single-file standalone `uv run` server script.

    Writes `out_path`: a self-contained Python http.server with the dashboard's
    pruned data (one zstd Parquet per referenced table, projected to its
    referenced columns) embedded as b85. The recipient runs `uv run <out_path>`;
    on first run the script extracts the Parquet to a content-keyed temp dir and
    serves the dashboard. No sibling files, no duckbill install.
    """
    dashboard = load_dashboard(dashboard_path)
    warehouse = open_backend(db_path)
    if not warehouse.bundleable:
        raise ValueError(
            f"bundle requires a DuckDB or SQLite backend; "
            f"{warehouse.dialect} is serve-only -- a bundle embeds the data as "
            f"Parquet, which a network backend can't export")
    if questions_dir is None:
        questions_dir = os.path.join(
            os.path.dirname(os.path.abspath(dashboard_path)), "questions")

    ref = referenced(dashboard, warehouse)
    print(f"exporting {len(ref)} of {len(warehouse.schema())} tables (pruned)...")
    names, raw = _collect_pruned_parquet(warehouse, ref)
    raw_mb = sum(len(b) for b in raw.values()) / 1e6
    b85data = {n: base64.b85encode(raw[n]).decode("ascii") for n in names}

    meta = _meta_payload(dashboard, warehouse)
    questions = QuestionStore(questions_dir).list()

    script = TEMPLATE.format(
        page=repr(PAGE),
        meta=repr(json.dumps(meta)),
        questions=repr(json.dumps(questions)),
        tables=repr(json.dumps(names)),
        data_key=repr(_data_key(names, raw)),
        b85data=repr(json.dumps(b85data)),
        cdn=repr(CDN),
        esm_cdn=repr(ESM_CDN),
        vega=repr(VEGA_VERSION),
        vegalite=repr(VEGALITE_VERSION),
        vegaembed=repr(VEGAEMBED_VERSION),
    )
    with open(out_path, "w") as f:
        f.write(script)

    out_mb = os.path.getsize(out_path) / 1e6
    print(f"wrote {out_path} ({len(names)} tables, {raw_mb:.1f} MB data, "
          f"{out_mb:.1f} MB self-contained)")
    print(f"run it with: uv run {out_path}")


# The generated script. Logic is lifted verbatim from base.py (bind machinery),
# server.py (the handler + endpoints), and bundle.py (the /vendor CDN proxy) so a
# reader can diff the two. `{...}` slots are str.format fields -- all other braces
# are doubled. Embedded text (PAGE, meta JSON) is injected as repr()'d literals.
TEMPLATE = '''# /// script
# requires-python = ">=3.10"
# dependencies = ["duckdb>=1.0", "sqlglot>=20", "pytz"]
# ///
"""Standalone duckbill dashboard server, generated by `duckbill bundle`.

Runs the dashboard's SQL server-side against the embedded pruned Parquet (decoded
once into a content-keyed temp dir, exposed as views) and serves the page.
Self-contained: `uv run` resolves the deps above; no duckbill install, no sibling
files, no static host. Loopback only.

pytz is required by DuckDB's Python client to convert a TIMESTAMPTZ result (any
`to_timestamp(...)` column) into a datetime; it is not a dependency of the duckdb
wheel, so without it such a query fails with "No module named 'pytz'".
"""

import base64
import hashlib
import json
import os
import re
import subprocess
import tempfile
import threading
import webbrowser
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import duckdb
import sqlglot

# ---- embedded data -------------------------------------------------------
PAGE = {page}
META = json.loads({meta})
QUESTIONS = json.loads({questions})
TABLES = json.loads({tables})
# Each referenced table's pruned, zstd-compressed Parquet, b85-encoded (RFC 1924;
# its alphabet has no quote/backslash, so it embeds verbatim). DATA_KEY identifies
# this dataset so repeat runs reuse an already-extracted copy.
DATA_KEY = {data_key}
B85DATA = json.loads({b85data})

CDN = {cdn}
ESM_CDN = {esm_cdn}
VEGA_VERSION = {vega}
VEGALITE_VERSION = {vegalite}
VEGAEMBED_VERSION = {vegaembed}
VENDOR = {{
    "vega.js": CDN + "/vega@" + VEGA_VERSION + "/build/vega.min.js",
    "vega-lite.js": CDN + "/vega-lite@" + VEGALITE_VERSION + "/build/vega-lite.min.js",
    "vega-embed.js": CDN + "/vega-embed@" + VEGAEMBED_VERSION + "/build/vega-embed.min.js",
}}

DIALECT = "duckdb"
PARAMSTYLE = "duckdb"
HOST = "127.0.0.1"
PORT = 8799

# ---- $param machinery (lifted from duckbill/backends/base.py) -------------
_PARAM = re.compile(r"\\$([A-Za-z_][A-Za-z0-9_]*)")
_PROTECTED_TOKENS = {{
    "STRING", "HEREDOC_STRING", "RAW_STRING", "NATIONAL_STRING",
    "BYTE_STRING", "HEX_STRING", "BIT_STRING", "IDENTIFIER",
}}
_LINE_COMMENTS = {{"mysql": ("--", "#")}}
_STYLE = {{
    "duckdb": lambda n: "$" + n,
    "sqlite": lambda n: ":" + n,
    "pyformat": lambda n: "%(" + n + ")s",
}}


def _in(spans, i):
    return any(a <= i <= b for a, b in spans)


def _comment_spans(sql, dialect, str_spans):
    markers = _LINE_COMMENTS.get(dialect, ("--",))
    spans, i, n = [], 0, len(sql)
    while i < n:
        if _in(str_spans, i):
            i += 1
            continue
        if sql.startswith("/*", i):
            j = sql.find("*/", i + 2)
            j = n - 1 if j < 0 else j + 1
            spans.append((i, j))
            i = j + 1
            continue
        if any(sql.startswith(m, i) for m in markers):
            j = sql.find("\\n", i)
            j = n - 1 if j < 0 else j - 1
            spans.append((i, j))
            i = j + 1
            continue
        i += 1
    return spans


def _protected(sql, dialect):
    try:
        toks = sqlglot.tokenize(sql, dialect=dialect)
    except Exception:
        toks = []
    str_spans = [(t.start, t.end) for t in toks if t.token_type.name in _PROTECTED_TOKENS]
    return str_spans + _comment_spans(sql, dialect, str_spans)


def referenced_params(sql, dialect="duckdb"):
    spans = _protected(sql, dialect)
    return {{m.group(1) for m in _PARAM.finditer(sql) if not _in(spans, m.start())}}


def bind(sql, args, dialect, paramstyle):
    spans = _protected(sql, dialect)
    fmt = _STYLE[paramstyle]
    esc = (lambda s: s.replace("%", "%%")) if paramstyle == "pyformat" else (lambda s: s)
    out, last, used = [], 0, set()
    for m in _PARAM.finditer(sql):
        if _in(spans, m.start()):
            continue
        name = m.group(1)
        out.append(esc(sql[last:m.start()]))
        out.append(fmt(name))
        last = m.end()
        used.add(name)
    if not used:
        return sql, {{}}
    out.append(esc(sql[last:]))
    return "".join(out), {{k: v for k, v in args.items() if k in used}}


def jsonable(v):
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (datetime, date, time)):
        return v.isoformat()
    if isinstance(v, (bytes, bytearray, memoryview)):
        return bytes(v).hex()
    return v


def jsonable_row(row):
    return [jsonable(v) for v in row]


# ---- coerce (lifted from duckbill/core.py) --------------------------------
_WINDOW_RE = re.compile(r"^(\\d+)([hd])$")


def _window_delta(preset):
    m = _WINDOW_RE.match(preset)
    if not m:
        raise ValueError("bad window preset " + repr(preset))
    n, unit = int(m.group(1)), m.group(2)
    return timedelta(hours=n * (24 if unit == "d" else 1))


def _timespan_param():
    return next((p for p in META["params"] if p.get("control") == "timespan"), None)


def _defaults():
    out = {{}}
    for p in META["params"]:
        if p.get("control") == "timespan":
            delta = _window_delta(p.get("default", "24h"))
            end = datetime.now(timezone.utc)
            out["start"] = (end - delta).isoformat()
            out["end"] = end.isoformat()
        else:
            out[p["name"]] = p.get("default", "")
    return out


def coerce(qs):
    types = {{p["name"]: p.get("type", "str") for p in META["params"]}}
    args = _defaults()
    for k, vals in qs.items():
        if k == "chart":
            continue
        val = vals[0] if isinstance(vals, (list, tuple)) else vals
        t = types.get(k, "str")
        try:
            args[k] = int(val) if t == "int" else float(val) if t == "float" else val
        except (TypeError, ValueError):
            args[k] = val
    return args


# ---- the warehouse: views over the embedded pruned Parquet -----------------
# The Parquet rides inside this script (b85). On first run we decode it once into
# a content-keyed temp dir; later runs of the same bundle reuse it. An in-memory
# DuckDB exposes each file as a view warehouse.<table>, so the dashboard's
# `warehouse.<name>` SQL runs unchanged.
def _extract_data():
    d = os.path.join(tempfile.gettempdir(), "duckbill-" + DATA_KEY)
    os.makedirs(d, exist_ok=True)
    for name in TABLES:
        path = os.path.join(d, name + ".parquet")
        if not os.path.exists(path):
            data = base64.b85decode(B85DATA[name])
            tmp = path + ".tmp"
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, path)  # atomic: a torn write never looks complete
    return d


_DATA_DIR = _extract_data()
_CON = duckdb.connect()
_CON.execute("CREATE SCHEMA IF NOT EXISTS warehouse")
for _name in TABLES:
    _pq = os.path.join(_DATA_DIR, _name + ".parquet").replace("'", "''")
    _view = _name.replace('"', '""')
    _CON.execute('CREATE VIEW warehouse."' + _view + '" AS '
                 "SELECT * FROM read_parquet('" + _pq + "')")
_LOCK = threading.Lock()


def run(sql, args):
    q, p = bind(sql, args, DIALECT, PARAMSTYLE)
    with _LOCK:
        cur = _CON.execute(q, p) if p else _CON.execute(q)
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
    return cols, [dict(zip(cols, jsonable_row(r))) for r in rows]


# ---- /vendor CDN proxy (lifted from duckbill/bundle.py) -------------------
_CACHE = os.path.expanduser("~/.cache/duckbill/vendor")


def _fetch(url):
    os.makedirs(_CACHE, exist_ok=True)
    path = os.path.join(_CACHE, re.sub(r"[^A-Za-z0-9]+", "_", url))
    if not os.path.exists(path):
        subprocess.run(["curl", "-fsSL", url, "-o", path], check=True)
    with open(path, "rb") as f:
        return f.read()


def vendor_js(name):
    return _fetch(VENDOR[name])


def vendor_esm(path, query=""):
    url = ESM_CDN + "/" + path + (("?" + query) if query else "")
    js = _fetch(url).decode("utf-8", "replace")
    js = re.sub(r'(from|import)("\\s*)/(?!/)', r'\\1\\2/vendor/esm/', js)
    js = re.sub(r'(from|import)(\\s+")/(?!/)', r'\\1\\2/vendor/esm/', js)
    js = re.sub(r'import\\(\\s*"/(?!/)', 'import("/vendor/esm/', js)
    js = js.replace('"' + ESM_CDN + '/', '"/vendor/esm/')
    return js.encode("utf-8")


# ---- HTTP handler (lifted from duckbill/server.py) ------------------------
_BY_ID = {{c["id"]: c for c in META["charts"]}}


def _json_default(o):
    if isinstance(o, (datetime, date, time)):
        return o.isoformat()
    return str(o)


class Handler(BaseHTTPRequestHandler):
    def _send(self, body, content_type, status=200, cache="no-store"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, status=200):
        self._send(json.dumps(obj, default=_json_default).encode(),
                   "application/json", status)

    def log_message(self, *a):
        pass

    def do_GET(self):
        u = urlparse(self.path)
        qs = parse_qs(u.query)

        if u.path == "/":
            return self._send(PAGE.encode(), "text/html; charset=utf-8")

        # Vendored assets are immutable (pinned versions), so they cache hard.
        js, js_cache = "application/javascript; charset=utf-8", "public, max-age=86400"

        if u.path.startswith("/vendor/esm/"):
            try:
                body = vendor_esm(u.path[len("/vendor/esm/"):], u.query)
            except Exception as e:
                return self._json({{"error": "could not vendor module: " + str(e)}}, 502)
            return self._send(body, js, cache=js_cache)

        if u.path.startswith("/vendor/"):
            try:
                body = vendor_js(u.path[len("/vendor/"):])
            except KeyError:
                return self._json({{"error": "unknown vendor asset"}}, 404)
            except Exception as e:
                return self._json({{"error": "could not vendor asset: " + str(e)}}, 502)
            return self._send(body, js, cache=js_cache)

        if u.path == "/meta":
            return self._json({{
                "title": META["title"],
                "params": META["params"],
                "charts": [{{k: v for k, v in c.items() if k not in ("sql", "spark")}}
                           | ({{"spark": True}} if c.get("spark") else {{}})
                           for c in META["charts"]],
                "markers": [{{k: v for k, v in m.items() if k != "sql"}}
                            for m in META["markers"]],
                "savable": False,           # the bundle is read-only: saving is disabled
            }})

        if u.path == "/markers":
            out = {{}}
            for m in META["markers"]:
                try:
                    _, rows = run(m["sql"], coerce(qs))
                except Exception:
                    rows = []
                out[m["id"]] = rows
            return self._json(out)

        if u.path == "/schema":
            schema = {{}}
            with _LOCK:
                rows = _CON.execute(
                    "SELECT table_schema, table_name, column_name "
                    "FROM information_schema.columns "
                    "WHERE table_schema NOT IN ('information_schema', 'pg_catalog') "
                    "ORDER BY table_schema, table_name, ordinal_position").fetchall()
            for sch, tbl, col in rows:
                schema.setdefault(sch + "." + tbl, []).append(col)
            return self._json(schema)

        if u.path == "/docs":
            return self._json(META["docs"])

        if u.path == "/sql":
            chart = _BY_ID.get(qs.get("chart", [None])[0])
            if not chart:
                return self._json({{"error": "unknown chart"}}, 404)
            return self._json({{"id": chart["id"], "sql": chart["sql"]}})

        if u.path == "/questions":
            return self._json(QUESTIONS)

        if u.path == "/q":
            cid = qs.get("chart", [None])[0]
            chart = _BY_ID.get(cid)
            if not chart:
                return self._json({{"error": "unknown chart " + repr(cid)}}, 404)
            sql = chart.get("spark") if qs.get("spark") else chart["sql"]
            if not sql:
                return self._json({{"id": cid, "rows": []}})
            try:
                _, rows = run(sql, coerce(qs))
                return self._json({{"id": cid, "rows": rows}})
            except Exception as e:
                return self._json({{"id": cid, "error": str(e)}}, 500)

        return self._json({{"error": "not found"}}, 404)

    def do_POST(self):
        u = urlparse(self.path)
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:                           # malformed header -> empty body, not a 500
            length = 0
        body = {{}}
        try:
            body = json.loads(self.rfile.read(length) or b"{{}}")
        except Exception:
            pass
        if u.path == "/ask":
            try:
                with _LOCK:
                    cur = _CON.execute(body.get("sql", ""))
                    cols = [d[0] for d in cur.description]
                    rows = cur.fetchmany(2001)
                truncated = len(rows) > 2000
                rows = rows[:2000]
                return self._json({{"cols": cols,
                                   "rows": [dict(zip(cols, jsonable_row(r))) for r in rows],
                                   "truncated": truncated}})
            except Exception as e:
                return self._json({{"error": str(e)}}, 400)
        # Saving questions is disabled in a standalone bundle.
        if u.path == "/questions":
            return self._json({{"error": "saving is disabled in a standalone bundle"}}, 400)
        if u.path == "/questions/delete":
            return self._json({{"ok": False}})
        return self._json({{"error": "not found"}}, 404)


def main():
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    url = "http://%s:%d" % (HOST, PORT)
    print("duckbill serving " + url + "  (" + repr(META["title"]) + ": "
          + str(len(META["charts"])) + " charts, " + str(len(TABLES)) + " tables)")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\\nbye")


if __name__ == "__main__":
    main()
'''
