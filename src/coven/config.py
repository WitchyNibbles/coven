"""The user's coven config: `~/.config/coven/config.json`."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field, fields
from pathlib import Path

from coven import paths
from coven.errors import CovenError

GUARD_REQUIRED = "required"


def _default_memory_path() -> Path:
    return Path.home() / "coven-memory"


@dataclass(frozen=True)
class Config:
    memory_path: Path = field(default_factory=_default_memory_path)
    memory_remote: str | None = None
    pm_cap: int = 2
    # "required" once the store holds a committed guard file; None otherwise.
    guard: str | None = None


def config_file() -> Path:
    return paths.config_dir() / "config.json"


def load_config() -> Config:
    """The config; defaults for what the file leaves out or when it is missing."""
    file = config_file()
    try:
        text = file.read_text(encoding="utf-8")
    except FileNotFoundError:
        return Config()
    except (OSError, UnicodeDecodeError) as error:
        raise CovenError(f"{file}: cannot be read ({error})") from error
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        raise CovenError(f"{file}: not valid JSON ({error})") from error
    if not isinstance(data, dict):
        raise CovenError(f"{file}: must hold a JSON object")
    unknown = sorted(set(data) - {f.name for f in fields(Config)})
    if unknown:
        raise CovenError(f"{file}: unknown key(s): {', '.join(unknown)}")
    return _from_dict(data, file)


def _from_dict(data: dict[str, object], file: Path) -> Config:
    defaults = Config()
    memory_path = data.get("memory_path", str(defaults.memory_path))
    memory_remote = data.get("memory_remote")
    pm_cap = data.get("pm_cap", defaults.pm_cap)
    guard = data.get("guard")
    if not isinstance(memory_path, str):
        raise CovenError(f"{file}: memory_path must be a string")
    if memory_remote is not None and not isinstance(memory_remote, str):
        raise CovenError(f"{file}: memory_remote must be a string or null")
    if isinstance(pm_cap, bool) or not isinstance(pm_cap, int) or pm_cap < 1:
        raise CovenError(f"{file}: pm_cap must be a whole number of 1 or more")
    if guard is not None and guard != GUARD_REQUIRED:
        raise CovenError(f'{file}: guard must be "{GUARD_REQUIRED}" or null')
    store = Path(memory_path).expanduser()
    if not store.is_absolute():
        raise CovenError(f"{file}: memory_path must be an absolute path")
    return Config(store, memory_remote, pm_cap, guard)


def save_config(config: Config) -> None:
    """Writes the config atomically: a temp file in the same folder, then a rename."""
    file = config_file()
    file.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "memory_path": str(config.memory_path),
        "memory_remote": config.memory_remote,
        "pm_cap": config.pm_cap,
        "guard": config.guard,
    }
    handle, temp = tempfile.mkstemp(prefix="coven-", suffix=".tmp", dir=file.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            out.write(json.dumps(data, indent=2) + "\n")
        os.replace(temp, file)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise
