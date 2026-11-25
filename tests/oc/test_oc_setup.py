import json
from pathlib import Path

import pytest
from fakes import install_superpowers
from support import REPO

from coven import cli, paths
from coven.opencode import generate, superpowers

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


LIMIT_FLAGS = ["--limit", "big=100000:8000", "--limit", "small=50000"]
NO_LIMIT_WARNING = (
    "warning: no limit for big, small: OpenCode assumes 200000 tokens of context "
    "(add --limit ID=CONTEXT[:OUTPUT] to the setup flags)"
)


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


def test_setup_writes_config_profile_and_plugin(
    home: Path, capsys: pytest.CaptureFixture[str], claude_plugins: Path
):
    install_superpowers(claude_plugins)
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
        "project-manager: big",
        NO_LIMIT_WARNING,
        "superpowers: Claude Code plugin",
        "next: coven opencode doctor",
    ]


def test_setup_names_the_missing_superpowers_on_one_warning_line(
    home: Path, capsys: pytest.CaptureFixture[str], claude_plugins: Path
):
    install_superpowers(claude_plugins, superpowers.SKILLS[:2])
    assert setup(*LITELLM_FLAGS) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[-4:] == [
        NO_LIMIT_WARNING,
        "superpowers: Claude Code plugin",
        "warning: superpowers skills not found: test-driven-development, systematic-debugging, "
        "verification-before-completion, using-git-worktrees, finishing-a-development-branch, "
        "executing-plans (run `coven opencode install`, which fetches superpowers v7.0.2, or "
        "install the superpowers plugin in Claude Code; then re-run coven opencode setup)",
        "next: coven opencode doctor",
    ]
    assert sum("warning: superpowers" in line for line in lines) == 1


def test_setup_with_no_superpowers_install_warns_and_still_succeeds(
    home: Path, capsys: pytest.CaptureFixture[str]
):
    assert setup(*LITELLM_FLAGS) == 0
    out = capsys.readouterr().out
    assert "warning: superpowers skills not found: brainstorming, writing-plans" in out
    assert "superpowers: " not in out.replace("warning: superpowers", "")
    assert (home / "config" / "opencode" / "AGENTS.md").is_file()


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
    [
        ["--model", "lead=x"],
        ["--base-url", "http://h/v1"],
        ["--key-env", "GATEWAY_KEY"],
        ["--limit", "x=1000"],
    ],
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
        (["--preset", "opencode", "--model", "lead=x", "--limit", "x=1000"], "--limit needs"),
        ([*LITELLM_FLAGS, "--limit", "nope=1000"], "not one of the profile's models"),
        ([*LITELLM_FLAGS, "--limit", "big=0"], "positive whole number"),
        ([*LITELLM_FLAGS, "--limit", "big=1000:1000"], "output must be smaller than context"),
        ([*LITELLM_FLAGS, "--limit", "big=1000", "--limit", "big=2000"], "given twice"),
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


def test_limits_reach_the_config_the_profile_and_the_summary(
    home: Path, capsys: pytest.CaptureFixture[str]
):
    assert setup(*LITELLM_FLAGS, *LIMIT_FLAGS) == 0
    config = json.loads((home / "config" / "opencode" / "opencode.jsonc").read_text())
    assert config["providers"]["litellm"]["models"] == {
        "big": {"name": "big", "limit": {"context": 100000, "output": 8000}},
        "small": {"name": "small", "limit": {"context": 50000}},
    }
    assert json.loads((home / "profile.json").read_text())["limits"] == {
        "big": {"context": 100000, "output": 8000},
        "small": {"context": 50000},
    }
    lines = capsys.readouterr().out.splitlines()
    first = lines.index("project-manager: big") + 1
    assert lines[first : first + 2] == [
        "limit big: 100000 context, 8000 output",
        "limit small: 50000 context",
    ]
    assert not any(line.startswith("warning: no limit") for line in lines)


def test_a_partial_limit_warns_only_for_the_model_without_one(
    home: Path, capsys: pytest.CaptureFixture[str]
):
    assert setup(*LITELLM_FLAGS, "--limit", "big=100000") == 0
    lines = capsys.readouterr().out.splitlines()
    assert "limit big: 100000 context" in lines
    assert [line for line in lines if line.startswith("warning: no limit")] == [
        "warning: no limit for small: OpenCode assumes 200000 tokens of context "
        "(add --limit ID=CONTEXT[:OUTPUT] to the setup flags)"
    ]


def test_rerun_without_flags_keeps_the_limits(home: Path, capsys: pytest.CaptureFixture[str]):
    assert setup(*LITELLM_FLAGS, *LIMIT_FLAGS) == 0
    first = snapshot(home)
    capsys.readouterr()
    assert setup() == 0
    assert snapshot(home) == first
    assert "limit big: 100000 context, 8000 output" in capsys.readouterr().out


def test_setup_with_flags_replaces_the_stored_limits(home: Path):
    assert setup(*LITELLM_FLAGS, *LIMIT_FLAGS) == 0
    assert setup(*LITELLM_FLAGS, "--limit", "small=4096") == 0
    assert json.loads((home / "profile.json").read_text())["limits"] == {"small": {"context": 4096}}


def test_the_opencode_preset_prints_no_limit_lines_or_warning(
    home: Path, capsys: pytest.CaptureFixture[str]
):
    assert setup("--preset", "opencode", "--model", "lead=big") == 0
    assert not [
        line
        for line in capsys.readouterr().out.splitlines()
        if line.startswith(("limit", "warning: no limit"))
    ]


def test_setup_copies_the_clone_and_names_it_even_with_a_plugin_installed(
    home: Path, capsys: pytest.CaptureFixture[str], claude_plugins: Path
):
    install_superpowers(claude_plugins)
    skills = home / "superpowers" / superpowers.PINNED_TAG / "skills"
    for name in superpowers.SKILLS:
        (skills / name).mkdir(parents=True)
        (skills / name / "SKILL.md").write_text(
            "---\nname: x\n---\nclone: superpowers:brainstorming\n"
        )
    assert setup(*LITELLM_FLAGS) == 0
    copied = home / "config" / "opencode" / "skills" / "superpowers-writing-plans" / "SKILL.md"
    assert copied.read_text() == "---\nname: x\n---\nclone: superpowers-brainstorming\n"
    out = capsys.readouterr().out
    assert "superpowers: v7.0.2 clone" in out.splitlines()
    assert "warning: superpowers" not in out
