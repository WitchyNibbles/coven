"""`coven pm status`: one block per project-manager session, from the agents rows and transcripts."""

import time
from datetime import timedelta
from pathlib import Path

import pytest
from pm.fixtures import (
    CWD,
    GM,
    NOW,
    PM,
    PmEnv,
    TranscriptBuilder,
    agents_row,
    at,
    encode_cwd,
    pm_env,
    session_id,
)

from coven.pm.report import Scene, build_view
from coven.pm.rows import parse_rows
from coven.pm.transcript import read_transcript

STATUS = "status"
ONE_LIVE = "1 project manager, 1 live"
NONE_LIVE = "1 project manager, 0 live"
PM_CODE = "acme-1"


@pytest.fixture
def env(tmp_path, monkeypatch):
    yield from pm_env(tmp_path, monkeypatch)


def brief() -> TranscriptBuilder:
    """A transcript that starts like a PM's: the brief, naming the GM's session."""
    return TranscriptBuilder().prompt(f"Brief for {PM}\nGM session: {GM}\n", at("09:00"))


def one_pm(env: PmEnv, builder: TranscriptBuilder, state: str = "idle", **fields: object) -> None:
    env.set_rows(agents_row(PM, 1, state, **fields))
    env.transcript(builder)


def status(env: PmEnv, capsys, *argv: str) -> tuple[int, str, str]:
    return env.run(capsys, STATUS, *argv)


def test_idle_notice_after_the_gm_answered_says_no(env, capsys):
    one_pm(
        env,
        brief()
        .send(GM, "QUESTION acme-1/3: which one?", at("10:02"))
        .send(GM, "DONE acme-1/1: done", at("11:30"))
        .turn_end(at("11:31"))
        .peer(GM, "ANSWER acme-1/2: go", at("11:41")),
    )
    code, out, err = status(env, capsys)
    assert (code, err) == (0, "")
    assert out.splitlines() == [
        "pm-acme-1  id=00000001  state=idle 29m  since GM: no",
        "  last out: 11:30 DONE acme-1/1: done   [delivered]",
        "  last in:  11:41 ANSWER acme-1/2: go  (from gm-main)",
        "  waits on GM: QUESTION acme-1/3 since 10:02",
        ONE_LIVE,
    ]


def test_message_after_the_gm_says_message(env, capsys):
    one_pm(
        env,
        brief()
        .peer(GM, "ANSWER acme-1/2: go", at("11:41"))
        .send(GM, "DONE acme-1/2: done", at("11:45"))
        .turn_end(at("11:46")),
    )
    _, out, _ = status(env, capsys)
    assert "state=idle 14m  since GM: message" in out


def test_turn_without_a_message_says_turn(env, capsys):
    one_pm(
        env,
        brief()
        .peer(GM, "ANSWER acme-1/2: go", at("11:41"))
        .text("looking into it", at("11:44"))
        .turn_end(at("11:46")),
    )
    _, out, _ = status(env, capsys)
    assert "since GM: turn" in out


def test_never_sent_final_text_is_flagged(env, capsys):
    one_pm(
        env,
        brief()
        .peer(GM, "ANSWER acme-1/2: go", at("10:00"))
        .text("BLOCKED acme-1/4: cannot reach the DB\nsecond line stays out", at("11:50"))
        .turn_end(at("11:51")),
    )
    code, out, _ = status(env, capsys)
    assert code == 0
    assert (
        "  last out: 11:50 BLOCKED acme-1/4: cannot reach the DB   [NOT DELIVERED: never sent]"
        in (out.splitlines())
    )
    assert "second line" not in out


def test_final_text_without_a_known_prefix_is_not_a_last_out(env, capsys):
    one_pm(
        env,
        brief()
        .peer(GM, "ANSWER acme-1/2: go", at("10:00"))
        .text("just thinking out loud", at("11:50"))
        .turn_end(at("11:51")),
    )
    _, out, _ = status(env, capsys)
    assert "last out" not in out
    assert "thinking" not in out


def test_failed_send_is_flagged_with_reason(env, capsys):
    one_pm(
        env,
        brief()
        .peer(GM, "ANSWER acme-1/2: go", at("10:00"))
        .send(
            GM,
            "QUESTION acme-1/5: which?",
            at("11:50"),
            delivered=False,
            error="No agent named gm-main is reachable",
        )
        .turn_end(at("11:51")),
    )
    _, out, _ = status(env, capsys)
    assert (
        "  last out: 11:50 QUESTION acme-1/5: which?   "
        "[NOT DELIVERED: No agent named gm-main is reachable]"
    ) in out.splitlines()
    assert "waits on GM" not in out


def test_other_peer_answer_is_not_the_gms(env, capsys):
    one_pm(
        env,
        brief().peer("pm-other", "ANSWER acme-1/2: x", at("11:41")).turn_end(at("11:45")),
    )
    _, out, _ = status(env, capsys)
    assert "since GM: -" in out
    assert "last in" not in out
    assert "pm-other" not in out


def test_gm_flag_names_a_renamed_gm(env, capsys):
    one_pm(
        env,
        brief().peer("gm-new", "ANSWER acme-1/2: go", at("11:41")).turn_end(at("11:45")),
    )
    _, without, _ = status(env, capsys)
    _, with_flag, _ = status(env, capsys, "--gm", "gm-new")
    assert "last in" not in without
    assert "  last in:  11:41 ANSWER acme-1/2: go  (from gm-new)" in with_flag.splitlines()


def test_resume_note_names_the_new_gm(env, capsys):
    one_pm(
        env,
        brief()
        .peer(GM, "ANSWER acme-1/1: go", at("09:30"))
        .prompt("RESUMED by gm-new\nGM session: gm-new\nANSWER acme-1/2: carry on\n", at("11:00"))
        .peer("gm-new", "TASK acme-1/6: next", at("11:20"))
        .turn_end(at("11:30")),
    )
    _, out, _ = status(env, capsys)
    assert "  last in:  11:20 TASK acme-1/6: next  (from gm-new)" in out.splitlines()


def test_dead_pid_is_stale(env, capsys):
    one_pm(env, brief().turn_end(at("11:46")), "working", pid=4242)
    _, out, _ = status(env, capsys)
    assert "state=stale" in out


def test_live_pid_keeps_the_row_state(env, capsys):
    one_pm(env, brief().turn_end(at("11:46")), "working", pid=4242)
    env.process(4242)
    _, out, _ = status(env, capsys)
    assert "state=working" in out


def test_working_row_without_pid_and_old_transcript_is_stale(env, capsys):
    old = NOW - timedelta(hours=26)
    builder = TranscriptBuilder().prompt(f"Brief\nGM session: {GM}\n", old).turn_end(old)
    one_pm(env, builder, "working")
    _, out, _ = status(env, capsys)
    assert "state=stale" in out


def test_working_row_without_pid_and_fresh_transcript_stays_working(env, capsys):
    one_pm(env, brief().turn_end(at("11:50")), "working")
    _, out, _ = status(env, capsys)
    assert "state=working" in out


def test_two_live_rows_are_duplicate(env, capsys):
    env.set_rows(agents_row(PM, 1, "working"), agents_row(PM, 2, "idle"))
    _, out, _ = status(env, capsys)
    assert out.splitlines()[0].startswith("pm-acme-1  id=00000002,00000001  state=duplicate")


def test_a_past_resume_does_not_make_a_duplicate(env, capsys):
    env.set_rows(agents_row(PM, 1, "stopped"), agents_row(PM, 2, "idle"))
    env.transcript(brief().turn_end(at("11:46")), 2)
    _, out, _ = status(env, capsys)
    assert out.splitlines()[0].startswith("pm-acme-1  id=00000002  state=idle")


def test_row_without_transcript_is_listed(env, capsys):
    env.set_rows(agents_row(PM, 1, "idle"))
    code, out, _ = status(env, capsys)
    assert code == 0
    assert out.splitlines() == [
        "pm-acme-1  id=00000001  state=unknown (no transcript)  since GM: -",
        ONE_LIVE,  # the row is idle: a missing transcript does not make it dead
    ]


def test_unreadable_transcript_is_that_pms_state_only(env, capsys):
    env.set_rows(agents_row("pm-acme-1", 1, "idle"), agents_row("pm-acme-2", 2, "idle"))
    (env.transcripts / f"{session_id(1)}.jsonl").mkdir()  # exists, cannot be read as a file
    env.transcript(brief().turn_end(at("11:46")), 2)
    code, out, _ = status(env, capsys)
    assert code == 0
    assert "state=unknown (transcript unreadable)" in out
    assert "pm-acme-2  id=00000002  state=idle 14m" in out


def test_row_without_session_id_is_listed(env, capsys):
    env.set_rows(agents_row(PM, 1, "idle", sessionId=None))
    _, out, _ = status(env, capsys)
    assert "state=unknown (no session id)" in out


def test_row_with_a_bad_session_id_is_listed(env, capsys):
    env.set_rows(agents_row(PM, 1, "idle", sessionId="../../etc"))
    _, out, _ = status(env, capsys)
    assert "state=unknown (bad session id)" in out


def test_row_fields_are_cleaned_before_printing(env, capsys):
    env.set_rows(agents_row("pm-acme-1", 1, "\x1b[31mred\x1b[0m\x07", id="ab\x1b]0;title\x07cd"))
    env.transcript(brief().turn_end(at("11:46")))
    _, out, _ = status(env, capsys)
    assert "\x1b" not in out
    assert "\x07" not in out
    assert "state=red" in out


def test_row_with_only_a_status_maps_busy_to_working(env, capsys):
    env.set_rows(agents_row(PM, 1, None, status="busy"))
    env.transcript(brief().turn_end(at("11:50")))
    _, out, _ = status(env, capsys)
    assert "state=working" in out


def test_a_new_pm_that_asked_before_any_peer_message_is_not_drifted(env, capsys):
    one_pm(env, brief().send(GM, "QUESTION acme-1/1: which?", at("11:30")).turn_end(at("11:31")))
    _, out, _ = status(env, capsys)
    assert out.splitlines() == [
        "pm-acme-1  id=00000001  state=idle 29m  since GM: -",
        "  last out: 11:30 QUESTION acme-1/1: which?   [delivered]",
        "  waits on GM: QUESTION acme-1/1 since 11:30",
        ONE_LIVE,
    ]


def drifted() -> TranscriptBuilder:
    """Several assistant turns and no turn end: the transcript format reads as changed."""
    return (
        brief()
        .peer(GM, "ANSWER acme-1/2: go", at("10:00"))
        .text("one", at("10:01"))
        .text("two", at("10:02"))
        .text("three", at("10:03"))
    )


def test_several_assistant_turns_without_a_turn_end_is_format_drift(env, capsys):
    one_pm(env, drifted())
    _, out, _ = status(env, capsys)
    lines = out.splitlines()
    assert lines[0] == "pm-acme-1  id=00000001  state=idle 1h  since GM: ?"
    assert "  transcript format unknown: read its log" in lines


@pytest.mark.parametrize("state", ["failed", "blocked", "stopped"])
def test_a_drifted_transcript_keeps_the_row_state(env, capsys, state):
    one_pm(env, drifted(), state)
    _, out, _ = status(env, capsys)
    assert out.splitlines()[0] == f"pm-acme-1  id=00000001  state={state} 1h  since GM: ?"


def test_a_drifted_transcript_never_says_since_gm_no(env, capsys):
    one_pm(
        env,
        brief()
        .send(GM, "DONE acme-1/1: done", at("10:00"))
        .peer(GM, "ANSWER acme-1/2: go", at("10:01"))
        .text("one", at("10:02"))
        .text("two", at("10:03"))
        .text("three", at("10:04")),
    )
    _, out, _ = status(env, capsys)
    assert out.splitlines()[0].endswith("since GM: ?")


def test_without_any_turn_end_since_gm_is_unknown_not_no(env, capsys):
    one_pm(
        env,
        brief().peer(GM, "ANSWER acme-1/2: go", at("10:00")).text("working", at("10:01")),
    )
    _, out, _ = status(env, capsys)
    assert out.splitlines()[0] == "pm-acme-1  id=00000001  state=idle 1h  since GM: ?"


def test_without_any_turn_end_a_message_after_the_gm_still_says_message(env, capsys):
    one_pm(
        env,
        brief()
        .peer(GM, "ANSWER acme-1/2: go", at("10:00"))
        .send(GM, "DONE acme-1/2: done", at("10:05")),
    )
    _, out, _ = status(env, capsys)
    assert out.splitlines()[0].endswith("since GM: message")


def test_skipped_lines_are_counted(env, capsys):
    one_pm(env, brief().raw('{"type": "assistant", broken').turn_end(at("11:46")))
    _, out, _ = status(env, capsys)
    assert out.splitlines()[-2] == "  skipped lines: 1"


def test_a_line_too_deep_to_decode_is_skipped_not_a_crash(env, capsys):
    deep = '{"type": "assistant", "n": ' + "[" * 100_000 + "]" * 100_000 + "}"
    one_pm(env, brief().raw(deep).turn_end(at("11:46")))
    code, out, _ = status(env, capsys)
    assert code == 0
    assert out.splitlines()[-2] == "  skipped lines: 1"


def test_a_time_before_any_timestamp_prints_as_a_question_mark(env, capsys):
    one_pm(
        env,
        TranscriptBuilder()
        .send(GM, "DONE acme-1/1: done", None)
        .peer(GM, "ANSWER acme-1/2: go", at("11:41"))
        .turn_end(at("11:45")),
    )
    _, out, _ = status(env, capsys)
    assert "  last out: ? DONE acme-1/1: done   [delivered]" in out.splitlines()


def test_a_later_gm_message_for_the_key_clears_the_wait(env, capsys):
    one_pm(
        env,
        brief()
        .send(GM, "QUESTION acme-1/3: which?", at("10:02"))
        .send(GM, "PROD-STEP acme-1/4: deploy", at("10:10"))
        .peer(GM, "ANSWER acme-1/3: this one", at("10:30"))
        .turn_end(at("10:31")),
    )
    _, out, _ = status(env, capsys)
    assert "  waits on GM: PROD-STEP acme-1/4 since 10:10" in out.splitlines()


def test_a_later_done_clears_the_wait(env, capsys):
    one_pm(
        env,
        brief()
        .send(GM, "BLOCKED acme-1/3: stuck", at("10:02"))
        .peer(GM, "ANSWER acme-1/9: other", at("10:05"))
        .send(GM, "DONE acme-1/3: unstuck", at("10:30"))
        .turn_end(at("10:31")),
    )
    _, out, _ = status(env, capsys)
    assert "waits on GM" not in out


def test_an_answer_for_another_key_does_not_clear_the_wait(env, capsys):
    one_pm(
        env,
        brief()
        .send(GM, "DRAFT acme-1/3: text", at("10:02"))
        .peer(GM, "ANSWER acme-1/9: other", at("10:05"))
        .turn_end(at("10:31")),
    )
    _, out, _ = status(env, capsys)
    assert "  waits on GM: DRAFT acme-1/3 since 10:02" in out.splitlines()


def test_an_unusable_key_is_no_wait_but_its_message_is_still_the_last_out(env, capsys):
    one_pm(
        env,
        brief()
        .peer(GM, "ANSWER acme-1/1: go", at("09:30"))
        .send(GM, "QUESTION ../../etc/passwd: hm", at("10:02"))
        .send(GM, "DRAFT", at("10:03"))
        .turn_end(at("10:31")),
    )
    _, out, _ = status(env, capsys)
    assert "waits on GM" not in out
    assert "  last out: 10:03 DRAFT   [delivered]" in out.splitlines()


# --- asks the GM already handled ----------------------------------------------------------------

OLD_GM = "gm-old"


def waits_of(env, capsys) -> list[str]:
    _, out, _ = status(env, capsys)
    return [line for line in out.splitlines() if "waits on GM" in line]


def test_a_reply_from_a_gm_name_the_pm_wrote_to_clears_the_wait(env, capsys):
    one_pm(
        env,
        brief()
        .send(OLD_GM, "PROD-STEP acme-1/4: deploy", at("10:02"))
        .peer(OLD_GM, "APPROVED acme-1/4 2026-10-30-id: go", at("10:30"))
        .turn_end(at("10:31")),
    )
    assert waits_of(env, capsys) == []


def test_a_reply_from_a_name_the_pm_never_wrote_to_is_still_ignored(env, capsys):
    one_pm(
        env,
        brief()
        .send(GM, "PROD-STEP acme-1/4: deploy", at("10:02"))
        .peer(OLD_GM, "APPROVED acme-1/4 2026-10-30-id: go", at("10:30"))
        .turn_end(at("10:31")),
    )
    assert waits_of(env, capsys) == ["  waits on GM: PROD-STEP acme-1/4 since 10:02"]


def test_a_name_the_pm_only_wrote_plain_text_or_a_reply_prefix_to_is_not_a_gm(env, capsys):
    one_pm(
        env,
        brief()
        .send(GM, "PROD-STEP acme-1/4: deploy", at("10:02"))
        .send(OLD_GM, "just a note", at("10:05"))
        .send(OLD_GM, "ANSWER acme-1/9: not a PM prefix", at("10:06"))
        .peer(OLD_GM, "APPROVED acme-1/4 2026-10-30-id: go", at("10:30"))
        .turn_end(at("10:31")),
    )
    assert waits_of(env, capsys) == ["  waits on GM: PROD-STEP acme-1/4 since 10:02"]


def test_an_unknown_sender_name_is_not_made_a_gm_by_a_message_sent_to_it(env, capsys):
    # A resume note with no `GM session:` line is received from "?": a PM that once wrote to a
    # peer called "?" must not make those notes count as the GM's replies.
    one_pm(
        env,
        brief()
        .send(GM, "PROD-STEP acme-1/4: deploy", at("10:02"))
        .send("?", "DONE acme-1/9: finished", at("10:03"))
        .prompt("RESUMED by someone\nAPPROVED acme-1/4 2026-10-30-id: go\n", at("10:30"))
        .turn_end(at("10:31")),
    )
    assert waits_of(env, capsys) == ["  waits on GM: PROD-STEP acme-1/4 since 10:02"]


def test_one_reply_naming_several_tasks_clears_each_of_them(env, capsys):
    one_pm(
        env,
        brief()
        .send(GM, "PROD-STEP acme-1/7x-A1b: deploy", at("10:02"))
        .send(GM, "QUESTION acme-1/7x-A2: which?", at("10:03"))
        .peer(GM, "APPROVED acme-1/7x-A1 and 7x-A1b 2026-10-30-id: go", at("10:30"))
        .turn_end(at("10:31")),
    )
    assert waits_of(env, capsys) == ["  waits on GM: QUESTION acme-1/7x-A2 since 10:03"]


def test_a_reply_naming_several_tasks_leaves_those_it_did_not_name(env, capsys):
    one_pm(
        env,
        brief()
        .send(GM, "QUESTION acme-1/1: first?", at("10:02"))
        .send(GM, "QUESTION acme-1/2: second?", at("10:03"))
        .peer(GM, "ANSWER acme-1/1 and 3: both", at("10:30"))
        .turn_end(at("10:31")),
    )
    assert waits_of(env, capsys) == ["  waits on GM: QUESTION acme-1/2 since 10:03"]


def test_a_reply_does_not_clear_a_task_asked_after_it(env, capsys):
    one_pm(
        env,
        brief()
        .send(GM, "QUESTION acme-1/1: first?", at("10:02"))
        .peer(GM, "ANSWER acme-1/1 and 2: both", at("10:30"))
        .send(GM, "QUESTION acme-1/2: again?", at("10:40"))
        .turn_end(at("10:41")),
    )
    assert waits_of(env, capsys) == ["  waits on GM: QUESTION acme-1/2 since 10:40"]


def test_one_done_naming_several_tasks_clears_each_of_them(env, capsys):
    one_pm(
        env,
        brief()
        .send(GM, "DRAFT acme-1/1: text", at("10:02"))
        .send(GM, "QUESTION acme-1/2: which?", at("10:03"))
        .send(GM, "DONE acme-1/1 and acme-1/2: both finished", at("10:30"))
        .turn_end(at("10:31")),
    )
    assert waits_of(env, capsys) == []


def test_a_done_line_inside_a_drafts_body_closes_the_draft(env, capsys):
    one_pm(
        env,
        brief()
        .send(
            GM,
            "DRAFT acme-1/10: hand-over\ntext for the user\nDONE acme-1/10: wrapped",
            at("10:02"),
        )
        .turn_end(at("10:03")),
    )
    assert waits_of(env, capsys) == []


def test_a_done_line_inside_a_body_may_leave_out_the_colon_and_name_several_tasks(env, capsys):
    body = "DRAFT acme-1/10: hand-over\nDONE acme-1/10 and acme-1/11"
    one_pm(
        env,
        brief()
        .send(GM, "QUESTION acme-1/11: which?", at("10:01"))
        .send(GM, body, at("10:02"))
        .turn_end(at("10:03")),
    )
    assert waits_of(env, capsys) == []


def test_other_lines_inside_a_body_are_not_asks_and_close_nothing(env, capsys):
    body = (
        "DRAFT acme-1/10: hand-over\nplain words\nQUESTION acme-1/11: inside\nANSWER acme-1/10: x"
    )
    one_pm(env, brief().send(GM, body, at("10:02")).turn_end(at("10:03")))
    assert waits_of(env, capsys) == ["  waits on GM: DRAFT acme-1/10 since 10:02"]


def test_an_ask_sent_in_the_turn_of_its_done_is_no_wait(env, capsys):
    one_pm(
        env,
        brief()
        .send(GM, "DONE acme-1/8: finished", at("10:02"))
        .send(GM, "DRAFT acme-1/8: hand-over text for the user", at("10:03"))
        .turn_end(at("10:04")),
    )
    assert waits_of(env, capsys) == []


def test_a_turn_end_stamped_like_the_ask_ends_the_asks_own_turn(env, capsys):
    # A turn-end entry comes after the messages of its turn, so one with the same stamp as the
    # DONE and the ask closes the turn they were sent in: the ask is hand-over text.
    one_pm(
        env,
        brief()
        .send(GM, "DONE acme-1/8: finished", at("10:02"))
        .send(GM, "DRAFT acme-1/8: hand-over text", at("10:02"))
        .turn_end(at("10:02")),
    )
    assert waits_of(env, capsys) == []


def test_a_turn_end_stamped_like_the_done_ends_the_turn_before_a_later_ask(env, capsys):
    one_pm(
        env,
        brief()
        .send(GM, "DONE acme-1/8: finished", at("10:02"))
        .turn_end(at("10:02"))
        .send(GM, "DRAFT acme-1/8: reopened", at("10:03"))
        .turn_end(at("10:04")),
    )
    assert waits_of(env, capsys) == ["  waits on GM: DRAFT acme-1/8 since 10:03"]


def test_an_ask_sent_in_a_later_turn_than_its_done_still_waits(env, capsys):
    one_pm(
        env,
        brief()
        .send(GM, "DONE acme-1/8: finished", at("10:02"))
        .turn_end(at("10:03"))
        .send(GM, "DRAFT acme-1/8: reopened", at("10:30"))
        .turn_end(at("10:31")),
    )
    assert waits_of(env, capsys) == ["  waits on GM: DRAFT acme-1/8 since 10:30"]


def test_an_ask_in_the_turn_of_a_done_for_another_task_still_waits(env, capsys):
    one_pm(
        env,
        brief()
        .send(GM, "DONE acme-1/7: finished", at("10:02"))
        .send(GM, "DRAFT acme-1/8: text", at("10:03"))
        .turn_end(at("10:04")),
    )
    assert waits_of(env, capsys) == ["  waits on GM: DRAFT acme-1/8 since 10:03"]


def test_age_uses_utc_now(env, capsys, monkeypatch):
    monkeypatch.setenv("TZ", "Asia/Tokyo")  # nine hours ahead of the transcript's UTC stamps
    time.tzset()
    one_pm(
        env,
        brief()
        .peer(GM, "ANSWER acme-1/2: go", at("10:00"))
        .send(GM, "DONE acme-1/1: done", at("11:45"))
        .turn_end(at("11:46")),
    )
    _, out, _ = status(env, capsys)
    assert "state=idle 14m" in out
    assert "  last out: 20:45 DONE" in out


@pytest.mark.parametrize(
    ("turn_end", "age"),
    [("11:59", "1m"), ("11:30", "30m"), ("09:00", "3h"), ("01:00", "11h")],
)
def test_age_is_minutes_hours_or_days(env, capsys, turn_end, age):
    one_pm(env, brief().turn_end(at(turn_end)))
    _, out, _ = status(env, capsys)
    assert f"state=idle {age}  since GM" in out


def test_age_in_days(env, capsys):
    one_pm(env, brief().turn_end(NOW - timedelta(days=2, hours=1)), "stopped")
    _, out, _ = status(env, capsys, "--all")
    assert "state=stopped 2d" in out


def test_default_hides_old_stopped_sessions_and_all_shows_them(env, capsys):
    old = int((NOW - timedelta(days=10)).timestamp() * 1000)
    env.set_rows(agents_row("pm-old-1", 1, "stopped", startedAt=old), agents_row(PM, 2, "idle"))
    _, default, _ = status(env, capsys)
    _, everything, _ = status(env, capsys, "--all")
    assert "pm-old-1" not in default
    assert "pm-old-1" in everything
    assert "pm-acme-1" in default


def test_a_live_session_shows_however_old_it_is(env, capsys):
    old = int((NOW - timedelta(days=10)).timestamp() * 1000)
    env.set_rows(agents_row(PM, 1, "idle", startedAt=old))
    _, out, _ = status(env, capsys)
    assert "pm-acme-1" in out


def test_codes_narrow_the_listing_even_to_old_sessions(env, capsys):
    old = int((NOW - timedelta(days=10)).timestamp() * 1000)
    env.set_rows(agents_row("pm-old-1", 1, "stopped", startedAt=old), agents_row(PM, 2, "idle"))
    _, out, _ = status(env, capsys, "old-1")
    assert "pm-old-1" in out
    assert "pm-acme-1" not in out


def test_an_unknown_code_is_an_error(env, capsys):
    env.set_rows(agents_row(PM, 1, "idle"))
    code, out, err = status(env, capsys, "nope")
    assert (code, out) == (2, "")
    assert err == "coven: no project-manager session named pm-nope\n"


def test_no_sessions_says_so(env, capsys):
    env.set_rows(agents_row("session-1", 1, "idle"))
    code, out, _ = status(env, capsys)
    assert (code, out) == (0, "no project-manager sessions\n")


def test_other_sessions_are_not_listed(env, capsys):
    env.set_rows(agents_row("session-1", 1, "idle"), agents_row(PM, 2, "idle"))
    _, out, _ = status(env, capsys, "--all")
    assert "session-1" not in out


def test_full_prints_the_whole_message(env, capsys):
    one_pm(
        env,
        brief()
        .peer(GM, "ANSWER acme-1/2: go", at("10:00"))
        .text(
            "DRAFT acme-1/7: reply\n\n\x1b[31mred\x1b[0m line two\nline three",
            at("11:50"),
        )
        .turn_end(at("11:51")),
    )
    code, out, _ = status(env, capsys, "--full", PM_CODE)
    assert code == 0
    assert out == "DRAFT acme-1/7: reply\n\nred line two\nline three\n"


def test_full_without_a_message_is_an_error(env, capsys):
    one_pm(env, brief().peer(GM, "ANSWER acme-1/2: go", at("10:00")).turn_end(at("11:51")))
    code, out, err = status(env, capsys, "--full", PM_CODE)
    assert (code, out) == (2, "")
    assert "pm-acme-1" in err


def test_printed_lines_are_clean_and_short(env, capsys):
    long_text = "DONE acme-1/1: \x1b[31m" + "x" * 400
    one_pm(
        env,
        brief()
        .peer(GM, "ANSWER acme-1/2: go", at("10:00"))
        .send(GM, long_text, at("11:30"))
        .turn_end(at("11:31")),
    )
    _, out, _ = status(env, capsys)
    assert "\x1b" not in out
    assert all(len(line) < 260 for line in out.splitlines())
    assert "…" in out


def test_printed_lines_hold_no_bidi_override_or_unicode_line_break(env, capsys):
    one_pm(
        env,
        brief()
        .peer(GM, "ANSWER acme-1/2: go", at("10:00"))
        .send(GM, "DONE acme-1/1: \u202eevil\u2028second\u2029third", at("11:30"))
        .turn_end(at("11:31")),
    )
    _, out, _ = status(env, capsys)
    _, full, _ = status(env, capsys, "--full", PM_CODE)
    assert "  last out: 11:30 DONE acme-1/1: evilsecondthird   [delivered]" in out.splitlines()
    assert full == "DONE acme-1/1: evilsecondthird\n"


CANARIES = (
    "CANARY-tool-result",
    "CANARY-thinking",
    "CANARY-tool-input",
    "CANARY-peer",
    "CANARY-brief",
)


def test_output_never_holds_canaries(env, capsys):
    builder = (
        TranscriptBuilder()
        .prompt(
            f"Brief for {PM}\nGM session: {GM}\nCANARY-brief\nANSWER acme-1/1: CANARY-brief\n",
            at("09:00"),
        )
        .peer(GM, "ANSWER acme-1/1: go", at("09:30"))
        .peer("pm-other", "ANSWER acme-1/2: CANARY-peer", at("09:40"))
        .tool_result_other("CANARY-tool-result", at("09:50"))
        .thinking("CANARY-thinking", at("09:55"))
        .raw(
            '{"type": "assistant", "timestamp": "2026-10-28T09:56:00.000Z", "message": '
            '{"role": "assistant", "content": [{"type": "tool_use", "id": "t9", "name": "Bash", '
            '"input": {"command": "echo CANARY-tool-input"}}]}}'
        )
        .send(GM, "DONE acme-1/1: done", at("11:30"))
        .turn_end(at("11:31"))
    )
    one_pm(env, builder)
    _, plain, _ = status(env, capsys)
    _, full, _ = status(env, capsys, "--full", PM_CODE)
    for canary in CANARIES:
        assert canary not in plain
        assert canary not in full


def test_a_reply_line_in_the_first_brief_is_not_received(env, capsys):
    one_pm(
        env,
        TranscriptBuilder()
        .prompt(f"Brief for {PM}\nGM session: {GM}\nANSWER acme-1/1: CANARY-brief\n", at("09:00"))
        .turn_end(at("09:30")),
    )
    _, out, _ = status(env, capsys)
    assert out.splitlines() == ["pm-acme-1  id=00000001  state=idle 2h  since GM: -", ONE_LIVE]


def test_status_exits_2_without_the_agents_cli(env, capsys):
    env.remove_cli()
    code, out, err = status(env, capsys)
    assert (code, out) == (2, "")
    assert err == "coven: claude not found on PATH\n"


def test_status_exits_2_when_agents_fails(env, capsys):
    env.fail_agents(3)
    code, out, err = status(env, capsys)
    assert (code, out) == (2, "")
    assert err.startswith("coven: agents exited 3")


def test_status_exits_2_when_agents_is_unreadable_json(env, capsys):
    env.rows_file.write_text("not json")
    code, _, err = status(env, capsys)
    assert code == 2
    assert err.startswith("coven: agents output is not JSON")


def test_status_exits_2_when_the_agents_cli_cannot_run(env, capsys):
    env.bin.joinpath("claude").write_text("#!/nonexistent/interpreter\n")
    env.bin.joinpath("claude").chmod(0o755)
    code, out, err = status(env, capsys)
    assert (code, out) == (2, "")
    assert err.startswith("coven: ")
    assert str(env.bin) not in err


def test_status_runs_the_agents_cli_once(env, capsys):
    env.set_rows(agents_row("pm-acme-1", 1), agents_row("pm-acme-2", 2))
    status(env, capsys, "--all")
    assert env.agents_runs == 1


# --- the view itself: fields the printed block does not carry --------------------------------


def view(env: PmEnv, builder: TranscriptBuilder, gm: str | None = None):
    path = env.transcript(builder)
    rows = parse_rows(env.rows_file.read_text())
    return build_view(PM, rows, read_transcript(path), Scene(NOW, gm, env.proc))


def test_known_gm_is_the_last_brief_or_note_naming_one(env):
    env.set_rows(agents_row(PM, 1))
    built = view(
        env,
        brief()
        .prompt("RESUMED by gm-new\nGM session: gm-new\n", at("10:00"))
        .turn_end(at("11:00")),
    )
    assert built.known_gm == "gm-new"


def test_known_gm_is_none_without_a_gm_session_line(env):
    env.set_rows(agents_row(PM, 1))
    built = view(env, TranscriptBuilder().prompt("a brief", at("09:00")).turn_end(at("11:00")))
    assert built.known_gm is None


def test_an_undelivered_message_is_unsent_until_the_gm_writes(env):
    env.set_rows(agents_row(PM, 1))
    failed = (
        brief()
        .peer(GM, "ANSWER acme-1/1: go", at("09:30"))
        .send(GM, "QUESTION acme-1/2: q", at("11:00"), delivered=False, error="gone")
        .turn_end(at("11:01"))
    )
    assert view(env, failed).unsent is True
    failed.peer(GM, "ANSWER acme-1/2: a", at("11:30"))
    assert view(env, failed).unsent is False


def test_a_delivered_message_is_not_unsent(env):
    env.set_rows(agents_row(PM, 1))
    built = view(
        env,
        brief()
        .peer(GM, "ANSWER acme-1/1: go", at("09:30"))
        .send(GM, "DONE acme-1/1: done", at("11:00"))
        .turn_end(at("11:01")),
    )
    assert built.unsent is False
    assert built.last_out_flag == "delivered"


def test_a_send_with_no_result_yet_is_reported_as_undelivered(env):
    env.set_rows(agents_row(PM, 1))
    built = view(
        env,
        brief()
        .peer(GM, "ANSWER acme-1/1: go", at("09:30"))
        .send(GM, "DONE acme-1/1: done", at("11:00"), delivered=None)
        .turn_end(at("11:01")),
    )
    assert built.unsent is True
    assert built.last_out_flag == "NOT DELIVERED: no result seen"


def test_skipped_counts_unparsable_lines(env):
    env.set_rows(agents_row(PM, 1))
    built = view(env, brief().raw('{"type": "assistant", x').turn_end(at("11:00")))
    assert built.skipped == 1


# --- a transcript this parser cannot read says unknown, never raises ------------------------------

UNKNOWN_ROW = "pm-acme-1  id=00000001  state=unknown (transcript unreadable)  since GM: -"


def test_garbled_transcript_gives_unknown_row(env, capsys):
    env.set_rows(agents_row(PM, 1, "idle"))
    (env.transcripts / f"{session_id(1)}.jsonl").write_bytes(
        b'\xff\xfe not json at all\n{"type":"assistant", broken\n\x00\x01\n'
    )
    code, out, err = status(env, capsys)
    assert (code, err) == (0, "")
    assert out.splitlines() == [UNKNOWN_ROW, ONE_LIVE]


def test_a_transcript_of_only_unknown_record_types_gives_unknown_row(env, capsys):
    one_pm(
        env,
        TranscriptBuilder()
        .raw('{"type": "brand-new", "assistant": {"turn_duration": 1}}')
        .raw('{"type": "also-new", "human": true}'),
    )
    _, out, _ = status(env, capsys)
    assert out.splitlines() == [UNKNOWN_ROW, ONE_LIVE]


def test_a_garbled_transcript_still_shows_a_dead_process_as_stale(env, capsys):
    env.set_rows(agents_row(PM, 1, "working", pid=4242))
    (env.transcripts / f"{session_id(1)}.jsonl").write_text("garbage\n", encoding="utf-8")
    _, out, _ = status(env, capsys)
    assert out.splitlines() == ["pm-acme-1  id=00000001  state=stale  since GM: -", NONE_LIVE]


def test_unknown_record_types_and_fields_do_not_change_the_block(env, capsys):
    noisy = (
        brief()
        .raw('{"type": "brand-new", "assistant": 1, "extra": {"a": [1, 2]}}')
        .peer(GM, "ANSWER acme-1/2: go", at("11:41"))
        .raw('{"type": "assistant", "novel": null, "message": {"content": [{"type": "x"}]}}')
        .send(GM, "DONE acme-1/2: done", at("11:45"))
        .turn_end(at("11:46"))
    )
    one_pm(env, noisy)
    _, out, _ = status(env, capsys)
    assert out.splitlines() == [
        "pm-acme-1  id=00000001  state=idle 14m  since GM: message",
        "  last out: 11:45 DONE acme-1/2: done   [delivered]",
        "  last in:  11:41 ANSWER acme-1/2: go  (from gm-main)",
        ONE_LIVE,
    ]


def test_an_agents_row_with_unknown_fields_is_listed(env, capsys):
    env.set_rows(agents_row(PM, 1, "idle", waitingFor="a permission", futureField=[1, {"a": 2}]))
    env.transcript(brief().turn_end(at("11:46")))
    _, out, _ = status(env, capsys)
    assert out.splitlines() == ["pm-acme-1  id=00000001  state=idle 14m  since GM: -", ONE_LIVE]


# --- where transcripts are read from --------------------------------------------------------------


def write_transcript_at(config: Path, builder: TranscriptBuilder) -> None:
    folder = config / "projects" / encode_cwd(CWD)
    folder.mkdir(parents=True)
    builder.write(folder / f"{session_id(1)}.jsonl")


def test_transcripts_under_claude_config_dir(env, capsys, monkeypatch, tmp_path):
    config = tmp_path / "elsewhere"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    write_transcript_at(config, brief().send(GM, "DONE acme-1/1: right one", at("11:30")))
    env.set_rows(agents_row(PM, 1, "idle"))
    env.transcript(brief().send(GM, "DONE acme-1/1: decoy under HOME", at("11:30")))
    _, out, _ = status(env, capsys)
    assert "right one" in out
    assert "decoy" not in out


def test_transcripts_default_to_dot_claude_in_home(env, capsys, monkeypatch):
    monkeypatch.delenv("CLAUDE_CONFIG_DIR")
    env.set_rows(agents_row(PM, 1, "idle"))
    env.transcript(brief().send(GM, "DONE acme-1/1: from dot claude", at("11:30")))
    # the autouse fixture put HOME under tmp_path: `~/.claude` is the folder `env` wrote to
    assert env.transcripts.is_relative_to(Path.home() / ".claude")
    _, out, _ = status(env, capsys)
    assert "from dot claude" in out


def test_the_transcript_is_found_whatever_the_folder_encoding(env, capsys):
    env.set_rows(agents_row(PM, 1, "idle"))
    other = env.transcripts.parent / "-some-other-encoding"
    other.mkdir()
    brief().send(GM, "DONE acme-1/1: found anyway", at("11:30")).write(
        other / f"{session_id(1)}.jsonl"
    )
    _, out, _ = status(env, capsys)
    assert "found anyway" in out


# --- arguments that end up in what the command prints --------------------------------------------


@pytest.mark.parametrize("argv", [("a;b",), ("--full", "A b"), ("--gm", "gm main")])
def test_unsafe_names_are_a_usage_error(env, capsys, argv):
    code, out, err = status(env, capsys, *argv)
    assert (code, out) == (2, "")
    assert "usage: coven pm status" in err
    assert env.agents_runs == 0


def test_help_names_the_pm_command(capsys):
    from coven import cli

    assert cli.main(["--help"]) == 0
    assert "pm" in capsys.readouterr().out


# --- one bad stamp, the count line ----------------------------------------------------------------


@pytest.mark.parametrize("stamp_text", ["0001-01-01T00:00:00+00:00", "9999-12-31T23:59:59-23:00"])
def test_a_timestamp_out_of_range_does_not_hide_the_listing(env, capsys, monkeypatch, stamp_text):
    monkeypatch.setenv("TZ", "America/New_York")
    time.tzset()
    env.set_rows(agents_row("pm-acme-1", 1, "idle"), agents_row("pm-acme-2", 2, "idle"))
    bad = (
        '{"type": "assistant", "timestamp": "' + stamp_text + '", "message": {"role": '
        '"assistant", "content": [{"type": "tool_use", "id": "t1", "name": "SendMessage", '
        '"input": {"to": "gm-main", "message": "DONE acme-1/1: done"}}]}}'
    )
    env.transcript(TranscriptBuilder().raw(bad).turn_end(at("11:46")), 1)
    env.transcript(brief().turn_end(at("11:46")), 2)
    code, out, err = status(env, capsys)
    assert (code, err) == (0, "")
    assert "  last out: ? DONE acme-1/1: done" in out
    assert "pm-acme-2  id=00000002  state=idle 14m" in out


def test_the_summary_counts_project_managers_and_live_ones(env, capsys):
    env.set_rows(
        agents_row("pm-acme-1", 1, "idle"),
        agents_row("pm-acme-2", 2, "working"),
        agents_row("pm-acme-3", 3, "blocked"),
        agents_row("pm-acme-4", 4, "stopped"),
        agents_row("pm-acme-5", 5, "failed"),
    )
    _, out, _ = status(env, capsys)
    assert out.splitlines()[-1] == "5 project managers, 3 live"


def test_a_duplicate_counts_as_live_and_a_stale_pm_does_not(env, capsys):
    env.set_rows(
        agents_row("pm-acme-1", 1, "idle"),
        agents_row("pm-acme-1", 2, "working"),
        agents_row("pm-acme-2", 3, "working", pid=4242),  # no such process: stale
    )
    _, out, _ = status(env, capsys)
    assert out.splitlines()[-1] == "2 project managers, 1 live"


def test_the_summary_counts_only_the_project_managers_shown(env, capsys):
    env.set_rows(agents_row("pm-acme-1", 1, "idle"), agents_row("pm-acme-2", 2, "idle"))
    _, out, _ = status(env, capsys, "acme-2")
    assert out.splitlines()[-1] == "1 project manager, 1 live"
