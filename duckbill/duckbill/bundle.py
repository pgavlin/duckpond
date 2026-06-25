"""Vendored front-end assets, shared by the live server and the bundler.

The dashboard page loads Vega and the CodeMirror editor from the server's own
/vendor endpoint rather than a CDN, so it works behind a proxy/VPN that blocks
third-party scripts. These helpers fetch those assets once and cache them under
~/.cache/duckbill/vendor. The pinned versions are also read by the standalone
server bundle (server_bundle.py) to fill its embedded /vendor proxy.

`bundle` produces one artifact: a single-file `uv run` server script (see
`build_server`, re-exported below).
"""

import os
import re
import subprocess

CDN = "https://cdn.jsdelivr.net/npm"
ESM_CDN = "https://esm.sh"
CACHE = os.path.expanduser("~/.cache/duckbill/vendor")

# Vega libraries, pinned to exact versions. Floating tags (vega@5) let the three
# resolve to a mismatched trio when a proxy/CDN serves them inconsistently, and a
# version can drift under the dashboard without notice. The server serves these
# same files at /vendor (see VENDOR), so the page never loads them from a CDN.
VEGA_VERSION = "5.33.1"
VEGALITE_VERSION = "5.23.0"
VEGAEMBED_VERSION = "6.29.0"

# Served by the server at /vendor/<name> (live server and bundle alike).
VENDOR = {
    "vega.js": f"{CDN}/vega@{VEGA_VERSION}/build/vega.min.js",
    "vega-lite.js": f"{CDN}/vega-lite@{VEGALITE_VERSION}/build/vega-lite.min.js",
    "vega-embed.js": f"{CDN}/vega-embed@{VEGAEMBED_VERSION}/build/vega-embed.min.js",
}


def _fetch(url: str) -> bytes:
    # Shell out to curl: it uses the system trust store, avoiding the cert issues
    # the bundled python.org Python hits on macOS.
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, re.sub(r"[^A-Za-z0-9]+", "_", url))
    if not os.path.exists(path):
        subprocess.run(["curl", "-fsSL", url, "-o", path], check=True)
    with open(path, "rb") as f:
        return f.read()


def vendor_js(name: str) -> bytes:
    """Bytes of a vendored Vega library by its /vendor name, fetched + cached once.
    Raises KeyError for an unknown name."""
    return _fetch(VENDOR[name])


def vendor_esm(path: str, query: str = "") -> bytes:
    """Fetch an esm.sh module (the CodeMirror graph) and rewrite its rooted import
    paths to /vendor/esm, so the whole module graph loads same-origin instead of
    from the CDN. Bare specifiers (e.g. `@codemirror/state`) are left untouched --
    the importmap resolves them, which keeps one shared copy of each package."""
    url = f"{ESM_CDN}/{path}" + (f"?{query}" if query else "")
    js = _fetch(url).decode("utf-8", "replace")
    # Rooted import/export sources (`/@codemirror/...`, `/*codemirror@...`) -> route
    # back through this server. The negative lookahead leaves `//` (protocol-relative)
    # alone; bare specifiers don't start with `/` so they are never matched.
    js = re.sub(r'(from|import)("\s*)/(?!/)', r'\1\2/vendor/esm/', js)
    js = re.sub(r'(from|import)(\s+")/(?!/)', r'\1\2/vendor/esm/', js)
    js = re.sub(r'import\(\s*"/(?!/)', 'import("/vendor/esm/', js)
    js = js.replace(f'"{ESM_CDN}/', '"/vendor/esm/')
    return js.encode("utf-8")


# The bundler's one output: a single-file `uv run` server script. Re-exported here
# so callers have one bundler import surface.
from .server_bundle import build_server as build_server  # noqa: E402
