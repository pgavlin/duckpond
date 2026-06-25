"""The HTTP server: imports a dashboard, runs its SQL per request, serves a page.

Endpoints:
  GET /          the dashboard page (static HTML+JS; data comes from the others)
  GET /meta      {title, params, charts} -- everything the page needs to lay out
  GET /q?chart=&<params>   run one chart's SQL with the request's params -> rows

Loopback only by default. Per-chart errors come back as JSON so one bad query
renders in its own card instead of blanking the page.
"""

import json
import os
from datetime import date, datetime, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from .backends import open_backend
from .backends.base import Backend
from .core import Dashboard
from .loader import load_dashboard
from .page import PAGE
from .questions import QuestionStore


def _json_default(o: object) -> str:
    # datetimes -> ISO8601 so Vega-Lite parses them as temporal.
    if isinstance(o, (datetime, date, time)):
        return o.isoformat()
    return str(o)


def make_handler(
    dashboard: Dashboard, warehouse: Backend, questions: QuestionStore
) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def _send(self, body: bytes, content_type: str, status: int = 200,
                  cache: str = "no-store") -> None:  # default: always live, never stale
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj: object, status: int = 200) -> None:
            self._send(json.dumps(obj, default=_json_default).encode(),
                       "application/json", status)

        def log_message(self, format: str, *args: Any) -> None:  # quiet
            pass

        def do_GET(self) -> None:
            u = urlparse(self.path)
            qs = parse_qs(u.query)

            if u.path == "/":
                return self._send(PAGE.encode(), "text/html; charset=utf-8")

            # Vendored assets are immutable (pinned versions), so they cache hard.
            js, js_cache = "application/javascript; charset=utf-8", "public, max-age=86400"

            if u.path.startswith("/vendor/esm/"):  # CodeMirror module graph, proxied same-origin
                from .bundle import vendor_esm
                try:
                    body = vendor_esm(u.path[len("/vendor/esm/"):], u.query)
                except Exception as e:  # fetch failed (offline / curl error)
                    return self._json({"error": f"could not vendor module: {e}"}, 502)
                return self._send(body, js, cache=js_cache)

            if u.path.startswith("/vendor/"):  # Vega libraries, served same-origin (no CDN)
                from .bundle import vendor_js
                try:
                    body = vendor_js(u.path[len("/vendor/"):])
                except KeyError:
                    return self._json({"error": "unknown vendor asset"}, 404)
                except Exception as e:  # fetch failed (offline / curl error)
                    return self._json({"error": f"could not vendor asset: {e}"}, 502)
                return self._send(body, js, cache=js_cache)

            if u.path == "/meta":
                return self._json({
                    "title": dashboard.title,
                    "params": dashboard.param_meta(warehouse),
                    "charts": dashboard.chart_meta(warehouse.dialect),
                    "markers": dashboard.marker_meta(),
                })

            if u.path == "/markers":
                return self._json(dashboard.marker_rows(warehouse, qs))

            if u.path == "/schema":
                return self._json(warehouse.schema())

            if u.path == "/docs":
                return self._json({"readme": dashboard.readme, "tables": warehouse.docs()})

            if u.path == "/sql":  # a chart's raw SQL, for "open in Ask" from the enlarge modal
                sid = qs.get("chart", [None])[0]
                chart = dashboard.by_id.get(sid) if sid is not None else None
                if not chart:
                    return self._json({"error": "unknown chart"}, 404)
                return self._json({"id": chart["id"], "sql": chart["sql"]})

            if u.path == "/questions":
                return self._json(questions.list())

            if u.path == "/q":
                cid = qs.get("chart", [None])[0]
                chart = dashboard.by_id.get(cid) if cid is not None else None
                if not chart:
                    return self._json({"error": f"unknown chart {cid!r}"}, 404)
                # spark=1 runs the chart's companion sparkline query instead of its main SQL
                sql = chart.get("spark") if qs.get("spark") else chart["sql"]
                if not sql:
                    return self._json({"id": cid, "rows": []})
                try:
                    _, rows = warehouse.run(sql, dashboard.coerce(qs))
                    return self._json({"id": cid, "rows": rows})
                except Exception as e:
                    return self._json({"id": cid, "error": str(e)}, 500)

            return self._json({"error": "not found"}, 404)

        def do_POST(self) -> None:
            u = urlparse(self.path)
            length = int(self.headers.get("Content-Length", 0))
            body: dict[str, Any] = {}
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except Exception:
                pass
            if u.path == "/ask":  # ad-hoc query from the Ask view
                try:
                    cols, rows, truncated = warehouse.query(body.get("sql", ""))
                    return self._json({"cols": cols, "rows": rows, "truncated": truncated})
                except Exception as e:
                    return self._json({"error": str(e)}, 400)
            if u.path == "/questions":  # save (upsert)
                if not body.get("name", "").strip():
                    return self._json({"error": "name required"}, 400)
                return self._json(questions.save(body["name"], body.get("sql", ""), body.get("viz", {})))
            if u.path == "/questions/delete":
                questions.delete(body.get("slug", ""))
                return self._json({"ok": True})
            return self._json({"error": "not found"}, 404)

    return Handler


def serve(
    dashboard_path: str,
    db_path: str,
    host: str = "127.0.0.1",
    port: int = 8799,
    questions_dir: str | None = None,
    pool: int = 4,
) -> None:
    """Load the dashboard, open the warehouse, and serve until interrupted."""
    dashboard = load_dashboard(dashboard_path)
    warehouse = open_backend(db_path, pool=pool)
    if questions_dir is None:  # default to questions/ next to the dashboard module
        questions_dir = os.path.join(os.path.dirname(os.path.abspath(dashboard_path)), "questions")
    handler = make_handler(dashboard, warehouse, QuestionStore(questions_dir))
    srv = ThreadingHTTPServer((host, port), handler)
    print(f"duckbill serving http://{host}:{port}  "
          f"({dashboard.title!r}: {len(dashboard.charts)} charts, db={db_path})")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
