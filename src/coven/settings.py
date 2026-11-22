"""The changes `coven setup` owns: Claude Code settings keys, coven config keys and the shim.

`plan` lists the changes a choice set needs, `apply` writes them and records each one in
`owned.json` (value before, value written), `undo` reverses exactly the recorded changes that
still hold. A file coven cannot parse is never rewritten.
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import stat
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Literal

from coven import paths
from coven.config import GUARD_REQUIRED, Config, config_file
from coven.errors import CovenError

DENY_KEY: Final = ("permissions", "deny")
CROSS_SESSION_KEY: Final = ("crossSessionInbound",)
DENY_RULES: Final = (
    "Read(./.env*)",
    "Read(~/.ssh/**)",
    "Read(~/.aws/**)",
    "Read(~/.gnupg/**)",
)
SHIM_TEXT: Final = """#!/bin/sh
# coven shim: runs the coven CLI of the installed plugin. Written by `coven setup`;
# `coven setup --undo` removes it.
plugins="${CLAUDE_CODE_PLUGIN_CACHE_DIR:-${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins}"
if ! command -v python3 >/dev/null 2>&1; then
  echo "coven: python3 not found" >&2
  exit 1
fi
root=$(python3 -I -c '
import json, sys
try:
    entry = json.load(open(sys.argv[1]))["plugins"]["coven@coven"]
    if isinstance(entry, list):
        installs = [e for e in entry if isinstance(e, dict) and e.get("installPath")]
        entry = next((e for e in installs if e.get("scope") == "user"), installs[0] if installs else None)
    print(entry["installPath"])
except (OSError, ValueError, KeyError, TypeError):
    pass
' "$plugins/installed_plugins.json" 2>/dev/null)
if [ -z "$root" ]; then
  echo "coven: plugin coven@coven is not installed" >&2
  exit 1
fi
if [ ! -x "$root/bin/coven" ]; then
  echo "coven: $root/bin/coven is missing; reinstall the plugin" >&2
  exit 1
fi
exec "$root/bin/coven" "$@"
"""
JsonObject = dict[str, object]


class _Missing:
    def __repr__(self) -> str:
        return "MISSING"


MISSING: Final = _Missing()


@dataclass(frozen=True)
class Change:
    """One key of one file: `key` is empty when the whole file is the value (the shim)."""

    file: Path
    key: tuple[str, ...]
    before: object
    after: object
    # Values coven wrote earlier that this change replaced: undo also accepts them as its own.
    previously: tuple[object, ...] = ()


@dataclass(frozen=True)
class Choices:
    deny_secrets: bool = False
    cross_session_accept: bool = False
    shim: bool = False
    pm_cap: int = 2
    memory_path: Path = field(default_factory=lambda: Config().memory_path)
    memory_remote: str | None = None
    guard: str | None = None


def settings_file() -> Path:
    return paths.claude_dir() / "settings.json"


def shim_file() -> Path:
    return Path.home() / ".local" / "bin" / "coven"


def owned_file() -> Path:
    return paths.config_dir() / "owned.json"


# ---- reading and writing files ----------------------------------------------------------


class _DuplicateKeyError(ValueError):
    def __init__(self, key: str) -> None:
        super().__init__(key)
        self.key = key


def _reject_duplicates(pairs: list[tuple[str, object]]) -> JsonObject:
    """`json.loads` keeps the last of two equal keys; setup would silently drop the other."""
    found: JsonObject = {}
    for key, value in pairs:
        if key in found:
            raise _DuplicateKeyError(key)
        found[key] = value
    return found


def _load_json(file: Path) -> JsonObject:
    """The JSON object in `file`; `{}` when the file is missing or empty."""
    try:
        text = file.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeDecodeError) as error:
        raise CovenError(f"{file}: cannot be read ({error})") from error
    if not text.strip():
        return {}
    try:
        data = json.loads(text, object_pairs_hook=_reject_duplicates)
    except _DuplicateKeyError as error:
        raise CovenError(
            f"{file}: duplicate key {error.key!r}; coven cannot tell which value counts and "
            "changed nothing"
        ) from error
    except json.JSONDecodeError as error:
        raise CovenError(
            f"{file}: line {error.lineno}: not valid JSON ({error.msg}); coven edits plain JSON "
            "only (no comments) and changed nothing"
        ) from error
    if not isinstance(data, dict):
        raise CovenError(f"{file}: must hold a JSON object, not {type(data).__name__}")
    return data


def _read_text(file: Path) -> str | _Missing:
    try:
        return file.read_text(encoding="utf-8")
    except FileNotFoundError:
        return MISSING
    except (OSError, UnicodeDecodeError) as error:
        raise CovenError(f"{file}: cannot be read ({error})") from error


def _stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")


def _utf8(file: Path, text: str) -> bytes:
    """The bytes to write; fails before any backup or record when the text cannot be UTF-8."""
    try:
        return text.encode("utf-8")
    except UnicodeEncodeError as error:
        raise CovenError(
            f"{file}: holds text that cannot be written as UTF-8 (a lone surrogate); "
            "fix it, nothing was changed"
        ) from error


def write_atomic(file: Path, payload: bytes, *, mode: int, backup: bool) -> None:
    """Temp file in the same folder, synced, then a rename; follows a symlinked `file`."""
    target = file.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    if backup and target.exists():
        shutil.copy2(target, target.with_name(f"{target.name}.coven-backup-{_stamp()}"))
    handle, temp = tempfile.mkstemp(prefix="coven-", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(payload)
            out.flush()
            os.fsync(out.fileno())
        os.chmod(temp, mode)
        os.replace(temp, target)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise


def _render(data: JsonObject) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def _json_mode(file: Path) -> int:
    """The file's own mode; 0600 for a file coven creates."""
    try:
        return stat.S_IMODE(file.stat().st_mode)
    except FileNotFoundError:
        return 0o600


def read_setting(key: tuple[str, ...]) -> object:
    """The value at `key` in the Claude Code settings file; `MISSING` when it is not there."""
    return _get(_load_json(settings_file()), key)


# ---- keys inside a JSON object ---------------------------------------------------------


def _get(data: JsonObject, key: tuple[str, ...]) -> object:
    node: object = data
    for part in key:
        if not isinstance(node, dict) or part not in node:
            return MISSING
        node = node[part]
    return node


def _set(data: JsonObject, key: tuple[str, ...], value: object, file: Path) -> None:
    node = data
    for depth, part in enumerate(key[:-1]):
        child = node.setdefault(part, {})
        if not isinstance(child, dict):
            raise CovenError(f"{file}: {'.'.join(key[: depth + 1])} is not an object")
        node = child
    node[key[-1]] = value


def _delete(data: JsonObject, key: tuple[str, ...]) -> None:
    """Remove the key, then the parent objects that this left empty."""
    chain = [data]
    for part in key[:-1]:
        child = chain[-1].get(part)
        if not isinstance(child, dict):
            return
        chain.append(child)
    chain[-1].pop(key[-1], None)
    for depth in range(len(key) - 1, 0, -1):
        if chain[depth]:
            break
        del chain[depth - 1][key[depth - 1]]


def _same(left: object, right: object) -> bool:
    if isinstance(left, _Missing) or isinstance(right, _Missing):
        return left is right
    return json.dumps(left, sort_keys=True) == json.dumps(right, sort_keys=True)


def _path(key: tuple[str, ...]) -> str:
    return ".".join(key)


# ---- planning -------------------------------------------------------------------------


def _validate(choices: Choices) -> None:
    if choices.pm_cap < 1:
        raise CovenError("pm_cap must be a whole number of 1 or more")
    if choices.guard is not None and choices.guard != GUARD_REQUIRED:
        raise CovenError(f'guard must be "{GUARD_REQUIRED}" or unset')
    remote = choices.memory_remote
    if remote is not None and (not remote.strip() or remote.startswith("-")):
        raise CovenError("memory_remote must be a repository name or URL")


def _want(data: JsonObject, file: Path, key: tuple[str, ...], value: object) -> Change | None:
    current = _get(data, key)
    if _same(current, value):
        return None
    _set(copy.deepcopy(data), key, value, file)  # fails now, not at apply, on a non-object parent
    return Change(file, key, current, value)


def _plan_settings(choices: Choices) -> list[Change]:
    file = settings_file()
    data = _load_json(file)
    changes: list[Change | None] = []
    if choices.deny_secrets:
        current = _get(data, DENY_KEY)
        if not isinstance(current, _Missing) and not isinstance(current, list):
            raise CovenError(f"{file}: {_path(DENY_KEY)} must be a list")
        existing = [] if isinstance(current, _Missing) else current
        merged = [*existing, *(rule for rule in DENY_RULES if rule not in existing)]
        changes.append(_want(data, file, DENY_KEY, merged))
    if choices.cross_session_accept:
        changes.append(_want(data, file, CROSS_SESSION_KEY, "accept"))
    return [change for change in changes if change]


def _plan_config(choices: Choices) -> list[Change]:
    file = config_file()
    data = _load_json(file)
    wanted: list[tuple[str, object]] = [
        ("pm_cap", choices.pm_cap),
        ("memory_path", str(choices.memory_path)),
    ]
    if choices.memory_remote is not None:
        wanted.append(("memory_remote", choices.memory_remote))
    if choices.guard is not None:
        wanted.append(("guard", choices.guard))
    changes = (_want(data, file, (name,), value) for name, value in wanted)
    return [change for change in changes if change]


def _plan_shim() -> list[Change]:
    file = shim_file()
    current = _read_text(file)
    if current == SHIM_TEXT:
        return []
    if not isinstance(current, _Missing):
        raise CovenError(f"{file} exists and is not coven's shim; move it or skip --shim")
    return [Change(file, (), current, SHIM_TEXT)]


def plan(choices: Choices) -> list[Change]:
    """What `apply` would change; reads files, writes nothing."""
    _validate(choices)
    changes = (
        _plan_settings(choices) if choices.deny_secrets or choices.cross_session_accept else []
    )
    changes += _plan_config(choices)
    if choices.shim:
        changes += _plan_shim()
    return changes


def _show(value: object) -> str:
    return "(missing)" if isinstance(value, _Missing) else json.dumps(value, ensure_ascii=False)


def describe(change: Change) -> str:
    """One readable line per change: `file: key.path: before -> after`."""
    if not change.key:
        lines = str(change.after).count("\n")
        verb = "installed" if isinstance(change.before, _Missing) else "updated"
        return f"{change.file}: coven shim: {verb} ({lines} lines)"
    return f"{change.file}: {_path(change.key)}: {_show(change.before)} -> {_show(change.after)}"


# ---- the record of owned changes ---------------------------------------------------------


@dataclass
class _Record:
    """What `owned.json` holds: the changes coven made and the files it created."""

    changes: list[Change] = field(default_factory=list)
    created: list[str] = field(default_factory=list)


def _encode_value(value: object) -> JsonObject:
    return {"present": False} if isinstance(value, _Missing) else {"present": True, "value": value}


def _decode_value(raw: object, file: Path) -> object:
    if not isinstance(raw, dict) or not isinstance(raw.get("present"), bool):
        raise CovenError(f"{file}: malformed entry; fix or delete the file")
    return raw.get("value") if raw["present"] else MISSING


def _decode_previously(raw: object, file: Path) -> tuple[object, ...]:
    if not isinstance(raw, list):
        raise CovenError(f"{file}: malformed entry; fix or delete the file")
    return tuple(_decode_value(value, file) for value in raw)


def _load_owned() -> _Record:
    file = owned_file()
    data = _load_json(file)
    entries = data.get("changes", [])
    created = data.get("created", [])
    if not isinstance(entries, list) or not isinstance(created, list):
        raise CovenError(f"{file}: changes and created must be lists; fix or delete the file")
    changes = []
    for entry in entries:
        key = entry.get("key") if isinstance(entry, dict) else None
        name = entry.get("file") if isinstance(entry, dict) else None
        if not isinstance(key, list) or not isinstance(name, str):
            raise CovenError(f"{file}: malformed entry; fix or delete the file")
        changes.append(
            Change(
                Path(name),
                tuple(str(part) for part in key),
                _decode_value(entry.get("before"), file),
                _decode_value(entry.get("after"), file),
                _decode_previously(entry.get("previously", []), file),
            )
        )
    return _Record(changes, [str(name) for name in created])


def _save_owned(record: _Record) -> None:
    file = owned_file()
    if not record.changes and not record.created:
        file.unlink(missing_ok=True)
        return
    entries = [
        {
            "file": str(change.file),
            "key": list(change.key),
            "before": _encode_value(change.before),
            "after": _encode_value(change.after),
            "previously": [_encode_value(value) for value in change.previously],
        }
        for change in record.changes
    ]
    body = {"version": 1, "changes": entries, "created": record.created}
    text = json.dumps(body, indent=2, ensure_ascii=False) + "\n"
    write_atomic(file, _utf8(file, text), mode=0o600, backup=False)


def _merged(earlier: Change, later: Change) -> Change:
    """One change from the first `before` to the last `after`, when `later` built on `earlier`."""
    if not _same(earlier.after, later.before):
        return later
    replaced = (*earlier.previously, earlier.after)
    return Change(later.file, later.key, earlier.before, later.after, replaced)


def _record_intent(record: _Record, group: list[Change], *, creates: bool) -> None:
    """Save `group` as owned before its file is written, so a crash leaves something to undo.

    A key coven already owns keeps its first `before`. Undo ignores a recorded key that still
    holds its `before` value, so an intent whose write never happened costs nothing.
    """
    for change in group:
        match = next(
            (o for o in record.changes if (o.file, o.key) == (change.file, change.key)), None
        )
        if match is not None:
            record.changes.remove(match)
        merged = change if match is None else _merged(match, change)
        if not _same(merged.before, merged.after):
            record.changes.append(merged)
    if creates and str(group[0].file) not in record.created:
        record.created.append(str(group[0].file))
    _save_owned(record)


# ---- apply ---------------------------------------------------------------------------


def _by_file(changes: list[Change]) -> dict[Path, list[Change]]:
    grouped: dict[Path, list[Change]] = {}
    for change in changes:
        grouped.setdefault(change.file, []).append(change)
    return grouped


def _changed_since_planning(change: Change) -> CovenError:
    return CovenError(
        f"{change.file}: {_path(change.key) or 'the file'} changed since the plan was made; "
        "nothing written for it, run setup again"
    )


def _apply_json(record: _Record, file: Path, group: list[Change]) -> None:
    existed = file.exists()
    data = _load_json(file)
    for change in group:
        if not _same(_get(data, change.key), change.before):
            raise _changed_since_planning(change)
        _set(data, change.key, change.after, file)
    payload = _utf8(file, _render(data))
    _record_intent(record, group, creates=not existed)
    write_atomic(file, payload, mode=_json_mode(file), backup=True)


def _apply_text(record: _Record, change: Change) -> None:
    if not _same(_read_text(change.file), change.before):
        raise _changed_since_planning(change)
    _record_intent(record, [change], creates=False)
    write_atomic(change.file, str(change.after).encode("utf-8"), mode=0o755, backup=False)


def apply(changes: list[Change]) -> None:
    """Write the changes (each file atomically, after a backup) and record them as owned.

    The record is loaded first, so one that cannot be read stops everything before a write.
    """
    record = _load_owned()
    for file, group in _by_file(changes).items():
        if all(not change.key for change in group):
            for change in group:
                _apply_text(record, change)
        else:
            _apply_json(record, file, group)


# ---- undo ----------------------------------------------------------------------------

Outcome = Literal["restored", "skipped", "unchanged"]


def _without_one_each(current: list[object], added: list[object]) -> list[object]:
    kept = list(current)
    for entry in added:
        if entry in kept:
            kept.remove(entry)
    return kept


def _restore_list(data: JsonObject, change: Change, file: Path) -> Outcome:
    current = _get(data, change.key)
    if not isinstance(current, list) or not isinstance(change.after, list):
        return "unchanged" if _same(current, change.before) else "skipped"
    original = change.before if isinstance(change.before, list) else []
    added = [entry for entry in change.after if entry not in original]
    kept = _without_one_each(current, added)
    restored: object = MISSING if not kept and isinstance(change.before, _Missing) else kept
    if _same(restored, current):
        return "unchanged"
    if isinstance(restored, _Missing):
        _delete(data, change.key)
    else:
        _set(data, change.key, restored, file)
    return "restored"


def _restore_key(data: JsonObject, change: Change, file: Path) -> Outcome:
    """Put the key back when it still holds what coven wrote."""
    if isinstance(change.after, list):
        return _restore_list(data, change, file)
    current = _get(data, change.key)
    if not any(_same(current, written) for written in (change.after, *change.previously)):
        return "unchanged" if _same(current, change.before) else "skipped"
    if isinstance(change.before, _Missing):
        _delete(data, change.key)
    else:
        _set(data, change.key, change.before, file)
    return "restored"


def _skipped(change: Change) -> str:
    where = f"{change.file}: {_path(change.key)}" if change.key else str(change.file)
    return f"{where}: changed since coven wrote it, left as is"


def _undo_json(
    file: Path, group: list[Change], *, created: bool, dry_run: bool
) -> tuple[list[str], list[str]]:
    data = _load_json(file)
    original = copy.deepcopy(data)
    restores: list[str] = []
    skipped: list[str] = []
    for change in group:
        current = _get(data, change.key)
        outcome = _restore_key(data, change, file)
        if outcome == "restored":
            restores.append(describe(Change(file, change.key, current, _get(data, change.key))))
        elif outcome == "skipped":
            skipped.append(_skipped(change))
    if created and not data and file.exists():
        restores.append(f"{file}: removed (coven created it and nothing else is in it)")
        if not dry_run:
            file.unlink()
    elif data != original and not dry_run:
        payload = _utf8(file, _render(data))
        write_atomic(file, payload, mode=_json_mode(file), backup=True)
    return restores, skipped


def _undo_text(change: Change, *, dry_run: bool) -> tuple[list[str], list[str]]:
    current = _read_text(change.file)
    if isinstance(current, _Missing):
        return [], []
    if not _same(current, change.after):
        return [], [_skipped(change)]
    if not dry_run:
        change.file.unlink()
    return [f"{change.file}: coven shim: removed"], []


def _undo(*, dry_run: bool) -> tuple[list[str], list[str]]:
    record = _load_owned()
    restores: list[str] = []
    skipped: list[str] = []
    for file, group in _by_file(record.changes).items():
        if all(not change.key for change in group):
            done = [_undo_text(change, dry_run=dry_run) for change in group]
        else:
            created = str(file) in record.created
            done = [_undo_json(file, group, created=created, dry_run=dry_run)]
        for file_restores, file_skipped in done:
            restores += file_restores
            skipped += file_skipped
        if not dry_run:
            record.changes = [change for change in record.changes if change.file != file]
            _save_owned(record)
    if not dry_run:
        _save_owned(_Record())
    return restores, skipped


def preview_undo() -> tuple[list[str], list[str]]:
    """What `undo` would restore and what it would leave as is; writes nothing."""
    return _undo(dry_run=True)


def undo() -> list[str]:
    """Reverse the recorded changes that still hold; return one line per key left as is.

    Skipped keys are released: they are no longer coven's, so the record forgets them.
    """
    return _undo(dry_run=False)[1]
