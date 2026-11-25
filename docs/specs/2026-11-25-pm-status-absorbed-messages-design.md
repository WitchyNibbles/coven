# coven pm status: count GM messages a PM absorbed mid-turn

## Problem

`coven pm status` (and so `coven pm health` and the aquelarre pane) said a PM "waits on GM:
QUESTION x/21 since 11:37" when the GM had answered at 11:39 and the PM was already working on
the answer.

`coven.pm.transcript` only counts a received message from a `user` entry whose
`origin.kind == "peer"`. That is how Claude Code records a cross-session message delivered at a
turn boundary. A message that arrives while the PM is mid-turn is recorded differently, and the
reader skips it.

## The shapes (Claude Code 2.1.331-2.1.332, checked on this machine read-only)

A message delivered at a turn boundary is one entry:

```json
{"type": "user", "timestamp": "...", "origin": {"kind": "peer", "from": "uds:...", "msg_id": "...", "name": "<sender>", "fromMode": "...", "body": "<text>"}, "message": {"role": "user", "content": "<cross-session-message ...>...</cross-session-message>"}}
```

A message absorbed mid-turn is two entries, written next to each other:

```json
{"type": "queue-operation", "operation": "remove", "reason": "absorbed_mid_turn", "timestamp": "<when it was taken in>", "content": "<cross-session-message from=\"uds:...\" from-name=\"<sender>\" from-mode=\"...\">\n<text>\n</cross-session-message>"}
{"type": "attachment", "timestamp": "<when it arrived>", "attachment": {"type": "queued_command", "prompt": "<cross-session-message ...>", "commandMode": "prompt", "origin": {"kind": "peer", "from": "uds:...", "msg_id": "...", "name": "<sender>", "fromMode": "...", "body": "<text>"}, "timestamp": "..."}}
```

The attachment's `origin` has the same fields as the `user` entry's. Its timestamp is the arrival
time and can be a few seconds earlier than the entry written just before it.

Counts across every transcript under `~/.claude/projects` on this machine:

- 103 `queued_command` attachments wrap a `<cross-session-message>`; all 103 carry
  `origin.kind == "peer"`.
- Every `absorbed_mid_turn` removal of a cross-session message in the PM transcript checked sits
  one or two lines before its attachment, one to one (8 of 8).
- No message showed up both as a peer `user` entry and as an attachment.
- Other `queued_command` attachments carry `origin.kind` `human` (a person typing mid-turn) or
  `task-notification`; `queue-operation` removals with reason `delivered_to_agent` are all task
  notifications. None of these are GM messages.

## Decision

- Count the attachment: `type == "attachment"`, `attachment.type == "queued_command"`,
  `attachment.origin.kind == "peer"`. Sender from `origin.name`, body from `origin.body`, time
  from the entry's `timestamp`, `via == "peer"`: the same reading as the turn-boundary entry.
- Ignore the `queue-operation` entry. It has no structured origin (the sender would come from
  parsing the `from-name` attribute out of the wrapper text) and it always comes paired with the
  attachment, so counting both would double-count.
- No parsing of the `<cross-session-message>` wrapper: `origin.name` and `origin.body` are the
  structured form of the same values.
- De-duplicate by `origin.msg_id`: a message whose `msg_id` was already counted is not counted
  again, whichever entry carried it. Not seen today, but it keeps a turn-boundary delivery and an
  absorbed one from ever counting twice if Claude Code starts writing both. Entries without a
  `msg_id` are counted as before.
- `Facts.last_entry_at` never moves back: an attachment stamped before the entry above it does
  not make a working PM look older (it feeds the stale check).
- A `human` or `task-notification` queued command stays ignored.

## Consumers

- `report.py` (`last in`, `since GM`, `waits on GM`) and `health.py` read `Facts.received`; no
  change needed.
- aquelarre parses the `coven pm status` text only (`last in:`, `waits on GM:`); it needs no
  change.

## Not in scope

- `since GM: no` still means "no turn has ended since the GM's last message". For an absorbed
  message the PM has read it but may still be mid-turn; that word stays as it is.
- `coven.claude_version.LAST_TESTED` (2.1.325) is not bumped here.
