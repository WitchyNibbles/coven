#!/usr/bin/env python3
"""SessionStart hook: hand the session coven's routing instructions.

Prints `{"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": <text>}}`
with the text of `instructions/routing.md` (next to this folder). The payload on stdin is not
needed and is not read. A plugin cannot ship a CLAUDE.md, so this is how the routing reaches every
session start, resume, clear and compaction.

Fails open: on any error (the text is missing or unreadable) it prints nothing, exits 0 and
appends the error to the hook log with `hooklog.log_event` (a sibling module).
"""

import importlib
import json
import sys
from pathlib import Path

# Hooks run as `python3 -I`: write no bytecode into the installed plugin folder.
sys.dont_write_bytecode = True
# `-I` leaves this folder off sys.path, so put it there for hook_support, the one module found so.
sys.path.insert(0, str(Path(__file__).resolve().parent))
# By name, not an import statement: ruff rejects one below code (E402) and suppressions are banned.
hook_support = importlib.import_module("hook_support")

HOOK = "session-context"
ROUTING = Path(__file__).resolve().parent.parent / "instructions" / "routing.md"


def main() -> int:
    try:
        text = ROUTING.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        hook_support.log_failure(HOOK, f"{ROUTING}: cannot be read ({error})")
        return 0
    print(
        json.dumps(
            {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}}
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
