"""A file-based store of saved Ask-view questions.

Each question is one JSON file in the store directory -- `<slug>.json` holding
`{name, sql, viz, saved}` -- so the questions are git-friendly and hand-editable,
and a project is just dashboard.py + questions/ + the .duckdb. Saving a name that
already exists updates it in place.
"""

import json
import os
import re
from datetime import datetime, timezone
from typing import Any

# A saved question record: {name, sql, viz, saved, slug}.
Question = dict[str, Any]


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return s or "untitled"


class QuestionStore:
    def __init__(self, directory: str):
        self.dir = directory

    def _path(self, slug: str) -> str:
        # slugify again so a slug from a request can never escape the directory.
        return os.path.join(self.dir, slugify(slug) + ".json")

    def list(self) -> list[Question]:
        out: list[Question] = []
        if os.path.isdir(self.dir):
            for fn in sorted(os.listdir(self.dir)):
                if not fn.endswith(".json"):
                    continue
                try:
                    with open(os.path.join(self.dir, fn)) as f:
                        rec = json.load(f)
                    rec["slug"] = fn[:-5]
                    out.append(rec)
                except (OSError, json.JSONDecodeError):
                    pass  # skip an unreadable/foreign file rather than fail the list
        out.sort(key=lambda r: str(r.get("name", "")).lower())
        return out

    def save(self, name: str, sql: str, viz: Any) -> Question:
        os.makedirs(self.dir, exist_ok=True)
        rec: Question = {"name": name, "sql": sql, "viz": viz,
                         "saved": datetime.now(timezone.utc).isoformat()}
        slug = slugify(name)
        with open(self._path(slug), "w") as f:
            json.dump(rec, f, indent=2)
        rec["slug"] = slug
        return rec

    def delete(self, slug: str) -> None:
        path = self._path(slug)
        if os.path.exists(path):
            os.remove(path)
