import pytest

from coven.opencode import rewrite
from coven.opencode.rewrite import RewriteError, rewrite_text

AGENTS = ("implementer", "reviewer", "final-reviewer", "explorer")


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("coven:clean-code", "coven-clean-code"),
        ("`coven:subagent-driven-development`", "`coven-subagent-driven-development`"),
        ("superpowers:writing-plans", "superpowers-writing-plans"),
        ("that is superpowers:brainstorming).", "that is superpowers-brainstorming)."),
        ("`coven:grill-me`", "`/coven-grill-me`"),
        ("coven:handoff", "/coven-handoff"),
        ('subagent_type: "coven:reviewer"', 'agent: "reviewer"'),
        ("Subagent (coven:implementer):", "subagent (agent: implementer):"),
        (
            "Subagent (coven:reviewer or coven:final-reviewer):",
            "subagent (agent: reviewer or final-reviewer):",
        ),
        ("Dispatch a `coven:reviewer` subagent", "Dispatch a `reviewer` subagent"),
        ("the Skill tool", "the `skill` tool"),
        ("The Read tool", "The `read` tool"),
        ("the Edit tool", "the `edit` tool"),
        ("the Write tool", "the `write` tool"),
        ("the Bash tool", "the `shell` tool"),
        ("the Grep tool", "the `grep` tool"),
        ("the Glob tool", "the `glob` tool"),
        ("Read, Grep and Glob", "`read`, `grep` and `glob`"),
        ("with Read, Grep and `rg`", "with `read`, `grep` and `rg`"),
        ("`TaskCreate`", "`todowrite`"),
        ("call TaskUpdate when done", "call todowrite when done"),
    ],
)
def test_rewrites(source: str, expected: str) -> None:
    assert rewrite_text(source, "skills/x/SKILL.md") == expected


@pytest.mark.parametrize("agent", AGENTS)
def test_agent_names_lose_the_prefix(agent: str) -> None:
    assert rewrite_text(f"coven:{agent}", "x.md") == agent


@pytest.mark.parametrize(
    "text",
    [
        "Read the plan once",
        "Write the table to the ledger",
        "Bash is a shell; Edit the file",
        "the Task loop",
        "plain line  with   spacing\t",
        "",
    ],
)
def test_lines_without_a_match_come_back_unchanged(text: str) -> None:
    assert rewrite_text(text, "x.md") == text


@pytest.mark.parametrize(
    "name",
    [
        "AskUserQuestion",
        "SendMessage",
        "claude --bg",
        "claude agents",
        "CLAUDE_PLUGIN_ROOT",
        "CLAUDE_SKILL_DIR",
        "CLAUDE_CONFIG_DIR",
        "subagent_type",
        "ToolSearch",
        "TaskList",
        "coven:manager",
        "coven:setup",
        "coven:systems-map",
        "coven:project-manager",
        "superpowers:unknown-skill",
    ],
)
def test_forbidden_names_fail_with_source_and_line(name: str) -> None:
    text = f"fine line\nanother\nuse {name} here\nlast"
    with pytest.raises(RewriteError) as raised:
        rewrite_text(text, "skills/x/SKILL.md")
    assert str(raised.value) == (
        f"skills/x/SKILL.md:3: {name} has no OpenCode equivalent (add an override)"
    )


def test_subagent_type_left_after_the_mapping_is_forbidden() -> None:
    with pytest.raises(RewriteError, match=r"x\.md:1: subagent_type"):
        rewrite_text('subagent_type: "general-purpose"', "x.md")


def test_line_count_never_changes() -> None:
    text = "a coven:reviewer\n\nthe Read tool\r\nlast line no newline"
    assert rewrite_text(text, "x.md").count("\n") == text.count("\n")
    assert rewrite_text(text + "\n", "x.md").endswith("\n")


def test_rename_skills_touches_skill_names_only() -> None:
    text = "coven:clean-code, superpowers:writing-plans, coven:reviewer, the Read tool"
    assert rewrite.rename_skills(text) == (
        "coven-clean-code, superpowers-writing-plans, coven:reviewer, the Read tool"
    )


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            "model: [MODEL — leave out for the role's default (Sonnet); name one per",
            "model: [MODEL — leave out for the agent's configured model; name one per",
        ),
        (
            "routine task review (the reviewer role's default",
            "routine task review (the reviewer agent's configured model",
        ),
        (
            "  reviewer role's default applies); name one",
            "  reviewer agent's configured model applies); name one",
        ),
    ],
)
def test_the_model_comes_from_the_profile_config(source: str, expected: str) -> None:
    assert rewrite_text(source, "x.md") == expected
