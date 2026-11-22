"""The superpowers skills coven's routing names, copied from Claude Code's plugin install."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Final

from coven import paths, safe_json
from coven.opencode.rewrite import rename_skills

SKILLS: Final = (
    "brainstorming",
    "writing-plans",
    "test-driven-development",
    "systematic-debugging",
    "verification-before-completion",
    "using-git-worktrees",
    "finishing-a-development-branch",
    "executing-plans",
)
PLUGIN_PREFIX: Final = "superpowers@"
FOLDER_PREFIX: Final = "superpowers-"


def _install_paths(entry: object) -> list[Path]:
    """The `installPath` of each install in a plugin's entry: a list (version 2) or one object."""
    installs = entry if isinstance(entry, list) else [entry]
    found = (item.get("installPath") for item in installs if isinstance(item, dict))
    return [Path(path) for path in found if isinstance(path, str) and path]


def _read_plugins() -> dict[str, object]:
    """The `plugins` table of the installed-plugins file; empty when it cannot be read."""
    try:
        text = paths.installed_plugins_file().read_text(encoding="utf-8", errors="replace")
        data = safe_json.loads(text)
    except (OSError, ValueError):
        return {}
    plugins = data.get("plugins") if isinstance(data, dict) else None
    return plugins if isinstance(plugins, dict) else {}


def find() -> Path | None:
    """The `skills/` folder of the first superpowers install that has one, else `None`."""
    for key, entry in _read_plugins().items():
        if not key.startswith(PLUGIN_PREFIX):
            continue
        for install in _install_paths(entry):
            if (install / "skills").is_dir():
                return install / "skills"
    return None


def _copy_skill(source: Path, target: Path) -> None:
    """The skill's files; markdown gets only the skill-name rewrite, the rest is copied as is."""
    for file in sorted(path for path in source.rglob("*") if path.is_file()):
        destination = target / file.relative_to(source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if file.suffix == ".md":
            destination.write_text(
                rename_skills(file.read_text(encoding="utf-8")), encoding="utf-8"
            )
        else:
            shutil.copy2(file, destination)


def copy(source: Path | None, skills_dir: Path) -> list[str]:
    """Copy each of `SKILLS` into `skills_dir/superpowers-<name>/`; returns the names not found."""
    missing = []
    for name in SKILLS:
        if source is None or not (source / name / "SKILL.md").is_file():
            missing.append(name)
        else:
            _copy_skill(source / name, skills_dir / f"{FOLDER_PREFIX}{name}")
    return missing
