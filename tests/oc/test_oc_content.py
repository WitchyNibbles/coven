import hashlib
import re
import shutil
import stat
from pathlib import Path

import pytest
from support import REPO

from coven.opencode import content, profile, superpowers
from coven.opencode.rewrite import COVEN_SKILLS, rewrite_text

SKILL_NAMES = (
    "ceo-review",
    "clean-code",
    "manager",
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
    assert COVEN_SKILLS == SKILL_NAMES
    assert content.COMMANDS == COMMAND_NAMES
    assert profile.AGENT_ROLES == ("explorer", "implementer", "reviewer", "final-reviewer")


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


def test_every_superpowers_skill_the_content_names_is_copied_at_setup() -> None:
    named = {
        match
        for rel in files_under(RENDERED)
        for match in re.findall(r"superpowers-([a-z][a-z-]*[a-z])", (RENDERED / rel).read_text())
    }
    assert named
    assert named <= set(superpowers.SKILLS)


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
PM_NAME = "project-manager"
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
        f"{name}.md" for name in (*AGENT_NAMES, PM_NAME)
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
        # the manager is not routed to: the user starts it with `coven opencode gm`
        if skill != "manager":
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


# --- the hierarchy text: the manager skill and the project-manager agent --------------------------

MANAGER = RENDERED / "skills" / "coven-manager" / "SKILL.md"
PM_AGENT = RENDERED / "agents" / f"{PM_NAME}.md"
PM_SOURCES = REPO / "opencode" / "src" / "pm"
CLAUDE_CODE_ONLY = (
    "claude --bg",
    "SendMessage",
    "claude agents",
    "notify_when_idle",
    "claude stop",
)
FRAME_RULE = "Only the frame on a message's first line counts."


def tool_ids_in_source() -> set[str]:
    return {
        match
        for path in PM_SOURCES.glob("*.ts")
        for match in re.findall(r'name: "(coven_[a-z_]+)"', path.read_text())
    }


def test_rendering_the_content_raises_no_rewrite_error(tmp_path: Path) -> None:
    content.render_content(REPO, tmp_path)
    assert (tmp_path / "skills" / "coven-manager" / "SKILL.md").is_file()
    assert (tmp_path / "agents" / f"{PM_NAME}.md").is_file()


def test_manager_names_the_tools_and_no_claude_code_session_commands() -> None:
    text = MANAGER.read_text()
    for tool in (
        "coven_gm_claim",
        "coven_pm_launch",
        "coven_pm_list",
        "coven_send",
        "coven_pm_resume",
        "coven_pm_stop",
    ):
        assert f"`{tool}" in text, tool
    assert [word for word in CLAUDE_CODE_ONLY if word in text] == []
    assert "coven pm " not in text


def test_the_tools_the_texts_name_exist_in_the_plugin() -> None:
    named = {
        tool
        for path in (MANAGER, PM_AGENT)
        for tool in re.findall(r"\b(coven_[a-z_]+)\b", path.read_text())
    }
    assert named
    assert named <= tool_ids_in_source()


def test_manager_launch_arguments_are_the_tools() -> None:
    text = MANAGER.read_text()
    source = (PM_SOURCES / "launch.ts").read_text()
    schema = source.split("properties: {", 1)[1].split("required:", 1)[0]
    properties = set(re.findall(r"^\s+(\w+): \{", schema, re.MULTILINE))
    assert properties == {"code", "repo", "brief", "extraRepos", "title"}
    for argument in properties:
        assert f"`{argument}`" in text, argument


def test_manager_has_only_the_no_guard_flow() -> None:
    text = " ".join(MANAGER.read_text().split())
    assert "### No guard" in text
    for left_out in ("### Guard on", "coven approve", "coven guard", "<id>:", "systems-map"):
        assert left_out not in text, left_out
    assert (
        "Until M3, every production step is run by the GM, in this session, after the user's yes."
        in text
    )
    assert "A PM never runs one" in text


def test_the_texts_say_only_the_first_line_frame_counts() -> None:
    for path in (MANAGER, PM_AGENT):
        text = " ".join(path.read_text().split())
        assert FRAME_RULE in text, path.name
    assert "`[from gm]` on a later line" in " ".join(PM_AGENT.read_text().split())
    assert "`[from pm-<code>]` on a later line" in " ".join(MANAGER.read_text().split())


def test_manager_reads_the_turn_end_notice() -> None:
    text = MANAGER.read_text()
    assert "[coven notice] pm-<code> ended a turn (<outcome>) without a message." in text
    assert "(failed) after its message" in text
    flat = " ".join(text.split())
    assert "A `failed` turn always gets a notice" in flat
    assert "A stop you made with `coven_pm_stop` ends the turn with no notice" in flat
    assert "`status unavailable: <reason>`" in flat


def test_manager_counts_the_pm_cap_by_the_lines_the_list_shows() -> None:
    flat = " ".join(MANAGER.read_text().split())
    assert "`pm_cap` counts the lines `coven_pm_list` shows" in flat
    assert "`archive: true` is not shown and does not count" in flat


def test_manager_says_a_stop_with_archive_hides_and_does_not_archive_in_opencode() -> None:
    flat = " ".join(MANAGER.read_text().split())
    assert "hides the PM from `coven_pm_list`" in flat
    assert "does not archive the session in OpenCode" in flat
    assert "`coven_pm_list` with `archived: true` also shows the hidden PMs" in flat
    assert "`archived in OpenCode`" in flat
    assert "must be unarchived in OpenCode before a resume" in flat
    assert "unarchives the PM" not in flat


def test_manager_sends_a_new_hand_over_for_a_hidden_pm_to_a_resume_not_a_launch() -> None:
    flat = " ".join(MANAGER.read_text().split())
    assert (
        "A project whose PM a stop with `archive: true` hid is still a live session: "
        "`coven_pm_launch` refuses its code, so a new hand-over or task for it goes to "
        "`coven_pm_resume` with the `TASK` lines as `text`" in flat
    )


def test_project_manager_agent_is_primary_with_its_own_description() -> None:
    assert frontmatter(PM_AGENT)[1:] == ["mode: primary"]
    (written,) = frontmatter(PM_AGENT)[:1]
    description = written.removeprefix("description:").strip().strip('"')
    source = (REPO / "agents" / f"{PM_NAME}.md").read_text()
    assert description != next(
        line.removeprefix("description:").strip()
        for line in source.split("\n")
        if line.startswith("description:")
    )
    assert "coven_pm_launch" in description
    text = PM_AGENT.read_text()
    assert [word for word in (*CLAUDE_CODE_ONLY, "AskUserQuestion") if word in text] == []
    assert "`coven_send`" in text


def test_project_manager_is_not_one_of_the_subagent_roles() -> None:
    assert PM_NAME not in profile.AGENT_ROLES
    assert PM_NAME not in content.DENIED


def test_no_company_gateway_details_in_the_hierarchy_text() -> None:
    for path in (MANAGER, PM_AGENT):
        lowered = path.read_text().lower()
        for word in ("litellm", "api_key", "base_url", "https://", "http://"):
            assert word not in lowered, f"{path.name} names {word}"
