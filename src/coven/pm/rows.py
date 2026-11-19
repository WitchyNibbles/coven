"""The `claude agents --json --all` rows, and whether a row's process is still running.

The row fields read here are internals of Claude Code, not a public format: cwd, id, kind, name,
pid, sessionId, startedAt, state, status and waitingFor were seen, the first nine are read. A field
of an unexpected type reads as missing and an unknown field is ignored, so a format change never
fails the whole listing.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from coven import safe_json
from coven.errors import CovenError
from coven.pm.text import clean_line

AGENTS_TIMEOUT = 10
AGENTS_COMMAND = ["claude", "agents", "--json", "--all"]
PM_NAME_PREFIX = "pm-"
LIVE = frozenset({"working", "idle", "blocked"})
STATUS_STATES = {"busy": "working", "idle": "idle"}
UNKNOWN_STATE = "unknown"
PROCESS_NAME = "claude"
VERSIONS_FOLDER = "versions"  # a versioned install keeps each binary at claude/versions/<version>
NOT_JSON = "agents output is not JSON"
DOCTOR_HINT = "(see coven doctor)"
JSON_STARTS = ("[", "{")
EPOCH = datetime.fromtimestamp(0, tz=UTC)

type Runner = Callable[[list[str]], subprocess.CompletedProcess[str]]


class PmError(CovenError):
    """A named failure of `coven pm`; exit 2, so exit 1 stays for "a PM needs the GM"."""

    exit_code = 2


@dataclass(frozen=True)
class Row:
    name: str
    kind: str | None
    id: str | None
    session_id: str | None
    cwd: str | None
    state: str | None
    status: str | None
    pid: int | None
    started_at: datetime | None


def _text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _integer(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _epoch_milliseconds(value: object) -> datetime | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        return datetime.fromtimestamp(value / 1000, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _row(entry: dict[str, object]) -> Row:
    return Row(
        name=_text(entry.get("name")) or "",
        kind=_text(entry.get("kind")),
        id=_text(entry.get("id")),
        session_id=_text(entry.get("sessionId")),
        cwd=_text(entry.get("cwd")),
        state=_text(entry.get("state")),
        status=_text(entry.get("status")),
        pid=_integer(entry.get("pid")),
        started_at=_epoch_milliseconds(entry.get("startedAt")),
    )


def _json_text(raw: str) -> str:
    """`raw` from its first line that starts a JSON list or object (a warning may come before)."""
    lines = raw.lstrip().splitlines()
    for number, line in enumerate(lines):
        if line.startswith(JSON_STARTS):
            return "\n".join(lines[number:])
    raise PmError(NOT_JSON)


def _entries(document: object) -> list[object]:
    if isinstance(document, list):
        return document
    if isinstance(document, dict):
        lists = [value for value in document.values() if isinstance(value, list)]
        if len(lists) == 1:
            return lists[0]
    raise PmError(NOT_JSON)


def parse_rows(raw: str) -> list[Row]:
    """The rows in the CLI's JSON output: a list, or an object holding one list."""
    try:
        document = safe_json.loads(_json_text(raw))
    except ValueError as error:  # bad JSON, a huge integer, or nesting past safe_json.MAX_DEPTH
        raise PmError(NOT_JSON) from error
    return [_row(entry) for entry in _entries(document) if isinstance(entry, dict)]


def _run(argv: list[str]) -> subprocess.CompletedProcess[str]:
    # shortcut: no size cap on the agents output, add one if a real listing ever exceeds ~10 MB.
    return subprocess.run(
        argv,
        capture_output=True,
        encoding="utf-8",
        errors="replace",  # output that is not UTF-8 is a parse error later, not a crash here
        timeout=AGENTS_TIMEOUT,
        check=False,
    )


def load_rows(run: Runner | None = None) -> list[Row]:
    """Ask the CLI for every session row; `run` stands in for the subprocess in tests."""
    runner = run or _run
    try:
        result = runner(list(AGENTS_COMMAND))
    except FileNotFoundError as error:
        raise PmError(f"{PROCESS_NAME} not found on PATH") from error
    except subprocess.TimeoutExpired as error:
        raise PmError(f"agents did not answer in {AGENTS_TIMEOUT} s") from error
    except OSError as error:  # strerror only: the error's text may name an absolute path
        raise PmError(
            f"cannot run {PROCESS_NAME}: {error.strerror or 'input/output error'}"
        ) from error
    if result.returncode != 0:
        detail = clean_line(result.stderr or "")
        shown = f": {detail}" if detail else ""
        raise PmError(f"agents exited {result.returncode}{shown} {DOCTOR_HINT}")
    return parse_rows(result.stdout)


def pm_groups(rows: list[Row]) -> dict[str, list[Row]]:
    """Background project-manager rows by name, each list newest first."""
    groups: dict[str, list[Row]] = {}
    for row in rows:
        if row.kind == "background" and row.name.startswith(PM_NAME_PREFIX):
            groups.setdefault(row.name, []).append(row)
    for group in groups.values():
        # A row without a start time counts as the oldest.
        group.sort(key=lambda row: row.started_at or EPOCH, reverse=True)
    return groups


def row_state(row: Row) -> str:
    """The row's `state`, else the state its `status` implies, else `unknown`."""
    return row.state or STATUS_STATES.get(row.status or "", UNKNOWN_STATE)


def pid_alive(pid: int, proc_root: Path = Path("/proc")) -> bool:
    """Whether `pid` is a running CLI process, from its command line under `proc_root`.

    Only argv[0] counts, so a process that merely names the CLI among its arguments, such as a
    grep for it, is not taken for one. A live PM shows the CLI's name there, with `bg-spare`, or,
    when started from a versioned install, a file in a `claude/versions` folder, such as
    `.../claude/versions/2.1.328`. That folder pair is matched by path component, never by
    substring.
    """
    try:
        cmdline = (proc_root / str(pid) / "cmdline").read_bytes()
    except OSError:
        return False
    program = cmdline.decode(errors="replace").split("\0", 1)[0]
    return PROCESS_NAME in os.path.basename(program) or _in_versions_folder(PurePosixPath(program))


def _in_versions_folder(program: PurePosixPath) -> bool:
    """Whether `program` is a file directly in a `versions` folder that is directly in `claude`."""
    return program.parent.name == VERSIONS_FOLDER and program.parent.parent.name == PROCESS_NAME
