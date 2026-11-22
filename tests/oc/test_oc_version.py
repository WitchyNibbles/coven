import os
import subprocess
from pathlib import Path

import pytest
from doctor_kit import Good, results_by_name
from fakes import fake_opencode

from coven.opencode import runtime
from coven.opencode.profile import Layout, OpencodeError, layout


def printing(folder: Path, text: str) -> Path:
    """An executable whose output is exactly `text`."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "opencode"
    path.write_text(f"#!/bin/sh\ncat <<'EOF'\n{text}\nEOF\n")
    path.chmod(0o755)
    return path


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("opencode v2.3.2", "2.3.2"),
        ("2.3.2", "2.3.2"),
        ("1.19.0", "1.19.0"),
        ("v2.3.2", "2.3.2"),
        ("  opencode v2.3.2  ", "2.3.2"),
        ("opencode v2.3.2-beta", "2.3.2-beta"),
        ("opencode v2.3.2\nmore", "2.3.2"),
        ("hello world", None),
        ("opencode vX.Y.Z", None),
        ("opencode 2.3", None),
        ("", None),
    ],
)
def test_binary_version_returns_the_version_token(tmp_path: Path, line: str, expected: str | None):
    assert runtime.binary_version(printing(tmp_path, line), os.environ) == expected


def test_the_fake_prints_the_version_the_way_the_real_binary_does(tmp_path: Path):
    shown = subprocess.run(
        [str(fake_opencode(tmp_path, "2.3.2")), "--version"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert shown.stdout.strip() == "opencode v2.3.2"


@pytest.fixture
def home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Layout:
    monkeypatch.setenv("COVEN_OPENCODE_HOME", str(tmp_path / "profile"))
    return layout()


def test_checked_binary_accepts_the_real_form_of_the_pinned_version(
    home: Layout, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    path = printing(tmp_path / "bin", "opencode v2.3.2")
    monkeypatch.setenv("COVEN_OPENCODE_BIN", str(path))
    assert runtime.checked_binary(home, os.environ) == path


@pytest.mark.parametrize(
    ("printed", "reported"),
    [
        ("opencode v1.19.0", "1.19.0"),
        ("opencode v2.3.2-beta", "2.3.2-beta"),
        ("opencode v2.3.3", "2.3.3"),
        ("not a version", "no version"),
    ],
)
def test_checked_binary_refuses_anything_but_the_exact_pin(
    home: Layout, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, printed: str, reported: str
):
    path = printing(tmp_path / "bin", printed)
    monkeypatch.setenv("COVEN_OPENCODE_BIN", str(path))
    with pytest.raises(OpencodeError, match=f"reports {reported}; coven needs 2.3.2"):
        runtime.checked_binary(home, os.environ)


def test_the_doctor_runtime_check_takes_the_real_form_and_refuses_the_rest(
    good: Good, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    assert results_by_name(good)["runtime"].status == "ok"
    for printed in ("opencode v1.19.0", "opencode v2.3.2-beta"):
        path = printing(tmp_path / "other", printed)
        monkeypatch.setenv("COVEN_OPENCODE_BIN", str(path))
        assert results_by_name(good)["runtime"].status == "fail"
    monkeypatch.setenv("COVEN_OPENCODE_BIN", str(printing(tmp_path / "real", "opencode v2.3.2")))
    assert results_by_name(good)["runtime"].status == "ok"
