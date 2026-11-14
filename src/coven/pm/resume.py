"""What `coven pm resume` does: writes the resume prompt file and builds the command to run.

The command is only printed. Nothing here starts or stops a session; the prompt file is the one
thing written.
"""

from __future__ import annotations

import errno
import os
import shlex
from dataclasses import dataclass
from pathlib import Path

from coven import paths
from coven.config import load_config
from coven.pm import report
from coven.pm.profile import refuse_option_like, session_flags
from coven.pm.rows import LIVE, PM_NAME_PREFIX, PmError, Row, pid_alive, pm_groups, row_state
from coven.pm.text import (
    clean_block,
    clean_line,
    is_valid_code,
    is_valid_gm_name,
    is_valid_row_id,
)
from coven.pm.transcript import Facts, find_transcript, read_transcript

PROMPT_SUFFIX = ".prompt.md"
PROMPT_MODE = 0o600
BRIEFS_DIR_MODE = 0o700
ROLE_FILE = Path("agents") / "project-manager.md"
ROLE_FILE_MISSING = "the project-manager agent file is missing: reinstall the coven plugin"


@dataclass(frozen=True)
class ResumeRequest:
    code: str
    gm: str
    session: str | None
    adds: list[Path]

    def __post_init__(self) -> None:
        if not is_valid_code(self.code):
            raise PmError(f"not a project code: {clean_line(self.code)}")
        if not is_valid_gm_name(self.gm):
            raise PmError(f"not a GM session name: {clean_line(self.gm)}")


@dataclass(frozen=True)
class ResumePlan:
    command: str
    prompt_file: Path


def _session_id(request: ResumeRequest, group: list[Row] | None) -> str:
    name = f"{PM_NAME_PREFIX}{request.code}"
    row_session = group[0].session_id if group else None
    if request.session is None:
        if row_session:
            return row_session
        raise PmError(f"{name} has no session id in agents: pass --session <sessionId>")
    if row_session and request.session != row_session:
        raise PmError(f"--session differs from the session id agents lists for {name}")
    return request.session


def _read_facts(session: str) -> Facts:
    try:
        found = find_transcript(report.transcripts_dir(), session)
    except ValueError:
        raise PmError("not a session id: it is a lower-case UUID") from None
    except OSError:
        raise PmError("the session's transcript cannot be read") from None
    if found is None:
        raise PmError("no transcript for that session id")
    try:
        return read_transcript(found)
    except OSError:
        raise PmError("the session's transcript cannot be read") from None


def _running_rows(group: list[Row], proc_root: Path) -> list[Row]:
    """Rows that stop a resume: any row whose process runs, whatever its state (a `done` row can
    still hold its process), and a row without a pid that looks live."""
    return [
        row
        for row in group
        if (pid_alive(row.pid, proc_root) if row.pid is not None else row_state(row) in LIVE)
    ]


def _refuse_if_running(group: list[Row], proc_root: Path) -> None:
    live = _running_rows(group, proc_root)  # a row with a dead process is not running
    if len(live) > 1:
        raise PmError(
            f"{group[0].name} is a duplicate: stop all but the newest, then check its state"
        )
    if live:
        row_id = live[0].id
        stop = f": claude stop {row_id}" if row_id and is_valid_row_id(row_id) else ""
        raise PmError(f"{group[0].name} is running: stop it first{stop}")


def _working_directory(group: list[Row] | None, facts: Facts) -> str:
    cwd = group[0].cwd if group and group[0].cwd else facts.cwd
    if not cwd or clean_block(cwd) != cwd or "\n" in cwd or not Path(cwd).is_dir():
        raise PmError("the session's working directory is not an existing directory")
    return cwd


def _read_adds(adds: list[Path]) -> list[str]:
    texts = []
    for number, path in enumerate(adds, start=1):
        try:
            texts.append(path.read_text(encoding="utf-8"))
        except UnicodeDecodeError:
            raise PmError(f"--add file {number} is not UTF-8 text") from None
        except OSError as error:
            raise PmError(f"cannot read {clean_line(path.name)}: {error.strerror}") from error
    return texts


def _prompt_text(gm: str, adds: list[str]) -> str:
    """The resume note, then each `--add` file; the PM's role comes from its `--agent`."""
    sections = [f"RESUMED by {gm}\nGM session: {gm}\n", *adds]
    return "\n".join(section if section.endswith("\n") else section + "\n" for section in sections)


def _write_prompt(path: Path, text: str) -> None:
    folder = path.parent
    folder.mkdir(mode=BRIEFS_DIR_MODE, parents=True, exist_ok=True)
    if folder.stat().st_uid != os.getuid():  # the GM's Write tool may have made it 0755 before
        raise PmError("the briefs folder belongs to another user")
    folder.chmod(BRIEFS_DIR_MODE)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, PROMPT_MODE)
    except OSError as error:
        if error.errno == errno.ELOOP:  # a link there would point the write somewhere else
            raise PmError("the prompt file is a symbolic link: remove it") from None
        raise
    os.fchmod(descriptor, PROMPT_MODE)  # a file left by an earlier run keeps its old mode otherwise
    with os.fdopen(descriptor, "w", encoding="utf-8") as prompt:
        prompt.write(text)


def prepare_resume(rows: list[Row], request: ResumeRequest, *, proc_root: Path) -> ResumePlan:
    """Check that the PM can be resumed, write its prompt file and build the command to run.

    Every check comes before the write; a refusal is a `PmError` naming the reason. A running PM
    is reported first, before a missing transcript.
    """
    name = f"{PM_NAME_PREFIX}{request.code}"
    group = pm_groups(rows).get(name)
    if group:
        _refuse_if_running(group, proc_root)
    session = _session_id(request, group)
    facts = _read_facts(session)
    if name not in facts.agent_names:
        raise PmError(f"that session is not named {name}")
    cwd = _working_directory(group, facts)
    if not (paths.root() / ROLE_FILE).is_file():
        raise PmError(ROLE_FILE_MISSING)
    adds = _read_adds(request.adds)
    prompt_file = paths.briefs_dir() / f"{name}{PROMPT_SUFFIX}"
    text = _prompt_text(request.gm, adds)
    refuse_option_like(text, "prompt")
    _write_prompt(prompt_file, text)
    flags = session_flags(request.code, load_config().memory_path)
    command = (
        f"cd {shlex.quote(cwd)} && claude --bg --resume {session} {flags}"
        f' "$(cat {shlex.quote(str(prompt_file))})"'
    )
    return ResumePlan(command, prompt_file)
