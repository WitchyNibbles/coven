"""`coven opencode gm`: start the server if needed, then exec the TUI attached to it."""

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from doctor_kit import Good
from serve_kit import Procs, healthy_routes, install_fake, start

from coven import cli
from coven.opencode import canary, serve

PASSWORD_VAR = "OPENCODE_SERVER_PASSWORD"
PLUGIN_MISSING = "coven's plugin is not active on the server"

pytestmark = pytest.mark.usefixtures("interpreter_is_the_server")


class Exec:
    """A stand-in for os.execve that records its call."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str], dict[str, str]]] = []

    def __call__(self, path: str, argv: list[str], env: dict[str, str]) -> None:
        self.calls.append((path, argv, env))


@pytest.fixture
def exec_calls(monkeypatch: pytest.MonkeyPatch) -> Exec:
    recorded = Exec()
    monkeypatch.setattr(os, "execve", recorded)
    return recorded


@pytest.fixture
def reaped(procs: Procs, monkeypatch: pytest.MonkeyPatch) -> Procs:
    """The command spawns with the default; swap it so teardown can reap what it starts."""
    assert serve.start.__kwdefaults__ is not None
    monkeypatch.setitem(serve.start.__kwdefaults__, "spawn", procs.spawn)
    return procs


type Install = Callable[[dict[str, Any]], None]


@pytest.fixture
def install(good: Good, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Install:
    """Put a fake server that answers `routes` where COVEN_OPENCODE_BIN points."""
    return lambda routes: install_fake(tmp_path, monkeypatch, routes=routes)


def test_gm_starts_server_when_stopped_then_execs_attach(
    good: Good, install: Install, monkeypatch: pytest.MonkeyPatch, reaped: Procs, exec_calls: Exec
) -> None:
    install(healthy_routes(()))
    monkeypatch.delenv(PASSWORD_VAR, raising=False)
    assert serve.running(good.layout) is None

    assert cli.main(["opencode", "gm"]) == 0

    info = serve.running(good.layout)
    assert info is not None
    assert len(reaped.started) == 1
    [(path, argv, env)] = exec_calls.calls
    assert path == os.environ["COVEN_OPENCODE_BIN"]
    assert argv[-2:] == ["--server", f"http://127.0.0.1:{info.port}"]
    password = serve.files(good.layout).password.read_text().strip()
    assert env[PASSWORD_VAR] == password
    assert PASSWORD_VAR not in os.environ
    assert [name for name, value in os.environ.items() if password in value] == []


def test_gm_attaches_to_the_server_that_runs_without_starting_another(
    good: Good, install: Install, procs: Procs, exec_calls: Exec
) -> None:
    install(healthy_routes(()))
    info = start(good, procs)

    assert cli.main(["opencode", "gm"]) == 0

    assert len(procs.started) == 1
    assert exec_calls.calls[0][1][-1] == f"http://127.0.0.1:{info.port}"


def test_gm_gives_the_tui_the_server_login_not_another_user_name(
    good: Good,
    install: Install,
    procs: Procs,
    exec_calls: Exec,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install(healthy_routes(()))
    start(good, procs)
    monkeypatch.setenv("OPENCODE_SERVER_USERNAME", "someone")

    assert cli.main(["opencode", "gm"]) == 0

    assert "OPENCODE_SERVER_USERNAME" not in exec_calls.calls[0][2]


def test_gm_stops_when_plugin_not_active(
    install: Install,
    monkeypatch: pytest.MonkeyPatch,
    reaped: Procs,
    exec_calls: Exec,
    capsys: pytest.CaptureFixture[str],
) -> None:
    install(healthy_routes(()) | {"/api/plugin": {"data": [{"id": "other"}]}})
    monkeypatch.setattr(canary, "SETTLE", 0.3)

    assert cli.main(["opencode", "gm"]) == 1

    assert PLUGIN_MISSING in capsys.readouterr().err
    assert exec_calls.calls == []


def test_gm_names_a_plugin_that_failed_to_load(
    install: Install, reaped: Procs, exec_calls: Exec, capsys: pytest.CaptureFixture[str]
) -> None:
    failed = {"data": [{"id": "coven", "state": {"status": "failed", "error": "boom"}}]}
    install(healthy_routes(()) | {"/api/plugin": failed})

    assert cli.main(["opencode", "gm"]) == 1

    error = capsys.readouterr().err
    assert PLUGIN_MISSING in error
    assert "boom" in error
    assert exec_calls.calls == []


def test_gm_without_a_profile_says_to_run_setup(
    good: Good, exec_calls: Exec, capsys: pytest.CaptureFixture[str]
) -> None:
    good.layout.profile_file.unlink()

    assert cli.main(["opencode", "gm"]) == 1

    assert "coven opencode setup" in capsys.readouterr().err
    assert exec_calls.calls == []
