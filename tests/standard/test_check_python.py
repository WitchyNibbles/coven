"""The Python rules: suppression scanning, the settings a project may not weaken, the coverage config."""

import copy
import json
import tomllib
from pathlib import Path

import pytest
from support import GOOD, commit_all, dump_toml, git

DELETE = object()
# What the "not allowed" message says the standard installs into `.coven/`.
INSTALLS_ONLY = "the standard installs only " + ", ".join(
    (
        "check.py",
        "check_python.py",
        "check_repo.py",
        "check_stages.py",
        "check_ts.py",
        "eslint.config.mjs",
        "ruff.toml",
        "tsconfig.base.json",
    )
)


def edit(root: Path, path: tuple[str, ...], value: object) -> None:
    """Set (or, with DELETE, remove) a nested key in the repo's pyproject.toml."""
    file = root / "pyproject.toml"
    data = tomllib.loads(file.read_text())
    table = data
    for part in path[:-1]:
        table = table.setdefault(part, {})
    if value is DELETE:
        del table[path[-1]]
    else:
        table[path[-1]] = value
    file.write_text(dump_toml(data))


@pytest.fixture
def good_repo(git_repo: Path) -> Path:
    (git_repo / "pyproject.toml").write_text(dump_toml(copy.deepcopy(GOOD)))
    (git_repo / "src").mkdir()
    return git_repo


def test_good_pyproject_has_no_problems(check_python, good_repo):
    assert check_python.python_settings_problems(good_repo) == []


def test_local_ignore_is_rejected(check_python, good_repo):
    edit(good_repo, ("tool", "ruff", "lint", "ignore"), ["B006"])
    assert check_python.python_settings_problems(good_repo) == [
        "pyproject.toml [tool.ruff.lint] ignore: not allowed (it weakens .coven/ruff.toml)"
    ]


def test_missing_extend(check_python, good_repo):
    edit(good_repo, ("tool", "ruff", "extend"), DELETE)
    assert check_python.python_settings_problems(good_repo) == [
        'pyproject.toml [tool.ruff] extend: must be ".coven/ruff.toml"'
    ]


def test_ruff_top_level_key_is_rejected(check_python, good_repo):
    edit(good_repo, ("tool", "ruff", "line-length"), 200)
    assert check_python.python_settings_problems(good_repo) == [
        "pyproject.toml [tool.ruff] line-length: not allowed (it weakens .coven/ruff.toml)"
    ]


MYPY_ALLOWED = "only strict, files, plugins, enable_error_code, python_version, mypy_path"


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("warn_return_any", False),
        ("ignore_missing_imports", True),
        ("allow_untyped_defs", True),
        ("allow_untyped_calls", True),
        ("allow_subclassing_any", True),
        ("allow_any_generics", True),
        ("allow_untyped_decorators", True),
        ("implicit_reexport", True),
        ("exclude", ["build"]),
    ],
)
def test_mypy_key_outside_allowlist_is_rejected(check_python, good_repo, key, value):
    edit(good_repo, ("tool", "mypy", key), value)
    assert check_python.python_settings_problems(good_repo) == [
        f"pyproject.toml [tool.mypy] {key}: not allowed ({MYPY_ALLOWED})"
    ]


def test_mypy_strengthening_flag_set_true_is_accepted(check_python, good_repo):
    edit(good_repo, ("tool", "mypy", "warn_unreachable"), True)
    assert check_python.python_settings_problems(good_repo) == []


def test_mypy_strengthening_flag_set_false_is_rejected(check_python, good_repo):
    edit(good_repo, ("tool", "mypy", "warn_unreachable"), False)
    assert check_python.python_settings_problems(good_repo) == [
        "pyproject.toml [tool.mypy] warn_unreachable: must be true when set "
        "(it may only make mypy stricter)"
    ]


def test_mypy_allowlisted_keys_are_fine(check_python, good_repo):
    edit(good_repo, ("tool", "mypy", "python_version"), "3.12")
    edit(good_repo, ("tool", "mypy", "mypy_path"), "stubs")
    assert check_python.python_settings_problems(good_repo) == []


def test_mypy_strict_must_be_true(check_python, good_repo):
    edit(good_repo, ("tool", "mypy", "strict"), False)
    assert check_python.python_settings_problems(good_repo) == [
        "pyproject.toml [tool.mypy] strict: must be true"
    ]


def test_mypy_error_codes_must_be_complete(check_python, good_repo):
    edit(good_repo, ("tool", "mypy", "enable_error_code"), ["ignore-without-code", "truthy-bool"])
    assert check_python.python_settings_problems(good_repo) == [
        "pyproject.toml [tool.mypy] enable_error_code: "
        "must include possibly-undefined, redundant-expr, exhaustive-match"
    ]


def test_mypy_files_must_be_non_empty(check_python, good_repo):
    edit(good_repo, ("tool", "mypy", "files"), [])
    assert check_python.python_settings_problems(good_repo) == [
        "pyproject.toml [tool.mypy] files: must be a non-empty list"
    ]


def test_mypy_files_entries_must_be_directories(check_python, good_repo):
    (good_repo / "src/core.py").write_text("x = 1\n")
    edit(good_repo, ("tool", "mypy", "files"), ["src", "src/core.py", "missing"])
    assert check_python.python_settings_problems(good_repo) == [
        'pyproject.toml [tool.mypy] files: "src/core.py" must be a directory',
        'pyproject.toml [tool.mypy] files: "missing" must be a directory',
    ]


def test_mypy_files_must_hold_every_non_test_python_file(check_python, good_repo):
    (good_repo / "tests").mkdir()
    (good_repo / "tests/test_core.py").write_text("")
    (good_repo / "app").mkdir()
    (good_repo / "app/untyped.py").write_text("def f(x):\n    return x\n")
    (good_repo / "app/stub.pyi").write_text("x: int\n")
    edit(good_repo, ("tool", "mypy", "files"), ["tests"])
    assert check_python.python_settings_problems(good_repo) == [
        "pyproject.toml [tool.mypy] files: must hold every non-test Python file "
        "(app/stub.pyi, app/untyped.py outside them)"
    ]


def test_mypy_files_long_outside_list_is_shortened(check_python, good_repo):
    (good_repo / "app").mkdir()
    for n in range(7):
        (good_repo / f"app/m{n}.py").write_text("")
    assert check_python.python_settings_problems(good_repo) == [
        "pyproject.toml [tool.mypy] files: must hold every non-test Python file "
        "(app/m0.py, app/m1.py, app/m2.py, app/m3.py, app/m4.py and 2 more outside them)"
    ]


def test_mypy_files_need_not_hold_tests_or_vendored_code(check_python, good_repo):
    for name in (
        "tests/helpers.py",
        "pkg/tests/fixtures.py",
        "test_top.py",
        "pkg/thing_test.py",
        "conftest.py",
        "vendor/lib.py",
    ):
        (good_repo / name).parent.mkdir(parents=True, exist_ok=True)
        (good_repo / name).write_text("")
    (good_repo / ".gitattributes").write_text("vendor/** linguist-vendored\n")
    git(good_repo, "add", ".gitattributes")
    git(good_repo, "commit", "-q", "-m", "attributes")
    assert check_python.python_settings_problems(good_repo) == []


def test_mypy_files_dot_holds_everything(check_python, good_repo):
    (good_repo / "app.py").write_text("")
    edit(good_repo, ("tool", "mypy", "files"), ["."])
    assert check_python.python_settings_problems(good_repo) == []


def test_pydantic_needs_plugin(check_python, good_repo):
    edit(good_repo, ("project", "dependencies"), ["pydantic>=2"])
    assert check_python.python_settings_problems(good_repo) == [
        'pyproject.toml [tool.mypy] plugins: must include "pydantic.mypy" (pydantic is a dependency)'
    ]


def test_pydantic_with_plugin_is_fine(check_python, good_repo):
    edit(good_repo, ("project", "dependencies"), ["pydantic>=2"])
    edit(good_repo, ("tool", "mypy", "plugins"), ["pydantic.mypy"])
    assert check_python.python_settings_problems(good_repo) == []


def test_filterwarnings_ignore_rejected(check_python, good_repo):
    edit(
        good_repo,
        ("tool", "pytest", "ini_options", "filterwarnings"),
        ["error", "ignore::DeprecationWarning"],
    )
    assert check_python.python_settings_problems(good_repo) == [
        "pyproject.toml [tool.pytest.ini_options] filterwarnings: "
        '"ignore::DeprecationWarning" must start with "error"'
    ]


def test_filterwarnings_must_be_non_empty(check_python, good_repo):
    edit(good_repo, ("tool", "pytest", "ini_options", "filterwarnings"), [])
    assert check_python.python_settings_problems(good_repo) == [
        "pyproject.toml [tool.pytest.ini_options] filterwarnings: must be a non-empty list"
    ]


def test_pytest_strict_required(check_python, good_repo):
    edit(good_repo, ("tool", "pytest", "ini_options", "strict"), DELETE)
    assert check_python.python_settings_problems(good_repo) == [
        "pyproject.toml [tool.pytest.ini_options] strict: must be true"
    ]


PYTEST_ALLOWED = (
    "only strict, filterwarnings, testpaths, markers, minversion, required_plugins, timeout, "
    "xfail_strict, pythonpath"
)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("addopts", "-p no:warnings"),
        ("python_files", ["test_core.py"]),
        ("python_functions", ["check_*"]),
        ("norecursedirs", ["tests"]),
        ("console_output_style", "classic"),
    ],
)
def test_pytest_key_outside_allowlist_is_rejected(check_python, good_repo, key, value):
    edit(good_repo, ("tool", "pytest", "ini_options", key), value)
    assert check_python.python_settings_problems(good_repo) == [
        f"pyproject.toml [tool.pytest.ini_options] {key}: not allowed ({PYTEST_ALLOWED})"
    ]


def test_pytest_allowlisted_keys_are_fine(check_python, good_repo):
    (good_repo / "tests").mkdir()
    (good_repo / "tests/test_a.py").write_text("")
    for key, value in (
        ("testpaths", ["tests"]),
        ("markers", ["slow: slow tests"]),
        ("minversion", "9.0"),
        ("required_plugins", ["pytest-cov"]),
        ("timeout", "30"),
        ("xfail_strict", True),
    ):
        edit(good_repo, ("tool", "pytest", "ini_options", key), value)
    assert check_python.python_settings_problems(good_repo) == []


def test_xfail_strict_false_is_rejected(check_python, good_repo):
    edit(good_repo, ("tool", "pytest", "ini_options", "xfail_strict"), False)
    assert check_python.python_settings_problems(good_repo) == [
        "pyproject.toml [tool.pytest.ini_options] xfail_strict: must be true when set"
    ]


def test_testpaths_must_cover_every_test_file(check_python, good_repo):
    (good_repo / "tests").mkdir()
    (good_repo / "tests/test_a.py").write_text("")
    (good_repo / "src/test_hidden.py").write_text("")
    (good_repo / "src/b_test.py").write_text("")
    edit(good_repo, ("tool", "pytest", "ini_options", "testpaths"), ["tests"])
    assert check_python.python_settings_problems(good_repo) == [
        "pyproject.toml [tool.pytest.ini_options] testpaths: src/b_test.py, src/test_hidden.py "
        "outside them (pytest would not run them)"
    ]


def test_testpaths_globs_and_vendored_tests(check_python, good_repo):
    (good_repo / "pkg_a/tests").mkdir(parents=True)
    (good_repo / "pkg_a/tests/test_a.py").write_text("")
    (good_repo / "vendor").mkdir()
    (good_repo / "vendor/test_v.py").write_text("")
    (good_repo / ".gitattributes").write_text("vendor/** linguist-vendored\n")
    git(good_repo, "add", ".gitattributes")
    git(good_repo, "commit", "-q", "-m", "attributes")
    edit(good_repo, ("tool", "pytest", "ini_options", "testpaths"), ["pkg_*/tests"])
    assert check_python.python_settings_problems(good_repo) == []


def test_testpaths_must_be_a_list_of_paths(check_python, good_repo):
    edit(good_repo, ("tool", "pytest", "ini_options", "testpaths"), True)
    assert check_python.python_settings_problems(good_repo) == [
        "pyproject.toml [tool.pytest.ini_options] testpaths: must be a list of paths"
    ]


def test_coverage_branch_required(check_python, good_repo):
    edit(good_repo, ("tool", "coverage", "run", "branch"), False)
    assert check_python.python_settings_problems(good_repo) == [
        "pyproject.toml [tool.coverage.run] branch: must be true"
    ]


COVERAGE_RUN = "only branch, concurrency, parallel, patch, sigterm"
REPORT_NOT_ALLOWED = "the check sets coverage reporting"


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("omit", ["src/legacy/*"]),
        ("source", ["src/pkg"]),
        ("include", ["src/pkg/*"]),
        ("data_file", "elsewhere"),
    ],
)
def test_coverage_run_key_outside_allowlist_is_rejected(check_python, good_repo, key, value):
    edit(good_repo, ("tool", "coverage", "run", key), value)
    assert check_python.python_settings_problems(good_repo) == [
        f"pyproject.toml [tool.coverage.run] {key}: not allowed ({COVERAGE_RUN})"
    ]


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("exclude_lines", ["raise NotImplementedError"]),
        ("exclude_also", ["def __repr__"]),
        ("include", ["src/pkg/core.py"]),
        ("omit", ["src/*"]),
        ("fail_under", 0),
        ("show_missing", True),
        ("precision", 1),
    ],
)
def test_every_coverage_report_key_is_rejected(check_python, good_repo, key, value):
    edit(good_repo, ("tool", "coverage", "report", key), value)
    assert check_python.python_settings_problems(good_repo) == [
        f"pyproject.toml [tool.coverage.report] {key}: not allowed ({REPORT_NOT_ALLOWED})"
    ]


def test_coverage_other_sections_are_rejected(check_python, good_repo):
    edit(good_repo, ("tool", "coverage", "paths", "source"), ["src", "/ci/src"])
    assert check_python.python_settings_problems(good_repo) == [
        "pyproject.toml [tool.coverage.paths]: not allowed (only run)"
    ]


def test_coverage_allowlisted_keys_are_fine(check_python, good_repo):
    for section, key, value in (
        ("run", "concurrency", ["thread"]),
        ("run", "parallel", True),
        ("run", "patch", ["subprocess"]),
        ("run", "sigterm", True),
    ):
        edit(good_repo, ("tool", "coverage", section, key), value)
    assert check_python.python_settings_problems(good_repo) == []


@pytest.mark.parametrize(
    ("name", "content", "problem"),
    [
        (
            "ruff.toml",
            "line-length = 80\n",
            "ruff.toml: not allowed (it replaces .coven/ruff.toml)",
        ),
        (
            ".ruff.toml",
            "line-length = 80\n",
            ".ruff.toml: not allowed (it replaces .coven/ruff.toml)",
        ),
        ("mypy.ini", "[mypy]\n", "mypy.ini: not allowed (mypy reads it before pyproject.toml)"),
        (".mypy.ini", "[mypy]\n", ".mypy.ini: not allowed (mypy reads it before pyproject.toml)"),
        (
            "setup.cfg",
            "[metadata]\nname = x\n\n[mypy]\nstrict = false\n",
            "setup.cfg [mypy]: not allowed (mypy reads it before pyproject.toml)",
        ),
        (
            "setup.cfg",
            "[tool:pytest]\naddopts = -q\n",
            "setup.cfg [tool:pytest]: not allowed (pytest reads it before pyproject.toml)",
        ),
        (
            "pytest.ini",
            "[pytest]\n",
            "pytest.ini: not allowed (pytest reads it before pyproject.toml)",
        ),
        (
            "tox.ini",
            "[pytest]\naddopts = -q\n",
            "tox.ini [pytest]: not allowed (pytest reads it before pyproject.toml)",
        ),
        (
            ".coveragerc",
            "[run]\nbranch = false\n",
            ".coveragerc: not allowed (coverage reads it before pyproject.toml)",
        ),
        (
            ".coveragerc.toml",
            "[run]\nbranch = false\n",
            ".coveragerc.toml: not allowed (coverage reads it before pyproject.toml)",
        ),
        (
            ".pytest.ini",
            "[pytest]\n",
            ".pytest.ini: not allowed (pytest reads it before pyproject.toml)",
        ),
        (
            "pytest.toml",
            "[pytest]\n",
            "pytest.toml: not allowed (pytest reads it before pyproject.toml)",
        ),
        (
            ".pytest.toml",
            "[pytest]\n",
            ".pytest.toml: not allowed (pytest reads it before pyproject.toml)",
        ),
        (
            "setup.cfg",
            "[coverage:run]\nbranch = false\n",
            "setup.cfg [coverage:*]: not allowed (coverage reads it before pyproject.toml)",
        ),
        (
            "tox.ini",
            "[coverage:report]\nomit = x\n",
            "tox.ini [coverage:*]: not allowed (coverage reads it before pyproject.toml)",
        ),
    ],
)
def test_stray_config_files(check_python, good_repo, name, content, problem):
    (good_repo / "src/x.py").write_text("x = 1\n")
    (good_repo / name).write_text(content)
    assert check_python.python_settings_problems(good_repo) == [problem]


def test_harmless_setup_cfg_and_tox_ini_are_fine(check_python, good_repo):
    (good_repo / "setup.cfg").write_text("[metadata]\nname = x\n")
    (good_repo / "tox.ini").write_text("[tox]\nenvlist = py312\n")
    assert check_python.python_settings_problems(good_repo) == []


def test_ruff_toml_in_ignored_dir_without_python_below_is_not_counted(check_python, good_repo):
    (good_repo / ".gitignore").write_text("build/\n")
    (good_repo / "build").mkdir()
    (good_repo / "build" / "ruff.toml").write_text("line-length = 80\n")
    assert check_python.python_settings_problems(good_repo) == []


def test_gitignored_config_files_still_count(check_python, good_repo):
    (good_repo / ".gitignore").write_text("mypy.ini\nruff.toml\n")
    (good_repo / "mypy.ini").write_text("[mypy]\nstrict = false\n")
    (good_repo / "ruff.toml").write_text("lint.ignore = ['F401']\n")
    (good_repo / "src/x.py").write_text("x = 1\n")
    assert check_python.python_settings_problems(good_repo) == [
        "mypy.ini: not allowed (mypy reads it before pyproject.toml)",
        "ruff.toml: not allowed (it replaces .coven/ruff.toml)",
    ]


def test_info_exclude_cannot_hide_a_config_file(check_python, good_repo):
    (good_repo / ".git/info").mkdir(exist_ok=True)
    (good_repo / ".git/info/exclude").write_text("ruff.toml\n")
    (good_repo / "ruff.toml").write_text("lint.ignore = ['F401']\n")
    (good_repo / "src/x.py").write_text("x = 1\n")
    assert check_python.python_settings_problems(good_repo) == [
        "ruff.toml: not allowed (it replaces .coven/ruff.toml)"
    ]


@pytest.mark.parametrize("skipped", [".venv", "venv", "node_modules", ".git"])
def test_tool_directories_are_not_walked(check_python, good_repo, skipped):
    (good_repo / skipped / "pkg").mkdir(parents=True, exist_ok=True)
    (good_repo / skipped / "pkg" / "mypy.ini").write_text("[mypy]\n")
    assert check_python.python_settings_problems(good_repo) == []


def test_config_files_in_the_standards_directory_are_reported_as_unowned_only(
    check_python, good_repo
):
    (good_repo / ".coven/pkg").mkdir(parents=True)
    (good_repo / ".coven/pkg/mypy.ini").write_text("[mypy]\n")
    assert check_python.python_settings_problems(good_repo) == [
        f".coven/pkg/mypy.ini: not allowed ({INSTALLS_ONLY})"
    ]


def test_problems_are_ordered_tool_by_tool_then_stray_files(check_python, good_repo):
    edit(good_repo, ("tool", "coverage", "run", "branch"), False)
    edit(good_repo, ("tool", "pytest", "ini_options", "strict"), False)
    edit(good_repo, ("tool", "mypy", "strict"), False)
    edit(good_repo, ("tool", "ruff", "extend"), DELETE)
    (good_repo / "mypy.ini").write_text("[mypy]\n")
    (good_repo / "src/x.py").write_text("x = 1\n")
    edit(good_repo, ("tool", "mypy", "files"), ["src", "missing"])
    problems = check_python.python_settings_problems(good_repo)
    assert [p.split(":")[0] for p in problems] == [
        "pyproject.toml [tool.ruff] extend",
        "pyproject.toml [tool.mypy] strict",
        "pyproject.toml [tool.mypy] files",
        "pyproject.toml [tool.pytest.ini_options] strict",
        "pyproject.toml [tool.coverage.run] branch",
        "mypy.ini",
    ]


def test_invalid_toml_is_a_problem(check_python, git_repo):
    (git_repo / "pyproject.toml").write_text("[tool\n")
    [problem] = check_python.python_settings_problems(git_repo)
    assert problem.startswith("pyproject.toml: invalid TOML")


def test_missing_pyproject_is_a_problem(check_python, git_repo):
    assert check_python.python_settings_problems(git_repo) == ["pyproject.toml: missing"]


def test_nested_ruff_toml_with_python_below_is_a_problem(check_python, good_repo):
    (good_repo / "src/sub").mkdir()
    (good_repo / "src/sub/ruff.toml").write_text("line-length = 80\n")
    (good_repo / "src/sub/x.py").write_text("x = 1\n")
    assert check_python.python_settings_problems(good_repo) == [
        "src/sub/ruff.toml: not allowed (it replaces .coven/ruff.toml)"
    ]


def test_ruff_toml_without_python_below_is_not_counted(check_python, good_repo):
    (good_repo / "src/standard/python").mkdir(parents=True)
    (good_repo / "src/standard/python/ruff.toml").write_text("line-length = 100\n")
    (good_repo / "src/standard/check.py").write_text("x = 1\n")
    assert check_python.python_settings_problems(good_repo) == []


def test_ruff_toml_does_not_apply_to_sibling_python(check_python, good_repo):
    (good_repo / "src/a").mkdir()
    (good_repo / "src/b").mkdir()
    (good_repo / "src/a/ruff.toml").write_text("line-length = 80\n")
    (good_repo / "src/b/x.py").write_text("x = 1\n")
    assert check_python.python_settings_problems(good_repo) == []


def test_root_ruff_toml_with_python_anywhere_is_a_problem(check_python, good_repo):
    (good_repo / "ruff.toml").write_text("line-length = 80\n")
    (good_repo / "src" / "m.pyi").write_text("x: int\n")
    assert check_python.python_settings_problems(good_repo) == [
        "ruff.toml: not allowed (it replaces .coven/ruff.toml)"
    ]


def test_coven_base_file_is_not_stray(check_python, good_repo):
    (good_repo / ".coven").mkdir()
    (good_repo / ".coven" / "ruff.toml").write_text("line-length = 100\n")
    (good_repo / "src/x.py").write_text("x = 1\n")
    assert check_python.python_settings_problems(good_repo) == []


def test_native_pytest_table_is_checked_when_it_has_keys(check_python, good_repo):
    edit(good_repo, ("tool", "pytest", "strict"), True)
    edit(good_repo, ("tool", "pytest", "filterwarnings"), ["error", "ignore::UserWarning"])
    assert check_python.python_settings_problems(good_repo) == [
        'pyproject.toml [tool.pytest] filterwarnings: "ignore::UserWarning" must start with "error"'
    ]


def test_native_pytest_table_is_complete_when_good(check_python, good_repo):
    edit(good_repo, ("tool", "pytest", "strict"), True)
    edit(good_repo, ("tool", "pytest", "filterwarnings"), ["error"])
    assert check_python.python_settings_problems(good_repo) == []


def test_native_pytest_table_needs_strict_and_rejects_addopts(check_python, good_repo):
    edit(good_repo, ("tool", "pytest", "addopts"), ["-q"])
    assert check_python.python_settings_problems(good_repo) == [
        "pyproject.toml [tool.pytest] strict: must be true",
        "pyproject.toml [tool.pytest] filterwarnings: must be a non-empty list",
        f"pyproject.toml [tool.pytest] addopts: not allowed ({PYTEST_ALLOWED})",
    ]


def test_info_attributes_file_is_rejected(check_python, check_repo, good_repo):
    info = good_repo / ".git/info"
    info.mkdir(exist_ok=True)
    (info / "attributes").write_text("own/** linguist-vendored\n")
    where = check_repo.git(good_repo, "rev-parse", "--git-path", "info/attributes").stdout.strip()
    assert check_python.python_settings_problems(good_repo) == [
        f"{where}: not allowed (local git attributes can hide code from the check)"
    ]


def test_empty_info_attributes_file_is_fine(check_python, good_repo):
    info = good_repo / ".git/info"
    info.mkdir(exist_ok=True)
    (info / "attributes").write_text("\n")
    assert check_python.python_settings_problems(good_repo) == []


def test_nested_coven_directory_is_walked(check_python, good_repo):
    nested = good_repo / "src/demo/.coven"
    nested.mkdir(parents=True)
    (nested / "mypy.ini").write_text("[mypy]\n")
    (nested / "ruff.toml").write_text("line-length = 80\n")
    (nested / "x.py").write_text("")
    assert check_python.python_settings_problems(good_repo) == [
        "pyproject.toml [tool.mypy] files: must hold every non-test Python file "
        "(src/demo/.coven/x.py outside them)",
        "src/demo/.coven/mypy.ini: not allowed (mypy reads it before pyproject.toml)",
        "src/demo/.coven/ruff.toml: not allowed (it replaces .coven/ruff.toml)",
        "src/demo/.coven: not allowed (.coven/ lives only at the repo root)",
    ]


def test_own_worktree_inside_the_repo_is_not_walked(check_python, good_repo):
    (good_repo / ".gitignore").write_text(".worktrees/\n")
    commit_all(good_repo)
    git(good_repo, "worktree", "add", "-q", ".worktrees/wt")
    (good_repo / ".worktrees/wt/.coven").mkdir()
    assert check_python.python_settings_problems(good_repo) == []


def test_directory_posing_as_a_worktree_is_walked(check_python, good_repo):
    fake = good_repo / "fake"
    (fake / ".coven").mkdir(parents=True)
    (fake / ".git").write_text("gitdir: /elsewhere/.git/worktrees/fake\n")
    assert check_python.python_settings_problems(good_repo) == [
        "fake/.coven: not allowed (.coven/ lives only at the repo root)"
    ]


@pytest.mark.parametrize("skipped", [".gen", "__pycache__", "site-packages", "node_modules"])
def test_mypy_files_do_not_hold_what_mypy_skips(check_python, good_repo, skipped):
    (good_repo / "src/demo" / skipped).mkdir(parents=True)
    (good_repo / "src/demo" / skipped / "x.py").write_text("")
    assert check_python.python_settings_problems(good_repo) == [
        "pyproject.toml [tool.mypy] files: must hold every non-test Python file "
        f"(src/demo/{skipped}/x.py outside them)"
    ]


def test_mypy_files_hold_a_hidden_directory_listed_by_name(check_python, good_repo):
    (good_repo / "src/demo/.gen").mkdir(parents=True)
    (good_repo / "src/demo/.gen/x.py").write_text("")
    edit(good_repo, ("tool", "mypy", "files"), ["src", "src/demo/.gen"])
    assert check_python.python_settings_problems(good_repo) == []


@pytest.mark.parametrize(
    "testpaths",
    [["/abs/tests"], ["../outside"], ["tests/../../outside"], ["/abs/*"], "tests /abs", ["../*"]],
)
def test_testpaths_must_stay_inside_the_project(check_python, good_repo, testpaths):
    (good_repo / "tests").mkdir()
    (good_repo / "tests/test_a.py").write_text("")
    edit(good_repo, ("tool", "pytest", "ini_options", "testpaths"), testpaths)
    problems = check_python.python_settings_problems(good_repo)
    assert len(problems) == 1
    assert problems[0].startswith(
        "pyproject.toml [tool.pytest.ini_options] testpaths: "
        "must be relative paths inside the project (got "
    )


def test_pythonpath_of_relative_directories_is_allowed(check_python, good_repo):
    (good_repo / "lib").mkdir()
    edit(good_repo, ("tool", "pytest", "ini_options", "pythonpath"), ["src", "lib"])
    assert check_python.python_settings_problems(good_repo) == []
    edit(good_repo, ("tool", "pytest", "ini_options", "pythonpath"), "src lib")
    assert check_python.python_settings_problems(good_repo) == []


@pytest.mark.parametrize("pythonpath", [["/abs"], ["../outside"], ["missing"], ["src", "/abs"], 3])
def test_pythonpath_must_be_relative_directories_inside_the_project(
    check_python, good_repo, pythonpath
):
    edit(good_repo, ("tool", "pytest", "ini_options", "pythonpath"), pythonpath)
    assert check_python.python_settings_problems(good_repo) == [
        "pyproject.toml [tool.pytest.ini_options] pythonpath: "
        "must be relative directories inside the project"
    ]


@pytest.mark.parametrize("entry", [".coven", "./.coven", ".coven/sub", "src/../.coven"])
def test_pythonpath_cannot_reach_the_standards_directory(check_python, good_repo, entry):
    (good_repo / ".coven/sub").mkdir(parents=True)
    edit(good_repo, ("tool", "pytest", "ini_options", "pythonpath"), ["src", entry])
    assert check_python.python_settings_problems(good_repo) == [
        "pyproject.toml [tool.pytest.ini_options] pythonpath: "
        ".coven/ is not allowed (code there is not checked)"
    ]


@pytest.mark.parametrize(
    "mypy_path", [".coven", ["src", ".coven/sub"], "src:./.coven", "src,.coven"]
)
def test_mypy_path_cannot_reach_the_standards_directory(check_python, good_repo, mypy_path):
    edit(good_repo, ("tool", "mypy", "mypy_path"), mypy_path)
    assert check_python.python_settings_problems(good_repo) == [
        "pyproject.toml [tool.mypy] mypy_path: .coven/ is not allowed (code there is not checked)"
    ]


def test_mypy_path_inside_the_project_is_fine(check_python, good_repo):
    edit(good_repo, ("tool", "mypy", "mypy_path"), ["src", "stubs"])
    assert check_python.python_settings_problems(good_repo) == []


def test_files_the_standard_did_not_install_are_a_problem(check_python, good_repo):
    (good_repo / ".coven/sub").mkdir(parents=True)
    (good_repo / ".coven/helpers.py").write_text("")
    (good_repo / ".coven/sub/more.txt").write_text("")
    assert check_python.python_settings_problems(good_repo) == [
        f".coven/helpers.py: not allowed ({INSTALLS_ONLY})",
        f".coven/sub/more.txt: not allowed ({INSTALLS_ONLY})",
    ]


def test_files_the_standard_installed_and_tool_caches_are_fine(check_python, good_repo):
    (good_repo / ".coven/.ruff_cache").mkdir(parents=True)
    (good_repo / ".coven/.ruff_cache/CACHEDIR.TAG").write_text("")
    (good_repo / ".coven/check.py").write_text("")
    (good_repo / ".coven/check_ts.py").write_text("")
    (good_repo / ".coven/ruff.toml").write_text("")
    assert check_python.python_settings_problems(good_repo) == []


def test_any_file_in_the_standards_bytecode_cache_is_a_problem(check_python, good_repo):
    # Python would load such bytecode in place of the standard's own source.
    (good_repo / ".coven/__pycache__").mkdir(parents=True)
    (good_repo / ".coven/__pycache__/check_ts.cpython-312.pyc").write_text("")
    (good_repo / ".coven/__pycache__/notes.txt").write_text("")
    assert check_python.python_settings_problems(good_repo) == [
        f".coven/__pycache__/check_ts.cpython-312.pyc: not allowed ({INSTALLS_ONLY})",
        f".coven/__pycache__/notes.txt: not allowed ({INSTALLS_ONLY})",
    ]


def test_symlinks_in_the_standards_directory_are_a_problem(check_python, good_repo):
    (good_repo / "tools").mkdir()
    (good_repo / "tools/stub.txt").write_text("")
    (good_repo / ".coven/.ruff_cache").mkdir(parents=True)
    (good_repo / ".coven/check.py").write_text("")
    (good_repo / ".coven/check_ts.py").symlink_to("../tools/stub.txt")
    (good_repo / ".coven/.ruff_cache/link").symlink_to("../../tools")
    assert check_python.python_settings_problems(good_repo) == [
        ".coven/.ruff_cache/link: not allowed (a symlink; the standard installs plain files)",
        ".coven/check_ts.py: not allowed (a symlink; the standard installs plain files)",
    ]


def test_a_symlinked_standards_directory_is_a_problem(check_python, good_repo):
    (good_repo / "tools").mkdir()
    (good_repo / "tools/ruff.txt").write_text("")
    (good_repo / ".coven").symlink_to("tools")
    assert check_python.python_settings_problems(good_repo) == [
        ".coven: not allowed (a symlink; the standard installs plain files)"
    ]


def test_a_py_with_a_pyi_stub_is_not_checked_by_mypy(check_python, good_repo):
    (good_repo / "src/core.py").write_text("def f(x):\n    return x\n")
    (good_repo / "src/core.pyi").write_text("def f(x: int) -> int: ...\n")
    (good_repo / "src/lone.pyi").write_text("x: int\n")
    assert check_python.python_settings_problems(good_repo) == [
        "pyproject.toml [tool.mypy] files: src/core.py is shadowed by a .pyi stub "
        "(mypy checks only the stub)"
    ]


def test_mypy_skips_files_whose_own_name_starts_with_a_dot(check_python, good_repo):
    (good_repo / "src/demo").mkdir()
    (good_repo / "src/demo/.hidden.py").write_text("x: int = 'a'\n")
    assert check_python.python_settings_problems(good_repo) == [
        "pyproject.toml [tool.mypy] files: must hold every non-test Python file "
        "(src/demo/.hidden.py outside them)"
    ]


@pytest.mark.parametrize("pattern", ["tests/**x", "tests/***/a", "**x/tests"])
def test_malformed_testpaths_glob_is_a_problem(check_python, good_repo, pattern):
    (good_repo / "tests").mkdir()
    edit(good_repo, ("tool", "pytest", "ini_options", "testpaths"), [pattern])
    assert check_python.python_settings_problems(good_repo) == [
        f'pyproject.toml [tool.pytest.ini_options] testpaths: invalid pattern "{pattern}"'
    ]


@pytest.mark.parametrize(
    "mypy_path",
    [
        "$MYPY_CONFIG_FILE_DIR/stubs",
        "~/stubs",
        "/abs/stubs",
        ["src", "/abs"],
        "src:$HOME/x",
        "../x",
    ],
)
def test_mypy_path_must_be_plain_relative_paths_inside_the_project(
    check_python, good_repo, mypy_path
):
    edit(good_repo, ("tool", "mypy", "mypy_path"), mypy_path)
    assert check_python.python_settings_problems(good_repo) == [
        "pyproject.toml [tool.mypy] mypy_path: must be relative paths inside the project "
        "(no $ or ~)"
    ]


@pytest.mark.parametrize(
    "name", [".ruff_cache/x.py", "__pycache__/evil.pyi", ".mypy_cache/3.12/n.ipynb"]
)
def test_python_files_in_cache_folders_of_the_standards_directory_are_a_problem(
    check_python, good_repo, name
):
    path = good_repo / ".coven" / name
    path.parent.mkdir(parents=True)
    path.write_text("")
    assert check_python.python_settings_problems(good_repo) == [
        f".coven/{name}: not allowed ({INSTALLS_ONLY})"
    ]


FLAG_PROBLEM = "pyproject.toml [tool.coverage.run] {key}: must be true or false"
NAME_PROBLEM = (
    "pyproject.toml [tool.coverage.run] {key}: must be a name or a list of names (a-z and _ only)"
)
INJECTION = "false\n[coverage:report]\nexclude_lines = if a > 1000"


@pytest.mark.parametrize("key", ["parallel", "sigterm"])
@pytest.mark.parametrize("value", [INJECTION, "true", 1, ["true"], INJECTION.replace("\n", "\r")])
def test_coverage_flags_must_be_booleans(check_python, good_repo, key, value):
    edit(good_repo, ("tool", "coverage", "run", key), value)
    assert check_python.python_settings_problems(good_repo) == [FLAG_PROBLEM.format(key=key)]


@pytest.mark.parametrize("key", ["concurrency", "patch"])
@pytest.mark.parametrize(
    "value",
    ["thread\n[coverage:report]", "thread\r", "thread\n", ["thread", "a\nb"], ["Thread"], 3, [3]],
)
def test_coverage_names_must_be_plain_words(check_python, good_repo, key, value):
    edit(good_repo, ("tool", "coverage", "run", key), value)
    assert check_python.python_settings_problems(good_repo) == [NAME_PROBLEM.format(key=key)]


def test_coverage_names_accept_a_word_or_words(check_python, good_repo):
    edit(good_repo, ("tool", "coverage", "run", "concurrency"), "thread")
    edit(good_repo, ("tool", "coverage", "run", "patch"), ["subprocess", "_exit"])
    assert check_python.python_settings_problems(good_repo) == []


# --- suppression scanning ------------------------------------------------------


def test_scan_finds_comment_suppressions(check_python, tmp_path):
    f = tmp_path / "m.py"
    f.write_text("x = 1  # noqa: E501\ny = f()  # type: ignore[attr-defined]\n# pragma: no cover\n")
    assert [(s.line, s.text) for s in check_python.scan_python(f)] == [
        (1, "# noqa: E501"),
        (2, "# type: ignore[attr-defined]"),
        (3, "# pragma: no cover"),
    ]


def test_scan_ignores_strings(check_python, tmp_path):
    f = tmp_path / "t.py"
    f.write_text('S = "x = 1  # noqa"\nT = """# type: ignore"""\n')
    assert check_python.scan_python(f) == []


def test_scan_finds_branch_and_isort_suppressions(check_python, tmp_path):
    f = tmp_path / "m.py"
    f.write_text(
        "if x:  # pragma: no branch\n"
        "    pass\n"
        "import b  # isort: skip\n"
        "# isort: skip_file\n"
        "# isort: off\n"
        "# ruff: isort: skip_file\n"
    )
    assert [(s.line, s.text) for s in check_python.scan_python(f)] == [
        (1, "# pragma: no branch"),
        (3, "# isort: skip"),
        (4, "# isort: skip_file"),
        (5, "# isort: off"),
        (6, "# ruff: isort: skip_file"),
    ]


def test_scan_finds_no_type_check_as_decorator_and_import(check_python, tmp_path):
    f = tmp_path / "m.py"
    f.write_text(
        "import typing\n"
        "from typing import no_type_check\n"
        "\n"
        "@no_type_check\n"
        "def f(x): return x\n"
        "@typing.no_type_check\n"
        "def g(x): return x\n"
        'NAME = "no_type_check"\n'
    )
    assert [(s.line, s.text) for s in check_python.scan_python(f)] == [
        (2, "from typing import no_type_check"),
        (4, "@no_type_check"),
        (6, "@typing.no_type_check"),
    ]


def notebook(path, *cells):
    path.write_text(json.dumps({"cells": list(cells), "nbformat": 4, "nbformat_minor": 5}))
    return path


def code_cell(source):
    return {"cell_type": "code", "source": source, "metadata": {}, "outputs": []}


def test_scan_notebook_reads_code_cells_only_and_counts_strings(check_python, tmp_path):
    f = notebook(
        tmp_path / "n.ipynb",
        {"cell_type": "markdown", "source": ["x = 1  # noqa\n"], "metadata": {}},
        code_cell(["%matplotlib inline\n", "!pip list\n", "import os  # noqa: F401\n"]),
        code_cell("y = 2  # type: ignore\nz = 'no_type_check'"),
    )
    assert [(s.cell, s.line, s.text) for s in check_python.scan_notebook(f)] == [
        (2, 3, "# noqa: F401"),
        (3, 1, "# type: ignore"),
        (3, 2, "z = 'no_type_check'"),
    ]


def test_scan_notebook_rejects_invalid_json(check_python, tmp_path):
    f = tmp_path / "n.ipynb"
    f.write_text("{not json")
    try:
        check_python.scan_notebook(f)
    except ValueError:
        return
    raise AssertionError("expected ValueError")


def test_scan_finds_colonless_pragmas_and_yapf(check_python, tmp_path):
    f = tmp_path / "m.py"
    f.write_text(
        "if x:  # pragma no branch\n"
        "    pass\n"
        "y = 1  # pragma:no cover\n"
        "z = 2  # PRAGMA NOCOVER\n"
        "w = 3  # yapf: disable\n"
        "v = 4  # pragma: not covered here\n"
    )
    assert [(s.line, s.text) for s in check_python.scan_python(f)] == [
        (1, "# pragma no branch"),
        (3, "# pragma:no cover"),
        (4, "# PRAGMA NOCOVER"),
        (5, "# yapf: disable"),
    ]


def test_scan_notebook_skips_cell_magic_cells_without_losing_other_cells(check_python, tmp_path):
    f = notebook(
        tmp_path / "n.ipynb",
        code_cell(["%%bash\n", "echo it's done\n"]),
        code_cell(["import os  # noqa: F401\n"]),
    )
    assert [(s.cell, s.line, s.text) for s in check_python.scan_notebook(f)] == [
        (2, 1, "# noqa: F401")
    ]


def test_scan_notebook_cell_magic_cell_with_python_body_is_still_counted(check_python, tmp_path):
    f = notebook(tmp_path / "n.ipynb", code_cell(["%%time\n", "x = 1  # noqa\n"]))
    assert [(s.cell, s.line) for s in check_python.scan_notebook(f)] == [(1, 2)]


def test_scan_notebook_does_not_need_cells_to_be_valid_python(check_python, tmp_path):
    f = notebook(
        tmp_path / "n.ipynb",
        code_cell(["def broken(:  # noqa\n"]),
        code_cell(["y = 2  # type: ignore\n"]),
    )
    assert [(s.cell, s.line) for s in check_python.scan_notebook(f)] == [(1, 1), (2, 1)]


def test_scan_python_untokenizable_file_is_reported_and_counted_by_line(check_python, tmp_path):
    f = tmp_path / "m.py"
    f.write_text("x = 1  # noqa\ndef broken(:  # type: ignore\n")
    with pytest.raises(check_python.UnscannableError) as raised:
        check_python.scan_python(f)
    assert [s.line for s in raised.value.found] == [1, 2]


def test_scan_notebook_invalid_json_is_reported_and_counted_by_line(check_python, tmp_path):
    f = tmp_path / "n.ipynb"
    f.write_text('{not json  # noqa"\n')
    with pytest.raises(check_python.UnscannableError) as raised:
        check_python.scan_notebook(f)
    assert [s.line for s in raised.value.found] == [1]
    assert str(raised.value) == "could not be read as a notebook"


@pytest.mark.parametrize(
    "directive",
    [
        "# ruff: disable[F401]",
        "# ruff: enable[F401]",
        "# ruff: ignore[F401]",
        "# ruff: noqa",
        "x = 1  # ruff: ignore[F401]",
        "# ruff : disable[F401]",
        "# ruff\t: ignore[F401]",
    ],
)
def test_scan_finds_every_ruff_directive(check_python, tmp_path, directive):
    f = tmp_path / "m.py"
    f.write_text(f"import os\n{directive}\n")
    assert [s.line for s in check_python.scan_python(f)] == [2]


def test_scan_counts_pragma_comments_only_not_strings(check_python, tmp_path):
    f = tmp_path / "m.py"
    f.write_text(
        'MARK = "# pragma: no cover"\n'
        "if x:  # pragma: no cover\n"
        "    pass\n"
        'DOC = """\n# pragma: no branch\n"""\n'
    )
    assert [(s.line, s.text) for s in check_python.scan_python(f)] == [(2, "# pragma: no cover")]


@pytest.mark.parametrize(
    "lead",
    ["", "x = %who ", "x = !echo ", "x.y = %who ", "%", "!", "?", ",", ";", "/"],
)
def test_scan_notebook_cannot_be_hidden_by_a_line_the_tokenizer_reads_differently(
    check_python, tmp_path, lead
):
    # IPython (and ruff) read these lines as magics or calls, the tokenizer as the start of a
    # string: so notebooks are scanned by line, never tokenized.
    on = notebook(
        tmp_path / "on.ipynb",
        code_cell([f'{lead}print """\n', "y = undefined_name  # noqa: F821\n", '# end """\n']),
    )
    off = notebook(
        tmp_path / "off.ipynb",
        code_cell([f'{lead}print """\n', "y = undefined_name\n", '# end """\n']),
    )
    assert [(s.cell, s.line) for s in check_python.scan_notebook(on)] == [(1, 2)]
    assert check_python.scan_notebook(off) == []


def test_scan_notebook_sees_a_suppression_on_a_continuation_line_that_starts_with_a_percent(
    check_python, tmp_path
):
    f = notebook(
        tmp_path / "n.ipynb",
        code_cell(["y = (\n", "    1\n", "    % undefined  # noqa: F821\n", ")\n"]),
        code_cell(["z = (\n", "    1\n", "    != 2  # noqa: F821\n", ")\n"]),
        code_cell(["!pip install x  # noqa\n"]),
    )
    assert [(s.cell, s.line) for s in check_python.scan_notebook(f)] == [(1, 3), (2, 3), (3, 1)]


def test_scan_notebook_counts_suppression_text_in_strings_but_not_plain_questions(
    check_python, tmp_path
):
    f = notebook(
        tmp_path / "n.ipynb",
        code_cell(['MSG = "x  # noqa"\n']),
        code_cell(["# is this fine?\n", "x = 1\n"]),
    )
    assert [(s.cell, s.line) for s in check_python.scan_notebook(f)] == [(1, 1)]


@pytest.mark.parametrize("space", ["\x0b", "\x0c", "\x85", "\u2028", "\u2029"])
def test_line_scan_keeps_the_comment_whole_across_unicode_line_separators(
    check_python, tmp_path, space
):
    # str.splitlines() breaks on these, ruff does not: a comment with one of them after the hash is still a suppression to ruff.
    line = f"import os  #{space}noqa: F401"
    nb = notebook(tmp_path / "n.ipynb", code_cell([line + "\n"]))
    assert [(s.cell, s.line) for s in check_python.scan_notebook(nb)] == [(1, 1)]
    py = tmp_path / "m.py"
    py.write_text(f"{line}\ndef broken(:\n", encoding="utf-8")
    with pytest.raises(check_python.UnscannableError) as raised:
        check_python.scan_python(py)
    assert [s.line for s in raised.value.found] == [1]


def test_line_scan_counts_lines_by_cr_lf_and_crlf_only(check_python, tmp_path):
    py = tmp_path / "m.py"
    py.write_bytes(b"a = 1\rb = 2  # noqa\r\ndef broken(:\nc = 3  # type: ignore\x0c\n")
    with pytest.raises(check_python.UnscannableError) as raised:
        check_python.scan_python(py)
    assert [s.line for s in raised.value.found] == [2, 4]
