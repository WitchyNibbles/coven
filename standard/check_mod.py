"""Claude Code mod parts of coven's quality check. Standard library only.

A mod is a plugin whose hooks file lists TypeScript `modules`. Claude Code's own tools check it:
`claude plugin validate --strict` type-checks it and `claude plugin test` runs its tests. Copied
next to `check.py` into a repo's `.coven/`; `check.py` loads it, after `check_ts.py`, when the repo
has a mod.
"""

from __future__ import annotations

import importlib
import os
import re
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import check_repo
    import check_stages
    import check_ts
else:
    check_repo = importlib.import_module("coven_check_repo")
    check_stages = importlib.import_module("coven_check_stages")
    check_ts = importlib.import_module("coven_check_ts")

# The standard's version: the same in every module of it (check.py refuses a mismatch).
STANDARD_VERSION = 6

# The files of a mod scanned for suppressions: the ones the TypeScript projects scan.
MOD_SUFFIXES = check_repo.SCANNED_TS_SUFFIXES
CLAUDE = "claude"
# The summary lines of `claude plugin test`: ` 253 pass`, ` 0 fail`.
PASS_COUNT = re.compile(r"^\s*(\d+) pass\s*$", re.MULTILINE)
FAIL_COUNT = re.compile(r"^\s*(\d+) fail\s*$", re.MULTILINE)
NOT_APPLICABLE: check_stages.Outcome = ("n/a", True)  # a mod has no formatter, linter or coverage


def _claude(root: Path, *args: str) -> tuple[int, str]:
    """Run Claude Code from the mod's directory, with the caller's environment."""
    try:
        done = subprocess.run(
            [CLAUDE, *args],
            cwd=root,
            env=os.environ.copy(),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError as error:
        return 127, f"{CLAUDE}: cannot be run ({error})"
    return done.returncode, done.stdout + done.stderr


def _types_stage(project: check_repo.Project, where: str) -> check_stages.Outcome:
    """`claude plugin validate --strict`: the manifest, the hooks file and the TypeScript."""
    code, output = _claude(project.root, "plugin", "validate", "--strict", str(project.root))
    if code == 0:
        return "ok", True
    check_stages.show_tail(f"mod types ({where})", output)
    return "fail", False


def _counts(output: str) -> tuple[int, int] | None:
    """The passed and failed test counts `claude plugin test` printed; None without a pass line."""
    passed = PASS_COUNT.search(output)
    if passed is None:
        return None
    failed = FAIL_COUNT.search(output)
    return int(passed.group(1)), int(failed.group(1)) if failed else 0


def _tests_stage(project: check_repo.Project, where: str, mode: str) -> check_stages.Outcome:
    """`claude plugin test`: every test file of the mod, whatever tests were named."""
    code, output = _claude(project.root, "plugin", "test", str(project.root))
    counts = _counts(output)
    if code == 0 and counts is None:
        print("tests: claude plugin test printed no pass count")
    elif code == 0 and counts is not None and counts[1] == 0:
        if mode == "full" and counts[0] == 0:
            print("tests: no tests ran (full mode needs at least one passing test)")
        else:
            return str(counts[0]), True
    check_stages.show_tail(f"mod tests ({where})", output)
    return "fail", False


def mod_stages(
    project: check_repo.Project,
    projects: list[check_repo.Project],
    run: check_stages.Run,
    *,
    run_tests: bool = True,
) -> check_stages.Stages:
    """A mod's stages. `run_tests` False (fast mode, no test of the mod named) counts no tests.

    Settings: no framework component the standard cannot check. Suppressions: the TypeScript
    projects' scan. Types and tests: Claude Code's tools. Format, lint and coverage: `n/a`.
    """
    where = check_repo.where(run.repo, project)
    files = check_repo.TsFiles.of(project, projects)
    problems = check_repo.framework_problems(files)
    for problem in problems:
        print(f"settings: {problem}")
    settings: check_stages.Outcome = ("fail", False) if problems else ("ok", True)
    suppressions = check_ts.ts_suppressions(project, files.scanned(), files.skipped)
    types = _types_stage(project, where)
    tests: check_stages.Outcome = (
        _tests_stage(project, where, run.mode) if run_tests else ("0", True)
    )
    return check_stages.Stages(
        settings=settings,
        suppressions=suppressions,
        format=NOT_APPLICABLE,
        lint=NOT_APPLICABLE,
        types=types,
        tests=tests,
        coverage=NOT_APPLICABLE,
        skipped=files.skipped_count(),
    )
