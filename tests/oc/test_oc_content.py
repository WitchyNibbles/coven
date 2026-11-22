import hashlib
import re
import stat
from pathlib import Path

import pytest
from support import REPO

from coven.opencode import content

SKILL_NAMES = (
    "ceo-review",
    "clean-code",
    "office-hours",
    "project-standard",
    "shared-memory",
    "requesting-code-review",
    "subagent-driven-development",
)
COMMAND_NAMES = ("grill-me", "handoff")
RENDERED = content.CONTENT


def files_under(root: Path) -> set[Path]:
    return {path.relative_to(root) for path in root.rglob("*") if path.is_file()}


def frontmatter(path: Path) -> list[str]:
    lines = path.read_text().split("\n")
    assert lines[0] == "---", path
    return lines[1 : lines.index("---", 1)]


def test_content_is_fresh(tmp_path: Path) -> None:
    content.render_content(REPO, tmp_path)
    assert files_under(tmp_path) == files_under(RENDERED)
    stale = [
        str(rel)
        for rel in files_under(tmp_path)
        if (tmp_path / rel).read_bytes() != (RENDERED / rel).read_bytes()
    ]
    assert not stale, f"regenerate with `uv run python -m coven.opencode.content`: {stale}"


def test_skill_layout() -> None:
    folders = sorted(path.name for path in (RENDERED / "skills").iterdir())
    assert folders == sorted(f"coven-{name}" for name in SKILL_NAMES)
    for name in SKILL_NAMES:
        source = REPO / "skills" / name
        rendered = RENDERED / "skills" / f"coven-{name}"
        assert files_under(rendered) == files_under(source), name
        for rel in files_under(source):
            if (source / rel).stat().st_mode & stat.S_IXUSR:
                assert (rendered / rel).stat().st_mode & stat.S_IXUSR, (
                    f"{name}/{rel} lost its executable bit"
                )


def test_the_constants_name_the_shipped_sources() -> None:
    assert content.SKILLS == SKILL_NAMES
    assert content.COMMANDS == COMMAND_NAMES
    assert content.AGENTS == ("explorer", "implementer", "reviewer", "final-reviewer")


@pytest.mark.parametrize("name", COMMAND_NAMES)
def test_commands(name: str) -> None:
    path = RENDERED / "commands" / f"coven-{name}.md"
    source_description = next(
        line
        for line in frontmatter(REPO / "skills" / name / "SKILL.md")
        if line.startswith("description:")
    )
    assert frontmatter(path) == [source_description]
    assert path.read_text().endswith("\n$ARGUMENTS\n")


def test_only_the_two_commands_are_rendered() -> None:
    names = sorted(path.name for path in (RENDERED / "commands").iterdir())
    assert names == [f"coven-{name}.md" for name in COMMAND_NAMES]


@pytest.mark.parametrize("name", SKILL_NAMES)
def test_frontmatter_name(name: str) -> None:
    folder = RENDERED / "skills" / f"coven-{name}"
    assert f"name: coven-{name}" in frontmatter(folder / "SKILL.md")


def test_override_header_stripped() -> None:
    started = [
        str(rel)
        for rel in files_under(RENDERED)
        if (RENDERED / rel).read_bytes().startswith(b"<!-- opencode override")
    ]
    assert not started


def overrides() -> list[Path]:
    return sorted(path for path in content.OVERRIDES.rglob("*") if path.is_file())


def test_override_hash_current() -> None:
    for override in overrides():
        header = content.parse_header(override.read_text())
        rel = override.relative_to(REPO)
        assert header is not None, (
            f"{rel} has no `<!-- opencode override of PATH sha256:HEX -->` first line"
        )
        source, digest = header
        actual = hashlib.sha256((REPO / source).read_bytes()).hexdigest()
        assert digest == actual, (
            f"{rel} replaces {source}, which changed: update the override, then its hash"
        )


def test_overrides_mirror_a_source_path() -> None:
    for override in overrides():
        header = content.parse_header(override.read_text())
        assert header is not None
        expected = override.relative_to(content.OVERRIDES).as_posix()
        assert header[0] == expected, f"{override} names {header[0]}, expected {expected}"


def test_no_machine_paths() -> None:
    # Task 2 removes this skip together with PENDING_OVERRIDE.
    skipped = {("skills", f"coven-{name}") for name in content.PENDING_OVERRIDE}
    pattern = re.compile(r"/home/|/Users/|~/\.")
    hits = [
        str(rel)
        for rel in files_under(RENDERED)
        if rel.parts[:2] not in skipped and pattern.search((RENDERED / rel).read_text())
    ]
    assert not hits
