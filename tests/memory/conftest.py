from pathlib import Path

import isolation
import pytest
from memory_support import Lab

# The test isolation (HOME, XDG, Claude and git), bound by name as in tests/conftest.py: this
# conftest may be loaded alone.
isolated_home = isolation.isolated_home
git_isolation = isolation.git_isolation


@pytest.fixture
def lab(tmp_path: Path) -> Lab:
    return Lab(tmp_path)


@pytest.fixture
def bare_remote(lab: Lab) -> Path:
    return lab.make_remote()
