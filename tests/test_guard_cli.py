"""`coven guard check` and `coven guard explain`: the production guard's own rules, read-only."""

import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from support import REPO, commit_all, git

from coven import cli, guard
from coven.errors import CovenError

HOOK = REPO / "hooks" / "prod_guard.py"
ROLE = "COVEN_SESSION_ROLE"
PROD_COMMAND = "psql -h orders-db.example.internal -c 'select 1'"
PROD_PATTERN = "psql\\s+.*-h\\s*orders-db"
GUARD = {
    "targets": [
        {
            "system": "orders-db",
            "kind": "production",
            "match": [PROD_PATTERN, "pg_dump\\s+.*orders-db"],
            "tools": ["mcp__orders__write"],
        },
        {
            "system": "mail-send",
            "kind": "gated",
            "match": ["mail-cli\\s+send"],
            "tools": ["mcp__mail__send"],
        },
    ]
}
VERDICTS = ("allowed", "refused for a PM", "refused for every session")

type Run = Callable[..., tuple[int, str, str]]


@pytest.fixture
def run(capsys: pytest.CaptureFixture[str]) -> Run:
    def runner(*args: str) -> tuple[int, str, str]:
        code = cli.main(["guard", *args])
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    return runner


def home() -> Path:
    return Path(os.environ["HOME"])


def write_config(text: str) -> None:
    file = home() / ".config" / "coven" / "config.json"
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(text)


def configure(store: Path, *, latch: bool = False) -> None:
    fields = {"memory_path": str(store), **({"guard": "required"} if latch else {})}
    write_config(json.dumps(fields))


def make_store(path: Path, *, committed: bool = True, guard_json: object = GUARD) -> Path:
    (path / "systems").mkdir(parents=True)
    (path / "approvals").mkdir()
    (path / "systems" / "guard.json").write_text(json.dumps(guard_json))
    git(path, "init", "-q")
    if committed:
        commit_all(path)
    return path


def approve(store: Path, name: str = "a1", **fields: object) -> str:
    expires = (datetime.now(UTC) + timedelta(hours=2)).replace(microsecond=0).isoformat()
    record: dict[str, object] = {
        "id": name,
        "system": "orders-db",
        "commands": [PROD_COMMAND],
        "tools": [],
        "expires": expires,
        **fields,
    }
    (store / "approvals" / f"{name}.json").write_text(json.dumps(record))
    commit_all(store)
    return str(record["expires"])


@pytest.fixture
def store() -> Path:
    path = make_store(home() / "coven-memory")
    configure(path)
    return path


def verdict_of(out: str) -> str:
    return out.rstrip("\n").splitlines()[-1]


# --- check ---


def test_check_accepts_a_valid_file(run: Run, tmp_path: Path) -> None:
    file = tmp_path / "guard.json"
    file.write_text(json.dumps(GUARD))
    assert run("check", str(file)) == (0, "ok: 2 targets\n", "")


@pytest.mark.parametrize(
    "text",
    ["{nope", json.dumps({"targets": [{"system": "x", "kind": "prod"}]}), "[]"],
    ids=["not-json", "bad-kind", "not-an-object"],
)
def test_check_says_why_a_file_is_invalid(run: Run, tmp_path: Path, text: str) -> None:
    file = tmp_path / "guard.json"
    file.write_text(text)
    code, out, err = run("check", str(file))
    assert (code, out) == (1, "")
    assert err.startswith(f"coven: {file}")


def test_check_says_why_a_file_cannot_be_read(run: Run, tmp_path: Path) -> None:
    code, out, err = run("check", str(tmp_path / "missing.json"))
    assert (code, out) == (1, "")
    assert "missing.json" in err


def test_check_never_reads_stdin(run: Run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class Closed:
        def read(self, *_args: object) -> str:
            raise AssertionError("read stdin")

        readline = read

    monkeypatch.setattr(sys, "stdin", Closed())
    file = tmp_path / "guard.json"
    file.write_text(json.dumps(GUARD))
    assert run("check", str(file))[0] == 0


def test_check_needs_no_store_and_no_config(run: Run, tmp_path: Path) -> None:
    file = tmp_path / "guard.json"
    file.write_text(json.dumps({"targets": []}))
    assert run("check", str(file)) == (0, "ok: 0 targets\n", "")


def test_the_guard_command_is_listed(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--help"]) == 0
    assert "guard" in capsys.readouterr().out


def test_load_guard_loads_the_hook_the_plugin_ships() -> None:
    module = guard.load_guard()
    assert Path(module.__file__) == HOOK
    assert guard.load_guard() is module
    assert callable(module.check_file)
    assert callable(module.explain)


# --- explain ---


def test_explain_shows_the_target_and_the_live_approval(run: Run, store: Path) -> None:
    expires = approve(store)
    code, out, err = run("explain", PROD_COMMAND)
    assert (code, err) == (0, "")
    assert "orders-db" in out
    assert "production" in out
    assert PROD_PATTERN in out
    assert "a1" in out
    assert expires in out
    assert "needs approval" not in out
    assert verdict_of(out) == "allowed"


def test_explain_shows_a_target_that_needs_approval(run: Run, store: Path) -> None:
    code, out, _ = run("explain", PROD_COMMAND)
    assert code == 0
    assert "orders-db" in out
    assert "needs approval" in out
    assert verdict_of(out) == "refused for every session"


def test_explain_shows_the_first_pattern_that_hit(run: Run, store: Path) -> None:
    _, out, _ = run("explain", "pg_dump -h orders-db.example.internal x")
    assert "pg_dump\\s+.*orders-db" in out
    assert PROD_PATTERN not in out


def test_explain_shows_every_target_of_a_chain(run: Run, store: Path) -> None:
    approve(store, commands=[f"{PROD_COMMAND} && mail-cli send x"])
    _, out, _ = run("explain", f"{PROD_COMMAND} && mail-cli send x")
    assert "orders-db" in out
    assert "mail-send" in out
    assert "gated" in out
    assert out.count("needs approval") == 1
    assert verdict_of(out) == "refused for every session"


def test_explain_says_allowed_for_an_ordinary_command(run: Run, store: Path) -> None:
    code, out, _ = run("explain", "git status")
    assert (code, verdict_of(out)) == (0, "allowed")
    assert "needs approval" not in out


def test_explain_shows_the_restricted_rule_a_pm_would_hit(run: Run, store: Path) -> None:
    code, out, _ = run("explain", "claude -p hello")
    assert code == 0
    assert "a PM: coven prod-guard:" in out
    assert "claude" in out
    assert "every session:" not in out
    assert verdict_of(out) == "refused for a PM"


def test_explain_has_one_verdict_line_and_it_is_the_last(run: Run, store: Path) -> None:
    for command in ("git status", PROD_COMMAND, "claude -p hello", f"{PROD_COMMAND} && claude x"):
        _, out, _ = run("explain", command)
        lines = out.splitlines()
        assert sum(line in VERDICTS for line in lines) == 1
        assert lines[-1] in VERDICTS


def test_explain_joins_a_command_the_shell_split(run: Run, store: Path) -> None:
    approve(store)
    split = PROD_COMMAND.replace("'select 1'", "select 1")
    approve(store, name="a2", commands=[split])
    _, out, _ = run("explain", *split.split(" "))
    assert verdict_of(out) == "allowed"
    assert "a2" in out


def test_explain_keeps_the_flags_of_the_command_and_drops_a_leading_double_dash(
    run: Run, store: Path
) -> None:
    approve(store, commands=["psql -h orders-db.example.internal"])
    _, out, _ = run("explain", "--", "psql", "-h", "orders-db.example.internal")
    assert verdict_of(out) == "allowed"
    _, out, _ = run("explain", "psql", "-h", "orders-db.example.internal", "-c", "x")
    assert "orders-db (production)" in out
    assert verdict_of(out) == "refused for every session"


def test_explain_help_says_a_split_command_is_joined_with_single_spaces(
    run: Run, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["guard", "explain", "--help"]) == 0
    text = " ".join(capsys.readouterr().out.split())
    assert "single spaces" in text


def test_explain_reads_a_multi_line_command_from_a_file(
    run: Run, store: Path, tmp_path: Path
) -> None:
    script = f"cd /tmp\n{PROD_COMMAND}\n"
    approve(store, commands=[script.strip()])
    file = tmp_path / "step.sh"
    file.write_text(script)
    code, out, _ = run("explain", "--file", str(file))
    assert code == 0
    assert "a1" in out
    assert verdict_of(out) == "allowed"
    file.write_text(script + "echo done\n")
    assert verdict_of(run("explain", "--file", str(file))[1]) == "refused for every session"


def test_explain_judges_a_tool_by_name(run: Run, store: Path) -> None:
    code, out, _ = run("explain", "--tool", "mcp__mail__send")
    assert code == 0
    assert "mail-send" in out
    assert "mcp__mail__send" in out
    assert "needs approval" in out
    assert verdict_of(out) == "refused for every session"
    assert verdict_of(run("explain", "--tool", "mcp__mail__list")[1]) == "allowed"
    approve(store, name="t1", system="mail-send", commands=[], tools=["mcp__mail__send"])
    out = run("explain", "--tool", "mcp__mail__send")[1]
    assert "t1" in out
    assert verdict_of(out) == "allowed"


def test_explain_ignores_the_callers_role(
    run: Run, store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = run("explain", "claude -p hello")
    monkeypatch.setenv(ROLE, "pm")
    assert run("explain", "claude -p hello") == before
    monkeypatch.setenv(ROLE, "")
    assert run("explain", "claude -p hello") == before


@pytest.mark.parametrize(
    "args",
    [
        (),
        ("--tool", "Bash"),
        ("--tool", "mcp__x__y", "ls"),
        ("--file", "/nowhere/step.sh"),
        ("--file", "{file}", "ls"),
        ("--tool", ""),
        ("   ",),
    ],
    ids=[
        "nothing",
        "bash-tool",
        "tool-and-command",
        "missing-file",
        "file-and-command",
        "empty-tool",
        "blank",
    ],
)
def test_explain_refuses_bad_input(
    run: Run, store: Path, tmp_path: Path, args: tuple[str, ...]
) -> None:
    file = tmp_path / "x.sh"
    file.write_text("ls\n")
    code, out, err = run("explain", *(a.format(file=file) for a in args))
    assert (code, out) == (1, "")
    assert err.startswith("coven: ")


def test_explain_says_the_guard_is_off(run: Run) -> None:
    code, out, err = run("explain", PROD_COMMAND)
    assert (code, err) == (0, "")
    assert out.startswith("guard off: ")
    assert "coven-memory" in out
    assert not any(line in VERDICTS for line in out.splitlines())


def test_explain_says_the_guard_is_off_without_a_guard_file(run: Run, tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    configure(plain)
    code, out, _ = run("explain", PROD_COMMAND)
    assert (code, out.startswith("guard off: ")) == (0, True)
    assert "guard.json" in out


def test_explain_says_why_the_config_is_unreadable(run: Run) -> None:
    write_config("{nope")
    code, out, err = run("explain", PROD_COMMAND)
    assert code == 1
    assert "config.json" in err
    assert verdict_of(out) == "refused for a PM"


def test_explain_says_why_the_store_is_unreadable(run: Run, tmp_path: Path) -> None:
    configure(make_store(home() / "coven-memory", committed=False))
    code, out, err = run("explain", PROD_COMMAND)
    assert code == 1
    assert "no commits" in err
    assert err.startswith("coven: ")
    assert verdict_of(out) == "refused for every session"


def test_explain_shows_the_older_guard_a_broken_head_falls_back_to(run: Run, store: Path) -> None:
    (store / "systems" / "guard.json").write_text("{nope")
    commit_all(store)
    _, out, _ = run("explain", PROD_COMMAND)
    assert "\nnote: the committed systems/guard.json is invalid, so its last valid version" in out
    assert verdict_of(out) == "refused for every session"


def test_explain_with_no_valid_guard_refuses_an_edit_for_a_pm_only(
    run: Run, tmp_path: Path
) -> None:
    broken = make_store(home() / "coven-memory", guard_json={"targets": "nope"})
    configure(broken)
    assert (
        verdict_of(run("explain", "--tool", "Write", "--path", "/tmp/x")[1]) == "refused for a PM"
    )
    assert verdict_of(run("explain", "--tool", "Read")[1]) == "allowed"
    assert verdict_of(run("explain", "ls")[1]) == "refused for every session"


# --- read-only ---


def snapshot(root: Path) -> dict[str, tuple[int, int]]:
    return {
        str(path.relative_to(root)): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in sorted(root.rglob("*"))
    }


def test_explain_and_check_write_nothing(run: Run, store: Path, tmp_path: Path) -> None:
    approve(store)
    status = git(
        store, "status", "--porcelain"
    )  # git refreshes its index here, before the snapshot
    before = snapshot(store)
    file = tmp_path / "step.sh"
    file.write_text(PROD_COMMAND)
    for args in (
        ("explain", PROD_COMMAND),
        ("explain", "claude -p x"),
        ("explain", "--tool", "mcp__mail__send"),
        ("explain", "--file", str(file)),
        ("check", str(store / "systems" / "guard.json")),
    ):
        run(*args)
    assert snapshot(store) == before
    assert git(store, "status", "--porcelain") == status == ""
    assert not (Path(os.environ["XDG_CACHE_HOME"]) / "coven" / "logs").exists()
    assert not list(store.rglob("*.lock"))


# --- the hook and explain agree ---


def _valid(path: Path) -> Path:
    configure(make_store(path))
    return path


def _off(path: Path) -> Path:
    return path


def _bad_config(path: Path) -> Path:
    write_config("{nope")
    return path


def _no_valid_guard(path: Path) -> Path:
    configure(make_store(path, guard_json={"targets": "nope"}))
    return path


def _broken_head(path: Path) -> Path:
    made = make_store(path)
    (made / "systems" / "guard.json").write_text("{nope")
    commit_all(made)
    configure(made)
    return path


def _guard_absent(path: Path) -> Path:
    made = make_store(path)
    (made / "systems" / "guard.json").unlink()
    commit_all(made)
    configure(made, latch=True)
    return path


def _no_commits(path: Path) -> Path:
    configure(make_store(path, committed=False))
    return path


def _latch_without_folder(path: Path) -> Path:
    configure(path, latch=True)
    return path


# state -> (builder, the exit code explain gives)
STATES: dict[str, tuple[Callable[[Path], Path], int]] = {
    "valid": (_valid, 0),
    "off": (_off, 0),
    "bad-config": (_bad_config, 1),
    "no-valid-guard": (_no_valid_guard, 0),
    "broken-head": (_broken_head, 0),
    "guard-absent-with-latch": (_guard_absent, 0),
    "no-commits": (_no_commits, 1),
    "latch-without-folder": (_latch_without_folder, 1),
}
# name -> (explain arguments, the hook payload)
CASES: dict[str, tuple[tuple[str, ...], dict[str, object]]] = {
    "ls": (("ls",), {"tool_name": "Bash", "tool_input": {"command": "ls"}}),
    "production": (
        (PROD_COMMAND,),
        {"tool_name": "Bash", "tool_input": {"command": PROD_COMMAND}},
    ),
    "start-claude": (
        ("claude -p hi",),
        {"tool_name": "Bash", "tool_input": {"command": "claude -p hi"}},
    ),
    "mcp-send": (
        ("--tool", "mcp__mail__send"),
        {"tool_name": "mcp__mail__send", "tool_input": {}},
    ),
    "mcp-read": (("--tool", "mcp__x__read"), {"tool_name": "mcp__x__read", "tool_input": {}}),
    "read": (("--tool", "Read"), {"tool_name": "Read", "tool_input": {}}),
    "task": (("--tool", "Task"), {"tool_name": "Task", "tool_input": {}}),
}
# Edit calls with a path; `{home}` is the test's home. name -> (tool, the path)
PATH_CASES: dict[str, tuple[str, str]] = {
    "write-elsewhere": ("Write", "/tmp/x.md"),
    "write-store": ("Write", "{home}/coven-memory/systems/orders-db.md"),
    "edit-store": ("Edit", "{home}/coven-memory/approvals/a1.json"),
    "multiedit-store": ("MultiEdit", "{home}/coven-memory/approvals/a1.json"),
    "multiedit-elsewhere": ("MultiEdit", "/tmp/x.md"),
    "write-config": ("Write", "{home}/.config/coven/config.json"),
    "edit-settings": ("Edit", "{home}/.claude/settings.json"),
    "multiedit-settings": ("MultiEdit", "{home}/.claude/settings.json"),
    "notebook-settings": ("NotebookEdit", "{home}/.claude/settings.local.json"),
}
for _name, (_tool, _path) in PATH_CASES.items():
    _key = "notebook_path" if _tool == "NotebookEdit" else "file_path"
    CASES[_name] = (
        ("--tool", _tool, "--path", _path),
        {"tool_name": _tool, "tool_input": {_key: _path}},
    )


def hook_refuses(payload: dict[str, object], role: str | None) -> bool:
    env = {**os.environ}
    env.pop(ROLE, None)
    if role is not None:
        env[ROLE] = role
    done = subprocess.run(
        [sys.executable, "-I", str(HOOK)],
        input=json.dumps({**payload, "cwd": os.getcwd()}),
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert done.returncode in (0, 2), done.stderr
    return done.returncode == 2


@pytest.mark.parametrize("case", CASES)
@pytest.mark.parametrize("state", STATES)
def test_explain_agrees_with_the_hook(run: Run, state: str, case: str) -> None:
    build, exit_code = STATES[state]
    build(home() / "coven-memory")
    arguments, payload = CASES[case]
    arguments = tuple(a.replace("{home}", str(home())) for a in arguments)
    payload = json.loads(json.dumps(payload).replace("{home}", str(home())))
    code, out, _ = run("explain", *arguments)
    assert code == exit_code
    refused_for_user = hook_refuses(payload, None)
    refused_for_a_pm = hook_refuses(payload, "pm")
    expected = (
        "refused for every session"
        if refused_for_user
        else "refused for a PM"
        if refused_for_a_pm
        else "allowed"
    )
    if state == "off":
        assert (out.startswith("guard off: "), expected) == (True, "allowed")
    else:
        assert verdict_of(out) == expected


def test_explain_judges_an_edit_by_its_path(run: Run, store: Path) -> None:
    for tool, path, verdict in (
        ("Write", str(store / "systems" / "orders-db.md"), "refused for a PM"),
        ("Edit", str(home() / ".config" / "coven" / "config.json"), "refused for a PM"),
        ("Edit", str(home() / ".claude" / "settings.json"), "refused for a PM"),
        ("MultiEdit", str(home() / ".claude" / "settings.json"), "refused for a PM"),
        ("MultiEdit", str(store / "systems" / "orders-db.md"), "refused for a PM"),
        ("MultiEdit", "/tmp/notes.md", "allowed"),
        ("NotebookEdit", str(home() / ".claude" / "settings.local.json"), "refused for a PM"),
        ("Write", "/tmp/notes.md", "allowed"),
    ):
        code, out, _ = run("explain", "--tool", tool, "--path", path)
        assert (code, verdict_of(out)) == (0, verdict), (tool, path)


def test_explain_knows_every_edit_tool_the_hook_guards() -> None:
    hook = guard.load_guard()
    assert set(guard.PATH_KEYS) == set(hook.EDIT_TOOLS)
    assert set(guard.PATH_KEYS.values()) <= set(hook.PATH_KEYS)


@pytest.mark.parametrize(
    ("args", "words"),
    [
        (("--tool", "Write"), "--path"),
        (("--tool", "Edit"), "--path"),
        (("--tool", "MultiEdit"), "--path"),
        (("--tool", "NotebookEdit"), "--path"),
        (("--tool", "Read", "--path", "/tmp/x"), "--path"),
        (("--tool", "mcp__x__y", "--path", "/tmp/x"), "--path"),
        (("--path", "/tmp/x", "ls"), "--path"),
        (("--tool", "Write", "--path", ""), "--path"),
    ],
    ids=["write", "edit", "multiedit", "notebook", "read", "mcp", "no-tool", "empty-path"],
)
def test_explain_needs_a_path_for_an_edit_tool_and_only_for_one(
    run: Run, store: Path, args: tuple[str, ...], words: str
) -> None:
    code, out, err = run("explain", *args)
    assert (code, out) == (1, "")
    assert words in err


# --- --cwd ---


def test_explain_judges_a_command_from_the_cwd_it_is_given(
    run: Run, store: Path, tmp_path: Path
) -> None:
    elsewhere = tmp_path / "worktree"
    elsewhere.mkdir()
    command = "cd ~/.config && rm -rf coven"
    assert verdict_of(run("explain", "--cwd", str(elsewhere), command)[1]) == "refused for a PM"
    holder = home() / ".config"
    holder.mkdir(parents=True, exist_ok=True)
    assert verdict_of(run("explain", "--cwd", str(holder), "rm -rf coven")[1]) == "refused for a PM"
    assert verdict_of(run("explain", "--cwd", str(elsewhere), "rm -rf coven")[1]) == "allowed"


def test_explain_judges_a_relative_path_from_the_cwd_it_is_given(
    run: Run, store: Path, tmp_path: Path
) -> None:
    relative = ("--tool", "Write", "--path", "coven-memory/systems/orders-db.md")
    assert verdict_of(run("explain", "--cwd", str(home()), *relative)[1]) == "refused for a PM"
    assert verdict_of(run("explain", "--cwd", str(tmp_path), *relative)[1]) == "allowed"


def test_explain_defaults_the_cwd_to_its_own(
    run: Run, store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    relative = ("--tool", "Write", "--path", "coven-memory/systems/orders-db.md")
    monkeypatch.chdir(home())
    assert verdict_of(run("explain", *relative)[1]) == "refused for a PM"
    monkeypatch.chdir("/")
    assert verdict_of(run("explain", *relative)[1]) == "allowed"


def test_explain_refuses_a_cwd_that_is_not_a_folder(run: Run, store: Path, tmp_path: Path) -> None:
    code, out, err = run("explain", "--cwd", str(tmp_path / "nowhere"), "ls")
    assert (code, out) == (1, "")
    assert "--cwd" in err


def test_explain_help_says_relative_paths_are_judged_from_cwd(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["guard", "explain", "--help"]) == 0
    text = " ".join(capsys.readouterr().out.split())
    assert "relative paths are judged from --cwd" in text
    assert "--path" in text


# --- the deadline ---


def test_run_with_deadline_returns_what_the_function_returns() -> None:
    assert guard.run_with_deadline(lambda: 42, 5.0) == 42


def test_run_with_deadline_stops_a_slow_function() -> None:
    with pytest.raises(CovenError, match="guard check timed out"):
        guard.run_with_deadline(lambda: time.sleep(30), 0.05)


def test_run_with_deadline_leaves_no_timer_or_handler_behind() -> None:
    before = signal.getsignal(signal.SIGALRM)
    guard.run_with_deadline(lambda: None, 5.0)
    assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)
    assert signal.getsignal(signal.SIGALRM) == before
    with pytest.raises(CovenError):
        guard.run_with_deadline(lambda: time.sleep(30), 0.05)
    assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)
    assert signal.getsignal(signal.SIGALRM) == before


def test_run_with_deadline_defaults_to_the_hooks_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(guard.load_guard(), "DEADLINE", 0.05)
    with pytest.raises(CovenError, match="timed out"):
        guard.run_with_deadline(lambda: time.sleep(30))


def test_a_slow_explain_times_out_with_exit_1(
    run: Run, store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hook = guard.load_guard()
    monkeypatch.setattr(hook, "DEADLINE", 0.05)
    monkeypatch.setattr(hook, "explain", lambda *_a: time.sleep(30))
    code, out, err = run("explain", "ls")
    assert (code, out) == (1, "")
    assert "guard check timed out" in err


def test_a_slow_check_times_out_with_exit_1(
    run: Run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hook = guard.load_guard()
    monkeypatch.setattr(hook, "DEADLINE", 0.05)
    monkeypatch.setattr(hook, "check_file", lambda *_a: time.sleep(30))
    code, out, err = run("check", str(tmp_path / "guard.json"))
    assert (code, out) == (1, "")
    assert "guard check timed out" in err


# --- loading the hook leaves the process as it was ---


def test_loading_the_hook_restores_the_bytecode_flag_and_sys_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "dont_write_bytecode", False)
    monkeypatch.setattr(sys, "path", list(sys.path))
    before = list(sys.path)
    guard.load_guard.cache_clear()
    try:
        assert guard.load_guard().explain
        assert sys.dont_write_bytecode is False
        assert sys.path == before
    finally:
        guard.load_guard.cache_clear()
