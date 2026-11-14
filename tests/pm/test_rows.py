"""Reading the CLI's agents rows and checking whether a session's process is alive."""

import json
import subprocess
from datetime import UTC, datetime

import pytest
from pm.fixtures import BYTES_CLI, SLEEP_CLI, fake_claude

from coven.errors import CovenError
from coven.pm import rows
from coven.pm.rows import PmError, load_rows, parse_rows, pid_alive, pm_groups, row_state

# The fields `claude agents --json --all` prints for a row, plus one a newer release may add.
SAMPLE = [
    {
        "cwd": "/work/repo-1",
        "id": "0a1b2c3d",
        "kind": "background",
        "name": "pm-acme-1",
        "pid": 4242,
        "sessionId": "00000000-0000-4000-8000-000000000001",
        "startedAt": 1790000000000,
        "state": "idle",
        "status": "idle",
        "waitingFor": None,
        "somethingNew": {"a": [1, 2]},
    },
    {
        "cwd": "/work/repo-2",
        "id": "1b2c3d4e",
        "kind": "background",
        "name": "pm-acme-2",
        "pid": 4243,
        "sessionId": "00000000-0000-4000-8000-000000000002",
        "startedAt": 1790000060000,
        "state": "blocked",
        "status": "busy",
        "waitingFor": "a permission",
    },
    {"cwd": "/work", "kind": "interactive", "name": "gm-main", "pid": 4200, "status": "busy"},
]


def _sample() -> str:
    return json.dumps(SAMPLE)


def test_sample_rows_parse():
    rows = parse_rows(_sample())
    assert [r.name for r in rows] == ["pm-acme-1", "pm-acme-2", "gm-main"]
    first = rows[0]
    assert (first.id, first.session_id, first.cwd, first.pid) == (
        "0a1b2c3d",
        "00000000-0000-4000-8000-000000000001",
        "/work/repo-1",
        4242,
    )
    interactive = rows[2]
    assert interactive.id is None
    assert interactive.state is None
    assert row_state(interactive) == "working"


def test_started_at_is_epoch_milliseconds_utc():
    (row,) = parse_rows('[{"name": "pm-acme-1", "kind": "background", "startedAt": 1790000000000}]')
    assert row.started_at == datetime(2026, 9, 21, 14, 13, 20, tzinfo=UTC)


def test_json_after_a_warning_line_is_read():
    assert len(parse_rows('warning: something\n[{"name": "pm-acme-1"}]')) == 1


def test_object_holding_one_list_is_read():
    assert len(parse_rows('{"agents": [{"name": "pm-acme-1"}]}')) == 1


def test_object_without_exactly_one_list_is_refused():
    with pytest.raises(PmError, match="agents output is not JSON"):
        parse_rows('{"a": [], "b": []}')
    with pytest.raises(PmError, match="agents output is not JSON"):
        parse_rows('{"a": 1}')


@pytest.mark.parametrize("raw", ["", "not json at all", "[1, 2", "42"])
def test_text_that_is_not_json_is_refused(raw):
    with pytest.raises(PmError, match="agents output is not JSON"):
        parse_rows(raw)


def test_wrong_typed_fields_become_none():
    (row,) = parse_rows(
        json.dumps(
            [
                {
                    "name": "pm-acme-1",
                    "kind": 5,
                    "id": ["x"],
                    "sessionId": None,
                    "cwd": 7,
                    "state": {},
                    "status": 1.5,
                    "pid": "123",
                    "startedAt": "yesterday",
                }
            ]
        )
    )
    assert (row.kind, row.id, row.session_id, row.cwd) == (None, None, None, None)
    assert (row.state, row.status, row.pid, row.started_at) == (None, None, None, None)
    assert row.name == "pm-acme-1"


def test_booleans_are_not_numbers():
    (row,) = parse_rows('[{"name": "pm-acme-1", "pid": true, "startedAt": true}]')
    assert row.pid is None
    assert row.started_at is None


def test_agents_failure_is_named():
    with pytest.raises(PmError, match=r"^agents exited 3: boom \(see coven doctor\)$"):
        load_rows(run=lambda argv: subprocess.CompletedProcess(argv, 3, "", "boom"))


def test_agents_stderr_in_the_error_is_one_clean_line():
    def run(argv):
        return subprocess.CompletedProcess(argv, 1, "", "\x1b[31mbad\x1b[0m\nsecond")

    with pytest.raises(PmError, match=r"^agents exited 1: bad \(see coven doctor\)$"):
        load_rows(run=run)


def test_load_rows_returns_parsed_rows():
    def run(argv):
        return subprocess.CompletedProcess(argv, 0, _sample(), "")

    assert len(load_rows(run=run)) == 3


def test_load_rows_asks_for_all_rows_as_json():
    seen: list[list[str]] = []

    def run(argv):
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, "[]", "")

    load_rows(run=run)
    assert len(seen) == 1
    assert seen[0][1:] == ["agents", "--json", "--all"]


def test_missing_cli_is_named():
    def run(argv):
        raise FileNotFoundError(argv[0])

    with pytest.raises(PmError, match="^claude not found on PATH$"):
        load_rows(run=run)


def test_timeout_is_named():
    def run(argv):
        raise subprocess.TimeoutExpired(argv, 10)

    with pytest.raises(PmError, match="agents did not answer in 10 s"):
        load_rows(run=run)


def test_groups_hold_only_background_pm_rows():
    groups = pm_groups(parse_rows(_sample()))
    assert list(groups) == ["pm-acme-1", "pm-acme-2"]


def test_groups_drop_other_kinds_and_names():
    raw = json.dumps(
        [
            {"name": "pm-acme-1", "kind": "background", "startedAt": 1000},
            {"name": "pm-acme-1", "kind": "background", "startedAt": 3000},
            {"name": "pm-acme-1", "kind": "background", "startedAt": 2000},
            {"name": "pm-acme-1", "kind": "interactive", "startedAt": 9000},
            {"name": "gm-main", "kind": "background", "startedAt": 9000},
        ]
    )
    groups = pm_groups(parse_rows(raw))
    assert list(groups) == ["pm-acme-1"]
    assert [r.started_at.timestamp() for r in groups["pm-acme-1"]] == [3.0, 2.0, 1.0]


def test_groups_keep_a_row_without_a_start_time_last():
    raw = json.dumps(
        [
            {"name": "pm-acme-1", "kind": "background"},
            {"name": "pm-acme-1", "kind": "background", "startedAt": 1000},
        ]
    )
    (group,) = pm_groups(parse_rows(raw)).values()
    assert [r.started_at is None for r in group] == [False, True]


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        ({"state": "blocked", "status": "busy"}, "blocked"),
        ({"status": "busy"}, "working"),
        ({"status": "idle"}, "idle"),
        ({"status": "weird"}, "unknown"),
        ({}, "unknown"),
    ],
)
def test_row_state(row, expected):
    (parsed,) = parse_rows(json.dumps([{"name": "pm-acme-1", **row}]))
    assert row_state(parsed) == expected


def test_pid_alive_reads_cmdline(tmp_path):
    (tmp_path / "41").mkdir()
    (tmp_path / "41" / "cmdline").write_bytes(b"/usr/bin/claude\0--bg\0")
    (tmp_path / "42").mkdir()
    (tmp_path / "42" / "cmdline").write_bytes(b"/usr/bin/python3\0x\0")
    assert pid_alive(41, tmp_path)
    assert not pid_alive(42, tmp_path)
    assert not pid_alive(43, tmp_path)


@pytest.mark.parametrize(
    ("cmdline", "alive"),
    [
        (b"claude bg-spare\0", True),  # a live PM's process title
        (b"claude\0", True),
        (b"grep\0claude\0", False),
        (b"node\0/opt/bin/claude\0--bg\0", False),
        (b"/opt/claude/bin/tool\0", False),
    ],
    ids=["pm-title", "gm", "grep", "node-script", "claude-directory"],
)
def test_pid_alive_reads_only_the_basename_of_argv0(tmp_path, cmdline, alive):
    (tmp_path / "44").mkdir()
    (tmp_path / "44" / "cmdline").write_bytes(cmdline)
    assert pid_alive(44, tmp_path) is alive


def test_a_cli_that_cannot_run_is_named_without_its_path():
    def run(argv):
        raise PermissionError(13, "Permission denied", "/some/where/claude")

    with pytest.raises(PmError, match=r"cannot run claude: Permission denied$"):
        load_rows(run=run)


def test_pm_error_is_a_coven_error_that_exits_2():
    assert issubclass(PmError, CovenError)
    assert PmError("x").exit_code == 2


def test_an_unknown_field_on_a_row_is_ignored():
    (row,) = parse_rows('[{"name": "pm-acme-1", "kind": "background", "brandNew": [1, {"a": 2}]}]')
    assert row.name == "pm-acme-1"


def test_agents_failure_without_stderr_has_no_trailing_colon():
    def run(argv):
        return subprocess.CompletedProcess(argv, 4, "", "")

    with pytest.raises(PmError) as error:
        load_rows(run=run)
    assert str(error.value) == "agents exited 4 (see coven doctor)"


@pytest.mark.parametrize(
    "raw",
    [
        '[{"pid": ' + "9" * 5000 + "}]",
        "[" * 100_000 + "]" * 100_000,
    ],
    ids=["huge-integer", "deep-nesting"],
)
def test_json_that_cannot_decode_safely_is_a_named_error(raw):
    with pytest.raises(PmError, match="agents output is not JSON"):
        parse_rows(raw)


@pytest.fixture
def bin_dir(tmp_path, monkeypatch):
    folder = tmp_path / "fake-bin"
    folder.mkdir()
    monkeypatch.setenv("PATH", str(folder))
    return folder


def test_output_that_is_not_utf8_is_a_named_error_not_a_crash(bin_dir):
    fake_claude(bin_dir, BYTES_CLI, output=b"\xff[]", error=b"", code=0)
    with pytest.raises(PmError, match="agents output is not JSON"):
        load_rows()


def test_stderr_that_is_not_utf8_is_decoded_leniently(bin_dir):
    fake_claude(bin_dir, BYTES_CLI, output=b"", error=b"bad \xff byte\n", code=3)
    with pytest.raises(PmError, match="^agents exited 3: bad .* byte"):
        load_rows()


def test_a_cli_that_never_answers_is_cut_off_by_the_timeout(bin_dir, monkeypatch):
    fake_claude(bin_dir, SLEEP_CLI)
    monkeypatch.setattr(rows, "AGENTS_TIMEOUT", 0.2)
    with pytest.raises(PmError, match=r"^agents did not answer in 0.2 s$"):
        load_rows()
