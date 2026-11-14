"""Every location coven reads or writes outside a repo, derived from the environment."""

from __future__ import annotations

import os
from pathlib import Path


def claude_dir() -> Path:
    """`${CLAUDE_CONFIG_DIR:-~/.claude}`."""
    configured = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(configured) if configured else Path.home() / ".claude"


def config_dir() -> Path:
    return Path.home() / ".config" / "coven"


def cache_dir() -> Path:
    """`${XDG_CACHE_HOME:-~/.cache}/coven`."""
    configured = os.environ.get("XDG_CACHE_HOME")
    base = Path(configured) if configured else Path.home() / ".cache"
    return base / "coven"


def logs_dir() -> Path:
    return cache_dir() / "logs"


def briefs_dir() -> Path:
    return cache_dir() / "briefs"


def installed_plugins_file() -> Path:
    """Claude Code's list of installed plugins; its folder moves with CLAUDE_CODE_PLUGIN_CACHE_DIR."""
    # shortcut: reads CLAUDE_CODE_PLUGIN_CACHE_DIR as the plugins root holding the file, the acceptance run confirms it.
    configured = os.environ.get("CLAUDE_CODE_PLUGIN_CACHE_DIR")
    plugins = Path(configured) if configured else claude_dir() / "plugins"
    return plugins / "installed_plugins.json"


def root() -> Path:
    """The folder holding `src/`, `standard/` and `bin/`: the checkout or the installed plugin."""
    return Path(__file__).resolve().parents[2]


def standard_dir() -> Path:
    return root() / "standard"
