"""The permission profile every PM starts with, and `coven pm launch`, which prints its command."""

import json
import shlex
import subprocess
from pathlib import Path

import pytest

from coven import cli
from coven.errors import CovenError
from coven.pm.profile import launch_command, pm_settings, refuse_option_like

MEMORY = Path("/home/user/coven-memory")


def test_profile_json_pinned() -> None:
    assert pm_settings(MEMORY) == {
        "env": {"COVEN_SESSION_ROLE": "pm"},
        "crossSessionInbound": "accept",
        "permissions": {
            "ask": ["Bash(git push:*)", "Bash(git merge:*)", "Bash(gh:*)"],
            "deny": [
                "Bash(gh pr merge:*)",
                "Bash(git push --force:*)",
                "Bash(git push -f:*)",
                "Bash(git push --force-with-lease:*)",
                "Bash(git push origin main:*)",
                "Bash(git push origin master:*)",
                "Write(//home/user/coven-memory/**)",
                "Edit(//home/user/coven-memory/**)",
            ],
        },
    }


def test_the_memory_rules_use_the_absolute_path_form(tmp_path: Path) -> None:
    # A single leading slash would anchor at the settings source, not the filesystem root.
    rules = json.dumps(pm_settings(tmp_path))
    assert f"Write(/{tmp_path}/**)" in rules
    assert f"Edit(/{tmp_path}/**)" in rules


def test_a_symlinked_store_is_denied_by_both_its_paths(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    rules = pm_settings(link)["permissions"]
    assert isinstance(rules, dict)
    deny = rules["deny"]
    for tool in ("Write", "Edit"):
        assert f"{tool}(/{link}/**)" in deny
        assert f"{tool}(/{real}/**)" in deny
    assert len(deny) == len(set(deny))


def test_launch_command_shape(tmp_path: Path) -> None:
    repo, brief = tmp_path / "repo", tmp_path / "pm-acme.md"
    brief.write_text("brief\n")
    command = launch_command("acme", repo, brief, MEMORY)
    settings = json.dumps(pm_settings(MEMORY), separators=(",", ":"))
    assert command == (
        f"cd {repo} && claude --bg --name pm-acme --agent coven:project-manager "
        f"--permission-mode bypassPermissions --settings {shlex.quote(settings)} "
        f'"$(cat {brief})"'
    )


def test_launch_quotes_paths_with_spaces(tmp_path: Path) -> None:
    repo = tmp_path / "my repo's dir"
    brief = tmp_path / "a brief.md"
    brief.write_text("brief\n")
    memory = tmp_path / "memory store"
    command = launch_command("acme", repo, brief, memory)
    settings = json.dumps(pm_settings(memory), separators=(",", ":"))
    assert command.startswith(f"cd {shlex.quote(str(repo))} && claude --bg ")
    assert f" --settings {shlex.quote(settings)} " in command
    assert command.endswith(f' "$(cat {shlex.quote(str(brief))})"')


@pytest.mark.parametrize("code", ["", "Acme", "-x", "a b", "a;rm x", "../x", "a/b", "é"])
def test_launch_rejects_bad_code(code: str, tmp_path: Path) -> None:
    (tmp_path / "b.md").write_text("brief\n")
    with pytest.raises(CovenError, match="project code"):
        launch_command(code, tmp_path, tmp_path / "b.md", MEMORY)


@pytest.mark.parametrize("text", ["-x", "--help\nmore", "-"])
def test_launch_refuses_a_brief_that_claude_would_read_as_a_flag(tmp_path: Path, text: str) -> None:
    brief = tmp_path / "b.md"
    brief.write_text(text)
    with pytest.raises(CovenError, match=r"^the brief starts with '-'") as raised:
        launch_command("acme", tmp_path, brief, MEMORY)
    assert "\n" not in str(raised.value)


@pytest.mark.parametrize("text", [" -x", "\n-x", "TASK -x", ""])
def test_launch_accepts_a_brief_that_does_not_start_with_a_dash(tmp_path: Path, text: str) -> None:
    brief = tmp_path / "b.md"
    brief.write_text(text)
    assert launch_command("acme", tmp_path, brief, MEMORY).startswith("cd ")


def test_launch_refuses_an_unreadable_brief_in_one_line(tmp_path: Path) -> None:
    with pytest.raises(CovenError, match="brief") as raised:
        launch_command("acme", tmp_path, tmp_path / "gone.md", MEMORY)
    assert "\n" not in str(raised.value)


def test_refuse_option_like_names_what_it_checked() -> None:
    refuse_option_like("fine", "prompt")
    with pytest.raises(CovenError, match=r"^the prompt starts with '-'"):
        refuse_option_like("-x", "prompt")


def test_the_printed_command_runs_the_launch_line_through_a_shell(tmp_path: Path) -> None:
    """A fake `claude` receives the exact arguments, with the brief text as the last one."""
    repo = tmp_path / "my repo"
    repo.mkdir()
    brief = tmp_path / "a brief.md"
    brief.write_text("TASK acme/1: it's $(not run) `either`\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    seen = tmp_path / "seen.json"
    fake = bin_dir / "claude"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        f"import json, os, sys\njson.dump([os.getcwd(), sys.argv[1:]], open({str(seen)!r}, 'w'))\n"
    )
    fake.chmod(0o755)
    memory = tmp_path / "mem's"  # a quote must survive the shell inside the settings JSON
    command = launch_command("acme", repo, brief, memory)
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin"}
    subprocess.run(["bash", "-c", command], env=env, check=True)
    cwd, argv = json.loads(seen.read_text())
    assert cwd == str(repo)
    assert argv[:6] == [
        "--bg",
        "--name",
        "pm-acme",
        "--agent",
        "coven:project-manager",
        "--permission-mode",
    ]
    assert argv[6] == "bypassPermissions"
    assert json.loads(argv[argv.index("--settings") + 1]) == pm_settings(memory)
    assert argv[-1] == "TASK acme/1: it's $(not run) `either`"


# --- the command ------------------------------------------------------------------------------


@pytest.fixture
def launch_files(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    brief = tmp_path / "pm-acme.md"
    brief.write_text("brief\n")
    return repo, brief


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    code = cli.main(["pm", "launch", *argv])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_launch_prints_the_command_and_starts_nothing(
    launch_files: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, brief = launch_files
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    marker = tmp_path / "ran"
    (bin_dir / "claude").write_text(f"#!/bin/sh\ntouch {marker}\n")
    (bin_dir / "claude").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    code, out, err = run(capsys, "acme", "--repo", str(repo), "--brief", str(brief))
    assert (code, err) == (0, "")
    memory = Path.home() / "coven-memory"  # the default memory path under the test home
    assert out == launch_command("acme", repo.resolve(), brief.resolve(), memory) + "\n"
    assert not marker.exists()


def test_launch_uses_the_configured_memory_path(
    launch_files: tuple[Path, Path], capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    repo, brief = launch_files
    config = Path.home() / ".config" / "coven" / "config.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({"memory_path": str(tmp_path / "elsewhere")}))
    _, out, _ = run(capsys, "acme", "--repo", str(repo), "--brief", str(brief))
    assert (
        out
        == launch_command("acme", repo.resolve(), brief.resolve(), tmp_path / "elsewhere") + "\n"
    )


def test_launch_refuses_a_missing_repo(
    launch_files: tuple[Path, Path], capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    _, brief = launch_files
    code, out, err = run(capsys, "acme", "--repo", str(tmp_path / "gone"), "--brief", str(brief))
    assert (code, out) == (2, "")
    assert err.startswith("coven: ")
    assert "repo" in err
    assert err.count("\n") == 1


def test_launch_refuses_a_missing_brief(
    launch_files: tuple[Path, Path], capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    repo, _ = launch_files
    code, out, err = run(capsys, "acme", "--repo", str(repo), "--brief", str(tmp_path / "gone.md"))
    assert (code, out) == (2, "")
    assert "brief" in err
    assert err.count("\n") == 1


def test_launch_refuses_a_bad_code(
    launch_files: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    repo, brief = launch_files
    code, out, _ = run(capsys, "--repo", str(repo), "--brief", str(brief), "--", "Bad;code")
    assert (code, out) == (2, "")


def test_launch_requires_repo_and_brief(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = run(capsys, "acme")
    assert (code, out) == (2, "")
    assert "--repo" in err
