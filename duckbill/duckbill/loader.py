"""Load a dashboard module from a file path.

A dashboard is an ordinary Python file that defines, at module level:
  - `charts`: list of chart dicts (required)
  - `params`: list of param dicts (optional)
  - `title`:  page title (optional)

It's plain Python, so the author can compute SQL, share fragments, and build the
lists however they like -- only the resulting data matters.
"""

import importlib.util
import os

from .core import Dashboard


class DashboardError(Exception):
    pass


def load_dashboard(path: str) -> Dashboard:
    """Import `path` and return a validated Dashboard."""
    path = os.path.abspath(path)
    if not os.path.exists(path):
        raise DashboardError(f"no such dashboard file: {path}")
    spec = importlib.util.spec_from_file_location("duckbill_dashboard", path)
    if spec is None or spec.loader is None:
        raise DashboardError(f"cannot load dashboard module from {path}")
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception as e:
        raise DashboardError(f"failed to import {path}: {e}") from e

    charts = getattr(mod, "charts", None)
    if charts is None:
        raise DashboardError(f"{path} defines no `charts` list")
    params = getattr(mod, "params", None) or []
    markers = getattr(mod, "markers", None) or []
    title = getattr(mod, "title", None) or os.path.splitext(os.path.basename(path))[0]
    readme = getattr(mod, "readme", "") or ""
    try:
        return Dashboard(charts, params, title, markers, readme)
    except ValueError as e:
        raise DashboardError(str(e)) from e
