"""Reading a project-manager transcript: what it sent, what it received, how it ended."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from pm.fixtures import CWD, SESSION_ID, TranscriptBuilder, stamp

from coven.pm.text import Head
from coven.pm.transcript import find_transcript, read_transcript

T0 = datetime(2026, 10, 28, 11, 0, tzinfo=UTC)
T1 = T0 + timedelta(minutes=5)
T2 = T0 + timedelta(minutes=10)


def test_failed_send_keeps_its_reason(tmp_path):
    path = (
        TranscriptBuilder()
        .send(
            "gm-old",
            "BLOCKED acme-1/1: x",
            T0,
            delivered=False,
            error="No agent named gm-old is reachable\nsecond line",
        )
        .write(tmp_path / "t.jsonl")
    )
    facts = read_transcript(path)
    assert facts.sent[-1].delivered is False
    assert facts.sent[-1].reason == "No agent named gm-old is reachable"


def test_send_flagged_as_error_is_not_delivered(tmp_path):
    path = (
        TranscriptBuilder()
        .send_flagged("gm-old", "DONE acme-1/1: x", T0, error="gone")
        .write(tmp_path / "t.jsonl")
    )
    facts = read_transcript(path)
    assert facts.sent[-1].delivered is False
    assert facts.sent[-1].reason == "gone"


def test_delivered_send_has_no_reason(tmp_path):
    facts = read_transcript(
        TranscriptBuilder().send("gm-main", "DONE acme-1/1: a", T0).write(tmp_path / "t.jsonl")
    )
    assert facts.sent[0].delivered is True
    assert facts.sent[0].reason is None


def test_send_without_a_result_yet_is_unknown(tmp_path):
    path = (
        TranscriptBuilder()
        .send("gm-main", "QUESTION acme-1/1: which?", T0, delivered=None)
        .write(tmp_path / "t.jsonl")
    )
    facts = read_transcript(path)
    assert facts.sent[0].delivered is None
    assert facts.sent[0].message.head == Head("QUESTION", "acme-1/1")
    assert facts.sent[0].message.at == T0


def test_final_text_without_a_send(tmp_path):
    path = (
        TranscriptBuilder()
        .send("gm-main", "DONE acme-1/1: a", T0)
        .text("BLOCKED acme-1/2: b", T1)
        .write(tmp_path / "t.jsonl")
    )
    facts = read_transcript(path)
    assert facts.last_text.head == Head("BLOCKED", "acme-1/2")
    assert facts.last_text_after_send


def test_text_followed_by_a_send_is_not_after_the_send(tmp_path):
    path = (
        TranscriptBuilder()
        .text("BLOCKED acme-1/2: b", T0)
        .send("gm-main", "BLOCKED acme-1/2: b", T1)
        .write(tmp_path / "t.jsonl")
    )
    facts = read_transcript(path)
    assert facts.last_text.text == "BLOCKED acme-1/2: b"
    assert not facts.last_text_after_send


def test_no_text_means_no_last_text(tmp_path):
    facts = read_transcript(TranscriptBuilder().turn_end(T0).write(tmp_path / "t.jsonl"))
    assert facts.last_text is None
    assert not facts.last_text_after_send


def test_resumed_note_replies_count_as_received(tmp_path):
    note = "RESUMED by gm-new\nGM session: gm-new\nANSWER acme-1/2: yes\nTASK acme-1/3: more"
    facts = read_transcript(TranscriptBuilder().prompt(note, T1).write(tmp_path / "t.jsonl"))
    assert [(r.sender, r.via, r.message.head.prefix) for r in facts.received] == [
        ("gm-new", "note", "ANSWER"),
        ("gm-new", "note", "TASK"),
    ]
    assert facts.received[0].message.text == "ANSWER acme-1/2: yes"
    assert facts.received[0].message.at == T1
    assert facts.prompts[-1].gm_session == "gm-new"


def test_a_note_after_the_role_body_is_read_from_its_resumed_line_on(tmp_path):
    prompt = (
        "You are a project manager.\nGM session: gm-body\nANSWER acme-1/1: from the body\n\n"
        "RESUMED by gm-new\nGM session: gm-new\n\nANSWER acme-1/2: go ahead\n"
    )
    facts = read_transcript(TranscriptBuilder().prompt(prompt, T1).write(tmp_path / "t.jsonl"))
    assert [(r.sender, r.via, r.message.text) for r in facts.received] == [
        ("gm-new", "note", "ANSWER acme-1/2: go ahead")
    ]
    assert facts.prompts[-1].gm_session == "gm-new"


def test_first_brief_is_a_prompt_but_not_a_received_message(tmp_path):
    brief = "Project: acme-1 - x\n\nGM session: gm-main\nANSWER acme-1/1: not a reply\nTasks:"
    facts = read_transcript(TranscriptBuilder().prompt(brief, T0).write(tmp_path / "t.jsonl"))
    assert facts.received == []
    assert facts.prompts[0].gm_session == "gm-main"
    assert facts.prompts[0].at == T0


def test_prompt_without_a_gm_session_line(tmp_path):
    facts = read_transcript(TranscriptBuilder().prompt("hello", T0).write(tmp_path / "t.jsonl"))
    assert facts.prompts[0].gm_session is None


def test_peer_message_is_received_with_its_sender(tmp_path):
    path = (
        TranscriptBuilder().peer("gm-main", "APPROVED acme-1/1: go", T1).write(tmp_path / "t.jsonl")
    )
    received = read_transcript(path).received[0]
    assert (received.sender, received.via, received.message.text) == (
        "gm-main",
        "peer",
        "APPROVED acme-1/1: go",
    )
    assert received.message.head == Head("APPROVED", "acme-1/1")


def test_cut_last_line_is_skipped(tmp_path):
    path = (
        TranscriptBuilder()
        .send("gm-main", "DONE acme-1/1: a", T0)
        .raw('{"type":"assistant","mess')
        .write(tmp_path / "t.jsonl")
    )
    facts = read_transcript(path)
    assert facts.skipped == 1
    assert len(facts.sent) == 1


@pytest.mark.parametrize(
    "line",
    [
        '{"type": "assistant", "n": ' + "9" * 5000 + "}",
        '{"type": "assistant", "n": ' + "[" * 100_000 + "]" * 100_000 + "}",
    ],
    ids=["huge-integer", "deep-nesting"],
)
def test_a_line_json_cannot_decode_safely_is_skipped(tmp_path, line):
    path = (
        TranscriptBuilder()
        .raw(line)
        .send("gm-main", "DONE acme-1/1: a", T0)
        .write(tmp_path / "t.jsonl")
    )
    facts = read_transcript(path)
    assert facts.skipped == 1
    assert len(facts.sent) == 1


def test_a_json_line_that_is_not_an_object_is_skipped(tmp_path):
    path = TranscriptBuilder().raw('["assistant"]').write(tmp_path / "t.jsonl")
    assert read_transcript(path).skipped == 1


def test_noise_entries_change_nothing(tmp_path):
    path = (
        TranscriptBuilder()
        .tool_result_other("ok", T0)
        .idle_subscription(T0)
        .thinking("hmm", T0)
        .raw('{"type":"attachment"}')
        .write(tmp_path / "t.jsonl")
    )
    facts = read_transcript(path)
    assert facts.sent == []
    assert facts.received == []
    assert facts.prompts == []
    assert facts.last_text is None
    assert facts.turn_ends == []
    assert facts.assistant_entries == 1
    assert facts.skipped == 0


def test_turn_ends_and_assistant_entries_are_collected(tmp_path):
    path = (
        TranscriptBuilder()
        .text("working", T0)
        .turn_end(T1)
        .send("gm-main", "DONE acme-1/1: a", T1)
        .turn_end(T2)
        .write(tmp_path / "t.jsonl")
    )
    facts = read_transcript(path)
    assert facts.turn_ends == [T1, T2]
    assert facts.assistant_entries == 2
    assert facts.last_entry_at == T2


def test_agent_names_and_cwd(tmp_path):
    path = (
        TranscriptBuilder()
        .agent_name("pm-acme-1")
        .agent_name("pm-acme-renamed")
        .text("hi", T0)
        .write(tmp_path / "t.jsonl")
    )
    facts = read_transcript(path)
    assert facts.agent_names == {"pm-acme-1", "pm-acme-renamed"}
    assert facts.cwd == CWD


def test_cwd_is_none_when_no_entry_names_one(tmp_path):
    path = tmp_path / "t.jsonl"
    path.write_text('{"type":"agent-name","agentName":"pm-acme-1"}\n', encoding="utf-8")
    assert read_transcript(path).cwd is None


def test_entry_without_a_timestamp_keeps_the_last_seen_time(tmp_path):
    path = (
        TranscriptBuilder()
        .text("first", T1)
        .send("gm-main", "DONE acme-1/1: a", None)
        .write(tmp_path / "t.jsonl")
    )
    assert read_transcript(path).sent[0].message.at == T1


def test_empty_file_gives_empty_facts(tmp_path):
    path = tmp_path / "t.jsonl"
    path.write_text("", encoding="utf-8")
    facts = read_transcript(path)
    assert facts.last_entry_at is None
    assert facts.skipped == 0
    assert facts.sent == []


def test_find_transcript_looks_in_every_project_folder(tmp_path):
    target = tmp_path / "-work-repo-1" / f"{SESSION_ID}.jsonl"
    target.parent.mkdir()
    target.write_text("{}\n", encoding="utf-8")
    (tmp_path / "-other").mkdir()
    assert find_transcript(tmp_path, SESSION_ID) == target


def test_find_transcript_returns_none_when_absent(tmp_path):
    (tmp_path / "-work-repo-1").mkdir()
    assert find_transcript(tmp_path, SESSION_ID) is None


@pytest.mark.parametrize(
    "session_id",
    [
        "*",
        "../x",
        "",
        "0000000A-0000-4000-8000-00000000000B",
        SESSION_ID.replace("-", ""),
        f"{SESSION_ID}/..",
    ],
    ids=["glob", "traversal", "empty", "upper-case", "no-dashes", "suffix"],
)
def test_non_uuid_session_id_is_refused(tmp_path, session_id):
    with pytest.raises(ValueError, match="session id"):
        find_transcript(tmp_path, session_id)


def test_unknown_record_types_ignored(tmp_path):
    """Entries from a newer Claude Code that this parser does not know change nothing."""
    path = (
        TranscriptBuilder()
        .raw(json.dumps({"type": "future-record", "note": "assistant peer human", "n": [1, {}]}))
        .raw(
            json.dumps(
                {
                    "type": "assistant",
                    "timestamp": stamp(T0),
                    "newField": {"deep": [1, 2]},
                    "message": {
                        "role": "assistant",
                        "extra": True,
                        "content": [
                            {"type": "hologram", "payload": 1},
                            {"type": "text", "text": "BLOCKED acme-1/2: b", "citations": []},
                        ],
                    },
                }
            )
        )
        .raw(
            json.dumps(
                {
                    "type": "user",
                    "origin": {"kind": "telepathy", "name": "x", "body": "ANSWER acme-1/2: no"},
                    "message": {"content": [{"type": "quantum", "human": 1}]},
                }
            )
        )
        .raw(json.dumps({"type": "system", "subtype": "turn_duration_v2", "at": "turn_duration"}))
        .send("gm-main", "DONE acme-1/1: a", T1)
        .write(tmp_path / "t.jsonl")
    )
    facts = read_transcript(path)
    assert facts.skipped == 0
    assert [s.message.text for s in facts.sent] == ["DONE acme-1/1: a"]
    assert facts.received == []
    assert facts.prompts == []
    assert facts.turn_ends == []
    assert facts.last_text is not None
    assert facts.last_text.head == Head("BLOCKED", "acme-1/2")
    assert facts.recognized > 0


def test_a_transcript_of_nothing_but_unknown_records_recognizes_none(tmp_path):
    path = (
        TranscriptBuilder()
        .raw(json.dumps({"type": "future-record", "note": "assistant"}))
        .raw(json.dumps({"type": 7, "assistant": None}))
        .write(tmp_path / "t.jsonl")
    )
    facts = read_transcript(path)
    assert (facts.lines, facts.recognized, facts.skipped) == (2, 0, 0)


def test_binary_garbage_never_raises(tmp_path):
    path = tmp_path / "t.jsonl"
    path.write_bytes(b'\xff\xfe\x00\x01 assistant \x80\n{"type":"assistant"\n\x00\n')
    facts = read_transcript(path)
    assert facts.recognized == 0
    assert facts.lines == 3
    assert facts.skipped == 1


def test_blank_lines_are_not_counted(tmp_path):
    path = tmp_path / "t.jsonl"
    path.write_text("\n\n  \n", encoding="utf-8")
    assert read_transcript(path).lines == 0
