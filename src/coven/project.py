"""Install the code standard's owned files into a repo and report drift."""

from __future__ import annotations

import argparse
import difflib
import importlib.util
import json
import os
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Final, Protocol

from coven import paths
from coven.errors import CovenError

STANDARD_DIR = ".coven"
WORKFLOW = ".github/workflows/coven-check.yml"
RUFF = f"{STANDARD_DIR}/ruff.toml"
TS_BASE = f"{STANDARD_DIR}/tsconfig.base.json"
ESLINT_BASE = f"{STANDARD_DIR}/eslint.config.mjs"
# Owned file in the repo -> its source under the coven checkout. The check scripts and the workflow
# go to every repo; ruff.toml only to a Python project, the two TypeScript files only to a
# TypeScript project, anywhere in the repo.
OWNED_PYTHON: dict[str, str] = {
    **{
        f"{STANDARD_DIR}/{name}": f"standard/{name}"
        for name in (
            "check.py",
            "check_python.py",
            "check_repo.py",
            "check_stages.py",
            "check_ts.py",
        )
    },
    RUFF: "standard/python/ruff.toml",
    WORKFLOW: "standard/workflows/coven-check.yml",
}
OWNED_TYPESCRIPT: dict[str, str] = {
    TS_BASE: "standard/typescript/tsconfig.base.json",
    ESLINT_BASE: "standard/typescript/eslint.config.mjs",
}
# What a person may have edited: coven shows a diff and overwrites it only with --force.
GUARDED: Final = frozenset({WORKFLOW, RUFF, *OWNED_TYPESCRIPT})
# The project kind each optional owned file is for; every repo gets the others.
KIND_OF: dict[str, str] = {RUFF: "python", **dict.fromkeys(OWNED_TYPESCRIPT, "typescript")}
KIND_NAMES = {"python": "Python", "typescript": "TypeScript"}
DEV_TOOLS = ("ruff", "mypy", "pytest", "pytest-cov", "diff-cover", "pytest-xdist")
_SPECIFIER_START = "<>=!~;[ ("
NO_LOCKFILE = (
    "no lockfile: install the dependencies with npm, pnpm, yarn or bun and commit the lockfile "
    "it writes"
)
# The base's rules are type-aware, so every linted file must be in a tsconfig.
IN_A_TSCONFIG = (
    "every TypeScript and JavaScript file git lists, configs such as eslint.config.* and vite or "
    'vitest configs included, must be in a tsconfig ("allowJs": true for .js and .mjs); re-run '
    "coven project once the project's tools run to list those that are not"
)
ROOT_IGNORES = (
    f'add {{ ignores: ["{STANDARD_DIR}/"] }} to the list it exports (the standard\'s own files are '
    "not the project's to lint)"
)
# A string (group 1, kept) or what is dropped: a comment, in JSON with comments or JavaScript; then
# in JSON with comments a trailing comma.
JSONC_COMMENT = re.compile(r'("(?:\\.|[^"\\\n])*")|//[^\n]*|/\*.*?\*/', re.DOTALL)
TRAILING_COMMA = re.compile(r'("(?:\\.|[^"\\\n])*")|,(?=\s*[}\]])')
JS_COMMENT = re.compile(
    r"""("(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*'|`(?:\\.|[^`\\])*`)|//[^\n]*|/\*.*?\*/""",
    re.DOTALL,
)
IGNORES_STANDARD = re.compile(r"""\bignores\s*:\s*\[[^\]]*["']\.coven(?:/|/\*\*)?["']""")


class ProjectError(CovenError):
    """The target directory cannot take the standard."""


class Project(Protocol):
    """A project check.py's `discover` found (its `Project`)."""

    @property
    def root(self) -> Path: ...
    @property
    def kind(self) -> str: ...
    @property
    def manager(self) -> str | None: ...


@dataclass(frozen=True)
class SetupResult:
    written: tuple[str, ...]
    unchanged: tuple[str, ...]
    wiring: tuple[str, ...]


def _load_check() -> ModuleType:
    """coven's standard/check.py; its `load_ts()` loads standard/check_ts.py next to it."""
    source = paths.standard_dir() / "check.py"
    spec = importlib.util.spec_from_file_location("coven_standard_check", source)
    if spec is None or spec.loader is None:
        raise ProjectError(f"cannot load {source}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _toplevel(project: Path) -> Path | None:
    """The top of the git work tree holding `project`, or None outside one."""
    try:
        done = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=project,
            capture_output=True,
            text=True,
            check=False,
        )
    except (FileNotFoundError, NotADirectoryError):
        return None
    top = done.stdout.strip()
    return Path(top) if done.returncode == 0 and top else None


def _requirement_name(requirement: str) -> str:
    text = requirement.strip()
    end = next((i for i, char in enumerate(text) if char in _SPECIFIER_START), len(text))
    return text[:end].lower()


def _pyproject(project: Path) -> dict[str, Any]:
    """The project's pyproject.toml; empty when it is not valid UTF-8 TOML."""
    try:
        return tomllib.loads((project / "pyproject.toml").read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError):
        return {}


def _dev_dependencies(project: Path) -> set[str]:
    data = _pyproject(project)
    groups = data.get("dependency-groups")
    dev = groups.get("dev") if isinstance(groups, dict) else None
    if not isinstance(dev, list):
        return set()
    return {_requirement_name(item) for item in dev if isinstance(item, str)}


def _missing_tools(project: Path, tools: tuple[str, ...]) -> list[str]:
    present = _dev_dependencies(project)
    return [tool for tool in tools if tool not in present]


def _python_wiring(check: ModuleType, repo: Path, project: Project) -> list[str]:
    where = check.check_repo.where(repo, project)
    problems = check.check_python.python_settings_problems(project.root, repo)
    lines = [check.check_repo.prefixed(where, problem) for problem in problems]
    missing = _missing_tools(project.root, DEV_TOOLS)
    if missing:
        run = "run:" if where == "." else f"run (in {where}):"
        lines.append(f"{run} uv add --dev {' '.join(missing)}")
    return lines


def _dropped(text: str, pattern: re.Pattern[str]) -> str:
    """`text` without what `pattern` matches outside its strings (group 1)."""
    return pattern.sub(lambda match: match.group(1) or ("" if match.group(0) == "," else " "), text)


def _jsonc(path: Path) -> dict[str, object]:
    """A tsconfig's JSON with comments and trailing commas; raises OSError or ValueError.

    A byte order mark is dropped, as TypeScript drops it.
    """
    text = _dropped(_dropped(path.read_text(encoding="utf-8-sig"), JSONC_COMMENT), TRAILING_COMMA)
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("not an object")
    return data


def _compiles_nothing(data: dict[str, object]) -> bool:
    """A solution-style tsconfig (as Vite's): an empty `files` or `include` and no other."""
    return (
        not data.get("files") and not data.get("include") and ("files" in data or "include" in data)
    )


def _extended(config: Path, data: dict[str, object]) -> Path | None:
    """The tsconfig `config` extends last (it wins over the others) when a relative path names it,
    as TypeScript finds it (adding a missing `.json`); None for a package name or no `extends`."""
    extends = data.get("extends")
    entries = [extends] if isinstance(extends, str) else extends
    if not isinstance(entries, list) or not entries or not isinstance(entries[-1], str):
        return None
    last = entries[-1]
    if not last.startswith(("./", "../")):
        return None
    path = Path(os.path.normpath(config.parent / last))
    if not path.is_file() and path.suffix != ".json":
        return path.with_name(f"{path.name}.json")
    return path


def _unwired_end(config: Path, data: dict[str, object], repo: Path) -> Path | None:
    """None when the `extends` chain of `config` ends with the base (each config extending the
    next last); otherwise the chain's last tsconfig in the repo, where the base belongs: one
    shared base gets one line, not one per config that extends it."""
    base = Path(os.path.normpath(repo / TS_BASE))
    end, seen = config, {config}
    while (extended := _extended(end, data)) is not None:
        if extended == base:
            return None
        if extended in seen or not extended.is_relative_to(repo) or not extended.is_file():
            break
        try:
            data = _jsonc(extended)
        except (OSError, ValueError):
            break
        end = extended
        seen.add(end)
    return end


def _tsconfig_lines(check_ts: ModuleType, repo: Path, root: Path, nested: list[Path]) -> list[str]:
    """Each tsconfig that compiles files but whose `extends` chain does not end with the base:
    those found on disk and those they refer to (as Vite's tsconfig.app.json)."""
    pending: list[Path] = check_ts.tsconfigs(root, nested)
    seen: set[Path] = set()
    lines: dict[str, None] = {}
    while pending:
        config = pending.pop(0)
        if config in seen or not config.is_relative_to(root) or not config.is_file():
            continue
        seen.add(config)
        rel = config.relative_to(repo).as_posix()
        try:
            data = _jsonc(config)
        except (OSError, ValueError) as error:
            lines[f"{rel}: cannot be read ({type(error).__name__})"] = None
            continue
        pending += check_ts.references(config, data)
        end = None if _compiles_nothing(data) else _unwired_end(config, data, repo)
        if end is not None:
            base = f"{Path(os.path.relpath(repo, end.parent)).as_posix()}/{TS_BASE}"
            named = end.relative_to(repo).as_posix()
            make = f'(make it a list: [<existing...>, "{base}"])'
            lines[f'{named}: add "{base}" last in "extends" {make}'] = None
    return list(lines)


def _eslint_lines(check_ts: ModuleType, repo: Path, root: Path) -> list[str]:
    """The base spread (an import of its path outside comments), and at the repo root an ignore
    for `.coven/`: a root project's own `eslint .` would lint it."""
    texts = []
    for path in sorted(root.iterdir()):
        if check_ts.ESLINT_CONFIG.fullmatch(path.name) and path.is_file():
            text = path.read_text(encoding="utf-8", errors="replace")
            texts.append(_dropped(text, JS_COMMENT))
    base = f"{Path(os.path.relpath(repo, root)).as_posix()}/{ESLINT_BASE}"
    imported = re.compile(f"[\"']{re.escape(base)}[\"']")
    here = (root.relative_to(repo) / "eslint.config.*").as_posix()
    lines = []
    if not any(imported.search(text) for text in texts):
        lines.append(f"{here}: spread the default export of {base} last")
    if root == repo and not any(IGNORES_STANDARD.search(text) for text in texts):
        lines.append(f"{here}: {ROOT_IGNORES}")
    return lines


def _ts_settings_lines(
    check: ModuleType, check_ts: ModuleType, repo: Path, project: Project, inside: Any
) -> list[str]:
    """With the project's tools installed: its resolved settings' problems, and the files git
    lists (`inside.listed`) that no tsconfig compiles: the type-aware lint cannot read them
    (ruling P9-R20)."""
    root = project.root
    where = check.check_repo.where(repo, project)
    settings = check_ts.resolved_settings(root, project.manager, repo / ".coven", where, inside)
    listed: tuple[Path, ...] = inside.listed
    lines: list[str] = list(settings.problems)
    compiled = set(settings.compiled)
    outside = [path.relative_to(root).as_posix() for path in listed if path not in compiled]
    if settings.compiled and outside:
        lines.append(
            f"{check.check_repo.shown(outside)}: in no tsconfig, "
            "so the type-aware lint cannot read it: "
            'add it to a tsconfig\'s include ("allowJs": true for .js and .mjs)'
        )
    return lines


def _ts_tool_lines(
    check: ModuleType, check_ts: ModuleType, root: Path, manager: str | None, where: str
) -> list[str]:
    """What keeps the project's tools from running (no lockfile, an unreadable package.json,
    missing dev dependencies with the command that adds them, the tools themselves), and the
    P9-R20 reminder that cannot be checked until they run; nothing when they can run."""

    def blocked(*problems: str) -> list[str]:
        return [check.check_repo.prefixed(where, problem) for problem in (*problems, IN_A_TSCONFIG)]

    if manager is None:
        return blocked(NO_LOCKFILE)
    try:
        missing = check_ts.missing_dev_dependencies(root)
    except (OSError, ValueError) as error:
        return blocked(f"package.json: cannot be read ({type(error).__name__})")
    problems: list[str] = check_ts.tool_problems(root, manager, where)
    if not missing and not problems:
        return []
    add = [f"run (in {where}): {check_ts.ADD_DEV[manager]} {' '.join(missing)}"] if missing else []
    return add + blocked(*problems)


def _ts_wiring(
    check: ModuleType, check_ts: ModuleType, repo: Path, project: Project, projects: list[Project]
) -> list[str]:
    root = project.root
    where = check.check_repo.where(repo, project)
    files = check.check_repo.TsFiles.of(project, projects)
    lines = _tsconfig_lines(check_ts, repo, root, files.nested)
    lines += _eslint_lines(check_ts, repo, root)
    blocked = _ts_tool_lines(check, check_ts, root, project.manager, where)
    if blocked:
        return lines + blocked
    listed = [root / rel for rel in files.kept() if rel.suffix in check.check_repo.TS_SUFFIXES]
    inside = check_ts.Inside(tuple(files.nested), tuple(listed))
    settings = _ts_settings_lines(check, check_ts, repo, project, inside)
    return lines + [check.check_repo.prefixed(where, line) for line in settings]


def _wiring(check: ModuleType, repo: Path, projects: list[Project]) -> tuple[str, ...]:
    check_ts = check.load_ts()
    lines: list[str] = []
    for project in projects:
        if project.kind == "python":
            lines += _python_wiring(check, repo, project)
        else:
            lines += _ts_wiring(check, check_ts, repo, project, projects)
    listing = check.check_repo.listing(repo)
    lines += check.check_repo.repo_problems(repo, listing) + check.check_repo.outside_problems(
        repo, projects, listing
    )
    return tuple(lines)


def _require_top(project: Path) -> None:
    """The standard's files live at the repo root, where the check and CI run."""
    top = _toplevel(project)
    if top is None:
        raise ProjectError(f"{project} is not inside a git work tree")
    if top.resolve() != project.resolve():
        raise ProjectError(f"{project} is not the top of its git work tree; run it in {top}")


def _projects(project: Path) -> tuple[ModuleType, list[Project]]:
    """The repo's projects, found as check.py finds them; at least one, or ProjectError."""
    _require_top(project)
    check = _load_check()
    projects: list[Project] = check.check_repo.discover(project)
    if not projects:
        raise ProjectError(f"no Python or TypeScript project found in {project}")
    return check, projects


def _owned(kinds: set[str]) -> dict[str, str]:
    """The owned files a repo with these project kinds gets."""
    owned = {**OWNED_PYTHON, **OWNED_TYPESCRIPT}
    return {rel: source for rel, source in owned.items() if KIND_OF.get(rel, "") in {"", *kinds}}


def _unused(project: Path, kinds: set[str]) -> list[tuple[str, str]]:
    """The owned files present that no project of the repo uses, with the kind they are for."""
    return [
        (rel, KIND_NAMES[kind])
        for rel, kind in KIND_OF.items()
        if kind not in kinds and (project / rel).exists()
    ]


def _source(rel: str) -> Path:
    return paths.root() / (OWNED_PYTHON.get(rel) or OWNED_TYPESCRIPT[rel])


def _install(source: Path, target: Path) -> bool:
    content = source.read_bytes()
    if target.is_file() and target.read_bytes() == content:
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    return True


def _text(content: bytes) -> list[str]:
    return content.decode("utf-8", errors="replace").splitlines()


def _conflicts(project: Path, owned: dict[str, str]) -> list[str]:
    """A unified diff for each guarded file the repo has that differs from coven's."""
    diffs: list[str] = []
    for rel in owned:
        target = project / rel
        if rel not in GUARDED or not target.is_file():
            continue
        ours = _source(rel).read_bytes()
        if target.read_bytes() != ours:
            diff = difflib.unified_diff(
                _text(target.read_bytes()),
                _text(ours),
                fromfile=rel,
                tofile=f"{rel} (coven's standard)",
                lineterm="",
            )
            diffs.append("\n".join(diff))
    return diffs


def _require_inside(project: Path, owned: dict[str, str]) -> None:
    """No owned file may resolve (through a symlink) to a place outside the repo."""
    top = project.resolve()
    outside = [rel for rel in owned if not (project / rel).resolve().is_relative_to(top)]
    if outside:
        raise ProjectError(
            f"{', '.join(outside)}: a symbolic link leads outside the repo; "
            "coven writes only inside the repo (replace the link with a plain file or folder)"
        )


def setup(project: Path, *, force: bool = False) -> SetupResult:
    """Installs the standard. A guarded file that differs stops it, before anything is written,
    unless `force`."""
    check, projects = _projects(project)
    kinds = {p.kind for p in projects}
    owned = _owned(kinds)
    _require_inside(project, owned)
    conflicts = [] if force else _conflicts(project, owned)
    if conflicts:
        raise ProjectError(
            "these files differ from coven's standard and were left alone; "
            "re-run with --force to overwrite them:\n" + "\n".join(conflicts)
        )
    written: list[str] = []
    unchanged: list[str] = []
    for rel in owned:
        changed = _install(_source(rel), project / rel)
        (written if changed else unchanged).append(rel)
    stale = [f"delete {rel}: no {kind} project uses it" for rel, kind in _unused(project, kinds)]
    wiring = _wiring(check, project, projects) + tuple(stale)
    return SetupResult(tuple(written), tuple(unchanged), wiring)


def drift(project: Path) -> list[str]:
    """What differs from coven's standard in the repo, one line each; empty when it matches."""
    _, projects = _projects(project)
    kinds = {p.kind for p in projects}
    lines = []
    for rel in _owned(kinds):
        target = project / rel
        if not target.is_file():
            lines.append(f"{rel}: missing")
        elif target.read_bytes() != _source(rel).read_bytes():
            lines.append(f"{rel}: differs from coven's standard (re-run coven project)")
    lines += [
        f"{rel}: no {kind} project uses it (delete it)" for rel, kind in _unused(project, kinds)
    ]
    return lines


def run(args: argparse.Namespace) -> int:
    project = Path(args.path).resolve()
    if args.check:
        lines = drift(project)
        print("\n".join(lines) if lines else "coven project: no drift")
        return 1 if lines else 0
    result = setup(project, force=args.force)
    for rel in result.written:
        print(f"wrote {rel}")
    for rel in result.unchanged:
        print(f"unchanged {rel}")
    if result.wiring:
        print("\nWiring left to do by hand:")
        for line in result.wiring:
            print(f"  {line}")
    return 0


def register(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "project", help="install or check the code standard in a repo (.coven/)"
    )
    parser.add_argument("path", nargs="?", default=".", help="the repo's top folder (default: .)")
    parser.add_argument(
        "--check", action="store_true", help="report drift from the standard, change nothing"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="overwrite a workflow, ruff.toml or TypeScript config that differs from coven's",
    )
    parser.set_defaults(handler=run)
