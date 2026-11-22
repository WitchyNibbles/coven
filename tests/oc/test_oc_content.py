import hashlib
import re
import shutil
import stat
from pathlib import Path

import pytest
from support import REPO

from coven.opencode import content
from coven.opencode.rewrite import rewrite_text

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
SOURCE_OF_ROUTING = "instructions/routing.md"


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
        # The routing override is `routing/AGENTS.md` in the profile; its source is not a skill.
        expected = SOURCE_OF_ROUTING if expected == "routing/AGENTS.md" else expected
        assert header[0] == expected, f"{override} names {header[0]}, expected {expected}"


def test_no_machine_paths() -> None:
    # The shipped sources use generic example paths; only this machine's paths are leaks.
    machine = (str(REPO), str(Path.home()))
    hits = [
        str(rel)
        for rel in files_under(RENDERED)
        if any(path in (RENDERED / rel).read_text() for path in machine)
    ]
    assert not hits


SDD = RENDERED / "skills" / "coven-subagent-driven-development"
SDD_FORBIDDEN = ("${CLAUDE_", "subagent_type", "Claude Code", "SendMessage")


def test_sdd_dispatch_words() -> None:
    text = (SDD / "SKILL.md").read_text()
    assert "`subagent` tool" in text
    for agent in ("implementer", "reviewer", "final-reviewer"):
        assert f'agent: "{agent}"' in text, agent
    assert 'await_agents({ids: <pending only>, mode: "all"})' in text
    assert "A `running` or progress report is not a result." in text
    assert [word for word in SDD_FORBIDDEN if word in text] == []


def test_sdd_scripts_by_base_dir() -> None:
    text = (SDD / "SKILL.md").read_text()
    calls = re.findall(r"bash \"[^\"]*/scripts/[\w-]+\"", text)
    assert calls
    assert all(
        re.fullmatch(r'bash "<base directory of this skill>/scripts/[\w-]+"', c) for c in calls
    )
    assert len(calls) == len(re.findall(r"/scripts/", text))
    assert "Base directory for this skill" in text


def test_no_pending_override() -> None:
    assert not hasattr(content, "PENDING_OVERRIDE")


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
    shutil.copytree(REPO / "agents", tmp_path / "agents")
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


AGENT_NAMES = ("explorer", "implementer", "reviewer", "final-reviewer")
DENY_EDIT = ["  - action: edit", '    resource: "*"', "    effect: deny"]
DENY_SUBAGENT = ["  - action: subagent", '    resource: "*"', "    effect: deny"]
DENIED = {
    "explorer": DENY_EDIT,
    "reviewer": DENY_EDIT,
    "final-reviewer": DENY_EDIT,
    "implementer": DENY_SUBAGENT,
}


def source_agent(name: str) -> tuple[str, str]:
    """The source's description value and its body, each through the rewrite."""
    text = (REPO / "agents" / f"{name}.md").read_text()
    front, body = content._split_frontmatter(rewrite_text(text, f"agents/{name}.md"))
    description = next(line for line in front if line.startswith("description:"))
    return description.removeprefix("description:").strip(), body


def test_agents() -> None:
    assert sorted(path.name for path in (RENDERED / "agents").iterdir()) == sorted(
        f"{name}.md" for name in AGENT_NAMES
    )
    for name in AGENT_NAMES:
        description, body = source_agent(name)
        path = RENDERED / "agents" / f"{name}.md"
        front = frontmatter(path)
        assert front[1] == "mode: subagent", name
        assert front[2:] == ["permissions:", *DENIED[name]], name
        assert front[0].removeprefix("description:").strip().strip('"') == description, name
        assert path.read_text().split("\n---\n", 1)[1] == body, name


ROUTING_OVERRIDE = content.OVERRIDES / "routing" / "AGENTS.md"


def test_routing() -> None:
    override = ROUTING_OVERRIDE.read_text()
    rendered = (RENDERED / "AGENTS.md").read_text()
    assert rendered == override.split("\n", 1)[1]
    assert "python3 .coven/check.py" in rendered
    assert "`AGENTS.md`" in rendered
    assert "`CLAUDE.md`" in rendered
    for skill in SKILL_NAMES:
        assert f"coven-{skill}" in rendered, skill
    for command in COMMAND_NAMES:
        assert f"/coven-{command}" in rendered, command
    assert not re.search(r"\b(coven|superpowers):", rendered)
    header = content.parse_header(override)
    assert header == (
        "instructions/routing.md",
        hashlib.sha256((REPO / "instructions" / "routing.md").read_bytes()).hexdigest(),
    )


def test_routing_leaves_out_the_m2_items() -> None:
    rendered = (RENDERED / "AGENTS.md").read_text().lower()
    for left_out in ("manager", "setup", "systems-map", "claude --bg", "general manager"):
        assert left_out not in rendered, left_out
