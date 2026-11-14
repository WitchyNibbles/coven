"""coven's quality check, copied into project repos. Standard library only."""

from __future__ import annotations

import argparse
import functools
import importlib.machinery
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from types import CodeType, ModuleType
from typing import TYPE_CHECKING

# tomllib is imported where it is used, so an older Python still gets a receipt.
MINIMUM_PYTHON = (3, 11)

# The standard's version, the same literal in every module of it. It goes up whenever a name one
# module uses from another is added, removed, or changes meaning or signature.
STANDARD_VERSION = 5
# The siblings loaded when the check starts, in dependency order. check_ts.py loads only when a
# TypeScript project needs it.
EAGER = ("check_repo", "check_stages", "check_python")
SIBLING_PREFIX = "coven_"
# What loading a broken or stale sibling raises, its module code included. Anything else is a bug
# in the standard and keeps its traceback.
LOAD_ERRORS = (
    OSError,
    SyntaxError,
    ValueError,
    ImportError,
    NameError,
    AttributeError,
    TypeError,
    LookupError,
    RuntimeError,
)


class StandardMismatchError(ImportError):
    """A sibling's `STANDARD_VERSION` differs from this script's, or is missing."""


class _SourceOnlyLoader(importlib.machinery.SourceFileLoader):
    """Compiles the source on every load: never reads or writes `__pycache__`.

    Bytecode there (an unchecked-hash .pyc) would otherwise stand in for the source, and a cache
    written into the repo's `.coven/` would make the tree dirty.
    """

    def get_code(self, fullname: str) -> CodeType:
        path = self.get_filename(fullname)
        return self.source_to_code(self.get_data(path), path)


# This script's eager siblings by name, once they have loaded.
_EAGER: dict[str, ModuleType] = {}


def _load(name: str, needs: tuple[str, ...] = ()) -> ModuleType:
    """The sibling `<name>.py` next to this script, registered in sys.modules as `coven_<name>`.

    Next to the script's own path, symlinks not followed: a symlinked `.coven/check.py` is a
    settings problem, and it still uses the `.coven/` siblings. This script's own siblings are
    registered again first, so the one loaded now binds to them even when another copy of the
    standard loaded since. One registered from the same file is reused; one from another file is
    replaced. Raises FileNotFoundError when the file is missing, AttributeError when it lacks one
    of the functions it `needs`, and StandardMismatchError when it is from another version of the
    standard.
    """
    for loaded, module in _EAGER.items():
        sys.modules[SIBLING_PREFIX + loaded] = module
    key = SIBLING_PREFIX + name
    source = str(Path(__file__).absolute().with_name(f"{name}.py"))
    known = sys.modules.get(key)
    if known is not None and getattr(known, "__file__", None) == source:
        return known
    loader = _SourceOnlyLoader(key, source)
    fresh = ModuleType(key)
    fresh.__file__ = source
    # Registered while it runs, as an import does (a dataclass looks its module up there), and
    # put back as it was when it fails, so a broken or stale sibling is never left registered.
    sys.modules[key] = fresh
    try:
        loader.exec_module(fresh)
        _check_usable(name, fresh, needs)
    except BaseException:
        if known is None:
            del sys.modules[key]
        else:
            sys.modules[key] = known
        raise
    return fresh


def _check_usable(name: str, module: ModuleType, needs: tuple[str, ...]) -> None:
    """Raises when the sibling cannot be used.

    AttributeError when it lacks a function it needs, then StandardMismatchError when it is from
    another version of the standard.
    """
    for function in needs:
        if not callable(getattr(module, function, None)):
            raise AttributeError(f"{name}.py has no {function}")
    if getattr(module, "STANDARD_VERSION", None) != STANDARD_VERSION:
        raise StandardMismatchError(f"{name}.py is from another version of the standard")


def _load_problem(name: str, error: BaseException) -> str:
    """The setup error for a sibling that cannot be used."""
    # Spelled out, not check_repo.STANDARD_DIR: check_repo.py may be the file that failed.
    path = f".coven/{name}.py"
    if isinstance(error, StandardMismatchError):
        return f"{path}: from another version of the standard; re-run coven project"
    return f"{path}: cannot be loaded ({type(error).__name__}); re-run coven project"


def _load_eager() -> tuple[dict[str, ModuleType], str | None]:
    """The eager siblings by name, and the setup error of the first that cannot be used.

    Loading stops at that one; the error is None when every sibling loaded.
    """
    loaded: dict[str, ModuleType] = {}
    for name in EAGER:
        try:
            module = _load(name)
        except LOAD_ERRORS as error:
            return loaded, _load_problem(name, error)
        loaded[name] = module
    return loaded, None


_EAGER, LOAD_PROBLEM = _load_eager()

if TYPE_CHECKING:
    import check_python
    import check_repo
    import check_stages
else:
    check_python = _EAGER.get("check_python")
    check_repo = _EAGER.get("check_repo")
    check_stages = _EAGER.get("check_stages")


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"


@dataclass
class Receipt:
    mode: str
    exit: int
    sha: str | None
    dirty: bool
    projects: int = 0
    settings: str = "fail"
    suppressions: int = 0
    # Vendored/generated files left out of scope (.gitattributes).
    skipped: int = 0
    format: str = "fail"
    lint: str = "fail"
    types: str = "fail"
    tests: int | str = "fail"
    coverage: str = "fail"
    # The commit changed lines are measured from: a 7-char sha, "none", or "-" in fast mode.
    base: str = "-"
    runner: str = "uv"
    error: str | None = None

    @classmethod
    def setup_error(cls, mode: str, sha: str | None, dirty: bool, error: str) -> Receipt:
        return cls(mode, 2, sha, dirty, error=error)

    def line(self) -> str:
        sha = self.sha or "none"
        head = f"CHECK {self.mode} exit={self.exit} sha={sha} dirty={_yes_no(self.dirty)}"
        if self.error is not None:
            return f'{head} error="{self.error}"'
        return (
            f"{head} projects={self.projects} settings={self.settings} "
            f"suppressions={self.suppressions} skipped={self.skipped} format={self.format} "
            f"lint={self.lint} types={self.types} tests={self.tests} coverage={self.coverage} "
            f"base={self.base} runner={self.runner}"
        )


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="check.py", description="coven quality check.")
    modes = parser.add_subparsers(dest="mode", required=True)
    fast = modes.add_parser("fast", help="format, lint, types and the named tests")
    fast.add_argument("paths", nargs="*", metavar="TEST_PATH")
    full = modes.add_parser("full", help="everything, plus changed-line coverage")
    full.add_argument(
        "--base",
        default=None,
        help="ref to diff against (default: where HEAD left the default branch)",
    )
    modes.add_parser("install", help="install every project's locked dependencies")
    args, extra = parser.parse_known_args(argv)
    if args.mode in ("full", "install") and extra:
        parser.error(f"unrecognized arguments: {' '.join(extra)}")
    if args.mode == "fast":
        args.paths = [*args.paths, *extra]  # options among them are refused with a receipt
    return args


# `shard` (sharded CI runs) is not supported; it gets a setup-error receipt, not usage text.
REMOVED_MODE = "shard"


def _python_version_error() -> str | None:
    found = sys.version_info[:2]
    if found >= MINIMUM_PYTHON:
        return None
    need = ".".join(map(str, MINIMUM_PYTHON))
    rerun = ["uv", "run", "--no-project", "--python", f">={need}", "python", ".coven/check.py"]
    return (
        f"Python {need} or newer is needed (found {'.'.join(map(str, found))});"
        f" with uv: {shlex.join([*rerun, *sys.argv[1:]])}"
    )


# pytest reads `@file` arguments as more options, so they are refused like `-` ones.
OPTION_PREFIXES = ("-", "@")


def _has_uv_lock(repo: Path, project: check_repo.Project) -> bool:
    """A `uv.lock` in the project's directory, or in the directory of a uv workspace above it."""
    if (project.root / "uv.lock").is_file():
        return True
    return any(
        (directory / "uv.lock").is_file() and _is_uv_workspace(directory)
        for directory in project.root.parents
        if directory.is_relative_to(repo)
    )


def _is_uv_workspace(directory: Path) -> bool:
    try:
        data = check_python.load_toml(directory / check_repo.PYPROJECT)
    except (OSError, check_python.toml_error(), UnicodeDecodeError):
        return False
    tool = check_python.table(data, "tool")
    return "workspace" in check_python.table(tool, "uv")


def _lock_problem(repo: Path, project: check_repo.Project) -> str | None:
    if project.kind == "python" and not _has_uv_lock(repo, project):
        return "no uv.lock (the standard supports uv projects)"
    if project.kind == "typescript" and project.manager is None:
        return "no lockfile"
    return None


def _owner(
    cwd: Path, projects: list[check_repo.Project], path: str
) -> tuple[check_repo.Project, str] | None:
    """The project a test path belongs to, and the path as seen from that project's root.

    The deepest project holding the path wins; where a Python and a TypeScript project share that
    directory, a script suffix decides. A `::node` part of a pytest id is kept.
    """
    name, separator, node = path.partition("::")
    target = Path(os.path.normpath(cwd / name))
    holding = [project for project in projects if target.is_relative_to(project.root)]
    if not holding:
        return None
    depth = max(len(project.root.parts) for project in holding)
    here = [project for project in holding if len(project.root.parts) == depth]
    prefer = "typescript" if target.suffix in check_repo.TS_SUFFIXES else "python"
    project = next((p for p in here if p.kind == prefer), here[0])
    return project, target.relative_to(project.root).as_posix() + separator + node


def _routes(
    cwd: Path, projects: list[check_repo.Project], paths: list[str]
) -> dict[check_repo.Project, list[str]]:
    routes: dict[check_repo.Project, list[str]] = {}
    for path in paths:
        owner = _owner(cwd, projects, path)
        if owner is not None:
            routes.setdefault(owner[0], []).append(owner[1])
    return routes


def _setup_problem(
    repo: Path, cwd: Path, projects: list[check_repo.Project], args: argparse.Namespace
) -> str | None:
    """Why the checks cannot run here, or None when they can."""
    version = _python_version_error()
    if version is not None:
        return version
    paths = getattr(args, "paths", [])
    if any(path.startswith(OPTION_PREFIXES) for path in paths):
        return "test paths only, no pytest options"
    if not projects:
        return "no Python or TypeScript project found"
    for project in projects:
        problem = _lock_problem(repo, project)
        if problem is not None:
            return check_repo.prefixed(check_repo.where(repo, project), problem)
    problem = _check_ts_problem(projects) or _typescript_problem(repo, projects, args.mode)
    if problem is not None:
        return problem
    if (
        any(project.kind == "python" for project in projects)
        and not check_stages.runner_override()
        and shutil.which("uv") is None
    ):
        return "uv not found on PATH"
    for path in paths:
        if _owner(cwd, projects, path) is None:
            return f"test path {path}: not inside a project"
    return None


def _typescript_problem(repo: Path, projects: list[check_repo.Project], mode: str) -> str | None:
    """Why the TypeScript projects cannot be checked here: a test runner or a program is missing.

    A missing check_ts.py is left to the stages, which fail and say to re-install.
    """
    typescript = [project for project in projects if project.kind == "typescript"]
    if not typescript:
        return None
    try:
        module = load_ts()
    except FileNotFoundError:
        return None
    for project in typescript:
        if module.test_runner(project.root) is None:
            return check_repo.prefixed(check_repo.where(repo, project), module.NO_TEST_RUNNER)
    if shutil.which("node") is None:
        return "node not found on PATH"
    for project in typescript:
        if project.manager == "bun" and shutil.which("bunx") is None:
            return check_repo.prefixed(check_repo.where(repo, project), "bunx not found on PATH")
    if mode == "full" and not check_stages.runner_override() and shutil.which("uvx") is None:
        return "uvx not found on PATH (full mode measures changed lines with diff-cover)"
    return None


def _repo_root(cwd: Path, mode: str) -> Path | Receipt:
    """The git work tree's top directory, or a setup-error receipt when there is none."""
    if shutil.which("git") is None:
        return Receipt.setup_error(mode, None, False, "git not found on PATH")
    inside = check_repo.git(cwd, "rev-parse", "--is-inside-work-tree")
    top = check_repo.git(cwd, "rev-parse", "--show-toplevel").stdout.strip()
    if inside.returncode != 0 or inside.stdout.strip() != "true" or not top:
        return Receipt.setup_error(mode, None, False, "not inside a git work tree")
    return Path(top)


def _base_field(root: Path, mode: str, base: str | None) -> str:
    if mode == "fast":
        return "-"
    return check_repo.short(root, base) if base is not None else "none"


def fold_ok(values: list[str]) -> str:
    """`fail` when any project failed the stage, else `ok`."""
    return "fail" if "fail" in values else "ok"


def fold_tests(values: list[str]) -> str:
    """The sum of the projects' test counts; `fail` when any project failed."""
    return "fail" if "fail" in values else str(sum(int(value) for value in values))


def fold_coverage(values: list[str]) -> str:
    """`fail` if any project failed, else the lowest percentage.

    Without percentages it is `n/a` only if every project said so, else `skip`.
    """
    if "fail" in values:
        return "fail"
    percentages = [int(value) for value in values if value.isdigit()]
    if percentages:
        return str(min(percentages))
    return "n/a" if all(value == "n/a" for value in values) else "skip"


CHECK_TS = "check_ts.py"


def _ts_missing() -> str:
    # A function, not a constant: check_repo may not have loaded when this script starts.
    return f"{check_repo.STANDARD_DIR}/{CHECK_TS} is missing (re-run coven project)"


@functools.cache
def load_ts() -> ModuleType:
    """The TypeScript module that sits next to this script (loaded once, see `_load`).

    Raises FileNotFoundError when `.coven/` has check.py without it; any other exception means
    it cannot be used.
    """
    return _load("check_ts", needs=("suppression_lines", "settings_problems"))


def _check_ts_problem(projects: list[check_repo.Project]) -> str | None:
    """Why check_ts.py cannot be used, when a TypeScript project needs it.

    A missing file is left to the stages, which fail and say to re-install.
    """
    if not any(project.kind == "typescript" for project in projects):
        return None
    try:
        load_ts()
    except FileNotFoundError:
        return None
    except LOAD_ERRORS as error:
        return _load_problem("check_ts", error)
    return None


def _missing_ts_stages(
    project: check_repo.Project, projects: list[check_repo.Project]
) -> check_stages.Stages:
    """A TypeScript project's stages when check_ts.py is missing: each fails and says so."""
    files = check_repo.TsFiles.of(project, projects)
    missing = _ts_missing()
    for problem in [missing, *check_repo.framework_problems(files)]:
        print(f"settings: {problem}")
    print(missing)
    failed: check_stages.Outcome = ("fail", False)
    return check_stages.Stages(
        failed, (0, False), failed, failed, failed, failed, failed, files.skipped_count()
    )


def _typescript_stages(
    project: check_repo.Project,
    run: check_stages.Run,
    projects: list[check_repo.Project],
    paths: list[str],
    out: Path,
) -> check_stages.Stages:
    """A TypeScript project's stages, run by check_ts.py; they fail when it is missing."""
    try:
        module = load_ts()
    except FileNotFoundError:
        return _missing_ts_stages(project, projects)
    stages: check_stages.Stages = module.typescript_stages(project, run, projects, paths, out)
    return stages


def _repo_settings(repo: Path, projects: list[check_repo.Project], base: str | None) -> bool:
    """Print the settings problems of the repo as a whole; whether there were none.

    `base` is known only in full mode; it is what the branch's commit messages are read from.
    """
    listing = check_repo.listing(repo)
    problems = [
        *check_repo.repo_problems(repo, listing),
        *check_repo.outside_problems(repo, projects, listing),
        *check_repo.attribution_problems(repo, base),
    ]
    for problem in problems[: check_stages.TAIL_LINES]:
        print(f"settings: {problem}")
    if len(problems) > check_stages.TAIL_LINES:
        print(f"settings: and {len(problems) - check_stages.TAIL_LINES} more")
    return not problems


def _run_stages(
    repo: Path,
    cwd: Path,
    projects: list[check_repo.Project],
    args: argparse.Namespace,
    state: check_repo.GitState,
) -> Receipt:
    requested = getattr(args, "base", None) or None
    base = check_repo.resolve_base(repo, requested) if args.mode == "full" else None
    routes = _routes(cwd, projects, getattr(args, "paths", []))
    repo_ok = _repo_settings(repo, projects, base)
    results = []
    run = check_stages.Run(repo, args.mode, base)
    with tempfile.TemporaryDirectory(prefix="coven-check-") as tmp:
        for index, project in enumerate(projects):
            where = check_repo.where(repo, project)
            if len(projects) > 1:
                print(f"=== {where} ({project.kind}) ===")
            paths = routes.get(project, [])
            out = Path(tmp) / str(index)
            if project.kind == "python":
                results.append(
                    check_python.python_stages(project, run, paths, out / "coverage.xml")
                )
            else:
                results.append(_typescript_stages(project, run, projects, paths, out))
            if len(projects) > 1:  # each project's own results, before they are folded together
                print(f"{where} ({project.kind}): {results[-1].summary()}")
    passed = repo_ok and all(stages.passed() for stages in results)
    return Receipt(
        mode=args.mode,
        exit=0 if passed else 1,
        sha=state.sha,
        dirty=state.dirty,
        projects=len(projects),
        settings=fold_ok([r.settings[0] for r in results] + ([] if repo_ok else ["fail"])),
        suppressions=sum(r.suppressions[0] for r in results),
        skipped=sum(r.skipped for r in results),
        format=fold_ok([r.format[0] for r in results]),
        lint=fold_ok([r.lint[0] for r in results]),
        types=fold_ok([r.types[0] for r in results]),
        tests=fold_tests([r.tests[0] for r in results]),
        coverage=fold_coverage([r.coverage[0] for r in results]),
        base=_base_field(repo, args.mode, base),
        runner="override" if check_stages.runner_override() else "uv",
    )


def _install_commands(
    repo: Path, projects: list[check_repo.Project]
) -> tuple[list[tuple[check_repo.Project, list[str]]], str | None]:
    """Per project, the command that installs its lockfile; or why nothing can be installed."""
    problem = _python_version_error()
    if problem is None and not projects:
        problem = "no Python or TypeScript project found"
    for project in projects:
        lock = _lock_problem(repo, project)
        if problem is None and lock is not None:
            problem = check_repo.prefixed(check_repo.where(repo, project), lock)
    problem = problem or _check_ts_problem(projects)
    if problem is not None:
        return [], problem
    commands = []
    for project in projects:
        if project.kind == "python":
            command = ["uv", "sync", "--locked"]
        else:
            try:
                command = load_ts().install_command(project.root, project.manager).split()
            except FileNotFoundError:
                return [], _ts_missing()
        if shutil.which(command[0]) is None:
            return [], check_repo.prefixed(
                check_repo.where(repo, project), f"{command[0]} not found on PATH"
            )
        commands.append((project, command))
    return commands, None


def install(cwd: Path) -> int:
    """Install every project's locked dependencies: one line per project.

    Exit 0 when every install worked, 1 when one failed, 2 when nothing could start (a missing
    tool or lockfile is found before anything is installed).
    """
    found = _repo_root(cwd, "install")
    if isinstance(found, Receipt):
        print(f'install: error="{found.error}"')
        return 2
    commands, problem = _install_commands(found, check_repo.discover(found))
    if problem is not None:
        print(f'install: error="{problem}"')
        return 2
    failed = False
    for project, command in commands:
        line = f"install {check_repo.where(found, project)} ({project.kind}): {' '.join(command)}"
        try:
            done = subprocess.run(
                command,
                cwd=project.root,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                check=False,
            )
            code, output = done.returncode, done.stdout + done.stderr
        except OSError as error:
            code, output = 127, f"{command[0]}: {error}"
        print(f"{line} -> {'ok' if code == 0 else f'failed (exit {code})'}")
        if code != 0:
            check_stages.show_tail("install", output)
            failed = True
    return 1 if failed else 0


def _receipt_for(cwd: Path, args: argparse.Namespace) -> Receipt:
    """Run the requested checks: their receipt, or the setup error that kept them from running."""
    found = _repo_root(cwd, args.mode)
    if isinstance(found, Receipt):
        return found
    # Read before any stage: the tools write caches that flip `dirty`.
    state = check_repo.git_state(found)
    projects = check_repo.discover(found)
    problem = _setup_problem(found, cwd, projects, args)
    if problem:
        return Receipt.setup_error(args.mode, state.sha, state.dirty, problem)
    return _run_stages(found, cwd, projects, args, state)


def _refuse_removed_mode(cwd: Path, mode: str) -> int:
    """Answer a mode this standard does not have with a setup-error receipt, not argparse usage."""
    found = _repo_root(cwd, mode)
    if isinstance(found, Receipt):
        receipt = found
    else:
        state = check_repo.git_state(found)
        error = f"{mode} mode does not exist (modes: fast, full, install)"
        receipt = Receipt.setup_error(mode, state.sha, state.dirty, error)
    print(receipt.line())
    return receipt.exit


def main(argv: list[str] | None = None) -> int:
    given = sys.argv[1:] if argv is None else argv
    if given[:1] == [REMOVED_MODE]:
        return _refuse_removed_mode(Path.cwd(), REMOVED_MODE)
    args = _parse(argv)
    if LOAD_PROBLEM:
        if args.mode == "install":
            print(f'install: error="{LOAD_PROBLEM}"')
        else:
            print(Receipt.setup_error(args.mode, None, False, LOAD_PROBLEM).line())
        return 2
    cwd = Path.cwd()
    if args.mode == "install":
        return install(cwd)
    receipt = _receipt_for(cwd, args)
    if receipt.skipped:
        print(f"note: {receipt.skipped} vendored/generated files skipped (.gitattributes)")
    if check_stages.runner_override():
        print(
            f"note: {check_stages.RUNNER_ENV}={check_stages.runner_override()} "
            "replaces uv run --frozen"
        )
    print(receipt.line())
    return receipt.exit


if __name__ == "__main__":
    raise SystemExit(main())
