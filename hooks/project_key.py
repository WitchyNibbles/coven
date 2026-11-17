"""The project key: the name coven files a repo's memory and handoffs under.

Not a hook itself: `memory_context.py` loads it by path (hooks run under `python3 -I` and are not
a package, so they cannot import each other or `coven`).
"""

import os
import subprocess
from pathlib import Path

GIT_TIMEOUT = 2  # seconds: a SessionStart hook must never hang a session


def project_key(cwd: Path) -> str:
    """The folder name of the main checkout of the repo holding `cwd`, lowercased, spaces as `-`.

    A worktree maps to the checkout that owns it. Outside git, in a folder git cannot read or when
    git is missing, the key is the name of `cwd` itself.
    """
    return normalise(_main_checkout_name(cwd) or Path(os.path.abspath(cwd)).name)


def _main_checkout_name(cwd: Path) -> str | None:
    # --path-format needs git 2.31; older gits print the path relative to cwd, resolved below.
    for options in (["--path-format=absolute", "--git-common-dir"], ["--git-common-dir"]):
        common = git_output(cwd, ["rev-parse", *options])
        if common:
            return Path(os.path.abspath(Path(cwd) / common)).parent.name or None
    return None


def git_output(cwd: Path, args: list[str]) -> str | None:
    """The trimmed stdout of `git -C cwd <args>`; None when git is missing, fails or says nothing."""
    try:
        done = subprocess.run(
            ["git", "-C", str(cwd), *args],
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired, ValueError):  # ValueError: output not UTF-8
        return None
    output = done.stdout.strip()
    return output if done.returncode == 0 and output else None


def normalise(name: str) -> str:
    """coven's key form of a folder name; `coven memory adopt` renames store folders to it."""
    return name.lower().replace(" ", "-")
