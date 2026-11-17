"""The prod guard: a PreToolUse hook that refuses calls reaching production or gated systems unless
the user approved the exact command. Every system, host and command here is invented, and no test
runs a remote CLI: a command is only a string the guard reads."""

import importlib.util
import io
import json
import os
import shutil
import signal
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from coven import paths

HOOKS = Path(__file__).resolve().parents[2] / "hooks"
SCRIPT = HOOKS / "prod_guard.py"
ROLE = "COVEN_SESSION_ROLE"
PREFIX = "coven prod-guard:"
PROD_COMMAND = "psql -h orders-db.example.internal -c 'x'"
PROD_MESSAGE = (
    "coven prod-guard: 'orders-db' is production; this exact step needs the user's approval"
    " (send a PROD-STEP; the GM approves it with coven approve)"
)
MAIL_MESSAGE = (
    "coven prod-guard: 'mail-send' is gated; this exact step needs the user's approval"
    " (send a PROD-STEP; the GM approves it with coven approve)"
)
GUARD = {
    "targets": [
        {
            "system": "orders-db",
            "kind": "production",
            "match": ["psql\\s+.*-h\\s*orders-db"],
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
BRANCH_GUARD = {
    "targets": [
        *GUARD["targets"],
        {"system": "repo-main", "kind": "gated", "match": ["git\\s+push\\b.*\\bmain\\b"]},
    ]
}
NOW = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)

type Run = Callable[..., subprocess.CompletedProcess[str]]
type Payload = dict[str, object]


def load() -> ModuleType:
    """Import the hook by path, as the hooks load each other (they are not a package)."""
    spec = importlib.util.spec_from_file_location("coven_prod_guard", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    old, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = old
    return module


pg = load()
# The shipped limits (5 s per git call, 20 s in all) would refuse a call on a loaded machine and
# fail a test of something else: the in-process tests wait this long. A test of a limit sets its own.
PATIENT_SECONDS = 30.0


@pytest.fixture(autouse=True)
def patient_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pg, "GIT_TIMEOUT", PATIENT_SECONDS)
    monkeypatch.setattr(pg, "DEADLINE", PATIENT_SECONDS)


def git(store: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(store), *args], capture_output=True, text=True, check=True)


def commit(store: Path) -> None:
    """Commit the whole working tree: the guard reads only what the store's HEAD holds."""
    git(store, "add", "-A")
    git(store, "commit", "-q", "--allow-empty", "-m", "test")


def make_store(path: Path, *, committed: bool = True, guard: object = GUARD) -> Path:
    (path / "systems").mkdir(parents=True)
    (path / "approvals").mkdir()
    (path / "systems/guard.json").write_text(json.dumps(guard))
    git(path, "init", "-q")
    if committed:
        commit(path)
    return path


def config_file() -> Path:
    return Path(os.environ["HOME"]) / ".config" / "coven" / "config.json"


def write_config(text: str) -> None:
    config_file().parent.mkdir(parents=True, exist_ok=True)
    config_file().write_text(text)


def configure(store: Path, *, latch: bool = False) -> None:
    """Point coven's config at `store`, with the `guard` latch when asked."""
    fields: dict[str, str] = {"memory_path": str(store)}
    if latch:
        fields["guard"] = "required"
    write_config(json.dumps(fields))


@pytest.fixture
def store(tmp_path: Path) -> Path:
    """A committed store with GUARD; the guard is on because the working tree has a guard.json."""
    path = make_store(tmp_path / "coven-memory")
    configure(path)
    return path


@pytest.fixture
def fresh(tmp_path: Path) -> Path:
    """A store with no commit yet: a guard.json the test commits is the only version."""
    path = make_store(tmp_path / "coven-memory", committed=False)
    configure(path)
    return path


@pytest.fixture
def run(run_hook: Run) -> Run:
    def runner(
        payload: object,
        role: str | None = None,
        args: tuple[str, ...] = (),
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        merged = {**(env or {})}
        if role is not None:
            merged[ROLE] = role
        return run_hook(SCRIPT, payload, merged, args=args)

    return runner


def bash(command: str) -> Payload:
    return {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": "/tmp"}


def tool(name: str, **tool_input: object) -> Payload:
    return {"tool_name": name, "tool_input": tool_input}


def approval(name: str = "a", **fields: object) -> str:
    record: dict[str, object] = {
        "id": name,
        "system": "orders-db",
        "commands": [PROD_COMMAND],
        "tools": [],
        "expires": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
    }
    record.update(fields)
    return json.dumps(record)


def approve(store: Path, name: str = "a", **fields: object) -> None:
    (store / "approvals" / f"{name}.json").write_text(approval(name, **fields))
    commit(store)


def in_hours(hours: float) -> str:
    return (datetime.now(UTC) + timedelta(hours=hours)).isoformat()


def decide(
    payload: Payload, store: Path, role: str | None = None, now: datetime = NOW
) -> str | None:
    result: str | None = pg.decide(payload, store, role, now)
    return result


# --- the off path: no git, no subprocess ---


@pytest.fixture
def no_git(tmp_path: Path) -> dict[str, str]:
    """A PATH with no git on it: whatever runs git on it fails, so a pass shows git never ran."""
    empty = tmp_path / "no-bin"
    empty.mkdir()
    return {"PATH": str(empty)}


def test_no_config_file_is_off(run: Run, no_git: dict[str, str]) -> None:
    proc = run(bash(PROD_COMMAND), env=no_git)
    assert (proc.returncode, proc.stderr) == (0, "")


def test_no_latch_and_no_guard_file_is_off(
    run: Run, tmp_path: Path, no_git: dict[str, str]
) -> None:
    plain = tmp_path / "coven-memory"
    (plain / "systems").mkdir(parents=True)
    configure(plain)
    for role in (None, "pm"):
        proc = run(bash(PROD_COMMAND), role=role, env=no_git)
        assert (proc.returncode, proc.stderr) == (0, "")


def test_a_store_in_the_default_place_without_a_config_file_is_off(
    run: Run, no_git: dict[str, str]
) -> None:
    make_store(Path(os.environ["HOME"]) / "coven-memory")
    proc = run(bash(PROD_COMMAND), role="pm", env=no_git)
    assert (proc.returncode, proc.stderr) == (0, "")


def test_the_default_store_is_coven_memory_in_home(run: Run, no_git: dict[str, str]) -> None:
    write_config("{}")
    assert run(bash(PROD_COMMAND), env=no_git).returncode == 0


def test_a_missing_store_folder_is_off_even_with_the_latch(
    run: Run, tmp_path: Path, no_git: dict[str, str]
) -> None:
    configure(tmp_path / "nowhere", latch=True)
    assert run(bash(PROD_COMMAND), role="pm", env=no_git).returncode == 0


def test_the_off_path_does_not_read_stdin(run: Run) -> None:
    assert run("not json at all").returncode == 0


def test_a_working_tree_guard_file_turns_the_guard_on(run: Run, store: Path) -> None:
    assert run(bash(PROD_COMMAND)).returncode == 2


def test_the_latch_turns_the_guard_on_without_a_guard_file(run: Run, tmp_path: Path) -> None:
    path = make_store(tmp_path / "coven-memory")
    (path / "systems/guard.json").unlink()
    commit(path)
    configure(path, latch=True)
    proc = run(bash("ls"), role="pm")
    assert proc.returncode == 2
    assert "systems/guard.json is missing" in proc.stderr


def test_the_latch_with_a_committed_guard_enforces_it(run: Run, tmp_path: Path) -> None:
    path = make_store(tmp_path / "coven-memory")
    configure(path, latch=True)
    (path / "systems/guard.json").unlink()  # deleted, not committed: HEAD still holds it
    proc = run(bash(PROD_COMMAND))
    assert proc.returncode == 2
    assert PROD_MESSAGE in proc.stderr


def test_the_config_and_memory_context_name_the_same_store(tmp_path: Path) -> None:
    sibling = load_module("memory_context")
    for text in ("{}", json.dumps({"memory_path": str(tmp_path / "elsewhere")})):
        write_config(text)
        config = pg.read_config()
        assert config is not None
        assert config.store == sibling.store_path()


def test_the_config_file_is_the_one_coven_reads() -> None:
    assert pg.config_file() == paths.config_dir() / "config.json"


def load_module(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"coven_{name}_under_test", HOOKS / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    old, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = old
    return module


# --- a config that cannot be read ---

BAD_CONFIGS = {
    "bad-json": "{nope",
    "not-an-object": "[]",
    "memory-path-not-str": json.dumps({"memory_path": 3}),
    "guard-unknown-value": json.dumps({"guard": "yes"}),
    "guard-not-str": json.dumps({"guard": True}),
}


@pytest.mark.parametrize("text", BAD_CONFIGS.values(), ids=BAD_CONFIGS.keys())
@pytest.mark.parametrize("role", ["pm", "", "other"])
@pytest.mark.parametrize("payload", [bash("ls"), tool("mcp__x__y")], ids=["bash", "mcp"])
def test_a_broken_config_refuses_a_restricted_session(
    run: Run, text: str, role: str, payload: Payload
) -> None:
    write_config(text)
    proc = run(payload, role=role)
    assert proc.returncode == 2
    assert proc.stderr.startswith(f"{PREFIX} cannot read the coven config")
    assert "coven doctor" in proc.stderr


@pytest.mark.parametrize("text", BAD_CONFIGS.values(), ids=BAD_CONFIGS.keys())
def test_a_broken_config_leaves_a_restricted_session_its_reads(run: Run, text: str) -> None:
    write_config(text)
    read = tool("Read", file_path="/tmp/x")
    assert run(read, role="pm").returncode == 0


@pytest.mark.parametrize("text", BAD_CONFIGS.values(), ids=BAD_CONFIGS.keys())
def test_a_broken_config_leaves_an_unrestricted_session_alone(run: Run, text: str) -> None:
    write_config(text)
    assert run(bash(PROD_COMMAND)).returncode == 0


def test_an_unreadable_config_is_refused_too(run: Run) -> None:
    config_file().parent.mkdir(parents=True)
    config_file().mkdir()  # a folder where the file should be
    proc = run(bash("ls"), role="pm")
    assert proc.returncode == 2
    assert "cannot read the coven config" in proc.stderr


# --- production targets ---


@pytest.mark.parametrize(
    "command",
    [PROD_COMMAND, "psql   -h   orders-db -c 'x'", "psql -U me -horders-db"],
    ids=["plain", "extra-spaces", "glued-flag"],
)
@pytest.mark.parametrize("role", [None, "pm"], ids=["unrestricted", "restricted"])
def test_blocks_a_production_command(run: Run, store: Path, command: str, role: str | None) -> None:
    proc = run(bash(command), role=role)
    assert proc.returncode == 2
    assert PROD_MESSAGE in proc.stderr


@pytest.mark.parametrize(
    "command",
    ["ls && psql -h orders-db -c 'x'", "ls; psql -h orders-db", "echo q | psql -h orders-db"],
    ids=["and", "semicolon", "pipe"],
)
def test_blocks_inside_a_chain(run: Run, store: Path, command: str) -> None:
    proc = run(bash(command))
    assert proc.returncode == 2
    assert PROD_MESSAGE in proc.stderr


@pytest.mark.parametrize("command", ["ls -la", "psql -h dev-db -c 'x'", "git status"])
def test_allows_an_unrelated_command(run: Run, store: Path, command: str) -> None:
    proc = run(bash(command))
    assert proc.returncode == 0, proc.stderr


def test_allows_an_unrelated_tool(run: Run, store: Path) -> None:
    assert run(tool("Read", file_path="/tmp/x")).returncode == 0


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (tool("mcp__orders__write", sql="x"), PROD_MESSAGE),
        (tool("mcp__mail__send", to="x"), MAIL_MESSAGE),
        (bash("mail-cli send --to x"), MAIL_MESSAGE),
    ],
    ids=["production-tool", "gated-tool", "gated-command"],
)
@pytest.mark.parametrize("role", [None, "pm"], ids=["unrestricted", "restricted"])
def test_blocks_a_target_tool_or_command(
    run: Run, store: Path, payload: Payload, expected: str, role: str | None
) -> None:
    proc = run(payload, role=role)
    assert proc.returncode == 2
    assert expected in proc.stderr


@pytest.mark.parametrize(
    "payload",
    [
        tool("WebFetch", url="https://orders-db.example.internal"),
        tool("Agent", prompt="psql -h orders-db"),
        tool("mcp__x__y", anything=1),
        tool("Task", prompt="x"),
        tool("SomeFutureTool"),
    ],
    ids=["webfetch", "agent", "mcp", "task", "future"],
)
@pytest.mark.parametrize("role", [None, "pm"], ids=["unrestricted", "restricted"])
def test_unknown_tool_names_are_handled(
    run: Run, store: Path, payload: Payload, role: str | None
) -> None:
    proc = run(payload, role=role)
    assert proc.returncode == 0, proc.stderr
    assert "internal error" not in proc.stderr


def test_a_subagent_payload_is_judged_like_its_parent(run: Run, store: Path) -> None:
    extra = {"agent_id": "agent-1", "agent_type": "general-purpose"}
    assert run({**bash(PROD_COMMAND), **extra}).returncode == 2
    assert run({**bash("ls"), **extra}).returncode == 0
    assert run({**tool("mcp__mail__send"), **extra}, role="pm").returncode == 2


def test_a_command_matching_two_systems_needs_both_approvals(run: Run, store: Path) -> None:
    chain = PROD_COMMAND + " && mail-cli send x"
    approve(store, commands=[chain])
    proc = run(bash(chain))
    assert proc.returncode == 2
    assert "'mail-send' is gated" in proc.stderr
    assert "'orders-db'" not in proc.stderr
    approve(store, name="b", system="mail-send", commands=[chain])
    assert run(bash(chain)).returncode == 0


def test_an_unapproved_chain_names_every_system(run: Run, store: Path) -> None:
    proc = run(bash(PROD_COMMAND + " && mail-cli send x"))
    assert "'orders-db' is production and 'mail-send' is gated" in proc.stderr


# --- approvals ---


@pytest.mark.parametrize(
    "command", [PROD_COMMAND + " ", "  " + PROD_COMMAND + "\n"], ids=["trailing", "both-sides"]
)
def test_an_exact_approval_allows_after_stripping_whitespace(
    run: Run, store: Path, command: str
) -> None:
    approve(store)
    proc = run(bash(command))
    assert proc.returncode == 0, proc.stderr


def test_an_approval_is_exact(run: Run, store: Path) -> None:
    approve(store)
    proc = run(bash("psql -h orders-db -c 'drop table x'"))
    assert proc.returncode == 2
    assert PROD_MESSAGE in proc.stderr


def test_a_tool_approval_allows_that_tool(run: Run, store: Path) -> None:
    approve(store, commands=[], tools=["mcp__orders__write"])
    assert run(tool("mcp__orders__write")).returncode == 0


def test_a_command_approval_does_not_approve_the_tool(run: Run, store: Path) -> None:
    approve(store)
    assert run(tool("mcp__orders__write")).returncode == 2


def test_an_approval_covers_one_system_only(run: Run, store: Path) -> None:
    approve(store, system="mail-send")
    proc = run(bash(PROD_COMMAND))
    assert proc.returncode == 2
    assert PROD_MESSAGE in proc.stderr


BAD_APPROVALS = {
    "expired": {"expires": in_hours(-1 / 60)},
    "far-future": {"expires": in_hours(13)},
    "wrong-system": {"system": "other-db"},
    "no-offset": {"expires": (datetime.now() + timedelta(hours=1)).isoformat()},
    "expires-not-iso": {"expires": "tomorrow"},
    "expires-not-str": {"expires": 1893456000},
    "commands-is-a-string": {"commands": PROD_COMMAND},
    "commands-not-strings": {"commands": [PROD_COMMAND, 3]},
    "tools-not-list": {"tools": "mcp__orders__write"},
    "id-not-str": {"id": 7},
    "extra-key": {"note": "x"},
}
RAW_APPROVALS = {
    "not-json": "{nope",
    "array": json.dumps([PROD_COMMAND]),
    "missing-key": json.dumps({"id": "a", "system": "orders-db", "commands": [PROD_COMMAND]}),
    "deeply-nested": "[" * 100000,
}


@pytest.mark.parametrize(
    ("fields", "raw"),
    [(f, None) for f in BAD_APPROVALS.values()] + [(None, r) for r in RAW_APPROVALS.values()],
    ids=[*BAD_APPROVALS, *RAW_APPROVALS],
)
def test_an_expired_far_future_or_malformed_approval_does_not_allow(
    run: Run, store: Path, fields: dict[str, object] | None, raw: str | None
) -> None:
    if raw is None:
        approve(store, **(fields or {}))
    else:
        (store / "approvals/a.json").write_text(raw)
        commit(store)
    proc = run(bash(PROD_COMMAND))
    assert proc.returncode == 2
    assert PROD_MESSAGE in proc.stderr


def test_a_non_utf8_approval_does_not_allow(run: Run, store: Path) -> None:
    (store / "approvals/a.json").write_bytes(b"\xff\xfe")
    commit(store)
    assert run(bash(PROD_COMMAND)).returncode == 2


def test_a_malformed_approval_does_not_hide_a_valid_one(run: Run, store: Path) -> None:
    (store / "approvals/0-bad.json").write_text("{nope")
    (store / "approvals/1-dir.json").mkdir()
    (store / "approvals/1-dir.json/x.json").write_text(approval())
    approve(store, name="2-good")
    assert run(bash(PROD_COMMAND)).returncode == 0


def test_an_approval_outside_json_files_is_ignored(run: Run, store: Path) -> None:
    approve(store)
    (store / "approvals/a.json").rename(store / "approvals/a.txt")
    commit(store)
    assert run(bash(PROD_COMMAND)).returncode == 2


@pytest.mark.parametrize(
    ("expires", "expected"),
    [
        (NOW, False),
        (NOW + timedelta(seconds=1), True),
        (NOW + timedelta(hours=12), True),
        (NOW + timedelta(hours=12, seconds=1), False),
        (datetime(2026, 10, 22, 16, 0, tzinfo=timezone(timedelta(hours=2))), True),
    ],
    ids=["at-now", "just-after", "at-cap", "past-cap", "other-offset"],
)
def test_the_approval_window(store: Path, expires: datetime, expected: bool) -> None:
    approve(store, expires=expires.isoformat())
    assert (decide(bash(PROD_COMMAND), store) is None) is expected


def test_no_approvals_folder_approves_nothing(store: Path) -> None:
    shutil.rmtree(store / "approvals")
    assert decide(bash(PROD_COMMAND), store) == PROD_MESSAGE


def test_approvals_reads_every_record_with_its_id(store: Path) -> None:
    approve(store, name="one")
    approve(store, name="two", system="mail-send", commands=[], tools=["mcp__mail__send"])
    found = {a.id: (a.system, a.commands, a.tools) for a in pg.approvals(store)}
    assert found == {
        "one": ("orders-db", [PROD_COMMAND], []),
        "two": ("mail-send", [], ["mcp__mail__send"]),
    }


# --- guard.json ---


def test_a_committed_store_without_a_guard_file_allows_everything_but_restricted_reach(
    run: Run, tmp_path: Path
) -> None:
    path = make_store(tmp_path / "coven-memory")
    (path / "systems/guard.json").unlink()
    commit(path)
    configure(path, latch=True)
    assert run(bash(PROD_COMMAND)).returncode == 0
    assert run(tool("mcp__x__y")).returncode == 0
    assert run(tool("Read", file_path="/x"), role="pm").returncode == 0
    for payload in (bash("ls"), tool("mcp__x__y")):
        proc = run(payload, role="pm")
        assert proc.returncode == 2
        assert f"{PREFIX} systems/guard.json is missing" in proc.stderr


def test_a_working_tree_guard_that_is_not_committed_counts_as_absent_at_head(
    run: Run, tmp_path: Path
) -> None:
    path = make_store(tmp_path / "coven-memory", committed=False)
    (path / "systems/guard.json").unlink()
    commit(path)  # a commit, but without guard.json
    configure(path)
    (path / "systems/guard.json").write_text(json.dumps(GUARD))  # written, never committed
    assert run(bash(PROD_COMMAND)).returncode == 0
    assert run(bash("ls"), role="pm").returncode == 2


def test_load_targets_defaults_match_and_tools(store: Path) -> None:
    (store / "systems/guard.json").write_text(
        json.dumps({"targets": [{"system": "s", "kind": "gated"}]})
    )
    commit(store)
    enforced = pg.load_targets(store)
    assert enforced.targets == [{"system": "s", "kind": "gated", "match": [], "tools": []}]
    assert enforced.invalid_head is None


def test_load_targets_gives_none_without_a_committed_guard_file(store: Path) -> None:
    (store / "systems/guard.json").unlink()
    commit(store)
    assert pg.load_targets(store).targets is None


INVALID_GUARDS = {
    "bad-json": "{nope",
    "bad-regex": json.dumps({"targets": [{"system": "s", "kind": "production", "match": ["("]}]}),
    "wrong-kind": json.dumps({"targets": [{"system": "s", "kind": "prod"}]}),
    "kind-not-str": json.dumps({"targets": [{"system": "s", "kind": ["production"]}]}),
    "top-level-array": json.dumps([]),
    "no-targets": json.dumps({}),
    "extra-top-key": json.dumps({"targets": [], "x": 1}),
    "targets-not-list": json.dumps({"targets": {}}),
    "target-not-object": json.dumps({"targets": ["s"]}),
    "no-system": json.dumps({"targets": [{"kind": "gated"}]}),
    "empty-system": json.dumps({"targets": [{"system": "", "kind": "gated"}]}),
    "match-is-string": json.dumps({"targets": [{"system": "s", "kind": "gated", "match": "x"}]}),
    "tools-not-strings": json.dumps({"targets": [{"system": "s", "kind": "gated", "tools": [1]}]}),
    "typo-key": json.dumps({"targets": [{"system": "s", "kind": "gated", "matches": ["x"]}]}),
    "deeply-nested": "[" * 100000,
}
NO_VALID_GUARD = "until the user fixes or reverts it in their own terminal (git -C "


@pytest.mark.parametrize("text", INVALID_GUARDS.values(), ids=INVALID_GUARDS.keys())
def test_an_invalid_guard_with_no_earlier_version_blocks_bash(
    run: Run, fresh: Path, text: str
) -> None:
    guard = fresh / "systems/guard.json"
    guard.write_text(text)
    commit(fresh)
    proc = run(bash("ls"))
    assert proc.returncode == 2
    assert proc.stderr.startswith(f"{PREFIX} {guard}: ")
    assert NO_VALID_GUARD in proc.stderr
    with pytest.raises(pg.GuardError, match="guard.json"):
        pg.load_targets(fresh)


def test_an_unreadable_guard_blocks_bash(run: Run, fresh: Path) -> None:
    guard = fresh / "systems/guard.json"
    guard.unlink()
    guard.mkdir()
    (guard / "x").write_text("{}")
    commit(fresh)
    proc = run(bash("ls"))
    assert proc.returncode == 2
    assert "cannot read" in proc.stderr


def invalid_guard(store: Path) -> Path:
    (store / "systems/guard.json").write_text("{nope")
    commit(store)
    return store


EDIT_GUARD = tool("Edit", file_path="/x/systems/guard.json", old_string="a", new_string="b")


@pytest.mark.parametrize(
    ("payload", "role"),
    [
        (bash("ls"), None),
        (tool("mcp__mail__send"), None),
        (tool("mcp__x__read"), None),
        (tool("Task", prompt="x"), None),
        (tool("Write", file_path="/tmp/x", content=""), "pm"),
        (EDIT_GUARD, "pm"),
        (tool("Write", file_path="/tmp/x", content=""), ""),
    ],
    ids=["bash", "mcp-send", "mcp-read", "task", "pm-write", "pm-edit", "empty-role-write"],
)
def test_with_no_valid_guard_only_reads_work(
    run: Run, fresh: Path, payload: Payload, role: str | None
) -> None:
    invalid_guard(fresh)
    proc = run(payload, role=role)
    assert proc.returncode == 2
    assert NO_VALID_GUARD in proc.stderr


@pytest.mark.parametrize(
    ("payload", "role"),
    [
        (tool("Read", file_path="/x/systems/guard.json"), None),
        (tool("Grep", pattern="x", path="/tmp"), "pm"),
        (tool("Glob", pattern="*.json"), "pm"),
        (tool("Write", file_path="/tmp/x", content="{}"), None),
        (EDIT_GUARD, None),
    ],
    ids=["read", "pm-grep", "pm-glob", "unrestricted-write", "unrestricted-edit"],
)
def test_with_no_valid_guard_reads_and_unrestricted_repairs_pass(
    run: Run, fresh: Path, payload: Payload, role: str | None
) -> None:
    invalid_guard(fresh)
    proc = run(payload, role=role)
    assert proc.returncode == 0, proc.stderr


def test_a_non_utf8_guard_blocks_bash(run: Run, fresh: Path) -> None:
    (fresh / "systems/guard.json").write_bytes(b"\xff\xfe")
    commit(fresh)
    assert run(bash("ls")).returncode == 2


SCHEMA_TYPO = json.dumps({"targets": [], "tool": []})
FALLBACK_NOTE = (
    "; the committed systems/guard.json is invalid, so its last valid version is enforced"
)


def break_guard(store: Path, text: str = SCHEMA_TYPO) -> None:
    (store / "systems/guard.json").write_text(text)
    commit(store)


@pytest.mark.parametrize("role", [None, "pm"], ids=["unrestricted", "restricted"])
def test_an_invalid_head_enforces_the_last_valid_guard_and_says_so(
    run: Run, store: Path, role: str | None
) -> None:
    break_guard(store)
    proc = run(bash(PROD_COMMAND), role=role)
    assert proc.returncode == 2
    assert PROD_MESSAGE in proc.stderr
    assert FALLBACK_NOTE in proc.stderr
    assert proc.stderr.rstrip().endswith('must be an object {"targets": [...]})')


def test_an_unrestricted_session_can_commit_a_fix_while_head_is_invalid(
    run: Run, store: Path
) -> None:
    break_guard(store)
    fix = bash(f"git -C {store} commit -am fix")
    assert run(fix).returncode == 0


def test_the_fallback_still_honours_approvals(run: Run, store: Path) -> None:
    break_guard(store)
    approve(store)
    assert run(bash(PROD_COMMAND)).returncode == 0


def test_the_fallback_skips_invalid_and_deleted_versions(run: Run, store: Path) -> None:
    break_guard(store, "{nope")
    (store / "systems/guard.json").unlink()
    commit(store)
    break_guard(store)
    proc = run(bash("mail-cli send --to x"))
    assert proc.returncode == 2
    assert "'mail-send' is gated" in proc.stderr
    assert FALLBACK_NOTE in proc.stderr


def test_the_fallback_looks_back_a_bounded_number_of_commits(
    store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    break_guard(store, "{nope")
    break_guard(store)
    assert pg.GUARD_HISTORY == 50
    monkeypatch.setattr(pg, "GUARD_HISTORY", 2)
    message = decide(bash("ls"), store)
    assert message is not None
    assert NO_VALID_GUARD in message
    monkeypatch.setattr(pg, "GUARD_HISTORY", 3)
    assert decide(bash("ls"), store) is None


def test_load_targets_reports_the_invalid_head_with_the_fallback(store: Path) -> None:
    break_guard(store)
    enforced = pg.load_targets(store)
    assert enforced.targets == GUARD["targets"]
    assert "must be an object" in str(enforced.invalid_head)


# --- --check <guard.json> ---


def check(run: Run, path: Path) -> subprocess.CompletedProcess[str]:
    return run("", args=("--check", str(path)))


def test_check_accepts_a_valid_file(run: Run, tmp_path: Path) -> None:
    guard = tmp_path / "guard.json"
    guard.write_text(json.dumps(GUARD))
    proc = check(run, guard)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "ok: 2 targets\n"


@pytest.mark.parametrize(
    "text",
    [INVALID_GUARDS["bad-json"], INVALID_GUARDS["typo-key"], SCHEMA_TYPO],
    ids=["bad-json", "typo-key", "top-level-typo"],
)
def test_check_rejects_an_invalid_file(run: Run, tmp_path: Path, text: str) -> None:
    guard = tmp_path / "guard.json"
    guard.write_text(text)
    proc = check(run, guard)
    assert proc.returncode == 1
    assert proc.stderr.startswith(f"{PREFIX} {guard}: ")
    assert proc.stdout == ""


@pytest.mark.parametrize("kind", ["missing", "symlink", "non-utf8"])
def test_check_rejects_an_unreadable_file(run: Run, tmp_path: Path, kind: str) -> None:
    guard = tmp_path / "guard.json"
    if kind == "symlink":
        (tmp_path / "real.json").write_text(json.dumps(GUARD))
        guard.symlink_to(tmp_path / "real.json")
    elif kind == "non-utf8":
        guard.write_bytes(b"\xff\xfe")
    proc = check(run, guard)
    assert proc.returncode == 1
    assert proc.stderr.startswith(f"{PREFIX} {guard}: cannot read")


@pytest.mark.parametrize(
    "args", [("--check",), ("--check", "a", "b"), ("x",)], ids=["no-path", "two", "other"]
)
def test_a_usage_error_blocks(run: Run, args: tuple[str, ...]) -> None:
    proc = run("", args=args)
    assert proc.returncode == 2
    assert "usage:" in proc.stderr


def test_check_file_returns_the_ok_line(tmp_path: Path) -> None:
    guard = tmp_path / "guard.json"
    guard.write_text(json.dumps(GUARD))
    assert pg.check_file(guard) == "ok: 2 targets"
    guard.write_text("{nope")
    with pytest.raises(pg.GuardError, match="not valid JSON"):
        pg.check_file(guard)


def test_check_never_reads_stdin(tmp_path: Path) -> None:
    class NoStdin(io.StringIO):
        def read(self, *_args: object) -> str:
            raise AssertionError("--check read stdin")

    guard = tmp_path / "guard.json"
    guard.write_text(json.dumps(GUARD))
    assert pg.main(["--check", str(guard)], NoStdin(), {}) == 0


# --- only the committed store counts ---


def unreadable_store(store: Path) -> str:
    return f"{PREFIX} cannot read the committed memory store at {store} ("


@pytest.mark.parametrize("role", [None, "pm"], ids=["unrestricted", "restricted"])
def test_an_uncommitted_guard_rewrite_changes_nothing(
    run: Run, store: Path, role: str | None
) -> None:
    (store / "systems/guard.json").write_text(json.dumps({"targets": []}))
    proc = run(bash(PROD_COMMAND), role=role)
    assert proc.returncode == 2
    assert PROD_MESSAGE in proc.stderr


def test_a_staged_guard_rewrite_changes_nothing(run: Run, store: Path) -> None:
    (store / "systems/guard.json").write_text(json.dumps({"targets": []}))
    git(store, "add", "-A")
    assert run(bash(PROD_COMMAND)).returncode == 2


def test_an_uncommitted_guard_deletion_changes_nothing_with_the_latch(
    run: Run, store: Path
) -> None:
    configure(store, latch=True)
    (store / "systems/guard.json").unlink()
    assert run(bash(PROD_COMMAND)).returncode == 2
    assert run(bash("ls"), role="pm").returncode == 0


def test_an_uncommitted_guard_deletion_without_the_latch_turns_the_guard_off(
    run: Run, store: Path
) -> None:
    """The documented cost of the one-stat off path: doctor warns while the latch is missing."""
    (store / "systems/guard.json").unlink()
    assert run(bash(PROD_COMMAND)).returncode == 0


def test_an_uncommitted_invalid_guard_changes_nothing(run: Run, store: Path) -> None:
    (store / "systems/guard.json").write_text("{nope")
    assert run(bash("ls")).returncode == 0


def test_an_uncommitted_approval_approves_nothing(run: Run, store: Path) -> None:
    (store / "approvals/a.json").write_text(approval())
    git(store, "add", "-A")
    proc = run(bash(PROD_COMMAND))
    assert proc.returncode == 2
    assert PROD_MESSAGE in proc.stderr


def test_a_committed_approval_approves(run: Run, store: Path) -> None:
    approve(store)
    assert run(bash(PROD_COMMAND)).returncode == 0


def test_a_committed_symlink_guard_is_invalid(run: Run, fresh: Path) -> None:
    guard = fresh / "systems/guard.json"
    guard.rename(fresh / "real.json")
    guard.symlink_to(fresh / "real.json")
    commit(fresh)
    proc = run(bash("ls"))
    assert proc.returncode == 2
    assert proc.stderr.startswith(f"{PREFIX} {guard}: cannot read: not a regular file")
    assert NO_VALID_GUARD in proc.stderr


def test_a_committed_symlink_approval_approves_nothing(run: Run, store: Path) -> None:
    (store / "real.json").write_text(approval())
    (store / "approvals/a.json").symlink_to(store / "real.json")
    commit(store)
    assert run(bash(PROD_COMMAND)).returncode == 2


# --- git cannot read the store ---


@pytest.mark.parametrize("payload", [bash("ls"), tool("mcp__x__read")], ids=["bash", "mcp"])
@pytest.mark.parametrize("role", [None, "pm"], ids=["unrestricted", "restricted"])
def test_a_store_without_commits_refuses_outward_calls(
    run: Run, fresh: Path, payload: Payload, role: str | None
) -> None:
    proc = run(payload, role=role)
    assert proc.returncode == 2
    assert proc.stderr.startswith(unreadable_store(fresh))
    assert "no commits" in proc.stderr
    assert "coven doctor" in proc.stderr


@pytest.mark.parametrize("payload", [bash("ls"), tool("mcp__x__read")], ids=["bash", "mcp"])
@pytest.mark.parametrize("role", [None, "pm"], ids=["unrestricted", "restricted"])
def test_a_store_without_git_refuses_outward_calls(
    run: Run, tmp_path: Path, payload: Payload, role: str | None
) -> None:
    plain = tmp_path / "coven-memory"
    (plain / "systems").mkdir(parents=True)
    (plain / "systems/guard.json").write_text(json.dumps(GUARD))
    configure(plain)
    proc = run(payload, role=role)
    assert proc.returncode == 2
    assert proc.stderr.startswith(unreadable_store(plain))
    assert "not a git repository" in proc.stderr


def test_a_store_without_git_allows_other_tools(run: Run, tmp_path: Path) -> None:
    plain = tmp_path / "coven-memory"
    (plain / "systems").mkdir(parents=True)
    (plain / "systems/guard.json").write_text(json.dumps(GUARD))
    configure(plain)
    assert run(tool("Read", file_path="/tmp/x")).returncode == 0
    assert run(tool("Write", file_path="/tmp/x", content=""), role="pm").returncode == 0


def fake_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str) -> None:
    """Put a `git` first on PATH that runs `body` (sh)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "git"
    script.write_text(f"#!/bin/sh\n{body}\n")
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")


def test_a_git_timeout_refuses(
    store: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_git(tmp_path, monkeypatch, "exec sleep 5")  # past the 0.2 s; the timeout kills it
    monkeypatch.setattr(pg, "GIT_TIMEOUT", 0.2)
    message = decide(bash("ls"), store)
    assert message is not None
    assert message.startswith(unreadable_store(store) + "git timed out after 0.2 s)")


def test_garbled_git_output_refuses(
    store: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_git(tmp_path, monkeypatch, "echo garbage")
    message = decide(bash("ls"), store)
    assert message is not None
    assert message.startswith(unreadable_store(store))


def test_garbled_object_contents_refuse(
    store: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = shutil.which("git")
    fake_git(
        tmp_path,
        monkeypatch,
        f'case "$*" in *--batch-check*) ;; *--batch*) echo garbage; exit 0;; esac\nexec {real} "$@"',
    )
    message = decide(bash("ls"), store)
    assert message is not None
    assert message.startswith(unreadable_store(store) + "unexpected git cat-file output")


def test_git_failing_on_approvals_refuses_a_production_step_only(
    store: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = shutil.which("git")
    fake_git(
        tmp_path,
        monkeypatch,
        f'case "$*" in *approvals/*) echo "fatal: broken" >&2; exit 1;; esac\nexec {real} "$@"',
    )
    message = decide(bash(PROD_COMMAND), store)
    assert message == (
        unreadable_store(store)
        + "fatal: broken); Bash and MCP tools are blocked until git can read it: run coven doctor"
    )
    assert decide(bash("ls"), store) is None


def test_missing_git_refuses(store: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    message = decide(bash("ls"), store)
    assert message is not None
    assert message.startswith(unreadable_store(store) + "cannot run git:")


@pytest.mark.parametrize(
    "variable",
    [
        "GIT_DIR",
        "GIT_COMMON_DIR",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_NAMESPACE",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_CEILING_DIRECTORIES",
        "GIT_DISCOVERY_ACROSS_FILESYSTEM",
    ],
)
def test_git_variables_cannot_move_the_guard_to_another_repo(
    run: Run, store: Path, tmp_path: Path, variable: str
) -> None:
    other = make_store(tmp_path / "other", guard={"targets": []})
    values = {
        "GIT_DIR": str(other / ".git"),
        "GIT_COMMON_DIR": str(other / ".git"),
        "GIT_OBJECT_DIRECTORY": str(other / ".git/objects"),
        "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(other / ".git/objects"),
        "GIT_NAMESPACE": "elsewhere",
        "GIT_WORK_TREE": str(other),
        "GIT_INDEX_FILE": str(other / ".git/index"),
        "GIT_CEILING_DIRECTORIES": str(tmp_path),
        "GIT_DISCOVERY_ACROSS_FILESYSTEM": "0",
    }
    approve(store)
    proc = run(bash(PROD_COMMAND + " "), env={variable: values[variable]})
    assert proc.returncode == 0, proc.stderr
    proc = run(bash("psql -h orders-db -c 'other'"), env={variable: values[variable]})
    assert proc.returncode == 2
    assert PROD_MESSAGE in proc.stderr


# --- fail closed ---


@pytest.mark.parametrize(
    "payload",
    [
        "not json",
        "[]",
        {"tool_input": {"command": "ls"}},
        {"tool_name": 3, "tool_input": {}},
        {"tool_name": "Bash"},
        {"tool_name": "Bash", "tool_input": "ls"},
        {"tool_name": "Bash", "tool_input": {}},
        {"tool_name": "Bash", "tool_input": {"command": ["ls"]}},
    ],
    ids=[
        "not-json",
        "array",
        "no-tool-name",
        "tool-name-not-str",
        "no-tool-input",
        "tool-input-not-object",
        "bash-without-command",
        "bash-command-not-str",
    ],
)
def test_an_unparseable_payload_blocks(run: Run, store: Path, payload: object) -> None:
    proc = run(payload)
    assert proc.returncode == 2
    assert f"{PREFIX} could not parse hook input" in proc.stderr


def test_internal_error_blocks(
    store: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def boom(*_args: object) -> None:
        raise RuntimeError("bug")

    monkeypatch.setattr(pg, "explain", boom)
    code = pg.main([], io.StringIO(json.dumps(bash("ls"))), {})
    assert code == 2
    assert f"{PREFIX} internal error" in capsys.readouterr().err


TOO_SLOW = (
    f"{PREFIX} the check took too long (a guard.json pattern may backtrack, or the input is very"
    " large); blocking"
)


def test_a_backtracking_pattern_hits_the_deadline(
    store: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    backtracking = {"targets": [{"system": "s", "kind": "production", "match": ["(a+)+$"]}]}
    (store / "systems/guard.json").write_text(json.dumps(backtracking))
    commit(store)
    monkeypatch.setattr(pg, "DEADLINE", 0.3)
    # 64 letters make (a+)+$ backtrack for longer than any machine lives, so only the deadline
    # ends it. `touch` is not an inert command, so the pattern reads the whole text.
    payload = json.dumps(bash("touch " + "a" * 64 + "b"))
    assert pg.main([], io.StringIO(payload), {}) == 2
    assert capsys.readouterr().err.strip() == TOO_SLOW


def test_the_deadline_is_cleared_after_a_call(store: Path) -> None:
    handler = signal.getsignal(signal.SIGALRM)
    assert pg.main([], io.StringIO(json.dumps(bash("ls"))), {}) == 0
    assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)
    assert signal.getsignal(signal.SIGALRM) is handler


def test_a_late_deadline_in_the_error_handler_still_blocks(
    store: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The alarm may land while main() handles another error: that must exit 2, never 1."""

    def bug(*_args: object) -> None:
        raise RuntimeError("bug")

    def alarm_lands(*_args: object, **_kwargs: object) -> None:
        raise pg.DeadlineError

    monkeypatch.setattr(pg, "run", bug)
    monkeypatch.setattr(pg.logging, "exception", alarm_lands)
    assert pg.main([], io.StringIO(json.dumps(bash("ls"))), {}) == 2
    assert TOO_SLOW in capsys.readouterr().err
    assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)


def test_a_late_deadline_while_disarming_still_blocks(
    store: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The alarm may land after run() returns, just before the timer is disarmed: exit 2."""
    real = signal.setitimer

    def setitimer(which: int, seconds: float, *rest: float) -> tuple[float, float]:
        if seconds == 0:
            monkeypatch.setattr(signal, "setitimer", real)
            raise pg.DeadlineError
        return (0.0, 0.0)

    monkeypatch.setattr(signal, "setitimer", setitimer)
    handler = signal.getsignal(signal.SIGALRM)
    assert pg.main([], io.StringIO(json.dumps(bash("ls"))), {}) == 2
    assert TOO_SLOW in capsys.readouterr().err
    assert signal.getsignal(signal.SIGALRM) is handler


def test_the_command_line_maps_a_stray_deadline_to_exit_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def late_alarm() -> int:
        raise pg.DeadlineError

    monkeypatch.setattr(pg, "main", late_alarm)
    assert pg.cli() == 2
    assert TOO_SLOW in capsys.readouterr().err


def test_closed_stdin_blocks(store: Path) -> None:
    proc = subprocess.run(
        [sys.executable, "-I", str(SCRIPT)],
        preexec_fn=lambda: os.close(0),  # sys.stdin is None: reading it raises AttributeError
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 2
    assert "internal error" in proc.stderr


@pytest.mark.parametrize("role", [None, "pm"], ids=["unrestricted", "restricted"])
def test_a_missing_shell_words_module_blocks(
    run_hook: Run, store: Path, tmp_path: Path, role: str | None
) -> None:
    lone = tmp_path / "lone"
    lone.mkdir()
    for name in ("prod_guard.py", "hook_support.py", "hooklog.py"):
        shutil.copy(HOOKS / name, lone / name)
    env = {} if role is None else {ROLE: role}
    proc = run_hook(lone / "prod_guard.py", bash("ls"), env)
    assert proc.returncode == 2
    assert "internal error" in proc.stderr


WRAPPER = HOOKS / "fail-closed.sh"


@pytest.fixture
def this_python() -> dict[str, str]:
    """A PATH whose python3 is this interpreter, so the wrapper does not meet an old system one."""
    return {"PATH": f"{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}"}


def test_a_crash_under_the_wrapper_exits_2(
    run_hook: Run, store: Path, tmp_path: Path, this_python: dict[str, str]
) -> None:
    broken = tmp_path / "hooks"
    shutil.copytree(HOOKS, broken, ignore=shutil.ignore_patterns("__pycache__"))
    (broken / "hook_support.py").write_text("raise RuntimeError('boom')\n")
    proc = run_hook(
        broken / "fail-closed.sh", bash("ls"), this_python, args=(str(broken / "prod_guard.py"),)
    )
    assert proc.returncode == 2
    assert "coven: prod-guard failed (exit 1); blocking" in proc.stderr


def test_the_real_hook_runs_through_the_wrapper(
    run_hook: Run, store: Path, this_python: dict[str, str]
) -> None:
    refused = run_hook(WRAPPER, bash(PROD_COMMAND), this_python, args=(str(SCRIPT),))
    assert refused.returncode == 2
    assert PROD_MESSAGE in refused.stderr
    assert run_hook(WRAPPER, bash("ls"), this_python, args=(str(SCRIPT),)).returncode == 0


def test_decide_in_process(store: Path) -> None:
    assert decide(bash(PROD_COMMAND), store) == PROD_MESSAGE
    assert decide(bash("ls"), store, "pm") is None
    assert decide(bash(PROD_COMMAND), None) is None  # no store: the guard is off


def test_a_bad_payload_raises_payload_error(store: Path) -> None:
    with pytest.raises(pg.PayloadError, match="no tool_name"):
        decide({"tool_input": {}}, store)


# --- the log ---


def read_log() -> list[dict[str, object]]:
    path = Path(os.environ["XDG_CACHE_HOME"]) / "coven" / "logs" / "prod-guard.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_decisions_are_logged(run: Run, store: Path) -> None:
    run(bash(PROD_COMMAND))
    run(bash("ls"))
    refused, allowed = read_log()
    assert refused["decision"] == "deny"
    assert refused["tool"] == "Bash"
    assert refused["command"] == PROD_COMMAND
    assert refused["message"] == PROD_MESSAGE
    assert (allowed["decision"], allowed["message"]) == ("allow", None)


def test_a_logged_command_is_cut(run: Run, store: Path) -> None:
    run(bash("echo " + "x" * 1000))
    (record,) = read_log()
    assert isinstance(record["command"], str)
    assert len(record["command"]) < 400


def test_failures_are_logged(run: Run, store: Path) -> None:
    run("not json")
    (record,) = read_log()
    assert "could not parse hook input" in str(record["error"])


def test_the_off_path_logs_nothing(run: Run) -> None:
    run(bash(PROD_COMMAND))
    assert not (Path(os.environ["XDG_CACHE_HOME"]) / "coven" / "logs").exists()


def test_a_broken_config_is_logged(run: Run) -> None:
    write_config("{nope")
    run(bash("ls"), role="pm")
    (record,) = read_log()
    assert "config" in str(record["error"])


# --- explain: the data `coven guard explain` prints, and the hook's own decision ---


def explanation(payload: Payload, store: Path, role: str | None = None) -> Any:
    return pg.explain(payload, store, role, datetime.now(UTC))


def test_explain_lists_each_hit_with_its_trigger_and_approval(store: Path) -> None:
    chain = PROD_COMMAND + " && mail-cli send x"
    approve(store, name="granted", commands=[chain])
    found = explanation(bash(chain), store)
    first, second = found.hits
    assert (first.system, first.kind, first.trigger) == (
        "orders-db",
        "production",
        "psql\\s+.*-h\\s*orders-db",
    )
    assert first.approval is not None
    assert first.approval.id == "granted"
    assert (second.system, second.kind, second.trigger) == (
        "mail-send",
        "gated",
        "mail-cli\\s+send",
    )
    assert second.approval is None
    assert found.message == MAIL_MESSAGE
    assert found.verdict == "refused for every session"


def test_explain_names_the_tool_that_hit(store: Path) -> None:
    found = explanation(tool("mcp__mail__send"), store)
    (hit,) = found.hits
    assert (hit.system, hit.trigger, hit.approval) == ("mail-send", "mcp__mail__send", None)


def test_explain_says_allowed_when_nothing_refuses(store: Path) -> None:
    found = explanation(bash("ls"), store)
    assert (found.hits, found.message, found.verdict, found.restricted) == (
        [],
        None,
        "allowed",
        None,
    )
    approve(store)
    granted = explanation(bash(PROD_COMMAND), store)
    assert granted.verdict == "allowed"
    assert granted.hits[0].approval is not None


@pytest.mark.parametrize(
    "payload",
    [
        bash(PROD_COMMAND),
        bash("ls"),
        tool("mcp__mail__send"),
        tool("WebFetch"),
        bash("mail-cli send"),
    ],
)
@pytest.mark.parametrize("role", [None, "pm", ""])
def test_decide_is_the_message_of_explain(store: Path, payload: Payload, role: str | None) -> None:
    assert (
        decide(payload, store, role, datetime.now(UTC)) == explanation(payload, store, role).message
    )


def test_the_fallback_note_is_in_the_explanation(store: Path) -> None:
    break_guard(store)
    found = explanation(bash(PROD_COMMAND), store)
    assert FALLBACK_NOTE in found.message
    assert "must be an object" in found.note


# --- text that runs nothing: targets are checked on the code text ---


@pytest.fixture
def branch_store(tmp_path: Path) -> Path:
    """A store that also guards pushes to main."""
    path = make_store(tmp_path / "coven-memory", guard=BRANCH_GUARD)
    configure(path)
    return path


TEXT_ONLY = [
    "git commit -m 'psql -h orders-db -c x'",
    "cat >> notes.md <<'EOF'\npsql -h orders-db -c 'x'\nEOF",
    'echo "psql -h orders-db" >> progress.md',
    "printf '%s\\n' 'psql -h orders-db' >> notes.md",
    'gh pr create --title t --body "run psql -h orders-db after merge"',
    "grep -rn 'psql -h orders-db' docs/",
    "rg 'psql -h orders-db' docs",
    "ls docs # psql -h orders-db",
    "git tag -a v1 -m 'psql -h orders-db'",
    "git commit -m 'after review: git push origin main'",
]


@pytest.mark.parametrize("command", TEXT_ONLY)
@pytest.mark.parametrize("role", [None, "pm"], ids=["unrestricted", "restricted"])
def test_text_that_runs_nothing_passes(
    run: Run, branch_store: Path, command: str, role: str | None
) -> None:
    proc = run(bash(command), role=role)
    assert proc.returncode == 0, proc.stderr


TWINS = {
    "code-command-on-line": "git commit -m x && psql -h orders-db -c 'x'",
    "heredoc-then-bash": "cat > x.sh <<'EOF'\npsql -h orders-db -c 'x'\nEOF\nbash x.sh",
    "echo-into-sh": 'echo "psql -h orders-db -c x" | sh',
    "echo-into-tee": "echo 'psql -h orders-db' | tee notes.md",
    "heredoc-into-sh": "cat <<'EOF' | sh\npsql -h orders-db\nEOF",
    "substitution": 'git commit -m "$(psql -h orders-db -c x)"',
    "backticks": 'git commit -m "`psql -h orders-db`"',
    "hook-then-commit": (
        "cat > .git/hooks/pre-commit <<'EOF'\npsql -h orders-db -c 'x'\nEOF\ngit commit -m x"
    ),
    "tee-hook-then-commit": (
        "tee .git/hooks/pre-commit <<'EOF'\npsql -h orders-db\nEOF\ngit commit -m x"
    ),
    "rg-pre": "cat > /tmp/x <<'EOF'\npsql -h orders-db\nEOF\nrg --pre sh . /tmp/x",
    "sort-compress": "sort -S 1b --compress-program=sh <<'EOF'\npsql -h orders-db\nEOF",
    "comment-ends-at-newline": "ls # x\npsql -h orders-db",
    "commit-then-push-main": "git commit -m x && git push origin main",
    "expanding-word-keeps-the-message": "cd $HOME/r && git commit -m 'psql -h orders-db'",
    "arithmetic-runs-the-message": "git commit -m 'a[$(psql -h orders-db)]'; echo $[$_]",
}


@pytest.mark.parametrize("name", TWINS)
@pytest.mark.parametrize("role", [None, "pm"], ids=["unrestricted", "restricted"])
def test_the_dangerous_twin_is_refused(
    run: Run, branch_store: Path, name: str, role: str | None
) -> None:
    proc = run(bash(TWINS[name]), role=role)
    assert proc.returncode == 2
    assert "internal error" not in proc.stderr  # refused by a rule, not by a crash
    assert "is production" in proc.stderr or "is gated" in proc.stderr


def test_an_approval_still_matches_the_whole_command(run: Run, store: Path) -> None:
    approve(store)
    assert run(bash(PROD_COMMAND)).returncode == 0
    assert run(bash("git commit -m x && " + PROD_COMMAND)).returncode == 2


def test_an_approval_matches_the_command_not_its_code_text(run: Run, store: Path) -> None:
    """Both commands have the same code text (the message is blanked, the pathspec is not)."""
    approved_command = "git commit -m 'a' -- 'psql -h orders-db -c x'"
    approve(store, commands=[approved_command])
    assert run(bash(approved_command)).returncode == 0
    proc = run(bash("git commit -m 'b' -- 'psql -h orders-db -c x'"))
    assert proc.returncode == 2
    assert PROD_MESSAGE in proc.stderr


RUN_LATER = {
    "zshenv-heredoc": "cat >> /home/user/.zshenv <<'EOF'\npsql -h orders-db -c x\nEOF",
    "bashrc-echo": "echo 'psql -h orders-db -c x' >> /home/user/.bashrc",
    "bashrc-upper-case": "echo 'psql -h orders-db -c x' >> /HOME/USER/.BASHRC",
    "envrc-relative": "echo 'psql -h orders-db -c x' >> ./.envrc",
    "gitconfig-fsmonitor": (
        "printf '[core]\\n\\tfsmonitor = psql -h orders-db -c x\\n' >> /home/user/.gitconfig"
    ),
    "xdg-git-config-fsmonitor": (
        "printf '[core]\\n\\tfsmonitor = psql -h orders-db -c x\\n' >> /home/user/.config/git/config"
    ),
    "hooks-path-folder": "cat > /tmp/h/pre-commit <<'EOF'\npsql -h orders-db\nEOF",
}


@pytest.mark.parametrize("command", RUN_LATER.values(), ids=RUN_LATER)
@pytest.mark.parametrize("role", [None, "pm"], ids=["unrestricted", "restricted"])
def test_a_write_to_a_file_run_later_is_checked_whole(
    run: Run, store: Path, command: str, role: str | None
) -> None:
    proc = run(bash(command), role=role)
    assert proc.returncode == 2
    assert PROD_MESSAGE in proc.stderr


NOTES = [
    ".coven-docs/notes.md",
    ".superpowers/sdd/x/progress.md",
    "docs/update.md",
    "/tmp/t/task-12-fix-1.md",
    ".gitignore",
]


@pytest.mark.parametrize("target", NOTES)
@pytest.mark.parametrize("role", [None, "pm"], ids=["unrestricted", "restricted"])
def test_a_note_naming_a_target_passes(
    run: Run, store: Path, target: str, role: str | None
) -> None:
    for command in (
        f"echo 'psql -h orders-db -c x' >> {target}",
        f"cat >> {target} <<'EOF'\npsql -h orders-db -c x\nEOF",
    ):
        proc = run(bash(command), role=role)
        assert proc.returncode == 0, proc.stderr


# --- a store with a short, generic name ---


@pytest.fixture
def short(tmp_path: Path) -> Path:
    """A store named `work`: the name is also an ordinary word and a prefix of other names."""
    path = make_store(tmp_path / "work")
    configure(path)
    return path


@pytest.mark.parametrize("command", ["cd work-items && ls", "ls ../workshop", "ls homework"])
def test_a_restricted_session_may_name_words_that_contain_the_store_name(
    run: Run, short: Path, command: str
) -> None:
    proc = run(bash(command), role="pm")
    assert proc.returncode == 0, proc.stderr


def test_a_short_named_store_still_enforces_its_targets(run: Run, short: Path) -> None:
    assert run(bash(PROD_COMMAND)).returncode == 2
    approve(short)
    assert run(bash(PROD_COMMAND)).returncode == 0


@pytest.mark.parametrize(
    "command",
    ["ls work-memory-notes", "cat homework-memory", "echo work-memory.bak", "ls my_work-memory"],
)
def test_a_restricted_session_may_use_words_that_only_contain_the_store_name(
    run: Run, store: Path, command: str
) -> None:
    proc = run(bash(command), role="pm")
    assert proc.returncode == 0, proc.stderr


@pytest.mark.parametrize("command", ["echo 'work-memory'", 'echo "work-memory"'])
def test_a_restricted_session_may_echo_the_store_name_as_text(
    run: Run, store: Path, command: str
) -> None:
    proc = run(bash(command), role="pm")
    assert proc.returncode == 0, proc.stderr
