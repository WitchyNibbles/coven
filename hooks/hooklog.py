"""Append one JSON line per hook event to `${XDG_CACHE_HOME:-~/.cache}/coven/logs/<hook>.jsonl`.

A SessionStart hook's stderr is invisible to the user, so this log is how a silent failure becomes
visible (`coven doctor` shows the last errors). Hooks load this module by path, so it cannot import
`coven.paths`: it repeats that module's cache rule below. When the log reaches 1 MB it moves to
`<hook>.jsonl.1`, replacing the one before. `log_event` never raises: logging must not turn a
working hook into a failing one.
"""

import json
import os
from datetime import UTC, datetime
from pathlib import Path

MAX_BYTES = 1024 * 1024


def logs_dir() -> Path:
    """`${XDG_CACHE_HOME:-~/.cache}/coven/logs`, the rule of `coven.paths.logs_dir`."""
    configured = os.environ.get("XDG_CACHE_HOME")
    base = Path(configured) if configured else Path.home() / ".cache"
    return base / "coven" / "logs"


def log_event(hook: str, record: dict[str, object]) -> None:
    """Append `record` plus a `ts` to the log of `hook`; any failure is swallowed."""
    try:
        folder = logs_dir()
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = folder / f"{hook}.jsonl"
        if path.exists() and path.stat().st_size >= MAX_BYTES:
            path.replace(folder / f"{hook}.jsonl.1")
        line = json.dumps({"ts": datetime.now(UTC).isoformat(), **record}, default=str)
        descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(descriptor, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except (OSError, ValueError, TypeError, RuntimeError):
        return
