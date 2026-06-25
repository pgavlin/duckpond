"""open_backend: pick a Backend from a DSN, expanding ${VAR} from the environment.

A bare path (no scheme) is treated as a DuckDB file (`--db /x.duckdb`).
Network drivers are imported lazily inside their module, so a missing extra
errors only when that backend is selected.
"""

import os
import re
from urllib.parse import urlparse

from .base import Backend as Backend  # re-exported for callers

_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _expand(dsn: str) -> str:
    return _VAR.sub(lambda m: os.environ.get(m.group(1), ""), dsn)


def open_backend(dsn: str, *, read_only: bool = True, pool: int = 4) -> Backend:
    dsn = _expand(dsn)
    scheme = urlparse(dsn).scheme

    if scheme in ("", "duckdb", "file"):
        from .duckdb import DuckDBBackend
        path = dsn
        if scheme:
            path = dsn.split("://", 1)[1]
        return DuckDBBackend(path, read_only=read_only)

    if scheme == "sqlite":
        from .sqlite import SQLiteBackend
        return SQLiteBackend(dsn.split("://", 1)[1], read_only=read_only)

    if scheme in ("postgres", "postgresql"):
        from .postgres import PostgresBackend
        return PostgresBackend(dsn, read_only=read_only, pool=pool)

    if scheme == "mysql":
        from .mysql import MySQLBackend
        return MySQLBackend(dsn, read_only=read_only, pool=pool)

    if scheme == "snowflake":
        from .snowflake import SnowflakeBackend
        return SnowflakeBackend(dsn, read_only=read_only, pool=pool)

    raise ValueError(f"unknown backend scheme {scheme!r} in {dsn!r}")
