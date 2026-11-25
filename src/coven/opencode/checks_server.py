"""The doctor checks about the server: its login, its plugin, its skills and the API it offers."""

from __future__ import annotations

import os
import re
import stat
from collections.abc import Mapping
from pathlib import Path
from typing import Final

from coven.doctor import one_line
from coven.opencode import serve
from coven.opencode.canary import COVEN_PLUGIN
from coven.opencode.check_types import RESET, Context, Outcome
from coven.opencode.profile import PINNED_VERSION, OpencodeError
from coven.opencode.runtime import probe

# The 2.3.2 operationIds of every API route coven calls over HTTP: the plugin and the CLI's session
# create, list, get, prompt, interrupt, update, permission list and reply, and the event stream;
# `serve status` (session.active); the canary and `gm` (location.reload, plugin.list, skill.list).
# The plugin's `ctx.session.wait` and `ctx.session.context` run in-process; they are assumed (not
# verified) to be the same operations of the same API, matched by name, so a runtime that drops
# them is assumed to break the join too.
REQUIRED_OPERATIONS: Final = (
    "session.create",
    "session.list",
    "session.get",
    "session.prompt",
    "session.interrupt",
    "session.update",
    "session.permission.list",
    "session.permission.reply",
    "event.subscribe",
    "session.active",
    "session.context",
    "experimental.session.wait",
    "location.reload",
    "plugin.list",
    "skill.list",
)
PYTHON_MINIMUM: Final = (3, 12)
PYTHON_VERSION: Final = re.compile(r"Python (\d+)\.(\d+)\.(\d+)")
RESTART: Final = "run coven opencode serve stop, then coven opencode serve start"


def python3_version(env: Mapping[str, str]) -> tuple[int, int, int] | None:
    """The version of the `python3` the plugin's hooks would run, or None when there is none."""
    try:
        done = probe(Path("python3"), ["--version"], env)
    except OpencodeError:
        return None
    found = PYTHON_VERSION.match(done.stdout.strip()) if done.returncode == 0 else None
    return (int(found[1]), int(found[2]), int(found[3])) if found else None


def python3_check(ctx: Context) -> Outcome:
    need = ".".join(map(str, PYTHON_MINIMUM))
    found = python3_version(ctx.env)
    if found is None:
        return Outcome(
            "fail",
            f"python3 was not found; coven's plugin needs {need} or newer",
            f"install python {need} or newer and put python3 on the PATH",
        )
    shown = ".".join(map(str, found))
    if found[:2] < PYTHON_MINIMUM:
        return Outcome(
            "fail",
            f"python3 {shown} is too old; coven's plugin needs {need} or newer",
            f"put a python3 {need} or newer first on the PATH",
        )
    return Outcome("ok", f"python3 {shown}")


def server_check(ctx: Context) -> Outcome:
    live = ctx.server.live
    if live is None:
        return Outcome("ok", "no server running; plugin and skills are read from a canary")
    head = f"running pid {live.info.pid} port {live.info.port}"
    try:
        ctx.server.fetch("/api/session")
    except OpencodeError as error:
        return Outcome("fail", f"{head} but does not answer: {one_line(str(error))}", RESTART)
    return Outcome("ok", f"{head}, answers with its password")


def password_check(ctx: Context) -> Outcome:
    path = serve.files(ctx.layout).password
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except FileNotFoundError:
        return Outcome(
            "ok", "no password file yet; the first `coven opencode serve start` makes it"
        )
    except OSError as error:
        return Outcome("fail", f"cannot read {path}: {one_line(str(error))}")
    if mode != serve.PASSWORD_MODE:
        detail = f"{path} is mode {mode:04o}; it must be {serve.PASSWORD_MODE:04o}"
        return Outcome("fail", detail, f"chmod 600 {path}")
    return Outcome("ok", f"{path} is mode {mode:04o}")


def operations_check(ctx: Context) -> Outcome:
    if ctx.profile is None:
        return ctx.skip()
    offered = ctx.server.view().operations
    missing = [name for name in REQUIRED_OPERATIONS if name not in offered]
    if missing:
        return Outcome(
            "fail",
            f"the server's OpenAPI lacks {', '.join(missing)}",
            f"coven needs OpenCode {PINNED_VERSION}: run coven opencode install, then restart the server",
        )
    return Outcome(
        "ok", f"all {len(REQUIRED_OPERATIONS)} operations coven calls are in the OpenAPI"
    )


def plugin_outcome(plugins: list[dict[str, object]]) -> Outcome:
    """Whether coven's plugin is in the server's plugin list and active."""
    entry = next((plugin for plugin in plugins if plugin.get("id") == COVEN_PLUGIN), None)
    if entry is None:
        loaded = ", ".join(str(plugin.get("id")) for plugin in plugins) or "none"
        return Outcome("fail", f"the server did not load coven (plugins: {loaded})", RESET)
    state = entry.get("state")
    state = state if isinstance(state, dict) else {}
    if state.get("status") == "active":
        return Outcome("ok", "coven plugin active")
    cause = one_line(str(state.get("error") or ""))
    detail = f"coven plugin {state.get('status') or 'not active'}"
    return Outcome("fail", f"{detail}: {cause}" if cause else detail, RESET)


def plugin_check(ctx: Context) -> Outcome:
    if ctx.profile is None:
        return ctx.skip()
    return plugin_outcome(ctx.server.view().plugins)


def _is_coven_skill(path: object, config_dir: Path) -> bool:
    """Built-in skills have a `skill://` path; the rest must live in the profile's config."""
    if not isinstance(path, str):
        return False
    if path.startswith("skill://"):
        return True
    own = config_dir / "skills"
    where = Path(os.path.normpath(path))  # `skills/../../x` must not read as inside `skills`
    return any(where.is_relative_to(folder) for folder in (own, own.resolve()))


def skills_check(ctx: Context) -> Outcome:
    if ctx.profile is None:
        return ctx.skip()
    skills = ctx.server.view().skills
    config_dir = ctx.layout.config_dir
    foreign = [str(s.get("id")) for s in skills if not _is_coven_skill(s.get("path"), config_dir)]
    if foreign:
        fix = (
            f"remove them from {ctx.layout.home}/.agents/skills "
            "or report an OpenCode isolation change"
        )
        return Outcome("fail", f"foreign skills: {', '.join(foreign)}", fix)
    return Outcome("ok", f"{len(skills)} skills, all built in or from the profile")
