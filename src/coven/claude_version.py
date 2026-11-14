"""The installed Claude Code version, and the range coven was built for."""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable

from coven.errors import CovenError

# Cross-session messaging and `claude --bg` need this release.
MINIMUM = (2, 1, 224)
# The release the builder ran against; `coven doctor` warns about anything newer.
LAST_TESTED = (2, 1, 325)
TIMEOUT = 10
COMMAND = ["claude", "--version"]
# `claude --version` prints `2.1.325 (Claude Code)`. The first three dotted numbers on the first
# line count, so a bare `2.1.325`, a `v` prefix, a leading product name and a `-beta.1` suffix read
# too.
VERSION = re.compile(r"(\d+)\.(\d+)\.(\d+)")
SHOWN = "`claude --version`"

type Version = tuple[int, int, int]
type Runner = Callable[[list[str]], subprocess.CompletedProcess[str]]


def run_command(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        capture_output=True,
        encoding="utf-8",
        errors="replace",  # a stray byte must not crash the version check
        timeout=TIMEOUT,
        check=False,
    )


def dotted(version: Version) -> str:
    return ".".join(str(part) for part in version)


def installed(run: Runner | None = None) -> Version:
    """The installed version; raises `CovenError` when `claude` is missing, unreadable or too old.

    `run` stands in for the subprocess in tests.
    """
    runner = run or run_command
    try:
        done = runner(list(COMMAND))
    except FileNotFoundError as error:
        raise CovenError("claude not found on PATH") from error
    except subprocess.TimeoutExpired as error:
        raise CovenError(f"{SHOWN} did not answer in {TIMEOUT} s") from error
    except OSError as error:  # strerror only: the error's text may name an absolute path
        raise CovenError(f"cannot run claude: {error.strerror or 'input/output error'}") from error
    if done.returncode != 0:
        raise CovenError(f"{SHOWN} exited {done.returncode}")
    found = VERSION.search(done.stdout.strip().split("\n", 1)[0])
    if found is None:
        raise CovenError(f"could not read the Claude Code version from {SHOWN}")
    major, minor, patch = (int(part) for part in found.groups())
    version = (major, minor, patch)
    if version < MINIMUM:
        raise CovenError(f"Claude Code {dotted(version)} is older than {dotted(MINIMUM)}")
    return version
