"""`coven memory adopt <path>`: make an existing git memory store coven's store, in place.

The store holds a person's notes, so every step either leaves the store as it was or finishes the
job: a backup is made and read back before anything changes, entries are only renamed with
`git mv` (never edited or deleted), one commit holds all the changes and goes through coven's
pre-commit hook, and a failure before that commit puts the work tree back. A run that stopped is
finished by running the command again.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import os
import shlex
import shutil
import stat
import sys
import tarfile
import tempfile
import zlib
from collections import defaultdict
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cache
from pathlib import Path

from coven import paths, settings
from coven.config import GUARD_REQUIRED, load_config
from coven.errors import CovenError
from coven.memory import entries, store

COMMIT_MESSAGE = "adopt: coven store"
STATE_SUFFIX = "-state.md"
SCOPES = ("user", "systems", "projects", "handoffs")
KEYED_SCOPES = ("projects", "handoffs")
APPROVALS = "approvals"
KNOWN_FOLDERS = frozenset({*SCOPES, APPROVALS, ".git"})
ROLE_VAR = "COVEN_SESSION_ROLE"
HOOK_NAME = "pre-commit"
MOVED_HOOK_PREFIX = f"{HOOK_NAME}.pre-adopt-"
TEMP_SUFFIX = ".adopt-tmp"
HOOKS_PATH_LABEL = "core.hooksPath was: "
OLD_HOOK_LABEL = "old hook: "
BACKUP_LABEL = "backup: "
NOT_SET = "(unset)"
PRIVATE_DIR = 0o700
PRIVATE_FILE = 0o600
READ_CHUNK = 1 << 20


# ---- the survey: what the store holds ---------------------------------------------------


@dataclass(frozen=True)
class Counts:
    """Markdown files per scope (a `projects/<key>` folder is its own scope), approvals apart."""

    entries: dict[str, int]  # files with `name` and `description` in their frontmatter
    unframed: dict[str, int]  # other Markdown files
    approvals: dict[str, int]  # `.step` and `.json` files in `approvals/`

    def relabelled(self, renames: list[Rename]) -> Counts:
        """The counts with each renamed folder under its new label."""
        moved = {rename.source: rename.target for rename in renames}
        return Counts(
            {moved.get(label, label): count for label, count in self.entries.items()},
            {moved.get(label, label): count for label, count in self.unframed.items()},
            self.approvals,
        )

    def differences(self, after: Counts) -> list[str]:
        lines: list[str] = []
        sections = (
            ("", self.entries, after.entries),
            (" without frontmatter", self.unframed, after.unframed),
            (" approvals", self.approvals, after.approvals),
        )
        for suffix, before_counts, after_counts in sections:
            for label in sorted({*before_counts, *after_counts}):
                was, now = before_counts.get(label, 0), after_counts.get(label, 0)
                if was != now:
                    lines.append(f"{label}{suffix}: {was} before, {now} after")
        return lines


@dataclass(frozen=True)
class MarkdownFile:
    label: str
    relative: str
    is_entry: bool


def _label(relative: str) -> str:
    parts = relative.split("/")
    return "/".join(parts[:2]) if parts[0] in KEYED_SCOPES and len(parts) > 2 else parts[0]


def _is_entry(file: Path) -> bool:
    try:
        entry = entries.read_entry(file.name, file.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return False
    return bool(entry.name and entry.description)


def _markdown_files(root: Path) -> Iterator[MarkdownFile]:
    for scope in SCOPES:
        base = root / scope
        if not base.is_dir():
            continue
        for current, folders, files in os.walk(base):
            folders.sort()
            for name in sorted(files):
                if not name.endswith(".md") or name in entries.NOT_ENTRIES:
                    continue
                file = Path(current) / name
                relative = file.relative_to(root).as_posix()
                yield MarkdownFile(_label(relative), relative, _is_entry(file))


def _scope_labels(root: Path) -> list[str]:
    labels = [scope for scope in ("user", "systems") if (root / scope).is_dir()]
    for scope in KEYED_SCOPES:
        base = root / scope
        if base.is_dir():
            labels += [f"{scope}/{p.name}" for p in sorted(base.iterdir()) if p.is_dir()]
    return labels


def count_entries(root: Path) -> Counts:
    found = list(_markdown_files(root))
    entry_counts = dict.fromkeys(_scope_labels(root), 0)
    unframed: dict[str, int] = defaultdict(int)
    for file in found:
        if file.is_entry:
            entry_counts[file.label] = entry_counts.get(file.label, 0) + 1
        else:
            unframed[file.label] += 1
    approvals = {"step": 0, "json": 0}
    folder = root / APPROVALS
    if folder.is_dir():
        for child in folder.iterdir():
            kind = child.suffix.removeprefix(".")
            if child.is_file() and kind in approvals:
                approvals[kind] += 1
    return Counts(entry_counts, dict(unframed), approvals)


def _unknown_folders(root: Path) -> list[str]:
    return sorted(p.name for p in root.iterdir() if p.is_dir() and p.name not in KNOWN_FOLDERS)


# ---- keys -------------------------------------------------------------------------------


@cache
def _key_form() -> Callable[[str], str]:
    """coven's key form, from the file the memory-context hook uses (hooks are not a package)."""
    file = paths.root() / "hooks" / "project_key.py"
    spec = importlib.util.spec_from_file_location("coven_adopt_project_key", file)
    if spec is None or spec.loader is None:
        raise CovenError(f"cannot load {file}")
    module = importlib.util.module_from_spec(spec)
    # The plugin folder stays as it was installed: no bytecode next to the hook files.
    old, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = old
    normalise: Callable[[str], str] = module.normalise
    return normalise


@dataclass(frozen=True)
class Rename:
    scope: str
    old: str
    new: str
    has_state_record: bool

    @property
    def source(self) -> str:
        return f"{self.scope}/{self.old}"

    @property
    def target(self) -> str:
        return f"{self.scope}/{self.new}"

    @property
    def old_state(self) -> str:
        return f"{self.old}{STATE_SUFFIX}"

    @property
    def new_state(self) -> str:
        return f"{self.new}{STATE_SUFFIX}"

    def describe(self) -> str:
        state = f" (and {self.old_state} -> {self.new_state})" if self.has_state_record else ""
        return f"{self.source} -> {self.target}{state}"


def plan_renames(root: Path) -> list[Rename]:
    """The folders under `projects/` and `handoffs/` not yet in key form.

    Raises `CovenError` before anything changes when two folders (or a state record and a file
    beside it) would end up with the same name.
    """
    key_form = _key_form()
    renames: list[Rename] = []
    clashes: list[str] = []
    for scope in KEYED_SCOPES:
        base = root / scope
        if not base.is_dir():
            continue
        groups: dict[str, list[str]] = defaultdict(list)
        for folder in sorted(p.name for p in base.iterdir() if p.is_dir()):
            groups[key_form(folder)].append(folder)
        for key, names in groups.items():
            if len(names) > 1:
                shown = " and ".join(f"{scope}/{name}" for name in names)
                clashes.append(f"{shown} would both become {scope}/{key}")
                continue
            if names[0] == key:
                continue
            rename = Rename(
                scope, names[0], key, os.path.lexists(base / names[0] / f"{names[0]}{STATE_SUFFIX}")
            )
            clash = _state_clash(root, rename)
            if clash:
                clashes.append(clash)
            renames.append(rename)
    if clashes:
        raise CovenError(
            "folders would collide when renamed to coven's key form; nothing was changed:\n  "
            + "\n  ".join(clashes)
        )
    return renames


def _state_clash(root: Path, rename: Rename) -> str | None:
    folder = root / rename.source
    old, new = folder / rename.old_state, folder / rename.new_state
    if not rename.has_state_record or not os.path.lexists(new) or os.path.samefile(old, new):
        return None
    return f"{rename.source}/{rename.old_state} and {rename.source}/{rename.new_state} both exist"


Journal = list[tuple[str, str]]  # (from, to) of every move done, in order


def _git_mv(root: Path, source: str, target: str, journal: Journal) -> None:
    """`git mv`; a rename that only changes case goes through a temporary name, so it also works
    on a case-insensitive file system. Each move done is added to `journal`."""
    if source != target and source.lower() == target.lower():
        steps = [(source, f"{source}{TEMP_SUFFIX}"), (f"{source}{TEMP_SUFFIX}", target)]
    else:
        steps = [(source, target)]
    for step in steps:
        store.git(root, "mv", "--", *step)
        journal.append(step)


def _apply_rename(root: Path, rename: Rename, journal: Journal) -> None:
    _git_mv(root, rename.source, rename.target, journal)
    if rename.has_state_record:
        folder = rename.target
        _git_mv(root, f"{folder}/{rename.old_state}", f"{folder}/{rename.new_state}", journal)


def _index_changes(root: Path, renames: list[Rename]) -> list[str]:
    """The INDEX.md files `rebuild_indexes` would write after the renames, found on a scratch copy
    of the scope folders so the store itself is not touched."""
    with tempfile.TemporaryDirectory(prefix="coven-adopt-") as scratch_name:
        scratch = Path(scratch_name)
        for scope in ("user", "systems", "projects"):
            if (root / scope).is_dir():
                shutil.copytree(root / scope, scratch / scope, symlinks=True)
        for rename in renames:
            if rename.scope != "projects":
                continue
            (scratch / rename.source).rename(scratch / rename.target)
            if rename.has_state_record:
                folder = scratch / rename.target
                (folder / rename.old_state).rename(folder / rename.new_state)
        return entries.rebuild_indexes(scratch)


# ---- the old hook and the git configuration ---------------------------------------------


def _hooks_folder(root: Path) -> Path:
    return root / store.RELATIVE_HOOKS


def _foreign_hook(root: Path) -> Path | None:
    """The store's pre-commit file when coven did not install it (no coven marker in it)."""
    hook = _hooks_folder(root) / HOOK_NAME
    if not os.path.lexists(hook):
        return None
    try:
        text = hook.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return hook
    return hook if store.hook_version(text) is None else None


def _hook_is_current(root: Path) -> bool:
    hook = _hooks_folder(root) / HOOK_NAME
    try:
        same = hook.read_bytes() == store.HOOK_SOURCE.read_bytes()
    except OSError:
        return False
    return same and os.access(hook, os.X_OK)


def _local_hooks_path(root: Path) -> str | None:
    done = store.git(root, "config", "--local", "--get", "core.hooksPath", check=False)
    if done.returncode == 0:
        return done.stdout.strip()
    if done.returncode == 1:
        return None
    raise CovenError(f"cannot read core.hooksPath of {root}: {done.stderr.strip() or 'git failed'}")


def _free_name(folder: Path, base: str, suffix: str = "") -> str:
    """`base`, or `base-2`, `base-3`... when a file of that name (plus `suffix`) already exists."""
    candidate, number = base, 2
    while os.path.lexists(folder / f"{candidate}{suffix}"):
        candidate, number = f"{base}-{number}", number + 1
    return candidate


def _ensure_attributes(root: Path) -> None:
    file = root / store.ATTRIBUTES
    current = file.read_text(encoding="utf-8") if file.is_file() else ""
    if store.UNION_LINE in current.splitlines():
        return
    separator = "" if not current or current.endswith("\n") else "\n"
    file.write_text(f"{current}{separator}{store.UNION_LINE}\n", encoding="utf-8")


def _attributes_missing(root: Path) -> bool:
    file = root / store.ATTRIBUTES
    try:
        return store.UNION_LINE not in file.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return True


# ---- backup -----------------------------------------------------------------------------


def stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def backups_folder() -> Path:
    """`${XDG_STATE_HOME:-~/.local/state}/coven/backups`."""
    configured = os.environ.get("XDG_STATE_HOME")
    base = Path(configured) if configured else Path.home() / ".local" / "state"
    return base / "coven" / "backups"


@dataclass(frozen=True)
class Backup:
    archive: Path
    checksum: Path
    digest: str


def folder_member_count(root: Path) -> int:
    """What a tar of `root` holds: the folder itself and everything below it (links not followed)."""

    def fail(error: OSError) -> None:
        raise error

    total = 1
    for _current, folders, files in os.walk(root, onerror=fail):
        total += len(folders) + len(files)
    return total


def _private_backups_folder() -> Path:
    folder = backups_folder()
    folder.parent.parent.mkdir(parents=True, exist_ok=True)
    for level in (folder.parent, folder):
        try:
            os.mkdir(level, PRIVATE_DIR)  # created with the mode, so no umask window
        except FileExistsError:
            pass
    if stat.S_IMODE(folder.stat().st_mode) & 0o077:
        raise CovenError(f"{folder} can be read by others; run chmod 700 on it, then adopt again")
    return folder


def _create_private(file: Path, created: list[Path]) -> int:
    """Open a new file with the private mode; one that exists already is never touched."""
    try:
        handle = os.open(file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, PRIVATE_FILE)
    except FileExistsError as error:
        raise CovenError(
            f"a backup named {file.name} already exists; run again in a second"
        ) from error
    created.append(file)
    return handle


def _read_back(archive: Path, expected_members: int) -> None:
    """Open the archive, read every member to its end and compare the member count."""
    count = 0
    try:
        with tarfile.open(archive, "r:gz") as tar:
            for member in tar:
                count += 1
                handle = tar.extractfile(member) if member.isfile() else None
                while handle is not None and handle.read(READ_CHUNK):
                    pass
    except (tarfile.TarError, OSError, EOFError, zlib.error) as error:
        raise CovenError(f"the backup {archive} cannot be read back ({error})") from error
    if count != expected_members:
        raise CovenError(
            f"the backup {archive} holds {count} items but the folder has {expected_members}"
        )


def _sha256(file: Path) -> str:
    with file.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def make_backup(root: Path, archive_name: str) -> Backup:
    """A gzip tar of the whole folder (`.git` included), its checksum file and a read-back.

    A failure removes the files this call made, and only those."""
    folder = _private_backups_folder()
    archive = folder / f"{archive_name}.tar.gz"
    checksum = folder / f"{archive_name}.tar.gz.sha256"
    created: list[Path] = []
    try:
        with os.fdopen(_create_private(archive, created), "wb") as raw:
            with tarfile.open(fileobj=raw, mode="w:gz") as tar:
                tar.add(root, arcname=root.name)
        _read_back(archive, folder_member_count(root))
        digest = _sha256(archive)
        with os.fdopen(_create_private(checksum, created), "w", encoding="utf-8") as out:
            out.write(f"{digest}  {archive.name}\n")
    except (CovenError, OSError, tarfile.TarError):
        for file in created:
            file.unlink(missing_ok=True)
        raise
    return Backup(archive, checksum, digest)


# ---- the plan ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Plan:
    root: Path
    when: str
    counts: Counts
    unframed_files: list[str]
    unknown_folders: list[str]
    renames: list[Rename]
    backup_name: str
    foreign_hook: Path | None
    moved_hook_name: str
    hook_current: bool
    old_hooks_path: str | None
    attributes_missing: bool
    index_changes: list[str]
    guarded: bool
    config_changes: list[settings.Change]
    is_current_store: bool

    @property
    def hooks_path_pending(self) -> bool:
        return self.old_hooks_path != store.RELATIVE_HOOKS

    @property
    def steps_left(self) -> bool:
        return bool(
            self.foreign_hook
            or not self.hook_current
            or self.hooks_path_pending
            or self.renames
            or self.attributes_missing
            or self.index_changes
            or self.config_changes
        )


def _git_ok(root: Path, *args: str) -> str | None:
    done = store.git(root, *args, check=False)
    return done.stdout.strip() if done.returncode == 0 else None


def symlinked_paths(root: Path) -> list[str]:
    """Scope folders, their keyed children and `.gitattributes` that are symlinks: adopt writes
    in or beside them, and a link may lead outside the store."""
    found = [scope for scope in SCOPES if (root / scope).is_symlink()]
    for scope in KEYED_SCOPES:
        base = root / scope
        if base.is_dir() and not base.is_symlink():
            found += [f"{scope}/{p.name}" for p in sorted(base.iterdir()) if p.is_symlink()]
    if (root / store.ATTRIBUTES).is_symlink():
        found.append(store.ATTRIBUTES)
    return sorted(found)


def _check_work_tree(root: Path) -> None:
    if not root.is_dir():
        raise CovenError(f"{root} is not a folder")
    if _git_ok(root, "rev-parse", "--is-bare-repository") == "true":
        raise CovenError(f"{root} is a bare repo; adopt a work tree that holds the notes")
    top = _git_ok(root, "rev-parse", "--show-toplevel")
    if top is None:
        raise CovenError(f"{root} is not a git work tree")
    if Path(top).resolve() != root.resolve():
        raise CovenError(f"{root} is not the top folder of a git work tree (that is {top})")
    if not (root / ".git").is_dir():
        raise CovenError(f"{root}/.git is not a folder (a linked work tree or submodule)")
    if _git_ok(root, "rev-parse", "--verify", "--quiet", "HEAD") is None:
        raise CovenError(f"{root} has no commits; make one first")


def _check_clean_and_sound(root: Path) -> None:
    status = store.git(
        root, "--no-optional-locks", "status", "--porcelain", "--untracked-files=all", check=False
    )
    if status.returncode != 0:
        raise CovenError(f"git status failed in {root}: {status.stderr.strip()}")
    if status.stdout.strip():
        shown = ", ".join(line[3:] for line in status.stdout.splitlines()[:5])
        raise CovenError(f"{root} has uncommitted changes ({shown}); commit or remove them first")
    fsck = store.git(root, "fsck", "--no-progress", check=False)
    if fsck.returncode != 0:
        detail = (fsck.stderr or fsck.stdout).strip().splitlines()
        raise CovenError(f"git fsck failed in {root}: {detail[-1] if detail else 'no output'}")


def check_fit(root: Path) -> None:
    """Raise `CovenError` naming why `root` cannot be adopted; reads only."""
    _check_work_tree(root)
    links = symlinked_paths(root)
    if links:
        raise CovenError(
            f"{root} holds symlinks adopt will not follow or write through: {', '.join(links)}; "
            "replace each with the real folder (or remove it), commit, then adopt again"
        )
    _check_clean_and_sound(root)


def _same_folder(first: Path, second: Path) -> bool:
    try:
        return os.path.samefile(first, second)
    except OSError:
        return os.path.abspath(first) == os.path.abspath(second)


def plan_adoption(root: Path, when: str) -> Plan:
    """Preflight and plan; changes nothing. Raises `CovenError` when `root` cannot be adopted."""
    check_fit(root)
    renames = plan_renames(root)
    guarded = store.is_guarded(root)
    current = load_config()
    changes = settings.plan(
        settings.Choices(
            pm_cap=current.pm_cap,
            memory_path=root,
            guard=GUARD_REQUIRED if guarded else None,
        )
    )
    hooks = _hooks_folder(root)
    plan = Plan(
        root=root,
        when=when,
        counts=count_entries(root),
        unframed_files=[f.relative for f in _markdown_files(root) if not f.is_entry],
        unknown_folders=_unknown_folders(root),
        renames=renames,
        backup_name=_free_name(backups_folder(), f"{root.name}-{when}", ".tar.gz"),
        foreign_hook=_foreign_hook(root),
        moved_hook_name=_free_name(hooks, f"{MOVED_HOOK_PREFIX}{when}"),
        hook_current=_hook_is_current(root),
        old_hooks_path=_local_hooks_path(root),
        attributes_missing=_attributes_missing(root),
        index_changes=_index_changes(root, renames),
        guarded=guarded,
        config_changes=changes,
        is_current_store=_same_folder(current.memory_path, root),
    )
    if plan.is_current_store and not plan.steps_left:
        raise CovenError(f"{root} is already coven's store and nothing is left to adopt")
    return plan


def describe(plan: Plan) -> list[str]:
    lines = [f"coven memory adopt: plan for {plan.root}", "  entries:"]
    lines += [
        f"    {label}: {count} entries" for label, count in sorted(plan.counts.entries.items())
    ]
    if plan.counts.approvals["step"] or plan.counts.approvals["json"]:
        lines.append(
            f"  approvals: {plan.counts.approvals['step']} step, "
            f"{plan.counts.approvals['json']} json (counted apart)"
        )
    if plan.unframed_files:
        lines.append(f"  kept as is, without frontmatter: {', '.join(plan.unframed_files)}")
    if plan.unknown_folders:
        lines.append(f"  kept as is, outside coven's scopes: {', '.join(plan.unknown_folders)}")
    backups = backups_folder()
    lines.append(f"  backup: {backups / plan.backup_name}.tar.gz, with a .sha256 file beside it")
    if plan.foreign_hook:
        lines.append(
            f"  hook: {plan.foreign_hook} is not coven's; it moves to {plan.moved_hook_name}"
        )
    lines.append(
        "  hook: coven's pre-commit hook is already in place"
        if plan.hook_current and not plan.foreign_hook
        else "  hook: coven's pre-commit hook is installed"
    )
    if plan.old_hooks_path not in (None, store.RELATIVE_HOOKS):
        lines.append(
            f"  core.hooksPath: local value {plan.old_hooks_path} is recorded and replaced by "
            f"{store.RELATIVE_HOOKS}"
        )
    lines += [f"  rename: {rename.describe()}" for rename in plan.renames] or ["  rename: none"]
    if plan.attributes_missing:
        lines.append(f"  {store.ATTRIBUTES}: adds {store.UNION_LINE}")
    if plan.index_changes:
        lines.append(f"  indexes rewritten: {', '.join(plan.index_changes)}")
    lines.append(f'  commit: "{COMMIT_MESSAGE}" through the store\'s hook')
    lines += [f"  config: {settings.describe(change)}" for change in plan.config_changes] or [
        "  config: already set"
    ]
    return lines


# ---- the steps --------------------------------------------------------------------------


def _say(line: str) -> None:
    print(line, flush=True)


@dataclass(frozen=True)
class Outcome:
    backup: Backup
    commit: str | None  # this run's commit; None when the store needed none
    old_hooks_path: str | None
    earlier: Record | None  # the commit of an earlier run, shown when this run made none

    @property
    def revert_target(self) -> str | None:
        return self.commit or (self.earlier.commit if self.earlier else None)


@dataclass(frozen=True)
class HookUndo:
    """How to put the pre-commit hook file back as it was before `_install_hook`."""

    hook: Path
    moved: Path | None  # a foreign hook moved aside
    previous: bytes | None  # an older coven hook, replaced
    mode: int

    def restore(self) -> None:
        with contextlib.suppress(OSError):
            self.hook.unlink(missing_ok=True)
            if self.moved is not None:
                os.rename(self.moved, self.hook)
            elif self.previous is not None:
                self.hook.write_bytes(self.previous)
                self.hook.chmod(self.mode)


def _install_hook(plan: Plan) -> HookUndo:
    """Move a foreign hook aside and put coven's hook file in place. The store is still pinned to
    its old hooks folder; `_pin_hooks_path` follows the commit, so a stop before it keeps that
    value for the next run."""
    hook = _hooks_folder(plan.root) / HOOK_NAME
    moved: Path | None = None
    previous: bytes | None = None
    mode = 0o755
    if plan.foreign_hook:
        moved = plan.foreign_hook.with_name(plan.moved_hook_name)
        os.rename(plan.foreign_hook, moved)
        _say(f"  old hook moved to {moved}")
    elif hook.is_file():
        previous, mode = hook.read_bytes(), stat.S_IMODE(hook.stat().st_mode)
    store.install_hook(plan.root)
    return HookUndo(hook, moved, previous, mode)


def _pin_hooks_path(plan: Plan) -> None:
    if plan.old_hooks_path not in (None, store.RELATIVE_HOOKS):
        _say(f"  core.hooksPath was {plan.old_hooks_path}; it is replaced")
    store.pin_hooks_path(plan.root)


def _moved_hooks(root: Path) -> list[str]:
    return sorted(p.name for p in _hooks_folder(root).glob(f"{MOVED_HOOK_PREFIX}*"))


@dataclass(frozen=True)
class Record:
    """What an adopt commit says about the way back (its message carries it)."""

    commit: str
    old_hooks_path: str | None
    moved_hooks: str
    backup: str


def _commit_message(old_hooks_path: str | None, moved: list[str], backup: Backup) -> str:
    return "\n".join(
        [
            COMMIT_MESSAGE,
            "",
            f"{HOOKS_PATH_LABEL}{old_hooks_path or NOT_SET}",
            f"{OLD_HOOK_LABEL}{', '.join(moved) or 'none'}",
            f"{BACKUP_LABEL}{backup.archive}",
        ]
    )


def _earlier_record(root: Path) -> Record | None:
    """The newest adopt commit's record, None when the store has none.

    `--grep` also matches a commit body, so the subject is compared again: a later commit that
    only quotes the subject in its body cannot stand in for the adopt commit."""
    listed = store.git(
        root, "log", f"--grep=^{COMMIT_MESSAGE}$", "--format=%h%x00%s", check=False
    ).stdout
    for line in listed.splitlines():
        commit, _, subject = line.partition("\0")
        if subject == COMMIT_MESSAGE:
            return _record(root, commit)
    return None


def _record(root: Path, commit: str) -> Record:
    message = store.git(root, "log", "-1", "--format=%B", commit).stdout
    fields = {
        label: line.removeprefix(label)
        for line in message.splitlines()
        for label in (HOOKS_PATH_LABEL, OLD_HOOK_LABEL, BACKUP_LABEL)
        if line.startswith(label)
    }
    old = fields.get(HOOKS_PATH_LABEL, NOT_SET)
    return Record(
        commit,
        None if old == NOT_SET else old,
        fields.get(OLD_HOOK_LABEL, "none"),
        fields.get(BACKUP_LABEL, "unknown"),
    )


def _old_hooks_path(plan: Plan, earlier: Record | None) -> str | None:
    """The `core.hooksPath` the store had before adopt: the one set now, or when an earlier run
    already replaced it, the one that run's commit recorded."""
    if plan.old_hooks_path != store.RELATIVE_HOOKS:
        return plan.old_hooks_path
    return earlier.old_hooks_path if earlier else None


def _before_moves(relative: str, journal: Journal) -> str:
    """`relative` as it was named before the moves in `journal`."""
    for source, target in reversed(journal):
        if relative == target or relative.startswith(f"{target}/"):
            relative = source + relative.removeprefix(target)
    return relative


def _roll_back(root: Path, created: list[str], journal: Journal, undo: HookUndo) -> None:
    """Put the work tree and the hook back as they were: the tree was clean at the start, so the
    last commit is exactly what to return to. Folders move back first, so files git does not
    track (ignored ones) go with them instead of staying in the new folder.

    The store's `core.hooksPath` needs no undo: `_pin_hooks_path` runs after the commit."""
    for source, target in reversed(journal):
        with contextlib.suppress(OSError):
            os.rename(root / target, root / source)
    store.git(root, "reset", "-q", "--hard", "HEAD", check=False)
    wrote = [_before_moves(path, journal) for path in created]
    store.git(root, "--literal-pathspecs", "clean", "-fdq", "--", *wrote, check=False)
    undo.restore()


def _commit_changes(plan: Plan, message: str, undo: HookUndo) -> str | None:
    """Renames, `.gitattributes` and the indexes in one commit; the short hash, None when the
    store needed no change. On a failure the work tree goes back to HEAD."""
    root = plan.root
    created = [store.ATTRIBUTES, *plan.index_changes]
    journal: Journal = []
    try:
        for rename in plan.renames:
            _apply_rename(root, rename, journal)
        _ensure_attributes(root)
        changed = entries.rebuild_indexes(root)
        store.git(root, "--literal-pathspecs", "add", "-A", "--", store.ATTRIBUTES, *changed)
        if store.git(root, "diff", "--cached", "--quiet", check=False).returncode == 0:
            return None
        # The hooks folder is named for this commit, so coven's hook runs whatever the store's
        # core.hooksPath still says.
        done = store.git(
            root,
            "-c",
            f"core.hooksPath={store.RELATIVE_HOOKS}",
            "commit",
            "-q",
            "-m",
            message,
            check=False,
        )
        if done.returncode != 0:
            detail = (done.stderr or done.stdout).strip() or "no output"
            raise CovenError(
                f"the commit was refused; the work tree and the hook are as they were: {detail} "
                "(a commit needs user.name and user.email set)"
            )
    except BaseException:
        _roll_back(root, created, journal, undo)
        raise
    return store.git(root, "rev-parse", "--short", "HEAD").stdout.strip()


def _way_back(plan: Plan, outcome: Outcome, *, config_changed: bool) -> list[str]:
    root = shlex.quote(str(plan.root))
    hooks = _hooks_folder(plan.root)
    lines: list[str] = []
    if outcome.revert_target:
        lines.append(f"git -C {root} revert {outcome.revert_target}")
    for moved in _moved_hooks(plan.root):
        lines.append(
            f"mv -f {shlex.quote(str(hooks / moved))} {shlex.quote(str(hooks / HOOK_NAME))}"
        )
    if outcome.old_hooks_path:
        lines.append(
            f"git -C {root} config --local core.hooksPath {shlex.quote(outcome.old_hooks_path)}"
        )
    else:
        lines.append(f"git -C {root} config --local --unset core.hooksPath")
    if config_changed:
        lines.append("coven setup --undo --yes")
    lines.append(
        f"last resort, with {plan.root} moved aside: "
        f"tar -xzf {shlex.quote(str(outcome.backup.archive))} -C {shlex.quote(str(plan.root.parent))}"
    )
    return lines


def _check_counts(plan: Plan, outcome: Outcome) -> None:
    differences = plan.counts.relabelled(plan.renames).differences(count_entries(plan.root))
    if not differences:
        return
    raise CovenError(
        "the entry counts changed, which adopt never does; stopping.\n  "
        + "\n  ".join(differences)
        + "\nTo restore:\n  "
        + "\n  ".join(_way_back(plan, outcome, config_changed=False))
    )


def _report(plan: Plan, outcome: Outcome) -> list[str]:
    lines = [
        "coven memory adopt: done, counts equal before and after",
        f"  backup: {outcome.backup.archive}",
        f"  sha256: {outcome.backup.digest}",
        "  renames: " + ("; ".join(rename.describe() for rename in plan.renames) or "none"),
    ]
    moved = _moved_hooks(plan.root)
    lines.append("  old hook: " + (", ".join(moved) if moved else "none moved"))
    if outcome.old_hooks_path not in (None, store.RELATIVE_HOOKS):
        lines.append(f"  core.hooksPath was: {outcome.old_hooks_path}")
    lines.append(f"  commit: {outcome.commit or 'none needed'}")
    if outcome.earlier:
        lines.append(
            f"  earlier run, commit {outcome.earlier.commit}: core.hooksPath was "
            f"{outcome.earlier.old_hooks_path or NOT_SET}, old hook {outcome.earlier.moved_hooks}, "
            f"backup {outcome.earlier.backup}"
        )
    lines.append("  the way back:")
    lines += [f"    {line}" for line in _way_back(plan, outcome, config_changed=True)]
    return lines


def adopt_store(plan: Plan) -> list[str]:
    """Run the plan; the report lines. Raises `CovenError` when a step fails or the check does."""
    _say(f"backing up {plan.root}")
    backup = make_backup(plan.root, plan.backup_name)
    _say(f"  backup {backup.archive} read back, sha256 {backup.digest}")
    earlier = _earlier_record(plan.root)
    old_hooks_path = _old_hooks_path(plan, earlier)
    undo = _install_hook(plan)
    message = _commit_message(old_hooks_path, _moved_hooks(plan.root), backup)
    commit = _commit_changes(plan, message, undo)
    _pin_hooks_path(plan)
    outcome = Outcome(backup, commit, old_hooks_path, None if commit else earlier)
    _check_counts(plan, outcome)  # before the config write: a mismatch leaves the config alone
    settings.apply(plan.config_changes)
    return _report(plan, outcome)


# ---- the command ------------------------------------------------------------------------


def _resolve(argument: str) -> Path:
    absolute = os.path.abspath(Path(argument).expanduser())
    real = os.path.realpath(absolute)
    if real != absolute:
        raise CovenError(
            f"{absolute} goes through a symlink to {real}; pass the real path, "
            "so the config and the backup name the store itself"
        )
    return Path(absolute)


def run(args: argparse.Namespace) -> int:
    if ROLE_VAR in os.environ:
        raise CovenError(
            "adopt changes the memory store; this session has a role set, so ask the GM"
        )
    plan = plan_adoption(_resolve(args.path), stamp())
    for line in describe(plan):
        _say(line)
    if not args.yes:
        _say("coven memory adopt: nothing changed; rerun with --yes to adopt")
        return 0
    for line in adopt_store(plan):
        _say(line)
    return 0
