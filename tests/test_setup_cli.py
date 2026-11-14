import json
import os
import subprocess
from pathlib import Path

import pytest

from coven import cli, config, paths


@pytest.fixture
def store(tmp_path: Path) -> Path:
    return tmp_path / "memory-store"


def settings_file() -> Path:
    return paths.claude_dir() / "settings.json"


def shim_file() -> Path:
    return Path.home() / ".local" / "bin" / "coven"


def run_setup(*args: str) -> int:
    return cli.main(["setup", *args])


def fake_gh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reply: str, exit_code: int = 0
) -> Path:
    """A `gh` on a test-only PATH that prints `reply`; it logs its arguments."""
    bin_dir = tmp_path / "fake-bin"
    bin_dir.mkdir(exist_ok=True)
    log = tmp_path / "gh.log"
    script = bin_dir / "gh"
    script.write_text(f"#!/bin/sh\necho \"$@\" >> '{log}'\necho '{reply}'\nexit {exit_code}\n")
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return log


def test_dry_run_writes_nothing(store: Path, capsys: pytest.CaptureFixture[str]):
    code = run_setup(
        "--dry-run",
        "--cross-session-accept",
        "--shim",
        "--pm-cap",
        "3",
        "--memory-path",
        str(store),
    )

    out = capsys.readouterr().out
    assert code == 0
    assert f'{settings_file()}: crossSessionInbound: (missing) -> "accept"' in out
    assert f"{config.config_file()}: pm_cap: (missing) -> 3" in out
    assert str(shim_file()) in out
    assert not settings_file().exists()
    assert not config.config_file().exists()
    assert not shim_file().exists()
    assert not store.exists()


def test_without_yes_nothing_is_applied(store: Path, capsys: pytest.CaptureFixture[str]):
    code = run_setup("--cross-session-accept", "--memory-path", str(store))

    assert code == 0
    assert "rerun with --yes" in capsys.readouterr().out
    assert not settings_file().exists()
    assert not store.exists()


def test_yes_applies_settings_config_and_creates_the_store(
    store: Path, capsys: pytest.CaptureFixture[str]
):
    code = run_setup(
        "--yes", "--cross-session-accept", "--pm-cap", "3", "--memory-path", str(store)
    )

    assert code == 0
    assert json.loads(settings_file().read_text())["crossSessionInbound"] == "accept"
    loaded = config.load_config()
    assert (loaded.pm_cap, loaded.memory_path) == (3, store)
    assert (store / ".git").is_dir()
    assert (store / "user").is_dir()
    assert str(store) in capsys.readouterr().out


def test_pm_cap_defaults_to_two(store: Path):
    assert run_setup("--yes", "--memory-path", str(store)) == 0

    assert config.load_config().pm_cap == 2


def test_pm_cap_below_one_is_refused(store: Path, capsys: pytest.CaptureFixture[str]):
    code = run_setup("--yes", "--pm-cap", "0", "--memory-path", str(store))

    assert code == 1
    assert "pm_cap" in capsys.readouterr().err
    assert not config.config_file().exists()
    assert not store.exists()


def test_comment_in_settings_stops_setup_naming_file_and_line(
    store: Path, capsys: pytest.CaptureFixture[str]
):
    settings_file().parent.mkdir(parents=True)
    text = '{\n  // note\n  "theme": "dark"\n}\n'
    settings_file().write_text(text)

    code = run_setup("--yes", "--cross-session-accept", "--memory-path", str(store))

    assert code == 1
    assert "settings.json" in capsys.readouterr().err
    assert settings_file().read_text() == text
    assert not config.config_file().exists()


def test_existing_store_is_left_alone(store: Path):
    assert run_setup("--yes", "--memory-path", str(store)) == 0
    head = subprocess.run(
        ["git", "-C", str(store), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout

    assert run_setup("--yes", "--memory-path", str(store), "--pm-cap", "4") == 0

    again = subprocess.run(
        ["git", "-C", str(store), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout
    assert again == head


def test_undo_restores_keys_and_keeps_the_store(store: Path, capsys: pytest.CaptureFixture[str]):
    settings_file().parent.mkdir(parents=True)
    settings_file().write_text(json.dumps({"theme": "dark"}))
    assert (
        run_setup(
            "--yes",
            "--cross-session-accept",
            "--deny-secrets",
            "--shim",
            "--memory-path",
            str(store),
        )
        == 0
    )

    code = run_setup("--undo", "--yes")

    assert code == 0
    assert json.loads(settings_file().read_text()) == {"theme": "dark"}
    assert not config.config_file().exists()
    assert not shim_file().exists()
    assert (store / ".git").is_dir()
    assert "left as is" not in capsys.readouterr().out


def test_undo_names_skipped_keys(store: Path, capsys: pytest.CaptureFixture[str]):
    assert run_setup("--yes", "--cross-session-accept", "--memory-path", str(store)) == 0
    settings_file().write_text(json.dumps({"crossSessionInbound": "ask"}))
    capsys.readouterr()

    code = run_setup("--undo", "--yes")

    assert code == 0
    assert "crossSessionInbound" in capsys.readouterr().out


def test_undo_cannot_be_combined_with_choices(capsys: pytest.CaptureFixture[str]):
    assert run_setup("--undo", "--shim") == 1
    assert "--undo" in capsys.readouterr().err


def test_remote_visibility_private_gives_no_warning(
    tmp_path: Path, store: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    log = fake_gh(tmp_path, monkeypatch, '{"visibility":"PRIVATE"}')

    code = run_setup("--yes", "--memory-path", str(store), "--memory-remote", "owner/repo")

    assert code == 0
    assert "repo view owner/repo --json visibility" in log.read_text()
    assert "warning" not in capsys.readouterr().err
    assert config.load_config().memory_remote == "owner/repo"


def test_remote_visibility_public_warns(
    tmp_path: Path, store: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    fake_gh(tmp_path, monkeypatch, '{"visibility":"PUBLIC"}')

    code = run_setup("--yes", "--memory-path", str(store), "--memory-remote", "owner/repo")

    assert code == 0
    assert "warning" in capsys.readouterr().err


def test_remote_check_failure_warns_it_could_not_check(
    tmp_path: Path, store: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    fake_gh(tmp_path, monkeypatch, "no such repo", exit_code=1)

    code = run_setup("--yes", "--memory-path", str(store), "--memory-remote", "owner/repo")

    assert code == 0
    assert "could not check" in capsys.readouterr().err


def test_remote_without_gh_warns_it_could_not_check(
    store: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    empty = Path(os.environ["HOME"]) / "no-tools"
    empty.mkdir()
    git = subprocess.run(
        ["which", "git"], capture_output=True, text=True, check=True
    ).stdout.strip()
    (empty / "git").symlink_to(git)
    monkeypatch.setenv("PATH", str(empty))

    code = run_setup("--yes", "--memory-path", str(store), "--memory-remote", "owner/repo")

    assert code == 0
    assert "could not check" in capsys.readouterr().err


def test_remote_starting_with_a_dash_is_refused(store: Path, capsys: pytest.CaptureFixture[str]):
    code = run_setup("--yes", "--memory-path", str(store), "--memory-remote=--json")

    assert code == 1
    assert "memory_remote" in capsys.readouterr().err


def test_path_hint_when_local_bin_is_not_on_path(
    store: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.setenv("PATH", os.environ["PATH"])
    capsys.readouterr()

    assert run_setup("--yes", "--shim", "--memory-path", str(store)) == 0

    out = capsys.readouterr().out
    assert sum("is not on your PATH" in line for line in out.splitlines()) == 1


def test_no_path_hint_when_local_bin_is_on_path(
    store: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    local_bin = Path.home() / ".local" / "bin"
    monkeypatch.setenv("PATH", f"{local_bin}{os.pathsep}{os.environ['PATH']}")

    assert run_setup("--yes", "--shim", "--memory-path", str(store)) == 0

    assert "is not on your PATH" not in capsys.readouterr().out


def write_installed_plugins(data: object) -> None:
    file = paths.installed_plugins_file()
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps(data))


def install_fake_plugin(root: Path) -> None:
    (root / "bin").mkdir(parents=True)
    script = root / "bin" / "coven"
    script.write_text('#!/bin/sh\necho "plugin coven got: $@"\n')
    script.chmod(0o755)


def run_shim(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["sh", str(shim_file()), *args], capture_output=True, text=True, check=False
    )


def test_shim_execs_install_path(tmp_path: Path, store: Path):
    assert run_setup("--yes", "--shim", "--memory-path", str(store)) == 0
    root = tmp_path / "plugin root"
    install_fake_plugin(root)
    write_installed_plugins({"version": 2, "plugins": {"coven@coven": {"installPath": str(root)}}})

    done = run_shim("memory", "sync", "two words")

    assert done.returncode == 0
    assert done.stdout == "plugin coven got: memory sync two words\n"


def test_shim_reads_the_list_form_of_installed_plugins(tmp_path: Path, store: Path):
    assert run_setup("--yes", "--shim", "--memory-path", str(store)) == 0
    root = tmp_path / "plugin"
    install_fake_plugin(root)
    write_installed_plugins(
        {
            "version": 2,
            "plugins": {
                "other@market": [{"installPath": str(tmp_path / "other")}],
                "coven@coven": [{"scope": "user", "installPath": str(root)}],
            },
        }
    )

    done = run_shim("doctor")

    assert done.stdout == "plugin coven got: doctor\n"


def test_shim_not_installed_message(store: Path):
    assert run_setup("--yes", "--shim", "--memory-path", str(store)) == 0
    write_installed_plugins({"version": 2, "plugins": {"other@market": {"installPath": "/x"}}})

    done = run_shim("doctor")

    assert done.returncode == 1
    assert "coven: plugin coven@coven is not installed" in done.stderr


def test_shim_with_no_plugins_file_says_not_installed(store: Path):
    assert run_setup("--yes", "--shim", "--memory-path", str(store)) == 0

    done = run_shim("doctor")

    assert done.returncode == 1
    assert "coven: plugin coven@coven is not installed" in done.stderr


def test_memory_path_that_is_a_file_is_refused_before_any_write(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    target = tmp_path / "a-file"
    target.write_text("x")

    code = run_setup("--yes", "--cross-session-accept", "--memory-path", str(target))

    assert code == 1
    assert str(target) in capsys.readouterr().err
    assert not settings_file().exists()
    assert not config.config_file().exists()


def test_memory_path_with_foreign_files_is_refused_before_any_write(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    target = tmp_path / "documents"
    target.mkdir()
    (target / "notes.txt").write_text("mine")

    code = run_setup("--yes", "--cross-session-accept", "--memory-path", str(target))

    assert code == 1
    assert "not a coven memory store" in capsys.readouterr().err
    assert not settings_file().exists()
    assert not config.config_file().exists()
    assert sorted(entry.name for entry in target.iterdir()) == ["notes.txt"]


def test_an_existing_store_gets_its_remote_and_hook_checked(
    tmp_path: Path, store: Path, monkeypatch: pytest.MonkeyPatch
):
    fake_gh(tmp_path, monkeypatch, '{"visibility":"PRIVATE"}')
    assert run_setup("--yes", "--memory-path", str(store)) == 0
    (store / ".git" / "hooks" / "pre-commit").unlink()

    code = run_setup("--yes", "--memory-path", str(store), "--memory-remote", "owner/repo")

    assert code == 0
    assert (store / ".git" / "hooks" / "pre-commit").exists()
    origin = subprocess.run(
        ["git", "-C", str(store), "remote", "get-url", "origin"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert origin == "owner/repo"


def test_undo_without_yes_previews_and_changes_nothing(
    store: Path, capsys: pytest.CaptureFixture[str]
):
    assert run_setup("--yes", "--cross-session-accept", "--memory-path", str(store)) == 0
    capsys.readouterr()

    code = run_setup("--undo")

    out = capsys.readouterr().out
    assert code == 0
    assert f'{settings_file()}: crossSessionInbound: "accept" -> (missing)' in out
    assert "rerun with --yes" in out
    assert json.loads(settings_file().read_text()) == {"crossSessionInbound": "accept"}


def test_undo_dry_run_previews_and_changes_nothing(store: Path, capsys: pytest.CaptureFixture[str]):
    assert run_setup("--yes", "--cross-session-accept", "--memory-path", str(store)) == 0
    capsys.readouterr()

    code = run_setup("--undo", "--dry-run", "--yes")

    out = capsys.readouterr().out
    assert code == 0
    assert "dry run" in out
    assert "crossSessionInbound" in out
    assert json.loads(settings_file().read_text()) == {"crossSessionInbound": "accept"}


def test_undo_with_nothing_recorded_says_so(capsys: pytest.CaptureFixture[str]):
    assert run_setup("--undo", "--yes") == 0

    out = capsys.readouterr().out
    assert "nothing to undo" in out
    assert "undone" not in out


def test_undo_removes_the_files_setup_created(store: Path):
    assert run_setup("--yes", "--cross-session-accept", "--memory-path", str(store)) == 0

    assert run_setup("--undo", "--yes") == 0

    assert not settings_file().exists()
    assert not config.config_file().exists()
    assert not (paths.config_dir() / "owned.json").exists()


def test_shim_prefers_the_user_scope_install(tmp_path: Path, store: Path):
    assert run_setup("--yes", "--shim", "--memory-path", str(store)) == 0
    project_root = tmp_path / "project-install"
    user_root = tmp_path / "user-install"
    install_fake_plugin(project_root)
    install_fake_plugin(user_root)
    (project_root / "bin" / "coven").write_text("#!/bin/sh\necho project\n")
    (user_root / "bin" / "coven").write_text("#!/bin/sh\necho user\n")
    write_installed_plugins(
        {
            "plugins": {
                "coven@coven": [
                    {"scope": "project", "installPath": str(project_root)},
                    {"scope": "user", "installPath": str(user_root)},
                ]
            }
        }
    )

    assert run_shim().stdout == "user\n"


def test_shim_says_when_python3_is_missing(tmp_path: Path, store: Path):
    assert run_setup("--yes", "--shim", "--memory-path", str(store)) == 0
    empty = tmp_path / "empty-path"
    empty.mkdir()

    done = subprocess.run(
        ["/bin/sh", str(shim_file())],
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": str(empty), "HOME": os.environ["HOME"]},
    )

    assert done.returncode == 1
    assert "coven: python3 not found" in done.stderr


def git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def test_a_no_checkout_clone_is_refused_before_any_write(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    source = tmp_path / "source"
    source.mkdir()
    git("init", "-q", cwd=source)
    git("commit", "-q", "--allow-empty", "-m", "history", cwd=source)
    clone = tmp_path / "clone"
    git("clone", "-q", "--no-checkout", str(source), str(clone), cwd=tmp_path)
    assert [entry.name for entry in clone.iterdir()] == [".git"]

    code = run_setup("--yes", "--cross-session-accept", "--memory-path", str(clone))

    assert code == 1
    assert "not empty" in capsys.readouterr().err
    assert not settings_file().exists()
    assert not config.config_file().exists()
    assert not (paths.config_dir() / "owned.json").exists()
