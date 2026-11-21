"""The receipt line and what the stages, setup errors and bases put into it."""

import configparser
import copy
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from support import CHECK, CORE, GOOD, REPO, TEST_CORE, commit_all, dump_toml, git, receipt

FAST_TAIL = " dirty=no projects=1 settings=ok suppressions=0 skipped=0 format=ok lint=ok types=ok"
OVERRIDE_TAIL = " runner=override"


def append(path: Path, text: str) -> None:
    path.write_text(path.read_text() + text)


def test_fast_all_green(run_check, py_project):
    out = run_check(py_project, "fast", "tests/test_core.py")
    assert out.returncode == 0
    assert receipt(out).startswith("CHECK fast exit=0 sha=")
    tail = f"{FAST_TAIL} tests=1 coverage=skip base=- runner=override"
    assert tail in out.stdout


def test_fast_without_tests_reports_zero(run_check, py_project):
    out = run_check(py_project, "fast")
    assert out.returncode == 0
    assert receipt(out).endswith("tests=0 coverage=skip base=- runner=override")


def test_dirty_read_before_stages(run_check, py_project):
    (py_project / ".gitignore").unlink()
    commit_all(py_project)
    out = run_check(py_project, "fast", "tests/test_core.py")
    assert " dirty=no " in receipt(out)
    assert git(py_project, "status", "--porcelain").strip()  # the tools did write caches


def test_dirty_tree_is_reported(run_check, py_project):
    append(
        py_project / "src/pkg/core.py", "\n\ndef sub(a: int, b: int) -> int:\n    return a - b\n"
    )
    assert " dirty=yes " in receipt(run_check(py_project, "fast"))


def test_suppression_fails_but_other_stages_run(run_check, py_project):
    append(py_project / "src/pkg/core.py", "\n\nZ = 1  # noqa\n")
    commit_all(py_project)
    out = run_check(py_project, "fast")
    assert "src/pkg/core.py:5: # noqa" in out.stdout
    assert out.returncode == 1
    last = receipt(out)
    assert " exit=1 " in last
    assert " suppressions=1 " in last
    assert " format=ok lint=" in last
    assert " types=ok " in last


def test_untokenizable_file_fails_the_suppressions_stage(run_check, py_project):
    (py_project / "src/pkg/broken.py").write_text("def broken(:  # noqa\n")
    commit_all(py_project)
    out = run_check(py_project, "fast")
    last = receipt(out)
    assert out.returncode == 1
    assert " suppressions=1 " in last  # counted by line when the tokenizer gives up
    assert (
        "src/pkg/broken.py: could not be tokenized (suppressions cannot be counted)" in out.stdout
    )
    assert "src/pkg/broken.py:1: # noqa" in out.stdout
    assert "Traceback" not in out.stdout + out.stderr


def test_notebook_that_is_not_json_fails_with_its_own_wording(run_check, py_project):
    (py_project / "src/pkg/explore.ipynb").write_text("{not json  # noqa\n")
    commit_all(py_project)
    out = run_check(py_project, "fast")
    assert (
        "src/pkg/explore.ipynb: could not be read as a notebook (suppressions cannot be counted)"
        in out.stdout
    )
    assert " suppressions=1 " in receipt(out)
    assert out.returncode == 1


def test_untokenizable_file_fails_even_with_nothing_to_count(run_check, py_project):
    (py_project / "src/pkg/broken.py").write_text("def broken(:\n")
    commit_all(py_project)
    out = run_check(py_project, "fast")
    assert " suppressions=0 " in receipt(out)
    assert "src/pkg/broken.py: could not be tokenized" in out.stdout
    assert out.returncode == 1


def test_notebook_suppression_is_counted(run_check, py_project):
    cell = {
        "cell_type": "code",
        "execution_count": None,
        "id": "c1",
        "metadata": {},
        "outputs": [],
        "source": ["import os  # noqa: F401\n"],
    }
    book = {"cells": [cell], "metadata": {}, "nbformat": 4, "nbformat_minor": 5}
    (py_project / "src/pkg/explore.ipynb").write_text(json.dumps(book))
    commit_all(py_project)
    out = run_check(py_project, "fast")
    assert "src/pkg/explore.ipynb:cell 1:1: # noqa: F401" in out.stdout
    assert " suppressions=1 " in receipt(out)
    assert out.returncode == 1


def test_notebook_cell_magic_cell_does_not_hide_other_cells(run_check, py_project):
    def cell(source):
        return {"cell_type": "code", "metadata": {}, "outputs": [], "source": source}

    book = {
        "cells": [cell(["%%bash\n", "echo it's done\n"]), cell(["import os  # noqa: F401\n"])],
        "metadata": {},
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    (py_project / "src/pkg/explore.ipynb").write_text(json.dumps(book))
    commit_all(py_project)
    out = run_check(py_project, "fast")
    assert "src/pkg/explore.ipynb:cell 2:1: # noqa: F401" in out.stdout
    assert " suppressions=1 " in receipt(out)
    assert "could not be tokenized" not in out.stdout
    assert out.returncode == 1


@pytest.mark.parametrize("assignment", ["x = %who", "x = !echo"])
def test_notebook_assignment_escape_cannot_hide_a_suppression(run_check, py_project, assignment):
    def book(second):
        source = [f'{assignment} """\n', second, '# end """']
        cell = {"cell_type": "code", "metadata": {}, "outputs": [], "source": source}
        return {"cells": [cell], "metadata": {}, "nbformat": 4, "nbformat_minor": 5}

    path = py_project / "src/pkg/explore.ipynb"
    path.write_text(json.dumps(book("y = undefined_name  # noqa: F821\n")))
    commit_all(py_project)
    out = run_check(py_project, "fast")
    assert "src/pkg/explore.ipynb:cell 1:2: # noqa: F821" in out.stdout
    assert " suppressions=1 " in receipt(out)
    path.write_text(json.dumps(book("y = undefined_name\n")))
    commit_all(py_project)
    assert " suppressions=0 " in receipt(run_check(py_project, "fast"))


def test_no_type_check_is_a_suppression(run_check, py_project):
    (py_project / "src/pkg/loose.py").write_text(
        "from typing import no_type_check\n\n\n@no_type_check\ndef loose(x):\n    return x\n"
    )
    commit_all(py_project)
    out = run_check(py_project, "fast")
    assert "src/pkg/loose.py:4: @no_type_check" in out.stdout
    assert " types=ok " in receipt(out)  # mypy alone would let it through
    assert " suppressions=2 " in receipt(out)
    assert out.returncode == 1


def test_settings_problem_fails_the_stage(run_check, py_project):
    (py_project / "mypy.ini").write_text("[mypy]\n")
    commit_all(py_project)
    out = run_check(py_project, "fast")
    assert "mypy.ini: not allowed" in out.stdout
    assert " settings=fail " in receipt(out)
    assert out.returncode == 1


def test_format_failure(run_check, py_project):
    (py_project / "src/pkg/core.py").write_text("def add(a:int,b:int)->int:\n    return a+b\n")
    commit_all(py_project)
    out = run_check(py_project, "fast")
    assert " format=fail lint=ok types=ok " in receipt(out)
    assert out.returncode == 1


def test_lint_failure(run_check, py_project):
    append(
        py_project / "src/pkg/core.py", "\n\ndef bad(items: list[int] = []) -> None:\n    pass\n"
    )
    commit_all(py_project)
    out = run_check(py_project, "fast")
    assert " lint=fail " in receipt(out)
    assert "B006" in out.stdout
    assert out.returncode == 1


def test_type_failure(run_check, py_project):
    append(py_project / "src/pkg/core.py", "\n\ndef untyped(x):\n    return x\n")
    commit_all(py_project)
    out = run_check(py_project, "fast")
    assert " types=fail " in receipt(out)
    assert out.returncode == 1


def test_failing_test_is_reported(run_check, py_project):
    (py_project / "tests/test_core.py").write_text(TEST_CORE.replace("== 3", "== 4"))
    commit_all(py_project)
    out = run_check(py_project, "fast", "tests/test_core.py")
    assert " tests=fail " in receipt(out)
    assert out.returncode == 1


LONG_FAILURES = (
    "def test_first():\n    assert list(range(30)) == list(range(1, 31))\n\n\n"
    "def test_second():\n    assert list(range(30)) == list(range(2, 32))\n"
)


def test_every_failing_test_is_named_when_their_messages_are_long(run_check, py_project):
    # With CI set, pytest prints each failure's whole message in its summary, so the last one
    # alone fills the tail of the output.
    (py_project / "tests/test_long.py").write_text(LONG_FAILURES)
    commit_all(py_project)
    out = run_check(py_project, "fast", "tests/test_long.py", env={"CI": "true"})
    assert " tests=fail " in receipt(out)
    assert "FAILED tests/test_long.py::test_first" in out.stdout
    assert "FAILED tests/test_long.py::test_second" in out.stdout


MANY_FAILURES = (
    "import pytest\n\n\n"
    '@pytest.mark.parametrize("n", range(22))\n'
    "def test_n(n):\n    assert n < 0\n"
)


def test_failing_tests_past_the_limit_are_counted(run_check, py_project):
    (py_project / "tests/test_many.py").write_text(MANY_FAILURES)
    commit_all(py_project)
    out = run_check(py_project, "fast", "tests/test_many.py", env={"CI": None})
    lines = out.stdout.splitlines()
    assert "--- tests: 22 failed or errored ---" in lines
    assert "FAILED tests/test_many.py::test_n[0]" in lines  # without its " - assert ..." message
    assert "FAILED tests/test_many.py::test_n[19]" in lines
    assert "FAILED tests/test_many.py::test_n[20]" not in lines
    assert "tests: and 2 more" in lines


def test_fast_with_paths_that_collect_nothing_counts_zero(run_check, py_project):
    (py_project / "tests/test_empty.py").write_text("")
    commit_all(py_project)
    out = run_check(py_project, "fast", "tests/test_empty.py")
    assert " tests=0 " in receipt(out)
    assert out.returncode == 0


def add_sub(root: Path, covered: bool) -> None:
    git(root, "checkout", "-q", "-b", "feature")
    append(root / "src/pkg/core.py", "\n\ndef sub(a: int, b: int) -> int:\n    return a - b\n")
    if covered:
        append(
            root / "tests/test_core.py",
            "\n\ndef test_sub():\n    from pkg.core import sub\n\n    assert sub(3, 1) == 2\n",
        )
    commit_all(root)


def short(root: Path, ref: str) -> str:
    return git(root, "rev-parse", "--short=7", ref).strip()


def test_full_mode_coverage(run_check, py_project):
    add_sub(py_project, covered=True)
    out = run_check(py_project, "full", "--base", "main")
    assert out.returncode == 0
    assert receipt(out).endswith(
        f" tests=2 coverage=100 base={short(py_project, 'main')} runner=override"
    )
    assert not list(py_project.rglob("coverage.xml"))
    assert not (py_project / ".coverage").exists()
    assert git(py_project, "status", "--porcelain").strip() == ""


WORKER_MARK = (
    "import os\n\n\n"
    "def test_worker_mark() -> None:\n"
    '    with open(os.environ["WORKER_MARK_FILE"], "a") as handle:\n'
    '        handle.write(os.environ.get("PYTEST_XDIST_WORKER", "serial") + "\\n")\n'
)
# The same mark from four tests.
FOUR_WORKER_MARKS = (
    "import os\n\nimport pytest\n\n\n"
    '@pytest.mark.parametrize("index", range(4))\n'
    "def test_worker_mark(index: int) -> None:\n"
    '    with open(os.environ["WORKER_MARK_FILE"], "a") as handle:\n'
    '        handle.write(os.environ.get("PYTEST_XDIST_WORKER", "serial") + "\\n")\n'
)


def marked_workers(py_project: Path, tmp_path: Path, run_check, mode: str, *args: str) -> list[str]:
    """The xdist worker names the mark test saw in a run of `mode` (`serial` without xdist)."""
    (py_project / "tests/test_worker.py").write_text(WORKER_MARK)
    commit_all(py_project)
    mark = tmp_path / "workers.txt"
    out = run_check(py_project, mode, *args, env={"WORKER_MARK_FILE": str(mark)})
    assert out.returncode == 0, out.stdout
    return mark.read_text().split()


def test_full_mode_runs_the_tests_in_parallel(run_check, py_project, tmp_path):
    workers = marked_workers(py_project, tmp_path, run_check, "full", "--base", "main")
    assert len(workers) == 1
    assert workers[0].startswith("gw")


def test_fast_mode_runs_the_tests_serially(run_check, py_project, tmp_path):
    workers = marked_workers(py_project, tmp_path, run_check, "fast", "tests/test_worker.py")
    assert workers == ["serial"]


def test_full_mode_honours_the_worker_count_variable(run_check, py_project, tmp_path):
    # Four marks and one allowed worker: the variable must reach pytest unscrubbed.
    (py_project / "tests/test_worker.py").write_text(FOUR_WORKER_MARKS)
    commit_all(py_project)
    mark = tmp_path / "workers.txt"
    out = run_check(
        py_project,
        "full",
        "--base",
        "main",
        env={"WORKER_MARK_FILE": str(mark), "PYTEST_XDIST_AUTO_NUM_WORKERS": "1"},
    )
    assert out.returncode == 0, out.stdout
    assert mark.read_text().split() == ["gw0"] * 4


def without_xdist(tmp_path: Path) -> dict[str, str | None]:
    """Environment under which pytest has pytest-cov but not pytest-xdist.

    Plugin autoload is off, and a sitecustomize (the nested pytest runs after the check scrubs
    PYTEST_ADDOPTS, so the variable has to be set inside that process) names pytest-cov.
    """
    shim = tmp_path / "no-xdist"
    shim.mkdir()
    (shim / "sitecustomize.py").write_text(
        'import os\n\nos.environ["PYTEST_ADDOPTS"] = "-p pytest_cov"\n'
    )
    return {"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTHONPATH": f"{shim}{os.pathsep}src"}


def test_full_mode_without_xdist_names_the_fix(run_check, py_project, tmp_path):
    out = run_check(py_project, "full", "--base", "main", env=without_xdist(tmp_path))
    assert out.returncode == 1
    assert "tests: pytest-xdist is missing (run: uv add --dev pytest-xdist)" in out.stdout
    assert " tests=fail " in receipt(out)


def test_fast_mode_without_xdist_still_passes(run_check, py_project, tmp_path):
    out = run_check(py_project, "fast", "tests/test_core.py", env=without_xdist(tmp_path))
    assert out.returncode == 0
    assert "pytest-xdist" not in out.stdout


def test_full_mode_coverage_below_floor_fails(run_check, py_project):
    add_sub(py_project, covered=False)
    out = run_check(py_project, "full", "--base", "main")
    assert out.returncode == 1
    assert " tests=1 coverage=50 base=" in receipt(out)
    assert "--- coverage failed" in out.stdout
    assert "Missing lines" in out.stdout


def test_full_default_base_is_the_merge_base_with_main(run_check, py_project):
    add_sub(py_project, covered=True)
    out = run_check(py_project, "full")
    assert out.returncode == 0
    assert f" coverage=100 base={short(py_project, 'main')} " in receipt(out)


def test_full_default_base_sees_every_commit_on_the_branch(run_check, py_project):
    add_sub(py_project, covered=False)
    (py_project / "README.md").write_text("docs\n")
    commit_all(py_project)
    out = run_check(py_project, "full")
    assert out.returncode == 1
    assert f" coverage=50 base={short(py_project, 'main')} " in receipt(out)


def test_full_default_base_on_the_default_branch_is_the_previous_commit(run_check, py_project):
    append(
        py_project / "src/pkg/core.py", "\n\ndef sub(a: int, b: int) -> int:\n    return a - b\n"
    )
    commit_all(py_project)
    out = run_check(py_project, "full")
    assert f" coverage=50 base={short(py_project, 'HEAD~1')} " in receipt(out)


def test_full_explicit_base_equal_to_head_falls_back_to_the_previous_commit(run_check, py_project):
    append(
        py_project / "src/pkg/core.py", "\n\ndef sub(a: int, b: int) -> int:\n    return a - b\n"
    )
    commit_all(py_project)
    out = run_check(py_project, "full", "--base", "main")
    previous = short(py_project, "HEAD~1")
    assert f"note: base main is HEAD itself, using {previous}" in out.stdout
    assert f" coverage=50 base={previous} " in receipt(out)


def test_full_default_base_prefers_origin_head(run_check, py_project):
    git(py_project, "update-ref", "refs/remotes/origin/trunk", "HEAD")
    git(py_project, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk")
    add_sub(py_project, covered=False)
    (py_project / "README.md").write_text("docs\n")
    commit_all(py_project)
    git(py_project, "branch", "-q", "-f", "main", "HEAD")
    out = run_check(py_project, "full")
    base = short(py_project, "origin/trunk")
    assert f" coverage=50 base={base} " in receipt(out)


def test_full_without_a_default_branch_uses_the_previous_commit(run_check, py_project):
    git(py_project, "branch", "-q", "-m", "main", "work")
    add_sub(py_project, covered=True)
    out = run_check(py_project, "full")
    assert "note: no default branch found; changed lines are measured from HEAD~1" in out.stdout
    assert f" base={short(py_project, 'HEAD~1')} " in receipt(out)


ZERO_SHA = "0" * 40


def test_full_unresolvable_base_falls_back_to_the_default(run_check, py_project):
    add_sub(py_project, covered=True)
    out = run_check(py_project, "full", "--base", ZERO_SHA)
    main = short(py_project, "main")
    assert f"note: base {ZERO_SHA} not found, using {main}" in out.stdout
    assert out.returncode == 0
    assert f"coverage=100 base={main} " in receipt(out)


def test_full_explicit_valid_base_is_used_unchanged(run_check, py_project):
    add_sub(py_project, covered=False)
    out = run_check(py_project, "full", "--base", "main")
    assert "note: base" not in out.stdout
    assert "no base commit" not in out.stdout
    base = short(py_project, "main")
    assert f" tests=1 coverage=50 base={base} " in receipt(out)


def test_full_without_any_base_skips_coverage(run_check, py_project):
    git(py_project, "checkout", "-q", "--orphan", "solo")
    commit_all(py_project)
    git(py_project, "branch", "-q", "-D", "main")
    out = run_check(py_project, "full", "--base", ZERO_SHA)
    assert "note: no base commit to compare; changed-line coverage skipped" in out.stdout
    assert "not found" not in out.stdout
    assert out.returncode == 0
    assert receipt(out).endswith(" tests=1 coverage=skip base=none runner=override")


def test_full_coverage_na_without_changed_lines(run_check, py_project):
    git(py_project, "checkout", "-q", "-b", "docs")
    (py_project / "README.md").write_text("docs\n")
    commit_all(py_project)
    out = run_check(py_project, "full", "--base", "main")
    assert out.returncode == 0
    assert " tests=1 coverage=n/a base=" in receipt(out)


def test_full_mode_without_tests_fails(run_check, py_project):
    shutil.rmtree(py_project / "tests")
    commit_all(py_project)
    out = run_check(py_project, "full", "--base", "main")
    assert " tests=fail " in receipt(out)
    assert out.returncode == 1


FORCE_EXIT_ZERO = "def pytest_sessionfinish(session):\n    session.exitstatus = 0\n"


def test_full_mode_with_zero_tests_and_exit_zero_fails(run_check, py_project):
    (py_project / "tests/test_core.py").unlink()
    (py_project / "tests/conftest.py").write_text(FORCE_EXIT_ZERO)
    commit_all(py_project)
    out = run_check(py_project, "full", "--base", "main")
    assert "tests: no tests ran (full mode needs at least one passing test)" in out.stdout
    assert " tests=fail " in receipt(out)
    assert out.returncode == 1


def test_fast_mode_with_zero_tests_and_exit_zero_counts_zero(run_check, py_project):
    (py_project / "tests/conftest.py").write_text(FORCE_EXIT_ZERO)
    (py_project / "tests/test_empty.py").write_text("")
    commit_all(py_project)
    out = run_check(py_project, "fast", "tests/test_empty.py")
    assert " tests=0 " in receipt(out)
    assert out.returncode == 0


@pytest.mark.parametrize(
    "args",
    [
        ("tests", "--", "--deselect", "tests/test_core.py::test_add"),
        ("--deselect", "tests/test_core.py::test_add", "tests"),
        ("-p", "no:cacheprovider"),
        ("@opts.txt",),
    ],
)
def test_fast_rejects_pytest_options(run_check, py_project, args):
    out = run_check(py_project, "fast", *args)
    assert out.returncode == 2
    assert receipt(out).endswith(' dirty=no error="test paths only, no pytest options"')


def test_missing_uv_lock_is_a_setup_error(run_check, py_project):
    (py_project / "uv.lock").unlink()
    commit_all(py_project)
    out = run_check(py_project, "fast")
    assert out.returncode == 2
    assert receipt(out).endswith(' error="no uv.lock (the standard supports uv projects)"')


OLD_PYTHON = """
import runpy, sys
sys.modules["tomllib"] = None
sys.version_info = (3, 10, 14, "final", 0)
sys.argv = ["check.py", "fast"]
runpy.run_path({check!r}, run_name="__main__")
"""


def test_old_python_gets_a_setup_error_receipt(py_project):
    code = OLD_PYTHON.format(check=str(CHECK))
    out = subprocess.run(
        [sys.executable, "-c", code], cwd=py_project, capture_output=True, text=True, check=False
    )
    assert out.returncode == 2
    assert "Traceback" not in out.stderr
    assert receipt(out) == (
        f"CHECK fast exit=2 sha={short(py_project, 'HEAD')} dirty=no "
        'error="Python 3.11 or newer is needed (found 3.10); with uv:'
        " uv run --no-project --python '>=3.11' python .coven/check.py fast\""
    )


def test_not_a_git_repo(run_check, tmp_path):
    out = run_check(tmp_path, "fast")
    assert out.returncode == 2
    assert receipt(out) == 'CHECK fast exit=2 sha=none dirty=no error="not inside a git work tree"'


def test_git_missing(run_check, tmp_path):
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    out = run_check(tmp_path, "fast", env={"PATH": str(empty)})
    assert out.returncode == 2
    assert receipt(out) == 'CHECK fast exit=2 sha=none dirty=no error="git not found on PATH"'


def test_no_project(run_check, git_repo):
    sha = git(git_repo, "rev-parse", "--short=7", "HEAD").strip()
    out = run_check(git_repo, "fast")
    assert out.returncode == 2
    assert receipt(out) == (
        f'CHECK fast exit=2 sha={sha} dirty=no error="no Python, TypeScript or Claude Code mod project found"'
    )


def test_uv_missing(run_check, py_project, tmp_path):
    only_git = tmp_path / "only-git"
    only_git.mkdir()
    (only_git / "git").symlink_to(shutil.which("git"))
    out = run_check(py_project, "fast", env={"COVEN_CHECK_RUNNER": None, "PATH": str(only_git)})
    assert out.returncode == 2
    assert " exit=2 " in receipt(out)
    assert receipt(out).endswith(' dirty=no error="uv not found on PATH"')


def test_runner_override_replaces_uv(run_check, py_project, tmp_path):
    only_git = tmp_path / "only-git"
    only_git.mkdir()
    (only_git / "git").symlink_to(shutil.which("git"))
    out = run_check(py_project, "fast", env={"PATH": str(only_git)})
    assert out.returncode == 0
    assert FAST_TAIL in receipt(out)


def test_failure_output_is_capped_at_twenty_lines(run_check, py_project):
    many = "".join(f"\n\ndef bad{n}(items: list[int] = []) -> None:\n    pass\n" for n in range(30))
    append(py_project / "src/pkg/core.py", many)
    commit_all(py_project)
    out = run_check(py_project, "fast")
    assert " lint=fail " in receipt(out)
    assert len(out.stdout.splitlines()) < 40


def test_inherited_pytest_addopts_cannot_hide_a_failing_test(run_check, py_project):
    (py_project / "tests/test_core.py").write_text(TEST_CORE.replace("== 3", "== 4"))
    commit_all(py_project)
    hide = {"PYTEST_ADDOPTS": "--deselect tests/test_core.py::test_add"}
    out = run_check(py_project, "fast", "tests/test_core.py", env=hide)
    assert " tests=fail " in receipt(out)
    assert out.returncode == 1


def test_pytest_exit_zero_without_a_summary_is_a_failure(run_check, py_project):
    out = run_check(py_project, "fast", "tests/test_core.py", env={"COVEN_CHECK_RUNNER": "true"})
    assert " tests=fail " in receipt(out)
    assert out.returncode == 1


def test_runner_override_is_announced_before_the_receipt(run_check, py_project):
    out = run_check(py_project, "fast", env={"COVEN_CHECK_RUNNER": "true"})
    lines = out.stdout.splitlines()
    assert lines[-2] == "note: COVEN_CHECK_RUNNER=true replaces uv run --frozen"
    assert lines[-1].startswith("CHECK fast ")
    assert lines[-1].endswith(" runner=override")


def test_runner_override_shows_in_the_receipt_even_when_green(run_check, py_project):
    out = run_check(py_project, "fast", env={"COVEN_CHECK_RUNNER": "true"})
    assert " format=ok lint=ok types=ok tests=0 coverage=skip base=- runner=override" in receipt(
        out
    )


def test_blank_runner_override_counts_as_unset(run_check, py_project, tmp_path):
    only_git = tmp_path / "only-git"
    only_git.mkdir()
    (only_git / "git").symlink_to(shutil.which("git"))
    out = run_check(py_project, "fast", env={"COVEN_CHECK_RUNNER": "  ", "PATH": str(only_git)})
    assert "note:" not in out.stdout
    assert receipt(out).endswith('error="uv not found on PATH"')


MUTABLE_DEFAULT = "def bad(items=[]):\n    return items\n\n\nZ = 1  # " + "noqa\n"


def vendor_project(root: Path, attributes: str) -> None:
    (root / ".gitattributes").write_text(f"vendor/** {attributes}\n")
    (root / "vendor").mkdir()
    (root / "vendor/x.py").write_text(MUTABLE_DEFAULT)
    commit_all(root)


@pytest.mark.parametrize("attribute", ["linguist-vendored", "linguist-generated"])
def test_vendored_and_generated_files_are_skipped(run_check, py_project, attribute):
    vendor_project(py_project, attribute)
    out = run_check(py_project, "fast")
    assert out.returncode == 0
    assert "suppressions=0 skipped=1 format=ok lint=ok" in receipt(out)
    assert "note: 1 vendored/generated files skipped (.gitattributes)" in out.stdout.splitlines()
    assert out.stdout.splitlines()[-1].startswith("CHECK fast ")


def test_attribute_unset_still_checked(run_check, py_project):
    vendor_project(py_project, "-linguist-vendored")
    out = run_check(py_project, "fast")
    assert out.returncode == 1
    assert "suppressions=1 " in receipt(out)
    assert " lint=fail " in receipt(out)
    assert "vendored/generated" not in out.stdout


def test_no_note_without_excluded_files(run_check, py_project):
    assert "vendored/generated" not in run_check(py_project, "fast").stdout


def test_assert_in_nested_conftest_is_not_flagged(run_check, py_project):
    (py_project / "tests/conftest.py").write_text("def check(value):\n    assert value\n")
    commit_all(py_project)
    out = run_check(py_project, "fast")
    assert " lint=ok " in receipt(out)


def set_pyproject(root: Path, *edits: tuple[tuple[str, ...], object]) -> None:
    data = tomllib.loads((root / "pyproject.toml").read_text())
    for path, value in edits:
        table = data
        for part in path[:-1]:
            table = table.setdefault(part, {})
        table[path[-1]] = value
    (root / "pyproject.toml").write_text(dump_toml(data))


def test_mypy_files_cannot_leave_code_out(run_check, py_project):
    set_pyproject(py_project, (("tool", "mypy", "files"), ["tests"]))
    (py_project / "src/pkg/loose.py").write_text("def f(x):\n    return x\n")
    commit_all(py_project)
    out = run_check(py_project, "fast")
    assert "src/pkg/core.py, src/pkg/loose.py outside them" in out.stdout
    assert " settings=fail " in receipt(out)
    assert out.returncode == 1


def test_python_files_cannot_hide_a_failing_test(run_check, py_project):
    set_pyproject(py_project, (("tool", "pytest", "ini_options", "python_files"), ["test_core.py"]))
    (py_project / "tests/test_broken.py").write_text(
        "def test_broken():\n    assert sum([]) == 1\n"
    )
    commit_all(py_project)
    out = run_check(py_project, "full", "--base", "main")
    assert "python_files: not allowed" in out.stdout
    assert " settings=fail " in receipt(out)
    assert out.returncode == 1


def test_pytest_toml_is_refused(run_check, py_project):
    (py_project / "pytest.toml").write_text('[pytest]\npython_files = ["test_core.py"]\n')
    commit_all(py_project)
    out = run_check(py_project, "fast")
    assert "pytest.toml: not allowed (pytest reads it before pyproject.toml)" in out.stdout
    assert out.returncode == 1


def test_coverage_report_include_is_refused(run_check, py_project):
    set_pyproject(py_project, (("tool", "coverage", "report", "include"), ["src/pkg/core.py"]))
    commit_all(py_project)
    out = run_check(py_project, "fast")
    assert "[tool.coverage.report] include: not allowed" in out.stdout
    assert out.returncode == 1


def test_locally_hidden_config_and_attributes_change_nothing(run_check, py_project):
    (py_project / ".git/info/exclude").write_text("ruff.toml\nsrc/.gitattributes\n")
    (py_project / "ruff.toml").write_text('extend = ".coven/ruff.toml"\nlint.ignore = ["F401"]\n')
    (py_project / "src/.gitattributes").write_text("*.py linguist-generated\n")
    append(py_project / "src/pkg/core.py", "\n\nimport os  # " + "noqa\n")
    commit_all(py_project)
    out = run_check(py_project, "fast")
    last = receipt(out)
    assert " dirty=no " in last
    assert "ruff.toml: not allowed (it replaces .coven/ruff.toml)" in out.stdout
    assert " settings=fail suppressions=1 skipped=0 " in last
    assert out.returncode == 1


def add_unimported_namespace_module(root: Path) -> None:
    git(root, "checkout", "-q", "-b", "feature")
    (root / "src/pkg/sub").mkdir()
    (root / "src/pkg/sub/mod.py").write_text(CORE)  # no __init__.py: coverage cannot see it
    commit_all(root)


def test_changed_file_missing_from_the_coverage_report_fails(run_check, py_project):
    add_unimported_namespace_module(py_project)
    out = run_check(py_project, "full", "--base", "main")
    assert "coverage: not measured: src/pkg/sub/mod.py" in out.stdout
    assert " coverage=fail " in receipt(out)
    assert out.returncode == 1


def test_changed_test_and_empty_files_need_no_coverage(run_check, py_project):
    git(py_project, "checkout", "-q", "-b", "feature")
    (py_project / "tests/sub").mkdir()
    (py_project / "tests/sub/helper.py").write_text(CORE)
    (py_project / "src/pkg/sub").mkdir()
    (py_project / "src/pkg/sub/empty.py").write_text("")
    commit_all(py_project)
    out = run_check(py_project, "full", "--base", "main")
    assert " coverage=n/a " in receipt(out)
    assert out.returncode == 0


def test_absolute_testpaths_are_a_settings_problem_not_a_traceback(run_check, py_project):
    set_pyproject(py_project, (("tool", "pytest", "ini_options", "testpaths"), ["/tmp/tests"]))
    commit_all(py_project)
    out = run_check(py_project, "fast")
    assert "testpaths: must be relative paths inside the project" in out.stdout
    assert " settings=fail " in receipt(out)
    assert "Traceback" not in out.stdout + out.stderr
    assert out.returncode == 1


UNCOVERED_BLOCK = (
    "        total = a * b\n        total += b\n        total -= a\n        return total * 3\n"
    "    return a + b\n"
)
# Source text that coverage's default exclusion regexes (matched over the whole file) would
# read as "do not measure this": none of it may hide the uncovered block below it.
HIDING_CORE = {
    "split pragma": "def add(a: int, b: int) -> int:\n    if a > 1000:  # pragma\n        nocover = 1\n"
    + UNCOVERED_BLOCK.replace("total", "nocover"),
    "ellipsis body": "def add(a: int, b: int) -> int:\n    ...\n    if a > 1000:\n"
    + UNCOVERED_BLOCK,
    "type checking text": "def add(a: int, b: int) -> int:\n"
    "    if a > 1000:  # unlike if TYPE_CHECKING: this runs\n" + UNCOVERED_BLOCK,
    "form feed": 'MARK = "# pragma\fno cover"\n\n\ndef add(a: int, b: int) -> int:\n'
    '    if a > 1000 and MARK != "# pragma\fno cover":\n' + UNCOVERED_BLOCK,
    "string": 'MARK = "# pragma: no cover"\n\n\ndef add(a: int, b: int) -> int:\n'
    '    if a > 1000 and MARK != "# pragma: no cover":\n' + UNCOVERED_BLOCK,
    "partial always": "def add(a: int, b: int) -> int:\n"
    "    if a > 1000:  # while True: if 0:\n" + UNCOVERED_BLOCK,
}


@pytest.mark.parametrize("source", list(HIDING_CORE.values()), ids=list(HIDING_CORE))
def test_source_text_cannot_exclude_code_from_coverage(run_check, py_project, source):
    git(py_project, "checkout", "-q", "-b", "feature")
    (py_project / "src/pkg/core.py").write_text(source)
    commit_all(py_project)
    out = run_check(py_project, "full", "--base", "main")
    percent = re.search(r" coverage=(\d+) ", receipt(out))
    assert percent, receipt(out)
    assert int(percent.group(1)) < 80
    assert out.returncode == 1


def test_coverage_rc_copies_only_the_allowed_run_keys(check_python, py_project, tmp_path):
    set_pyproject(
        py_project,
        (("tool", "coverage", "run", "parallel"), True),
        (("tool", "coverage", "run", "concurrency"), ["thread", "multiprocessing"]),
        (("tool", "coverage", "run", "source"), ["tests"]),
        (("tool", "coverage", "report", "exclude_also"), ["def "]),
    )
    rc = check_python.write_coverage_rc(py_project, tmp_path / "coveragerc")
    assert rc.read_text() == (
        "[run]\nbranch = true\nconcurrency = thread, multiprocessing\nparallel = true\n"
        "source = src\n\n"
        "[report]\nexclude_lines =\n    (?!)\npartial_branches =\n    (?!)\n"
        "partial_branches_always =\n    (?!)\n"
    )


def test_coverage_rc_always_measures_branches(check_python, py_project, tmp_path):
    set_pyproject(py_project, (("tool", "coverage", "run"), {}))
    rc = check_python.write_coverage_rc(py_project, tmp_path / "coveragerc")
    assert rc.read_text().startswith("[run]\nbranch = true\n")


def test_coverage_rc_names_every_mypy_directory_as_source(check_python, py_project, tmp_path):
    set_pyproject(py_project, (("tool", "mypy", "files"), ["src", "services/api"]))
    rc = check_python.write_coverage_rc(py_project, tmp_path / "coveragerc")
    assert "\nsource = src, services/api\n" in rc.read_text()


def test_coverage_rc_has_no_source_without_mypy_directories(check_python, py_project, tmp_path):
    set_pyproject(py_project, (("tool", "mypy", "files"), []))
    rc = check_python.write_coverage_rc(py_project, tmp_path / "coveragerc")
    assert "source" not in rc.read_text()


def test_a_file_the_standard_did_not_install_cannot_hide_in_dot_coven(run_check, py_project):
    (py_project / ".coven/helpers.py").write_text("def scale(v: int) -> int:\n    return v\n")
    set_pyproject(py_project, (("tool", "pytest", "ini_options", "pythonpath"), [".coven"]))
    commit_all(py_project)
    out = run_check(py_project, "fast")
    assert ".coven/helpers.py: not allowed" in out.stdout
    assert "pythonpath: .coven/ is not allowed" in out.stdout
    assert " settings=fail " in receipt(out)
    assert out.returncode == 1


def test_same_relative_path_under_two_coverage_sources_is_ambiguous(run_check, py_project):
    set_pyproject(py_project, (("tool", "mypy", "files"), ["src", "tools"]))
    commit_all(py_project)
    git(py_project, "checkout", "-q", "-b", "feature")
    (py_project / "tools/pkg").mkdir(parents=True)
    (py_project / "tools/pkg/core.py").write_text(
        "def mul(a: int, b: int) -> int:\n    return a * b\n"
    )
    commit_all(py_project)
    out = run_check(py_project, "full", "--base", "main")
    assert "coverage: ambiguous coverage paths: tools/pkg/core.py" in out.stdout
    assert " coverage=fail " in receipt(out)
    assert out.returncode == 1


def test_a_stub_next_to_a_module_is_a_settings_problem_run(run_check, py_project):
    (py_project / "src/pkg/core.pyi").write_text("def add(a: int, b: int) -> int: ...\n")
    commit_all(py_project)
    out = run_check(py_project, "fast")
    assert "src/pkg/core.py is shadowed by a .pyi stub" in out.stdout
    assert " settings=fail " in receipt(out)
    assert out.returncode == 1


def uncovered_feature(root: Path) -> None:
    """A feature branch whose only change is a block no test reaches (coverage 20)."""
    git(root, "checkout", "-q", "-b", "feature")
    (root / "src/pkg/core.py").write_text(
        "def add(a: int, b: int) -> int:\n    if a > 1000:\n" + UNCOVERED_BLOCK
    )
    commit_all(root)


def coverage_percent(out) -> int:
    found = re.search(r" coverage=(\d+) ", receipt(out))
    assert found, receipt(out)
    return int(found.group(1))


def test_coverage_settings_with_a_newline_cannot_inject_rc_sections(run_check, py_project):
    injection = "false\n[coverage:report]\nexclude_lines = if a > 1000"
    set_pyproject(py_project, (("tool", "coverage", "run", "sigterm"), injection))
    uncovered_feature(py_project)
    out = run_check(py_project, "full", "--base", "main")
    assert "[tool.coverage.run] sigterm: must be true or false" in out.stdout
    assert " settings=fail " in receipt(out)
    assert " coverage=fail " in receipt(out)
    assert out.returncode == 1


@pytest.mark.parametrize("key", ["branch", "parallel", "sigterm", "concurrency", "patch"])
@pytest.mark.parametrize("separator", ["\n", "\r"])
def test_coverage_rc_refuses_a_value_with_a_line_break(
    check_python, py_project, tmp_path, key, separator
):
    set_pyproject(py_project, (("tool", "coverage", "run", key), f"x{separator}[coverage:report]"))
    with pytest.raises(ValueError, match=key):
        check_python.write_coverage_rc(py_project, tmp_path / "coveragerc")


def test_coverage_rc_has_only_the_run_and_report_sections(check_python, py_project, tmp_path):
    rc = check_python.write_coverage_rc(py_project, tmp_path / "coveragerc")
    parser = configparser.RawConfigParser()
    parser.read(rc)
    assert parser.sections() == ["run", "report"]


def test_inherited_coverage_variables_cannot_change_the_measurement(
    run_check, py_project, tmp_path
):
    forced = tmp_path / "force.rc"
    forced.write_text("[report]\nexclude_lines =\n    if a > 1000\n")
    uncovered_feature(py_project)
    out = run_check(
        py_project,
        "full",
        "--base",
        "main",
        env={"COVERAGE_FORCE_CONFIG": str(forced), "COVERAGE_DEBUG": "config"},
    )
    assert coverage_percent(out) < 80
    assert out.returncode == 1


def test_inherited_mypypath_cannot_supply_missing_stubs(run_check, py_project, tmp_path):
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    (stubs / "extlib.pyi").write_text("thing: int\n")
    (py_project / "src/pkg/core.py").write_text(
        "from extlib import thing\n\n\ndef value() -> int:\n    return thing\n"
    )
    commit_all(py_project)
    out = run_check(py_project, "fast", env={"MYPYPATH": str(stubs)})
    assert " types=fail " in receipt(out)
    assert out.returncode == 1


# --- commit messages (agent attribution) --------------------------------------

AGENT_TRAILER = "Co-Authored-By: Claude <noreply@anthropic.com>"
GENERATED_LINE = "Generated with [Claude Code](https://claude.com/claude-code)"


def commit(root: Path, message: str) -> str:
    """Adds an empty commit with this message; returns its short sha."""
    git(root, "commit", "-q", "--allow-empty", "-m", message)
    return git(root, "rev-parse", "--short=7", "HEAD").strip()


@pytest.fixture
def base(git_repo: Path) -> str:
    """The commit the branch under test starts from."""
    return git(git_repo, "rev-parse", "HEAD").strip()


def test_a_clean_branch_has_no_problems(check_repo, git_repo, base):
    commit(git_repo, "feat: add a thing\n\nWhy it is needed.")
    assert check_repo.attribution_problems(git_repo, base) == []


@pytest.mark.parametrize(
    "line",
    [
        AGENT_TRAILER,
        "co-authored-by: codex <noreply@openai.com>",
        "Co-authored-by: Some Bot <noreply@openai.com>",
        "Co-authored-by: <noreply@mail.anthropic.com>",
        "Co-authored-by: OpenCode <noreply@opencode.ai>",
        "Co-authored-by: Copilot <noreply@github.com>",
        "Co-authored-by: Some Bot <no-reply@anthropic.com>",
        GENERATED_LINE,
        "\N{ROBOT FACE} Generated with Claude Code",
        "generated with [opencode]",
    ],
    ids=[
        "agent-trailer",
        "codex-lowercase",
        "openai-address",
        "subdomain-address",
        "opencode-trailer",
        "copilot-trailer",
        "agent-vendor-address",
        "generated-link",
        "generated-plain-emoji",
        "generated-opencode",
    ],
)
def test_an_agent_attribution_line_is_a_problem(check_repo, git_repo, base, line):
    sha = commit(git_repo, f"feat: add a thing\n\nBody.\n\n{line}")
    assert check_repo.attribution_problems(git_repo, base) == [
        f'commit {sha}: agent attribution line "{line}"'
    ]


def test_a_human_co_author_is_allowed(check_repo, git_repo, base):
    commit(git_repo, "feat: pair work\n\nCo-authored-by: Ada Lovelace <ada@example.com>")
    assert check_repo.attribution_problems(git_repo, base) == []


@pytest.mark.parametrize(
    "line",
    [
        "- Co-authored-by: lines naming Codex now fail the check",
        "Co-authored-by: Jane Doe <jane.codex@example.com>",
        "Co-authored-by: Jane Doe <jane@notanthropic.example.com>",
        "Co-authored-by: Jane Doe <jane@anthropic.com.example.com>",
    ],
    ids=["bullet-in-prose", "agent-word-in-human-address", "lookalike-domain", "domain-prefix"],
)
def test_a_line_that_only_looks_like_attribution_is_allowed(check_repo, git_repo, base, line):
    commit(git_repo, f"docs: explain the check\n\n{line}")
    assert check_repo.attribution_problems(git_repo, base) == []


def test_prose_mentioning_an_agent_is_allowed(check_repo, git_repo, base):
    commit(git_repo, "docs: explain the Claude settings\n\nCodex has no switch for this.")
    assert check_repo.attribution_problems(git_repo, base) == []


def test_each_offending_commit_is_reported_once(check_repo, git_repo, base):
    first = commit(git_repo, f"feat: one\n\n{AGENT_TRAILER}\n{GENERATED_LINE}")
    commit(git_repo, "feat: two")
    third = commit(git_repo, f"feat: three\n\n{GENERATED_LINE}")
    assert check_repo.attribution_problems(git_repo, base) == [
        f'commit {first}: agent attribution line "{AGENT_TRAILER}"',
        f'commit {third}: agent attribution line "{GENERATED_LINE}"',
    ]


def test_commits_before_the_base_are_ignored(check_repo, git_repo):
    commit(git_repo, f"feat: old\n\n{AGENT_TRAILER}")
    base = git(git_repo, "rev-parse", "HEAD").strip()
    commit(git_repo, "feat: new")
    assert check_repo.attribution_problems(git_repo, base) == []


def test_an_unreadable_range_is_a_problem_not_a_pass(check_repo, git_repo):
    commit(git_repo, "feat: anything")
    missing = "0123456789abcdef0123456789abcdef01234567"
    problems = check_repo.attribution_problems(git_repo, missing)
    assert len(problems) == 1
    assert problems[0].startswith(f"cannot read commit messages in {missing}..HEAD: ")
    assert len(problems[0]) > len(f"cannot read commit messages in {missing}..HEAD: ")


def test_no_base_means_nothing_to_check(check_repo, git_repo):
    commit(git_repo, f"feat: old\n\n{AGENT_TRAILER}")
    assert check_repo.attribution_problems(git_repo, None) == []


@pytest.fixture
def tagged_project(py_project: Path) -> Path:
    """A passing project with one branch commit carrying an agent trailer."""
    git(py_project, "switch", "-q", "-c", "feature")
    (py_project / "NOTES.md").write_text("notes\n")
    git(py_project, "add", "-A")
    git(py_project, "commit", "-q", "-m", f"docs: notes\n\n{AGENT_TRAILER}")
    return py_project


def test_full_check_fails_the_receipt_on_an_attribution_line(run_check, tagged_project):
    out = run_check(tagged_project, "full", "--base", "main")
    sha = git(tagged_project, "rev-parse", "--short=7", "HEAD").strip()
    assert f'settings: commit {sha}: agent attribution line "{AGENT_TRAILER}"' in out.stdout
    assert out.returncode == 1
    assert " settings=fail " in receipt(out)


def test_full_check_passes_a_clean_branch(run_check, py_project):
    git(py_project, "switch", "-q", "-c", "feature")
    (py_project / "NOTES.md").write_text("notes\n")
    commit_all(py_project)
    out = run_check(py_project, "full", "--base", "main")
    assert out.returncode == 0
    assert " settings=ok " in receipt(out)


def test_fast_check_does_not_read_commit_messages(run_check, tagged_project):
    out = run_check(tagged_project, "fast")
    assert "agent attribution" not in out.stdout
    assert " settings=ok " in receipt(out)


# --- several projects in one repo ---------------------------------------------

RECORDER = """\
import os
import sys

with open(os.environ["RECORD_TO"], "a", encoding="utf-8") as log:
    log.write(os.getcwd() + " " + sys.argv[1] + "\\n")
os.execv(sys.executable, [sys.executable, "-m", *sys.argv[1:]])
"""


def standard_files(repo: Path) -> None:
    """The repo's one `.coven/`, as `coven project` installs it."""
    (repo / ".coven").mkdir(exist_ok=True)
    shutil.copy(REPO / "standard/python/ruff.toml", repo / ".coven/ruff.toml")


def write_python_project(root: Path, repo: Path | None = None) -> None:
    """The files of a Python project that passes every stage, below `root`.

    Its ruff config extends the repo's one `.coven/ruff.toml` (`repo` is the repo's top).
    """
    root.mkdir(parents=True, exist_ok=True)
    extend = Path(os.path.relpath((repo or root) / ".coven/ruff.toml", root)).as_posix()
    pyproject = copy.deepcopy(GOOD)
    pyproject["tool"]["ruff"]["extend"] = extend
    (root / "pyproject.toml").write_text(dump_toml(pyproject))
    (root / "uv.lock").write_text("")
    (root / "src/pkg").mkdir(parents=True)
    (root / "src/pkg/__init__.py").write_text("")
    (root / "src/pkg/core.py").write_text(CORE)
    (root / "tests").mkdir()
    (root / "tests/test_core.py").write_text(TEST_CORE)


@pytest.fixture
def monorepo(git_repo: Path) -> Path:
    """Two green Python projects, `backend/` and `service/`, in one repo on branch main."""
    (git_repo / ".gitignore").write_text("__pycache__/\n")
    standard_files(git_repo)
    write_python_project(git_repo / "backend", git_repo)
    write_python_project(git_repo / "service", git_repo)
    git(git_repo, "branch", "-M", "main")
    commit_all(git_repo)
    return git_repo


def test_python_project_in_subdir_runs_from_its_root(run_check, monorepo, tmp_path):
    shutil.rmtree(monorepo / "service")
    commit_all(monorepo)
    (tmp_path / "recorder.py").write_text(RECORDER)
    log = tmp_path / "calls.log"
    runner = f"{sys.executable} {tmp_path / 'recorder.py'}"
    out = run_check(
        monorepo,
        "fast",
        "backend/tests/test_core.py",
        env={"COVEN_CHECK_RUNNER": runner, "RECORD_TO": str(log)},
    )
    assert out.returncode == 0
    assert " projects=1 settings=ok " in receipt(out)
    assert " tests=1 coverage=skip base=- " in receipt(out)
    calls = log.read_text().splitlines()
    assert calls
    assert {call.split(" ")[0] for call in calls} == {str(monorepo / "backend")}


def test_two_projects_fold_into_one_receipt(run_check, monorepo):
    out = run_check(monorepo, "fast", "backend/tests/test_core.py", "service/tests/test_core.py")
    assert out.returncode == 0
    assert " projects=2 settings=ok suppressions=0 skipped=0 format=ok lint=ok types=ok " in (
        receipt(out)
    )
    assert " tests=2 coverage=skip base=- runner=override" in receipt(out)


def test_test_paths_go_to_the_project_that_holds_them(run_check, monorepo):
    out = run_check(monorepo, "fast", "backend/tests/test_core.py")
    assert " projects=2 " in receipt(out)
    assert " tests=1 " in receipt(out)


def test_test_path_outside_every_project_is_a_setup_error(run_check, monorepo):
    (monorepo / "stray.py").write_text("")
    out = run_check(monorepo, "fast", "stray.py")
    assert out.returncode == 2
    assert receipt(out).endswith(' error="test path stray.py: not inside a project"')


def test_one_failing_project_does_not_hide_the_other(run_check, monorepo):
    (monorepo / "service/src/pkg/core.py").write_text(CORE + "\n\nZ = 1  # noqa\n")
    commit_all(monorepo)
    out = run_check(monorepo, "fast", "backend/tests/test_core.py")
    assert out.returncode == 1
    assert " projects=2 " in receipt(out)
    assert " suppressions=1 " in receipt(out)
    assert " tests=1 " in receipt(out)
    assert "=== backend (python) ===" in out.stdout
    assert "=== service (python) ===" in out.stdout
    service = out.stdout.split("=== service (python) ===")[1]
    assert "src/pkg/core.py:5: # noqa" in service


def test_a_stage_fails_when_any_project_fails_it(run_check, monorepo):
    (monorepo / "service/src/pkg/core.py").write_text(CORE + "\n\ndef bad(x=[]):\n    return x\n")
    commit_all(monorepo)
    out = run_check(monorepo, "fast")
    assert out.returncode == 1
    assert " format=ok lint=fail types=fail " in receipt(out)


def test_nested_pyproject_gets_full_settings_verification(run_check, py_project):
    write_python_project(py_project / "tools", py_project)
    weak = copy.deepcopy(GOOD)
    weak["tool"]["ruff"]["extend"] = "../.coven/ruff.toml"
    weak["tool"]["mypy"]["strict"] = False
    (py_project / "tools/pyproject.toml").write_text(dump_toml(weak))
    commit_all(py_project)
    out = run_check(py_project, "fast")
    assert " projects=2 settings=fail " in receipt(out)
    tools = out.stdout.split("=== tools (python) ===")[1]
    assert "settings: pyproject.toml [tool.mypy] strict: must be true" in tools


def test_a_nested_project_is_not_scanned_by_the_project_around_it(run_check, py_project):
    write_python_project(py_project / "tools", py_project)
    (py_project / "tools/src/pkg/core.py").write_text(CORE + "\n\nZ = 1  # noqa\n")
    commit_all(py_project)
    out = run_check(py_project, "fast")
    assert " projects=2 " in receipt(out)
    assert " suppressions=1 " in receipt(out)


def test_a_nested_project_needs_its_own_uv_lock(run_check, monorepo):
    (monorepo / "service/uv.lock").unlink()
    commit_all(monorepo)
    out = run_check(monorepo, "fast")
    assert out.returncode == 2
    assert receipt(out).endswith(' error="service: no uv.lock (the standard supports uv projects)"')


def test_the_repo_root_is_the_git_toplevel_not_the_cwd(run_check, py_project):
    out = run_check(py_project / "src", "fast", "../tests/test_core.py")
    assert out.returncode == 0
    assert " projects=1 settings=ok " in receipt(out)
    assert " tests=1 " in receipt(out)


def test_running_inside_a_project_directory_finds_the_whole_repo(run_check, monorepo):
    out = run_check(monorepo / "backend", "fast", "tests/test_core.py")
    assert out.returncode == 0
    assert " projects=2 " in receipt(out)
    assert " tests=1 " in receipt(out)


def test_no_lockfile_is_setup_error(run_check, git_repo):
    (git_repo / "frontend").mkdir()
    (git_repo / "frontend/package.json").write_text("{}")
    (git_repo / "frontend/tsconfig.json").write_text("{}")
    commit_all(git_repo)
    out = run_check(git_repo, "fast")
    assert out.returncode == 2
    assert receipt(out).endswith(' dirty=no error="frontend: no lockfile"')


def test_no_project_at_all_is_a_setup_error(run_check, git_repo):
    out = run_check(git_repo, "fast")
    assert out.returncode == 2
    assert receipt(out).endswith(' error="no Python, TypeScript or Claude Code mod project found"')


# A package.json naming a supported test runner, for projects whose tools are never installed.
UNINSTALLED_PACKAGE = '{ "devDependencies": { "c8": "12.0.0" } }\n'


def write_typescript(root: Path, lockfile: str = "package-lock.json") -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "package.json").write_text(UNINSTALLED_PACKAGE)
    for name in ("tsconfig.json", lockfile):
        (root / name).write_text("{}")


def typescript_repo(root: Path) -> None:
    write_typescript(root)
    commit_all(root)


BAD_IMPORT = "import os\n\n\ndef f(x: list[int]) -> list[int]:\n    return x\n"


def test_the_standard_lives_once_at_the_repo_root(run_check, monorepo):
    out = run_check(monorepo, "fast", "backend/tests/test_core.py")
    assert out.returncode == 0
    assert 'extend = "../.coven/ruff.toml"' in (monorepo / "backend/pyproject.toml").read_text()
    assert " settings=ok " in receipt(out)


def test_a_nested_project_with_its_own_coven_directory_fails(run_check, monorepo):
    own = monorepo / "backend/.coven"
    own.mkdir()
    (own / "ruff.toml").write_text("line-length = 100\n[lint]\nselect = []\n")
    pyproject = monorepo / "backend/pyproject.toml"
    pyproject.write_text(pyproject.read_text().replace("../.coven", ".coven"))
    (monorepo / "backend/src/pkg/bad.py").write_text(BAD_IMPORT)
    commit_all(monorepo)
    out = run_check(monorepo, "fast")
    assert out.returncode == 1
    assert " settings=fail " in receipt(out)
    assert "settings: backend/.coven: not allowed" in out.stdout
    backend = out.stdout.split("=== backend (python) ===")[1].split("=== service")[0]
    assert 'extend: must be "../.coven/ruff.toml"' in backend


def test_a_nested_project_must_extend_the_repo_coven(run_check, monorepo):
    pyproject = monorepo / "service/pyproject.toml"
    pyproject.write_text(pyproject.read_text().replace("../.coven", "../../.coven"))
    commit_all(monorepo)
    out = run_check(monorepo, "fast")
    assert " settings=fail " in receipt(out)
    assert 'extend: must be "../.coven/ruff.toml"' in out.stdout


def test_unowned_files_in_the_repo_coven_fail_once(run_check, monorepo):
    (monorepo / ".coven/extra.py").write_text("")
    commit_all(monorepo)
    out = run_check(monorepo, "fast")
    assert " settings=fail " in receipt(out)
    assert out.stdout.count(".coven/extra.py: not allowed") == 1


def test_the_standards_own_check_script_is_not_outside_a_project(run_check, monorepo):
    (monorepo / ".coven/check.py").write_text("")
    commit_all(monorepo)
    assert " settings=ok " in receipt(run_check(monorepo, "fast"))


def test_exclusions_are_anchored_so_a_same_named_directory_is_still_linted(run_check, py_project):
    write_python_project(py_project / "tools", py_project)
    (py_project / "src/pkg/tools").mkdir()
    (py_project / "src/pkg/tools/__init__.py").write_text("")
    (py_project / "src/pkg/tools/bad.py").write_text(BAD_IMPORT)
    commit_all(py_project)
    out = run_check(py_project, "fast")
    assert " lint=fail " in receipt(out)
    assert "src/pkg/tools/bad.py" in out.stdout


def test_a_vendored_file_does_not_hide_a_file_of_the_same_name(run_check, py_project):
    (py_project / "gen.py").write_text(BAD_IMPORT)
    (py_project / "src/pkg/gen.py").write_text(BAD_IMPORT)
    (py_project / ".gitattributes").write_text("/gen.py linguist-generated\n")
    commit_all(py_project)
    out = run_check(py_project, "fast")
    assert " skipped=1 " in receipt(out)
    assert " lint=fail " in receipt(out)
    assert "src/pkg/gen.py" in out.stdout


def test_exclude_arguments_are_absolute_paths(check_python, check_repo, py_project):
    write_python_project(py_project / "tools", py_project)
    commit_all(py_project)
    skipped = {py_project / "gen.py"}
    assert check_python.exclude_args(py_project, skipped, check_repo.listing(py_project)) == [
        "--extend-exclude",
        (py_project / ".coven").as_posix(),
        "--extend-exclude",
        (py_project / "gen.py").as_posix(),
        "--extend-exclude",
        (py_project / "tools").as_posix(),
    ]


def test_python_outside_every_project_fails_the_settings(run_check, monorepo):
    (monorepo / "scripts").mkdir()
    (monorepo / "scripts/deploy.py").write_text(BAD_IMPORT + "Z = 1  # noqa\n")
    commit_all(monorepo)
    out = run_check(monorepo, "fast")
    assert out.returncode == 1
    assert " settings=fail " in receipt(out)
    assert "settings: scripts/deploy.py: outside every project" in out.stdout


def test_vendored_python_outside_every_project_is_exempt(run_check, monorepo):
    (monorepo / "vendor").mkdir()
    (monorepo / "vendor/lib.py").write_text(BAD_IMPORT)
    (monorepo / ".gitattributes").write_text("vendor/** linguist-vendored\n")
    commit_all(monorepo)
    assert " settings=ok " in receipt(run_check(monorepo, "fast"))


def test_javascript_outside_every_typescript_project_fails(run_check, monorepo):
    (monorepo / "frontend/src").mkdir(parents=True)
    (monorepo / "frontend/package.json").write_text("{}")
    (monorepo / "frontend/src/main.ts").write_text("export const x = 1\n")
    commit_all(monorepo)
    out = run_check(monorepo, "fast")
    assert " projects=2 settings=fail " in receipt(out)
    assert "settings: frontend/src/main.ts: outside every project" in out.stdout


def test_typescript_inside_a_typescript_project_is_not_outside(run_check, git_repo):
    typescript_repo(git_repo)
    (git_repo / "x.ts").write_text("export const x = 1\n")
    out = run_check(git_repo, "fast")
    assert "outside every project" not in out.stdout


def test_a_pyproject_hidden_from_git_is_a_settings_problem(run_check, py_project):
    (py_project / "src/pkg/sub").mkdir()
    (py_project / "src/pkg/sub/pyproject.toml").write_text("[tool.ruff]\nline-length = 100\n")
    (py_project / ".git/info/exclude").write_text("src/pkg/sub/pyproject.toml\n")
    out = run_check(py_project, "fast")
    assert " dirty=no " in receipt(out)
    assert " settings=fail " in receipt(out)
    assert "src/pkg/sub/pyproject.toml: hidden from git; ruff and pytest read it" in out.stdout


def make_uv_workspace_root(repo: Path) -> None:
    """The repo's top as a Python project that declares `backend` and `service` its members."""
    write_python_project(repo, repo)
    pyproject = repo / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text() + '\n[tool.uv.workspace]\nmembers = ["backend", "service"]\n'
    )


def test_uv_lock_of_a_workspace_root_serves_its_members(run_check, monorepo):
    (monorepo / "backend/uv.lock").unlink()
    make_uv_workspace_root(monorepo)
    commit_all(monorepo)
    out = run_check(monorepo, "fast", "backend/tests/test_core.py")
    assert out.returncode == 0, out.stdout
    assert " projects=3 " in receipt(out)


def test_uv_lock_above_a_project_that_is_no_workspace_does_not_count(run_check, monorepo):
    (monorepo / "backend/uv.lock").unlink()
    write_python_project(monorepo, monorepo)
    commit_all(monorepo)
    out = run_check(monorepo, "fast")
    assert out.returncode == 2
    assert receipt(out).endswith(' error="backend: no uv.lock (the standard supports uv projects)"')


def test_mypy_files_reaching_a_nested_project_is_named(run_check, py_project):
    write_python_project(py_project / "tools", py_project)
    root = copy.deepcopy(GOOD)
    root["tool"]["mypy"]["files"] = ["."]
    (py_project / "pyproject.toml").write_text(dump_toml(root))
    commit_all(py_project)
    out = run_check(py_project, "fast")
    assert " settings=fail " in receipt(out)
    assert "[tool.mypy] files: . reaches the nested project tools" in out.stdout


def test_a_nested_projects_stray_files_are_reported_once(run_check, py_project):
    write_python_project(py_project / "tools", py_project)
    (py_project / "tools/mypy.ini").write_text("[mypy]\n")
    commit_all(py_project)
    out = run_check(py_project, "fast")
    assert out.stdout.count("mypy.ini: not allowed") == 1


def test_a_project_asks_git_for_its_files_once(
    check_python, check_repo, check_stages, py_project, monkeypatch
):
    monkeypatch.setenv("COVEN_CHECK_RUNNER", f"{sys.executable} -m")
    asked = []
    real = check_repo.git

    def counting(root, *args):
        asked.append(args[0])
        return real(root, *args)

    monkeypatch.setattr(check_repo, "git", counting)
    project = check_repo.Project(py_project, "python", None)
    check_python.python_stages(
        project, check_stages.Run(py_project, "fast", None), [], py_project / "x" / "c.xml"
    )
    assert asked.count("ls-files") == 1


def test_a_vendored_file_named_with_a_glob_does_not_hide_its_siblings(run_check, py_project):
    (py_project / "src/pkg/bad.py").write_text(BAD_IMPORT)
    (py_project / "src/pkg/*.py").write_text("")
    (py_project / ".gitattributes").write_text("src/pkg/[*].py linguist-vendored\n")
    commit_all(py_project)
    out = run_check(py_project, "fast")
    assert " lint=fail " in receipt(out)
    assert " settings=fail " in receipt(out)
    assert "settings: src/pkg/*.py: glob characters in a path are not allowed" in out.stdout


def test_a_nested_project_directory_named_with_a_glob_does_not_hide_the_outer_one(
    run_check, py_project
):
    write_python_project(py_project / "*", py_project)
    (py_project / "src/pkg/bad.py").write_text(BAD_IMPORT)
    commit_all(py_project)
    out = run_check(py_project, "fast")
    assert " lint=fail " in receipt(out)
    assert " settings=fail " in receipt(out)
    assert "settings: *: glob characters in a path are not allowed" in out.stdout


def test_a_repo_path_with_glob_characters_keeps_the_exclusions(run_check, py_project, tmp_path):
    (py_project / "src/pkg/vendor.py").write_text(BAD_IMPORT)
    (py_project / ".gitattributes").write_text("src/pkg/vendor.py linguist-vendored\n")
    commit_all(py_project)
    moved = tmp_path / "a[1]{x}" / "project"
    shutil.copytree(py_project, moved, symlinks=True)
    out = run_check(moved, "fast", "tests/test_core.py")
    assert out.returncode == 0, out.stdout
    assert " skipped=1 " in receipt(out)
    assert " lint=ok " in receipt(out)


def test_exclude_arguments_are_written_out_literally(check_python, tmp_path):
    root = tmp_path / "a*[1]"
    root.mkdir()
    args = check_python.exclude_args(root, {root / "v?.py"}, [Path("tools/pyproject.toml")])
    assert args == [
        "--extend-exclude",
        check_python.literal(str(root)) + "/tools",
        "--extend-exclude",
        check_python.literal(str(root)) + "/v[?].py",
    ]
    assert check_python.literal("a*b[c]{d}\\e?") == "a[*]b[[]c[]][{]d[}][\\]e[?]"


def test_python_below_node_modules_outside_every_project_is_not_exempt(run_check, monorepo):
    (monorepo / "scripts/node_modules").mkdir(parents=True)
    (monorepo / "scripts/node_modules/deploy.py").write_text(BAD_IMPORT)
    commit_all(monorepo)
    out = run_check(monorepo, "fast")
    assert " settings=fail " in receipt(out)
    assert "settings: scripts/node_modules/deploy.py: outside every project" in out.stdout


def test_tracked_files_below_node_modules_are_a_problem(run_check, monorepo):
    # tsc still reads TypeScript there, but no check would look at it.
    for name in ("node_modules/dep/index.ts", "node_modules/dep/a.js", "web/node_modules/x/b.ts"):
        (monorepo / name).parent.mkdir(parents=True, exist_ok=True)
        (monorepo / name).write_text("export const a = 1\n")
    commit_all(monorepo)
    out = run_check(monorepo, "fast")
    assert " settings=fail " in receipt(out)
    problem = "settings: {}: not allowed to hold tracked files (nothing checks them)"
    assert problem.format("node_modules") in out.stdout
    assert problem.format("web/node_modules") in out.stdout
    assert "outside every project" not in out.stdout


def test_untracked_files_below_node_modules_are_skipped(run_check, monorepo):
    (monorepo / "node_modules/dep").mkdir(parents=True)
    (monorepo / "node_modules/dep/index.js").write_text("module.exports = 1\n")
    assert " settings=ok " in receipt(run_check(monorepo, "fast"))


def add_backend_sub(root: Path) -> None:
    git(root, "checkout", "-q", "-b", "feature")
    core = root / "backend/src/pkg/core.py"
    core.write_text(core.read_text() + "\n\ndef sub(a: int, b: int) -> int:\n    return a - b\n")
    test = root / "backend/tests/test_core.py"
    test.write_text(
        test.read_text()
        + "\n\ndef test_sub():\n    from pkg.core import sub\n\n    assert sub(3, 1) == 2\n"
    )
    commit_all(root)


def test_full_mode_measures_changed_lines_inside_a_subdirectory_project(run_check, monorepo):
    add_backend_sub(monorepo)
    out = run_check(monorepo, "full", "--base", "main")
    assert out.returncode == 0, out.stdout
    base = git(monorepo, "rev-parse", "--short=7", "main").strip()
    assert " projects=2 " in receipt(out)
    assert receipt(out).endswith(f" tests=3 coverage=100 base={base} runner=override")
    assert git(monorepo, "status", "--porcelain").strip() == ""


@pytest.mark.parametrize(
    ("values", "folded"),
    [
        (["ok"], "ok"),
        (["ok", "ok"], "ok"),
        (["ok", "fail"], "fail"),
        (["fail", "ok"], "fail"),
    ],
)
def test_fold_ok(check, values, folded):
    assert check.fold_ok(values) == folded


@pytest.mark.parametrize(
    ("values", "folded"),
    [(["3"], "3"), (["2", "5"], "7"), (["0", "0"], "0"), (["2", "fail"], "fail")],
)
def test_fold_tests_sums_counts(check, values, folded):
    assert check.fold_tests(values) == folded


@pytest.mark.parametrize(
    ("values", "folded"),
    [
        (["skip", "skip"], "skip"),
        (["n/a", "n/a"], "n/a"),
        (["n/a", "100"], "100"),
        (["95", "80", "100"], "80"),
        (["9", "100"], "9"),
        (["100", "fail"], "fail"),
        (["n/a", "fail"], "fail"),
    ],
)
def test_fold_coverage_takes_the_lowest(check, values, folded):
    assert check.fold_coverage(values) == folded


def test_framework_files_outside_every_project_fail(run_check, monorepo):
    (monorepo / "site").mkdir()
    (monorepo / "site/page.astro").write_text("---\n---\n")
    (monorepo / "site/App.vue").write_text("<p>app</p>\n")
    commit_all(monorepo)
    out = run_check(monorepo, "fast")
    assert " settings=fail " in receipt(out)
    assert "settings: site/App.vue: outside every project" in out.stdout
    assert "settings: site/page.astro: outside every project" in out.stdout


# --- the receipt line ---------------------------------------------------------

RECEIPT_FORMAT = re.compile(
    r"CHECK (?P<mode>fast|full) exit=[012] sha=(?:[0-9a-f]{7}|none) dirty=(?:yes|no)"
    r" projects=\d+ settings=(?:ok|fail) suppressions=\d+ skipped=\d+"
    r" format=(?:ok|fail) lint=(?:ok|fail) types=(?:ok|fail) tests=(?:\d+|fail)"
    r" coverage=(?:\d+|n/a|skip|fail) base=(?:[0-9a-f]{7}|none|-) runner=(?:uv|override)"
)


def test_receipt_line(check):
    r = check.Receipt(
        mode="fast",
        exit=0,
        sha="abc1234",
        dirty=False,
        projects=1,
        settings="ok",
        suppressions=0,
        format="ok",
        lint="ok",
        types="ok",
        tests=12,
        coverage="skip",
    )
    assert r.line() == (
        "CHECK fast exit=0 sha=abc1234 dirty=no projects=1 settings=ok suppressions=0 skipped=0 "
        "format=ok lint=ok types=ok tests=12 coverage=skip base=- runner=uv"
    )


def test_receipt_line_full_fields(check):
    r = check.Receipt(
        mode="full",
        exit=1,
        sha="abc1234",
        dirty=True,
        projects=1,
        settings="ok",
        suppressions=2,
        skipped=3,
        format="ok",
        lint="ok",
        types="ok",
        tests="fail",
        coverage="n/a",
        base="def5678",
        runner="override",
    )
    assert r.line() == (
        "CHECK full exit=1 sha=abc1234 dirty=yes projects=1 settings=ok suppressions=2 skipped=3 "
        "format=ok lint=ok types=ok tests=fail coverage=n/a base=def5678 runner=override"
    )


def test_receipt_error_line(check):
    r = check.Receipt.setup_error("full", sha=None, dirty=False, error="uv not found on PATH")
    assert r.line() == 'CHECK full exit=2 sha=none dirty=no error="uv not found on PATH"'


def test_receipt_has_no_contracts_or_bench_fields(run_check, py_project):
    fast = receipt(run_check(py_project, "fast", "tests/test_core.py"))
    git(py_project, "checkout", "-q", "-b", "feature")
    (py_project / "README.md").write_text("docs\n")
    commit_all(py_project)
    full = receipt(run_check(py_project, "full", "--base", "main"))
    for line in (fast, full):
        assert RECEIPT_FORMAT.fullmatch(line), line
        assert "contracts=" not in line
        assert "bench=" not in line


def test_shard_mode_is_rejected(run_check, py_project):
    out = run_check(py_project, "shard", "1/2")
    sha = git(py_project, "rev-parse", "--short=7", "HEAD").strip()
    assert out.returncode == 2
    assert "Traceback" not in out.stderr
    assert receipt(out).startswith(f'CHECK shard exit=2 sha={sha} dirty=no error="')
    assert "usage:" not in out.stdout + out.stderr


def test_shard_mode_outside_a_git_tree_is_a_setup_error_receipt(run_check, tmp_path):
    out = run_check(tmp_path, "shard", "1/2")
    assert out.returncode == 2
    assert receipt(out) == 'CHECK shard exit=2 sha=none dirty=no error="not inside a git work tree"'


# --- install mode -------------------------------------------------------------


def fake_programs(where: Path, log: Path, failing: tuple[str, ...] = ()) -> Path:
    """`git` plus a `uv` that logs where it ran; returns the directory."""
    where.mkdir()
    (where / "git").symlink_to(shutil.which("git"))
    code = 1 if "uv" in failing else 0
    (where / "uv").write_text(
        f'#!/bin/sh\necho "$PWD uv $*" >> {log}\necho "uv says hi"\nexit {code}\n'
    )
    (where / "uv").chmod(0o755)
    return where


def test_install_mode_lines(run_check, monorepo, tmp_path):
    log = tmp_path / "calls.log"
    path = fake_programs(tmp_path / "bin", log)
    out = run_check(monorepo, "install", env={"PATH": str(path), "COVEN_CHECK_RUNNER": None})
    assert out.returncode == 0, out.stdout
    assert out.stdout.splitlines() == [
        "install backend (python): uv sync --locked -> ok",
        "install service (python): uv sync --locked -> ok",
    ]
    assert log.read_text().splitlines() == [
        f"{monorepo / 'backend'} uv sync --locked",
        f"{monorepo / 'service'} uv sync --locked",
    ]


def test_install_mode_needs_uv_before_it_starts(run_check, monorepo, tmp_path):
    path = fake_programs(tmp_path / "bin", tmp_path / "calls.log")
    (path / "uv").unlink()
    out = run_check(monorepo, "install", env={"PATH": str(path)})
    assert out.returncode == 2
    assert out.stdout.splitlines() == ['install: error="backend: uv not found on PATH"']


def test_install_mode_failure_shows_the_output(run_check, monorepo, tmp_path):
    path = fake_programs(tmp_path / "bin", tmp_path / "calls.log", failing=("uv",))
    out = run_check(monorepo, "install", env={"PATH": str(path)})
    assert out.returncode == 1
    lines = out.stdout.splitlines()
    assert lines[0] == "install backend (python): uv sync --locked -> failed (exit 1)"
    assert "install service (python): uv sync --locked -> failed (exit 1)" in lines
    assert lines.count("uv says hi") == 2


def test_install_mode_without_a_lockfile_is_a_setup_error(run_check, monorepo, tmp_path):
    (monorepo / "service/uv.lock").unlink()
    commit_all(monorepo)
    path = fake_programs(tmp_path / "bin", tmp_path / "calls.log")
    out = run_check(monorepo, "install", env={"PATH": str(path)})
    assert out.returncode == 2
    assert out.stdout.splitlines() == [
        'install: error="service: no uv.lock (the standard supports uv projects)"'
    ]
