"""The plugin canary: run coven's OpenCode server for a moment and read what it loaded."""

from __future__ import annotations

import base64
import http.client
import queue
import re
import secrets
import subprocess
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final
from urllib.parse import urlsplit

from coven import safe_json
from coven.opencode.profile import OpencodeError

# method, URL, password; returns the parsed JSON body.
type Fetch = Callable[[str, str, str], object]

USERNAME: Final = "opencode"
HTTP_TIMEOUT: Final = 10.0
START_TIMEOUT: Final = 30.0
GRACE: Final = 5.0
AUTH_VAR: Final = "OPENCODE_SERVER_PASSWORD"
USERNAME_ENV: Final = "OPENCODE_SERVER_USERNAME"
LISTENING: Final = re.compile(r"^server listening on (http://127\.0\.0\.1:\d+)$")
WATCHER_JOIN: Final = 1.0
COVEN_PLUGIN: Final = "coven"
# How long to wait for the plugins to load once the server listens, and between reads.
SETTLE: Final = 10.0
POLL: Final = 0.1


def http_json(method: str, url: str, password: str) -> object:
    """One request with basic auth; the parsed JSON body, or None when the body is empty."""
    parts = urlsplit(url)
    token = base64.b64encode(f"{USERNAME}:{password}".encode()).decode()
    what = f"{method} {parts.path}"
    connection = http.client.HTTPConnection(
        parts.hostname or "", parts.port or 80, timeout=HTTP_TIMEOUT
    )
    try:
        connection.request(method, parts.path, headers={"Authorization": f"Basic {token}"})
        response = connection.getresponse()
        body = response.read()
    except (OSError, http.client.HTTPException) as error:
        raise OpencodeError(f"{what} failed: {error}") from error
    finally:
        connection.close()
    if not 200 <= response.status < 300:
        raise OpencodeError(f"{what} returned HTTP {response.status}")
    if not body.strip():
        return None
    try:
        return safe_json.loads(body.decode("utf-8", errors="replace"))
    except ValueError as error:
        raise OpencodeError(f"{what} reply is not JSON") from error


@dataclass(frozen=True)
class ServerView:
    """What the running server says it loaded."""

    plugins: list[dict[str, object]]
    skills: list[dict[str, object]]


@dataclass(frozen=True)
class ServerOptions:
    fetch: Fetch = http_json
    start_timeout: float = START_TIMEOUT
    grace: float = GRACE


def stop_server(process: subprocess.Popen[str], grace: float) -> None:
    """SIGTERM, wait `grace` seconds, then SIGKILL and wait."""
    process.terminate()
    try:
        process.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def _watch(stream: Iterable[str], found: queue.Queue[str | None]) -> None:
    """Pass on the server's URL, then keep draining so a chatty server never blocks on a pipe."""
    announced = False
    try:
        for line in stream:
            match = LISTENING.match(line.strip())
            if match and not announced:
                announced = True
                found.put(match.group(1))
    except (OSError, ValueError):
        pass
    found.put(None)


def _wait_for_url(found: queue.Queue[str | None], timeout: float) -> str:
    try:
        url = found.get(timeout=timeout)
    except queue.Empty:
        raise OpencodeError(f"the server did not start within {timeout:g} s") from None
    if url is None:
        raise OpencodeError("the server exited before it listened")
    return url


def _items(payload: object, path: str) -> list[dict[str, object]]:
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        raise OpencodeError(f"GET {path} returned no data list")
    return [item for item in data if isinstance(item, dict)]


def _loaded_plugins(url: str, password: str, fetch: Fetch) -> list[dict[str, object]]:
    """The plugin list once coven is in it; 2.3.2 lists none until they have loaded (~0.5 s)."""
    deadline = time.monotonic() + SETTLE
    while True:
        plugins = _items(fetch("GET", f"{url}/api/plugin", password), "/api/plugin")
        if any(plugin.get("id") == COVEN_PLUGIN for plugin in plugins):
            return plugins
        if time.monotonic() >= deadline:
            return plugins
        time.sleep(POLL)


def _read_view(url: str, password: str, fetch: Fetch) -> ServerView:
    # Skills planted before boot only show after a reload (probe 2).
    fetch("POST", f"{url}/api/location/reload", password)
    plugins = _loaded_plugins(url, password, fetch)
    skills = _items(fetch("GET", f"{url}/api/skill", password), "/api/skill")
    return ServerView(plugins, skills)


def _start(binary: Path, env: Mapping[str, str], cwd: Path) -> subprocess.Popen[str]:
    argv = [str(binary), "serve", "--hostname", "127.0.0.1", "--port", "0"]
    try:
        return subprocess.Popen(
            argv,
            env=dict(env),
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except OSError as error:
        raise OpencodeError(f"cannot start {binary}: {error}") from error


def read_server(binary: Path, env: Mapping[str, str], cwd: Path, opts: ServerOptions) -> ServerView:
    """Start `serve` with a fresh password, read plugins and skills, and always stop it."""
    password = secrets.token_urlsafe(24)
    server_env = {k: v for k, v in env.items() if k != USERNAME_ENV} | {AUTH_VAR: password}
    process = _start(binary, server_env, cwd)
    found: queue.Queue[str | None] = queue.Queue()
    watcher = threading.Thread(target=_watch, args=(process.stdout or (), found), daemon=True)
    watcher.start()
    try:
        return _read_view(_wait_for_url(found, opts.start_timeout), password, opts.fetch)
    finally:
        stop_server(process, opts.grace)
        watcher.join(WATCHER_JOIN)
        if process.stdout and not watcher.is_alive():
            process.stdout.close()
