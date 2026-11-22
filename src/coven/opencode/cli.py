"""`coven opencode`: the subcommands that set up, check and run coven's OpenCode profile."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

from coven import paths
from coven.opencode import checks, generate, install, runtime
from coven.opencode.profile import (
    PINNED_VERSION,
    ROLES,
    OpencodeError,
    Profile,
    layout,
    load_profile,
    make_profile,
)

FLAGS_NEED_PRESET = "pass --preset with the other flags, or no flags to regenerate"


def _chosen_profile(args: argparse.Namespace) -> Profile:
    """The profile the flags describe, or the saved one when there are no flags."""
    if args.preset is None:
        if args.base_url is not None or args.key_env is not None or args.model:
            raise OpencodeError(FLAGS_NEED_PRESET)
        saved = load_profile(layout())
        if saved is None:
            raise OpencodeError(runtime.NO_PROFILE)
        return saved
    return make_profile(args.preset, args.base_url, args.key_env, args.model)


def _preset_line(profile: Profile) -> str:
    if profile.preset == "litellm":
        return f"preset: litellm ({profile.base_url}, key from ${profile.key_env})"
    return f"preset: opencode (key from ${profile.key_env} or a stored login)"


def _superpowers_warning(missing: list[str]) -> str:
    return (
        f"warning: superpowers skills not found: {', '.join(missing)} "
        "(install the superpowers plugin in Claude Code, then re-run coven opencode setup)"
    )


def run_setup(args: argparse.Namespace) -> int:
    profile = _chosen_profile(args)
    home = layout()
    missing = generate.write_profile(profile, home, paths.root(), generate.plugin_source())
    print(f"profile: {home.root}")
    print(_preset_line(profile))
    for role in ROLES:
        print(f"{role}: {profile.model(role)}")
    if missing:
        print(_superpowers_warning(missing))
    print("next: coven opencode doctor")
    return 0


def run_run(args: argparse.Namespace) -> int:
    passed = args.opencode_args
    return runtime.run(passed[1:] if passed[:1] == ["--"] else passed, exec_fn=os.execve)


def run_install(args: argparse.Namespace) -> int:
    binary = install.install(layout(), args.version)
    print(f"runtime: {binary}")
    return 0


def run_doctor_command(_args: argparse.Namespace) -> int:
    results = checks.run_doctor(layout(), Path.cwd())
    for line in checks.render(results):
        print(line)
    return checks.exit_code(results)


def register(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "opencode", help="set up, check and run coven's own OpenCode profile"
    )
    commands = parser.add_subparsers(dest="opencode_command", required=True, metavar="COMMAND")
    setup = commands.add_parser(
        "setup",
        help="write the profile's config and plugin (no flags: regenerate from profile.json)",
    )
    setup.add_argument("--preset", metavar="{litellm,opencode}", help="where the models come from")
    setup.add_argument("--base-url", metavar="URL", help="the gateway's OpenAI-compatible URL")
    setup.add_argument("--key-env", metavar="NAME", help="the environment variable holding the key")
    setup.add_argument(
        "--model",
        action="append",
        default=[],
        metavar="ROLE=ID",
        help="a model per role; lead is required",
    )
    setup.set_defaults(handler=run_setup)
    run = commands.add_parser(
        "run", help="start the pinned OpenCode in coven's profile (arguments follow --)"
    )
    run.add_argument("opencode_args", nargs=argparse.REMAINDER, metavar="-- ARGS")
    run.set_defaults(handler=run_run)
    install_parser = commands.add_parser(
        "install", help="install the pinned OpenCode runtime into the profile with npm"
    )
    install_parser.add_argument(
        "--version", default=PINNED_VERSION, metavar="X.Y.Z", help=f"default {PINNED_VERSION}"
    )
    install_parser.set_defaults(handler=run_install)
    doctor = commands.add_parser(
        "doctor", help="check the runtime, the profile's isolation and coven's plugin"
    )
    doctor.set_defaults(handler=run_doctor_command)
