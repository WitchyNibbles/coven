"""Claude Code mods as a project kind: discovery, the outside rule, the stages and the receipt.

`claude` is a fake script first on PATH in every test but the one marked `claude`.
"""

import json
import os
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path

import pytest
from support import commit_all, git, receipt

MOD = "plugins/mod"
MANIFEST = {
    "name": "mod",
    "version": "1.0.0",
    "description": "A mod for the tests",
    "author": {"name": "Test"},
}
REGISTER = "export function register(): void {}\n\nexport const answer: number = 42;\n"
SUITE = (
    'import { expect, test } from "claude-code/testing";\n'
    'import { answer } from "./register";\n\n'
    'test("answers", () => expect(answer).toBe(42));\n'
)

# Answers what `claude plugin validate --strict <dir>` and `claude plugin test <dir>` print.
FAKE_CLAUDE = """#!/bin/sh
echo "$@" >> "$FAKE_CLAUDE_LOG"
case "$2" in
  validate)
    if [ "$FAKE_CLAUDE_TYPES" = fail ]; then
      echo "line 1166, column 7: error TS2322: Type 'string' is not assignable to type 'number'."
      exit 1
    fi
    echo "Validation passed"
    exit 0
    ;;
  test)
    if [ "$FAKE_CLAUDE_TESTS" = fail ]; then
      printf ' 6 pass\\n 1 fail\\n\\nRan 7 tests across 1 file. [1.00s]\\n'
      exit 1
    fi
    if [ "$FAKE_CLAUDE_TESTS" = silent ]; then
      echo "nothing to report"
      exit 0
    fi
    printf ' 7 pass\\n 0 fail\\n\\nRan 7 tests across 1 file. [1.00s]\\n'
    exit 0
    ;;
esac
exit 2
"""


@dataclass
class FakeClaude:
    log: Path
    env: dict[str, str | None]

    def calls(self) -> list[str]:
        return self.log.read_text().splitlines() if self.log.exists() else []


@pytest.fixture
def fake_claude(tmp_path: Path) -> FakeClaude:
    """A `claude` first on PATH that logs its arguments, one line per call."""
    folder = tmp_path / "fake-bin"
    folder.mkdir()
    script = folder / "claude"
    script.write_text(FAKE_CLAUDE)
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    log = tmp_path / "claude.log"
    path = f"{folder}{os.pathsep}{os.environ['PATH']}"
    return FakeClaude(log, {"PATH": path, "FAKE_CLAUDE_LOG": str(log)})


@pytest.fixture
def real_claude() -> None:
    """Fails, never skips, a test marked `claude` when Claude Code is not on PATH."""
    if shutil.which("claude") is None:
        pytest.fail("claude (Claude Code) must be on PATH to run its mod tools")


def write_mod(root: Path, where: str = MOD, hooks: object = None) -> Path:
    """A mod below `root` that passes every stage; returns its directory."""
    mod = root / where
    (mod / ".claude-plugin").mkdir(parents=True)
    (mod / "hooks").mkdir()
    (mod / ".claude-plugin/plugin.json").write_text(json.dumps(MANIFEST))
    hooks = {"modules": ["./register.ts"]} if hooks is None else hooks
    (mod / "hooks/hooks.json").write_text(json.dumps(hooks))
    (mod / "hooks/register.ts").write_text(REGISTER)
    (mod / "hooks/register.test.ts").write_text(SUITE)
    return mod


@pytest.fixture
def mod_repo(git_repo: Path) -> Path:
    """A committed repo whose only project is a mod."""
    write_mod(git_repo)
    commit_all(git_repo)
    return git_repo


def kinds(check_repo, repo: Path) -> list[str]:
    return [project.kind for project in check_repo.discover(repo)]


def test_a_repo_with_only_a_mod_is_checked(run_check, mod_repo, fake_claude):
    out = run_check(mod_repo, "fast", f"{MOD}/hooks/register.test.ts", env=fake_claude.env)
    assert out.returncode == 0, out.stdout
    assert " exit=0 " in receipt(out)
    assert (
        " projects=1 settings=ok suppressions=0 skipped=0 format=n/a lint=n/a types=ok tests=7 "
        "coverage=n/a base=- " in receipt(out)
    )
    mod = (mod_repo / MOD).resolve()
    assert fake_claude.calls() == [f"plugin validate --strict {mod}", f"plugin test {mod}"]
    assert " dirty=no " in receipt(out)


def test_fast_without_test_paths_validates_and_runs_no_tests(run_check, mod_repo, fake_claude):
    out = run_check(mod_repo, "fast", env=fake_claude.env)
    assert out.returncode == 0, out.stdout
    assert " types=ok tests=0 coverage=n/a " in receipt(out)
    assert [call.split()[:2] for call in fake_claude.calls()] == [["plugin", "validate"]]


def test_fast_python_test_paths_leave_the_mod_tests_alone(run_check, py_project, fake_claude):
    write_mod(py_project)
    commit_all(py_project)
    out = run_check(py_project, "fast", "tests/test_core.py", env=fake_claude.env)
    assert out.returncode == 0, out.stdout
    assert " projects=2 " in receipt(out)
    assert " types=ok tests=1 " in receipt(out)
    assert [call.split()[:2] for call in fake_claude.calls()] == [["plugin", "validate"]]


def test_full_runs_the_mod_tests_whole(run_check, mod_repo, fake_claude):
    out = run_check(mod_repo, "full", env=fake_claude.env)
    assert out.returncode == 0, out.stdout
    assert " tests=7 coverage=n/a " in receipt(out)
    assert [call.split()[:2] for call in fake_claude.calls()] == [
        ["plugin", "validate"],
        ["plugin", "test"],
    ]


def test_command_hooks_alone_are_not_a_mod(check_repo, git_repo):
    write_mod(git_repo, hooks={"hooks": {}})
    assert kinds(check_repo, git_repo) == []


@pytest.mark.parametrize(
    "hooks",
    [{"modules": []}, {"modules": "./register.ts"}, ["./register.ts"], "text"],
    ids=["empty-list", "not-a-list", "array", "string"],
)
def test_only_a_non_empty_modules_list_makes_a_mod(check_repo, git_repo, hooks):
    write_mod(git_repo, hooks=hooks)
    assert kinds(check_repo, git_repo) == []


def test_an_unreadable_hooks_file_is_not_a_mod(check_repo, git_repo):
    mod = write_mod(git_repo)
    (mod / "hooks/hooks.json").write_text("{not json")
    assert kinds(check_repo, git_repo) == []


def test_a_mod_needs_its_manifest(check_repo, git_repo):
    mod = write_mod(git_repo)
    (mod / ".claude-plugin/plugin.json").unlink()
    assert kinds(check_repo, git_repo) == []


def test_a_mod_at_the_repo_root_is_found(check_repo, git_repo):
    write_mod(git_repo, ".")
    projects = check_repo.discover(git_repo)
    assert [(p.root, p.kind, p.manager) for p in projects] == [(git_repo.resolve(), "mod", None)]


def test_a_mod_inside_a_typescript_project_is_left_to_it(check_repo, git_repo):
    (git_repo / "package.json").write_text("{}")
    (git_repo / "tsconfig.json").write_text("{}")
    write_mod(git_repo)
    assert kinds(check_repo, git_repo) == ["typescript"]


def test_a_mod_inside_a_python_project_is_still_a_mod(check_repo, git_repo):
    (git_repo / "pyproject.toml").write_text("")
    write_mod(git_repo)
    assert kinds(check_repo, git_repo) == ["python", "mod"]


def test_two_mods_are_two_projects(check_repo, git_repo):
    write_mod(git_repo, "one")
    write_mod(git_repo, "two")
    assert kinds(check_repo, git_repo) == ["mod", "mod"]


def outside(check_repo, repo: Path) -> list[str]:
    commit_all(repo)
    return check_repo.outside_problems(repo, check_repo.discover(repo), check_repo.listing(repo))


def test_mod_sources_are_not_outside_every_project(check_repo, git_repo):
    mod = write_mod(git_repo)
    (mod / "hooks/view.tsx").write_text("export const view = 1;\n")
    assert outside(check_repo, git_repo) == []


def test_tsx_outside_a_mod_still_is(check_repo, git_repo):
    write_mod(git_repo)
    (git_repo / "other").mkdir()
    (git_repo / "other/view.tsx").write_text("export const view = 1;\n")
    assert outside(check_repo, git_repo) == ["other/view.tsx: outside every project"]


def test_command_hook_sources_are_outside_every_project(check_repo, git_repo):
    write_mod(git_repo, hooks={"hooks": {}})
    assert outside(check_repo, git_repo) == [
        f"{MOD}/hooks/register.test.ts: outside every project",
        f"{MOD}/hooks/register.ts: outside every project",
    ]


def test_a_type_error_fails_types(run_check, mod_repo, fake_claude):
    env = {**fake_claude.env, "FAKE_CLAUDE_TYPES": "fail"}
    out = run_check(mod_repo, "fast", f"{MOD}/hooks/register.test.ts", env=env)
    assert out.returncode == 1
    assert " types=fail tests=7 " in receipt(out)
    assert f"--- mod types ({MOD}) failed; last 20 lines of output ---" in out.stdout
    assert "error TS2322" in out.stdout


def test_a_failing_mod_test_fails_tests(run_check, mod_repo, fake_claude):
    env = {**fake_claude.env, "FAKE_CLAUDE_TESTS": "fail"}
    out = run_check(mod_repo, "fast", f"{MOD}/hooks/register.test.ts", env=env)
    assert out.returncode == 1
    assert " types=ok tests=fail " in receipt(out)
    assert f"--- mod tests ({MOD}) failed; last 20 lines of output ---" in out.stdout
    assert "1 fail" in out.stdout


def test_a_test_run_without_a_summary_fails_tests(run_check, mod_repo, fake_claude):
    env = {**fake_claude.env, "FAKE_CLAUDE_TESTS": "silent"}
    out = run_check(mod_repo, "fast", f"{MOD}/hooks/register.test.ts", env=env)
    assert out.returncode == 1
    assert " tests=fail " in receipt(out)
    assert "claude plugin test printed no pass count" in out.stdout


def test_a_suppression_in_a_mod_fails(run_check, mod_repo, fake_claude):
    marker = "// @ts" + "-ignore"
    (mod_repo / MOD / "hooks/register.tsx").write_text(f"{marker}\nexport const x: number = 'a';\n")
    commit_all(mod_repo)
    out = run_check(mod_repo, "fast", env=fake_claude.env)
    assert out.returncode == 1
    assert " suppressions=1 " in receipt(out)
    assert f"hooks/register.tsx:1: {marker}" in out.stdout


def test_a_vendored_mod_file_is_not_scanned(run_check, mod_repo, fake_claude):
    marker = "// @ts" + "-ignore"
    (mod_repo / MOD / "hooks/vendor.ts").write_text(f"{marker}\n")
    (mod_repo / ".gitattributes").write_text(f"{MOD}/hooks/vendor.ts linguist-vendored\n")
    commit_all(mod_repo)
    out = run_check(mod_repo, "fast", env=fake_claude.env)
    assert out.returncode == 0, out.stdout
    assert " suppressions=0 skipped=1 " in receipt(out)


def test_a_framework_component_in_a_mod_fails_settings(run_check, mod_repo, fake_claude):
    (mod_repo / MOD / "hooks/panel.vue").write_text("<template></template>\n")
    commit_all(mod_repo)
    out = run_check(mod_repo, "fast", env=fake_claude.env)
    assert out.returncode == 1
    assert "settings: hooks/panel.vue: not checked by the standard yet" in out.stdout


def test_without_claude_the_check_cannot_run(run_check, mod_repo, fake_claude):
    folders = os.environ["PATH"].split(os.pathsep)
    path = os.pathsep.join(folder for folder in folders if not (Path(folder) / "claude").exists())
    out = run_check(mod_repo, "fast", env={"PATH": path})
    assert out.returncode == 2
    assert receipt(out).endswith(' error="claude not found on PATH"')
    assert fake_claude.calls() == []


def test_a_repo_without_a_mod_never_needs_claude(run_check, py_project):
    folders = os.environ["PATH"].split(os.pathsep)
    path = os.pathsep.join(folder for folder in folders if not (Path(folder) / "claude").exists())
    out = run_check(py_project, "fast", env={"PATH": path})
    assert out.returncode == 0, out.stdout


def test_install_has_nothing_to_install_for_a_mod(run_check, mod_repo, fake_claude):
    out = run_check(mod_repo, "install", env=fake_claude.env)
    assert out.returncode == 0
    assert f"install {MOD} (mod): nothing to install" in out.stdout.splitlines()
    assert fake_claude.calls() == []


def test_python_and_mod_fold_together(run_check, py_project, fake_claude):
    git(py_project, "checkout", "-q", "-b", "feature")
    core = py_project / "src/pkg/core.py"
    core.write_text(core.read_text() + "\n\ndef sub(a: int, b: int) -> int:\n    return a - b\n")
    suite = py_project / "tests/test_core.py"
    body = "\n\ndef test_sub():\n    from pkg.core import sub\n\n    assert sub(3, 1) == 2\n"
    suite.write_text(suite.read_text() + body)
    write_mod(py_project)
    commit_all(py_project)
    out = run_check(py_project, "full", "--base", "main", env=fake_claude.env)
    assert out.returncode == 0, out.stdout
    assert " projects=2 " in receipt(out)
    assert " format=ok lint=ok types=ok tests=9 coverage=100 " in receipt(out)
    assert f"{MOD} (mod): settings=ok suppressions=0 format=n/a lint=n/a types=ok " in out.stdout


def test_a_mod_test_path_goes_to_the_mod_not_to_python(run_check, py_project, fake_claude):
    write_mod(py_project)
    commit_all(py_project)
    out = run_check(py_project, "fast", f"{MOD}/hooks/register.test.ts", env=fake_claude.env)
    assert out.returncode == 0, out.stdout
    assert " tests=7 " in receipt(out)


@pytest.mark.claude
@pytest.mark.usefixtures("real_claude")
def test_real_claude_checks_a_minimal_mod(run_check, git_repo, tmp_path):
    mod = git_repo / MOD
    (mod / ".claude-plugin").mkdir(parents=True)
    (mod / "hooks").mkdir()
    (mod / ".claude-plugin/plugin.json").write_text(json.dumps(MANIFEST))
    (mod / "hooks/hooks.json").write_text(json.dumps({"modules": ["./register.ts"]}))
    (mod / "hooks/register.ts").write_text(REGISTER)
    (mod / "hooks/register.test.ts").write_text(SUITE)
    commit_all(git_repo)
    home = tmp_path / "claude-home"
    config = tmp_path / "claude-config"
    home.mkdir()
    config.mkdir()
    env = {"HOME": str(home), "CLAUDE_CONFIG_DIR": str(config)}
    out = run_check(git_repo, "fast", f"{MOD}/hooks/register.test.ts", env=env)
    assert out.returncode == 0, out.stdout
    assert " types=ok tests=1 coverage=n/a " in receipt(out)
    assert git(git_repo, "status", "--porcelain").strip() == ""
