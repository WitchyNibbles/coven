"""hook_support: the loader and the failure log the hooks share. The hooks reach it with an import
at module top, so a missing or broken one crashes the hook, and fail-closed.sh must turn that crash
into a block."""

import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType

import pytest

from coven import safe_json

HOOKS = Path(__file__).resolve().parents[2] / "hooks"
SUPPORT = "hook_support.py"
ROLE = "COVEN_SESSION_ROLE"
SECRET = "/p/" + ".env"
# `python3 -I` still writes bytecode unless told not to; an empty value counts as unset.
BYTECODE_ALLOWED = {"PYTHONDONTWRITEBYTECODE": ""}

Run = Callable[..., subprocess.CompletedProcess[str]]

BROKEN_SUPPORT = {
    "missing": None,
    "import": "raise RuntimeError('hook_support broken at import')\n",
    "syntax": "def broken(:\n",
}
BROKEN_LOGS = {
    "missing": None,
    "import": "raise RuntimeError('log module broken at import')\n",
    "call": "def log_event(hook, record):\n    raise RuntimeError('log call broken')\n",
    "syntax": "def broken(:\n",
}


def copy_hooks(tmp_path: Path) -> Path:
    """A private copy of the hooks folder, without bytecode."""
    copy = tmp_path / "plugin" / "hooks"
    shutil.copytree(HOOKS, copy, ignore=shutil.ignore_patterns("__pycache__"))
    return copy


def break_module(folder: Path, name: str, source: str | None) -> None:
    """Replace `name` in `folder` with `source`, or delete it when `source` is None."""
    if source is None:
        (folder / name).unlink()
    else:
        (folder / name).write_text(source, encoding="utf-8")


def load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def modern_python(tmp_path: Path) -> dict[str, str]:
    """PATH whose python3 is this interpreter, so fail-closed.sh does not depend on the system one."""
    shim = tmp_path / "modern-bin"
    shim.mkdir()
    python = shim / "python3"
    python.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n', encoding="utf-8")
    python.chmod(python.stat().st_mode | stat.S_IXUSR)
    return {"PATH": f"{shim}{os.pathsep}{os.environ['PATH']}"}


def read_payload(path: str) -> dict[str, object]:
    return {"tool_name": "Read", "tool_input": {"file_path": path}}


def bash_payload(command: str, cwd: Path) -> dict[str, object]:
    return {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd)}


@pytest.fixture
def support() -> Iterator[ModuleType]:
    """The shipped hook_support, loaded in this process; the siblings it registers are removed."""
    known = set(sys.modules)
    yield load(HOOKS / SUPPORT, "coven_test_hook_support")
    for name in set(sys.modules) - known:
        del sys.modules[name]


@pytest.mark.parametrize("breakage", sorted(BROKEN_SUPPORT))
def test_a_broken_hook_support_blocks_deny_secrets_through_the_wrapper(
    run_hook: Run, tmp_path: Path, modern_python: dict[str, str], breakage: str
) -> None:
    hooks = copy_hooks(tmp_path)
    break_module(hooks, SUPPORT, BROKEN_SUPPORT[breakage])
    proc = run_hook(
        hooks / "fail-closed.sh",
        read_payload("/p/notes.txt"),
        modern_python,
        args=(str(hooks / "deny_secrets.py"),),
    )
    assert proc.returncode == 2
    assert proc.stderr.endswith("coven: deny-secrets failed (exit 1); blocking\n")


def run_pm(
    run_hook: Run, hooks: Path, env: dict[str, str], tmp_path: Path, *argv: str
) -> subprocess.CompletedProcess[str]:
    """pm_permissions through fail-closed.sh, asked about `git status`."""
    return run_hook(
        hooks / "fail-closed.sh",
        bash_payload("git status", tmp_path),
        env,
        args=(str(hooks / "pm_permissions.py"), *argv),
    )


@pytest.mark.parametrize("breakage", sorted(BROKEN_SUPPORT))
def test_a_pm_permission_request_is_denied_when_hook_support_is_broken(
    run_hook: Run, tmp_path: Path, modern_python: dict[str, str], breakage: str
) -> None:
    # Exit 2 is not honoured for a PermissionRequest, so the answer must be the deny verdict.
    hooks = copy_hooks(tmp_path)
    env = {**modern_python, ROLE: "pm"}
    intact = run_pm(run_hook, hooks, env, tmp_path)
    break_module(hooks, SUPPORT, BROKEN_SUPPORT[breakage])
    proc = run_pm(run_hook, hooks, env, tmp_path)
    decision = json.loads(proc.stdout)["hookSpecificOutput"]
    assert (intact.returncode, "damaged" in intact.stdout) == (0, False)
    assert proc.returncode == 0
    assert decision["hookEventName"] == "PermissionRequest"
    assert decision["decision"]["behavior"] == "deny"
    assert "hook files are damaged" in decision["decision"]["message"]
    assert "reinstall" in decision["decision"]["message"].lower()


@pytest.mark.parametrize("breakage", sorted(BROKEN_SUPPORT))
def test_a_pm_pre_tool_use_guard_blocks_with_exit_2_when_hook_support_is_broken(
    run_hook: Run, tmp_path: Path, modern_python: dict[str, str], breakage: str
) -> None:
    hooks = copy_hooks(tmp_path)
    break_module(hooks, SUPPORT, BROKEN_SUPPORT[breakage])
    proc = run_pm(run_hook, hooks, {**modern_python, ROLE: "pm"}, tmp_path, "--pre-tool-use")
    assert proc.returncode == 2
    assert proc.stdout == ""
    assert "coven pm-permissions: " in proc.stderr
    assert "hook files are damaged" in proc.stderr


@pytest.mark.parametrize("argv", [(), ("--pre-tool-use",)], ids=["permission", "pre-tool-use"])
def test_outside_a_pm_session_a_broken_hook_support_prints_nothing_and_allows(
    run_hook: Run, tmp_path: Path, modern_python: dict[str, str], argv: tuple[str, ...]
) -> None:
    hooks = copy_hooks(tmp_path)
    break_module(hooks, SUPPORT, None)
    proc = run_pm(run_hook, hooks, modern_python, tmp_path, *argv)
    assert (proc.returncode, proc.stdout) == (0, "")


@pytest.mark.parametrize("hook", ["session_context.py", "memory_context.py"])
def test_a_missing_hook_support_is_reported_by_the_context_hooks_not_swallowed(
    run_hook: Run, tmp_path: Path, modern_python: dict[str, str], hook: str
) -> None:
    hooks = copy_hooks(tmp_path)
    break_module(hooks, SUPPORT, None)
    proc = run_hook(hooks / "fail-closed.sh", {}, modern_python, args=(str(hooks / hook),))
    assert proc.returncode == 2
    assert proc.stdout == ""
    assert "failed (exit 1); blocking" in proc.stderr


@pytest.mark.parametrize("breakage", sorted(BROKEN_LOGS))
@pytest.mark.parametrize(("target", "code"), [("/p/notes.txt", 0), (SECRET, 2)])
def test_a_broken_hooklog_does_not_change_the_deny_secrets_verdict(
    run_hook: Run, tmp_path: Path, breakage: str, target: str, code: int
) -> None:
    hooks = copy_hooks(tmp_path)
    break_module(hooks, "hooklog.py", BROKEN_LOGS[breakage])
    proc = run_hook(hooks / "deny_secrets.py", read_payload(target))
    assert proc.returncode == code, proc.stderr


@pytest.mark.parametrize("breakage", sorted(BROKEN_LOGS))
def test_log_failure_never_raises_when_the_log_module_is_broken(
    tmp_path: Path, support: ModuleType, breakage: str
) -> None:
    hooks = copy_hooks(tmp_path)
    break_module(hooks, "hooklog.py", BROKEN_LOGS[breakage])
    copied = load(hooks / SUPPORT, "coven_test_broken_log_support")
    assert copied.log_failure("demo", "something failed") is None


def test_log_failure_writes_one_error_record_for_the_hook(
    support: ModuleType, tmp_path: Path
) -> None:
    support.log_failure("demo-hook", "it broke")
    log = tmp_path / "home" / ".cache" / "coven" / "logs" / "demo-hook.jsonl"
    record = log.read_text(encoding="utf-8").splitlines()[-1]
    assert '"error": "it broke"' in record
    assert '"ts"' in record


def test_siblings_are_registered_as_coven_hook_and_loaded_once(support: ModuleType) -> None:
    module = support.sibling("hooklog")
    assert module.__name__ == "coven_hook_hooklog"
    assert sys.modules["coven_hook_hooklog"] is module
    assert support.sibling("hooklog") is module
    assert Path(module.__file__) == HOOKS / "hooklog.py"


def test_a_sibling_is_found_next_to_hook_support(tmp_path: Path, support: ModuleType) -> None:
    hooks = copy_hooks(tmp_path)
    (hooks / "extra.py").write_text("VALUE = 7\n", encoding="utf-8")
    copied = load(hooks / SUPPORT, "coven_test_copied_support")
    assert copied.sibling("extra").VALUE == 7
    with pytest.raises(FileNotFoundError):
        support.sibling("extra")


def test_a_missing_sibling_raises_so_the_check_that_needs_it_fails(support: ModuleType) -> None:
    with pytest.raises(FileNotFoundError):
        support.sibling("no_such_hook_module")


def test_running_the_hooks_writes_no_bytecode_into_the_hooks_folder(
    run_hook: Run, tmp_path: Path
) -> None:
    hooks = copy_hooks(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    run_hook(hooks / "deny_secrets.py", read_payload(SECRET), BYTECODE_ALLOWED)
    run_hook(hooks / "deny_secrets.py", "not json", BYTECODE_ALLOWED)
    run_hook(
        hooks / "deny_secrets.py", bash_payload("cat notes.txt | head", repo), BYTECODE_ALLOWED
    )
    run_hook(
        hooks / "pm_permissions.py",
        bash_payload("git push origin main", repo),
        {**BYTECODE_ALLOWED, ROLE: "pm"},
    )
    run_hook(hooks / "session_context.py", {}, BYTECODE_ALLOWED)
    run_hook(hooks / "memory_context.py", {"cwd": str(repo)}, BYTECODE_ALLOWED)
    assert list(hooks.rglob("__pycache__")) == []
    assert list(hooks.rglob("*.pyc")) == []


def test_log_failure_survives_a_failing_sibling_loader(
    support: ModuleType, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def refuse(name: str) -> ModuleType:
        raise ImportError(name)

    monkeypatch.setattr(support, "sibling", refuse)
    assert support.log_failure("demo", "something failed") is None
    assert "coven demo: could not write the hook log" in caplog.text


def test_no_hook_is_named_like_a_standard_library_module() -> None:
    # Each hook puts its folder first on sys.path, so such a name would shadow the real module.
    names = {path.stem for path in HOOKS.glob("*.py")}
    assert names
    assert names & sys.stdlib_module_names == set()


def test_loads_decodes_up_to_the_depth_limit_and_refuses_past_it(support: ModuleType) -> None:
    assert support.loads("[" * 512 + "]" * 512)
    with pytest.raises(ValueError, match="nested deeper than 512"):
        support.loads("[" * 513 + "]" * 513)
    with pytest.raises(ValueError, match="nested deeper than 512"):
        support.loads('{"a":' * 100_000)


def test_loads_does_not_count_brackets_inside_strings(support: ModuleType) -> None:
    text = '{"k": "' + "[{" * 1000 + r'\"' + "[" * 1000 + '", "l": ["\\\\"]}'
    assert support.loads(text) == json.loads(text)


def test_loads_and_coven_safe_json_agree(support: ModuleType) -> None:
    """The hooks cannot import coven (they run `python3 -I` from the plugin folder), so the guard
    exists twice; this keeps the two from drifting."""
    depths = (1, 511, 512, 513, 5000)
    samples = [
        *("[" * depth + "]" * depth for depth in depths),
        r'{"a": "[[[[", "b": ["\\"]}',
        "[1,",
        "",
        '"' + "[" * 2000 + '"',
    ]
    for text in samples:
        outcomes = []
        for loads in (support.loads, safe_json.loads):
            try:
                outcomes.append(("value", loads(text)))
            except ValueError as error:
                outcomes.append(("error", type(error).__name__))
        assert outcomes[0] == outcomes[1], text[:40]
