"""The installed Claude Code version: the minimum, the last tested, and one-line errors."""

import subprocess
from pathlib import Path

import pytest
from pm.fixtures import BYTES_CLI, fake_claude

from coven import claude_version
from coven.errors import CovenError


@pytest.fixture
def bin_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = tmp_path / "fake-bin"
    folder.mkdir()
    monkeypatch.setenv("PATH", str(folder))  # the real claude is out of reach
    return folder


def install(bin_dir: Path, output: str | bytes, code: int = 0) -> None:
    data = output.encode() if isinstance(output, str) else output
    fake_claude(bin_dir, BYTES_CLI, output=data, error=b"", code=code)


def test_constants():
    assert claude_version.MINIMUM == (2, 1, 224)
    assert claude_version.MINIMUM <= claude_version.LAST_TESTED


def test_the_version_claude_prints_is_parsed(bin_dir):
    install(bin_dir, "2.1.323 (Claude Code)\n")
    assert claude_version.installed() == (2, 1, 323)


@pytest.mark.parametrize(
    ("output", "version"),
    [
        ("2.1.224 (Claude Code)\n", (2, 1, 224)),
        ("2.1.224\n", (2, 1, 224)),
        ("v2.2.0\n", (2, 2, 0)),
        ("Claude Code 2.1.400\n", (2, 1, 400)),
        ("2.1.323-beta.1 (Claude Code)\n", (2, 1, 323)),
        ("10.0.1 (Claude Code)\nsecond line 1.2.3\n", (10, 0, 1)),
    ],
)
def test_other_version_formats_are_read(bin_dir, output, version):
    install(bin_dir, output)
    assert claude_version.installed() == version


def test_claude_missing_one_line_error(bin_dir):
    with pytest.raises(CovenError) as error:
        claude_version.installed()
    assert str(error.value) == "claude not found on PATH"


def test_claude_too_old_one_line_error(bin_dir):
    install(bin_dir, "2.1.223 (Claude Code)\n")
    with pytest.raises(CovenError) as error:
        claude_version.installed()
    assert str(error.value) == "Claude Code 2.1.223 is older than 2.1.224"


def test_the_minimum_itself_is_accepted(bin_dir):
    install(bin_dir, "2.1.224 (Claude Code)\n")
    assert claude_version.installed() == claude_version.MINIMUM


def test_a_newer_major_is_not_too_old(bin_dir):
    install(bin_dir, "3.0.0 (Claude Code)\n")
    assert claude_version.installed() == (3, 0, 0)


@pytest.mark.parametrize("output", ["", "no version here\n", "2.1 (Claude Code)\n"])
def test_unreadable_output_one_line_error(bin_dir, output):
    install(bin_dir, output)
    with pytest.raises(CovenError) as error:
        claude_version.installed()
    assert str(error.value) == "could not read the Claude Code version from `claude --version`"


def test_a_failing_claude_one_line_error(bin_dir):
    install(bin_dir, "2.1.323 (Claude Code)\n", code=3)
    with pytest.raises(CovenError, match=r"^`claude --version` exited 3$"):
        claude_version.installed()


def test_a_claude_that_cannot_run_one_line_error(bin_dir):
    (bin_dir / "claude").write_text("#!/nonexistent/interpreter\n")
    (bin_dir / "claude").chmod(0o755)
    with pytest.raises(CovenError) as error:
        claude_version.installed()
    assert "\n" not in str(error.value)
    assert str(bin_dir) not in str(error.value)


def test_a_claude_that_hangs_one_line_error():
    def run(argv):
        raise subprocess.TimeoutExpired(argv, 10)

    with pytest.raises(CovenError, match=r"^`claude --version` did not answer in 10 s$"):
        claude_version.installed(run=run)


def test_installed_asks_for_the_version_once():
    seen: list[list[str]] = []

    def run(argv):
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, "2.1.323 (Claude Code)\n", "")

    assert claude_version.installed(run=run) == (2, 1, 323)
    assert seen == [["claude", "--version"]]


def test_output_that_is_not_utf8_is_decoded_leniently(bin_dir):
    install(bin_dir, b"\xff 2.1.323 (Claude Code)\n")
    assert claude_version.installed() == (2, 1, 323)


def test_unreadable_non_utf8_output_is_a_one_line_error(bin_dir):
    install(bin_dir, b"\xff\xfe\n")
    with pytest.raises(CovenError, match="could not read the Claude Code version"):
        claude_version.installed()
