"""Helpers shared by the memory tests (imported by name: `tests/` has no packages)."""

import subprocess
from pathlib import Path
from types import ModuleType

from support import PROC_LINE as PROC_LINE
from support import load_module, neutral_hook

from coven.memory import store

HOOKS = Path(__file__).resolve().parents[2] / "hooks"


def load_hook(name: str) -> ModuleType:
    """A script of the repo's hooks folder, loaded by path (hooks are not a package)."""
    return load_module(HOOKS / f"{name}.py", f"coven_test_{name}")


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
