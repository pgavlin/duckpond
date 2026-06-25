"""Generic keyless HTTP + clock helpers shared by every source.

`fetch_json` is a plain GET+JSON; `_now` is the upper bound for ingested timestamps.
Both are the test's monkeypatch seams -- sources call them through this module so a
single patch of `sources._http` is seen everywhere.
"""
import json
import time
import urllib.request
from typing import Any


def _now() -> int:
    """Upper bound for ingested timestamps (test override point)."""
    return int(time.time())


def fetch_json(url: str) -> dict[str, Any]:
    """GET a URL and parse JSON (test override point)."""
    req = urllib.request.Request(url, headers={"User-Agent": "duckpond-example/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.load(resp)
    if not isinstance(data, dict):
        raise ValueError(f"expected a JSON object from {url}")
    return data
