#!/usr/bin/env python3
"""SessionStart hook: hand the session coven's routing instructions.

Prints `{"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": <text>}}`
with the text of `instructions/routing.md` (next to this folder). The payload on stdin is not
needed and is not read. A plugin cannot ship a CLAUDE.md, so this is how the routing reaches every
session start, resume, clear and compaction.

Fails open: on any error (the text is missing or unreadable) it prints nothing, exits 0 and
appends the error to the hook log with `hooklog.log_event` (a sibling module).
"""

import functools
import importlib.util
import json
import logging
import sys
from pathlib import Path
from types import ModuleType

HOOK = "session-context"
ROUTING = Path(__file__).resolve().parent.parent / "instructions" / "routing.md"


@functools.cache
def _sibling(name: str) -> ModuleType:
    """The module `<name>.py` next to this script, loaded by path: `python3 -I` leaves this folder
    off sys.path and the hooks are not a package."""
    path = Path(__file__).resolve().with_name(f"{name}.py")
    spec = importlib.util.spec_from_file_location(f"coven_hook_{name}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    old, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = old
    return module


def _log_failure(error: str) -> None:
    """Record a failure in the hook log; the log is best effort, so this never raises."""
    try:
        _sibling("hooklog").log_event(HOOK, {"error": error})
    except Exception:
        logging.exception("coven %s: could not write the hook log", HOOK)


def main() -> int:
    try:
        text = ROUTING.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        _log_failure(f"{ROUTING}: cannot be read ({error})")
        return 0
    print(
        json.dumps(
            {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}}
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
