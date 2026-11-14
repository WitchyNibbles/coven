"""Helpers shared by the memory tests (imported by name: `tests/` has no packages)."""

import importlib.util
import subprocess
from pathlib import Path
from types import ModuleType

from coven.memory import store

HOOKS = Path(__file__).resolve().parents[2] / "hooks"
PROC_LINE = 'PROC_ROOT = Path("/proc")'


def load_hook(name: str) -> ModuleType:
    """A script of the repo's hooks folder, loaded by path (hooks are not a package)."""
    spec = importlib.util.spec_from_file_location(f"coven_test_{name}", HOOKS / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def neutral_hook(store_path: Path) -> None:
    """Point the installed hook's ancestor walk at a folder without processes.

    The real process tree may or may not run under a session role (a test run from a project
    manager does), and a hook that git starts as a subprocess cannot be monkeypatched. The role
    refusal itself is tested in process with a fake /proc.
    """
    hook = store_path / ".git" / "hooks" / "pre-commit"
    text = hook.read_text(encoding="utf-8")
    assert PROC_LINE in text
    hook.write_text(text.replace(PROC_LINE, 'PROC_ROOT = Path("/nonexistent-coven-proc")'))


class Lab:
    """Stores, clones and a local bare remote under one test folder. All git is local."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.remote = root / "remote.git"

    def git(self, cwd: Path, *args: str) -> str:
        done = subprocess.run(
            ["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True
        )
        return done.stdout

    def make_remote(self) -> Path:
        self.git(self.root, "init", "--bare", "-q", str(self.remote))
        return self.remote

    def store(self, name: str = "store", remote: Path | None = None) -> Path:
        """An initialised store with a neutral hook."""
        path = self.root / name
        store.init_store(path, str(remote) if remote else None)
        neutral_hook(path)
        return path

    def clone(self, name: str) -> Path:
        """A clone of the bare remote with a neutral hook."""
        path = self.root / name
        self.git(self.root, "clone", "-q", str(self.remote), str(path))
        store.install_hook(path)
        neutral_hook(path)
        return path

    @staticmethod
    def write(
        root: Path, relative: str, name: str, description: str, body: str = "Body.\n"
    ) -> None:
        """Write an entry. `name` and `description` sit two lines apart, so two clones can change
        one each without their edits touching."""
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            f"---\nname: {name}\nmetadata:\n  type: user\ndescription: {description}\n---\n{body}"
        )

    def commit(self, root: Path, message: str = "change") -> None:
        """Stage everything and commit; the store's hook runs."""
        self.git(root, "add", "-A")
        self.git(root, "commit", "-q", "-m", message)
