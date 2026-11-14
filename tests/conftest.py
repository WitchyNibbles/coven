import copy
import hashlib
import importlib.util
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest
from support import (
    CHECK,
    CORE,
    FIXTURES,
    GOOD,
    REPO,
    TEST_CORE,
    commit_all,
    dump_toml,
    git,
    install_once,
    npm_ci,
)


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No test reads or writes the real home, config, cache or Claude directory."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(home / ".cache"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home / ".claude"))
    monkeypatch.delenv("COVEN_SESSION_ROLE", raising=False)


# What git exports to a hook it runs (a pre-commit run starts pytest with some of these set): each
# one would point a test's git at the repo that started the run.
GIT_HOOK_VARIABLES = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_PREFIX",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_COMMON_DIR",
)


@pytest.fixture(autouse=True)
def git_isolation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Git reads one test-owned config and identity, never the user's, and never the outer repo."""
    config = tmp_path / "gitconfig"
    config.write_text(
        "[user]\n\tname = Test\n\temail = test@example.com\n[init]\n\tdefaultBranch = main\n"
    )
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for role in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{role}_NAME", "Test")
        monkeypatch.setenv(f"GIT_{role}_EMAIL", "test@example.com")
    for name in GIT_HOOK_VARIABLES:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def few_nested_workers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nested `check.py full` runs start 2 pytest-xdist workers, not one per CPU each."""
    monkeypatch.setenv("PYTEST_XDIST_AUTO_NUM_WORKERS", "2")


@pytest.fixture(scope="session")
def check() -> ModuleType:
    """The standard's check script, loaded from the repo without installing it."""
    spec = importlib.util.spec_from_file_location("coven_check", CHECK)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def check_python(check: ModuleType) -> ModuleType:
    """The Python rules and stages module, as the session's `check` loaded it."""
    return check.check_python


@pytest.fixture(scope="session")
def check_repo(check: ModuleType) -> ModuleType:
    """The repo plumbing module, as the session's `check` loaded it."""
    return check.check_repo


@pytest.fixture(scope="session")
def check_stages(check: ModuleType) -> ModuleType:
    """The stage types and tool runner module, as the session's `check` loaded it."""
    return check.check_stages


@pytest.fixture
def check_env() -> dict[str, str | None]:
    """The environment overrides `run_check` applies; a module overrides this fixture to change them.

    The default runs the checks in-process, so a test needs no installed tools or package.
    A test of the real runner returns `REAL_RUNNER` instead.
    """
    return {
        "COVEN_CHECK_RUNNER": f"{sys.executable} -m",
        "PYTHONPATH": "src",  # the fixture project's package is not installed
    }


@pytest.fixture
def run_check(check_env: dict[str, str | None]) -> Callable[..., subprocess.CompletedProcess[str]]:
    """Runs `check.py` (or the copy `check=` names) in `cwd`; `env` overrides, None removes.

    The outer pytest-xdist worker is hidden from the child, so a nested pytest starts its own run.
    """

    def run(
        cwd: Path,
        *args: str,
        check: Path = CHECK,
        env: dict[str, str | None] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        outer = {
            key: value
            for key, value in os.environ.items()
            # Under pytest-xdist these name the outer worker; a nested pytest must not see them.
            # The worker count is configuration (`few_nested_workers` sets it), so it is kept.
            if not key.startswith("PYTEST_XDIST") or key == "PYTEST_XDIST_AUTO_NUM_WORKERS"
        }
        environment: dict[str, str | None] = {**outer, **check_env, **(env or {})}
        clean = {key: value for key, value in environment.items() if value is not None}
        return subprocess.run(
            [sys.executable, str(check), *args],
            cwd=cwd,
            env=clean,
            capture_output=True,
            text=True,
            check=False,
        )

    return run


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """A temp git repo with one empty commit; its identity is set locally, never globally."""
    root = tmp_path / "project"
    root.mkdir()
    for args in (
        ["init", "-q"],
        ["config", "user.name", "Test"],
        ["config", "user.email", "test@example.com"],
        ["commit", "-q", "--allow-empty", "-m", "init"],
    ):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
    return root


@pytest.fixture
def py_project(git_repo: Path) -> Path:
    """A clean, committed project on branch main that passes every stage."""
    (git_repo / "pyproject.toml").write_text(dump_toml(copy.deepcopy(GOOD)))
    (git_repo / ".gitignore").write_text("__pycache__/\n")
    (git_repo / "uv.lock").write_text("")  # the runner override never reads it
    (git_repo / ".coven").mkdir()
    shutil.copy(REPO / "standard/python/ruff.toml", git_repo / ".coven/ruff.toml")
    (git_repo / "src/pkg").mkdir(parents=True)
    (git_repo / "src/pkg/__init__.py").write_text("")
    (git_repo / "src/pkg/core.py").write_text(CORE)
    (git_repo / "tests").mkdir()
    (git_repo / "tests/test_core.py").write_text(TEST_CORE)
    git(git_repo, "branch", "-M", "main")
    commit_all(git_repo)
    return git_repo


@pytest.fixture(scope="session")
def install_fixture(pytestconfig, tmp_path_factory):
    """Installs a fixture's packages once from its lockfile, cached by the lockfile's hash.

    `install_fixture("ts-project")` returns the installed `node_modules`; it fails the test when
    node or npm is missing.
    """

    def install(name: str) -> Path:
        missing = [tool for tool in ("node", "npm") if shutil.which(tool) is None]
        if missing:  # a test marked `node` fails loudly: a skip would hide a broken setup
            pytest.fail(f"{' and '.join(missing)} must be on PATH to run the TypeScript tools")
        fixture = FIXTURES / name
        digest = hashlib.sha256()
        for file in ("package.json", "package-lock.json"):
            digest.update((fixture / file).read_bytes())
        key = f"coven-{name}-{digest.hexdigest()[:16]}"
        cache = getattr(pytestconfig, "cache", None)
        home = cache.mkdir(key) if cache is not None else tmp_path_factory.mktemp(key)
        install_once(home, lambda target: npm_ci(fixture, target))
        return home / "node_modules"

    return install
