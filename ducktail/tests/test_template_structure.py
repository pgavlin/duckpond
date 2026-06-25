from conftest import TEMPLATE


def test_template_has_required_files():
    expected = [
        ".gitignore",
        "README.md",
        "ducktail.py",
        "refresh.py",
        "dash.py",
        "sources/__init__.py",
        "sources/example_hourly.py",
    ]
    missing = [p for p in expected if not (TEMPLATE / p).exists()]
    assert not missing, f"template missing files: {missing}"


def test_refresh_declares_inline_deps():
    # uv-first: deps live in refresh.py's PEP 723 header, not a requirements.txt.
    text = (TEMPLATE / "refresh.py").read_text()
    assert "# /// script" in text, "refresh.py should carry a PEP 723 inline-deps header"
    for dep in ("duckdb", "pyarrow"):
        assert dep in text, f"inline deps should include {dep}"
    assert not (TEMPLATE / "requirements.txt").exists(), "requirements.txt should be gone (uv-first)"
