"""The check that `coven opencode` runs from a full copy of the coven repo, not a wheel."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from functools import wraps
from pathlib import Path
from typing import Final

from coven import paths
from coven.opencode.profile import OpencodeError

Handler = Callable[[argparse.Namespace], int]

NEEDS_CLONE: Final = (
    "coven opencode needs a full copy of the coven repo (a git clone): opencode/ and hooks/ are "
    "not in an installed wheel. Clone https://github.com/WitchyNibbles/coven and run its bin/coven."
)


def require_checkout(root: Path) -> None:
    """Raise unless `root` holds the plugin's source and the deny-secrets hook the profile points at."""
    if not ((root / "opencode").is_dir() and (root / "hooks" / "deny_secrets.py").is_file()):
        raise OpencodeError(NEEDS_CLONE)


def needs_checkout(handler: Handler) -> Handler:
    """`handler`, run only from a full copy of the repo."""

    @wraps(handler)
    def checked(args: argparse.Namespace) -> int:
        require_checkout(paths.root())
        return handler(args)

    return checked
