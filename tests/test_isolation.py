"""The test isolation loads with any conftest, not only the root one.

A run that loads one sub-conftest alone (`-p conftest` with a test file outside `tests/`, as a
probe or a tool may do) never reaches `tests/conftest.py`. The guards' fixtures write HOME, so
without the isolation they wrote the real home. Each conftest therefore binds the isolation
fixtures by name, and the helpers that write coven's config refuse a HOME that is not the
scratch home `isolated_home` marked.
"""

import ast
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from isolation import (
    ISOLATION_FIXTURES,
    SCRATCH_HOME,
    ScratchHomeError,
    require_scratch_home,
)
from support import REPO

TESTS = Path(__file__).resolve().parent

# What every probe checks first: it runs with a real HOME it must not reach and no marker.
CHECK_ISOLATED = """
import os
import subprocess


def check_isolated():
    real = os.environ["PROBE_REAL_HOME"]
    assert os.environ["HOME"] != real
    assert os.environ["XDG_CONFIG_HOME"] != real + "/.config"
    assert os.environ["CLAUDE_CONFIG_DIR"] != real + "/.claude"
    # `runners` clears the other GIT_ variables, and keeps this one.
    assert os.environ["GIT_CONFIG_NOSYSTEM"] == "1"
"""

# One probe per conftest: the fixtures it names are that conftest's own, and the ones that write
# HOME are the reason the isolation must come with it.
PROBES = {
    "tests": ("", "def test_probe(git_repo):\n    check_isolated()\n"),
    "tests/hooks": (
        "tests/hooks",
        "def test_probe(world, store, runners, context):\n    check_isolated()\n",
    ),
    "tests/memory": (
        "tests/memory",
        "def test_probe(lab, bare_remote):\n    check_isolated()\n",
    ),
    "tests/oc": ("tests/oc", "def test_probe(good):\n    check_isolated()\n"),
}


def run_probe(
    tmp_path: Path, folder: str, test: str
) -> tuple[subprocess.CompletedProcess[str], Path]:
    """Run `test` outside `tests/` with only the conftest of `folder` loaded, as the probe did.

    The returned folder is the stand-in for the real home: it is empty when nothing reached it.
    """
    real_home = tmp_path / "real-home"
    real_home.mkdir()
    probe = tmp_path / "probe" / "test_probe.py"
    probe.parent.mkdir()
    probe.write_text(textwrap.dedent(CHECK_ISOLATED) + "\n\n" + test)
    path = [str(REPO / folder)] if folder else []
    environment = {
        key: value
        for key, value in os.environ.items()
        # Everything the outer isolation set, and the outer pytest's own options and worker.
        if not key.startswith(("PYTEST_", "GIT_", "XDG_", "CLAUDE_", "COVEN_", "OPENCODE_"))
        and key not in {"HOME", SCRATCH_HOME}
    }
    environment |= {
        "HOME": str(real_home),
        "XDG_CONFIG_HOME": str(real_home / ".config"),
        "PROBE_REAL_HOME": str(real_home),
        "PYTHONPATH": os.pathsep.join([*path, str(TESTS)]),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(probe),
            "-p",
            "conftest",
            "-c",
            "/dev/null",
            "--rootdir",
            str(tmp_path),
            "-q",
            "-x",
            "-p",
            "no:cacheprovider",
        ],
        cwd=REPO,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )
    return done, real_home


@pytest.mark.parametrize("folder", PROBES)
def test_loading_one_conftest_alone_keeps_the_real_home_untouched(
    tmp_path: Path, folder: str
) -> None:
    path, test = PROBES[folder]
    done, real_home = run_probe(tmp_path, path, test)
    touched = sorted(str(file.relative_to(real_home)) for file in real_home.rglob("*"))
    assert touched == [], f"the probe wrote the real home: {touched}"
    assert done.returncode == 0, done.stdout + done.stderr


def conftests() -> list[Path]:
    return sorted(
        file
        for file in TESTS.rglob("conftest.py")
        if "fixtures" not in file.relative_to(TESTS).parts
    )


def bound_names(file: Path) -> set[str]:
    """The names a module binds at its top level by assignment, import or definition."""
    names: set[str] = set()
    for node in ast.parse(file.read_text()).body:
        match node:
            case ast.Assign(targets=targets):
                names |= {target.id for target in targets if isinstance(target, ast.Name)}
            case ast.AnnAssign(target=ast.Name(id=name)):
                names.add(name)
            case ast.Import(names=aliases) | ast.ImportFrom(names=aliases):
                names |= {(alias.asname or alias.name).split(".")[0] for alias in aliases}
            case ast.FunctionDef(name=name):
                names.add(name)
    return names


def test_there_are_conftests_to_check() -> None:
    folders = {file.parent.name for file in conftests()}
    assert {"tests", "hooks", "memory", "oc"} <= folders


@pytest.mark.parametrize("file", conftests(), ids=lambda file: str(file.relative_to(TESTS)))
def test_every_conftest_binds_the_isolation_fixtures(file: Path) -> None:
    missing = set(ISOLATION_FIXTURES) - bound_names(file)
    assert not missing, f"{file.relative_to(REPO)} must bind {sorted(missing)} from isolation.py"


def test_the_isolation_module_defines_exactly_the_fixtures_the_conftests_must_bind() -> None:
    module = ast.parse((TESTS / "isolation.py").read_text())
    autouse = {
        node.name
        for node in module.body
        if isinstance(node, ast.FunctionDef)
        and any("autouse=True" in ast.unparse(decorator) for decorator in node.decorator_list)
    }
    assert autouse == set(ISOLATION_FIXTURES)


def test_isolated_home_marks_the_scratch_home() -> None:
    assert os.environ[SCRATCH_HOME] == os.environ["HOME"]
    assert Path(os.environ["HOME"]).is_dir()


def test_require_scratch_home_accepts_the_marked_home() -> None:
    assert require_scratch_home() == Path(os.environ["HOME"])


def test_require_scratch_home_refuses_a_home_without_the_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(SCRATCH_HOME)
    with pytest.raises(ScratchHomeError, match="scratch home"):
        require_scratch_home()


def test_require_scratch_home_refuses_a_home_other_than_the_marked_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "elsewhere"))
    with pytest.raises(ScratchHomeError, match="scratch home"):
        require_scratch_home()


def test_require_scratch_home_refuses_when_home_is_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HOME")
    with pytest.raises(ScratchHomeError, match="scratch home"):
        require_scratch_home()
