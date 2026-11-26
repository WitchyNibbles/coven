"""The doctor line for PM shell isolation: run the wrapper on `true` and report what it says."""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Final

from coven import paths
from coven.doctor import one_line
from coven.opencode.check_types import Context, Outcome

HOOK: Final = "hooks/pm-shell-isolate.sh"
TIMEOUT: Final = 10.0
REAL_SHELL: Final = "/bin/sh"
# The wrapper's refusal is `<PREFIX><missing piece><SUFFIX>`; a PM never sees these two test seams.
PREFIX: Final = "not available here: "
SUFFIX: Final = "; ask the user"
SEAMS: Final = ("COVEN_ISOLATE_SYS", "COVEN_ISOLATE_PATH")


def _not_available(piece: str) -> Outcome:
    return Outcome("fail", f"not available: {piece}")


def _missing_piece(stderr: str, code: int) -> str:
    """The piece the wrapper names, else its stderr as one line, else the exit code."""
    for line in stderr.splitlines():
        start = line.find(PREFIX)
        if start < 0:
            continue
        piece = line[start + len(PREFIX) :]
        return one_line(piece.removesuffix(SUFFIX))
    return one_line(stderr) or f"exit code {code}"


def probe_isolation(wrapper: Path, base: Mapping[str, str], timeout: float = TIMEOUT) -> Outcome:
    """Run `wrapper -c true` the way a PM's shell runs it, on a temp folder to hide."""
    if not (wrapper.is_file() and os.access(wrapper, os.X_OK)):
        return _not_available(f"wrapper not found: {wrapper}")
    env = {key: value for key, value in base.items() if key not in SEAMS}
    env["COVEN_PM_REAL_SHELL"] = REAL_SHELL
    with (
        tempfile.TemporaryDirectory(prefix="coven-isolation-") as hide,
        tempfile.TemporaryDirectory(prefix="coven-isolation-pids-") as pids,
    ):
        env["COVEN_PM_HIDE"] = hide
        env["COVEN_PM_PIDS"] = pids  # the wrapper's pid record goes here and goes with the folder
        try:
            code, stderr = _run(wrapper, env, timeout)
        except subprocess.TimeoutExpired:
            return _not_available(f"timed out after {timeout:g} s")
        except OSError as error:
            return _not_available(one_line(f"cannot run {wrapper}: {error}"))
    if code == 0:
        return Outcome("ok", "ok")
    return _not_available(_missing_piece(stderr, code))


def _run(wrapper: Path, env: Mapping[str, str], timeout: float) -> tuple[int, str]:
    """The exit code and stderr of `wrapper -c true`, run in a group of its own.

    On a timeout the whole group is killed, so a child the wrapper started does not outlive it.
    """
    with subprocess.Popen(
        [str(wrapper), "-c", "true"],
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
        start_new_session=True,
    ) as process:
        try:
            _, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            raise
        return process.returncode, stderr


def isolation_check(ctx: Context) -> Outcome:
    return ctx.isolation(paths.root() / HOOK, ctx.base)
