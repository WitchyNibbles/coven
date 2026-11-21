import copy
import json
import subprocess
from pathlib import Path

import pytest
from support import GOOD, REPO, dump_toml

from coven import cli
from coven.project import ProjectError, drift, setup

BARE = '[project]\nname = "demo"\ndependencies = []\n'
TOOLS = ("ruff", "mypy", "pytest", "pytest-cov", "diff-cover", "pytest-xdist")
WORKFLOW = ".github/workflows/coven-check.yml"
SCRIPTS = tuple(
    f".coven/{name}"
    for name in (
        "check.py",
        "check_mod.py",
        "check_python.py",
        "check_repo.py",
        "check_stages.py",
        "check_ts.py",
    )
)
PYTHON_FILES = {*SCRIPTS, ".coven/ruff.toml", WORKFLOW}
TS_FILES = {*SCRIPTS, ".coven/tsconfig.base.json", ".coven/eslint.config.mjs", WORKFLOW}
SOURCES = {
    ".coven/ruff.toml": "standard/python/ruff.toml",
    ".coven/tsconfig.base.json": "standard/typescript/tsconfig.base.json",
    ".coven/eslint.config.mjs": "standard/typescript/eslint.config.mjs",
    WORKFLOW: "standard/workflows/coven-check.yml",
    **{rel: f"standard/{rel.removeprefix('.coven/')}" for rel in SCRIPTS},
}


@pytest.fixture
def bare_repo(git_repo: Path) -> Path:
    (git_repo / "pyproject.toml").write_text(BARE)
    return git_repo


def ts_app(root: Path, directory: str = ".", dev: tuple[str, ...] = ()) -> Path:
    """A TypeScript project at `root/directory` with no dependencies installed."""
    app = root / directory
    app.mkdir(parents=True, exist_ok=True)
    package = {"name": "app", "private": True, "devDependencies": dict.fromkeys(dev, "1.0.0")}
    (app / "package.json").write_text(json.dumps(package))
    (app / "tsconfig.json").write_text('{\n  "include": ["src"]\n}\n')
    (app / "package-lock.json").write_text("")
    return app


def test_setup_writes_owned_files(bare_repo: Path):
    result = setup(bare_repo)
    assert set(result.written) == PYTHON_FILES
    assert result.unchanged == ()
    for rel in PYTHON_FILES:
        assert (bare_repo / rel).read_bytes() == (REPO / SOURCES[rel]).read_bytes()
    assert not (bare_repo / ".coven/tsconfig.base.json").exists()


def test_setup_lists_wiring(bare_repo: Path):
    wiring = setup(bare_repo).wiring
    assert any("[tool.ruff] extend" in line for line in wiring)
    assert f"run: uv add --dev {' '.join(TOOLS)}" in wiring


def test_setup_lists_only_missing_dev_dependencies(bare_repo: Path):
    data = {
        "project": {"name": "demo"},
        "dependency-groups": {"dev": ["Ruff>=0.5", "pytest", "mypy[extra]==1.0"]},
    }
    (bare_repo / "pyproject.toml").write_text(dump_toml(data))
    assert "run: uv add --dev pytest-cov diff-cover pytest-xdist" in setup(bare_repo).wiring


def test_setup_has_no_wiring_for_a_conforming_project(py_project: Path):
    data = {**copy.deepcopy(GOOD), "dependency-groups": {"dev": list(TOOLS)}}
    (py_project / "pyproject.toml").write_text(dump_toml(data))
    assert setup(py_project).wiring == ()


def test_setup_updates_a_changed_check_script_without_force(bare_repo: Path):
    setup(bare_repo)
    target = bare_repo / ".coven/check_python.py"
    target.write_text("# stale copy\n")
    result = setup(bare_repo)
    assert result.written == (".coven/check_python.py",)
    assert target.read_bytes() == (REPO / "standard/check_python.py").read_bytes()


def test_setup_is_idempotent(bare_repo: Path):
    setup(bare_repo)
    result = setup(bare_repo)
    assert result.written == ()
    assert set(result.unchanged) == PYTHON_FILES


def test_project_refuses_to_overwrite_differing_workflow(
    bare_repo: Path, capsys: pytest.CaptureFixture[str]
):
    workflow = bare_repo / WORKFLOW
    workflow.parent.mkdir(parents=True)
    workflow.write_text("name: my-own-ci\non: push\n")
    assert cli.main(["project", str(bare_repo)]) == 1
    captured = capsys.readouterr()
    assert captured.err.startswith("coven: ")
    assert "--force" in captured.err
    assert f"--- {WORKFLOW}" in captured.err
    assert "-name: my-own-ci" in captured.err
    assert "+name: coven-check" in captured.err
    assert workflow.read_text() == "name: my-own-ci\non: push\n"
    assert not (bare_repo / ".coven").exists()


def test_project_force_overwrites(bare_repo: Path, capsys: pytest.CaptureFixture[str]):
    workflow = bare_repo / WORKFLOW
    workflow.parent.mkdir(parents=True)
    workflow.write_text("name: my-own-ci\non: push\n")
    assert cli.main(["project", str(bare_repo), "--force"]) == 0
    assert workflow.read_bytes() == (REPO / SOURCES[WORKFLOW]).read_bytes()
    assert f"wrote {WORKFLOW}" in capsys.readouterr().out


@pytest.mark.parametrize("rel", [".coven/ruff.toml", WORKFLOW])
def test_setup_refuses_every_differing_config_and_writes_nothing(bare_repo: Path, rel: str):
    target = bare_repo / rel
    target.parent.mkdir(parents=True)
    target.write_text("# mine\n")
    with pytest.raises(ProjectError, match="--force") as error:
        setup(bare_repo)
    assert rel in str(error.value)
    assert target.read_text() == "# mine\n"
    assert not (bare_repo / ".coven/check.py").exists()


def test_setup_refuses_a_differing_typescript_config(git_repo: Path):
    ts_app(git_repo)
    eslint = git_repo / ".coven/eslint.config.mjs"
    eslint.parent.mkdir()
    eslint.write_text("export default [];\n")
    with pytest.raises(ProjectError, match="eslint.config.mjs"):
        setup(git_repo)
    assert eslint.read_text() == "export default [];\n"
    setup(git_repo, force=True)
    assert eslint.read_bytes() == (REPO / "standard/typescript/eslint.config.mjs").read_bytes()


def test_a_config_equal_to_the_standard_is_not_a_conflict(bare_repo: Path):
    (bare_repo / ".coven").mkdir()
    (bare_repo / ".coven/ruff.toml").write_bytes((REPO / "standard/python/ruff.toml").read_bytes())
    assert ".coven/ruff.toml" in setup(bare_repo).unchanged


def test_not_a_git_repo(tmp_path: Path):
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "pyproject.toml").write_text(BARE)
    with pytest.raises(ProjectError, match="is not inside a git work tree") as error:
        setup(plain)
    assert str(plain) in str(error.value)


def test_setup_refuses_a_subdirectory_of_the_repo(bare_repo: Path):
    sub = bare_repo / "pkg"
    sub.mkdir()
    (sub / "pyproject.toml").write_text(BARE)
    with pytest.raises(ProjectError, match="is not the top of its git work tree") as error:
        setup(sub)
    assert str(bare_repo) in str(error.value)
    assert not (sub / ".coven").exists()
    assert not (sub / ".github").exists()


def test_drift_refuses_a_subdirectory_of_the_repo(bare_repo: Path):
    (bare_repo / "pkg").mkdir()
    with pytest.raises(ProjectError, match="is not the top of its git work tree"):
        drift(bare_repo / "pkg")


def test_no_project(git_repo: Path):
    with pytest.raises(
        ProjectError, match="no Python, TypeScript or Claude Code mod project found in"
    ) as error:
        setup(git_repo)
    assert str(git_repo) in str(error.value)


def test_drift_is_empty_after_setup(bare_repo: Path):
    setup(bare_repo)
    assert drift(bare_repo) == []


def test_drift_reports_missing_and_differs(bare_repo: Path):
    setup(bare_repo)
    (bare_repo / ".coven/check.py").unlink()
    (bare_repo / WORKFLOW).write_text("name: other\n")
    assert drift(bare_repo) == [
        ".coven/check.py: missing",
        f"{WORKFLOW}: differs from coven's standard (re-run coven project)",
    ]


def test_project_check_reports_drift_exit_1(bare_repo: Path, capsys: pytest.CaptureFixture[str]):
    setup(bare_repo)
    (bare_repo / ".coven/ruff.toml").write_text("# edited\n")
    assert cli.main(["project", str(bare_repo), "--check"]) == 1
    out = capsys.readouterr().out
    assert ".coven/ruff.toml: differs from coven's standard (re-run coven project)" in out


def test_project_check_exits_0_without_drift(bare_repo: Path, capsys: pytest.CaptureFixture[str]):
    setup(bare_repo)
    assert cli.main(["project", str(bare_repo), "--check"]) == 0
    assert capsys.readouterr().out == "coven project: no drift\n"


def test_project_check_changes_nothing(bare_repo: Path):
    assert cli.main(["project", str(bare_repo), "--check"]) == 1
    assert not (bare_repo / ".coven").exists()


def test_project_prints_what_it_wrote_and_the_wiring(
    bare_repo: Path, capsys: pytest.CaptureFixture[str]
):
    assert cli.main(["project", str(bare_repo)]) == 0
    out = capsys.readouterr().out
    assert "wrote .coven/check.py" in out
    assert f"run: uv add --dev {' '.join(TOOLS)}" in out
    assert cli.main(["project", str(bare_repo)]) == 0
    assert "unchanged .coven/check.py" in capsys.readouterr().out


def test_project_errors_print_without_a_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    assert cli.main(["project", str(tmp_path / "plain")]) == 1
    assert capsys.readouterr().err.startswith("coven: ")


def test_setup_does_not_touch_git_state(bare_repo: Path):
    def state() -> str:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=bare_repo, capture_output=True, text=True, check=True
        ).stdout

    before = state()
    setup(bare_repo)
    assert state() == before


def test_setup_ts_only_writes_ts_files_not_ruff(git_repo: Path):
    ts_app(git_repo)
    result = setup(git_repo)
    assert set(result.written) == TS_FILES
    assert not (git_repo / ".coven/ruff.toml").exists()
    for rel in TS_FILES:
        assert (git_repo / rel).read_bytes() == (REPO / SOURCES[rel]).read_bytes()
    assert drift(git_repo) == []


def test_setup_backend_frontend_writes_both(git_repo: Path):
    (git_repo / "backend").mkdir()
    (git_repo / "backend/pyproject.toml").write_text(BARE)
    ts_app(git_repo, "frontend")
    result = setup(git_repo)
    assert set(result.written) == PYTHON_FILES | TS_FILES
    assert f"run (in backend): uv add --dev {' '.join(TOOLS)}" in result.wiring
    assert drift(git_repo) == []


def test_ts_wiring_lines(git_repo: Path):
    ts_app(git_repo, "web")
    base = ".coven/tsconfig.base.json"
    assert setup(git_repo).wiring == (
        f'web/tsconfig.json: add "../{base}" last in "extends" '
        f'(make it a list: [<existing...>, "../{base}"])',
        "web/eslint.config.*: spread the default export of ../.coven/eslint.config.mjs last",
        "run (in web): npm install -D c8 eslint prettier typescript typescript-eslint",
        "web: dependencies not installed: run (in web): npm ci",
        "web: every TypeScript and JavaScript file git lists, configs such as eslint.config.* and "
        'vite or vitest configs included, must be in a tsconfig ("allowJs": true for .js and '
        ".mjs); re-run coven project once the project's tools run to list those that are not",
    )


def test_a_root_ts_project_is_told_to_ignore_the_standards_folder(git_repo: Path):
    ts_app(git_repo, dev=("c8", "eslint", "prettier", "typescript", "typescript-eslint"))
    wiring = setup(git_repo).wiring
    assert (
        'eslint.config.*: add { ignores: [".coven/"] } to the list it exports (the standard\'s '
        "own files are not the project's to lint)"
    ) in wiring


def test_ts_without_a_lockfile_says_so(git_repo: Path):
    ts_app(git_repo)
    (git_repo / "package-lock.json").unlink()
    assert any("no lockfile" in line for line in setup(git_repo).wiring)


def test_wired_ts_configs_need_no_extends_or_eslint_line(git_repo: Path):
    ts_app(git_repo, dev=("c8", "eslint", "prettier", "typescript", "typescript-eslint"))
    (git_repo / "tsconfig.json").write_text(
        '{\n  "extends": ["./.coven/tsconfig.base.json"],\n  "include": ["src"]\n}\n'
    )
    (git_repo / "eslint.config.mjs").write_text(
        'import base from "./.coven/eslint.config.mjs";\n'
        'export default [{ ignores: [".coven/"] }, ...base];\n'
    )
    wiring = setup(git_repo).wiring
    assert not any(line.startswith(("tsconfig.json:", "eslint.config.*:")) for line in wiring)


def test_an_eslint_config_naming_the_base_only_in_a_comment_is_named(git_repo: Path):
    ts_app(git_repo, "web")
    (git_repo / "web/eslint.config.mjs").write_text(
        '// "../.coven/eslint.config.mjs"\nexport default [];\n'
    )
    assert any(
        line.startswith("web/eslint.config.*: spread the default export")
        for line in setup(git_repo).wiring
    )


def test_a_ts_only_repo_reports_a_leftover_ruff_toml(git_repo: Path):
    ts_app(git_repo)
    setup(git_repo)
    (git_repo / ".coven/ruff.toml").write_text("")
    assert drift(git_repo) == [".coven/ruff.toml: no Python project uses it (delete it)"]


def test_setup_refuses_a_symlinked_standard_dir_pointing_outside(bare_repo: Path, tmp_path: Path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (bare_repo / ".coven").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ProjectError, match="outside the repo") as error:
        setup(bare_repo)
    assert ".coven" in str(error.value)
    assert list(outside.iterdir()) == []
    assert not (bare_repo / WORKFLOW).exists()


def test_setup_refuses_a_symlinked_owned_file_pointing_outside(bare_repo: Path, tmp_path: Path):
    victim = tmp_path / "victim.py"
    victim.write_text("# not coven's\n")
    (bare_repo / ".coven").mkdir()
    (bare_repo / ".coven/check.py").symlink_to(victim)
    with pytest.raises(ProjectError, match="outside the repo") as error:
        setup(bare_repo, force=True)
    assert ".coven/check.py" in str(error.value)
    assert victim.read_text() == "# not coven's\n"
    assert not (bare_repo / ".coven/ruff.toml").exists()


def test_setup_refuses_a_symlinked_workflows_dir_pointing_outside(bare_repo: Path, tmp_path: Path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (bare_repo / ".github").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ProjectError, match="outside the repo"):
        setup(bare_repo)
    assert list(outside.iterdir()) == []


def test_setup_allows_a_symlink_that_stays_inside_the_repo(bare_repo: Path):
    (bare_repo / "tools").mkdir()
    (bare_repo / ".coven").symlink_to("tools", target_is_directory=True)
    assert ".coven/check.py" in setup(bare_repo).written
    assert (bare_repo / "tools/check.py").is_file()
