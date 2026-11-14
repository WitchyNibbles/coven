"""`coven pm`: argument parsing and printing for starting, resuming and checking project managers."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

from coven.config import load_config
from coven.pm import health, report, resume
from coven.pm.profile import launch_command
from coven.pm.rows import PmError, load_rows
from coven.pm.text import clean_line, is_valid_code, is_valid_gm_name


def _code(value: str) -> str:
    """An argparse type: a project code that is safe to print."""
    if not is_valid_code(value):
        raise argparse.ArgumentTypeError("a PM code is lower-case letters, digits and '-'")
    return value


def _gm_name(value: str) -> str:
    """An argparse type: a GM session name that is safe to print inside a command."""
    if not is_valid_gm_name(value):
        raise argparse.ArgumentTypeError("a GM name is letters, digits, '.', '_' and '-'")
    return value


def run_status(args: argparse.Namespace) -> int:
    rows = load_rows()
    request = report.StatusRequest(args.codes, args.all, args.full, args.gm)
    try:
        print("\n".join(report.status_report(rows, request)))
        sys.stdout.flush()  # a closed pipe shows here, not in the exit-time flush
    except BrokenPipeError:
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())  # keep the exit flush quiet
    return 0


def run_launch(args: argparse.Namespace) -> int:
    repo, brief = Path(args.repo).resolve(), Path(args.brief).resolve()
    if not repo.is_dir():
        raise PmError(f"the repo is not a directory: {clean_line(args.repo)}")
    if not brief.is_file():
        raise PmError(f"the brief is not a file: {clean_line(args.brief)}")
    print(launch_command(args.code, repo, brief, load_config().memory_path))
    return 0


def run_resume(args: argparse.Namespace) -> int:
    request = resume.ResumeRequest(args.code, args.gm, args.session, [Path(f) for f in args.add])
    plan = resume.prepare_resume(load_rows(), request, proc_root=report.PROC_ROOT)
    print(f"prompt: {plan.prompt_file}")
    print(plan.command)
    return 0


def run_health(args: argparse.Namespace) -> int:
    lines = health.health_report(load_rows(), args.gm)
    print("\n".join(lines) if lines else "ok")
    return 1 if lines else 0


def register(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "pm", help="start, resume and check background project-manager sessions (general manager)"
    )
    commands = parser.add_subparsers(dest="pm_command", required=True, metavar="COMMAND")
    status = commands.add_parser(
        "status", help="show project-manager sessions: state, last messages, what waits on the GM"
    )
    status.add_argument(
        "codes", nargs="*", type=_code, metavar="CODE", help="a PM code (pm-<CODE>)"
    )
    status.add_argument("--all", action="store_true", help="include sessions older than a week")
    status.add_argument("--full", type=_code, metavar="CODE", help="print that PM's last message")
    status.add_argument(
        "--gm", type=_gm_name, metavar="NAME", help="also count messages from this GM name"
    )
    status.set_defaults(handler=run_status)

    launch = commands.add_parser(
        "launch",
        help="print the command that starts a project manager (starts nothing)",
        description=(
            "Print the line that starts the background session pm-<CODE> in a repo, with the "
            "brief as its prompt and the permission profile attached. The GM runs that line."
        ),
    )
    launch.add_argument("code", type=_code, metavar="CODE", help="a PM code (pm-<CODE>)")
    launch.add_argument("--repo", required=True, metavar="PATH", help="the project's repo folder")
    launch.add_argument(
        "--brief", required=True, metavar="FILE", help="the brief file to start from"
    )
    launch.set_defaults(handler=run_launch)
    resume_parser = commands.add_parser(
        "resume",
        help="write a resume prompt and print the command that resumes a project manager",
        description=(
            "Refuses when the PM still runs, the session is not named pm-<CODE>, or its "
            "working directory is gone. Writes the prompt file (mode 0600) and prints the "
            "command; starts nothing."
        ),
    )
    resume_parser.add_argument("code", type=_code, metavar="CODE", help="a PM code (pm-<CODE>)")
    resume_parser.add_argument(
        "--gm", required=True, type=_gm_name, metavar="NAME", help="the GM's session name"
    )
    resume_parser.add_argument(
        "--session", metavar="ID", help="the session id, when agents does not list the PM"
    )
    resume_parser.add_argument(
        "--add",
        action="append",
        default=[],
        metavar="FILE",
        help="a file to append to the resume prompt (repeatable)",
    )
    resume_parser.set_defaults(handler=run_resume)
    health_parser = commands.add_parser(
        "health",
        help="print what needs the GM: one line per problem, or ok",
        description=(
            "Compares the state records in the memory store with the live sessions. Prints "
            "`ok` and exits 0, or one line per PM that needs the GM and exits 1. An error "
            "(no store, agents failed) exits 2."
        ),
    )
    health_parser.add_argument(
        "--gm", required=True, type=_gm_name, metavar="NAME", help="the GM's session name"
    )
    health_parser.set_defaults(handler=run_health)
