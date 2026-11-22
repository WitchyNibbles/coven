import json
from pathlib import Path
from typing import Any

import pytest
from fakes import NPM_PATH_VARS, fake_npm, fake_opencode, npm_env_log, npm_log, npm_paths_log

from coven import cli
from coven.opencode import install as installer
from coven.opencode.profile import Layout, OpencodeError, layout, make_profile, save_profile


def spec(version: str) -> str:
    return "@opencode/cli@" + version


@pytest.fixture
def home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Layout:
    monkeypatch.setenv("COVEN_OPENCODE_HOME", str(tmp_path / "profile"))
    return layout()


@pytest.fixture
def npm_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    folder = tmp_path / "fakebin"
    fake_npm(folder)
    monkeypatch.setenv("PATH", str(folder))
    return folder


def log_lines(folder: Path) -> list[str]:
    return npm_log(folder).read_text().splitlines()


def test_install_runs_npm_and_returns_the_binary(
    home: Layout, npm_dir: Path, capsys: pytest.CaptureFixture[str]
):
    binary = installer.install(home, "2.3.2")
    assert binary == home.runtime_binary("2.3.2")
    assert log_lines(npm_dir) == [
        f"install --prefix {home.runtime_dir('2.3.2')} --save-exact {spec('2.3.2')}"
    ]
    assert capsys.readouterr().err == ""


def test_install_is_idempotent(home: Layout, npm_dir: Path, capsys: pytest.CaptureFixture[str]):
    installer.install(home, "2.3.2")
    capsys.readouterr()
    assert installer.install(home, "2.3.2") == home.runtime_binary("2.3.2")
    assert len(log_lines(npm_dir)) == 1
    assert "already installed" in capsys.readouterr().out


def test_install_reports_npm_failure_with_the_last_stderr_lines(
    home: Layout, npm_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("FAKE_NPM_EXIT", "3")
    with pytest.raises(OpencodeError) as caught:
        installer.install(home, "2.3.2")
    message = str(caught.value)
    assert message.startswith("npm install failed (exit 3): ")
    for number in (1, 2, 3):
        assert f"npm ERR! line {number}" in message


def test_install_refuses_a_different_version_from_the_package(
    home: Layout, npm_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("FAKE_OPENCODE_VERSION", "2.3.1")
    with pytest.raises(OpencodeError, match="installed opencode reports 2.3.1, expected 2.3.2"):
        installer.install(home, "2.3.2")


def test_install_needs_npm_on_the_path(
    home: Layout, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(OpencodeError, match="npm not found on PATH; install Node.js with npm"):
        installer.install(home, "2.3.2")


@pytest.mark.parametrize("version", ["2.x", "2.3", "v2.3.2", "2.3.2 ", "", "2.3.2-beta"])
def test_install_refuses_a_malformed_version(home: Layout, npm_dir: Path, version: str):
    with pytest.raises(OpencodeError, match="--version"):
        installer.install(home, version)
    assert not npm_log(npm_dir).exists()


def test_install_of_another_version_goes_to_its_own_folder_and_warns(
    home: Layout, npm_dir: Path, capsys: pytest.CaptureFixture[str]
):
    binary = installer.install(home, "2.4.0")
    assert binary == home.runtime_binary("2.4.0")
    assert home.runtime_binary("2.4.0").is_file()
    assert "run and doctor use 2.3.2 only" in capsys.readouterr().err
    assert log_lines(npm_dir) == [
        f"install --prefix {home.runtime_dir('2.4.0')} --save-exact {spec('2.4.0')}"
    ]


def test_install_skips_npm_for_an_existing_matching_binary(home: Layout, npm_dir: Path):
    target = home.runtime_binary("2.3.2")
    fake_opencode(target.parent, "2.3.2")
    installer.install(home, "2.3.2")
    assert not npm_log(npm_dir).exists()


def test_cli_install_defaults_to_the_pinned_version(
    home: Layout, npm_dir: Path, capsys: pytest.CaptureFixture[str]
):
    assert cli.main(["opencode", "install"]) == 0
    assert str(home.runtime_binary("2.3.2")) in capsys.readouterr().out
    assert cli.main(["opencode", "install", "--version", "2.x"]) == 1


def test_npm_does_not_inherit_the_key_env_value(
    home: Layout, npm_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    save_profile(
        home, make_profile("litellm", "http://127.0.0.1:4000/v1", "GATEWAY_KEY", ["lead=x"])
    )
    for name in ("GATEWAY_KEY", "OPENCODE_API_KEY", "HTTPS_PROXY"):
        monkeypatch.setenv(name, "sk-test-value")
    monkeypatch.setenv("FAKE_NPM_WATCH", "GATEWAY_KEY,OPENCODE_API_KEY,HTTPS_PROXY,PATH")
    installer.install(home, "2.3.2")
    assert npm_env_log(npm_dir).read_text().splitlines() == [
        "GATEWAY_KEY unset",
        "OPENCODE_API_KEY unset",
        "HTTPS_PROXY set",
        "PATH set",
    ]


def test_npm_without_a_profile_still_loses_the_opencode_api_key(
    home: Layout, npm_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("OPENCODE_API_KEY", "sk-test-value")
    monkeypatch.setenv("FAKE_NPM_WATCH", "OPENCODE_API_KEY")
    installer.install(home, "2.3.2")
    assert npm_env_log(npm_dir).read_text().splitlines() == ["OPENCODE_API_KEY unset"]


def test_install_does_not_need_a_valid_profile(home: Layout, npm_dir: Path):
    home.root.mkdir(parents=True)
    home.profile_file.write_text("not json")
    assert installer.install(home, "2.3.2") == home.runtime_binary("2.3.2")


def npm_saw(folder: Path) -> dict[str, Any]:
    seen: dict[str, Any] = json.loads(npm_paths_log(folder).read_text())
    return seen


def test_npm_runs_with_the_profile_env_and_a_tmp_folder(
    home: Layout, npm_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    for name in NPM_PATH_VARS:
        monkeypatch.setenv(name, "/not/the/profile")
    monkeypatch.setenv("OPENCODE_CONFIG_CONTENT", "{}")
    installer.install(home, "2.3.2")
    seen = npm_saw(npm_dir)
    assert seen["values"] == {
        "XDG_DATA_HOME": str(home.data),
        "XDG_CONFIG_HOME": str(home.config_home),
        "XDG_STATE_HOME": str(home.state),
        "XDG_CACHE_HOME": str(home.cache),
        "TMPDIR": str(home.tmp),
        "OPENCODE_CONFIG_DIR": str(home.config_dir),
    }
    assert seen["tmp_exists"] is True
    assert "OPENCODE_CONFIG_CONTENT" not in seen["opencode_names"]


def test_npm_env_keeps_home_and_path_for_the_npm_config_and_cache(
    home: Layout, npm_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("FAKE_NPM_WATCH", "HOME,PATH")
    installer.install(home, "2.3.2")
    assert npm_env_log(npm_dir).read_text().splitlines() == ["HOME set", "PATH set"]
