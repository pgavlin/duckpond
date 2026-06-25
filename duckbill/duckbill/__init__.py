"""duckbill -- live, query-backed dashboards over a local DuckDB.

A dashboard is a plain Python module that defines `charts` (and optionally
`params` and `title`) as data. The server runs each chart's SQL per request, so
the page is live: it re-queries on every interaction and reflects the current
warehouse.
"""

from .core import Dashboard, Warehouse, params_in
from .loader import DashboardError, load_dashboard
from .server import serve

__all__ = ["Dashboard", "Warehouse", "params_in", "load_dashboard",
           "DashboardError", "serve"]
__version__ = "0.1.0"
