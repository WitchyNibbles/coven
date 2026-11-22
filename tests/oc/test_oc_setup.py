from pathlib import Path

import pytest
from support import REPO

from coven import cli, paths
from coven.opencode import generate

LITELLM_FLAGS = [
    "--preset",
    "litellm",
    "--base-url",
    "http://127.0.0.1:4000/v1",
    "--key-env",
    "GATEWAY_KEY",
    "--model",
    "lead=big",
    "--model",
    "explorer=small",
    "--model",
    "title=small",
]


@pytest.fixture
def home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    folder = tmp_path / "profile"
    monkeypatch.setenv("COVEN_OPENCODE_HOME", str(folder))
    return folder


def setup(*flags: str) -> int:
    return cli.main(["opencode", "setup", *flags])


def snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()
    }


def test_setup_writes_config_profile_and_plugin(home: Path, capsys: pytest.CaptureFixture[str]):
    assert setup(*LITELLM_FLAGS) == 0
    config = home / "config" / "opencode" / "opencode.jsonc"
    assert '"model": "litellm/big"' in config.read_text()
    assert str(paths.root()) in config.read_text()
    assert (home / "profile.json").is_file()
    source = REPO / "opencode" / "src"
    expected = {str(p.relative_to(source)): p.read_bytes() for p in source.rglob("*.ts")}
    plugin = home / "plugin" / "coven"
    assert snapshot(plugin) == expected
    assert "index.ts" in expected
    assert capsys.readouterr().out.splitlines() == [
        f"profile: {home}",
        "preset: litellm (http://127.0.0.1:4000/v1, key from $GATEWAY_KEY)",
        "lead: big",
        "implementer: big",
        "reviewer: big",
        "explorer: small",
        "final-reviewer: big",
        "title: small",
        "next: coven opencode doctor",
    ]


def test_opencode_preset_output(home: Path, capsys: pytest.CaptureFixture[str]):
    assert setup("--preset", "opencode", "--model", "lead=big") == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[1] == "preset: opencode (key from $OPENCODE_API_KEY or a stored login)"
    assert "providers" not in (home / "config" / "opencode" / "opencode.jsonc").read_text()


def test_rerun_without_flags_regenerates_the_same_bytes(home: Path):
    assert setup(*LITELLM_FLAGS) == 0
    first = snapshot(home)
    (home / "plugin" / "coven" / "old.ts").write_text("stale")
    assert setup() == 0
    assert snapshot(home) == first
    assert not (home / "plugin" / "coven" / "old.ts").exists()


def test_no_profile_and_no_flags_fails(home: Path, capsys: pytest.CaptureFixture[str]):
    assert setup() == 1
    assert (
        capsys.readouterr().err.strip()
        == "coven: no profile yet; run coven opencode setup --preset ..."
    )
    assert not home.exists()


@pytest.mark.parametrize(
    "flags",
    [["--model", "lead=x"], ["--base-url", "http://h/v1"], ["--key-env", "GATEWAY_KEY"]],
)
def test_flags_without_a_preset_fail(
    home: Path, capsys: pytest.CaptureFixture[str], flags: list[str]
):
    assert setup(*flags) == 1
    assert (
        "coven: pass --preset with the other flags, or no flags to regenerate"
        in capsys.readouterr().err
    )


@pytest.mark.parametrize(
    ("flags", "message"),
    [
        (["--preset", "litellm", "--key-env", "K", "--model", "lead=x"], "needs --base-url"),
        (["--preset", "other", "--model", "lead=x"], "unknown preset"),
        (["--preset", "opencode"], "--model lead=ID is required"),
        (["--preset", "opencode", "--model", "boss=x"], "unknown role"),
        (["--preset", "opencode", "--key-env", "K", "--model", "lead=x"], "takes no --key-env"),
    ],
)
def test_input_errors_exit_one(
    home: Path, capsys: pytest.CaptureFixture[str], flags: list[str], message: str
):
    assert setup(*flags) == 1
    err = capsys.readouterr().err
    assert err.startswith("coven: ")
    assert message in err
    assert not home.exists()


def test_a_missing_plugin_source_keeps_the_old_state(
    monkeypatch: pytest.MonkeyPatch, home: Path, capsys: pytest.CaptureFixture[str]
):
    assert setup(*LITELLM_FLAGS) == 0
    capsys.readouterr()
    before = snapshot(home)
    missing = home.parent / "gone"
    monkeypatch.setattr(generate, "plugin_source", lambda: missing)
    assert setup("--preset", "opencode", "--model", "lead=other") == 1
    assert str(missing) in capsys.readouterr().err
    assert snapshot(home) == before
    assert not [p for p in home.iterdir() if p.name.startswith(".tmp-")]


def test_the_key_value_is_nowhere(
    monkeypatch: pytest.MonkeyPatch, home: Path, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.setenv("GATEWAY_KEY", "sk-test-value")
    assert setup(*LITELLM_FLAGS) == 0
    seen = capsys.readouterr()
    assert "sk-test-value" not in seen.out + seen.err
    assert not any(b"sk-test-value" in data for data in snapshot(home).values())


def test_setup_refuses_a_key_env_that_is_an_opencode_setting(
    home: Path, capsys: pytest.CaptureFixture[str]
):
    flags = [flag if flag != "GATEWAY_KEY" else "OPENCODE_CONFIG_CONTENT" for flag in LITELLM_FLAGS]
    assert setup(*flags) == 1
    error = capsys.readouterr().err
    assert "OPENCODE_API_KEY" in error
    assert "CONFIG_CONTENT" not in error
    assert not home.exists()
