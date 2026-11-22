#!/usr/bin/env python3
"""PreToolUse hook: block tool calls that touch .env files or ~/.ssh.

Reads the hook payload on stdin. Exit 2 blocks the call and shows stderr to the agent.
Unparseable input and unexpected errors are blocked too (fail closed). The plugin runs this script
through fail-closed.sh, which turns a crash before main() (a syntax error, a failed import, a
Python older than 3.12) into exit 2 as well; any other non-zero exit would let the tool call through.

It matches text, not meaning. Every string of the call is searched for `.env…` or `.ssh` as a
separate word. A name glued to a letter, digit, `.`, `_` or `-` (`secrets.env`) passes, and so does
a regex that escapes the dot of such a name (a backslash between `app` and `.env`); a backslash
before a name that starts a word does not. A name after a `)` or `]` that closes a call or an index
on a name (`rows[0].env`, `load().env`) passes too, when the group holds only letters, digits, `_`,
`.`, `,` and `-`; one after a shell expansion (`$(…)`, `$name[…]`), a bare group, or a group with
a quote, blank or bracket in it (`cfg["k"].env`) does not. This holds for Read, Grep, Glob and Bash
text, not for a write target.

A Bash call is checked a little differently: its `description` is never run, so it is skipped, and
its `command` is checked without the parts that are only data, found by the shell_words module
next to this script: the message of `git commit|tag|merge` and `gh pr|issue|release`, a `grep`,
`rg` or `git grep` pattern, and a quoted-delimiter heredoc fed to `cat`, `tee` or a message
command given `-F -` (or the like) when the line has no pipe. A command the scanner cannot follow
(`$(...)`, backticks, comments, process substitution, `((`, a function or alias definition, `eval`,
`hash` and the like, an unquoted heredoc, a wrapper or assignment before the command) is checked
whole. This stops accidents, not a determined agent: a heredoc written to a file and run in a later
command passes, as a Write would.

An Edit, Write, NotebookEdit or MultiEdit call is checked by the path it writes to and nothing
else: its `file_path` and `notebook_path`. What it writes (`content`, `old_string`, `new_string`,
`edits`, `new_source`) and any other key are skipped, so a README that tells you to `cat .env`
can be written. A call with neither key, or with one that is not a non-empty string, is blocked.
The target is checked as written and with its leading and trailing blanks stripped (Claude trims
it), then made absolute from the payload's `cwd` both with the home directory expanded from `~`,
`$HOME` or `${HOME}` and without, with `..`, `//` and `/./` collapsed, then with its symlinks
resolved (the file and every parent; a dangling link gives the text it points to). Each form is
matched in any case and case-folded too (`.ENV`, `~/.SSH/config`), since a filesystem that ignores
case (WSL's `/mnt/c`, macOS) takes them for `.env` and `.ssh`. It is blocked when a form has:

- a `.ssh` component (the word rule above, so `notes.ssh.md` passes);
- a component that can be the DOS short name drvfs gives `.env…` or `.ssh` (`ENV~1`, `ENV~1.LOC`,
  `ENVRC~1`, `SSH~1`); every `ENV…~N` and `SSH…~N` name is refused, a false alarm accepted;
- an env file as its basename: `.env`, `.envrc`, or a name starting `.env.`, `.env-` or `.env_`
  (`.environment.yml`, `.envoy.yaml`, `secrets.env` and a directory such as `.environment/` or
  `.env/` pass), unless it is a template: its last `.`, `-` or `_` separated part is `example`,
  `sample`, `template` or `dist` (`.env.example`, `.env.local.example`, `.env-sample`), while
  `.env.example.local` is refused;
- a path under `/proc`, where `/proc/self` is this hook's process, not Claude's, so the hook
  cannot tell what Claude would write to;
- a symlink loop, whose end cannot be checked (fail closed).

A name reached through a symlink is reported as written, with where it resolves. Reads and Bash
commands stay case-sensitive for `.env…` and `.ssh`. A DOS short name of `.env…` or `.ssh` is also
refused, in any case, as a component of a Read, Grep or Glob path and as a component of a word of
a Bash command (its data parts blanked, as for `.env`; words split at blanks, quotes, `;`, `&`,
`|`, parentheses, `<`, `>`, `=`, `:`, `,`, braces and backslashes): `cat /mnt/c/p/ENV~1` and
`ls SSH~1` are refused, `git log HEAD~1` passes.

A template may be read too: a Read path, or a Grep or Glob path argument (`path`, a Glob pattern,
a Grep `glob`), whose basename is an env template by the rule above passes the `.env…` word rule
for that basename, when the path checked as a write target would be (so a template under `.ssh`,
under `/proc`, or that links to `.env` is still refused). Claude Code refuses an Edit, and a Write
to an existing file, that the session has not Read first, so an agent updates an existing template
by reading it, then editing or writing it. Every other env file stays refused to Read, and a Bash
command that names a template (`cat .env.example`) still is.

Limits. This is defence in depth, not a boundary; these get through:

- a Read or a Bash command that spells `.env` or `.ssh` in another case (`.ENV`), which on a
  filesystem that ignores case reaches the same file: only a write target is matched in any case;
- a write into a folder of env files named otherwise, such as cookiecutter's `.envs/`: only the
  basename counts;
- a write through a hard link, to a symlink made after the check (a race), or through a symlink
  whose text points into `/proc/self` (the hook resolves its own process there, not Claude's);
- an interpreter payload, such as a `python3 -c "..."` string, that builds a path.

The hook's matcher in hooks.json is `Read|Grep|Glob|Bash|Edit|Write|NotebookEdit`; `MultiEdit` is
handled in case a call still arrives under that name.
"""

import errno
import importlib
import logging
import os
import re
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import NamedTuple

# `-I` leaves this folder off sys.path, so put it there for hook_support, the one module found so.
sys.path.insert(0, str(Path(__file__).resolve().parent))
# By name, not an import statement: ruff rejects one below code (E402) and suppressions are banned.
hook_support = importlib.import_module("hook_support")

HOOK = "deny-secrets"
ENV_FILE = re.compile(r"(?<![\w.\-])(?<![\w.\-]\\)(\.env[\w.\-]*)(?=$|[^\w.\-])")
SSH_DIR = re.compile(r"(?<![\w.\-])(?<![\w.\-]\\)(\.ssh)(?=$|[^\w.\-])")
# A `)` or `]` before a name closes a call or an index when the bracket opens right after a name
# (`load(`, `rows[`) that is not a shell variable (`$rows[`, zsh's subscript), and holds only
# letters, digits, `_`, `.`, `,` and `-`: anything else (a quote, a blank, `#`, `|`, a newline)
# could hide the real opener of a `$(...)` or be a zsh glob qualifier (`x(#c0)`, `x(|)`).
GROUP_OPENING = {
    ")": re.compile(r"\([\w.,\-]*\Z"),
    "]": re.compile(r"\[[\w.,\-]*\Z"),
}
NAME_CHAR = re.compile(r"\w")
# `$name`, or `$~name`, `$=name`, `$^name` and the like (zsh parameter flags between `$` and the name).
SHELL_VARIABLE = re.compile(r"\$[^\w\s]*\w+\Z")
HOME_VARIABLE = re.compile(r"^(?:\$\{HOME\}|\$HOME(?!\w))")
PROC = "/proc"
PATH_KEYS = frozenset({"file_path", "notebook_path", "path"})
WRITERS = frozenset({"Edit", "Write", "NotebookEdit", "MultiEdit"})
TARGET_KEYS = ("file_path", "notebook_path")
# A write target is matched in any case: on a filesystem that ignores case (WSL's /mnt/c, macOS)
# `.ENV` is `.env`. Reads and Bash stay case-sensitive.
SSH_DIR_ANY_CASE = re.compile(SSH_DIR.pattern, re.IGNORECASE)
# drvfs (WSL's /mnt/c) opens `.env` as `ENV~1`, `.env.local` as `ENV~1.LOC`, `.ssh` as `SSH~1`.
# Matched in a write target and a Read, Grep or Glob path; `ENVIRO~1` and the like are refused too.
SHORT_NAME = re.compile(r"(?:env|ssh)[^/~]{0,5}~\d+(?:\.[^/]{0,3})?", re.IGNORECASE)
# What splits a Bash command into the words a short name is looked for in, as for `.env`.
SHELL_WORD_BREAK = re.compile(r"[\s\"'`;&|()<>=:,{}\\]+")
# The basenames a write target counts as an env file by, case-folded (`.environment.yml` is not).
ENV_NAMES = frozenset({".env", ".envrc"})
ENV_PREFIXES = (".env.", ".env-", ".env_")
# Templates by convention, which agents create and update: an env name whose last `.`, `-` or
# `_` separated part is one of these may be written (`.env.local.example`, `.env-sample`).
ENV_PART = re.compile(r"[._-]")
ENV_TEMPLATE_PARTS = frozenset({"example", "sample", "template", "dist"})
SEARCH_PATTERNS = frozenset({("Glob", "pattern"), ("Grep", "glob")})
PATH, TEXT, COMMAND = "path", "text", "command"


class Checked(NamedTuple):
    text: str
    kind: str  # PATH: a path or glob; TEXT: any other string of a tool call; COMMAND: Bash code


class Hit(NamedTuple):
    name: str
    reason: str


def strings(value: object) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)


def _without_data() -> Callable[[str], str]:
    function: Callable[[str], str] = hook_support.sibling("shell_words").without_data
    return function


def is_absolute_form(text: str) -> bool:
    return text.startswith(("/", "~/")) or text == "~" or bool(HOME_VARIABLE.match(text))


def joined(base: object, text: str) -> str:
    """`text` as a search finds it: relative to the search path `base`, when it is relative."""
    if not isinstance(base, str) or not base or is_absolute_form(text):
        return text
    return f"{base}/{text}"


def checked_strings(payload: dict[str, object]) -> Iterator[Checked]:
    """The text of the call to search: all of it, except that a Bash call's description is
    skipped and its command is cut down to the parts that are not data."""
    tool = payload.get("tool_name")
    tool_input = payload.get("tool_input", {})
    if not isinstance(tool_input, dict):
        yield from (Checked(text, TEXT) for text in strings(tool_input))
        return
    for key, value in tool_input.items():
        if not isinstance(value, str):
            yield from (Checked(text, TEXT) for text in strings(value))
        elif tool == "Bash" and key == "command":
            yield Checked(_without_data()(value), COMMAND)
        elif tool == "Bash" and key == "description":
            continue
        elif (tool, key) in SEARCH_PATTERNS:
            yield Checked(joined(tool_input.get("path"), value), PATH)
        else:
            yield Checked(value, PATH if key in PATH_KEYS else TEXT)


def write_targets(tool_input: object) -> list[str]:
    """The paths a writing tool writes to (`file_path`, `notebook_path`); `[]` when it has none,
    or when one of them is not a path, so the call is blocked."""
    if not isinstance(tool_input, dict):
        return []
    values = [tool_input[key] for key in TARGET_KEYS if key in tool_input]
    if not all(isinstance(value, str) and value for value in values):
        return []
    return values


def short_name(path: str) -> str | None:
    """A component of `path` that can be the DOS short name of a `.env…` file or of `.ssh`."""
    return next((part for part in path.split("/") if SHORT_NAME.fullmatch(part)), None)


def is_env_file(basename: str) -> bool:
    """A basename is an env file: `.env`, `.envrc`, or `.env.`, `.env-` or `.env_` and more, in
    any case."""
    folded = basename.casefold()
    return folded in ENV_NAMES or folded.startswith(ENV_PREFIXES)


def is_env_template(basename: str) -> bool:
    """Its last `.`, `-` or `_` separated part says it is a template (`.env.local.example`)."""
    return ENV_PART.split(basename.casefold())[-1] in ENV_TEMPLATE_PARTS


def _secret_in_spelling(path: str) -> str | None:
    ssh = SSH_DIR_ANY_CASE.search(path)
    if ssh:
        return ssh.group(1)
    short = short_name(path)
    if short:
        return short
    basename = os.path.basename(path)
    return basename if is_env_file(basename) and not is_env_template(basename) else None


def secret_in_target(path: str) -> str | None:
    """The guarded name a write target has, in any case and also case-folded (`.ßh` is `.ssh`):
    a `.ssh` component, a short name, or a `.env…` basename that is not exactly a template. A
    directory named like `.env…` does not count."""
    for spelling in dict.fromkeys((path, path.casefold())):
        name = _secret_in_spelling(spelling)
        if name:
            return name
    return None


def _normal(path: str) -> str:
    normal = os.path.normpath(path)
    return "/" + normal.lstrip("/") if normal.startswith("//") else normal


def _resolved(path: str) -> str | None:
    try:
        return os.path.realpath(path)
    except (ValueError, OSError):
        return None


def _forms(path: str) -> Iterator[str]:
    """A path as written (`//`, `/./` and `..` collapsed) and, when it differs, with its
    symlinks resolved. A relative one is resolved from the working directory."""
    normal = _normal(path)
    yield normal
    resolved = _resolved(normal)
    if resolved is not None and resolved != normal:
        yield resolved


def _home_expanded(word: str, home: Path) -> str:
    """`word` with a leading `~`, `$HOME` or `${HOME}` replaced by the home directory."""
    if word == "~" or word.startswith("~/"):
        return str(home) + word[1:]
    return HOME_VARIABLE.sub(lambda _: str(home), word)


def target_paths(target: str, cwd: object, home: Path) -> list[str]:
    """The absolute paths a write target can stand for, from the call's `cwd`: as written and with
    its blanks stripped (Claude trims it), each with the home directory expanded and not (Claude
    expands `~` but not `$HOME`)."""
    base = cwd if isinstance(cwd, str) and cwd.startswith("/") else os.getcwd()
    spellings = dict.fromkeys((target, target.strip()))
    paths = (os.path.join(base, path) for s in spellings for path in (_home_expanded(s, home), s))
    return list(dict.fromkeys(paths))


def _loops(path: str) -> bool:
    """Following `path`'s symlinks loops (the kernel's ELOOP); `realpath` then stops short."""
    try:
        os.stat(path)
    except OSError as error:
        return error.errno == errno.ELOOP
    return False


def form_hit(path: str) -> Hit | None:
    """What one absolute, normalised form of a write target names: a secret, or `/proc`, where
    `/proc/self` (and so `cwd`, `root`, `fd`) is this hook's process, not Claude's, so what the
    hook would resolve is not what Claude writes."""
    name = secret_in_target(path)
    if name:
        return Hit(name, "secrets")
    if path == PROC or path.startswith(PROC + "/"):
        return Hit(path, "proc: /proc/self there is the hook's own process, not Claude's")
    if _loops(path):
        return Hit(path, "a symlink loop: where it leads cannot be checked")
    return None


def target_hit(target: str, cwd: object, home: Path) -> Hit | None:
    """What writing `target` reaches: checked as written, made absolute (`..`, `//` and `/./`
    collapsed) in each way `target_paths` gives, and with its symlinks resolved."""
    for spelling in dict.fromkeys((target, target.strip())):
        name = secret_in_target(spelling)
        if name:
            return Hit(name, "secrets")
    forms = [list(_forms(path)) for path in target_paths(target, cwd, home)]
    for normal, *_ in forms:
        hit = form_hit(normal)
        if hit:
            return hit
    for form in (form for _, *resolved in forms for form in resolved):
        hit = form_hit(form)
        if hit:
            return Hit(target, f"{hit.reason}: it resolves into a guarded place, {form}")
    return None


def write_hit(payload: dict[str, object], home: Path) -> Hit | None:
    """Edit, Write, NotebookEdit and MultiEdit: only the paths they write to are checked."""
    targets = write_targets(payload.get("tool_input"))
    if not targets:
        return Hit(str(payload.get("tool_name")), "no file_path or notebook_path to check")
    for target in targets:
        hit = target_hit(target, payload.get("cwd"), home)
        if hit:
            return hit
    return None


def is_template_path(path: str, cwd: object, home: Path) -> bool:
    """A Read, Grep or Glob path whose basename is an env template, and that nothing secret is
    reached through: as a write target, none of its forms (resolved too) is guarded."""
    basename = os.path.basename(path)
    if not (is_env_file(basename) and is_env_template(basename)):
        return False
    return target_hit(path, cwd, home) is None


def _opener(text: str, close: int) -> int | None:
    """The index of the bracket that `text[close]` (`)` or `]`) closes, when only name characters
    lie between them."""
    opening = GROUP_OPENING[text[close]].search(text, 0, close)
    return opening.start() if opening else None


def closes_call_or_index(text: str, close: int) -> bool:
    """The `)` or `]` at `close` ends a call or an index on a name (`load()`, `rows[0]`,
    `a[0][1]`), not a shell expansion (`$(x)`, `$name[1]`) or a group (`(x)`, `<(x)`). Each group
    holds only name characters, so a quote, blank or bracket in it means the opener found is not
    the real one (`$(true 'a(').env`) and the name stays refused."""
    while True:
        opener = _opener(text, close)
        if opener is None or opener == 0:
            return False
        before = text[opener - 1]
        if before not in GROUP_OPENING:
            return bool(NAME_CHAR.match(before)) and not SHELL_VARIABLE.search(text[:opener])
        close = opener - 1


def follows_call_or_index(text: str, start: int) -> bool:
    """The match at `start` is an attribute of a call or an index (`rows[0].env`)."""
    return start > 0 and text[start - 1] in GROUP_OPENING and closes_call_or_index(text, start - 1)


def unglued_match(pattern: re.Pattern[str], text: str) -> re.Match[str] | None:
    """The first match of `pattern` that is not an attribute of a call or an index."""
    matches = pattern.finditer(text)
    return next((m for m in matches if not follows_call_or_index(text, m.start())), None)


def text_hit(checked: Checked, cwd: object, home: Path) -> Hit | None:
    """A `.env…` or `.ssh` word in one string of a Read, Grep, Glob or Bash call, or a short name
    in one of its paths or as a word of its command (the data parts blanked, as for `.env`). The
    basename of a path that is an env template is left out."""
    text = checked.text
    if checked.kind == PATH and is_template_path(text, cwd, home):
        text = os.path.dirname(text)
    for pattern in (ENV_FILE, SSH_DIR):
        match = unglued_match(pattern, text)
        if match:
            return Hit(match.group(1), "secrets")
    words = {PATH: [text], COMMAND: SHELL_WORD_BREAK.split(text)}.get(checked.kind, [])
    short = next(filter(None, map(short_name, words)), None)
    return Hit(short, "secrets") if short else None


def blocked(payload: dict[str, object]) -> Hit | None:
    home = Path.home()
    if payload.get("tool_name") in WRITERS:
        return write_hit(payload, home)
    for checked in checked_strings(payload):
        secret = text_hit(checked, payload.get("cwd"), home)
        if secret:
            return secret
    return None


def run() -> int:
    try:
        payload = hook_support.loads(sys.stdin.read())
        if not isinstance(payload, dict):
            raise ValueError("payload is not an object")
    except ValueError as exc:
        print(f"coven {HOOK}: could not parse hook input ({exc}); blocking", file=sys.stderr)
        hook_support.log_failure(HOOK, f"could not parse hook input ({exc})")
        return 2
    hit = blocked(payload)
    if hit:
        print(f"coven {HOOK}: access to '{hit.name}' is blocked ({hit.reason})", file=sys.stderr)
        return 2
    return 0


def main() -> int:
    try:
        return run()
    # Fail closed on any error (closed stdin, a broken sibling module, a bug): Claude treats every
    # exit code but 2 as non-blocking.
    except Exception as exc:
        print(f"coven {HOOK}: internal error ({exc!r}); blocking", file=sys.stderr)
        logging.exception("coven %s: internal error; blocking", HOOK)
        hook_support.log_failure(HOOK, f"internal error ({exc!r})")
        return 2


if __name__ == "__main__":
    sys.exit(main())
