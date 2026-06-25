"""Example source: a synthetic hourly metric in the shape of a real incremental source.

The one correct incremental pattern, ready to copy:
  - a Table(mode="merge", primary_key=..., cursor=..., overlap=...) declares the idempotent upsert
  - the fetch bounds its request on `since` -- the high-water mark ALREADY rewound by the overlap.
    Do not subtract the overlap yourself.
  - produce() yields (Table, batch); batch is a list of dicts (or a pyarrow.Table for bulk fetches)

Everything above `--- end swap point ---` is synthetic so the template self-tests with no
credentials. Replace `_fetch` with a real fetch -- aws/boto3, a Snowflake query, an observability API
-- when you wire a real source, and add its deps to refresh.py's inline `# /// script` header.

Synthetic clock, env-driven so the template's test can simulate time advancing:
  NOW_HOURS  how many hours of data "exist" now (default 6)
  REVISION   value stamped on rows fetched this run, to prove merge upserts (default 0)
"""
import os
from collections.abc import Iterator

from ducktail import Batch, Source, Table

HOUR = 3600
EPOCH0 = 1_700_000_000 // HOUR * HOUR
PARTITIONS = ["us-east", "eu-west", "ap-south"]
OVERLAP = 2 * HOUR


# --- the only part you'd swap for a real fetch ---
def _fetch(since: int) -> Iterator[dict[str, object]]:
    now_ts = EPOCH0 + int(os.environ.get("NOW_HOURS", "6")) * HOUR
    revision = int(os.environ.get("REVISION", "0"))
    ts = (since // HOUR) * HOUR  # align to the hour
    while ts < now_ts:
        for i, partition in enumerate(PARTITIONS):
            yield {"ts": ts, "partition": partition,
                   "requests": ((ts // HOUR) % 24) * 100 + i * 10, "loaded_rev": revision}
        ts += HOUR
# --- end swap point ---


# A real source starts its first run at `initial()` (now - the lookback; `from ducktail import initial`).
# This synthetic one pins to EPOCH0 so the env-driven test clock (NOW_HOURS) works.
EXAMPLE_HOURLY = Table("example_hourly", "merge", ("ts", "partition"), "ts", OVERLAP, EPOCH0)


def produce(starts: dict[str, int]) -> Iterator[tuple[Table, Batch]]:
    # starts["example_hourly"] is the high-water mark, already rewound by OVERLAP.
    yield EXAMPLE_HOURLY, list(_fetch(starts["example_hourly"]))


EXAMPLE = Source("example_hourly", [EXAMPLE_HOURLY], produce)
