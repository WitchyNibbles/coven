"""`coven opencode`: the subcommands that set up, check and run coven's OpenCode profile."""

from __future__ import annotations

import argparse
from typing import Any

from coven import paths
from coven.opencode import generate
from coven.opencode.profile import ROLES, OpencodeError, Profile, layout, load_profile, make_profile

NO_PROFILE = "no profile yet; run coven opencode setup --preset ..."
FLAGS_NEED_PRESET = "pass --preset with the other flags, or no flags to regenerate"


def _chosen_profile(args: argparse.Namespace) -> Profile:
    """The profile the flags describe, or the saved one when there are no flags."""
    if args.preset is None:
        if args.base_url is not None or args.key_env is not None or args.model:
            raise OpencodeError(FLAGS_NEED_PRESET)
        saved = load_profile(layout())
        if saved is None:
            raise OpencodeError(NO_PROFILE)
        return saved
    return make_profile(args.preset, args.base_url, args.key_env, args.model)


def _preset_line(profile: Profile) -> str:
    if profile.preset == "litellm":
        return f"preset: litellm ({profile.base_url}, key from ${profile.key_env})"
    return f"preset: opencode (key from ${profile.key_env} or a stored login)"


def run_setup(args: argparse.Namespace) -> int:
    profile = _chosen_profile(args)
    home = layout()
    generate.write_profile(profile, home, paths.root(), generate.plugin_source())
    print(f"profile: {home.root}")
    print(_preset_line(profile))
    for role in ROLES:
        print(f"{role}: {profile.model(role)}")
    print("next: coven opencode doctor")
    return 0


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
