from pathlib import Path

import pytest

from coven import paths


def test_claude_dir_respects_claude_config_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "elsewhere"))
    assert paths.claude_dir() == tmp_path / "elsewhere"


def test_claude_dir_defaults_to_dot_claude_in_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.delenv("CLAUDE_CONFIG_DIR")
    assert paths.claude_dir() == Path(tmp_path / "home" / ".claude")


def test_config_dir_is_under_dot_config(tmp_path: Path):
    assert paths.config_dir() == tmp_path / "home" / ".config" / "coven"


def test_cache_dirs_respect_xdg_cache_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    assert paths.cache_dir() == tmp_path / "cache" / "coven"
    assert paths.logs_dir() == tmp_path / "cache" / "coven" / "logs"
    assert paths.briefs_dir() == tmp_path / "cache" / "coven" / "briefs"


def test_cache_dir_defaults_to_dot_cache_in_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.delenv("XDG_CACHE_HOME")
    assert paths.cache_dir() == tmp_path / "home" / ".cache" / "coven"


def test_installed_plugins_file_is_under_the_claude_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.delenv("CLAUDE_CODE_PLUGIN_CACHE_DIR", raising=False)
    expected = tmp_path / "home" / ".claude" / "plugins" / "installed_plugins.json"
    assert paths.installed_plugins_file() == expected


def test_installed_plugins_file_moves_with_the_plugin_cache_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.setenv("CLAUDE_CODE_PLUGIN_CACHE_DIR", str(tmp_path / "plugins-root"))
    assert paths.installed_plugins_file() == tmp_path / "plugins-root" / "installed_plugins.json"


def test_standard_dir_is_the_repos_standard_folder():
    assert (paths.standard_dir() / "check.py").is_file()
