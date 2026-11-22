"""Renders the repo's skills into `opencode/content/` through the rewrite table.

    uv run python -m coven.opencode.content

The output is committed; a test renders it again and compares. A skill file whose Claude Code
names have no OpenCode equivalent gets an override under `opencode/overrides/`: a full replacement
of that source file whose first line is `<!-- opencode override of PATH sha256:HEX -->`, so a test
can tell when the source changed under it.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Final

from coven import paths
from coven.opencode.rewrite import COMMANDS, COVEN_SKILLS, rewrite_text

SKILLS: Final = COVEN_SKILLS
# The rendered order of the agent files; `rewrite.AGENTS` is the same set, whose order only the table reads.
AGENTS: Final = ("explorer", "implementer", "reviewer", "final-reviewer")
# Skills whose override is not written yet: rendered from the source without the rewrite table.
PENDING_OVERRIDE: Final = ("subagent-driven-development",)
PREFIX: Final = "coven-"
OVERRIDES_REL: Final = Path("opencode/overrides")
OVERRIDES: Final = paths.root() / OVERRIDES_REL
CONTENT: Final = paths.root() / "opencode" / "content"
YAML_INDICATORS: Final = (
    "-",
    "?",
    ":",
    ",",
    "[",
    "]",
    "{",
    "}",
    "#",
    "&",
    "*",
    "!",
    "|",
    ">",
    "'",
    '"',
    "%",
    "@",
    "`",
)
HEADER: Final = re.compile(r"<!-- opencode override of (\S+) sha256:([0-9a-f]{64}) -->")


def parse_header(text: str) -> tuple[str, str] | None:
    """The (source path, sha256) of an override's first line, or None when it has none."""
    found = HEADER.fullmatch(text.split("\n", 1)[0])
    return (found.group(1), found.group(2)) if found else None


def _strip_header(text: str) -> str:
    return text.split("\n", 1)[1] if parse_header(text) else text


def _split_frontmatter(text: str) -> tuple[list[str], str]:
    """The frontmatter lines (between the `---` lines) and the body after them."""
    lines = text.split("\n")
    if lines[0] != "---" or "---" not in lines[1:]:
        raise ValueError("no frontmatter")
    end = lines.index("---", 1)
    return lines[1:end], "\n".join(lines[end + 1 :])


def _file(repo: Path, skill: str, rel: Path) -> Path:
    """The file that renders `rel`: the skill's override when there is one, else its source."""
    override = repo / OVERRIDES_REL / "skills" / skill / rel
    return override if override.is_file() else repo / "skills" / skill / rel


def _read(repo: Path, skill: str, rel: Path, *, rewrite: bool = True) -> str:
    path = _file(repo, skill, rel)
    text = path.read_text()
    if rewrite:
        text = rewrite_text(text, path.relative_to(repo).as_posix())
    return _strip_header(text)


def _skill_files(repo: Path, skill: str) -> list[Path]:
    roots = (repo / "skills" / skill, repo / OVERRIDES_REL / "skills" / skill)
    found = {
        path.relative_to(root)
        for root in roots
        if root.is_dir()
        for path in root.rglob("*")
        if path.is_file()
    }
    return sorted(found)


class ContentError(Exception):
    """A source file the generator cannot render."""


def _yaml_safe(line: str) -> str:
    """A `description:` line whose plain value YAML would misread, as a double-quoted string.

    OpenCode reads frontmatter with a strict YAML parser: `only: the` in a plain value is an error.
    """
    key, value = line.split(":", 1)
    value = value.strip()
    plain = bool(value) and not value.startswith(('"', "'"))
    if plain and (": " in value or " #" in value or value.startswith(YAML_INDICATORS)):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'{key}: "{escaped}"'
    return line


def _frontmatter_line(line: str) -> str:
    return _yaml_safe(line) if line.startswith("description:") else line


def _with_name(text: str, name: str, source: str) -> str:
    front, body = _split_frontmatter(text)
    if not any(line.startswith("name:") for line in front):
        raise ContentError(f"{source}: the frontmatter has no `name:` line")
    named = [
        f"name: {name}" if line.startswith("name:") else _frontmatter_line(line) for line in front
    ]
    return "\n".join(["---", *named, "---"]) + "\n" + body


def _copy(repo: Path, skill: str, rel: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if rel.suffix != ".md":
        shutil.copy2(_file(repo, skill, rel), target)
        return
    text = _read(repo, skill, rel, rewrite=skill not in PENDING_OVERRIDE)
    if rel.name == "SKILL.md":
        text = _with_name(
            text, PREFIX + skill, _file(repo, skill, rel).relative_to(repo).as_posix()
        )
    target.write_text(text)


def _skill(repo: Path, skill: str, out: Path) -> None:
    for rel in _skill_files(repo, skill):
        _copy(repo, skill, rel, out / "skills" / (PREFIX + skill) / rel)


def _command(repo: Path, name: str, out: Path) -> None:
    front, body = _split_frontmatter(_read(repo, name, Path("SKILL.md")))
    description = _yaml_safe(next(line for line in front if line.startswith("description:")))
    target = out / "commands" / f"{PREFIX}{name}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(f"---\n{description}\n---\n{body.rstrip(chr(10))}\n\n$ARGUMENTS\n")


def _agents(repo: Path, out: Path) -> None:
    """Task 3 renders the agent files here."""


def _routing(repo: Path, out: Path) -> None:
    """Task 3 renders the routing instructions (`AGENTS.md`) here."""


def render_content(repo: Path, out: Path) -> None:
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    for skill in SKILLS:
        _skill(repo, skill, out)
    for command in COMMANDS:
        _command(repo, command, out)
    _agents(repo, out)
    _routing(repo, out)


def main() -> int:
    render_content(paths.root(), CONTENT)
    print(
        f"rendered {sum(1 for path in CONTENT.rglob('*') if path.is_file())} files into {CONTENT}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
