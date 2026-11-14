"""How check.py loads the modules beside it, what it says when one cannot be used, and how it finds projects."""

import ast
import importlib.util
import json
import py_compile
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest
from support import CHECK_MODULES, REAL_RUNNER, REPO, commit_all, receipt

STANDARD = REPO / "standard"
# The siblings check.py loads when it starts.
EAGER_UNDER_TEST = ("check_repo", "check_stages", "check_python")
# A module may import only the modules ranked below it.
RANK = {"check_repo": 0, "check_stages": 1, "check_python": 2, "check_ts": 2, "check": 3}
SIBLING_PREFIX = "coven_"


@pytest.fixture
def check_env() -> dict[str, str | None]:
    """Run the real runner: no in-process override, whatever tools this machine has."""
    return REAL_RUNNER


def copy_standard(repo: Path) -> Path:
    """The repo's `.coven/` with every module of the standard; returns the copied check.py."""
    (repo / ".coven").mkdir(exist_ok=True)
    for name in CHECK_MODULES:
        shutil.copy(STANDARD / name, repo / ".coven" / name)
    return repo / ".coven/check.py"


# A package.json naming a supported test runner, for projects whose tools are never installed.
UNINSTALLED_PACKAGE = '{ "devDependencies": { "c8": "12.0.0" } }\n'


def typescript_repo(root: Path, lockfile: str = "package-lock.json") -> None:
    (root / "package.json").write_text(UNINSTALLED_PACKAGE)
    for name in ("tsconfig.json", lockfile):
        (root / name).write_text("{}")


def test_a_lone_check_py_says_which_sibling_is_missing(run_check, py_project):
    (py_project / ".coven").mkdir(exist_ok=True)
    shutil.copy(STANDARD / "check.py", py_project / ".coven/check.py")
    out = run_check(py_project, "fast", check=py_project / ".coven/check.py")
    assert out.returncode == 2
    assert "Traceback" not in out.stderr
    assert receipt(out) == (
        'CHECK fast exit=2 sha=none dirty=no error=".coven/check_repo.py: '
        'cannot be loaded (FileNotFoundError); re-run coven project"'
    )


@pytest.mark.parametrize("name", EAGER_UNDER_TEST)
@pytest.mark.parametrize(
    ("source", "error"),
    [
        ("def broken(:\n", "cannot be loaded (SyntaxError)"),
        ("STANDARD_VERSION = 99\n", "from another version of the standard"),
        ('"""No version."""\n', "from another version of the standard"),
        ('{}["missing"]\n', "cannot be loaded (KeyError)"),
        ('raise RuntimeError("stale")\n', "cannot be loaded (RuntimeError)"),
    ],
    ids=["syntax-error", "other-version", "no-version", "key-error", "runtime-error"],
)
def test_a_bad_eager_sibling_is_a_setup_error(run_check, py_project, name, source, error):
    check = copy_standard(py_project)
    (py_project / f".coven/{name}.py").write_text(source)
    out = run_check(py_project, "fast", check=check)
    assert out.returncode == 2
    assert "Traceback" not in out.stderr
    assert receipt(out) == (
        f'CHECK fast exit=2 sha=none dirty=no error=".coven/{name}.py: {error}; '
        're-run coven project"'
    )


@pytest.mark.parametrize("name", EAGER_UNDER_TEST)
def test_bytecode_next_to_an_eager_sibling_is_never_loaded(run_check, py_project, name):
    check = copy_standard(py_project)
    stub = py_project / "stub.py"
    stub.write_text('"""A module from no version of the standard."""\n')
    sibling = str(py_project / f".coven/{name}.py")
    cached = Path(importlib.util.cache_from_source(sibling))
    py_compile.compile(
        str(stub),
        cfile=str(cached),
        dfile=sibling,
        invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH,
    )
    stub.unlink()
    commit_all(py_project)
    out = run_check(py_project, "fast", check=check)
    assert "another version" not in out.stdout
    assert "cannot be loaded" not in out.stdout
    rel = cached.relative_to(py_project).as_posix()
    assert f"settings: {rel}: not allowed" in out.stdout


@pytest.mark.parametrize("name", EAGER_UNDER_TEST)
def test_a_missing_eager_sibling_is_a_setup_error(run_check, py_project, name):
    check = copy_standard(py_project)
    (py_project / f".coven/{name}.py").unlink()
    out = run_check(py_project, "fast", check=check)
    assert out.returncode == 2
    assert "Traceback" not in out.stderr
    assert receipt(out) == (
        f'CHECK fast exit=2 sha=none dirty=no error=".coven/{name}.py: '
        'cannot be loaded (FileNotFoundError); re-run coven project"'
    )


def test_install_mode_reports_a_bad_sibling_in_its_own_form(run_check, py_project):
    check = copy_standard(py_project)
    (py_project / ".coven/check_repo.py").write_text("STANDARD_VERSION = 99\n")
    out = run_check(py_project, "install", check=check)
    assert out.returncode == 2
    assert out.stdout.splitlines()[-1] == (
        'install: error=".coven/check_repo.py: from another version of the standard; '
        're-run coven project"'
    )


TS_CALLABLES = (
    "def suppression_lines(text):\n    return []\n\n\ndef settings_problems(*a):\n    return []\n"
)
TS_MISMATCH = ".coven/check_ts.py: from another version of the standard; re-run coven project"


# "" is a check_ts.py from before the split: the callables, no STANDARD_VERSION.
@pytest.mark.parametrize("version", ["STANDARD_VERSION = 99\n", ""], ids=["other", "none"])
def test_a_pre_split_check_ts_is_from_another_version(run_check, git_repo, version):
    typescript_repo(git_repo)
    check = copy_standard(git_repo)
    (git_repo / ".coven/check_ts.py").write_text(version + TS_CALLABLES)
    commit_all(git_repo)
    out = run_check(git_repo, "fast", check=check)
    assert out.returncode == 2
    assert "Traceback" not in out.stderr
    assert receipt(out).endswith(f'error="{TS_MISMATCH}"')


@pytest.mark.parametrize("version", ["STANDARD_VERSION = 99\n", ""], ids=["other", "none"])
def test_install_mode_reports_a_pre_split_check_ts_in_its_own_form(run_check, git_repo, version):
    typescript_repo(git_repo)
    check = copy_standard(git_repo)
    (git_repo / ".coven/check_ts.py").write_text(version + TS_CALLABLES)
    commit_all(git_repo)
    out = run_check(git_repo, "install", check=check)
    assert out.returncode == 2
    assert "Traceback" not in out.stderr
    assert out.stdout.splitlines()[-1] == f'install: error="{TS_MISMATCH}"'


def test_a_sibling_cannot_be_imported_past_the_loader(tmp_path):
    # What today's self-contained check.py does with a new sibling: execute it with
    # .coven/ first on sys.path and nothing registered.
    standard_dir = tmp_path / ".coven"
    standard_dir.mkdir()
    for name in CHECK_MODULES:
        shutil.copy(STANDARD / name, standard_dir / name)
    code = "import runpy, sys; sys.path.insert(0, {d!r}); runpy.run_path({f!r})"
    out = subprocess.run(
        [
            sys.executable,
            "-c",
            code.format(d=str(standard_dir), f=str(standard_dir / "check_python.py")),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert "ModuleNotFoundError: No module named 'coven_check_repo'" in out.stderr
    assert not (standard_dir / "__pycache__").exists()


def test_every_module_carries_the_same_version(check):
    folder = STANDARD
    paths = sorted(folder.glob("check*.py"))
    assert [path.name for path in paths] == sorted(CHECK_MODULES), folder.name
    for path in paths:
        tree = ast.parse(path.read_text())
        values = [
            node.value.value
            for node in tree.body
            if isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Constant)
            and [t.id for t in node.targets if isinstance(t, ast.Name)] == ["STANDARD_VERSION"]
        ]
        assert values == [check.STANDARD_VERSION], f"{folder.name}/{path.name}"


def sibling_imports(path: Path) -> list[str]:
    """The standard's modules `path` imports: `import <name>` or `import_module("coven_<name>")`."""
    names = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names += [alias.name for alias in node.names if alias.name.startswith("check")]
        elif isinstance(node, ast.ImportFrom) and (node.module or "").startswith("check"):
            names.append(node.module or "")
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "import_module"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and str(node.args[0].value).startswith(SIBLING_PREFIX)
        ):
            names.append(str(node.args[0].value).removeprefix(SIBLING_PREFIX))
    return names


def test_modules_import_each_other_in_one_direction():
    for path in sorted(STANDARD.glob("check*.py")):
        for name in sibling_imports(path):
            assert name != "check", path.name
            assert RANK[name] < RANK[path.stem], f"{path.stem} imports {name}"


def stub_check_ts(directory: Path, version: int) -> None:
    """A minimal check_ts.py at this version of the standard, to stand in for the real one."""
    (directory / "check_ts.py").write_text(
        "import importlib\n\n"
        f"STANDARD_VERSION = {version}\n"
        'check_repo = importlib.import_module("coven_check_repo")\n\n\n'
        "def suppression_lines(text):\n    return []\n\n\n"
        "def settings_problems(*args):\n    return []\n"
    )


def load_copy(directory: Path, name: str) -> ModuleType:
    """A copy of the standard in `directory`, its check.py loaded as the `check` fixture does."""
    directory.mkdir(parents=True)
    for module in CHECK_MODULES:
        shutil.copy(STANDARD / module, directory / module)
    spec = importlib.util.spec_from_file_location(name, directory / "check.py")
    assert spec is not None
    assert spec.loader is not None
    loaded = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


@pytest.fixture
def isolated_siblings(monkeypatch):
    """Puts back whatever the loaders' sys.modules names held before the test, once it ends."""
    names = [Path(module).stem for module in CHECK_MODULES] + ["check_ts", "check_a", "check_b"]
    for name in names:
        monkeypatch.delitem(sys.modules, SIBLING_PREFIX + name, raising=False)


@pytest.mark.usefixtures("isolated_siblings")
def test_two_standards_keep_their_own_siblings(tmp_path):
    first = load_copy(tmp_path / "a", "coven_check_a")
    second = load_copy(tmp_path / "b", "coven_check_b")
    assert Path(first.check_repo.__file__).parent == tmp_path / "a"
    assert Path(second.check_repo.__file__).parent == tmp_path / "b"
    assert Path(first.check_stages.__file__).parent == tmp_path / "a"
    assert Path(second.check_stages.__file__).parent == tmp_path / "b"
    assert Path(first.check_python.__file__).parent == tmp_path / "a"
    assert Path(second.check_python.__file__).parent == tmp_path / "b"


@pytest.mark.usefixtures("isolated_siblings")
def test_a_lazy_check_ts_binds_its_own_check_repo(tmp_path):
    first = load_copy(tmp_path / "a", "coven_check_a")
    load_copy(tmp_path / "b", "coven_check_b")  # registers b's siblings since
    assert first.load_ts().check_repo is first.check_repo


def other_version(check: ModuleType, directory: Path) -> None:
    """Replaces the copied check_ts.py with one from another version of the standard than `check`."""
    stub_check_ts(directory, check.STANDARD_VERSION + 1)


@pytest.mark.usefixtures("isolated_siblings")
def test_a_sibling_from_another_version_leaves_the_registered_one(tmp_path):
    first = load_copy(tmp_path / "a", "coven_check_a")
    registered = first.load_ts()
    second = load_copy(tmp_path / "b", "coven_check_b")
    other_version(second, tmp_path / "b")
    with pytest.raises(second.StandardMismatchError):
        second.load_ts()
    assert sys.modules["coven_check_ts"] is registered


@pytest.mark.usefixtures("isolated_siblings")
def test_a_sibling_from_another_version_is_not_left_registered(tmp_path):
    check = load_copy(tmp_path / "a", "coven_check_a")
    other_version(check, tmp_path / "a")
    with pytest.raises(check.StandardMismatchError):
        check.load_ts()
    assert "coven_check_ts" not in sys.modules


@pytest.mark.usefixtures("isolated_siblings")
def test_a_check_ts_without_its_functions_is_not_left_registered(tmp_path):
    check = load_copy(tmp_path / "a", "coven_check_a")
    (tmp_path / "a/check_ts.py").write_text(f"STANDARD_VERSION = {check.STANDARD_VERSION}\n")
    with pytest.raises(AttributeError, match="check_ts.py has no suppression_lines"):
        check.load_ts()
    assert "coven_check_ts" not in sys.modules


def test_a_sibling_without_a_function_it_needs_cannot_be_used(check):
    with pytest.raises(AttributeError, match="check_ts.py has no suppression_lines"):
        check._check_usable("check_ts", ModuleType("stub"), ("suppression_lines",))


MISSING_TS = ".coven/check_ts.py is missing (re-run coven project)"


def without_check_ts(check: ModuleType, monkeypatch, repo: Path) -> object:
    """`repo` as a TypeScript project, with `check` finding no check_ts.py beside it."""

    def missing() -> ModuleType:
        raise FileNotFoundError(check.CHECK_TS)

    monkeypatch.setattr(check, "load_ts", missing)
    typescript_repo(repo)
    commit_all(repo)
    return check.check_repo.Project(repo, "typescript", "npm")


def test_without_check_ts_the_typescript_stages_fail(
    check, monkeypatch, tmp_path, git_repo, capsys
):
    project = without_check_ts(check, monkeypatch, git_repo)
    run = check.check_stages.Run(git_repo, "fast", None)
    stages = check._typescript_stages(project, run, [project], [], tmp_path / "out")
    failed = ("fail", False)
    assert stages == check.check_stages.Stages(
        failed, (0, False), failed, failed, failed, failed, failed, 0
    )
    assert capsys.readouterr().out.splitlines() == [f"settings: {MISSING_TS}", MISSING_TS]


def test_without_check_ts_nothing_is_installed(check, monkeypatch, git_repo):
    project = without_check_ts(check, monkeypatch, git_repo)
    assert check._install_commands(git_repo, [project]) == ([], MISSING_TS)


FRAMEWORK_PROBLEM = (
    "not checked by the standard yet (tsc and the base ESLint config do not cover it)"
)


def test_a_missing_check_ts_fails_every_typescript_stage(run_check, git_repo):
    typescript_repo(git_repo)
    (git_repo / "vendor").mkdir()
    (git_repo / "vendor/v.ts").write_text("export const v = 1\n")
    (git_repo / ".gitattributes").write_text("vendor/** linguist-vendored\n")
    (git_repo / "App.vue").write_text("<template><p>hi</p></template>\n")
    check = copy_standard(git_repo)
    (git_repo / ".coven/check_ts.py").unlink()
    commit_all(git_repo)
    out = run_check(git_repo, "fast", check=check)
    assert out.returncode == 1
    assert "Traceback" not in out.stderr
    assert out.stdout.splitlines()[:-1] == [
        f"settings: {MISSING_TS}",
        f"settings: App.vue: {FRAMEWORK_PROBLEM}",
        MISSING_TS,
        "note: 1 vendored/generated files skipped (.gitattributes)",
    ]
    assert receipt(out).endswith(
        " projects=1 settings=fail suppressions=0 skipped=1 format=fail lint=fail types=fail"
        " tests=fail coverage=fail base=- runner=uv"
    )


# --- the repo as git sees it -----------------------------------------------------


def test_git_state_clean_and_dirty(check_repo, git_repo):
    assert check_repo.git_state(git_repo).dirty is False
    (git_repo / "new.txt").write_text("x")
    state = check_repo.git_state(git_repo)
    assert state.dirty is True
    assert len(state.sha) == 7


def test_git_state_without_a_commit(check_repo, tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True, capture_output=True)
    assert check_repo.git_state(tmp_path).sha is None


def test_project_files_skips_coven_dir(check_repo, git_repo):
    (git_repo / ".coven").mkdir()
    (git_repo / ".coven/check.py").write_text("")
    (git_repo / "a.py").write_text("")
    assert [p.name for p in check_repo.project_files(git_repo, (".py",))] == ["a.py"]


def test_project_files_keeps_non_ascii_names(check_repo, git_repo):
    (git_repo / "é.py").write_text("")
    assert [p.name for p in check_repo.project_files(git_repo, (".py",))] == ["é.py"]


def mark(root, pattern, *attributes):
    (root / ".gitattributes").write_text(f"{pattern} {' '.join(attributes)}\n")
    commit_all(root)


def test_excluded_files_unit(check_repo, git_repo):
    mark(git_repo, "vendor/**", "linguist-vendored")
    (git_repo / "vendor").mkdir()
    (git_repo / "vendor/a.py").write_text("")
    (git_repo / "own.py").write_text("")
    files = [git_repo / "vendor/a.py", git_repo / "own.py"]
    assert check_repo.excluded_files(git_repo, files) == {git_repo / "vendor/a.py"}
    assert check_repo.excluded_files(git_repo, []) == set()


def test_excluded_files_honours_generated_and_unset(check_repo, git_repo):
    (git_repo / ".gitattributes").write_text(
        "gen/** linguist-generated=true\nno/** -linguist-vendored\nfalse/** linguist-vendored=false\n"
    )
    commit_all(git_repo)
    paths = [git_repo / name for name in ("gen/a.py", "no/b.py", "false/c.py")]
    assert check_repo.excluded_files(git_repo, paths) == {git_repo / "gen/a.py"}


def test_uncommitted_attributes_do_not_exclude_files(check_repo, git_repo):
    (git_repo / "src").mkdir()
    (git_repo / "src/.gitattributes").write_text("*.py linguist-generated\n")
    (git_repo / ".gitattributes").write_text("own/** linguist-vendored\n")
    files = [git_repo / "src/core.py", git_repo / "own/b.py"]
    assert check_repo.excluded_files(git_repo, files) == set()
    commit_all(git_repo)
    assert check_repo.excluded_files(git_repo, files) == set(files)


def test_attributes_edited_after_the_commit_do_not_count(check_repo, git_repo):
    (git_repo / ".gitattributes").write_text("own/** linguist-vendored\n")
    commit_all(git_repo)
    (git_repo / ".gitattributes").write_text("")
    assert check_repo.excluded_files(git_repo, [git_repo / "own/b.py"]) == {git_repo / "own/b.py"}


def test_excluded_files_before_the_first_commit(check_repo, tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / ".gitattributes").write_text("*.py linguist-vendored\n")
    assert check_repo.excluded_files(tmp_path, [tmp_path / "a.py"]) == set()


def test_info_attributes_do_not_exclude_files(check_repo, git_repo):
    info = git_repo / ".git/info"
    info.mkdir(exist_ok=True)
    (info / "attributes").write_text("own/** linguist-vendored\n")
    assert check_repo.excluded_files(git_repo, [git_repo / "own/b.py"]) == set()


def test_global_attributes_do_not_exclude_files(check_repo, git_repo, tmp_path, monkeypatch):
    attributes = tmp_path / "global-attributes"
    attributes.write_text("own/** linguist-vendored\n")
    config = tmp_path / "global-config"
    config.write_text(f"[core]\n\tattributesFile = {attributes}\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    assert check_repo.excluded_files(git_repo, [git_repo / "own/b.py"]) == set()


def test_system_attributes_do_not_exclude_files(check_repo, git_repo, tmp_path, monkeypatch):
    system = tmp_path / "system-config"
    attributes = tmp_path / "system-attributes"
    attributes.write_text("own/** linguist-vendored\n")
    system.write_text(f"[core]\n\tattributesFile = {attributes}\n")
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(system))
    monkeypatch.delenv("GIT_CONFIG_NOSYSTEM", raising=False)
    assert check_repo.excluded_files(git_repo, [git_repo / "own/b.py"]) == set()


# --- project discovery -----------------------------------------------------------


def write(root: Path, files: dict[str, str]) -> Path:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


def found(check, root: Path) -> list[tuple[str, str, str | None]]:
    return [
        (project.root.relative_to(root).as_posix(), project.kind, project.manager)
        for project in check.check_repo.discover(root)
    ]


def test_discover_backend_frontend(check, git_repo):
    write(
        git_repo,
        {
            "backend/pyproject.toml": "",
            "backend/uv.lock": "",
            "frontend/package.json": "{}",
            "frontend/tsconfig.json": "{}",
            "frontend/package-lock.json": "{}",
        },
    )
    assert found(check, git_repo) == [
        ("backend", "python", None),
        ("frontend", "typescript", "npm"),
    ]


def test_discover_workspace_is_one_project(check, git_repo):
    write(
        git_repo,
        {
            "package.json": json.dumps({"workspaces": ["packages/*"]}),
            "package-lock.json": "{}",
            "packages/a/package.json": "{}",
            "packages/a/tsconfig.json": "{}",
        },
    )
    assert found(check, git_repo) == [(".", "typescript", "npm")]


def test_discover_pnpm_workspace_file_makes_a_workspace(check, git_repo):
    write(
        git_repo,
        {
            "package.json": "{}",
            "pnpm-workspace.yaml": "packages:\n  - packages/*\n",
            "pnpm-lock.yaml": "",
            "tsconfig.json": "{}",
            "packages/a/package.json": "{}",
            "packages/a/tsconfig.json": "{}",
        },
    )
    assert found(check, git_repo) == [(".", "typescript", "pnpm")]


def test_discover_workspace_without_a_tsconfig_is_no_project(check, git_repo):
    write(
        git_repo,
        {
            "package.json": json.dumps({"workspaces": ["packages/*"]}),
            "packages/a/package.json": "{}",
        },
    )
    assert found(check, git_repo) == []


def test_package_without_tsconfig_is_not_a_typescript_project(check, git_repo):
    write(git_repo, {"package.json": "{}"})
    assert found(check, git_repo) == []


def test_skips_node_modules(check, git_repo):
    write(
        git_repo,
        {
            "app/package.json": "{}",
            "app/tsconfig.json": "{}",
            "app/node_modules/dep/package.json": "{}",
            "app/node_modules/dep/tsconfig.json": "{}",
            "app/node_modules/dep/pyproject.toml": "",
        },
    )
    assert found(check, git_repo) == [("app", "typescript", None)]


def test_skips_the_standard_directory(check, git_repo):
    write(git_repo, {"pyproject.toml": "", ".coven/pyproject.toml": ""})
    assert found(check, git_repo) == [(".", "python", None)]


@pytest.mark.parametrize(
    ("lockfile", "manager"),
    [
        ("package-lock.json", "npm"),
        ("pnpm-lock.yaml", "pnpm"),
        ("yarn.lock", "yarn"),
        ("bun.lock", "bun"),
        ("bun.lockb", "bun"),
    ],
)
def test_package_manager_comes_from_the_lockfile(check, git_repo, lockfile, manager):
    write(git_repo, {"package.json": "{}", "tsconfig.json": "{}", lockfile: ""})
    assert found(check, git_repo) == [(".", "typescript", manager)]


def test_no_lockfile_means_no_manager(check, git_repo):
    write(git_repo, {"package.json": "{}", "tsconfig.json": "{}"})
    assert found(check, git_repo) == [(".", "typescript", None)]


def test_nested_pyproject_is_its_own_project(check, git_repo):
    write(git_repo, {"pyproject.toml": "", "tools/pyproject.toml": ""})
    assert found(check, git_repo) == [(".", "python", None), ("tools", "python", None)]


def test_one_directory_can_hold_a_python_and_a_typescript_project(check, git_repo):
    write(git_repo, {"pyproject.toml": "", "package.json": "{}", "tsconfig.json": "{}"})
    assert found(check, git_repo) == [(".", "python", None), (".", "typescript", None)]


def test_projects_are_sorted_by_path(check, git_repo):
    write(git_repo, {"b/pyproject.toml": "", "a/pyproject.toml": "", "c/pyproject.toml": ""})
    assert [name for name, *_ in found(check, git_repo)] == ["a", "b", "c"]


def test_untracked_files_count_but_ignored_ones_do_not(check, git_repo):
    write(
        git_repo, {".gitignore": "vendor/\n", "vendor/pyproject.toml": "", "lib/pyproject.toml": ""}
    )
    assert found(check, git_repo) == [("lib", "python", None)]


def test_a_nested_typescript_project_inside_a_plain_package_stays_separate(check, git_repo):
    write(
        git_repo,
        {
            "package.json": "{}",
            "tsconfig.json": "{}",
            "apps/web/package.json": "{}",
            "apps/web/tsconfig.json": "{}",
        },
    )
    assert [name for name, *_ in found(check, git_repo)] == [".", "apps/web"]


def test_no_projects(check, git_repo):
    assert found(check, git_repo) == []


def test_discover_yarn_workspaces_object_form(check, git_repo):
    write(
        git_repo,
        {
            "package.json": json.dumps({"workspaces": {"packages": ["packages/*"]}}),
            "yarn.lock": "",
            "packages/a/package.json": "{}",
            "packages/a/tsconfig.json": "{}",
        },
    )
    assert found(check, git_repo) == [(".", "typescript", "yarn")]
