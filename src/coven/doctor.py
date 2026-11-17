"""`coven doctor`: a read-only health check of the coven install.

One line per check, `ok|warn|fail <name>: <detail>`; the exit code is 1 when any check fails. A
check that crashes fails alone with a one-line error and the others still run. Doctor writes
nothing, not even a log.
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any, Final, Literal

from coven import claude_version, guard, paths, safe_json, settings
from coven.claude_version import dotted
from coven.config import load_config
from coven.errors import CovenError
from coven.memory import store

type Status = Literal["ok", "warn", "fail"]

PYTHON_MINIMUM: Final = (3, 12)
SUPERPOWERS: Final = "superpowers@claude-plugins-official"
RECENT: Final = timedelta(hours=24)
ERRORS_SHOWN: Final = 5
LOG_TAIL_BYTES: Final = (
    1024 * 1024
)  # a log rotates at 1 MB; a larger file is not ours to read whole
DETAIL_LIMIT: Final = 160
GUARD_LOG: Final = "prod-guard"
REFUSALS_SHOWN: Final = 5
REFUSAL_TAIL_BYTES: Final = 64 * 1024
REFUSAL_FIELD_LIMIT: Final = 80
ROLE_CLASSES: Final = ("pm", "unset", "other")  # what the hook logs; never a raw role
SETUP_FIX: Final = "run coven setup --yes"
EARLIEST: Final = datetime.min.replace(tzinfo=UTC)


@dataclass(frozen=True)
class Outcome:
    status: Status
    detail: str
    # Indented lines under the check's line (the hook errors).
    more: tuple[str, ...] = ()


@dataclass(frozen=True)
class Result:
    name: str
    status: Status
    detail: str
    more: tuple[str, ...] = ()


def _ok(detail: str) -> Outcome:
    return Outcome("ok", detail)


def _one_line(text: str, limit: int = DETAIL_LIMIT) -> str:
    """`text` as one short printable line: log and tool output is data, never trusted layout."""
    flat = " ".join("".join(ch if ch.isprintable() else " " for ch in text).split())
    return flat if len(flat) <= limit else flat[: limit - 3] + "..."


def _tool(argv: list[str]) -> subprocess.CompletedProcess[str]:
    """Run a tool with the shared timeout and lenient decoding; `CovenError` when it cannot run."""
    name = argv[0]
    try:
        return claude_version.run_command(argv)
    except FileNotFoundError as error:
        raise CovenError(f"{name} not found on PATH") from error
    except subprocess.TimeoutExpired as error:
        raise CovenError(f"{name} did not answer in {claude_version.TIMEOUT} s") from error
    except OSError as error:
        raise CovenError(f"cannot run {name}: {error.strerror or 'input/output error'}") from error


def _first_line(done: subprocess.CompletedProcess[str]) -> str:
    return _one_line(done.stdout.strip().split("\n", 1)[0])


# ---- the checks -----------------------------------------------------------------------------


def _claude(_now: datetime) -> Outcome:
    try:
        version = claude_version.installed()
    except CovenError as error:
        return Outcome("fail", str(error))
    if version > claude_version.LAST_TESTED:
        return Outcome(
            "warn",
            f"{dotted(version)} is newer than {dotted(claude_version.LAST_TESTED)}, "
            "the last version coven was tested on",
        )
    return _ok(dotted(version))


def _python(_now: datetime) -> Outcome:
    try:
        done = _tool(["python3", "--version"])
    except CovenError as error:
        return Outcome("fail", f"{error}; hooks and the coven command need Python 3.12 or later")
    found = claude_version.VERSION.search(done.stdout or done.stderr)
    if done.returncode != 0 or found is None:
        return Outcome("fail", "could not read the version from `python3 --version`")
    major, minor, patch = (int(part) for part in found.groups())
    if (major, minor) < PYTHON_MINIMUM:
        return Outcome("fail", f"python3 is {major}.{minor}.{patch}; coven needs 3.12 or later")
    return _ok(f"{major}.{minor}.{patch}")


def _git(_now: datetime) -> Outcome:
    try:
        return _ok(_first_line(_tool(["git", "--version"])))
    except CovenError as error:
        return Outcome("fail", str(error))


def _uv(_now: datetime) -> Outcome:
    try:
        return _ok(_first_line(_tool(["uv", "--version"])))
    except CovenError as error:
        return Outcome("warn", f"{error}; the code standard's checks run through uv")


def _gh(_now: datetime) -> Outcome:
    try:
        done = _tool(["gh", "auth", "status"])
    except CovenError as error:
        return Outcome("warn", f"{error}; project managers need it")
    if done.returncode != 0:
        return Outcome("warn", "not logged in; run gh auth login (project managers need it)")
    return _ok("logged in")


def _is_install(entry: object) -> bool:
    return isinstance(entry, dict) and bool(entry.get("installPath"))


def _superpowers(_now: datetime) -> Outcome:
    file = paths.installed_plugins_file()
    try:
        data = safe_json.loads(file.read_text(encoding="utf-8", errors="replace"))
    except FileNotFoundError:
        return Outcome("fail", f"{file} does not exist; run claude plugin install {SUPERPOWERS}")
    except (OSError, ValueError) as error:
        return Outcome("fail", f"{file} cannot be read: {_one_line(str(error))}")
    plugins = data.get("plugins") if isinstance(data, dict) else None
    entry = plugins.get(SUPERPOWERS) if isinstance(plugins, dict) else None
    # Version 2 files hold a list of installs per plugin; the shim also accepts one object.
    installed = (
        any(_is_install(item) for item in entry) if isinstance(entry, list) else _is_install(entry)
    )
    if installed:
        return _ok(SUPERPOWERS)
    return Outcome(
        "fail", f"{SUPERPOWERS} is not installed; run claude plugin install {SUPERPOWERS}"
    )


def _config(_now: datetime) -> Outcome:
    try:
        load_config()
    except CovenError as error:
        return Outcome("fail", _one_line(str(error)))
    return _ok("valid")


def _store_path() -> Path:
    try:
        return load_config().memory_path
    except CovenError as error:
        raise CovenError("the store cannot be found while the config is invalid") from error


NOT_SET_UP: Final = "not set up yet; run /coven:setup"


def _store_state(path: Path) -> Outcome | None:
    """The outcome when there is no usable store at `path`, None when there is one."""
    if not path.exists():
        return Outcome("warn", NOT_SET_UP)  # a fresh install runs doctor before setup
    if not store.is_store(path):
        shown = _one_line(str(path))
        return Outcome("fail", f"{shown} is not a coven memory store; run coven memory init")
    return None


def _memory_store(_now: datetime) -> Outcome:
    path = _store_path()
    return _store_state(path) or _ok(_one_line(str(path)))


def _memory_hook(_now: datetime) -> Outcome:
    path = _store_path()
    if missing := _store_state(path):
        return missing
    problem = store.hook_problem(path)
    if problem:
        shown = _one_line(str(path))
        return Outcome("fail", f"{_one_line(problem)}; run coven memory init --path {shown}")
    return _ok("current")


def _cross_session(_now: datetime) -> Outcome:
    try:
        value = settings.read_setting(settings.CROSS_SESSION_KEY)
    except CovenError as error:
        return Outcome("warn", _one_line(str(error)))
    if value is settings.MISSING:
        return Outcome(
            "warn",
            "not set; messages between the GM and PMs wait for approval (offered by coven setup)",
        )
    return _ok(_one_line(json.dumps(value)))


def _owned(_now: datetime) -> Outcome:
    try:
        restores, skipped = settings.preview_undo()
    except CovenError as error:
        return Outcome("warn", _one_line(str(error)))
    if skipped:
        return Outcome("warn", _one_line("; ".join(skipped), 2 * DETAIL_LIMIT))
    if not restores:
        return _ok("nothing recorded")
    return _ok(f"{len(restores)} change(s) still as written")


def _log_lines(file: Path, tail: int = LOG_TAIL_BYTES) -> list[str]:
    """The last `tail` bytes of a hook log as lines, decoded leniently; a partial first line is
    dropped."""
    with file.open("rb") as handle:
        size = handle.seek(0, 2)
        start = max(0, size - tail)
        handle.seek(start)
        text = handle.read().decode("utf-8", errors="replace")
    lines = text.splitlines()
    return lines[1:] if start else lines


def _stamp(raw: object) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        when = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=UTC)


def _hook_errors() -> list[tuple[datetime | None, str]]:
    """Every error record of every hook log as (time, shown line), oldest first."""
    found: list[tuple[datetime | None, str]] = []
    folder = paths.logs_dir()
    for file in sorted([*folder.glob("*.jsonl"), *folder.glob("*.jsonl.1")]):
        if not file.is_file():  # a FIFO or device named like a log would block the read
            continue
        hook = _one_line(file.name.split(".jsonl", 1)[0])
        try:
            lines = _log_lines(file)
        except OSError:
            continue
        for line in lines:
            try:
                record = safe_json.loads(line)
            except ValueError:  # a line of nothing but "[" is a log, not a bug
                continue
            if not isinstance(record, dict) or not isinstance(record.get("error"), str):
                continue
            when = _stamp(record.get("ts"))
            shown = when.isoformat(timespec="seconds") if when else "unknown time"
            found.append((when, f"{hook} {shown}: {_one_line(record['error'])}"))
    return sorted(found, key=lambda item: item[0] or EARLIEST)


def _hook_log(now: datetime) -> Outcome:
    last = _hook_errors()[-ERRORS_SHOWN:]
    if not last:
        return _ok("no hook errors logged")
    lines = tuple(f"  {line}" for _when, line in last)
    since = now - RECENT
    recent = sum(1 for when, _line in last if when is not None and when >= since)
    if recent:
        return Outcome(
            "warn", f"{recent} of the last {len(last)} hook errors in the last 24 h", lines
        )
    return Outcome("ok", f"last {len(last)} hook errors are older than 24 h", lines)


# ---- the production guard -------------------------------------------------------------------


def _warning(text: str) -> str:
    return f"  warning: {text}"


def _guard_targets(hook: ModuleType, store_path: Path) -> tuple[bool, list[str]]:
    """Whether guard.json is committed at HEAD, and the report lines about it. The hook's own
    `load_targets` reads it, so doctor judges a file by the rules the guard enforces."""
    try:
        enforced = hook.load_targets(store_path)
    except hook.GuardError as error:  # invalid at HEAD, and no earlier version is valid
        return True, [_warning(f"guard.json at HEAD is not valid: {_one_line(str(error))}")]
    if enforced.targets is None:
        return False, [_warning("guard.json is not committed; commit it, or the guard refuses")]
    if enforced.invalid_head:
        reason = _one_line(enforced.invalid_head)
        return True, [
            _warning(f"guard.json at HEAD is not valid ({reason}); an older valid one is enforced")
        ]
    return True, [f"  guard.json at HEAD: ok: {len(enforced.targets)} targets"]


def _live_approvals(hook: ModuleType, store_path: Path, now: datetime) -> list[str]:
    live = sorted(
        (a for a in hook.approvals(store_path) if hook.is_live(a, now)),
        key=lambda approval: approval.expires,
    )
    if not live:
        return ["  no live approvals"]
    return [
        "  "
        + _one_line(
            f"approval {a.id} ({a.system}) until "
            f"{a.expires.astimezone(UTC).isoformat(timespec='seconds')}",
            2 * DETAIL_LIMIT,
        )
        for a in live
    ]


def _remote_warning(store_path: Path) -> list[str]:
    names = store.git(store_path, "remote", check=False).stdout.split()
    where = []
    if load_config().memory_remote:
        where.append("memory_remote in the config")
    if names:
        where.append(f"git remote {_one_line(' '.join(names), 40)}")
    if not where:
        return []
    return [
        _warning(
            f"a guarded store has a remote ({', '.join(where)}); sync never pushes a guarded "
            "store: remove the remote, or keep it private"
        )
    ]


def _guard_on(hook: ModuleType, config: Any, now: datetime) -> Outcome:
    try:
        committed, lines = _guard_targets(hook, config.store)
        lines += _live_approvals(hook, config.store, now)
        if not config.latch and committed:
            lines.append(
                _warning(f"guard.json is committed but the config has no latch; {SETUP_FIX}")
            )
        lines += _remote_warning(config.store)
    except hook.StoreError as error:
        return Outcome("warn", _one_line(f"on; cannot read the store: {error}"))
    if config.latch:
        why = "latch set"
    elif committed:
        why = "guard.json committed"
    else:
        why = "guard.json present"
    problems = sum(1 for line in lines if line.startswith("  warning:"))
    detail = f"on ({why})" + (f"; {problems} warning(s)" if problems else "")
    return Outcome("warn" if problems else "ok", detail, tuple(lines))


def _guard_report(hook: ModuleType, now: datetime) -> Outcome:
    try:
        config = hook.read_config()
    except hook.ConfigError as error:
        return Outcome("warn", _one_line(f"cannot read the config: {error}"))
    off = hook.off_reason(config)
    if off is None:
        return _guard_on(hook, config, now)
    shown = f"off ({_one_line(off)})"
    if store.is_guarded(config.store):  # committed at HEAD, yet nothing switches the hook on
        return Outcome("warn", shown, (_warning(f"guard.json is committed; {SETUP_FIX}"),))
    return Outcome("ok", shown)


def _guard(now: datetime) -> Outcome:
    try:
        hook = guard.load_guard()
        return guard.run_with_deadline(lambda: _guard_report(hook, now))
    except CovenError as error:  # a timeout, an unloadable hook, git or the config failing
        return Outcome("warn", _one_line(f"cannot check the guard: {error}"))


def _refusal_role(record: dict[str, object]) -> str:
    if "role" not in record:
        return "role not logged"  # a record from before the hook logged it
    role = record["role"]
    return role if isinstance(role, str) and role in ROLE_CLASSES else "other"


def _refusal_system(record: dict[str, object]) -> str | None:
    """The logged target system or rule name; None when it has the wrong type."""
    if record.get("system") is None:
        return "system not logged"
    system = record["system"]
    return _one_line(system, REFUSAL_FIELD_LIMIT) if isinstance(system, str) else None


def _refusal(line: str) -> str | None:
    """One refusal as a display line; None for anything else in the log, a bad line included."""
    try:
        record = safe_json.loads(line)
    except ValueError:
        return None
    if not isinstance(record, dict) or record.get("decision") != "deny":
        return None
    when, tool, command = _stamp(record.get("ts")), record.get("tool"), record.get("command")
    system = _refusal_system(record)
    if when is None or system is None or not isinstance(tool, str):
        return None
    if command is not None and not isinstance(command, str):
        return None
    fields = (
        when.isoformat(timespec="seconds"),
        _refusal_role(record),
        system,
        _one_line(command or tool, REFUSAL_FIELD_LIMIT),
    )
    return "  " + " | ".join(fields)


def _guard_refusals(_now: datetime) -> Outcome:
    found: list[str] = []
    folder = paths.logs_dir()
    for name in (f"{GUARD_LOG}.jsonl", f"{GUARD_LOG}.jsonl.1"):  # the current log, then the rotated
        file = folder / name
        if len(found) >= REFUSALS_SHOWN or not file.is_file():  # a FIFO would block the read
            continue
        try:
            lines = _log_lines(file, REFUSAL_TAIL_BYTES)
        except OSError:
            continue
        for line in reversed(lines):
            if len(found) < REFUSALS_SHOWN and (shown := _refusal(line)):
                found.append(shown)
    if not found:
        return _ok("no guard refusals logged")
    return Outcome("ok", f"{len(found)} recent refusal(s), newest first", tuple(found))


# (name, what a crash in the check reports, the check): a warn-level check cannot fail doctor.
CHECKS: Final[tuple[tuple[str, Status, Callable[[datetime], Outcome]], ...]] = (
    ("claude code", "fail", _claude),
    ("python3", "fail", _python),
    ("git", "fail", _git),
    ("uv", "warn", _uv),
    ("gh", "warn", _gh),
    ("superpowers plugin", "fail", _superpowers),
    ("config", "fail", _config),
    ("memory store", "fail", _memory_store),
    ("memory hook", "fail", _memory_hook),
    ("crossSessionInbound", "warn", _cross_session),
    ("owned settings", "warn", _owned),
    ("hook errors", "warn", _hook_log),
    ("guard", "warn", _guard),
    ("guard refusals", "warn", _guard_refusals),
)


def _crash(error: Exception) -> str:
    text = _one_line((str(error).splitlines() or [""])[0])
    return text if isinstance(error, CovenError) else f"{type(error).__name__}: {text}".rstrip(": ")


def run_checks(now: datetime) -> list[Result]:
    """Every check in order; `now` is passed in so the 24 h rule is testable."""
    results = []
    for name, crash_level, check in CHECKS:
        try:
            outcome = check(now)
        except Exception as error:  # a crashing check must not stop the others
            logging.getLogger(__name__).debug("check %s crashed", name, exc_info=True)
            outcome = Outcome(crash_level, _crash(error))
        results.append(Result(name, outcome.status, outcome.detail, outcome.more))
    return results


def run_doctor(_args: argparse.Namespace) -> int:
    results = run_checks(datetime.now(UTC))
    for result in results:
        print(f"{result.status} {result.name}: {result.detail}")
        for line in result.more:
            print(line)
    return 1 if any(result.status == "fail" for result in results) else 0


def register(subparsers: Any) -> None:
    parser = subparsers.add_parser("doctor", help="check the coven install (read-only)")
    parser.set_defaults(handler=run_doctor)
