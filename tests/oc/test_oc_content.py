import hashlib
import re
import shutil
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
    (written,) = frontmatter(path)
    # The generator may quote the value for YAML; the text is the source's.
    assert (
        written.removeprefix("description:").strip().strip('"')
        == source_description.removeprefix("description:").strip()
    )
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


def descriptions() -> list[tuple[Path, str]]:
    paths = [*RENDERED.glob("commands/*.md"), *RENDERED.glob("skills/*/SKILL.md")]
    return [
        (path, line.removeprefix("description:").strip())
        for path in paths
        for line in frontmatter(path)
        if line.startswith("description:")
    ]


def test_every_rendered_description_is_valid_yaml_plain_or_quoted() -> None:
    found = descriptions()
    assert len(found) == len(SKILL_NAMES) + len(COMMAND_NAMES)
    for path, value in found:
        assert value.startswith(('"', "'")) or (": " not in value and " #" not in value), path


def copy_of_skills(tmp_path: Path) -> Path:
    shutil.copytree(REPO / "skills", tmp_path / "skills")
    shutil.copytree(content.OVERRIDES, tmp_path / "opencode" / "overrides")
    return tmp_path


def test_a_description_with_yaml_syntax_is_quoted_and_escaped(tmp_path: Path) -> None:
    repo = copy_of_skills(tmp_path / "repo")
    source = repo / "skills" / "grill-me" / "SKILL.md"
    lines = source.read_text().split("\n")
    lines[2] = 'description: Say "hi": a \\ b'
    source.write_text("\n".join(lines))
    out = tmp_path / "out"
    content.render_content(repo, out)
    written = (out / "commands" / "coven-grill-me.md").read_text().split("\n")[1]
    assert written == 'description: "Say \\"hi\\": a \\\\ b"'


def test_a_skill_without_a_name_line_is_an_error(tmp_path: Path) -> None:
    repo = copy_of_skills(tmp_path / "repo")
    source = repo / "skills" / "clean-code" / "SKILL.md"
    source.write_text(source.read_text().replace("name: clean-code\n", "", 1))
    with pytest.raises(content.ContentError, match=r"skills/clean-code/SKILL\.md: .*name"):
        content.render_content(repo, tmp_path / "out")
