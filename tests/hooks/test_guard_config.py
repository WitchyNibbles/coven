"""guard_config: the config reads the production guard's off path needs, in a module that imports
only os, sys and json. The guard's own wrappers (GuardConfig, read_config) live in prod_guard and
are tested with it."""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

HOOKS = Path(__file__).resolve().parents[2] / "hooks"
# What a thin off path must not pull in: each one is milliseconds on every tool call.
HEAVY = ("pathlib", "typing", "logging", "subprocess", "ipaddress", "dataclasses", "hook_support")


def load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"coven_test_{name}", HOOKS / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def gc() -> ModuleType:
    return load("guard_config")


def home() -> Path:
    return Path(os.environ["HOME"])


def write_config(text: str) -> Path:
    file = home() / ".config" / "coven" / "config.json"
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(text, encoding="utf-8")
    return file


def test_the_config_file_is_under_home(gc: ModuleType) -> None:
    assert gc.config_path() == str(home() / ".config" / "coven" / "config.json")


def test_without_a_config_file_the_store_is_the_default_one_and_there_is_no_latch(
    gc: ModuleType,
) -> None:
    assert gc.read_settings() == (str(home() / "coven-memory"), False)


@pytest.mark.parametrize(
    ("text", "store", "latch"),
    [
        ("{}", "{home}/coven-memory", False),
        ('{"memory_path": null, "guard": null}', "{home}/coven-memory", False),
        ('{"guard": "required"}', "{home}/coven-memory", True),
        ('{"memory_path": "~/notes"}', "{home}/notes", False),
        ('{"memory_path": "~"}', "{home}", False),
        ('{"pm_cap": 3, "memory_path": "/srv/store", "guard": "required"}', "/srv/store", True),
    ],
)
def test_the_store_and_the_latch_come_from_the_config(
    gc: ModuleType, text: str, store: str, latch: bool
) -> None:
    write_config(text)
    assert gc.read_settings() == (store.format(home=home()), latch)


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("{not json", "not valid JSON"),
        ("[]", "must hold a JSON object"),
        ('{"memory_path": 3}', "memory_path must be a string"),
        ('{"memory_path": "notes"}', "memory_path must be an absolute path"),
        ('{"guard": "yes"}', 'guard must be "required" or null'),
        ('{"guard": true}', 'guard must be "required" or null'),
    ],
)
def test_a_config_that_is_not_what_is_expected_is_a_config_error(
    gc: ModuleType, text: str, reason: str
) -> None:
    file = write_config(text)
    with pytest.raises(gc.ConfigError) as error:
        gc.read_settings()
    assert str(error.value).startswith(f"{file}: ")
    assert reason in str(error.value)


def test_a_memory_path_of_an_unknown_user_is_the_error_path_home_raises(gc: ModuleType) -> None:
    write_config('{"memory_path": "~coven-no-such-user/notes"}')
    with pytest.raises(RuntimeError, match="Could not determine home directory."):
        gc.read_settings()


def test_without_a_home_folder_the_config_path_is_the_error_path_home_raises(
    gc: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(os.path, "expanduser", lambda path: path)  # `~` stays `~`: no home
    with pytest.raises(RuntimeError, match="Could not determine home directory."):
        gc.config_path()
    with pytest.raises(RuntimeError, match="Could not determine home directory."):
        gc.guard_is_off()


def test_a_config_that_cannot_be_read_is_a_config_error(gc: ModuleType) -> None:
    file = write_config("")
    file.unlink()
    file.mkdir()  # a folder where the file should be
    with pytest.raises(gc.ConfigError, match="cannot be read"):
        gc.read_settings()


def test_a_config_that_is_not_utf8_is_a_config_error(gc: ModuleType) -> None:
    write_config("").write_bytes(b'{"memory_path": "\xff"}')
    with pytest.raises(gc.ConfigError, match="cannot be read"):
        gc.read_settings()


def test_the_depth_limit_is_the_one_hook_support_applies(gc: ModuleType) -> None:
    assert gc.MAX_DEPTH == load("hook_support").MAX_DEPTH


def test_a_config_nested_deeper_than_the_limit_is_not_valid_json(gc: ModuleType) -> None:
    depth = gc.MAX_DEPTH + 1
    write_config('{"x": ' + "[" * depth + "]" * depth + "}")
    with pytest.raises(gc.ConfigError, match=f"JSON nested deeper than {gc.MAX_DEPTH} levels"):
        gc.read_settings()


def test_the_depth_check_finds_hook_support_when_the_hooks_folder_is_not_on_the_path(
    gc: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    # `python3 -I` leaves the script's folder off sys.path: the module adds it itself, once.
    monkeypatch.setattr(sys, "path", [p for p in sys.path if Path(p).resolve() != HOOKS])
    # Restores whatever was loaded as hook_support (or nothing) when the test ends.
    monkeypatch.setitem(sys.modules, "hook_support", ModuleType("placeholder"))
    del sys.modules["hook_support"]
    depth = gc.MAX_DEPTH + 1
    write_config('{"x": ' + "[" * depth + "]" * depth + "}")
    for _ in range(2):
        with pytest.raises(gc.ConfigError, match=f"JSON nested deeper than {gc.MAX_DEPTH} levels"):
            gc.read_settings()
    assert [Path(p).resolve() for p in sys.path].count(HOOKS) == 1


def test_a_config_at_the_depth_limit_is_read(gc: ModuleType) -> None:
    depth = gc.MAX_DEPTH - 1  # the object itself is the first level
    write_config('{"x": ' + "[" * depth + "]" * depth + "}")
    assert gc.read_settings() == (str(home() / "coven-memory"), False)


def test_brackets_inside_a_string_do_not_count_as_depth(gc: ModuleType) -> None:
    # More brackets than the limit, none of them nesting: the slow path must accept it.
    write_config(json.dumps({"note": "[" * (gc.MAX_DEPTH + 10), "guard": "required"}))
    assert gc.read_settings() == (str(home() / "coven-memory"), True)


def test_the_latch_is_never_off(gc: ModuleType, tmp_path: Path) -> None:
    assert gc.off_reason_for(str(tmp_path / "nowhere"), latch=True) is None


def test_a_missing_store_folder_is_off(gc: ModuleType, tmp_path: Path) -> None:
    reason = gc.off_reason_for(str(tmp_path / "nowhere"), latch=False)
    assert reason == f"no memory store folder at {tmp_path / 'nowhere'}"


def test_a_store_without_a_guard_file_is_off(gc: ModuleType, tmp_path: Path) -> None:
    reason = gc.off_reason_for(str(tmp_path), latch=False)
    assert reason == (
        f'no "guard": "required" in the config and no systems/guard.json in {tmp_path}'
    )


def test_a_guard_file_turns_the_guard_on_even_when_it_is_a_dangling_link(
    gc: ModuleType, tmp_path: Path
) -> None:
    (tmp_path / "systems").mkdir()
    (tmp_path / "systems" / "guard.json").symlink_to(tmp_path / "nowhere")
    assert gc.off_reason_for(str(tmp_path), latch=False) is None


def test_the_guard_is_off_without_a_config_and_a_store(gc: ModuleType) -> None:
    assert gc.guard_is_off() is True


def test_the_guard_is_on_with_the_latch(gc: ModuleType) -> None:
    write_config('{"guard": "required"}')
    assert gc.guard_is_off() is False


def test_the_guard_is_on_with_a_guard_file_in_the_default_store(gc: ModuleType) -> None:
    (home() / "coven-memory" / "systems").mkdir(parents=True)
    (home() / "coven-memory" / "systems" / "guard.json").write_text("{}")
    assert gc.guard_is_off() is False


def test_a_config_that_cannot_be_read_is_not_off(gc: ModuleType) -> None:
    write_config("{not json")
    assert gc.guard_is_off() is False


def new_modules(statement: str) -> set[str]:
    """The modules `statement` imports on top of what an isolated interpreter starts with."""
    code = (
        "import json, sys\n"
        "before = set(sys.modules)\n"
        f"sys.path.insert(0, {str(HOOKS)!r})\n"
        f"{statement}\n"
        "print(json.dumps(sorted(set(sys.modules) - before)))\n"
    )
    done = subprocess.run(
        [sys.executable, "-I", "-c", code], capture_output=True, text=True, check=True
    )
    return set(json.loads(done.stdout))


def test_importing_it_and_reading_the_config_loads_none_of_the_heavy_modules() -> None:
    loaded = new_modules("import guard_config; guard_config.guard_is_off()")
    assert "guard_config" in loaded
    assert not loaded & set(HEAVY), sorted(loaded & set(HEAVY))
