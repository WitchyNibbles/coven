import json
import os
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parents[2] / "hooks"

type RunHook = Callable[..., subprocess.CompletedProcess[str]]


def _run_hook(
    script: Path,
    payload: dict[str, object] | list[object] | str,
    env: Mapping[str, str] | None = None,
    *,
    args: tuple[str, ...] = (),
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a hook the way Claude Code does, with `payload` (a dict or list, sent as JSON, or a
    raw string) on stdin. A `.sh` script runs under `/bin/sh` with `args`, a `.py` one under
    `python3 -I`. `env` is added to the test's own, which already points HOME and the XDG folders
    under `tmp_path`."""
    stdin = payload if isinstance(payload, str) else json.dumps(payload)
    command = (
        ["/bin/sh", str(script), *args]
        if script.suffix == ".sh"
        else [sys.executable, "-I", str(script), *args]
    )
    return subprocess.run(
        command,
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
        cwd=cwd,
        env={**os.environ, **(env or {})},
    )


@pytest.fixture
def hooks_dir() -> Path:
    return HOOKS


@pytest.fixture
def run_hook() -> RunHook:
    """`run_hook(script, payload, env=None, *, args=(), cwd=None) -> CompletedProcess`."""
    return _run_hook
