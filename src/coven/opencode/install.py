"""Installing the pinned OpenCode runtime with npm and the pinned superpowers with git."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Final

from coven.errors import warn
from coven.opencode import superpowers
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
GIT_TIMEOUT: Final = 900.0
_SHA_SHOWN: Final = 8


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


class _GitFailed(Exception):
    """A git command that did not give a result; the message says why."""


def _run_git(git: str, arguments: list[str]) -> str:
    """The stdout of `git` with `arguments`: no prompt, no hooks, bounded in time."""
    command = [git, "-c", "core.hooksPath=/dev/null", *arguments]
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    try:
        done = subprocess.run(
            command, env=env, capture_output=True, text=True, timeout=GIT_TIMEOUT, check=False
        )
    except subprocess.TimeoutExpired as error:
        raise _GitFailed(f"git did not finish within {GIT_TIMEOUT:.0f} s") from error
    except OSError as error:
        raise _GitFailed(f"cannot run git: {error}") from error
    if done.returncode != 0:
        tail = " | ".join(done.stderr.strip().splitlines()[-_STDERR_LINES:])
        raise _GitFailed(f"git exited {done.returncode}: {tail}")
    return done.stdout


def _skip_superpowers(why: str) -> None:
    tag = superpowers.PINNED_TAG
    warn(
        f"cannot fetch superpowers {tag}: {why}; superpowers will come from the Claude Code "
        "plugin if it is installed"
    )


def _clone_pinned(git: str, work: Path) -> str:
    """Clone the pinned tag into `work` and return the commit it checked out."""
    url, tag = superpowers.REPO_URL, superpowers.PINNED_TAG
    _run_git(git, ["clone", "--depth", "1", "--branch", tag, "--quiet", url, str(work)])
    return _run_git(git, ["-C", str(work), "rev-parse", "HEAD"]).strip()


def _fetch_superpowers(layout: Layout) -> None:
    """Clone the pinned superpowers into the profile unless it is there; a failed fetch only warns.

    The clone happens in a sibling folder and is renamed into place only after its commit matches
    the pin, so the target never holds a half clone or a different commit.
    """
    tag = superpowers.PINNED_TAG
    target = layout.superpowers_dir(tag)
    if (target / "skills").is_dir():
        print(f"superpowers {tag} is already installed: {target}")
        return
    git = shutil.which("git")
    if git is None:
        _skip_superpowers("git not found on PATH")
        return
    work = target.with_name(f".tmp-{tag}-{os.getpid()}")
    work.parent.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(work, ignore_errors=True)
    try:
        try:
            found = _clone_pinned(git, work)
        except _GitFailed as error:
            _skip_superpowers(str(error))
            return
        if found != superpowers.PINNED_SHA:
            raise OpencodeError(
                f"superpowers {tag} resolved to {found}, expected "
                f"{superpowers.PINNED_SHA[:_SHA_SHOWN]}…; not using it"
            )
        shutil.rmtree(target, ignore_errors=True)
        work.rename(target)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print(f"superpowers {tag}: {target}")


def install(layout: Layout, version: str) -> Path:
    """Install `version` into `layout.runtime_dir(version)`, fetch superpowers, return the binary."""
    binary = _install_runtime(layout, version)
    _fetch_superpowers(layout)
    return binary


def _install_runtime(layout: Layout, version: str) -> Path:
    """Install OpenCode `version` with npm unless its binary is already there."""
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
