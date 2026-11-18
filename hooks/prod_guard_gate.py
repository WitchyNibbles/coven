"""The production guard's entry, which hooks.json runs on every tool call.

Most users have no guard. This gate reads the config with the reads the guard uses (guard_config.py:
one JSON read, one `stat`), and when the guard is off it exits 0 at once: it does not read stdin and
does not import prod_guard.py, whose logging, subprocess, dataclasses and ipaddress imports and 3000
lines would otherwise be paid for on every call.

In every other case (the guard is on, the config cannot be read, the read takes longer than
GATE_DEADLINE, or anything in this file goes wrong) it imports prod_guard and returns
`prod_guard.cli()`. The guard then reads the config again under its own deadline and error
handling, so the on path, the error path and fail-closed behaviour are exactly those of running
prod_guard.py itself. The gate's deadline matters because a config read that hangs (a FIFO in place
of the file, a stalled mount) would otherwise run into Claude's hook timeout, which lets the call
through: GATE_DEADLINE plus the guard's own 20 s stay under the 30 s timeout.
"""

import os
import signal
import sys

# The longest the gate spends on its check before it hands over to the guard.
GATE_DEADLINE = 2.0


class GateTimeout(Exception):
    """The gate's check ran past GATE_DEADLINE."""


def _past_deadline(_signum: int, _frame: object) -> None:
    raise GateTimeout


# What the gate's own check can raise besides guard_config.ConfigError (which it turns into
# "not off"): a deadline, a broken or missing sibling module, no home folder, an odd file system.
GATE_ERRORS = (
    GateTimeout,
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
    """Whether the guard is off, read under GATE_DEADLINE. Any error, or a read past the deadline,
    says "not off" so that the guard decides. On return the timer is off and SIGALRM has the
    handler it had before: the guard sets up its own deadline from a clean state."""
    previous = signal.signal(signal.SIGALRM, _past_deadline)
    try:
        try:
            signal.setitimer(signal.ITIMER_REAL, GATE_DEADLINE)
            import guard_config

            return guard_config.guard_is_off()
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
    except GATE_ERRORS:
        return False
    finally:
        signal.signal(signal.SIGALRM, previous)


def main() -> int:
    if guard_is_off():
        return 0
    import prod_guard

    return prod_guard.cli()


if __name__ == "__main__":
    sys.exit(main())
