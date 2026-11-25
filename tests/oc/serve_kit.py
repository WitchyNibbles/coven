"""A fake `opencode` that serves on 127.0.0.1, and the helpers to start and reap it.

Shared by the serve, gm and doctor-server tests: none of them runs the real runtime.
"""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from doctor_kit import Good

from coven.opencode import serve
from coven.opencode.profile import load_profile

WAIT = 10.0
# The server's answer to every route the fake does not know.
SESSIONS = {"data": [], "cursor": None}
ACTIVE_PLUGINS = {"data": [{"id": "coven", "state": {"status": "active"}}]}
SKILLS = {"data": [{"id": "opencode", "path": "skill://opencode/SKILL.md"}]}

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


def openapi(operations: tuple[str, ...]) -> dict[str, Any]:
    """An OpenAPI document holding one GET operation per id."""
    paths = {f"/api/{op}": {"get": {"operationId": op}} for op in operations}
    return {"openapi": "3.1.0", "paths": paths}


def healthy_routes(operations: tuple[str, ...]) -> dict[str, Any]:
    """The routes of a coven server whose plugin is active and whose OpenAPI lists `operations`."""
    return {
        "/api/session": SESSIONS,
        "/api/plugin": ACTIVE_PLUGINS,
        "/api/skill": SKILLS,
        "/openapi.json": openapi(operations),
    }


def install_fake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **config: Any) -> None:
    script = tmp_path / "fake-server" / "opencode"
    script.parent.mkdir()
    shown = {"routes": {"/api/session": SESSIONS}, **config}
    script.write_text(FAKE_OPENCODE % {"python": sys.executable, "config": json.dumps(shown)})
    script.chmod(0o755)
    monkeypatch.setenv("COVEN_OPENCODE_BIN", str(script))


def start(
    good: Good, procs: Procs, port: int | None = None, wait_s: float = WAIT
) -> serve.ServerInfo:
    profile = load_profile(good.layout)
    assert profile is not None
    return serve.start(good.layout, profile, port, spawn=procs.spawn, wait_s=wait_s)
