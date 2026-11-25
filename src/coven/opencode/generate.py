"""The generated OpenCode config and plugin copy, written into the profile as one swap."""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Final

from coven import paths
from coven.opencode import memory, superpowers
from coven.opencode.content import CONTENT
from coven.opencode.profile import AGENT_ROLES, Layout, OpencodeError, Profile, save_profile

GUARD_TIMEOUT_MS: Final = 5000
# The provider package OpenCode loads for an OpenAI-compatible gateway.
OPENAI_COMPATIBLE: Final = "@opencode/ai/providers/openai-compatible"
CONFIG_FILE: Final = "opencode.jsonc"
RUN_FOLDERS: Final = ("data", "cache", "state", "home", "tmp")
AGENTS_FILE: Final = "AGENTS.md"
# The second guard layer, global so OpenCode adds it to every agent; last match wins.
SECRET_RULES: Final = (
    ("read", "*.env", "deny"),
    ("read", "*.env.*", "deny"),
    ("read", "*.env.example", "allow"),
    ("edit", "*.env", "deny"),
    ("edit", "*.env.*", "deny"),
)
# Last in the list, so it beats the built-in agents' `shell allow` and `* allow`. No MCP rule:
# the profile loads no MCP server, and a glob like `*_*` would also hit `external_directory`,
# `coven_ping` and `await_agents`.
ASK_RULES: Final = (("shell", "*", "ask"), ("webfetch", "*", "ask"))


def plugin_source() -> Path:
    return paths.root() / "opencode" / "src"


def plugin_options(profile: Profile, coven_root: Path) -> dict[str, object]:
    return {
        "covenRoot": str(coven_root),
        "toolChoiceAuto": ["litellm"] if profile.preset == "litellm" else [],
        "guardTimeoutMs": GUARD_TIMEOUT_MS,
    }


def _provider(profile: Profile) -> dict[str, object]:
    return {
        "name": "LiteLLM",
        "package": OPENAI_COMPATIBLE,
        "env": [profile.key_env],
        "settings": {"baseURL": profile.base_url},
        "models": {model_id: _model(profile, model_id) for model_id in profile.model_ids},
    }


def _model(profile: Profile, model_id: str) -> dict[str, object]:
    limit = profile.limits.get(model_id)
    return {"name": model_id} | ({"limit": limit.data()} if limit else {})


def _rule(action: str, resource: str, effect: str) -> dict[str, str]:
    return {"action": action, "resource": resource, "effect": effect}


def permissions(layout: Layout, store: Path | None) -> list[dict[str, str]]:
    """The secret rules, the allows for the files the prompts and the routing point at, a deny of
    edits to the installed skills (the allow on that folder covers writes too), then the asks for
    shell and webfetch, last so no agent's own rule re-allows them."""
    rules = [_rule(*rule) for rule in SECRET_RULES]
    skills = layout.config_dir / "skills"
    outside = [skills, *([store] if store else [])]
    allows = [_rule("external_directory", f"{folder}/*", "allow") for folder in outside]
    skills_deny = [_rule("edit", f"{skills}/*", "deny")]
    return rules + allows + skills_deny + [_rule(*rule) for rule in ASK_RULES]


def config_data(
    profile: Profile, layout: Layout, coven_root: Path, store: Path | None = None
) -> dict[str, object]:
    """The `opencode.jsonc` content, keys in the order the file shows them."""

    def model_ref(role: str) -> str:
        return f"{profile.preset}/{profile.model(role)}"

    data: dict[str, object] = {
        "update": "disable",
        "model": model_ref("lead"),
        "agents": {role: {"model": model_ref(role)} for role in (*AGENT_ROLES, "title")},
    }
    if profile.preset == "litellm":
        data["providers"] = {"litellm": _provider(profile)}
    data["plugins"] = [
        {"package": str(layout.plugin_dir), "options": plugin_options(profile, coven_root)}
    ]
    data["experimental"] = {"todo": True}
    data["permissions"] = permissions(layout, store)
    return data


def render_config(
    profile: Profile, layout: Layout, coven_root: Path, store: Path | None = None
) -> str:
    """Plain JSON, no comments, so doctor and the guard test parse it with a JSON parser."""
    return json.dumps(config_data(profile, layout, coven_root, store), indent=2) + "\n"


def _copy_plugin(source: Path, target: Path) -> None:
    if not (source / "index.ts").is_file():
        raise OpencodeError(f"the coven plugin source is missing: {source / 'index.ts'}")
    for file in sorted(source.rglob("*.ts")):
        if file.is_file() and "node_modules" not in file.relative_to(source).parts:
            destination = target / file.relative_to(source)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(file, destination)


def _old(target: Path) -> Path:
    return target.with_name(target.name + ".old")


def _install(new: Path, target: Path) -> Callable[[], None]:
    """Put `new` at `target`, keeping what was there as `<target>.old`; returns the undo."""
    old = _old(target)
    shutil.rmtree(old, ignore_errors=True)
    had_target = target.exists()
    if had_target:
        target.rename(old)
    try:
        new.rename(target)
    except OSError:
        if had_target:
            old.rename(target)
        raise

    def undo() -> None:
        shutil.rmtree(target, ignore_errors=True)
        if had_target:
            old.rename(target)

    return undo


def _swap_and_save(targets: list[tuple[Path, Path]], save: Callable[[], None]) -> None:
    """Install every (new, target) pair, then `save`; on any failure put the old ones back."""
    undos: list[Callable[[], None]] = []
    try:
        for new, target in targets:
            undos.append(_install(new, target))
        save()
    except BaseException:
        for undo in reversed(undos):
            undo()
        raise


def _write_config_dir(
    config_new: Path, profile: Profile, layout: Layout, coven_root: Path, content: Path
) -> list[str]:
    """Fill the new config dir; returns the superpowers skills that were not found."""
    if not (content / AGENTS_FILE).is_file():
        raise OpencodeError(f"the generated content is missing: {content / AGENTS_FILE}")
    store = memory.store_path()
    shutil.copytree(content, config_new)
    missing = superpowers.copy(superpowers.find(), config_new / "skills")
    routing = (content / AGENTS_FILE).read_text(encoding="utf-8").rstrip("\n")
    tail = memory.agents_tail(store, missing)
    (config_new / AGENTS_FILE).write_text(f"{routing}\n\n{tail}", encoding="utf-8")
    (config_new / CONFIG_FILE).write_text(render_config(profile, layout, coven_root, store))
    return missing


def write_profile(
    profile: Profile,
    layout: Layout,
    coven_root: Path,
    source: Path,
    *,
    content: Path = CONTENT,
) -> list[str]:
    """Write the config dir, the plugin copy and `profile.json`, all of them or none.

    Returns the superpowers skills setup could not find.
    """
    work = layout.root / f".tmp-{os.getpid()}"
    config_new, plugin_new = work / "config-opencode", work / "plugin-coven"
    targets = [(config_new, layout.config_dir), (plugin_new, layout.plugin_dir)]
    shutil.rmtree(work, ignore_errors=True)
    try:
        missing = _write_config_dir(config_new, profile, layout, coven_root, content)
        _copy_plugin(source, plugin_new)
        for _, target in targets:
            target.parent.mkdir(parents=True, exist_ok=True)
        _swap_and_save(targets, lambda: save_profile(layout, profile))
        for _, target in targets:
            shutil.rmtree(_old(target), ignore_errors=True)
        for name in RUN_FOLDERS:
            (layout.root / name).mkdir(exist_ok=True)
        return missing
    finally:
        shutil.rmtree(work, ignore_errors=True)
