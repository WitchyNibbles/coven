"""Installing the pinned OpenCode runtime with npm and the pinned superpowers with git."""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import signal
import subprocess
from pathlib import Path
from typing import Final
from urllib.parse import urlparse

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


def git_options(url: str) -> list[str]:
    """The `-c` options every git call gets: no hooks, and only `url`'s own protocol allowed.

    The pinned URL is https, so git cannot be redirected to another transport; a local path
    (a test's stand-in) is the `file` protocol.
    """
    protocol = urlparse(url).scheme or "file"
    return [
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "protocol.allow=never",
        "-c",
        f"protocol.{protocol}.allow=always",
    ]


def _git_env() -> dict[str, str]:
    """The env git runs with: the proxy variables stay, nothing that points git at another repo,
    another config or a credential helper. The pinned commit checks the content, so git's own
    config (mirrors, proxies, credentials) is not used."""
    env = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith("GIT_") and name != "SSH_ASKPASS"
    }
    env.update(GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0")
    return env


def _kill_group(process: subprocess.Popen[str]) -> None:
    """Kill git and every process it started (`git-remote-https`, `index-pack`)."""
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)


def _run_git(git: str, arguments: list[str]) -> str:
    """The stdout of `git` with `arguments`: no prompt, no hooks, bounded in time."""
    command = [git, *git_options(superpowers.REPO_URL), *arguments]
    try:
        # Its own session, so a timeout can kill the whole group and nothing keeps writing.
        with subprocess.Popen(
            command,
            env=_git_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        ) as process:
            try:
                stdout, stderr = process.communicate(timeout=GIT_TIMEOUT)
            except subprocess.TimeoutExpired as error:
                _kill_group(process)
                process.communicate()
                raise _GitFailed(f"git did not finish within {GIT_TIMEOUT:g} s") from error
            except BaseException:
                _kill_group(process)
                raise
    except OSError as error:
        raise _GitFailed(f"cannot run git: {error}") from error
    if process.returncode != 0:
        tail = " | ".join(stderr.strip().splitlines()[-_STDERR_LINES:])
        raise _GitFailed(f"git exited {process.returncode}: {tail}")
    return stdout


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


def _clone_into_place(git: str, target: Path, work: Path) -> None:
    """Clone the pinned tag to `work`, check its commit, and only then move it to `target`."""
    work.parent.mkdir(parents=True, exist_ok=True)
    for stale in work.parent.glob(".tmp-*"):  # left by an install that was killed
        shutil.rmtree(stale, ignore_errors=True)
    found = _clone_pinned(git, work)
    if found != superpowers.PINNED_SHA:
        raise OpencodeError(
            f"superpowers {superpowers.PINNED_TAG} resolved to {found}, expected "
            f"{superpowers.PINNED_SHA[:_SHA_SHOWN]}…; not using it"
        )
    if target.exists():
        shutil.rmtree(target)
    work.rename(target)


def _fetch_superpowers(layout: Layout) -> None:
    """Clone the pinned superpowers into the profile unless it is there; a failed fetch only warns.

    The clone happens in a sibling folder and is renamed into place only after its commit matches
    the pin, so the target never holds a half clone or a different commit. A wrong commit is not a
    failed fetch: it raises.
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
    try:
        _clone_into_place(git, target, work)
    except (_GitFailed, OSError) as error:
        _skip_superpowers(str(error))
        return
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
