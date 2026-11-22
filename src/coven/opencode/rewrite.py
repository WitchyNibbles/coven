"""The rewrite table: Claude Code names in coven's skill text become their OpenCode 2.3.2 names.

The table is data, applied in order to each line. Rewriting never drops or adds a line. A name
with no OpenCode equivalent is not guessed at: it raises `RewriteError`, and the skill gets an
override under `opencode/overrides/` instead.
"""

from __future__ import annotations

import re
from typing import Final

AGENTS: Final = ("implementer", "reviewer", "final-reviewer", "explorer")
COVEN_SKILLS: Final = (
    "ceo-review",
    "clean-code",
    "office-hours",
    "project-standard",
    "shared-memory",
    "requesting-code-review",
    "subagent-driven-development",
)
COMMANDS: Final = ("grill-me", "handoff")
SUPERPOWERS_SKILLS: Final = (
    "brainstorming",
    "dispatching-parallel-agents",
    "executing-plans",
    "finishing-a-development-branch",
    "receiving-code-review",
    "requesting-code-review",
    "subagent-driven-development",
    "systematic-debugging",
    "test-driven-development",
    "using-git-worktrees",
    "using-superpowers",
    "verification-before-completion",
    "writing-plans",
    "writing-skills",
)
# OpenCode 2.3.2 tool ids for the Claude Code tool names that skill text mentions.
TOOLS: Final = {
    "Read": "read",
    "Edit": "edit",
    "Write": "write",
    "Bash": "shell",
    "Grep": "grep",
    "Glob": "glob",
    "Skill": "skill",
}
WORD_END: Final = r"(?![\w-])"


def _any(names: tuple[str, ...]) -> str:
    return "(?:" + "|".join(re.escape(name) for name in names) + ")"


def _entry(pattern: str, replacement: str) -> tuple[re.Pattern[str], str]:
    return re.compile(pattern), replacement


# Skill names only: `coven:x` and `superpowers:x` become folder-style ids.
SKILL_NAMES: Final = [
    _entry(rf"\b(coven):({_any(COVEN_SKILLS)}){WORD_END}", r"\1-\2"),
    _entry(rf"\b(superpowers):({_any(SUPERPOWERS_SKILLS)}){WORD_END}", r"\1-\2"),
]

TABLE: Final = [
    _entry(rf'subagent_type: "coven:({_any(AGENTS)})"', r'agent: "\1"'),
    _entry(
        rf"\bSubagent \(coven:({_any(AGENTS)}) or coven:({_any(AGENTS)})\):",
        r"subagent (agent: \1 or \2):",
    ),
    _entry(rf"\bSubagent \(coven:({_any(AGENTS)})\):", r"subagent (agent: \1):"),
    _entry(rf"\bcoven:({_any(AGENTS)}){WORD_END}", r"\1"),
    _entry(rf"\bcoven:({_any(COMMANDS)}){WORD_END}", r"/coven-\1"),
    *SKILL_NAMES,
    _entry(r"\bRead, Grep and Glob\b", "`read`, `grep` and `glob`"),
    _entry(r"\bRead, Grep and `rg`", "`read`, `grep` and `rg`"),
    *(_entry(rf"\b([Tt]he) {name} tool\b", rf"\1 `{tool}` tool") for name, tool in TOOLS.items()),
    _entry(r"\bTask(?:Create|Update)\b", "todowrite"),
]

# Names no table entry maps. Whatever of these is left in a line after the table ran is an error.
FORBIDDEN: Final = [
    re.compile(pattern)
    for pattern in (
        r"AskUserQuestion",
        r"SendMessage",
        r"claude --bg",
        r"claude agents",
        r"CLAUDE_PLUGIN_ROOT",
        r"CLAUDE_SKILL_DIR",
        r"CLAUDE_CONFIG_DIR",
        r"subagent_type",
        r"ToolSearch",
        r"TaskList",
        r"\b(?:coven|superpowers):[a-z][\w-]*",
    )
]


class RewriteError(Exception):
    """A skill line names something OpenCode has no equivalent for."""


def _rewrite_line(table: list[tuple[re.Pattern[str], str]], line: str) -> str:
    for pattern, replacement in table:
        line = pattern.sub(replacement, line)
    return line


def _apply(table: list[tuple[re.Pattern[str], str]], text: str) -> str:
    """The table applied to each line, so the line count cannot change."""
    return "\n".join(_rewrite_line(table, line) for line in text.split("\n"))


def _check(text: str, source: str) -> None:
    for number, line in enumerate(text.split("\n"), start=1):
        for pattern in FORBIDDEN:
            found = pattern.search(line)
            if found:
                raise RewriteError(
                    f"{source}:{number}: {found.group()} has no OpenCode equivalent (add an override)"
                )


def rewrite_text(text: str, source: str) -> str:
    rewritten = _apply(TABLE, text)
    _check(rewritten, source)
    return rewritten


def rename_skills(text: str) -> str:
    """Only the skill names (`coven:x`, `superpowers:x`) of `text`, nothing else."""
    return _apply(SKILL_NAMES, text)
