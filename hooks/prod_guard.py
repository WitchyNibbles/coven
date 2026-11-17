#!/usr/bin/env python3
"""PreToolUse hook: refuse tool calls that reach production or gated systems unless the user
approved that exact command.

Reads the hook payload on stdin and runs under fail-closed.sh like deny-secrets. Exit 2 refuses the
call and shows stderr to the agent; exit 0 lets it through. `prod_guard.py --check <guard.json>`
validates a file with the same rules before it is committed: it prints `ok: N targets` and exits 0,
or prints the reason and exits 1; it never reads stdin.

When it acts. The hook reads coven's config (`~/.config/coven/config.json`, as plain JSON, the way
memory-context does) for `memory_path` (default `~/coven-memory`) and `guard`, whose only valid value
is `"required"`. It is off, and exits 0 at once with no git call and no subprocess, when the store folder is
missing, or when there is neither `"guard": "required"` nor a `systems/guard.json` in the store's
working tree. With no config file the store is the default one, so deleting the file does not
switch off a store that holds a guard.json. It is on otherwise. A config that exists but cannot be read or is invalid
refuses Bash and MCP tools in a restricted session (the message points to `coven doctor`) and
leaves any other session as if the guard were off.

Who it restricts. The target rule applies to every session. A session whose COVEN_SESSION_ROLE is
set to any value, an empty or unknown one included, is also restricted: only an unset variable is
unrestricted. The extra rules for a restricted session plug in at RESTRICTED_RULES.

What it reads. Everything comes from the store's git HEAD (`git cat-file`, `git ls-tree`), never
from its working tree, so an uncommitted edit, deletion or approval has no effect. Every git call
runs without the variables that move git to another repository (GIT_VARIABLES).
- `systems/guard.json` valid at HEAD is enforced.
- Invalid at HEAD, the newest valid version among the last GUARD_HISTORY commits that changed it is
  enforced, and every target refusal says so. With no valid version only reads work, and an
  unrestricted session may also edit files, so the file can be repaired; the user fixes or reverts
  it in their own terminal.
- Absent at HEAD, a restricted session may not use Bash or MCP tools; an unrestricted one goes on.
- When git cannot read the store (no commits, a broken repository, a timeout) any call that reaches
  outside, Bash and MCP tools, is refused for every session, naming the git error.

guard.json is `{"targets": [{"system", "kind", "match", "tools"}]}`. `kind` is `production` or
`gated`; `match` are Python regexes searched in a Bash command's code text; `tools` are exact MCP
tool names. A call that reaches a target needs a live approval for that target's system, committed
as `approvals/*.json` (`{"id", "system", "commands", "tools", "expires"}`), that names the exact
command (whitespace stripped) or tool. A command that reaches two systems needs one approval each.
An approval is live when `now < expires <= now + MAX_APPROVAL`. Approvals match the whole command,
never its code text.

Code text. `shell_words.code_text` blanks data only where the command runs nothing it is given (a
commit message, a grep pattern, quoted echo text); a shell, an interpreter, `$(...)`, backticks or
anything the scanner cannot follow is read whole.

Limits. The whole check has a deadline of DEADLINE seconds and each git call a timeout; past either,
the call is refused. The guard is best effort against a session that follows its role: its rules are
public, and the committed store is its boundary. A process running as the same OS user can forge the
store's state, and the user's own terminal outside Claude Code is never guarded.

`decide` is built on `explain`, which also returns what `coven guard explain` shows (the hits, their
approvals, the restricted refusal and a verdict), so the two cannot disagree.
"""

import importlib
import logging
import os
import re
import signal
import subprocess
import sys
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, NamedTuple, NoReturn, TextIO, TypedDict, TypeGuard

# Hooks run as `python3 -I`: write no bytecode into the installed plugin folder.
sys.dont_write_bytecode = True
# `-I` leaves this folder off sys.path, so put it there for hook_support, the one module found so.
sys.path.insert(0, str(Path(__file__).resolve().parent))
# By name, not an import statement: ruff rejects one below code (E402) and suppressions are banned.
hook_support = importlib.import_module("hook_support")

HOOK = "prod-guard"
PREFIX = "coven prod-guard:"
USAGE = "usage: prod_guard.py | prod_guard.py --check <guard.json>"
CHECK_FLAG = "--check"
ROLE_VAR = "COVEN_SESSION_ROLE"
GUARD_FILE = Path("systems/guard.json")
APPROVALS_DIR = "approvals"
LATCH_VALUE = "required"
# A hook still running at Claude's hook timeout lets the call through, so the whole check has a
# deadline below it (a backtracking guard.json pattern, hanging git calls), and each git call has
# its own timeout.
DEADLINE = 20.0
GIT_TIMEOUT = 5.0
TOO_SLOW = (
    f"{PREFIX} the check took too long (a guard.json pattern may backtrack, or the input is very"
    " large); blocking"
)
# Commits of guard.json searched for a valid version when HEAD's is invalid.
GUARD_HISTORY = 50
MAX_APPROVAL = timedelta(hours=12)
# The variables that point git at another repository, which a hook inherits from whoever starts it.
GIT_VARIABLES = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_COMMON_DIR",
    "GIT_NAMESPACE",
    "GIT_CEILING_DIRECTORIES",
    "GIT_DISCOVERY_ACROSS_FILESYSTEM",
)
# One `git cat-file --batch` header: "<oid> <type> <size>"; a missing object is "<name> missing".
BATCH_HEADER = re.compile(r"(\S+) (\w+) (\d+)")
BATCH_MISSING = " missing"
# `git ls-tree -z` entries for regular files: approvals/<name>.json and guard.json.
REGULAR_BLOB = rb"100(?:644|755) blob ([0-9a-f]+)\t"
APPROVAL_ENTRY = re.compile(REGULAR_BLOB + APPROVALS_DIR.encode() + rb"/[^/]+\.json")
GUARD_ENTRY = re.compile(REGULAR_BLOB + re.escape(GUARD_FILE.as_posix().encode()))
MCP_PREFIX = "mcp__"
READ_TOOLS = frozenset({"Read", "Grep", "Glob"})
EDIT_TOOLS = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit"})
KINDS = ("production", "gated")
TARGET_KEYS = frozenset({"system", "kind", "match", "tools"})
APPROVAL_KEYS = frozenset({"id", "system", "commands", "tools", "expires"})
# The role a hit is explained for when the session has none: what a restricted session would meet.
ASSUMED_ROLE = "pm"
LOGGED_COMMAND_LIMIT = 200
CONFIG_ERROR = "cannot read the coven config"


class PayloadError(ValueError):
    """The hook payload does not have the shape of a tool call."""


class ConfigError(Exception):
    """coven's config file cannot be read or is invalid."""


class StoreError(Exception):
    """git cannot read the memory store's committed files (no commits, failing, too slow, odd
    output)."""


class GuardError(Exception):
    """guard.json is unusable: not JSON, the wrong shape, a bad regex or kind, or unreadable."""


class DeadlineError(Exception):
    """The check ran past DEADLINE."""


def _past_deadline(_signum: int, _frame: object) -> NoReturn:
    raise DeadlineError


class Target(TypedDict):
    system: str
    kind: str
    match: list[str]
    tools: list[str]


class Enforced(NamedTuple):
    targets: list[Target] | None  # None: no guard.json committed at HEAD
    invalid_head: str | None = None  # why HEAD's guard.json is invalid, when an older one is used


@dataclass(frozen=True)
class Approval:
    id: str
    system: str
    commands: list[str]
    tools: list[str]
    expires: datetime


class GuardConfig(NamedTuple):
    store: Path
    latch: bool  # `"guard": "required"`: the store held a guard.json once, so it must keep one


class Bash(NamedTuple):
    command: str
    # The command with its data blanked (shell_words.code_text): what the target patterns read.
    code: str


@dataclass(frozen=True)
class Call:
    tool: str
    bash: Bash | None  # None for every tool but Bash


@dataclass(frozen=True)
class Hit:
    """One way a call reaches a target, and the live approval that grants it (None: it needs one)."""

    system: str
    kind: str  # the target's kind: production or gated
    via: Literal["command", "tool"]
    trigger: str  # the first pattern that matched, or the tool's name
    approval: Approval | None


type Verdict = Literal["allowed", "refused for a PM", "refused for every session"]


@dataclass(frozen=True)
class Explanation:
    """What the guard decides for one call, and why."""

    call: Call
    hits: list[Hit]
    restricted: str | None  # the refusal a restricted session meets, whatever this session is
    note: str  # said after a target refusal: an older guard.json is enforced
    message: str | None  # why this session's call is refused; None: allowed
    verdict: Verdict


# --- config ---


def config_file() -> Path:
    """`~/.config/coven/config.json`: where `coven.paths.config_dir()` and memory-context look."""
    return Path.home() / ".config" / "coven" / "config.json"


def read_config() -> GuardConfig:
    """The store and latch of coven's config. With no config file that is the default store and no
    latch, so deleting the file cannot switch off a store that holds a guard.json. ConfigError
    when the file cannot be read or holds something else than expected."""
    file = config_file()
    try:
        text = file.read_text(encoding="utf-8")
    except FileNotFoundError:
        return GuardConfig(Path.home() / "coven-memory", latch=False)
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError(f"{file}: cannot be read ({exc})") from None
    try:
        data = hook_support.loads(text)
    except ValueError as exc:
        raise ConfigError(f"{file}: not valid JSON ({exc})") from None
    if not isinstance(data, dict):
        raise ConfigError(f"{file}: must hold a JSON object")
    configured, guard = data.get("memory_path"), data.get("guard")
    if configured is not None and not isinstance(configured, str):
        raise ConfigError(f"{file}: memory_path must be a string")
    if guard is not None and guard != LATCH_VALUE:
        raise ConfigError(f'{file}: guard must be "{LATCH_VALUE}" or null')
    store = Path.home() / "coven-memory" if configured is None else Path(configured).expanduser()
    return GuardConfig(store, guard == LATCH_VALUE)


def off_reason(config: GuardConfig) -> str | None:
    """Why the guard is off for this config, or None when it is on. One stat or two, no git."""
    if not config.store.is_dir():
        return f"no memory store folder at {config.store}"
    if config.latch or os.path.lexists(config.store / GUARD_FILE):
        return None
    return f'no "guard": "{LATCH_VALUE}" in the config and no {GUARD_FILE} in {config.store}'


# --- guard.json ---


def _string_list(value: object) -> TypeGuard[list[str]]:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _target(item: object, where: str) -> Target:
    if not isinstance(item, dict):
        raise GuardError(f"{where} is not an object")
    unknown = sorted(set(item) - TARGET_KEYS)
    if unknown:
        raise GuardError(f"{where} has unknown key(s) {', '.join(unknown)}")
    system, kind = item.get("system"), item.get("kind")
    if not isinstance(system, str) or not system:
        raise GuardError(f"{where} needs a non-empty 'system'")
    if not isinstance(kind, str) or kind not in KINDS:
        raise GuardError(f"{where} 'kind' must be one of {', '.join(KINDS)}")
    match, tools = item.get("match", []), item.get("tools", [])
    if not _string_list(match) or not _string_list(tools):
        raise GuardError(f"{where} 'match' and 'tools' must be lists of strings")
    for pattern in match:
        try:
            re.compile(pattern)
        except re.error as exc:
            raise GuardError(f"{where} has a bad regex {pattern!r}: {exc}") from None
    return Target(system=system, kind=kind, match=list(match), tools=list(tools))


def _parse_targets(text: str, path: Path) -> list[Target]:
    try:
        raw = hook_support.loads(text)
    except ValueError as exc:
        raise GuardError(f"{path}: not valid JSON: {exc}") from None
    if not isinstance(raw, dict) or set(raw) != {"targets"} or not isinstance(raw["targets"], list):
        raise GuardError(f'{path}: must be an object {{"targets": [...]}}')
    return [_target(item, f"{path}: target {index}") for index, item in enumerate(raw["targets"])]


def check_file(path: Path) -> str:
    """`ok: N targets` for a guard.json file that follows the rules the committed one follows;
    GuardError otherwise."""
    if path.is_symlink():
        raise GuardError(f"{path}: cannot read: not a regular file")
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise GuardError(f"{path}: cannot read: {exc}") from None
    return f"ok: {len(_parse_targets(text, path))} targets"


# --- the store, read through git at HEAD ---


def _git(store: Path, *args: str, stdin: bytes = b"") -> bytes:
    """git's stdout, run on the store's own repository (never one found above it or named by the
    environment). StoreError when git is missing, fails or times out."""
    env = {key: value for key, value in os.environ.items() if key not in GIT_VARIABLES}
    try:
        proc = subprocess.run(
            ["git", f"--git-dir={store / '.git'}", *args],
            input=stdin,
            capture_output=True,
            timeout=GIT_TIMEOUT,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise StoreError(f"git timed out after {GIT_TIMEOUT:g} s") from None
    except OSError as exc:
        raise StoreError(f"cannot run git: {exc}") from None
    if proc.returncode != 0:
        lines = proc.stderr.decode("utf-8", errors="replace").strip().splitlines()
        raise StoreError(lines[-1] if lines else f"git exited with {proc.returncode}")
    return proc.stdout


def _head(store: Path) -> str:
    """The commit HEAD points to; StoreError in a store without commits."""
    out = _git(store, "cat-file", "--batch-check", stdin=b"HEAD\n")
    header = out.decode("utf-8", errors="replace").strip()
    if header == "HEAD" + BATCH_MISSING:
        raise StoreError("the store has no commits")
    match = BATCH_HEADER.fullmatch(header)
    if match is None or match[2] != "commit":
        raise StoreError(f"unexpected git cat-file output {header[:80]!r}")
    return match[1]


def _objects(store: Path, names: Sequence[str]) -> list[bytes]:
    """The content of each git object name; StoreError when one does not exist."""
    out = _git(store, "cat-file", "--batch", stdin="".join(f"{name}\n" for name in names).encode())
    contents: list[bytes] = []
    start = 0
    for _name in names:
        end = out.find(b"\n", start)
        header = out[start:end].decode("utf-8", errors="replace")
        match = BATCH_HEADER.fullmatch(header)
        if match is None:
            raise StoreError(f"unexpected git cat-file output {header[:80]!r}")
        size = int(match[3])
        contents.append(out[end + 1 : end + 1 + size])
        start = end + 1 + size + 1  # the content ends with a newline
    return contents


def _guard_text_at(store: Path, commit: str) -> str | None:
    """guard.json as committed in `commit`; None when that commit has none."""
    listing = _git(store, "ls-tree", "-z", commit, "--", GUARD_FILE.as_posix())
    if not listing:
        return None
    path = store / GUARD_FILE
    match = GUARD_ENTRY.fullmatch(listing.rstrip(b"\0"))
    if match is None:
        raise GuardError(f"{path}: cannot read: not a regular file in HEAD")
    (content,) = _objects(store, [match[1].decode()])
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise GuardError(f"{path}: cannot read: {exc}") from None


def _targets_at(store: Path, commit: str) -> list[Target] | None:
    """The targets committed in `commit`; None without guard.json, GuardError if it is unusable."""
    text = _guard_text_at(store, commit)
    return None if text is None else _parse_targets(text, store / GUARD_FILE)


def _last_valid_targets(store: Path, head: str) -> list[Target] | None:
    """The newest valid guard.json among the last GUARD_HISTORY commits that changed it."""
    log = _git(
        store,
        "log",
        f"--max-count={GUARD_HISTORY}",
        "--format=%H",
        head,
        "--",
        GUARD_FILE.as_posix(),
    )
    for commit in log.decode().split():
        try:
            targets = _targets_at(store, commit)
        except GuardError:
            continue
        if targets is not None:
            return targets
    return None


def load_targets(store: Path) -> Enforced:
    """The targets enforced at the store's HEAD: HEAD's own, or the last valid version's (with
    the reason HEAD's is invalid). `targets` is None without a committed guard.json. StoreError
    when git cannot read the store; GuardError when HEAD's guard.json is invalid and no earlier
    version is valid."""
    head = _head(store)
    try:
        return Enforced(_targets_at(store, head))
    except GuardError as exc:
        fallback = _last_valid_targets(store, head)
        if fallback is None:
            raise
        return Enforced(fallback, str(exc))


def _expiry(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        expires = datetime.fromisoformat(value)
    except ValueError:
        return None
    return expires if expires.utcoffset() is not None else None


def _approval(raw: object) -> Approval | None:
    """The approval record, or None when it does not follow the schema exactly."""
    if not isinstance(raw, dict) or set(raw) != APPROVAL_KEYS:
        return None
    ident, system, commands, tools = raw["id"], raw["system"], raw["commands"], raw["tools"]
    expires = _expiry(raw["expires"])
    if not isinstance(ident, str) or not isinstance(system, str) or expires is None:
        return None
    if _string_list(commands) and _string_list(tools):
        return Approval(ident, system, commands, tools, expires)
    return None


def _approval_records(store: Path) -> Iterator[Approval]:
    listing = _git(store, "ls-tree", "-z", "HEAD", "--", f"{APPROVALS_DIR}/")
    entries = (APPROVAL_ENTRY.fullmatch(entry) for entry in listing.split(b"\0"))
    oids = [match[1].decode() for match in entries if match]
    if not oids:
        return
    for content in _objects(store, oids):
        try:
            approval = _approval(hook_support.loads(content.decode("utf-8")))
        except ValueError:
            continue  # an unreadable approval approves nothing
        if approval is not None:
            yield approval


def approvals(store: Path) -> list[Approval]:
    """The approvals committed at HEAD as regular files approvals/*.json that follow the schema,
    live or not; StoreError when git cannot read them."""
    return list(_approval_records(store))


def _live(approval: Approval, now: datetime) -> bool:
    """Whether an approval is in force: not expired, and ending at most MAX_APPROVAL from now."""
    return now < approval.expires <= now + MAX_APPROVAL


# --- the payload ---


def _call(payload: Mapping[str, object]) -> Call:
    tool, tool_input = payload.get("tool_name"), payload.get("tool_input")
    if not isinstance(tool, str) or not tool:
        raise PayloadError("no tool_name")
    if not isinstance(tool_input, dict):
        raise PayloadError("tool_input is not an object")
    if tool != "Bash":
        return Call(tool, None)
    command = tool_input.get("command")
    if not isinstance(command, str):
        raise PayloadError("a Bash call without a command")
    code: str = hook_support.sibling("shell_words").code_text(command)
    return Call(tool, Bash(command, code))


def _reaches_out(call: Call) -> bool:
    """Bash and MCP tools: the calls that can reach a system the guard cannot see without the store."""
    return call.bash is not None or call.tool.startswith(MCP_PREFIX)


def _may_repair(call: Call, role: str | None) -> bool:
    """Calls allowed while no version of guard.json is valid: reads, and an unrestricted
    session's edits."""
    return call.tool in READ_TOOLS or (role is None and call.tool in EDIT_TOOLS)


# --- the decision ---

type Rule = Callable[[Call, Path, str, datetime], str | None]
# The rules only a restricted session meets, tried in order before the target rule; each returns
# the refusal or None. No approval lifts one.
RESTRICTED_RULES: tuple[Rule, ...] = ()


def _restricted_refusal(call: Call, store: Path, role: str, now: datetime) -> str | None:
    for rule in RESTRICTED_RULES:
        refusal = rule(call, store, role, now)
        if refusal:
            return refusal
    return None


def _unreadable_store(store: Path, exc: StoreError) -> str:
    return (
        f"{PREFIX} cannot read the committed memory store at {store} ({exc}); Bash and MCP tools"
        " are blocked until git can read it: run coven doctor"
    )


def _no_valid_guard(store: Path, exc: GuardError) -> str:
    return (
        f"{PREFIX} {exc}; no earlier committed version is valid, so only reads work until the"
        f" user fixes or reverts it in their own terminal (git -C {store} revert HEAD)"
    )


def _fallback_note(enforced: Enforced) -> str:
    if enforced.invalid_head is None:
        return ""
    return (
        f"; the committed {GUARD_FILE} is invalid, so its last valid version is enforced"
        f" ({enforced.invalid_head})"
    )


def _triggers(call: Call, target: Target) -> Iterator[tuple[Literal["command", "tool"], str]]:
    """How this call reaches `target`: the first pattern its code text matches, and its tool name."""
    if call.bash is not None:
        code = call.bash.code
        pattern = next((p for p in target["match"] if re.search(p, code)), None)
        if pattern is not None:
            yield "command", pattern
    if call.tool in target["tools"]:
        yield "tool", call.tool


def _grants(approval: Approval, hit_system: str, via: str, call: Call, now: datetime) -> bool:
    """Whether a live approval of `hit_system` names the exact command (stripped) or tool."""
    if approval.system != hit_system or not _live(approval, now):
        return False
    if via == "tool":
        return call.tool in approval.tools
    return call.bash is not None and call.bash.command.strip() in approval.commands


def _hits(call: Call, targets: Sequence[Target], store: Path, now: datetime) -> list[Hit]:
    """Every target the call reaches. Approvals are read only when it reaches one: StoreError."""
    reached = [(t, via, trigger) for t in targets for via, trigger in _triggers(call, t)]
    if not reached:
        return []
    found = approvals(store)
    return [
        Hit(
            target["system"],
            target["kind"],
            via,
            trigger,
            next((a for a in found if _grants(a, target["system"], via, call, now)), None),
        )
        for target, via, trigger in reached
    ]


def _target_refusal(hits: Sequence[Hit], note: str) -> str | None:
    """Why the call may not reach the targets it hits: no live approval names it for each."""
    needed = dict.fromkeys(f"'{h.system}' is {h.kind}" for h in hits if h.approval is None)
    if not needed:
        return None
    return (
        f"{PREFIX} {' and '.join(needed)}; this exact step needs the user's approval"
        f" (send a PROD-STEP; the GM approves it with coven approve){note}"
    )


@dataclass(frozen=True)
class _Reading:
    """What the store says about one call."""

    hits: list[Hit]
    note: str = ""
    everyone: str | None = None  # the refusal every session meets
    restricted_only: str | None = None  # the refusal only a restricted session meets


def _read_store(call: Call, store: Path, role: str | None, now: datetime) -> _Reading:
    out = _reaches_out(call)
    try:
        enforced = load_targets(store)
    except StoreError as exc:
        return _Reading([], everyone=_unreadable_store(store, exc) if out else None)
    except GuardError as exc:
        return _Reading(
            [], everyone=None if _may_repair(call, role) else _no_valid_guard(store, exc)
        )
    if enforced.targets is None:
        missing = f"{PREFIX} {GUARD_FILE} is missing from the committed store; ask the user"
        return _Reading([], restricted_only=missing if out else None)
    note = _fallback_note(enforced)
    try:
        hits = _hits(call, enforced.targets, store, now)
    except StoreError as exc:
        # Only a call that reaches a target reads approvals: refuse it, whatever tool it is.
        return _Reading([], note, everyone=_unreadable_store(store, exc))
    return _Reading(hits, note, everyone=_target_refusal(hits, note))


def _verdict(everyone: str | None, restricted: str | None) -> Verdict:
    if everyone:
        return "refused for every session"
    return "refused for a PM" if restricted else "allowed"


def explain(
    payload: Mapping[str, object], store: Path, role: str | None, now: datetime
) -> Explanation:
    """What the guard decides for this tool call and why; PayloadError on a bad payload.

    `role` is COVEN_SESSION_ROLE: None (unset) is an unrestricted session; any value, empty or
    unknown included, restricts it. `restricted` is the refusal a restricted session meets even
    when `role` is None, and `message` is the refusal this session meets."""
    call = _call(payload)
    reading = _read_store(call, store, role, now)
    rule = _restricted_refusal(call, store, ASSUMED_ROLE if role is None else role, now)
    restricted = rule or reading.restricted_only
    message = restricted if role is not None and restricted else reading.everyone
    return Explanation(
        call,
        reading.hits,
        restricted,
        reading.note,
        message,
        _verdict(reading.everyone, restricted),
    )


def decide(
    payload: Mapping[str, object], store: Path | None, role: str | None, now: datetime
) -> str | None:
    """The refusal for this tool call, or None to allow it; PayloadError on a bad payload.
    `store` None means the guard is off."""
    if store is None:
        _call(payload)
        return None
    return explain(payload, store, role, now).message


# --- the hook ---


def _payload(stdin: TextIO) -> dict[str, object]:
    try:
        payload = hook_support.loads(stdin.read())
    except ValueError as exc:
        raise PayloadError(str(exc)) from None
    if not isinstance(payload, dict):
        raise PayloadError("payload is not an object")
    return payload


def _record(found: Explanation) -> None:
    """Log the decision. The log is best effort: a broken one must not change the answer."""
    command = found.call.bash.command if found.call.bash else None
    record = {
        "decision": "allow" if found.message is None else "deny",
        "tool": found.call.tool,
        "command": command if command is None else command[:LOGGED_COMMAND_LIMIT],
        "message": found.message,
    }
    try:
        hook_support.sibling("hooklog").log_event(HOOK, record)
    except Exception:
        logging.exception("coven %s: could not write the hook log", HOOK)


def _refuse_bad_input(exc: PayloadError) -> int:
    print(f"{PREFIX} could not parse hook input ({exc}); blocking", file=sys.stderr)
    hook_support.log_failure(HOOK, f"could not parse hook input ({exc})")
    return 2


def _guard(store: Path, role: str | None, stdin: TextIO) -> int:
    try:
        found = explain(_payload(stdin), store, role, datetime.now(UTC))
    except PayloadError as exc:
        return _refuse_bad_input(exc)
    _record(found)
    if found.message:
        print(found.message, file=sys.stderr)
        return 2
    return 0


def _unreadable_config(exc: ConfigError, role: str | None, stdin: TextIO) -> int:
    """A config that cannot be read: a restricted session loses Bash and MCP tools, any other
    session goes on as if the guard were off."""
    hook_support.log_failure(HOOK, f"{CONFIG_ERROR}: {exc}")
    if role is None:
        return 0
    try:
        call = _call(_payload(stdin))
    except PayloadError as bad:
        return _refuse_bad_input(bad)
    if not _reaches_out(call):
        return 0
    print(
        f"{PREFIX} {CONFIG_ERROR} ({exc}); Bash and MCP tools are blocked in this session: run"
        " coven doctor",
        file=sys.stderr,
    )
    return 2


def _check(argv: Sequence[str]) -> int:
    """`--check <guard.json>`: 0 and `ok: N targets` for a valid file, 1 and the reason if not."""
    if len(argv) != 1:
        print(f"{PREFIX} {USAGE}", file=sys.stderr)
        return 2
    try:
        print(check_file(Path(argv[0])))
    except GuardError as exc:
        print(f"{PREFIX} {exc}", file=sys.stderr)
        return 1
    return 0


def run(argv: Sequence[str], stdin: TextIO, environ: Mapping[str, str]) -> int:
    if argv[:1] == [CHECK_FLAG]:
        return _check(argv[1:])
    if argv:
        print(f"{PREFIX} {USAGE}; blocking", file=sys.stderr)
        return 2
    role = environ.get(ROLE_VAR)
    try:
        config = read_config()
    except ConfigError as exc:
        return _unreadable_config(exc, role, stdin)
    if off_reason(config) is not None:
        return 0
    return _guard(config.store, role, stdin)


def _checked(argv: Sequence[str], stdin: TextIO, environ: Mapping[str, str]) -> int:
    """run() under the deadline, disarmed before anything else; any other error blocks the call."""
    signal.setitimer(signal.ITIMER_REAL, DEADLINE)  # interrupts re.search and git waits alike
    try:
        try:
            return run(argv, stdin, environ)
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
    except DeadlineError:
        raise
    # Fail closed on any error (closed stdin, a broken sibling module, a bug): Claude treats every
    # exit code but 2 as non-blocking.
    except Exception as exc:
        print(f"{PREFIX} internal error ({exc!r}); blocking", file=sys.stderr)
        logging.exception("coven %s: internal error; blocking", HOOK)
        hook_support.log_failure(HOOK, f"internal error ({exc!r})")
        return 2


def _too_slow() -> int:
    print(TOO_SLOW, file=sys.stderr)
    hook_support.log_failure(HOOK, "the check took too long")
    return 2


def main(
    argv: Sequence[str] | None = None,
    stdin: TextIO | None = None,
    environ: Mapping[str, str] | None = None,
) -> int:
    previous = signal.signal(signal.SIGALRM, _past_deadline)
    try:
        return _checked(
            sys.argv[1:] if argv is None else argv,
            stdin or sys.stdin,
            os.environ if environ is None else environ,
        )
    # The one-shot alarm can land anywhere in _checked, its error handler included: still block.
    except DeadlineError:
        return _too_slow()
    finally:
        signal.signal(signal.SIGALRM, previous)  # _checked disarmed the timer already


def cli() -> int:
    """main() for the command line, with a last catch: an uncaught exception would exit 1, which
    lets the call through."""
    try:
        return main()
    except DeadlineError:
        return _too_slow()


if __name__ == "__main__":
    sys.exit(cli())
