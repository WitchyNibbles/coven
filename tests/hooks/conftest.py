import contextlib
import io
import json
import os
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path

import corpus_harness as harness
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


# The guards' own fixtures, bound by name: pytest finds a fixture in the namespace of this module,
# and the fixtures ask for each other by these names (`store` wants `pg`). The cross-guard corpus
# and the cache tests of test_shell_words.py both run on them.
world = harness.world
repo = harness.repo
patient_hook = harness.patient_hook
store = harness.store
patient_guard = harness.patient_guard
# The modules the fixtures patch, which `store` asks for by name.
pm = harness.pm
pg = harness.pg
opencode_guard = harness.opencode_guard


@pytest.fixture
def runners(
    monkeypatch: pytest.MonkeyPatch,
    world,
    store: Path,
    repo: Path,
) -> dict[str, harness.Runner]:
    """One runner per guard: (command, cwd) -> Result, in this process, each as its hook runs it."""
    monkeypatch.setenv(harness.ROLE, "pm")
    # pm's guard refuses a push while GIT_* variables are set (pm's own `isolated_git` fixture
    # clears them, but it is autouse and would run before `store` commits): clear them now.
    # `GIT_CONFIG_NOSYSTEM` (the conftest's, which keeps the machine's git config out) stays; only
    # the pm runner lifts it, for its own call.
    for name in [
        name for name in os.environ if name.startswith("GIT_") and name != harness.KEPT_GIT
    ]:
        monkeypatch.delenv(name)
    # The gate imports `prod_guard` by name: hand it the module the prod tests patch.
    monkeypatch.setitem(sys.modules, "prod_guard", pg)

    def stdin_of(command: str, cwd: Path) -> None:
        monkeypatch.setattr(sys, "stdin", io.StringIO(harness.payload_of(command, cwd)))

    def call(guard: str, main: Callable[[], int], command: str, cwd: Path) -> harness.Result:
        stdin_of(command, cwd)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main()
        assert out.getvalue() == "", f"{guard} wrote to stdout: {out.getvalue()!r}"
        return harness.result_of(guard, code, err.getvalue())

    def run_deny_secrets(command: str, cwd: Path) -> harness.Result:
        return call("deny_secrets", harness.deny_secrets.main, command, cwd)

    def run_pm(command: str, cwd: Path) -> harness.Result:
        if not pm._is_guarded(command):
            return harness.Result("n/a", "")
        stdin = io.StringIO(harness.payload_of(command, cwd))
        out, err = io.StringIO(), io.StringIO()
        # pm refuses a push while any GIT_* variable is set, and a PM session has none: take the
        # one the other guards keep out of this call only.
        with (
            monkeypatch.context() as scoped,
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            scoped.delenv(harness.KEPT_GIT, raising=False)
            code = pm.main(["--pre-tool-use"], stdin, {harness.ROLE: "pm"})
        assert out.getvalue() == "", f"pm_permissions wrote to stdout: {out.getvalue()!r}"
        return harness.result_of("pm_permissions", code, err.getvalue())

    def run_prod(command: str, cwd: Path) -> harness.Result:
        monkeypatch.setattr(sys, "argv", [str(HOOKS / "prod_guard_gate.py")])
        return call("prod_guard", harness.gate.main, command, cwd)

    def run_opencode(command: str, cwd: Path) -> harness.Result:
        arguments = [
            "--profile",
            str(world.profile),
            "--serve",
            str(world.serve),
            "--coven-root",
            str(world.coven),
        ]
        return call("opencode_server_guard", lambda: opencode_guard.main(arguments), command, cwd)

    return {
        "deny_secrets": run_deny_secrets,
        "pm_permissions": run_pm,
        "prod_guard": run_prod,
        "opencode_server_guard": run_opencode,
    }


@pytest.fixture
def context(world, repo: Path, store: Path) -> harness.Context:
    """The folders the corpus entries name: `<project>` (the working folder, a git repo under tmp),
    `<home>` (the scratch HOME), `<claude>` (its user folder), `<store>` (the committed store with
    the made-up guard config), `<profile>`, `<serve>` (holds the password file) and `<coven>`."""
    home = Path(world.profile.parent)
    marks = {
        "<project>": str(repo),
        "<home>": str(home),
        "<claude>": str(home / ".claude"),
        "<store>": str(store),
        "<profile>": str(world.profile),
        "<serve>": str(world.serve),
        "<coven>": str(world.coven),
        "<tmp>": str(world.root),
    }
    return harness.Context(repo, marks)
