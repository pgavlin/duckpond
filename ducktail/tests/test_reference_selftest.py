"""The reference refresh.py's --selftest is the deterministic proof of the incremental
contract -- fetch-reducing incrementality (a re-run pulls only [since, now]), overlap
re-fetch, merge upsert, and windowed replace -- against synthetic, credential-free sources.
Run it in CI so that contract can't silently regress."""

import pathlib
import subprocess
import sys

REF = (pathlib.Path(__file__).resolve().parent.parent
       / "skills" / "ducktail" / "references" / "refresh.py")


def test_reference_selftest_passes():
    r = subprocess.run([sys.executable, str(REF), "--selftest"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    # The selftest asserts run 2 fetched the incremental slice (not the full window from
    # scratch), that the overlap re-fetch upserted, and that no rows duplicated.
    assert "SELFTEST: PASS" in r.stdout, r.stdout
    assert "incremental" in r.stdout.lower()
