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


def git(store: Path, *args: str, stdin: str | None = None) -> str:
    done = subprocess.run(
        ["git", "-C", str(store), *args], input=stdin, capture_output=True, text=True, check=True
    )
    return done.stdout.strip()


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


def home() -> Path:
    return Path(os.environ["HOME"])


@pytest.fixture
def store() -> Path:
    """A committed store with GUARD, at ~/coven-memory (the restricted rules read `~` forms); the
    guard is on because the working tree has a guard.json."""
    path = make_store(home() / "coven-memory")
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


def bash(command: str, cwd: str = "/tmp") -> Payload:
    return {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": cwd}


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


def test_no_config_file_and_no_default_store_is_off(run: Run, no_git: dict[str, str]) -> None:
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


@pytest.mark.parametrize("role", [None, "pm"], ids=["unrestricted", "restricted"])
def test_no_config_file_uses_the_default_store_and_enforces_it(run: Run, role: str | None) -> None:
    """Deleting the config file must not switch off a store that holds a guard.json."""
    make_store(Path(os.environ["HOME"]) / "coven-memory")
    assert not config_file().exists()
    proc = run(bash(PROD_COMMAND), role=role)
    assert proc.returncode == 2
    assert PROD_MESSAGE in proc.stderr


def test_no_config_file_and_a_default_store_without_a_guard_file_is_off(
    run: Run, no_git: dict[str, str]
) -> None:
    (Path(os.environ["HOME"]) / "coven-memory" / "systems").mkdir(parents=True)
    proc = run(bash(PROD_COMMAND), role="pm", env=no_git)
    assert (proc.returncode, proc.stderr) == (0, "")


def test_the_default_store_is_coven_memory_in_home(run: Run, no_git: dict[str, str]) -> None:
    write_config("{}")
    assert run(bash(PROD_COMMAND), env=no_git).returncode == 0


def test_a_missing_store_folder_without_the_latch_is_off(
    run: Run, tmp_path: Path, no_git: dict[str, str]
) -> None:
    configure(tmp_path / "nowhere")
    assert run(bash(PROD_COMMAND), role="pm", env=no_git).returncode == 0


@pytest.mark.parametrize("payload", [bash("ls"), tool("mcp__x__y")], ids=["bash", "mcp"])
@pytest.mark.parametrize("role", [None, "pm"], ids=["unrestricted", "restricted"])
def test_a_missing_store_folder_with_the_latch_refuses_outward_calls(
    run: Run, tmp_path: Path, payload: Payload, role: str | None
) -> None:
    """The latch says the store held a guard.json: a deleted or moved folder must not unguard it."""
    nowhere = tmp_path / "nowhere"
    configure(nowhere, latch=True)
    proc = run(payload, role=role)
    assert proc.returncode == 2
    assert proc.stderr.startswith(unreadable_store(nowhere))
    assert "does not exist" in proc.stderr


def test_a_missing_store_folder_with_the_latch_leaves_the_other_tools_alone(
    run: Run, tmp_path: Path
) -> None:
    configure(tmp_path / "nowhere", latch=True)
    assert run(tool("Read", file_path="/tmp/x"), role="pm").returncode == 0


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
    assert pg.read_config() == pg.GuardConfig(sibling.store_path(), latch=False)  # no file
    for text in ("{}", json.dumps({"memory_path": str(tmp_path / "elsewhere")})):
        write_config(text)
        assert pg.read_config().store == sibling.store_path()


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
    "memory-path-relative": json.dumps({"memory_path": "coven-memory"}),
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


# --- git replace refs and grafts change what git reads without a commit ---


def blob(store: Path, text: str) -> str:
    return git(store, "hash-object", "-w", "--stdin", stdin=text)


def test_a_replace_ref_on_guard_json_changes_nothing(run: Run, store: Path) -> None:
    old = git(store, "rev-parse", "HEAD:systems/guard.json")
    git(store, "replace", old, blob(store, json.dumps({"targets": []})))
    proc = run(bash(PROD_COMMAND))
    assert proc.returncode == 2
    assert PROD_MESSAGE in proc.stderr


def test_a_replace_ref_on_an_approval_changes_nothing(run: Run, store: Path) -> None:
    approve(store, expires=in_hours(-1))  # expired: it approves nothing
    old = git(store, "rev-parse", "HEAD:approvals/a.json")
    git(store, "replace", old, blob(store, approval()))  # a live one
    proc = run(bash(PROD_COMMAND))
    assert proc.returncode == 2
    assert PROD_MESSAGE in proc.stderr


def test_a_graft_on_head_changes_nothing(run: Run, store: Path) -> None:
    """HEAD's guard.json is invalid, so the last valid version in the history is enforced: a graft
    that gives HEAD another parent must not swap that history for one with a lax guard.json."""
    break_guard(store)
    head = git(store, "rev-parse", "HEAD")
    (store / "systems/guard.json").write_text(json.dumps({"targets": []}))
    git(store, "add", "-A")
    tree = git(store, "write-tree")
    lax = git(store, "commit-tree", tree, "-m", "lax")
    git(store, "replace", "--graft", head, lax)
    proc = run(bash(PROD_COMMAND))
    assert proc.returncode == 2
    assert PROD_MESSAGE in proc.stderr


def test_git_gets_no_git_variable_but_the_one_that_turns_replace_refs_off(
    store: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = tmp_path / "git-env"
    real = shutil.which("git")
    fake_git(tmp_path, monkeypatch, f'env | grep "^GIT_" | sort >> {seen}\nexec {real} "$@"')
    monkeypatch.setenv("GIT_TRACE", "0")
    monkeypatch.setenv("GIT_REPLACE_REF_BASE", "refs/elsewhere/")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "0")
    assert decide(bash("ls"), store) is None
    assert set(seen.read_text().split()) == {"GIT_NO_REPLACE_OBJECTS=1"}


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
@pytest.mark.parametrize("granted", [False, True], ids=["unapproved", "approved"])
def test_decide_is_the_message_of_explain(
    store: Path, payload: Payload, role: str | None, granted: bool
) -> None:
    if granted:
        approve(store, commands=[PROD_COMMAND, "mail-cli send"], tools=["mcp__mail__send"])
        approve(
            store,
            name="b",
            system="mail-send",
            commands=["mail-cli send"],
            tools=["mcp__mail__send"],
        )
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
    path = make_store(home() / "coven-memory", guard=BRANCH_GUARD)
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
    if role is None:
        assert "is production" in proc.stderr or "is gated" in proc.stderr
    else:  # a restricted rule may speak first: a shell run on text whose host is not written out
        assert (
            "is production" in proc.stderr
            or "is gated" in proc.stderr
            or HOST_REFUSAL in proc.stderr
        )


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
def short() -> Path:
    """A store named `work`: the name is also an ordinary word and a prefix of other names."""
    (home() / "proj").mkdir()
    path = make_store(home() / "work")
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
    [
        "ls coven-memory-notes",
        "cat homecoven-memory",
        "echo coven-memory.bak",
        "ls my_coven-memory",
    ],
)
def test_a_restricted_session_may_use_words_that_only_contain_the_store_name(
    run: Run, store: Path, command: str
) -> None:
    proc = run(bash(command), role="pm")
    assert proc.returncode == 0, proc.stderr


@pytest.mark.parametrize("command", ["echo 'coven-memory'", 'echo "coven-memory"'])
def test_a_restricted_session_may_echo_the_store_name_as_text(
    run: Run, store: Path, command: str
) -> None:
    proc = run(bash(command), role="pm")
    assert proc.returncode == 0, proc.stderr


@pytest.mark.parametrize(
    "command",
    [
        "cd ~ && rm -rf work/systems",
        "cd ~ && rm -rf work",
        "cat ~/work/systems/x.md",
        "echo x > $HOME/work/approvals/a.json",
        "cp x $y/work/.git/HEAD",
        "rm ../work/systems/guard.json",
        "X=work; rm -rf ~/$X/systems",
        "echo work | xargs -I{} rm -rf ~/{}/systems",
        "d=work && cp x ~/`echo $d`/.git/HEAD",
    ],
)
def test_a_restricted_session_cannot_reach_a_short_named_store(
    run: Run, short: Path, command: str
) -> None:
    proc = run(bash(command, cwd=str(home() / "proj")), role="pm")
    assert proc.returncode == 2
    assert "cannot write to the memory store" in proc.stderr


@pytest.mark.parametrize(
    "command",
    ["cat 'coven-memory'", 'cat "coven-memory"', "ls /x/coven-memory/", "f(coven-memory,1)"],
)
def test_the_store_name_as_a_whole_token_is_refused(run: Run, store: Path, command: str) -> None:
    proc = run(bash(command), role="pm")
    assert proc.returncode == 2
    assert "cannot write to the memory store" in proc.stderr


# --- restricted sessions: any set COVEN_SESSION_ROLE ---

STORE_REFUSAL = f"{PREFIX} a 'pm' session cannot write to the memory store"
CONFIG_REFUSAL = f"{PREFIX} a 'pm' session cannot write to coven's config"
HOST_REFUSAL = "a host that is not written out"
CLAUDE_REFUSAL = "cannot start claude sessions"
INSTALLER_REFUSAL = "cannot run coven "


def project_dir() -> Path:
    path = home() / "proj"
    path.mkdir(exist_ok=True)
    return path


def pm_write_calls(store):
    approvals = store / "approvals"
    return [
        {"tool_name": "Write", "tool_input": {"file_path": f"{approvals}/a.json", "content": "{}"}},
        {
            "tool_name": "Edit",
            "tool_input": {
                "file_path": f"{approvals}/a.json",
                "old_string": "a",
                "new_string": "b",
            },
        },
        bash(f"echo x > {approvals}/a.json"),
        bash(f"cp a {approvals}/"),
        bash(f"echo x | tee {approvals}/b"),
        bash(f"mkdir -p {approvals}"),
        bash(f"cd {store} && echo x > approvals/c.json"),
        bash("echo x > approvals/d.json", cwd=str(store)),
        bash("echo x > ../coven-memory/approvals/e.json", cwd=str(store.parent / "proj")),
        bash("echo x > ~/coven-memory/approvals/f.json"),
        bash("echo x >$HOME/coven-memory/approvals/g.json"),
        bash(f'cp a "{approvals}/h.json"'),
        {"tool_name": "Write", "tool_input": {"file_path": "approvals/i.json"}, "cwd": str(store)},
        {"tool_name": "mcp__fs__write", "tool_input": {"args": {"path": f"{approvals}/j.json"}}},
        {
            "tool_name": "mcp__fs__write",
            "tool_input": {"n": 1, "sources": ["/tmp/x"], "dest": [f"{approvals}/m"]},
        },
        {"tool_name": "Write", "tool_input": {"file_path": f"{store / 'link'}/k.json"}},
        bash(f"echo x > {store / 'link'}/l.json"),
        bash(f"rm {store}/systems/guard.json"),
        bash(f"mv {store}/systems/guard.json /tmp/g"),
        bash(f"cp /tmp/empty.json {store}/systems/guard.json"),
        bash(f"echo '{{}}' | tee {store}/systems/guard.json"),
        bash(f"echo '{{\"targets\": []}}' > {store}/systems/guard.json"),
        bash("rm systems/guard.json", cwd=str(store)),
        bash(f"cat {store}/systems/orders-db.md"),
        {
            "tool_name": "Write",
            "tool_input": {"file_path": f"{store}/systems/guard.json", "content": "{}"},
        },
        {
            "tool_name": "Edit",
            "tool_input": {
                "file_path": f"{store}/systems/guard.json",
                "old_string": "a",
                "new_string": "b",
            },
        },
        {"tool_name": "Write", "tool_input": {"file_path": f"{store}/systems/x.md"}},
        {"tool_name": "NotebookEdit", "tool_input": {"notebook_path": f"{store}/n.ipynb"}},
        {"tool_name": "mcp__fs__delete", "tool_input": {"path": f"{store}/systems/guard.json"}},
        bash(f"cp -t{store}/systems a.md"),
        bash("cd ~ && rm coven-memory/systems/guard.json"),
        bash("rm ~/coven*/systems/guard.json"),
        bash("rm -rf ~/coven-memor?"),
        bash("rm -rf ~/*"),
        bash("rm -rf ../*", cwd=str(store.parent / "proj")),
        bash("rm -rf ../[c]oven-mem*/systems", cwd=str(store.parent / "proj")),
        bash(f"echo x > {store.parent / 'alias'}/a.json"),
        bash("cd ~ && cp /tmp/e.json coven-memor?/systems/guard.json"),
        bash("cd ~ && rm coven*/systems/guard.json"),
        bash("cd && cd coven* && cp /tmp/e.json systems/guard.json"),
        bash("pushd ~; rm -rf ./c*"),
        bash("cd ~ && cp x */.git/HEAD"),
        bash("cd ~ && cp x */.git/refs/heads/main"),
        bash("cd ~ && rm -rf */.git"),
        bash("cd ~ && cp -r /tmp/forge/.git/objects/. */.git/objects/"),
        bash("cd ~/cov''en-memory && git reset --hard HEAD~1"),
        bash('cd ~/"coven-memory" && git reset --hard HEAD~1'),
        bash("cd ~/cov\\en-memory && git reset --hard HEAD~1"),
        bash("cd ~ && cp x */systems/guard.json"),
        bash("cd ~ && cp x */approvals/a.json"),
        bash(
            "python3 - <<'EOF'\nfrom pathlib import Path\n"
            '(Path.home() / "coven-memory" / "approvals" / "a.json").write_text("{}")\nEOF'
        ),
        bash(
            "python3 -c \"import os; open(os.path.join(os.path.expanduser('~'), 'coven-memory',"
            " 'approvals', 'a.json'), 'w').write('{}')\""
        ),
        bash("find ~ -maxdepth 1 -name coven-memory -exec rm -rf {} +"),
        bash("env -C ~ rm -rf coven-memory"),
        bash("git -C ~ rm -rf coven-memory"),
        {"tool_name": "mcp__fs__write", "tool_input": {"file_path": f"{approvals}/a b.json"}},
        {
            "tool_name": "mcp__fs__move_file",
            "tool_input": {"source": "/tmp/a.json", "destination": f"{approvals}/a b.json"},
        },
        {"tool_name": "mcp__fs__write", "tool_input": {"to": f"  {approvals}/a.json\n"}},
        {
            "tool_name": "mcp__chat__send",
            "tool_input": {"text": f"{store}/systems/billing-api.md needs an update"},
        },
        {
            "tool_name": "mcp__shell__exec",
            "tool_input": {"command": f"cp /tmp/g.json {store}/systems/guard.json"},
        },
        {
            "tool_name": "mcp__shell__exec",
            "tool_input": {"command": f"mv /tmp/x.json {approvals}/x.json"},
        },
        {
            "tool_name": "mcp__db__query",
            "tool_input": {"sql": f"COPY t TO '{approvals}/x.json'"},
        },
        {
            "tool_name": "mcp__shell__exec",
            "tool_input": {"command": "cd ~ && cp x */approvals/a.json"},
        },
        {
            "tool_name": "mcp__shell__exec",
            "tool_input": {"args": ["-C", "~", "rm", "coven-memory"]},
        },
        {"tool_name": "mcp__x__y", "tool_input": {"a": {"b": "/tmp/c"}, "z": f"{store}\u0000"}},
        {
            "tool_name": "mcp__chat__send",
            "tool_input": {"text": f"Please update {store}/systems/billing-api.md today"},
        },
        {
            "tool_name": "mcp__shell__exec",
            "tool_input": {
                "cwd": str(store.parent),
                "command": "cp /tmp/g.json */systems/guard.json",
            },
        },
        {
            "tool_name": "mcp__shell__exec",
            "tool_input": {"cwd": "~", "command": "cp /tmp/g.json */systems/guard.json"},
        },
    ]


PM_WRITE_IDS = [
    "write",
    "edit",
    "bash-redirect",
    "bash-cp",
    "bash-tee",
    "bash-mkdir",
    "bash-cd-store",
    "bash-cwd-in-store",
    "bash-relative",
    "bash-tilde",
    "bash-home-var",
    "bash-quoted",
    "write-relative",
    "mcp-nested",
    "mcp-list",
    "write-symlink",
    "bash-symlink",
    "rm-guard",
    "mv-guard",
    "cp-guard",
    "tee-guard",
    "redirect-guard",
    "rm-guard-from-store",
    "bash-reads-store",
    "write-guard",
    "edit-guard",
    "write-systems-entry",
    "notebook-edit",
    "mcp-delete-guard",
    "bash-glued-option",
    "cd-home-then-relative",
    "glob-in-store",
    "glob-is-store",
    "glob-is-parent",
    "relative-glob-is-parent",
    "bracket-glob",
    "outside-symlink",
    "cd-then-glob-cp",
    "cd-then-glob-rm",
    "cd-into-glob",
    "pushd-then-glob",
    "cd-then-star-git-head",
    "cd-then-star-git-ref",
    "cd-then-star-git-dir",
    "cd-then-star-git-objects",
    "cd-empty-quotes-in-name",
    "cd-quoted-name",
    "cd-backslash-in-name",
    "cd-then-star-guard",
    "cd-then-star-approval",
    "python-heredoc-path-parts",
    "python-c-path-join",
    "find-by-name",
    "env-c",
    "git-c",
    "mcp-path-field-with-space",
    "mcp-move-file-destination-with-space",
    "mcp-padded-path",
    "mcp-text-starting-with-store-path",
    "mcp-command-cp-guard",
    "mcp-command-mv-approval",
    "mcp-sql-copy-to-approval",
    "mcp-command-cd-then-star-approval",
    "mcp-names-store",
    "mcp-nul-after-store-path",
    "mcp-sentence-mentioning-store",
    "mcp-own-cwd-then-star-guard",
    "mcp-own-cwd-home-then-star-guard",
]


@pytest.mark.parametrize("index", range(len(PM_WRITE_IDS)), ids=PM_WRITE_IDS)
def test_a_restricted_session_cannot_write_or_reach_the_memory_store(
    run: Run, store: Path, index: int
) -> None:
    (store / "link").symlink_to(store / "approvals")
    (home() / "alias").symlink_to(store / "approvals")
    project_dir()
    payload = pm_write_calls(store)[index]
    proc = run(payload, role="pm")
    assert proc.returncode == 2
    assert STORE_REFUSAL in proc.stderr
    assert "ask the GM" in proc.stderr
    assert run(payload).returncode == 0


def test_pm_cannot_write_approvals(run: Run, store: Path) -> None:
    proc = run(bash(f"echo x > {store}/approvals/a.json"), role="pm")
    assert proc.returncode == 2
    assert f"cannot write to the memory store ({store})" in proc.stderr


@pytest.mark.parametrize(
    "payload",
    [
        bash("ls -la"),
        bash("git commit -m 'docs: approvals flow'"),
        bash("cat docs/approvals.md"),
        bash("ls *.py src/[a-z]*.md", cwd="/tmp"),
        {
            "tool_name": "Write",
            "tool_input": {"file_path": "/tmp/approvals/a.json", "content": "\0"},
        },
        {"tool_name": "Read", "tool_input": {"file_path": "/tmp/x"}},
        {"tool_name": "Read", "tool_input": {"file_path": "STORE/systems/orders-db.md"}},
        {"tool_name": "Grep", "tool_input": {"pattern": "x", "path": "STORE/approvals"}},
        {"tool_name": "Write", "tool_input": {"file_path": "/tmp/n.md", "content": "see STORE"}},
        {"tool_name": "Task", "tool_input": {"prompt": "read STORE/systems/orders-db.md"}},
        {"tool_name": "Write", "tool_input": {"file_path": "/tmp/n.md", "content": "STORE"}},
        {"tool_name": "mcp__db__query", "tool_input": {"query": "select 1"}},
        {"tool_name": "mcp__shell__exec", "tool_input": {"command": "ls src"}},
        {"tool_name": "mcp__shell__exec", "tool_input": {"cwd": "~", "command": "ls src/*.py"}},
        {"tool_name": "mcp__x__y", "tool_input": {"a": {"b": "/tmp/c"}, "z": "/tmp/c\u0000"}},
    ],
    ids=[
        "ls",
        "word-in-message",
        "other-approvals-file",
        "unrelated-globs",
        "other-approvals-dir",
        "read",
        "read-map",
        "grep-approvals",
        "write-mentioning-store",
        "task-mentioning-store",
        "write-content-is-store-path",
        "mcp-query-without-store",
        "mcp-command-without-store",
        "mcp-ordinary-glob",
        "mcp-nul-without-store",
    ],
)
def test_pm_runs_ordinary_calls(run: Run, store: Path, payload: Payload) -> None:
    payload = json.loads(json.dumps(payload).replace("STORE", str(store)))
    proc = run(payload, role="pm")
    assert proc.returncode == 0, proc.stderr


def test_a_restricted_session_cannot_write_approvals_and_the_message_names_the_store(
    run: Run, store: Path
) -> None:
    proc = run(bash(f"echo x > {store}/approvals/a.json"), role="pm")
    assert proc.returncode == 2
    assert f"cannot write to the memory store ({store})" in proc.stderr


def test_unknown_role_is_treated_as_restricted(run: Run, store: Path) -> None:
    proc = run(bash(f"echo x > {store}/approvals/a.json"), role="gm")
    assert proc.returncode == 2
    assert "a 'gm' session cannot write to the memory store" in proc.stderr


@pytest.mark.parametrize("role", ["", "PM"], ids=["empty", "other-case"])
def test_any_set_role_is_restricted(run: Run, store: Path, role: str | None) -> None:
    assert run(bash("claude -p hi"), role=role).returncode == 2


@pytest.mark.parametrize(
    "command",
    ["printenv COVEN_SESSION_ROLE", "  printenv COVEN_SESSION_ROLE\n"],
    ids=["exact", "surrounding-space"],
)
def test_pm_may_run_the_role_self_check(run: Run, store: Path, command: str) -> None:
    proc = run(bash(command), role="pm")
    assert proc.returncode == 0, proc.stderr


@pytest.mark.parametrize(
    "command",
    [
        "env -u COVEN_SESSION_ROLE claude -p hi",
        "unset COVEN_SESSION_ROLE; ls",
        "COVEN_SESSION_ROLE= ./run.sh",
        "echo $COVEN_SESSION_ROLE",
        "printenv COVEN_SESSION_ROLE && unset COVEN_SESSION_ROLE",
        "printenv COVEN_SESSION_ROLE\nunset COVEN_SESSION_ROLE",
        "printenv COVEN_SESSION_ROLE > /tmp/role",
        "export COVEN_SESSION_ROLE=",
    ],
    ids=[
        "env-u",
        "unset",
        "reassign",
        "read",
        "self-check-then-unset",
        "self-check-newline-unset",
        "self-check-redirect",
        "export-empty",
    ],
)
def test_pm_cannot_touch_the_role_variable(run: Run, store: Path, command: str) -> None:
    proc = run(bash(command), role="pm")
    assert proc.returncode == 2
    assert "coven prod-guard: a 'pm' session cannot change COVEN_SESSION_ROLE" in proc.stderr
    assert run(bash(command)).returncode == 0


SELF_CHECKS = [
    f"printenv {ROLE} && git status",
    f"printenv {ROLE}; pwd",
    f"git status; printenv {ROLE}",
    f"printenv {ROLE}\nls",
    f"grep -rn {ROLE} src/",
    f"rg {ROLE} docs",
    f"git commit -m 'docs: {ROLE}'",
    f"cat >> notes.md <<'EOF'\n{ROLE}\nEOF",
    f"echo '{ROLE} is pm'",
    f"printenv {ROLE} && cd x && ls",
    f"printenv {ROLE} || pwd",
    f"printenv {ROLE} & pwd",
]
ROLE_TWINS = [
    f"read {ROLE}",
    f"printf -v {ROLE} x",
    f"declare -n r={ROLE}",
    f"echo ${{{ROLE}:=x}}",
    f"mapfile {ROLE} < f",
    f"local {ROLE}",
    f"export -n {ROLE}",
    f"printenv {ROLE} x",
    f"grep {ROLE} f; unset {ROLE}",
    f"{ROLE}= git status",
    f"cat <<'EOF' | sh\nunset {ROLE}\nEOF",
    f"cat > x.sh <<'EOF'\nunset {ROLE}\nEOF\nsource x.sh",
]
# The self-check in a position where its output or its words may run or change something.
SELF_CHECK_TWINS = [
    f"echo $(printenv {ROLE})",
    f'x="$(printenv {ROLE})"',
    f"printenv {ROLE} | sh",
    f"printenv {ROLE} |& sh",
    f"printenv {ROLE} > /tmp/r",
    f"env -u {ROLE} ls",
    f"{ROLE}=x ls",
    f"{ROLE}=x printenv {ROLE}",
    # zsh reads the words of a parenthesised list as data: the lone command must stay code
    f"for v (printenv {ROLE}) unset $v; ./child",
    f"a=(printenv {ROLE}); unset $a; ./child",
    f"a=(\nprintenv {ROLE}\n); unset $a; ./child",
    f"for v (\nprintenv {ROLE}\n) unset $v; ./child",
    f"foreach v (printenv {ROLE})\nunset $v\nend\n./child",
    f"a=(printenv {ROLE}); env -u $a[2] make agent",
    f"(printenv {ROLE})",
    # a later command may read the blanked words back ($_, a trap, the execution string)
    f"printenv {ROLE}; unset $_; ./child",
    f'printenv {ROLE} && env -u "$_" ./child',
    f"printenv {ROLE}; v=$_; unset $v; ./child",
    f"printenv {ROLE}; sh -c 'unset $0; ./child' $_",
    f"printenv {ROLE}; env -u$_ ./child",
    f"printenv {ROLE} & unset $_",
    f"trap 'unset ${{BASH_COMMAND#* }}' DEBUG\nprintenv {ROLE}\n./child",
    f"trap 'unset ${{ZSH_DEBUG_CMD#* }}' DEBUG\nprintenv {ROLE}\n./child",
    f"trap 'env -u ${{BASH_COMMAND#* }} ./child' DEBUG; printenv {ROLE}",
    f'printenv {ROLE}; read a b c <<< "$ZSH_EXECUTION_STRING"; unset $b; ./child',
    f'printenv {ROLE}; read a b c <<< "$BASH_EXECUTION_STRING"; unset $b; ./child',
    f"printenv {ROLE} && make test",
    # a word left as code that may expand ($_, arithmetic) keeps the whole line as code
    f"git commit --allow-empty -m 'a[$(env -u {ROLE} ./child)]'; echo $[$_]",
    f"git commit --allow-empty -m 'a[$(env -u {ROLE} ./child)]'; echo $(($_))",
    f"printenv {ROLE}; echo $[$_=1]; git commit -m x",
    f"printenv {ROLE}; ls $[$_=0]",
    f'printenv {ROLE}; echo "-v $_"; printf $_ gm; git commit -m y',
    f"printenv {ROLE}; printf $=_ x",
]


@pytest.mark.parametrize("command", SELF_CHECKS)
def test_pm_may_use_the_role_variable_as_data_or_self_check(
    run: Run, store: Path, command: str
) -> None:
    proc = run(bash(command), role="pm")
    assert proc.returncode == 0, proc.stderr


@pytest.mark.parametrize("command", ROLE_TWINS + SELF_CHECK_TWINS)
def test_pm_cannot_touch_the_role_variable_elsewhere(run: Run, store: Path, command: str) -> None:
    proc = run(bash(command), role="pm")
    assert proc.returncode == 2
    assert f"cannot change {ROLE}" in proc.stderr


HEAD, TAIL = ROLE[: len(ROLE) // 2], ROLE[len(ROLE) // 2 :]


@pytest.mark.parametrize(
    "command",
    [
        f"unset {HEAD}''{TAIL}",
        f'unset "{HEAD}"{TAIL}',
        f"unset {HEAD}\\{TAIL}",
        f'export {HEAD}""{TAIL}=',
        f"unset {HEAD}\\\n{TAIL}; make agent",
        f'unset "{HEAD}\\\n{TAIL}"; make agent',
        f"unset {HEAD}$'{TAIL}'",
        f'unset {HEAD}$"{TAIL}"',
        f"export {HEAD}$'{TAIL}'=gm",
        f"env -u {HEAD}$'{TAIL}' ./child",
    ],
)
def test_pm_cannot_touch_the_role_variable_split_by_quotes(
    run: Run, store: Path, command: str
) -> None:
    proc = run(bash(command), role="pm")
    assert proc.returncode == 2
    assert f"cannot change {ROLE}" in proc.stderr


@pytest.mark.parametrize(
    "command",
    [f"grep -rn '{ROLE}' src/", f'git commit -m "docs: {ROLE}"', f"printenv {ROLE} && git status"],
)
def test_quoted_role_variable_as_data_or_self_check_still_passes(
    run: Run, store: Path, command: str
) -> None:
    proc = run(bash(command), role="pm")
    assert proc.returncode == 0, proc.stderr


def test_self_check_chained_to_claude_is_refused(run: Run, store: Path) -> None:
    # claude is not inert, so the self-check stays code and the role rule, first, refuses it
    proc = run(bash(f"printenv {ROLE}; claude -p x"), role="pm")
    assert proc.returncode == 2
    assert f"cannot change {ROLE}" in proc.stderr


@pytest.mark.parametrize(
    "command",
    [
        "claude --bg 'do x'",
        "cd x && claude -p hi",
        "ls || claude",
        "ls; claude -p hi",
        "echo hi | claude -p",
        "(claude -p hi)",
        "echo $(claude -p hi)",
        "ls &\nclaude -p hi",
        "FOO=1 claude -p hi",
        "nohup claude -p hi &",
        "env A=1 claude -p hi",
        "/usr/local/bin/claude -p hi",
        "~/.local/bin/claude",
        "bash -c 'claude -p hi'",
        "env -i claude -p hi",
        "exec env -i claude -p hi",
        "sudo -E claude -p hi",
        "env -i HOME=/x claude -p hi",
        "sudo -u me claude -p hi",
        "timeout 9 claude -p hi",
        "timeout -s KILL 9m claude",
        "echo x | xargs claude -p",
        "echo x | xargs -n1 -I{} claude -p {}",
        '"claude" -p hi',
        "'claude' -p hi",
        '"/usr/local/bin/claude" -p hi',
        "nohup env FOO=1 claude -p hi",
        "exec -a x claude",
        "nice -n 5 claude",
        "if true; then claude -p hi; fi",
        "for i in 1; do claude -p hi; done",
        "while true; do claude -p hi; done",
        "! claude -p hi",
        "if claude -p hi; then :; fi",
        "if false; then :; else claude -p hi; fi",
        "\\claude -p hi",
        "c\\laude -p hi",
        "timeout infinity claude",
        "timeout -s KILL infinity claude -p hi",
        "timeout -k 5 infinity claude -p hi",
        "eval claude -p hi",
        "coproc claude -p hi",
        "command claude -p hi",
        "cl''aude -p hi",
        '"cl"aude -p hi',
        "nohup 'cl'\"au\"de -p hi",
        "env -i claude",
        "exec claude",
        "command claude",
        "sudo -E claude",
        '"claude" -p',
        "/usr/local/bin/claude -p",
        "xargs claude",
        # a line continuation joins the name (`cla\` newline `ude` is claude)
        "cla\\\nude -p hi",
        "c\\\nlaude",
        # a newline after an escaped backslash, or after one in a comment, ends the command
        "echo a\\\\\nclaude -p hi",
        "ls # a \\\nclaude -p hi",
        # a redirect may come before the command's name
        ">out claude -p hi",
        "> /dev/null claude -p hi",
        "&>/dev/null claude -p hi",
        "<n.md claude -p hi",
        "2> err claude -p hi",
        "nohup >out claude -p hi",
        "<<'EOF' claude -p hi\nx\nEOF",
        # more wrappers, and their command-string forms
        'script -q -c "claude -p hi" /dev/null',
        "script -q /dev/null claude -p hi",
        "screen -dmS pm claude -p hi",
        "tmux new-session -d -s pm 'claude -p hi'",
        "tmux new -d 'cd x && claude -p hi'",
        "su -c 'claude -p hi' u",
        "su u -c 'claude -p hi'",
        "runuser -u u -- claude -p hi",
        "flock /tmp/l claude -p hi",
        "flock /tmp/l -c 'claude -p hi'",
        "systemd-run --user claude -p hi",
        "ksh -c 'claude -p hi'",
        "busybox sh -c 'claude -p hi'",
        "chrt 10 claude -p hi",
        "ionice -c 3 claude -p hi",
        "taskset 0x1 claude -p hi",
        "taskset -c 0 claude -p hi",
        "strace -f -o out claude -p hi",
        "ltrace claude -p hi",
        "unbuffer claude -p hi",
        # a lookup of claude in a substitution puts its path in command position
        "$(which claude) -p hi",
        '"$(command -v claude)" -p hi',
        "`which claude` -p hi",
        'node "$(which claude)" -p hi',
        "$(type -P claude) -p hi",
        "$(whence -p claude) -p hi",
        "$(whereis -b claude) -p hi",
        "$(builtin command -V claude) -p hi",
        "$(true; which claude) -p hi",
        "p=$(which claude); $p -p hi",
    ],
)
def test_pm_cannot_start_claude(run: Run, store: Path, command: str) -> None:
    proc = run(bash(command), role="pm")
    assert proc.returncode == 2
    assert "coven prod-guard: a 'pm' session cannot start claude" in proc.stderr
    assert run(bash(command)).returncode == 0


@pytest.mark.parametrize(
    "command",
    [
        "npx @anthropic-ai/claude-code -p hi",
        "npx @anthropic-ai/claude-code",
        "bunx @anthropic-ai/claude-code",
        "pnpm dlx @anthropic-ai/claude-code",
        "npm exec @anthropic-ai/claude-code",
        "bash -c 'npx @anthropic-ai/claude-code'",
        "sh <<'EOF'\nnpx @anthropic-ai/claude-code\nEOF",
        "cat <<'EOF' | sh\nnpx @anthropic-ai/claude-code\nEOF",
        "node /usr/lib/node_modules/@anthropic-ai/claude-code/cli.js",
        'gh pr create --body "$(npx @anthropic-ai/claude-code -p hi)"',
        "npx https://registry.npmjs.org/@anthropic-ai/claude-code/-/claude-code-2.0.0.tgz",
        "curl https://claude.com/claude-code",
        "git commit -m y --trailer -m claude-code",
        "grep claude-code -e KEY",
        "(( cat <<'X' ))\nnpx @anthropic-ai/claude-code\nX",
        "eval 'cat() { bash; }'; cat <<'EOF'\nnpx @anthropic-ai/claude-code\nEOF",
    ],
)
def test_pm_cannot_run_claude_code(run: Run, store: Path, command: str) -> None:
    proc = run(bash(command), role="pm")
    assert proc.returncode == 2
    assert "coven prod-guard: a 'pm' session cannot run claude-code; ask the GM" in proc.stderr
    assert run(bash(command)).returncode == 0


@pytest.mark.parametrize(
    "command",
    [
        "gh pr create --title t --body \"$(cat <<'EOF'\n"
        '🤖 Generated with [Claude Code](https://claude.com/claude-code)\nEOF\n)"',
        'gh pr create --title t --body "See https://claude.com/claude-code"',
        "git commit -m 'docs: mention claude-code'",
        "grep -rn claude-code docs/",
        "cat > body.md <<'EOF'\nhttps://claude.com/claude-code\nEOF",
    ],
)
def test_pm_may_mention_claude_code_as_data(run: Run, store: Path, command: str) -> None:
    proc = run(bash(command), role="pm")
    assert proc.returncode == 0, proc.stderr


@pytest.mark.parametrize(
    "command",
    [
        "echo claude",
        "git commit -m 'claude: fix'",
        "ls claude-notes",
        "claudette -p",
        "cat x/claude",
        "find . | xargs grep claude",
        "time make docs",
        "timeout 9 echo claude",
        "cd src && ls *.py",
        "command -v claude",
        "command -V claude",
        "builtin command -v claude",
        "type claude",
        "which claude",
        "type -P claude",
        "whereis claude",
        "command -v claude && git status",
    ],
)
def test_pm_may_mention_claude(run: Run, store: Path, command: str) -> None:
    proc = run(bash(command), role="pm")
    assert proc.returncode == 0, proc.stderr


CLAUDE_PASSES = [
    "claude --version",
    "claude -v",
    "  claude --version\n",
    "cat >> notes.md <<'EOF'\nrun claude -p 'summarise' in the GM\nEOF",
    'echo "claude -p hi" >> notes.md',
]
CLAUDE_TWINS = [
    "claude --version; claude -p hi",
    "claude -v && claude",
    "claude --version -p hi",
    "claude -p --version",
    "claude -v -p hi",
    "claude --version\nclaude -p hi",
    "claude --version | claude -p",
    "echo $(claude --version)",
    "claude --version &",
    "cat <<'EOF' | sh\nclaude -p hi\nEOF",
    "bash <<'EOF'\nclaude -p hi\nEOF",
    "cat > x.sh <<'EOF'\nclaude -p hi\nEOF\nbash x.sh",
]


@pytest.mark.parametrize("command", CLAUDE_PASSES)
def test_pm_may_query_the_version_or_write_claude_text(run: Run, store: Path, command: str) -> None:
    proc = run(bash(command), role="pm")
    assert proc.returncode == 0, proc.stderr


@pytest.mark.parametrize("command", CLAUDE_TWINS)
def test_pm_still_cannot_start_claude_around_those(run: Run, store: Path, command: str) -> None:
    proc = run(bash(command), role="pm")
    assert proc.returncode == 2
    assert "cannot start claude sessions" in proc.stderr


@pytest.mark.parametrize(
    "command",
    [
        "PATH=x claude --version",
        "./claude --version",
        "claude --version 2>&1",
        "claude --version >/dev/null",
        # a newline makes `--version` a second command, after a plain `claude`
        "claude\n--version",
        "claude \\\n--version",
        # bash keeps \v and \x1c inside a word: `\v--version` is a prompt, not an option
        "claude \x0b--version",
        "claude\x1c-v",
    ],
)
def test_only_the_exact_version_query_is_skipped(run: Run, store: Path, command: str) -> None:
    proc = run(bash(command), role="pm")
    assert proc.returncode == 2
    assert "cannot start claude sessions" in proc.stderr


@pytest.mark.parametrize(
    "command",
    [
        # the live false positive: a note whose own line names the headless flag
        "cat >> notes.md <<'EOF'\nclaude -p 'summarise' in the GM\nEOF",
        "git commit -m 'docs: notes\n\nclaude -p runs headless'",
        "grep -c x <<'EOF'\nclaude -p hi\nEOF",
        # a line continuation: claude is echo's argument
        "echo a \\\nclaude",
    ],
)
def test_pm_may_write_claude_lines_as_data(run: Run, store: Path, command: str) -> None:
    proc = run(bash(command), role="pm")
    assert proc.returncode == 0, proc.stderr


@pytest.mark.parametrize(
    "command",
    [
        "cat >> notes.md <<'EOF'\nx\nEOF\nclaude -p 'summarise' in the GM",
        "git commit -m 'docs: notes'\nclaude -p hi",
        "grep -c x <<'EOF' | sh\nclaude -p hi\nEOF",
        "cat >> notes.md <<'EOF' && sh notes.md\nclaude -p hi\nEOF",
        "cat >> ~/.bashrc <<'EOF'\nclaude -p hi\nEOF",
        "cat > .git/hooks/pre-commit <<'EOF'\nclaude -p hi\nEOF",
    ],
)
def test_pm_still_cannot_start_claude_from_those_lines(run: Run, store: Path, command: str) -> None:
    proc = run(bash(command), role="pm")
    assert proc.returncode == 2
    assert "cannot start claude sessions" in proc.stderr


PYTHON_READS_CLAUDE = (
    "python3 - <<'EOF'\nimport json\nraw = json.load(open('tiers.json'))\n"
    'print(raw["tiers"]["deep"]["claude"])\nEOF'
)


@pytest.mark.parametrize(
    "command",
    [
        "echo 'claude -p hi' | sh",
        "echo 'claude -p hi' | sh -s",
        "echo 'claude -p hi' | bash -",
        "echo 'claude -p hi' | zsh",
        "echo 'claude -p hi' | ksh",
        "echo 'claude -p hi' |& sh",
        "echo 'claude -p hi' | (sh)",
        "echo 'claude -p hi' | sudo -u me bash",
        "printf 'cl''aude -p hi' | sh",
        "echo 'claude -p hi' > >(sh)",
        "sh <<< 'claude -p hi'",
        "sh <(echo claude -p hi)",
        "bash < <(echo claude -p hi)",
        "source <(echo claude)",
        "echo claude | . /dev/stdin",
        "echo claude | xargs env",
        "echo claude | xargs -I{} sh -c {}",
        "echo claude | xargs nohup",
        "echo 'claude -p hi' | parallel",
        PYTHON_READS_CLAUDE.replace("<<'EOF'", "<<'EOF' | sh"),
    ],
)
def test_pm_cannot_feed_claude_to_a_shell(run: Run, store: Path, command: str) -> None:
    proc = run(bash(command), role="pm")
    assert proc.returncode == 2
    assert "cannot start claude sessions" in proc.stderr
    assert run(bash(command)).returncode == 0


@pytest.mark.parametrize(
    "command",
    [
        "rg -l 'claude' | xargs sed -n 1p",
        PYTHON_READS_CLAUDE,
        # `.claude` is not claude
        "ls ~/.claude | sh",
        "echo claude | grep -c sh",
        "echo claude | xargs",
        "sh -c 'echo hi' <<< claude",
        "bash x.sh <<< claude",
        "echo claude; sh -s",
    ],
)
def test_pm_may_feed_a_shell_without_claude(run: Run, store: Path, command: str) -> None:
    proc = run(bash(command), role="pm")
    assert proc.returncode == 0, proc.stderr


# --- non-literal hosts ---

EDGE_GUARD = {
    "targets": [
        {
            "system": "edge",
            "kind": "production",
            "match": ["192\\.0\\.2\\.10", "192\\.0\\.2\\.20"],
        }
    ]
}


@pytest.fixture
def edge_store() -> Path:
    path = make_store(home() / "coven-memory", guard=EDGE_GUARD)
    configure(path)
    return path


NON_LITERAL = [  # refused for a pm
    "for h in $(cat hosts); do ssh $h uptime; done",
    "while read h; do ssh $h uptime; done < hosts",
    "ssh $(cat host)",
    "ssh `cat host`",
    "ssh ${hosts[0]} uptime",
    "ssh $H uptime",
    "h=$(cat f); ssh $h",
    "h=192.0.2.10 ssh $h uptime",
    "cat hosts | xargs -I{} ssh {} uptime",
    "cat hosts | xargs ssh",
    "xargs -n1 ssh < hosts",
    "parallel ssh {} uptime ::: 192.0.2.10",
    "sh -c 'for h in $(cat f); do ssh $h; done'",
    'bash -c "ssh $H"',
    "timeout 5 ssh $H",
    "env -i ssh $H",
    "sudo -u me ssh $H",
    "nice -n 5 ssh $H",
    "nohup ssh $H &",
    "setsid -f ssh $H",
    "command ssh $H",
    "exec ssh $H",
    'scp f "$H":/tmp',
    'rsync -a src "$H":/dst',
    "sftp $H",
    "ssh -J $J 192.0.2.10",
    "ssh -o ProxyJump=$J 192.0.2.10",
    "nc $H 22",
    "f() { ssh $1; }; f 192.0.2.10",
    "eval ssh $H",
]
CONTROLS = [  # pass for a pm
    'nc -z localhost "$port"',
    'rsync -a "$src" "$dst"',
    "ssh -p 2222 127.0.0.1 'echo $HOME'",
    "rsync -a a/ b/",
    "for h in localhost 127.0.0.1; do ssh $h true; done",
    "h=localhost; ssh $h true",
    "nc -l $port",
    'git commit -m "ssh $H"',
    'git commit -m "$(date) notes on ssh keys"',
]
LITERAL_EDGE = [  # the GM's shapes: refused for every session by the bare-host target
    "for h in 192.0.2.10 192.0.2.20; do ssh $h uptime; done",
    "h=192.0.2.20; ssh $h uptime",
    "sh -c 'for h in 192.0.2.10; do ssh $h; done'",
    "ssh 192.0.2.10 uptime",
]
# Twins of the controls and of the brief's shapes: a binding that may not hold where ssh runs, a
# host in a glued option or after a user, a remote CLI fed to a shell, shells nested too deep.
NON_LITERAL_TWINS = [
    # a quoted command string given to a wrapper
    "cat hosts | parallel 'ssh {} uptime'",
    'parallel "ssh {} uptime" < hosts',
    'watch -n5 "ssh $H uptime"',
    "watch 'ssh $H'",
    "env -S 'ssh ${H} uptime'",
    # env -S splits its value into a command, also glued or in a cluster
    'env -S"ssh $H"',
    "env -S'ssh $H'",
    "env -iS'ssh $H'",
    "env --split-string='ssh $H'",
    'env --split-string="ssh $H"',
    # parallel runs each argument after ::: as a command
    'parallel ::: "ssh $H1 uptime" "ssh $H2 uptime"',
    'parallel ::: "ssh $H"',
    'parallel -j2 ::: "ssh $H uptime"',
    # ssh settings that run a command
    "ssh -o ProxyCommand='ssh -W %h:%p $J' 127.0.0.1",
    "ssh -o 'ProxyCommand=nc $H 22' x",
    "scp -o 'ProxyCommand=nc $H 22' f x:/tmp",
    "sftp -o 'ProxyCommand=nc $H 22' x",
    "ssh -o PermitLocalCommand=yes -o 'LocalCommand=ssh $H' x",
    'ssh -o "KnownHostsCommand=ssh $H cat k" x',
    "ssh -o KnownHostsCommand='nc $H 22' x",
    'sftp -D "ssh $H /usr/lib/openssh/sftp-server"',
    "h=localhost; read h; ssh $h",
    "read h < f; false && h=localhost; ssh $h",
    "h=localhost; h+=$x; ssh $h",
    "if true; then h=localhost; fi; ssh $h",
    "h=localhost; for h in a $x; do :; done; ssh $h",
    "for h; do ssh $h; done",
    "for h in localhost; do h=$x; done; ssh $h",
    "h=localhost & ssh $h",
    "h=localhost | true; ssh $h",
    "(h=localhost); ssh $h",
    "y=localhost; h=x read y < f; ssh $y",
    'h=localhost; printf -v h %s "$x"; ssh $h',
    'h=localhost; printf -vh %s "$X"; ssh $h',
    "h=localhost; read -rah < f; ssh $h",
    "h=localhost; read h[0] < f; ssh $h",
    'h=localhost; printf -v h[0] %s "$X"; ssh $h',
    # zsh's print -v sets its name too
    'h=localhost; print -v h "$X"; ssh $h uptime',
    'h=localhost; print -vh "$X"; ssh $h uptime',
    # an array assignment binds its name to words the guard does not read
    'args=(-p 22 "$X"); ssh $args uptime',
    "args+=(x); ssh ${args[@]} uptime",
    "args=(x); ssh $args[1] uptime",
    "h=localhost; h=(x); ssh $h uptime",
    "h=localhost; h+=(x); ssh $h uptime",
    # a launcher runs the program it names
    'uv run psql "$DATABASE_URL"',
    "poetry run ssh $H uptime",
    'pdm run curl "http://$H/x"',
    "conda run -n base ssh $H uptime",
    "direnv exec . ssh $H uptime",
    "mise exec -- ssh $H uptime",
    "uvx --from x ssh $H uptime",
    "sh -c 'uv run ssh $H uptime'",
    # options between a launcher and its run word
    "poetry --directory dir run ssh $H uptime",
    'uv --project p run psql "$DATABASE_URL"',
    'pdm -p dir run curl "http://$H/x"',
    "nix shell nixpkgs#openssh -c ssh $H uptime",
    "nix develop .#ops --command ssh $H uptime",
    "mise exec node@20 -- ssh $H uptime",
    # dash keeps a prefix assignment on a special builtin
    "sh -c 'h=localhost; h=$X :; ssh $h'",
    "h=localhost; h=$X :; ssh $h",
    # an alias runs its value as a command
    "alias s='ssh $H'\ns",
    "alias a=ls s='ssh $H'\ns",
    "alias s='TERM=xterm ssh $H'\ns",
    "alias s='LC_ALL=C sudo ssh $H'\ns",
    "builtin alias s='ssh $H'\ns",
    "command alias s='ssh $H'\ns",
    "BASH_ALIASES[s]='ssh $H'\ns",
    "{ ssh $H; }",
    "( ssh $H )",
    "tmux new -d 'ssh $H'",
    "su -c 'ssh $H' u",
    'script -qc "ssh $H" /dev/null',
    'read src < f; rsync -a "$src" /d',
    "rsync -a src/ $H:/dst",
    "rsync -a src/ rsync://$H/mod",
    "ssh u@$H",
    'ssh "$H"',
    "ssh -J$J 192.0.2.10",
    "ssh -oProxyJump=$J 192.0.2.10",
    "ssh -vJ $J 192.0.2.10",
    "ssh -W $H:22 192.0.2.10",
    'ssh -o "hostname=$H" x',
    "scp -J $J f 192.0.2.10:/tmp",
    "sftp -J $J 192.0.2.10",
    "nc -x $P 192.0.2.10 22",
    "nohup >out ssh $H",
    "git commit -m x; ssh $H",
    'echo "ssh $H" | sh',
    "cat hosts | xargs -I{} sh -c 'ssh {}'",
    'sh -c "sh -c \'sh -c \\"sh -c ls\\"\'"',
]
LITERAL_CONTROLS = [  # pass for a pm: the host is written out, or there is none
    "ssh $U@host.example.com true",
    'rsync -a "$src/" "$dst"',
    'h=localhost; ssh "$h" true',
    "h=localhost && ssh $h true",
    'h=localhost; print -v x "$X"; ssh $h true',
    "h=localhost; args=(-v); ssh $h true",
    "uv run psql -h db1 -c x",
    "poetry -C dir run pytest",
    'uv run pytest tests/test_ssh.py -k "$K"',
    "python3 tools/report.py ssh $H",
    "h=localhost\nssh $h true",
    "tmux new -d 'ssh 127.0.0.1 true'",
    "scp f 127.0.0.1:/tmp",
    "ssh -o ConnectTimeout=$T 127.0.0.1 true",
    "command -v ssh",
    "nc -U /tmp/s",
    "echo ssh | grep -c ssh",
    "ssh user@build-1 uptime",
    "scp f host:/tmp/",
    "rsync -av d/ host:d/",
    'ssh host "echo $HOME"',
    'scp "$f" host:/tmp/',
    "ssh -p $PORT host",
    "ssh -o ProxyCommand='ssh -W %h:%p bastion' host",
    "ssh -o ProxyJump=bastion host",
    "alias ll='ls -l'\nll",
]


@pytest.mark.parametrize("command", NON_LITERAL + NON_LITERAL_TWINS)
def test_pm_cannot_take_a_host_from_a_variable(run: Run, store: Path, command: str) -> None:
    proc = run(bash(command), role="pm")
    assert proc.returncode == 2
    assert "a host that is not written out" in proc.stderr
    assert run(bash(command)).returncode == 0  # (e) is for pm sessions


@pytest.mark.parametrize("command", CONTROLS + LITERAL_CONTROLS)
def test_pm_literal_hosts_and_controls_pass(run: Run, store: Path, command: str) -> None:
    proc = run(bash(command), role="pm")
    assert proc.returncode == 0, proc.stderr


@pytest.mark.parametrize("command", LITERAL_EDGE)
@pytest.mark.parametrize("role", [None, "pm"], ids=["gm", "pm"])
def test_literal_hosts_reach_the_bare_host_target(
    run: Run, edge_store: Path, command: str, role: str | None
) -> None:
    proc = run(bash(command), role=role)
    assert proc.returncode == 2
    assert "'edge' is production" in proc.stderr


def test_an_appended_host_is_not_written_out(run: Run, edge_store: Path) -> None:
    """`h+=` builds a host no target pattern sees whole: the name is no longer literal."""
    command = "h=192.0.2.; h+=10; ssh $h uptime"
    proc = run(bash(command), role="pm")
    assert proc.returncode == 2
    assert "a host that is not written out" in proc.stderr
    assert run(bash(command)).returncode == 0  # (e) is for pm sessions


def test_an_approval_does_not_lift_the_host_rule(run: Run, store: Path) -> None:
    """No approval lifts a restricted rule: the host is written out, or the command is refused."""
    approve(store, commands=["ssh $H uptime"])
    proc = run(bash("ssh $H uptime"), role="pm")
    assert proc.returncode == 2
    assert HOST_REFUSAL in proc.stderr
    assert run(bash("ssh $H uptime")).returncode == 0


def test_host_rule_names_the_cli_and_the_reason(run: Run, store: Path) -> None:
    proc = run(bash("cat hosts | xargs ssh"), role="pm")
    assert proc.stderr.strip() == (
        "coven prod-guard: a 'pm' session cannot run ssh with a host that is not written out"
        " in the command (xargs/parallel supplies its arguments); write the host literally"
    )


@pytest.mark.parametrize("command", ["ssh host <<X\nuptime\nX", "ssh host <<-X\n\tuptime\n\tX"])
def test_an_unquoted_heredoc_names_the_fix(run: Run, store: Path, command: str) -> None:
    proc = run(bash(command), role="pm")
    assert proc.returncode == 2
    assert "a host that is not written out" in proc.stderr
    assert "quote the heredoc delimiter" in proc.stderr
    assert run(bash(command)).returncode == 0  # (e) is for pm sessions


def test_a_heredoc_with_another_blocker_gives_the_generic_reason(run: Run, store: Path) -> None:
    """Quoting the delimiter would not let the scanner follow this line: `$(...)` still blocks."""
    command = "cat <<EOF\nx\nEOF\nssh $(hostfor x)"
    proc = run(bash(command), role="pm")
    assert proc.returncode == 2
    assert "a host that is not written out" in proc.stderr
    assert "cannot tell where its host comes from" in proc.stderr
    assert "quote the heredoc delimiter" not in proc.stderr
    assert run(bash(command)).returncode == 0  # (e) is for pm sessions


def test_any_restricted_role_cannot_take_a_host_from_a_variable(run: Run, store: Path) -> None:
    proc = run(bash("ssh $H uptime"), role="")
    assert proc.returncode == 2
    assert "a host that is not written out" in proc.stderr


DB_NON_LITERAL = [
    'psql "$DATABASE_URL"',
    "psql -h $H -c 'select 1'",
    "psql --host=$H",
    "PGHOST=$H psql",
    "PGHOST=$(cat f); psql -c x",
    'pg_dump -d "$URL" -f out.sql',
    "pg_restore -h $H -d app x.dump",
    "mysql -h $H app",
    'mysql "mysql://u@$H/app"',
    "sqlcmd -S $H -Q 'select 1'",
    "SQLCMDSERVER=$H sqlcmd",
    'curl "$URL"',
    'curl -s "http://$H/x"',
    "curl -K hosts.cfg",
    "curl --config=hosts.cfg",
    "curl -x $P https://example.com",
    "wget -i urls.txt",
    "wget $URL",
    "wget -e http_proxy=$P https://example.com",
]
DB_CONTROLS = [
    "until curl -sf localhost:8000/health; do sleep 1; done",
    'DATABASE_URL=postgres://localhost:5432/app; psql "$DATABASE_URL"',
    "psql -h localhost -c \"select * from t where a = '$x'\"",
    "psql -h /var/run/postgresql app",
    "psql -c 'select 1'",
    "mysql -h 127.0.0.1 -p app",
    'curl -s "http://localhost:8000/$path"',
    "wget -q -O - http://127.0.0.1:8000/health",
]
# Twins of the brief's shapes: each tool's own flags, which must not hide the host after them;
# option words in quotes, glued, long or abbreviated; a URL's host behind a user, a password or a
# `#`; curl's URL globs; host variables set in the head, on the line or through input.
DB_TWINS = [
    # flags of one tool that another tool reads as taking a value
    'psql -t "$URL"',
    'psql -At "$URL" -c x',
    'pg_dump -c "$URL"',
    'pg_dump -v "$URL"',
    'pg_restore -c -d "$URL" x.dump',
    "mysql -pS -h $H app",
    # an option word in quotes is still an option
    'mysql "--host=$H" app',
    'sqlcmd "-S$H" -Q x',
    'pg_restore "-h$H" -d app x.dump',
    'scp "-J$J" f host:/tmp',
    'env "A=1" ssh $H',
    'env "A=1" psql -h $H',
    # an option name that may expand may be any option, a host option too
    'mysql -"$O" app',
    'psql --"$O"',
    'ssh -"$O" 192.0.2.10',
    # curl's variables fill the URL of an --expand- option from the environment or a file
    'curl --variable %H --expand-url "https://{{H}}/"',
    "curl --variable %H --expand-url=https://{{H}}/",
    # glued, long and abbreviated forms
    "psql -h$H",
    'psql --host "$H"',
    "psql --ho=$H",
    "psql --dbname=$URL",
    "mysql --host=$H app",
    "mysql --ho $H app",
    "sqlcmd -S$H -Q x",
    'curl --url "$URL"',
    "curl --proxy=$P https://example.com",
    "curl --socks5-hostname $P https://example.com",
    'curl --connect-to "example.com:443:$H:443" https://example.com',
    'curl --resolve "example.com:443:$A" https://example.com',
    "curl -sK hosts.cfg https://example.com",
    "curl --config hosts.cfg",
    "curl --conf=hosts.cfg",
    "wget -qi urls.txt",
    "wget --input-file=urls.txt",
    'wget --execute "http_proxy=$P" https://example.com',
    'wget -B "$BASE" https://example.com/x',
    # a URL's scheme and authority (`scheme://[userinfo@]host[:port]`) must be written out:
    # nothing may expand in them, and nothing may hide where they end
    'curl "http://u:pw@$H/x"',
    'curl "http://$H#@example.com/"',
    'curl "http://$H?@example.com/"',
    'curl "http://example.com$H/"',
    'curl "http://example.com\\@$H/"',
    'curl "https://u:$PW@example.com/x"',
    'psql "postgresql://app:$PGPASSWORD@db1/app"',
    'curl "http://localhost:$PORT/health"',
    'curl "$S://example.com/"',
    'curl "http://u@$H/"',
    'curl "http://$H:8080/"',
    'curl "http://[::1]:$PORT/"',
    # a value in the userinfo or the port may carry `@host` and move the host; one the line binds
    # to a literal is read through its value, which must not move or build the host
    "PORT='1@orders-db'; curl \"http://localhost:$PORT/health\"",
    "U='x@orders-db/'; curl \"http://$U@example.com/\"",
    "U='http://pro-{db,x}/'; curl \"$U\"",
    'H=pro; curl "http://$H-db/"',
    'B=http://pro; curl "$B-db/x"',
    'H=example.com; curl "http://$H$X/"',
    "B='http://a b'; curl $B/x",
    'H=pro; psql "postgresql://$H-db/app"',
    'Q=x; psql "postgresql://db1/app?host=$Q"',
    'H=db1; psql "host=$H dbname=app"',
    # curl and wget read a URL with no scheme as http://, its authority the same way
    "curl localhost:$PORT/x",
    'curl "$U@example.com/x"',
    'wget "localhost:$PORT"',
    'psql "postgresql://app:pw@$H/app"',
    'psql "host=$H dbname=app"',
    # libpq reads a URL's host after a `?` or `#` too, and from its query: judged whole, like -d
    'psql "postgresql://x?y@$H/app"',
    'psql "postgresql://x#@$H/app"',
    'psql "postgres://localhost?@$H/app"',
    'psql "postgresql:///app?host=$H"',
    'psql "postgresql://localhost/app?host=$H"',
    'psql "postgresql://localhost/app?hostaddr=$A"',
    'psql "postgresql://localhost/app?service=$S"',
    'pg_dump "postgresql://localhost/app?host=$H"',
    'psql "postgresql://db1/$DB"',
    # curl expands {} and [] in a URL into hosts no target pattern sees whole
    'curl "http://192.0.2.1{0,1}/x"',
    'curl "http://example.[a-c]om/"',
    "curl 'http://[1-3].example.com/'",
    "curl 'http://[::g]/'",
    "curl 'http://[::1]{a,b}/'",
    "curl -g https://example.com; curl 'http://[1-3].x/'",
    'curl -g "http://$H/"',
    "curl 'http://u:{a,b}@[::1]/'",
    # host variables
    "PGHOST=$H; psql -c x",
    "PGHOSTADDR=$A pg_dump app",
    "env PGHOST=$H psql",
    'env "PGHOST=$H" psql',
    "sudo PGHOST=$H psql",
    "PGHOST=$H sh -c 'psql -c x'",
    "read PGHOST < f; psql -c x",
    "PGHOST=db1; PGHOST+=x; psql -c x",
    "PGHOST=db1; read PGHOST < f; psql",
    "for PGHOST in $HOSTS; do psql -c x; done",
    'DATABASE_URL=$U; psql "$DATABASE_URL"',
    "SQLCMDSERVER=$S; sqlcmd -Q x",
    "PGHOST=$H ssh 192.0.2.10 psql",
    "MYSQL_HOST=$H mysql app",
    "MYSQL_HOST=$H; mysql app",
    "https_proxy=$P curl https://example.com",
    "https_proxy=$P; curl https://example.com",
    "ALL_PROXY=$P wget https://example.com",
    "PGSERVICE=$S psql",
    # more host options, files of hosts, host variables and routes to them
    'curl --doh-url "$D" https://example.com',
    'curl --dns-servers "$N" https://example.com',
    'mysql --dns-srv-name "$N" app',
    "wget --input-metalink=x.metalink",
    "ftp_proxy=$P curl ftp://example.com/x",
    "FTP_PROXY=$P; wget ftp://example.com/x",
    "SQLCMDINI=$F sqlcmd -S db1 -Q x",
    "PGSYSCONFDIR=$D psql -h db1",
    # a connect meta-command picks a new host: none with a value that may expand
    'psql -h localhost -c "\\connect $DATABASE_URL"',
    'psql -h localhost -c "\\c - - $H"',
    "psql -h localhost -v h=$H -c '\\c - - :h'",
    'psql -h localhost --command="\\c - - $H"',
    'psql -h localhost -c"\\c - - $H"',
    'echo "\\c - - $H" | psql -h localhost',
    "psql -h localhost -v h=$H <<'EOF'\n\\c - - :h\nEOF",
    'mysql -h 127.0.0.1 -e "connect app $H"',
    'mysql -h 127.0.0.1 --execute "connect app $H"',
    'mysql -h 127.0.0.1 <<< "connect app $H"',
    'sqlcmd -S localhost -Q ":connect $H"',
    'sqlcmd -S localhost -q ":CONNECT $H"',
    # wrappers, input runners and shells
    'timeout 5 curl "$URL"',
    "xargs curl < urls.txt",
    "sh -c 'curl $U'",
    'echo "curl $U" | sh',
]
DB_LITERAL_CONTROLS = [
    "psql -h db1 -d app -c 'select 1'",
    "curl https://example.com/x",
    'curl -H "Authorization: Bearer $T" https://api.example.com',
    "wget -O out https://example.com/f",
    "mysql -h db1 -u u -p app",
    "PGHOST=db1 psql",
    "PGHOST=db1 psql -c a; PGHOST=db1 psql -c b",
    "PGHOST=localhost; psql -c x",
    "MYSQL_HOST=db1 mysql app",
    "https_proxy=http://proxy.example.com:3128 curl https://example.com",
    'psql -v "x=$X" -h db1 app',
    'pg_dump -f "$OUT" -h db1 app',
    'pg_restore -h db1 -d app "$DUMP"',
    'mysql -p"$PW" -h db1 -e "$SQL" app',
    'sqlcmd -S db1 -U "$U" -Q "$Q"',
    'curl -sS -o "$OUT" -d "$BODY" --header="X-Token: $T" https://example.com/api',
    # past the authority (path, query, fragment) a value may be unknown
    'curl "https://example.com?q=$Q"',
    'curl "https://example.com/items/$ID"',
    'wget "https://example.com/$F"',
    'curl "https://example.com#$F"',
    'curl -s "https://u:pw@example.com:8443/x?a=$A"',
    'URL=https://example.com/x; curl "$URL"',
    "curl localhost:8080/x/$ID",
    "curl example.com/$P",
    # an IPv6 literal in brackets is a host, not a URL glob
    "curl http://[::1]:8080/",
    "curl 'http://[2001:db8::1]/'",
    'curl "http://[::1]:8080/$p"',
    'wget -q -O "$OUT" --header "X: $T" https://example.com/f',
    "git commit -m 'psql -h $H'",
    "echo curl | grep -c curl",
    "SQLCMDINI=init.sql sqlcmd -S db1 -Q x",
    "PGSYSCONFDIR=/etc/postgresql-common psql -h db1",
    "curl --dns-servers 192.0.2.53 https://example.com",
    'curl --proxy-header "X: $T" https://example.com',
    'curl --request-target "/$P" https://example.com',
    # -g and --globoff turn curl's URL globs off for that command
    "curl -g 'http://[1-3].x/'",
    "curl --globoff 'http://[1-3].x/'",
    "curl -sg 'http://{a,b}.example.com/'",
    # a name the line binds to a literal counts through its value in a URL
    'BASE=http://localhost:8000; curl -s "$BASE/health"',
    'BASE=https://example.com; curl "$BASE/items"',
    'BASE=https://example.com; wget "$BASE/f"',
    'HOST=example.com; curl "https://$HOST/x"',
    'H=db1; psql "postgresql://$H/app"',
    'PW=secret; curl "https://u:$PW@example.com/x"',
    "BASE=http://localhost:8000; curl -s $BASE/health",
    # a command value that may expand is fine without a connect meta-command
    'psql -h db1 -c "select $X"',
    "psql -h db1 -c '\\c app'",
    'psql -h db1 -c "\\copy t to $F"',
    'echo "select $X" | psql -h db1',
    "mysql -h db1 -e \"select '$X'\"",
    'sqlcmd -S db1 -Q "select $X"',
]


@pytest.mark.parametrize("command", DB_NON_LITERAL)
def test_pm_database_and_http_hosts_from_variables_are_refused(
    run: Run, store: Path, command: str
) -> None:
    proc = run(bash(command), role="pm")
    assert proc.returncode == 2
    assert "a host that is not written out" in proc.stderr


@pytest.mark.parametrize("command", DB_CONTROLS)
def test_pm_database_and_http_controls_pass(
    run: Run, store: Path, tmp_path: Path, command: str
) -> None:
    # From /tmp, the `*` in a SQL text is a glob that can reach a store under /tmp (the store rule).
    proc = run(bash(command, cwd=str(tmp_path / "project")), role="pm")
    assert proc.returncode == 0, proc.stderr


@pytest.mark.parametrize("role", [None, "pm"], ids=["gm", "pm"])
def test_literal_database_url_reaches_the_bare_host_target(
    run: Run, edge_store: Path, role: str | None
) -> None:
    proc = run(bash('DATABASE_URL=postgres://192.0.2.10/app; psql "$DATABASE_URL"'), role=role)
    assert proc.returncode == 2
    assert "'edge' is production" in proc.stderr


@pytest.mark.parametrize("command", DB_TWINS)
def test_pm_database_and_http_twins_are_refused(run: Run, store: Path, command: str) -> None:
    proc = run(bash(command), role="pm")
    assert proc.returncode == 2
    assert "a host that is not written out" in proc.stderr


@pytest.mark.parametrize("command", DB_NON_LITERAL + DB_TWINS)
def test_gm_runs_database_and_http_shapes(run: Run, store: Path, command: str) -> None:
    proc = run(bash(command))
    assert proc.returncode == 0, proc.stderr  # (e) is for pm sessions


@pytest.mark.parametrize("command", DB_LITERAL_CONTROLS)
def test_pm_database_and_http_literal_hosts_pass(run: Run, store: Path, command: str) -> None:
    proc = run(bash(command), role="pm")
    assert proc.returncode == 0, proc.stderr


@pytest.mark.parametrize(
    ("command", "why"),
    [
        ("curl -K hosts.cfg", "curl with a host that is not written out in the command (-K reads"),
        ("wget --input-file=u", "wget with a host that is not written out in the command (--input"),
        ("PGHOST=$H psql", "psql with a host that is not written out in the command (PGHOST is"),
    ],
)
def test_database_and_http_refusals_name_the_reason(
    run: Run, store: Path, command: str, why: str
) -> None:
    proc = run(bash(command), role="pm")
    assert why in proc.stderr


@pytest.mark.parametrize(
    "command",
    [
        'TOKEN=$(cat t); curl -H "Authorization: Bearer $TOKEN" https://example.com',
        'psql "$DB"',
        'curl "http://localhost:$PORT/health"',
        'curl "https://u:$PW@example.com/x"',
    ],
)
def test_database_and_http_refusals_name_the_way_out(run: Run, store: Path, command: str) -> None:
    """The spec's accepted cost: a line the scanner cannot follow, or a database named through a
    variable, is refused; the message names the way out."""
    proc = run(bash(command), role="pm")
    assert proc.returncode == 2
    assert "write the host literally" in proc.stderr


@pytest.mark.parametrize(
    "command",
    [
        'psql "postgres://192.0.2.10/app"',
        "curl http://192.0.2.10/x",
        "for h in 192.0.2.10; do psql -h $h; done",
    ],
)
def test_literal_database_and_http_hosts_reach_the_bare_host_target(
    run: Run, edge_store: Path, command: str
) -> None:
    for role in (None, "pm"):
        proc = run(bash(command), role=role)
        assert proc.returncode == 2
    assert "'edge' is production" in proc.stderr


@pytest.mark.parametrize(
    "command",
    [
        "cd docs && ls h*",
        "cd build && rm -rf *",
        "cd src && ls src/*",
        "cd x && ls */",
        "cd src && rm -rf */node_modules",
        "cd src && ls */*.py",
        "cd src && pytest */test_x.py",
    ],
)
def test_pm_may_glob_after_cd_when_only_a_parent_of_the_store_matches(
    run: Run, store: Path, command: str
) -> None:
    proc = run(bash(command, cwd=str(project_dir())), role="pm")
    assert proc.returncode == 0, proc.stderr


# A regex in a quoted-delimiter heredoc body that a non-shell reads is data, not a relative glob.
# (No `r` prefix: `r"[A-Za-z]..."` glues an `r` to the word and the glob no longer fits any name.)
BODY_REGEXES = {
    "class-then-star": 'NAME = re.compile("[A-Za-z][A-Za-z_]*")',
    "backslash-w-star": 'WORD = re.compile("\\\\w*")',
    "class-star-slash-class-plus": 'KEY = re.compile("[a-z_]*/[0-9]+")',
}


def heredoc(command: str, body: str) -> str:
    return f"{command} <<'EOF'\nimport re\n{body}\nEOF"


@pytest.mark.parametrize("body", BODY_REGEXES.values(), ids=BODY_REGEXES)
@pytest.mark.parametrize(
    "command",
    [
        "cd src && python3 -",
        # The `cd` is inside the quoted remote command, as in a real remote run.
        "ssh user@host.example.com 'cd /srv/app && timeout 1500 python -B -' > /tmp/out.txt",
        "cd src && /opt/venv/bin/python3.12 -",
        "ssh user@host.example.com 'cd /srv/app && python -'",
    ],
    ids=["cd-then-python", "ssh-cd-in-remote-command", "python-by-path", "ssh-without-redirect"],
)
def test_pm_may_run_an_interpreter_whose_heredoc_body_holds_a_regex(
    run: Run, tmp_path: Path, store: Path, command: str, body: str
) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    proc = run(bash(heredoc(command, body), cwd=str(project)), role="pm")
    assert proc.returncode == 0, proc.stderr


def test_pm_may_run_interpreters_whose_heredoc_bodies_hold_regexes_after_tabs_and_in_pairs(
    run: Run, tmp_path: Path, store: Path
) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    tabbed = "cd src && python3 - <<-'EOF'\n\tx = re.compile(\"[A-Za-z_]*\")\n\tEOF"
    pair = (
        "cd src && python3 - <<'A' && ssh user@host.example.com 'cd /srv/app' <<'B'\n"
        'x = re.compile("[A-Za-z_]*")\nA\ny = re.compile("\\\\w*")\nB'
    )
    for command in (tabbed, pair):
        proc = run(bash(command, cwd=str(project)), role="pm")
        assert proc.returncode == 0, proc.stderr


GLOB_BODIES = {
    "star-in-name": 'glob.glob("c*-memory")',
    "class-star-then-store-entry": 'glob.glob("[a-z]*/systems/guard.json")',
    "the-store-name": 'open("coven-memory/systems/guard.json")',
    "quotes-split-a-literal": "glob.glob(\"co''ven-m*\")",
    "backslash-in-a-literal": 'glob.glob("c\\oven-m*")',
    "regex-escape-then-store-entry": 'glob.glob("\\w*/systems/guard.json")',
    # A class of one character is that character: it is a literal, not a loose component.
    "one-character-class-then-star": 'glob.glob("[c]*")',
    "the-name-in-one-character-classes": 'glob.glob("[c][o][v][e][n]-[m][e][m][o][r][y]")',
}


@pytest.mark.parametrize("body", GLOB_BODIES.values(), ids=GLOB_BODIES)
def test_pm_cannot_glob_to_the_store_from_an_interpreter_body(
    run: Run, tmp_path: Path, store: Path, body: str
) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    command = heredoc("cd ~ && python3 -", body)
    proc = run(bash(command, cwd=str(project)), role="pm")
    assert proc.returncode == 2
    assert STORE_REFUSAL in proc.stderr


@pytest.mark.parametrize(
    "command",
    [
        "cd ~ && rm -rf [a-z]*",
        "cd ~ && ls c*",
        "bash -c 'cd ~ && rm -rf [a-z]*'",
    ],
    ids=["class-star", "letter-star", "shell-command-string"],
)
def test_pm_cannot_glob_to_the_store_in_shell_position(
    run: Run, tmp_path: Path, store: Path, command: str
) -> None:
    proc = run(bash(command, cwd=str(tmp_path / "proj")), role="pm")
    assert proc.returncode == 2
    assert STORE_REFUSAL in proc.stderr


@pytest.mark.parametrize(
    "reader",
    [
        "bash",
        "sudo bash",
        "env bash",
        "/bin/sh",
        "'ba''sh'",
        "source /dev/stdin",
        "sudo -s",
        "$SHELL",
    ],
    ids=[
        "bash",
        "sudo-bash",
        "env-bash",
        "sh-path",
        "split-quotes",
        "source-stdin",
        "sudo-shell",
        "unknown-word",
    ],
)
def test_pm_cannot_glob_to_the_store_from_a_body_a_shell_reads(
    run: Run, tmp_path: Path, store: Path, reader: str
) -> None:
    command = f"cd ~ && {reader} <<'EOF'\nrm -rf [a-z]*\nEOF"
    proc = run(bash(command, cwd=str(tmp_path / "proj")), role="pm")
    assert proc.returncode == 2
    assert STORE_REFUSAL in proc.stderr


UNLISTED_READERS = {
    "ash": "cd ~ && ash <<'EOF'\ncat co''ven-m*/systems/guard.json\nEOF",
    "rbash": "cd ~ && rbash <<'EOF'\nrm -rf [a-z]*\nEOF",
    "bash5": "cd ~ && bash5 <<'EOF'\nrm -rf [a-z]*\nEOF",
    "read-then-use": "cd ~ && read -r p <<'EOF'\n\\c*/systems/guard.json\nEOF\ncat $p",
    "while-read-loop": "cd ~ && while read c; do $c; done <<'EOF'\nrm -rf [a-z]*\nEOF",
    "brace-group": "cd ~ && { ash; } <<'EOF'\nrm -rf [a-z]*\nEOF",
    "nice-python": "cd ~ && nice python3 - <<'EOF'\nrm -rf [a-z]*\nEOF",
    "env-python": "cd ~ && env python3 - <<'EOF'\nrm -rf [a-z]*\nEOF",
    "assignment-then-python": "cd ~ && X=1 python3 - <<'EOF'\nrm -rf [a-z]*\nEOF",
    "unknown-name": "cd ~ && frobnicate <<'EOF'\nrm -rf [a-z]*\nEOF",
    "no-command": "cd ~ && <<'EOF'\nrm -rf [a-z]*\nEOF",
    "python-then-unlisted": "cd ~ && python3 - <<'A' && ash <<'B'\nimport re\nA\nrm -rf [a-z]*\nB",
    "unlisted-then-python": "cd ~ && ash <<'A' && python3 - <<'B'\nrm -rf [a-z]*\nA\nimport re\nB",
    "tabbed-shell-body": "cd ~ && ash <<-'EOF'\n\trm -rf [a-z]*\n\tEOF",
}


@pytest.mark.parametrize("command", UNLISTED_READERS.values(), ids=UNLISTED_READERS)
def test_pm_cannot_glob_to_the_store_from_a_body_read_by_an_unlisted_command(
    run: Run, tmp_path: Path, store: Path, command: str
) -> None:
    """Only a few interpreters (python, node, perl, ruby, ssh) get the lenient reading. Anything
    else that reads a body, or that the guard cannot name, is read strictly."""
    proc = run(bash(command, cwd=str(tmp_path / "proj")), role="pm")
    assert proc.returncode == 2
    assert STORE_REFUSAL in proc.stderr


def test_pm_cannot_glob_to_the_store_from_a_tabbed_interpreter_body(
    run: Run, tmp_path: Path, store: Path
) -> None:
    command = "cd ~ && python3 - <<-'EOF'\n\tglob.glob(\"c*-memory\")\n\tEOF"
    proc = run(bash(command, cwd=str(tmp_path / "proj")), role="pm")
    assert proc.returncode == 2
    assert STORE_REFUSAL in proc.stderr


@pytest.mark.parametrize(
    "command",
    [
        "cd ~ && python3 - <<EOF\nprint(1)\n[a-z]*\nEOF",
        # A piped body may be run by the next command.
        "cd ~ && cat <<'EOF' | python3 -\nrm -rf [a-z]*\nEOF",
    ],
    ids=["unquoted-delimiter", "piped"],
)
def test_pm_cannot_glob_to_the_store_from_a_body_the_guard_cannot_follow(
    run: Run, tmp_path: Path, store: Path, command: str
) -> None:
    proc = run(bash(command, cwd=str(tmp_path / "proj")), role="pm")
    assert proc.returncode == 2
    assert STORE_REFUSAL in proc.stderr


def test_pm_cannot_glob_to_the_store_from_a_python_c_string_after_cd(
    run: Run, tmp_path: Path, store: Path
) -> None:
    """The narrower reading is for heredoc bodies only: a quoted `python3 -c` string is a word of
    the command, so a regex in it stays a relative glob after a `cd`."""
    command = "cd src && python3 -c 'import re; re.compile(\"[A-Za-z][A-Za-z_]*\")'"
    proc = run(bash(command, cwd=str(tmp_path / "proj")), role="pm")
    assert proc.returncode == 2
    assert STORE_REFUSAL in proc.stderr


# A regex in a grep pattern, or in a body copied to a file, is not a relative glob either.
PASSING_REGEX_LINES = {
    "grep-pattern-in-a-pipe-after-sed": (
        'cd src && sed -n 1,5p a.js; grep -n "^function \\|^const [A-Za-z]* = " a.js | head -5'
    ),
    "grep-e-after-sed": "cd src && sed -n 1,5p a.js; grep -e '[A-Za-z]*' file",
    "grep-pattern-then-a-python-heredoc": (
        "cd src && grep -n '[A-Za-z]*' a.js && python3 - <<'EOF'\nimport re\nEOF"
    ),
    "cat-body-to-an-absolute-target-then-run": (
        "cd /tmp && cat > /tmp/probe.py <<'EOF'\n"
        'P = "[A-Za-z][A-Za-z_]*"\n'
        "EOF\n"
        "python3 -I /tmp/probe.py"
    ),
    "cat-body-to-a-relative-target-then-run": (
        "cd /tmp && cat > probe.py <<'EOF'\nP = \"[A-Za-z][A-Za-z_]*\"\nEOF\npython3 -I probe.py"
    ),
    "tee-body-to-a-relative-target-then-run": (
        "cd /tmp && tee probe.py <<'EOF'\nP = \"[A-Za-z][A-Za-z_]*\"\nEOF\npython3 -I probe.py"
    ),
}


@pytest.mark.parametrize("command", PASSING_REGEX_LINES.values(), ids=PASSING_REGEX_LINES)
def test_pm_may_run_a_line_whose_regex_is_a_grep_pattern_or_a_copied_body(
    run: Run, tmp_path: Path, store: Path, command: str
) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    proc = run(bash(command, cwd=str(project)), role="pm")
    assert proc.returncode == 0, proc.stderr


REFUSED_GLOB_LINES = {
    "grep-file-operand": "cd ~ && grep -n x c*/systems/a.md",
    "grep-unquoted-glob-as-pattern": "cd ~ && grep c* a.txt",
    "grep-substitution-in-pattern": 'cd ~ && grep "$(rm -rf c*)" a.txt',
    # On a line where something is not inert, `code_text` blanks nothing: the name is read.
    "grep-pattern-names-the-store": "cd ~ && sed -n 1p a; grep 'coven-memory' a.txt",
    "cat-body-with-a-literal": "cd ~ && cat > /tmp/x <<'EOF'\nrm -rf c*-memory\nEOF\npython3 -I /tmp/x",
    "tee-body-with-a-literal": "cd ~ && tee /tmp/x <<'EOF'\nrm -rf c*-memory\nEOF\npython3 -I /tmp/x",
    # `ash` is no SHELL_READERS name, so only the pipe rule reads the body whole.
    "cat-body-piped-to-an-unlisted-reader": "cd ~ && cat <<'EOF' | ash\n[a-z]*\nEOF",
    "cat-body-one-character-classes-then-star": (
        "cd ~ && cat > /tmp/x <<'EOF'\nrm -rf [c][o][v][e][n]*\nEOF\npython3 -I /tmp/x"
    ),
    "cat-body-the-name-in-one-character-classes": (
        "cd ~ && cat > /tmp/x <<'EOF'\nrm -rf [c][o][v][e][n]-[m][e][m][o][r][y]\nEOF\npython3 -I /tmp/x"
    ),
    "cat-body-then-a-shell-word": "cd ~ && cat > /tmp/x <<'EOF'\nrm -rf [a-z]*\nEOF\nsh /tmp/x",
    "glob-in-the-sink-target": "cd ~ && cat > c*/x <<'EOF'\nx\nEOF",
}


@pytest.mark.parametrize("command", REFUSED_GLOB_LINES.values(), ids=REFUSED_GLOB_LINES)
def test_pm_cannot_glob_to_the_store_past_a_grep_pattern_or_a_copied_body(
    run: Run, tmp_path: Path, store: Path, command: str
) -> None:
    proc = run(bash(command, cwd=str(tmp_path / "proj")), role="pm")
    assert proc.returncode == 2
    assert STORE_REFUSAL in proc.stderr


def test_an_mcp_string_with_a_regex_is_read_as_it_was(
    run: Run, tmp_path: Path, store: Path
) -> None:
    """The MCP path has no `cd` to wait for and no heredoc reading: a relative glob that can name
    the store's directory counts, in a body or not."""
    text = heredoc("python3 -", BODY_REGEXES["class-then-star"])
    payload = {"tool_name": "mcp__shell__exec", "tool_input": {"command": text}}
    proc = run(payload, role="pm")
    assert proc.returncode == 2
    assert STORE_REFUSAL in proc.stderr


UNTOLD_MESSAGE = (
    "coven prod-guard: a 'pm' session cannot run coven with a subcommand it cannot tell"
    " (it may be approve, setup or memory adopt|init|sync): it changes the memory store or"
    " coven's config; ask the GM"
)


WRITERS = [
    "coven setup",
    "coven memory adopt /srv/store --yes",
    "coven approve a1 --expires-in 1h",
    "env -i coven setup",
    "env X=y coven setup",
    "command coven setup",
    "exec coven setup",
    "nohup coven setup &",
    "setsid -f coven setup",
    "timeout 60 coven setup",
    "( coven setup & )",
    "sh -c 'coven setup'",
    "uv run coven setup",
    "python -m coven setup",
    "uv run python -m coven memory sync",
    ".venv/bin/coven setup",
    "/home/user/.local/bin/coven approve a1 --expires-in 1h",
    "ls; coven setup",
    "coven $(printf setup)",
    "coven `printf setup`",
    "g=coven; $g setup",
    "coven $s",
]
WRITERS_TOO = [
    "uvx coven setup",
    "pipx run coven setup",
    "bash -lc 'coven setup'",
    "xargs coven < f",
    "coven --verbose setup",
    "COVEN_X=1 coven setup",
    "uvx --from coven coven setup",
    "uvx coven@latest setup",
    "python3 -mcoven setup",
    "python3 src/coven setup",
    "python3 src/coven/__main__.py setup",
    "python3 -m coven.__main__ setup",
    "cov''en setup",
    "cov\\\nen setup",
    "echo a\\\\\ncoven setup",
    "sudo -E coven setup",
    "sudo -u me coven memory sync",
    "watch -n 5 'coven setup'",
    "tmux new-session -d 'coven setup'",
    "su -c 'coven setup' me",
    "env -S 'coven setup'",
    "f() { coven setup; }; f",
    "if true; then coven setup; fi",
    "ls && coven setup 2>&1 | tail -5",
    "coven 2>/dev/null setup",
    'coven "$(printf setup)"',
    "coven ${s}",
    "${g} setup",
    "$g --dry-run memory sync",
    "$(echo coven) setup",
    "`echo coven` setup",
    "xargs -I{} coven {} < f",
    "parallel coven ::: setup",
    "parallel ::: 'coven setup'",
    "echo 'coven setup' | sh",
    "sh <<< 'coven setup'",
    "sh <<'EOF'\ncoven setup\nEOF",
    "sh -c \"$(cat <<'EOF'\ncoven setup\nEOF\n)\"",
    "nice -n 5 coven setup",
    "uv tool run coven setup",
    "uv run --project . coven setup",
    "pipx run --spec coven coven memory sync",
    "python3.12 -m coven setup",
    "./coven setup",
    "coven -- setup",
    "while true; do coven setup; done",
    "coven doctor && coven setup",
    "bash -c 'uv run coven setup'",
    "sh -c 'python3 -m coven memory sync'",
]
READERS = [
    "coven doctor",
    "coven pm status",
    "coven guard check guard.json",
    "git commit -m 'run coven setup'",
    "grep -rn 'coven setup' docs",
    "cat src/coven/setup.py",
    "cd src/coven && ls",
    "uv run pytest tests/test_setup_cli.py",
]
READERS_TOO = [
    "coven guard explain ls",
    "coven doctor --online",
    "coven",
    "coven --help",
    "command -v coven",
    "which coven",
    "echo coven setup",
    "echo 'coven doctor' | sh",
    "git ls-files | xargs grep -l 'coven setup'",
    "uv run pytest tests/test_cli.py -k setup",
    "uv run ruff check src/coven",
    "uvx coven doctor",
    "python3 -m coven status",
    "cd ~/projects/coven && git status",
    "ls src/coven/setup.py src/coven/memory/store.py",
    "cat >> notes.md <<'EOF'\nrun coven setup in the GM\nEOF",
    "gh pr create --title t --body 'then coven setup'",
    "git commit -m \"$(cat <<'EOF'\ncoven setup now works\nEOF\n)\"",
]


@pytest.mark.parametrize("command", WRITERS + WRITERS_TOO)
def test_pm_cannot_run_store_writing_subcommands(run: Run, store: Path, command: str) -> None:
    proc = run(bash(command), role="pm")
    assert proc.returncode == 2
    assert "cannot run coven" in proc.stderr
    gm = run(bash(command))
    assert gm.returncode == 0, gm.stderr


@pytest.mark.parametrize("command", READERS + READERS_TOO)
def test_pm_may_run_or_mention_other_subcommands(run: Run, store: Path, command: str) -> None:
    proc = run(bash(command), role="pm")
    assert proc.returncode == 0, proc.stderr


@pytest.mark.parametrize(
    ("command", "message"),
    [
        (
            "env -i coven approve a1 --expires-in 1h",
            "coven prod-guard: a 'pm' session cannot run coven approve: it changes the memory"
            " store or coven's config; ask the GM",
        ),
        ("coven $s", UNTOLD_MESSAGE),
        ("xargs coven < f", UNTOLD_MESSAGE),
    ],
)
def test_store_writer_refusal_names_the_subcommand(
    run: Run, store: Path, command: str, message: str
) -> None:
    proc = run(bash(command), role="pm")
    assert (proc.returncode, proc.stderr.strip()) == (2, message)


def assert_pm_refused_gm_not(run: Run, command: str, refusal: str) -> None:
    proc = run(bash(command), role="pm")
    assert proc.returncode == 2
    assert refusal in proc.stderr
    gm = run(bash(command))
    assert gm.returncode == 0, gm.stderr


VARIABLE_WRITERS = [  # the name and the subcommand both come from variables
    "g=coven s=setup; setsid -f env -i $g $s",
    'g=coven; s=setup; $g "$s"',
    "(env -i bash -c '$0 $1' coven setup &)",
    "read -r a b <<< 'coven setup'; $a $b",
    'f() { "$@"; }; f coven setup',
]


@pytest.mark.parametrize("command", VARIABLE_WRITERS)
def test_pm_cannot_run_coven_from_variables(run: Run, store: Path, command: str) -> None:
    assert_pm_refused_gm_not(run, command, "cannot run coven")


GLUED_WRITERS = [  # an expansion glued to the name: IFS splits it from the subcommand
    "coven${IFS}setup",
    "coven$IFS'setup'",
    '"coven"${IFS}setup',
    ".venv/bin/coven${IFS}memory sync",
]


@pytest.mark.parametrize("command", GLUED_WRITERS)
def test_pm_cannot_run_coven_glued_to_an_expansion(run: Run, store: Path, command: str) -> None:
    assert_pm_refused_gm_not(run, command, "cannot run coven")


SPLIT_STRING_WRITERS = [  # env's -S or --split-string glued to the command string it runs
    "env -S'coven setup'",
    "env -iS'coven memory sync'",
    "env --split-string='coven setup'",
    "env --split='coven approve a1 --expires-in 1h'",
]
SPLIT_STRING_CLAUDE = ["env --split-string='claude -p x'", "env -S'claude -p x'"]


@pytest.mark.parametrize("command", SPLIT_STRING_WRITERS)
def test_pm_cannot_run_coven_through_a_glued_split_string(
    run: Run, store: Path, command: str
) -> None:
    assert_pm_refused_gm_not(run, command, "cannot run coven")


@pytest.mark.parametrize("command", SPLIT_STRING_CLAUDE)
def test_pm_cannot_start_claude_through_a_glued_split_string(
    run: Run, store: Path, command: str
) -> None:
    assert_pm_refused_gm_not(run, command, "cannot start claude sessions")


@pytest.mark.parametrize(
    "command",
    [
        "python3 -Imcoven setup",
        "python3 -Im coven setup",
        "uv run python -Bmcoven memory sync",
    ],
)
def test_pm_cannot_run_coven_through_a_python_flag_cluster(
    run: Run, store: Path, command: str
) -> None:
    assert_pm_refused_gm_not(run, command, "cannot run coven")


def test_pm_may_run_another_module_through_a_python_flag_cluster(run: Run, store: Path) -> None:
    proc = run(bash("python3 -Impytest tests/test_setup_cli.py"), role="pm")
    assert proc.returncode == 0, proc.stderr


@pytest.mark.parametrize(
    "command", ["bash /dev/stdin <<< 'coven setup'", "echo 'coven memory sync' | sh /dev/fd/0"]
)
def test_pm_cannot_run_coven_through_a_shell_reading_dev_stdin(
    run: Run, store: Path, command: str
) -> None:
    assert_pm_refused_gm_not(run, command, "cannot run coven")


@pytest.mark.parametrize(
    "command", ["bash /dev/stdin <<< 'claude -p x'", "echo 'claude -p x' | sh /dev/fd/0"]
)
def test_pm_cannot_start_claude_through_a_shell_reading_dev_stdin(
    run: Run, store: Path, command: str
) -> None:
    assert_pm_refused_gm_not(run, command, "cannot start claude sessions")


def test_pm_may_run_a_script_file_fed_on_stdin(run: Run, store: Path) -> None:
    proc = run(bash("bash build.sh <<< 'claude -p x coven setup'"), role="pm")
    assert proc.returncode == 0, proc.stderr


EXPANDING_SUBCOMMANDS = [  # brace expansion or an ANSI-C string builds the subcommand
    "coven {setup,}",
    "coven s{etup,}",
    "coven $'\\x73etup'",
    "coven --verbose $'setup'",
]


@pytest.mark.parametrize("command", EXPANDING_SUBCOMMANDS)
def test_pm_cannot_run_coven_with_an_expanding_subcommand(
    run: Run, store: Path, command: str
) -> None:
    assert_pm_refused_gm_not(run, command, "cannot run coven")


@pytest.mark.parametrize("command", ["coven doctor {a,b}", "{ coven doctor; }"])
def test_pm_may_run_coven_next_to_braces(run: Run, store: Path, command: str) -> None:
    proc = run(bash(command), role="pm")
    assert proc.returncode == 0, proc.stderr


LAUNCHED_WRITERS = [  # environment and project runners that run the program they are given
    "poetry run coven setup",
    "hatch run coven setup",
    "pdm run coven setup",
    "conda run -n base coven setup",
    "mise exec -- coven setup",
    "direnv exec . coven setup",
    "nix-shell --run 'coven setup'",
    "nix develop -c coven setup",
]


@pytest.mark.parametrize("command", LAUNCHED_WRITERS)
def test_pm_cannot_run_coven_through_a_launcher(run: Run, store: Path, command: str) -> None:
    assert_pm_refused_gm_not(run, command, "cannot run coven")


@pytest.mark.parametrize("command", ["poetry run coven doctor", "mise exec -- pytest tests"])
def test_pm_may_run_other_programs_through_a_launcher(run: Run, store: Path, command: str) -> None:
    proc = run(bash(command), role="pm")
    assert proc.returncode == 0, proc.stderr


@pytest.mark.parametrize(
    "command",
    [
        "uv run claude -p hi",
        "poetry run claude",
        "direnv exec . claude -p x",
        "poetry -C dir run claude -p hi",
        "nix shell nixpkgs#nodejs -c claude -p hi",
    ],
)
def test_pm_cannot_start_claude_through_a_launcher(run: Run, store: Path, command: str) -> None:
    assert_pm_refused_gm_not(run, command, "cannot start claude sessions")


@pytest.mark.parametrize("command", ["uv run pytest -k claude", "python3 tools/x.py claude"])
def test_pm_may_name_claude_after_a_launched_program(run: Run, store: Path, command: str) -> None:
    proc = run(bash(command), role="pm")
    assert proc.returncode == 0, proc.stderr


def test_python_code_naming_claude_is_refused_for_a_pm(run: Run, store: Path) -> None:
    """Fail closed, as the Limits paragraph says: `-c` code is read as words in command position."""
    assert_pm_refused_gm_not(run, 'python3 -c "import claude"', "cannot start claude sessions")


# --- coven's config file: the memory path and the guard latch live there ---


def config_calls() -> list[Payload]:
    folder = config_file().parent
    return [
        {"tool_name": "Write", "tool_input": {"file_path": str(config_file()), "content": "{}"}},
        {
            "tool_name": "Edit",
            "tool_input": {"file_path": str(config_file()), "old_string": "a", "new_string": "b"},
        },
        {"tool_name": "MultiEdit", "tool_input": {"file_path": str(config_file()), "edits": []}},
        {"tool_name": "NotebookEdit", "tool_input": {"notebook_path": f"{folder}/n.ipynb"}},
        {"tool_name": "Write", "tool_input": {"file_path": f"{folder}/owned.json"}},
        {"tool_name": "Write", "tool_input": {"file_path": "config.json"}, "cwd": str(folder)},
        {"tool_name": "Write", "tool_input": {"file_path": f"{home()}/link/config.json"}},
        {"tool_name": "mcp__fs__write", "tool_input": {"args": {"path": str(config_file())}}},
        {
            "tool_name": "mcp__shell__exec",
            "tool_input": {"command": "echo '{}' > ~/.config/coven/config.json"},
        },
        bash("echo '{}' > ~/.config/coven/config.json"),
        bash("echo '{}' >$HOME/.config/coven/config.json"),
        bash(f"echo '{{}}' > {config_file()}"),
        bash(f"cp /tmp/c.json {config_file()}"),
        bash(f"cp -t{folder} /tmp/c.json"),
        bash(f"echo '{{}}' | tee {config_file()}"),
        bash(f"sed -i 's/a/b/' {config_file()}"),
        bash(f"rm {config_file()}"),
        bash(f"mv {config_file()} /tmp/c.json"),
        bash(f"rm -rf {folder}"),
        bash("rm -rf ~/.config/coven"),
        bash("rm -rf ~/.config/*"),
        bash("rm -rf ~/.config/cov?n"),
        bash("rm -rf ~/.c*/coven"),
        bash("rm -rf ~/.[c]onfig/coven/"),
        bash("rm -rf ~/.config/coven/c*"),
        bash(f"echo x > {home()}/link/config.json"),
        bash("cd ~/.config/coven && echo '{}' > config.json"),
        bash("cd ~/.config && rm -rf co*"),
        bash("cd ~/.config && rm co*/config.json"),
        bash("cd ~ && rm .config/coven/config.json"),
        bash("echo '{}' > config.json", cwd=str(folder)),
        bash("echo '{}' > coven/config.json", cwd=str(folder.parent)),
        bash("python3 -c \"open('.config/coven/config.json', 'w')\"", cwd=str(home())),
        bash("env -C ~/.config/coven rm config.json"),
        bash("git -C ~/.config/coven rm -f config.json"),
        {
            "tool_name": "mcp__shell__exec",
            "tool_input": {"cwd": "~/.config", "command": "rm co*/config.json"},
        },
        # The folder named without a glob, from a parent: the literal name is no safer than `co*`.
        bash('cd ~/.config && echo \'{"memory_path":"/tmp/nowhere"}\' > coven/config.json'),
        bash("cd ~/.config && rm -rf coven"),
        bash("pushd ~/.config; echo '{}' > coven/config.json"),
        bash("cd ~/.config; cd coven; echo {} > config.json"),
        bash("env -C ~/.config rm -rf coven"),
        bash("git -C ~/.config rm -r coven"),
        bash("find ~/.config -name coven -exec rm -rf {} +"),
    ]


CONFIG_IDS = [
    "write",
    "edit",
    "multi-edit",
    "notebook-edit",
    "write-owned-file",
    "write-relative",
    "write-symlink",
    "mcp-nested-path",
    "mcp-command",
    "redirect-tilde",
    "redirect-home-var",
    "redirect-absolute",
    "cp",
    "cp-glued-option",
    "tee",
    "sed-in-place",
    "rm-file",
    "mv-file",
    "rm-folder",
    "rm-folder-tilde",
    "glob-parent",
    "glob-name",
    "glob-dotted-parent",
    "glob-bracket",
    "glob-entry",
    "bash-symlink",
    "cd-into-folder",
    "cd-parent-then-glob",
    "cd-parent-then-glob-entry",
    "cd-home-then-relative",
    "cwd-in-folder",
    "cwd-parent-relative",
    "python-c",
    "env-c",
    "git-c",
    "mcp-own-cwd",
    "cd-parent-then-literal-folder-file",
    "cd-parent-then-rm-folder",
    "pushd-parent-then-literal-folder-file",
    "cd-parent-cd-folder-then-file",
    "env-c-parent-rm-folder",
    "git-c-parent-rm-folder",
    "find-parent-exec-rm",
]


@pytest.mark.parametrize("index", range(len(CONFIG_IDS)), ids=CONFIG_IDS)
def test_a_restricted_session_cannot_write_or_reach_coven_config(run: Run, index: int) -> None:
    # The store has another name, so a glob like `co*` can only mean coven's config folder.
    configure(make_store(home() / "vault"))
    (home() / "link").symlink_to(config_file().parent)
    payload = config_calls()[index]
    proc = run(payload, role="pm")
    assert proc.returncode == 2
    assert CONFIG_REFUSAL in proc.stderr
    assert str(config_file()) in proc.stderr
    assert run(payload).returncode == 0


def test_the_config_file_is_the_one_the_hook_reads(run: Run, store: Path) -> None:
    """The rule protects the file that `read_config` reads, wherever HOME is."""
    assert pg.config_file() == config_file()
    proc = run(bash(f"echo x > {pg.config_file()}"), role="pm")
    assert proc.returncode == 2
    assert CONFIG_REFUSAL in proc.stderr


@pytest.mark.parametrize(
    "payload",
    [
        tool("Read", file_path="CONFIG"),
        tool("Read", file_path="FOLDER/owned.json"),
        tool("Grep", pattern="guard", path="CONFIG"),
        tool("Glob", pattern="*.json", path="FOLDER"),
        tool("Write", file_path="/tmp/n.md", content="see CONFIG"),
        tool("Write", file_path="/tmp/config.json", content="{}"),
        tool("Task", prompt="read CONFIG"),
        tool("mcp__db__query", query="select 1"),
        bash("coven doctor"),
        bash("coven guard explain 'ls'"),
        bash("coven guard check guard.json"),
        bash("coven pm status"),
        bash("cd src/coven && ls"),
        bash("cd ~/projects/coven && git status"),
        bash("cat docs/config.json"),
        bash("ls ~/.config/git ~/.config/fish"),
        bash("git commit -m 'docs: edit ~/.config/coven/config.json by hand'"),
        bash("echo 'see ~/.config/coven/config.json' >> notes.md"),
        bash("cat >> notes.md <<'EOF'\nrm -rf ~/.config/coven\nEOF"),
        bash("grep -rn '.config/coven' docs"),
        bash("ls my.config/coven-notes ~/.config/coven-notes"),
    ],
    ids=[
        "read-config",
        "read-owned-file",
        "grep-config",
        "glob-folder",
        "write-mentioning-config",
        "write-another-config-json",
        "task-mentioning-config",
        "mcp-query",
        "coven-doctor",
        "coven-guard-explain",
        "coven-guard-check",
        "coven-pm-status",
        "cd-into-source",
        "cd-into-checkout",
        "another-config-json",
        "other-config-folders",
        "commit-message",
        "echo-text",
        "heredoc-note",
        "grep-pattern",
        "longer-names",
    ],
)
def test_a_restricted_session_reads_coven_config_and_runs_ordinary_commands(
    run: Run, store: Path, payload: Payload
) -> None:
    text = json.dumps(payload)
    text = text.replace("CONFIG", str(config_file())).replace("FOLDER", str(config_file().parent))
    proc = run(json.loads(text), role="pm")
    assert proc.returncode == 0, proc.stderr


def test_coven_config_is_guarded_with_no_config_file_too(run: Run, tmp_path: Path) -> None:
    """The refusal does not depend on the file existing: a session must not create it either."""
    make_store(home() / "coven-memory")
    assert not config_file().exists()
    proc = run(bash("echo '{}' > ~/.config/coven/config.json"), role="pm")
    assert proc.returncode == 2
    assert CONFIG_REFUSAL in proc.stderr


# --- Claude Code's own settings and plugins: a restricted session cannot switch the hooks off ---

SETTINGS_REFUSAL = f"{PREFIX} a 'pm' session cannot write to Claude Code's settings"
PLUGINS_REFUSAL = f"{PREFIX} a 'pm' session cannot write to Claude Code's plugins folder"


def claude_dir() -> Path:
    return Path(os.environ["CLAUDE_CONFIG_DIR"])


def claude_calls(repo: Path, folder: Path) -> dict[str, tuple[Payload, str]]:
    """Calls that change Claude Code's settings or plugins, each with the refusal it must meet."""
    settings, local = folder / "settings.json", folder / "settings.local.json"
    project = repo / ".claude" / "settings.local.json"
    return {
        "redirect-tilde": (
            bash("echo '{\"disableAllHooks\":true}' > ~/.claude/settings.json"),
            SETTINGS_REFUSAL,
        ),
        "write": (tool("Write", file_path=str(settings), content="{}"), SETTINGS_REFUSAL),
        "edit-local": (
            tool("Edit", file_path=str(local), old_string="a", new_string="b"),
            SETTINGS_REFUSAL,
        ),
        "notebook-edit": (tool("NotebookEdit", notebook_path=str(settings)), SETTINGS_REFUSAL),
        "mcp-path": (
            {"tool_name": "mcp__fs__write", "tool_input": {"args": {"path": str(local)}}},
            SETTINGS_REFUSAL,
        ),
        "write-project-local": (
            tool("Write", file_path=str(project), content="{}"),
            SETTINGS_REFUSAL,
        ),
        "write-project-relative": (
            {**tool("Write", file_path=".claude/settings.json"), "cwd": str(repo)},
            SETTINGS_REFUSAL,
        ),
        "redirect-project": (
            bash("echo '{}' > .claude/settings.local.json", cwd=str(repo)),
            SETTINGS_REFUSAL,
        ),
        "redirect-project-absolute": (bash(f"echo '{{}}' > {project}"), SETTINGS_REFUSAL),
        "cd-into-project-folder": (
            bash("cd .claude && echo '{}' > settings.json", cwd=str(repo)),
            SETTINGS_REFUSAL,
        ),
        "cwd-in-project-folder": (
            bash("echo '{}' > settings.json", cwd=str(repo / ".claude")),
            SETTINGS_REFUSAL,
        ),
        "cd-into-folder": (bash("cd ~/.claude && echo '{}' > settings.json"), SETTINGS_REFUSAL),
        "cd-into-folder-local": (
            bash("cd ~/.claude; echo '{}' > settings.local.json"),
            SETTINGS_REFUSAL,
        ),
        "glob": (bash("rm -f ~/.claude/settings*"), SETTINGS_REFUSAL),
        "glob-dotted-parent": (bash("rm -f ~/.cl*/settings.json"), SETTINGS_REFUSAL),
        "cp": (bash("cp /tmp/s.json ~/.claude/settings.json"), SETTINGS_REFUSAL),
        "tee": (bash("echo '{}' | tee ~/.claude/settings.local.json"), SETTINGS_REFUSAL),
        "ln": (bash("ln -sf /tmp/s.json ~/.claude/settings.json"), SETTINGS_REFUSAL),
        "mv": (bash("mv ~/.claude/settings.json /tmp/s.json"), SETTINGS_REFUSAL),
        "home-variable": (bash("echo '{}' >$HOME/.claude/settings.json"), SETTINGS_REFUSAL),
        "rm-plugins-cache": (bash("rm -rf ~/.claude/plugins/cache/coven"), PLUGINS_REFUSAL),
        "rm-plugins": (bash("rm -rf ~/.claude/plugins"), PLUGINS_REFUSAL),
        "mv-plugins": (bash("mv ~/.claude/plugins /tmp/p"), PLUGINS_REFUSAL),
        "cd-then-rm-plugins": (bash("cd ~/.claude && rm -rf plugins"), PLUGINS_REFUSAL),
        "write-plugin-file": (
            tool("Write", file_path=str(folder / "plugins" / "cache" / "x.py")),
            PLUGINS_REFUSAL,
        ),
        "glob-plugins": (bash("rm -rf ~/.claude/plug*"), PLUGINS_REFUSAL),
    }


CLAUDE_IDS = list(claude_calls(Path("/x"), Path("/y")))


@pytest.mark.parametrize("name", CLAUDE_IDS)
def test_a_restricted_session_cannot_change_claude_settings_or_plugins(
    run: Run, store: Path, name: str
) -> None:
    payload, refusal = claude_calls(project_dir(), claude_dir())[name]
    proc = run(payload, role="pm")
    assert proc.returncode == 2
    assert refusal in proc.stderr
    assert run(payload).returncode == 0


def test_a_custom_claude_config_dir_is_guarded_where_it_is(
    run: Run, store: Path, tmp_path: Path
) -> None:
    folder = tmp_path / "elsewhere"
    env = {"CLAUDE_CONFIG_DIR": str(folder)}
    for payload, refusal in (
        (bash(f"echo '{{}}' > {folder}/settings.json"), SETTINGS_REFUSAL),
        (bash(f"cd {folder} && echo '{{}}' > settings.local.json"), SETTINGS_REFUSAL),
        (tool("Write", file_path=f"{folder}/plugins/x"), PLUGINS_REFUSAL),
    ):
        proc = run(payload, role="pm", env=env)
        assert proc.returncode == 2
        assert refusal in proc.stderr
        assert run(payload, env=env).returncode == 0


@pytest.mark.parametrize(
    "payload",
    [
        tool("Read", file_path="CLAUDE/settings.json"),
        tool("Read", file_path="CLAUDE/plugins/cache/x.py"),
        tool("Grep", pattern="hooks", path="CLAUDE/settings.json"),
        tool("Glob", pattern="*.json", path="CLAUDE"),
        tool("Write", file_path="/tmp/settings.json", content="{}"),
        tool("Write", file_path="/tmp/n.md", content="see ~/.claude/settings.json"),
        tool("Write", file_path="CLAUDE/projects/notes.md", content="x"),
        bash("git status"),
        bash("ls .claude-plugin"),
        bash("echo 'edit ~/.claude/settings.json by hand' >> notes.md"),
        bash("git commit -m 'docs: explain .claude/settings.json'"),
        bash("grep -rn '.claude/settings.json' docs"),
        bash("ls ~/.claude-backup ~/.config/git"),
        bash("cat docs/settings.json"),
        bash("cd src && ls plugins"),
        bash("coven doctor"),
    ],
    ids=[
        "read-settings",
        "read-plugin",
        "grep-settings",
        "glob-folder",
        "write-another-settings-json",
        "write-text-naming-settings",
        "write-elsewhere-in-the-folder",
        "git-status",
        "plugin-manifest-folder",
        "echo-text",
        "commit-message",
        "grep-pattern",
        "longer-names",
        "another-settings-json",
        "plugins-folder-of-a-project",
        "coven-doctor",
    ],
)
def test_a_restricted_session_reads_claude_settings_and_runs_ordinary_commands(
    run: Run, store: Path, payload: Payload
) -> None:
    text = json.dumps(payload).replace("CLAUDE", str(claude_dir()))
    proc = run(json.loads(text), role="pm")
    assert proc.returncode == 0, proc.stderr


# --- who is restricted, and what no approval lifts ---


def test_an_unset_role_is_not_restricted(run: Run, store: Path) -> None:
    assert run(bash("claude -p hi")).returncode == 0
    assert run(bash(f"echo x > {store}/approvals/a.json")).returncode == 0
    assert run(bash("ssh $H uptime")).returncode == 0


@pytest.mark.parametrize("role", ["", "x", "pm", "gm"], ids=["empty", "x", "pm", "gm"])
@pytest.mark.parametrize(
    "command",
    ["claude -p hi", "ssh $H uptime", f"unset {ROLE}", "coven setup", "rm -rf ~/coven-memory"],
)
def test_every_set_role_value_meets_the_restricted_rules(
    run: Run, store: Path, command: str, role: str
) -> None:
    proc = run(bash(command), role=role)
    assert proc.returncode == 2
    assert f"a '{role}' session cannot" in proc.stderr


@pytest.mark.parametrize(
    "extra",
    [
        "claude -p hi",
        "ssh $H uptime",
        f"unset {ROLE}",
        "coven approve a1 --expires-in 1h",
        "rm -rf ~/coven-memory/approvals",
        "rm ~/.config/coven/config.json",
    ],
)
def test_an_approved_command_that_breaks_a_restricted_rule_is_still_refused(
    run: Run, store: Path, extra: str
) -> None:
    command = f"{PROD_COMMAND} && {extra}"
    approve(store, commands=[command])
    assert run(bash(command)).returncode == 0  # an unrestricted session: the approval counts
    proc = run(bash(command), role="pm")
    assert proc.returncode == 2
    assert PROD_MESSAGE not in proc.stderr  # a restricted rule spoke, not the target rule


def test_an_approved_target_still_passes_for_a_restricted_session(run: Run, store: Path) -> None:
    approve(store)
    proc = run(bash(PROD_COMMAND), role="pm")
    assert proc.returncode == 0, proc.stderr


SUBAGENT = {"agent_id": "a1b2c3", "agent_type": "general-purpose"}


@pytest.mark.parametrize(
    "payload",
    [
        bash("claude -p hi"),
        bash("ssh $H uptime"),
        bash("echo x > ~/coven-memory/approvals/a.json"),
        bash("coven setup"),
        {"tool_name": "Write", "tool_input": {"file_path": "~/coven-memory/systems/x.md"}},
        {"tool_name": "Write", "tool_input": {"file_path": "~/.config/coven/config.json"}},
    ],
    ids=["claude", "host", "store-bash", "installer", "store-write", "config-write"],
)
def test_a_subagent_payload_meets_the_same_restricted_rules(
    run: Run, store: Path, payload: Payload
) -> None:
    payload = json.loads(json.dumps(payload).replace("~", str(home())))
    subagent = {**payload, **SUBAGENT}
    refused = run(subagent, role="pm")
    assert refused.returncode == 2
    assert run(payload, role="pm").stderr == refused.stderr
    assert run(subagent).returncode == 0


def explained(payload: Payload, role: str | None) -> Any:
    return pg.explain(payload, home() / "coven-memory", role, NOW)


@pytest.mark.parametrize(
    "command",
    ["claude -p hi", "ssh $H uptime", f"unset {ROLE}", "coven approve a1", "rm -rf ~/coven-memory"],
)
def test_explain_reports_the_restricted_rule_a_pm_would_hit(store: Path, command: str) -> None:
    unrestricted = explained(bash(command), None)
    assert unrestricted.restricted is not None
    assert unrestricted.message is None
    assert unrestricted.verdict == "refused for a PM"
    restricted = explained(bash(command), "pm")
    assert restricted.message == unrestricted.restricted
    assert restricted.verdict == "refused for a PM"


def test_explain_has_no_restricted_rule_for_an_ordinary_command(store: Path) -> None:
    found = explained(bash("git status"), None)
    assert (found.restricted, found.message, found.verdict) == (None, None, "allowed")


def test_a_target_refusal_wins_over_a_restricted_rule_in_the_verdict(store: Path) -> None:
    found = explained(bash(f"{PROD_COMMAND} && claude -p hi"), "pm")
    assert found.verdict == "refused for every session"
    assert found.message is not None
    assert "cannot start claude sessions" in found.message  # a PM meets the restricted rule first


@pytest.mark.parametrize(
    "payload",
    [
        bash("claude -p hi"),
        bash("ssh $H uptime"),
        bash("coven memory sync"),
        bash(f"unset {ROLE}"),
        bash("echo x > ~/coven-memory/approvals/a.json"),
        bash("echo x > ~/.config/coven/config.json"),
        {"tool_name": "Write", "tool_input": {"file_path": "~/coven-memory/systems/x.md"}},
        {"tool_name": "Edit", "tool_input": {"file_path": "~/.config/coven/config.json"}},
        {"tool_name": "mcp__fs__write", "tool_input": {"path": "~/coven-memory/a.md"}},
    ],
)
@pytest.mark.parametrize("role", [None, "", "pm"], ids=["unset", "empty", "pm"])
def test_decide_is_the_message_of_explain_for_the_restricted_rules(
    store: Path, payload: Payload, role: str | None
) -> None:
    payload = json.loads(json.dumps(payload).replace("~", str(home())))
    assert decide(payload, store, role) == explained(payload, role).message


def test_the_hook_names_the_subcommands_that_write_the_store_or_the_config() -> None:
    """The hook's list is what `coven approve`, `coven setup` and `coven memory adopt|init|sync`
    are: a command added to coven's CLI that writes either must be added here."""
    assert pg.STORE_WRITING_SUBCOMMANDS == {
        "approve",
        "setup",
        "memory adopt",
        "memory init",
        "memory sync",
    }
