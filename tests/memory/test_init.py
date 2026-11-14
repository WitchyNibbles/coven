import os
import stat
import subprocess
from pathlib import Path

import pytest
from memory_support import Lab, neutral_hook

from coven import cli
from coven.errors import CovenError
from coven.memory import store


def version_of(hook: Path) -> int | None:
    return store.hook_version(hook.read_text(encoding="utf-8"))


def test_init_layout_and_hook(tmp_path: Path, lab: Lab) -> None:
    root = tmp_path / "mem"
    store.init_store(root, None)
    for folder in ("user", "projects", "handoffs"):
        assert (root / folder).is_dir()
    assert "INDEX.md merge=union" in (root / ".gitattributes").read_text().splitlines()
    assert lab.git(root, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"
    assert lab.git(root, "rev-list", "--count", "HEAD").strip() == "1"
    assert lab.git(root, "status", "--porcelain") == ""
    hook = root / ".git" / "hooks" / "pre-commit"
    assert hook.read_bytes() == store.HOOK_SOURCE.read_bytes()
    assert hook.stat().st_mode & stat.S_IXUSR
    assert isinstance(version_of(hook), int)
    assert f"# coven-hook-version: {version_of(hook)}" in hook.read_text().splitlines()


def test_init_sets_origin(tmp_path: Path, lab: Lab) -> None:
    store.init_store(tmp_path / "mem", "https://example.com/me/mem.git")
    assert lab.git(tmp_path / "mem", "remote", "get-url", "origin").strip() == (
        "https://example.com/me/mem.git"
    )


def test_init_refuses_foreign_nonempty_dir(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    root.mkdir()
    (root / "notes.txt").write_text("mine\n")
    with pytest.raises(CovenError, match="not empty"):
        store.init_store(root, None)
    assert sorted(p.name for p in root.iterdir()) == ["notes.txt"]


def test_init_accepts_an_empty_existing_dir(tmp_path: Path) -> None:
    root = tmp_path / "mem"
    root.mkdir()
    store.init_store(root, None)
    assert (root / ".git").is_dir()


def test_init_again_updates_the_hook_and_keeps_history(tmp_path: Path, lab: Lab) -> None:
    root = tmp_path / "mem"
    store.init_store(root, None)
    hook = root / ".git" / "hooks" / "pre-commit"
    hook.write_text(hook.read_text().replace(f"version: {version_of(hook)}", "version: 0"))
    store.init_store(root, "https://example.com/me/mem.git")
    assert hook.read_bytes() == store.HOOK_SOURCE.read_bytes()
    assert lab.git(root, "rev-list", "--count", "HEAD").strip() == "1"
    assert "mem.git" in lab.git(root, "remote", "get-url", "origin")


def test_init_again_moves_origin_to_a_new_remote(tmp_path: Path, lab: Lab) -> None:
    store.init_store(tmp_path / "mem", "https://example.com/old.git")
    store.init_store(tmp_path / "mem", "https://example.com/new.git")
    assert (
        lab.git(tmp_path / "mem", "remote", "get-url", "origin").strip()
        == "https://example.com/new.git"
    )


def test_init_never_replaces_a_foreign_hook(tmp_path: Path) -> None:
    root = tmp_path / "mem"
    store.init_store(root, None)
    hook = root / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 0\n")
    with pytest.raises(CovenError, match="not coven's"):
        store.init_store(root, None)
    assert hook.read_text() == "#!/bin/sh\nexit 0\n"


def test_init_finishes_a_store_that_has_no_commit_yet(tmp_path: Path, lab: Lab) -> None:
    root = tmp_path / "mem"
    store.init_store(root, None)
    neutral_hook(root)
    lab.git(root, "update-ref", "-d", "refs/heads/main")
    store.init_store(root, None)
    assert lab.git(root, "rev-list", "--count", "HEAD").strip() == "1"


def test_init_reports_a_failed_first_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "no-identity"
    config.write_text("[user]\n\tuseConfigOnly = true\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    for name in ("AUTHOR_NAME", "AUTHOR_EMAIL", "COMMITTER_NAME", "COMMITTER_EMAIL"):
        monkeypatch.delenv(f"GIT_{name}")  # the autouse identity; this test has none
    with pytest.raises(CovenError, match="git commit failed"):
        store.init_store(tmp_path / "mem", None)


def test_path_with_spaces_and_tilde(capsys: pytest.CaptureFixture[str], lab: Lab) -> None:
    assert cli.main(["memory", "init", "--path", "~/memory store"]) == 0
    root = Path(os.environ["HOME"]) / "memory store"
    assert (root / ".git").is_dir()
    assert str(root) in capsys.readouterr().out
    neutral_hook(root)
    lab.write(root, "user/a.md", "A", "first fact")
    lab.commit(root)
    assert "- [A](a.md) — first fact" in lab.git(root, "show", "HEAD:user/INDEX.md")


def test_init_without_a_path_uses_the_configured_memory_path() -> None:
    assert cli.main(["memory", "init"]) == 0
    assert (Path(os.environ["HOME"]) / "coven-memory" / ".git").is_dir()


def test_init_with_remote_flag(tmp_path: Path, lab: Lab, bare_remote: Path) -> None:
    root = tmp_path / "mem"
    assert cli.main(["memory", "init", "--path", str(root), "--remote", str(bare_remote)]) == 0
    assert lab.git(root, "remote", "get-url", "origin").strip() == str(bare_remote)


def test_init_with_a_remote_gives_the_remote_privacy_warning(
    tmp_path: Path,
    bare_remote: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    asked: list[str] = []

    def public(remote: str) -> str:
        asked.append(remote)
        return f"{remote} is PUBLIC, not PRIVATE"

    monkeypatch.setattr(store, "remote_warning", public)

    code = cli.main(["memory", "init", "--path", str(tmp_path / "mem"), "--remote", "owner/repo"])

    assert code == 0
    assert asked == ["owner/repo"]
    assert "coven: warning: owner/repo is PUBLIC, not PRIVATE" in capsys.readouterr().err


def test_init_without_a_remote_asks_nothing_about_privacy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def never(remote: str) -> str:
        raise AssertionError(remote)

    monkeypatch.setattr(store, "remote_warning", never)

    assert cli.main(["memory", "init", "--path", str(tmp_path / "mem")]) == 0
    assert "warning" not in capsys.readouterr().err


def test_init_treats_a_folder_with_only_an_empty_git_repo_as_empty(
    tmp_path: Path, lab: Lab
) -> None:
    root = tmp_path / "half"
    root.mkdir()
    lab.git(root, "init", "-q")
    store.init_store(root, None)
    assert store.is_store(root)
    assert lab.git(root, "rev-list", "--count", "HEAD").strip() == "1"
    store.init_store(root, None)  # and again, now that it is a store


def test_init_refuses_a_git_folder_with_history(tmp_path: Path, lab: Lab) -> None:
    root = tmp_path / "history"
    root.mkdir()
    lab.git(root, "init", "-q")
    lab.git(root, "commit", "-q", "--allow-empty", "-m", "someone's repo")
    with pytest.raises(CovenError, match="not empty"):
        store.init_store(root, None)
    assert lab.git(root, "rev-list", "--count", "HEAD").strip() == "1"


def test_check_path_refuses_a_no_checkout_clone_and_accepts_an_unborn_repo(
    tmp_path: Path, lab: Lab
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    lab.git(source, "init", "-q")
    lab.git(source, "commit", "-q", "--allow-empty", "-m", "history")
    clone = tmp_path / "clone"
    lab.git(tmp_path, "clone", "-q", "--no-checkout", str(source), str(clone))
    with pytest.raises(CovenError, match="not empty"):
        store.check_path(clone)
    unborn = tmp_path / "unborn"
    unborn.mkdir()
    lab.git(unborn, "init", "-q")
    store.check_path(unborn)


def test_init_makes_git_run_the_hook_despite_a_global_hooks_path(
    tmp_path: Path, lab: Lab, monkeypatch: pytest.MonkeyPatch
) -> None:
    elsewhere = tmp_path / "global-hooks"
    elsewhere.mkdir()
    config = Path(os.environ["GIT_CONFIG_GLOBAL"])
    config.write_text(config.read_text() + f"[core]\n\thooksPath = {elsewhere}\n")
    root = lab.store("mem")
    lab.write(root, "user/a.md", "A", "d", body="key " + "ghp_" + "aB3d" * 9 + "\n")
    with pytest.raises(subprocess.CalledProcessError) as failed:
        lab.commit(root)
    assert "GitHub token" in failed.value.stderr
    hooks = lab.git(root, "rev-parse", "--git-path", "hooks/pre-commit").strip()
    assert (root / hooks).resolve() == (root / ".git" / "hooks" / "pre-commit").resolve()


def test_init_fails_when_git_would_not_run_the_installed_hook(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A command-line style setting outranks the repo's own config, so init cannot fix it.
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.hooksPath")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", str(tmp_path / "forced"))
    with pytest.raises(CovenError, match="would not run"):
        store.init_store(tmp_path / "mem", None)


def test_a_moved_store_still_runs_its_hook(tmp_path: Path, lab: Lab) -> None:
    root = lab.store("mem")
    moved = tmp_path / "elsewhere"
    root.rename(moved)
    lab.write(moved, "user/a.md", "A", "d", body="key " + "ghp_" + "aB3d" * 9 + "\n")
    with pytest.raises(subprocess.CalledProcessError) as failed:
        lab.commit(moved)
    assert "GitHub token" in failed.value.stderr
