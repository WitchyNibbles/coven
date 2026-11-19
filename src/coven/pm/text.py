"""Printing what a project-manager session wrote: one safe line, or a safe block, and its prefix."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

LINE_LIMIT = 200
PM_PREFIXES = ("QUESTION", "PROD-STEP", "DRAFT", "DONE", "BLOCKED")
GM_PREFIXES = ("ANSWER", "APPROVED", "DENIED", "TASK")
UNKNOWN_KEY = "?"
ELLIPSIS = "…"

KEY = re.compile(r"[a-z0-9][a-z0-9-]*/[A-Za-z0-9][A-Za-z0-9-]*")
TASK = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*")
JOINERS = frozenset({"and", "&", "+", ","})
ESCAPES = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b.", re.DOTALL)
SPACES = re.compile(r" {2,}")
# Control and format characters (bidi overrides among them) and the Unicode line and paragraph
# separators: none may reach the terminal, and `\n` is the only line break a block keeps.
DROPPED_CATEGORIES = frozenset({"Cc", "Cf", "Zl", "Zp"})
# Names that are printed and may end up inside a command the GM is told to run.
PROJECT_CODE = re.compile(r"[a-z0-9][a-z0-9-]*")
NOT_ASCII_WORD_CHARS = re.compile(r"[^A-Za-z0-9-]")
ROW_ID = re.compile(r"[0-9a-f]{8}")
GM_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


@dataclass(frozen=True)
class Head:
    """The first words of a message: its prefix and the task key it names (`?` when unusable).

    A message may name more tasks (`DONE a/1 and a/2`): `others` holds the keys after the first.
    """

    prefix: str
    key: str
    others: tuple[str, ...] = ()

    @property
    def keys(self) -> tuple[str, ...]:
        return (self.key, *self.others)


def clean_block(text: str) -> str:
    """`text` without escape sequences, control or format characters; newlines stay."""
    plain = ESCAPES.sub("", text)
    return "".join(
        c for c in plain if c == "\n" or unicodedata.category(c) not in DROPPED_CATEGORIES
    )


def clean_line(text: str, limit: int = LINE_LIMIT) -> str:
    """The first line of `text`, cleaned, single-spaced and cut to `limit` characters."""
    first = clean_block(text.split("\n", 1)[0])
    line = SPACES.sub(" ", first).strip()
    if len(line) <= limit:
        return line
    return line[: limit - 1] + ELLIPSIS


def parse_head(text: str) -> Head | None:
    """The prefix and keys of a message's first line; `None` unless it starts with a known prefix."""
    words = text.split("\n", 1)[0].split()
    if not words or words[0] not in PM_PREFIXES + GM_PREFIXES:
        return None
    first, *others = _keys(words[1:])
    return Head(words[0], first, tuple(others))


def _keys(words: list[str]) -> list[str]:
    """The task keys at the start of `words`: `a/1`, then more after `and`, `,`, `&` or `+`.

    A bare task (`a/1 and 2`) takes the first key's project. The list ends at a word with a colon
    or at the first word that is not joined to the list.
    """
    if not words or KEY.fullmatch(words[0].rstrip(":,")) is None:
        return [UNKNOWN_KEY]
    keys = [words[0].rstrip(":,")]
    project = keys[0].split("/")[0]
    closed = words[0].endswith(":")
    joined = words[0].endswith(",")
    for word in words[1:]:
        if closed:
            break
        if word in JOINERS:
            joined = True
            continue
        key = _task_key(word.rstrip(":,"), project) if joined else None
        if key is None:
            break
        keys.append(key)
        closed = word.endswith(":")
        joined = word.endswith(",")
    return keys


def _task_key(name: str, project: str) -> str | None:
    if KEY.fullmatch(name):
        return name
    return f"{project}/{name}" if TASK.fullmatch(name) else None


def is_valid_code(code: str) -> bool:
    """Whether `code` is a project code that is safe to put in a command.

    A code longer than `LINE_LIMIT` is not: `clean_line` would cut it inside the command.
    """
    return len(code) <= LINE_LIMIT and PROJECT_CODE.fullmatch(code) is not None


def code_for_key(key: str) -> str:
    """The PM code of the project key `key`: lower case, every other character turned into `-`.

    The rule is the manager skill's, with ASCII semantics: only a-z, 0-9 and `-` stay, so any
    non-ASCII letter becomes `-` too (`Café` gives `caf-`; the Kelvin sign is no `k`). The result
    may still not be a valid code (`_x` gives `-x`): check it with `is_valid_code`.
    """
    return NOT_ASCII_WORD_CHARS.sub("-", key).lower()  # lower() last: only ASCII is left


def is_valid_row_id(row_id: str) -> bool:
    """Whether `row_id` is a session id (8 hex digits) that is safe to put in a command."""
    return ROW_ID.fullmatch(row_id) is not None


def is_valid_gm_name(name: str) -> bool:
    """Whether `name` is a GM session name that is safe to put in a command."""
    return GM_NAME.fullmatch(name) is not None
