import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
TEMPLATE = REPO / "skills" / "ducktail" / "assets" / "template"


@pytest.fixture
def investigation(tmp_path):
    """Scaffold the template into a temp dir -- a fresh investigation."""
    dst = tmp_path / "inv"
    shutil.copytree(TEMPLATE, dst)
    return dst


def run_script(script, cwd, env=None):
    """Run a template script with the dev interpreter in `cwd`."""
    full_env = {**os.environ, **(env or {})}
    return subprocess.run(
        [sys.executable, str(Path(cwd) / script)],
        cwd=cwd, env=full_env, capture_output=True, text=True,
    )
