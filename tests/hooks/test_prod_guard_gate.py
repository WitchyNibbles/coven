"""prod_guard_gate.py: the entry hooks.json runs for the production guard. With the guard off it
exits 0 without reading stdin and without importing prod_guard; in every other case it hands over
to prod_guard, which decides exactly as it does when run directly."""

import importlib.util
import json
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

HOOKS = Path(__file__).resolve().parents[2] / "hooks"
GATE = HOOKS / "prod_guard_gate.py"
GUARD = HOOKS / "prod_guard.py"
ROLE = "COVEN_SESSION_ROLE"
PROD_COMMAND = "psql -h orders-db.example.internal -c 'x'"
STORE_GUARD = {
    "targets": [
        {"system": "orders-db", "kind": "production", "match": ["psql\\s+.*-h\\s*orders-db"]}
    ]
}
# What the gate must not import while the guard is off.
HEAVY = ("prod_guard", "subprocess", "logging", "pathlib", "typing", "ipaddress", "hook_support")

Run = Callable[..., subprocess.CompletedProcess[str]]


def home() -> Path:
    return Path(os.environ["HOME"])


def write_config(text: str) -> None:
    file = home() / ".config" / "coven" / "config.json"
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(text, encoding="utf-8")


def store_without_guard_file() -> Path:
    store = home() / "coven-memory"
    (store / "systems").mkdir(parents=True)
    write_config(json.dumps({"memory_path": str(store)}))
    return store


def committed_store() -> Path:
    store = home() / "coven-memory"
    (store / "systems").mkdir(parents=True)
    (store / "approvals").mkdir()
    (store / "systems" / "guard.json").write_text(json.dumps(STORE_GUARD))
    for command in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "guard"]):
        subprocess.run(["git", "-C", str(store), *command], check=True, capture_output=True)
    return store


def bash(command: str) -> dict[str, object]:
    return {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": "/tmp"}


def read(path: str = "/tmp/x") -> dict[str, object]:
    return {"tool_name": "Read", "tool_input": {"file_path": path}}


def gate_run(payload: str) -> dict[str, Any]:
    """Run the gate in a fresh interpreter: its exit code, the modules it imported, and what is
    left on stdin."""
    code = (
        "import json, runpy, sys\n"
        "before = set(sys.modules)\n"
        "code = 0\n"
        "try:\n"
        f"    runpy.run_path({str(GATE)!r}, run_name='__main__')\n"
        "except SystemExit as stop:\n"
        "    code = stop.code\n"
        "new = sorted(set(sys.modules) - before)\n"
        "print(json.dumps({'code': code, 'new': new, 'stdin': sys.stdin.read()}))\n"
    )
    done = subprocess.run(
        [sys.executable, "-I", "-c", code],
        input=payload,
        capture_output=True,
        text=True,
        check=True,
    )
    result: dict[str, Any] = json.loads(done.stdout.splitlines()[-1])
    return result


def assert_never_loaded_the_guard(result: dict[str, Any]) -> None:
    loaded = set(result["new"])
    assert not loaded & set(HEAVY), sorted(loaded & set(HEAVY))


def test_with_no_config_and_no_store_the_gate_exits_0_before_it_imports_the_guard() -> None:
    result = gate_run("not even json")
    assert result["code"] == 0
    assert result["stdin"] == "not even json"
    assert_never_loaded_the_guard(result)


def test_with_a_store_that_has_no_guard_file_the_gate_exits_0_before_it_imports_the_guard() -> None:
    store_without_guard_file()
    result = gate_run(json.dumps(bash(PROD_COMMAND)))
    assert result["code"] == 0
    assert result["stdin"] == json.dumps(bash(PROD_COMMAND))
    assert_never_loaded_the_guard(result)


def verdicts(run_hook: Run, payload: object, env: dict[str, str]) -> tuple[tuple[object, ...], ...]:
    """(code, stdout, stderr) of the gate and of prod_guard.py itself, side by side."""
    both = []
    for script in (GATE, GUARD):
        done = run_hook(script, payload, env)
        both.append((done.returncode, done.stdout, done.stderr))
    return tuple(both)


def same_verdict(run_hook: Run, payload: object, env: dict[str, str]) -> tuple[object, ...]:
    gate, guard = verdicts(run_hook, payload, env)
    assert gate == guard
    return gate


def test_a_working_tree_guard_file_gives_the_same_verdicts_as_the_guard(run_hook: Run) -> None:
    committed_store()
    allowed = same_verdict(run_hook, read(), {})
    refused = same_verdict(run_hook, bash(PROD_COMMAND), {})
    assert allowed == (0, "", "")
    assert refused[0] == 2
    assert "'orders-db' is production" in str(refused[2])


def test_the_latch_alone_turns_the_gate_over_to_the_guard(run_hook: Run, tmp_path: Path) -> None:
    write_config(json.dumps({"memory_path": str(tmp_path / "nowhere"), "guard": "required"}))
    refused = same_verdict(run_hook, bash("ls"), {})
    assert refused[0] == 2
    assert "does not exist" in str(refused[2])
    assert same_verdict(run_hook, read(), {}) == (0, "", "")


@pytest.mark.parametrize("config", ["{nope", "[]", '{"guard": "maybe"}', '{"memory_path": "x"}'])
def test_a_broken_config_gives_the_same_verdicts_as_the_guard(run_hook: Run, config: str) -> None:
    write_config(config)
    unset = same_verdict(run_hook, bash("ls"), {})
    pm = same_verdict(run_hook, bash("ls"), {ROLE: "pm"})
    assert unset[0] == 0
    assert pm[0] == 2
    assert "run coven doctor" in str(pm[2])
    assert same_verdict(run_hook, read(), {ROLE: "pm"})[0] == 0


def test_a_store_without_a_guard_file_gives_the_same_verdicts_as_the_guard(run_hook: Run) -> None:
    store_without_guard_file()
    assert same_verdict(run_hook, bash(PROD_COMMAND), {ROLE: "pm"}) == (0, "", "")


def test_the_guard_is_still_runnable_directly(run_hook: Run) -> None:
    committed_store()
    assert run_hook(GUARD, bash(PROD_COMMAND), {}).returncode == 2
    assert run_hook(GUARD, read(), {}).returncode == 0


def load_gate() -> ModuleType:
    spec = importlib.util.spec_from_file_location("coven_test_gate", GATE)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeGuard:
    """Stands in for prod_guard: what the gate returns is whatever its cli() returns."""

    @staticmethod
    def cli() -> int:
        return 7


@pytest.mark.parametrize("error", [RuntimeError("home"), OSError("disk"), ImportError("gone")])
def test_an_error_inside_the_gate_hands_over_to_the_guard(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    gate = load_gate()
    import guard_config  # on sys.path: loading the gate put the hooks folder there

    def broken() -> bool:
        raise error

    monkeypatch.setattr(guard_config, "guard_is_off", broken)
    monkeypatch.setitem(sys.modules, "prod_guard", FakeGuard)
    assert gate.main() == 7


def test_an_off_guard_never_looks_for_prod_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    gate = load_gate()
    import guard_config

    monkeypatch.setattr(guard_config, "guard_is_off", lambda: True)
    monkeypatch.setitem(sys.modules, "prod_guard", None)  # importing it would raise
    assert gate.main() == 0
