import json
from pathlib import Path

import pytest

from coven import paths
from coven.config import Config, load_config, save_config
from coven.errors import CovenError


def config_file() -> Path:
    return paths.config_dir() / "config.json"


def test_defaults_when_the_file_is_missing(tmp_path: Path):
    assert load_config() == Config(
        memory_path=tmp_path / "home" / "coven-memory", memory_remote=None, pm_cap=2
    )


def test_save_then_load_round_trips(tmp_path: Path):
    config = Config(
        memory_path=tmp_path / "mem", memory_remote="git@example.com:me/mem.git", pm_cap=4
    )
    save_config(config)
    assert load_config() == config


def test_save_writes_json_and_leaves_no_temp_file(tmp_path: Path):
    save_config(Config(memory_path=tmp_path / "mem"))
    assert json.loads(config_file().read_text()) == {
        "memory_path": str(tmp_path / "mem"),
        "memory_remote": None,
        "pm_cap": 2,
    }
    assert [p.name for p in config_file().parent.iterdir()] == ["config.json"]


def test_partial_file_keeps_the_other_defaults(tmp_path: Path):
    config_file().parent.mkdir(parents=True)
    config_file().write_text('{"pm_cap": 3}')
    assert load_config() == Config(memory_path=tmp_path / "home" / "coven-memory", pm_cap=3)


def test_memory_path_expands_a_tilde(tmp_path: Path):
    config_file().parent.mkdir(parents=True)
    config_file().write_text('{"memory_path": "~/notes"}')
    assert load_config().memory_path == tmp_path / "home" / "notes"


def test_config_bad_json_names_file():
    config_file().parent.mkdir(parents=True)
    config_file().write_text("{not json")
    with pytest.raises(CovenError, match="config.json") as error:
        load_config()
    assert str(config_file()) in str(error.value)


def test_config_unknown_key_rejected():
    config_file().parent.mkdir(parents=True)
    config_file().write_text('{"pm_cap": 2, "colour": "red"}')
    with pytest.raises(CovenError, match="colour") as error:
        load_config()
    assert str(config_file()) in str(error.value)


@pytest.mark.parametrize(
    "text",
    [
        "[]",
        '{"pm_cap": "2"}',
        '{"pm_cap": true}',
        '{"pm_cap": 0}',
        '{"memory_path": 3}',
        '{"memory_remote": 3}',
    ],
)
def test_config_wrong_types_name_the_file(text: str):
    config_file().parent.mkdir(parents=True)
    config_file().write_text(text)
    with pytest.raises(CovenError) as error:
        load_config()
    assert str(config_file()) in str(error.value)
