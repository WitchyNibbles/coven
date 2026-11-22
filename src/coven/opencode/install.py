"""Installing the pinned OpenCode runtime into the profile with npm."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Final

from coven.errors import warn
from coven.opencode.canary import AUTH_VAR, USERNAME_ENV
from coven.opencode.profile import (
    OPENCODE_KEY_ENV,
    PINNED_VERSION,
    Layout,
    OpencodeError,
    load_profile,
)
from coven.opencode.runtime import binary_version, isolated_env

PACKAGE: Final = "@opencode/cli"
_VERSION = re.compile(r"\d+\.\d+\.\d+")
_STDERR_LINES: Final = 5
NPM_TIMEOUT: Final = 900.0


def _failure(code: int, stderr: str) -> OpencodeError:
    tail = " | ".join(stderr.strip().splitlines()[-_STDERR_LINES:])
    return OpencodeError(f"npm install failed (exit {code}): {tail}")


def _run_npm(npm: str, prefix: Path, version: str, env: dict[str, str]) -> None:
    command = [npm, "install", "--prefix", str(prefix), "--save-exact", f"{PACKAGE}@{version}"]
    try:
        done = subprocess.run(
            command, env=env, capture_output=True, text=True, timeout=NPM_TIMEOUT, check=False
        )
    except subprocess.TimeoutExpired as error:
        raise OpencodeError(f"npm install did not finish within {NPM_TIMEOUT:.0f} s") from error
    except OSError as error:
        raise OpencodeError(f"cannot run npm: {error}") from error
    if done.returncode != 0:
        raise _failure(done.returncode, done.stderr)


def _npm_env(layout: Layout) -> dict[str, str]:
    """The profile env for npm: package scripts run the new OpenCode, which must not touch the
    user's own OpenCode folders, and must not see the key or the server login. `HOME`, `PATH` and
    the proxy variables stay, so npm's own config and cache keep working."""
    hidden = {OPENCODE_KEY_ENV, AUTH_VAR, USERNAME_ENV}
    try:
        profile = load_profile(layout)
    except OpencodeError:
        profile = None  # a broken profile must not block the install
    if profile is not None:
        hidden.add(profile.key_env)
    return isolated_env(layout, os.environ, drop=hidden)


def install(layout: Layout, version: str) -> Path:
    """Install `version` into `layout.runtime_dir(version)` and return its binary."""
    if not _VERSION.fullmatch(version):
        raise OpencodeError(f"--version must look like 2.3.2, not {version!r}")
    if version != PINNED_VERSION:
        warn(f"run and doctor use {PINNED_VERSION} only")
    binary = layout.runtime_binary(version)
    for folder in layout.folders():
        folder.mkdir(parents=True, exist_ok=True)
    env = isolated_env(layout, os.environ)
    if binary_version(binary, env) == version:
        print(f"opencode {version} is already installed: {binary}")
        return binary
    npm = shutil.which("npm")
    if npm is None:
        raise OpencodeError("npm not found on PATH; install Node.js with npm and re-run")
    _run_npm(npm, layout.runtime_dir(version), version, _npm_env(layout))
    found = binary_version(binary, env)
    if found != version:
        raise OpencodeError(f"installed opencode reports {found}, expected {version}")
    return binary
