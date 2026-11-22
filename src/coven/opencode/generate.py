"""The generated OpenCode config and plugin copy, written into the profile as one swap."""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Final

from coven import paths
from coven.opencode.profile import AGENT_ROLES, ROLES, Layout, OpencodeError, Profile, save_profile

GUARD_TIMEOUT_MS: Final = 5000
# The provider package OpenCode loads for an OpenAI-compatible gateway.
OPENAI_COMPATIBLE: Final = "@opencode/ai/providers/openai-compatible"
CONFIG_FILE: Final = "opencode.jsonc"
RUN_FOLDERS: Final = ("data", "cache", "state", "home", "tmp")


def plugin_source() -> Path:
    return paths.root() / "opencode" / "src"


def plugin_options(profile: Profile, coven_root: Path) -> dict[str, object]:
    return {
        "covenRoot": str(coven_root),
        "toolChoiceAuto": ["litellm"] if profile.preset == "litellm" else [],
        "guardTimeoutMs": GUARD_TIMEOUT_MS,
    }


def _provider(profile: Profile) -> dict[str, object]:
    ids = sorted({profile.model(role) for role in ROLES})
    return {
        "name": "LiteLLM",
        "package": OPENAI_COMPATIBLE,
        "env": [profile.key_env],
        "settings": {"baseURL": profile.base_url},
        "models": {model_id: {"name": model_id} for model_id in ids},
    }


def config_data(profile: Profile, layout: Layout, coven_root: Path) -> dict[str, object]:
    """The `opencode.jsonc` content, keys in the order the file shows them."""

    def chosen(role: str) -> dict[str, str]:
        return {"model": f"{profile.provider_id}/{profile.model(role)}"}

    data: dict[str, object] = {
        "update": "disable",
        "model": chosen("lead")["model"],
        "agents": {role: chosen(role) for role in (*AGENT_ROLES, "title")},
    }
    if profile.preset == "litellm":
        data["providers"] = {"litellm": _provider(profile)}
    data["plugins"] = [
        {"package": str(layout.plugin_dir), "options": plugin_options(profile, coven_root)}
    ]
    return data


def render_config(profile: Profile, layout: Layout, coven_root: Path) -> str:
    """Plain JSON, no comments, so doctor and the guard test parse it with a JSON parser."""
    return json.dumps(config_data(profile, layout, coven_root), indent=2) + "\n"


def _copy_plugin(source: Path, target: Path) -> None:
    if not (source / "index.ts").is_file():
        raise OpencodeError(f"the coven plugin source is missing: {source / 'index.ts'}")
    for file in sorted(source.rglob("*.ts")):
        if file.is_file():
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


def write_profile(profile: Profile, layout: Layout, coven_root: Path, source: Path) -> None:
    """Write the config dir, the plugin copy and `profile.json`, all of them or none."""
    work = layout.root / f".tmp-{os.getpid()}"
    config_new, plugin_new = work / "config-opencode", work / "plugin-coven"
    targets = [(config_new, layout.config_dir), (plugin_new, layout.plugin_dir)]
    shutil.rmtree(work, ignore_errors=True)
    try:
        config_new.mkdir(parents=True)
        (config_new / CONFIG_FILE).write_text(render_config(profile, layout, coven_root))
        _copy_plugin(source, plugin_new)
        for _, target in targets:
            target.parent.mkdir(parents=True, exist_ok=True)
        _swap_and_save(targets, lambda: save_profile(layout, profile))
        for _, target in targets:
            shutil.rmtree(_old(target), ignore_errors=True)
        for name in RUN_FOLDERS:
            (layout.root / name).mkdir(exist_ok=True)
    finally:
        shutil.rmtree(work, ignore_errors=True)
