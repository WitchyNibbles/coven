"""`coven setup`: show the changes, apply them on `--yes`, create the store, or undo it all."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

from coven import settings
from coven.config import load_config
from coven.errors import CovenError, warn
from coven.memory import store
from coven.settings import Choices


def _on_path(folder: Path) -> bool:
    wanted = os.path.abspath(folder)
    entries = os.environ.get("PATH", "").split(os.pathsep)
    return any(entry and os.path.abspath(Path(entry).expanduser()) == wanted for entry in entries)


def _needs_store(path: Path) -> bool:
    return not path.exists() or (path.is_dir() and not any(path.iterdir()))


def _choices(args: argparse.Namespace) -> Choices:
    current = load_config()
    memory_path = (
        Path(os.path.abspath(Path(args.memory_path).expanduser()))
        if args.memory_path
        else current.memory_path
    )
    return Choices(
        deny_secrets=args.deny_secrets,
        cross_session_accept=args.cross_session_accept,
        shim=args.shim,
        pm_cap=args.pm_cap if args.pm_cap is not None else current.pm_cap,
        memory_path=memory_path,
        memory_remote=args.memory_remote,
    )


def _confirm(args: argparse.Namespace, what: str) -> bool:
    """False, after saying why, when nothing should be written."""
    if args.dry_run:
        print("coven setup: dry run, nothing written")
        return False
    if not args.yes:
        print(f"coven setup: nothing written; rerun with --yes to {what}")
        return False
    return True


def _run_undo(args: argparse.Namespace) -> int:
    chosen = [
        flag
        for flag, given in (
            ("--deny-secrets", args.deny_secrets),
            ("--cross-session-accept", args.cross_session_accept),
            ("--shim", args.shim),
            ("--pm-cap", args.pm_cap is not None),
            ("--memory-path", args.memory_path),
            ("--memory-remote", args.memory_remote),
        )
        if given
    ]
    if chosen:
        raise CovenError(f"--undo cannot be combined with {', '.join(chosen)}")
    restores, skipped = settings.preview_undo()
    if not restores and not skipped:
        if args.yes and not args.dry_run:
            settings.undo()  # drops a record that has nothing left to restore
        print("coven setup: nothing to undo")
        return 0
    for line in restores:
        print(f"  {line}")
    for line in skipped:
        print(f"  left as is: {line}")
    if not _confirm(args, "undo these changes"):
        return 0
    settings.undo()
    print("coven setup: undone. The memory store was not touched.")
    return 0


def run(args: argparse.Namespace) -> int:
    if args.undo:
        return _run_undo(args)
    choices = _choices(args)
    store.check_path(choices.memory_path)
    changes = settings.plan(choices)
    if changes:
        print(f"coven setup: {len(changes)} change(s):")
        for change in changes:
            print(f"  {settings.describe(change)}")
    else:
        print("coven setup: nothing to change in settings or config")
    if args.memory_remote and (warning := store.remote_warning(args.memory_remote)):
        warn(warning)
    if _needs_store(choices.memory_path):
        print(f"  memory store: will be created at {choices.memory_path}")
    if not _confirm(args, "apply these changes"):
        return 0
    settings.apply(changes)
    remote = choices.memory_remote or load_config().memory_remote
    store.init_store(choices.memory_path, remote)
    print(f"memory store ready at {choices.memory_path}")
    if choices.shim and not _on_path(settings.shim_file().parent):
        print("~/.local/bin is not on your PATH; add it in your shell profile to run `coven` there")
    print("coven setup: done; `coven setup --undo --yes` reverses it")
    return 0


def register(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "setup", help="write coven's settings and config keys, or undo them (--undo)"
    )
    parser.add_argument("--dry-run", action="store_true", help="show the changes, write nothing")
    parser.add_argument("--undo", action="store_true", help="restore the keys setup wrote")
    parser.add_argument("--yes", action="store_true", help="apply the changes shown")
    parser.add_argument(
        "--deny-secrets", action="store_true", help="add read-deny rules for secret files"
    )
    parser.add_argument(
        "--cross-session-accept",
        action="store_true",
        help='set crossSessionInbound to "accept" so PMs and the GM can message each other',
    )
    parser.add_argument("--shim", action="store_true", help="install ~/.local/bin/coven")
    parser.add_argument(
        "--pm-cap", type=int, help="most project managers at once (default: config, else 2)"
    )
    parser.add_argument("--memory-path", help="the memory store's folder")
    parser.add_argument("--memory-remote", help="a private git remote for the store")
    parser.set_defaults(handler=run)
