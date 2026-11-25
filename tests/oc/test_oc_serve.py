"""`coven opencode serve`: start, stop and status against a fake `opencode` that binds 127.0.0.1."""

import os
import re
import signal
import socket
import stat
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest
from doctor_kit import Good
from serve_kit import Procs, install_fake, start

from coven import cli
from coven.opencode import serve
from coven.opencode.profile import OpencodeError

PASSWORD_SHAPE = re.compile(r"[0-9a-f]{64}")

pytestmark = pytest.mark.usefixtures("interpreter_is_the_server")


@pytest.fixture
def fake(good: Good, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Good:
    install_fake(tmp_path, monkeypatch)
    return good


def test_start_writes_password_port_pid_and_answers(fake: Good, procs: Procs) -> None:
    info = start(fake, procs)

    files = serve.files(fake.layout)
    password = files.password.read_text().strip()
    assert PASSWORD_SHAPE.fullmatch(password)
    assert stat.S_IMODE(files.password.stat().st_mode) == 0o600
    assert int(files.pid.read_text()) == procs.started[0].pid == info.pid
    assert int(files.port.read_text()) == info.port
    assert f"server listening on http://127.0.0.1:{info.port}" in files.log.read_text()
    assert serve.running(fake.layout) == info


def test_password_is_kept_across_starts(fake: Good, procs: Procs) -> None:
    start(fake, procs)
    first = serve.files(fake.layout).password.read_text()
    assert serve.stop(fake.layout, grace_s=5.0)

    start(fake, procs)

    assert serve.files(fake.layout).password.read_text() == first


def test_second_start_is_refused(fake: Good, procs: Procs) -> None:
    info = start(fake, procs)

    with pytest.raises(serve.ServeError, match=f"a server is already running on port {info.port}"):
        start(fake, procs)
    assert len(procs.started) == 1


def test_stale_pid_of_other_process_is_replaced(fake: Good, procs: Procs) -> None:
    other = subprocess.Popen(["sleep", "60"])
    procs.started.append(other)
    files = serve.files(fake.layout)
    files.pid.parent.mkdir(parents=True)
    files.pid.write_text(f"{other.pid}\n")
    files.port.write_text("1\n")
    assert serve.running(fake.layout) is None

    info = start(fake, procs)

    assert info.pid == procs.started[1].pid != other.pid
    assert int(files.pid.read_text()) == info.pid
    assert other.poll() is None


def test_stop_kills_and_removes_pid_and_port_keeps_password(fake: Good, procs: Procs) -> None:
    info = start(fake, procs)
    files = serve.files(fake.layout)

    assert serve.stop(fake.layout, grace_s=5.0) is True

    assert procs.started[0].wait(timeout=5) != 0
    assert not files.pid.exists()
    assert not files.port.exists()
    assert files.password.is_file()
    assert serve.running(fake.layout) is None
    assert info.pid == procs.started[0].pid
    assert serve.stop(fake.layout) is False


def no_sigkill(pid: int, signum: int) -> None:
    """A kill that never delivers SIGKILL, so a server that ignores SIGTERM does not stop."""
    if signum != signal.SIGKILL:
        os.kill(pid, signum)


def test_stop_keeps_the_files_of_a_server_that_does_not_stop(
    good: Good, procs: Procs, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake(tmp_path, monkeypatch, ignore_term=True)
    info = start(good, procs)
    monkeypatch.setattr(serve, "KILL_WAIT", 0.2)

    with pytest.raises(serve.ServeError, match=f"process {info.pid} did not stop"):
        serve.stop(good.layout, grace_s=0.2, kill=no_sigkill)

    files = serve.files(good.layout)
    assert files.pid.is_file()
    assert files.port.is_file()
    assert serve.running(good.layout) == info


def test_the_stop_command_reports_a_server_that_does_not_stop(
    good: Good,
    procs: Procs,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    install_fake(tmp_path, monkeypatch, ignore_term=True)
    info = start(good, procs)
    monkeypatch.setattr(serve, "KILL_WAIT", 0.2)
    assert serve.stop.__kwdefaults__ is not None
    monkeypatch.setitem(serve.stop.__kwdefaults__, "kill", no_sigkill)
    monkeypatch.setitem(serve.stop.__kwdefaults__, "grace_s", 0.2)

    assert cli.main(["opencode", "serve", "stop"]) == 1

    assert f"process {info.pid} did not stop" in capsys.readouterr().err
    assert serve.running(good.layout) == info


def test_a_second_start_while_one_is_starting_is_refused(fake: Good, procs: Procs) -> None:
    lock = serve.files(fake.layout).lock
    lock.parent.mkdir(parents=True)
    lock.write_text(f"{os.getpid()}\n")

    with pytest.raises(serve.ServeError, match="a server is starting"):
        start(fake, procs)

    assert procs.started == []
    assert lock.read_text() == f"{os.getpid()}\n"


def test_a_stale_start_lock_is_replaced_and_released(fake: Good, procs: Procs) -> None:
    gone = subprocess.Popen(["true"])
    gone.wait()
    lock = serve.files(fake.layout).lock
    lock.parent.mkdir(parents=True)
    lock.write_text(f"{gone.pid}\n")

    info = start(fake, procs)

    assert serve.running(fake.layout) == info
    assert not lock.exists()


def _pidless_lock(layout: Any, age_s: float) -> Path:
    """A start lock with no pid in it (a crash between its creation and the pid write)."""
    lock = serve.files(layout).lock
    lock.parent.mkdir(parents=True)
    lock.write_text("")
    then = time.time() - age_s
    os.utime(lock, (then, then))
    return lock


def test_a_pidless_start_lock_older_than_the_wait_is_replaced(fake: Good, procs: Procs) -> None:
    lock = _pidless_lock(fake.layout, age_s=60.0)

    info = start(fake, procs, wait_s=5.0)

    assert serve.running(fake.layout) == info
    assert not lock.exists()


def test_a_fresh_pidless_start_lock_is_refused_and_named(fake: Good, procs: Procs) -> None:
    lock = _pidless_lock(fake.layout, age_s=0.0)

    with pytest.raises(serve.ServeError, match=f"a server is starting.*{re.escape(str(lock))}"):
        start(fake, procs, wait_s=5.0)

    assert procs.started == []
    assert lock.exists()


def test_a_live_holders_lock_is_never_stale_by_age(fake: Good, procs: Procs) -> None:
    lock = serve.files(fake.layout).lock
    lock.parent.mkdir(parents=True)
    lock.write_text(f"{os.getpid()}\n")
    then = time.time() - 3600
    os.utime(lock, (then, then))

    with pytest.raises(serve.ServeError, match=f"a server is starting.*{re.escape(str(lock))}"):
        start(fake, procs, wait_s=5.0)

    assert procs.started == []


def test_the_server_env_has_no_username(
    fake: Good, procs: Procs, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENCODE_SERVER_USERNAME", "someone")

    start(fake, procs)

    env = procs.envs[0]
    assert "OPENCODE_SERVER_USERNAME" not in env
    assert PASSWORD_SHAPE.fullmatch(env["OPENCODE_SERVER_PASSWORD"])


def test_read_password_reads_the_saved_password(fake: Good, procs: Procs) -> None:
    start(fake, procs)

    saved = serve.files(fake.layout).password.read_text().strip()
    assert serve.read_password(fake.layout) == saved


def test_read_password_names_a_missing_password(fake: Good) -> None:
    with pytest.raises(OpencodeError, match="cannot read the server password"):
        serve.read_password(fake.layout)


def test_start_times_out_with_log_path(
    good: Good, procs: Procs, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake(tmp_path, monkeypatch, mute=True)

    with pytest.raises(serve.ServeError) as caught:
        start(good, procs, wait_s=0.5)

    assert str(serve.files(good.layout).log) in str(caught.value)
    assert not serve.files(good.layout).pid.exists()
    assert not serve.files(good.layout).lock.exists()
    assert procs.started[0].wait(timeout=5) != 0


def test_a_server_that_exits_at_once_is_named_with_its_log(
    good: Good, procs: Procs, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake(tmp_path, monkeypatch, exit_at_once=True)
    log = serve.files(good.layout).log

    with pytest.raises(serve.ServeError, match=f"the server exited before it listened; see {log}"):
        start(good, procs)

    assert not serve.files(good.layout).lock.exists()


def test_explicit_port_in_use_is_named(fake: Good, procs: Procs) -> None:
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        port = taken.getsockname()[1]

        with pytest.raises(serve.ServeError, match=f"port {port} in use"):
            start(fake, procs, port=port)
    assert procs.started == []


def test_status_says_stopped_without_a_server(fake: Good) -> None:
    assert serve.status(fake.layout) == ["stopped"]


def pm_session(code: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": f"ses_{code}",
        "title": f"pm-{code}",
        "metadata": {"coven": {"role": "pm", "code": code}},
        **extra,
    }


def test_status_lists_pm_sessions(
    good: Good, procs: Procs, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sessions = [
        {"id": "ses_gm", "metadata": {"coven": {"role": "gm", "code": "gm"}}},
        pm_session("x1"),
        pm_session("x2", parentID="ses_x1"),
        {"id": "ses_plain", "title": "chat"},
    ]
    routes = {
        "/api/session": {"data": sessions, "cursor": None},
        "/api/session/active": {"data": [{"id": "ses_x1"}]},
        "/api/session/ses_x1/permission": {"data": []},
    }
    install_fake(tmp_path, monkeypatch, routes=routes)
    info = start(good, procs)

    lines = serve.status(good.layout)

    assert lines[0] == f"running pid {info.pid} port {info.port}"
    assert len(lines) == 2
    assert "pm-x1" in lines[1]
    assert "busy" in lines[1]
    assert "ASK WAITING" not in lines[1]


def test_status_shows_a_waiting_ask(
    good: Good, procs: Procs, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    routes = {
        "/api/session": {"data": [pm_session("x1", outcome="succeeded")], "cursor": None},
        "/api/session/active": {"data": []},
        "/api/session/ses_x1/permission": {"data": [{"id": "per_42"}]},
    }
    install_fake(tmp_path, monkeypatch, routes=routes)
    start(good, procs)

    lines = serve.status(good.layout)

    assert len(lines) == 2
    assert "idle" in lines[1]
    assert "ASK WAITING (bug)" in lines[1]
    assert "per_42" in lines[1]


@pytest.mark.parametrize(
    "case",
    [({"requestID": "req_7"}, "req_7"), ({"tool": "bash"}, "unknown id")],
)
def test_status_names_an_ask_by_its_request_id_or_says_it_has_none(
    good: Good,
    procs: Procs,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: tuple[dict[str, str], str],
) -> None:
    ask, shown = case
    routes = {
        "/api/session": {"data": [pm_session("x1")], "cursor": None},
        "/api/session/active": {"data": []},
        "/api/session/ses_x1/permission": {"data": [ask]},
    }
    install_fake(tmp_path, monkeypatch, routes=routes)
    start(good, procs)

    lines = serve.status(good.layout)

    assert f"ASK WAITING (bug) {shown}" in lines[1]


def test_status_warns_when_the_session_list_has_another_page(
    good: Good, procs: Procs, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    routes = {
        "/api/session": {"data": [pm_session("x1")], "cursor": "next-page"},
        "/api/session/active": {"data": []},
        "/api/session/ses_x1/permission": {"data": []},
    }
    install_fake(tmp_path, monkeypatch, routes=routes)
    start(good, procs)

    lines = serve.status(good.layout)

    assert len(lines) == 3
    assert "pm-x1" in lines[1]
    assert lines[2] == "more sessions exist; this list may be incomplete"


def test_run_refuses_while_server_runs(
    fake: Good, procs: Procs, capsys: pytest.CaptureFixture[str]
) -> None:
    start(fake, procs)

    assert cli.main(["opencode", "run"]) == 1

    assert "a coven server is running; use `coven opencode gm`" in capsys.readouterr().err


def test_the_command_starts_reports_and_stops_a_server(
    fake: Good, procs: Procs, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # The command spawns with the default; swap it so teardown can reap what it starts.
    assert serve.start.__kwdefaults__ is not None
    monkeypatch.setitem(serve.start.__kwdefaults__, "spawn", procs.spawn)
    try:
        assert cli.main(["opencode", "serve", "start"]) == 0
        info = serve.running(fake.layout)
        assert info is not None
        assert capsys.readouterr().out == f"running pid {info.pid} port {info.port}\n"

        assert cli.main(["opencode", "serve", "status"]) == 0
        assert capsys.readouterr().out.startswith(f"running pid {info.pid} port {info.port}\n")
    finally:
        assert cli.main(["opencode", "serve", "stop"]) == 0

    assert capsys.readouterr().out == "stopped\n"
    assert serve.running(fake.layout) is None
    assert cli.main(["opencode", "serve", "stop"]) == 0
    assert capsys.readouterr().out == "no server was running\n"
