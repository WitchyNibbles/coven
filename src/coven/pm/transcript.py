"""Reading a project-manager session's transcript: the messages it sent and received, and how it ended.

A transcript is one JSON object per line. The entry shapes this reads are internals of the
Claude Code transcript format, not a public one, last checked against the version named in
`coven.claude_version.LAST_TESTED`. A line that is not JSON, a record type or a field this does
not know is skipped or ignored, never an error: `Facts` counts what was read so that the caller can
say `unknown` when nothing was.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from coven.pm.text import GM_PREFIXES, UNKNOWN_KEY, Head, clean_line, parse_head

SEND_TOOL = "SendMessage"
RESUME_NOTE_LINE = re.compile(r"^RESUMED by ", re.MULTILINE)
# A line is parsed only when it holds one of these (or the id of a send still waiting for its
# result): the rest is attachments and API noise, and much of it is large.
MARKERS = ('"assistant"', '"peer"', '"human"', "turn_duration", "agent-name")
GM_SESSION_LINE = re.compile(r"^GM session: (\S+)", re.MULTILINE)
EPOCH = datetime.fromtimestamp(0, tz=UTC)

type Entry = dict[str, object]


@dataclass(frozen=True)
class Message:
    at: datetime
    text: str
    head: Head | None


@dataclass(frozen=True)
class Sent:
    message: Message
    to: str
    delivered: bool | None  # None: no result seen yet
    reason: str | None


@dataclass(frozen=True)
class Received:
    message: Message
    sender: str
    via: Literal["peer", "note"]


@dataclass(frozen=True)
class Prompt:
    """A human prompt: the brief, or the note a resume adds."""

    at: datetime
    gm_session: str | None


@dataclass
class Facts:
    agent_names: set[str] = field(default_factory=set)
    cwd: str | None = None
    prompts: list[Prompt] = field(default_factory=list)
    sent: list[Sent] = field(default_factory=list)
    received: list[Received] = field(default_factory=list)
    last_text: Message | None = None
    last_text_after_send: bool = False
    turn_ends: list[datetime] = field(default_factory=list)
    assistant_entries: int = 0
    last_entry_at: datetime | None = None
    skipped: int = 0
    lines: int = 0  # non-blank lines in the file
    recognized: int = 0  # entries of a type this reader acts on


def find_transcript(projects_dir: Path, session_id: str) -> Path | None:
    """The transcript of `session_id` under any project folder, the newest if several.

    Raises `ValueError` before touching the disk unless `session_id` is a lower-case UUID, so it
    cannot widen the glob.
    """
    try:
        valid = str(uuid.UUID(session_id)) == session_id
    except ValueError:
        valid = False
    if not valid:
        raise ValueError(f"not a session id: {session_id!r}")
    matches = projects_dir.glob(f"*/{session_id}.jsonl")
    return max(matches, key=lambda path: path.stat().st_mtime, default=None)


def read_transcript(path: Path) -> Facts:
    """What the session did, in one pass over `path`."""
    reader = _Reader()
    # shortcut: no size cap on a transcript line, add one if a real line ever exceeds ~10 MB.
    with path.open(encoding="utf-8", errors="replace") as lines:
        for line in lines:
            reader.feed(line)
    return reader.facts


class _Reader:
    def __init__(self) -> None:
        self.facts = Facts()
        self._now = EPOCH
        self._waiting: dict[str, int] = {}  # send tool_use id -> index in facts.sent

    def feed(self, line: str) -> None:
        if not line.strip():
            return
        self.facts.lines += 1
        if not self._wanted(line):
            return
        try:
            entry = json.loads(line)
        except (ValueError, RecursionError):  # bad JSON, a huge integer, or nesting too deep
            self.facts.skipped += 1
            return
        if not isinstance(entry, dict):
            self.facts.skipped += 1
            return
        self._read(entry)

    def _wanted(self, line: str) -> bool:
        return any(marker in line for marker in MARKERS) or any(
            tool_id in line for tool_id in self._waiting
        )

    def _read(self, entry: Entry) -> None:
        at = self._advance(entry)
        cwd = entry.get("cwd")
        if self.facts.cwd is None and isinstance(cwd, str):
            self.facts.cwd = cwd
        match entry.get("type"):
            case "assistant":
                self.facts.recognized += 1
                self._assistant(entry, at)
            case "user":
                self.facts.recognized += 1
                self._user(entry, at)
            case "system" if entry.get("subtype") == "turn_duration":
                self.facts.recognized += 1
                self.facts.turn_ends.append(at)
            case "agent-name" if isinstance(name := entry.get("agentName"), str):
                self.facts.recognized += 1
                self.facts.agent_names.add(name)

    def _advance(self, entry: Entry) -> datetime:
        stamped = _parse_time(entry.get("timestamp"))
        if stamped is not None:
            self._now = stamped
            self.facts.last_entry_at = stamped
        return self._now

    def _assistant(self, entry: Entry, at: datetime) -> None:
        self.facts.assistant_entries += 1
        for item in _content_items(entry):
            text = item.get("text")
            if item.get("type") == "text" and isinstance(text, str):
                self.facts.last_text = _message(at, text)
                self.facts.last_text_after_send = True
            elif item.get("type") == "tool_use" and item.get("name") == SEND_TOOL:
                self._send(item, at)

    def _send(self, call: Entry, at: datetime) -> None:
        args = _as_entry(call.get("input"))
        to, text = args.get("to"), args.get("message")
        if not isinstance(to, str) or not isinstance(text, str):
            return
        self.facts.sent.append(Sent(_message(at, text), to, delivered=None, reason=None))
        self.facts.last_text_after_send = False
        tool_id = call.get("id")
        if isinstance(tool_id, str):
            self._waiting[tool_id] = len(self.facts.sent) - 1

    def _user(self, entry: Entry, at: datetime) -> None:
        origin = _as_entry(entry.get("origin"))
        match origin.get("kind"):
            case "peer":
                self._peer(origin, at)
            case "human":
                self._prompt(_prompt_text(entry), at)
            case _:
                for item in _content_items(entry):
                    if item.get("type") == "tool_result":
                        self._result(item, entry)

    def _peer(self, origin: Entry, at: datetime) -> None:
        sender, body = origin.get("name"), origin.get("body")
        if isinstance(sender, str) and isinstance(body, str):
            self.facts.received.append(Received(_message(at, body), sender, "peer"))

    def _prompt(self, text: str, at: datetime) -> None:
        # A resume prompt is the role body, then the note: only the note speaks for the GM.
        note = RESUME_NOTE_LINE.search(text)
        scope = text[note.start() :] if note else text
        found = GM_SESSION_LINE.search(scope)
        gm_session = found.group(1) if found else None
        self.facts.prompts.append(Prompt(at, gm_session))
        if note is None:
            return
        for line in scope.splitlines():
            head = parse_head(line)
            if head is not None and head.prefix in GM_PREFIXES:
                reply = Received(_message(at, line.strip()), gm_session or UNKNOWN_KEY, "note")
                self.facts.received.append(reply)

    def _result(self, item: Entry, entry: Entry) -> None:
        tool_id = item.get("tool_use_id")
        if not isinstance(tool_id, str) or tool_id not in self._waiting:
            return
        index = self._waiting.pop(tool_id)
        success = _as_entry(entry.get("toolUseResult")).get("success")
        if success is False or item.get("is_error") is True:
            reason = clean_line(_result_text(item)) or None
            self.facts.sent[index] = replace(self.facts.sent[index], delivered=False, reason=reason)
        elif success is True:
            self.facts.sent[index] = replace(self.facts.sent[index], delivered=True)


def _message(at: datetime, text: str) -> Message:
    return Message(at, text, parse_head(text))


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _as_entry(value: object) -> Entry:
    return value if isinstance(value, dict) else {}


def _content_items(entry: Entry) -> list[Entry]:
    content = _as_entry(entry.get("message")).get("content")
    if not isinstance(content, list):
        return []
    return [item for item in content if isinstance(item, dict)]


def _prompt_text(entry: Entry) -> str:
    content = _as_entry(entry.get("message")).get("content")
    if isinstance(content, str):
        return content
    texts = (item.get("text") for item in _content_items(entry))
    return "\n".join(text for text in texts if isinstance(text, str))


def _result_text(item: Entry) -> str:
    content = item.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    texts = (part.get("text") for part in content if isinstance(part, dict))
    return "\n".join(text for text in texts if isinstance(text, str))
