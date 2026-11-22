"""What the doctor checks share: a result, the context they run in and the lazy server read."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from coven.opencode.canary import ServerOptions, ServerView
from coven.opencode.profile import Layout, OpencodeError, Profile

type Status = Literal["ok", "warn", "fail"]
type Read = Callable[[Path, Mapping[str, str], Path, ServerOptions], ServerView]

RESET: Final = "re-run coven opencode setup"
SETUP: Final = "run coven opencode setup"
SKIPPED: Final = "skipped: no profile — run `coven opencode setup`"
DETAIL_LIMIT: Final = 200


@dataclass(frozen=True)
class Outcome:
    status: Status
    detail: str
    fix: str = ""


def one_line(text: str, limit: int = DETAIL_LIMIT) -> str:
    """`text` as one short printable line: server and tool output is data, never layout."""
    flat = " ".join("".join(ch if ch.isprintable() else " " for ch in text).split())
    return flat if len(flat) <= limit else flat[: limit - 3] + "..."


class Server:
    """One `read_server` call shared by the plugin and skills checks, made on first use."""

    def __init__(self, read: Read, binary: Path, env: Mapping[str, str], cwd: Path) -> None:
        self._read = read
        self._call = (binary, env, cwd, ServerOptions())
        self._blocked: str | None = None
        self._view: ServerView | None = None
        self._error: Exception | None = None

    def block(self, reason: str) -> None:
        """Make every later `view()` fail with `reason`, without starting a server."""
        self._blocked = reason

    def view(self) -> ServerView:
        if self._blocked:
            raise OpencodeError(self._blocked)
        if self._error:
            raise self._error
        if self._view is None:
            try:
                self._view = self._read(*self._call)
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
