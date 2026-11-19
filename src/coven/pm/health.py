"""What `coven pm health` prints: the project-manager sessions that need the general manager.

A line names what is wrong and the command that fixes it. The state records the GM keeps in the
memory store say which PMs should be running; the rows and transcripts say which are.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from coven.config import load_config
from coven.pm import report
from coven.pm.report import STALE, PmView, Scene
from coven.pm.rows import PM_NAME_PREFIX, PmError, Row, pm_groups
from coven.pm.text import clean_line, code_for_key, is_valid_code, is_valid_row_id

PROJECTS_DIR = "projects"
STATE_SUFFIX = "-state.md"
UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
STATUS_LINE = re.compile(r"Project status:\s*(running|idle|waiting for a slot|done)\b")
SESSION_LINE = re.compile(rf"sessionId:?\s+({UUID})")
NO_STATUS = "no 'Project status:' line"
RUNNING = "running"
DONE = "done"
NOT_EXPECTED_ALIVE = (DONE, "waiting for a slot")
EXPECTED_ALIVE = (RUNNING, "idle")
DEAD_STATES = ("stopped", "failed", STALE)
UNKNOWN_PREFIX = "unknown ("
INVALID_CODE = "invalid project code"
NO_SESSION_ID = "no sessionId in the record: launch a new PM from the record"
NO_STORE = "no memory store: run `coven memory init`"


@dataclass(frozen=True)
class Record:
    """What `health` reads from one project's state record.

    `key` is the project's folder name in the store; `code` is the PM code derived from it.
    """

    key: str
    code: str
    status: str | None
    session_id: str | None
    problem: str | None


def _last(pattern: re.Pattern[str], text: str) -> str | None:
    """The first group of the last match: a record grows at the end, so the newest line is last."""
    found = pattern.findall(text)
    return found[-1] if found else None


def _parse(key: str, text: str) -> Record:
    status = _last(STATUS_LINE, text)
    return Record(
        key=key,
        code=code_for_key(key),
        status=status,
        session_id=_last(SESSION_LINE, text),
        problem=None if status else NO_STATUS,
    )


def _read_text(file: Path, shown: str) -> str:
    """The text of the state record `file`; `shown` names it in a problem line."""
    if file.is_symlink() or not file.is_file():
        raise ValueError(f"'{shown}': not a regular file")
    try:
        data = file.read_bytes()
        if b"\0" in data:
            raise ValueError(f"'{shown}': a binary file")
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError(f"'{shown}': not UTF-8 text") from None
    except OSError as error:
        raise ValueError(f"'{shown}': {error.strerror or 'cannot be read'}") from None


def _read_record(projects: Path, key: str) -> Record | None:
    """The record of the project in folder `key`, or `None` when the folder holds no state file."""
    name = f"{key}{STATE_SUFFIX}"
    file = projects / key / name
    if not (file.exists() or file.is_symlink()):
        return None
    code = code_for_key(key)
    if not is_valid_code(code):  # its name would end up in a command
        return Record(key, code, None, None, INVALID_CODE)
    try:
        return _parse(key, _read_text(file, f"{PROJECTS_DIR}/{key}/{name}"))
    except ValueError as error:
        return Record(key, code, None, None, str(error))


def _clash_problem(record: Record, records: list[Record]) -> str | None:
    """The problem of `record` when other records derive the same PM code; they are sorted."""
    others = [other.key for other in records if other.code == record.code and other is not record]
    if not others or not is_valid_code(record.code):
        return None
    return f"same PM code '{record.code}' as {', '.join(others)}"


def _without_clashes(records: list[Record]) -> list[Record]:
    """`records`, those whose key shares a PM code with another key turned into problems."""
    clashes = [_clash_problem(record, records) for record in records]
    return [
        record if clash is None else Record(record.key, record.code, None, None, clash)
        for record, clash in zip(records, clashes, strict=True)
    ]


def read_records(memory_path: Path) -> list[Record]:
    """Every `projects/<key>/<key>-state.md` of the store; no store is a `PmError`."""
    if not memory_path.is_dir():
        raise PmError(NO_STORE)
    projects = memory_path / PROJECTS_DIR
    try:
        folders = sorted(entry.name for entry in projects.iterdir() if entry.is_dir())
    except FileNotFoundError:
        return []
    except OSError as error:
        raise PmError(f"cannot read the memory store: {error.strerror}") from error
    found = (_read_record(projects, key) for key in folders)
    return _without_clashes([record for record in found if record is not None])


# --- the lines ------------------------------------------------------------------------------


def _pm_name(record: Record) -> str:
    return f"{PM_NAME_PREFIX}{record.code}"


def _record_line(record: Record, view: PmView | None, gm: str) -> list[str]:
    """Line 1: the record says `running` but the PM does not."""
    name = clean_line(_pm_name(record))
    code = clean_line(record.code)
    if view is None:
        if record.session_id is None:
            return [f"{name}: record says running, PM gone: {NO_SESSION_ID}"]
        resume = f"coven pm resume {code} --gm {gm} --session {record.session_id}"
        return [f"{name}: record says running, PM gone: {resume}"]
    if view.state == STALE and not view.has_pid:  # `resume` refuses until the row is stopped
        return [_with_stop_then_resume(f"{name}: record says running, PM {STALE}", view, gm)]
    if view.state in DEAD_STATES:
        return [f"{name}: record says running, PM {view.state}: coven pm resume {code} --gm {gm}"]
    return []


def _command_args(view: PmView) -> tuple[str, str] | None:
    """The row id and project code of `view` when both are safe to put in a command."""
    code = view.name.removeprefix(PM_NAME_PREFIX)
    if not (is_valid_code(code) and is_valid_row_id(view.ids[0])):
        return None
    return view.ids[0], code


def _attach_line(view: PmView) -> str:
    line = f"{clean_line(view.name)}: waits on a prompt"
    args = _command_args(view)
    return line if args is None else f"{line}: claude attach {args[0]}"


def _unsent_line(view: PmView) -> str:
    head = view.last_out.head if view.last_out is not None else None
    label = f"{head.prefix} {head.key}" if head is not None else "message"
    line = f"{clean_line(view.name)}: last message never reached the GM ({label})"
    args = _command_args(view)
    return line if args is None else f"{line}: coven pm status --full {args[1]}"


def _with_stop_then_resume(line: str, view: PmView, gm: str) -> str:
    """`line`, then the commands that stop `view`'s row and resume it, when both are safe."""
    args = _command_args(view)
    if args is None:
        return line
    return f"{line}: claude stop {args[0]}, then coven pm resume {args[1]} --gm {gm}"


def _renamed_line(view: PmView, gm: str) -> str:
    known = clean_line(view.known_gm or "")
    return _with_stop_then_resume(
        f"{clean_line(view.name)}: knows GM as '{known}' (current: {gm})", view, gm
    )


def _gone_line(view: PmView) -> str:
    line = f"{clean_line(view.name)}: row says working, session gone"
    args = _command_args(view)
    return line if args is None else f"{line}: claude stop {args[0]}"


def _view_lines(view: PmView, record: Record | None, gm: str) -> list[str]:
    """The lines after line 1 for one PM, in the spec's order.

    A `done` record means the GM closed the project: its asks are no longer waits. A stale row
    that no record expects alive is a dead row left behind, and the GM stops it.
    """
    name = clean_line(view.name)
    lines = []
    if view.state == STALE and (record is None or record.status in NOT_EXPECTED_ALIVE):
        lines.append(_gone_line(view))
    if view.state == report.DUPLICATE:
        ids = ", ".join(clean_line(identifier) for identifier in view.ids)
        lines.append(f"{name}: duplicate sessions {ids}: stop all but the newest")
    if view.state == "blocked":
        lines.append(_attach_line(view))
    if view.unsent:
        lines.append(_unsent_line(view))
    if view.known_gm and view.known_gm != gm and view.state not in DEAD_STATES + ("done",):
        lines.append(_renamed_line(view, gm))
    if record is None or record.status != DONE:
        lines += [
            f"{name}: waits on the GM: {head.prefix} {head.key} since {report.clock(at)}"
            for head, at in view.waits
        ]
    if view.drifted:
        lines.append(f"{name}: {report.DRIFT_NOTE}")
    if view.state.startswith(UNKNOWN_PREFIX):
        lines.append(f"{name}: {clean_line(view.state)}")
    return lines


def health_lines(views: list[PmView], records: list[Record], gm: str) -> list[str]:
    """The lines for `views` and `records`, PM by PM; a record with no view has no PM row."""
    gm = clean_line(gm)
    by_name = {view.name: view for view in views}
    by_record: dict[str, list[Record]] = {}
    for each in records:
        by_record.setdefault(_pm_name(each), []).append(each)
    needed = {_pm_name(r) for r in records if r.status in EXPECTED_ALIVE or r.problem}
    lines: list[str] = []
    for name in sorted(set(by_name) | needed):
        view, matching = by_name.get(name), by_record.get(name, [])
        record = matching[0] if len(matching) == 1 else None  # a shared code names no record
        if record is not None and record.status == RUNNING:
            lines += _record_line(record, view, gm)
        if view is not None:
            lines += _view_lines(view, record, gm)
        lines += [
            f"{clean_line(found.key)}: {clean_line(found.problem)}"
            for found in matching
            if found.problem
        ]
    return lines


# --- the command ----------------------------------------------------------------------------


def _considered(rows: list[Row], records: list[Record], scene: Scene) -> list[str]:
    """The codes of PM names that should run (a record says so) or do run (a live row)."""
    recorded = {_pm_name(r) for r in records if r.status in EXPECTED_ALIVE}
    groups = pm_groups(rows)
    running = {name for name, group in groups.items() if report.live_rows(group, scene.proc_root)}
    return sorted(
        name.removeprefix(PM_NAME_PREFIX) for name in (recorded | running) if name in groups
    )


def _cap_lines(rows: list[Row], scene: Scene, cap: int) -> list[str]:
    """`PMs running: N of cap M` when more PM names run than the cap allows."""
    running = sum(
        bool(report.live_rows(group, scene.proc_root)) for group in pm_groups(rows).values()
    )
    return [f"PMs running: {running} of cap {cap}"] if running > cap else []


def health_report(rows: list[Row], gm: str) -> list[str]:
    """The lines `coven pm health` prints; none means all is well."""
    config = load_config()
    records = read_records(config.memory_path)
    scene = Scene(report.utcnow(), gm, report.PROC_ROOT)
    codes = _considered(rows, records, scene)
    shown = report.views(report.transcripts_dir(), rows, scene, codes, True) if codes else []
    return health_lines(shown, records, gm) + _cap_lines(rows, scene, config.pm_cap)
