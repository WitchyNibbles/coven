"""The production guard's entry, which hooks.json runs on every tool call.

Most users have no guard. This gate reads the config with the reads the guard uses (guard_config.py:
one JSON read, one `stat`), and when the guard is off it exits 0 at once: it does not read stdin and
does not import prod_guard.py, whose logging, subprocess, dataclasses and ipaddress imports and 3000
lines would otherwise be paid for on every call.

In every other case (the guard is on, the config cannot be read, or anything in this file goes
wrong) it imports prod_guard and returns `prod_guard.cli()`. The guard then reads the config again
under its own deadline and error handling, so the on path, the error path and fail-closed behaviour
are exactly those of running prod_guard.py itself.
"""

import os
import sys

# What the gate's own check can raise besides guard_config.ConfigError (which it turns into
# "not off"): a broken or missing sibling module, no home folder, an odd file system.
GATE_ERRORS = (
    ImportError,
    SyntaxError,
    OSError,
    RuntimeError,
    ValueError,
    LookupError,
    TypeError,
    AttributeError,
    NameError,
    MemoryError,
)

# `-I` leaves this folder off sys.path.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def guard_is_off() -> bool:
    import guard_config

    return guard_config.guard_is_off()


def main() -> int:
    try:
        if guard_is_off():
            return 0
    except GATE_ERRORS:
        pass  # the guard reads the config again and decides
    import prod_guard

    return prod_guard.cli()


if __name__ == "__main__":
    sys.exit(main())
