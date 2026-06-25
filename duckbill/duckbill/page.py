"""The dashboard page: static HTML + JS, served at `/`.

It fetches `/meta`, builds controls from the declared params, lays charts out by
section, and draws each by calling `/q`. Every interaction -- a control change, a
drill click, or a timespan brush -- sets a param and re-queries only the charts
that reference a changed param. Vega-Lite (from a CDN) renders the charts; a
chart may carry a raw Vega-Lite `spec` as an escape hatch.
"""

PAGE = r"""<!doctype html>
<html><head>
<meta charset="utf-8">
<title>duckbill</title>
<!-- Vega is served by the duckbill server itself (same-origin /vendor), not a CDN:
     the page must work behind a proxy/VPN that blocks or mangles third-party CDN
     scripts. The server fetches and caches these once; the browser never touches
     the CDN. The bundle strips these tags and embeds the libraries instead. -->
<script src="/vendor/vega.js"></script>
<script src="/vendor/vega-lite.js"></script>
<script src="/vendor/vega-embed.js"></script>
<!-- CodeMirror for the Ask editor. The `*` prefix externalizes every dep so the
     importmap resolves them to one shared copy (else @codemirror/state loads twice).
     Served through the server's /vendor/esm proxy, not a CDN, for the same reason as
     Vega: the page must work behind a proxy/VPN that blocks third-party scripts. -->
<script type="importmap">
{ "imports": {
  "@codemirror/state": "/vendor/esm/@codemirror/state@6.4.1",
  "@codemirror/view": "/vendor/esm/*@codemirror/view@6.26.3",
  "@codemirror/language": "/vendor/esm/*@codemirror/language@6.10.2",
  "@codemirror/commands": "/vendor/esm/*@codemirror/commands@6.6.0",
  "@codemirror/autocomplete": "/vendor/esm/*@codemirror/autocomplete@6.18.0",
  "@codemirror/lint": "/vendor/esm/*@codemirror/lint@6.8.1",
  "@codemirror/search": "/vendor/esm/*@codemirror/search@6.5.6",
  "@codemirror/lang-sql": "/vendor/esm/*@codemirror/lang-sql@6.7.0",
  "@lezer/common": "/vendor/esm/@lezer/common@1.2.1",
  "@lezer/highlight": "/vendor/esm/*@lezer/highlight@1.2.1",
  "@lezer/lr": "/vendor/esm/*@lezer/lr@1.4.2",
  "crelt": "/vendor/esm/crelt@1.0.6",
  "style-mod": "/vendor/esm/style-mod@4.1.2",
  "w3c-keyname": "/vendor/esm/w3c-keyname@2.2.8",
  "codemirror": "/vendor/esm/*codemirror@6.0.1"
}}
</script>
<style>
  body { font: 14px -apple-system, system-ui, sans-serif; margin: 0; background: #f6f7f9; color: #1c1e21; }
  header { background: #1c1e21; color: #fff; padding: 12px 20px; display: flex; gap: 18px; align-items: center; flex-wrap: wrap; position: sticky; top: 0; z-index: 20; }
  header h1 { font-size: 16px; margin: 0; font-weight: 600; letter-spacing: .3px; }
  header label { font-size: 12px; opacity: .85; display: inline-flex; gap: 6px; align-items: center; }
  select, input[type=datetime-local] { font: inherit; font-size: 12px; padding: 3px 6px; border-radius: 5px; border: 1px solid #444; background: #2a2d31; color: #fff; }
  .ts { display: inline-flex; gap: 4px; align-items: center; }
  .ts button { font: inherit; font-size: 12px; padding: 3px 8px; border-radius: 5px; border: 1px solid #444; background: #2a2d31; color: #ccc; cursor: pointer; }
  .ts button.on { background: #3b6fb0; color: #fff; border-color: #3b6fb0; }
  .ts .custom { margin-left: 6px; }
  #status { font-size: 12px; opacity: .7; margin-left: auto; }
  .grid { padding: 16px; }
  h2.sec { font-size: 14px; margin: 18px 4px 8px; display: flex; gap: 10px; align-items: baseline; }
  h2.sec .sub { font-weight: 400; color: #888; font-size: 12px; }
  .crumb { display: flex; gap: 12px; align-items: baseline; margin: 4px 4px 0; }
  .crumb a { color: #3b6fb0; text-decoration: none; font-size: 13px; font-weight: 600; }
  .crumb a:hover { text-decoration: underline; }
  .crumb .val { color: #555; font-size: 13px; font-variant-numeric: tabular-nums; }
  .crumb .pill { display: inline-flex; align-items: center; gap: 6px; background: #eef1f5; border: 1px solid #e0e4ea; border-radius: 999px; padding: 2px 6px 2px 10px; font-size: 12px; color: #374151; font-variant-numeric: tabular-nums; }
  .crumb .pill b { font-weight: 600; }
  .crumb .pill .x { color: #999; text-decoration: none; font-size: 15px; line-height: 1; padding: 0 2px; }
  .crumb .pill .x:hover { color: #c0392b; }
  .cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(460px, 1fr)); gap: 16px; }
  .card { position: relative; background: #fff; border: 1px solid #e3e5e8; border-radius: 8px; padding: 12px 14px; box-shadow: 0 1px 2px rgba(0,0,0,.04); }
  .card-expand { position: absolute; top: 7px; right: 7px; z-index: 2; border: 0; background: none; color: #aeb4bd; cursor: pointer; font-size: 14px; line-height: 1; padding: 3px 5px; border-radius: 5px; opacity: 0; transition: opacity .1s; }
  .card:hover .card-expand { opacity: 1; }
  .card-expand:hover { background: #eef1f5; color: #3b6fb0; }
  .modal { position: fixed; inset: 0; z-index: 50; display: flex; align-items: center; justify-content: center; }
  .modal-backdrop { position: absolute; inset: 0; background: rgba(20,22,26,.45); }
  .modal-panel { position: relative; background: #fff; border-radius: 10px; width: 80vw; height: 82vh; display: flex; flex-direction: column; box-shadow: 0 12px 44px rgba(0,0,0,.32); }
  .modal-head { display: flex; align-items: center; gap: 12px; padding: 11px 14px; border-bottom: 1px solid #eceef1; }
  .modal-head h3 { margin: 0; font-size: 15px; font-weight: 600; }
  .modal-tabs { display: flex; }
  .modal-tabs button { font: inherit; font-size: 12px; padding: 4px 12px; border: 1px solid #cfd4da; background: #fff; cursor: pointer; }
  .modal-tabs button:first-child { border-radius: 6px 0 0 6px; }
  .modal-tabs button:last-child { border-radius: 0 6px 6px 0; border-left: 0; }
  .modal-tabs button.on { background: #3b6fb0; color: #fff; border-color: #3b6fb0; }
  .modal-ask { margin-left: auto; font: inherit; font-size: 12px; padding: 4px 12px; border: 1px solid #cfd4da; border-radius: 6px; background: #fff; cursor: pointer; }
  .modal-ask:hover { border-color: #3b6fb0; color: #3b6fb0; }
  .modal-x { border: 0; background: none; font-size: 22px; line-height: 1; color: #999; cursor: pointer; padding: 0 2px; }
  .modal-x:hover { color: #c0392b; }
  .modal-body { flex: 1; overflow: auto; padding: 16px 18px; }
  .card h3 { font-size: 13px; margin: 0 0 8px; font-weight: 600; }
  .card > div { width: 100%; min-height: 60px; }
  .err { color: #b00020; font-size: 12px; white-space: pre-wrap; }
  /* hero metrics: a strip of big figures, one per result column */
  .metrics { display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 12px 24px; min-height: 0; padding: 2px; }
  .metric .mval { font-size: 30px; font-weight: 650; line-height: 1.1; font-variant-numeric: tabular-nums; }
  .metric .mlabel { font-size: 12px; color: #666; margin-top: 3px; }
  .metric .delta { font-size: 12px; font-weight: 600; margin-top: 4px; font-variant-numeric: tabular-nums; }
  .metric .delta.good { color: #2f8a4e; }
  .metric .delta.bad { color: #c0392b; }
  .metric .delta.neutral { color: #5a6b7b; }
  .metric .delta.flat { color: #999; }
  .metric .spark { width: 100%; height: 22px; display: block; margin: 5px 0 1px; }
  .metric .spark polyline { fill: none; stroke: #4f46e5; stroke-width: 1.25; vector-effect: non-scaling-stroke; }
  /* leaderboard: ranked rows with an inline magnitude bar behind each value */
  .lb { display: flex; flex-direction: column; gap: 1px; }
  .lbrow { position: relative; display: flex; align-items: center; gap: 8px; padding: 4px 7px; font-size: 12px; border-radius: 4px; }
  .lbrow::before { content: ""; position: absolute; left: 0; top: 2px; bottom: 2px; width: var(--pct, 0%); background: #ebedfb; border-radius: 4px; z-index: 0; }
  .lbrow > * { position: relative; z-index: 1; }
  .lbrow .lbl { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #374151; }
  .lbrow .lbv { font-variant-numeric: tabular-nums; font-weight: 600; }
  .lbrow .lbd { font-size: 11px; color: #888; font-variant-numeric: tabular-nums; }
  .lbrow.link { cursor: pointer; }
  .lbrow.link:hover::before { background: #dfe3fb; }
  .lbrow.link:hover .lbl { color: #3b46b0; }
  /* compare toggle sits in the timespan control */
  .ts .cmp { margin-left: 6px; }
  .ts .step { padding: 3px 7px; }
  /* table styling applies in cards and in the enlarge modal's Data view */
  .card table, .modal-body table { border-collapse: collapse; width: 100%; font-size: 12px; table-layout: auto; }
  .card th, .card td, .modal-body th, .modal-body td { text-align: left; padding: 3px 7px; border-bottom: 1px solid #eee; }
  .card th, .modal-body th { color: #666; font-weight: 600; white-space: nowrap; }
  /* numeric columns hug their content; text columns absorb the rest and clip. */
  .card td.num, .modal-body td.num { width: 1%; white-space: nowrap; text-align: right; font-variant-numeric: tabular-nums; }
  .card td:not(.num), .modal-body td:not(.num) { max-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .card td.link, .modal-body td.link { color: #3b6fb0; cursor: pointer; }
  .card td.link:hover, .modal-body td.link:hover { text-decoration: underline; }
  .card tbody tr:hover, .modal-body tbody tr:hover { background: #eef3f8; }
  nav#nav { display: flex; gap: 4px; }
  nav#nav a { font-size: 13px; color: #bbb; text-decoration: none; padding: 4px 10px; border-radius: 6px; }
  nav#nav a.on { background: #3b6fb0; color: #fff; }
  /* ---- Ask view ---- */
  .ask { display: grid; grid-template-columns: 220px 1fr; gap: 14px; align-items: start; }
  .ask-schema { background: #fff; border: 1px solid #e3e5e8; border-radius: 8px; padding: 8px 10px; max-height: 78vh; overflow: auto; font-size: 12px; }
  .ask-schema .t { font-weight: 600; cursor: pointer; padding: 3px 2px; }
  .ask-schema .t:hover { color: #3b6fb0; }
  .ask-schema .cols { margin: 0 0 6px 10px; display: none; }
  .ask-schema .cols.open { display: block; }
  .ask-schema .c { color: #555; cursor: pointer; padding: 1px 2px; }
  .ask-schema .c:hover { color: #3b6fb0; text-decoration: underline; }
  /* min-width:0 lets the grid/flex items shrink below the editor's content width
     so a long SQL line scrolls/wraps inside the editor instead of widening the page */
  .ask-main { display: flex; flex-direction: column; gap: 10px; min-width: 0; }
  .ask-editor-row { display: flex; gap: 10px; align-items: flex-start; }
  .ask-editor { flex: 1; min-width: 0; border: 1px solid #cfd4da; border-radius: 8px; background: #fff; }
  .cm-tooltip-autocomplete { z-index: 30; }
  .ask-editor .cm-editor { max-height: 320px; }
  .ask-editor textarea { width: 100%; box-sizing: border-box; min-height: 90px; border: 0; padding: 10px; font: 13px ui-monospace, SFMono-Regular, Menlo, monospace; resize: vertical; outline: none; }
  .ask-run { font: inherit; font-size: 13px; font-weight: 600; padding: 8px 16px; border-radius: 8px; border: 0; background: #2f8a4e; color: #fff; cursor: pointer; }
  .ask-bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; font-size: 12px; }
  .ask-bar .seg button { font: inherit; font-size: 12px; padding: 3px 9px; border: 1px solid #cfd4da; background: #fff; cursor: pointer; }
  .ask-bar .seg button:first-child { border-radius: 6px 0 0 6px; }
  .ask-bar .seg button:last-child { border-radius: 0 6px 6px 0; }
  .ask-bar .seg button.on { background: #3b6fb0; color: #fff; border-color: #3b6fb0; }
  .ask-bar label { color: #555; display: inline-flex; gap: 4px; align-items: center; }
  .ask-bar select { background: #fff; color: #1c1e21; border-color: #cfd4da; }
  .ask-bar .spacer { margin-left: auto; }
  .ask-bar .copy { font: inherit; font-size: 12px; padding: 3px 10px; border: 1px solid #cfd4da; border-radius: 6px; background: #fff; cursor: pointer; }
  .about { max-width: 880px; }
  .about .readme { line-height: 1.5; }
  .about .readme h1 { font-size: 20px; margin: 4px 0 12px; }
  .about .readme h2 { font-size: 16px; margin: 16px 0 8px; }
  .about .readme h3, .about .readme h4 { font-size: 14px; margin: 12px 0 6px; }
  .about .readme p { margin: 8px 0; }
  .about .readme ul { margin: 8px 0; padding-left: 22px; }
  .about .readme code { background: #eef1f5; padding: 0 4px; border-radius: 3px; font-size: 12px; }
  .about .readme pre { background: #f5f6f8; padding: 10px 12px; border-radius: 6px; overflow: auto; }
  .about .readme pre code { background: none; padding: 0; }
  .about section.card { margin-bottom: 14px; }
  .about section.card h3 { margin: 0 0 6px; font-size: 14px; }
  .about .tc { color: #555; margin: 0 0 8px; }
  .about td.ty { color: #888; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; white-space: nowrap; }
</style>
</head><body>
<header>
  <h1 id="title">duckbill</h1>
  <nav id="nav"><a href="#" data-view="dash">Dashboard</a><a href="#ask" data-view="ask">Ask</a><a href="#about" data-view="about">About</a></nav>
  <div id="controls" style="display:flex;gap:18px;align-items:center;flex-wrap:wrap"></div>
  <span id="status">loading&hellip;</span>
</header>
<div class="grid" id="grid"></div>

<script>
const PARAMS = {};      // current bind values, by name (timespan -> start/end)
let CHARTS = [], PARAMSPEC = [], TS = null;   // TS = the timespan param spec, if any
let MARKERSPEC = [], MARKERS = {};            // overlay defs + their current rows, by id
let WINDOW = { mode: "preset", preset: null }; // timespan UI state
let COMPARE = false;                            // overlay/delta vs the previous window

// Layout: every section is a "page". Sections not driven by a drill param are
// the home page; each drill param has a detail page (the section whose charts
// all reference it). The current page lives in the URL hash, so a drill is just
// a navigation and the browser back button works.
let ORDER = [], DRILL_PARAMS = new Set(), PARAM_SECTION = {}, HOME_SECTIONS = [];
let VISIBLE = [];                              // charts on the current page

// ---- timespan helpers ----------------------------------------------------
function windowMs(preset) {
  const n = parseInt(preset, 10), u = preset.slice(-1);
  return (u === "d" ? n * 24 : n) * 3600 * 1000;
}
function applyPreset(preset) {
  const end = new Date(), start = new Date(end - windowMs(preset));
  PARAMS.start = start.toISOString();
  PARAMS.end = end.toISOString();
  WINDOW = { mode: "preset", preset };
}
function applyCustom(startISO, endISO) {
  PARAMS.start = startISO; PARAMS.end = endISO;
  WINDOW = { mode: "custom", preset: null };
}
function localValue(iso) {                 // ISO -> datetime-local input value
  const d = new Date(iso), p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}
function syncTimespanUI() {
  if (!TS) return;
  document.querySelectorAll(".ts button[data-preset]").forEach((b) =>
    b.classList.toggle("on", WINDOW.mode === "preset" && b.dataset.preset === WINDOW.preset));
  const f = document.getElementById("ts-from"), t = document.getElementById("ts-to");
  if (f && t) { f.value = localValue(PARAMS.start); t.value = localValue(PARAMS.end); }
}

// ---- controls ------------------------------------------------------------
function buildControls() {
  const host = document.getElementById("controls");
  host.innerHTML = "";
  for (const p of PARAMSPEC) {
    if (p.control === "timespan") { host.appendChild(buildTimespan(p)); continue; }
    if (p.control === "select") { host.appendChild(buildSelect(p)); continue; }
    // control "none" (or unset): driven by drill/brush only -- no UI.
  }
}

// Show a control only when some chart on the current page references its
// param(s) -- e.g. a home-only filter hides on a detail page that ignores it.
function updateControls() {
  const referenced = new Set(VISIBLE.flatMap((c) => c.params));
  document.querySelectorAll("#controls [data-params]").forEach((el) => {
    el.style.display = el.dataset.params.split(",").some((p) => referenced.has(p)) ? "" : "none";
  });
}
function buildSelect(p) {
  const label = document.createElement("label");
  label.dataset.params = p.name;                   // shown only when a visible chart uses it
  label.textContent = (p.label || p.name) + " ";
  const sel = document.createElement("select");
  sel.id = "ctl-" + p.name;
  for (const c of (p.choices || [])) {
    const o = document.createElement("option");
    o.value = o.textContent = c;
    if (String(c) === String(PARAMS[p.name])) o.selected = true;
    sel.appendChild(o);
  }
  sel.addEventListener("change", () => { PARAMS[p.name] = sel.value; refresh([p.name]); });
  label.appendChild(sel);
  return label;
}
function buildTimespan(p) {
  const wrap = document.createElement("div");
  wrap.className = "ts";
  wrap.dataset.params = "start,end";               // shown only when a visible chart is windowed
  // step the window backward/forward by its own length
  const step = (dir) => {
    const s = new Date(PARAMS.start), e = new Date(PARAMS.end), d = e - s;
    if (!d) return;
    applyCustom(new Date(+s + dir * d).toISOString(), new Date(+e + dir * d).toISOString());
    syncTimespanUI(); refresh(["start", "end"]);
  };
  for (const [glyph, dir] of [["&lsaquo;", -1], ["&rsaquo;", 1]]) {
    const b = document.createElement("button");
    b.className = "step"; b.innerHTML = glyph; b.title = dir < 0 ? "previous window" : "next window";
    b.addEventListener("click", () => step(dir));
    wrap.appendChild(b);
  }
  for (const preset of (p.presets || [])) {
    const b = document.createElement("button");
    b.dataset.preset = preset; b.textContent = preset;
    b.addEventListener("click", () => { applyPreset(preset); syncTimespanUI(); refresh(["start", "end"]); });
    wrap.appendChild(b);
  }
  const custom = document.createElement("span");
  custom.className = "custom";
  custom.innerHTML = `<input type="datetime-local" id="ts-from"> &ndash; <input type="datetime-local" id="ts-to">`;
  wrap.appendChild(custom);
  const commit = () => {
    const f = document.getElementById("ts-from").value, t = document.getElementById("ts-to").value;
    if (f && t) { applyCustom(new Date(f).toISOString(), new Date(t).toISOString()); syncTimespanUI(); refresh(["start", "end"]); }
  };
  custom.querySelector("#ts-from").addEventListener("change", commit);
  custom.querySelector("#ts-to").addEventListener("change", commit);
  // compare toggle: overlay the previous equal-length window (and delta% on leaderboards)
  const cmp = document.createElement("button");
  cmp.className = "cmp"; cmp.textContent = "Compare"; cmp.title = "overlay the previous window";
  cmp.classList.toggle("on", COMPARE);
  cmp.addEventListener("click", () => { COMPARE = !COMPARE; cmp.classList.toggle("on", COMPARE); refresh(["start", "end"]); });
  wrap.appendChild(cmp);
  return wrap;
}

// ---- charts --------------------------------------------------------------
const encOf = (e) => Object.fromEntries(Object.entries(e).filter(([, v]) => v && v.field));

// A shared Vega-Lite config so every chart picks up the same calm styling: light
// horizontal gridlines only, muted axes/labels, one accent color, no chart border.
const ACCENT = "#4f46e5";
const THEME = {
  view: { stroke: null },
  font: "-apple-system, system-ui, sans-serif",
  axis: { gridColor: "#eef1f5", domainColor: "#dfe3e8", tickColor: "#dfe3e8",
          labelColor: "#6b7280", titleColor: "#6b7280", labelFontSize: 11,
          titleFontSize: 11, titleFontWeight: 500, titlePadding: 6 },
  axisX: { grid: false },
  legend: { labelColor: "#374151", titleColor: "#6b7280", labelFontSize: 11,
            titleFontSize: 11, symbolType: "circle" },
  mark: { color: ACCENT },
  range: { category: ["#4f46e5", "#0891b2", "#d97706", "#db2777", "#65a30d",
                      "#7c3aed", "#0d9488", "#dc2626"] },
};

// prevRows (when compare mode is on) overlays the previous window as a faded
// second series on a single-series line/area chart.
function vlSpec(chart, rows, prevRows, height) {
  const base = { data: { values: rows }, width: "container", height: height || 220,
                 background: null, config: THEME };
  let spec, barUnit = null;
  if (chart.spec) {                                  // raw Vega-Lite escape hatch
    spec = { ...base, ...chart.spec, data: { values: rows }, width: "container" };
  } else {
    const t = chart.type;
    let e = encOf(chart.encoding);
    if (t === "line" || t === "area") {
      e = withTimeAxis(e, chart, rows);              // readable UTC date/time x-axis
      if (prevRows && prevRows.length) {             // compare overlay: previous vs current
        rows = [...prevRows.map((r) => ({ ...r, _period: "previous" })),
                ...rows.map((r) => ({ ...r, _period: "current" }))];
        base.data = { values: rows };
        e = { ...e, color: { field: "_period", type: "nominal", legend: null,
                scale: { domain: ["previous", "current"], range: ["#c3c9d4", ACCENT] } },
              order: { field: "_period" } };
      }
      spec = { ...base, layer: hoverLayers(t, e) };  // line/area get a hover crosshair
    } else {
      let mark = { type: "point" };
      if (t === "bar" || t === "stacked-bar") {
        mark = { type: "bar" };
        // Time bars: a timeUnit makes x a band scale, so bars auto-fit their slot
        // (no pixel math). Markers must share the unit to stay on the same scale.
        const x = chart.encoding && chart.encoding.x;
        if (x && x.type === "temporal" && x.field) {
          barUnit = barTimeUnit(rows, x.field);
          if (barUnit) e = { ...e, x: { ...e.x, timeUnit: barUnit } };
        }
      } else {
        e = withTimeAxis(e, chart, rows);            // readable UTC date/time x-axis for scatter
      }
      mark.tooltip = true;                           // hover a mark to see its fields
      spec = { ...base, mark, encoding: e };
    }
  }
  // Interactive legend: click a legend entry to focus that series (others dim);
  // click again to clear, shift-click for several. This filters within the chart
  // and never drills. Skipped for `spec` charts, which own their encoding.
  const colorField = !chart.spec && chart.encoding && chart.encoding.color && chart.encoding.color.field;
  if (colorField) {
    const sel = { name: "series", select: { type: "point", fields: [colorField] }, bind: "legend" };
    const opacity = { condition: { param: "series", value: 1 }, value: 0.18 };
    if (spec.layer) spec.layer[0] = { ...spec.layer[0], params: [...(spec.layer[0].params || []), sel],
                                      encoding: { ...spec.layer[0].encoding, opacity } };
    else { spec.params = [...(spec.params || []), sel]; spec.encoding = { ...spec.encoding, opacity }; }
  }
  // Timespan brush: an x-interval selection whose extent re-queries the window.
  // On a layered spec it must ride a unit layer (the data layer), not the top.
  if (chart.brush === "timespan") {
    const bp = { name: "brush", select: { type: "interval", encodings: ["x"] } };
    if (spec.layer) spec.layer[0] = { ...spec.layer[0], params: [...(spec.layer[0].params || []), bp] };
    else spec.params = [...(spec.params || []), bp];
  }
  return applyMarkers(spec, chart, barUnit);
}

// A line/area chart plus a hover crosshair (Vega-Lite's canonical multi-series
// tooltip pattern): a transparent selector layer carries a nearest-x point
// selection on the x FIELD; a rule and the revealed points key their opacity off
// it, so a vertical cursor and per-series dots appear at the hovered x.
// Explicit tooltip channels (x, y, color) so the temporal x shows its time, not just the
// date: a UTC timeUnit renders date + hour:minute (`tooltip: true` would format it bare).
function tooltipFor(e) {
  const tt = [];
  if (e.x && e.x.field) {
    tt.push(e.x.type === "temporal"
      ? { field: e.x.field, type: "temporal", timeUnit: "utcyearmonthdatehoursminutes",
          title: e.x.title || e.x.field }
      : { field: e.x.field, type: e.x.type, title: e.x.title || e.x.field });
  }
  for (const ch of ["y", "color"]) {
    if (e[ch] && e[ch].field)
      tt.push({ field: e[ch].field, type: e[ch].type, title: e[ch].title || e[ch].field });
  }
  return tt;
}

function hoverLayers(t, e) {
  const baseMark = t === "line" ? { type: "line", point: false } : { type: "area" };
  const layers = [{ mark: baseMark, encoding: e }];
  if (e.x && e.x.field) {
    const tip = tooltipFor(e);                        // x shows date + time, not just the date
    const hover = { name: "hover", select: { type: "point", fields: [e.x.field], nearest: true,
                                             on: "pointerover", clear: "pointerout" } };
    const at = { condition: { param: "hover", empty: false, value: 1 }, value: 0 };
    layers.push({  // invisible selectors at every point (a voronoi makes the whole
      mark: { type: "point" },                        // plot hoverable); owns the selection and the tooltip
      encoding: { ...e, opacity: { value: 0 }, tooltip: tip },
      params: [hover],
    });
    layers.push({  // vertical cursor rule at the hovered x
      mark: { type: "rule", color: "#888" },
      encoding: { x: e.x, opacity: { condition: { param: "hover", empty: false, value: 0.5 }, value: 0 } },
    });
    layers.push({  // per-series dots + tooltip at the hovered x
      mark: { type: "point", size: 55, filled: true },
      encoding: { ...e, opacity: at, tooltip: tip },
    });
  }
  return layers;
}

// Pick the timeUnit for a temporal bar axis from the data's tightest gap, so each
// distinct timestamp gets its own band and Vega-Lite sizes the bars to fit. The
// unit equals the data granularity (hourly -> hours, daily -> date, ...).
function barTimeUnit(rows, field) {
  const ts = [...new Set(rows.map((r) => +new Date(r[field])))].filter((n) => !isNaN(n)).sort((a, b) => a - b);
  if (ts.length < 2) return null;
  let gap = Infinity;
  for (let i = 1; i < ts.length; i++) gap = Math.min(gap, ts[i] - ts[i - 1]);
  const s = gap / 1000;
  if (s >= 20 * 86400) return "utcyearmonth";
  if (s >= 20 * 3600) return "utcyearmonthdate";
  if (s >= 50 * 60) return "utcyearmonthdatehours";
  if (s >= 50) return "utcyearmonthdatehoursminutes";
  return "utcyearmonthdatehoursminutesseconds";
}

// A readable UTC time axis for a continuous temporal x: date-vs-time labels and day
// gridlines keyed to the data's granularity and span, so day boundaries are easy to pick
// out. Multi-day windows label one date per UTC day (the hover crosshair carries the exact
// time); a short intraday window labels times with a date at midnight.
function temporalXAxis(rows, field) {
  const ts = [...new Set(rows.map((r) => +new Date(r[field])))].filter((n) => !isNaN(n)).sort((a, b) => a - b);
  if (ts.length < 2) return null;
  let minGapH = Infinity;
  for (let i = 1; i < ts.length; i++) minGapH = Math.min(minGapH, (ts[i] - ts[i - 1]) / 3600000);
  // Daily-or-coarser data (e.g. pageviews): one date tick per UTC day. There is no sub-day
  // detail to show, and Vega would otherwise label these points with times of day.
  if (minGapH >= 20)
    return { grid: true, gridColor: "#eef1f5", gridDash: [2, 2],
             format: "%b %-d", tickCount: { interval: "day", step: 1 }, labelOverlap: true };
  // Intraday data: keep Vega's time-of-day ticks (its default shows the date at midnight and
  // the time elsewhere) but make day boundaries pop -- a darker gridline plus a bold, darker
  // date label at UTC midnight, faint elsewhere. Exact times stay on the axis.
  const mid = "utchours(datum.value)===0&&utcminutes(datum.value)===0";
  return {
    grid: true, gridDash: [2, 2],
    gridColor: { condition: { test: mid, value: "#c7ccd4" }, value: "#f1f3f5" },
    labelColor: { condition: { test: mid, value: "#374151" }, value: "#9ca3af" },
    labelFontWeight: { condition: { test: mid, value: 700 }, value: 400 },
  };
}

// Render a temporal x in UTC (matching the "(UTC)" axis titles) with temporalXAxis labels.
// No-op for non-temporal x or `spec` charts that own their encoding.
function withTimeAxis(e, chart, rows) {
  const x = chart.encoding && chart.encoding.x;
  if (chart.spec || !x || x.type !== "temporal" || !x.field) return e;
  const ax = temporalXAxis(rows, x.field);
  if (!ax) return e;
  return { ...e, x: { ...e.x, scale: { ...(e.x.scale || {}), type: "utc" },
                      axis: { ...(e.x.axis || {}), ...ax } } };
}

// Overlay marker rule layers (e.g. deploys) on a chart that opted in with
// `markers: true`. Each marker set becomes a layer with its own data, so the
// rules don't disturb the chart's own data.
function applyMarkers(spec, chart, timeUnit) {
  if (!chart.markers) return spec;
  const wanted = chart.markers === true ? MARKERSPEC
               : MARKERSPEC.filter((m) => [].concat(chart.markers).includes(m.id));
  const layers = wanted.filter((m) => (MARKERS[m.id] || []).length).map((m) => ({
    data: { values: MARKERS[m.id] },
    mark: { type: "rule", color: m.color, strokeDash: [3, 3] },
    encoding: {  // match the bars' timeUnit so the rule shares their band scale
      x: { field: m.field, type: "temporal", ...(timeUnit ? { timeUnit } : {}) },
      ...(m.label ? { tooltip: { field: m.label, type: "nominal" } } : {}),
    },
  }));
  if (!layers.length) return spec;
  // Pull mark/encoding/params off the top: a layered spec can't carry them, and
  // an interval selection must live on a unit layer (top-level + layer would
  // duplicate the brush_x signal). The brush rides on the data layer.
  const { mark, encoding, params, ...rest } = spec;
  const body = spec.layer ? spec.layer.slice() : [{ mark, encoding }];
  if (params) body[0] = { ...body[0], params };
  return { ...rest, layer: [...body, ...layers] };
}

const esc = (v) => String(v ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;")
  .replace(/>/g, "&gt;").replace(/"/g, "&quot;");

// Tables may declare drill as a { column: param | {param, value} } map: clicking
// a cell in that column navigates to the param's detail page. Columns whose name
// starts with "_" are data-only (e.g. a drill value) and aren't displayed.
function renderTable(host, rows, chart) {
  if (!rows.length) { host.innerHTML = "<div class='err'>no rows</div>"; return; }
  const drill = (chart && chart.drill) || {};
  const cols = Object.keys(rows[0]).filter((c) => !c.startsWith("_"));
  let h = "<table><thead><tr>" + cols.map((c) => `<th>${esc(c)}</th>`).join("") + "</tr></thead><tbody>";
  for (const r of rows) h += "<tr>" + cols.map((c) => {  // title reveals a clipped cell in full
    const d = drill[c];
    const value = d ? r[typeof d === "string" ? c : (d.value || c)] : null;
    const linkable = d && value != null && value !== "";  // skip rows with no drill value
    const cls = (typeof r[c] === "number" ? "num" : "") + (linkable ? " link" : "");
    if (linkable) {
      const param = typeof d === "string" ? d : d.param;
      return `<td class="${cls}" data-p="${esc(param)}" data-v="${esc(value)}" title="${esc(r[c])}">${esc(r[c])}</td>`;
    }
    return `<td class="${cls}" title="${esc(r[c])}">${esc(r[c])}</td>`;
  }).join("") + "</tr>";
  host.innerHTML = h + "</tbody></table>";
  host.querySelectorAll("td.link").forEach((td) => td.addEventListener("click",
    () => { location.hash = td.dataset.p + "=" + encodeURIComponent(td.dataset.v); }));
}

// Compact number: 30225 -> "30.2k", 1.2e6 -> "1.2M", 77 -> "77". Lowercase k for
// thousands (SI), uppercase M/B/T, so a strip of figures stays scannable.
function fmtCompact(v) {
  if (typeof v !== "number" || !isFinite(v)) return String(v);
  return new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 })
    .format(v).replace(/K$/, "k");
}

// The good direction for a figure: chart.good is "up"/"down"/"neutral" (applies
// to every figure) or a {label: direction} map; unlisted figures default to "up"
// (higher is better). "up"/"down" judge an increase as good or bad; "neutral"
// shows the change without a value judgment.
function goodDir(chart, label) {
  const g = chart.good;
  if (!g) return "up";
  return typeof g === "string" ? g : (g[label] || "up");
}

// A tiny inline-SVG sparkline from a numeric series, scaled to its own range.
function sparkline(values) {
  const nums = values.map(Number).filter((v) => isFinite(v));
  if (nums.length < 2) return "";
  const min = Math.min(...nums), max = Math.max(...nums), span = (max - min) || 1, w = 120, h = 22;
  const pts = nums.map((v, i) =>
    `${(i / (nums.length - 1) * w).toFixed(1)},${(h - 1 - (v - min) / span * (h - 2)).toFixed(1)}`).join(" ");
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"><polyline points="${pts}"/></svg>`;
}

// A metric chart's one-row result becomes a strip of big figures -- the column
// (SQL alias) is the label, the value is compacted, each figure shows an optional
// sparkline (from the chart's `spark` query, columns matched by name) and, when a
// previous-window row is supplied, its change as a good/bad-colored signed percent.
function renderMetric(host, rows, prevRows, sparkRows, chart) {
  if (!rows || !rows.length) { host.innerHTML = "<div class='err'>no rows</div>"; return; }
  const row = rows[0], prev = (prevRows && prevRows[0]) || null;
  const spk = (sparkRows && sparkRows.length) ? sparkRows : null;
  const cols = Object.keys(row).filter((c) => !c.startsWith("_"));
  host.innerHTML = `<div class="metrics">` + cols.map((c) => {
    const v = row[c], num = typeof v === "number";
    const val = v == null ? "-" : (num ? fmtCompact(v) : esc(String(v)));
    const spark = (num && spk && (c in spk[0])) ? sparkline(spk.map((r) => r[c])) : "";
    let delta = "";
    if (num && prev && typeof prev[c] === "number" && prev[c] !== 0) {
      const d = (v - prev[c]) / Math.abs(prev[c]) * 100;
      const dir = goodDir(chart, c);
      let cls;
      if (Math.abs(d) <= 0.05) cls = "flat";
      else if (dir === "neutral") cls = "neutral";
      else cls = ((dir === "down") ? d < 0 : d > 0) ? "good" : "bad";
      const txt = (d > 0 ? "+" : "") + (Math.round(d * 10) / 10) + "%";
      delta = `<div class="delta ${cls}" title="vs previous window">${txt}</div>`;
    }
    return `<div class="metric"><div class="mval" title="${esc(String(v))}">${val}</div>` +
           `${spark}<div class="mlabel">${esc(c)}</div>${delta}</div>`;
  }).join("") + `</div>`;
}

// A leaderboard: a ranked list of (label, value) rows with an inline magnitude bar
// behind each value, click-to-drill, and delta% vs the previous window in compare mode.
// label = first non-`_` text column; value = first numeric column.
function renderLeaderboard(host, rows, prevRows, chart) {
  if (!rows || !rows.length) { host.innerHTML = "<div class='err'>no rows</div>"; return; }
  const keys = Object.keys(rows[0]);
  const labelCol = keys.find((k) => !k.startsWith("_") && typeof rows[0][k] !== "number") || keys[0];
  const valCol = keys.find((k) => typeof rows[0][k] === "number") || keys[1];
  const max = Math.max(1, ...rows.map((r) => Math.abs(Number(r[valCol]) || 0)));
  const drill = chart.drill;
  const prevBy = {};
  if (prevRows) for (const r of prevRows) prevBy[r[labelCol]] = Number(r[valCol]);
  host.innerHTML = `<div class="lb">` + rows.map((r) => {
    const v = Number(r[valCol]) || 0;
    const pct = Math.min(100, Math.abs(v) / max * 100).toFixed(1);
    let delta = "";
    const p = prevBy[r[labelCol]];
    if (prevRows && typeof p === "number" && p !== 0) {
      const d = (v - p) / Math.abs(p) * 100;
      delta = `<span class="lbd">${d > 0 ? "+" : ""}${Math.round(d * 10) / 10}%</span>`;
    }
    const dv = drill ? r[drill.field] : null;
    const link = drill && dv != null && dv !== "";
    return `<div class="lbrow${link ? " link" : ""}"${link ? ` data-p="${esc(drill.param)}" data-v="${esc(dv)}"` : ""}` +
           ` style="--pct:${pct}%"><span class="lbl" title="${esc(r[labelCol])}">${esc(r[labelCol])}</span>` +
           `${delta}<span class="lbv">${fmtCompact(v)}</span></div>`;
  }).join("") + `</div>`;
  host.querySelectorAll(".lbrow.link").forEach((el) => el.addEventListener("click",
    () => { location.hash = el.dataset.p + "=" + encodeURIComponent(el.dataset.v); }));
}

// The window immediately before the current one (same length), for a metric's
// delta: prev = [start - (end-start), start]. Null if there's no active window.
function prevWindowParams() {
  if (!PARAMS.start || !PARAMS.end) return null;
  const s = new Date(PARAMS.start), e = new Date(PARAMS.end);
  if (isNaN(s) || isNaN(e) || e <= s) return null;
  return { ...PARAMS, start: new Date(s.getTime() - (e - s)).toISOString(), end: PARAMS.start };
}

async function loadChart(chart) {
  const host = document.getElementById("body-" + chart.id);
  try {
    const data = await DB.runChart(chart, PARAMS);
    if (data.error) { host.innerHTML = `<div class='err'>${data.error}</div>`; return; }
    const pw = chart.params.includes("start") ? prevWindowParams() : null;
    if (chart.type === "table") { renderTable(host, data.rows, chart); return; }
    if (chart.type === "metric") {                   // big-figure tiles + delta + sparkline
      let prevRows = null, sparkRows = null;
      if (pw) { try { const pd = await DB.runChart(chart, pw); if (!pd.error) prevRows = pd.rows; } catch (e) {} }
      if (chart.spark) { try { const sd = await DB.runSpark(chart, PARAMS); if (!sd.error) sparkRows = sd.rows; } catch (e) {} }
      renderMetric(host, data.rows, prevRows, sparkRows, chart);
      return;
    }
    if (chart.type === "leaderboard") {              // ranked rows + inline bar; delta% under compare
      let prevRows = null;
      if (COMPARE && pw) { try { const pd = await DB.runChart(chart, pw); if (!pd.error) prevRows = pd.rows; } catch (e) {} }
      renderLeaderboard(host, data.rows, prevRows, chart);
      return;
    }
    // compare overlay: previous window as a faded series on a single-series temporal line/area
    let prevRows = null;
    const xt = chart.encoding && chart.encoding.x;
    const singleSeries = !(chart.encoding && chart.encoding.color && chart.encoding.color.field);
    if (COMPARE && pw && (chart.type === "line" || chart.type === "area") && singleSeries
        && xt && xt.type === "temporal" && xt.field) {
      try {
        const pd = await DB.runChart(chart, pw);
        if (!pd.error) {
          const delta = new Date(PARAMS.end) - new Date(PARAMS.start), xf = xt.field;
          prevRows = pd.rows.map((r) => ({ ...r, [xf]: new Date(+new Date(r[xf]) + delta).toISOString() }));
        }
      } catch (e) {}
    }
    const res = await vegaEmbed(host, vlSpec(chart, data.rows, prevRows), { actions: false, renderer: "svg" });

    if (chart.drill) {                               // click a mark -> open its detail page
      res.view.addEventListener("click", (event, item) => {
        if (item && item.mark && /legend|axis/.test(item.mark.role || "")) return;  // legend click filters, not drills
        if (item && item.datum && item.datum[chart.drill.field] != null) {
          location.hash = chart.drill.param + "=" + encodeURIComponent(item.datum[chart.drill.field]);
        }
      });
    }
    if (chart.brush === "timespan") {                // brush x -> set the window
      let timer;
      res.view.addSignalListener("brush", (_, val) => {
        // The selection has one x key; a timeUnit renames it (e.g. utc..._hour),
        // so read the sole value rather than the raw field name. Extent is [lo,hi] ms.
        const ext = val ? Object.values(val)[0] : null;
        if (!ext || ext.length < 2) return;
        clearTimeout(timer);
        timer = setTimeout(() => {
          applyCustom(new Date(ext[0]).toISOString(), new Date(ext[ext.length - 1]).toISOString());
          syncTimespanUI();
          refresh(["start", "end"]);
        }, 300);
      });
    }
  } catch (e) {
    host.innerHTML = `<div class='err'>${e}</div>`;
  }
}

async function loadMarkers() {
  if (!MARKERSPEC.length) return;
  try {
    MARKERS = await DB.markers(PARAMS);
  } catch (e) { MARKERS = {}; }
}

// Re-query only the VISIBLE charts that reference a changed param; null => all
// of them. Confining to the current page keeps a control change from querying
// detail pages that aren't on screen.
async function refresh(changed) {
  const set = changed && changed.length ? new Set(changed) : null;
  const todo = set ? VISIBLE.filter((c) => c.params.some((p) => set.has(p))) : VISIBLE;
  document.getElementById("status").textContent = "querying...";
  // Markers can depend on the window; refresh them when it (or everything) changes.
  if (!set || set.has("start") || set.has("end")) await loadMarkers();
  await Promise.all(todo.map(loadChart));
  const win = WINDOW.mode === "preset" ? WINDOW.preset
            : `${localValue(PARAMS.start)} -> ${localValue(PARAMS.end)}`;
  document.getElementById("status").textContent = `${VISIBLE.length} charts | ${win}`;
}

// ---- routing -------------------------------------------------------------
// The hash is `#ask[=state]` for the Ask view, `#<param>=<value>` for a detail
// page, empty for home.
function currentView() {
  const h = location.hash.replace(/^#/, "");
  if (!h) return null;
  const i = h.indexOf("=");
  const key = i < 0 ? h : h.slice(0, i);
  const val = i < 0 ? "" : decodeURIComponent(h.slice(i + 1));
  if (key === "ask") return { ask: true, state: val };
  if (key === "q") return { ask: true, slug: val };  // a saved question by slug
  if (key === "about") return { about: true };
  if (!DRILL_PARAMS.has(key)) return null;
  return { param: key, value: val, section: PARAM_SECTION[key] };
}

function setNav(active) {
  document.querySelectorAll("#nav a").forEach((a) => a.classList.toggle("on", a.dataset.view === active));
}

function sectionCards(section) {
  const cards = CHARTS.filter((c) => c.section === section).map((c) => {
    // span: "full" -> whole row; an integer N -> N columns (clamped later to the
    // columns that actually fit, so it degrades to full width on a narrow window).
    let attr = "";
    if (c.span === "full") attr = ' style="grid-column:1/-1"';
    else if (c.span > 1) attr = ` data-span="${c.span}"`;
    return `<div class="card"${attr}><button class="card-expand" data-id="${c.id}" title="enlarge">&#x26F6;</button>` +
           `<h3>${c.title}</h3><div id="body-${c.id}"></div></div>`;
  }).join("");
  return `<section><h2 class="sec">${section}</h2><div class="cards">${cards}</div></section>`;
}

// Clamp each integer-span card to the number of columns its grid currently has,
// so `span: 3` never overflows a window only wide enough for two.
function clampSpans() {
  for (const cardsEl of document.querySelectorAll(".cards")) {
    const cols = getComputedStyle(cardsEl).gridTemplateColumns.split(" ").length;
    for (const card of cardsEl.querySelectorAll("[data-span]")) {
      card.style.gridColumn = "span " + Math.min(Number(card.dataset.span), cols);
    }
  }
}

async function render() {
  const view = currentView();
  const grid = document.getElementById("grid");
  if (view && view.ask) { setNav("ask"); return renderAsk(view); }
  if (view && view.about) { setNav("about"); return renderAbout(); }
  setNav("dash");
  document.getElementById("controls").style.display = "";
  let sections;
  if (view) {
    PARAMS[view.param] = view.value;
    sections = [view.section];
    grid.innerHTML =
      `<div class="crumb"><a href="#">&larr; ${ORDER[0]}</a>` +
      `<span class="pill"><b>${esc(view.param)}</b>: ${esc(view.value)}` +
      `<a href="#" class="x" title="clear">&times;</a></span></div>` +
      sections.map(sectionCards).join("");
  } else {
    sections = HOME_SECTIONS;
    grid.innerHTML = sections.map(sectionCards).join("");
  }
  VISIBLE = CHARTS.filter((c) => sections.includes(c.section));
  clampSpans();
  updateControls();
  window.scrollTo(0, 0);
  await refresh(null);
}

// Click a card's expand icon to enlarge it: a modal with the chart at full size,
// a Data tab for the raw rows, and "Open in Ask" to explore the query further.
async function openCard(chartId) {
  const chart = CHARTS.find((c) => c.id === chartId);
  if (!chart) return;
  const m = document.createElement("div");
  m.className = "modal";
  m.innerHTML =
    `<div class="modal-backdrop"></div>` +
    `<div class="modal-panel"><div class="modal-head"><h3>${esc(chart.title)}</h3>` +
    `<div class="modal-tabs"><button class="on" data-tab="chart">Chart</button><button data-tab="data">Data</button></div>` +
    `<button class="modal-ask" title="open this query in Ask">Open in Ask</button>` +
    `<button class="modal-x" title="close">&times;</button></div>` +
    `<div class="modal-body" id="modal-body"></div></div>`;
  document.body.appendChild(m);
  const body = m.querySelector("#modal-body");
  const onKey = (e) => { if (e.key === "Escape") close(); };
  const close = () => { m.remove(); document.removeEventListener("keydown", onKey); window.removeEventListener("hashchange", close); };
  document.addEventListener("keydown", onKey);
  window.addEventListener("hashchange", close);    // a drill (chart/table/leaderboard) navigates -> close the modal
  m.querySelector(".modal-backdrop").addEventListener("click", close);
  m.querySelector(".modal-x").addEventListener("click", close);

  let rows = null, timer;
  const ensureRows = async () => {
    if (rows !== null) return rows;
    const data = await DB.runChart(chart, PARAMS);
    rows = data.error ? { error: data.error } : data.rows;
    return rows;
  };
  const fail = (r) => r && r.error ? (body.innerHTML = `<div class='err'>${r.error}</div>`, true) : false;
  const showChart = async () => {
    body.innerHTML = "loading&hellip;";
    const r = await ensureRows(); body.innerHTML = ""; if (fail(r)) return;
    if (chart.type === "table") renderTable(body, r, chart);
    else if (chart.type === "leaderboard") renderLeaderboard(body, r, null, chart);
    else if (chart.type === "metric") renderMetric(body, r, null, null, chart);
    else {
      // Fit the whole chart -- plot, axes, and legend -- inside the modal body so it
      // doesn't scroll. Vega's default autosize ("pad") adds the chrome outside the
      // given size; explicit dims + autosize:fit shrink the plot to make room instead.
      const spec = vlSpec(chart, r, null);
      spec.width = Math.max(320, body.clientWidth - 36);
      spec.height = Math.max(280, body.clientHeight - 40);
      spec.autosize = { type: "fit", contains: "padding" };
      const res = await vegaEmbed(body, spec, { actions: false, renderer: "svg" });
      if (chart.drill) res.view.addEventListener("click", (event, item) => {  // enlarged charts drill too; the hashchange closes the modal
        if (item && item.mark && /legend|axis/.test(item.mark.role || "")) return;
        if (item && item.datum && item.datum[chart.drill.field] != null)
          location.hash = chart.drill.param + "=" + encodeURIComponent(item.datum[chart.drill.field]);
      });
      if (chart.brush === "timespan") res.view.addSignalListener("brush", (_, val) => {  // scrub to zoom the window
        const ext = val ? Object.values(val)[0] : null;
        if (!ext || ext.length < 2) return;
        clearTimeout(timer);
        timer = setTimeout(() => {
          applyCustom(new Date(ext[0]).toISOString(), new Date(ext[ext.length - 1]).toISOString());
          syncTimespanUI();
          rows = null; showChart();          // re-render the enlarged chart at the new window
          refresh(["start", "end"]);          // and the dashboard behind it
        }, 300);
      });
    }
  };
  const showData = async () => {
    body.innerHTML = "loading&hellip;";
    const r = await ensureRows(); body.innerHTML = ""; if (fail(r)) return;
    renderTable(body, r, null);                       // the raw rows, no drilling
  };
  m.querySelectorAll(".modal-tabs button").forEach((b) => b.addEventListener("click", () => {
    m.querySelectorAll(".modal-tabs button").forEach((x) => x.classList.toggle("on", x === b));
    (b.dataset.tab === "data" ? showData : showChart)();
  }));
  m.querySelector(".modal-ask").addEventListener("click", async () => {
    const sql = await DB.chartSql(chart, PARAMS);
    if (sql) { ASK.lastSql = sql; ASK.currentSlug = ""; close(); location.hash = "ask"; }
  });
  showChart();
}

// ---- Ask view (ad-hoc query) --------------------------------------------
const ASK = { cm: null, cols: [], rows: [], truncated: false, lastSql: "",
              saved: [], currentSlug: "",
              viz: { type: "table", x: "", y: "", color: "" } };
const DEFAULT_SQL = "SELECT route, count(*) AS n\nFROM warehouse.events\nGROUP BY 1 ORDER BY n DESC";

async function renderAsk(view) {
  if (ASK.cm) { try { ASK.cm.destroy(); } catch (e) {} ASK.cm = null; }
  document.getElementById("controls").style.display = "none";
  let sql = ASK.lastSql || DEFAULT_SQL;
  if (view && view.state) {  // a content link: #ask=<json>
    try { const s = JSON.parse(view.state); sql = s.sql || sql; ASK.viz = { ...ASK.viz, ...(s.viz || {}) }; } catch (e) {}
  }
  document.getElementById("grid").innerHTML = `
    <div class="ask">
      <aside class="ask-schema"><div id="ask-tables">loading schema&hellip;</div></aside>
      <div class="ask-main">
        <div class="ask-editor-row">
          <div id="ask-editor" class="ask-editor"></div>
          <button class="ask-run" id="ask-run">Run &#9656;</button>
        </div>
        <div class="ask-bar" id="ask-bar"></div>
        <div class="card"><div id="ask-result">Write a query and Run it (&#8984;/Ctrl+Enter).</div></div>
      </div>
    </div>`;
  document.getElementById("ask-run").addEventListener("click", runAsk);
  document.getElementById("status").textContent = "ask";
  const schema = await DB.schema();
  let docs = null; try { docs = await DB.docs(); } catch (e) {}
  renderSchemaSidebar(schema, docs);
  await setupEditor(schema, sql);
  await loadSavedList();                              // populates the Saved dropdown
  if (view && view.slug) loadSaved(view.slug);        // a saved-question link: #q=<slug>
  else if (view && view.state) runAsk();
}

async function loadSavedList() {
  try { ASK.saved = await DB.questions(); } catch (e) { ASK.saved = []; }
  renderVizBar();
}

async function saveQuestion() {
  const cur = ASK.saved.find((q) => q.slug === ASK.currentSlug);
  const name = prompt("Save question as:", cur ? cur.name : "");
  if (!name) return;
  const rec = await DB.saveQuestion({ name, sql: askSql(), viz: ASK.viz });
  if (rec.error) { alert(rec.error); return; }
  ASK.currentSlug = rec.slug;
  await loadSavedList();
}

function loadSaved(slug) {
  const q = ASK.saved.find((x) => x.slug === slug);
  if (!q) return;
  ASK.currentSlug = slug;
  ASK.viz = { type: "table", x: "", y: "", color: "", ...(q.viz || {}) };
  setEditorText(q.sql || "");
  runAsk();
}

function setEditorText(sql) {
  if (ASK.cm) ASK.cm.dispatch({ changes: { from: 0, to: ASK.cm.state.doc.length, insert: sql } });
  else { const ta = document.getElementById("ask-ta"); if (ta) ta.value = sql; }
}

async function deleteCurrent() {
  if (!ASK.currentSlug) return;
  const q = ASK.saved.find((x) => x.slug === ASK.currentSlug);
  if (!confirm(`Delete "${q ? q.name : ASK.currentSlug}"?`)) return;
  await DB.deleteQuestion(ASK.currentSlug);
  ASK.currentSlug = "";
  await loadSavedList();
}

function renderSchemaSidebar(schema, docs) {
  // Index any COMMENTs so we can hang them off the tables/columns as tooltips.
  const tcom = {}, ccom = {};
  for (const t of (docs && docs.tables) || []) {
    if (t.comment) tcom[t.name] = t.comment;
    for (const c of t.columns) if (c.comment) ccom[t.name + "." + c.name] = c.comment;
  }
  const ti = (s) => (s ? ` title="${esc(s)}"` : "");
  const host = document.getElementById("ask-tables");
  host.innerHTML = Object.entries(schema).map(([t, cols], i) =>
    `<div><div class="t" data-t="${esc(t)}" data-i="${i}"${ti(tcom[t])}>${esc(t)}</div>` +
    `<div class="cols" id="cols-${i}">` +
    cols.map((c) => `<div class="c" data-ins="${esc(c)}"${ti(ccom[t + "." + c])}>${esc(c)}</div>`).join("") + `</div></div>`).join("");
  host.querySelectorAll(".t").forEach((el) => el.addEventListener("click", () => {
    document.getElementById("cols-" + el.dataset.i).classList.toggle("open");
    insertAtCursor(el.dataset.t);
  }));
  host.querySelectorAll(".c").forEach((el) => el.addEventListener("click", () => insertAtCursor(el.dataset.ins)));
}

async function setupEditor(schema, initialSql) {
  const host = document.getElementById("ask-editor");
  // Run on Cmd/Ctrl+Enter. Capture phase so it fires before CodeMirror's own
  // keymap consumes the key; covers the textarea fallback too.
  host.addEventListener("keydown", (ev) => {
    if ((ev.metaKey || ev.ctrlKey) && ev.key === "Enter") { ev.preventDefault(); ev.stopPropagation(); runAsk(); }
  }, true);
  const cmSchema = {};
  for (const [t, cols] of Object.entries(schema)) {
    cmSchema[t] = cols;                              // qualified, e.g. warehouse.events
    const bare = t.split(".").pop();
    if (!(bare in cmSchema)) cmSchema[bare] = cols;  // also unqualified
  }
  try {
    const [{ basicSetup }, { EditorView }, { sql, MySQL }] = await Promise.all([
      import("codemirror"), import("@codemirror/view"), import("@codemirror/lang-sql"),
    ]);
    ASK.cm = new EditorView({
      parent: host, doc: initialSql,
      extensions: [basicSetup, EditorView.lineWrapping,
                   sql({ dialect: MySQL, schema: cmSchema, upperCaseKeywords: true })],
    });
  } catch (e) {                                      // fall back to a plain textarea
    host.innerHTML = `<textarea id="ask-ta" spellcheck="false"></textarea>`;
    host.querySelector("textarea").value = initialSql;
  }
}

function askSql() {
  if (ASK.cm) return ASK.cm.state.doc.toString();
  const ta = document.getElementById("ask-ta");
  return ta ? ta.value : "";
}

function insertAtCursor(text) {
  if (ASK.cm) {
    const pos = ASK.cm.state.selection.main.head;
    ASK.cm.dispatch({ changes: { from: pos, insert: text }, selection: { anchor: pos + text.length } });
    ASK.cm.focus();
  } else {
    const ta = document.getElementById("ask-ta");
    if (!ta) return;
    const s = ta.selectionStart, e = ta.selectionEnd;
    ta.value = ta.value.slice(0, s) + text + ta.value.slice(e);
    ta.selectionStart = ta.selectionEnd = s + text.length;
    ta.focus();
  }
}

async function runAsk() {
  const host = document.getElementById("ask-result");
  const sql = askSql();
  ASK.lastSql = sql;
  host.innerHTML = "running&hellip;";
  try {
    const data = await DB.ask(sql);
    if (data.error) { host.innerHTML = `<div class='err'>${esc(data.error)}</div>`; document.getElementById("ask-bar").innerHTML = ""; ASK.rows = []; return; }
    ASK.cols = data.cols; ASK.rows = data.rows; ASK.truncated = data.truncated;
    if (!ASK.cols.includes(ASK.viz.x)) ASK.viz.x = ASK.cols[0] || "";
    if (!ASK.cols.includes(ASK.viz.y))
      ASK.viz.y = ASK.cols.find((c) => typeof (ASK.rows[0] || {})[c] === "number") || ASK.cols[1] || ASK.cols[0] || "";
    renderVizBar();
    renderAskResult();
    document.getElementById("status").textContent = `${ASK.rows.length}${ASK.truncated ? "+" : ""} rows`;
  } catch (e) {
    host.innerHTML = `<div class='err'>${esc(e)}</div>`;
  }
}

function renderVizBar() {
  const bar = document.getElementById("ask-bar");
  if (!bar) return;
  const types = ["table", "bar", "stacked-bar", "line", "area", "point"];
  const opt = (sel, blank) => (blank ? `<option value="">${blank}</option>` : "") +
    ASK.cols.map((c) => `<option ${c === sel ? "selected" : ""}>${esc(c)}</option>`).join("");
  const hasResult = ASK.rows.length > 0;
  const enc = hasResult && ASK.viz.type !== "table";
  const vizControls = !hasResult ? "" :
    `<div class="seg">` + types.map((t) => `<button data-t="${t}" class="${t === ASK.viz.type ? "on" : ""}">${t}</button>`).join("") + `</div>` +
    (enc ? `<label>x <select id="vz-x">${opt(ASK.viz.x)}</select></label>` +
           `<label>y <select id="vz-y">${opt(ASK.viz.y)}</select></label>` +
           `<label>color <select id="vz-c">${opt(ASK.viz.color, "none")}</select></label>` : "");
  const writable = DB.savable;
  const saved = (ASK.saved.length || writable ?
    `<label>Saved <select id="ask-saved"><option value="">&mdash; open &mdash;</option>` +
    ASK.saved.map((q) => `<option value="${esc(q.slug)}" ${q.slug === ASK.currentSlug ? "selected" : ""}>${esc(q.name)}</option>`).join("") +
    `</select></label>` : "") +
    (writable ? `<button class="copy" id="ask-del" title="delete the open question"${ASK.currentSlug ? "" : " disabled"}>&times;</button>` +
                `<button class="copy" id="ask-save">Save</button>` : "");
  bar.innerHTML = vizControls + `<span class="spacer"></span>` + saved +
    `<button class="copy" id="ask-copy">Copy link</button>`;
  bar.querySelectorAll(".seg button").forEach((b) =>
    b.addEventListener("click", () => { ASK.viz.type = b.dataset.t; renderVizBar(); renderAskResult(); }));
  for (const [id, key] of [["vz-x", "x"], ["vz-y", "y"], ["vz-c", "color"]]) {
    const el = document.getElementById(id);
    if (el) el.addEventListener("change", () => { ASK.viz[key] = el.value; renderAskResult(); });
  }
  const on = (id, ev, fn) => { const el = document.getElementById(id); if (el) el.addEventListener(ev, fn); };
  on("ask-saved", "change", (ev) => loadSaved(ev.target.value));
  on("ask-del", "click", deleteCurrent);
  on("ask-save", "click", saveQuestion);
  on("ask-copy", "click", copyLink);
}

function inferType(rows, field) {
  for (const r of rows) {
    const v = r[field];
    if (v == null) continue;
    if (typeof v === "number") return "quantitative";
    if (typeof v === "string" && /^\d{4}-\d\d-\d\dT/.test(v)) return "temporal";
    return "nominal";
  }
  return "nominal";
}

function renderAskResult() {
  const host = document.getElementById("ask-result");
  if (!ASK.rows.length) { host.innerHTML = "<div class='err'>no rows</div>"; return; }
  if (ASK.viz.type === "table") { renderTable(host, ASK.rows, null); return; }
  const e = { x: { field: ASK.viz.x, type: inferType(ASK.rows, ASK.viz.x), title: ASK.viz.x },
              y: { field: ASK.viz.y, type: inferType(ASK.rows, ASK.viz.y), title: ASK.viz.y } };
  if (ASK.viz.type === "stacked-bar") e.y.stack = "zero";
  if (ASK.viz.color) e.color = { field: ASK.viz.color, type: inferType(ASK.rows, ASK.viz.color) };
  const chart = { id: "ask", type: ASK.viz.type, encoding: e };
  vegaEmbed(host, vlSpec(chart, ASK.rows), { actions: false, renderer: "svg" })
    .catch((err) => host.innerHTML = `<div class='err'>${esc(err)}</div>`);
}

function copyLink() {
  const url = location.origin + location.pathname +
    "#ask=" + encodeURIComponent(JSON.stringify({ sql: askSql(), viz: ASK.viz }));
  navigator.clipboard.writeText(url).then(() => {
    const b = document.getElementById("ask-copy"), t = b.textContent;
    b.textContent = "Copied!"; setTimeout(() => { b.textContent = t; }, 1200);
  });
}

// ---- About view (warehouse docs) ----------------------------------------
async function renderAbout() {
  document.getElementById("controls").style.display = "none";
  document.getElementById("status").textContent = "about";
  const grid = document.getElementById("grid");
  grid.innerHTML = `<div class="about"><div class="card">loading&hellip;</div></div>`;
  const { readme, tables } = await DB.docs();
  const ref = (tables || []).map((t) => {
    const cols = t.columns.map((c) =>
      `<tr><td><code>${esc(c.name)}</code></td><td class="ty">${esc(c.type)}</td><td>${esc(c.comment || "")}</td></tr>`).join("");
    return `<section class="card"><h3><code>${esc(t.name)}</code></h3>` +
      (t.comment ? `<p class="tc">${esc(t.comment)}</p>` : "") +
      `<table><thead><tr><th>column</th><th>type</th><th>description</th></tr></thead><tbody>${cols}</tbody></table></section>`;
  }).join("");
  grid.innerHTML = `<div class="about">` +
    (readme && readme.trim() ? `<div class="card readme">${mdToHtml(readme)}</div>` : "") +
    `<h2 class="sec">Schema</h2>` + ref + `</div>`;
  window.scrollTo(0, 0);
}

// A small Markdown -> HTML renderer (headings, lists, code, inline emphasis,
// links) -- enough for a warehouse README, with no dependency.
function mdToHtml(md) {
  const inline = (s) => esc(s)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^_])_([^_]+)_/g, "$1<em>$2</em>")
    .replace(/\[([^\]]+)\]\((https?:[^)]+)\)/g, '<a href="$2">$1</a>');
  // Block-based: blank lines separate blocks, soft-wrapped lines within a block
  // join with a space (so a hard-wrapped paragraph or list item stays one block).
  const lines = md.replace(/\r\n?/g, "\n").split("\n");
  const out = []; const isBullet = (s) => /^\s*[-*]\s+/.test(s);
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i++; continue; }
    if (line.trim().startsWith("```")) {                     // fenced code
      const buf = []; i++;
      while (i < lines.length && !lines[i].trim().startsWith("```")) { buf.push(lines[i]); i++; }
      i++;
      out.push("<pre><code>" + esc(buf.join("\n")) + "</code></pre>");
      continue;
    }
    const h = line.match(/^(#{1,4})\s+(.*)/);                // heading
    if (h) { out.push(`<h${h[1].length}>${inline(h[2])}</h${h[1].length}>`); i++; continue; }
    if (isBullet(line)) {                                     // list block
      const items = [];
      while (i < lines.length && lines[i].trim()) {
        const m = lines[i].match(/^\s*[-*]\s+(.*)/);
        if (m) items.push(m[1]);
        else if (items.length) items[items.length - 1] += " " + lines[i].trim();
        i++;
      }
      out.push("<ul>" + items.map((t) => "<li>" + inline(t) + "</li>").join("") + "</ul>");
      continue;
    }
    const buf = [];                                          // paragraph block
    while (i < lines.length && lines[i].trim() && !isBullet(lines[i]) &&
           !/^#{1,4}\s/.test(lines[i]) && !lines[i].trim().startsWith("```")) {
      buf.push(lines[i].trim()); i++;
    }
    out.push("<p>" + inline(buf.join(" ")) + "</p>");
  }
  return out.join("\n");
}

async function init() {
  const meta = await DB.meta();
  document.getElementById("title").textContent = meta.title;
  document.title = meta.title;
  PARAMSPEC = meta.params; CHARTS = meta.charts; MARKERSPEC = meta.markers || [];
  TS = PARAMSPEC.find((p) => p.control === "timespan") || null;

  for (const p of PARAMSPEC) {                       // seed PARAMS from defaults
    if (p.control === "timespan") applyPreset(p.default || (p.presets && p.presets[p.presets.length - 1]) || "24h");
    else PARAMS[p.name] = p.default ?? "";
  }
  buildControls();
  syncTimespanUI();

  // Work out the page layout: section order, drill params, and which section is
  // each drill param's detail page (the one whose charts all reference it).
  for (const c of CHARTS) if (!ORDER.includes(c.section)) ORDER.push(c.section);
  DRILL_PARAMS = new Set();
  for (const c of CHARTS) {
    if (!c.drill) continue;
    if (c.type === "table") for (const k in c.drill)
      DRILL_PARAMS.add(typeof c.drill[k] === "string" ? c.drill[k] : c.drill[k].param);
    else DRILL_PARAMS.add(c.drill.param);
  }
  for (const s of ORDER) {
    const inSec = CHARTS.filter((c) => c.section === s);
    const owner = [...DRILL_PARAMS].find((p) => inSec.length && inSec.every((c) => c.params.includes(p)));
    if (owner) PARAM_SECTION[owner] = s; else HOME_SECTIONS.push(s);
  }

  window.addEventListener("hashchange", render);
  // delegate the per-card expand clicks (the grid is rebuilt each render)
  document.getElementById("grid").addEventListener("click", (e) => {
    const b = e.target.closest(".card-expand");
    if (b) openCard(b.dataset.id);
  });
  await render();
}

// ---- backends ------------------------------------------------------------
// The page talks to a DB object, not the network. ServerDB hits the duckbill
// server -- both the live `serve` command and a standalone `uv run` bundle, which
// is just a self-contained server.
let DB;

const ServerDB = {
  savable: true,
  async meta() { return (await fetch("/meta")).json(); },
  async runChart(chart, params) { return (await fetch("/q?" + new URLSearchParams({ chart: chart.id, ...params }))).json(); },
  async runSpark(chart, params) { return (await fetch("/q?" + new URLSearchParams({ chart: chart.id, spark: "1", ...params }))).json(); },
  async chartSql(chart, params) { const r = await (await fetch("/sql?" + new URLSearchParams({ chart: chart.id }))).json(); return r.sql ? substParams(r.sql, params) : ""; },
  async markers(params) { return (await fetch("/markers?" + new URLSearchParams(params))).json(); },
  async schema() { return (await fetch("/schema")).json(); },
  async ask(sql) { return (await fetch("/ask", { method: "POST", body: JSON.stringify({ sql }) })).json(); },
  async questions() { return (await fetch("/questions")).json(); },
  async saveQuestion(rec) { return (await fetch("/questions", { method: "POST", body: JSON.stringify(rec) })).json(); },
  async deleteQuestion(slug) { return (await fetch("/questions/delete", { method: "POST", body: JSON.stringify({ slug }) })).json(); },
  async docs() { return (await fetch("/docs")).json(); },
};

// Inline a `$param` into SQL for display (ServerDB.chartSql): numbers raw, else
// single-quoted. The dashboard SQL already carries ::TYPE casts.
function substParams(sql, params) {
  return sql.replace(/\$([a-zA-Z_][a-zA-Z0-9_]*)/g, (m, name) => {
    if (!(name in params)) return m;
    const v = params[name];
    return typeof v === "number" ? String(v) : "'" + String(v).replace(/'/g, "''") + "'";
  });
}

async function main() {
  DB = window.__duckbillDB__ || ServerDB;
  await init();
}
main();
</script>
</body></html>
"""
