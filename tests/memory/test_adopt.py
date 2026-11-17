"""`coven memory adopt`: an existing git memory store becomes coven's store, in place and safely."""

import dataclasses
import hashlib
import os
import re
import shlex
import stat
import subprocess
import tarfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from memory_support import PROC_LINE, Lab

from coven import cli, config, settings
from coven.errors import CovenError
from coven.memory import adopt, store

FOREIGN_HOOK = "#!/bin/sh\necho foreign hook\nexit 0\n"
OLD_COVEN_HOOK = "#!/bin/sh\n# coven-hook-version: 1\nexit 0\n"
OLD_HOOKS_PATH = "/home/user/old-hooks"
FOLDER_STATE = "Orders App-state.md"


@pytest.fixture(autouse=True)
def neutral_hook_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Coven's hook as adopt installs it, with its ancestor walk pointed away from the real /proc
    (a test run from a project manager has a role set in an ancestor)."""
    text = store.HOOK_SOURCE.read_text(encoding="utf-8")
    assert PROC_LINE in text
    source = tmp_path / "hook-source" / "memory_pre_commit.py"
    source.parent.mkdir()
    source.write_text(text.replace(PROC_LINE, 'PROC_ROOT = Path("/nonexistent-coven-proc")'))
    monkeypatch.setattr(store, "HOOK_SOURCE", source)


@pytest.fixture(autouse=True)
def state_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(home))
    return home


def backups(state: Path) -> list[Path]:
    folder = state / "coven" / "backups"
    return sorted(folder.glob("*.tar.gz")) if folder.is_dir() else []


def seed(lab: Lab, name: str = "orders-notes") -> Path:
    """A store in another tool's layout: mixed-case keys, a state record, unknown folders and a
    file without frontmatter. Its only hook is a foreign one."""
    root = lab.root / name
    root.mkdir()
    lab.git(root, "init", "-q")
    lab.git(root, "symbolic-ref", "HEAD", "refs/heads/main")
    lab.write(root, "user/prefs.md", "Prefs", "how I like answers")
    lab.write(root, "user/tools.md", "Tools", "my editor setup")
    lab.write(root, "projects/Orders App/design.md", "Design", "how orders flow")
    lab.write(root, f"projects/Orders App/{FOLDER_STATE}", "State", "where the work stands")
    lab.write(root, "projects/billing-api/auth.md", "Auth", "tokens for billing")
    lab.write(root, "handoffs/Orders App/handoff-1.md", "Handoff", "pick up the import")
    lab.write(root, "systems/orders-db.md", "Orders DB", "the orders database")
    (root / "user" / "loose.md").write_text("no frontmatter here\n", encoding="utf-8")
    (root / "archive").mkdir()
    (root / "archive" / "old.txt").write_text("kept\n", encoding="utf-8")
    (root / "approvals").mkdir()
    (root / "approvals" / "s1.step").write_text("step\n", encoding="utf-8")
    (root / "approvals" / "s1.json").write_text('{"id": "s1"}\n', encoding="utf-8")
    lab.git(root, "add", "-A")
    lab.git(root, "commit", "-q", "-m", "seed")
    return root


def put_foreign_hook(root: Path, text: str = FOREIGN_HOOK) -> Path:
    hook = root / ".git" / "hooks" / "pre-commit"
    hook.write_text(text, encoding="utf-8")
    hook.chmod(0o755)
    return hook


def snapshot(root: Path) -> dict[str, bytes | None]:
    """Every file (bytes) and folder (None) under root, `.git` included."""
    found: dict[str, bytes | None] = {}
    for current, dirs, files in os.walk(root):
        for name in dirs:
            found[str((Path(current) / name).relative_to(root))] = None
        for name in files:
            file = Path(current) / name
            found[str(file.relative_to(root))] = file.read_bytes()
    return found


def worktree_snapshot(root: Path) -> dict[str, bytes | None]:
    """The snapshot without the `.git` folder (`.gitattributes` and `.gitignore` stay in)."""
    return {
        key: data
        for key, data in snapshot(root).items()
        if key != ".git" and not key.startswith(".git/")
    }


def refusing_hook(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Make coven's hook source a hook that refuses every commit; the good source is returned."""
    good = store.HOOK_SOURCE
    refusing = tmp_path / "refusing-hook"
    refusing.write_text(
        "#!/bin/sh\n# coven-hook-version: 99\necho 'refused for the test' >&2\nexit 1\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(store, "HOOK_SOURCE", refusing)
    return good


def run_adopt(path: Path, *flags: str) -> int:
    return cli.main(["memory", "adopt", str(path), *flags])


def head(lab: Lab, root: Path) -> str:
    return lab.git(root, "rev-parse", "HEAD").strip()


def commit_count(lab: Lab, root: Path) -> int:
    return int(lab.git(root, "rev-list", "--count", "HEAD").strip())


def adopted(lab: Lab, capsys: pytest.CaptureFixture[str]) -> tuple[Path, str]:
    root = seed(lab)
    put_foreign_hook(root)
    assert run_adopt(root, "--yes") == 0
    return root, capsys.readouterr().out


# ---- the plan and the dry run -----------------------------------------------------------


def test_without_yes_it_prints_the_plan_and_changes_nothing(
    lab: Lab, state_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = seed(lab)
    put_foreign_hook(root)
    lab.git(root, "config", "--local", "core.hooksPath", OLD_HOOKS_PATH)
    before = snapshot(root)
    config_before = config.config_file().exists()

    assert run_adopt(root) == 0

    assert snapshot(root) == before
    assert config.config_file().exists() == config_before
    assert not state_home.exists()
    out = capsys.readouterr().out
    assert "user: 2 entries" in out
    assert "projects/Orders App: 2 entries" in out
    assert "projects/billing-api: 1 entries" in out
    assert "handoffs/Orders App: 1 entries" in out
    assert "systems: 1 entries" in out
    assert "approvals: 1 step, 1 json" in out
    assert "user/loose.md" in out  # without frontmatter, kept
    assert "archive" in out  # unknown folder, kept
    assert "projects/Orders App -> projects/orders-app" in out
    assert "handoffs/Orders App -> handoffs/orders-app" in out
    assert FOLDER_STATE in out
    assert "pre-commit.pre-adopt-" in out
    assert OLD_HOOKS_PATH in out
    assert "memory_path" in out
    assert "backup" in out
    assert "nothing changed" in out


# ---- preflight --------------------------------------------------------------------------


def test_refuses_a_dirty_tracked_file(lab: Lab, capsys: pytest.CaptureFixture[str]) -> None:
    root = seed(lab)
    (root / "user" / "prefs.md").write_text("changed\n", encoding="utf-8")
    assert run_adopt(root, "--yes") == 1
    assert "uncommitted changes" in capsys.readouterr().err


def test_refuses_an_untracked_file(lab: Lab, capsys: pytest.CaptureFixture[str]) -> None:
    root = seed(lab)
    (root / "user" / "new.md").write_text("new\n", encoding="utf-8")
    assert run_adopt(root, "--yes") == 1
    assert "uncommitted changes" in capsys.readouterr().err


def test_refuses_a_repo_without_commits(lab: Lab, capsys: pytest.CaptureFixture[str]) -> None:
    root = lab.root / "empty"
    root.mkdir()
    lab.git(root, "init", "-q")
    assert run_adopt(root, "--yes") == 1
    assert "no commits" in capsys.readouterr().err


def test_refuses_when_git_fsck_fails(lab: Lab, capsys: pytest.CaptureFixture[str]) -> None:
    root = seed(lab)
    blob = lab.git(root, "rev-parse", "HEAD:user/prefs.md").strip()
    object_file = root / ".git" / "objects" / blob[:2] / blob[2:]
    object_file.chmod(0o644)
    object_file.unlink()
    assert run_adopt(root, "--yes") == 1
    assert "git fsck" in capsys.readouterr().err


@pytest.mark.parametrize("flag", ["", "--yes"])
def test_refuses_a_folder_that_is_not_a_repo(
    lab: Lab, capsys: pytest.CaptureFixture[str], flag: str
) -> None:
    root = lab.root / "plain"
    root.mkdir()
    (root / "a.md").write_text("a\n", encoding="utf-8")
    assert run_adopt(root, *([flag] if flag else [])) == 1
    assert "not a git work tree" in capsys.readouterr().err


def test_refuses_a_bare_repo(lab: Lab, capsys: pytest.CaptureFixture[str]) -> None:
    root = lab.root / "bare.git"
    lab.git(lab.root, "init", "--bare", "-q", str(root))
    assert run_adopt(root, "--yes") == 1
    assert "bare" in capsys.readouterr().err


def test_refuses_a_symlinked_path_and_names_the_real_folder(
    lab: Lab, capsys: pytest.CaptureFixture[str]
) -> None:
    root = seed(lab)
    link = lab.root / "link"
    link.symlink_to(root)
    before = snapshot(root)
    assert run_adopt(link, "--yes") == 1
    err = capsys.readouterr().err
    assert "symlink" in err
    assert str(root) in err
    assert snapshot(root) == before


def test_refuses_a_folder_inside_a_repo(lab: Lab, capsys: pytest.CaptureFixture[str]) -> None:
    root = seed(lab)
    assert run_adopt(root / "user", "--yes") == 1
    assert "not the top folder" in capsys.readouterr().err


def test_refuses_a_missing_path(lab: Lab, capsys: pytest.CaptureFixture[str]) -> None:
    assert run_adopt(lab.root / "nowhere", "--yes") == 1
    assert "is not a folder" in capsys.readouterr().err


@pytest.mark.parametrize("role", ["pm", ""])
def test_a_restricted_session_is_refused(
    lab: Lab,
    state_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    role: str,
) -> None:
    root = seed(lab)
    before = snapshot(root)
    monkeypatch.setenv("COVEN_SESSION_ROLE", role)
    assert run_adopt(root, "--yes") == 1
    assert "role" in capsys.readouterr().err
    assert snapshot(root) == before
    assert not state_home.exists()


def test_a_colliding_pair_stops_before_any_change(
    lab: Lab, state_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = seed(lab)
    lab.write(root, "projects/orders-app/notes.md", "Notes", "already in form")
    lab.commit(root, "second folder")
    put_foreign_hook(root)
    before = snapshot(root)
    assert run_adopt(root, "--yes") == 1
    err = capsys.readouterr().err
    assert "collide" in err
    assert "projects/Orders App" in err
    assert "projects/orders-app" in err
    assert snapshot(root) == before
    assert not state_home.exists()


def test_a_state_record_that_would_overwrite_another_file_stops_before_any_change(
    lab: Lab, state_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = seed(lab)
    lab.write(root, "projects/Orders App/orders-app-state.md", "Other", "a second record")
    lab.commit(root, "second record")
    before = snapshot(root)
    assert run_adopt(root, "--yes") == 1
    err = capsys.readouterr().err
    assert "collide" in err
    assert f"projects/Orders App/{FOLDER_STATE}" in err
    assert "projects/Orders App/orders-app-state.md" in err
    assert snapshot(root) == before
    assert not state_home.exists()


# ---- backup -----------------------------------------------------------------------------


def test_backup_is_private_readable_and_taken_before_any_rename(lab: Lab, state_home: Path) -> None:
    root = seed(lab)
    old = os.umask(0)  # a loose umask must not open a window
    try:
        assert run_adopt(root, "--yes") == 0
    finally:
        os.umask(old)

    [archive] = backups(state_home)
    folder = archive.parent
    assert folder == state_home / "coven" / "backups"
    assert stat.S_IMODE(folder.stat().st_mode) == 0o700
    assert stat.S_IMODE(archive.stat().st_mode) == 0o600
    assert re.fullmatch(r"orders-notes-\d{8}T\d{6}Z(-\d+)?\.tar\.gz", archive.name)
    checksum = archive.with_name(archive.name + ".sha256")
    assert stat.S_IMODE(checksum.stat().st_mode) == 0o600
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    assert checksum.read_text(encoding="utf-8") == f"{digest}  {archive.name}\n"
    with tarfile.open(archive, "r:gz") as tar:
        names = set(tar.getnames())
    assert "orders-notes/.git/HEAD" in names
    assert "orders-notes/projects/Orders App/design.md" in names
    assert "orders-notes/projects/orders-app/design.md" not in names


def test_backup_folder_defaults_to_the_home_state_folder(
    lab: Lab, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("XDG_STATE_HOME")
    root = seed(lab)
    assert run_adopt(root, "--yes") == 0
    assert len(backups(Path.home() / ".local" / "state")) == 1


def test_a_backup_that_does_not_read_back_stops_before_any_change(
    lab: Lab,
    state_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = seed(lab)
    hook = put_foreign_hook(root)
    before = snapshot(root)
    real = adopt.folder_member_count
    monkeypatch.setattr(adopt, "folder_member_count", lambda path: real(path) + 1)
    assert run_adopt(root, "--yes") == 1
    assert "backup" in capsys.readouterr().err
    assert snapshot(root) == before
    assert hook.read_text(encoding="utf-8") == FOREIGN_HOOK
    assert not config.config_file().exists()
    assert backups(state_home) == []  # a backup that failed its check is not left as if good


def test_a_second_run_in_the_same_second_makes_its_own_backup(
    lab: Lab, state_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(adopt, "stamp", lambda: "20260101T000000Z")
    root = seed(lab)

    def refuse(_changes: object) -> None:
        raise CovenError("injected stop")

    with monkeypatch.context() as patch:
        patch.setattr(adopt.settings, "apply", refuse)
        assert run_adopt(root, "--yes") == 1
    assert run_adopt(root, "--yes") == 0
    first, second = backups(state_home)
    assert first.name != second.name
    assert first.read_bytes() != b""


def test_a_backup_name_taken_stops_and_the_first_backup_survives(
    lab: Lab, state_home: Path
) -> None:
    root = seed(lab)
    first = adopt.make_backup(root, "same-name")
    archive_bytes = first.archive.read_bytes()
    checksum_text = first.checksum.read_text(encoding="utf-8")

    with pytest.raises(CovenError, match="already exists; run again in a second"):
        adopt.make_backup(root, "same-name")

    assert first.archive.read_bytes() == archive_bytes
    assert first.checksum.read_text(encoding="utf-8") == checksum_text


def test_a_checksum_name_taken_removes_only_what_this_backup_made(
    lab: Lab, state_home: Path
) -> None:
    root = seed(lab)
    adopt.make_backup(root, "first")  # makes the private backups folder
    stale = adopt.backups_folder() / "taken.tar.gz.sha256"
    stale.write_text("not mine\n", encoding="utf-8")

    with pytest.raises(CovenError, match="already exists"):
        adopt.make_backup(root, "taken")

    assert stale.read_text(encoding="utf-8") == "not mine\n"
    assert not (adopt.backups_folder() / "taken.tar.gz").exists()


# ---- the old hook -----------------------------------------------------------------------


def test_a_foreign_hook_moves_aside_and_coven_s_takes_its_place(
    lab: Lab, capsys: pytest.CaptureFixture[str]
) -> None:
    root, out = adopted(lab, capsys)
    hooks = root / ".git" / "hooks"
    [moved] = hooks.glob("pre-commit.pre-adopt-*")
    assert re.fullmatch(r"pre-commit\.pre-adopt-\d{8}T\d{6}Z(-\d+)?", moved.name)
    assert moved.read_text(encoding="utf-8") == FOREIGN_HOOK
    assert (hooks / "pre-commit").read_bytes() == store.HOOK_SOURCE.read_bytes()
    assert moved.name in out


def test_a_hook_coven_installed_is_replaced_not_moved(lab: Lab) -> None:
    root = seed(lab)
    put_foreign_hook(root, OLD_COVEN_HOOK)
    assert run_adopt(root, "--yes") == 0
    hooks = root / ".git" / "hooks"
    assert list(hooks.glob("pre-commit.pre-adopt-*")) == []
    assert (hooks / "pre-commit").read_bytes() == store.HOOK_SOURCE.read_bytes()


def test_a_local_hooks_path_is_recorded_and_replaced(
    lab: Lab, capsys: pytest.CaptureFixture[str]
) -> None:
    root = seed(lab)
    lab.git(root, "config", "--local", "core.hooksPath", OLD_HOOKS_PATH)
    assert run_adopt(root, "--yes") == 0
    assert lab.git(root, "config", "--local", "core.hooksPath").strip() == ".git/hooks"
    out = capsys.readouterr().out
    assert OLD_HOOKS_PATH in out
    assert f"git -C {root} config --local core.hooksPath {OLD_HOOKS_PATH}" in out


def test_without_a_hooks_path_the_way_back_unsets_it(
    lab: Lab, capsys: pytest.CaptureFixture[str]
) -> None:
    _, out = adopted(lab, capsys)
    assert "config --local --unset core.hooksPath" in out


# ---- symlinks --------------------------------------------------------------------------


def outside_folder(lab: Lab) -> Path:
    """A folder beside the store that holds an entry and a hand-written index."""
    outside = lab.root / "outside"
    outside.mkdir()
    lab.write(outside, "ext.md", "Ext", "outside the store")
    (outside / "INDEX.md").write_text("hand-written\n", encoding="utf-8")
    return outside


@pytest.mark.parametrize("flag", [[], ["--yes"]], ids=["dry-run", "yes"])
def test_a_symlinked_project_folder_is_refused_and_what_it_points_to_is_not_written(
    lab: Lab, capsys: pytest.CaptureFixture[str], flag: list[str]
) -> None:
    root = seed(lab)
    outside = outside_folder(lab)
    (root / "projects" / "shared").symlink_to(outside)
    lab.git(root, "add", "-A")
    lab.git(root, "commit", "-q", "-m", "link a shared folder")
    store_before, outside_before = snapshot(root), snapshot(outside)

    assert run_adopt(root, *flag) == 1

    assert "projects/shared" in capsys.readouterr().err
    assert snapshot(outside) == outside_before
    assert snapshot(root) == store_before


@pytest.mark.parametrize("link", ["handoffs", "systems", "user", ".gitattributes"])
def test_a_symlinked_scope_folder_or_attributes_file_is_refused_by_name(
    lab: Lab, capsys: pytest.CaptureFixture[str], link: str
) -> None:
    root = seed(lab)
    outside = outside_folder(lab)
    if (root / link).exists():
        lab.git(root, "rm", "-rq", "--", link)
    (root / link).symlink_to(outside / "INDEX.md" if link == ".gitattributes" else outside)
    lab.git(root, "add", "-A")
    lab.git(root, "commit", "-q", "-m", f"link {link}")
    outside_before = snapshot(outside)

    assert run_adopt(root, "--yes") == 1

    assert link in capsys.readouterr().err
    assert snapshot(outside) == outside_before


def link_hook_to_dotfiles(lab: Lab, root: Path, text: str) -> Path:
    """`.git/hooks/pre-commit` as a link to a file outside `.git`; returns that file."""
    target = lab.root / "dotfiles" / "pre-commit"
    target.parent.mkdir()
    target.write_text(text, encoding="utf-8")
    target.chmod(0o755)
    (root / ".git" / "hooks" / "pre-commit").symlink_to(target)
    return target


@pytest.mark.parametrize("flag", [[], ["--yes"]], ids=["dry-run", "yes"])
def test_a_symlinked_pre_commit_hook_is_refused_and_its_target_is_not_written(
    lab: Lab, capsys: pytest.CaptureFixture[str], flag: list[str]
) -> None:
    root = seed(lab)
    target = link_hook_to_dotfiles(lab, root, OLD_COVEN_HOOK)

    assert run_adopt(root, *flag) == 1

    err = capsys.readouterr().err
    assert str(root / ".git" / "hooks" / "pre-commit") in err
    assert "replace the link with the file it points to" in err
    assert target.read_text(encoding="utf-8") == OLD_COVEN_HOOK


def test_a_symlinked_index_is_listed_as_kept_and_never_written(
    lab: Lab, capsys: pytest.CaptureFixture[str]
) -> None:
    root = seed(lab)
    kept = lab.root / "shared-index.md"
    kept.write_text("hand-written\n", encoding="utf-8")
    (root / "user" / "INDEX.md").symlink_to(kept)
    lab.git(root, "add", "-A")
    lab.git(root, "commit", "-q", "-m", "link an index")

    assert run_adopt(root) == 0
    plan = capsys.readouterr().out
    assert "kept as they are (symlinks): user/INDEX.md" in plan

    assert run_adopt(root, "--yes") == 0
    assert kept.read_text(encoding="utf-8") == "hand-written\n"
    assert (root / "user" / "INDEX.md").is_symlink()


# ---- a rollback that cannot finish says so ----------------------------------------------


def write_script(file: Path, body: str) -> None:
    file.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    file.chmod(0o755)


def test_a_rollback_that_cannot_move_a_folder_back_says_where_it_is_and_how_to_finish(
    lab: Lab, capsys: pytest.CaptureFixture[str]
) -> None:
    root = seed(lab)
    (root / ".gitignore").write_text("*.local\n", encoding="utf-8")
    lab.git(root, "add", "-A")
    lab.git(root, "commit", "-q", "-m", "ignore local files")
    (root / "projects" / "Orders App" / "keys.local").write_text("local only\n", encoding="utf-8")
    # A hook that refuses the commit after recreating the old folder, so moving back cannot work.
    write_script(
        root / ".git" / "hooks" / "commit-msg",
        'mkdir -p "projects/Orders App" && echo x > "projects/Orders App/recreated.md"\n'
        "echo 'refused for the test' >&2\nexit 1",
    )

    assert run_adopt(root, "--yes") == 1

    err = capsys.readouterr().err
    assert "refused for the test" in err
    assert "are as they were" not in err  # never claimed when it is not so
    assert "projects/Orders App is still at projects/orders-app" in err
    stranded = root / "projects" / "orders-app" / "keys.local"
    assert stranded.read_text(encoding="utf-8") == "local only\n"
    back = root / "projects" / "Orders App" / "keys.local"
    assert f"mv -n -- {shlex.quote(str(stranded))} {shlex.quote(str(back))}" in err


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_each_hook_step_of_a_rollback_is_tried_and_each_failure_reported(
    lab: Lab, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = seed(lab)
    put_foreign_hook(root)
    hooks = root / ".git" / "hooks"
    good = store.HOOK_SOURCE
    refusing = tmp_path / "locking-hook"
    # The hook locks the hooks folder, so removing it and moving the old hook back both fail.
    refusing.write_text(
        "#!/bin/sh\n# coven-hook-version: 99\nchmod 555 .git/hooks\nexit 1\n", encoding="utf-8"
    )
    monkeypatch.setattr(store, "HOOK_SOURCE", refusing)
    try:
        assert run_adopt(root, "--yes") == 1
        err = capsys.readouterr().err
        [moved] = hooks.glob("pre-commit.pre-adopt-*")
        assert "are as they were" not in err
        assert f"cannot remove {hooks / 'pre-commit'}" in err
        assert f"the old hook is still at {moved}" in err
        assert f"mv -f {moved} {hooks / 'pre-commit'}" in err
    finally:
        hooks.chmod(0o755)
    monkeypatch.setattr(store, "HOOK_SOURCE", good)


# ---- keys -------------------------------------------------------------------------------


def test_folders_and_the_state_record_are_renamed_with_git_mv_and_entries_stay_byte_equal(
    lab: Lab,
) -> None:
    root = seed(lab)
    before = {rel: data for rel, data in snapshot(root).items() if data is not None}
    assert run_adopt(root, "--yes") == 0

    assert not (root / "projects" / "Orders App").exists()
    assert not (root / "handoffs" / "Orders App").exists()
    moves = {
        "projects/Orders App/design.md": "projects/orders-app/design.md",
        f"projects/Orders App/{FOLDER_STATE}": "projects/orders-app/orders-app-state.md",
        "handoffs/Orders App/handoff-1.md": "handoffs/orders-app/handoff-1.md",
    }
    for old, new in moves.items():
        assert (root / new).read_bytes() == before[old]
    status = {
        fields[-1]: fields[0]
        for fields in (
            line.split("\t")
            for line in lab.git(root, "diff", "--name-status", "-M", "HEAD~1", "HEAD").splitlines()
        )
    }
    for new in moves.values():
        assert status[new] == "R100"  # git sees a pure rename: the text did not change
    untouched = [
        "user/prefs.md",
        "user/tools.md",
        "user/loose.md",
        "projects/billing-api/auth.md",
        "systems/orders-db.md",
        "archive/old.txt",
        "approvals/s1.step",
        "approvals/s1.json",
    ]
    for rel in untouched:
        assert (root / rel).read_bytes() == before[rel]


def test_an_empty_untracked_keyed_folder_is_kept_and_adopt_succeeds(
    lab: Lab, capsys: pytest.CaptureFixture[str]
) -> None:
    root = seed(lab)
    (root / "handoffs" / "My Proj").mkdir()
    assert run_adopt(root) == 0
    plan = capsys.readouterr().out
    assert "kept as they are (not tracked): handoffs/My Proj" in plan
    assert "handoffs/My Proj ->" not in plan

    assert run_adopt(root, "--yes") == 0

    assert (root / "handoffs" / "My Proj").is_dir()
    assert list((root / "handoffs" / "My Proj").iterdir()) == []
    assert not (root / "handoffs" / "my-proj").exists()
    assert (root / "handoffs" / "orders-app" / "handoff-1.md").is_file()  # tracked ones still move


def test_an_untracked_folder_does_not_clash_with_a_tracked_one_of_the_same_key(lab: Lab) -> None:
    root = seed(lab)
    (root / "handoffs" / "ORDERS APP").mkdir()
    assert run_adopt(root, "--yes") == 0
    assert (root / "handoffs" / "ORDERS APP").is_dir()
    assert (root / "handoffs" / "orders-app" / "handoff-1.md").is_file()


def test_a_case_only_rename_goes_through_a_temporary_name(
    lab: Lab, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = lab.root / "case-store"
    root.mkdir()
    lab.git(root, "init", "-q")
    lab.git(root, "symbolic-ref", "HEAD", "refs/heads/main")
    lab.write(root, "projects/Billing/notes.md", "Notes", "billing notes")
    lab.git(root, "add", "-A")
    lab.git(root, "commit", "-q", "-m", "seed")
    moves: list[tuple[str, ...]] = []
    real = store.git

    def spy(path: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        if "mv" in args:
            moves.append(args)
        return real(path, *args, check=check)

    monkeypatch.setattr(store, "git", spy)
    assert run_adopt(root, "--yes") == 0
    assert len(moves) == 2
    assert moves[0][-2] == "projects/Billing"
    assert moves[1][-1] == "projects/billing"
    assert moves[0][-1] == moves[1][-2]
    assert "projects/billing/notes.md" in lab.git(root, "ls-files")


# ---- one commit, indexes, attributes ----------------------------------------------------


def test_one_commit_holds_renames_attributes_and_indexes(lab: Lab) -> None:
    root = seed(lab)
    before = commit_count(lab, root)
    assert run_adopt(root, "--yes") == 0
    assert commit_count(lab, root) == before + 1
    assert lab.git(root, "log", "-1", "--format=%s").strip() == "adopt: coven store"
    assert lab.git(root, "status", "--porcelain") == ""
    changed = lab.git(root, "show", "--name-only", "--format=", "HEAD").splitlines()
    assert ".gitattributes" in changed
    assert "user/INDEX.md" in changed
    assert "projects/orders-app/INDEX.md" in changed
    assert "systems/INDEX.md" in changed
    assert "INDEX.md merge=union" in (root / ".gitattributes").read_text().splitlines()
    index = (root / "projects" / "orders-app" / "INDEX.md").read_text(encoding="utf-8")
    assert "orders-app-state.md" in index
    assert store.is_store(root)


def test_an_existing_attributes_file_keeps_its_lines(lab: Lab) -> None:
    root = seed(lab)
    (root / ".gitattributes").write_text("*.png binary", encoding="utf-8")
    lab.git(root, "add", "-A")
    lab.git(root, "commit", "-q", "-m", "attributes")
    assert run_adopt(root, "--yes") == 0
    lines = (root / ".gitattributes").read_text(encoding="utf-8").splitlines()
    assert lines == ["*.png binary", "INDEX.md merge=union"]


@pytest.mark.parametrize(
    "old_hook", [FOREIGN_HOOK, OLD_COVEN_HOOK, None], ids=["foreign", "old-coven", "none"]
)
def test_a_hook_that_refuses_the_commit_puts_the_store_back_and_is_not_bypassed(
    lab: Lab,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    old_hook: str | None,
) -> None:
    refusing_hook(tmp_path, monkeypatch)
    root = seed(lab)
    if old_hook is not None:
        put_foreign_hook(root, old_hook)
    lab.git(root, "config", "--local", "core.hooksPath", OLD_HOOKS_PATH)
    before = head(lab, root)
    tree = worktree_snapshot(root)
    hooks = snapshot(root / ".git" / "hooks")
    git_config = (root / ".git" / "config").read_bytes()

    assert run_adopt(root, "--yes") == 1

    assert "refused for the test" in capsys.readouterr().err
    assert head(lab, root) == before
    assert lab.git(root, "status", "--porcelain") == ""
    assert worktree_snapshot(root) == tree
    assert snapshot(root / ".git" / "hooks") == hooks  # a foreign hook is back, none left over
    assert (root / ".git" / "config").read_bytes() == git_config  # core.hooksPath as it was


def test_files_git_ignores_go_back_with_their_folder_and_a_rerun_succeeds(
    lab: Lab, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = seed(lab)
    (root / ".gitignore").write_text("*.local\n", encoding="utf-8")
    lab.git(root, "add", "-A")
    lab.git(root, "commit", "-q", "-m", "ignore local files")
    secret = root / "projects" / "Orders App" / "keys.local"
    secret.write_text("local only\n", encoding="utf-8")
    good = refusing_hook(tmp_path, monkeypatch)

    assert run_adopt(root, "--yes") == 1

    capsys.readouterr()
    assert secret.read_text(encoding="utf-8") == "local only\n"
    assert not (root / "projects" / "orders-app").exists()
    monkeypatch.setattr(store, "HOOK_SOURCE", good)
    assert run_adopt(root, "--yes") == 0
    moved = root / "projects" / "orders-app" / "keys.local"
    assert moved.read_text(encoding="utf-8") == "local only\n"


def test_an_index_written_before_a_failure_is_removed_again(
    lab: Lab, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = seed(lab)
    tree = worktree_snapshot(root)
    real = adopt.entries.rebuild_indexes

    def write_then_fail(path: Path) -> list[str]:
        changed = real(path)
        if not (path / ".git").exists():  # the plan's scratch copy
            return changed
        raise CovenError("injected stop")

    monkeypatch.setattr(adopt.entries, "rebuild_indexes", write_then_fail)
    assert run_adopt(root, "--yes") == 1
    assert lab.git(root, "status", "--porcelain") == ""
    assert worktree_snapshot(root) == tree


# ---- config -----------------------------------------------------------------------------


def test_memory_path_is_set_and_setup_undo_restores_the_old_one(lab: Lab) -> None:
    previous = Path("/home/user/old-store")
    config.save_config(config.Config(memory_path=previous))
    root = seed(lab)
    assert run_adopt(root, "--yes") == 0
    assert config.load_config().memory_path == root
    assert cli.main(["setup", "--undo", "--yes"]) == 0
    assert config.load_config().memory_path == previous


def test_a_committed_guard_file_also_sets_the_guard_latch(lab: Lab) -> None:
    root = seed(lab)
    (root / "systems" / "guard.json").write_text("{}\n", encoding="utf-8")
    lab.git(root, "add", "-A")
    lab.git(root, "commit", "-q", "-m", "guard")
    assert run_adopt(root, "--yes") == 0
    assert config.load_config().guard == "required"
    assert cli.main(["setup", "--undo", "--yes"]) == 0
    assert config.load_config().guard is None


def test_without_a_guard_file_there_is_no_latch(lab: Lab) -> None:
    root = seed(lab)
    assert run_adopt(root, "--yes") == 0
    assert config.load_config().guard is None


# ---- check and report -------------------------------------------------------------------


def test_a_count_difference_stops_and_prints_the_restore_commands(
    lab: Lab,
    state_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = seed(lab)
    config.save_config(config.Config(memory_path=Path("/home/user/old-store")))
    config_before = config.config_file().read_bytes()
    calls = {"n": 0}
    real = adopt.count_entries

    def lying(path: Path) -> adopt.Counts:
        calls["n"] += 1
        counts = real(path)
        if calls["n"] == 1:
            return counts
        return dataclasses.replace(counts, entries={**counts.entries, "user": 1})

    monkeypatch.setattr(adopt, "count_entries", lying)
    assert run_adopt(root, "--yes") == 1
    err = capsys.readouterr().err
    assert "user: 2 before, 1 after" in err
    assert f"git -C {root} revert" in err
    assert "coven setup --undo" not in err  # the config was never changed
    assert config.config_file().read_bytes() == config_before
    assert not settings.owned_file().exists()
    [archive] = backups(state_home)
    assert str(archive) in err


def test_the_report_names_backup_checksum_renames_hook_and_the_way_back(
    lab: Lab, state_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root, out = adopted(lab, capsys)
    [archive] = backups(state_home)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    [moved] = (root / ".git" / "hooks").glob("pre-commit.pre-adopt-*")
    sha = lab.git(root, "rev-parse", "--short", "HEAD").strip()
    assert str(archive) in out
    assert digest in out
    assert "projects/Orders App -> projects/orders-app" in out
    assert moved.name in out
    assert f"git -C {root} revert {sha}" in out
    assert f"mv -f {moved} {root / '.git' / 'hooks' / 'pre-commit'}" in out
    assert "coven setup --undo" in out
    assert f"tar -xzf {archive}" in out


def test_it_never_pushes_fetches_or_touches_a_remote(lab: Lab, bare_remote: Path) -> None:
    other = lab.root / "someone-else"
    other.mkdir()
    lab.git(other, "init", "-q")
    (other / "note.md").write_text("elsewhere\n", encoding="utf-8")
    lab.git(other, "add", "-A")
    lab.git(other, "commit", "-q", "-m", "elsewhere")
    lab.git(other, "push", "-q", str(bare_remote), "HEAD:refs/heads/theirs")
    remote_refs = lab.git(bare_remote, "for-each-ref")
    root = seed(lab)
    lab.git(root, "remote", "add", "origin", str(bare_remote))
    lab.git(root, "remote", "add", "elsewhere", "/home/user/nowhere.git")
    assert run_adopt(root, "--yes") == 0
    assert lab.git(bare_remote, "for-each-ref") == remote_refs  # nothing pushed
    assert lab.git(root, "for-each-ref", "refs/remotes") == ""  # nothing fetched
    assert not (root / ".git" / "FETCH_HEAD").exists()
    assert lab.git(root, "remote", "get-url", "elsewhere").strip() == "/home/user/nowhere.git"


REMOTE_URL = "https://git.example.internal/user/notes.git"
REMOTE_WARNING = "sync never pushes a guarded store"


def guard_and_remote(lab: Lab, root: Path, *, guard: bool, remote: bool) -> None:
    if guard:
        (root / "systems" / "guard.json").write_text(
            '{"targets": [{"system": "orders-db", "kind": "production", "match": ["psql"]}]}\n',
            encoding="utf-8",
        )
        lab.git(root, "add", "-A")
        lab.git(root, "commit", "-q", "-m", "guard")
    if remote:
        lab.git(root, "remote", "add", "origin", REMOTE_URL)


def test_the_report_warns_once_when_a_guarded_store_has_a_remote_and_never_shows_the_url(
    lab: Lab, capsys: pytest.CaptureFixture[str]
) -> None:
    root = seed(lab)
    guard_and_remote(lab, root, guard=True, remote=True)
    assert run_adopt(root, "--yes") == 0
    out = capsys.readouterr().out
    assert out.count(REMOTE_WARNING) == 1
    [line] = [line for line in out.splitlines() if REMOTE_WARNING in line]
    assert "warning" in line
    assert "remove the remote, or keep it private" in line
    assert REMOTE_URL not in out
    assert "git.example.internal" not in out


@pytest.mark.parametrize(
    ("guard", "remote"), [(True, False), (False, True)], ids=["no-remote", "not-guarded"]
)
def test_the_report_has_no_remote_warning_without_both(
    lab: Lab, capsys: pytest.CaptureFixture[str], guard: bool, remote: bool
) -> None:
    root = seed(lab)
    guard_and_remote(lab, root, guard=guard, remote=remote)
    assert run_adopt(root, "--yes") == 0
    assert REMOTE_WARNING not in capsys.readouterr().out


def test_the_command_runs_on_a_store_coven_already_made(lab: Lab) -> None:
    root = lab.root / "made-by-init"
    store.init_store(root, None)
    lab.write(root, "user/prefs.md", "Prefs", "how I like answers")
    lab.commit(root, "entry")
    assert run_adopt(root, "--yes") == 0
    assert config.load_config().memory_path == root


def test_a_later_commit_that_quotes_the_subject_in_its_body_cannot_redirect_the_way_back(
    lab: Lab, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = seed(lab)
    lab.git(root, "config", "--local", "core.hooksPath", OLD_HOOKS_PATH)

    def refuse(_changes: object) -> None:
        raise CovenError("injected stop")

    with monkeypatch.context() as patch:
        patch.setattr(adopt.settings, "apply", refuse)
        assert run_adopt(root, "--yes") == 1
    capsys.readouterr()
    lab.write(root, "user/later.md", "Later", "a note made after the adopt")
    lab.commit(
        root,
        "later note\n\nadopt: coven store\ncore.hooksPath was: /home/user/forged\n"
        "old hook: forged\nbackup: /home/user/forged.tar.gz",
    )

    assert run_adopt(root, "--yes") == 0

    out = capsys.readouterr().out
    assert "forged" not in out
    assert f"core.hooksPath {OLD_HOOKS_PATH}" in out


def test_a_path_through_a_symlinked_parent_is_refused_with_the_real_path(
    lab: Lab, capsys: pytest.CaptureFixture[str]
) -> None:
    root = seed(lab)
    alias = lab.root / "alias"
    alias.symlink_to(lab.root)
    before = snapshot(root)

    assert run_adopt(alias / root.name, "--yes") == 1

    err = capsys.readouterr().err
    assert "symlink" in err
    assert str(root) in err
    assert snapshot(root) == before


# ---- re-run after a stop ----------------------------------------------------------------


@contextmanager
def failing_nth_move(nth: int, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    seen = {"n": 0}
    real = store.git

    def flaky(path: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        if "mv" in args:
            seen["n"] += 1
            if seen["n"] == nth:
                raise CovenError("injected stop")
        return real(path, *args, check=check)

    with monkeypatch.context() as patch:
        patch.setattr(store, "git", flaky)
        yield


def test_a_stop_before_the_commit_puts_the_hook_back_and_a_rerun_finishes(
    lab: Lab,
    state_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = seed(lab)
    put_foreign_hook(root)
    before = head(lab, root)
    with failing_nth_move(2, monkeypatch):
        assert run_adopt(root, "--yes") == 1
    capsys.readouterr()
    assert head(lab, root) == before
    assert lab.git(root, "status", "--porcelain") == ""
    assert (root / "projects" / "Orders App").is_dir()
    hooks = root / ".git" / "hooks"
    assert (hooks / "pre-commit").read_text(encoding="utf-8") == FOREIGN_HOOK  # moved back
    assert list(hooks.glob("pre-commit.pre-adopt-*")) == []
    assert not config.config_file().exists()

    assert run_adopt(root, "--yes") == 0
    out = capsys.readouterr().out
    assert len(backups(state_home)) == 2  # each run makes its own
    assert len(list((root / ".git" / "hooks").glob("pre-commit.pre-adopt-*"))) == 1
    assert "pre-commit.pre-adopt-" in out  # the earlier run's moved hook still named
    assert (root / "projects" / "orders-app" / "design.md").is_file()
    assert commit_count(lab, root) == 2
    assert config.load_config().memory_path == root
    assert (root / ".git" / "hooks" / "pre-commit").read_bytes() == store.HOOK_SOURCE.read_bytes()


def test_a_stop_after_the_commit_is_finished_by_a_rerun_without_a_second_commit(
    lab: Lab, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = seed(lab)

    def refuse(_changes: object) -> None:
        raise CovenError("injected stop")

    with monkeypatch.context() as patch:
        patch.setattr(adopt.settings, "apply", refuse)
        assert run_adopt(root, "--yes") == 1
    done = commit_count(lab, root)
    assert not config.config_file().exists()

    assert run_adopt(root, "--yes") == 0
    assert commit_count(lab, root) == done
    assert config.load_config().memory_path == root


def test_the_commit_message_carries_the_way_back(lab: Lab, state_home: Path) -> None:
    root = seed(lab)
    put_foreign_hook(root)
    lab.git(root, "config", "--local", "core.hooksPath", OLD_HOOKS_PATH)
    assert run_adopt(root, "--yes") == 0
    [archive] = backups(state_home)
    [moved] = (root / ".git" / "hooks").glob("pre-commit.pre-adopt-*")
    message = lab.git(root, "log", "-1", "--format=%B").splitlines()
    assert message[0] == "adopt: coven store"
    assert f"core.hooksPath was: {OLD_HOOKS_PATH}" in message
    assert f"old hook: {moved.name}" in message
    assert f"backup: {archive}" in message


def test_a_commit_without_a_hooks_path_says_it_was_unset(lab: Lab) -> None:
    root = seed(lab)
    assert run_adopt(root, "--yes") == 0
    message = lab.git(root, "log", "-1", "--format=%B").splitlines()
    assert "core.hooksPath was: (unset)" in message
    assert "old hook: none" in message


def test_a_rerun_reads_the_way_back_from_the_adopt_commit(
    lab: Lab,
    state_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = seed(lab)
    put_foreign_hook(root)
    lab.git(root, "config", "--local", "core.hooksPath", OLD_HOOKS_PATH)

    def refuse(_changes: object) -> None:
        raise CovenError("injected stop")

    with monkeypatch.context() as patch:
        patch.setattr(adopt.settings, "apply", refuse)
        assert run_adopt(root, "--yes") == 1
    capsys.readouterr()
    (first_backup,) = backups(state_home)
    [moved] = (root / ".git" / "hooks").glob("pre-commit.pre-adopt-*")
    sha = lab.git(root, "rev-parse", "--short", "HEAD").strip()
    # The first run already replaced the hooks path: the rerun cannot ask git for the old one.
    assert lab.git(root, "config", "--local", "core.hooksPath").strip() == ".git/hooks"

    assert run_adopt(root, "--yes") == 0
    out = capsys.readouterr().out
    assert f"git -C {root} config --local core.hooksPath {OLD_HOOKS_PATH}" in out
    assert f"git -C {root} revert {sha}" in out
    assert f"backup {first_backup}" in out
    assert moved.name in out
    assert commit_count(lab, root) == 2


def test_a_stop_before_the_commit_keeps_the_old_hooks_path_for_the_rerun(
    lab: Lab, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = seed(lab)
    lab.git(root, "config", "--local", "core.hooksPath", OLD_HOOKS_PATH)
    with failing_nth_move(2, monkeypatch):
        assert run_adopt(root, "--yes") == 1
    capsys.readouterr()
    assert lab.git(root, "config", "--local", "core.hooksPath").strip() == OLD_HOOKS_PATH
    assert run_adopt(root, "--yes") == 0
    assert f"core.hooksPath was: {OLD_HOOKS_PATH}" in lab.git(root, "log", "-1", "--format=%B")


def test_a_run_with_nothing_left_is_refused(lab: Lab, capsys: pytest.CaptureFixture[str]) -> None:
    root, _ = adopted(lab, capsys)
    before = snapshot(root)
    assert run_adopt(root, "--yes") == 1
    assert "already coven's store" in capsys.readouterr().err
    assert snapshot(root) == before


def test_a_dry_run_of_a_finished_store_is_refused_too(
    lab: Lab, capsys: pytest.CaptureFixture[str]
) -> None:
    root, _ = adopted(lab, capsys)
    assert run_adopt(root) == 1
    assert "already coven's store" in capsys.readouterr().err


# ---- helpers of the module --------------------------------------------------------------


def test_counts_equal_after_a_normal_run(lab: Lab, capsys: pytest.CaptureFixture[str]) -> None:
    _, out = adopted(lab, capsys)
    assert "counts equal" in out
