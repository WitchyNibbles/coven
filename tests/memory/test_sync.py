import os
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest
from memory_support import Lab

from coven import cli
from coven.errors import CovenError
from coven.memory import store


def heads(lab: Lab, remote: Path) -> str:
    return lab.git(remote, "for-each-ref", "refs/heads")


def test_sync_pushes_the_first_branch_and_sets_the_upstream(lab: Lab, bare_remote: Path) -> None:
    root = lab.store("a", bare_remote)
    lab.write(root, "user/a.md", "A", "first fact")
    lab.commit(root)
    report = store.sync_store(root)
    assert "pushed 2 commit(s)" in report
    assert lab.git(root, "rev-parse", "--abbrev-ref", "@{upstream}").strip() == "origin/main"
    assert lab.git(bare_remote, "rev-parse", "main") == lab.git(root, "rev-parse", "HEAD")
    assert store.sync_store(root) == ["already up to date"]


def test_sync_pulls_what_another_clone_pushed(lab: Lab, bare_remote: Path) -> None:
    first = lab.store("a", bare_remote)
    store.sync_store(first)
    second = lab.clone("b")
    lab.write(second, "user/b.md", "B", "from the other clone")
    lab.commit(second)
    store.sync_store(second)
    assert "pulled 1 commit(s)" in store.sync_store(first)
    assert (first / "user/b.md").exists()


def test_sync_pushes_after_rebasing_local_commits(lab: Lab, bare_remote: Path) -> None:
    first = lab.store("a", bare_remote)
    store.sync_store(first)
    second = lab.clone("b")
    lab.write(second, "user/b.md", "B", "other clone")
    lab.commit(second)
    store.sync_store(second)
    lab.write(first, "user/a.md", "A", "mine")
    lab.commit(first)
    report = store.sync_store(first)
    assert report[0] == "pulled 1 commit(s)"
    assert report[-1].startswith("pushed ")
    assert lab.git(bare_remote, "rev-parse", "main") == lab.git(first, "rev-parse", "HEAD")
    index = (first / "user/INDEX.md").read_text()
    assert "](a.md)" in index
    assert "](b.md)" in index


def test_sync_without_a_remote_only_rebuilds_indexes(lab: Lab) -> None:
    root = lab.store("a")
    lab.write(root, "user/a.md", "A", "fact")
    lab.commit(root)
    assert store.sync_store(root) == ["no remote: nothing to fetch or push"]


def test_sync_refuses_uncommitted_changes(lab: Lab, bare_remote: Path) -> None:
    root = lab.store("a", bare_remote)
    (root / "user" / "draft.md").write_text("draft\n")
    with pytest.raises(CovenError, match="uncommitted"):
        store.sync_store(root)


def test_sync_refuses_a_folder_that_is_not_a_store(tmp_path: Path) -> None:
    with pytest.raises(CovenError, match="coven memory init"):
        store.sync_store(tmp_path)


def test_sync_conflict_aborts_and_lists_files(lab: Lab, bare_remote: Path) -> None:
    first = lab.store("a", bare_remote)
    lab.write(first, "user/a.md", "A", "fact", body="line one\n")
    lab.commit(first)
    store.sync_store(first)
    second = lab.clone("b")
    lab.write(second, "user/a.md", "A", "fact", body="from b\n")
    lab.commit(second)
    store.sync_store(second)
    lab.write(first, "user/a.md", "A", "fact", body="from a\n")
    lab.commit(first)
    mine = lab.git(first, "rev-parse", "HEAD")
    with pytest.raises(CovenError) as failed:
        store.sync_store(first)
    message = str(failed.value)
    assert "user/a.md" in message
    assert f"git -C {first} rebase origin/main" in message
    assert lab.git(first, "rev-parse", "HEAD") == mine
    assert not (first / ".git" / "rebase-merge").exists()
    assert not (first / ".git" / "rebase-apply").exists()
    assert lab.git(first, "status", "--porcelain") == ""
    assert (first / "user/a.md").read_text().endswith("from a\n")


def behind_by_one(lab: Lab, bare_remote: Path) -> Path:
    """A store whose remote holds one commit it lacks, so its next sync must rebase."""
    first = lab.store("a", bare_remote)
    store.sync_store(first)
    second = lab.clone("b")
    lab.write(second, "user/b.md", "B", "fact")
    lab.commit(second)
    store.sync_store(second)
    return first


def test_sync_retries_index_lock(
    lab: Lab, bare_remote: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = behind_by_one(lab, bare_remote)
    lock = first / ".git" / "index.lock"
    lock.write_text("")
    pauses: list[None] = []

    def release() -> None:
        pauses.append(None)
        lock.unlink()

    monkeypatch.setattr(store, "pause", release)
    store.sync_store(first)
    assert len(pauses) == 1
    assert (first / "user/b.md").exists()


def test_sync_gives_up_when_the_index_lock_stays(
    lab: Lab, bare_remote: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = behind_by_one(lab, bare_remote)
    (first / ".git" / "index.lock").write_text("")
    pauses: list[None] = []
    monkeypatch.setattr(store, "pause", lambda: pauses.append(None))
    with pytest.raises(CovenError, match="index.lock"):
        store.sync_store(first)
    assert len(pauses) == 1


def drop_hook(root: Path) -> None:
    (root / ".git" / "hooks" / "pre-commit").unlink()


def old_hook(root: Path) -> None:
    hook = root / ".git" / "hooks" / "pre-commit"
    text = hook.read_text()
    hook.write_text(text.replace(f"version: {store.hook_version(text)}", "version: 0"))


def foreign_hook(root: Path) -> None:
    (root / ".git" / "hooks" / "pre-commit").write_text("#!/bin/sh\nexit 0\n")


def not_executable(root: Path) -> None:
    (root / ".git" / "hooks" / "pre-commit").chmod(0o644)


@pytest.mark.parametrize("break_hook", [drop_hook, old_hook, foreign_hook, not_executable])
def test_sync_refuses_push_without_hook(
    lab: Lab, bare_remote: Path, break_hook: Callable[[Path], None]
) -> None:
    root = lab.store("a", bare_remote)
    lab.write(root, "user/a.md", "A", "fact")
    lab.commit(root)
    break_hook(root)
    with pytest.raises(CovenError, match="pre-commit hook"):
        store.sync_store(root)
    assert heads(lab, bare_remote) == ""


def test_sync_refuses_push_when_git_runs_hooks_from_elsewhere(
    lab: Lab, bare_remote: Path, tmp_path: Path
) -> None:
    root = lab.store("a", bare_remote)
    other = tmp_path / "other-hooks"
    other.mkdir()
    lab.git(root, "config", "core.hooksPath", str(other))
    with pytest.raises(CovenError, match="pre-commit hook"):
        store.sync_store(root)
    assert heads(lab, bare_remote) == ""


def test_sync_dedupes_union_merged_index(lab: Lab, bare_remote: Path) -> None:
    first = lab.store("a", bare_remote)
    lab.write(first, "user/a.md", "Alpha", "one")
    lab.commit(first)
    store.sync_store(first)
    second = lab.clone("b")
    # The two clones edit different lines of one entry, so the entry merges cleanly, while both
    # rewrite its one INDEX line: the union driver keeps both versions of that line.
    lab.write(second, "user/a.md", "Alpha renamed", "one")
    lab.commit(second)
    store.sync_store(second)
    lab.write(first, "user/a.md", "Alpha", "two")
    lab.commit(first)
    report = store.sync_store(first)
    index = (first / "user/INDEX.md").read_text()
    assert index.count("](a.md)") == 1
    assert "- [Alpha renamed](a.md) — two" in index
    assert any("rebuilt" in line and "user/INDEX.md" in line for line in report)
    assert lab.git(first, "status", "--porcelain") == ""
    assert lab.git(bare_remote, "rev-parse", "main") == lab.git(first, "rev-parse", "HEAD")
    assert lab.git(first, "show", "HEAD:user/INDEX.md") == index


def test_sync_command_prints_the_report(
    lab: Lab, bare_remote: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = lab.store("a", bare_remote)
    config = Path(os.environ["HOME"]) / ".config" / "coven"
    config.mkdir(parents=True)
    (config / "config.json").write_text(f'{{"memory_path": "{root}"}}')
    assert cli.main(["memory", "sync"]) == 0
    assert "pushed 1 commit(s)" in capsys.readouterr().out


def test_sync_command_reports_errors_without_a_traceback(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["memory", "sync"]) == 1
    captured = capsys.readouterr()
    assert captured.err.startswith("coven: ")
    assert "coven memory init" in captured.err
    assert "Traceback" not in captured.err


def test_sync_aborts_the_rebase_when_a_git_step_fails_midway(
    lab: Lab, bare_remote: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = behind_by_one(lab, bare_remote)
    calls: list[tuple[str, ...]] = []
    original = store._run

    def run(path: Path, args: tuple[str, ...]):
        calls.append(args)
        if args[0] == "rebase" and "--quiet" in args:
            raise CovenError("git rebase took longer than 60 seconds")
        return original(path, args)

    monkeypatch.setattr(store, "_run", run)
    with pytest.raises(CovenError, match="took longer"):
        store.sync_store(first)
    assert ("rebase", "--abort") in calls


def test_sync_reports_a_rejected_push_and_never_forces(
    lab: Lab, bare_remote: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = lab.store("a", bare_remote)
    store.sync_store(root)
    other = lab.clone("b")
    lab.write(other, "user/b.md", "B", "pushed in between")
    lab.commit(other)
    lab.write(root, "user/a.md", "A", "mine")
    lab.commit(root)
    mine = lab.git(root, "rev-parse", "HEAD")
    calls: list[tuple[str, ...]] = []
    original = store._run

    def run(path: Path, args: tuple[str, ...]):
        calls.append(args)
        done = original(path, args)
        if args[0] == "fetch":  # the remote moves after the fetch, before the push
            lab.git(other, "push", "-q", "origin", "HEAD")
        return done

    monkeypatch.setattr(store, "_run", run)
    with pytest.raises(CovenError, match="git push failed"):
        store.sync_store(root)
    pushes = [args for args in calls if args[0] == "push"]
    assert len(pushes) == 1
    assert not {"-f", "--force", "--force-with-lease", "--mirror"} & set(pushes[0])
    assert lab.git(root, "rev-parse", "HEAD") == mine
    assert lab.git(bare_remote, "rev-parse", "main") == lab.git(other, "rev-parse", "HEAD")


def commit_guard(lab: Lab, root: Path) -> None:
    (root / "systems").mkdir(exist_ok=True)
    (root / "systems" / "guard.json").write_text('{"systems": {}}\n')
    lab.commit(root, "add the guard")


def test_is_guarded_follows_head_not_the_work_tree(lab: Lab) -> None:
    root = lab.store("a")
    assert not store.is_guarded(root)
    (root / "systems").mkdir()
    (root / "systems" / "guard.json").write_text("{}\n")
    assert not store.is_guarded(root)
    lab.commit(root, "add the guard")
    assert store.is_guarded(root)
    (root / "systems" / "guard.json").unlink()
    assert store.is_guarded(root)


def test_is_guarded_is_false_for_a_missing_folder_and_a_folder_inside_another_repo(
    lab: Lab, tmp_path: Path
) -> None:
    outer = lab.store("outer")
    commit_guard(lab, outer)
    (outer / "inner").mkdir()
    assert not store.is_guarded(tmp_path / "missing")
    assert not store.is_guarded(outer / "inner")


def test_is_guarded_is_false_before_the_first_commit(tmp_path: Path, lab: Lab) -> None:
    lab.git(tmp_path, "init", "-q")
    assert not store.is_guarded(tmp_path)


def test_is_guarded_is_false_when_head_has_no_guard_file(lab: Lab) -> None:
    root = lab.store("a")
    lab.write(root, "systems/orders-db.md", "orders-db", "the orders database")
    lab.commit(root, "a system without a guard")
    assert not store.is_guarded(root)


def test_is_guarded_raises_when_git_cannot_read_the_repo(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()  # a folder git does not accept as a repo
    with pytest.raises(CovenError, match="git"):
        store.is_guarded(tmp_path)


def test_is_guarded_raises_on_a_damaged_head(lab: Lab) -> None:
    root = lab.store("a")
    commit_guard(lab, root)
    (root / ".git" / "HEAD").write_text("garbage\n")
    with pytest.raises(CovenError, match="git"):
        store.is_guarded(root)


def test_sync_on_a_store_git_cannot_read_pushes_nothing(lab: Lab, bare_remote: Path) -> None:
    root = lab.store("a", bare_remote)
    commit_guard(lab, root)
    (root / ".git" / "HEAD").write_text("garbage\n")
    with pytest.raises(CovenError):
        store.sync_store(root)
    assert heads(lab, bare_remote) == ""


def point_git_at(monkeypatch: pytest.MonkeyPatch, other: Path) -> None:
    """What a caller exporting GIT_DIR and GIT_WORK_TREE does to every git command it starts."""
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(other))


def test_is_guarded_ignores_an_exported_git_dir(lab: Lab, monkeypatch: pytest.MonkeyPatch) -> None:
    root = lab.store("a")
    commit_guard(lab, root)
    point_git_at(monkeypatch, lab.store("other"))
    assert store.is_guarded(root)


def test_is_guarded_ignores_a_replace_ref_on_head(lab: Lab) -> None:
    """A replace ref swaps the commit git reads: HEAD without guard.json must not unguard a store."""
    root = lab.store("a")
    commit_guard(lab, root)
    empty = subprocess.run(
        ["git", "-C", str(root), "commit-tree", "4b825dc642cb6eb9a060e54bf8d69288fbee4904"],
        input="no guard\n",
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    head = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    subprocess.run(["git", "-C", str(root), "replace", head, empty], check=True)
    assert store.is_guarded(root)


def test_sync_on_a_guarded_store_pushes_nothing_under_an_exported_git_dir(
    lab: Lab, bare_remote: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = lab.store("a", bare_remote)
    commit_guard(lab, root)
    point_git_at(monkeypatch, lab.store("other"))
    report = store.sync_store(root)
    monkeypatch.delenv("GIT_DIR")  # the test's own git calls must see the real repos again
    monkeypatch.delenv("GIT_WORK_TREE")
    assert report[-1] == "not pushed: guarded store"
    assert heads(lab, bare_remote) == ""


def test_sync_on_a_guarded_store_commits_but_never_pushes(lab: Lab, bare_remote: Path) -> None:
    root = lab.store("a", bare_remote)
    store.sync_store(root)
    pushed = heads(lab, bare_remote)
    commit_guard(lab, root)
    drop_hook(root)  # an entry committed without the hook leaves its index missing
    lab.write(root, "systems/orders-db.md", "orders-db", "the orders database")
    lab.commit(root, "orders-db")

    report = store.sync_store(root)

    assert "not pushed: guarded store" in report
    assert not any(line.startswith("pushed") for line in report)
    assert "rebuilt systems/INDEX.md and committed" in report[0]
    assert lab.git(root, "status", "--porcelain") == ""
    assert "orders-db" in lab.git(root, "show", "HEAD:systems/INDEX.md")
    assert heads(lab, bare_remote) == pushed
    assert store.sync_store(root) == ["not pushed: guarded store"]


def test_sync_on_a_guarded_store_without_a_remote_says_the_same(lab: Lab) -> None:
    root = lab.store("a")
    commit_guard(lab, root)
    assert store.sync_store(root) == ["not pushed: guarded store"]


def test_sync_on_a_guarded_store_does_not_pull_either(lab: Lab, bare_remote: Path) -> None:
    first = lab.store("a", bare_remote)
    store.sync_store(first)
    second = lab.clone("b")
    lab.write(second, "user/b.md", "B", "from the other clone")
    lab.commit(second)
    store.sync_store(second)
    commit_guard(lab, first)

    store.sync_store(first)

    assert not (first / "user/b.md").exists()


def test_the_cli_prints_the_guarded_line(lab: Lab, capsys: pytest.CaptureFixture[str]) -> None:
    root = lab.store("a")
    commit_guard(lab, root)
    config = Path(os.environ["HOME"]) / ".config" / "coven"
    config.mkdir(parents=True)
    (config / "config.json").write_text(f'{{"memory_path": "{root}"}}')
    assert cli.main(["memory", "sync"]) == 0
    assert "not pushed: guarded store" in capsys.readouterr().out
