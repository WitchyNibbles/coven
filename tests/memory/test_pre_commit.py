import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest
from memory_support import Lab, load_hook

from coven.memory import entries

HOOK = Path(__file__).resolve().parents[2] / "hooks" / "memory_pre_commit.py"
ORIGIN = "origin: https://example.com/team/app.git"


@pytest.fixture(scope="module")
def hook() -> ModuleType:
    return load_hook("memory_pre_commit")


@pytest.fixture(autouse=True)
def no_ancestor_role(hook: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The ancestor walk reads an empty fake /proc, whatever the real process tree holds."""
    empty = tmp_path / "empty-proc"
    empty.mkdir()
    monkeypatch.setattr(hook, "PROC_ROOT", empty)


class Repo:
    """A git repo the hook runs in; its top folder is the working directory, as under git."""

    def __init__(self, path: Path, lab: Lab) -> None:
        self.path = path
        self.git = lambda *args: lab.git(path, *args)

    def stage(self, relative: str, text: str) -> None:
        target = self.path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
        self.git("add", relative)


@pytest.fixture
def repo(tmp_path: Path, lab: Lab, monkeypatch: pytest.MonkeyPatch) -> Repo:
    path = tmp_path / "store"
    path.mkdir()
    lab.git(path, "init", "-q")
    monkeypatch.chdir(path)
    return Repo(path, lab)


def refuses(
    hook: ModuleType,
    repo: Repo,
    capsys: pytest.CaptureFixture[str],
    token: str,
    name: str,
) -> None:
    repo.stage("user/note.md", f"---\nname: n\ndescription: d\n---\nsee {token} here\n")
    assert hook.main() == 1
    err = capsys.readouterr().err
    assert name in err
    assert "user/note.md:5" in err
    assert token not in err
    assert not (repo.path / "user" / "INDEX.md").exists()


def test_pre_commit_refuses_github_token(hook, repo, capsys) -> None:
    refuses(hook, repo, capsys, "ghp_" + "aB3d" * 9, "GitHub token")


def test_pre_commit_refuses_github_fine_grained_token(hook, repo, capsys) -> None:
    refuses(hook, repo, capsys, "github_pat_" + "x1Y2" * 6, "GitHub fine-grained token")


def test_pre_commit_refuses_api_key(hook, repo, capsys) -> None:
    refuses(hook, repo, capsys, "sk-" + "ab12" * 6, "API key (sk-)")


def test_pre_commit_refuses_aws_access_key(hook, repo, capsys) -> None:
    refuses(hook, repo, capsys, "AKIA" + "ABCD1234EFGH5678", "AWS access key")


def test_pre_commit_refuses_slack_token(hook, repo, capsys) -> None:
    refuses(hook, repo, capsys, "xoxb-" + "1234567890-abcdef", "Slack token")


def test_pre_commit_refuses_private_key(hook, repo, capsys) -> None:
    refuses(hook, repo, capsys, "-----BEGIN " + "RSA PRIVATE KEY-----", "private key")


def test_pre_commit_refuses_a_token_glued_to_a_name(hook, repo, capsys) -> None:
    refuses(hook, repo, capsys, "MY_TOKEN_ghp_" + "aB3d" * 9, "GitHub token")


def test_sk_pattern_ignores_risk_assessment(hook, repo, capsys) -> None:
    prose = "risk-assessment-2024-quarterly-review and task-1234567890123456789012345 and sk-no-digits-here-at-all-in-this-one"
    repo.stage("user/note.md", f"---\nname: n\ndescription: d\n---\n{prose}\n")
    assert hook.main() == 0
    assert capsys.readouterr().err == ""


def test_pre_commit_refuses_pm_role(hook, repo, capsys, monkeypatch) -> None:
    repo.stage("user/note.md", "---\nname: n\ndescription: d\n---\n")
    monkeypatch.setenv("COVEN_SESSION_ROLE", "pm")
    assert hook.main() == 1
    err = capsys.readouterr().err
    assert "project-manager sessions cannot commit" in err
    assert not (repo.path / "user" / "INDEX.md").exists()


@pytest.mark.parametrize("role", ["", "gm", "PM", "pm2"])
def test_pre_commit_allows_roles_other_than_pm(hook, repo, monkeypatch, role) -> None:
    repo.stage("user/note.md", "---\nname: n\ndescription: d\n---\n")
    monkeypatch.setenv("COVEN_SESSION_ROLE", role)
    assert hook.main() == 0


def fake_proc(root: Path, chain: dict[int, tuple[int, str | None]]) -> None:
    """A fake /proc: pid -> (parent pid, role or None)."""
    for pid, (parent, role) in chain.items():
        folder = root / str(pid)
        folder.mkdir(parents=True)
        (folder / "status").write_text(f"Name:\tx\nPPid:\t{parent}\n")
        env = b"HOME=/x\0" + (f"COVEN_SESSION_ROLE={role}\0".encode() if role else b"")
        (folder / "environ").write_bytes(env)


def test_pre_commit_refuses_when_an_ancestor_has_the_role(
    hook, repo, capsys, tmp_path, monkeypatch
) -> None:
    proc = tmp_path / "proc"
    me = os.getpid()
    fake_proc(proc, {me: (500, None), 500: (400, None), 400: (1, "pm")})
    monkeypatch.setattr(hook, "PROC_ROOT", proc)
    repo.stage("user/note.md", "---\nname: n\ndescription: d\n---\n")
    assert hook.main() == 1
    assert "project-manager sessions cannot commit" in capsys.readouterr().err


def test_pre_commit_allows_ancestors_without_the_role(hook, repo, tmp_path, monkeypatch) -> None:
    proc = tmp_path / "proc"
    fake_proc(proc, {os.getpid(): (500, None), 500: (1, None)})
    monkeypatch.setattr(hook, "PROC_ROOT", proc)
    repo.stage("user/note.md", "---\nname: n\ndescription: d\n---\n")
    assert hook.main() == 0


def test_pre_commit_refuses_a_gm_process_under_a_pm_ancestor(
    hook, repo, capsys, tmp_path, monkeypatch
) -> None:
    proc = tmp_path / "proc"
    fake_proc(proc, {os.getpid(): (500, "gm"), 500: (400, "gm"), 400: (1, "pm")})
    monkeypatch.setattr(hook, "PROC_ROOT", proc)
    monkeypatch.setenv("COVEN_SESSION_ROLE", "gm")
    repo.stage("user/note.md", "---\nname: n\ndescription: d\n---\n")
    assert hook.main() == 1
    assert "project-manager sessions cannot commit" in capsys.readouterr().err


def test_pre_commit_allows_gm_processes_all_the_way_up(hook, repo, tmp_path, monkeypatch) -> None:
    proc = tmp_path / "proc"
    fake_proc(proc, {os.getpid(): (500, "gm"), 500: (1, "gm")})
    monkeypatch.setattr(hook, "PROC_ROOT", proc)
    repo.stage("user/note.md", "---\nname: n\ndescription: d\n---\n")
    assert hook.main() == 0


def test_pm_session_walk_stops_on_loops_and_unreadable_processes(tmp_path: Path, hook) -> None:
    proc = tmp_path / "proc"
    fake_proc(proc, {10: (11, None), 11: (10, None)})
    assert not hook.is_pm_session({}, proc, 10)
    assert not hook.is_pm_session({}, proc, 999)
    assert hook.is_pm_session({"COVEN_SESSION_ROLE": "pm"}, proc, 10)


def test_staged_clean_entry_commits_although_the_worktree_now_holds_a_token(hook, repo) -> None:
    repo.stage("user/a.md", "---\nname: A\ndescription: clean\n---\n")
    (repo.path / "user" / "a.md").write_text("key " + "ghp_" + "aB3d" * 9 + "\n")
    assert hook.main() == 0
    assert "ghp_" not in repo.git("show", ":user/a.md")
    assert "- [A](a.md) — clean" in repo.git("show", ":user/INDEX.md")


def test_staged_token_is_refused_although_the_worktree_was_cleaned(hook, repo, capsys) -> None:
    token = "ghp_" + "aB3d" * 9
    repo.stage("user/a.md", f"---\nname: A\ndescription: d\n---\n{token}\n")
    (repo.path / "user" / "a.md").write_text("---\nname: A\ndescription: d\n---\nclean\n")
    assert hook.main() == 1
    assert "user/a.md" in capsys.readouterr().err


def test_index_uses_the_staged_description_not_the_worktree_one(hook, repo) -> None:
    repo.stage("user/a.md", "---\nname: A\ndescription: staged text\n---\n")
    (repo.path / "user" / "a.md").write_text("---\nname: A\ndescription: worktree text\n---\n")
    assert hook.main() == 0
    index = repo.git("show", ":user/INDEX.md")
    assert "staged text" in index
    assert "worktree text" not in index


def test_installed_hook_commits_what_is_staged_not_what_is_in_the_worktree(lab: Lab) -> None:
    store = lab.store()
    lab.write(store, "user/a.md", "A", "clean")
    lab.git(store, "add", "-A")
    (store / "user" / "a.md").write_text("key " + "ghp_" + "aB3d" * 9 + "\n")
    lab.git(store, "commit", "-q", "-m", "staged only")
    assert "ghp_" not in lab.git(store, "show", "HEAD:user/a.md")


def test_hook_on_an_old_python_prints_one_line_and_exits_1() -> None:
    code = (
        "import runpy, sys; sys.hexversion = 0x03090000; "
        f"runpy.run_path({str(HOOK)!r}, run_name='__main__')"
    )
    done = subprocess.run(
        [sys.executable, "-I", "-c", code], capture_output=True, text=True, check=False
    )
    assert done.returncode == 1
    assert done.stderr == "coven memory hook needs Python 3.12+\n"


def test_index_regenerated(hook, repo) -> None:
    repo.stage("user/b.md", "---\nname: Beta\ndescription: second\n---\n")
    repo.stage("user/a.md", "---\nname: Alpha\ndescription: " + "w " * 100 + "\n---\n")
    repo.stage("projects/app/c.md", "---\nname: C\ndescription: third\n---\n")
    (repo.path / "user" / "unstaged.md").write_text("---\nname: Nope\ndescription: no\n---\n")
    assert hook.main() == 0
    user = (repo.path / "user" / "INDEX.md").read_text()
    assert user == entries.index_text(
        [entries.Entry("a.md", "Alpha", "w " * 100), entries.Entry("b.md", "Beta", "second")]
    )
    assert "Nope" not in user
    assert (repo.path / "projects/app/INDEX.md").read_text().endswith("- [C](c.md) — third\n")
    staged = repo.git("diff", "--cached", "--name-only").split()
    assert "user/INDEX.md" in staged
    assert "projects/app/INDEX.md" in staged


def test_index_regenerated_warns_about_an_entry_without_frontmatter(hook, repo, capsys) -> None:
    repo.stage("user/plain.md", "no frontmatter\n")
    assert hook.main() == 0
    assert "user/plain.md: no name/description frontmatter" in capsys.readouterr().err
    assert "- [plain.md](plain.md) — (no description)" in (repo.path / "user/INDEX.md").read_text()


def test_index_keeps_the_origin_line_byte_for_byte(hook, repo) -> None:
    line = "origin:\t https://example.com/team/app.git  "
    repo.stage("projects/app/INDEX.md", f"{entries.HEADER}\n{line}\n- [Old](old.md) — gone\n")
    repo.stage("projects/app/new.md", "---\nname: New\ndescription: fresh\n---\n")
    assert hook.main() == 0
    lines = (repo.path / "projects/app/INDEX.md").read_text().split("\n")
    assert lines[1] == line
    assert lines[2] == "- [New](new.md) — fresh"
    assert repo.git("show", ":projects/app/INDEX.md").split("\n")[1] == line


def test_index_with_an_origin_line_survives_losing_every_entry(hook, repo) -> None:
    repo.stage("projects/app/INDEX.md", f"{entries.HEADER}\n{ORIGIN}\n")
    repo.stage("projects/app/.gitkeep", "")
    assert hook.main() == 0
    assert (repo.path / "projects/app/INDEX.md").read_text() == entries.index_text([], ORIGIN)


def test_index_without_entries_or_origin_is_removed(hook, repo) -> None:
    repo.stage("user/a.md", "---\nname: A\ndescription: d\n---\n")
    assert hook.main() == 0
    repo.git("commit", "-q", "-m", "first")
    repo.git("rm", "-q", "user/a.md")
    assert hook.main() == 0
    assert not (repo.path / "user/INDEX.md").exists()
    assert "user/INDEX.md" not in repo.git("ls-files")


def test_pre_commit_fails_closed_on_any_error(hook, repo, monkeypatch) -> None:
    def boom() -> list[str]:
        raise RuntimeError("boom\nsecond line")

    monkeypatch.setattr(hook, "changed_paths", boom)
    with pytest.raises(SystemExit) as stop:
        hook.main()
    message = stop.value.code
    assert isinstance(message, str)
    assert "\n" not in message
    assert (
        message
        == "coven memory: pre-commit failed (RuntimeError: boom second line); commit refused"
    )


def test_pre_commit_fails_closed_outside_a_repo(tmp_path: Path) -> None:
    plain = tmp_path / "not-a-repo"
    plain.mkdir()
    done = subprocess.run(
        [sys.executable, "-I", str(HOOK)], cwd=plain, capture_output=True, text=True, check=False
    )
    assert done.returncode != 0
    assert done.stderr.startswith("coven memory: pre-commit failed (")
    assert len(done.stderr.splitlines()) == 1


def test_installed_hook_blocks_a_commit_with_a_token(lab: Lab) -> None:
    store = lab.store()
    lab.write(store, "user/a.md", "A", "d", body="key " + "ghp_" + "aB3d" * 9 + "\n")
    with pytest.raises(subprocess.CalledProcessError) as failed:
        lab.commit(store)
    assert "GitHub token" in failed.value.stderr
    assert "user/a.md" in failed.value.stderr


def test_installed_hook_writes_the_index_into_the_commit(lab: Lab) -> None:
    store = lab.store()
    lab.write(store, "user/a.md", "A", "first fact")
    lab.commit(store)
    assert "- [A](a.md) — first fact" in lab.git(store, "show", "HEAD:user/INDEX.md")
    assert lab.git(store, "status", "--porcelain") == ""
