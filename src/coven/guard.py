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
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

from coven import paths
from coven.errors import CovenError

HOOK = paths.root() / "hooks" / "prod_guard.py"
SPLIT_COMMAND_NOTE = "words the shell split are joined with single spaces"


@functools.cache
def load_guard() -> ModuleType:
    """The production guard hook, loaded from the plugin the way the memory store finds its own
    hook: by path under `paths.root()`. One module for the whole process."""
    spec = importlib.util.spec_from_file_location("coven_prod_guard", HOOK)
    if spec is None or spec.loader is None:
        raise CovenError(f"cannot load the production guard at {HOOK}")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except (OSError, SyntaxError, ImportError) as error:
        raise CovenError(f"cannot load the production guard at {HOOK}: {error}") from error
    return module


def run_check(args: argparse.Namespace) -> int:
    hook = load_guard()
    try:
        print(hook.check_file(Path(args.path)))
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


def _payload(args: argparse.Namespace) -> dict[str, object]:
    """The hook payload for one call. The role is not in it: explain judges both roles."""
    command = _command_text(args)
    if args.tool is not None:
        if command is not None:
            raise CovenError("--tool judges a tool by name; it takes no command or --file")
        if not args.tool.strip():
            raise CovenError("--tool needs a tool name")
        if args.tool == "Bash":
            raise CovenError("Bash is judged by its command: pass the command, or --file")
        return {"tool_name": args.tool, "tool_input": {}, "cwd": os.getcwd()}
    if command is None or not command.strip():
        raise CovenError("give a command, --file <path>, or --tool <name>")
    return {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": os.getcwd()}


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
        found = _explanation(hook, payload)
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
    explain.set_defaults(handler=run_explain)
