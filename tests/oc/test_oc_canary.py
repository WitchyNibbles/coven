import base64
import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fakes import Serve, fake_opencode

from coven.opencode import canary
from coven.opencode.canary import Fetch, ServerOptions, ServerView, http_json, read_server
from coven.opencode.profile import OpencodeError

LISTENING_URL = "http://127.0.0.1:1"
PLUGINS: list[dict[str, object]] = [{"id": "coven", "state": {"status": "active"}}]
SKILLS: list[dict[str, object]] = [{"id": "opencode", "path": "skill://opencode/SKILL.md"}]


class Fetches:
    """A stand-in for http_json: records each call and answers like the server."""

    def __init__(self, fail: Exception | None = None) -> None:
        self.calls: list[tuple[str, str, str]] = []
        self.fail = fail

    def __call__(self, method: str, url: str, password: str) -> object:
        self.calls.append((method, url, password))
        if self.fail:
            raise self.fail
        if url.endswith("/api/plugin"):
            return {"data": PLUGINS}
        if url.endswith("/api/skill"):
            return {"data": SKILLS}
        return None


@dataclass
class Spawn:
    """One process read_server started: how, and the process itself."""

    argv: list[str]
    environ: dict[str, str]
    cwd: Path
    process: subprocess.Popen[str]


@pytest.fixture
def spawned(monkeypatch: pytest.MonkeyPatch) -> list[Spawn]:
    """Every process read_server starts, so a test can see whether it is still alive."""
    made: list[Spawn] = []
    real = subprocess.Popen

    def spy(argv: list[str], **kwargs: Any) -> subprocess.Popen[str]:
        process = real(argv, **kwargs)
        made.append(Spawn(argv, kwargs["env"], kwargs["cwd"], process))
        return process

    monkeypatch.setattr(canary.subprocess, "Popen", spy)
    return made


def start(tmp_path: Path, serve: Serve, fetch: Fetch, **options: float) -> ServerView:
    binary = fake_opencode(tmp_path / "bin", "2.3.2", serve=serve)
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    opts = ServerOptions(fetch=fetch, **options)
    return read_server(binary, {"PATH": "/usr/bin", "KEEP": "1"}, work, opts)


def test_read_server_returns_the_view_and_kills_a_server_that_ignores_term(
    tmp_path: Path, spawned: list[Spawn]
):
    fetch = Fetches()
    began = time.monotonic()
    view = start(tmp_path, "ignore-term", fetch, start_timeout=10.0, grace=0.2)
    assert time.monotonic() - began < 3
    assert view == ServerView(PLUGINS, SKILLS)
    assert [(method, url) for method, url, _ in fetch.calls] == [
        ("POST", f"{LISTENING_URL}/api/location/reload"),
        ("GET", f"{LISTENING_URL}/api/plugin"),
        ("GET", f"{LISTENING_URL}/api/skill"),
    ]
    assert len(spawned) == 1
    assert spawned[0].process.poll() is not None


class Loading:
    """A server whose plugin list is empty for the first `empty` reads, as 2.3.2's is while loading."""

    def __init__(self, empty: int) -> None:
        self.empty = empty
        self.calls: list[str] = []

    def __call__(self, method: str, url: str, password: str) -> object:
        self.calls.append(f"{method} {url.rsplit('/api', 1)[1]}")
        if url.endswith("/api/plugin"):
            self.empty -= 1
            return {"data": [] if self.empty >= 0 else PLUGINS}
        return {"data": SKILLS} if url.endswith("/api/skill") else None


def test_read_server_waits_for_the_plugins_to_finish_loading(tmp_path: Path, spawned: list[Spawn]):
    loading = Loading(empty=2)
    view = start(tmp_path, "ok", loading, start_timeout=10.0, grace=0.5)
    assert view == ServerView(PLUGINS, SKILLS)
    assert loading.calls == [
        "POST /location/reload",
        "GET /plugin",
        "GET /plugin",
        "GET /plugin",
        "GET /skill",
    ]


def test_read_server_gives_up_waiting_for_coven_and_returns_what_it_saw(
    tmp_path: Path, spawned: list[Spawn], monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(canary, "SETTLE", 0.3)
    view = start(tmp_path, "ok", Loading(empty=10**6), start_timeout=10.0, grace=0.5)
    assert view == ServerView([], SKILLS)
    assert spawned[0].process.poll() is not None


def test_read_server_starts_plain_serve_with_a_fresh_password(tmp_path: Path, spawned: list[Spawn]):
    fetch = Fetches()
    start(tmp_path, "ok", fetch, start_timeout=10.0, grace=1.0)
    passwords = {password for _, _, password in fetch.calls}
    assert len(passwords) == 1
    (password,) = passwords
    assert len(password) >= 24
    assert spawned[0].argv[1:] == ["serve", "--hostname", "127.0.0.1", "--port", "0"]
    assert spawned[0].environ == {
        "PATH": "/usr/bin",
        "KEEP": "1",
        "OPENCODE_SERVER_PASSWORD": password,
    }
    assert spawned[0].cwd == tmp_path / "work"


def test_read_server_drops_a_server_username_from_the_run_settings(
    tmp_path: Path, spawned: list[Spawn]
):
    binary = fake_opencode(tmp_path / "bin", "2.3.2")
    given = {"OPENCODE_SERVER_USERNAME": "someone", "OPENCODE_SERVER_PASSWORD": "old"}
    read_server(binary, given, tmp_path, ServerOptions(fetch=Fetches(), grace=0.5))
    sent = spawned[0].environ
    assert "OPENCODE_SERVER_USERNAME" not in sent
    assert sent["OPENCODE_SERVER_PASSWORD"] != "old"


def test_a_server_that_prints_nothing_fails_after_the_start_timeout(
    tmp_path: Path, spawned: list[Spawn]
):
    fetch = Fetches()
    with pytest.raises(OpencodeError, match="did not start"):
        start(tmp_path, "silent", fetch, start_timeout=0.3, grace=0.5)
    assert fetch.calls == []
    assert spawned[0].process.poll() is not None


def test_a_failed_fetch_still_stops_the_server_and_hides_the_password(
    tmp_path: Path, spawned: list[Spawn]
):
    fetch = Fetches(fail=OpencodeError("GET /api/plugin returned HTTP 401"))
    with pytest.raises(OpencodeError, match="HTTP 401") as caught:
        start(tmp_path, "ignore-term", fetch, start_timeout=10.0, grace=0.2)
    assert fetch.calls[0][2] not in str(caught.value)
    assert spawned[0].process.poll() is not None


def test_a_watcher_that_cannot_start_never_orphans_the_server(
    tmp_path: Path, spawned: list[Spawn], monkeypatch: pytest.MonkeyPatch
):
    def refuse(*args: object, **kwargs: object) -> None:
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(canary.threading, "Thread", refuse)
    with pytest.raises(RuntimeError, match="new thread"):
        start(tmp_path, "ignore-term", Fetches(), start_timeout=10.0, grace=0.2)
    assert len(spawned) == 1
    assert spawned[0].process.poll() is not None


class Interrupted:
    """A process whose first wait is cut short by a second Ctrl-C."""

    def __init__(self) -> None:
        self.killed = False

    def terminate(self) -> None:
        pass

    def kill(self) -> None:
        self.killed = True

    def wait(self, timeout: float | None = None) -> int:
        if not self.killed:
            raise KeyboardInterrupt
        return -9


def test_a_second_interrupt_while_stopping_still_kills_the_server():
    process = Interrupted()
    with pytest.raises(KeyboardInterrupt):
        canary.stop_server(process, 1.0)
    assert process.killed


def test_an_unrunnable_binary_is_an_opencode_error(tmp_path: Path):
    with pytest.raises(OpencodeError, match="cannot start"):
        read_server(tmp_path / "missing", {}, tmp_path, ServerOptions(fetch=Fetches()))


@pytest.mark.parametrize("payload", [{"data": "x"}, {"other": []}, [1]])
def test_a_reply_without_a_data_list_is_an_error(tmp_path: Path, payload: object):
    def fetch(method: str, url: str, password: str) -> object:
        return payload if url.endswith("/api/plugin") else None

    with pytest.raises(OpencodeError, match="/api/plugin"):
        start(tmp_path, "ok", fetch, start_timeout=10.0, grace=0.5)


class FakeResponse:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self.body = body

    def read(self) -> bytes:
        return self.body


def fake_connection(
    monkeypatch: pytest.MonkeyPatch, response: FakeResponse
) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []

    class Connection:
        def __init__(self, host: str, port: int, timeout: float) -> None:
            seen.append({"host": host, "port": port, "timeout": timeout})

        def request(self, method: str, path: str, headers: dict[str, str]) -> None:
            seen.append({"method": method, "path": path, "headers": headers})

        def getresponse(self) -> FakeResponse:
            return response

        def close(self) -> None:
            seen.append({"closed": True})

    monkeypatch.setattr(canary.http.client, "HTTPConnection", Connection)
    return seen


def test_http_json_sends_basic_auth_and_parses_the_body(monkeypatch: pytest.MonkeyPatch):
    seen = fake_connection(monkeypatch, FakeResponse(200, json.dumps({"data": [1]}).encode()))
    assert http_json("GET", "http://127.0.0.1:4242/api/plugin", "pw") == {"data": [1]}
    token = base64.b64encode(b"opencode:pw").decode()
    assert seen[0] == {"host": "127.0.0.1", "port": 4242, "timeout": 10.0}
    assert seen[1] == {
        "method": "GET",
        "path": "/api/plugin",
        "headers": {"Authorization": f"Basic {token}"},
    }
    assert seen[-1] == {"closed": True}


def test_http_json_accepts_an_empty_body(monkeypatch: pytest.MonkeyPatch):
    fake_connection(monkeypatch, FakeResponse(204, b""))
    assert http_json("POST", "http://127.0.0.1:1/api/location/reload", "pw") is None


@pytest.mark.parametrize(
    ("response", "message"),
    [(FakeResponse(401, b"nope"), "HTTP 401"), (FakeResponse(200, b"not json"), "not JSON")],
)
def test_http_json_names_a_bad_reply_without_the_password(
    monkeypatch: pytest.MonkeyPatch, response: FakeResponse, message: str
):
    fake_connection(monkeypatch, response)
    with pytest.raises(OpencodeError, match=message) as caught:
        http_json("GET", "http://127.0.0.1:1/api/plugin", "secret-pw")
    assert "secret-pw" not in str(caught.value)
