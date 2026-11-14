"""`coven pm resume`: prepares the resume prompt file and prints the command to run."""

import json
import os
import shlex
import stat
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import pytest
from pm.fixtures import (
    GM,
    PM,
    PmEnv,
    TranscriptBuilder,
    agents_row,
    at,
    pm_env,
    session_id,
)

from coven import paths
from coven.config import load_config
from coven.pm.profile import launch_command, pm_settings, session_flags

RESUME = "resume"
CODE = "acme-1"


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[PmEnv]:
    yield from pm_env(tmp_path, monkeypatch)


def repo(env: PmEnv, name: str = "repo-1") -> Path:
    """An existing working directory for the PM."""
    folder = env.work / name
    folder.mkdir(exist_ok=True)
    return folder


def named(builder: TranscriptBuilder | None = None, name: str = PM) -> TranscriptBuilder:
    """A transcript whose session carries the `agent-name` entry `name`."""
    base = builder or TranscriptBuilder().prompt(f"Brief for {PM}\nGM session: {GM}\n", at("09:00"))
    return base.agent_name(name)


def stopped_pm(env: PmEnv, state: str = "stopped", **fields: object) -> Path:
    """A PM row in `state` with a transcript that names it; returns its working directory."""
    folder = repo(env)
    env.set_rows(agents_row(PM, 1, state, cwd=str(folder), **fields))
    env.transcript(named())
    return folder


def resume(
    env: PmEnv, capsys: pytest.CaptureFixture[str], *argv: str
) -> tuple[int, list[str], str]:
    code, out, err = env.run(capsys, RESUME, CODE, "--gm", GM, *argv)
    return code, out.splitlines(), err


def prompt_file() -> Path:
    return paths.briefs_dir() / f"pm-{CODE}.prompt.md"


def expected_command(folder: Path, session: str = session_id(1)) -> str:
    flags = session_flags(CODE, load_config().memory_path)
    return f'cd {folder} && claude --bg --resume {session} {flags} "$(cat {prompt_file()})"'


def test_prints_the_exact_command_and_writes_the_prompt(
    env: PmEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = stopped_pm(env)
    reply = env.work / "reply.md"
    reply.write_text("ANSWER acme-1/1: use the staging table\n")
    other = env.work / "other.md"
    other.write_text("TASK acme-1/2: add the index")
    code, out, err = resume(env, capsys, "--add", str(reply), "--add", str(other))
    assert (code, err) == (0, "")
    assert out == [f"prompt: {prompt_file()}", expected_command(folder)]
    assert prompt_file().read_text() == (
        f"RESUMED by {GM}\nGM session: {GM}\n"
        "\nANSWER acme-1/1: use the staging table\n"
        "\nTASK acme-1/2: add the index\n"
    )
    assert stat.S_IMODE(prompt_file().stat().st_mode) == 0o600
    assert stat.S_IMODE(prompt_file().parent.stat().st_mode) == 0o700
    assert env.agents_runs == 1  # the one listing: nothing else ran, so nothing was started


def settings_of(command: str) -> object:
    """The parsed JSON after `--settings` in a printed command, read the way a shell would."""
    words = shlex.split(command)
    return json.loads(words[words.index("--settings") + 1])


def test_resume_uses_same_profile(
    env: PmEnv, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    folder = stopped_pm(env)
    _, out, _ = resume(env, capsys)
    brief = tmp_path / "b.md"
    brief.write_text("brief\n")
    launch = launch_command(CODE, folder, brief, load_config().memory_path)
    assert settings_of(out[1]) == settings_of(launch) == pm_settings(load_config().memory_path)
    for flag in ("--name pm-acme-1", "--agent coven:project-manager"):
        assert flag in out[1]
        assert flag in launch
    assert "--permission-mode bypassPermissions" in out[1]


def test_a_dash_led_add_file_never_leads_the_prompt(
    env: PmEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    stopped_pm(env)
    add = env.work / "add.md"
    add.write_text("-x: not a flag here\n")
    code, _, _ = resume(env, capsys, "--add", str(add))
    assert code == 0
    assert prompt_file().read_text().startswith("RESUMED by ")


def test_a_second_run_replaces_the_prompt_file(
    env: PmEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    stopped_pm(env)
    prompt_file().parent.mkdir(parents=True)
    prompt_file().write_text("old " * 1000)
    prompt_file().chmod(0o644)
    assert resume(env, capsys)[0] == 0
    assert prompt_file().read_text().startswith("RESUMED by ")
    assert stat.S_IMODE(prompt_file().stat().st_mode) == 0o600


@pytest.mark.parametrize("state", ["idle", "working", "blocked"])
def test_live_row_refuses(env: PmEnv, capsys: pytest.CaptureFixture[str], state: str) -> None:
    env.transcript(named())
    env.process(4242)
    env.set_rows(agents_row(PM, 1, state, cwd=str(repo(env)), pid=4242))
    code, out, err = resume(env, capsys)
    assert (code, out) == (2, [])
    assert f"{PM} is running: stop it first: claude stop 00000001" in err
    assert not prompt_file().exists()


def test_stale_row_may_be_resumed(env: PmEnv, capsys: pytest.CaptureFixture[str]) -> None:
    folder = stopped_pm(env, "working", pid=4242)  # no such process in the fake /proc
    code, out, _ = resume(env, capsys)
    assert code == 0
    assert out[1] == expected_command(folder)


def test_a_done_row_with_a_live_process_refuses(
    env: PmEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    env.transcript(named())
    env.process(4242)
    env.set_rows(agents_row(PM, 1, "done", cwd=str(repo(env)), pid=4242))
    code, out, err = resume(env, capsys)
    assert (code, out) == (2, [])
    assert f"{PM} is running: stop it first: claude stop 00000001" in err
    assert not prompt_file().exists()


def test_a_done_row_with_a_dead_process_may_be_resumed(
    env: PmEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = stopped_pm(env, "done", pid=4242)  # no such process in the fake /proc
    code, out, _ = resume(env, capsys)
    assert code == 0
    assert out[1] == expected_command(folder)


def test_duplicate_refuses(env: PmEnv, capsys: pytest.CaptureFixture[str]) -> None:
    folder = repo(env)
    env.process(1)
    env.process(2)
    env.set_rows(
        agents_row(PM, 1, "idle", cwd=str(folder), pid=1),
        agents_row(PM, 2, "idle", cwd=str(folder), pid=2),
    )
    env.transcript(named(), 2)
    code, out, err = resume(env, capsys)
    assert (code, out) == (2, [])
    assert err == f"coven: {PM} is a duplicate: stop all but the newest, then check its state\n"
    assert not prompt_file().exists()


def test_gone_row_needs_session(env: PmEnv, capsys: pytest.CaptureFixture[str]) -> None:
    env.set_rows()
    code, out, err = resume(env, capsys)
    assert (code, out) == (2, [])
    assert "--session" in err


def test_gone_row_takes_the_directory_from_the_transcript(
    env: PmEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    env.set_rows()
    folder = repo(env)
    builder = TranscriptBuilder().agent_name(PM).prompt("Brief\n", at("09:00"))
    builder.lines = [line.replace("/work/repo-1", str(folder)) for line in builder.lines]
    env.transcript(builder)
    code, out, err = resume(env, capsys, "--session", session_id(1))
    assert (code, err) == (0, "")
    assert out[1] == expected_command(folder)


@pytest.mark.parametrize("code", ["../x", "*", "-x", "Acme", "a b"])
def test_bad_code_refuses_before_anything_runs(
    env: PmEnv, capsys: pytest.CaptureFixture[str], code: str
) -> None:
    stopped_pm(env)
    status, out, err = env.run(capsys, RESUME, "--gm", GM, "--", code)
    assert (status, out) == (2, "")
    assert "PM code" in err
    assert env.agents_runs == 0
    assert not prompt_file().exists()


@pytest.mark.parametrize("name", ["-gm", "gm;x", "gm x", ".gm"])
def test_bad_gm_name_is_refused_by_the_parser(
    env: PmEnv, capsys: pytest.CaptureFixture[str], name: str
) -> None:
    code, out, _ = env.run(capsys, RESUME, CODE, f"--gm={name}")
    assert (code, out) == (2, "")
    assert env.agents_runs == 0


@pytest.mark.parametrize("session", ["../x", "*", "-x", "00000000-0000-4000-8000-00000000000A", ""])
def test_bad_session_refuses(env: PmEnv, capsys: pytest.CaptureFixture[str], session: str) -> None:
    env.set_rows()
    code, out, err = resume(env, capsys, f"--session={session}")
    assert (code, out) == (2, [])
    assert "session" in err
    assert not prompt_file().exists()


def test_a_row_with_a_bad_session_id_refuses(
    env: PmEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = repo(env)
    env.set_rows(agents_row(PM, 1, "stopped", cwd=str(folder), sessionId="../../x"))
    code, out, err = resume(env, capsys)
    assert (code, out) == (2, [])
    assert "session" in err


def test_a_row_without_a_session_id_asks_for_one(
    env: PmEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    row = agents_row(PM, 1, "stopped", cwd=str(repo(env)))
    del row["sessionId"]
    env.set_rows(row)
    code, _, err = resume(env, capsys)
    assert code == 2
    assert "--session" in err


def test_a_missing_transcript_refuses(env: PmEnv, capsys: pytest.CaptureFixture[str]) -> None:
    env.set_rows(agents_row(PM, 1, "stopped", cwd=str(repo(env))))
    code, out, err = resume(env, capsys)
    assert (code, out) == (2, [])
    assert "transcript" in err


def test_wrong_agent_name_refuses(env: PmEnv, capsys: pytest.CaptureFixture[str]) -> None:
    env.set_rows(agents_row(PM, 1, "stopped", cwd=str(repo(env))))
    env.transcript(named(name="pm-other"))
    code, out, err = resume(env, capsys)
    assert (code, out) == (2, [])
    assert f"{PM}" in err
    assert not prompt_file().exists()


def test_missing_cwd_refuses(env: PmEnv, capsys: pytest.CaptureFixture[str]) -> None:
    env.set_rows(agents_row(PM, 1, "stopped", cwd=str(env.work / "gone")))
    env.transcript(named())
    code, out, err = resume(env, capsys)
    assert (code, out) == (2, [])
    assert "directory" in err
    assert not prompt_file().exists()


def test_a_cwd_with_control_characters_refuses(
    env: PmEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = env.work / "bad\x1b[31mdir"
    folder.mkdir()
    env.set_rows(agents_row(PM, 1, "stopped", cwd=str(folder)))
    env.transcript(named())
    code, out, err = resume(env, capsys)
    assert (code, out) == (2, [])
    assert "\x1b" not in err
    assert "directory" in err


def test_cwd_with_spaces_is_quoted(env: PmEnv, capsys: pytest.CaptureFixture[str]) -> None:
    folder = env.work / "my repo's dir"
    folder.mkdir()
    env.set_rows(agents_row(PM, 1, "stopped", cwd=str(folder)))
    env.transcript(named())
    code, out, _ = resume(env, capsys)
    assert code == 0
    quoted = "'" + str(folder).replace("'", "'\"'\"'") + "'"
    assert out[1].startswith(f"cd {quoted} && claude --bg --resume ")


def test_the_prompt_path_is_quoted_in_the_command(
    env: PmEnv, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(env.work / "spaced cache"))
    stopped_pm(env)
    code, out, _ = resume(env, capsys)
    assert code == 0
    assert out[1].endswith(f""" "$(cat '{prompt_file()}')\"""")


def test_a_missing_agent_file_refuses(
    env: PmEnv, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    stopped_pm(env)
    monkeypatch.setattr("coven.paths.root", lambda: env.work)
    code, out, err = resume(env, capsys)
    assert (code, out) == (2, [])
    assert "agent file" in err
    assert str(env.work) not in err
    assert not prompt_file().exists()


def test_a_session_that_differs_from_the_row_refuses(
    env: PmEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    other = env.work / "repo-2"
    other.mkdir()
    env.set_rows(agents_row(PM, 1, "stopped", cwd=str(other)))
    env.transcript(named())
    env.transcript(named(), 2)
    code, out, err = resume(env, capsys, f"--session={session_id(2)}")
    assert (code, out) == (2, [])
    assert "--session differs" in err
    assert not prompt_file().exists()


def test_the_row_s_own_session_may_be_given(env: PmEnv, capsys: pytest.CaptureFixture[str]) -> None:
    folder = stopped_pm(env)
    code, out, _ = resume(env, capsys, f"--session={session_id(1)}")
    assert code == 0
    assert out[1] == expected_command(folder)


def test_a_working_row_without_a_pid_refuses_even_with_an_old_transcript(
    env: PmEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    env.set_rows(agents_row(PM, 1, "working", cwd=str(repo(env))))  # no pid: nothing says it died
    old = TranscriptBuilder().agent_name(PM).prompt("Brief\n", at("09:00") - timedelta(days=2))
    env.transcript(old)
    code, out, err = resume(env, capsys)
    assert (code, out) == (2, [])
    assert f"{PM} is running: stop it first: claude stop 00000001" in err
    assert not prompt_file().exists()


def test_an_existing_briefs_folder_is_made_private(
    env: PmEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    stopped_pm(env)
    prompt_file().parent.mkdir(parents=True, mode=0o755)
    prompt_file().parent.chmod(0o755)
    assert resume(env, capsys)[0] == 0
    assert stat.S_IMODE(prompt_file().parent.stat().st_mode) == 0o700


def test_a_briefs_folder_of_another_user_refuses(
    env: PmEnv, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    stopped_pm(env)
    prompt_file().parent.mkdir(parents=True)
    someone_else = os.getuid() + 1
    monkeypatch.setattr("coven.pm.resume.os.getuid", lambda: someone_else)
    code, out, err = resume(env, capsys)
    assert (code, out) == (2, [])
    assert "briefs folder" in err
    assert not prompt_file().exists()


def test_a_symlink_at_the_prompt_path_refuses_and_its_target_is_untouched(
    env: PmEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    stopped_pm(env)
    target = env.work / "target.txt"
    target.write_text("keep")
    prompt_file().parent.mkdir(parents=True, mode=0o700)
    prompt_file().symlink_to(target)
    code, out, err = resume(env, capsys)
    assert (code, out) == (2, [])
    assert err == "coven: the prompt file is a symbolic link: remove it\n"
    assert target.read_text() == "keep"
    assert prompt_file().is_symlink()


def test_an_add_file_that_is_not_utf8_refuses(
    env: PmEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    stopped_pm(env)
    good = env.work / "good.md"
    good.write_text("ANSWER acme-1/1: yes\n")
    bad = env.work / "bad.md"
    bad.write_bytes(b"\xff\xfe")
    code, out, err = resume(env, capsys, "--add", str(good), "--add", str(bad))
    assert (code, out) == (2, [])
    assert "--add file 2 is not UTF-8" in err
    assert not prompt_file().exists()


def test_a_missing_add_file_refuses_before_writing(
    env: PmEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    stopped_pm(env)
    code, out, err = resume(env, capsys, "--add", str(env.work / "nope.md"))
    assert (code, out) == (2, [])
    assert "nope.md" in err
    assert not prompt_file().exists()


def test_a_bad_config_refuses(env: PmEnv, capsys: pytest.CaptureFixture[str]) -> None:
    stopped_pm(env)
    config = Path.home() / ".config" / "coven"
    config.mkdir(parents=True)
    (config / "config.json").write_text("{not json")
    code, out, err = resume(env, capsys)
    assert code != 0
    assert out == []
    assert "config.json" in err


def test_a_failing_agents_listing_is_one_line(
    env: PmEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    env.fail_agents()
    code, out, err = resume(env, capsys)
    assert (code, out) == (2, [])
    assert err.startswith("coven: ")
    assert len(err.splitlines()) == 1


def test_a_live_row_with_a_drifted_format_still_refuses(
    env: PmEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    env.process(4242)
    env.set_rows(agents_row(PM, 1, "idle", cwd=str(repo(env)), pid=4242))
    drifted = named()
    for number in range(4):  # several assistant turns, no turn end: the format reads as drifted
        drifted.text(f"thinking {number}", at("09:10"))
    env.transcript(drifted)
    code, out, err = resume(env, capsys)
    assert (code, out) == (2, [])
    assert "stop it first" in err


def test_the_prompt_file_read_back_answers_the_wait_and_names_the_new_gm(
    env: PmEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = repo(env)
    asked = (
        named()
        .peer(GM, "TASK acme-1/1: start", at("09:30"))
        .send(GM, "QUESTION acme-1/2: which table?", at("10:00"))
        .turn_end(at("10:01"))
    )
    env.transcript(asked)
    env.set_rows(agents_row(PM, 1, "stopped", cwd=str(folder)))
    reply = env.work / "reply.md"
    reply.write_text("ANSWER acme-1/2: go ahead\n")
    code, _, _ = env.run(capsys, RESUME, CODE, "--gm", "gm-new", "--add", str(reply))
    assert code == 0
    env.transcript(asked.prompt(prompt_file().read_text(), at("11:00")).turn_end(at("11:01")))
    env.set_rows(agents_row(PM, 1, "idle", cwd=str(folder)))
    load_config().memory_path.mkdir(parents=True)  # health needs a store, an empty one is enough
    _, status, _ = env.run(capsys, "status", "--gm", "gm-new")
    assert "waits on GM" not in status
    assert "  last in:  11:00 ANSWER acme-1/2: go ahead  (from gm-new)" in status.splitlines()
    assert env.run(capsys, "health", "--gm", "gm-new") == (0, "ok\n", "")
