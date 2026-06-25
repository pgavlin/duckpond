"""build_server refuses a serve-only backend before touching it (no connection)."""

import pytest

from duckbill import bundle


def test_bundle_rejects_network_backend(tmp_path):
    mod = tmp_path / "dash.py"
    mod.write_text('charts=[{"id":"c","title":"C","type":"bar","sql":"SELECT 1"}]\n')
    # A postgres DSN constructs a backend object but connects lazily; the guard
    # must fire before any connection attempt.
    with pytest.raises(ValueError, match="serve-only"):
        bundle.build_server(str(mod), "postgresql://u@localhost/db", str(tmp_path / "o.py"))
