"""The doctor checks that look at files and the binary: no server runs for these."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Final

from coven import safe_json
from coven.opencode.check_types import RESET, SETUP, Context, Outcome, one_line
from coven.opencode.generate import CONFIG_FILE, config_data
from coven.opencode.profile import AGENT_ROLES, PINNED_VERSION, Layout, OpencodeError, Profile
from coven.opencode.runtime import BINARY_OVERRIDE, VERSION_TIMEOUT, binary_version

PROJECT_CONFIG: Final = (".opencode", "opencode.json", "opencode.jsonc")
GUARD_HOOK: Final = "hooks/deny_secrets.py"

type Fields = list[tuple[str, tuple[str, ...]]]


def runtime_check(ctx: Context) -> Outcome:
    if BINARY_OVERRIDE in os.environ:
        fix = f"point {BINARY_OVERRIDE} at {PINNED_VERSION}"
    else:
        fix = "run coven opencode install"
    if not ctx.binary.is_file():
        return Outcome("fail", f"{ctx.binary} is missing", fix)
    found = binary_version(ctx.binary, ctx.env)
    if found != PINNED_VERSION:
        detail = f"{ctx.binary} reports {found or 'no version'}; coven needs {PINNED_VERSION}"
        return Outcome("fail", detail, fix)
    return Outcome("ok", f"{PINNED_VERSION} ({ctx.binary})")


def read_paths(binary: Path, env: Mapping[str, str]) -> list[tuple[str, str]]:
    """The (name, path) lines of `opencode debug paths`."""
    try:
        done = subprocess.run(
            [str(binary), "debug", "paths"],
            env=dict(env),
            capture_output=True,
            text=True,
            timeout=VERSION_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise OpencodeError(f"cannot run {binary} debug paths: {error}") from error
    if done.returncode != 0:
        raise OpencodeError(f"{binary} debug paths exited {done.returncode}")
    pairs = (line.split(None, 1) for line in done.stdout.splitlines())
    return [(pair[0], pair[1].strip()) for pair in pairs if len(pair) == 2]


def paths_check(ctx: Context) -> Outcome:
    root = ctx.layout.root
    try:
        found = read_paths(ctx.binary, ctx.env)
    except OpencodeError as error:
        return Outcome("fail", one_line(str(error)), RESET)
    roots = (root, root.resolve())
    for name, value in found:
        if not any(Path(value).is_relative_to(each) for each in roots):
            return Outcome("fail", f"{name} is {value}, outside {root}", RESET)
    if not found:
        return Outcome("fail", "debug paths printed no paths", RESET)
    return Outcome("ok", f"{len(found)} paths, all under {root}")


def _read_config(layout: Layout) -> dict[str, object]:
    path = layout.config_dir / CONFIG_FILE
    try:
        data = safe_json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise OpencodeError(f"{path} is missing") from None
    except (OSError, ValueError) as error:
        raise OpencodeError(f"{path} is not valid JSON: {one_line(str(error))}") from error
    if not isinstance(data, dict):
        raise OpencodeError(f"{path} is not a JSON object")
    return data


def _field(data: object, *path: str) -> object:
    for step in path:
        if isinstance(data, dict):
            data = data.get(step)
        elif isinstance(data, list) and step.isdecimal() and int(step) < len(data):
            data = data[int(step)]
        else:
            return None
    return data


def _watched(profile: Profile) -> Fields:
    fields: Fields = [("model", ("model",))]
    for role in (*AGENT_ROLES, "title"):
        fields.append((f"agents.{role}.model", ("agents", role, "model")))
    if profile.preset == "litellm":
        provider = ("providers", "litellm")
        fields.append(("providers.litellm.settings.baseURL", (*provider, "settings", "baseURL")))
        fields.append(("providers.litellm.env", (*provider, "env")))
    return fields


def config_check(ctx: Context) -> Outcome:
    if ctx.profile is None:
        return Outcome("fail", ctx.profile_note or "no profile yet", SETUP)
    try:
        written = _read_config(ctx.layout)
    except OpencodeError as error:
        return Outcome("fail", str(error), RESET)
    wanted = config_data(ctx.profile, ctx.layout, ctx.layout.root)
    for label, path in _watched(ctx.profile):
        if _field(written, *path) != _field(wanted, *path):
            return Outcome("fail", f"{label} differs from profile.json", RESET)
    return Outcome("ok", f"model {wanted['model']} and the agents match profile.json")


def key_check(ctx: Context) -> Outcome:
    if ctx.profile is None:
        return ctx.skip()
    name = ctx.profile.key_env
    if name in ctx.base:
        return Outcome("ok", f"${name} is set")
    return Outcome("warn", f"${name} is not set", f"export {name} where you run coven opencode run")


def coven_root_check(ctx: Context) -> Outcome:
    try:
        written = _read_config(ctx.layout)
    except OpencodeError as error:
        return Outcome("fail", str(error), RESET)
    root = _field(written, "plugins", "0", "options", "covenRoot")
    if not isinstance(root, str) or not root:
        return Outcome("fail", f"{CONFIG_FILE} names no covenRoot", RESET)
    hook = Path(root) / GUARD_HOOK
    if not hook.is_file():
        return Outcome("fail", f"{hook} is missing", RESET)
    return Outcome("ok", f"{root} holds {GUARD_HOOK}")


def project_config_check(ctx: Context) -> Outcome:
    here = ctx.cwd.resolve()
    found = [
        str(folder / name)
        for folder in (here, *here.parents)
        for name in PROJECT_CONFIG
        if (folder / name).exists()
    ]
    if not found:
        return Outcome("ok", "no project config above the working directory")
    return Outcome(
        "warn", f"{', '.join(found)}: ignored in the coven profile (project config is off)"
    )
