import os
from pathlib import Path

import pytest
from fakes import fake_opencode

from coven import cli
from coven.opencode import runtime
from coven.opencode.profile import Layout, Profile, layout, make_profile, save_profile

LITELLM = make_profile("litellm", "http://127.0.0.1:4000/v1", "GATEWAY_KEY", ["lead=big"])
OPENCODE = make_profile("opencode", None, None, ["lead=big"])


class Exec:
    """A stand-in for os.execve that records its call."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str], dict[str, str]]] = []

    def __call__(self, path: str, argv: list[str], env: dict[str, str]) -> None:
        self.calls.append((path, argv, env))


@pytest.fixture
def home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Layout:
    folder = tmp_path / "profile"
    monkeypatch.setenv("COVEN_OPENCODE_HOME", str(folder))
    return layout()


@pytest.fixture
def binary(home: Layout, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    path = fake_opencode(tmp_path / "bin", "2.3.2")
    monkeypatch.setenv("COVEN_OPENCODE_BIN", str(path))
    return path


def expected_env(home: Layout, **extra: str) -> dict[str, str]:
    return {
        "OPENCODE_CONFIG_DIR": str(home.config_dir),
        "XDG_CONFIG_HOME": str(home.config_home),
        "XDG_DATA_HOME": str(home.data),
        "XDG_CACHE_HOME": str(home.cache),
        "XDG_STATE_HOME": str(home.state),
        "OPENCODE_TEST_HOME": str(home.home),
        "TMPDIR": str(home.tmp),
        "OPENCODE_DISABLE_AUTOUPDATE": "1",
        "OPENCODE_DISABLE_CLAUDE_CODE": "1",
        "OPENCODE_DISABLE_PROJECT_CONFIG": "1",
        "HOME": "/h",
        "PATH": "/bin",
        **extra,
    }


BASE = {"HOME": "/h", "PATH": "/bin"}


def test_run_env_litellm_has_exactly_the_listed_keys(home: Layout):
    env = runtime.run_env(LITELLM, home, BASE, offline=False)
    assert env == expected_env(home, OPENCODE_DISABLE_FREE_MODELS="1")


def test_run_env_offline_adds_only_the_models_fetch_switch(home: Layout):
    online = runtime.run_env(LITELLM, home, BASE, offline=False)
    offline = runtime.run_env(LITELLM, home, BASE, offline=True)
    assert offline == {**online, "OPENCODE_DISABLE_MODELS_FETCH": "1"}


def test_run_env_opencode_preset_keeps_free_models(home: Layout):
    env = runtime.run_env(OPENCODE, home, BASE, offline=False)
    assert env == expected_env(home)


def test_run_env_drops_inherited_opencode_settings(home: Layout):
    base = {
        **BASE,
        "OPENCODE_CONFIG": "/x",
        "OPENCODE_CONFIG_CONTENT": "{}",
        "OPENCODE_DB": "/x.db",
        "OPENCODE_CONFIG_DIR": "/old",
        "OPENCODE_DISABLE_PROJECT_CONFIG": "0",
    }
    env = runtime.run_env(LITELLM, home, base, offline=False)
    assert env == runtime.run_env(LITELLM, home, BASE, offline=False)
    assert "/x" not in env.values()
    assert "/old" not in env.values()


def test_run_env_passes_the_key_and_server_settings_through(home: Layout):
    base = {
        **BASE,
        "OPENCODE_SERVER_PASSWORD": "pw",
        "OPENCODE_SERVER_USERNAME": "user",
        "OPENCODE_DISABLE_MODELS_FETCH": "1",
        "GATEWAY_KEY": "sk-test-value",
        "OPENCODE_API_KEY": "sk-other-value",
    }
    litellm = runtime.run_env(LITELLM, home, base, offline=False)
    assert litellm["OPENCODE_SERVER_PASSWORD"] == "pw"
    assert litellm["OPENCODE_SERVER_USERNAME"] == "user"
    assert litellm["OPENCODE_DISABLE_MODELS_FETCH"] == "1"
    assert litellm["GATEWAY_KEY"] == "sk-test-value"
    assert "OPENCODE_API_KEY" not in litellm
    assert runtime.run_env(OPENCODE, home, base, offline=False)["OPENCODE_API_KEY"] == (
        "sk-other-value"
    )


def test_binary_path_defaults_to_the_pinned_runtime(home: Layout):
    assert runtime.binary_path(home) == home.runtime_binary("2.3.2")


def test_binary_path_follows_the_override(home: Layout, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("COVEN_OPENCODE_BIN", "/somewhere/opencode")
    assert runtime.binary_path(home) == Path("/somewhere/opencode")


def test_binary_version_reads_the_first_line(tmp_path: Path):
    assert runtime.binary_version(fake_opencode(tmp_path, "1.19.0"), os.environ) == "1.19.0"


def test_binary_version_is_none_when_the_binary_cannot_run(tmp_path: Path):
    assert runtime.binary_version(tmp_path / "missing", os.environ) is None
    broken = tmp_path / "broken"
    broken.write_text("#!/bin/sh\nexit 3\n")
    broken.chmod(0o755)
    assert runtime.binary_version(broken, os.environ) is None


def saved(home: Layout, profile: Profile = LITELLM) -> None:
    save_profile(home, profile)


def test_run_execs_the_binary_with_the_run_env(
    home: Layout, binary: Path, monkeypatch: pytest.MonkeyPatch
):
    saved(home)
    monkeypatch.setenv("GATEWAY_KEY", "sk-test-value")
    fake = Exec()
    assert runtime.run(["run", "--format", "json", "hi"], exec_fn=fake) == 0
    [(path, argv, env)] = fake.calls
    assert (path, argv) == (str(binary), [str(binary), "run", "--format", "json", "hi"])
    assert env == runtime.run_env(LITELLM, home, dict(os.environ), offline=False)


def test_cli_run_drops_the_leading_separator(
    home: Layout, binary: Path, monkeypatch: pytest.MonkeyPatch
):
    saved(home)
    monkeypatch.setenv("GATEWAY_KEY", "sk-test-value")
    fake = Exec()
    monkeypatch.setattr(os, "execve", fake)
    assert cli.main(["opencode", "run", "--", "run", "--format", "json", "hi"]) == 0
    [(_, argv, env)] = fake.calls
    assert argv == [str(binary), "run", "--format", "json", "hi"]
    assert env == runtime.run_env(LITELLM, home, dict(os.environ), offline=False)


def test_run_without_a_binary_is_refused(
    home: Layout, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    saved(home)
    fake = Exec()
    monkeypatch.setattr(os, "execve", fake)
    assert cli.main(["opencode", "run"]) == 1
    assert "the OpenCode runtime is missing; run coven opencode install" in capsys.readouterr().err
    assert fake.calls == []


def test_run_refuses_a_binary_that_is_not_the_pinned_version(
    home: Layout,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    saved(home)
    old = fake_opencode(tmp_path / "old", "1.19.0")
    monkeypatch.setenv("COVEN_OPENCODE_BIN", str(old))
    fake = Exec()
    with pytest.raises(runtime.OpencodeError) as caught:
        runtime.run([], exec_fn=fake)
    assert str(caught.value) == (
        f"{old} reports 1.19.0; coven needs 2.3.2 "
        "(run coven opencode install, or point COVEN_OPENCODE_BIN at 2.3.2)"
    )
    assert fake.calls == []


def test_run_without_a_profile_is_refused(home: Layout, binary: Path):
    fake = Exec()
    with pytest.raises(runtime.OpencodeError, match="no profile yet; run coven opencode setup"):
        runtime.run([], exec_fn=fake)
    assert fake.calls == []


def test_run_warns_about_an_unset_key_by_name_and_goes_on(
    home: Layout, binary: Path, capsys: pytest.CaptureFixture[str]
):
    saved(home)
    fake = Exec()
    assert runtime.run([], exec_fn=fake) == 0
    assert "$GATEWAY_KEY is not set; the run goes on" in capsys.readouterr().err
    assert len(fake.calls) == 1


def test_run_never_prints_the_key_value(
    home: Layout, binary: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    saved(home)
    monkeypatch.setenv("GATEWAY_KEY", "sk-test-value")
    runtime.run([], exec_fn=Exec())
    shown = capsys.readouterr()
    assert "sk-test-value" not in shown.out + shown.err
