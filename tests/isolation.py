"""What keeps a test away from the real home, config, cache, Claude directory and git identity.

The fixtures are autouse, but pytest only collects a fixture from a conftest's namespace, and a run
may load one conftest alone (`-p conftest` for a test file outside `tests/`, which never reaches
`tests/conftest.py`). So every `conftest.py` under `tests/` binds `ISOLATION_FIXTURES` by name:

    isolated_home = isolation.isolated_home

A name bound in several conftests is still one fixture: pytest runs it once per test.

`isolated_home` also marks the scratch home in `SCRATCH_HOME`. The helpers that write coven's
config or a store under HOME call `require_scratch_home` first, so a run without the isolation
fails with a clear error instead of writing the real home.
"""

import os
from pathlib import Path

import pytest

SCRATCH_HOME = "COVEN_TEST_SCRATCH_HOME"
ISOLATION_FIXTURES = ("isolated_home", "git_isolation")

# What git exports to a hook it runs (a pre-commit run starts pytest with some of these set): each
# one would point a test's git at the repo that started the run.
GIT_HOOK_VARIABLES = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_PREFIX",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_COMMON_DIR",
)


class ScratchHomeError(RuntimeError):
    """A test helper was about to write under a HOME that `isolated_home` did not make."""


def require_scratch_home() -> Path:
    """HOME, when it is the scratch home `isolated_home` marked; otherwise a refusal."""
    home = os.environ.get("HOME")
    if not home or os.environ.get(SCRATCH_HOME) != home:
        raise ScratchHomeError(
            f"HOME ({home!r}) is not the scratch home that isolated_home marks in {SCRATCH_HOME}: "
            "the conftest in use does not bind the isolation fixtures from tests/isolation.py"
        )
    return Path(home)


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No test reads or writes the real home, config, cache or Claude directory."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv(SCRATCH_HOME, str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(home / ".cache"))
    monkeypatch.setenv("XDG_DATA_HOME", str(home / ".local" / "share"))
    monkeypatch.setenv("XDG_STATE_HOME", str(home / ".local" / "state"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home / ".claude"))
    monkeypatch.delenv("COVEN_SESSION_ROLE", raising=False)
    # Claude Code sets it in a background session: a run from one must not hand it to a hook.
    monkeypatch.delenv("CLAUDE_JOB_DIR", raising=False)
    for name in ("COVEN_OPENCODE_HOME", "COVEN_OPENCODE_BIN", *os.environ):
        if name.startswith("OPENCODE_") or name.startswith("COVEN_OPENCODE_"):
            monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def git_isolation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Git reads one test-owned config and identity, never the user's, and never the outer repo."""
    config = tmp_path / "gitconfig"
    config.write_text(
        "[user]\n\tname = Test\n\temail = test@example.com\n[init]\n\tdefaultBranch = main\n"
        # No detached auto-maintenance: it holds lock files while a test copies the repo.
        "[maintenance]\n\tauto = false\n[gc]\n\tauto = 0\n"
    )
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for role in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{role}_NAME", "Test")
        monkeypatch.setenv(f"GIT_{role}_EMAIL", "test@example.com")
    for name in GIT_HOOK_VARIABLES:
        monkeypatch.delenv(name, raising=False)
