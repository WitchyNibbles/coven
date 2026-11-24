"""The prod guard: a PreToolUse hook that refuses calls reaching production or gated systems unless
the user approved the exact command. Every system, host and command here is invented, and no test
runs a remote CLI: a command is only a string the guard reads."""

import argparse
import io
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from support import load_module

from coven import cli, paths

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
    return load_module(SCRIPT, "coven_prod_guard")


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
    sibling = load_hook("memory_context")
    assert pg.read_config() == pg.GuardConfig(sibling.store_path(), latch=False)  # no file
    for text in ("{}", json.dumps({"memory_path": str(tmp_path / "elsewhere")})):
        write_config(text)
        assert pg.read_config().store == sibling.store_path()


def test_the_config_file_is_the_one_coven_reads() -> None:
    assert pg.config_file() == paths.config_dir() / "config.json"


def load_hook(name: str) -> ModuleType:
    return load_module(HOOKS / f"{name}.py", f"coven_{name}_under_test")


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
    for name in ("prod_guard.py", "guard_config.py", "hook_support.py", "hooklog.py"):
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
        # `.claude` is not claude. Relative on purpose: a read of `~/.claude` piped into a shell is
        # refused by the user folder zone, and this case is about the word, not the folder.
        "ls .claude | sh",
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
        # No write elsewhere in CLAUDE: the whole user folder is a zone now, so one is refused.
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


# --- all of Claude Code's user folder: a restricted session may read it, not change it ---

FOLDER_REFUSAL = f"{PREFIX} a 'pm' session cannot write to Claude Code's user folder"


def folder_calls(user: Path, folder: Path) -> dict[str, Payload]:
    """Calls that write, move or delete something in Claude Code's user folder or `~/.claude.json`,
    each by a different route: `user` is the home folder, `folder` Claude Code's folder."""
    mcp = {"tool_name": "mcp__fs__write", "tool_input": {"args": {"path": f"{folder}/skills/x"}}}
    mcp_command = {"tool_name": "mcp__shell__run", "tool_input": {"command": "rm -rf ~/.claude/x"}}
    return {
        "rm-folder": bash("rm -rf ~/.claude"),
        "rm-home-variable": bash('rm -rf "$HOME/.claude/skills"'),
        "rm-braced-home-variable": bash("rm -rf ${HOME}/.claude/skills"),
        "rm-literal-home": bash(f"rm -rf {user}/.claude/skills"),
        "rm-config-dir-variable": bash("rm -rf $CLAUDE_CONFIG_DIR/skills"),
        "rm-braced-config-dir-variable": bash("rm -rf ${CLAUDE_CONFIG_DIR}"),
        "rm-literal-folder": bash(f"rm -rf {folder}/skills"),
        "mv-agent": bash("mv ~/.claude/agents/x.md ~/.cache/"),
        "mv-in": bash("mv ~/.cache/x.md ~/.claude/agents/"),
        "redirect": bash("echo x > ~/.claude/CLAUDE.md"),
        "redirect-append": bash("echo x >> ~/.claude/CLAUDE.md"),
        "redirect-home-variable": bash("echo x > $HOME/.claude/CLAUDE.md"),
        "cp-destination": bash("cp x ~/.claude/commands/"),
        "tee": bash("echo x | tee ~/.claude/todos/x"),
        "ln": bash("ln -sf /tmp/x ~/.claude/skills/x"),
        "chmod": bash("chmod 777 ~/.claude/hooks"),
        "sed-in-place": bash("sed -i s/a/b/ ~/.claude/CLAUDE.md"),
        "find-delete": bash("find ~/.claude -delete"),
        "find-exec": bash("find ~/.claude/skills -name '*.md' -exec rm {} +"),
        "find-delete-in-skills": bash("find ~/.claude/skills -delete"),
        "read-piped-to-xargs": bash("ls ~/.claude/skills | xargs rm -rf"),
        "read-piped-to-sh": bash("cat ~/.claude/CLAUDE.md | sh"),
        "read-then-redirect-into-it": bash("ls ~/.claude/skills > ~/.claude/CLAUDE.md"),
        "read-with-substitution": bash("cat $(rm -rf ~/.claude/skills)"),
        "read-with-quoted-substitution": bash('ls "$(rm -rf ~/.claude/skills)"'),
        "echo-with-substitution": bash("echo $(rm -rf ~/.claude/skills)"),
        "echo-with-backticks": bash("echo `rm -rf ~/.claude/skills`"),
        "echo-piped-to-xargs": bash("echo ~/.claude/skills | xargs rm -rf"),
        "cp-between-two": bash("cp ~/.claude/a ~/.claude/b"),
        "cp-target-directory": bash("cp -t ~/.claude/skills x"),
        "cp-hard-link": bash("cp -l ~/.claude/CLAUDE.md /tmp/c"),
        "cp-long-option": bash("cp --remove-destination x ~/.claude/y"),
        "tar-archive-inside": bash("tar -cf ~/.claude/x.tar y"),
        "tar-removing-files": bash("tar --remove-files -cf /tmp/x.tar ~/.claude/skills"),
        "tar-extract-to-it": bash("tar -xf x.tar -C ~/.claude"),
        "env-chdir": bash("env -C ~/.claude rm -rf skills"),
        "bash-c": bash("bash -c 'rm -rf ~/.claude/skills'"),
        "touch": bash("touch ~/.claude/skills/x"),
        "install": bash("install -m 644 x ~/.claude/CLAUDE.md"),
        "tar": bash("tar -C ~/.claude -xf x.tar"),
        "dot-dot": bash("rm -rf ~/.claude/../.claude/skills"),
        "heredoc-into-it": bash("cat > ~/.claude/CLAUDE.md <<EOF\nx\nEOF"),
        "cd-rm": bash("cd ~/.claude && rm -rf skills"),
        "cd-rm-after-reads": bash("cd ~/.claude && ls skills; rm -rf skills"),
        "cd-home-variable-rm": bash("cd $HOME/.claude && rm -rf skills"),
        "cd-literal-rm": bash(f"cd {user}/.claude && rm -rf skills"),
        "cd-config-dir-variable-rm": bash("cd $CLAUDE_CONFIG_DIR && rm -rf skills"),
        "cd-redirect": bash("cd ~/.claude && echo x > CLAUDE.md"),
        "cd-reader-redirect": bash("cd ~/.claude && ls > out"),
        "cd-reader-redirect-file-name": bash("cd ~/.claude && ls > out.txt"),
        "cd-reader-append": bash("cd ~/.claude && ls >> out"),
        "cd-reader-stderr-to-file": bash("cd ~/.claude && ls 2>err"),
        "cd-reader-discard-then-file": bash("cd ~/.claude && ls 2>/dev/null > out"),
        "cd-reader-dup-to-file": bash("cd ~/.claude && ls >&out"),
        "cd-reader-input-substitution": bash('cd ~/.claude && cat <<< "$(rm -rf skills)"'),
        "jq-redirect-into-it": bash("jq . ~/.claude.json > ~/.claude.json"),
        "jq-redirect-into-folder": bash("jq . ~/.claude.json > ~/.claude/x.json"),
        "jq-after-cd-redirect": bash("cd ~ && jq . .claude.json > .claude/x.json"),
        "loop-rm": bash('for f in ~/.claude/skills/*; do rm -rf "$f"; done'),
        "loop-sed-in-place": bash('for f in ~/.claude/agents/*.md; do sed -i s/a/b/ "$f"; done'),
        "loop-cd-mv": bash('cd ~/.claude && for f in *; do mv "$f" /tmp/; done'),
        "loop-runs-script": bash('for f in ~/.claude/skills/*.sh; do sh "$f"; done'),
        "loop-substitution-writes": bash("for f in $(rm -rf ~/.claude); do :; done"),
        "loop-substitution-find-delete": bash("for f in $(find ~/.claude -delete); do ls; done"),
        "loop-redirect-in-body": bash('for f in ~/.claude/a; do cat "$f" > "$f.bak"; done'),
        "loop-redirect-after-done": bash("for f in ~/.claude/a; do ls; done > ~/.claude/out"),
        "loop-write-after-read": bash('for f in ~/.claude/a; do ls "$f"; rm -rf "$f"; done'),
        "loop-piped-to-shell": bash('for f in ~/.claude/a; do cat "$f" | sh; done'),
        "loop-nested": bash(
            "for f in ~/.claude/a; do for g in ~/.claude/b; do rm -rf $g; done; done"
        ),
        "bound-name-assigned-twice-then-rm": bash(
            "J=~/.claude/x; n=$(grep -c x $J); n=$(grep -c y $J); rm -rf $n"
        ),
        "bound-name-assigned-twice-second-writes": bash(
            "J=~/.claude/x; n=$(grep -c x $J); n=$(rm -rf $J); echo $n"
        ),
        "backslash-substitution-writes": bash(
            'for f in ~/.claude/a; do n=$(grep -c "a\\|b" "$f"; rm -rf ~/.claude/x); done'
        ),
        "group-rm": bash("ls ~/.claude && { rm -rf ~/.claude/a; }"),
        "group-redirect": bash("ls && { echo a; } > ~/.claude/out"),
        "group-redirect-in-body": bash("ls && { echo a > ~/.claude/out; }"),
        "loop-over-path-variable": bash("for PATH in ~/.claude/x; do ls; done"),
        "loop-without-words": bash('cd ~/.claude && for f; do rm -rf "$f"; done'),
        "loop-then-write": bash("for f in ~/.claude/a; do ls; done; rm -rf ~/.claude/a"),
        "loop-while": bash("while true; do rm -rf ~/.claude/a; done"),
        "loop-quoted-substitution": bash('for f in "$(rm -rf ~/.claude)"; do ls; done'),
        "loop-backticks": bash("for f in `rm -rf ~/.claude`; do ls; done"),
        "loop-case-in-substitution": bash(
            "for f in $(case x in a) rm -rf ~/.claude;; esac); do ls; done"
        ),
        "loop-two-substitutions": bash("for f in $(ls)$(rm -rf ~/.claude); do ls; done"),
        "loop-substitution-with-more": bash("for f in $(ls ~/.claude)x; do rm -rf ~/.claude; done"),
        "loop-substitution-redirect": bash("for f in $(ls > ~/.claude/x); do cat; done"),
        "loop-substitution-semicolon": bash("for f in $(ls; rm -rf ~/.claude/a); do cat; done"),
        "loop-over-uppercase-variable": bash("for HOME in ~/.claude/a; do ls; done"),
        # zsh ties these to PATH, CDPATH, FPATH, MANPATH, MODULE_PATH, MAILPATH and FIGNORE.
        **{
            f"loop-over-zsh-{name}": bash(f"for {name} in ~/.claude/a; do ls; done")
            for name in ("path", "cdpath", "fpath", "manpath", "module_path", "mailpath")
        },
        "read-into-zsh-path": bash("while read path; do ls; done < ~/.claude/list"),
        "read-into-zsh-fpath": bash("find ~/.claude/p | while read -r fpath; do ls; done"),
        "loop-over-fpath-upper": bash("for FPATH in ~/.claude/a; do ls; done"),
        "read-into-fignore": bash("while read fignore; do ls; done < ~/.claude/list"),
        "cd-reader-heredoc": bash("cd ~/.claude && cat > CLAUDE.md <<EOF\nx\nEOF"),
        "cd-script": bash("cd ~/.claude && sh x.sh"),
        "cd-tee": bash("cd ~/.claude && ls | tee x"),
        "cd-substitution": bash("cd ~/.claude && ls $(rm -rf skills)"),
        "cd-backticks": bash("cd ~/.claude && ls `rm -rf skills`"),
        "cd-sed-in-place": bash("cd ~/.claude && sed -i s/a/b/ CLAUDE.md"),
        "cd-glob-rm": bash("cd ~/.claude && rm -rf s*"),
        "cd-pushd-rm": bash("pushd ~/.claude && rm -rf skills"),
        "cd-home-rm": bash("cd ~ && rm -rf .claude"),
        "cd-home-glob-rm": bash("cd ~ && rm -rf .cl*"),
        "cd-reads-then-cd-away-rm": bash("cd ~/.claude && ls; cd /tmp && rm -rf x"),
        "cd-relative-rm": bash("cd .claude && rm -rf skills", cwd=str(user)),
        "cd-relative-redirect": bash("cd .claude && echo x > CLAUDE.md", cwd=str(user)),
        "rm-relative-from-home": bash("rm -rf .claude/skills", cwd=str(user)),
        "cd-home-rm-inside": bash("cd ~ && rm -rf .claude/skills"),
        "cd-home-variable-rm-inside": bash("cd $HOME && rm -rf .claude/skills"),
        "cd-unknown-rm-inside": bash('cd "$X" && rm -rf .claude/skills'),
        "cd-unknown-rm-json": bash('cd "$X" && rm .claude.json'),
        "write-json-backup": bash("echo {} > ~/.claude.json.backup"),
        "write-json-bak": bash("echo x > ~/.claude.json.bak"),
        "cp-over-json-backup": bash("cp /tmp/x ~/.claude.json.backup"),
        "mv-json-backup": bash("mv ~/.claude.json.backup /tmp/x"),
        "rm-json-backups-glob": bash("rm -f ~/.claude.json.*"),
        "rm-json-backups-loose-glob": bash("rm -f ~/.claude.j*.b*"),
        "sed-in-place-json-backup": bash("sed -i s/a/b/ ~/.claude.json.backup"),
        "write-json-backup-tool": tool(
            "Write", file_path=str(home() / ".claude.json.backup"), content="{}"
        ),
        "edit-json-backup-tool": tool(
            "Edit", file_path=str(home() / ".claude.json.1"), old_string="a", new_string="b"
        ),
        "cd-home-rm-json-backup": bash("cd ~ && rm .claude.json.backup"),
        "cd-home-rm-json-backups-glob": bash("cd ~ && rm .claude.json.*"),
        "cd-unknown-rm-json-backup": bash('cd "$X" && rm .claude.json.backup'),
        "cd-unknown-rm-json-backups-glob": bash('cd "$X" && rm .claude.json.*'),
        "write-json-backup-in-folder": bash("echo x > ~/.claude/.claude.json.backup"),
        "cd-home-sed-in-place": bash("cd ~ && sed -i s/a/b/ .claude/CLAUDE.md"),
        "cd-home-touch": bash("cd ~ && touch .claude/x"),
        "cd-home-mkdir": bash("cd ~ && mkdir -p .claude/x"),
        "cd-home-dd": bash("cd ~ && dd if=/tmp/x of=.claude/x"),
        "cd-home-curl": bash("cd ~ && curl -o .claude/x https://example.com/x"),
        "cd-home-curl-glued": bash("cd ~ && curl -o.claude/x https://example.com/x"),
        "cd-home-sort-output": bash("cd ~ && sort --output=.claude/x /tmp/a"),
        "cd-home-patch": bash("cd ~ && patch .claude/x /tmp/p"),
        "cd-home-touch-json": bash("cd ~ && touch .claude.json"),
        "cd-home-then-folder-touch": bash("cd ~ && cd .claude && touch x"),
        "cd-project-touch-up": bash("cd ~/proj && touch ../.claude/x"),
        "cd-home-variable-sed": bash("cd $HOME && sed -i s/a/b/ .claude/CLAUDE.md"),
        "cd-braced-home-variable-touch": bash('cd "${HOME}" && touch .claude/x'),
        "bare-cd-touch": bash("cd && touch .claude/x"),
        "cd-back-touch": bash("cd - && touch .claude/x"),
        "cd-unknown-sed": bash('cd "$X" && sed -i s/a/b/ .claude/x'),
        "cd-unknown-touch-up": bash('cd "$X" && touch ../.claude/x'),
        "echo-script-then-sh": bash("echo 'rm -rf ~/.claude/skills' > /tmp/s.sh; sh /tmp/s.sh"),
        "printf-script-then-bash": bash(
            "printf 'rm -rf %s\\n' ~/.claude/skills > /tmp/s.sh && bash /tmp/s.sh"
        ),
        "echo-script-then-source": bash("echo 'rm -rf ~/.claude/x' > /tmp/s; . /tmp/s"),
        "echo-script-run-by-path": bash("echo 'rm -rf ~/.claude/x' >> /tmp/s.sh; /tmp/s.sh"),
        "echo-script-nohup": bash("echo 'rm -rf ~/.claude/x' > /tmp/s; nohup /tmp/s"),
        "echo-script-to-python": bash(
            "echo \"import shutil; shutil.rmtree('$HOME/.claude')\" > /tmp/s.py; python3 /tmp/s.py"
        ),
        "tee-script-then-sh": bash("echo 'rm -rf ~/.claude/x' | tee /tmp/s; sh /tmp/s"),
        "listing-made-script": bash(
            "ls -d ~/.claude/skills/* | sed 's/^/rm -rf /' > /tmp/s; sh /tmp/s"
        ),
        "group-made-script": bash("{ echo 'rm -rf ~/.claude/x'; } > /tmp/s; sh /tmp/s"),
        "loop-made-script": bash(
            'for f in ~/.claude/skills/*; do echo "rm -rf $f"; done > /tmp/s; bash /tmp/s'
        ),
        "exec-made-script": bash("exec > /tmp/s; echo 'rm -rf ~/.claude/x'; sh /tmp/s"),
        "subshell-made-script": bash("(echo 'rm -rf ~/.claude/x') > /tmp/s; sh /tmp/s"),
        "if-made-script": bash("if true; then echo 'rm -rf ~/.claude/x'; fi > /tmp/s; sh /tmp/s"),
        "env-runs-made-script": bash("echo 'rm -rf ~/.claude/x' > /tmp/s; env sh /tmp/s"),
        "find-runs-made-script": bash(
            "echo 'rm -rf ~/.claude/x' > /tmp/s; find /tmp -name s -exec sh {} \\;"
        ),
        "variable-runs-made-script": bash("echo 'rm -rf ~/.claude/x' > /tmp/s; $RUN /tmp/s"),
        "cwd-in-folder": bash("rm -rf skills", cwd=str(folder)),
        "cwd-in-folder-redirect": bash("echo x > CLAUDE.md", cwd=str(folder / "skills")),
        "cwd-in-folder-read-then-rm": bash("ls skills; rm -rf skills", cwd=str(folder)),
        "cwd-in-folder-cp-in": bash("cp CLAUDE.md CLAUDE.bak", cwd=str(folder)),
        "cwd-in-folder-copy-relative-out": bash("cp CLAUDE.md out", cwd=str(folder)),
        "cwd-in-folder-sed-i": bash("sed -i s/a/b/ CLAUDE.md", cwd=str(folder)),
        "cwd-in-folder-tee": bash("ls | tee CLAUDE.md", cwd=str(folder)),
        "cwd-in-folder-script": bash('python3 -c \'open("x", "w")\'', cwd=str(folder)),
        "glob-dotted": bash("rm -rf ~/.cl*"),
        "glob-folder-contents": bash("rm -rf ~/.claude/*"),
        "glob-name": bash("rm -rf ~/.claude/sk*"),
        "rm-json": bash("rm ~/.claude.json"),
        "rm-json-variable": bash("rm $HOME/.claude.json"),
        "redirect-json": bash("echo > ~/.claude.json"),
        "mv-json": bash("mv ~/.claude.json /tmp/c.json"),
        "cp-json": bash("cp /tmp/c.json ~/.claude.json"),
        "glob-json": bash("rm ~/.claude.js*"),
        "cd-home-rm-json": bash("cd ~ && rm .claude.json"),
        "pipe-into-xargs": bash("grep -l x ~/.claude/p/*.jsonl | xargs rm"),
        "xargs-replace": bash("ls -d ~/.claude/p/* | xargs -I{} cp {} /tmp"),
        "xargs-replace-glued": bash("ls -d ~/.claude/p/* | xargs -I% cat %"),
        "xargs-i": bash("ls -d ~/.claude/p/* | xargs -i cat {}"),
        "xargs-replace-long": bash("ls -d ~/.claude/p/* | xargs --replace cat {}"),
        "xargs-shell": bash("ls -d ~/.claude/p/* | xargs sh -c 'cat $0'"),
        "xargs-bash": bash("ls -d ~/.claude/p/* | xargs -n1 bash"),
        "xargs-env": bash("ls -d ~/.claude/p/* | xargs env cat"),
        "xargs-sed": bash("ls -d ~/.claude/p/* | xargs sed -n 1p"),
        "xargs-find": bash("ls -d ~/.claude/p/* | xargs find"),
        "xargs-sort": bash("ls -d ~/.claude/p/* | xargs sort"),
        "xargs-xargs": bash("ls -d ~/.claude/p/* | xargs -n1 xargs rm"),
        "xargs-unknown-option": bash("ls -d ~/.claude/p/* | xargs -E x cat"),
        "xargs-args-file": bash("ls -d ~/.claude/p/* | xargs -a /tmp/l cat"),
        "xargs-into-folder": bash("ls -d ~/.claude/p/* | xargs grep x > ~/.claude/out"),
        "cd-in-xargs-rm": bash("cd ~/.claude/p && grep -rl x . | xargs -r rm"),
        "cd-in-xargs-replace": bash("cd ~/.claude/p && ls | xargs -I{} cp {} /tmp"),
        "cd-in-xargs-into-folder": bash("cd ~/.claude/p && ls | xargs grep x > out"),
        "xargs-then-xargs-rm": bash("ls -d ~/.claude/p/* | xargs grep -l x | xargs rm"),
        "xargs-bare": bash("ls -d ~/.claude/p/* | xargs"),
        "xargs-output-run": bash(
            "ls -d ~/.claude/p/* | xargs -n1 basename | sed 's/^/rm /' > /tmp/s; sh /tmp/s"
        ),
        "sort-output-file": bash("sort -o ~/.claude/a ~/.claude/b"),
        "sort-output-long-option": bash("ls ~/.claude | sort --output=~/.claude/x"),
        "uniq-second-operand": bash("uniq ~/.claude/a ~/.claude/b"),
        "read-redirected-into-it": bash("grep x ~/.claude/a > ~/.claude/out"),
        "read-appended-into-it": bash("grep x ~/.claude/a | sort >> ~/.claude/out"),
        "cd-read-redirected-to-relative": bash("cd ~/.claude && grep x a | sort > out"),
        "env-chdir-write": bash("env -C ~/.claude touch x"),
        "timeout-rm": bash("timeout 5 rm ~/.claude/x"),
        "environment-rm": bash("LC_ALL=C rm ~/.claude/x"),
        "reader-by-writable-path": bash("/tmp/bin/grep x ~/.claude/a"),
        "reader-by-local-bin": bash("/usr/local/bin/grep x ~/.claude/a"),
        "cd-cp-into-it": bash("cd ~/.claude && cp /tmp/x relfile"),
        "cp-into-it": bash("cp /tmp/x ~/.claude/y"),
        "cd-cp-then-rm": bash("cd ~/.claude && cp a /tmp/b; rm a"),
        "find-fprint": bash("find ~/.claude -fprint ~/.claude/x"),
        "find-fprintf": bash("find ~/.claude -fprintf ~/.claude/x '%p'"),
        "find-fls": bash("find ~/.claude -fls ~/.claude/x"),
        "find-delete-iname": bash("find ~/.claude -iname '*.tmp' -delete"),
        "find-exec-semicolon": bash("find ~/.claude -name x -exec rm {} ;"),
        "find-execdir": bash("find ~/.claude -name x -execdir rm {} ;"),
        "find-ok": bash("find ~/.claude -name x -ok rm {} ;"),
        "find-okdir": bash("find ~/.claude -name x -okdir rm {} ;"),
        "read-loop-rm": bash('while read f; do rm "$f"; done < ~/.claude/list'),
        "find-loop-rm": bash('find ~/.claude/p | while read f; do rm "$f"; done'),
        "substitution-name-rm": bash('f=$(grep -l k ~/.claude/p/*.jsonl | head -1); rm "$f"'),
        "substitution-in-reader-writes": bash("ls -la $(rm ~/.claude/x)"),
        "substitution-assignment-writes": bash('n=$(rm ~/.claude/x); [ "$n" != 0 ]'),
        "sed-range-writes": bash("sed -n '/x/,$w ~/.claude/o' ~/.claude/a"),
        "heredoc-cat-into-it": bash("cat > ~/.claude/x <<'EOF'\ntext\nEOF"),
        "heredoc-tee-into-it": bash("tee ~/.claude/x <<'EOF'\ntext\nEOF"),
        "heredoc-cat-relative-after-cd": bash("cd ~/.claude && cat > x <<'EOF'\ntext\nEOF"),
        "heredoc-unquoted-substitution": bash("cat > /tmp/f <<EOF\n$(rm ~/.claude/x)\nEOF"),
        "bound-name-rm": bash("F=~/.claude/a; sed -n 1p $F; rm $F"),
        "bound-name-read-into": bash("F=~/.claude/a; read F; sed -n p $F"),
        "bound-name-rebound": bash("F=~/.claude/a; F=-i; sed -n p $F x"),
        "bash-c-writes": bash("bash -c 'rm ~/.claude/x'"),
        "bash-c-with-arguments": bash("bash -c 'cat ~/.claude/a' x"),
        "bash-c-redirected-into-it": bash("bash -c 'cat ~/.claude/a' > ~/.claude/b"),
        "sleep-then-rm": bash("sleep 1; rm ~/.claude/x"),
        "command-rm": bash("command rm ~/.claude/x"),
        "cd-then-unknown-program": bash("cd ~/.claude && ls; mytool x"),
        "cd-away-then-home-glob-rm": bash(
            "cd /tmp/x && git log --grep 'a.*b'; cd ~ && rm -rf .cl*"
        ),
        "node-reads-it": bash("node -e \"require('fs').readFileSync('~/.claude/a')\""),
        "perl-reads-it": bash("perl -e 'open F, \"~/.claude/a\"'"),
        "ruby-reads-it": bash("ruby -e 'File.read(\"~/.claude/a\")'"),
        "awk-reads-it": bash("awk '{print}' ~/.claude/a"),
        "sqlite-reads-it": bash("sqlite3 ~/.claude/a.db 'select 1'"),
        "python-heredoc-piped": bash("cat <<'E' | python3 -\nopen('~/.claude/a')\nE"),
        "python-home-lookup": bash(
            "python3 -c \"import os; open(os.path.expanduser('~/.claude/a'))\""
        ),
        "rg-pre": bash("rg --pre sh x ~/.claude"),
        "rg-pre-equals": bash("rg --pre=sh x ~/.claude/skills"),
        "rg-pre-glob": bash("rg --pre-glob '*.gz' --pre sh x ~/.claude"),
        "rg-relative-glob-after-cd": bash("cd ~/.claude && rg -l x *.md"),
        "rg-loop-variable": bash("for f in $(ls ~/.claude/p/*.jsonl); do rg -c x $f; done"),
        "write": tool("Write", file_path=f"{folder}/agents/x.md", content="x"),
        "write-json": tool("Write", file_path=f"{user}/.claude.json", content="{}"),
        "edit": tool("Edit", file_path=f"{folder}/CLAUDE.md", old_string="a", new_string="b"),
        "notebook-edit": tool("NotebookEdit", notebook_path=f"{folder}/skills/x.ipynb"),
        "mcp-path": mcp,
        "mcp-command": mcp_command,
    }


FOLDER_IDS = list(folder_calls(Path("/x"), Path("/y")))


@pytest.mark.parametrize("name", FOLDER_IDS)
def test_a_restricted_session_cannot_change_claude_codes_user_folder(
    run: Run, store: Path, name: str
) -> None:
    """Refused for a restricted session, by this zone or by a zone of a file inside it that names
    the call first; a plain session is not refused."""
    payload = folder_calls(home(), claude_dir())[name]
    proc = run(payload, role="pm")
    assert proc.returncode == 2
    assert "cannot write to" in proc.stderr
    assert run(payload).returncode == 0


@pytest.mark.parametrize("name", FOLDER_IDS)
def test_the_user_folder_zone_refuses_each_call_by_itself(
    store: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    """With no other zone, so each call shows a hole in this one."""
    monkeypatch.setattr(pg, "_zones", lambda _store: (pg._claude_folder_zone(),))
    message = decide(folder_calls(home(), claude_dir())[name], store, "pm")
    assert message is not None
    assert FOLDER_REFUSAL in message
    assert decide(folder_calls(home(), claude_dir())[name], store) is None


def test_a_restricted_session_cannot_remove_the_home_folder(run: Run, store: Path) -> None:
    """Whichever zone names it first: `~` holds the user folder and the store."""
    payload = bash("rm -rf ~")
    assert run(payload, role="pm").returncode == 2
    assert run(payload).returncode == 0


def test_a_custom_claude_config_dir_is_a_zone_where_it_is(
    run: Run, store: Path, tmp_path: Path
) -> None:
    folder = tmp_path / "elsewhere"
    env = {"CLAUDE_CONFIG_DIR": str(folder)}
    for payload in (
        bash(f"rm -rf {folder}/skills"),
        bash("rm -rf $CLAUDE_CONFIG_DIR/skills"),
        bash(f"cd {folder} && rm -rf skills"),
        bash(f"echo x > {folder}/CLAUDE.md"),
        bash("rm -rf ~/.claude/skills"),
        bash("rm ~/.claude.json"),
        tool("Write", file_path=f"{folder}/agents/x.md"),
    ):
        proc = run(payload, role="pm", env=env)
        assert proc.returncode == 2, payload
        assert FOLDER_REFUSAL in proc.stderr
        assert run(payload, env=env).returncode == 0
    assert run(bash(f"cd {folder} && ls skills"), role="pm", env=env).returncode == 0
    assert run(bash(f"cat {folder}/CLAUDE.md"), role="pm", env=env).returncode == 0


def test_a_custom_claude_config_dir_is_followed_after_a_cd(run: Run, store: Path) -> None:
    folder = home() / "cfgdir"
    env = {"CLAUDE_CONFIG_DIR": str(folder)}
    for command in ("cd ~ && touch cfgdir/x", "cd ~ && sed -i s/a/b/ cfgdir/CLAUDE.md"):
        proc = run(bash(command), role="pm", env=env)
        assert proc.returncode == 2, command
        assert FOLDER_REFUSAL in proc.stderr
        assert run(bash(command), env=env).returncode == 0
    assert run(bash("cd ~ && cat cfgdir/CLAUDE.md"), role="pm", env=env).returncode == 0


def folder_reads(user: Path, folder: Path) -> dict[str, Payload]:
    """Calls a restricted session may make: reading the user folder, and writing next to it."""
    worktree = user / "proj" / ".claude" / "worktrees" / "x"
    return {
        "cat": bash("cat ~/.claude/CLAUDE.md"),
        "cat-home-variable": bash('cat "$HOME/.claude/CLAUDE.md"'),
        "cat-literal-home": bash(f"cat {user}/.claude/CLAUDE.md"),
        "cat-config-dir-variable": bash("cat $CLAUDE_CONFIG_DIR/CLAUDE.md"),
        "ls": bash("ls -la ~/.claude/skills"),
        "ls-folder": bash("ls ~/.claude"),
        "head-json": bash("head ~/.claude.json"),
        "wc-json": bash("wc -c ~/.claude.json"),
        "rg": bash("rg foo ~/.claude/skills"),
        "rg-options": bash("rg -n -i --glob '*.md' foo ~/.claude/agents"),
        "rg-files": bash("rg --files ~/.claude/skills"),
        "rg-absolute-glob": bash(f"rg -o 'x[^\"]{{0,40}}' {folder}/p/*.jsonl | sort -u"),
        "grep": bash("grep -r x ~/.claude/agents"),
        "find": bash("find ~/.claude/skills -name '*.md'"),
        "find-print": bash("find ~/.claude -maxdepth 2 -type d -print"),
        "sed-print": bash("sed -n 1,5p ~/.claude/CLAUDE.md"),
        "read-piped": bash("cat ~/.claude/CLAUDE.md | grep x | head -3"),
        "ls-piped-to-wc": bash("ls ~/.claude/skills | wc -l"),
        "read-then-read": bash("ls ~/.claude/skills && cat ~/.claude/CLAUDE.md"),
        "diff": bash("diff ~/.claude/CLAUDE.md /tmp/CLAUDE.md"),
        "cd-ls": bash("cd ~/.claude && ls skills"),
        "cd-ls-bare": bash("cd ~/.claude && ls"),
        "cd-grep-discarding-errors": bash(
            "cd ~/.claude/projects/p && grep -l foo *.jsonl 2>/dev/null | head"
        ),
        "cd-ls-errors-to-output": bash("cd ~/.claude/projects/p && ls -t 2>&1 | head -5"),
        "cd-ls-to-stderr": bash("cd ~/.claude && ls >&2"),
        "cd-ls-numbered-to-stderr": bash("cd ~/.claude && ls 1>&2"),
        "cd-ls-to-null": bash("cd ~/.claude && ls >/dev/null"),
        "cd-ls-both-to-null": bash("cd ~/.claude && ls &>/dev/null"),
        "jq": bash("jq '.projects | keys' ~/.claude.json"),
        "jq-options": bash("jq -r --arg k x '.[$k]' ~/.claude.json"),
        "jq-files": bash("jq --slurpfile s ~/.claude/a.json -f ~/.claude/p.jq ~/.claude.json"),
        "jq-piped": bash("cat ~/.claude/projects/p/a.jsonl | jq -c .type | head -3"),
        "cd-jq": bash("cd ~/.claude && jq . history.json | head"),
        "loop-over-files": bash(
            "for f in ~/.claude/projects/p/a.jsonl ~/.claude/projects/p/b.jsonl;"
            ' do grep -c foo "$f"; done'
        ),
        "loop-over-listing": bash(
            "cd ~/.claude/projects/p && for f in $(ls -t *.jsonl | head -3);"
            ' do grep -l foo "$f"; done'
        ),
        "loop-over-glob-with-pipe": bash(
            "for f in ~/.claude/projects/*/x.jsonl;"
            ' do echo "== $f"; grep -o \'k.\\{0,40\\}\' "$f" | head -3; done'
        ),
        "loop-over-find": bash(
            'for f in $(find ~/.claude/projects -name "*.jsonl"); do wc -l "$f"; done'
        ),
        "loop-multiline": bash('for f in ~/.claude/a ~/.claude/b\ndo\n  cat "$f"\ndone'),
        "loop-discarding-errors": bash('for f in ~/.claude/a; do cat "$f" 2>/dev/null; done'),
        "loop-body-reads-path": bash('for f in ~/.claude/a; do cat ~/.claude/b "$f"; done'),
        "cd-relative-ls": bash("cd .claude && ls skills", cwd=str(user)),
        "read-redirected-elsewhere": bash("cat ~/.claude/CLAUDE.md > /tmp/x"),
        "echo-the-folder": bash("echo $CLAUDE_CONFIG_DIR"),
        "echo-text-naming-it": bash("echo see ~/.claude/CLAUDE.md > /tmp/n.md"),
        "cp-out-of-it": bash("cp ~/.claude/CLAUDE.md ~/.cache/c.md"),
        "cp-folder-out": bash("cp -a ~/.claude ~/.cache/c"),
        "tar-out-of-it": bash("tar -czf ~/.cache/b.tgz ~/.claude/skills"),
        "read-discarding-errors": bash("ls ~/.claude 2>/dev/null"),
        "cd-reads": bash("cd ~/.claude && cat CLAUDE.md && rg foo skills | head"),
        "cd-semicolon-reads": bash("cd ~/.claude; ls skills; pwd"),
        "cd-echo": bash("cd ~/.claude && echo skills && printf x && true"),
        "cd-config-dir-variable-ls": bash("cd $CLAUDE_CONFIG_DIR && ls skills"),
        "cd-home-variable-ls": bash("cd $HOME/.claude && ls skills"),
        "cd-find": bash("cd ~/.claude && find skills -name '*.md'"),
        "environment-reader": bash("LC_ALL=C grep -c x ~/.claude/a.jsonl"),
        "timeout-reader": bash("timeout 30 grep x ~/.claude/a"),
        "nice-reader": bash("nice -n 5 rg x ~/.claude/a"),
        "env-unset-reader": bash("env -u X grep x ~/.claude/a"),
        "reader-by-system-path": bash("/usr/bin/grep x ~/.claude/a"),
        "reader-in-bin": bash("/bin/ls ~/.claude"),
        "filters": bash(
            "grep -l k ~/.claude/p/*.jsonl | sort -u | uniq -c | cut -d: -f1 | tr a b"
            " | column -t | basename -a $(head -1) | wc -l"
        ),
        "filter-sed-jq": bash("cat ~/.claude/a.jsonl | sed -n 2p | jq .type | tail -1"),
        "read-to-file-outside": bash("grep x ~/.claude/a > /tmp/out"),
        "read-to-file-then-count": bash("grep x ~/.claude/a > /tmp/out; wc -l /tmp/out"),
        "read-word-naming-a-shell": bash("grep -c bash ~/.claude/a > /tmp/n"),
        "script-output-to-file": bash("bash ~/.claude/skills/x/run.sh > /tmp/log"),
        "read-to-file-then-assign": bash("tr -d x < ~/.claude/a > /tmp/b; F=/tmp/b; wc -l $F"),
        "read-to-file-then-dot-pattern": bash("cut -c1-9 ~/.claude/a > /tmp/b; grep -c . /tmp/b"),
        "note-written-then-script-reads-elsewhere": bash(
            "echo done > /tmp/n; sh ~/proj/x.sh; J=~/.claude/a; grep -c x $J"
        ),
        "read-appended-outside": bash("grep x ~/.claude/a >> /tmp/out"),
        "read-sorted-to-file-outside": bash("ls ~/.claude/p | sort > /tmp/x"),
        "input-redirect-wc": bash("wc -l < ~/.claude/a.jsonl"),
        "input-redirect-grep": bash("grep x < ~/.claude/a.jsonl"),
        "read-loop-over-file": bash('while read f; do wc -l "$f"; done < ~/.claude/list.txt'),
        "xargs-basename": bash("ls -d ~/.claude/p/* | xargs -n1 basename"),
        "xargs-grep": bash("find ~/.claude/p -name '*.jsonl' | xargs grep -l x"),
        "xargs-null-wc": bash("find ~/.claude/p -name '*.jsonl' -print0 | xargs -0 wc -l"),
        "xargs-options-head": bash("ls -d ~/.claude/p/* | xargs -r -P4 -n 2 head -3"),
        "xargs-long-options-cat": bash("ls -d ~/.claude/p/* | xargs --null --max-args=1 cat"),
        "xargs-to-file-outside": bash("ls -d ~/.claude/p/* | xargs grep -c x > /tmp/out"),
        "xargs-then-filter": bash("ls -d ~/.claude/p/* | xargs grep -h x | sort | uniq -c"),
        "cd-in-xargs-ls": bash("cd ~/.claude/p && grep -rl x . | xargs -r ls -t | head -3"),
        "cd-in-xargs-stat": bash("cd ~/.claude/p && ls | xargs stat -c %y | sort | uniq -c"),
        "find-loop": bash(
            'find ~/.claude/p -name "*.jsonl" | while read f; do grep -c x "$f"; done'
        ),
        "substitution-after-cd": bash(
            "cd ~/.claude/projects/p && ls -la $(grep -l k *.jsonl | head -5)"
        ),
        "substitution-name": bash('f=$(grep -l k ~/.claude/p/*.jsonl | head -1); grep -o x "$f"'),
        "substitution-count-test": bash(
            'cd ~/.claude && n=$(grep -c x a.jsonl); [ "$n" != "0" ] && echo hit'
        ),
        "test-after-cd": bash("cd ~/.claude && [ -f CLAUDE.md ] && ls skills"),
        "test-command": bash("test -d ~/.claude/skills && ls ~/.claude/skills"),
        "find-tests": bash(
            "find ~/.claude -maxdepth 3 -iname '*.md' -newermt 2026-01-01 -size +1k"
            " -not -path '*/x/*' -ipath '*s*' -printf '%p\\n'"
        ),
        "sed-range-to-end": bash("sed -n '/start/,$p' ~/.claude/a.md"),
        "cd-cp-out": bash("cd ~/.claude && cp CLAUDE.md /tmp/x.md"),
        "cd-cp-several-out": bash("cd ~/.claude && cp -p a b /var/tmp/"),
        "heredoc-text-to-cat": bash("cat > /tmp/n.md <<'EOF'\nsee ~/.claude/CLAUDE.md\nEOF"),
        "heredoc-text-to-tee": bash("tee /tmp/n.md <<'EOF'\nsee ~/.claude/CLAUDE.md\nEOF"),
        "bound-name": bash("F=~/.claude/p/a.jsonl; sed -n '/a/,/b/p' \"$F\" | head -3"),
        "bound-name-test": bash('J=~/.claude/x.jsonl; [ -f "$J" ] && grep -c x $J'),
        "bound-name-in-text-with-other-commands": bash(
            "docker ps; F=~/.claude/a.txt; sed -n 1p $F | cut -c1-20; git log -1"
        ),
        "bound-name-assigned-twice": bash(
            'J=~/.claude/x.jsonl; n=$(grep -c x $J); [ "$n" -ge 2 ] || { n=$(grep -c y $J); echo $n; }'
        ),
        "bound-name-folder-listing": bash(
            'W=~/.claude/w; ls $W | tail -2; for f in $W/a-*; do wc -l < "$f"; done'
        ),
        "sleep-until": bash(
            'until [ "$(grep -c x ~/.claude/p/j.jsonl)" -ge 2 ]; do sleep 5; done; echo ok'
        ),
        "command-wrapper": bash("command sleep 1; ls ~/.claude"),
        "cwd-in-folder-reads": bash(
            "ls skills | head -3; grep -c x CLAUDE.md; find . -name '*.md' | sort | wc -l",
            cwd=str(folder),
        ),
        "cwd-in-folder-copy-out": bash("cp CLAUDE.md /var/tmp/out", cwd=str(folder)),
        "backslash-in-quoted-pattern-of-substitution": bash(
            "cd ~/.claude/projects && find . -maxdepth 1 -name '*.jsonl' -newermt '2026-09-11'"
            ' | while read f; do n=$(grep -c "a\\|b" "$f"); echo "$(stat -c %y "$f") $n"; done'
            " | sort"
        ),
        "group-reads": bash(
            'n=$(grep -c x ~/.claude/p/j.jsonl); [ "$n" -gt 0 ] && { echo found $n; exit 0; }'
        ),
        "bash-c-reads": bash("bash -c 'grep -c x ~/.claude/a.jsonl | head -1'"),
        "cd-away-quoted-glob": bash(
            "cd /tmp/x && git log --grep 'a.*b' | head -3; cat ~/.claude/a"
        ),
        "read-tool": tool("Read", file_path=f"{folder}/CLAUDE.md"),
        "read-tool-json": tool("Read", file_path=f"{user}/.claude.json"),
        "grep-tool": tool("Grep", pattern="x", path=f"{folder}/agents"),
        "glob-tool": tool("Glob", pattern="*.md", path=f"{folder}/skills"),
        "write-tmp": bash("echo x > /tmp/x"),
        "rm-tmp": bash("rm -rf /tmp/x"),
        "write-cache": bash("echo x > ~/.cache/x"),
        "rm-cache": bash("rm -rf ~/.cache/x"),
        "cd-cache-rm": bash("cd ~/.cache && rm -rf x"),
        "write-cache-tool": tool("Write", file_path=f"{user}/.cache/x", content="x"),
        "write-backup": bash("echo x > ~/.claude-backup/x"),
        "write-json-lookalike": bash("echo x > ~/.claude.json-x"),
        "write-json-prefix-lookalike": bash("echo x > ~/.claude.jsonl"),
        "read-json-backup": bash("cat ~/.claude.json.backup"),
        "grep-json-backups": bash("grep -c x ~/.claude.json.*"),
        "list-json-backups": bash("ls -la ~/.claude.json.* | head"),
        "cd-home-list-json-backups": bash("cd ~ && ls .claude.json.*"),
        "read-tool-json-backup": tool("Read", file_path=f"{user}/.claude.json.backup"),
        "rm-other-dotfile-glob": bash("rm -f ~/.cache/*.bak"),
        "write-worktree-tool": tool("Write", file_path=f"{worktree}/f.py", content="x"),
        "edit-worktree-tool": tool(
            "Edit", file_path=f"{worktree}/f.py", old_string="a", new_string="b"
        ),
        "rm-worktree": bash(f"rm -rf {worktree}"),
        "rm-worktree-tilde": bash("rm -rf ~/proj/.claude/worktrees/x"),
        "redirect-worktree": bash(f"echo x > {worktree}/f.py"),
        "cd-worktree-redirect": bash(f"cd {worktree} && echo x > f.py"),
        "cd-worktree-rm": bash(f"cd {worktree} && rm -rf build"),
        "cwd-worktree-redirect": bash("echo x > f.py", cwd=str(worktree)),
        "cwd-worktree-rm": bash("rm -rf build", cwd=str(worktree)),
        "mkdir-worktree": bash("mkdir -p ~/proj/.claude/worktrees/y"),
        "cp-into-worktree": bash("cp -r a ~/proj/.claude/worktrees/x/"),
        "git-worktree-add": bash("git worktree add ~/proj/.claude/worktrees/y"),
        "git-in-worktree": bash("git -C ~/proj/.claude/worktrees/x commit -m x"),
        "cd-repo-git-worktree-remove": bash("cd ~/proj && git worktree remove .claude/worktrees/x"),
        "ln-lookalike": bash("ln -s a ~/.claude-x"),
        "cwd-repo-relative-worktree-rm": bash("rm -rf .claude/worktrees/x", cwd=str(user / "proj")),
        "cd-home-cat-relative": bash("cd ~ && cat .claude/CLAUDE.md"),
        "cd-home-grep-relative": bash("cd ~ && grep -r x .claude/agents | head"),
        "cd-home-list-then-git": bash("cd ~ && ls -la .claude; git status"),
        "cd-project-touch": bash("cd ~/proj && touch notes.md"),
        "cd-home-touch-lookalike": bash("cd ~ && touch .claude-notes"),
        "cd-unknown-touch": bash('cd "$X" && touch notes.md'),
        "cd-project-edit-own-claude": bash("cd ~/proj && sed -i s/a/b/ .claude/worktrees/x/f.py"),
        "cd-home-heredoc-naming-it": bash(
            "cd ~ && cat > /tmp/n.md <<'EOF'\nsee .claude/CLAUDE.md\nEOF"
        ),
        "cd-home-commit-message": bash("cd ~/proj && git commit -m 'edit ../.claude/CLAUDE.md'"),
    }


@pytest.mark.parametrize(
    "command",
    [
        "rg foo ~/.claude/plugins",
        "rg foo ~/coven-memory",
        "rg foo ~/.config/coven",
        "rg foo ~/.claude/settings.json",
        "cd ~/coven-memory && ls",
        "cd ~/.config/coven && cat config.json",
        "cd ~/.claude/plugins && ls",
        "cat ~/.claude/settings.json",
        "cat ~/coven-memory/systems/guard.json",
        "jq . ~/.claude/settings.json",
        "jq . ~/coven-memory/systems/guard.json",
        "cd ~/coven-memory && ls 2>/dev/null",
        "cd ~/.config/coven && ls 2>&1",
        'for f in ~/.claude/plugins/*; do cat "$f"; done',
        'for f in ~/coven-memory/systems/*; do cat "$f"; done',
    ],
)
def test_the_other_zones_take_no_reader_of_the_user_folders_zone(
    run: Run, store: Path, command: str
) -> None:
    """`rg` and a `cd` that only reads are the user folder's own: the store, config, settings and
    plugins zones judge them as they did."""
    proc = run(bash(command), role="pm")
    assert proc.returncode == 2
    assert "cannot write to" in proc.stderr


PYTHON_READS_FOLDER = "python3 - <<'EOF'\nprint(open('~/.claude/projects/p/a.jsonl').read())\nEOF"


def test_an_interpreter_reading_the_user_folder_stays_refused(run: Run, store: Path) -> None:
    """python3 is no reader of the user folder: its code could write there as well. Pinned until
    the question is decided."""
    for command in (PYTHON_READS_FOLDER, "python3 -c \"print(open('~/.claude.json').read())\""):
        proc = run(bash(command), role="pm")
        assert proc.returncode == 2, command
        assert FOLDER_REFUSAL in proc.stderr
        assert run(bash(command)).returncode == 0


READS_IDS = list(folder_reads(Path("/x"), Path("/y")))


@pytest.mark.parametrize("name", READS_IDS)
def test_a_restricted_session_reads_claude_codes_user_folder_and_writes_next_to_it(
    run: Run, store: Path, name: str
) -> None:
    (home() / "proj" / ".claude" / "worktrees" / "x").mkdir(parents=True)
    proc = run(folder_reads(home(), claude_dir())[name], role="pm")
    assert proc.returncode == 0, proc.stderr


# --- a session's own scratch folders: its CLAUDE_JOB_DIR and its transcript's session folder ---

SESSION_ID = "0b5d3c1e-2f4a-4b6c-8d9e-0123456789ab"
OTHER_SESSION_ID = "9f8e7d6c-5b4a-4321-8fed-cba987654321"
JOB_ID = SESSION_ID[:8]
OTHER_JOB_ID = OTHER_SESSION_ID[:8]


def job_folder(job: str = JOB_ID) -> Path:
    return claude_dir() / "jobs" / job


def session_folder(session: str = SESSION_ID) -> Path:
    return claude_dir() / "projects" / "-proj" / session


def transcript(session: str = SESSION_ID) -> str:
    return f"{session_folder(session)}.jsonl"


def in_session(payload: Payload, transcript_path: object = None) -> Payload:
    """The payload as Claude Code sends it for this session: with its transcript's path."""
    path = transcript() if transcript_path is None else transcript_path
    return {**payload, "transcript_path": path}


def job_env(job_dir: str | None = None) -> dict[str, str]:
    """The hook's own environment, where Claude Code sets CLAUDE_JOB_DIR for a background
    session."""
    return {"CLAUDE_JOB_DIR": str(job_folder()) if job_dir is None else job_dir}


def own_folder_writes(job: Path, session: Path) -> dict[str, Payload]:
    """Writes inside the session's own folders: allowed for a restricted session."""
    return {
        "write-in-job": tool("Write", file_path=f"{job}/notes.md", content="x"),
        "edit-in-job": tool("Edit", file_path=f"{job}/notes.md", old_string="a", new_string="b"),
        "write-in-session": tool("Write", file_path=f"{session}/plan.md", content="x"),
        "edit-in-session": tool(
            "Edit", file_path=f"{session}/plan.md", old_string="a", new_string="b"
        ),
        "notebook-in-job": tool("NotebookEdit", notebook_path=f"{job}/x.ipynb"),
        "redirect-job-variable": bash('echo x > "$CLAUDE_JOB_DIR/out.txt"'),
        "redirect-braced-job-variable": bash("echo x >> ${CLAUDE_JOB_DIR}/out.txt"),
        "redirect-job-literal": bash(f"echo x > {job}/out.txt"),
        "redirect-session-literal": bash(f"printf x > {session}/notes.md"),
        "mkdir-in-job": bash('mkdir -p "$CLAUDE_JOB_DIR/sub/deeper"'),
        "mkdir-the-job-folder": bash('mkdir -p "$CLAUDE_JOB_DIR"'),
        "mkdir-in-session": bash(f"mkdir -p {session}/scratch"),
        "cp-into-job": bash('cp /tmp/x "$CLAUDE_JOB_DIR/x"'),
        "cp-into-job-folder": bash('cp /tmp/x "$CLAUDE_JOB_DIR/"'),
        "cp-into-session": bash(f"cp -r /tmp/d {session}/d"),
        "rm-file-in-job": bash('rm "$CLAUDE_JOB_DIR/out.txt"'),
        "rm-folder-in-job": bash('rm -rf "$CLAUDE_JOB_DIR/sub"'),
        "rm-glob-in-job": bash('rm -f "$CLAUDE_JOB_DIR"/*.tmp'),
        "rm-file-in-session": bash(f"rm -f {session}/notes.md"),
        "mv-inside-job": bash('mv "$CLAUDE_JOB_DIR/a" "$CLAUDE_JOB_DIR/b"'),
        "tee-in-job": bash('ls | tee "$CLAUDE_JOB_DIR/list.txt"'),
        "sed-in-place-in-job": bash('sed -i s/a/b/ "$CLAUDE_JOB_DIR/notes.md"'),
        "touch-in-session": bash(f"touch {session}/x"),
        "cd-job-then-write": bash('cd "$CLAUDE_JOB_DIR" && echo x > out.txt && mkdir -p sub'),
        "cd-session-then-write": bash(f"cd {session} && echo x > notes.md"),
        "heredoc-into-job": bash("cat > \"$CLAUDE_JOB_DIR/s.md\" <<'EOF'\ntext\nEOF"),
        "read-then-write-in-job": bash('grep x ~/.claude/a > "$CLAUDE_JOB_DIR/hits.txt"'),
    }


OWN_WRITE_IDS = list(own_folder_writes(Path("/j"), Path("/s")))


@pytest.mark.parametrize("name", OWN_WRITE_IDS)
def test_a_restricted_session_writes_in_its_own_scratch_folders(
    run: Run, store: Path, name: str
) -> None:
    payload = in_session(own_folder_writes(job_folder(), session_folder())[name])
    proc = run(payload, role="pm", env=job_env())
    assert proc.returncode == 0, proc.stderr


@pytest.mark.parametrize("command", ["echo x > out.txt", "rm -rf sub", "mkdir -p a/b"])
def test_a_restricted_session_working_in_its_job_folder_writes_there(
    run: Run, store: Path, command: str
) -> None:
    job_folder().mkdir(parents=True)
    payload = in_session(bash(command, cwd=str(job_folder())))
    assert run(payload, role="pm", env=job_env()).returncode == 0


def own_folder_refusals(job: Path, session: Path, folder: Path) -> dict[str, Payload]:
    """What stays refused with the session's own folders known: the folders themselves, what is
    beside or above them, and their way out."""
    other_job, other_session = folder / "jobs" / OTHER_JOB_ID, session.parent / OTHER_SESSION_ID
    return {
        "rm-job-folder": bash('rm -rf "$CLAUDE_JOB_DIR"'),
        "rm-job-folder-slash": bash('rm -rf "$CLAUDE_JOB_DIR/"'),
        "rm-job-folder-literal": bash(f"rm -rf {job}"),
        "rm-job-folder-dot": bash('rm -rf "$CLAUDE_JOB_DIR/."'),
        "rm-session-folder": bash(f"rm -rf {session}"),
        "mv-job-folder": bash('mv "$CLAUDE_JOB_DIR" /tmp/j'),
        "mv-into-job-folder-name": bash('mv /tmp/l "$CLAUDE_JOB_DIR"'),
        "chmod-job-folder": bash('chmod 000 "$CLAUDE_JOB_DIR"'),
        "chmod-session-folder": bash(f"chmod -R 000 {session}"),
        "ln-job-folder": bash('ln -sfn ~/.claude/skills "$CLAUDE_JOB_DIR"'),
        "ln-inside-job": bash('ln -s ../../skills "$CLAUDE_JOB_DIR/s"'),
        "find-delete-job-folder": bash('find "$CLAUDE_JOB_DIR" -name "*.tmp" -delete'),
        "rm-jobs-root": bash("rm -rf ~/.claude/jobs"),
        "rm-projects-root": bash("rm -rf ~/.claude/projects"),
        "rm-project-of-session": bash(f"rm -rf {session.parent}"),
        "write-jobs-root": bash("echo x > ~/.claude/jobs/x"),
        "write-projects-root": tool("Write", file_path=f"{folder}/projects/x.md", content="x"),
        "write-transcript": bash(f"echo x >> {session}.jsonl"),
        "write-transcript-tool": tool("Write", file_path=f"{session}.jsonl", content="x"),
        "write-other-job": bash(f"echo x > {other_job}/x"),
        "write-other-job-tool": tool("Write", file_path=f"{other_job}/x", content="x"),
        "write-other-session": bash(f"echo x > {other_session}/x"),
        "write-other-session-tool": tool("Write", file_path=f"{other_session}/x", content="x"),
        "glob-other-jobs": bash('rm -rf "$CLAUDE_JOB_DIR"*'),
        "glob-beside-job": bash('rm -rf "$CLAUDE_JOB_DIR"/../*'),
        "escape-job": bash(f'echo x > "$CLAUDE_JOB_DIR/../{OTHER_JOB_ID}/x"'),
        "escape-job-tool": tool("Write", file_path=f"{job}/../{OTHER_JOB_ID}/x", content="x"),
        "escape-session": bash(f"echo x > {session}/../{OTHER_SESSION_ID}/x"),
        "escape-session-tool": tool(
            "Write", file_path=f"{session}/../{OTHER_SESSION_ID}/x", content="x"
        ),
        "escape-to-skills": bash('echo x > "$CLAUDE_JOB_DIR/../../skills/x"'),
        "cd-job-then-up": bash('cd "$CLAUDE_JOB_DIR" && rm -rf ../x'),
        "subagents": bash(f"echo x > {session}/subagents/x"),
        "subagents-tool": tool("Write", file_path=f"{session}/subagents/a.jsonl", content="x"),
        "rm-subagents": bash(f"rm -rf {session}/subagents"),
        "glob-subagents": bash(f"rm -rf {session}/sub*"),
        "glob-session-contents": bash(f"rm -rf {session}/*"),
        "auto-memory": tool("Write", file_path=f"{session.parent}/memory/MEMORY.md", content="x"),
        "auto-memory-bash": bash(f"echo x >> {session.parent}/memory/MEMORY.md"),
        "skills": bash("echo x > ~/.claude/skills/x"),
        "claude-json": bash("echo x > ~/.claude.json"),
        "inline-job-variable": bash('CLAUDE_JOB_DIR=~/.claude/skills; rm -rf "$CLAUDE_JOB_DIR/x"'),
        "inline-job-variable-prefix": bash(
            "CLAUDE_JOB_DIR=~/.claude/skills sh -c 'echo x > \"$CLAUDE_JOB_DIR/y\"'"
        ),
        "exported-job-variable": bash(
            'export CLAUDE_JOB_DIR=~/.claude/skills && echo x > "$CLAUDE_JOB_DIR/y"'
        ),
        "unset-job-variable": bash('unset CLAUDE_JOB_DIR; echo x > "$CLAUDE_JOB_DIR/y"'),
        "read-into-job-variable": bash(
            'read CLAUDE_JOB_DIR < /tmp/p; echo x > "$CLAUDE_JOB_DIR/y"'
        ),
        "loop-job-variable": bash(
            'for CLAUDE_JOB_DIR in ~/.claude/skills; do echo x > "$CLAUDE_JOB_DIR/y"; done'
        ),
    }


OWN_REFUSAL_IDS = list(own_folder_refusals(Path("/j"), Path("/s"), Path("/f")))


@pytest.mark.parametrize("name", OWN_REFUSAL_IDS)
def test_a_restricted_session_cannot_change_its_scratch_folders_or_what_is_beside_them(
    run: Run, store: Path, name: str
) -> None:
    payload = in_session(own_folder_refusals(job_folder(), session_folder(), claude_dir())[name])
    proc = run(payload, role="pm", env=job_env())
    assert proc.returncode == 2
    assert FOLDER_REFUSAL in proc.stderr
    assert run(payload, env=job_env()).returncode == 0


@pytest.mark.parametrize("command", ["rm -rf .", "rm -rf ..", "rm -rf ../x", "chmod 000 ."])
def test_a_restricted_session_working_in_its_job_folder_changes_nothing_around_it(
    run: Run, store: Path, command: str
) -> None:
    job_folder().mkdir(parents=True)
    payload = in_session(bash(command, cwd=str(job_folder())))
    assert run(payload, role="pm", env=job_env()).returncode == 2


def test_a_restricted_session_working_in_another_job_folder_writes_nothing_there(
    run: Run, store: Path
) -> None:
    job_folder(OTHER_JOB_ID).mkdir(parents=True)
    payload = in_session(bash("echo x > out.txt", cwd=str(job_folder(OTHER_JOB_ID))))
    assert run(payload, role="pm", env=job_env()).returncode == 2


@pytest.mark.parametrize(
    "job_dir",
    ["", "jobs/0b5d3c1e", "JOBS", "SKILLS", "JOBS/0b5d3c1e/deeper", "/tmp/jobs/0b5d3c1e"],
    ids=["empty", "relative", "jobs-root", "not-under-jobs", "nested", "another-folder"],
)
def test_a_job_folder_that_is_not_one_widens_nothing(run: Run, store: Path, job_dir: str) -> None:
    """Only a CLAUDE_JOB_DIR that is a folder right under `<claude folder>/jobs/` is a scratch
    folder: any other value leaves the zone whole."""
    value = job_dir.replace("JOBS", str(claude_dir() / "jobs"))
    value = value.replace("SKILLS", str(claude_dir() / "skills"))
    for payload in (
        bash(f"echo x > {job_folder()}/out.txt"),
        bash(f"echo x > {claude_dir()}/skills/x"),
        bash(f"mkdir -p {claude_dir()}/jobs/0b5d3c1e/deeper/x"),
        tool("Write", file_path=f"{job_folder()}/notes.md", content="x"),
    ):
        proc = run(payload, role="pm", env=job_env(value))
        assert proc.returncode == 2, (payload, proc.stderr)


def test_without_a_job_folder_the_jobs_folder_stays_refused(run: Run, store: Path) -> None:
    payload = bash(f"echo x > {job_folder()}/out.txt")
    proc = run(payload, role="pm", env={"CLAUDE_JOB_DIR": ""})
    assert proc.returncode == 2
    assert FOLDER_REFUSAL in proc.stderr


@pytest.mark.parametrize(
    "transcript_path",
    [
        None,
        "",
        7,
        f"projects/-proj/{SESSION_ID}.jsonl",
        "CLAUDE/SESSION.jsonl",
        "CLAUDE/projects/SESSION.jsonl",
        "CLAUDE/projects/-proj/x/SESSION.jsonl",
        "CLAUDE/projects/-proj/SESSION",
        "CLAUDE/projects/-proj/SESSION.txt",
        "CLAUDE/projects/-proj/memory.jsonl",
        "CLAUDE/projects/-proj/../-proj/SESSION.jsonl",
        f"/tmp/projects/-proj/{SESSION_ID}.jsonl",
    ],
    ids=[
        "missing",
        "empty",
        "not-a-string",
        "relative",
        "not-under-projects",
        "projects-root",
        "too-deep",
        "no-jsonl",
        "other-suffix",
        "memory-name",
        "dot-dot",
        "another-folder",
    ],
)
def test_an_odd_transcript_path_widens_nothing(
    run: Run, store: Path, transcript_path: object
) -> None:
    """Only a transcript right under `<claude folder>/projects/<project>/`, named for a session id,
    gives a scratch folder; anything else leaves the zone whole."""
    if isinstance(transcript_path, str):
        transcript_path = transcript_path.replace("CLAUDE", str(claude_dir()))
        transcript_path = transcript_path.replace("SESSION", SESSION_ID)
    extra = {} if transcript_path is None else {"transcript_path": transcript_path}
    for payload in (
        tool("Write", file_path=f"{session_folder()}/x.md", content="x"),
        bash(f"echo x > {session_folder()}/x.md"),
        bash(f"echo x > {session_folder().parent}/memory/x"),
    ):
        proc = run({**payload, **extra}, role="pm", env=job_env(""))
        assert proc.returncode == 2, (payload, proc.stderr)


def test_a_symlink_out_of_a_scratch_folder_is_followed(run: Run, store: Path) -> None:
    """The guard resolves a path that exists through its symlinks (os.path.realpath), so a link in
    the job folder that points at the skills folder is the skills folder."""
    job_folder().mkdir(parents=True)
    (claude_dir() / "skills").mkdir()
    (job_folder() / "out").symlink_to(claude_dir() / "skills")
    for payload in (
        bash('echo x > "$CLAUDE_JOB_DIR/out/y"'),
        tool("Write", file_path=f"{job_folder()}/out/y", content="x"),
    ):
        assert run(in_session(payload), role="pm", env=job_env()).returncode == 2


def test_a_job_folder_that_is_a_symlink_elsewhere_is_no_scratch_folder(
    run: Run, store: Path
) -> None:
    (claude_dir() / "skills").mkdir(parents=True)
    (claude_dir() / "jobs").mkdir()
    job_folder().symlink_to(claude_dir() / "skills")
    payload = bash(f"echo x > {claude_dir()}/skills/y")
    assert run(payload, role="pm", env=job_env()).returncode == 2


def test_the_settings_zone_still_holds_inside_a_scratch_folder(run: Run, store: Path) -> None:
    """The scratch folders are the user folder zone's alone: a settings file in one stays the
    settings zone's."""
    for payload in (
        bash('echo {} > "$CLAUDE_JOB_DIR/.claude/settings.json"'),
        tool("Write", file_path=f"{session_folder()}/.claude/settings.local.json", content="{}"),
    ):
        proc = run(in_session(payload), role="pm", env=job_env())
        assert proc.returncode == 2
        assert SETTINGS_REFUSAL in proc.stderr


@pytest.mark.parametrize("name", ["redirect-job-literal", "write-in-session", "mkdir-in-job"])
def test_a_plain_session_writes_in_the_scratch_folders_as_before(
    run: Run, store: Path, name: str
) -> None:
    payload = in_session(own_folder_writes(job_folder(), session_folder())[name])
    assert run(payload, env=job_env()).returncode == 0


# --- the user folder: a write the guard cannot pin to a path, or that may alias a folder ---


def pinned(text: str) -> str:
    """A case with its folders written in: H home, C the user folder, J the job folder, S the
    session folder."""
    return (
        text.replace("{S}", str(session_folder()))
        .replace("{J}", str(job_folder()))
        .replace("{C}", str(claude_dir()))
        .replace("{H}", str(home()))
    )


# Written words with an expansion the guard does not follow: each fails closed when it names
# `.claude`, puts the expansion right under home or `/`, or climbs from a folder it cannot pin.
UNPINNED_WRITES: dict[str, tuple[str, str]] = {
    "variable-holds-home": ("D=~; rm -rf $D/.claude/skills", "/tmp"),
    "stripped-home": ("rm -rf ${HOME%/}/.claude/skills", "/tmp"),
    "stripped-home-mv": ("mv /tmp/e.md ${HOME%/}/.claude/CLAUDE.md", "/tmp"),
    "stripped-home-json": ("echo {} > ${HOME%/}/.claude.json", "/tmp"),
    "stripped-home-cp": ("cp /tmp/s.json ${HOME%/}/.claude/agents/x.md", "/tmp"),
    "stripped-home-ln": ("ln -sf /tmp/x ${HOME%/}/.claude/skills/x", "/tmp"),
    "stripped-config-dir": ("rm -rf ${CLAUDE_CONFIG_DIR%/}/skills", "/tmp"),
    "defaulted-home": ('rm -rf "${HOME:-/}"/.claude', "/tmp"),
    "split-name": ("rm -rf ~/.cl${X}aude/skills", "/tmp"),
    "split-name-plain": ("rm -rf ~/.cl$Xaude/skills", "/tmp"),
    "split-json": ("echo {} > ~/.cl${X}aude.json", "/tmp"),
    "name-in-variable": ("N=.claude; rm -rf ~/$N/skills", "/tmp"),
    "array-holds-home": ("a=(~); rm -rf ${a[0]}/.claude/skills", "/tmp"),
    "substitution-dirname": ('rm -rf "$(dirname ~/x)/.claude/skills"', "/tmp"),
    "substitution-pwd": ('rm -rf "$(pwd)/../.claude/skills"', "{H}/proj"),
    "backtick-pwd": ("rm -rf `pwd`/../.claude/skills", "{H}/proj"),
    "tilde-plus": ("rm -rf ~+/../.claude/skills", "{H}/proj"),
    "tilde-plus-two-up": ("rm -rf ~+/../../.claude/skills", "{H}/proj/sub"),
    "pwd-variable": ('rm -rf "$PWD"/../.claude/skills', "{H}/proj"),
    "pwd-variable-two-up": ('rm -rf "$PWD"/../../.claude/skills', "{H}/proj/sub"),
    "brace-climb": ("rm -rf {.,}./.claude/skills", "{H}/proj"),
    "brace-climb-after": ("rm -rf .{.,}/.claude/skills", "{H}/proj"),
    "tilde-minus": ("cd ~ && cd /tmp && rm -rf ~-/.claude/skills", "/tmp"),
    "oldpwd": ("cd ~ && cd /tmp && rm -rf $OLDPWD/.claude/skills", "/tmp"),
    "dir-stack": ("cd ~ && pushd /tmp && rm -rf ~1/.claude/skills", "/tmp"),
    "unknown-climb": ("rm -rf $X/../../skills", "{C}/jobs"),
    "cd-unknown-climb": ("cd $X/.. && rm -rf skills", "/tmp"),
    "cd-tilde-plus": ("cd ~+/.. && rm -rf .claude/skills", "{H}/proj"),
    "job-defaulted": ("rm -rf ${CLAUDE_JOB_DIR:-x}/../../skills", "/tmp"),
    "job-stripped": ("echo x > ${CLAUDE_JOB_DIR%/*}/../CLAUDE.md", "/tmp"),
    "job-stripped-twice": ("rm -rf ${CLAUDE_JOB_DIR%/*/*}/skills", "/tmp"),
    "job-unset-climb": ('rm -rf "$CLAUDE_JOB_DIR/../../skills"; export A=1', "/tmp"),
    "job-zsh-modifier": ("rm -rf $CLAUDE_JOB_DIR:h/../skills", "/tmp"),
    "job-glued": ("rm -rf x$CLAUDE_JOB_DIR/../skills", "/tmp"),
    "job-escaped": ("cd .. && echo x > \\$CLAUDE_JOB_DIR", "{J}"),
    "job-single-quoted": ("cd .. && touch '$CLAUDE_JOB_DIR'", "{J}"),
    "piped-heredoc-to-shell": ("cat <<'E' | sh\nrm -rf ${HOME%/}/.cl${X}aude\nE", "/srv"),
}

# Written words with an expansion that stays pinned or far from the folder, and reads.
PINNED_WRITES: dict[str, tuple[str, str]] = {
    "unknown-variable-folder": ('echo x > "$OUT/result.txt"', "/tmp"),
    "substitution-in-tmp": ("echo x > /srv/run-$(date +%s).log", "/tmp"),
    "brace-in-tmp": ("mkdir -p /tmp/w/{a,b}", "/tmp"),
    "pid-in-tmp": ("rm -rf /srv/x-$$", "/tmp"),
    "home-variable-elsewhere": ('echo x > "$HOME/proj/out.txt"', "/tmp"),
    "job-folder-file": ('echo x > "$CLAUDE_JOB_DIR/out.txt"', "/tmp"),
    "job-braced-file": ("echo x > ${CLAUDE_JOB_DIR}/out.txt", "/tmp"),
    "read-to-named-file": ("cat ~/.claude/CLAUDE.md > /srv/$(date +%s).md", "/tmp"),
    "python-heredoc-markdown": (
        "cd /srv/docs && python3 - <<'PY'\ns = '> `A or B`.** settled'\nPY",
        "/srv",
    ),
    "python-heredoc-then-piped-substitution": (
        "python3 - <<'PY'\ns = '> `A or B`.** settled'\nPY\n"
        'sed -n "$(grep -n x /srv/d.md | cut -d: -f1),+4p" /srv/d.md',
        "/srv",
    ),
}

UNPINNED_IDS = list(UNPINNED_WRITES)
PINNED_IDS = list(PINNED_WRITES)


@pytest.mark.parametrize("name", UNPINNED_IDS)
def test_a_written_word_the_guard_cannot_pin_fails_closed_near_the_user_folder(
    run: Run, store: Path, name: str
) -> None:
    command, cwd = (pinned(text) for text in UNPINNED_WRITES[name])
    payload = in_session(bash(command, cwd=cwd))
    assert run(payload, role="pm", env=job_env()).returncode == 2
    assert run(payload, env=job_env()).returncode == 0


@pytest.mark.parametrize("name", PINNED_IDS)
def test_a_written_word_with_an_expansion_far_from_the_user_folder_runs(
    run: Run, store: Path, name: str
) -> None:
    command, cwd = (pinned(text) for text in PINNED_WRITES[name])
    proc = run(in_session(bash(command, cwd=cwd)), role="pm", env=job_env())
    assert proc.returncode == 0, proc.stderr


def test_a_job_variable_with_no_job_folder_still_fails_closed_when_it_climbs(
    run: Run, store: Path
) -> None:
    payload = bash('rm -rf "$CLAUDE_JOB_DIR/../../skills"')
    assert run(payload, role="pm").returncode == 2


# Writes through a path that aliases a folder: /proc/self/cwd is where the shell is.
ALIASED_WRITES: dict[str, tuple[str, str]] = {
    "proc-cwd-after-cd": ("cd ~/.claude && echo x > /proc/self/cwd/CLAUDE.md", "/tmp"),
    "proc-cwd-cp": ("cd ~/.claude/skills/x && cp /tmp/e.md /proc/self/cwd/SKILL.md", "/tmp"),
    "proc-cwd-tee": ("cd ~/.claude/agents && echo x | tee /proc/self/cwd/a.md", "/tmp"),
    "proc-cwd-from-cwd": ("echo x > /proc/self/cwd/CLAUDE.md", "{C}"),
    "proc-thread-self": ("cd ~/.claude && echo x > /proc/thread-self/cwd/CLAUDE.md", "/tmp"),
    "proc-pid-root": ("echo x > /proc/1/root{C}/CLAUDE.md", "/tmp"),
    "proc-fd": ("cd ~/.claude && exec 3<. && echo x > /proc/self/fd/3/CLAUDE.md", "/tmp"),
    "dev-fd": ("cd ~/.claude && exec 3<. && echo x > /dev/fd/3/CLAUDE.md", "/tmp"),
    "proc-cwd-settings": ("cd ~/.claude && echo {} > /proc/self/cwd/settings.json", "/tmp"),
    "proc-cwd-plugins": ("cd ~/.claude/plugins && echo x > /proc/self/cwd/x", "/tmp"),
}

ALIASED_IDS = list(ALIASED_WRITES)


@pytest.mark.parametrize("name", ALIASED_IDS)
def test_a_write_through_a_path_that_aliases_a_folder_is_refused(
    run: Run, store: Path, name: str
) -> None:
    command, cwd = (pinned(text) for text in ALIASED_WRITES[name])
    payload = bash(command, cwd=cwd)
    assert run(payload, role="pm").returncode == 2
    assert run(payload).returncode == 0


@pytest.mark.parametrize("device", ["/dev/null", "/dev/stdout", "/dev/stderr", "/dev/tty"])
def test_a_read_of_the_user_folder_may_print_to_a_terminal_device(
    run: Run, store: Path, device: str
) -> None:
    proc = run(bash(f"cat ~/.claude/CLAUDE.md > {device}"), role="pm")
    assert proc.returncode == 0, proc.stderr


# What a read sets for the programs it runs: an option variable turns a read into a write.
ARMED_READS: dict[str, str] = {
    "export-tar-options": "export TAR_OPTIONS=--remove-files; tar -cf /tmp/x.tar ~/.claude/skills",
    "export-tar-options-and": (
        "export TAR_OPTIONS=--remove-files && tar -cf /tmp/x.tar ~/.claude/skills"
    ),
    "export-tar-checkpoint": (
        "export TAR_OPTIONS=--checkpoint-action=exec=sh; tar -cf /tmp/x.tar ~/.claude/a"
    ),
    "prefix-tar-options": "TAR_OPTIONS=--remove-files tar -cf /tmp/x.tar ~/.claude/skills",
    "prefix-ripgrep-config": "RIPGREP_CONFIG_PATH=/tmp/rc rg x ~/.claude",
    "env-ripgrep-config": "env RIPGREP_CONFIG_PATH=/tmp/rc rg x ~/.claude",
    "export-ripgrep-config": (
        "printf -- '--pre=/tmp/x\\n' > /tmp/rc; export RIPGREP_CONFIG_PATH=/tmp/rc; rg x ~/.claude"
    ),
    "prefix-lessopen": "LESSOPEN='|/tmp/x %s' cat ~/.claude/a",
    "declare-exported": "declare -x TAR_OPTIONS=--remove-files; tar -cf /tmp/x ~/.claude/a",
    "all-export": "set -a; TAR_OPTIONS=--remove-files; tar -cf /tmp/x ~/.claude/a",
    "export-later": "TAR_OPTIONS=--remove-files; export TAR_OPTIONS; tar -cf /tmp/x ~/.claude/a",
    "printf-to-variable": (
        "printf -vTAR_OPTIONS %s --remove-files; export TAR_OPTIONS; tar -cf /tmp/x ~/.claude/a"
    ),
    "printf-to-path": (
        "mkdir -p /tmp/b; echo 'rm -rf ~/.claude/skills' > /tmp/b/cat; chmod +x /tmp/b/cat;"
        " printf -vPATH %s /tmp/b; cat /x"
    ),
}

ARMED_IDS = list(ARMED_READS)


@pytest.mark.parametrize("name", ARMED_IDS)
def test_a_read_of_the_user_folder_is_no_read_once_the_text_sets_what_programs_read(
    run: Run, store: Path, name: str
) -> None:
    payload = bash(ARMED_READS[name])
    assert run(payload, role="pm").returncode == 2
    assert run(payload).returncode == 0


@pytest.mark.parametrize(
    "command",
    [
        "LC_ALL=C grep -c x ~/.claude/a.jsonl",
        "export LANG=C.UTF-8; cat ~/.claude/CLAUDE.md",
        "TZ=UTC ls -l ~/.claude",
        "COLUMNS=200 NO_COLOR=1 ls ~/.claude",
        "export TERM=xterm-256color; ls --color=always ~/.claude",
        "J=~/.claude/x.jsonl; grep -c x $J",
    ],
    ids=["lc-all", "export-lang", "tz", "columns-no-color", "export-term", "lone-name"],
)
def test_a_read_of_the_user_folder_may_set_what_only_changes_its_output(
    run: Run, store: Path, command: str
) -> None:
    proc = run(bash(command), role="pm")
    assert proc.returncode == 0, proc.stderr


def test_a_lone_assignment_of_a_name_the_environment_holds_turns_a_read_off(
    run: Run, store: Path
) -> None:
    """The shell hands an exported name's new value to every program it runs."""
    payload = bash("TAR_OPTIONS=--remove-files; tar -cf /tmp/x.tar ~/.claude/skills")
    assert run(payload, role="pm", env={"TAR_OPTIONS": "-v"}).returncode == 2


# A file written with words that name the folder, in a text that runs more than readers.
SAME_CALL_RUNS: dict[str, tuple[str, str]] = {
    "git-hook": (
        "printf '#!/bin/sh\\nrm -rf ~/.claude/skills\\n' > /tmp/r/.git/hooks/pre-commit;"
        " chmod +x /tmp/r/.git/hooks/pre-commit; git -C /tmp/r commit --allow-empty -m x",
        "/tmp",
    ),
    "heredoc-git-hook": (
        "cat > .git/hooks/pre-commit <<'E'\n#!/bin/sh\nrm -rf ~/.claude/skills\nE\n"
        "chmod +x .git/hooks/pre-commit && git commit -m x",
        "{H}/proj",
    ),
    "git-alias": (
        "printf '[alias]\\n\\tx = !rm -rf ~/.claude/skills\\n' > /tmp/gc;"
        " git -c include.path=/tmp/gc x",
        "/tmp",
    ),
    "npm-script": (
        'echo \'{"scripts":{"x":"rm -rf ~/.claude/skills"}}\' > /tmp/p/package.json;'
        " npm --prefix /tmp/p run x",
        "/tmp",
    ),
    "pytest-conftest": (
        "echo 'import shutil; shutil.rmtree(\"{C}/skills\")' > /tmp/t/conftest.py; pytest /tmp/t",
        "/tmp",
    ),
    "uv-run": ("echo 'rm -rf ~/.claude/skills' > /tmp/s; uv run sh /tmp/s", "/tmp"),
    "busybox": ("echo 'rm -rf ~/.claude/skills' > /tmp/s; busybox sh /tmp/s", "/tmp"),
    "unshare": ("echo 'rm -rf ~/.claude/skills' > /tmp/s; unshare -r sh /tmp/s", "/tmp"),
    "setpriv": ("echo 'rm -rf ~/.claude/skills' > /tmp/s; setpriv sh /tmp/s", "/tmp"),
    "at-file": ("echo 'rm -rf ~/.claude/skills' > /tmp/s; at -f /tmp/s now", "/tmp"),
    "at-input": ("echo 'rm -rf ~/.claude/skills' > /tmp/s; at now < /tmp/s", "/tmp"),
    "batch-input": ("echo 'rm -rf ~/.claude/skills' > /tmp/s; batch < /tmp/s", "/tmp"),
    "crontab-heredoc": (
        "cat > /tmp/c <<'E'\n* * * * * rm -rf ~/.claude/skills\nE\ncrontab /tmp/c",
        "/tmp",
    ),
    "crontab-printf": (
        "printf '* * * * * rm -rf ~/.claude/skills\\n' > /tmp/c; crontab /tmp/c",
        "/tmp",
    ),
    "make": ("printf 'all:\\n\\trm -rf ~/.claude/skills\\n' > /tmp/M; make -f /tmp/M", "/tmp"),
    "echo-then-git": ("echo see ~/.claude/a > /tmp/n.md; git status", "/tmp"),
    "tee-then-tool": ("echo 'rm -rf ~/.claude/x' | tee /tmp/s >/dev/null; mytool /tmp/s", "/tmp"),
}

SAME_CALL_IDS = list(SAME_CALL_RUNS)


@pytest.mark.parametrize("name", SAME_CALL_IDS)
def test_a_file_naming_the_user_folder_is_refused_beside_a_command_that_is_no_reader(
    run: Run, store: Path, name: str
) -> None:
    command, cwd = (pinned(text) for text in SAME_CALL_RUNS[name])
    payload = bash(command, cwd=cwd)
    assert run(payload, role="pm").returncode == 2
    assert run(payload).returncode == 0


@pytest.mark.parametrize(
    "command",
    [
        "cat > /tmp/notes.md <<'EOF'\nthe skills live in ~/.claude/skills\nEOF",
        "echo 'see ~/.claude/skills' > /tmp/n; cat /tmp/n",
        "grep x ~/.claude/p/a.jsonl > /tmp/out; wc -l /tmp/out",
        "printf '%s\\n' ~/.claude/a > /tmp/l; sort /tmp/l | uniq",
        'cd /srv && { echo "transcripts: ~/.claude/projects/"; ls ~/.claude; } > /srv/i; wc -l i',
    ],
    ids=["heredoc-alone", "echo-then-cat", "grep-then-count", "printf-then-sort", "group-to-file"],
)
def test_a_file_naming_the_user_folder_may_be_written_beside_readers(
    run: Run, store: Path, command: str
) -> None:
    proc = run(bash(command), role="pm")
    assert proc.returncode == 0, proc.stderr


# A write into the session folder itself, with names the guard does not know: `subagents/`.
SESSION_FOLDER_FILLS: dict[str, tuple[str, str]] = {
    "cp-folder": ("cp -r /tmp/subagents {S}", "/tmp"),
    "cp-folder-slash": ("cp -r /tmp/subagents {S}/", "/tmp"),
    "cp-file": ("cp /tmp/a {S}", "/tmp"),
    "mv-folder": ("mv /tmp/subagents {S}/", "/tmp"),
    "tar-into": ("tar -xf /tmp/x.tar -C {S}", "/tmp"),
    "rsync-delete": ("rsync -a --delete /tmp/empty/ {S}/", "/tmp"),
    "unzip-into": ("unzip /tmp/x.zip -d {S}", "/tmp"),
    "cd-then-tar": ("cd {S} && tar -xf /tmp/x.tar", "/tmp"),
    "cwd-tar": ("tar -xf /tmp/x.tar", "{S}"),
    "cwd-cp-here": ("cp -r /tmp/subagents .", "{S}"),
    "cwd-unzip": ("unzip /tmp/x.zip", "{S}"),
}

SESSION_FILL_IDS = list(SESSION_FOLDER_FILLS)


@pytest.mark.parametrize("name", SESSION_FILL_IDS)
def test_a_copy_or_unpacking_into_the_session_folder_itself_is_refused(
    run: Run, store: Path, name: str
) -> None:
    command, cwd = (pinned(text) for text in SESSION_FOLDER_FILLS[name])
    payload = in_session(bash(command, cwd=cwd))
    assert run(payload, role="pm", env=job_env()).returncode == 2
    assert run(payload, env=job_env()).returncode == 0


@pytest.mark.parametrize(
    "command",
    [
        "cp /tmp/a {S}/a.txt",
        "tar -xf /tmp/x.tar -C {S}/unpacked",
        "cp -r /tmp/subagents {S}/copy",
        "tar -xf /tmp/x.tar -C {J}",
        "rsync -a /tmp/d/ {J}/",
    ],
    ids=["named-file", "tar-into-sub", "cp-folder-named", "tar-into-job", "rsync-into-job"],
)
def test_a_named_file_in_the_session_folder_or_any_copy_into_the_job_folder_runs(
    run: Run, store: Path, command: str
) -> None:
    payload = in_session(bash(pinned(command)))
    proc = run(payload, role="pm", env=job_env())
    assert proc.returncode == 0, proc.stderr


# --- brace expansions: a word read as each of the words the shell makes of it ---

# Writes whose brace expansion makes a word in the user folder: `{/tmp/zz,~}/.cl{a,}ude` is
# `/tmp/zz/.claude ~/.claude /tmp/zz/.clude ~/.clude`.
BRACED_WRITES: dict[str, str] = {
    "tee": "echo x | tee {/tmp/zz,~}/.cl{a,}ude/CLAUDE.md",
    "rm": "rm -rf {/tmp/zz,~}/.cl{a,}ude/skills",
    "redirect-json": "echo x > {/tmp/zz,~}/.cl{a,}ude.json",
    "cp-destination": "cp /tmp/e.md {~/.cl{a,}ude,/tmp/zz}/agents/x.md",
    "letter-range": "rm -rf {/tmp/zz,~}/.cl{a..b}ude/skills",
    "number-range": "rm -rf {/srv,~}/.cl{a,}ude/skills{1..3}",
    "nested": "rm -rf {/tmp/zz,{/srv,~}}/.cl{a,{b,}}ude/skills",
    "from-root": "echo x | tee /{srv,{H}}/.cl{a,}ude/CLAUDE.md",
    "command-word": "{rm,-rf,~/.cl{a,}ude/skills}",
    "cd-then-write": "cd {/srv,~}/.cl{a,}ude && rm -rf skills",
    "past-the-cap": "rm -rf {/srv,~}/.cl{a,}ude{1..100}",
    "range-from-variable": "rm -rf ~/.cl{a..$X}ude",
}

BRACED_IDS = list(BRACED_WRITES)


@pytest.mark.parametrize("name", BRACED_IDS)
def test_a_write_whose_brace_expansion_makes_a_word_in_the_user_folder_is_refused(
    run: Run, store: Path, name: str
) -> None:
    payload = in_session(bash(pinned(BRACED_WRITES[name])))
    assert run(payload, role="pm", env=job_env()).returncode == 2
    assert run(payload, env=job_env()).returncode == 0


@pytest.mark.parametrize(
    "command",
    [
        "ls {/tmp/zz,~}/.cl{a,}ude/skills",
        "cat ~/.claude/{CLAUDE.md,agents/a.md}",
        "rm -rf /srv/{a,b}/x",
        "mkdir -p /srv/w/{a..c}/{x,y}",
        "cp /srv/f{,.bak}",
        "touch /srv/f{1..60}",
    ],
    ids=["ls", "cat-two", "rm-far", "mkdir-ranges", "backup-copy", "many-far"],
)
def test_a_brace_expansion_that_stays_far_from_the_user_folder_or_only_reads_runs(
    run: Run, store: Path, command: str
) -> None:
    proc = run(in_session(bash(command)), role="pm", env=job_env())
    assert proc.returncode == 0, proc.stderr


@pytest.mark.parametrize(
    "command",
    [
        "rm -rf {/tmp/zz,~}/coven-mem{o,}ry/systems",
        "echo x > {/srv,~}/coven-mem{o,}ry/systems/guard.json",
    ],
    ids=["rm", "redirect"],
)
def test_a_brace_expansion_that_makes_the_store_is_refused_in_a_restricted_session(
    run: Run, store: Path, command: str
) -> None:
    assert run(bash(command), role="pm").returncode == 2
    assert run(bash(command)).returncode == 0


# --- a printf that sets a variable is no read ---

# `printf -v NAME` and `printf -vNAME` set NAME to the path: a later write by `$NAME` goes there.
PRINTED_VARIABLES: dict[str, str] = {
    "glued-rm": 'printf -vD %s ~/.claude/skills; rm -rf "$D"',
    "glued-cd": 'printf -vD %s ~/.claude; cd "$D" && rm -rf skills',
    "glued-redirect": 'printf -vD %s ~/.claude/CLAUDE.md; cat /tmp/e > "$D"',
    "spaced-rm": 'printf -v D %s ~/.claude/skills; rm -rf "$D"',
}

PRINTED_IDS = list(PRINTED_VARIABLES)


@pytest.mark.parametrize("name", PRINTED_IDS)
def test_a_printf_that_sets_a_variable_to_the_user_folder_is_no_read(
    run: Run, store: Path, name: str
) -> None:
    payload = bash(PRINTED_VARIABLES[name])
    assert run(payload, role="pm").returncode == 2
    assert run(payload).returncode == 0


@pytest.mark.parametrize(
    "command",
    [
        "printf '%s\\n' ~/.claude/skills",
        'D=~/.claude/skills; ls "$D"',
        'printf -vD %s /srv/x; rm -rf "$D"',
    ],
    ids=["printf-prints", "assigned-then-read", "printf-sets-far"],
)
def test_a_printf_that_prints_the_user_folder_or_sets_a_far_path_runs(
    run: Run, store: Path, command: str
) -> None:
    proc = run(bash(command), role="pm")
    assert proc.returncode == 0, proc.stderr


# --- a variable's subscript or arithmetic runs the command substitutions in it ---

# `D='a[$(...)]'`: whatever reads D as a variable name or a number runs what is in the brackets.
RUN_BY_SUBSCRIPT = "D='a[$(rm${IFS}-rf${IFS}~/.claude/skills)]'; "
SUBSCRIPT_RUNS: dict[str, str] = {
    "test-v": 'test -v "$D"',
    "bracket-v": '[ -v "$D" ]',
    "bracket-named-ref": '[ -R "$D" ]',
    "test-eq": 'test "$D" -eq 0',
    "bracket-gt": '[ 1 -gt "$D" ]',
    "operator-from-variable": 'O=-v; test "$O" "$D"',
    "indirect": 'echo "${!D}"',
    "substring-offset": 'x=abc; echo "${x:D}"',
    "array-subscript": 'a=(x y); echo "${a[D]}"',
    "zsh-subscript": "a=(x y); echo $a[D]",
    "printf-number": 'printf %d "$D"',
    "count-or-variable": 'n=$(grep -c x ~/.claude/a || echo "$D"); [ "$n" -ge 2 ]',
    "copied-variable": 'm=$D; [ "$m" -gt 0 ]',
    "variable-through-tr": 'n=$(echo "$D" | tr -d " "); [ "$n" -gt 0 ]',
    "break-count": 'for f in x; do break "$D"; done',
    "exit-status": 'exit "$D"',
}
SUBSCRIPT_IDS = list(SUBSCRIPT_RUNS)


@pytest.mark.parametrize("name", SUBSCRIPT_IDS)
def test_a_read_that_may_evaluate_a_variable_as_a_name_or_number_is_no_read(
    run: Run, store: Path, name: str
) -> None:
    payload = bash(RUN_BY_SUBSCRIPT + SUBSCRIPT_RUNS[name])
    assert run(payload, role="pm").returncode == 2
    assert run(payload).returncode == 0


# The command text a value keeps for later: single quotes, an escape, ANSI-C quoting.
KEPT_COMMAND_VALUES: dict[str, str] = {
    "escaped": 'D=a[\\$\\(rm\\ -rf\\ ~/.claude/skills\\)]; ls "$D"',
    "ansi-c": "D=$'a[\\x24(rm -rf ~/.claude/skills)]'; ls \"$D\"",
    "backtick": "D='a[`rm -rf ~/.claude/skills`]'; ls \"$D\"",
}
KEPT_IDS = list(KEPT_COMMAND_VALUES)


@pytest.mark.parametrize("name", KEPT_IDS)
def test_an_assignment_that_keeps_command_text_is_no_read(run: Run, store: Path, name: str) -> None:
    payload = bash(KEPT_COMMAND_VALUES[name])
    assert run(payload, role="pm").returncode == 2


@pytest.mark.parametrize(
    "command",
    [
        "test -d ~/.claude/skills && ls ~/.claude/skills",
        "[ -f ~/.claude/CLAUDE.md ] && cat ~/.claude/CLAUDE.md",
        '[ "$x" = y ] && cat ~/.claude/CLAUDE.md',
        '[ -n "$(ls ~/.claude)" ] && ls ~/.claude',
        "n=$(grep -c x ~/.claude/a); printf '%d files\\n' \"$n\"; ls ~/.claude",
        'n=$(grep -c x ~/.claude/a 2>/dev/null || echo 0); [ "$n" -ge 2 ] && ls ~/.claude',
        '[ "$(grep -c x ~/.claude/a || grep -c y ~/.claude/a)" -gt 6 ] && ls ~/.claude',
        'n=$([ -f ~/.claude/a ] && grep -c x ~/.claude/a || echo 0); [ "$n" -ge 2 ]',
        "n=$(wc -l < ~/.claude/a | tr -d ' '); [ \"$n\" -gt 0 ] && ls ~/.claude",
        'n=$(grep -c x ~/.claude/a || true); [ "$n" -gt 0 ]',
        'n=$(grep -c x ~/.claude/a || :); [ "$n" -gt 0 ]',
        'n=$(grep -c x ~/.claude/a); m=$n; [ "$m" -gt 0 ]',
        'D=~/.claude/skills; ls "$D"',
    ],
    ids=[
        "test-d",
        "bracket-f",
        "bracket-equals",
        "count-test",
        "printf-count",
        "count-or-zero",
        "count-or-count",
        "tested-count",
        "count-through-tr",
        "count-or-true",
        "count-or-colon",
        "copied-count",
        "assigned-path",
    ],
)
def test_a_test_or_expansion_that_evaluates_nothing_still_reads(
    run: Run, store: Path, command: str
) -> None:
    proc = run(bash(command), role="pm")
    assert proc.returncode == 0, proc.stderr


# --- uniq's operands, after `--` and a lone `-` too: the second one is where it writes ---


@pytest.mark.parametrize(
    "command",
    [
        "cd ~/.claude/projects && uniq /tmp/e -- -tmp-proj/memory/MEMORY.md",
        "cd ~/.claude/skills && uniq -c -- /tmp/e -SKILL.md",
        "echo x | uniq - ~/.claude/CLAUDE.md",
        "uniq - ~/.claude/.credentials.json < /dev/null",
        "printf '{}\\n' | uniq - ~/.claude.json",
        "echo x | uniq -z - ~/.claude/agents/e.md",
        "cd ~/.claude && echo x | uniq - CLAUDE.md",
    ],
    ids=["after-cd", "counted", "stdin-dash", "dash-redirected", "json", "zero-ended", "dash-cd"],
)
def test_a_uniq_with_a_second_operand_is_no_read(run: Run, store: Path, command: str) -> None:
    payload = bash(command)
    assert run(payload, role="pm").returncode == 2
    assert run(payload).returncode == 0


@pytest.mark.parametrize(
    "command",
    [
        "uniq -c ~/.claude/a.txt",
        "uniq -- ~/.claude/a.txt",
        "cat ~/.claude/a.txt | sort | uniq -c",
        "cat ~/.claude/a.txt | uniq -c -",
    ],
    ids=["count", "double-dash-one-file", "piped", "stdin-dash-alone"],
)
def test_a_uniq_of_one_file_in_the_user_folder_reads(run: Run, store: Path, command: str) -> None:
    proc = run(bash(command), role="pm")
    assert proc.returncode == 0, proc.stderr


# --- zsh's NULLCMD and READNULLCMD: a redirection with no command runs them ---

NULL_COMMAND_RUNS: dict[str, str] = {
    "nullcmd-heredoc": "NULLCMD=sh\n<<'E'\nrm -rf ~/.cl''aude/skills\nE",
    "nullcmd-file": "echo 'rm -rf ~/.claude/skills' > /tmp/s; NULLCMD=sh; < /tmp/s",
    "readnullcmd-file": "echo 'rm -rf ~/.claude/skills' > /tmp/s; READNULLCMD=sh; < /tmp/s",
    "precmd-array": "precmd_functions+=(x); ls ~/.claude",
    "rprompt": "RPROMPT='$(x)'; ls ~/.claude",
}
NULL_COMMAND_IDS = list(NULL_COMMAND_RUNS)


@pytest.mark.parametrize("name", NULL_COMMAND_IDS)
def test_a_text_that_sets_what_the_shell_runs_on_its_own_is_no_read(
    run: Run, store: Path, name: str
) -> None:
    payload = bash(NULL_COMMAND_RUNS[name])
    assert run(payload, role="pm").returncode == 2
    assert run(payload).returncode == 0


def test_a_bare_redirection_is_no_read_when_the_hook_environment_sets_nullcmd(
    run: Run, store: Path
) -> None:
    payload = bash("< ~/.claude/CLAUDE.md")
    assert run(payload, role="pm", env={"READNULLCMD": "sh"}).returncode == 2
    assert run(payload, role="pm").returncode == 0


# --- zsh's two-operand cd: `cd old new` goes to the folder with `old` replaced by `new` ---


@pytest.mark.parametrize(
    "command",
    [
        "cd ~/proj && cd proj .claude && rm -rf skills",
        "cd ~/proj && pushd proj .claude && rm -rf skills",
    ],
    ids=["cd", "pushd"],
)
def test_a_two_operand_cd_leaves_the_folder_unknown(run: Run, store: Path, command: str) -> None:
    payload = bash(command)
    assert run(payload, role="pm").returncode == 2
    assert run(payload).returncode == 0


@pytest.mark.parametrize(
    "command",
    ["cd ~/proj && cd proj .claude && ls", "cd /srv/a && cd a b && rm -rf /srv/out"],
    ids=["then-read", "then-far-write"],
)
def test_a_two_operand_cd_then_a_read_or_a_far_write_runs(
    run: Run, store: Path, command: str
) -> None:
    proc = run(bash(command), role="pm")
    assert proc.returncode == 0, proc.stderr


# --- a copy of what the text feeds it writes that text: a script the same call may run ---

COPIED_SCRIPTS: dict[str, str] = {
    "sh": "cp /dev/stdin /tmp/s <<'E'\nrm -rf ~/.claude/skills\nE\nsh /tmp/s",
    "unnamed": "cp /dev/stdin /tmp/s <<'E'\nrm -rf ~/.cl''aude/skills\nE\nsh /tmp/s",
    "python": "cp /dev/stdin /tmp/s.py <<'E'\nimport shutil\nshutil.rmtree('~/.claude/skills')\nE\n"
    "python3 /tmp/s.py",
    "proc-fd": "cp /proc/self/fd/0 /tmp/s <<'E'\nrm -rf ~/.claude/skills\nE\nsh /tmp/s",
    "dev-fd": "cp /dev/fd/0 /tmp/s <<'E'\nrm -rf ~/.claude/skills\nE\nbash /tmp/s",
    "run-by-path": "cp /dev/stdin /tmp/s <<'E'\nrm -rf ~/.claude/skills\nE\n"
    "chmod +x /tmp/s; /tmp/s",
    "git-hook": "cp /dev/stdin /tmp/r/.git/hooks/pre-commit <<'E'\nrm -rf ~/.claude/skills\nE\n"
    "git -C /tmp/r commit --allow-empty -m x",
    "here-string": "cp /dev/stdin /tmp/s <<< 'rm -rf ~/.claude/skills'; sh /tmp/s",
    "install": "install /dev/stdin /tmp/s <<'E'\nrm -rf ~/.claude/skills\nE\nsh /tmp/s",
}
COPIED_IDS = list(COPIED_SCRIPTS)


@pytest.mark.parametrize("name", COPIED_IDS)
def test_a_script_copied_from_the_text_and_run_is_no_read(run: Run, store: Path, name: str) -> None:
    payload = bash(COPIED_SCRIPTS[name])
    assert run(payload, role="pm").returncode == 2
    assert run(payload).returncode == 0


@pytest.mark.parametrize(
    "command",
    [
        "cp /dev/stdin /tmp/n <<'E'\nnotes on ~/.claude\nE\ncat /tmp/n",
        "cp ~/.claude/CLAUDE.md /tmp/c.md; ls /tmp",
        "cp /dev/stdin /tmp/s <<'E'\nrm -rf ~/.claude/skills\nE",
        "cp ~/.claude/CLAUDE.md /tmp/x < /dev/null; git status",
    ],
    ids=["copied-then-read", "copy-out", "copied-not-run", "copy-out-null-input"],
)
def test_a_copy_beside_reads_only_still_reads(run: Run, store: Path, command: str) -> None:
    proc = run(bash(command), role="pm")
    assert proc.returncode == 0, proc.stderr


# --- an MCP tool's relative path or `file://` URL: its folder is the tool's own, unknown ---


@pytest.mark.parametrize(
    "path",
    [
        ".claude/CLAUDE.md",
        ".claude.json",
        ".claude/skills/x/SKILL.md",
        "../.claude/CLAUDE.md",
        "file://{home}/.claude/CLAUDE.md",
        "file://localhost{home}/.claude/%43LAUDE.md",
    ],
    ids=["folder-file", "json", "skill", "climbing", "file-url", "file-url-host"],
)
def test_an_mcp_relative_path_or_file_url_into_the_folder_is_refused(
    run: Run, store: Path, path: str
) -> None:
    payload = tool("mcp__fs__write_file", path=path.format(home=home()), content="x")
    assert run(payload, role="pm").returncode == 2
    assert run(payload).returncode == 0


@pytest.mark.parametrize(
    "path",
    ["notes/CLAUDE.md", "src/claude.py", "file:///tmp/CLAUDE.md"],
    ids=["other-folder", "like-named", "file-url-far"],
)
def test_an_mcp_relative_path_outside_the_folder_is_allowed(
    run: Run, store: Path, path: str
) -> None:
    proc = run(tool("mcp__fs__write_file", path=path, content="x"), role="pm")
    assert proc.returncode == 0, proc.stderr


@pytest.mark.parametrize(
    "payload",
    [
        tool("mcp__github__create_issue", body="Changed packages/app/.claude/agents/foo.md"),
        tool("mcp__web__fetch", url="https://example.com/.claude/x"),
        tool("mcp__web__fetch", url="ssh://host/.claude/x"),
    ],
    ids=["prose", "https-url", "other-scheme"],
)
def test_mcp_prose_or_a_web_url_naming_a_claude_folder_is_no_relative_path(
    run: Run, store: Path, payload: Payload
) -> None:
    proc = run(payload, role="pm")
    assert proc.returncode == 0, proc.stderr


# --- a text that changes how words split gets no job folder: its pieces may be the folder ---

SPLIT_JOB_WRITES: dict[str, str] = {
    "ifs-assigned": "cd ~; IFS=/; rm -rf $CLAUDE_JOB_DIR/x",
    "ifs-exported": "cd ~; export IFS=/; rm -rf $CLAUDE_JOB_DIR/x",
    "ifs-declared": "cd ~; declare IFS=/; rm -rf $CLAUDE_JOB_DIR/x",
    "ifs-read": "cd ~; IFS=/ read -r a; rm -rf $CLAUDE_JOB_DIR/x",
    "ifs-unset": "cd ~; unset IFS; rm -rf $CLAUDE_JOB_DIR/x",
    "zsh-word-split": "cd ~; setopt shwordsplit; rm -rf $CLAUDE_JOB_DIR/x",
    "zsh-word-split-underscored": "cd ~; setopt SH_WORD_SPLIT; rm -rf $CLAUDE_JOB_DIR/x",
    "zsh-emulate-sh": "cd ~; emulate sh; rm -rf $CLAUDE_JOB_DIR/x",
    "zsh-emulate-ksh-local": "cd ~; emulate -L ksh; rm -rf $CLAUDE_JOB_DIR/x",
}
SPLIT_JOB_IDS = list(SPLIT_JOB_WRITES)


@pytest.mark.parametrize("name", SPLIT_JOB_IDS)
def test_a_job_folder_write_in_a_text_that_changes_word_splitting_is_refused(
    run: Run, store: Path, name: str
) -> None:
    payload = in_session(bash(SPLIT_JOB_WRITES[name]))
    assert run(payload, role="pm", env=job_env()).returncode == 2
    assert run(payload, env=job_env()).returncode == 0


@pytest.mark.parametrize(
    "command",
    [
        "cd ~; rm -rf $CLAUDE_JOB_DIR/x",
        'echo x > "$CLAUDE_JOB_DIR/out.txt"',
        "rm${IFS}-f $CLAUDE_JOB_DIR/x",
    ],
    ids=["after-cd-home", "quoted", "ifs-only-read"],
)
def test_a_job_folder_write_that_leaves_word_splitting_alone_is_allowed(
    run: Run, store: Path, command: str
) -> None:
    proc = run(in_session(bash(command)), role="pm", env=job_env())
    assert proc.returncode == 0, proc.stderr


# --- long words and many braces: decided fast, as words the guard cannot expand ---

# Loose enough for a loaded machine; the quadratic scan took minutes on these.
FAST_DECISION_SECONDS = 10.0
LONG_WORD = "A" * 100_000
MANY_BRACES = "{a,b}" * 1500


@pytest.mark.parametrize(
    "payload",
    [
        bash("ls ~/.claude; echo " + LONG_WORD),
        bash("ls ~/.claude; echo " + LONG_WORD[:50_000] + "{" + LONG_WORD[:50_000]),
        tool("mcp__x__send", body=LONG_WORD),
        tool("mcp__x__send", body=LONG_WORD[:50_000] + "{" + LONG_WORD[:50_000]),
        tool("mcp__x__send", body="[" + ",".join(['{"a":1,"b":2}'] * 1500) + "]"),
        bash("touch /tmp/" + MANY_BRACES),
    ],
    ids=["bash-no-brace", "bash-one-brace", "mcp-no-brace", "mcp-one-brace", "mcp-json", "far"],
)
def test_a_long_word_or_many_braces_is_decided_fast_and_allowed(
    run: Run, store: Path, payload: Payload
) -> None:
    started = time.monotonic()
    proc = run(payload, role="pm")
    assert proc.returncode == 0, proc.stderr
    assert time.monotonic() - started < FAST_DECISION_SECONDS


def test_a_folder_write_with_more_braces_than_the_cap_is_refused(run: Run, store: Path) -> None:
    payload = bash("rm -rf ~/.cl{a,b}ude" + MANY_BRACES)
    assert run(payload, role="pm").returncode == 2
    assert run(payload).returncode == 0


# --- a cd clear of the folder, read from the text when the scanner cannot follow it ---

# Each `cd` has one literal operand from `/` or `~/` that is clear of the user folder, so a relative
# glob after it (`.*`) names nothing there, though the scanner gives up on the text (`<(...)`) or
# the cd sits in a quoted ssh or `sh -c` string.
CLEAR_CD_TEXTS = {
    "ssh-string": "ssh h 'cd /srv/app && sed -n \"s/.*//p\" f'",
    "sh-c-string": "docker run img sh -c 'cd /srv/c && ls -d .*'",
    "process-substitution": "diff <(cd /srv/a && git diff) <(sed 's/.*//' /tmp/f)",
    "pipe-and-substitution": "cd /srv/app && git log --format='%s' | sed 's/.*//' && cat <(echo x)",
    "dot-glob-read": "cd /srv/app && cat <(echo x) && ls -d .*",
    "in-place-sed": "cd /srv/app && cat <(echo x) && sed -i 's/.*//' f",
    "subshell-piped": "(cd /srv/a && git log) | sed 's/.*//'; cat <(echo)",
    "double-dash": "cd -- /srv/app && cat <(echo x) && ls -d .*",
    "pushd": "pushd /srv/app && cat <(echo x) && ls -d .*",
    "quoted-operand": "cd '/srv/app' && cat <(echo x) && ls -d .*",
    "two-cds": "cd /srv/a; cd /srv/b && cat <(echo x) && ls -d .*",
    "below-home-sed-script": "cd HOME/proj && sed -i 's/.*//' f",
    "below-home-glob-in-place": "cd HOME/proj && rm -f *.tmp .*.swp",
    "below-home-read-dot-glob": "cd HOME/proj && ls -d .*",
}


@pytest.mark.parametrize("name", CLEAR_CD_TEXTS)
def test_a_cd_clear_of_the_user_folder_keeps_a_relative_glob_out_of_it(
    run: Run, store: Path, name: str
) -> None:
    # The tests' home is under /tmp, so the session starts in /srv, a folder clear of it.
    command = CLEAR_CD_TEXTS[name].replace("HOME", str(home()))
    proc = run(bash(command, cwd="/srv"), role="pm")
    assert proc.returncode == 0, proc.stderr


# Not clear: a cd home or to a folder the guard cannot tell, a cd word it cannot read, a `..`.
UNCLEAR_CD_TEXTS = {
    "ssh-string-home": "ssh h 'cd ~ && rm -rf .c*'",
    "quoted-cd-word": "c''d ~; cat <(echo) ; rm -rf .c*",
    "variable-folder": "cd /tmp/$X && cat <(echo) && rm -rf .c*",
    "up-path": "cd /tmp && cat <(echo) && rm -rf ../home/*/.c*",
    "home-later": "cd /srv && cd ~ && cat <(echo) && rm -rf .c*",
    "bare-pushd": "cd /srv && pushd && cat <(echo) && rm -rf .c*",
    "popd": "pushd /srv && popd && cat <(echo) && rm -rf .c*",
    "quoted-tilde": "cd '~/x' && cat <(echo) && rm -rf .c*",
    # The shell reads `cd <home's parent>/home`: a quote glued to the operand goes on with it.
    "glued-quote": "sh -c 'cd PARENT/ho'me' && rm -rf .c*'",
    "operand-with-space": "cd '/srv/a b' && cat <(echo) && rm -rf .c*",
    "backslash-operand": "cd /srv\\ a && cat <(echo) && rm -rf .c*",
    "substitution-operand": "cd /srv/$(echo x) && cat <(echo) && rm -rf .c*",
    "two-operands": "cd /srv ~ && cat <(echo) && rm -rf .c*",
    # Bash before 5.2 matches `..` with `.*`: a cd one folder below home names the home folder.
    "dot-glob-climbs-home": "cd ~/proj && cat <(echo) && rm -rf .*/.claude",
    "dot-glob-climbs-home-scanned": "cd ~/proj && rm -rf .*/.claude",
    "two-dot-globs-climb": "cd ~/proj/sub && cat <(echo) && rm -rf .*/.*/.claude",
    # `/` holds the folder: `chmod -R 000 .*` there may change all of it.
    "dot-glob-climbs-to-root": "docker run img sh -c 'cd /c && chmod -R 000 .*'",
    "operand-then-more": "sh -c 'cd /srv ~' && cat <(echo) && rm -rf .c*",
    # The same climb where the scanner follows the text and the cd names home by its path.
    "scanned-dot-glob-chmod": "cd HOME/proj && chmod -R 000 .*",
    "scanned-dot-glob-touch": "cd HOME/proj && touch .*/.claude/x",
    "scanned-dot-glob-rm": "cd HOME/proj && rm -rf .*/.claude",
}


@pytest.mark.parametrize("name", UNCLEAR_CD_TEXTS)
def test_a_cd_that_may_lead_near_the_user_folder_keeps_a_relative_glob_strict(
    store: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    """With no other zone, as the settings zone refuses these too."""
    monkeypatch.setattr(pg, "_zones", lambda _store: (pg._claude_folder_zone(),))
    command = UNCLEAR_CD_TEXTS[name].replace("PARENT", str(home().parent))
    command = command.replace("HOME", str(home()))
    message = decide(bash(command, cwd="/srv"), store, "pm")
    assert message is not None
    assert FOLDER_REFUSAL in message


# --- a quoted glob is literal: in `~`, `.*` matches `.claude` only when the shell expands it ---

QUOTED_GLOBS = {
    "single-quoted": "ls '.*'",
    "double-quoted": 'ls ".*"',
    "escaped": "ls .\\*",
    "quoted-file-operand": 'grep x f ".*)"',
    "quoted-pattern": 'grep ".*)" f',
    "here-string": "grep -c x <<< '.*'",
    "python-quoted-heredoc": (
        "python3 - <<'EOF'\nimport re\nprint(re.sub(r'(a).*', 'b', 'c'))\nEOF"
    ),
    "after-a-body-with-an-apostrophe": "cat <<'EOF' > /tmp/n.md\nit's\nEOF\nls '.*'",
    "find-name": "find ~/proj -name '.*'",
}


@pytest.mark.parametrize("name", QUOTED_GLOBS)
def test_a_pm_may_quote_a_glob_in_the_home_folder(store: Path, name: str) -> None:
    assert decide(bash(QUOTED_GLOBS[name], cwd=str(home())), store, "pm") is None


def test_a_pm_may_quote_a_glob_in_a_project_with_a_claude_folder(store: Path) -> None:
    """Read unquoted, `(A|B)\\s*=` gives the word `s*`, which can be `settings.json`, and `.*`
    can be `.claude`, the folder that holds it."""
    (project_dir() / ".claude").mkdir()
    command = 'grep -nE "(A|B)\\s*=" -r app/*.py | sed -E "s/,.*/)/"'
    assert decide(bash(command, cwd=str(project_dir())), store, "pm") is None


# sed scripts whose letters are no `e`, `w` or `W` command or flag, nor an `-f` file: the line
# keeps its quotes literal, so the grep pattern's `\\s*` is no glob.
SED_SCRIPTS = {
    "character-class": 'sed -E "s/(:[[:space:]]*).*/\\1<literal>/"',
    "expressions": "sed -e 's/.*/web/' -e '/^$/d'",
    "range-and-group": "sed -n '/begin/,/end/{s/.*/=/;p}'",
    "hold-and-label": "sed -n ':end;N;$!bend;s/.*/x/;p'",
    "transliterate": "sed 'y/abc/xyz/;s/.*/e/g'",
    "comment": "sed -n '# keep the ends\n/.*/p'",
    "append-text": "sed 's/.*/x/;1a see the web'",
    "read-file": "sed 's/.*/x/;$r /tmp/footer.txt'",
    "quit-with-status": "sed -E 's/.*/x/;/error/q5'",
}


@pytest.mark.parametrize("name", SED_SCRIPTS)
def test_a_pm_may_pipe_into_a_sed_script_in_a_project_with_a_claude_folder(
    store: Path, name: str
) -> None:
    (project_dir() / ".claude").mkdir()
    command = f'grep -nE "(A|B)\\s*=" -r app/*.py | {SED_SCRIPTS[name]}'
    assert decide(bash(command, cwd=str(project_dir())), store, "pm") is None


@pytest.mark.parametrize(
    "python",
    ["python3", "/usr/bin/python3", "/bin/python3"],
)
def test_a_pm_may_run_python_by_its_path_on_a_body_with_globs(store: Path, python: str) -> None:
    """A body python reads is a script: its regex `[a-z]*` is no glob, though read as one by a
    shell next to `~/.config` it names the coven folder in it."""
    body = "# from ~/.config/x\nimport re\nword = '[a-z]*'\nprint(re.findall(word, 'a b'))\n"
    command = f"{python} - <<'PY'\n{body}PY"
    assert decide(bash(command, cwd=str(project_dir())), store, "pm") is None


READER_GLOB_BODIES = {"settings": "print('.cl*')", "config": "print('.con*')"}


@pytest.mark.parametrize(
    "reader",
    [
        "./python3",
        "/tmp/x/python3",
        "bin/node",
        "~/t/python3",
        '"$HOME/t/python3"',
        "/usr/local/bin/python3",
    ],
)
@pytest.mark.parametrize("body", READER_GLOB_BODIES)
def test_a_pm_cannot_run_a_body_with_a_glob_by_a_reader_anywhere_else(
    store: Path, reader: str, body: str
) -> None:
    """A program by a path the session may write may be a shell under a reader's name: its
    body's relative words are read as the shell would expand them, here from the home folder."""
    command = f"{reader} - <<'PY'\n{READER_GLOB_BODIES[body]}\nPY"
    assert decide(bash(command, cwd=str(home())), store, "pm") is not None


@pytest.mark.parametrize(
    ("reader", "body"),
    # `/usr/bin/python3` on `.cl*` stays refused: a program by its path is blanked as a plugin
    # run, and the body is then read with no reader (docs/known-gaps.md).
    [("python3", "settings"), ("python3", "config"), ("/usr/bin/python3", "config")],
)
def test_a_pm_may_run_a_body_with_a_glob_by_a_reader_it_knows(
    store: Path, reader: str, body: str
) -> None:
    command = f"{reader} - <<'PY'\n{READER_GLOB_BODIES[body]}\nPY"
    assert decide(bash(command, cwd=str(home())), store, "pm") is None


# Still refused: a glob the shell expands, a quoted word a shell or interpreter runs, a body a
# shell reads, an unquoted heredoc (its `$(...)` expands), and a Bash read of the settings.
QUOTED_GLOB_REFUSALS = {
    "unquoted-dot-glob": "ls .*",
    "half-quoted-word": 'ls ".c"*/settings.json',
    "read-settings": "cat ~/.claude/settings.json",
    "shell-command-string": "bash -c 'ls .*'",
    "eval": "eval 'ls .*'",
    "watch": "watch 'ls .*'",
    "python-c-glob": "python3 -c \"import glob; print(glob.glob('.c*/settings.json'))\"",
    "quoted-body-a-shell-reads": "bash <<'EOF'\nls .*\nEOF",
    "unquoted-heredoc-body": "cat <<EOF\n$(ls $HOME/.claude/*)\nEOF",
    "substitution-in-double-quotes": 'echo "$(ls .*)"',
    "backticks": "echo `ls .*` '.*'",
    # A quoted glob the shell expands later: through a variable or a command's output.
    "loop-variable": "for d in '.cl*'; do rm -rf $d; done",
    "assigned-variable": "d='.cl*'; ls -d $d",
    "substitution-output": "ls -d $(echo '.cl*')",
    # A program that reads its own quoted words as globs or runs them.
    "find-action": "find . -maxdepth 1 -name '.cl*' -exec rm -rf {} +",
    "find-brace-action": "find /tmp -maxdepth 0 -e{xec,xec} sh -c 'cd ~; rm -rf .cl*/plugins' ';'",
    "git-pathspec": "git rm -r --cached '.cl*'",
    "sed-runs-its-output": "sed 's/^/ls -d .cl* /e' /tmp/x",
    "sed-run-command": "sed '1e ls -d .cl*' /tmp/x",
    "sed-run-command-after-another": "sed 'p;e ls -d .cl*' /tmp/x",
    "sed-run-command-after-a-label": "sed ':a;e ls -d .cl*' /tmp/x",
    "sed-run-command-after-a-comment": "sed '#x\ne ls -d .cl*' /tmp/x",
    "sed-run-command-after-a-transliteration": "sed 'y/ab/cd/;e ls -d .cl*' /tmp/x",
    "sed-run-command-in-a-group": "sed -e '1{' -e 'e ls -d .cl*' -e '}' /tmp/x",
    "sed-run-command-in-a-second-expression": "sed -e p -e 'e ls -d .cl*' /tmp/x",
    "sed-run-command-in-an-option-cluster": "sed -ne 'e ls -d .cl*' /tmp/x",
    "sed-run-command-in-a-long-option": "sed --expression='e ls -d .cl*' /tmp/x",
    "sed-run-command-in-an-abbreviated-option": "sed --expr='e ls -d .cl*' /tmp/x",
    "sed-run-flag-after-an-escaped-delimiter": "sed 's/a\\/x/ls -d .cl* #/e' /tmp/x",
    "sed-run-flag-with-other-flags": "sed 's/^/ls -d .cl* /ge' /tmp/x",
    "sed-write-flag": "sed -n 's/x/y/w .cl*' /tmp/x",
    "sed-write-command": "sed -n 'W .cl*' /tmp/x",
    "sed-unknown-command": "sed 'v;e ls -d .cl*' /tmp/x",
    "sed-run-command-after-text": "sed -e '1a x' -e 'e ls -d .cl*' /tmp/x",
    "sed-run-command-after-continued-text": "sed '1a x\\\\\ne ls -d .cl*' /tmp/x",
    "sed-script-file": "sed -f /tmp/s.sed '.cl*'",
    # Under POSIXLY_CORRECT, sed takes `-e` after its first operand as a file, the operand as
    # the script.
    "sed-expression-after-the-script": "sed 'e ls -d .cl*' -e p /tmp/x",
    "sed-variable-script": "sed \"$S\" '.cl*'",
    # GNU sed ends a comment or a file name at the newline, a backslash or not, and reads the
    # delimiter inside `[...]` as part of the regex.
    "sed-run-command-after-a-comment-with-a-backslash": "sed -n -e '#x\\' -e 'e ls -d .cl*' f",
    "sed-run-command-after-a-read-file-with-a-backslash": "sed -n -e 'r x\\' -e 'e ls -d .cl*' f",
    "sed-run-command-after-a-queued-file": "sed -n -e 'R x\\' -e 'e ls -d .cl*' f",
    "sed-write-after-a-read-file": "sed -n -e 'r x\\' -e 'w .cl*' f",
    "sed-piped-run-command-after-a-comment": "cat f | sed -n -e '#x\\' -e 'e ls -d .cl*'",
    "sed-run-command-in-a-bracket": "sed -n 's/[a/b]x/;/;e ls -d .cl* ;/p' f",
    "sed-run-command-after-an-address-bracket": "sed -n '/[/]/p;/;e ls -d .cl* ;/p' f",
    "sed-run-flag-after-a-bracket": "sed 's/x[^/]/;i/e;e ls -d .cl* ;/p' f",
    "trap": "trap 'ls .*' EXIT; ls '.x*'",
    "python-body-glob": (
        "python3 - <<'EOF'\nimport glob, shutil\nfor p in glob.glob('.cl*'): shutil.rmtree(p)\nEOF"
    ),
}


def test_a_body_refused_for_its_glob_like_words_points_to_write_and_a_separate_run(
    store: Path,
) -> None:
    """A body word with a literal (`**en**`) still counts as a glob next to `~/.config`: python
    may hand it to a shell. The refusal says how to run such a script."""
    body = "# from ~/.config/x\nold = '**en** route'\nprint(old)\n"
    refusal = decide(bash(f"python3 - <<'PY'\n{body}PY", cwd=str(project_dir())), store, "pm")
    assert refusal is not None
    assert "with the Write tool" in refusal
    assert "in its own Bash call" in refusal


@pytest.mark.parametrize("name", QUOTED_GLOB_REFUSALS)
def test_a_pm_cannot_reach_the_settings_with_a_glob_the_shell_expands(
    store: Path, name: str
) -> None:
    refusal = decide(bash(QUOTED_GLOB_REFUSALS[name], cwd=str(home())), store, "pm")
    assert refusal is not None
    assert "cannot write to" in refusal


# --- a pm may read and run what the plugins folder holds (a skill's own scripts), not write it ---

SDD = "~/.claude/plugins/cache/claude-plugins-official/superpowers/7.0.2/skills/sdd"


def plugin_reads() -> dict[str, str]:
    impeccable = home() / ".claude/plugins/cache/impeccable/impeccable/4.7.0/scripts/impeccable"
    return {
        "script-by-variable": (
            "S=$HOME/.claude/plugins/cache/claude-plugins-official/superpowers/7.0.2/skills/sdd;"
            " bash $S/scripts/sdd-workspace /tmp/wt /tmp/wt/docs/plan.md"
        ),
        "script-by-variable-after-cd": (
            f"S={SDD}; cd /tmp/wt && git status --porcelain | head -3;"
            " bash $S/scripts/review-package docs/plan.md 1"
        ),
        "script-by-path": f"cd /tmp/wt && bash {SDD}/scripts/review-package docs/plan.md 2",
        "program-by-quoted-path": f'"{impeccable}" context --target x.html',
        "python-script": f"python3 -I {SDD}/scripts/x.py --plan p.md",
        "plugin-root-script": "bash $CLAUDE_PLUGIN_ROOT/bin/x",
        "sed-print": f"sed -n 60,200p {SDD}/task-reviewer-prompt.md",
        "cat": "cat ~/.claude/plugins/installed_plugins.json",
        "ls-into-head": "ls ~/.claude/plugins/marketplaces 2>/dev/null | head",
        "grep-into-head": "grep -n '^#' ~/.claude/plugins/marketplaces/x/DESIGN.md | head -40",
        "find": "find ~/.claude/plugins -name '*.md' -path '*impeccable*'",
        "read-then-write-elsewhere": f"cat {SDD}/SKILL.md > /tmp/skill.md",
        "sed-by-variable": (
            f"CM={SDD}; wc -l $CM/scripts/*.py; sed -n 60,110p $CM/commands/map.md;"
            " grep -n 'files' $CM/workflows/x.js | head -15"
        ),
        "sed-by-quoted-variable": f'S={SDD}; sed -n 1,80p "$S/references/fix-loop.md"',
        "sed-by-variable-and-path": f"S={SDD} && sed -n 1p $S/a.md {SDD}/b.md",
        "program-under-home": (
            "~/.claude/plugins/cache/impeccable/impeccable/4.7.0/scripts/impeccable context"
        ),
        "reads-in-pipelines-in-a-row": (
            "ls ~/.claude/skills/ | head -50; ls ~/.claude/plugins/marketplaces | head;"
            " find ~/.claude -path '*impeccable*' -iname '*design*md*' 2>/dev/null | head"
        ),
        "sed-into-sed": f"S={SDD}; sed -n 1,200p $S/prompt.md | sed -n '/```/,/```/p'",
        "program-under-home-variable": (
            '"$HOME/.claude/plugins/cache/impeccable/impeccable/4.7.0/scripts/impeccable" context'
        ),
    }


@pytest.mark.parametrize("name", list(plugin_reads()))
def test_a_pm_may_read_and_run_files_in_the_plugins_folder(store: Path, name: str) -> None:
    command = plugin_reads()[name]
    assert decide(bash(command, cwd=str(project_dir())), store, "pm") is None


PLUGIN_WRITES = {
    "redirect": "echo x > ~/.claude/plugins/foo",
    "rm-cache": "rm -r ~/.claude/plugins/cache/x",
    "cp-into-cache": "cp /tmp/x.py ~/.claude/plugins/cache/x/hooks/x.py",
    "sed-in-place": "sed -i s/a/b/ ~/.claude/plugins/cache/x/hooks/hooks.json",
    "sed-in-place-cluster": "sed -ni p ~/.claude/plugins/cache/x/hooks/hooks.json",
    "sed-expression": "sed -e p ~/.claude/plugins/cache/x/hooks/hooks.json",
    "touch": "touch ~/.claude/plugins/cache/x/hooks/new.py",
    "dd": "dd if=/tmp/x of=$HOME/.claude/plugins/cache/x/hooks/x.py",
    "tee": "cat /tmp/x | tee ~/.claude/plugins/x",
    "read-then-redirect-into": "cat ~/.claude/plugins/a > ~/.claude/plugins/b",
    "variable-then-rm": "S=~/.claude/plugins/cache; bash $S/x/run; rm -rf $S",
    "variable-exported": "S=~/.claude/plugins/cache; export S; bash /tmp/x.sh",
    "variable-in-a-shell-string": "S=~/.claude/plugins/cache; bash -c 'rm -rf $S'",
    "find-delete": "find ~/.claude/plugins/cache -name '*.pyc' -delete",
    "find-exec": "find ~/.claude/plugins -name x -exec rm {} \\;",
    "find-unknown-word": "find ~/.claude/plugins/cache $ACTION",
    "grep-into-xargs": "grep -rl x ~/.claude/plugins | xargs rm",
    "find-into-sed-e": "find ~/.claude/plugins | sed 's/^/rm /e'",
    "plugin-path-as-argument": "bash /tmp/x.sh ~/.claude/plugins/cache",
    "shell-string": "bash -c 'rm -rf ~/.claude/plugins/cache'",
    "python-body-writes": "python3 - <<'EOF'\nopen('/h/.claude/plugins/x', 'w')\nEOF",
    "cd-into": "cd ~/.claude/plugins/cache && rm -rf x",
    "unparsed-line": "cat ~/.claude/plugins/x; echo $(rm -rf ~/.claude/plugins)",
    "loop": "for d in ~/.claude/plugins/cache/*; do rm -rf $d; done",
    "git-in-it": "git -C ~/.claude/plugins/marketplaces/x pull",
    "sed-write-command": f"sed -n '1w {SDD}/x' {SDD}/SKILL.md",
    "sed-run-command": f"sed -n 's/^/rm /e' {SDD}/list",
    "sort-output": f"cat {SDD}/SKILL.md | sort -o {SDD}/SKILL.md",
    "into-a-shell": f"cat {SDD}/list | sh",
    "python-code": f"python3 -c 'import shutil; shutil.rmtree(\"{SDD}\")'",
    "python-module": f"python3 -m shutil {SDD}",
    "node-code": f"node -e 'x' {SDD}/x.js",
    "find-fprint": f"find {SDD} -fprint {SDD}/list",
    "assignment-prefix": f"S={SDD} rm -rf $S",
    # The shell hands a read's words to a later command.
    "last-argument": f"cat {SDD}/SKILL.md; rm -rf $_",
    "debug-trap": f"trap 'rm -rf $BASH_COMMAND' DEBUG; cat {SDD}/SKILL.md",
    "append-assignment": f"X+={SDD}; rm -rf $X",
    # A brace or a glob that expands to an option.
    "brace-python-code": "python3 {-c,\"import shutil; shutil.rmtree('" + SDD + "')\"}",
    "brace-shell-code": 'bash -x {-c,"rm -rf ' + SDD + '"}',
    "node-long-eval": 'node --eval=\'require("fs").rmSync("' + SDD + "\")'",
    "split-into-code": 'bash $Y-c$IFS"rm$IFS-rf$IFS' + SDD + '"',
    "find-brace-action": f"find {SDD} -d{{elete,elete}}",
    "find-brace-word": f"find {SDD} {{-delete,-print}}",
    "sed-brace-option": f"sed -n 1p {SDD}/x {{-i,-i}}",
    "sed-glob-word": f"sed -n 1p {SDD}/x *",
    # A sed operand by a variable that may not hold the assigned folder when sed runs: it may
    # expand to an option (`-i`).
    "sed-variable-assigned-after": f"sed -n 1p $CM/x {SDD}/SKILL.md; CM={SDD}",
    "sed-variable-assigned-twice": f"CM={SDD}; CM=-i; sed -n 1p $CM/x {SDD}/SKILL.md",
    "sed-variable-assigned-on-a-condition": f"false && CM={SDD}; sed -n 1p $CM/x {SDD}/SKILL.md",
    "sed-variable-assigned-in-a-subshell": f"(CM={SDD}); sed -n 1p $CM/x {SDD}/SKILL.md",
    "sed-variable-assigned-in-a-group": f"{{ CM={SDD}; }}; sed -n 1p $CM/x {SDD}/SKILL.md",
    "sed-variable-assigned-in-the-background": f"CM={SDD} & sed -n 1p $CM/x {SDD}/SKILL.md",
    "sed-variable-assigned-in-a-pipe": f"CM={SDD} | sed -n 1p $CM/x {SDD}/SKILL.md",
    "sed-variable-assigned-an-option": f"CM=-i; sed -n 1p $CM {SDD}/SKILL.md",
    "sed-variable-split-on-a-new-ifs": f"CM=/tmp/-i; IFS=/; sed -n 1p $CM {SDD}/SKILL.md",
    "sed-variable-with-a-default": f"CM={SDD}; sed -n 1p ${{CM:--i}} {SDD}/SKILL.md",
    "sed-second-variable": f"CM={SDD}; sed -n 1p $CM/x $X {SDD}/SKILL.md",
    "sed-variable-in-place": f"CM={SDD}; sed -i 1d $CM/SKILL.md",
    # A folder variable set again through a name built at run time.
    "sed-variable-printed-into-a-built-name": (
        f'CM={SDD}; n=C; m=M; printf -v "$n$m" "%s" -i; sed -n 1p $CM {SDD}/b'
    ),
    "sed-variable-read-into-a-built-name": (
        f'CM={SDD}; n=C; m=M; read $n$m <<< "-i"; sed -n 1p $CM/a {SDD}/b'
    ),
    "sed-variable-mapped-into-a-built-name": (
        f'CM={SDD}; n=C; m=M; mapfile -t $n$m <<< "-i"; sed -n 1p $CM {SDD}/b'
    ),
    # In a subshell the binding ends at `)`: `$CM` is then whatever the caller's CM holds.
    "sed-variable-in-a-subshell-on-two-lines": f"(CM={SDD}\n); sed -n 1p $CM {SDD}/b",
    "sed-variable-set-by-getopts": f"CM={SDD}; getopts i: $n -i; sed -n 1p $CM {SDD}/b",
    # An unquoted `$HOME` program: a HOME set through a built name splits into other words.
    "program-under-a-home-read-into-a-built-name": (
        f'a=HO; b=ME; read $a$b <<< "rm -rf {SDD}"; $HOME/.claude/plugins/x/run'
    ),
    # A read piped into a sed that writes or runs, or a pipeline after one that only reads.
    "sed-filter-writes": f"find {SDD} | sed -n 'w /tmp/list'",
    "sed-filter-writes-each-line": f"find {SDD} | sed 'W /tmp/list'",
    "sed-filter-in-place": f"cat {SDD}/a | sed -i 1d {SDD}/b",
    "pipeline-after-a-read": f"ls {SDD} | head; find {SDD} | xargs rm",
    # A program run under the home folder is run, its arguments are not read.
    "program-under-home-given-a-path": f"~/bin/tool --rm {SDD}",
    # A read in a group or subshell whose output a pipe acts on.
    "subshell-into-xargs": f"(find {SDD} -type f) | xargs rm",
    "group-into-xargs": f"{{ :; find {SDD}; }} | xargs rm",
}


@pytest.mark.parametrize("name", PLUGIN_WRITES)
def test_a_pm_cannot_write_the_plugins_folder_through_bash(store: Path, name: str) -> None:
    refusal = decide(bash(PLUGIN_WRITES[name], cwd=str(project_dir())), store, "pm")
    assert refusal is not None
    assert PLUGINS_REFUSAL in refusal


def test_a_script_in_the_plugins_folder_is_not_run_from_inside_it(store: Path) -> None:
    """A command whose working folder is in the zone may write it by a relative name."""
    folder = claude_dir() / "plugins" / "cache" / "x"
    folder.mkdir(parents=True)
    refusal = decide(bash("bash scripts/run", cwd=str(folder)), store, "pm")
    assert refusal is not None
    assert PLUGINS_REFUSAL in refusal


# --- a write into the parent of a zone: replacing `~/.claude` replaces the hooks inside it ---

PARENT_WRITES = {
    "rm-claude-folder": "rm -rf ~/.claude",
    "mv-claude-folder-away": "mv ~/.claude /tmp/old",
    "mv-over-claude-folder": "rm -rf ~/.claude && mv /tmp/c ~/.claude",
    "rm-config-folder": "rm -rf ~/.config",
    "rm-home": "rm -rf ~",
    "rm-home-variable": "rm -rf $HOME",
    "rm-quoted-home-variable": 'rm -rf "${HOME}/.claude"',
    "sudo-rm-home": "sudo rm -rf ~",
    "cp-into-claude-folder": "cp /tmp/x/settings.json ~/.claude/",
    "cp-into-relative-claude-folder": "cp /tmp/x/settings.json .claude/",
    "cp-target-directory-option": "cp -t ~/.claude /tmp/x/settings.json",
    "cp-into-config-folder": "cp -r /tmp/evil/coven ~/.config/",
    "install-into-claude-folder": "install -m 600 /tmp/x/settings.json ~/.claude",
    "rsync-into-home": "rsync -a /tmp/evil/ ~/",
    "tar-into-home": "tar -C ~ -xf x.tar",
    "tar-directory-option": "tar --directory=~/.config -xf x.tar",
    "tar-cluster-into-home": "tar -xCf ~ x.tar",
    "unzip-into-home": "unzip -d ~ x.zip",
    "rm-then-link": "rm -rf .claude && ln -s /tmp/e .claude",
    "link-over-claude-folder": "ln -sfn /tmp/e ~/.claude",
    "chmod-claude-folder": "chmod -R 777 ~/.claude",
    "tee-into-claude-folder": "echo x | tee ~/.claude",
    "redirect-into-home": "echo x > ~",
    "cd-home-then-rm-claude": "cd ~ && rm -rf .claude",
    "cd-home-then-rm-dotted-glob": "cd ~ && rm -rf .c*",
    "cd-home-then-rm-all-dotted": "cd ~ && rm -rf .*",
    "cd-home-then-mv-config": "cd ~ && mv .config /tmp/c",
    "cd-then-rm-parent": "cd ~/proj && rm -rf ..",
    "find-delete-home": "find ~ -delete",
    "cd-home-then-untar": "cd ~ && tar -xf /tmp/x.tar",
    "cd-config-then-rsync-here": "cd ~/.config && rsync -a /tmp/evil/ .",
    "cd-home-then-copy-here": "cd ~ && cp -r /tmp/evil/.claude .",
    "cd-home-then-move-in": "cd ~ && mv /tmp/c .claude",
    "cd-variable-then-rm-sibling": "cd $HOME/.config && rm -rf ../.claude",
    "cd-unknown-then-rm-claude": "cd $SOMEWHERE && rm -rf .claude",
    "path-around-home": "rm -rf ~/x/../.claude",
    "shell-string": "bash -c 'rm -rf ~/.claude'",
    "braced-operator": "rm -rf ${HOME:?}/.claude",
}


@pytest.mark.parametrize("name", PARENT_WRITES)
def test_a_restricted_session_cannot_replace_a_folder_that_holds_a_zone(
    run: Run, store: Path, name: str
) -> None:
    payload = bash(PARENT_WRITES[name], cwd=str(project_dir()))
    proc = run(payload, role="pm")
    assert proc.returncode == 2
    assert "cannot write to" in proc.stderr
    assert run(payload).returncode == 0


def test_the_copy_edit_replace_sequence_is_refused_at_its_last_step(run: Run, store: Path) -> None:
    """Copy the folder, edit the copy's hook, put the copy back: the first step only reads."""
    copy = run(bash("cp -a ~/.claude /tmp/c"), role="pm")
    assert copy.returncode == 0, copy.stderr
    swap = run(bash("rm -rf ~/.claude && mv /tmp/c ~/.claude"), role="pm")
    assert swap.returncode == 2
    assert "cannot write to" in swap.stderr


@pytest.mark.parametrize(
    "command",
    [
        "cp -a ~/.claude /tmp/c",
        "cp -a ~ /tmp/backup",
        "ls ~",
        "ls ~/.config ~/.claude",
        "cd ~ && ls -a",
        "cat ~/.config/git/config",
        "cp /tmp/x ~/note.md",
        "rm -rf ~/scratch",
        "mv /tmp/a ~/.cache/b",
        "echo x > ~/note.md",
        "tar -xf x.tar -C /tmp/out",
        "tar -cf /tmp/x.tar ~/.claude",
        "unzip -d /tmp/out x.zip",
        "rsync -a ~/proj/ /tmp/backup/",
        "cd src && rm -rf build",
        "cd src && rm -rf coven/__pycache__",
        "rm -rf .git/hooks-old",
        "cd ~/proj && rm -rf ../scratch",
        "find ~ -name '*.pyc'",
        "cd src && cp /tmp/a .",
        "cd src && tar -xf /tmp/x.tar",
        "cd ~/proj && rsync -a /tmp/x/ .",
        "cd .. && rm -rf proj",
        "cd ~ && tar -cf /tmp/x.tar .",
        "unzip -l x.zip",
    ],
)
def test_a_restricted_session_reads_folders_that_hold_a_zone(
    run: Run, store: Path, command: str
) -> None:
    proc = run(bash(command, cwd=str(project_dir())), role="pm")
    assert proc.returncode == 0, proc.stderr


# --- variables that name a zone: `$CLAUDE_CONFIG_DIR`, `${XDG_CONFIG_HOME}`, `$CLAUDE_PLUGIN_ROOT` ---

VARIABLE_WRITES = {
    "cd-config-dir-rm-plugins": "cd $CLAUDE_CONFIG_DIR && rm -rf plugins",
    "cd-quoted-config-dir-write-settings": 'cd "$CLAUDE_CONFIG_DIR" && echo {} > settings.json',
    "rm-braced-plugins": "rm -rf ${CLAUDE_CONFIG_DIR}/plugins",
    "redirect-braced-settings": "echo {} > ${CLAUDE_CONFIG_DIR}/settings.json",
    "rm-quoted-plugins": 'rm -rf "$CLAUDE_CONFIG_DIR/plugins"',
    "rm-quoted-braced-plugins": 'rm -rf "${CLAUDE_CONFIG_DIR}/plugins"',
    "rm-braced-with-default": "rm -rf ${CLAUDE_CONFIG_DIR:-~/.claude}/plugins",
    "rm-config-dir-itself": "rm -rf $CLAUDE_CONFIG_DIR",
    "cd-xdg-rm-coven": "cd ${XDG_CONFIG_HOME} && rm -rf coven",
    "redirect-xdg-config": "echo {} > $XDG_CONFIG_HOME/coven/config.json",
    "rm-braced-xdg": 'rm -rf "${XDG_CONFIG_HOME}"',
    "sed-plugin-root": "sed -i d ${CLAUDE_PLUGIN_ROOT}/hooks/hooks.json",
    "rm-plugin-root-hooks": "rm -rf ${CLAUDE_PLUGIN_ROOT}/hooks",
    "rm-unbraced-plugin-root": "rm -rf $CLAUDE_PLUGIN_ROOT/hooks",
    "write-quoted-plugin-root": 'echo x > "${CLAUDE_PLUGIN_ROOT}/hooks/prod_guard.py"',
    "rm-home-claude-braced": "rm -rf ${HOME}/.claude",
    "write-home-settings": "echo {} > ${HOME}/.claude/settings.json",
}
# What the hook sees of each variable: as the test environment sets them, none of them, or
# somewhere else. The command may run under the others, so every state is refused.
VARIABLE_STATES = {
    "set": {},
    "unset": {"CLAUDE_CONFIG_DIR": None, "XDG_CONFIG_HOME": None, "CLAUDE_PLUGIN_ROOT": None},
    "empty": {"CLAUDE_CONFIG_DIR": "", "XDG_CONFIG_HOME": "", "CLAUDE_PLUGIN_ROOT": ""},
    "elsewhere": {
        "CLAUDE_CONFIG_DIR": "/srv/elsewhere/claude",
        "XDG_CONFIG_HOME": "/srv/elsewhere/xdg",
        "CLAUDE_PLUGIN_ROOT": "/srv/elsewhere/plugin",
    },
}


@pytest.mark.parametrize("state", VARIABLE_STATES)
@pytest.mark.parametrize("name", VARIABLE_WRITES)
def test_a_restricted_session_cannot_reach_a_zone_through_a_variable(
    run: Run, store: Path, monkeypatch: pytest.MonkeyPatch, name: str, state: str
) -> None:
    for variable, value in VARIABLE_STATES[state].items():
        if value is None:
            monkeypatch.delenv(variable, raising=False)
        else:
            monkeypatch.setenv(variable, value)
    payload = bash(VARIABLE_WRITES[name], cwd=str(project_dir()))
    proc = run(payload, role="pm")
    assert proc.returncode == 2
    assert "cannot write to" in proc.stderr
    assert run(payload).returncode == 0


@pytest.mark.parametrize(
    "command",
    [
        "echo $CLAUDE_CONFIG_DIR",
        'echo "${XDG_CONFIG_HOME}"',
        "cd $HOME && ls",
        "ls $HOME/projects",
        "cd ${XDG_CONFIG_HOME}/git && ls",
        "rm -rf $HOME/scratch",
        "cd src && rm -rf plugins",
        "ls $OTHER_DIR/plugins",
    ],
)
def test_a_restricted_session_may_name_a_variable_without_writing_a_zone(
    run: Run, store: Path, command: str
) -> None:
    proc = run(bash(command, cwd=str(project_dir())), role="pm")
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


# Every coven subcommand, classified by reading what it does. A writer changes the memory store or
# coven's config; the hook must refuse it to a restricted session. A new subcommand fails
# `test_every_coven_subcommand_is_classified` until it is put in one of the two sets.
WRITERS = frozenset({"approve", "setup", "memory adopt", "memory init", "memory sync"})
NON_WRITERS = frozenset(
    {
        "doctor",  # read-only
        "guard check",  # reads the file it is given
        "guard explain",  # runs the guard's decision on a command, writes nothing
        "pm status",  # reads sessions
        "pm launch",  # prints the command to run
        "pm resume",  # writes a prompt file under the cache folder, never the store or config
        "pm health",  # reads state records
        "project",  # writes the standard into the repo it is pointed at; see the next test
        "opencode doctor",  # reads the profile folder and runs a loopback canary server
        "opencode install",  # npm into the profile folder
        "opencode run",  # starts the pinned OpenCode with the profile env
        "opencode setup",  # writes the profile folder only, never the store or the config
    }
)


def subcommand_paths(parser: argparse.ArgumentParser, prefix: str = "") -> set[str]:
    """The words the user types for every command that is not only a group of commands."""
    found: set[str] = set()
    for action in parser._actions:  # argparse has no public way to list subcommands
        if isinstance(action, argparse._SubParsersAction):
            for name, child in action.choices.items():
                found |= subcommand_paths(child, f"{prefix}{name} ")
            return found
    return {prefix.strip()}


def test_every_coven_subcommand_is_classified_as_a_writer_or_not() -> None:
    commands = subcommand_paths(cli.build_parser())
    assert WRITERS.isdisjoint(NON_WRITERS)
    assert commands == WRITERS | NON_WRITERS, "classify the new or removed coven subcommand"


def test_the_hook_refuses_exactly_the_subcommands_that_write_the_store_or_the_config() -> None:
    """A subcommand added to coven's CLI that writes either must be added to the hook's list."""
    assert pg.STORE_WRITING_SUBCOMMANDS == WRITERS


@pytest.mark.parametrize("command", sorted(WRITERS))
def test_a_pm_is_refused_every_writing_subcommand(store: Path, command: str) -> None:
    assert decide(bash(f"coven {command}"), store, "pm") is not None


@pytest.mark.parametrize("command", ["coven project ~/coven-memory", "coven project ."])
def test_a_pm_cannot_point_the_project_command_at_the_store(store: Path, command: str) -> None:
    """`coven project` writes into any repo it is given; a PM that names the store (or runs in it)
    meets the store rule, so it is not a writer of the hook's list."""
    named = command.replace("~", str(home()))
    assert decide(bash(named, cwd=str(store)), store, "pm") is not None


def test_a_pm_running_opencode_setup_is_not_read_as_the_coven_setup_command(store: Path) -> None:
    """`coven opencode setup` writes only the profile folder, so the store rule does not apply
    when the working directory is outside the store."""
    assert decide(bash("coven opencode setup", cwd="/tmp"), store, "pm") is None


# --- explain gives the verdict the hook gives, in every state of the store ---


def _state_valid(path: Path) -> Path:
    return make_store(path)


def _state_no_valid_guard(path: Path) -> Path:
    made = make_store(path, committed=False, guard={"targets": "nope"})
    commit(made)
    return made


def _state_invalid_head_with_a_valid_version(path: Path) -> Path:
    made = make_store(path)
    break_guard(made)
    return made


def _state_guard_absent_at_head(path: Path) -> Path:
    made = make_store(path)
    (made / "systems/guard.json").unlink()
    commit(made)
    return made


def _state_no_commits(path: Path) -> Path:
    return make_store(path, committed=False)


def _state_no_folder(path: Path) -> Path:
    return path


STORE_STATES = {
    "valid": _state_valid,
    "no-valid-guard": _state_no_valid_guard,
    "invalid-head-with-a-valid-version": _state_invalid_head_with_a_valid_version,
    "guard-absent-at-head": _state_guard_absent_at_head,
    "no-commits": _state_no_commits,
    "no-folder": _state_no_folder,
}
VERDICT_PAYLOADS = {
    "ls": bash("ls"),
    "production": bash(PROD_COMMAND),
    "start-claude": bash("claude -p hi"),
    "mcp-send": tool("mcp__mail__send"),
    "mcp-read": tool("mcp__x__read"),
    "read": tool("Read", file_path="/tmp/x"),
    "grep": tool("Grep", pattern="x"),
    "write": tool("Write", file_path="/tmp/x", content=""),
    "edit": tool("Edit", file_path="/tmp/x", old_string="a", new_string="b"),
    "task": tool("Task", prompt="x"),
}


@pytest.mark.parametrize("name", VERDICT_PAYLOADS)
@pytest.mark.parametrize("state", STORE_STATES)
def test_the_verdict_is_what_the_hook_gives_an_unrestricted_and_a_restricted_session(
    state: str, name: str
) -> None:
    path = STORE_STATES[state](home() / "coven-memory")
    payload = VERDICT_PAYLOADS[name]
    now = datetime.now(UTC)
    refused_for_user = pg.decide(payload, path, None, now) is not None
    refused_for_a_pm = pg.decide(payload, path, "pm", now) is not None
    expected = (
        "refused for every session"
        if refused_for_user
        else "refused for a PM"
        if refused_for_a_pm
        else "allowed"
    )
    for role in (None, "pm", ""):
        assert pg.explain(payload, path, role, now).verdict == expected, role


@pytest.mark.parametrize("tool_name", ["Write", "Edit", "MultiEdit", "NotebookEdit"])
def test_with_no_valid_guard_an_edit_is_refused_for_a_pm_only(tool_name: str) -> None:
    path = _state_no_valid_guard(home() / "coven-memory")
    payload = tool(tool_name, file_path="/tmp/x")
    unrestricted = explanation(payload, path)
    assert (unrestricted.verdict, unrestricted.message) == ("refused for a PM", None)
    assert NO_VALID_GUARD in unrestricted.restricted
    restricted = explanation(payload, path, "pm")
    assert restricted.verdict == "refused for a PM"
    assert restricted.message == unrestricted.restricted


def test_with_no_valid_guard_a_read_is_allowed_and_bash_is_refused_for_every_session() -> None:
    path = _state_no_valid_guard(home() / "coven-memory")
    assert explanation(tool("Read", file_path="/tmp/x"), path).verdict == "allowed"
    refused = explanation(bash("ls"), path)
    assert refused.verdict == "refused for every session"
    assert NO_VALID_GUARD in refused.message


def test_explain_says_when_the_store_could_not_be_read() -> None:
    path = _state_no_commits(home() / "coven-memory")
    found = explanation(bash("ls"), path)
    assert "no commits" in found.unreadable
    assert found.verdict == "refused for every session"
    assert explanation(tool("Read", file_path="/tmp/x"), path).unreadable is not None
    assert explanation(bash("ls"), _state_valid(home() / "other")).unreadable is None


@pytest.mark.parametrize("name", VERDICT_PAYLOADS)
def test_explain_without_a_readable_config_gives_the_verdict_the_hook_gives(
    run: Run, name: str
) -> None:
    write_config("{nope")
    payload = VERDICT_PAYLOADS[name]
    problem = pg.ConfigError("bad config")
    found = pg.explain_without_config(payload, problem, None)
    refused_for_a_pm = run(payload, role="pm").returncode == 2
    assert run(payload).returncode == (2 if found.message else 0)
    assert found.verdict == ("refused for a PM" if refused_for_a_pm else "allowed")
    assert (found.message, found.hits) == (None, [])
    assert found.unreadable == "bad config"
    assert pg.explain_without_config(payload, problem, "pm").message == found.restricted


# --- the log names the role class and the system or rule of each decision ---

RULE_CALLS = {
    "role-change": bash("unset COVEN_SESSION_ROLE"),
    "installer": bash("npx claude-code --version"),
    "claude-start": bash("claude -p hi"),
    "remote-host": bash("ssh $DB_HOST"),
    "coven-write": bash("coven approve step-1"),
    "store-write": tool("Write", file_path="~/coven-memory/user/note.md"),
    "config-write": tool("Write", file_path="~/.config/coven/config.json"),
    "claude-settings": tool("Write", file_path="~/.claude/settings.json"),
    "claude-plugins": tool("Write", file_path="~/.claude/plugins/x/hook.py"),
}


@pytest.mark.parametrize("name", RULE_CALLS)
def test_a_restricted_refusal_names_its_rule_as_the_system(store: Path, name: str) -> None:
    payload = json.loads(json.dumps(RULE_CALLS[name]).replace("~", str(home())))
    found = explanation(payload, store, "pm")
    assert found.message is not None
    assert found.system == name


def test_a_target_refusal_names_the_target_system(store: Path) -> None:
    assert explanation(bash(PROD_COMMAND), store).system == "orders-db"
    assert explanation(bash(PROD_COMMAND), store, "pm").system == "orders-db"


def test_a_call_to_two_targets_names_both(store: Path) -> None:
    both = explanation(bash(PROD_COMMAND + " && mail-cli send x"), store)
    assert both.system == "orders-db,mail-send"


def test_an_unreadable_store_is_named_unreadable_store() -> None:
    found = explanation(bash("ls"), home() / "nowhere")
    assert found.message is not None
    assert found.system == "unreadable-store"


def test_a_store_with_no_valid_guard_is_named_invalid_guard() -> None:
    path = _state_no_valid_guard(home() / "coven-memory")
    assert explanation(bash("ls"), path).system == "invalid-guard"
    assert explanation(tool("Write", file_path="/tmp/x"), path, "pm").system == "invalid-guard"


def test_a_store_without_guard_json_at_head_is_named_missing_guard() -> None:
    path = _state_guard_absent_at_head(home() / "coven-memory")
    assert explanation(tool("mcp__x__read"), path, "pm").system == "missing-guard"


def test_an_unreadable_config_is_named_unreadable_config() -> None:
    problem = pg.ConfigError("broken")
    found = pg.explain_without_config(bash("ls"), problem, "pm")
    assert found.message is not None
    assert found.system == "unreadable-config"


def test_an_allowed_call_has_no_system(store: Path) -> None:
    found = explanation(bash("ls"), store, "pm")
    assert (found.message, found.system) == (None, None)


@pytest.mark.parametrize("name", RULE_CALLS)
def test_the_log_holds_the_rule_name_of_a_refusal(run: Run, store: Path, name: str) -> None:
    payload = json.loads(json.dumps(RULE_CALLS[name]).replace("~", str(home())))
    assert run(payload, role="pm").returncode == 2
    (record,) = read_log()
    assert (record["decision"], record["system"]) == ("deny", name)


def test_the_log_holds_the_target_system_of_a_refusal(run: Run, store: Path) -> None:
    run(bash(PROD_COMMAND))
    run(bash("ls"))
    refused, allowed = read_log()
    assert refused["system"] == "orders-db"
    assert allowed["system"] is None


@pytest.mark.parametrize(
    ("role", "logged"),
    [(None, "unset"), ("", "other"), ("pm", "pm"), ("x", "other"), ("gm-secret-7", "other")],
    ids=["unset", "empty", "pm", "x", "long-value"],
)
def test_the_log_holds_the_role_class_and_never_the_raw_role(
    run: Run, store: Path, role: str | None, logged: str
) -> None:
    run(bash(PROD_COMMAND), role=role)
    run(bash("ls"), role=role)
    refused, allowed = read_log()
    assert refused["role"] == allowed["role"] == logged
    path = Path(os.environ["XDG_CACHE_HOME"]) / "coven" / "logs" / "prod-guard.jsonl"
    if role not in (None, "", "pm", "x"):
        assert role not in path.read_text()
