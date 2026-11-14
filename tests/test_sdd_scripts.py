"""The subagent-driven-development helper scripts, run on a synthetic plan in a temporary repo."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from support import REPO, git

SCRIPTS = REPO / "skills" / "subagent-driven-development" / "scripts"
NAMES = ("sdd-workspace", "task-brief", "review-package")

PLAN = """# Demo plan

### Task 1: First

Do the first thing.

```
### Task 2: not a heading, it sits in a fence
```

### Task 2: Second

Do the second thing.

### Task 10: Tenth

Do the tenth thing.
"""


def run(script: str, *args: str | Path, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPTS / script), *map(str, args)],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "docs").mkdir(parents=True)
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "user.name", "Test")
    (root / "docs/plan.md").write_text(PLAN, encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "plan")
    return root


def test_scripts_are_executable_and_free_of_private_paths() -> None:
    for name in NAMES:
        script = SCRIPTS / name
        assert os.access(script, os.X_OK), name
        text = script.read_text(encoding="utf-8")
        assert text.startswith("#!/usr/bin/env bash\n")
        assert "/home/" not in text
        assert "~/" not in text


def test_workspace_lives_under_superpowers_sdd_and_ignores_itself(repo: Path) -> None:
    done = run("sdd-workspace", "docs/plan.md", cwd=repo)
    assert done.returncode == 0, done.stderr
    workspace = Path(done.stdout.strip())
    assert workspace == (repo / ".superpowers/sdd/plan").resolve()
    assert (workspace / "plan-path").read_text(encoding="utf-8") == "docs/plan.md\n"
    assert (repo / ".superpowers/sdd/.gitignore").read_text(encoding="utf-8") == "*\n"
    assert git(repo, "status", "--porcelain") == ""


def test_workspace_separates_two_plans_with_one_file_name(repo: Path) -> None:
    other = repo / "notes"
    other.mkdir()
    (other / "plan.md").write_text(PLAN, encoding="utf-8")
    first = run("sdd-workspace", "docs/plan.md", cwd=repo).stdout.strip()
    second = run("sdd-workspace", "notes/plan.md", cwd=repo).stdout.strip()
    assert first != second
    assert Path(second).name == "plan-notes"


def test_task_brief_extracts_one_task(repo: Path) -> None:
    done = run("task-brief", "docs/plan.md", "2", cwd=repo)
    assert done.returncode == 0, done.stderr
    brief = repo / ".superpowers/sdd/plan/task-2-brief.md"
    assert done.stdout.startswith(f"wrote {brief.resolve()}")
    text = brief.read_text(encoding="utf-8")
    assert text.startswith("### Task 2: Second")
    assert "Do the second thing." in text
    assert "Do the first thing." not in text
    assert "Do the tenth thing." not in text


def test_task_brief_keeps_a_task_heading_inside_a_fence(repo: Path) -> None:
    text = run("task-brief", "docs/plan.md", "1", cwd=repo)
    assert text.returncode == 0
    brief = (repo / ".superpowers/sdd/plan/task-1-brief.md").read_text(encoding="utf-8")
    assert "### Task 2: not a heading, it sits in a fence" in brief
    assert "Do the second thing." not in brief


def test_task_brief_matches_a_whole_task_number(repo: Path) -> None:
    run("task-brief", "docs/plan.md", "1", cwd=repo)
    done = run("task-brief", "docs/plan.md", "10", cwd=repo)
    assert done.returncode == 0
    brief = (repo / ".superpowers/sdd/plan/task-10-brief.md").read_text(encoding="utf-8")
    assert brief.startswith("### Task 10: Tenth")
    assert "Do the first thing." not in brief


def test_task_brief_reports_a_missing_task(repo: Path) -> None:
    done = run("task-brief", "docs/plan.md", "7", cwd=repo)
    assert done.returncode == 3
    assert "task 7 not found" in done.stderr


def test_task_brief_writes_to_a_named_file(repo: Path, tmp_path: Path) -> None:
    out = tmp_path / "brief.md"
    done = run("task-brief", "docs/plan.md", "1", out, cwd=repo)
    assert done.returncode == 0, done.stderr
    assert out.read_text(encoding="utf-8").startswith("### Task 1: First")


def test_task_brief_needs_a_plan_file(repo: Path) -> None:
    assert run("task-brief", "docs/missing.md", "1", cwd=repo).returncode == 2
    assert run("task-brief", cwd=repo).returncode == 2


def test_review_package_lists_commits_stat_and_diff(repo: Path) -> None:
    base = git(repo, "rev-parse", "HEAD").strip()
    (repo / "a.txt").write_text("one\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "add a")
    (repo / "a.txt").write_text("one\ntwo\n", encoding="utf-8")
    git(repo, "commit", "-q", "-am", "extend a")
    head = git(repo, "rev-parse", "HEAD").strip()
    done = run("review-package", "docs/plan.md", base, head, cwd=repo)
    assert done.returncode == 0, done.stderr
    assert "2 commit(s)" in done.stdout
    package = next((repo / ".superpowers/sdd/plan").glob("review-*..*.diff"))
    text = package.read_text(encoding="utf-8")
    assert "add a" in text
    assert "extend a" in text
    assert " a.txt | 2 ++" in text
    assert "+two" in text


def test_review_package_refuses_a_head_that_is_not_a_descendant(repo: Path) -> None:
    git(repo, "switch", "-q", "-c", "side")
    (repo / "b.txt").write_text("b\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "side")
    side = git(repo, "rev-parse", "HEAD").strip()
    git(repo, "switch", "-q", "main")
    (repo / "c.txt").write_text("c\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "main")
    main = git(repo, "rev-parse", "HEAD").strip()
    done = run("review-package", "docs/plan.md", main, side, cwd=repo)
    assert done.returncode == 3
    assert "not a descendant" in done.stderr


def test_review_package_refuses_an_empty_range(repo: Path) -> None:
    head = git(repo, "rev-parse", "HEAD").strip()
    done = run("review-package", "docs/plan.md", head, head, cwd=repo)
    assert done.returncode == 3
    assert "empty commit range" in done.stderr


@pytest.mark.shellcheck
def test_scripts_pass_shellcheck() -> None:
    # The dev dependency `shellcheck-py` puts the binary next to the interpreter, as ruff's is.
    venv_bin = str(Path(sys.executable).parent)
    shellcheck = shutil.which("shellcheck", path=venv_bin) or shutil.which("shellcheck")
    if shellcheck is None:  # a test marked `shellcheck` fails loudly: a skip would hide a gap
        pytest.fail("shellcheck is missing: run `uv sync` (the dev group holds shellcheck-py)")
    done = subprocess.run(
        [shellcheck, *(str(SCRIPTS / name) for name in NAMES)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stdout
