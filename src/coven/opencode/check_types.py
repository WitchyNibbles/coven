"""What the doctor checks share: a result, the context they run in and the lazy server read."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from coven.opencode.canary import ServerOptions, ServerView, data_items, http_json
from coven.opencode.profile import RESET_HINT, Layout, OpencodeError, Profile
from coven.opencode.serve import HOST, ServerInfo

type Status = Literal["ok", "warn", "fail"]
type Read = Callable[[Path, Mapping[str, str], Path, ServerOptions], ServerView]

RESET: Final = RESET_HINT
SETUP: Final = "run coven opencode setup"
SKIPPED: Final = "skipped: no profile — run `coven opencode setup`"


@dataclass(frozen=True)
class Outcome:
    status: Status
    detail: str
    fix: str = ""


@dataclass(frozen=True)
class Live:
    """The coven server that already runs, and the file that holds its password."""

    info: ServerInfo
    password_file: Path


class Server:
    """What doctor reads from a server, made on first use.

    With a coven server running, plugins and skills come from it and no other server starts: two
    OpenCode processes on one database is unsafe. With none, one `read_server` call starts a canary.
    """

    def __init__(
        self,
        read: Read,
        binary: Path,
        env: Mapping[str, str],
        cwd: Path,
        live: Live | None = None,
    ) -> None:
        self._read = read
        self._call = (binary, env, cwd, ServerOptions())
        self.live = live
        self._blocked: str | None = None
        self._view: ServerView | None = None
        self._error: Exception | None = None

    def block(self, reason: str) -> None:
        """Make every later `view()` fail with `reason`, without starting a server."""
        self._blocked = reason

    def fetch(self, path: str) -> object:
        """GET `path` from the running server with its saved password."""
        if self.live is None:
            raise OpencodeError("no coven server is running")
        try:
            password = self.live.password_file.read_text().strip()
        except OSError as error:
            raise OpencodeError(f"cannot read the server password: {error}") from error
        return http_json("GET", f"http://{HOST}:{self.live.info.port}{path}", password)

    def _read_live(self) -> ServerView:
        plugins = data_items(self.fetch("/api/plugin"), "/api/plugin")
        return ServerView(plugins, data_items(self.fetch("/api/skill"), "/api/skill"))

    def view(self) -> ServerView:
        if self._blocked:
            raise OpencodeError(self._blocked)
        if self._error:
            raise self._error
        if self._view is None:
            try:
                self._view = self._read_live() if self.live else self._read(*self._call)
            except Exception as error:
                self._error = error
                raise
        return self._view


@dataclass(frozen=True)
class Context:
    """Everything a check may look at."""

    layout: Layout
    cwd: Path
    base: Mapping[str, str]
    profile: Profile | None
    profile_note: str
    env: Mapping[str, str]
    binary: Path
    server: Server

    def skip(self) -> Outcome:
        """The failure for a check that needs a profile when there is none."""
        return Outcome("fail", f"skipped: {self.profile_note}" if self.profile_note else SKIPPED)
