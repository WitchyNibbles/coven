from pathlib import Path

import pytest

from coven import cli, paths
from coven.opencode import checkout
from coven.opencode.profile import OpencodeError

REFUSAL = (
    "coven: coven opencode needs a full copy of the coven repo (a git clone): opencode/ and "
    "hooks/ are not in an installed wheel. Clone https://github.com/WitchyNibbles/coven and "
    "run its bin/coven.\n"
)
SETUP_FLAGS = [
    "--preset",
    "litellm",
    "--base-url",
    "https://gateway.example/v1",
    "--key-env",
    "GATEWAY_API_KEY",
    "--model",
    "lead=MODEL_ID",
]
SUBCOMMANDS = {
    "install": ["install"],
    "setup": ["setup", *SETUP_FLAGS],
    "run": ["run", "--", "--version"],
    "doctor": ["doctor"],
}


def tree_at(root: Path, *, opencode: bool = True, hook: bool = True) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    if opencode:
        (root / "opencode").mkdir()
    if hook:
        (root / "hooks").mkdir()
        (root / "hooks" / "deny_secrets.py").write_text("")
    return root


@pytest.fixture
def wheel_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """What `paths.root()` is in a wheel install: a folder with `src/` and nothing else."""
    root = tmp_path / "site-packages"
    (root / "coven").mkdir(parents=True)
    monkeypatch.setattr(paths, "root", lambda: root)
    return root


@pytest.mark.parametrize("args", SUBCOMMANDS.values(), ids=SUBCOMMANDS.keys())
def test_every_subcommand_refuses_a_wheel_install_before_doing_anything(
    args: list[str],
    wheel_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    profile = tmp_path / "profile"
    monkeypatch.setenv("COVEN_OPENCODE_HOME", str(profile))

    code = cli.main(["opencode", *args])

    captured = capsys.readouterr()
    assert code == 1
    assert captured.err == REFUSAL
    assert captured.out == ""
    assert not profile.exists()


@pytest.mark.parametrize(
    "missing", [{"opencode": False}, {"hook": False}], ids=["no-opencode-dir", "no-deny-secrets"]
)
def test_a_tree_missing_either_part_is_refused(tmp_path: Path, missing: dict[str, bool]):
    with pytest.raises(OpencodeError, match="needs a full copy of the coven repo"):
        checkout.require_checkout(tree_at(tmp_path / "partial", **missing))


def test_a_full_tree_passes(tmp_path: Path):
    checkout.require_checkout(tree_at(tmp_path / "clone"))


def test_the_repo_this_runs_from_passes():
    checkout.require_checkout(paths.root())
