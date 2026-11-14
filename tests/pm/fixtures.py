"""Synthetic `claude agents --json` rows and session transcripts, and the fake CLI that serves them.

The entry shapes are the ones the parser reads (see `coven.pm.transcript`); nothing here is a
recording of a real session.
"""

from __future__ import annotations

import json
import re
import stat
import sys
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Self

import pytest

from coven import cli, paths

SESSION_ID = "00000000-0000-4000-8000-000000000001"
CWD = "/work/repo-1"


def encode_cwd(cwd: str) -> str:
    """The folder name Claude Code gives a working directory: every other character becomes `-`."""
    return re.sub(r"[^A-Za-z0-9]", "-", cwd)


def stamp(at: datetime) -> str:
    """`at` the way transcripts write it: UTC, milliseconds, a `Z` suffix."""
    utc = at.astimezone(UTC)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.") + f"{utc.microsecond // 1000:03d}Z"


class TranscriptBuilder:
    """Collects transcript lines in order; `write` puts them in a file, one per line."""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self._tool_ids = 0

    def _entry(self, kind: str, at: datetime | None, **fields: object) -> Self:
        entry: dict[str, object] = {"type": kind, "sessionId": SESSION_ID, "cwd": CWD}
        if at is not None:
            entry["timestamp"] = stamp(at)
        entry.update(fields)
        return self.raw(json.dumps(entry))

    def raw(self, line: str) -> Self:
        self.lines.append(line)
        return self

    def prompt(self, text: str, at: datetime | None) -> Self:
        return self._entry(
            "user",
            at,
            origin={"kind": "human"},
            message={"role": "user", "content": text},
        )

    def peer(self, sender: str, body: str, at: datetime | None) -> Self:
        wrapped = f'<cross-session-message from="{sender}">{body}</cross-session-message>'
        return self._entry(
            "user",
            at,
            origin={"kind": "peer", "name": sender, "body": body},
            message={"role": "user", "content": wrapped},
        )

    def _send_call(self, to: str, message: str, at: datetime | None) -> str:
        """Write a SendMessage call; return its tool_use id."""
        self._tool_ids += 1
        tool_id = f"toolu_{self._tool_ids:04d}"
        call: dict[str, object] = {"type": "tool_use", "id": tool_id, "name": "SendMessage"}
        call["input"] = {"to": to, "summary": "placeholder", "message": message}
        self._entry("assistant", at, message={"role": "assistant", "content": [call]})
        return tool_id

    def send(
        self,
        to: str,
        message: str,
        at: datetime | None,
        delivered: bool | None = True,
        error: str | None = None,
    ) -> Self:
        """A SendMessage call and, unless `delivered` is None, its result.

        A failed send (`delivered=False`) writes `toolUseResult.success == false`.
        """
        tool_id = self._send_call(to, message, at)
        if delivered is None:
            return self
        outcome: dict[str, object] = {"success": delivered}
        outcome["message"] = "delivered" if delivered else (error or "not delivered")
        item: dict[str, object] = {"type": "tool_result", "tool_use_id": tool_id}
        item["content"] = [{"type": "text", "text": str(outcome["message"])}]
        return self._entry(
            "user",
            at,
            message={"role": "user", "content": [item]},
            toolUseResult=outcome,
        )

    def send_flagged(self, to: str, message: str, at: datetime | None, error: str) -> Self:
        """A SendMessage call whose result is flagged `is_error`, with no `success` field.

        A failed send's exact shape is not known; the parser accepts this one and `send(...,
        delivered=False)`.
        """
        tool_id = self._send_call(to, message, at)
        item: dict[str, object] = {"type": "tool_result", "tool_use_id": tool_id, "is_error": True}
        item["content"] = [{"type": "text", "text": error}]
        return self._entry(
            "user",
            at,
            message={"role": "user", "content": [item]},
            toolUseResult=f"Error: {error}",
        )

    def text(self, text: str, at: datetime | None) -> Self:
        content = [{"type": "text", "text": text}]
        return self._entry("assistant", at, message={"role": "assistant", "content": content})

    def thinking(self, text: str, at: datetime | None) -> Self:
        content = [{"type": "thinking", "thinking": text}]
        return self._entry("assistant", at, message={"role": "assistant", "content": content})

    def turn_end(self, at: datetime | None) -> Self:
        return self._entry("system", at, subtype="turn_duration", durationMs=1000)

    def idle_subscription(self, at: datetime | None) -> Self:
        return self._entry(
            "system",
            at,
            subtype="informational",
            content="A process asked to be told when this session is next idle.",
        )

    def agent_name(self, name: str) -> Self:
        return self.raw(
            json.dumps({"type": "agent-name", "agentName": name, "sessionId": SESSION_ID})
        )

    def tool_result_other(self, text: str, at: datetime | None) -> Self:
        """The result of some tool that is not a send: no `toolUseResult.success`."""
        item: dict[str, object] = {"type": "tool_result", "tool_use_id": "toolu_other"}
        item["content"] = [{"type": "text", "text": text}]
        return self._entry("user", at, message={"role": "user", "content": [item]})

    def write(self, path: Path) -> Path:
        path.write_text("\n".join(self.lines) + "\n", encoding="utf-8")
        return path


# --- the environment `coven pm` commands run in -------------------------------------------------

NOW = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)
GM = "gm-main"
PM = "pm-acme-1"
CLI_NAME = "claude"

# The fake CLI: it notes that it ran, then fails or prints the rows the test wrote.
FAKE_CLI = """#!{python}
import sys
from pathlib import Path

with Path({ran!r}).open("a") as ran:
    ran.write("ran\\n")
exit_file = Path({exit!r})
if exit_file.exists():
    sys.stderr.write("agents failed\\n")
    sys.exit(int(exit_file.read_text()))
sys.stdout.write(Path({rows!r}).read_text())
"""

# A fake CLI that writes exact bytes to stdout and stderr, then exits: `output` and `error` are bytes.
BYTES_CLI = """#!{python}
import sys

sys.stdout.buffer.write({output!r})
sys.stderr.buffer.write({error!r})
sys.exit({code})
"""

SLEEP_CLI = """#!{python}
import time

time.sleep(30)
"""


def fake_claude(bin_dir: Path, template: str, **fields: object) -> Path:
    """An executable `claude` in `bin_dir`, written from `template` (python as its interpreter)."""
    script = bin_dir / CLI_NAME
    script.write_text(template.format(python=sys.executable, **fields))
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return script


def at(clock: str) -> datetime:
    """The time `HH:MM` on the test day, in UTC."""
    hour, minute = clock.split(":")
    return NOW.replace(hour=int(hour), minute=int(minute))


def session_id(number: int) -> str:
    return f"00000000-0000-4000-8000-{number:012d}"


def agents_row(
    name: str = PM, number: int = 1, state: str | None = "idle", **fields: object
) -> dict[str, object]:
    """An agents row; `number` makes its id, sessionId and start time (later rows start later)."""
    started = int(NOW.timestamp() * 1000) - (10 - number) * 60_000
    row: dict[str, object] = {
        "id": f"{number:08x}",
        "cwd": CWD,
        "kind": "background",
        "startedAt": started,
        "sessionId": session_id(number),
        "name": name,
    }
    if state is not None:
        row["state"] = state
    row.update(fields)
    return {key: value for key, value in row.items() if value is not None}


class PmEnv:
    """A fake `claude` on PATH, a transcript folder under the Claude config dir, a fake /proc."""

    def __init__(self, tmp_path: Path) -> None:
        self.bin = tmp_path / "bin"
        self.proc = tmp_path / "pm-proc"
        self.work = tmp_path / "work"
        self.transcripts = paths.claude_dir() / "projects" / encode_cwd(CWD)
        for folder in (self.bin, self.proc, self.work, self.transcripts):
            folder.mkdir(parents=True)
        self.rows_file = self.work / "rows.json"
        self.exit_file = self.work / "exit"
        self.ran_file = self.work / "ran"
        self.set_rows()
        self.install_cli()

    def install_cli(self) -> None:
        fake_claude(
            self.bin,
            FAKE_CLI,
            ran=str(self.ran_file),
            exit=str(self.exit_file),
            rows=str(self.rows_file),
        )

    def remove_cli(self) -> None:
        (self.bin / CLI_NAME).unlink()

    def fail_agents(self, code: int = 1) -> None:
        self.exit_file.write_text(str(code))

    def set_rows(self, *rows: dict[str, object]) -> None:
        self.rows_file.write_text(json.dumps(list(rows)))

    @property
    def agents_runs(self) -> int:
        return len(self.ran_file.read_text().splitlines()) if self.ran_file.exists() else 0

    def transcript(self, builder: TranscriptBuilder, number: int = 1) -> Path:
        return builder.write(self.transcripts / f"{session_id(number)}.jsonl")

    def process(self, pid: int, cmdline: str = CLI_NAME) -> None:
        """A running process `pid` in the fake /proc."""
        folder = self.proc / str(pid)
        folder.mkdir()
        (folder / "cmdline").write_bytes(cmdline.encode() + b"\0")

    def run(self, capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
        code = cli.main(["pm", *argv])
        captured = capsys.readouterr()
        return code, captured.out, captured.err


def pm_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[PmEnv]:
    """Set up a `PmEnv`; a test file wraps this in its own fixture with `yield from`.

    The clock is NOW and the local zone UTC, so printed times are `HH:MM` of `at(...)`.
    """
    made = PmEnv(tmp_path)
    monkeypatch.setenv("PATH", str(made.bin))  # the real claude is out of reach
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    monkeypatch.setattr("coven.pm.report.PROC_ROOT", made.proc)
    monkeypatch.setattr("coven.pm.report.utcnow", lambda: NOW)
    yield made
    monkeypatch.undo()
    time.tzset()
