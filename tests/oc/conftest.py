import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from doctor_kit import KEY_NAME, KEY_VALUE, LITELLM_FLAGS, Good
from fakes import fake_opencode, install_superpowers
from serve_kit import Procs

from coven import cli
from coven.opencode import serve, superpowers
from coven.opencode.profile import layout


@pytest.fixture(autouse=True)
def claude_plugins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty Claude Code plugins folder: no test reads the real installed-plugins file."""
    folder = tmp_path / "claude-plugins"
    folder.mkdir()
    monkeypatch.setenv("CLAUDE_CODE_PLUGIN_CACHE_DIR", str(folder))
    return folder


@pytest.fixture(autouse=True)
def no_superpowers_download(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`coven opencode install` clones from a path that does not exist, never from github.com.

    A test that wants a clone points `superpowers.REPO_URL` at a local repo. As a second guard,
    any process a test starts with a network URL among its arguments fails the test.
    """
    monkeypatch.setattr(superpowers, "REPO_URL", str(tmp_path / "no-such-superpowers-repo"))
    start = subprocess.Popen.__init__

    def guarded(self: subprocess.Popen[Any], args: Any, *rest: Any, **named: Any) -> None:
        parts = [args] if isinstance(args, str | os.PathLike) else list(args)
        for part in map(os.fsdecode, parts):
            if "://" in part and not part.startswith("file://"):
                pytest.fail(f"a test tried to reach the network: {part}")
        start(self, args, *rest, **named)

    monkeypatch.setattr(subprocess.Popen, "__init__", guarded)


@pytest.fixture
def good(claude_plugins: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Good:
    """A profile set up with the litellm preset, a fake 2.3.2 binary and the key set."""
    install_superpowers(claude_plugins)
    monkeypatch.setenv("COVEN_OPENCODE_HOME", str(tmp_path / "profile"))
    assert cli.main(["opencode", "setup", *LITELLM_FLAGS]) == 0
    bin_dir = tmp_path / "bin"
    monkeypatch.setenv("COVEN_OPENCODE_BIN", str(fake_opencode(bin_dir, "2.3.2")))
    monkeypatch.setenv(KEY_NAME, KEY_VALUE)
    cwd = tmp_path / "work"
    cwd.mkdir()
    return Good(layout(), bin_dir, cwd)


@pytest.fixture
def procs() -> Iterator[Procs]:
    """The processes a test starts through `procs.spawn`, killed in teardown."""
    tracked = Procs()
    yield tracked
    for process in tracked.started:
        process.kill()
        process.wait()


@pytest.fixture
def interpreter_is_the_server(monkeypatch: pytest.MonkeyPatch) -> None:
    """The fake server is a script, so the process behind it is the interpreter."""
    exe = Path(os.path.realpath(sys.executable))
    monkeypatch.setattr(serve, "server_exe", lambda _layout: exe)
