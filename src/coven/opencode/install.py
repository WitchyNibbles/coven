"""Installing the pinned OpenCode runtime into the profile with npm."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Final

from coven.errors import warn
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


def _failure(code: int, stderr: str) -> OpencodeError:
    tail = " | ".join(stderr.strip().splitlines()[-_STDERR_LINES:])
    return OpencodeError(f"npm install failed (exit {code}): {tail}")


def _run_npm(npm: str, prefix: Path, version: str, env: dict[str, str], timeout: float) -> None:
    command = [npm, "install", "--prefix", str(prefix), "--save-exact", f"{PACKAGE}@{version}"]
    try:
        done = subprocess.run(
            command, env=env, capture_output=True, text=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired as error:
        raise OpencodeError(f"npm install did not finish within {timeout:.0f} s") from error
    except OSError as error:
        raise OpencodeError(f"cannot run npm: {error}") from error
    if done.returncode != 0:
        raise _failure(done.returncode, done.stderr)


def _npm_env(layout: Layout) -> dict[str, str]:
    """The profile env for npm: package scripts run the new OpenCode, which must not touch the
    user's own OpenCode folders, and must not see the key. `HOME`, `PATH` and the proxy variables
    stay, so npm's own config and cache keep working."""
    secret_names = {OPENCODE_KEY_ENV}
    try:
        profile = load_profile(layout)
    except OpencodeError:
        profile = None  # a broken profile must not block the install
    if profile is not None:
        secret_names.add(profile.key_env)
    base = {k: v for k, v in os.environ.items() if k not in secret_names}
    return isolated_env(layout, base)


def _make_folders(layout: Layout) -> None:
    """The folders `isolated_env` points at: the postinstall step makes a temp folder in `tmp`."""
    for folder in (
        layout.config_dir,
        layout.data,
        layout.cache,
        layout.state,
        layout.home,
        layout.tmp,
    ):
        folder.mkdir(parents=True, exist_ok=True)


def install(layout: Layout, version: str, *, timeout: float = 900.0) -> Path:
    """Install `version` into `layout.runtime_dir(version)` and return its binary."""
    if not _VERSION.fullmatch(version):
        raise OpencodeError(f"--version must look like 2.3.2, not {version!r}")
    if version != PINNED_VERSION:
        warn(f"run and doctor use {PINNED_VERSION} only")
    binary = layout.runtime_binary(version)
    _make_folders(layout)
    env = isolated_env(layout, os.environ)
    if binary_version(binary, env) == version:
        print(f"opencode {version} is already installed: {binary}")
        return binary
    npm = shutil.which("npm")
    if npm is None:
        raise OpencodeError("npm not found on PATH; install Node.js with npm and re-run")
    _run_npm(npm, layout.runtime_dir(version), version, _npm_env(layout), timeout)
    found = binary_version(binary, env)
    if found != version:
        raise OpencodeError(f"installed opencode reports {found}, expected {version}")
    return binary
