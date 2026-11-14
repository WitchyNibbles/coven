"""The failure and the warning every command reports the same way."""

import sys


class CovenError(Exception):
    """An expected failure with a message meant for the user; `cli.main` prints it, no traceback."""

    exit_code: int = 1


def warn(message: str) -> None:
    """Print a warning on stderr; the command goes on."""
    print(f"coven: warning: {message}", file=sys.stderr)
