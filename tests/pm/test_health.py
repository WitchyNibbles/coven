"""`coven pm health`: only what needs the GM, from the agents rows, transcripts and state records."""

import json
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import pytest
from pm.fixtures import (
    GM,
    NOW,
    PM,
    PmEnv,
    TranscriptBuilder,
    agents_row,
    at,
    pm_env,
    session_id,
)

from coven.config import Config, load_config, save_config

HEALTH = "health"
CODE = "acme-1"
OLD_GM = "gm-old"
RECORD_ID = session_id(1)


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[PmEnv]:
    yield from pm_env(tmp_path, monkeypatch)


def memory() -> Path:
    """The memory store at its default place under the test home, created empty."""
    store = load_config().memory_path
    store.mkdir(parents=True, exist_ok=True)
    return store


def record(code: str, text: str) -> Path:
    """Write the state record of project `code` into the memory store."""
    folder = memory() / "projects" / code
    folder.mkdir(parents=True, exist_ok=True)
    file = folder / f"{code}-state.md"
    file.write_text(text)
    return file


def brief(gm: str = GM) -> TranscriptBuilder:
    """A transcript that starts like a PM's: the brief, naming the GM's session."""
    return TranscriptBuilder().prompt(f"Brief for {PM}\nGM session: {gm}\n", at("09:00"))


def quiet() -> TranscriptBuilder:
    """A PM that sent nothing and ended its turn."""
    return brief().turn_end(at("09:30"))


def record_text(status: str | None = "running", session: str | None = RECORD_ID) -> str:
    lines = ["---", f"name: {CODE}-state", "metadata:", "  type: project", "---", "# State"]
    if session is not None:
        lines.append(f"PM: {PM}, id 00000001, sessionId {session}")
    if status is not None:
        lines.append(f"Project status: {status}")
    return "\n".join(lines) + "\n"


def one_pm(env: PmEnv, builder: TranscriptBuilder, state: str = "idle", **fields: object) -> None:
    env.set_rows(agents_row(PM, 1, state, **fields))
    env.transcript(builder)


def health(
    env: PmEnv, capsys: pytest.CaptureFixture[str], *argv: str
) -> tuple[int, list[str], str]:
    code, out, err = env.run(capsys, HEALTH, "--gm", GM, *argv)
    return code, out.splitlines(), err


def test_nothing_to_report_prints_ok(env: PmEnv, capsys: pytest.CaptureFixture[str]) -> None:
    record(CODE, record_text("idle"))
    one_pm(env, quiet())
    assert health(env, capsys) == (0, ["ok"], "")


def test_no_pm_and_no_record_prints_ok(env: PmEnv, capsys: pytest.CaptureFixture[str]) -> None:
    memory()
    assert health(env, capsys) == (0, ["ok"], "")


@pytest.mark.parametrize("state", ["stopped", "failed"])
def test_running_record_with_a_dead_pm_says_to_resume(
    env: PmEnv, capsys: pytest.CaptureFixture[str], state: str
) -> None:
    record(CODE, record_text("running"))
    one_pm(env, quiet(), state)
    code, lines, err = health(env, capsys)
    assert (code, err) == (1, "")
    assert lines == [f"{PM}: record says running, PM {state}: coven pm resume {CODE} --gm {GM}"]


def test_running_record_with_a_stale_pm_says_to_resume(
    env: PmEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    record(CODE, record_text("running"))
    one_pm(env, quiet(), "working", pid=4242)  # no such process in the fake /proc
    code, lines, _ = health(env, capsys)
    assert code == 1
    assert lines == [f"{PM}: record says running, PM stale: coven pm resume {CODE} --gm {GM}"]


def old_quiet() -> TranscriptBuilder:
    """A PM whose transcript has not moved for more than a day."""
    old = NOW - timedelta(hours=26)
    return TranscriptBuilder().prompt(f"Brief for {PM}\nGM session: {GM}\n", old).turn_end(old)


def test_a_stale_pm_without_a_pid_is_stopped_before_the_resume(
    env: PmEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    record(CODE, record_text("running"))
    one_pm(env, old_quiet(), "working")  # no pid: resume would take the row as running
    code, lines, _ = health(env, capsys)
    assert code == 1
    assert lines == [
        f"{PM}: record says running, PM stale: "
        f"claude stop 00000001, then coven pm resume {CODE} --gm {GM}"
    ]


def test_a_stale_pm_without_a_pid_and_a_bad_id_gets_no_command(
    env: PmEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    record(CODE, record_text("running"))
    one_pm(env, old_quiet(), "working", id="a;touch x")
    _, lines, _ = health(env, capsys)
    assert lines == [f"{PM}: record says running, PM stale"]


def test_running_record_with_no_row_names_the_session(
    env: PmEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    record(CODE, record_text("running"))
    code, lines, _ = health(env, capsys)
    assert code == 1
    assert lines == [
        f"{PM}: record says running, PM gone: "
        f"coven pm resume {CODE} --gm {GM} --session {RECORD_ID}"
    ]


def test_running_record_with_no_row_and_no_session_id_says_to_launch(
    env: PmEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    record(CODE, record_text("running", session=None))
    code, lines, _ = health(env, capsys)
    assert code == 1
    assert lines == [
        f"{PM}: record says running, PM gone: "
        "no sessionId in the record: launch a new PM from the record"
    ]


def test_the_record_session_id_may_carry_a_colon(
    env: PmEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    record(CODE, f"Project status: running\nsessionId: {RECORD_ID}\n")
    _, lines, _ = health(env, capsys)
    assert lines[0].endswith(f"--session {RECORD_ID}")


def test_idle_record_with_gone_pm_is_quiet(env: PmEnv, capsys: pytest.CaptureFixture[str]) -> None:
    record(CODE, record_text("idle"))
    assert health(env, capsys) == (0, ["ok"], "")


@pytest.mark.parametrize("status", ["done", "waiting for a slot"])
def test_other_record_statuses_with_a_dead_pm_are_quiet(
    env: PmEnv, capsys: pytest.CaptureFixture[str], status: str
) -> None:
    record(CODE, record_text(status))
    one_pm(env, quiet(), "stopped")
    assert health(env, capsys) == (0, ["ok"], "")


def test_a_dead_pm_without_a_record_is_quiet(
    env: PmEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    memory()
    one_pm(env, quiet(), "stopped")
    assert health(env, capsys) == (0, ["ok"], "")


def test_a_live_pm_without_a_record_is_still_checked(
    env: PmEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    memory()
    one_pm(env, quiet(), "blocked")
    code, lines, _ = health(env, capsys)
    assert code == 1
    assert lines == [f"{PM}: waits on a prompt: claude attach 00000001"]


def test_two_live_sessions_are_a_duplicate(env: PmEnv, capsys: pytest.CaptureFixture[str]) -> None:
    record(CODE, record_text("running"))
    env.set_rows(agents_row(PM, 1, "idle"), agents_row(PM, 2, "working"))
    env.transcript(quiet(), 2)
    code, lines, _ = health(env, capsys)
    assert code == 1
    assert lines == [f"{PM}: duplicate sessions 00000002, 00000001: stop all but the newest"]


def test_blocked_on_a_prompt_is_not_waiting_on_gm(
    env: PmEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    record(CODE, record_text("running"))
    one_pm(env, quiet(), "blocked")
    code, lines, _ = health(env, capsys)
    assert code == 1
    assert lines == [f"{PM}: waits on a prompt: claude attach 00000001"]


def test_delivered_blocked_without_reply_waits_on_gm(
    env: PmEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    record(CODE, record_text("running"))
    one_pm(
        env,
        brief()
        .peer(GM, "ANSWER acme-1/9: earlier", at("09:30"))
        .send(GM, "BLOCKED acme-1/1: need access", at("10:02"))
        .turn_end(at("10:03")),
    )
    code, lines, _ = health(env, capsys)
    assert code == 1
    assert lines == [f"{PM}: waits on the GM: BLOCKED acme-1/1 since 10:02"]


def test_an_answered_wait_is_quiet(env: PmEnv, capsys: pytest.CaptureFixture[str]) -> None:
    record(CODE, record_text("running"))
    one_pm(
        env,
        brief()
        .peer(GM, "ANSWER acme-1/9: earlier", at("09:30"))
        .send(GM, "QUESTION acme-1/1: which one?", at("10:02"))
        .peer(GM, "ANSWER acme-1/1: this one", at("10:30"))
        .turn_end(at("10:31")),
    )
    assert health(env, capsys) == (0, ["ok"], "")


def test_every_open_wait_is_listed_oldest_first(
    env: PmEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    one_pm(
        env,
        brief()
        .peer(GM, "ANSWER acme-1/9: earlier", at("09:30"))
        .send(GM, "QUESTION acme-1/1: first?", at("10:02"))
        .send(GM, "PROD-STEP acme-1/2: run it", at("10:05"))
        .turn_end(at("10:06")),
    )
    memory()
    _, lines, _ = health(env, capsys)
    assert lines == [
        f"{PM}: waits on the GM: QUESTION acme-1/1 since 10:02",
        f"{PM}: waits on the GM: PROD-STEP acme-1/2 since 10:05",
    ]


def test_a_reply_from_an_older_gm_name_naming_several_tasks_clears_the_waits(
    env: PmEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    memory()
    one_pm(
        env,
        brief()
        .send(OLD_GM, "PROD-STEP acme-1/7x-A1b: run it", at("10:02"))
        .send(OLD_GM, "QUESTION acme-1/7x-A2: which?", at("10:03"))
        .peer(OLD_GM, "APPROVED acme-1/7x-A1 and 7x-A1b 2026-10-30-id: go", at("10:30"))
        .turn_end(at("10:31")),
    )
    _, lines, _ = health(env, capsys)
    assert lines == [f"{PM}: waits on the GM: QUESTION acme-1/7x-A2 since 10:03"]


def test_an_ask_without_a_usable_key_is_not_a_wait_on_the_gm(
    env: PmEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    memory()
    one_pm(env, brief().send(GM, "DRAFT", at("10:02")).turn_end(at("10:03")))
    assert health(env, capsys) == (0, ["ok"], "")


def test_a_draft_sent_right_after_its_done_is_not_a_wait_on_the_gm(
    env: PmEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    memory()
    one_pm(
        env,
        brief()
        .send(GM, "DONE acme-1/8: finished", at("10:02"))
        .send(GM, "DRAFT acme-1/8: text for the user\nDONE acme-1/9: also", at("10:03"))
        .turn_end(at("10:04")),
    )
    assert health(env, capsys) == (0, ["ok"], "")


# --- a project the GM closed, a session that is gone --------------------------------------------


def old_waiting() -> TranscriptBuilder:
    """A PM that asked and then went quiet for more than a day."""
    old = NOW - timedelta(hours=26)
    return old_quiet().send(GM, "QUESTION acme-1/1: which?", old).turn_end(old)


def gone_line(row_id: str = "00000001") -> str:
    return f"{PM}: row says working, session gone: claude stop {row_id}"


@pytest.mark.parametrize("status", ["done", "waiting for a slot"])
def test_a_working_row_with_no_session_is_told_to_stop_when_the_record_does_not_expect_it(
    env: PmEnv, capsys: pytest.CaptureFixture[str], status: str
) -> None:
    record(CODE, record_text(status))
    one_pm(env, old_quiet(), "working")
    code, lines, _ = health(env, capsys)
    assert code == 1
    assert lines == [gone_line()]


def test_a_working_row_with_no_session_and_no_record_is_told_to_stop(
    env: PmEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    memory()
    one_pm(env, old_quiet(), "working")
    _, lines, _ = health(env, capsys)
    assert lines == [gone_line()]


def test_the_gone_line_names_no_command_for_an_unsafe_row_id(
    env: PmEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    memory()
    one_pm(env, old_quiet(), "working", id="a;touch x")
    _, lines, _ = health(env, capsys)
    assert lines == [f"{PM}: row says working, session gone"]


def test_a_working_row_with_no_session_and_an_idle_record_is_left_alone(
    env: PmEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    record(CODE, record_text("idle"))
    one_pm(env, old_quiet(), "working")
    assert health(env, capsys) == (0, ["ok"], "")


def test_a_done_project_prints_no_waits_on_the_gm_even_for_a_dead_session(
    env: PmEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    record(CODE, record_text("done"))
    one_pm(env, old_waiting(), "working")
    _, lines, _ = health(env, capsys)
    assert lines == [gone_line()]


def test_a_done_project_prints_no_waits_on_the_gm_for_a_live_pm(
    env: PmEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    record(CODE, record_text("done"))
    one_pm(env, brief().send(GM, "QUESTION acme-1/1: which?", at("10:02")).turn_end(at("10:03")))
    assert health(env, capsys) == (0, ["ok"], "")


def test_a_done_project_still_prints_what_is_not_a_wait(
    env: PmEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    record(CODE, record_text("done"))
    one_pm(
        env,
        brief().send(GM, "QUESTION acme-1/1: which?", at("10:02")).turn_end(at("10:03")),
        "blocked",
    )
    _, lines, _ = health(env, capsys)
    assert lines == [f"{PM}: waits on a prompt: claude attach 00000001"]


def test_a_dead_session_with_no_record_still_lists_its_waits_after_the_stop_line(
    env: PmEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    memory()
    one_pm(env, old_waiting(), "working")
    _, lines, _ = health(env, capsys)
    assert lines == [gone_line(), f"{PM}: waits on the GM: QUESTION acme-1/1 since 10:00"]


def undelivered() -> TranscriptBuilder:
    return (
        brief()
        .peer(GM, "TASK acme-1/1: go", at("09:30"))
        .send(GM, "DONE acme-1/1: finished", at("10:00"), delivered=False, error="not reachable")
        .turn_end(at("10:01"))
    )


def test_an_undelivered_message_is_flagged(env: PmEnv, capsys: pytest.CaptureFixture[str]) -> None:
    record(CODE, record_text("idle"))
    one_pm(env, undelivered())
    code, lines, _ = health(env, capsys)
    assert code == 1
    assert lines == [
        f"{PM}: last message never reached the GM (DONE acme-1/1): coven pm status --full {CODE}"
    ]


def test_undelivered_flag_clears_after_a_gm_message(
    env: PmEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    record(CODE, record_text("idle"))
    one_pm(env, undelivered().peer(GM, "ANSWER acme-1/1: seen", at("10:30")).turn_end(at("10:31")))
    assert health(env, capsys) == (0, ["ok"], "")


def test_a_final_text_that_was_never_sent_is_flagged(
    env: PmEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    one_pm(
        env,
        brief()
        .peer(GM, "TASK acme-1/1: go", at("09:30"))
        .text("QUESTION acme-1/2: should I?", at("10:00"))
        .turn_end(at("10:01")),
    )
    memory()
    _, lines, _ = health(env, capsys)
    assert lines == [
        f"{PM}: last message never reached the GM (QUESTION acme-1/2): "
        f"coven pm status --full {CODE}"
    ]


def test_renamed_gm_is_flagged_until_a_resume_note_names_it(
    env: PmEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    record(CODE, record_text("idle"))
    one_pm(env, brief(OLD_GM).turn_end(at("09:30")))
    code, lines, _ = health(env, capsys)
    assert code == 1
    assert lines == [
        f"{PM}: knows GM as '{OLD_GM}' (current: {GM}): "
        f"claude stop 00000001, then coven pm resume {CODE} --gm {GM}"
    ]
    one_pm(
        env,
        brief(OLD_GM)
        .turn_end(at("09:30"))
        .prompt(f"RESUMED by {GM}\nGM session: {GM}\n", at("10:00"))
        .turn_end(at("10:01")),
    )
    assert health(env, capsys) == (0, ["ok"], "")


def test_a_dead_pm_that_knows_the_old_gm_is_told_to_resume_not_to_stop(
    env: PmEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    record(CODE, record_text("running"))
    one_pm(env, brief(OLD_GM).turn_end(at("09:30")), "stopped")
    _, lines, _ = health(env, capsys)
    assert lines == [f"{PM}: record says running, PM stopped: coven pm resume {CODE} --gm {GM}"]


def test_an_unreadable_live_pm_is_named(env: PmEnv, capsys: pytest.CaptureFixture[str]) -> None:
    memory()
    env.set_rows(agents_row(PM, 1, "idle"))  # no transcript written
    code, lines, _ = health(env, capsys)
    assert code == 1
    assert lines == [f"{PM}: unknown (no transcript)"]


def test_lines_come_in_the_spec_order_for_each_pm(
    env: PmEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    record(CODE, record_text("running"))
    one_pm(
        env,
        brief(OLD_GM)
        .peer(OLD_GM, "ANSWER acme-1/9: earlier", at("09:30"))
        .send(OLD_GM, "QUESTION acme-1/1: first?", at("10:02"))
        .send(OLD_GM, "DONE acme-1/2: x", at("10:03"), delivered=False, error="nope")
        .turn_end(at("10:04")),
        "blocked",
    )
    _, lines, _ = health(env, capsys)
    starts = [
        f"{PM}: waits on a prompt",
        f"{PM}: last message never reached the GM (DONE acme-1/2)",
        f"{PM}: knows GM as '{OLD_GM}' (current: {GM})",
        f"{PM}: waits on the GM",
    ]
    assert len(lines) == len(starts)
    assert all(line.startswith(start) for line, start in zip(lines, starts, strict=True))


def test_each_pm_gets_its_own_lines(env: PmEnv, capsys: pytest.CaptureFixture[str]) -> None:
    record("acme-2", record_text("running").replace(CODE, "acme-2"))
    env.set_rows(agents_row("pm-acme-1", 1, "blocked"), agents_row("pm-acme-2", 2, "stopped"))
    env.transcript(quiet(), 1)
    env.transcript(quiet(), 2)
    _, lines, _ = health(env, capsys)
    assert lines == [
        "pm-acme-1: waits on a prompt: claude attach 00000001",
        f"pm-acme-2: record says running, PM stopped: coven pm resume acme-2 --gm {GM}",
    ]


def drifted() -> TranscriptBuilder:
    """Several assistant turns and no turn end: the transcript format reads as changed."""
    return brief().text("one", at("10:01")).text("two", at("10:02")).text("three", at("10:03"))


DRIFT_LINE = f"{PM}: transcript format unknown: read its log"


@pytest.mark.parametrize("state", ["stopped", "failed"])
def test_a_dead_pm_with_a_drifted_transcript_is_still_told_to_resume(
    env: PmEnv, capsys: pytest.CaptureFixture[str], state: str
) -> None:
    record(CODE, record_text("running"))
    one_pm(env, drifted(), state)
    _, lines, _ = health(env, capsys)
    assert lines == [
        f"{PM}: record says running, PM {state}: coven pm resume {CODE} --gm {GM}",
        DRIFT_LINE,
    ]


def test_a_blocked_pm_with_a_drifted_transcript_is_still_told_to_attach(
    env: PmEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    memory()
    one_pm(env, drifted(), "blocked")
    _, lines, _ = health(env, capsys)
    assert lines == [f"{PM}: waits on a prompt: claude attach 00000001", DRIFT_LINE]


def test_a_new_pm_that_asked_before_any_peer_message_only_waits(
    env: PmEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    memory()
    one_pm(env, brief().send(GM, "QUESTION acme-1/1: which?", at("10:00")).turn_end(at("10:01")))
    _, lines, _ = health(env, capsys)
    assert lines == [f"{PM}: waits on the GM: QUESTION acme-1/1 since 10:00"]


# --- the record -----------------------------------------------------------------------------


def test_record_without_status_line_is_reported(
    env: PmEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    record(CODE, record_text(None))
    code, lines, _ = health(env, capsys)
    assert code == 1
    assert lines == [f"{CODE}: no 'Project status:' line"]


def test_record_with_an_unknown_status_is_reported(
    env: PmEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    record(CODE, record_text("paused"))
    _, lines, _ = health(env, capsys)
    assert lines == [f"{CODE}: no 'Project status:' line"]


def resume_line(session: str = RECORD_ID) -> str:
    return (
        f"{PM}: record says running, PM gone: coven pm resume {CODE} --gm {GM} --session {session}"
    )


def test_a_newer_done_line_overrides_an_older_running_one(
    env: PmEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    record(CODE, record_text("running") + "Project status: done\n")
    assert health(env, capsys) == (0, ["ok"], "")


def test_only_the_last_status_line_counts(env: PmEnv, capsys: pytest.CaptureFixture[str]) -> None:
    record(CODE, record_text("idle") + "Project status: running\n")
    code, lines, _ = health(env, capsys)
    assert code == 1
    assert lines == [resume_line()]


def test_a_newer_session_id_line_overrides_an_older_one(
    env: PmEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    record(CODE, record_text("running") + f"PM: {PM}, sessionId {session_id(2)}\n")
    _, lines, _ = health(env, capsys)
    assert lines == [resume_line(session_id(2))]


def test_an_invalid_status_line_after_a_valid_one_is_ignored(
    env: PmEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    record(CODE, record_text("running") + "Project status: frozen (user)\n")
    code, lines, _ = health(env, capsys)
    assert code == 1
    assert lines == [resume_line()]


def test_an_unreadable_record_becomes_a_problem_line(
    env: PmEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    path = record(CODE, "")
    path.write_bytes(b"\x00binary")
    code, lines, err = health(env, capsys)
    assert (code, err) == (1, "")
    assert lines == [f"{CODE}: 'projects/{CODE}/{CODE}-state.md': a binary file"]


def test_a_project_folder_without_a_state_record_is_ignored(
    env: PmEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    store = memory()
    (store / "projects" / "acme-9").mkdir(parents=True)
    (store / "projects" / "acme-9" / "notes.md").write_text("Project status: running\n")
    assert health(env, capsys) == (0, ["ok"], "")


def test_a_store_without_projects_has_no_records(
    env: PmEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    memory()
    assert health(env, capsys) == (0, ["ok"], "")


def test_without_any_store_health_is_a_named_error(
    env: PmEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    one_pm(env, quiet())
    code, out, err = env.run(capsys, HEALTH, "--gm", GM)
    assert (code, out) == (2, "")
    assert err.startswith("coven: ")
    assert err.count("\n") == 1


def test_a_record_cannot_inject_terminal_control_into_the_output(
    env: PmEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    memory()
    record("acme-\x1b[31m1", record_text(None))
    _, lines, _ = health(env, capsys)
    assert len(lines) == 1
    assert "\x1b" not in lines[0]


# --- the command ----------------------------------------------------------------------------


def test_health_requires_gm(env: PmEnv, capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = env.run(capsys, HEALTH)
    assert (code, out) == (2, "")
    assert "--gm" in err


def test_health_runs_the_agents_cli_once(env: PmEnv, capsys: pytest.CaptureFixture[str]) -> None:
    record(CODE, record_text("running"))
    one_pm(env, quiet(), "stopped")
    health(env, capsys)
    assert env.agents_runs == 1


def test_health_reports_an_agents_failure(env: PmEnv, capsys: pytest.CaptureFixture[str]) -> None:
    memory()
    env.fail_agents()
    code, out, err = env.run(capsys, HEALTH, "--gm", GM)
    assert (code, out) == (2, "")
    assert err.startswith("coven: agents exited 1")


CANARIES = (
    "CANARY-brief",
    "CANARY-peer",
    "CANARY-tool-result",
    "CANARY-thinking",
    "CANARY-tool-input",
    "CANARY-sent",
)


def test_health_output_never_holds_canaries(env: PmEnv, capsys: pytest.CaptureFixture[str]) -> None:
    record(CODE, record_text("running"))
    builder = (
        TranscriptBuilder()
        .prompt(
            f"Brief for {PM}\nGM session: {OLD_GM}\nCANARY-brief\nANSWER acme-1/1: CANARY-brief\n",
            at("09:00"),
        )
        .peer(OLD_GM, "TASK acme-1/1: go", at("09:10"))
        .peer("pm-other", "ANSWER acme-1/3: CANARY-peer", at("09:20"))
        .tool_result_other("CANARY-tool-result", at("09:30"))
        .thinking("CANARY-thinking", at("09:35"))
        .raw(
            '{"type": "assistant", "timestamp": "2026-10-28T09:40:00.000Z", "message": '
            '{"role": "assistant", "content": [{"type": "tool_use", "id": "t9", "name": "Bash", '
            '"input": {"command": "echo CANARY-tool-input"}}]}}'
        )
        .send(OLD_GM, "QUESTION acme-1/3: CANARY-sent", at("10:00"))
        .send(OLD_GM, "DONE acme-1/1: CANARY-sent", at("10:05"), delivered=False, error="gone")
        .turn_end(at("10:06"))
    )
    one_pm(env, builder, "blocked")
    _, lines, _ = health(env, capsys)
    assert len(lines) == 4  # attach, never reached, knows GM as, waits on the GM
    for canary in CANARIES:
        assert canary not in "\n".join(lines)


def test_the_manager_skill_pins_the_record_lines_and_runs_health_at_start() -> None:
    skill = Path(__file__).resolve().parents[2] / "skills" / "manager" / "SKILL.md"
    prose = " ".join(skill.read_text().split())
    assert "`Project status: <running|idle|waiting for a slot|done>`" in prose
    assert "`sessionId <uuid>`" in prose
    assert "run `coven pm health --gm <your name>`" in prose
    assert "`since GM: ?` means unknown" in prose


# --- a record is found by its folder name (the key), a PM by its code --------------------------


def test_a_record_with_upper_case_in_its_key_names_the_pm_by_its_code(
    env: PmEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    record("Spellbound-Themes", record_text("running"))
    code, lines, _ = health(env, capsys)
    assert code == 1
    assert lines == [
        "pm-spellbound-themes: record says running, PM gone: "
        f"coven pm resume spellbound-themes --gm {GM} --session {RECORD_ID}"
    ]


def test_a_record_with_an_underscore_in_its_key_matches_the_row_of_its_code(
    env: PmEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    record("jinx_firefox_theme", record_text("running"))
    env.set_rows(agents_row("pm-jinx-firefox-theme", 1, "blocked"))
    env.transcript(quiet())
    code, lines, _ = health(env, capsys)
    assert code == 1
    assert lines == ["pm-jinx-firefox-theme: waits on a prompt: claude attach 00000001"]


def test_two_keys_with_one_code_are_a_problem_each_and_get_no_command(
    env: PmEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    record("Foo", record_text("running"))
    record("foo", record_text("running"))
    code, lines, _ = health(env, capsys)
    assert code == 1
    assert lines == [
        "Foo: same PM code 'foo' as foo",
        "foo: same PM code 'foo' as Foo",
    ]


def test_three_keys_with_one_code_each_name_the_other_two(
    env: PmEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    for key in ("a_b", "a-b", "A.b"):
        record(key, record_text("running"))
    _, lines, _ = health(env, capsys)
    assert lines == [
        "A.b: same PM code 'a-b' as a-b, a_b",
        "a-b: same PM code 'a-b' as A.b, a_b",
        "a_b: same PM code 'a-b' as A.b, a-b",
    ]


def test_a_clash_does_not_hide_the_row_of_the_shared_code(
    env: PmEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    record("Foo", record_text("running"))
    record("foo", record_text("running"))
    env.set_rows(agents_row("pm-foo", 1, "blocked"))
    env.transcript(quiet())
    _, lines, _ = health(env, capsys)
    assert lines == [
        "pm-foo: waits on a prompt: claude attach 00000001",
        "Foo: same PM code 'foo' as foo",
        "foo: same PM code 'foo' as Foo",
    ]


# --- nothing unsafe in a suggested command ------------------------------------------------------

BAD_CODE = "a;touch x"
BAD_KEY = ";touch x"  # lower-cased and cleaned it is `-touch-x`, which no command may carry


def test_a_record_folder_whose_code_is_invalid_gets_no_command(
    env: PmEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    record(BAD_KEY, record_text("running"))
    code, lines, _ = health(env, capsys)
    assert code == 1
    assert lines == [f"{BAD_KEY}: invalid project code"]


def test_a_record_folder_with_a_name_that_cleans_to_a_code_gets_a_command(
    env: PmEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    record(BAD_CODE, record_text("running"))
    _, lines, _ = health(env, capsys)
    assert lines == [
        "pm-a-touch-x: record says running, PM gone: "
        f"coven pm resume a-touch-x --gm {GM} --session {RECORD_ID}"
    ]


def test_a_record_folder_that_starts_with_an_underscore_is_an_invalid_code(
    env: PmEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    record("_x", record_text("running"))
    code, lines, _ = health(env, capsys)
    assert code == 1
    assert lines == ["_x: invalid project code"]


def test_a_bad_code_folder_without_a_state_record_is_ignored(
    env: PmEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    store = memory()
    (store / "projects" / BAD_CODE).mkdir(parents=True)
    assert health(env, capsys) == (0, ["ok"], "")


def test_a_row_name_with_a_bad_code_gets_no_command(
    env: PmEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    memory()
    env.set_rows(agents_row(f"pm-{BAD_CODE}", 1, "blocked"))
    env.transcript(quiet())
    _, lines, _ = health(env, capsys)
    assert lines == [f"pm-{BAD_CODE}: waits on a prompt"]


@pytest.mark.parametrize("bad_id", ["a;touch x", "0A1B2C3D", "0000000"])
def test_a_row_id_that_is_not_hex_gets_no_command(
    env: PmEnv, capsys: pytest.CaptureFixture[str], bad_id: str
) -> None:
    memory()
    one_pm(env, quiet(), "blocked", id=bad_id)
    _, lines, _ = health(env, capsys)
    assert lines == [f"{PM}: waits on a prompt"]


def test_a_bad_row_id_leaves_the_command_out_of_an_undelivered_flag(
    env: PmEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    memory()
    one_pm(env, undelivered(), id="a;touch x")
    _, lines, _ = health(env, capsys)
    assert lines == [f"{PM}: last message never reached the GM (DONE acme-1/1)"]


def test_a_bad_row_id_leaves_the_command_out_of_a_renamed_gm_flag(
    env: PmEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    memory()
    one_pm(env, brief(OLD_GM).turn_end(at("09:30")), id="a;touch x")
    _, lines, _ = health(env, capsys)
    assert lines == [f"{PM}: knows GM as '{OLD_GM}' (current: {GM})"]


@pytest.mark.parametrize("bad_gm", ["gm;touch x", "-gm", "gm main", "gm$(x)"])
def test_a_bad_gm_name_is_refused_before_anything_runs(
    env: PmEnv, capsys: pytest.CaptureFixture[str], bad_gm: str
) -> None:
    record(CODE, record_text("running"))
    code, out, err = env.run(capsys, HEALTH, f"--gm={bad_gm}")
    assert (code, out) == (2, "")
    assert "--gm" in err
    assert env.agents_runs == 0


# --- the cap and the exit code ------------------------------------------------------------------


def live_pms(env: PmEnv, count: int) -> None:
    """`count` idle PMs with a running process each, named pm-acme-1, pm-acme-2, ..."""
    rows = []
    for number in range(1, count + 1):
        env.process(1000 + number)
        rows.append(agents_row(f"pm-acme-{number}", number, "idle", pid=1000 + number))
        env.transcript(quiet(), number)
    env.set_rows(*rows)


def test_health_flags_over_cap(env: PmEnv, capsys: pytest.CaptureFixture[str]) -> None:
    memory()
    live_pms(env, 3)  # the default cap is 2
    code, lines, err = health(env, capsys)
    assert (code, err) == (1, "")
    assert lines == ["PMs running: 3 of cap 2"]


def test_health_follows_the_configured_cap(env: PmEnv, capsys: pytest.CaptureFixture[str]) -> None:
    memory()
    save_config(Config(load_config().memory_path, None, 3))
    live_pms(env, 3)
    assert health(env, capsys) == (0, ["ok"], "")


def test_health_at_the_cap_is_quiet(env: PmEnv, capsys: pytest.CaptureFixture[str]) -> None:
    memory()
    live_pms(env, 2)
    assert health(env, capsys) == (0, ["ok"], "")


def test_a_dead_pm_does_not_count_against_the_cap(
    env: PmEnv, capsys: pytest.CaptureFixture[str]
) -> None:
    memory()
    live_pms(env, 2)
    env.set_rows(*json.loads(env.rows_file.read_text()), agents_row("pm-acme-9", 9, "stopped"))
    assert health(env, capsys) == (0, ["ok"], "")
