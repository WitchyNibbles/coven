# coven pm status: absorbed GM messages Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use coven:subagent-driven-development to implement
> this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `coven pm status` counts a GM message the PM absorbed mid-turn as received, so `last in`
and `waits on GM` stop showing a question the GM already answered.

**Architecture:** `coven.pm.transcript._Reader` learns one more entry shape: an `attachment` of type
`queued_command` whose `origin.kind` is `peer`. It reads it the way it reads a peer `user` entry.
Received messages are de-duplicated by `origin.msg_id`. `report.py`, `health.py` and aquelarre
are untouched.

**Tech Stack:** Python 3.12+, pytest, `python3 .coven/check.py`.

**Spec:** `docs/specs/2026-11-25-pm-status-absorbed-messages-design.md`

## Global Constraints

- Fixtures are synthetic: no text, ids, paths or names copied from a real transcript. Use the
  existing `gm-main` / `pm-acme-1` / `acme-1/N` names.
- The `queue-operation` entry is never counted.
- No parsing of the `<cross-session-message>` wrapper text.
- No new dependency. No suppression, no weakened lint or type rule.
- Commits carry no `Co-Authored-By` trailer and no "Generated with" line.

## Review Focus

1. Same message twice (an absorbed attachment and a peer `user` entry with the same `msg_id`):
   counted once. Test in Task 1.
2. A `queued_command` attachment from a person (`origin.kind == "human"`) or a task notification:
   not received. Test in Task 1.
3. An attachment stamped earlier than the entry above it: `last_entry_at` keeps the later time.
   Test in Task 1.
4. A peer attachment without `origin.name` or `origin.body` (strings): ignored, no error. Test in
   Task 1.
5. End to end: the reported bug (QUESTION sent, GM answer absorbed mid-turn) no longer shows
   `waits on GM`, and `last in` is the answer. Test in Task 1.

---

### Task 1: Read absorbed peer messages

**Files:**
- Modify: `src/coven/pm/transcript.py` (`_Reader._read`, `_Reader._peer`, `_Reader._advance`,
  module docstring)
- Modify: `tests/pm/fixtures.py` (`TranscriptBuilder`: new `absorbed_peer`, `msg_id` on `peer`)
- Test: `tests/pm/test_transcript.py`, `tests/pm/test_status.py`

**Interfaces:**
- Produces, in `tests/pm/fixtures.py`:
  - `TranscriptBuilder.peer(sender: str, body: str, at: datetime | None, msg_id: str | None = None) -> Self`:
    as today; when `msg_id` is given, `origin` also has `"msg_id": msg_id`.
  - `TranscriptBuilder.absorbed_peer(sender: str, body: str, at: datetime | None, msg_id: str | None = None, kind: str = "peer") -> Self`:
    writes the two-entry shape from the spec: first a `queue-operation` (`operation` `remove`,
    `reason` `absorbed_mid_turn`, `content` the wrapper with `from="uds:/tmp/fake.sock"`,
    `from-name="<sender>"`, `from-mode="bypass"`), then an `attachment` entry with
    `attachment = {"type": "queued_command", "prompt": <wrapper>, "commandMode": "prompt",
    "origin": {"kind": kind, "from": "uds:/tmp/fake.sock", "name": sender, "fromMode": "bypass",
    "body": body} (+ "msg_id" when given)}`. Both entries stamped `at`.
- Produces, in `src/coven/pm/transcript.py`: no new public name. `Received.via` stays
  `"peer"` for an absorbed message.

- [ ] **Step 1: Write the failing transcript tests** (in `tests/pm/test_transcript.py`)

```python
def test_absorbed_peer_message_is_received_with_its_sender(tmp_path):
    path = TranscriptBuilder().absorbed_peer("gm-main", "ANSWER acme-1/2: b", T1).write(tmp_path / "t.jsonl")
    facts = read_transcript(path)
    assert [(r.sender, r.via, r.message.text, r.message.at) for r in facts.received] == [
        ("gm-main", "peer", "ANSWER acme-1/2: b", T1)
    ]
    assert facts.received[0].message.head == Head("ANSWER", "acme-1/2")


def test_same_msg_id_absorbed_and_delivered_counts_once(tmp_path):
    path = (
        TranscriptBuilder()
        .absorbed_peer("gm-main", "ANSWER acme-1/2: b", T1, msg_id="m-1")
        .peer("gm-main", "ANSWER acme-1/2: b", T2, msg_id="m-1")
        .write(tmp_path / "t.jsonl")
    )
    assert [r.message.at for r in read_transcript(path).received] == [T1]


def test_distinct_messages_without_msg_id_all_count(tmp_path):
    path = (
        TranscriptBuilder()
        .peer("gm-main", "ANSWER acme-1/1: a", T0)
        .absorbed_peer("gm-main", "ANSWER acme-1/2: b", T1)
        .peer("gm-main", "APPROVED acme-1/3: c", T2)
        .write(tmp_path / "t.jsonl")
    )
    assert [r.message.text for r in read_transcript(path).received] == [
        "ANSWER acme-1/1: a", "ANSWER acme-1/2: b", "APPROVED acme-1/3: c"
    ]


@pytest.mark.parametrize("kind", ["human", "task-notification"])
def test_absorbed_command_that_is_not_a_peer_message_is_not_received(tmp_path, kind):
    path = TranscriptBuilder().absorbed_peer("gm-main", "ANSWER acme-1/2: b", T1, kind=kind).write(tmp_path / "t.jsonl")
    assert read_transcript(path).received == []


def test_absorbed_peer_without_name_or_body_is_ignored(tmp_path):
    path = ...  # see the note below
    facts = read_transcript(path)
    assert facts.received == [] and facts.skipped == 0


def test_an_earlier_stamped_attachment_does_not_move_the_last_entry_back(tmp_path):
    path = (
        TranscriptBuilder()
        .text("working", T2)
        .absorbed_peer("gm-main", "ANSWER acme-1/2: b", T1)
        .write(tmp_path / "t.jsonl")
    )
    facts = read_transcript(path)
    assert facts.last_entry_at == T2
    assert facts.received[0].message.at == T1
```

`test_absorbed_peer_without_name_or_body_is_ignored` builds its two entries with `.raw(json.dumps(...))`
in the attachment shape above: one whose `origin` has no `name`, one whose `origin.body` is `3`.

- [ ] **Step 2: Write the failing status test** (in `tests/pm/test_status.py`, next to
  `test_idle_notice_after_the_gm_answered_says_no`; same helpers)

`test_an_answer_absorbed_mid_turn_ends_the_wait`: brief, `send(GM, "QUESTION acme-1/3: which one?",
at("10:02"))`, `turn_end(at("10:03"))`, `absorbed_peer(GM, "ANSWER acme-1/3: b", at("11:39"))`,
`text("on it", at("11:40"))`, row state `working`. Assert the output lines contain
`"  last in:  11:39 ANSWER acme-1/3: b  (from gm-main)"` and no line starts with
`"  waits on GM:"`. Pin the full `out.splitlines()` once you see it, as the neighbouring tests do.

- [ ] **Step 3: Run them to see them fail**

Run: `python3 .coven/check.py fast tests/pm/test_transcript.py tests/pm/test_status.py`
Expected: the new tests FAIL (`absorbed_peer` missing, then `received == []`); old tests pass.

- [ ] **Step 4: Implement in `src/coven/pm/transcript.py`**

- `_read`: a `case "attachment"` where `attachment.type == "queued_command"` and
  `attachment.origin.kind == "peer"`: count it in `recognized` and pass the origin to `_peer`.
- `_peer`: skip a message whose `origin.msg_id` (a string) was already counted; keep the seen ids
  in a `set[str]` on the reader.
- `_advance`: `last_entry_at` is the latest timestamp seen, not the last one; the received
  message's time is still the entry's own timestamp.
- Module docstring: name the two peer shapes (turn boundary `user` entry, mid-turn
  `queued_command` attachment) and that the paired `queue-operation` is ignored on purpose.
- `MARKERS` already lets the attachment line through (it holds `"peer"`); do not add markers.

- [ ] **Step 5: Run the tests to see them pass**

Run: `python3 .coven/check.py fast tests/pm`
Expected: all pass, receipt line printed.

- [ ] **Step 6: Commit**

```bash
git add src/coven/pm/transcript.py tests/pm/fixtures.py tests/pm/test_transcript.py tests/pm/test_status.py
git commit -m "fix(pm): count GM messages absorbed mid-turn as received"
```
