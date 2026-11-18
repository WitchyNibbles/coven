"""The production guard's config reads, in a module that imports only os, sys and json.

Every tool call runs the guard's entry (prod_guard_gate.py), and most users have no guard: one JSON
read and one `stat` must be enough to know that, without importing the guard's logging, subprocess,
dataclasses or ipaddress, nor pathlib and typing (about 3 ms each on a 12 ms interpreter start).
This module therefore holds the reads as plain strings; prod_guard.py wraps them into its Path-based
`GuardConfig`, `read_config`, `config_file` and `off_reason`, so the gate and the guard read the
config by one set of rules.
"""

import importlib
import json
import os
import sys

LATCH_VALUE = "required"
GUARD_FILE_NAME = "systems/guard.json"
# The depth hook_support.loads allows; tests/hooks/test_guard_config.py checks the two agree.
MAX_DEPTH = 512
CONFIG_PARTS = (".config", "coven", "config.json")
DEFAULT_STORE_NAME = "coven-memory"


class ConfigError(Exception):
    """coven's config file cannot be read or is invalid."""


def _home() -> str:
    """The home folder, or the RuntimeError `Path.home()` raises when there is none."""
    home = os.path.expanduser("~")
    if home.startswith("~"):
        raise RuntimeError("Could not determine home directory.")
    return home


def config_path() -> str:
    """`~/.config/coven/config.json`: where `coven.paths.config_dir()` and memory-context look."""
    return os.path.join(_home(), *CONFIG_PARTS)


def _loads(text: str) -> object:
    """`json.loads(text)`, or a ValueError when it nests deeper than MAX_DEPTH, as
    hook_support.loads does. A text with at most MAX_DEPTH brackets cannot nest deeper (a bracket
    inside a string only overcounts), so only the rare long one pays for the full scan."""
    if text.count("[") + text.count("{") <= MAX_DEPTH:
        return json.loads(text)
    folder = os.path.dirname(os.path.abspath(__file__))
    if folder not in sys.path:  # `-I` leaves it off
        sys.path.insert(0, folder)
    return importlib.import_module("hook_support").loads(text)


def _store(configured: str | None, file: str) -> str:
    if configured is None:
        return os.path.join(_home(), DEFAULT_STORE_NAME)
    store = os.path.expanduser(configured)
    if store.startswith("~"):  # an unknown user: Path.expanduser raises the same
        raise RuntimeError("Could not determine home directory.")
    if not os.path.isabs(store):
        raise ConfigError(f"{file}: memory_path must be an absolute path")
    return store


def read_settings() -> tuple[str, bool]:
    """The store folder and the latch of coven's config. With no config file that is the default
    store and no latch, so deleting the file cannot switch off a store that holds a guard.json.
    ConfigError when the file cannot be read or holds something else than expected."""
    file = config_path()
    try:
        with open(file, encoding="utf-8") as handle:
            text = handle.read()
    except FileNotFoundError:
        return os.path.join(_home(), DEFAULT_STORE_NAME), False
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError(f"{file}: cannot be read ({exc})") from None
    try:
        data = _loads(text)
    except ValueError as exc:
        raise ConfigError(f"{file}: not valid JSON ({exc})") from None
    if not isinstance(data, dict):
        raise ConfigError(f"{file}: must hold a JSON object")
    configured, guard = data.get("memory_path"), data.get("guard")
    if configured is not None and not isinstance(configured, str):
        raise ConfigError(f"{file}: memory_path must be a string")
    if guard is not None and guard != LATCH_VALUE:
        raise ConfigError(f'{file}: guard must be "{LATCH_VALUE}" or null')
    return _store(configured, file), guard == LATCH_VALUE


def off_reason_for(store: str, latch: bool) -> str | None:
    """Why the guard is off for this store and latch, or None when it is on. One stat or two, no
    git.

    With the latch a missing store folder is not off: the store held a guard.json once, and a
    deleted or moved folder must not unguard it. The git reads then refuse, naming the folder."""
    if latch:
        return None
    if not os.path.isdir(store):
        return f"no memory store folder at {store}"
    if os.path.lexists(os.path.join(store, GUARD_FILE_NAME)):
        return None
    return f'no "guard": "{LATCH_VALUE}" in the config and no {GUARD_FILE_NAME} in {store}'


def guard_is_off() -> bool:
    """Whether the guard is off for this machine's config. A config that cannot be read is not
    off: the guard decides what an unreadable config means."""
    try:
        store, latch = read_settings()
    except ConfigError:
        return False
    return off_reason_for(store, latch) is not None
