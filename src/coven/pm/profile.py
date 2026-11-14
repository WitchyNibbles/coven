"""The permission profile every PM starts with, and the command that starts one.

The profile rides on the launch line, so the user's own sessions keep whatever mode they use. Both
`coven pm launch` and `coven pm resume` print a command built here; nothing in this module starts
a session.
"""

from __future__ import annotations

import json
import shlex
from pathlib import Path

from coven.pm.rows import PM_NAME_PREFIX, PmError
from coven.pm.text import clean_line, is_valid_code

AGENT = "coven:project-manager"
PERMISSION_MODE = "bypassPermissions"
ROLE = "pm"
# These prompt even in bypass mode; the pm-permissions hook decides each one.
ASK = ["Bash(git push:*)", "Bash(git merge:*)", "Bash(gh:*)"]
DENY = [
    "Bash(gh pr merge:*)",
    "Bash(git push --force:*)",
    "Bash(git push -f:*)",
    "Bash(git push --force-with-lease:*)",
    "Bash(git push origin main:*)",
    "Bash(git push origin master:*)",
]


def pm_settings(memory_path: Path) -> dict[str, object]:
    """The `--settings` JSON of a PM: its role, its prompts and what it may never do.

    A file rule with one leading slash is relative to the settings source, so the memory store's
    rules take an extra slash to name the absolute path. A store reached through a symbolic link
    is named by both its own path and the real one, since a rule matches only the path a tool
    names.
    """
    stores = list(dict.fromkeys((memory_path.absolute(), memory_path.resolve())))
    rules = [f"{tool}(/{store}/**)" for store in stores for tool in ("Write", "Edit")]
    return {
        "env": {"COVEN_SESSION_ROLE": ROLE},
        "crossSessionInbound": "accept",
        "permissions": {
            "ask": list(ASK),
            "deny": [*DENY, *rules],
        },
    }


def session_flags(code: str, memory_path: Path) -> str:
    """The flags both a launch and a resume carry: the name, the agent and the profile."""
    if not is_valid_code(code):
        raise PmError(f"not a project code: {clean_line(code)}")
    settings = json.dumps(pm_settings(memory_path), separators=(",", ":"))
    return (
        f"--name {PM_NAME_PREFIX}{code} --agent {AGENT} --permission-mode {PERMISSION_MODE}"
        f" --settings {shlex.quote(settings)}"
    )


def refuse_option_like(text: str, what: str) -> None:
    """Refuse `text` that starts with `-`: `claude` would take it for a flag, not a prompt."""
    if text.startswith("-"):
        raise PmError(f"the {what} starts with '-': claude would read it as a flag")


def _refuse_option_like_brief(brief: Path) -> None:
    try:
        with brief.open(encoding="utf-8", errors="replace") as file:
            first = file.read(1)
    except OSError as error:
        raise PmError(f"cannot read the brief: {error.strerror or 'input/output error'}") from error
    refuse_option_like(first, "brief")


def launch_command(code: str, repo: Path, brief: Path, memory_path: Path) -> str:
    """The line the GM runs to start the PM `pm-<code>` in `repo`, with `brief` as its prompt."""
    flags = session_flags(code, memory_path)
    _refuse_option_like_brief(brief)
    return f'cd {shlex.quote(str(repo))} && claude --bg {flags} "$(cat {shlex.quote(str(brief))})"'
