"""The `coven` command line: argparse subcommands, each registered by its feature module."""

from __future__ import annotations

import argparse
import sys

from coven import doctor, memory, pm, project, setup
from coven.errors import CovenError

COMMANDS = (doctor, memory, pm, project, setup)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="coven", description="Run work through coven.")
    subparsers = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    for module in COMMANDS:
        module.register(subparsers)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as stop:
        return stop.code if isinstance(stop.code, int) else 1
    try:
        return int(args.handler(args))
    except CovenError as error:
        print(f"coven: {error}", file=sys.stderr)
        return error.exit_code
