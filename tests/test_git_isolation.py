"""Tests run git against test-owned config and never against the repo that started pytest."""

import os
import subprocess
import sys
from pathlib import Path

from support import REPO, git

INNER = "tests/memory/test_init.py::test_init_layout_and_hook"


def test_an_exported_git_dir_does_not_reach_the_outer_repo(tmp_path: Path) -> None:
    outer = tmp_path / "outer"
    outer.mkdir()
    git(outer, "init", "-q")
    config = outer / ".git" / "config"
    before = config.read_bytes()
    env = {**os.environ, "GIT_DIR": str(outer / ".git"), "GIT_WORK_TREE": str(outer)}
    inner = subprocess.run(
        [sys.executable, "-m", "pytest", INNER, "tests/test_doctor.py", "-q", "-p", "no:randomly"],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert inner.returncode == 0, inner.stdout[-2000:]
    assert config.read_bytes() == before
    assert git(outer, "status", "--porcelain") == ""


def test_git_runs_no_background_maintenance_in_tests(tmp_path: Path) -> None:
    # A detached `git maintenance` after a commit holds .git/objects/maintenance.lock, and a test
    # that copies the repo then races it (seen on GitHub's runner, 2026-11-14).
    git(tmp_path, "init", "-q")
    assert git(tmp_path, "config", "--get", "maintenance.auto").strip() == "false"
    assert git(tmp_path, "config", "--get", "gc.auto").strip() == "0"
