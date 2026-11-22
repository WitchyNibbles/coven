from pathlib import Path

import pytest
from doctor_kit import KEY_NAME, KEY_VALUE, LITELLM_FLAGS, Good
from fakes import fake_opencode

from coven import cli
from coven.opencode.profile import layout


@pytest.fixture
def good(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Good:
    """A profile set up with the litellm preset, a fake 2.3.2 binary and the key set."""
    monkeypatch.setenv("COVEN_OPENCODE_HOME", str(tmp_path / "profile"))
    assert cli.main(["opencode", "setup", *LITELLM_FLAGS]) == 0
    bin_dir = tmp_path / "bin"
    monkeypatch.setenv("COVEN_OPENCODE_BIN", str(fake_opencode(bin_dir, "2.3.2")))
    monkeypatch.setenv(KEY_NAME, KEY_VALUE)
    cwd = tmp_path / "work"
    cwd.mkdir()
    return Good(layout(), bin_dir, cwd)
