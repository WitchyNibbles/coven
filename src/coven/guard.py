"""`coven guard check` and `coven guard explain`: the production guard's rules, run read-only.

The rules live in the hook (`hooks/prod_guard.py`), which runs under Claude Code; these commands
load that file and call it, so they cannot disagree with it. They read the store the way the hook
does (HEAD only, no GIT_* variables, no replace refs) and write nothing: no hook log line, no lock
or temp file in the store."""

from __future__ import annotations

import argparse
import functools
import importlib.util
import os
import signal
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from types import FrameType, ModuleType
from typing import Any

from coven import paths
from coven.errors import CovenError

HOOK = paths.root() / "hooks" / "prod_guard.py"
SPLIT_COMMAND_NOTE = "words the shell split are joined with single spaces"
# The edit tools `explain --tool` can judge by a path, and the tool_input key each reads it from.
PATH_KEYS = {
    "Write": "file_path",
    "Edit": "file_path",
    "MultiEdit": "file_path",
    "NotebookEdit": "notebook_path",
}
TIMED_OUT = "guard check timed out"


@functools.cache
def load_guard() -> ModuleType:
    """The production guard hook, loaded from the plugin the way the memory store finds its own
    hook: by path under `paths.root()`. One module for the whole process."""
    spec = importlib.util.spec_from_file_location("coven_prod_guard", HOOK)
    if spec is None or spec.loader is None:
        raise CovenError(f"cannot load the production guard at {HOOK}")
    module = importlib.util.module_from_spec(spec)
    # The hook puts its folder on sys.path for its imports (`hook_support`, `guard_config`) as it
    # loads; this process gets the path (and the bytecode flag, which the hook no longer changes)
    # back. Like a hook run, this load caches bytecode in the plugin's hooks/__pycache__. The hook
    # loads its other siblings by path, on first use (hook_support.sibling), and its imported
    # modules stay imported, so nothing later needs the path.
    path, no_bytecode = list(sys.path), sys.dont_write_bytecode
    try:
        spec.loader.exec_module(module)
    except (OSError, SyntaxError, ImportError) as error:
        raise CovenError(f"cannot load the production guard at {HOOK}: {error}") from error
    except (Exception, SystemExit) as error:  # a hook that fails or exits as it loads
        raise CovenError(
            f"cannot load the production guard at {HOOK}: {type(error).__name__}: {error}"
        ) from error
    finally:
        sys.path[:], sys.dont_write_bytecode = path, no_bytecode
    return module


class _PastDeadline(Exception):
    """The alarm went off."""


def _alarm(_signum: int, _frame: FrameType | None) -> None:
    raise _PastDeadline


def run_with_deadline[T](function: Callable[[], T], seconds: float | None = None) -> T:
    """`function()`, or a CovenError saying the check timed out once `seconds` have passed (the
    hook's DEADLINE by default). A SIGALRM interrupts a regex search or a git wait alike; the timer
    and the handler are put back as they were. Main thread only, like the hook."""
    limit = load_guard().DEADLINE if seconds is None else seconds
    previous = signal.signal(signal.SIGALRM, _alarm)
    signal.setitimer(signal.ITIMER_REAL, limit)
    try:
        return function()
    except _PastDeadline:
        raise CovenError(TIMED_OUT) from None
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def run_check(args: argparse.Namespace) -> int:
    hook = load_guard()
    try:
        print(run_with_deadline(lambda: hook.check_file(Path(args.path))))
    except hook.GuardError as error:
        raise CovenError(str(error)) from None
    return 0


def _command_text(args: argparse.Namespace) -> str | None:
    if args.file is not None and args.command:
        raise CovenError("give the command as an argument or with --file, not both")
    if args.file is not None:
        try:
            return Path(args.file).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            raise CovenError(f"cannot read {args.file}: {error}") from error
    words = args.command[1:] if args.command[:1] == ["--"] else args.command
    return " ".join(words) if words else None


def _cwd(args: argparse.Namespace) -> str:
    if args.cwd is None:
        return os.getcwd()
    folder: str = os.path.abspath(args.cwd)
    if not os.path.isdir(folder):
        raise CovenError(f"--cwd {args.cwd} is not a folder")
    return folder


def _tool_input(args: argparse.Namespace) -> dict[str, object]:
    """What `--tool` sends: the path of an edit tool, nothing for another tool."""
    key = PATH_KEYS.get(args.tool)
    if key is None:
        if args.path is not None:
            raise CovenError(f"--path goes with --tool {', '.join(PATH_KEYS)}, not {args.tool}")
        return {}
    if not args.path:
        raise CovenError(f"--tool {args.tool} needs --path <file>: the file it would write")
    return {key: args.path}


def _payload(args: argparse.Namespace) -> dict[str, object]:
    """The hook payload for one call. The role is not in it: explain judges both roles."""
    command = _command_text(args)
    cwd = _cwd(args)
    if args.tool is not None:
        if command is not None:
            raise CovenError("--tool judges a tool by name; it takes no command or --file")
        if not args.tool.strip():
            raise CovenError("--tool needs a tool name")
        if args.tool == "Bash":
            raise CovenError("Bash is judged by its command: pass the command, or --file")
        return {"tool_name": args.tool, "tool_input": _tool_input(args), "cwd": cwd}
    if args.path is not None:
        raise CovenError(f"--path goes with --tool {', '.join(PATH_KEYS)}")
    if command is None or not command.strip():
        raise CovenError("give a command, --file <path>, or --tool <name>")
    return {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": cwd}


def _explanation(hook: ModuleType, payload: dict[str, object]) -> Any | str:
    """The hook's explanation of this call, or why the guard is off (a string)."""
    try:
        config = hook.read_config()
    except hook.ConfigError as error:
        return hook.explain_without_config(payload, error, None)
    off = hook.off_reason(config)
    if off is not None:
        return off
    return hook.explain(payload, config.store, None, datetime.now(UTC))


def _print_hit(hit: Any) -> None:
    how = f"command matches {hit.trigger}" if hit.via == "command" else f"tool {hit.trigger}"
    print(f"{hit.system} ({hit.kind}): {how}")
    if hit.approval is None:
        print("  needs approval")
    else:
        print(f"  approved by {hit.approval.id} until {hit.approval.expires.isoformat()}")


def _print(found: Any) -> None:
    for hit in found.hits:
        _print_hit(hit)
    if found.note:
        print(f"note: {found.note.removeprefix('; ')}")
    if found.message:
        print(f"every session: {found.message}")
    if found.restricted:
        print(f"a PM: {found.restricted}")
    print(found.verdict)


def run_explain(args: argparse.Namespace) -> int:
    hook = load_guard()
    payload = _payload(args)
    try:
        found = run_with_deadline(lambda: _explanation(hook, payload))
    except hook.PayloadError as error:
        raise CovenError(f"not a tool call the guard can read: {error}") from None
    if isinstance(found, str):
        print(f"guard off: {found}")
        return 0
    _print(found)
    if found.unreadable:
        raise CovenError(found.unreadable)
    return 0


def register(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "guard", help="check guard.json or explain a production-guard decision"
    )
    commands = parser.add_subparsers(dest="guard_command", required=True, metavar="COMMAND")
    check = commands.add_parser(
        "check", help="validate a guard.json file with the hook's own rules"
    )
    check.add_argument("path", help="the guard.json file")
    check.set_defaults(handler=run_check)
    explain = commands.add_parser(
        "explain",
        help="show what the guard decides for one command or tool, for a PM and for the user",
        description=(
            "Run the guard's own decision, read-only, on one Bash command or one tool, against"
            " the memory store's committed HEAD. Shows each target hit with its live approval or"
            " 'needs approval', the rule a PM would hit, and one verdict line: allowed, refused"
            " for a PM, or refused for every session. Your own role is ignored. Writes nothing."
        ),
    )
    explain.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help=(
            f"the Bash command, last on the line, so its own flags stay in it ({SPLIT_COMMAND_NOTE};"
            " quote it to keep its spacing)"
        ),
    )
    explain.add_argument("--file", help="read the command, which may span lines, from this file")
    explain.add_argument("--tool", help="judge an MCP or other tool by name instead of a command")
    explain.add_argument(
        "--path",
        help="the file an edit tool (--tool Write, Edit, MultiEdit or NotebookEdit) would write; required for them",
    )
    explain.add_argument(
        "--cwd",
        help=(
            "the session's working directory (default: this one); relative paths are judged from"
            " --cwd, so pass the PM's worktree"
        ),
    )
    explain.set_defaults(handler=run_explain)
