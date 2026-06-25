"""Render a warehouse's docs to Markdown: the dashboard's narrative `readme`
plus a schema reference (each table's COMMENT, and its columns with types and
COMMENTs). Used by `duckbill docs`; the in-app About view shows the same content.
"""


from collections.abc import Sequence

from .backends.base import DocsTable


def to_markdown(title: str, readme: str, tables: Sequence[DocsTable]) -> str:
    out = [f"# {title}", ""]
    if readme and readme.strip():
        out += [readme.strip(), ""]
    out += ["## Schema", ""]
    for t in tables:
        out.append(f"### `{t['name']}`")
        if t.get("comment"):
            out += ["", t["comment"].strip()]
        out += ["", "| column | type | description |", "| --- | --- | --- |"]
        for c in t["columns"]:
            desc = (c.get("comment") or "").replace("|", "\\|").replace("\n", " ")
            out.append(f"| `{c['name']}` | {c['type']} | {desc} |")
        out.append("")
    return "\n".join(out) + "\n"
