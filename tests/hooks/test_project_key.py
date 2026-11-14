import importlib.util
import os
import shutil
import subprocess
from pathlib import Path
from types import ModuleType

import pytest

PROJECT_KEY = Path(__file__).resolve().parents[2] / "hooks" / "project_key.py"


@pytest.fixture(scope="module")
def key_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("coven_test_project_key", PROJECT_KEY)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=T", "-c", "user.email=t@example.com", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


def test_key_for_worktree_is_main_checkout(key_module: ModuleType, tmp_path: Path) -> None:
    main = tmp_path / "My App"
    main.mkdir()
    git(main, "init", "-q")
    git(main, "commit", "-q", "--allow-empty", "-m", "init")
    tree = tmp_path / "elsewhere" / "feature-tree"
    git(main, "worktree", "add", "-q", str(tree), "-b", "feature")
    assert key_module.project_key(tree) == "my-app"
    assert key_module.project_key(main) == "my-app"


def test_key_from_a_subfolder_of_the_repo(key_module: ModuleType, tmp_path: Path) -> None:
    repo = tmp_path / "app"
    (repo / "src" / "deep").mkdir(parents=True)
    git(repo, "init", "-q")
    assert key_module.project_key(repo / "src" / "deep") == "app"


def test_key_without_git_is_folder_name(key_module: ModuleType, tmp_path: Path) -> None:
    folder = tmp_path / "Some Folder"
    folder.mkdir()
    assert key_module.project_key(folder) == "some-folder"


def test_key_in_repo_with_no_commits(key_module: ModuleType, tmp_path: Path) -> None:
    repo = tmp_path / "Fresh Repo"
    repo.mkdir()
    git(repo, "init", "-q")
    assert key_module.project_key(repo) == "fresh-repo"


def test_key_for_a_missing_folder_is_still_its_name(key_module: ModuleType, tmp_path: Path) -> None:
    assert key_module.project_key(tmp_path / "Gone") == "gone"


@pytest.fixture
def old_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A `git` on PATH that refuses `--path-format` the way git before 2.31 does."""
    real = shutil.which("git")
    assert real is not None
    folder = tmp_path / "old-git-bin"
    folder.mkdir()
    fake = folder / "git"
    fake.write_text(
        "#!/bin/sh\n"
        'for arg in "$@"; do\n'
        '  case "$arg" in --path-format*) echo "unknown option" >&2; exit 129;; esac\n'
        "done\n"
        f'exec {real} "$@"\n'
    )
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{folder}{os.pathsep}{os.environ['PATH']}")


def test_key_with_a_git_older_than_2_31(
    key_module: ModuleType, tmp_path: Path, old_git: None
) -> None:
    main = tmp_path / "Old App"
    (main / "src").mkdir(parents=True)
    git(main, "init", "-q")
    git(main, "commit", "-q", "--allow-empty", "-m", "init")
    tree = tmp_path / "elsewhere" / "feature-tree"
    git(main, "worktree", "add", "-q", str(tree), "-b", "feature")
    assert key_module.project_key(tree) == "old-app"
    assert key_module.project_key(main) == "old-app"
    assert key_module.project_key(main / "src") == "old-app"
