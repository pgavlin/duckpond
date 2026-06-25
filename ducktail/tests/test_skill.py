import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SKILL = REPO / "skills" / "ducktail" / "SKILL.md"


def test_skill_frontmatter_and_references():
    text = SKILL.read_text()
    assert text.startswith("---\n"), "missing YAML frontmatter"
    frontmatter = text.split("---\n", 2)[1]
    assert re.search(r"^name:\s*ducktail\s*$", frontmatter, re.M), "name must be ducktail"
    assert re.search(r"^description:\s*\S", frontmatter, re.M), "description required"
    # the skill must point at the things it depends on, and they must exist
    for ref in ("assets/template/", "references/refresh.py", "refresh.py", "dash.py", "duckbill",
                "overlap", "uv run"):
        assert ref in text, f"skill should reference {ref!r}"
    assert (REPO / "skills" / "ducktail" / "assets" / "template").is_dir()
    assert (REPO / "skills" / "ducktail" / "references" / "refresh.py").exists()


def test_skill_documents_validated_patterns():
    # Guidance hardened by fresh-agent skill-validation builds. Each needle marks a gap a
    # build hit; guard against silently dropping the fix in a future edit.
    text = SKILL.read_text()
    for needle, why in [
        ("since-query", "incrementality reduces fetch only with a since-queryable upstream"),
        ("two `run()`", "dependent sources sequence with two run() calls"),
        ("parallel=False", "parallel=False is thread-affinity, not ordering"),
        ("no primary key", "replace tables need no primary key"),
    ]:
        assert needle in text, f"skill should still document: {why} ({needle!r})"


def test_skill_description_keeps_trigger_phrases():
    # The description is what makes the skill discoverable. A natural request -- "a local data
    # warehouse + dashboard ... join across players/teams/managers ... current and historical" --
    # once failed to trigger it (superpowers brainstorming grabbed it instead); these phrases are
    # why it now does. Guard them so a future edit can't quietly weaken discovery.
    frontmatter = SKILL.read_text().split("---\n", 2)[1]
    desc = next((ln for ln in frontmatter.splitlines() if ln.startswith("description:")), "")
    for phrase in ("dashboard", "current and historical", "join across"):
        assert phrase in desc, f"skill description should keep the trigger phrase {phrase!r}"
