"""`coven approve`: write and commit the memory store's approval files for a production step.

The production guard (`hooks/prod_guard.py`) lets a call through only when a live approval committed
at the store's HEAD names it exactly. This module writes those files from a step file the general
manager put in the store (`approvals/<id>.step`), so an approval never covers more than the step
names: a command or tool no guard.json target claims is an error, never a silent skip.

The step comes from a file inside the store and never from the command line or a path elsewhere: the
guard refuses a restricted session any write into the store, so a session that merely runs this
command cannot make it approve anything. Which target a command or tool belongs to is decided by
the guard's own `explain`, the same code the hook runs, so the two cannot disagree.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

from coven import guard
from coven.config import load_config
from coven.errors import CovenError, warn
from coven.memory import store as memory_store

ROLE_VAR = "COVEN_SESSION_ROLE"
# Where a step is judged from: a PM runs it in a project, never in the store, whose own cwd the
# guard refuses a PM.
PM_CWD = "/"
APPROVALS_DIR = "approvals"
STEP_SUFFIX = ".step"
JSON_SUFFIX = ".json"
FENCE = "```"
TOOL_PREFIX = "tool:"
# What approve uses from the guard module; `_hook` checks they exist.
GUARD_NAMES = (
    "MAX_APPROVAL",
    "StoreError",
    "GuardError",
    "PayloadError",
    "load_targets",
    "explain",
)
# Equal to the guard's MAX_APPROVAL (a test compares them): the guard ignores an approval that
# expires further ahead.
MAX_DURATION = timedelta(hours=12)
DURATION = re.compile(r"(?:(?P<hours>[0-9]{1,4})h)?(?:(?P<minutes>[0-9]{1,4})m)?")
# A name safe as one path segment: an approval id, and a system name inside a file name.
FILE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
# Seconds that matching every command against every guard.json pattern may take (the guard's own
# whole check is allowed 20): a pattern that backtracks must stop the run, not hang it.
PATTERN_DEADLINE = 5.0


@dataclass(frozen=True)
class Step:
    """What a step file names: the exact Bash commands and MCP tools to approve."""

    commands: list[str]
    tools: list[str]


@dataclass
class Grant:
    """What one approval file will name."""

    commands: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Change:
    """One commit: files to write (name to text) and files to delete, all under `approvals/`."""

    message: str
    write: dict[str, str] = field(default_factory=dict)
    delete: tuple[str, ...] = ()

    @property
    def names(self) -> list[str]:
        return [*self.write, *self.delete]


def parse_duration(text: str) -> timedelta:
    """`90m`, `2h` or `1h30m`; CovenError when it is anything else, zero or over the guard's cap."""
    found = DURATION.fullmatch(text)
    if not text or found is None:
        raise CovenError(f"bad duration {text!r}: use a form like 90m, 2h or 1h30m")
    duration = timedelta(hours=int(found["hours"] or 0), minutes=int(found["minutes"] or 0))
    if duration <= timedelta(0):
        raise CovenError(f"bad duration {text!r}: it must be longer than zero")
    if duration > MAX_DURATION:
        raise CovenError(f"bad duration {text!r}: an approval lasts at most 12h")
    return duration


def parse_step(text: str) -> Step:
    """The commands and tools in a step file; each stored stripped, once, in order.

    Lines are split on "\\n" only (one trailing "\\r" goes), so a Unicode line separator inside a
    command stays in it. A line starting with three or more backticks opens a block; the block is
    one command, its lines joined with newlines, and it closes only on a line that is exactly the
    opener's backtick run. Any other fence line inside a block is an error. Outside blocks every
    non-blank line is one command, except `tool: <exact name>`, which names an MCP tool. The guard
    compares a call's command stripped, so a command is stored stripped, and a carriage return left
    inside one is an error: no shell call could carry it the way the file does.
    """
    commands: list[str] = []
    tools: list[str] = []
    block: list[str] | None = None
    closer = ""
    for number, raw in enumerate(text.split("\n"), 1):
        line = raw.removesuffix("\r")
        if block is None:
            if line.startswith(FENCE):
                closer = "`" * (len(line) - len(line.lstrip("`")))
                block = []
            elif stripped := line.strip():
                if stripped.startswith(TOOL_PREFIX):
                    tools.append(_tool_name(stripped, number))
                else:
                    commands.append(_command(stripped, number))
        elif line.rstrip() == closer:
            command = "\n".join(block).strip()
            if not command:
                raise CovenError(f"line {number}: empty fenced block in the step file")
            commands.append(_command(command, number))
            block = None
        elif line.startswith(FENCE):
            raise CovenError(
                f"line {number}: a fence line inside a fenced block; only {closer} alone closes it"
            )
        else:
            block.append(line)
    if block is not None:
        raise CovenError("unclosed fenced block in the step file")
    return Step(list(dict.fromkeys(commands)), list(dict.fromkeys(tools)))


def _command(command: str, number: int) -> str:
    if "\r" in command:
        raise CovenError(
            f"line {number}: a carriage return inside a command; use \\n line ends in the step file"
        )
    return command


def _tool_name(line: str, number: int) -> str:
    name = line.removeprefix(TOOL_PREFIX).strip()
    if not name or len(name.split()) > 1:
        raise CovenError(f"line {number}: 'tool:' needs one exact tool name")
    return name


# --- matching, by the guard's own rules ---


def _hook() -> ModuleType:
    """The production guard, the rules that will read the files written here."""
    module = guard.load_guard()
    missing = [name for name in GUARD_NAMES if not hasattr(module, name)]
    if missing:
        raise CovenError(f"the production guard at {guard.HOOK} lacks {', '.join(missing)}")
    return module


def _committed_systems(hook: ModuleType, store: Path) -> set[str]:
    """The systems of the targets enforced at the store's HEAD, read and validated by the guard."""
    try:
        enforced = hook.load_targets(store)
    except hook.StoreError as error:
        raise CovenError(f"cannot read the committed store {store}: {error}") from error
    except hook.GuardError as error:
        raise CovenError(f"cannot use the committed guard.json of {store}: {error}") from error
    if enforced.targets is None:
        raise CovenError(f"{store} has no committed systems/guard.json to approve against")
    if enforced.invalid_head is not None:
        warn(f"HEAD's guard.json is invalid ({enforced.invalid_head}); its last valid one is used")
    return {target["system"] for target in enforced.targets}


def _owners(
    hook: ModuleType, store: Path, payload: Mapping[str, object], now: datetime, what: str
) -> set[str]:
    """The systems whose targets the guard says this call reaches.

    CovenError for a call the guard refuses a PM whatever is approved: with a guard on the PM runs
    every step, so no approval file would let it through."""
    try:
        found = hook.explain(payload, store, None, now)
    except hook.PayloadError as error:
        raise CovenError(f"not a call the guard can judge: {error}") from error
    if found.unreadable:
        raise CovenError(found.unreadable)
    if found.restricted:
        raise CovenError(
            f"a PM cannot run this ({found.restricted}); ask the PM for a form the guard can read:"
            f" {what}"
        )
    if payload["tool_name"] != "Bash":
        return {hit.system for hit in found.hits if hit.via == "tool"}
    for hit in found.hits:
        if hit.via == "tool":
            # An approval file cannot answer a target that lists the Bash tool itself.
            raise CovenError(
                f"the target of {hit.system} lists the {hit.trigger} tool, so the guard judges every"
                " Bash call by it: no command approval can lift that; change guard.json"
            )
    return {hit.system for hit in found.hits}


def _chosen(owners: set[str], system: str | None, what: str) -> set[str]:
    if system is None:
        if not owners:
            raise CovenError(f"no guard.json target matches {what}")
        return owners
    if system not in owners:
        raise CovenError(f"system {system} does not claim {what}")
    others = owners - {system}
    if others:
        raise CovenError(
            f"also claimed by {', '.join(sorted(others))}, so it needs their approval too;"
            f" approve it without --system: {what}"
        )
    return {system}


def _assign(
    hook: ModuleType, store: Path, step: Step, system: str | None, now: datetime
) -> dict[str, Grant]:
    """Each command and tool for every system whose target claims it.

    CovenError for one nothing claims. With `system`, the one system takes everything, and a command
    or tool it does not claim, or that another system claims as well, is an error: an approval for
    one system would leave the other's check unanswered.
    """
    systems = _committed_systems(hook, store)
    if system is not None and system not in systems:
        raise CovenError(f"guard.json has no target for system {system!r}")
    grants: dict[str, Grant] = {}
    for command in step.commands:
        payload = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": PM_CWD}
        what = f"the command: {command}"
        for owner in _chosen(_owners(hook, store, payload, now, what), system, what):
            grants.setdefault(owner, Grant()).commands.append(command)
    for tool in step.tools:
        payload = {"tool_name": tool, "tool_input": {}, "cwd": PM_CWD}
        what = f"the tool: {tool}"
        for owner in _chosen(_owners(hook, store, payload, now, what), system, what):
            grants.setdefault(owner, Grant()).tools.append(tool)
    return grants


# --- the store ---


def _refuse_role() -> None:
    if ROLE_VAR in os.environ:
        raise CovenError(
            "only the general manager writes approvals; this session has a role set, so ask it"
        )


def _check_id(approval_id: str) -> None:
    if FILE_NAME.fullmatch(approval_id) is None:
        raise CovenError(
            f"bad approval id {approval_id!r}: 1 to 100 letters, digits, '.', '_' and '-',"
            " starting with a letter or digit"
        )


def _store() -> Path:
    path = load_config().memory_path
    if not (path / ".git").exists():
        raise CovenError(f"no memory store at {path}: run coven memory init first")
    if (path / APPROVALS_DIR).is_symlink():
        raise CovenError(f"{path / APPROVALS_DIR} is a link; approvals live in a real folder")
    return path


def _git(store: Path, *args: str) -> str:
    """git's stdout in the store, through the store's own git runner."""
    return memory_store.git(store, *args).stdout


def _status(store: Path) -> list[str]:
    return _git(store, "status", "--porcelain", "--untracked-files=all").splitlines()


def _require_only(store: Path, step_file: Path) -> None:
    """The store holds nothing but the one new, untracked step file, so the commit can hold nothing
    else and deleting the step file afterwards leaves the store clean."""
    rel = step_file.relative_to(store).as_posix()
    if _git(store, "ls-files", "--", rel).strip():
        raise CovenError(
            f"{rel} is already tracked in {store}: approving would leave its deletion uncommitted;"
            " remove it from the store's history first, then write the step file again"
        )
    status = _status(store)
    if status != [f"?? {rel}"]:
        raise CovenError(
            f"{store} must hold nothing but the one new step file {rel}; git status shows:\n"
            + ("\n".join(status) or "(nothing)")
        )


def _require_clean(store: Path) -> None:
    status = _status(store)
    if status:
        raise CovenError(
            f"{store} must have no uncommitted changes to revoke; git status shows:\n"
            + "\n".join(status)
        )


def _step_file(store: Path, approval_id: str) -> Path:
    """The step file inside the store; CovenError unless it is a regular file."""
    path = store / APPROVALS_DIR / f"{approval_id}{STEP_SUFFIX}"
    if not path.exists() and not path.is_symlink():
        raise CovenError(
            f"no step file {path}: write the PROD-STEP's commands there first, then run approve"
        )
    if path.is_symlink() or not path.is_file():
        raise CovenError(f"{path} is not a regular file")
    return path


def _read_step(path: Path) -> Step:
    try:
        step = parse_step(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as error:
        raise CovenError(f"cannot read the step file {path}: {error}") from error
    if not step.commands and not step.tools:
        raise CovenError(f"nothing to approve in {path}")
    return step


def _record(path: Path) -> dict[str, Any] | None:
    """An approval file's JSON object, None for a file that is not one."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _expires(record: dict[str, Any] | None) -> datetime | None:
    """The record's expiry when it is an ISO-8601 time with an offset, as the guard reads it."""
    value = record.get("expires") if record else None
    try:
        expires = datetime.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None
    return expires if expires is not None and expires.utcoffset() is not None else None


def _approval_names(store: Path) -> list[str]:
    folder = store / APPROVALS_DIR
    if not folder.is_dir():
        return []
    return sorted(
        entry.name
        for entry in folder.iterdir()
        if entry.name.endswith(JSON_SUFFIX) and (entry.is_file() or entry.is_symlink())
    )


def _expired(store: Path, now: datetime) -> tuple[str, ...]:
    """The approval files whose expiry has passed at `now`. A file the guard cannot read is not
    one it could count, and not ours to judge: it stays."""
    found = []
    for name in _approval_names(store):
        expires = _expires(_record(store / APPROVALS_DIR / name))
        if expires is not None and expires <= now:
            found.append(name)
    return tuple(found)


def _files_of(store: Path, approval_id: str) -> tuple[str, ...]:
    """`<id>.json` and each `<id>-*.json` whose own id is this one (`a-b.json` may belong to the
    approval `a-b`, which `a` must not remove, and `a.json` may hold another id)."""
    own_name = f"{approval_id}{JSON_SUFFIX}"
    system_prefix = f"{approval_id}-"
    names = []
    for name in _approval_names(store):
        if name == own_name or name.startswith(system_prefix):
            record = _record(store / APPROVALS_DIR / name)
            if record is not None and record.get("id") == approval_id:
                names.append(name)
    return tuple(names)


def _approval_files(approval_id: str, grants: dict[str, Grant], expires: str) -> dict[str, str]:
    """File name -> text: `<id>.json` for one system, else `<id>-<system>.json` for each."""
    files: dict[str, str] = {}
    for system, grant in sorted(grants.items()):
        name = approval_id
        if len(grants) > 1:
            if FILE_NAME.fullmatch(system) is None:
                raise CovenError(
                    f"system {system!r} cannot be part of a file name: approve it alone with --system"
                )
            name = f"{approval_id}-{system}"
        record = {
            "id": approval_id,
            "system": system,
            "commands": grant.commands,
            "tools": grant.tools,
            "expires": expires,
        }
        files[f"{name}{JSON_SUFFIX}"] = json.dumps(record, indent=2) + "\n"
    return files


def _require_new(store: Path, files: dict[str, str]) -> None:
    for name in files:
        path = store / APPROVALS_DIR / name
        if path.exists() or path.is_symlink():
            raise CovenError(f"{path} exists already: pick another approval id")


# --- the commit ---


def _head(store: Path) -> str:
    return _git(store, "rev-parse", "HEAD").strip()


def _rel(name: str) -> str:
    return f"{APPROVALS_DIR}/{name}"


def _write_and_stage(store: Path, change: Change, written: list[Path]) -> None:
    folder = store / APPROVALS_DIR
    folder.mkdir(exist_ok=True)
    for name, text in change.write.items():
        with (folder / name).open("x", encoding="utf-8") as handle:
            written.append(folder / name)
            handle.write(text)
    if change.write:
        _git(store, "add", "--", *map(_rel, change.write))
    if change.delete:
        _git(store, "rm", "-q", "--", *map(_rel, change.delete))


def _commit_change(store: Path, change: Change) -> None:
    """Write, stage and commit only this change's files, through the store's own hooks, or leave
    nothing behind: no new file, every deleted file back, nothing staged.

    A commit that timed out or errored may still have landed (HEAD moved): that counts as success.
    When git cannot say, the files stay where they are.
    """
    start = _head(store)
    written: list[Path] = []
    rels = [_rel(name) for name in change.names]
    try:
        _write_and_stage(store, change, written)
        _git(store, "commit", "-q", "-m", change.message, "--", *rels)
    except (CovenError, OSError) as error:
        if _landed(store, start):
            return
        _rollback(store, change, written, error)
        if isinstance(error, OSError):
            raise CovenError(f"cannot write the approval in {store}: {error}") from error
        raise


def _landed(store: Path, start: str) -> bool:
    """Whether the commit moved HEAD; CovenError when git cannot answer."""
    try:
        return _head(store) != start
    except CovenError as error:
        raise CovenError(
            f"git cannot tell whether the commit landed in {store} ({error}): check git log"
            " there; the approval files were left in place"
        ) from error


def _rollback(store: Path, change: Change, written: list[Path], cause: Exception) -> None:
    """Unstage this change's files, delete the ones this run wrote, restore the ones it deleted."""
    rels = [_rel(name) for name in change.names]
    problems: list[str] = []
    try:
        _git(store, "reset", "-q", "--", *rels)
        if change.delete:
            _git(store, "checkout", "--", *map(_rel, change.delete))
    except CovenError as error:
        problems.append(str(error))
    for path in written:
        try:
            path.unlink(missing_ok=True)
        except OSError as error:
            problems.append(f"cannot remove {path}: {error}")
    if problems:
        raise CovenError(
            f"{cause}; and the rollback in {store} did not finish ({'; '.join(problems)}): check"
            " git status there"
        ) from cause


def _remove_step(step_file: Path) -> None:
    """The step is done: a leftover would make the store dirty for the next approval."""
    try:
        step_file.unlink(missing_ok=True)
    except OSError as error:
        raise CovenError(
            f"the approval is committed, but cannot delete {step_file}: {error}; remove it by hand"
        ) from error


# --- the two operations ---


def approve(
    approval_id: str, expires_in: str, now: datetime, system: str | None = None
) -> list[str]:
    """Approve the step in `approvals/<id>.step`; the lines to print.

    Writes, stages and commits one approval file per system, deletes the approval files that have
    expired at `now` in the same commit, then deletes the step file. `now` must carry a UTC offset.
    Nothing is written until every check has passed, and a failed commit puts everything back and
    keeps the step file.
    """
    _refuse_role()
    _check_id(approval_id)
    duration = parse_duration(expires_in)
    store = _store()
    step_file = _step_file(store, approval_id)
    _require_only(store, step_file)
    step = _read_step(step_file)
    hook = _hook()
    grants = guard.run_with_deadline(
        lambda: _assign(hook, store, step, system, now), PATTERN_DEADLINE
    )
    expires = (now + duration).replace(microsecond=0).isoformat(timespec="seconds")
    files = _approval_files(approval_id, grants, expires)
    _require_new(store, files)
    pruned = _expired(store, now)
    systems = ", ".join(sorted(grants))
    _commit_change(store, Change(f"approve {approval_id}: {systems}", files, pruned))
    _remove_step(step_file)
    return _report(files, grants, expires) + [f"pruned {name}" for name in pruned]


def revoke(approval_id: str) -> list[str]:
    """Remove the approval files of `approval_id` in one commit; the lines to print."""
    _refuse_role()
    _check_id(approval_id)
    store = _store()
    _require_clean(store)
    names = _files_of(store, approval_id)
    if not names:
        raise CovenError(f"no approval files for {approval_id!r} in {store / APPROVALS_DIR}")
    _commit_change(store, Change(f"revoke {approval_id}", delete=names))
    return [f"revoked {name}" for name in names]


def _report(files: dict[str, str], grants: dict[str, Grant], expires: str) -> list[str]:
    """One `approved` line per file, then every command (JSON-quoted, so exact) and tool in it."""
    lines: list[str] = []
    for name, (system, grant) in zip(files, sorted(grants.items()), strict=True):
        lines.append(
            f"approved {name} system={system} expires={expires}"
            f" commands={len(grant.commands)} tools={len(grant.tools)}"
        )
        lines += [
            f"  command: {json.dumps(command, ensure_ascii=True)}" for command in grant.commands
        ]
        lines += [f"  tool: {json.dumps(tool, ensure_ascii=True)}" for tool in grant.tools]
    return lines


# --- the command line ---


def run_approve(args: argparse.Namespace) -> int:
    if args.revoke is not None:
        if args.approval_id is not None or args.expires_in is not None or args.system is not None:
            raise CovenError("--revoke <id> takes no other id, --expires-in or --system")
        lines = revoke(args.revoke)
    else:
        if args.approval_id is None or args.expires_in is None:
            raise CovenError("give an approval id and --expires-in <duration>, or --revoke <id>")
        lines = approve(args.approval_id, args.expires_in, datetime.now().astimezone(), args.system)
    print("\n".join(lines))
    return 0


def register(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "approve",
        help="approve the production step in the store's approvals/<id>.step, or revoke it",
        description=(
            "Match the commands and tools in <store>/approvals/<id>.step to the committed guard.json"
            " targets, write one approval file per system, commit them, and delete the step file."
            " It refuses unless the step file is the only change in the store. Print each file with"
            " every command and tool it approves, to compare with the PROD-STEP. Expired approval"
            " files go in the same commit. Never pushes. Refuses in a session with a role set."
        ),
    )
    parser.add_argument("approval_id", nargs="?", metavar="id", help="the step file's name")
    parser.add_argument(
        "--expires-in",
        help="how long the approval lasts: 90m, 2h or 1h30m, at most 12h (required)",
    )
    parser.add_argument(
        "--system",
        help="approve everything for this one system; refused for what it does not alone claim",
    )
    parser.add_argument(
        "--revoke",
        metavar="id",
        help="remove the approval files of this id in one commit (no step file needed)",
    )
    parser.set_defaults(handler=run_approve)
