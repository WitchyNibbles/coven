"""`coven opencode`: the subcommands that set up, check and run coven's OpenCode profile."""

from __future__ import annotations

import argparse
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from coven import paths
from coven.opencode import checks, generate, install, runtime, serve, superpowers
from coven.opencode.canary import AUTH_VAR, USERNAME_ENV, http_json, loaded_plugins
from coven.opencode.checkout import needs_checkout
from coven.opencode.checks_server import plugin_outcome
from coven.opencode.profile import (
    ASSUMED_CONTEXT,
    PINNED_VERSION,
    ROLES,
    OpencodeError,
    Profile,
    layout,
    load_profile,
    make_profile,
)

type ExecFn = Callable[[str, list[str], dict[str, str]], object]

FLAGS_NEED_PRESET = "pass --preset with the other flags, or no flags to regenerate"


def _chosen_profile(args: argparse.Namespace) -> Profile:
    """The profile the flags describe, or the saved one when there are no flags."""
    if args.preset is None:
        if args.base_url is not None or args.key_env is not None or args.model or args.limit:
            raise OpencodeError(FLAGS_NEED_PRESET)
        saved = load_profile(layout())
        if saved is None:
            raise OpencodeError(runtime.NO_PROFILE)
        return saved
    return make_profile(args.preset, args.base_url, args.key_env, args.model, args.limit)


def _preset_line(profile: Profile) -> str:
    if profile.preset == "litellm":
        return f"preset: litellm ({profile.base_url}, key from ${profile.key_env})"
    return f"preset: opencode (key from ${profile.key_env} or a stored login)"


def _superpowers_warning(missing: list[str]) -> str:
    return (
        f"warning: superpowers skills not found: {', '.join(missing)} "
        f"(run `coven opencode install`, which fetches superpowers {superpowers.PINNED_TAG}, or "
        "install the superpowers plugin in Claude Code; then re-run coven opencode setup)"
    )


def _limit_lines(profile: Profile) -> list[str]:
    lines = []
    for model_id in profile.model_ids:
        if limit := profile.limits.get(model_id):
            output = f", {limit.output} output" if limit.output is not None else ""
            lines.append(f"limit {model_id}: {limit.context} context{output}")
    return lines


def _limit_warning(profile: Profile) -> list[str]:
    """One warning naming the litellm models that have no limit; nothing for any other preset."""
    missing = profile.unlimited
    if profile.preset != "litellm" or not missing:
        return []
    return [
        f"warning: no limit for {', '.join(missing)}: OpenCode assumes {ASSUMED_CONTEXT} tokens "
        "of context (add --limit ID=CONTEXT[:OUTPUT] to the setup flags)"
    ]


def run_setup(args: argparse.Namespace) -> int:
    profile = _chosen_profile(args)
    home = layout()
    missing = generate.write_profile(profile, home, paths.root(), generate.plugin_source())
    print(f"profile: {home.root}")
    print(_preset_line(profile))
    for role in ROLES:
        print(f"{role}: {profile.model(role)}")
    for line in (*_limit_lines(profile), *_limit_warning(profile)):
        print(line)
    if found := superpowers.source(home):
        print(f"superpowers: {found.label}")
    if missing:
        print(_superpowers_warning(missing))
    print("next: coven opencode doctor")
    return 0


RUN_REFUSED = "a coven server is running; use `coven opencode gm`"


def run_run(args: argparse.Namespace) -> int:
    # `run` and the server share the profile's database; two OpenCode processes writing it is unsafe.
    if serve.running(layout()) is not None:
        raise OpencodeError(RUN_REFUSED)
    passed = args.opencode_args
    return runtime.run(passed[1:] if passed[:1] == ["--"] else passed, exec_fn=os.execve)


PLUGIN_INACTIVE = "coven's plugin is not active on the server"


def _attach_target() -> tuple[str, str]:
    """The URL and password of the coven server, started first when none runs."""
    home = layout()
    profile = load_profile(home)
    if profile is None:
        raise OpencodeError(runtime.NO_PROFILE)
    info = serve.running(home) or serve.start(home, profile)
    try:
        password = serve.files(home).password.read_text().strip()
    except OSError as error:
        raise OpencodeError(f"cannot read the server password: {error}") from error
    return f"http://{serve.HOST}:{info.port}", password


def _with_login(password: str, exec_fn: ExecFn) -> ExecFn:
    """`exec_fn` with the server password added to the env it execs, and nowhere else."""

    def attach(path: str, argv: list[str], env: dict[str, str]) -> object:
        login = {k: v for k, v in env.items() if k != USERNAME_ENV} | {AUTH_VAR: password}
        return exec_fn(path, argv, login)

    return attach


def run_gm(_args: argparse.Namespace) -> int:
    url, password = _attach_target()
    plugins = loaded_plugins(url, password, http_json)
    if (active := plugin_outcome(plugins)).status != "ok":
        raise OpencodeError(f"{PLUGIN_INACTIVE} ({active.detail}); run `coven opencode doctor`")
    return runtime.run(["--server", url], exec_fn=_with_login(password, os.execve))


def run_install(args: argparse.Namespace) -> int:
    binary = install.install(layout(), args.version)
    print(f"runtime: {binary}")
    return 0


def run_doctor_command(_args: argparse.Namespace) -> int:
    results = checks.run_doctor(layout(), Path.cwd())
    for line in checks.render(results):
        print(line)
    return checks.exit_code(results)


def run_serve_start(args: argparse.Namespace) -> int:
    home = layout()
    profile = load_profile(home)
    if profile is None:
        raise OpencodeError(runtime.NO_PROFILE)
    info = serve.start(home, profile, args.port)
    print(f"running pid {info.pid} port {info.port}")
    return 0


def run_serve_stop(_args: argparse.Namespace) -> int:
    print("stopped" if serve.stop(layout()) else "no server was running")
    return 0


def run_serve_status(_args: argparse.Namespace) -> int:
    for line in serve.status(layout()):
        print(line)
    return 0


def _register_serve(commands: Any) -> None:
    parser = commands.add_parser(
        "serve", help="start, stop and look at coven's long-lived OpenCode server"
    )
    actions = parser.add_subparsers(dest="serve_command", required=True, metavar="ACTION")
    start = actions.add_parser(
        "start", help="start the server (127.0.0.1, password in the profile)"
    )
    start.add_argument("--port", type=int, metavar="N", help="a fixed port; default: any free one")
    start.set_defaults(handler=needs_checkout(run_serve_start))
    stop = actions.add_parser("stop", help="stop the server, keep its password")
    stop.set_defaults(handler=needs_checkout(run_serve_stop))
    status = actions.add_parser("status", help="say whether it runs, with the PM sessions")
    status.set_defaults(handler=needs_checkout(run_serve_status))


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
    setup.add_argument(
        "--limit",
        action="append",
        default=[],
        metavar="ID=CONTEXT[:OUTPUT]",
        help="a model's token window, litellm only; without one OpenCode assumes "
        f"{ASSUMED_CONTEXT} tokens of context",
    )
    setup.set_defaults(handler=needs_checkout(run_setup))
    run = commands.add_parser(
        "run", help="start the pinned OpenCode in coven's profile (arguments follow --)"
    )
    run.add_argument("opencode_args", nargs=argparse.REMAINDER, metavar="-- ARGS")
    run.set_defaults(handler=needs_checkout(run_run))
    gm = commands.add_parser(
        "gm", help="open the TUI on coven's server as the GM (starts the server if needed)"
    )
    gm.set_defaults(handler=needs_checkout(run_gm))
    install_parser = commands.add_parser(
        "install",
        help="install the pinned OpenCode (npm) and superpowers v7.0.2 (git) into the profile",
    )
    install_parser.add_argument(
        "--version", default=PINNED_VERSION, metavar="X.Y.Z", help=f"default {PINNED_VERSION}"
    )
    install_parser.set_defaults(handler=needs_checkout(run_install))
    doctor = commands.add_parser(
        "doctor", help="check the runtime, the profile's isolation and coven's plugin"
    )
    doctor.set_defaults(handler=needs_checkout(run_doctor_command))
    _register_serve(commands)
