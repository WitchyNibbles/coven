"""The doctor checks that read the running server: coven's plugin and the skills it lists."""

from __future__ import annotations

import os
from pathlib import Path

from coven.doctor import one_line
from coven.opencode.canary import COVEN_PLUGIN
from coven.opencode.check_types import RESET, Context, Outcome


def plugin_check(ctx: Context) -> Outcome:
    if ctx.profile is None:
        return ctx.skip()
    plugins = ctx.server.view().plugins
    entry = next((plugin for plugin in plugins if plugin.get("id") == COVEN_PLUGIN), None)
    if entry is None:
        loaded = ", ".join(str(plugin.get("id")) for plugin in plugins) or "none"
        return Outcome("fail", f"the server did not load coven (plugins: {loaded})", RESET)
    state = entry.get("state")
    state = state if isinstance(state, dict) else {}
    if state.get("status") == "active":
        return Outcome("ok", "coven plugin active")
    cause = one_line(str(state.get("error") or ""))
    detail = f"coven plugin {state.get('status') or 'not active'}"
    return Outcome("fail", f"{detail}: {cause}" if cause else detail, RESET)


def _is_coven_skill(path: object, config_dir: Path) -> bool:
    """Built-in skills have a `skill://` path; the rest must live in the profile's config."""
    if not isinstance(path, str):
        return False
    if path.startswith("skill://"):
        return True
    own = config_dir / "skills"
    where = Path(os.path.normpath(path))  # `skills/../../x` must not read as inside `skills`
    return any(where.is_relative_to(folder) for folder in (own, own.resolve()))


def skills_check(ctx: Context) -> Outcome:
    if ctx.profile is None:
        return ctx.skip()
    skills = ctx.server.view().skills
    config_dir = ctx.layout.config_dir
    foreign = [str(s.get("id")) for s in skills if not _is_coven_skill(s.get("path"), config_dir)]
    if foreign:
        fix = (
            f"remove them from {ctx.layout.home}/.agents/skills "
            "or report an OpenCode isolation change"
        )
        return Outcome("fail", f"foreign skills: {', '.join(foreign)}", fix)
    return Outcome("ok", f"{len(skills)} skills, all built in or from the profile")
