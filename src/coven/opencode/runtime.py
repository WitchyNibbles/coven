"""Running coven's pinned OpenCode: the binary, its run env and the exec."""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Callable, Collection, Mapping, Sequence
from pathlib import Path
from typing import Final

from coven.errors import warn
from coven.opencode.profile import (
    OPENCODE_KEY_ENV,
    PINNED_VERSION,
    Layout,
    OpencodeError,
    Profile,
    layout,
    load_profile,
)

PASSED_THROUGH: Final = (
    "OPENCODE_SERVER_PASSWORD",
    "OPENCODE_SERVER_USERNAME",
    "OPENCODE_DISABLE_MODELS_FETCH",
)
VERSION_TIMEOUT: Final = 10.0
# 2.x prints `opencode v2.3.2`; 1.x printed the bare `1.19.0`.
VERSION_LINE: Final = re.compile(r"(?:opencode )?v?(\d+\.\d+\.\d+\S*)")
BINARY_OVERRIDE: Final = "COVEN_OPENCODE_BIN"

MISSING = "the OpenCode runtime is missing; run coven opencode install"
NO_PROFILE = "no profile yet; run coven opencode setup --preset ..."


def binary_path(layout: Layout) -> Path:
    """`COVEN_OPENCODE_BIN` when it is set, else the pinned runtime inside the profile."""
    override = os.environ.get(BINARY_OVERRIDE)
    return Path(override) if override else layout.runtime_binary(PINNED_VERSION)


def probe(
    binary: Path, args: Sequence[str], env: Mapping[str, str]
) -> subprocess.CompletedProcess[str]:
    """Run `binary args` with a short timeout; OpencodeError when it cannot run."""
    try:
        return subprocess.run(
            [str(binary), *args],
            env=dict(env),
            capture_output=True,
            text=True,
            timeout=VERSION_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError, UnicodeDecodeError) as error:
        raise OpencodeError(f"cannot run {binary} {' '.join(args)}: {error}") from error


def binary_version(binary: Path, env: Mapping[str, str]) -> str | None:
    """The version token of `binary --version`, or None when it cannot run or prints no version."""
    try:
        done = probe(binary, ["--version"], env)
    except OpencodeError:
        return None
    lines = done.stdout.strip().splitlines()
    if done.returncode != 0 or not lines:
        return None
    found = VERSION_LINE.fullmatch(lines[0].strip())
    return found.group(1) if found else None


def isolated_env(
    layout: Layout,
    base: Mapping[str, str],
    keep: Sequence[str] = (),
    drop: Collection[str] = (),
) -> dict[str, str]:
    """`base` without OpenCode settings, pointed at the profile's folders, minus the `drop` names."""
    kept = (*PASSED_THROUGH, *keep)
    env = {k: v for k, v in base.items() if not k.startswith("OPENCODE_") or k in kept}
    env.update(
        OPENCODE_CONFIG_DIR=str(layout.config_dir),
        XDG_CONFIG_HOME=str(layout.config_home),
        XDG_DATA_HOME=str(layout.data),
        XDG_CACHE_HOME=str(layout.cache),
        XDG_STATE_HOME=str(layout.state),
        OPENCODE_TEST_HOME=str(layout.home),
        TMPDIR=str(layout.tmp),
    )
    for name in drop:
        env.pop(name, None)
    return env


def _may_pass_through(name: str) -> bool:
    """Whether a key env of this name may come from `base`: never another OPENCODE_ setting."""
    return name == OPENCODE_KEY_ENV or not name.startswith("OPENCODE_")


def run_env(
    profile: Profile, layout: Layout, base: Mapping[str, str], *, offline: bool
) -> dict[str, str]:
    """The environment OpenCode runs in: the profile's folders and switches, nothing inherited."""
    key_env = profile.key_env
    kept = (key_env,) if _may_pass_through(key_env) else ()
    env = isolated_env(layout, base, kept)
    env.update(
        OPENCODE_DISABLE_AUTOUPDATE="1",
        OPENCODE_DISABLE_CLAUDE_CODE="1",
        # Without this 2.3.2's project walk reaches $HOME/.agents.
        OPENCODE_DISABLE_PROJECT_CONFIG="1",
    )
    if profile.preset == "litellm":
        env["OPENCODE_DISABLE_FREE_MODELS"] = "1"
    if offline:
        env["OPENCODE_DISABLE_MODELS_FETCH"] = "1"
    return env


def checked_binary(home: Layout, base: Mapping[str, str]) -> Path:
    """The runtime binary, once it exists and reports the pinned version."""
    binary = binary_path(home)
    if not binary.is_file():
        if BINARY_OVERRIDE in os.environ:
            raise OpencodeError(
                f"{binary} is missing; fix {BINARY_OVERRIDE} or unset it to use the profile's runtime"
            )
        raise OpencodeError(MISSING)
    found = binary_version(binary, isolated_env(home, base))
    if found != PINNED_VERSION:
        raise OpencodeError(
            f"{binary} reports {found or 'no version'}; coven needs {PINNED_VERSION} "
            f"(run coven opencode install, or point {BINARY_OVERRIDE} at {PINNED_VERSION})"
        )
    return binary


def run(
    args: Sequence[str],
    *,
    exec_fn: Callable[[str, list[str], dict[str, str]], object] = os.execve,
) -> int:
    """Replace this process with the pinned OpenCode, in the profile's env."""
    home = layout()
    binary = checked_binary(home, os.environ)
    profile = load_profile(home)
    if profile is None:
        raise OpencodeError(NO_PROFILE)
    if profile.key_env not in os.environ:
        warn(f"${profile.key_env} is not set; the run goes on")
    env = run_env(profile, home, os.environ, offline=False)
    exec_fn(str(binary), [str(binary), *args], env)
    return 0
