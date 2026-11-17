"""`coven approve`: the approval files it writes, and what the real production guard does with them.
Every system and host here is invented; a command is only a string the guard reads."""

import json
import os
import signal
import subprocess
import sys
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from support import REPO, git

from coven import approve, cli, guard
from coven.errors import CovenError
from coven.memory import store as memory_store

HOOK = REPO / "hooks" / "prod_guard.py"
ROLE = "COVEN_SESSION_ROLE"
TOKEN = "gh" + "p_" + "A1b2" * 9  # looks like a GitHub token to the store's secret scan
NOW = datetime(2026, 10, 27, 12, 0, 0, tzinfo=timezone(timedelta(hours=2)))

ORDERS = "psql -h orders-db.example.internal -c 'select 1'"
ORDERS_AND_AUDIT = "psql -h orders-db.example.internal -c 'select 1 from audit_log'"
SEND = "mail-cli send --to team-list"
GUARD = {
    "targets": [
        {
            "system": "orders-db",
            "kind": "production",
            "match": ["psql\\s+.*-h\\s*orders-db"],
            "tools": ["mcp__orders__write"],
        },
        {"system": "audit-db", "kind": "production", "match": ["psql\\s+.*audit_log"]},
        {
            "system": "mail-send",
            "kind": "gated",
            "match": ["mail-cli\\s+send"],
            "tools": ["mcp__mail__send"],
        },
    ]
}


def home() -> Path:
    return Path(os.environ["HOME"])


def configure(store: Path) -> None:
    file = home() / ".config" / "coven" / "config.json"
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps({"memory_path": str(store)}))


def make_store(path: Path, guard_json: object = GUARD) -> Path:
    """A store with the real pre-commit hook, a committed guard.json, and coven's config pointing
    at it."""
    memory_store.init_store(path, None)
    (path / "systems").mkdir(exist_ok=True)
    (path / "systems" / "guard.json").write_text(json.dumps(guard_json))
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "systems: guard")
    configure(path)
    return path


@pytest.fixture
def store() -> Path:
    return make_store(home() / "coven-memory")


def head(store: Path) -> str:
    return git(store, "rev-parse", "HEAD").strip()


def write_step(store: Path, approval_id: str, text: str) -> Path:
    path = store / "approvals" / f"{approval_id}.step"
    path.parent.mkdir(exist_ok=True)
    path.write_text(text)
    return path


def run(approval_id: str, *, now: datetime = NOW, **fields: Any) -> list[str]:
    """`coven approve <id>` for the step file already in the store, at a fixed clock."""
    return approve.approve(approval_id, fields.pop("expires_in", "1h"), now, **fields)


def approved(store: Path, approval_id: str, text: str, **kwargs: Any) -> list[str]:
    """A successful approve, which must leave the store committed and clean."""
    write_step(store, approval_id, text)
    lines = run(approval_id, **kwargs)
    assert git(store, "status", "--porcelain", "--untracked-files=all") == ""
    return lines


def revoked(approval_id: str) -> list[str]:
    """A successful revoke in the default store, which must leave it committed and clean."""
    lines = approve.revoke(approval_id)
    assert git(home() / "coven-memory", "status", "--porcelain", "--untracked-files=all") == ""
    return lines


def refused(store: Path, approval_id: str, text: str, message: str, **kwargs: Any) -> str:
    """Runs approve on a step that must be refused; returns the message."""
    write_step(store, approval_id, text)
    with pytest.raises(CovenError, match=message) as caught:
        run(approval_id, **kwargs)
    return str(caught.value)


def committed(store: Path, name: str) -> dict[str, Any]:
    result: dict[str, Any] = json.loads(git(store, "show", f"HEAD:approvals/{name}"))
    return result


def approval_files(store: Path) -> list[str]:
    folder = store / "approvals"
    return sorted(p.name for p in folder.glob("*.json")) if folder.is_dir() else []


def step_only(store: Path, approval_id: str) -> str:
    """What `git status` shows after a refusal: the step file and nothing else."""
    return f"?? approvals/{approval_id}.step\n"


def live() -> datetime:
    """The real clock: the real guard compares approvals with it."""
    return datetime.now().astimezone()


def bash(command: str) -> dict[str, object]:
    return {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": "/tmp"}


def guard_exit(payload: dict[str, object]) -> int:
    """The exit code of the real hook, run as Claude Code runs it: 0 allows, 2 refuses."""
    proc = subprocess.run(
        [sys.executable, "-I", str(HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=dict(os.environ),
        check=False,
    )
    return proc.returncode


# --- the step file ---


def test_each_line_outside_a_fence_is_one_command_stored_stripped() -> None:
    step = approve.parse_step("  first one \n\n\t\n   \nsecond\n")
    assert step == approve.Step(["first one", "second"], [])


def test_a_fenced_block_is_one_command_joined_with_newlines() -> None:
    text = "before\n```sh\nline 1\n  line 2\n\nline 4\n```\nafter\n"
    assert approve.parse_step(text).commands == ["before", "line 1\n  line 2\n\nline 4", "after"]


def test_tool_lines_name_mcp_tools_and_are_plain_text_inside_a_fence() -> None:
    text = "tool: mcp__orders__write\nls\n```\ntool: not-a-tool\n```\ntool:   mcp__mail__send  \n"
    assert approve.parse_step(text) == approve.Step(
        ["ls", "tool: not-a-tool"], ["mcp__orders__write", "mcp__mail__send"]
    )


@pytest.mark.parametrize("line", ["tool:", "tool:   ", "tool: two words"])
def test_a_tool_line_needs_one_exact_name(line: str) -> None:
    with pytest.raises(CovenError, match="tool"):
        approve.parse_step(f"{line}\n")


def test_an_unclosed_fence_is_an_error() -> None:
    with pytest.raises(CovenError, match="unclosed"):
        approve.parse_step("```\nline 1\nline 2\n")


def test_an_empty_fence_is_an_error() -> None:
    with pytest.raises(CovenError, match="empty"):
        approve.parse_step("```\n\n```\n")


def test_a_repeated_command_or_tool_is_kept_once() -> None:
    assert approve.parse_step("a\nb\na\ntool: t\ntool: t\n") == approve.Step(["a", "b"], ["t"])


def test_an_empty_file_has_nothing_in_it() -> None:
    assert approve.parse_step("") == approve.Step([], [])


def test_crlf_line_ends_leave_no_carriage_return_in_a_command() -> None:
    step = approve.parse_step("a\r\n```\r\nline 1\r\nline 2\r\n```\r\ntool: t\r\n")
    assert step == approve.Step(["a", "line 1\nline 2"], ["t"])


def test_trailing_blanks_and_blank_lines_are_not_part_of_a_command() -> None:
    text = "one \t \n\n```\nline 1  \nline 2 \t\n\n```\n\n\n"
    assert approve.parse_step(text).commands == ["one", "line 1  \nline 2"]


@pytest.mark.parametrize(
    "text",
    ["a\rb\n", "```\nline 1\rline 2\n```\n", "```\nline 1\r\r\nline 2\n```\n"],
    ids=["plain", "fenced", "doubled-before-the-line-end"],
)
def test_a_carriage_return_inside_a_command_is_refused(text: str) -> None:
    with pytest.raises(CovenError, match="carriage return"):
        approve.parse_step(text)


def test_a_unicode_line_separator_is_not_a_line_end() -> None:
    assert approve.parse_step("echo a echo b\n").commands == ["echo a echo b"]


def test_a_longer_opener_needs_the_same_closer() -> None:
    text = "````sh\nline 1\nline 2\n````  \nafter\n"
    assert approve.parse_step(text).commands == ["line 1\nline 2", "after"]


@pytest.mark.parametrize(
    "text",
    [
        "````\nline 1\n```\nline 2\n````\n",  # a shorter fence inside
        "```\nline 1\n````\nline 2\n```\n",  # a longer one inside
        "```\nline 1\n```sh\nline 2\n```\n",  # another opener inside
    ],
    ids=["shorter-inside", "longer-inside", "opener-inside"],
)
def test_another_fence_line_inside_a_block_is_an_error(text: str) -> None:
    with pytest.raises(CovenError, match="fence"):
        approve.parse_step(text)


# --- the duration ---


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("90m", timedelta(minutes=90)),
        ("2h", timedelta(hours=2)),
        ("1h30m", timedelta(hours=1, minutes=30)),
        ("12h", timedelta(hours=12)),
        ("720m", timedelta(hours=12)),
    ],
)
def test_duration_forms(text: str, expected: timedelta) -> None:
    assert approve.parse_duration(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        *("", "0m", "0h0m", "12h1m", "13h", "721m", "90", "1d", "-5m", "2h30", "m", "1.5h"),
        *("99999999999999999999h", "10000m", "9999h", "٣h", " 2h", "2h\n"),
    ],
)
def test_bad_or_too_long_durations_are_refused(text: str) -> None:
    with pytest.raises(CovenError, match="duration"):
        approve.parse_duration(text)


def test_the_longest_duration_is_the_guards_cap() -> None:
    assert approve.MAX_DURATION == guard.load_guard().MAX_APPROVAL


# --- matching commands and tools to systems ---


def test_one_matching_system_gets_one_file_named_by_the_id(store: Path) -> None:
    lines = approved(store, "step-1", f"{ORDERS}\n", expires_in="90m")
    assert approval_files(store) == ["step-1.json"]
    record = committed(store, "step-1.json")
    assert list(record) == ["id", "system", "commands", "tools", "expires"]
    assert record == {
        "id": "step-1",
        "system": "orders-db",
        "commands": [ORDERS],
        "tools": [],
        "expires": "2026-10-27T13:30:00+02:00",
    }
    assert lines[0] == (
        "approved step-1.json system=orders-db expires=2026-10-27T13:30:00+02:00 commands=1 tools=0"
    )


def test_every_approved_command_and_tool_is_printed_under_its_line(store: Path) -> None:
    multi = "psql -h orders-db.example.internal -c 'update t set a = 1\nwhere id = 7;'"
    step = f"{ORDERS}\n```\n{multi}\n```\ntool: mcp__orders__write\n"
    lines = approved(store, "step-p", step)
    assert lines == [
        "approved step-p.json system=orders-db expires=2026-10-27T13:00:00+02:00"
        " commands=2 tools=1",
        f"  command: {json.dumps(ORDERS)}",
        f"  command: {json.dumps(multi)}",
        '  tool: "mcp__orders__write"',
    ]


def test_a_command_matching_two_systems_goes_to_each_in_its_own_file(store: Path) -> None:
    lines = approved(store, "step-2", f"{ORDERS_AND_AUDIT}\n")
    assert approval_files(store) == ["step-2-audit-db.json", "step-2-orders-db.json"]
    for name, system in (
        ("step-2-audit-db.json", "audit-db"),
        ("step-2-orders-db.json", "orders-db"),
    ):
        record = committed(store, name)
        assert (record["id"], record["system"], record["commands"]) == (
            "step-2",
            system,
            [ORDERS_AND_AUDIT],
        )
    assert [line.split()[2] for line in lines if line.startswith("approved")] == [
        "system=audit-db",
        "system=orders-db",
    ]


def test_each_command_and_tool_goes_only_to_the_systems_it_belongs_to(store: Path) -> None:
    approved(store, "step-3", f"{ORDERS}\n{SEND}\ntool: mcp__mail__send\n")
    orders, mail = (
        committed(store, "step-3-orders-db.json"),
        committed(store, "step-3-mail-send.json"),
    )
    assert (orders["commands"], orders["tools"]) == ([ORDERS], [])
    assert (mail["commands"], mail["tools"]) == ([SEND], ["mcp__mail__send"])
    assert approval_files(store) == ["step-3-mail-send.json", "step-3-orders-db.json"]


def test_a_tool_alone_is_approved_for_the_system_that_lists_it(store: Path) -> None:
    lines = approved(store, "tool-1", "tool: mcp__orders__write\n")
    record = committed(store, "tool-1.json")
    assert (record["system"], record["commands"], record["tools"]) == (
        "orders-db",
        [],
        ["mcp__orders__write"],
    )
    assert lines[0].endswith("commands=0 tools=1")


@pytest.mark.parametrize(
    ("text", "named"),
    [
        ("ls -la\n", "ls -la"),
        (f"{ORDERS}\nls -la\n", "ls -la"),
        ("tool: mcp__other__call\n", "mcp__other__call"),
    ],
    ids=["command", "second-command", "tool"],
)
def test_something_no_target_matches_is_an_error_that_writes_nothing(
    store: Path, text: str, named: str
) -> None:
    start = head(store)
    message = refused(store, "step-4", text, "no guard.json target")
    assert named in message
    assert approval_files(store) == []
    assert head(store) == start


def test_a_command_the_guard_reads_as_data_is_not_matched(store: Path) -> None:
    """The guard matches the command's code, not text it only prints: approve judges it the same."""
    refused(store, "step-d", "echo 'psql -h orders-db.example.internal'\n", "no guard.json target")
    assert approval_files(store) == []


def test_system_option_sends_everything_to_that_system(store: Path) -> None:
    approved(store, "step-5", "psql -h elsewhere -c 'select 1 from audit_log'\n", system="audit-db")
    assert approval_files(store) == ["step-5.json"]  # one system: no suffix
    assert committed(store, "step-5.json")["system"] == "audit-db"


@pytest.mark.parametrize(
    ("text", "system", "message"),
    [
        (f"{ORDERS}\n", "mail-send", "does not claim"),
        ("tool: mcp__orders__write\n", "mail-send", "does not claim"),
        (f"{ORDERS}\n", "no-such-system", "no target for system"),
        (f"{ORDERS_AND_AUDIT}\n", "audit-db", "also claimed by orders-db"),
        (f"{ORDERS_AND_AUDIT}\n", "orders-db", "also claimed by audit-db"),
    ],
    ids=["command-outside", "tool-not-listed", "unknown-system", "audit-shared", "orders-shared"],
)
def test_system_option_refuses_what_the_system_does_not_cover_alone(
    store: Path, text: str, system: str, message: str
) -> None:
    refused(store, "step-6", text, message, system=system)
    assert approval_files(store) == []


def test_a_tool_claimed_by_two_systems_is_refused_for_one_of_them() -> None:
    both = {
        "targets": [
            {"system": "a", "kind": "gated", "tools": ["mcp__x__go"]},
            {"system": "b", "kind": "gated", "tools": ["mcp__x__go"]},
        ]
    }
    shared = make_store(home() / "shared-memory", both)
    refused(shared, "step-s", "tool: mcp__x__go\n", "also claimed by b", system="a")


def test_a_tool_line_naming_bash_is_refused(store: Path) -> None:
    refused(store, "step-b", "tool: Bash\n", "Bash")
    assert approval_files(store) == []


def test_a_step_with_nothing_in_it_is_an_error(store: Path) -> None:
    refused(store, "step-7", "\n  \n", "nothing to approve")


@pytest.mark.parametrize(
    "approval_id",
    ["../up", "a b", "a/b", ".hidden", "", "-flag", "a\0b", "a\n", "x" * 200],
    ids=["dotdot", "space", "slash", "dot", "empty", "dash", "nul", "newline", "long"],
)
def test_an_approval_id_that_is_not_a_plain_file_name_is_refused(
    store: Path, approval_id: str
) -> None:
    start = head(store)
    with pytest.raises(CovenError, match="approval id"):
        run(approval_id)
    assert approval_files(store) == []
    assert head(store) == start


def test_a_duration_over_the_guards_cap_is_refused(store: Path) -> None:
    refused(store, "step-8", f"{ORDERS}\n", "duration", expires_in="12h1m")
    assert approval_files(store) == []


def test_a_pattern_that_backtracks_stops_the_run(monkeypatch: pytest.MonkeyPatch) -> None:
    slow_guard = {"targets": [{"system": "slow", "kind": "production", "match": ["(a+)+$"]}]}
    slow = make_store(home() / "slow-memory", slow_guard)
    monkeypatch.setattr(approve, "PATTERN_DEADLINE", 0.3)
    handler = signal.getsignal(signal.SIGALRM)
    refused(slow, "step-9", "a" * 60 + "b\n", "timed out")
    assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)
    assert signal.getsignal(signal.SIGALRM) == handler
    assert approval_files(slow) == []
    assert (slow / "approvals/step-9.step").is_file()


def test_the_pattern_deadline_is_five_seconds() -> None:
    assert approve.PATTERN_DEADLINE == 5.0


# --- refusals before anything is written ---


@pytest.mark.parametrize("value", ["pm", "", "anything"], ids=["pm", "empty", "other"])
def test_a_session_with_a_role_may_not_approve(
    store: Path, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv(ROLE, value)
    start = head(store)
    refused(store, "step-10", f"{ORDERS}\n", "only the general manager")
    assert approval_files(store) == []
    assert head(store) == start
    assert (store / "approvals/step-10.step").is_file()


def test_a_session_with_a_role_may_not_revoke(store: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    approved(store, "step-r0", f"{ORDERS}\n")
    monkeypatch.setenv(ROLE, "")
    with pytest.raises(CovenError, match="only the general manager"):
        approve.revoke("step-r0")
    assert approval_files(store) == ["step-r0.json"]


def test_without_a_store_nothing_is_written() -> None:
    with pytest.raises(CovenError, match="memory store"):
        run("step-11")


def test_no_step_file_is_an_error(store: Path) -> None:
    start = head(store)
    with pytest.raises(CovenError, match=r"approvals/step-12\.step"):
        run("step-12")
    assert head(store) == start


def test_a_step_file_outside_approvals_is_not_read(store: Path, tmp_path: Path) -> None:
    (store / "step-13.step").write_text(f"{ORDERS}\n")
    (tmp_path / "approvals").mkdir()
    (tmp_path / "approvals/step-13.step").write_text(f"{ORDERS}\n")
    with pytest.raises(CovenError, match=r"approvals/step-13\.step"):
        run("step-13")
    assert approval_files(store) == []


def test_a_symlinked_step_file_is_not_read(store: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text(f"{ORDERS}\n")
    (store / "approvals").mkdir()
    (store / "approvals/step-14.step").symlink_to(outside)
    with pytest.raises(CovenError, match="regular file"):
        run("step-14")
    assert approval_files(store) == []


@pytest.mark.parametrize(
    "extra",
    ["notes.txt", "approvals/other.step", "systems/new.md"],
    ids=["top-level", "second-step-file", "in-systems"],
)
def test_any_other_untracked_file_stops_the_run_and_keeps_the_step(store: Path, extra: str) -> None:
    (store / extra).parent.mkdir(exist_ok=True)
    (store / extra).write_text("not mine")
    start = head(store)
    write_step(store, "step-15", f"{ORDERS}\n")
    with pytest.raises(CovenError, match="nothing but"):
        run("step-15")
    assert approval_files(store) == []
    assert (store / "approvals/step-15.step").is_file()
    assert head(store) == start


def test_a_modified_tracked_file_stops_the_run(store: Path) -> None:
    (store / "systems/guard.json").write_text(json.dumps({"targets": []}))
    start = head(store)
    refused(store, "step-16", f"{ORDERS}\n", "nothing but")
    assert approval_files(store) == []
    assert head(store) == start


def test_a_staged_change_stops_the_run(store: Path) -> None:
    (store / "notes.txt").write_text("staged")
    git(store, "add", "notes.txt")
    start = head(store)
    refused(store, "step-16b", f"{ORDERS}\n", "nothing but")
    assert git(store, "diff", "--cached", "--name-only").split() == ["notes.txt"]
    assert head(store) == start


def test_a_step_file_that_is_already_tracked_is_refused_and_nothing_is_touched(
    store: Path,
) -> None:
    write_step(store, "step-16c", "ls\n")
    git(store, "add", "-A")
    git(store, "commit", "-q", "-m", "keep a step")
    write_step(store, "step-16c", f"{ORDERS}\n")
    start = head(store)
    with pytest.raises(CovenError, match="history") as caught:
        run("step-16c")
    assert "approvals/step-16c.step" in str(caught.value)
    assert approval_files(store) == []
    assert git(store, "status", "--porcelain") == " M approvals/step-16c.step\n"
    assert head(store) == start


def test_a_target_that_lists_the_bash_tool_cannot_be_approved_by_command(store: Path) -> None:
    """The guard would judge every Bash call through that target's `tools`, which a command in an
    approval file does not answer: approving would print `approved` and the guard would still refuse."""
    shell = {"system": "shell", "kind": "gated", "match": [], "tools": ["Bash"]}
    shared = make_store(home() / "shell-memory", {"targets": [*GUARD["targets"], shell]})
    start = head(shared)
    message = refused(shared, "step-sh", f"{ORDERS}\n", "shell")
    assert "Bash" in message
    assert approval_files(shared) == []
    assert head(shared) == start


ANY_ORDERS_HOST = {
    "targets": [{"system": "orders-db", "kind": "production", "match": ["psql\\s+.*orders-db"]}]
}
HOST_UNREAD = 'psql -h "$ORDERS" -c "select 1" orders-db.example.internal'
HEREDOC = "psql -h orders-db.example.internal <<SQL\nselect 1;\nSQL"


@pytest.mark.parametrize(
    ("text", "named"),
    [
        (f"{HOST_UNREAD}\n", HOST_UNREAD),
        (f"```sh\n{HEREDOC}\n```\n", "<<SQL"),
        (f"{ORDERS}\n{HOST_UNREAD}\n", HOST_UNREAD),
    ],
    ids=["host-not-written-out", "unquoted-heredoc", "second-command"],
)
def test_a_command_the_guard_refuses_a_pm_cannot_be_approved(text: str, named: str) -> None:
    """With a guard on the PM runs every step, so an approval for a command that trips a restricted
    rule would print `approved` and the hook would still refuse the PM."""
    shared = make_store(home() / "any-memory", ANY_ORDERS_HOST)
    start = head(shared)
    message = refused(shared, "step-pm", text, "a PM cannot run this")
    assert named in message
    assert "ask the PM for a form the guard can read" in message
    assert approval_files(shared) == []
    assert head(shared) == start
    assert git(shared, "status", "--porcelain", "--untracked-files=all") == step_only(
        shared, "step-pm"
    )


def test_a_refusal_for_a_pm_names_the_rule_the_guard_gave() -> None:
    shared = make_store(home() / "any-memory", ANY_ORDERS_HOST)
    message = refused(shared, "step-rule", f"{HOST_UNREAD}\n", "a PM cannot run this")
    assert "host" in message
    assert "not written out" in message


def test_an_approved_command_is_allowed_for_a_pm_by_the_real_guard(
    store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ROLE, "pm")
    assert guard_exit(bash(ORDERS)) == 2
    monkeypatch.delenv(ROLE)
    approved(store, "e2e-pm", f"{ORDERS}\n", now=live())
    monkeypatch.setenv(ROLE, "pm")
    assert guard_exit(bash(ORDERS)) == 0


def test_an_existing_approval_is_never_overwritten(store: Path) -> None:
    approved(store, "step-17", f"{ORDERS}\n")
    first = committed(store, "step-17.json")
    start = head(store)
    refused(store, "step-17", f"{ORDERS}\n", "exists already", expires_in="2h")
    assert committed(store, "step-17.json") == first
    assert head(store) == start


def test_one_existing_file_stops_a_multi_system_approval_before_any_write(store: Path) -> None:
    approved(store, "step-18", f"{ORDERS_AND_AUDIT}\n")
    git(store, "rm", "-q", "approvals/step-18-audit-db.json")
    git(store, "commit", "-q", "-m", "drop one")
    start = head(store)
    refused(store, "step-18", f"{ORDERS_AND_AUDIT}\n", r"step-18-orders-db\.json exists already")
    assert approval_files(store) == ["step-18-orders-db.json"]  # the audit file was never written
    assert head(store) == start


def test_a_store_with_no_committed_guard_json_has_nothing_to_approve_against() -> None:
    bare = home() / "bare-memory"
    memory_store.init_store(bare, None)
    configure(bare)
    refused(bare, "step-ng", f"{ORDERS}\n", "no committed systems/guard.json")
    assert approval_files(bare) == []


def test_a_guard_json_that_is_invalid_at_head_with_no_earlier_version_is_an_error() -> None:
    broken = make_store(home() / "broken-memory", {"targets": [{"system": "x", "kind": "prod"}]})
    refused(broken, "step-bg", f"{ORDERS}\n", "guard.json")
    assert approval_files(broken) == []


def test_an_invalid_guard_json_at_head_falls_back_to_the_last_valid_one_with_a_warning(
    store: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (store / "systems/guard.json").write_text("{nope")
    git(store, "add", "-A")
    git(store, "commit", "-q", "-m", "break the guard")
    approved(store, "step-fb", f"{ORDERS}\n")
    assert committed(store, "step-fb.json")["system"] == "orders-db"
    assert "invalid" in capsys.readouterr().err


def test_a_system_name_that_is_not_a_file_name_cannot_be_one_of_several() -> None:
    odd = {
        "targets": [
            {"system": "orders db", "kind": "production", "match": ["psql"]},
            {"system": "audit-db", "kind": "production", "match": ["psql"]},
        ]
    }
    odd_store = make_store(home() / "odd-memory", odd)
    refused(odd_store, "step-o", "psql -c 1\n", "approve it alone with --system")
    assert approval_files(odd_store) == []


# --- the commit ---


def test_the_commit_holds_only_the_new_files_and_the_step_file_goes(store: Path) -> None:
    start = head(store)
    approved(store, "step-19", f"{ORDERS_AND_AUDIT}\n")
    assert git(store, "rev-list", "--count", f"{start}..HEAD").strip() == "1"
    assert git(store, "log", "-1", "--format=%s").strip() == "approve step-19: audit-db, orders-db"
    changed = git(store, "diff", "--name-status", start, "HEAD").splitlines()
    assert changed == ["A\tapprovals/step-19-audit-db.json", "A\tapprovals/step-19-orders-db.json"]
    assert not (store / "approvals/step-19.step").exists()
    assert git(store, "status", "--porcelain") == ""


def test_a_change_staged_meanwhile_stays_out_of_the_commit(
    store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Whatever else is in the index when git commits, only the approval files are committed."""
    original = memory_store.git

    def stage_unrelated_first(path: Path, *args: str, **kwargs: Any) -> Any:
        if args[0] == "add":
            (store / "notes.txt").write_text("not an approval")
            original(path, "add", "--", "notes.txt")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(memory_store, "git", stage_unrelated_first)
    start = head(store)
    write_step(store, "step-20", f"{ORDERS}\n")
    run("step-20")
    assert git(store, "diff", "--name-only", start, "HEAD").split() == ["approvals/step-20.json"]
    assert git(store, "diff", "--cached", "--name-only").split() == ["notes.txt"]


def test_a_refused_commit_leaves_no_json_file_a_clean_store_and_the_step(store: Path) -> None:
    secret = f"psql -h orders-db.example.internal -c \"select '{TOKEN}'\""
    start = head(store)
    refused(store, "step-21", f"{secret}\n", "secret")
    assert approval_files(store) == []
    assert git(store, "status", "--porcelain", "--untracked-files=all") == step_only(
        store, "step-21"
    )
    assert head(store) == start


def test_a_commit_that_fails_for_another_reason_rolls_back_too(
    store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (store / ".git/hooks/pre-commit").write_text("#!/bin/sh\necho 'no commits today' >&2\nexit 1\n")
    start = head(store)
    message = refused(store, "step-21b", f"{ORDERS}\n", "no commits today")
    assert "git commit failed" in message
    assert approval_files(store) == []
    assert git(store, "status", "--porcelain", "--untracked-files=all") == step_only(
        store, "step-21b"
    )
    assert head(store) == start


def commit_times_out(monkeypatch: pytest.MonkeyPatch, *, lands: bool) -> None:
    original = memory_store.git

    def fake(path: Path, *args: str, **kwargs: Any) -> Any:
        if args[0] == "commit":
            if lands:
                original(path, *args, **kwargs)
            raise CovenError("git commit took longer than 60 seconds")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(memory_store, "git", fake)


def test_a_commit_that_timed_out_but_landed_is_a_success(
    store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    commit_times_out(monkeypatch, lands=True)
    lines = approved(store, "step-22", f"{ORDERS}\n")
    assert lines[0].startswith("approved step-22.json system=orders-db")
    assert committed(store, "step-22.json")["commands"] == [ORDERS]
    assert not (store / "approvals/step-22.step").exists()
    assert git(store, "status", "--porcelain") == ""


def test_a_commit_that_timed_out_and_did_not_land_leaves_no_json_file(
    store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    commit_times_out(monkeypatch, lands=False)
    start = head(store)
    refused(store, "step-23", f"{ORDERS}\n", "took longer")
    assert approval_files(store) == []
    assert (store / "approvals/step-23.step").is_file()
    assert git(store, "status", "--porcelain", "--untracked-files=all") == step_only(
        store, "step-23"
    )
    assert head(store) == start


def test_when_it_cannot_tell_whether_the_commit_landed_it_deletes_nothing(
    store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = memory_store.git
    heads = {"read": 0}

    def fake(path: Path, *args: str, **kwargs: Any) -> Any:
        if args[0] == "rev-parse":  # the first read is the run's own, before the commit
            heads["read"] += 1
            if heads["read"] > 1:
                raise CovenError("git rev-parse took longer than 60 seconds")
        if args[0] == "commit":
            raise CovenError("git commit took longer than 60 seconds")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(memory_store, "git", fake)
    refused(store, "step-24", f"{ORDERS}\n", "cannot tell")
    assert (store / "approvals/step-24.json").is_file()
    assert (store / "approvals/step-24.step").is_file()


def test_approve_never_pushes_or_fetches(store: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    git(store, "remote", "add", "origin", "https://example.invalid/memory.git")
    seen: list[str] = []
    original = memory_store.git

    def recording(path: Path, *args: str, **kwargs: Any) -> Any:
        seen.append(args[0])
        return original(path, *args, **kwargs)

    monkeypatch.setattr(memory_store, "git", recording)
    approved(store, "step-np", f"{ORDERS}\n")
    revoked("step-np")
    assert seen
    assert not {"push", "fetch", "pull", "remote", "ls-remote"} & set(seen)


# --- expired approvals ---


def write_approval(store: Path, name: str, expires: datetime | str, **fields: Any) -> None:
    record = {
        "id": name.removesuffix(".json"),
        "system": "orders-db",
        "commands": [ORDERS],
        "tools": [],
        "expires": expires if isinstance(expires, str) else expires.isoformat(),
        **fields,
    }
    (store / "approvals").mkdir(exist_ok=True)
    (store / "approvals" / name).write_text(json.dumps(record))


def commit_approvals(store: Path) -> None:
    git(store, "add", "-A")
    git(store, "commit", "-q", "-m", "earlier approvals")


def test_expired_approval_files_go_in_the_same_commit(store: Path) -> None:
    write_approval(store, "old-1.json", NOW - timedelta(minutes=1))
    write_approval(store, "old-2-orders-db.json", NOW - timedelta(days=3))
    write_approval(store, "fresh.json", NOW + timedelta(hours=1))
    commit_approvals(store)
    start = head(store)
    lines = approved(store, "step-30", f"{ORDERS}\n")
    assert git(store, "rev-list", "--count", f"{start}..HEAD").strip() == "1"
    changed = sorted(git(store, "diff", "--name-status", start, "HEAD").splitlines())
    assert changed == [
        "A\tapprovals/step-30.json",
        "D\tapprovals/old-1.json",
        "D\tapprovals/old-2-orders-db.json",
    ]
    assert approval_files(store) == ["fresh.json", "step-30.json"]
    assert "pruned old-1.json" in lines
    assert "pruned old-2-orders-db.json" in lines


def test_an_approval_expiring_exactly_now_is_pruned_by_the_same_clock(store: Path) -> None:
    write_approval(store, "edge.json", NOW)
    commit_approvals(store)
    approved(store, "step-31", f"{ORDERS}\n")
    assert approval_files(store) == ["step-31.json"]


def test_files_the_guard_cannot_read_are_left_alone(store: Path) -> None:
    (store / "approvals").mkdir(exist_ok=True)
    (store / "approvals/notes.json").write_text("not json")
    write_approval(store, "naive.json", "2020-01-01T00:00:00")  # no offset
    commit_approvals(store)
    approved(store, "step-32", f"{ORDERS}\n")
    assert approval_files(store) == ["naive.json", "notes.json", "step-32.json"]


def test_a_failed_approval_restores_the_expired_files_too(store: Path) -> None:
    write_approval(store, "old-3.json", NOW - timedelta(hours=1))
    commit_approvals(store)
    start = head(store)
    secret = f"psql -h orders-db.example.internal -c \"select '{TOKEN}'\""
    refused(store, "step-33", f"{secret}\n", "secret")
    assert approval_files(store) == ["old-3.json"]
    assert git(store, "status", "--porcelain", "--untracked-files=all") == step_only(
        store, "step-33"
    )
    assert head(store) == start


# --- revoke ---


def test_revoke_removes_the_ids_files_in_one_commit(store: Path) -> None:
    approved(store, "step-40", f"{ORDERS_AND_AUDIT}\n")
    approved(store, "other-40", f"{ORDERS}\n")
    start = head(store)
    lines = revoked("step-40")
    assert git(store, "rev-list", "--count", f"{start}..HEAD").strip() == "1"
    assert git(store, "log", "-1", "--format=%s").strip() == "revoke step-40"
    assert approval_files(store) == ["other-40.json"]
    assert lines == ["revoked step-40-audit-db.json", "revoked step-40-orders-db.json"]
    assert git(store, "status", "--porcelain") == ""


def test_revoke_of_one_file_id_removes_that_file(store: Path) -> None:
    approved(store, "step-41", f"{ORDERS}\n")
    assert revoked("step-41") == ["revoked step-41.json"]
    assert approval_files(store) == []


def test_revoke_leaves_another_id_that_starts_with_the_same_name(store: Path) -> None:
    approved(store, "step-42", f"{ORDERS_AND_AUDIT}\n")
    approved(store, "step-42-extra", f"{ORDERS}\n")
    revoked("step-42")
    assert approval_files(store) == ["step-42-extra.json"]


def test_revoke_leaves_a_file_named_for_the_id_that_holds_another_id(store: Path) -> None:
    write_approval(store, "step-47.json", NOW + timedelta(hours=1), id="someone-else")
    commit_approvals(store)
    start = head(store)
    with pytest.raises(CovenError, match="no approval"):
        approve.revoke("step-47")
    assert approval_files(store) == ["step-47.json"]
    assert head(store) == start


def test_revoke_of_an_unknown_id_is_refused_and_changes_nothing(store: Path) -> None:
    approved(store, "step-43", f"{ORDERS}\n")
    start = head(store)
    with pytest.raises(CovenError, match="no approval"):
        approve.revoke("nope")
    assert head(store) == start
    assert approval_files(store) == ["step-43.json"]


def test_revoke_needs_no_step_file(store: Path) -> None:
    approved(store, "step-44", f"{ORDERS}\n")
    assert not (store / "approvals/step-44.step").exists()
    revoked("step-44")
    assert approval_files(store) == []


@pytest.mark.parametrize("approval_id", ["../up", "a/b", "", "-x"])
def test_revoke_checks_the_id_like_approve(store: Path, approval_id: str) -> None:
    with pytest.raises(CovenError, match="approval id"):
        approve.revoke(approval_id)


def test_revoke_refuses_when_the_store_has_other_uncommitted_changes(store: Path) -> None:
    approved(store, "step-45", f"{ORDERS}\n")
    (store / "notes.txt").write_text("not mine")
    start = head(store)
    with pytest.raises(CovenError, match="no uncommitted changes"):
        approve.revoke("step-45")
    assert head(store) == start
    assert approval_files(store) == ["step-45.json"]


def test_a_refused_revoke_commit_puts_the_files_back(store: Path) -> None:
    approved(store, "step-46", f"{ORDERS}\n")
    (store / ".git/hooks/pre-commit").write_text("#!/bin/sh\necho 'no commits today' >&2\nexit 1\n")
    start = head(store)
    with pytest.raises(CovenError, match="no commits today"):
        approve.revoke("step-46")
    assert approval_files(store) == ["step-46.json"]
    assert git(store, "status", "--porcelain") == ""
    assert head(store) == start


# --- the command line ---


def test_the_command_reads_the_step_file_and_prints_what_it_approved(
    store: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_step(store, "step-25", f"{ORDERS}\n")
    assert cli.main(["approve", "step-25", "--expires-in", "30m"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("approved step-25.json system=orders-db expires=")
    assert out[1:] == [f"  command: {json.dumps(ORDERS)}"]
    assert committed(store, "step-25.json")["commands"] == [ORDERS]
    assert git(store, "status", "--porcelain", "--untracked-files=all") == ""
    expires = datetime.fromisoformat(committed(store, "step-25.json")["expires"])
    assert expires.utcoffset() is not None
    assert timedelta(minutes=29) < expires - datetime.now(UTC) <= timedelta(minutes=30)


def test_non_ascii_characters_are_printed_as_escapes_so_none_hides(store: Path) -> None:
    sneaky = "psql -h orders-db.example.internal -c 'select 1' \u202e\u200b"
    lines = approved(store, "step-uni", f"{sneaky}\n")
    assert (
        lines[1] == "  command: \"psql -h orders-db.example.internal -c 'select 1' \\u202e\\u200b\""
    )
    assert all(line.isascii() for line in lines)
    assert committed(store, "step-uni.json")["commands"] == [sneaky]


def test_the_system_option_reaches_the_command(
    store: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_step(store, "step-25b", "psql -h elsewhere -c 'select 1 from audit_log'\n")
    code = cli.main(["approve", "step-25b", "--expires-in", "1h", "--system", "audit-db"])
    assert code == 0
    assert committed(store, "step-25b.json")["system"] == "audit-db"
    assert "system=audit-db" in capsys.readouterr().out


def test_the_revoke_option_removes_the_files(
    store: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_step(store, "step-26b", f"{ORDERS}\n")
    cli.main(["approve", "step-26b", "--expires-in", "1h"])
    capsys.readouterr()
    assert cli.main(["approve", "--revoke", "step-26b"]) == 0
    assert capsys.readouterr().out == "revoked step-26b.json\n"
    assert approval_files(store) == []
    assert git(store, "status", "--porcelain", "--untracked-files=all") == ""


def test_a_refusal_is_one_error_line_and_exit_1(
    store: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["approve", "step-26", "--expires-in", "30m"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("coven: ")
    assert len(captured.err.splitlines()) == 1


def test_a_role_is_refused_through_the_command_line(
    store: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    write_step(store, "step-26c", f"{ORDERS}\n")
    monkeypatch.setenv(ROLE, "")
    assert cli.main(["approve", "step-26c", "--expires-in", "30m"]) == 1
    assert "only the general manager" in capsys.readouterr().err
    assert approval_files(store) == []


@pytest.mark.parametrize(
    "argv",
    [
        ["approve"],
        ["approve", "step-x"],
        ["approve", "--expires-in", "1h"],
        ["approve", "--revoke", "step-x", "--expires-in", "1h"],
        ["approve", "--revoke", "step-x", "--system", "orders-db"],
        ["approve", "step-x", "--revoke", "step-y"],
    ],
    ids=["nothing", "no-duration", "no-id", "revoke-duration", "revoke-system", "two-ids"],
)
def test_the_options_that_do_not_go_together_are_refused(
    store: Path, capsys: pytest.CaptureFixture[str], argv: list[str]
) -> None:
    assert cli.main(argv) == 1
    assert capsys.readouterr().err.startswith("coven: ")


@pytest.mark.parametrize("option", [["--commands", "x"], ["--tool", "t"]])
def test_commands_and_tools_cannot_come_from_the_command_line(
    store: Path, option: list[str]
) -> None:
    assert cli.main(["approve", "step-27", "--expires-in", "30m", *option]) != 0
    assert approval_files(store) == []


def test_the_command_is_listed(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--help"]) == 0
    assert "approve" in capsys.readouterr().out


# --- what the real production guard does with the written files ---


def test_the_guard_allows_exactly_the_approved_command_until_it_is_revoked(store: Path) -> None:
    other = "psql -h orders-db.example.internal -c 'drop table t'"
    assert guard_exit(bash(ORDERS)) == 2  # refused before approval, or this proves nothing
    approved(store, "e2e-1", f"{ORDERS}\n", now=live())
    assert guard_exit(bash(ORDERS)) == 0
    assert guard_exit(bash(other)) == 2
    assert guard_exit(bash(f"{ORDERS} && {other}")) == 2
    assert guard_exit(bash("ls -la")) == 0  # not guarded at all
    revoked("e2e-1")
    assert guard_exit(bash(ORDERS)) == 2


def test_a_fenced_command_stays_one_command_for_the_guard(store: Path) -> None:
    first = "psql -h orders-db.example.internal -c 'update t set a = 1"
    full = f"{first}\nwhere id = 7;'"
    assert guard_exit(bash(full)) == 2
    approved(store, "e2e-2", f"```sh\n{full}\n```\n", now=live())
    assert committed(store, "e2e-2.json")["commands"] == [full]
    assert guard_exit(bash(full)) == 0
    assert guard_exit(bash(f"{full}\n")) == 0  # the guard strips; so does the file
    assert guard_exit(bash(first)) == 2  # its first line alone is another command
    assert guard_exit(bash(f"{full}\nwhere id = 8;")) == 2


def test_a_crlf_step_file_with_trailing_blanks_approves_what_an_lf_one_does(store: Path) -> None:
    full = "psql -h orders-db.example.internal -c 'update t set a = 1\nwhere id = 7;'"
    text = f"```\n{full}\n```\n\n   \n{ORDERS}   \n\n".replace("\n", "\r\n")
    approved(store, "e2e-crlf", text, now=live())
    assert committed(store, "e2e-crlf.json")["commands"] == [full, ORDERS]
    assert guard_exit(bash(full)) == 0
    assert guard_exit(bash(ORDERS)) == 0


def test_a_two_system_approval_makes_the_guard_allow_the_shared_command(store: Path) -> None:
    assert guard_exit(bash(ORDERS_AND_AUDIT)) == 2
    approved(store, "e2e-3", f"{ORDERS_AND_AUDIT}\n", now=live())
    assert guard_exit(bash(ORDERS_AND_AUDIT)) == 0  # it needs both files
    assert guard_exit(bash(ORDERS)) == 2  # orders-db alone is not what was approved


def test_an_approved_tool_is_allowed_and_another_tool_is_not(store: Path) -> None:
    call: dict[str, object] = {"tool_name": "mcp__orders__write", "tool_input": {}, "cwd": "/tmp"}
    assert guard_exit(call) == 2
    approved(store, "e2e-4", "tool: mcp__orders__write\n", now=live())
    assert guard_exit(call) == 0
    assert guard_exit({**call, "tool_name": "mcp__mail__send"}) == 2


def test_an_approval_longer_than_the_guards_cap_would_be_ignored_so_none_is_written(
    store: Path,
) -> None:
    refused(store, "e2e-5", f"{ORDERS}\n", "duration", expires_in="13h")
    assert guard_exit(bash(ORDERS)) == 2


# --- the guard module approve depends on ---


@pytest.mark.parametrize(
    "source",
    [
        "def broken(:\n",
        "import no_such_module_anywhere\n",
        "raise SystemExit(3)\n",
        "raise RuntimeError('boom')\n",
    ],
    ids=["syntax", "import", "exit", "runtime"],
)
def test_a_guard_that_cannot_load_is_an_error_not_a_traceback(
    store: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: str
) -> None:
    broken = tmp_path / "prod_guard.py"
    broken.write_text(source)
    monkeypatch.setattr(guard, "HOOK", broken)
    guard.load_guard.cache_clear()
    try:
        message = refused(store, "step-g", f"{ORDERS}\n", "production guard")
    finally:
        guard.load_guard.cache_clear()
    assert str(broken) in message
    assert approval_files(store) == []
