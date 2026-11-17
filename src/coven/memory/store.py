"""The memory store: a git repo with a pre-commit hook, created by `init` and kept in step by `sync`."""

from __future__ import annotations

import contextlib
import json
import os
import re
import shlex
import shutil
import subprocess
import time
from pathlib import Path

from coven import paths
from coven.errors import CovenError
from coven.memory import entries

HOOK_SOURCE = paths.root() / "hooks" / "memory_pre_commit.py"
HOOK_VERSION_LINE = re.compile(r"^# coven-hook-version: (\d+)[ \t]*$", re.MULTILINE)
ATTRIBUTES = ".gitattributes"
UNION_LINE = "INDEX.md merge=union"
LAYOUT = ("user", "projects", "handoffs")
GUARD_FILE = "systems/guard.json"
NOT_PUSHED_GUARDED = "not pushed: guarded store"
BRANCH = "main"
RELATIVE_HOOKS = ".git/hooks"
# Variables that move git to another repository; a caller's value must not redirect `git -C <store>`.
REPO_VARIABLES = frozenset(
    {
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_COMMON_DIR",
        "GIT_NAMESPACE",
        "GIT_CEILING_DIRECTORIES",
        "GIT_DISCOVERY_ACROSS_FILESYSTEM",
    }
)
GIT_TIMEOUT = 60  # seconds per git command
GH_TIMEOUT = 30  # seconds for the gh call that checks a remote's visibility
LOCK_RETRY_DELAY = 0.5  # seconds before the one retry of a command that met `index.lock`


def remote_warning(remote: str) -> str | None:
    """Why the remote may not be private, or None when `gh` says it is."""
    gh = shutil.which("gh")
    if gh is None:
        return f"could not check that {remote} is private (gh is not installed); check it yourself"
    try:
        done = subprocess.run(
            [gh, "repo", "view", remote, "--json", "visibility"],
            capture_output=True,
            text=True,
            timeout=GH_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return f"could not check that {remote} is private (gh failed: {error}); check it yourself"
    if done.returncode != 0:
        return f"could not check that {remote} is private (gh exited {done.returncode}); check it yourself"
    try:
        visibility = json.loads(done.stdout)["visibility"]
    except (ValueError, KeyError, TypeError):
        return f"could not check that {remote} is private (unreadable gh output); check it yourself"
    if str(visibility).upper() == "PRIVATE":
        return None
    return f"{remote} is {visibility}, not PRIVATE; the store holds personal notes, make it private"


def hook_version(text: str) -> int | None:
    """The number of the `# coven-hook-version: <n>` line of a hook script, None without one."""
    found = HOOK_VERSION_LINE.search(text)
    return int(found.group(1)) if found else None


def is_store(path: Path) -> bool:
    """A git repo whose `.gitattributes` has the INDEX.md union rule `init` writes."""
    try:
        lines = (path / ATTRIBUTES).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return False
    return (path / ".git").exists() and UNION_LINE in lines


def is_guarded(path: Path) -> bool:
    """Whether the store's HEAD commit holds `systems/guard.json` (the work tree does not count).

    False when the folder has no `.git` (git would otherwise look at a repo above it), when HEAD
    does not exist yet and when HEAD lacks the file. Any other git failure (a repo git refuses, a
    damaged one) raises `CovenError` with git's message: a guard must not read as off because git
    could not answer.
    """
    if not (path / ".git").exists():
        return False
    head = _git(path, "rev-parse", "--verify", "--quiet", "HEAD", check=False)
    if head.returncode == 1 and not head.stderr.strip():
        return False  # `--quiet` makes a missing HEAD exit 1 without a message
    if head.returncode != 0:
        raise CovenError(f"git rev-parse failed in {path}: {_last_line(head.stderr)}")
    if _git(path, "cat-file", "-e", f"HEAD:{GUARD_FILE}", check=False).returncode == 0:
        return True
    # cat-file words a missing path and a real failure alike; ls-tree tells them apart by output.
    listed = _git(path, "ls-tree", "--name-only", "HEAD", "--", GUARD_FILE)
    return bool(listed.stdout.strip())


def pause() -> None:
    """The wait before a locked git command is retried (a function so tests can replace it)."""
    time.sleep(LOCK_RETRY_DELAY)


def _run(path: Path, args: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k not in REPO_VARIABLES}
    env["GIT_TERMINAL_PROMPT"] = "0"
    env.setdefault("GIT_SSH_COMMAND", "ssh -o BatchMode=yes")
    try:
        return subprocess.run(
            ["git", "-C", str(path), *args],
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT,
            env=env,
            check=False,
        )
    except OSError as error:
        raise CovenError(f"cannot run git: {error}") from error
    except subprocess.TimeoutExpired as error:
        raise CovenError(f"git {args[0]} took longer than {GIT_TIMEOUT} seconds") from error


def _last_line(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else "no output"


def _git(path: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    """`git -C path <args>`; one retry when another git process holds `index.lock`.

    A failure raises `CovenError` when `check` is set; a held lock raises it either way.
    """
    done = _run(path, args)
    if done.returncode != 0 and "index.lock" in done.stderr:
        pause()
        done = _run(path, args)
        if done.returncode != 0 and "index.lock" in done.stderr:
            raise CovenError(
                f"git {args[0]} failed in {path}: {_last_line(done.stderr)}; wait for the other "
                "git process, or remove .git/index.lock when none is running"
            )
    if check and done.returncode != 0:
        raise CovenError(f"git {args[0]} failed in {path}: {_last_line(done.stderr)}")
    return done


def _commit(path: Path, message: str) -> None:
    done = _git(path, "commit", "-q", "-m", message, check=False)
    if done.returncode != 0:
        detail = (done.stderr or done.stdout).strip() or "no output"
        raise CovenError(
            f"git commit failed in {path} (git needs user.name and user.email set): {detail}"
        )


def install_hook(path: Path) -> None:
    """Copy the pre-commit hook into the store, replacing an older copy of it, never a foreign one."""
    hooks = path / ".git" / "hooks"
    hook = hooks / "pre-commit"
    if hook.exists() and hook_version(hook.read_text(encoding="utf-8", errors="replace")) is None:
        raise CovenError(
            f"{hook} is not coven's hook; move it away, then run "
            f"coven memory init --path {shlex.quote(str(path))} again"
        )
    hooks.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(HOOK_SOURCE, hook)
    hook.chmod(0o755)


def _create_layout(path: Path) -> None:
    _git(path, "init", "-q")
    _git(path, "symbolic-ref", "HEAD", f"refs/heads/{BRANCH}")
    for folder in LAYOUT:
        (path / folder).mkdir(exist_ok=True)
        (path / folder / ".gitkeep").touch()
    (path / ATTRIBUTES).write_text(f"{UNION_LINE}\n", encoding="utf-8")


def _set_origin(path: Path, remote: str) -> None:
    current = _git(path, "remote", "get-url", "origin", check=False)
    if current.returncode != 0:
        _git(path, "remote", "add", "origin", remote)
    elif current.stdout.strip() != remote:
        _git(path, "remote", "set-url", "origin", remote)


def _is_unborn_repo_only(path: Path) -> bool:
    """A folder holding nothing but a git repo with no commit: a half-made `init`."""
    if {entry.name for entry in path.iterdir()} != {".git"}:
        return False
    return _git(path, "rev-parse", "--verify", "--quiet", "HEAD", check=False).returncode != 0


def _installed_hook(path: Path) -> Path:
    """The pre-commit hook file git runs in this store (it follows `core.hooksPath`)."""
    where = _git(path, "rev-parse", "--git-path", "hooks/pre-commit").stdout.strip()
    return path / where


def _pin_hooks_path(path: Path) -> None:
    """Make git run the store's own hooks folder, whatever a global `core.hooksPath` says, and
    check that it does. The setting is relative, which git resolves against the top folder of the
    work tree, so a moved store keeps working."""
    _git(path, "config", "--local", "core.hooksPath", RELATIVE_HOOKS)
    hooks = (path / RELATIVE_HOOKS).resolve()
    runs = _installed_hook(path)
    if runs.resolve() != hooks / "pre-commit":
        raise CovenError(
            f"git would not run the hook installed at {hooks / 'pre-commit'}: it runs {runs} "
            "(a core.hooksPath set outside the repo's config wins); unset that, then run "
            f"coven memory init --path {shlex.quote(str(path))} again"
        )


def check_path(path: Path) -> None:
    """Raise CovenError unless `init_store` would accept `path`: nothing there, an empty folder, a
    git repo without commits, or a coven store."""
    if path.exists() and not path.is_dir():
        raise CovenError(f"{path} is not a folder")
    if (
        path.is_dir()
        and any(path.iterdir())
        and not is_store(path)
        and not _is_unborn_repo_only(path)
    ):
        raise CovenError(f"{path} is not empty and is not a coven memory store; pick another path")


def init_store(path: Path, remote: str | None) -> None:
    """Create the store at `path` (or finish and update the one there) and install its hook.

    Refuses a folder that holds files and is not already a coven store. A folder holding only a
    git repo without commits counts as empty.
    """
    check_path(path)
    path.mkdir(parents=True, exist_ok=True)
    if not is_store(path):
        _create_layout(path)
    if _git(path, "rev-parse", "--verify", "--quiet", "HEAD", check=False).returncode != 0:
        _git(path, "add", "-A")
        _commit(path, "init memory store")
    install_hook(path)
    _pin_hooks_path(path)
    if remote:
        _set_origin(path, remote)


def _require_clean(path: Path) -> None:
    status = _git(path, "status", "--porcelain").stdout.strip()
    if status:
        shown = ", ".join(line[3:] for line in status.splitlines()[:5])
        raise CovenError(
            f"{path} has uncommitted changes ({shown}); commit them first, then sync again"
        )


def _upstream(path: Path) -> str | None:
    """The ref the branch follows, else `origin/<branch>` when the remote has that branch."""
    tracked = _git(
        path, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}", check=False
    )
    if tracked.returncode == 0:
        return tracked.stdout.strip()
    branch = _git(path, "symbolic-ref", "--short", "HEAD").stdout.strip()
    guess = f"origin/{branch}"
    found = _git(path, "rev-parse", "--verify", "--quiet", guess, check=False)
    return guess if found.returncode == 0 else None


def _count(path: Path, spec: str) -> int:
    return int(_git(path, "rev-list", "--count", spec).stdout.strip())


def _rebase(path: Path, upstream: str) -> None:
    """Rebase onto `upstream`; on a conflict abort, so the store is never left mid-rebase."""
    try:
        done = _git(path, "rebase", "--quiet", upstream, check=False)
    except CovenError:  # a timeout or a held lock may leave the rebase half done
        with contextlib.suppress(CovenError):
            _git(path, "rebase", "--abort", check=False)
        raise
    if done.returncode == 0:
        return
    conflicts = _git(path, "diff", "--name-only", "--diff-filter=U", check=False).stdout.split()
    aborted = _git(path, "rebase", "--abort", check=False).returncode == 0
    where = shlex.quote(str(path))
    if not conflicts:
        raise CovenError(f"git rebase onto {upstream} failed in {path}: {_last_line(done.stderr)}")
    state = (
        "The rebase was aborted and your commits are untouched."
        if aborted
        else (f"Run git -C {where} rebase --abort first.")
    )
    raise CovenError(
        f"sync stopped: your commits conflict with {upstream} in {', '.join(conflicts)}. {state} "
        f"To resolve: git -C {where} rebase {upstream}, fix the files, git add them, "
        "git rebase --continue; then run coven memory sync again."
    )


def _rebuild_indexes(path: Path) -> list[str]:
    """Regenerate every INDEX.md (a union merge can leave duplicate lines) and commit the change."""
    changed = entries.rebuild_indexes(path)
    if not changed:
        return []
    try:
        _git(path, "add", "-A", "--", *changed)
        _commit(path, "rebuild memory indexes")
    except CovenError:
        _git(path, "reset", "-q", "--hard", check=False)
        _git(path, "clean", "-fdq", "--", *changed, check=False)
        raise
    return [f"rebuilt {', '.join(changed)} and committed"]


def hook_problem(path: Path) -> str | None:
    """Why git would not run coven's current pre-commit hook in this store, or None."""
    shipped = hook_version(HOOK_SOURCE.read_text(encoding="utf-8"))
    if shipped is None:
        raise CovenError(f"{HOOK_SOURCE} has no coven-hook-version line")
    hook = _installed_hook(path)
    try:
        installed = hook_version(hook.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return f"{hook} is missing"
    if installed is None:
        return f"{hook} is not coven's hook"
    if installed < shipped:
        return f"{hook} is version {installed}, coven ships {shipped}"
    if not os.access(hook, os.X_OK):
        return f"{hook} is not executable"
    return None


def _push(path: Path, upstream: str | None) -> list[str]:
    ahead = _count(path, f"{upstream}..HEAD" if upstream else "HEAD")
    if upstream and not ahead:
        return []
    problem = hook_problem(path)
    if problem:
        raise CovenError(
            f"not pushing: the pre-commit hook of {path} is not current ({problem}), so what "
            f"was committed may not have been scanned for secrets; run "
            f"coven memory init --path {shlex.quote(str(path))}, check the commits, then sync again"
        )
    pushed = _git(path, "push", "--quiet", "-u", "origin", "HEAD", check=False)
    if pushed.returncode != 0:
        raise CovenError(f"git push failed in {path}: {_last_line(pushed.stderr)}")
    return [f"pushed {ahead} commit(s)"]


def sync_store(path: Path) -> list[str]:
    """Fetch, rebase onto the upstream, rebuild the indexes, push; the report lines.

    A guarded store (HEAD holds `systems/guard.json`) only rebuilds its indexes and reports
    `not pushed: guarded store`.

    On a rebase conflict the rebase is aborted and `CovenError` lists the files and the resolve
    command. A push is refused when the store's pre-commit hook is missing or older than coven's.
    """
    if not is_store(path):
        raise CovenError(
            f"{path} is not a coven memory store; run coven memory init --path {shlex.quote(str(path))}"
        )
    _require_clean(path)
    report: list[str] = []
    if is_guarded(path):
        # A guarded store stays on this machine: no fetch, no rebase onto remote history, no push.
        return [*_rebuild_indexes(path), NOT_PUSHED_GUARDED]
    has_remote = _git(path, "remote", "get-url", "origin", check=False).returncode == 0
    upstream = None
    if has_remote:
        _git(path, "fetch", "--quiet", "origin")
        upstream = _upstream(path)
        behind = _count(path, f"HEAD..{upstream}") if upstream else 0
        if upstream and behind:
            _rebase(path, upstream)
            report.append(f"pulled {behind} commit(s)")
    report += _rebuild_indexes(path)
    if not has_remote:
        return [*report, "no remote: nothing to fetch or push"]
    report += _push(path, upstream)
    return report or ["already up to date"]
