import os
import stat
import subprocess
from pathlib import Path

from support import REPO

BIN = REPO / "bin" / "coven"


def run(
    *args: str, path: str | None = None, launcher: Path = BIN
) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PATH": path if path is not None else os.environ["PATH"]}
    return subprocess.run(
        [str(launcher), *args], capture_output=True, text=True, env=env, check=False
    )


def fake_python(directory: Path, version: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    script = directory / "python3"
    script.write_text(f"#!/bin/sh\necho {version}\n")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return script


def test_the_launcher_is_executable():
    assert BIN.stat().st_mode & stat.S_IXUSR


def test_bin_refuses_old_python(tmp_path: Path):
    script = fake_python(tmp_path / "old", "3.9.6")
    done = run("project", "--help", path=f"{tmp_path / 'old'}:/usr/bin:/bin")
    assert done.returncode == 1
    assert done.stderr == f"coven: needs Python 3.12 or later, found 3.9.6 at {script}\n"
    assert done.stdout == ""


def test_bin_refuses_python_2(tmp_path: Path):
    fake_python(tmp_path / "old", "2.7.18")
    done = run(path=f"{tmp_path / 'old'}:/usr/bin:/bin")
    assert done.returncode == 1
    assert "found 2.7.18" in done.stderr


def test_bin_refuses_a_version_it_cannot_read(tmp_path: Path):
    fake_python(tmp_path / "odd", "banana")
    done = run(path=f"{tmp_path / 'odd'}:/usr/bin:/bin")
    assert done.returncode == 1
    assert "needs Python 3.12 or later, found banana" in done.stderr


def test_bin_says_when_python3_is_missing(tmp_path: Path):
    (tmp_path / "empty").mkdir()
    done = run(path=str(tmp_path / "empty"))
    assert done.returncode == 1
    assert done.stderr == "coven: python3 not found on PATH (needs Python 3.12 or later)\n"


def test_bin_runs_the_cli():
    done = run("project", "--help")
    assert done.returncode == 0
    assert "usage: coven project" in done.stdout


def test_bin_works_through_a_symlink(tmp_path: Path):
    link = tmp_path / "somewhere" / "else" / "coven"
    link.parent.mkdir(parents=True)
    link.symlink_to(BIN)
    done = run("project", "--help", launcher=link)
    assert done.returncode == 0
    assert "usage: coven project" in done.stdout


def test_bin_works_through_a_chain_of_relative_symlinks(tmp_path: Path):
    first = tmp_path / "a" / "coven"
    first.parent.mkdir()
    first.symlink_to(BIN)
    second = tmp_path / "b" / "coven"
    second.parent.mkdir()
    second.symlink_to(Path(os.path.relpath(first, second.parent)))
    done = run("project", "--help", launcher=second)
    assert done.returncode == 0
    assert "usage: coven project" in done.stdout


def test_bin_passes_the_exit_code_and_error_through(tmp_path: Path):
    done = run("project", str(tmp_path / "nowhere"))
    assert done.returncode == 1
    assert done.stderr.startswith("coven: ")
    assert "Traceback" not in done.stderr
