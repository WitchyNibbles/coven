"""The shared memory store: `coven memory init` and `coven memory sync`."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from coven.config import load_config
from coven.errors import warn
from coven.memory import store


def _store_path(path: str | None) -> Path:
    return Path(path).expanduser() if path else load_config().memory_path


def run_init(args: argparse.Namespace) -> int:
    path = _store_path(args.path)
    if args.remote and (warning := store.remote_warning(args.remote)):
        warn(warning)
    store.init_store(path, args.remote)
    print(f"memory store ready at {path}")
    return 0


def run_sync(_args: argparse.Namespace) -> int:
    path = _store_path(None)
    for line in store.sync_store(path):
        print(line)
    return 0


def register(subparsers: Any) -> None:
    parser = subparsers.add_parser("memory", help="create or sync the shared memory store")
    commands = parser.add_subparsers(dest="memory_command", required=True, metavar="COMMAND")
    init = commands.add_parser("init", help="create the store (a git repo) and install its hook")
    init.add_argument("--path", help="the store's folder (default: memory_path of the config)")
    init.add_argument("--remote", help="a private git remote to set as origin")
    init.set_defaults(handler=run_init)
    sync = commands.add_parser("sync", help="fetch, rebase, rebuild indexes and push")
    sync.set_defaults(handler=run_sync)
