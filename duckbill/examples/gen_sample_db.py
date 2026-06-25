"""Generate a synthetic warehouse for the example dashboard.

    python examples/gen_sample_db.py            # writes examples/sample.duckdb
    python examples/gen_sample_db.py -o /tmp/x.duckdb

Builds a small, fabricated web-service access log -- two tables, `events` (one row
per HTTP request) and `deploys` (a handful of releases, for the dashboard's
markers) -- so `duckbill serve examples/web_service.py --db examples/sample.duckdb`
runs out of the box. The data is random but seeded, so re-running is deterministic.
The tables carry DuckDB `COMMENT`s, which `duckbill docs` renders into a schema
reference and the in-app About view shows.
"""

import argparse
import os
import random
import time

import duckdb

# Fabricated shape: a handful of routes across three regions, with a realistic mix
# of statuses and a couple of routes deliberately slower than the rest.
ROUTES = ["/", "/api/items", "/api/items/:id", "/api/search", "/login",
          "/checkout", "/static/app.js", "/health"]
REGIONS = ["us-east", "eu-west", "ap-south"]
SLOW_ROUTES = {"/api/search", "/checkout"}  # heavier tail, so detail/leaderboard charts have signal

DAYS = 14
EVENTS_PER_HOUR = 120


def _status(rng: random.Random) -> int:
    r = rng.random()
    if r < 0.90:
        return 200
    if r < 0.95:
        return 304
    if r < 0.98:
        return 404
    return 500 if rng.random() < 0.6 else 503


def _latency_ms(rng: random.Random, route: str) -> float:
    base = 180.0 if route in SLOW_ROUTES else 35.0
    return round(base * rng.lognormvariate(0.0, 0.6), 1)


def build(path: str) -> None:
    rng = random.Random(1729)  # seeded: re-running yields the same warehouse
    now = int(time.time())
    start = now - DAYS * 86400

    events: list[tuple[int, str, str, int, float, int]] = []
    for ts in range(start, now, 3600):
        for _ in range(EVENTS_PER_HOUR):
            route = rng.choice(ROUTES)
            region = rng.choice(REGIONS)
            status = _status(rng)
            jitter = rng.randint(0, 3599)
            events.append((ts + jitter, route, region, status,
                           _latency_ms(rng, route), rng.randint(180, 24_000)))

    # A deploy every ~3 days, for the dashboard's marker rules.
    deploys: list[tuple[int, str]] = [
        (start + d * 86400 + 9 * 3600, f"v1.{4 + d // 3}.0")
        for d in range(0, DAYS, 3)
    ]

    if os.path.exists(path):
        os.remove(path)
    con = duckdb.connect(path)
    try:
        con.execute("CREATE SCHEMA IF NOT EXISTS warehouse")
        con.execute(
            "CREATE TABLE warehouse.events "
            "(ts BIGINT, route VARCHAR, region VARCHAR, status INTEGER, "
            " latency_ms DOUBLE, bytes BIGINT)")
        con.executemany(
            "INSERT INTO warehouse.events VALUES (?, ?, ?, ?, ?, ?)", events)
        con.execute("CREATE TABLE warehouse.deploys (build_time BIGINT, version VARCHAR)")
        con.executemany("INSERT INTO warehouse.deploys VALUES (?, ?)", deploys)

        con.execute("COMMENT ON TABLE warehouse.events IS "
                    "'One row per HTTP request to the service.'")
        con.execute("COMMENT ON COLUMN warehouse.events.ts IS "
                    "'When the request completed, epoch seconds (UTC).'")
        con.execute("COMMENT ON COLUMN warehouse.events.route IS "
                    "'The matched route template, e.g. /api/items/:id.'")
        con.execute("COMMENT ON COLUMN warehouse.events.latency_ms IS "
                    "'Server-side response time in milliseconds.'")
        con.execute("COMMENT ON TABLE warehouse.deploys IS "
                    "'One row per release, used to overlay deploy markers.'")
    finally:
        con.close()
    print(f"wrote {path}: {len(events):,} events, {len(deploys)} deploys over {DAYS} days")


def main() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-o", "--out", default=os.path.join(here, "sample.duckdb"),
                    help="output DuckDB path (default: examples/sample.duckdb)")
    build(ap.parse_args().out)


if __name__ == "__main__":
    main()
