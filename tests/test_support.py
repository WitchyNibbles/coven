"""support.load_module: the by-path loader the tests share. Everything happens under tmp_path."""

import sys
from pathlib import Path
from types import ModuleType

import pytest
from support import load_module

NAME = "coven_test_support_subject"


def write_module(folder: Path, source: str) -> Path:
    path = folder / "subject.py"
    path.write_text(source, encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def forget_subject(monkeypatch: pytest.MonkeyPatch) -> None:
    """Whatever a test registers under NAME is removed again when it ends."""
    monkeypatch.setitem(sys.modules, NAME, ModuleType("placeholder"))
    monkeypatch.delitem(sys.modules, NAME)


def test_it_returns_the_module_the_file_defines(tmp_path: Path) -> None:
    module = load_module(write_module(tmp_path, "ANSWER = 42\n"), NAME)
    assert (module.__name__, module.ANSWER) == (NAME, 42)


def test_it_does_not_register_the_module_by_default(tmp_path: Path) -> None:
    load_module(write_module(tmp_path, "ANSWER = 42\n"), NAME)
    assert NAME not in sys.modules


def test_register_puts_the_module_in_sys_modules_before_it_runs(tmp_path: Path) -> None:
    source = "import sys\nSEEN = sys.modules.get(__name__)\n"
    module = load_module(write_module(tmp_path, source), NAME, register=True)
    assert sys.modules[NAME] is module
    assert module.SEEN is module


def test_a_path_the_module_inserts_stays_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "path", list(sys.path))
    source = f"import sys\nsys.path.insert(0, {str(tmp_path)!r})\n"
    load_module(write_module(tmp_path, source), NAME)
    assert sys.path[0] == str(tmp_path)


def test_restore_path_puts_sys_path_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "path", list(sys.path))
    before = list(sys.path)
    source = f"import sys\nsys.path.insert(0, {str(tmp_path)!r})\n"
    load_module(write_module(tmp_path, source), NAME, restore_path=True)
    assert sys.path == before


def test_no_bytecode_is_written_next_to_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "dont_write_bytecode", False)
    load_module(write_module(tmp_path, "ANSWER = 42\n"), NAME)
    assert not (tmp_path / "__pycache__").exists()


def test_the_bytecode_flag_is_back_after_a_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "dont_write_bytecode", False)
    load_module(write_module(tmp_path, "ANSWER = 42\n"), NAME)
    assert sys.dont_write_bytecode is False


def test_the_bytecode_flag_is_back_after_a_module_that_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "dont_write_bytecode", False)
    with pytest.raises(RuntimeError, match="no good"):
        load_module(write_module(tmp_path, 'raise RuntimeError("no good")\n'), NAME)
    assert sys.dont_write_bytecode is False


def test_the_path_is_back_after_a_module_that_fails_with_restore_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "path", list(sys.path))
    before = list(sys.path)
    source = f'import sys\nsys.path.insert(0, {str(tmp_path)!r})\nraise RuntimeError("no good")\n'
    with pytest.raises(RuntimeError, match="no good"):
        load_module(write_module(tmp_path, source), NAME, restore_path=True)
    assert sys.path == before


def test_a_file_that_cannot_be_a_module_is_an_import_error(tmp_path: Path) -> None:
    with pytest.raises(ImportError, match="cannot load"):
        load_module(tmp_path / "subject.txt", NAME)
