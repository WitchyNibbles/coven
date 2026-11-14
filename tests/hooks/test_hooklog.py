import importlib.util
import json
import os
import stat
from pathlib import Path
from types import ModuleType

import pytest

from coven import paths

HOOKLOG = Path(__file__).resolve().parents[2] / "hooks" / "hooklog.py"


@pytest.fixture
def hooklog() -> ModuleType:
    spec = importlib.util.spec_from_file_location("coven_test_hooklog", HOOKLOG)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def lines(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_log_event_appends_one_json_line_with_a_timestamp(hooklog: ModuleType) -> None:
    hooklog.log_event("demo", {"error": "first", "code": 3})
    hooklog.log_event("demo", {"error": "second"})
    records = lines(paths.logs_dir() / "demo.jsonl")
    assert [r["error"] for r in records] == ["first", "second"]
    assert records[0]["code"] == 3
    assert isinstance(records[0]["ts"], str)
    assert records[0]["ts"].endswith("+00:00")


def test_the_log_folder_is_the_one_coven_paths_names(
    hooklog: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    assert hooklog.logs_dir() == paths.logs_dir()
    monkeypatch.delenv("XDG_CACHE_HOME")
    assert hooklog.logs_dir() == paths.logs_dir() == Path.home() / ".cache" / "coven" / "logs"
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "elsewhere"))
    assert hooklog.logs_dir() == paths.logs_dir() == tmp_path / "elsewhere" / "coven" / "logs"


def test_log_rotates_at_1mb(hooklog: ModuleType) -> None:
    log = paths.logs_dir() / "demo.jsonl"
    log.parent.mkdir(parents=True)
    log.write_text("x" * (1024 * 1024))
    hooklog.log_event("demo", {"error": "after"})
    assert (log.parent / "demo.jsonl.1").read_text() == "x" * (1024 * 1024)
    assert [r["error"] for r in lines(log)] == ["after"]


def test_a_log_just_under_1mb_is_not_rotated(hooklog: ModuleType) -> None:
    log = paths.logs_dir() / "demo.jsonl"
    log.parent.mkdir(parents=True)
    log.write_text("x" * (1024 * 1024 - 1))
    hooklog.log_event("demo", {"error": "after"})
    assert not (log.parent / "demo.jsonl.1").exists()


def test_a_second_rotation_replaces_the_old_backup(hooklog: ModuleType) -> None:
    log = paths.logs_dir() / "demo.jsonl"
    log.parent.mkdir(parents=True)
    (log.parent / "demo.jsonl.1").write_text("ancient")
    log.write_text("y" * (1024 * 1024))
    hooklog.log_event("demo", {"error": "after"})
    assert (log.parent / "demo.jsonl.1").read_text() == "y" * (1024 * 1024)


def test_log_event_never_raises_when_the_folder_cannot_be_made(
    hooklog: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("not a folder")
    monkeypatch.setenv("XDG_CACHE_HOME", str(blocker))
    hooklog.log_event("demo", {"error": "lost"})


def test_log_event_never_raises_for_a_value_json_cannot_hold(hooklog: ModuleType) -> None:
    loop: list[object] = []
    loop.append(loop)
    hooklog.log_event("demo", {"error": loop})
    hooklog.log_event("demo", {"error": object()})
    written = lines(paths.logs_dir() / "demo.jsonl")
    assert len(written) == 1
    assert "object object" in str(written[0]["error"])


def test_log_files_are_private_to_the_user(hooklog: ModuleType) -> None:
    old = os.umask(0o022)
    try:
        hooklog.log_event("demo", {"error": "first"})
    finally:
        os.umask(old)
    folder = paths.logs_dir()
    assert stat.S_IMODE((folder / "demo.jsonl").stat().st_mode) == 0o600
    assert stat.S_IMODE(folder.stat().st_mode) == 0o700
