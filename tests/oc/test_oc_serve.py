"""`coven opencode serve`: start, stop and status against a fake `opencode` that binds 127.0.0.1."""

import json
import os
import re
import socket
import stat
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from doctor_kit import Good

from coven import cli
from coven.opencode import serve
from coven.opencode.profile import load_profile

PASSWORD_SHAPE = re.compile(r"[0-9a-f]{64}")
WAIT = 10.0
# The server's answer to every route the fake does not know.
SESSIONS = {"data": [], "cursor": None}

# The stand-in for the 2.3.2 binary. `routes` maps a request path to the JSON it answers with,
# once the basic-auth password matches. `mute` makes it listen to nothing and print nothing.
FAKE_OPENCODE = """#!%(python)s
import base64
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

CONFIG = json.loads(%(config)r)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        token = base64.b64encode(("opencode:" + os.environ["OPENCODE_SERVER_PASSWORD"]).encode())
        if self.headers.get("Authorization") != "Basic " + token.decode():
            self.send_response(401)
            self.end_headers()
            return
        body = CONFIG["routes"].get(self.path)
        self.send_response(404 if body is None else 200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())

    def log_message(self, *args):
        pass


def main(argv):
    if argv[:1] == ["--version"]:
        print("opencode v2.3.2")
        return 0
    if CONFIG.get("mute"):
        time.sleep(600)
        return 0
    port = int(argv[argv.index("--port") + 1])
    server = HTTPServer(("127.0.0.1", port), Handler)
    print("server listening on http://127.0.0.1:%%d" %% server.server_address[1], flush=True)
    server.serve_forever()


sys.exit(main(sys.argv[1:]))
"""


class Procs:
    """Every process a test started, killed in teardown."""

    def __init__(self) -> None:
        self.started: list[subprocess.Popen[bytes]] = []

    def spawn(self, *args: Any, **named: Any) -> subprocess.Popen[bytes]:
        process = subprocess.Popen(*args, **named)
        self.started.append(process)
        return process


@pytest.fixture
def procs() -> Iterator[Procs]:
    tracked = Procs()
    yield tracked
    for process in tracked.started:
        process.kill()
        process.wait()


@pytest.fixture(autouse=True)
def interpreter_is_the_server(monkeypatch: pytest.MonkeyPatch) -> None:
    """The fake is a script, so the process behind it is the interpreter."""
    exe = Path(os.path.realpath(sys.executable))
    monkeypatch.setattr(serve, "server_exe", lambda _layout: exe)


def install_fake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **config: Any) -> None:
    script = tmp_path / "fake-server" / "opencode"
    script.parent.mkdir()
    shown = {"routes": {"/api/session": SESSIONS}, **config}
    script.write_text(FAKE_OPENCODE % {"python": sys.executable, "config": json.dumps(shown)})
    script.chmod(0o755)
    monkeypatch.setenv("COVEN_OPENCODE_BIN", str(script))


@pytest.fixture
def fake(good: Good, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Good:
    install_fake(tmp_path, monkeypatch)
    return good


def start(
    good: Good, procs: Procs, port: int | None = None, wait_s: float = WAIT
) -> serve.ServerInfo:
    profile = load_profile(good.layout)
    assert profile is not None
    return serve.start(good.layout, profile, port, spawn=procs.spawn, wait_s=wait_s)


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


def test_start_times_out_with_log_path(
    good: Good, procs: Procs, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_fake(tmp_path, monkeypatch, mute=True)

    with pytest.raises(serve.ServeError) as caught:
        start(good, procs, wait_s=0.5)

    assert str(serve.files(good.layout).log) in str(caught.value)
    assert not serve.files(good.layout).pid.exists()
    assert procs.started[0].wait(timeout=5) != 0


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
