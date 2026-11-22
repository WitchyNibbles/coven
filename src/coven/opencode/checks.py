"""`coven opencode doctor`: the checks, in order, and how their results print."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Final

from coven.doctor import one_line
from coven.errors import CovenError
from coven.opencode.canary import read_server
from coven.opencode.check_types import Context, Outcome, Read, Server
from coven.opencode.checks_runtime import (
    config_check,
    coven_root_check,
    key_check,
    paths_check,
    project_config_check,
    runtime_check,
)
from coven.opencode.checks_server import plugin_check, skills_check
from coven.opencode.profile import Layout, OpencodeError, Profile, load_profile
from coven.opencode.runtime import binary_path, isolated_env, run_env

__all__ = ["CHECKS", "Outcome", "exit_code", "render", "run_doctor"]

type Check = Callable[[Context], Outcome]

CHECKS: Final[tuple[tuple[str, Check], ...]] = (
    ("runtime", runtime_check),
    ("paths", paths_check),
    ("config", config_check),
    ("key", key_check),
    ("plugin", plugin_check),
    ("skills", skills_check),
    ("coven root", coven_root_check),
    ("project config", project_config_check),
)


def _crash(error: Exception) -> str:
    text = one_line((str(error).splitlines() or [""])[0])
    return text if isinstance(error, CovenError) else f"{type(error).__name__}: {text}".rstrip(": ")


def _guarded(check: Check, ctx: Context) -> Outcome:
    """The check's outcome; a check that raises fails alone with a one-line cause."""
    try:
        return check(ctx)
    except Exception as error:  # a crashing check must not stop the others
        logging.getLogger(__name__).debug("doctor check crashed", exc_info=True)
        return Outcome("fail", _crash(error))


def _profile(layout: Layout) -> tuple[Profile | None, str]:
    try:
        return load_profile(layout), ""
    except OpencodeError as error:
        return None, one_line(str(error))


def _context(layout: Layout, cwd: Path, base: Mapping[str, str], read: Read) -> Context:
    profile, note = _profile(layout)
    env = run_env(profile, layout, base, offline=True) if profile else isolated_env(layout, base)
    binary = binary_path(layout)
    server = Server(read, binary, env, layout.config_dir)
    return Context(layout, cwd, base, profile, note, env, binary, server)


def run_doctor(layout: Layout, cwd: Path, *, read: Read = read_server) -> list[tuple[str, Outcome]]:
    """Every check in order. Plugin and skills share one server read."""
    ctx = _context(layout, cwd, os.environ, read)
    results: list[tuple[str, Outcome]] = []
    for name, check in CHECKS:
        outcome = _guarded(check, ctx)
        if name == "runtime" and outcome.status == "fail":
            ctx.server.block(f"runtime check failed: {outcome.detail}")
        results.append((name, outcome))
    return results


def render(results: list[tuple[str, Outcome]]) -> list[str]:
    """One line per check; the fix follows when the check is not ok."""
    lines = []
    for name, outcome in results:
        fix = f" (fix: {outcome.fix})" if outcome.fix and outcome.status != "ok" else ""
        lines.append(f"{outcome.status} {name}: {outcome.detail}{fix}")
    return lines


def exit_code(results: list[tuple[str, Outcome]]) -> int:
    return 1 if any(outcome.status == "fail" for _, outcome in results) else 0
