from pathlib import Path

import pytest
from memory_support import Lab


@pytest.fixture
def lab(tmp_path: Path) -> Lab:
    return Lab(tmp_path)


@pytest.fixture
def bare_remote(lab: Lab) -> Path:
    return lab.make_remote()
