"""coven's one long-lived `opencode serve` per profile: start, stop and status."""

from __future__ import annotations

import os
import secrets
import signal
import socket
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final
from urllib.parse import urlsplit

from coven.opencode import runtime
from coven.opencode.canary import (
    AUTH_VAR,
    GRACE,
    USERNAME_ENV,
    data_items,
    http_json,
    listening_url,
    stop_server,
)
from coven.opencode.profile import Layout, OpencodeError, Profile

type Spawn = Callable[..., subprocess.Popen[bytes]]

HOST: Final = "127.0.0.1"
POLL: Final = 0.05
# After SIGKILL the process is gone within a moment; this only bounds a stuck wait.
KILL_WAIT: Final = 5.0
PM_ROLE: Final = "pm"
MORE_SESSIONS: Final = "more sessions exist; this list may be incomplete"


class ServeError(OpencodeError):
    """The server could not start or stop; the message names what to look at."""


@dataclass(frozen=True)
class ServeFiles:
    password: Path
    port: Path
    pid: Path
    log: Path


@dataclass(frozen=True)
class ServerInfo:
    pid: int
    port: int


def files(layout: Layout) -> ServeFiles:
    folder = layout.serve_dir
    return ServeFiles(folder / "password", folder / "port", folder / "pid", folder / "log")


def server_exe(layout: Layout) -> Path:
    """The runtime's `opencode.exe`, resolved: the CLI execs into it, so no wrapper holds the port."""
    return Path(os.path.realpath(runtime.binary_path(layout)))


def _read_int(path: Path) -> int | None:
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return None


def _is_server(pid: int, layout: Layout) -> bool:
    """Whether `pid` is a live process running the runtime's binary (a gone or reused pid is not)."""
    return os.path.realpath(f"/proc/{pid}/exe") == str(server_exe(layout))


def running(layout: Layout) -> ServerInfo | None:
    paths = files(layout)
    pid = _read_int(paths.pid)
    port = _read_int(paths.port)
    if pid is None or port is None or not _is_server(pid, layout):
        return None
    return ServerInfo(pid, port)


def _password(paths: ServeFiles) -> str:
    """The saved password, or a new one: 32 random bytes as hex, readable by the owner only."""
    paths.password.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        saved = paths.password.read_text().strip()
    except FileNotFoundError:
        saved = ""
    if saved:
        return saved
    fresh = secrets.token_hex(32)
    handle = os.open(paths.password, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(handle, "w") as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(f"{fresh}\n")
    return fresh


def _require_free(port: int) -> None:
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((HOST, port))
        except OSError as error:
            raise ServeError(f"port {port} in use") from error


def _server_env(profile: Profile, layout: Layout, password: str) -> dict[str, str]:
    base = {k: v for k, v in os.environ.items() if k != USERNAME_ENV}
    return runtime.run_env(profile, layout, base, offline=False) | {AUTH_VAR: password}


def _spawn(
    spawn: Spawn, binary: Path, port: int, env: dict[str, str], layout: Layout
) -> subprocess.Popen[bytes]:
    argv = [str(binary), "serve", "--hostname", HOST, "--port", str(port)]
    log = files(layout).log
    try:
        with log.open("wb") as output:
            return spawn(
                argv,
                env=env,
                cwd=layout.home,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
    except OSError as error:
        raise ServeError(f"cannot start {binary}: {error}") from error


def _wait_for[T](
    attempt: Callable[[], T | None],
    process: subprocess.Popen[bytes],
    what: str,
    wait_s: float,
    log: Path,
) -> T:
    """Poll `attempt` until it gives a value; fail when the server exits or `wait_s` runs out."""
    deadline = time.monotonic() + wait_s
    while True:
        found = attempt()
        if found is not None:
            return found
        if process.poll() is not None:
            raise ServeError(f"the server exited before it {what}; see {log}")
        if time.monotonic() >= deadline:
            raise ServeError(f"the server did not {what} within {wait_s:g} s; see {log}")
        time.sleep(POLL)


def _listening_port(log: Path) -> int | None:
    for line in log.read_text(errors="replace").splitlines():
        if url := listening_url(line):
            return urlsplit(url).port
    return None


def _answers(port: int, password: str) -> bool | None:
    try:
        http_json("GET", f"http://{HOST}:{port}/api/session", password)
    except OpencodeError:
        return None
    return True


def start(
    layout: Layout,
    profile: Profile,
    port: int | None = None,
    *,
    spawn: Spawn = subprocess.Popen,
    wait_s: float = 20.0,
) -> ServerInfo:
    """Start the server detached and return once it answers with the saved password."""
    if (info := running(layout)) is not None:
        raise ServeError(f"a server is already running on port {info.port}")
    if port is not None:
        _require_free(port)
    binary = runtime.checked_binary(layout, os.environ)
    paths = files(layout)
    password = _password(paths)
    process = _spawn(spawn, binary, port or 0, _server_env(profile, layout, password), layout)
    try:
        bound = _wait_for(lambda: _listening_port(paths.log), process, "listen", wait_s, paths.log)
        _wait_for(lambda: _answers(bound, password), process, "answer", wait_s, paths.log)
    except BaseException:
        stop_server(process, GRACE)
        raise
    paths.pid.write_text(f"{process.pid}\n")
    paths.port.write_text(f"{bound}\n")
    return ServerInfo(process.pid, bound)


def _gone(pid: int, layout: Layout, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while _is_server(pid, layout):
        if time.monotonic() >= deadline:
            return False
        time.sleep(POLL)
    return True


def _terminate(pid: int, layout: Layout, grace_s: float) -> None:
    """SIGTERM, then SIGKILL once `grace_s` has passed."""
    try:
        os.kill(pid, signal.SIGTERM)
        if _gone(pid, layout, grace_s):
            return
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    if not _gone(pid, layout, KILL_WAIT):
        raise ServeError(f"process {pid} did not stop")


def stop(layout: Layout, *, grace_s: float = 10.0) -> bool:
    """Stop the running server; True when there was one. Removes the pid and port, keeps the password."""
    paths = files(layout)
    info = running(layout)
    try:
        if info is not None:
            _terminate(info.pid, layout, grace_s)
    finally:
        paths.pid.unlink(missing_ok=True)
        paths.port.unlink(missing_ok=True)
    return info is not None


def _sub(value: object, key: str) -> object:
    return value.get(key) if isinstance(value, dict) else None


def _pm_code(session: dict[str, object]) -> str | None:
    """The code of a PM root session, None for any other session."""
    if session.get("parentID"):
        return None
    coven = _sub(session.get("metadata"), "coven")
    code = _sub(coven, "code")
    return code if _sub(coven, "role") == PM_ROLE and isinstance(code, str) else None


def _pm_line(code: str, session: dict[str, object], busy: bool, asks: list[str]) -> str:
    parts = [f"pm-{code}", "busy" if busy else "idle"]
    title = session.get("title")
    if isinstance(title, str) and title != f"pm-{code}":
        parts.append(f'"{title}"')
    if isinstance(outcome := session.get("outcome"), str):
        parts.append(f"last {outcome}")
    parts.extend(f"ASK WAITING (bug) {ask}" for ask in asks)
    return "  ".join(parts)


def _pm_lines(info: ServerInfo, password: str) -> list[str]:
    def fetch(path: str) -> object:
        return http_json("GET", f"http://{HOST}:{info.port}{path}", password)

    def get(path: str) -> list[dict[str, object]]:
        return data_items(fetch(path), path)

    busy = {item.get("id") for item in get("/api/session/active")}
    listing = fetch("/api/session")
    lines = []
    for session in data_items(listing, "/api/session"):
        code, session_id = _pm_code(session), session.get("id")
        if code is None or not isinstance(session_id, str):
            continue
        asks = [str(ask.get("id")) for ask in get(f"/api/session/{session_id}/permission")]
        lines.append(_pm_line(code, session, session_id in busy, asks))
    # The cursor's request parameter is unverified, so a longer list is reported, not followed.
    if _sub(listing, "cursor"):
        lines.append(MORE_SESSIONS)
    return lines


def status(layout: Layout) -> list[str]:
    """`stopped`, or `running pid P port N` and a line per PM session. Read-only."""
    info = running(layout)
    if info is None:
        return ["stopped"]
    head = f"running pid {info.pid} port {info.port}"
    try:
        password = files(layout).password.read_text().strip()
        return [head, *_pm_lines(info, password)]
    except (OpencodeError, OSError) as error:
        return [head, f"sessions unavailable: {error}"]
