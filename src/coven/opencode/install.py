"""Installing the pinned OpenCode runtime into the profile with npm."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Final

from coven.errors import warn
from coven.opencode.profile import PINNED_VERSION, Layout, OpencodeError
from coven.opencode.runtime import binary_version, isolated_env

PACKAGE: Final = "@opencode/cli"
_VERSION = re.compile(r"\d+\.\d+\.\d+")
_STDERR_LINES: Final = 5


def _failure(code: int, stderr: str) -> OpencodeError:
    tail = " | ".join(stderr.strip().splitlines()[-_STDERR_LINES:])
    return OpencodeError(f"npm install failed (exit {code}): {tail}")


def _run_npm(npm: str, prefix: Path, version: str, timeout: float) -> None:
    command = [npm, "install", "--prefix", str(prefix), "--save-exact", f"{PACKAGE}@{version}"]
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as error:
        raise OpencodeError(f"npm install did not finish within {timeout:.0f} s") from error
    except OSError as error:
        raise OpencodeError(f"cannot run npm: {error}") from error
    if done.returncode != 0:
        raise _failure(done.returncode, done.stderr)


def install(layout: Layout, version: str, *, timeout: float = 900.0) -> Path:
    """Install `version` into `layout.runtime_dir(version)` and return its binary."""
    if not _VERSION.fullmatch(version):
        raise OpencodeError(f"--version must look like 2.3.2, not {version!r}")
    if version != PINNED_VERSION:
        warn(f"run and doctor use {PINNED_VERSION} only")
    binary = layout.runtime_binary(version)
    env = isolated_env(layout, os.environ)
    if binary_version(binary, env) == version:
        print(f"opencode {version} is already installed: {binary}")
        return binary
    npm = shutil.which("npm")
    if npm is None:
        raise OpencodeError("npm not found on PATH; install Node.js with npm and re-run")
    _run_npm(npm, layout.runtime_dir(version), version, timeout)
    found = binary_version(binary, env)
    if found != version:
        raise OpencodeError(f"installed opencode reports {found}, expected {version}")
    return binary
