"""The static bundler: emit a multi-file site that runs the dashboard in the browser.

`build_static` prunes the warehouse to the referenced tables/columns (reusing the server
bundler's pipeline), writes one Parquet per table under `data/`, and emits `index.html` =
page.py's PAGE plus an injected `WasmDB` -- a DuckDB-WASM data layer that fetches those
Parquet files and answers the page's `DB` calls in the browser. No server.

Vendor assets (Vega, CodeMirror) and DuckDB-WASM load from CDN; the Parquet fetch path is
relative so the site works under a GitHub Pages project subpath.
"""

import json
import os
import re
from typing import Any

from .backends import open_backend
from .bundle import CDN, ESM_CDN, VEGA_VERSION, VEGALITE_VERSION, VEGAEMBED_VERSION
from .loader import load_dashboard
from .page import PAGE
from .prune import referenced
from .questions import QuestionStore
from .server_bundle import _collect_pruned_parquet, _meta_payload

DUCKDB_WASM_VERSION = "1.29.0"

# Shown immediately on load and removed once the engine + data are ready. Injected into the
# body so it paints before the WASM bundle finishes downloading (no blank-page flash). The
# matching JS lives in _wasm_db_js: _wlMsg/_wlDone/_wlErr drive the message and dismissal.
_LOADING_OVERLAY = """
<div id="wasm-loading">
  <div class="wl-box">
    <div class="wl-spin"></div>
    <div class="wl-msg" id="wasm-loading-msg">Loading...</div>
    <div class="wl-sub">running in your browser via DuckDB-WASM</div>
  </div>
</div>
<style>
  #wasm-loading { position: fixed; inset: 0; z-index: 1000; display: flex; align-items: center;
    justify-content: center; background: #f6f7f9; transition: opacity .3s ease; }
  #wasm-loading.done { opacity: 0; pointer-events: none; }
  #wasm-loading .wl-box { text-align: center; color: #1c1e21;
    font: 14px -apple-system, system-ui, sans-serif; padding: 0 24px; }
  #wasm-loading .wl-spin { width: 32px; height: 32px; margin: 0 auto 14px; border: 3px solid #d7dadf;
    border-top-color: #3b6fb0; border-radius: 50%; animation: wl-rot .8s linear infinite; }
  #wasm-loading.error .wl-spin { animation: none; border-color: #c0392b; border-top-color: #c0392b; }
  #wasm-loading.error .wl-msg { color: #c0392b; }
  #wasm-loading .wl-msg { font-weight: 600; }
  #wasm-loading .wl-sub { margin-top: 4px; font-size: 12px; color: #888; }
  @keyframes wl-rot { to { transform: rotate(360deg); } }
</style>
"""


def _rewrite_vendor(page: str) -> str:
    """Rewrite the page's same-origin /vendor asset URLs to absolute CDN URLs.

    Vega loads via <script src="/vendor/vega.js"> etc.; CodeMirror via an importmap of
    /vendor/esm/<spec> entries. Without a server to proxy them, point both at the CDN.
    """
    page = page.replace('src="/vendor/vega-lite.js"',
                        f'src="{CDN}/vega-lite@{VEGALITE_VERSION}/build/vega-lite.min.js"')
    page = page.replace('src="/vendor/vega-embed.js"',
                        f'src="{CDN}/vega-embed@{VEGAEMBED_VERSION}/build/vega-embed.min.js"')
    page = page.replace('src="/vendor/vega.js"',
                        f'src="{CDN}/vega@{VEGA_VERSION}/build/vega.min.js"')
    # CodeMirror importmap: /vendor/esm/<spec> -> esm.sh/<spec>. The "*" externalize prefix
    # is an esm.sh convention, so the mapped URL is ESM_CDN/<spec> with the leading * dropped.
    def esm(m: "re.Match[str]") -> str:
        return f'"{ESM_CDN}/{m.group(1).lstrip("*")}"'
    page = re.sub(r'"/vendor/esm/([^"]+)"', esm, page)
    return page


def _wasm_db_js() -> str:
    """The injected <script> block: the WasmDB implementation.

    WasmDB mirrors ServerDB's 11-method interface (page.py) but answers from DuckDB-WASM
    over the fetched Parquet. It reads window.__duckbillStatic__ (injected by build_static
    before this script) and sets window.__duckbillDB__, which page.py prefers.
    """
    return r"""
<script>
const STATIC = window.__duckbillStatic__;

// ---- helpers: coerce request params and inline $name (string/comment-aware) ----
function coerce(specs, req) {
  const out = {};
  for (const p of specs) {
    if (p.control === "timespan") { /* start/end arrive in req */ }
    else if (p.name in (req || {})) {
      const t = p.type, v = req[p.name];
      out[p.name] = t === "int" ? parseInt(v, 10) : t === "float" ? parseFloat(v) : v;
    } else if ("default" in p) { out[p.name] = p.default; }
  }
  for (const k of ["start", "end"]) if (req && k in req) out[k] = req[k];
  for (const k in (req || {})) if (!(k in out) && k !== "chart") out[k] = req[k];
  return out;
}
function lit(v) {
  if (v === null || v === undefined) return "NULL";
  if (typeof v === "number") return String(v);
  return "'" + String(v).replace(/'/g, "''") + "'";
}
// substitute $name outside single-quoted strings and -- / /* */ comments
function inline(sql, params) {
  let out = "", i = 0, n = sql.length;
  while (i < n) {
    const c = sql[i];
    if (c === "'") { const j = sql.indexOf("'", i + 1); const e = j < 0 ? n : j + 1; out += sql.slice(i, e); i = e; continue; }
    if (c === "-" && sql[i + 1] === "-") { const j = sql.indexOf("\n", i); const e = j < 0 ? n : j; out += sql.slice(i, e); i = e; continue; }
    if (c === "/" && sql[i + 1] === "*") { const j = sql.indexOf("*/", i + 2); const e = j < 0 ? n : j + 2; out += sql.slice(i, e); i = e; continue; }
    const m = /^\$([A-Za-z_][A-Za-z0-9_]*)/.exec(sql.slice(i));
    if (m) { out += (m[1] in params) ? lit(params[m[1]]) : m[0]; i += m[0].length; continue; }
    out += c; i++;
  }
  return out;
}

// ---- loading overlay (static build only: the WASM engine is a multi-MB CDN load) ----
function _wlMsg(s) { const e = document.getElementById("wasm-loading-msg"); if (e) e.textContent = s; }
function _wlDone() { const e = document.getElementById("wasm-loading"); if (e) { e.classList.add("done"); setTimeout(() => e.remove(), 350); } }
function _wlErr(s) { const e = document.getElementById("wasm-loading"); if (e) { e.classList.add("error"); _wlMsg(s); } }

// ---- DuckDB-WASM boot + Parquet registration (lazy, once) ----
let _ready = null, _conn = null, _engine = "";
async function ready() {
  if (_ready) return _ready;
  _ready = (async () => {
    try {
      _wlMsg("Starting query engine...");
      const duckdb = await import("https://cdn.jsdelivr.net/npm/@duckdb/duckdb-wasm@__WASMVER__/+esm");
      const bundle = await duckdb.selectBundle(duckdb.getJsDelivrBundles());
      _engine = bundle.mainModule.includes("coi") ? "coi (threaded)" : "eh (single-threaded)";
      const wurl = URL.createObjectURL(new Blob([`importScripts("${bundle.mainWorker}");`], { type: "text/javascript" }));
      const db = new duckdb.AsyncDuckDB(new duckdb.ConsoleLogger(), new Worker(wurl));
      await db.instantiate(bundle.mainModule, bundle.pthreadWorker);
      URL.revokeObjectURL(wurl);
      _conn = await db.connect();
      await _conn.query("CREATE SCHEMA IF NOT EXISTS warehouse");
      _wlMsg("Loading data...");
      for (const t of STATIC.tables) {
        const buf = new Uint8Array(await (await fetch(`${STATIC.dataDir}/${t}.parquet`)).arrayBuffer());
        await db.registerFileBuffer(`${t}.parquet`, buf);
        await _conn.query(`CREATE VIEW warehouse."${t}" AS SELECT * FROM parquet_scan('${t}.parquet')`);
      }
      const note = document.createElement("div");
      note.style.cssText = "position:fixed;bottom:4px;right:6px;font:11px monospace;opacity:.5;z-index:9999";
      note.textContent = "engine: " + _engine;
      document.body.appendChild(note);
      _wlDone();
    } catch (e) {
      _wlErr("Could not load the query engine. Check your connection and reload.");
      throw e;
    }
  })();
  return _ready;
}

// ---- Arrow -> JSON-shaped rows (ISO temporal, number for bigint/decimal, hex blob) ----
function rows(table) {
  const fields = table.schema.fields;
  const kind = {};
  for (const f of fields) kind[f.name] = String(f.type);
  const out = [];
  for (const r of table.toArray()) {
    const o = {};
    for (const f of fields) {
      let v = r[f.name];
      const k = kind[f.name];
      if (v === null || v === undefined) { o[f.name] = null; continue; }
      if (v instanceof Date) { o[f.name] = v.toISOString(); continue; }
      if (v instanceof Uint8Array) { o[f.name] = Array.from(v).map(b => b.toString(16).padStart(2, "0")).join(""); continue; }
      if (/Timestamp|Date32|Date64|Time\d/.test(k)) {
        // duckdb-wasm declares the unit (often MICRO) but hands back a JS number whose
        // magnitude varies by version; normalize any epoch ns/us/ms/s to milliseconds.
        let ms = Number(v);
        if (ms > 1e16) ms /= 1e6;        // nanoseconds
        else if (ms > 1e14) ms /= 1e3;   // microseconds
        else if (ms < 1e11) ms *= 1e3;   // seconds
        o[f.name] = new Date(ms).toISOString(); continue;
      }
      if (typeof v === "bigint") { o[f.name] = Number(v); continue; }
      if (typeof v === "object" && v !== null && "toNumber" in v) { o[f.name] = v.toNumber(); continue; }
      o[f.name] = v;
    }
    out.push(o);
  }
  return out;
}

async function run(sql, params) {
  await ready();
  return rows(await _conn.query(inline(sql, params)));
}

const byId = {};
for (const c of STATIC.meta.charts) byId[c.id] = c;

window.__duckbillDB__ = {
  savable: false,
  async meta() {
    const charts = STATIC.meta.charts.map(c => { const { sql, spark, ...rest } = c; return { ...rest, spark: !!spark }; });
    const markers = (STATIC.meta.markers || []).map(m => { const { sql, ...rest } = m; return rest; });
    return { title: STATIC.meta.title, params: STATIC.meta.params, charts, markers };
  },
  async runChart(chart, params) {
    const c = byId[chart.id]; if (!c || !c.sql) return { id: chart.id, rows: [] };
    try { return { id: chart.id, rows: await run(c.sql, coerce(STATIC.meta.params, params)) }; }
    catch (e) { return { id: chart.id, error: String(e) }; }
  },
  async runSpark(chart, params) {
    const c = byId[chart.id]; if (!c || !c.spark) return { id: chart.id, rows: [] };
    try { return { id: chart.id, rows: await run(c.spark, coerce(STATIC.meta.params, params)) }; }
    catch (e) { return { id: chart.id, error: String(e) }; }
  },
  async chartSql(chart, params) { const c = byId[chart.id]; return c && c.sql ? inline(c.sql, coerce(STATIC.meta.params, params)) : ""; },
  async markers(params) {
    const out = {}; const p = coerce(STATIC.meta.params, params);
    for (const m of (STATIC.meta.markers || [])) { try { out[m.id] = await run(m.sql, p); } catch (e) { out[m.id] = []; } }
    return out;
  },
  async schema() {
    const t = await run("SELECT table_schema, table_name, column_name FROM information_schema.columns WHERE table_schema NOT IN ('information_schema','pg_catalog') ORDER BY 1,2,3", {});
    const out = {}; for (const r of t) { const k = r.table_schema + "." + r.table_name; (out[k] = out[k] || []).push(r.column_name); } return out;
  },
  async ask(sql) {
    try { await ready(); const tbl = await _conn.query(sql); const all = rows(tbl); const cols = tbl.schema.fields.map(f => f.name);
      return { cols, rows: all.slice(0, 2000), truncated: all.length > 2000 }; }
    catch (e) { return { error: String(e) }; }
  },
  async questions() { return STATIC.meta.questions || []; },
  async docs() { return STATIC.meta.docs || { readme: "", tables: [] }; },
};
</script>
""".replace("__WASMVER__", DUCKDB_WASM_VERSION)


def build_static(dashboard_path: str, db_path: str, out_dir: str,
                 questions_dir: str | None = None) -> None:
    """Emit a multi-file static site under out_dir: index.html + data/<table>.parquet."""
    dashboard = load_dashboard(dashboard_path)
    warehouse = open_backend(db_path)
    if not warehouse.bundleable:
        raise ValueError(
            f"bundle --static requires a DuckDB or SQLite backend; {warehouse.dialect} "
            f"is serve-only -- it can't export the data as Parquet")
    if questions_dir is None:
        questions_dir = os.path.join(os.path.dirname(os.path.abspath(dashboard_path)), "questions")

    ref = referenced(dashboard, warehouse)
    print(f"exporting {len(ref)} of {len(warehouse.schema())} tables (pruned)...")
    names, raw = _collect_pruned_parquet(warehouse, ref)

    os.makedirs(os.path.join(out_dir, "data"), exist_ok=True)
    for name in names:
        with open(os.path.join(out_dir, "data", f"{name}.parquet"), "wb") as f:
            f.write(raw[name])

    meta = _meta_payload(dashboard, warehouse)
    meta["questions"] = QuestionStore(questions_dir).list()
    payload: dict[str, Any] = {"meta": meta, "tables": names, "dataDir": "./data"}

    page = _rewrite_vendor(PAGE)
    inject = (f'<script>window.__duckbillStatic__ = {json.dumps(payload)};</script>\n'
              + _wasm_db_js())
    # Inject before </head> so the module loads before the page's main script runs.
    html = page.replace("</head>", inject + "</head>", 1)
    # The loading overlay goes first in the body so it paints before the engine downloads.
    html = html.replace("<body>", "<body>" + _LOADING_OVERLAY, 1)
    with open(os.path.join(out_dir, "index.html"), "w") as f:
        f.write(html)

    total_mb = sum(len(raw[n]) for n in names) / 1e6
    print(f"wrote {out_dir}/ ({len(names)} tables, {total_mb:.1f} MB data)")
    print(f"serve it over http (NOT file://): e.g. `python -m http.server -d {out_dir}`")
