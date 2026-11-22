import json
import os
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from doctor_kit import KEY_NAME, KEY_VALUE, NAMES, Good, Reader, results_by_name
from fakes import fake_opencode

from coven import paths
from coven.opencode import checks_runtime
from coven.opencode.canary import ServerOptions
from coven.opencode.checks import Outcome, exit_code, render, run_doctor
from coven.opencode.profile import Layout, Profile, load_profile
from coven.opencode.runtime import run_env

type Change = Callable[[dict[str, Any]], object]


def edit_config(good: Good, change: Change) -> None:
    path = good.layout.config_dir / "opencode.jsonc"
    data = json.loads(path.read_text())
    change(data)
    path.write_text(json.dumps(data))


def test_a_good_profile_passes_every_check_in_order(good: Good):
    reader = Reader()
    done = run_doctor(good.layout, good.cwd, read=reader)
    assert [name for name, _ in done] == NAMES
    assert [outcome.status for _, outcome in done] == ["ok"] * 9
    assert exit_code(done) == 0
    assert len(reader.calls) == 1


def test_the_server_reads_in_the_run_env_from_the_config_dir(good: Good):
    reader = Reader()
    results_by_name(good, reader)
    binary, env, cwd, opts = reader.calls[0]
    assert binary.name == "opencode"
    assert cwd == good.layout.config_dir
    assert opts == ServerOptions()
    assert opts.start_timeout == 30.0
    assert opts.grace == 5.0
    assert env["OPENCODE_DISABLE_MODELS_FETCH"] == "1"
    assert env["OPENCODE_CONFIG_DIR"] == str(good.layout.config_dir)
    assert env["OPENCODE_DISABLE_PROJECT_CONFIG"] == "1"


def test_the_server_env_leaves_out_other_opencode_settings(
    good: Good, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("OPENCODE_CONFIG_CONTENT", "{}")
    reader = Reader()
    results_by_name(good, reader)
    assert "OPENCODE_CONFIG_CONTENT" not in reader.calls[0][1]


def test_an_old_runtime_fails_runtime_and_blocks_the_server_checks(good: Good):
    fake_opencode(good.bin_dir, "1.19.0")
    reader = Reader()
    found = results_by_name(good, reader)
    assert found["runtime"].status == "fail"
    assert "1.19.0" in found["runtime"].detail
    assert found["runtime"].fix == "point COVEN_OPENCODE_BIN at 2.3.2"
    for name in ("plugin", "skills"):
        assert found[name].status == "fail"
        assert "runtime check failed" in found[name].detail
    assert reader.calls == []


def test_a_missing_profile_runtime_says_to_install(good: Good, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("COVEN_OPENCODE_BIN")
    found = results_by_name(good)
    assert found["runtime"].status == "fail"
    assert found["runtime"].fix == "run coven opencode install"
    assert found["paths"].status == "fail"


def test_a_path_outside_the_profile_fails_paths_naming_it(good: Good):
    fake_opencode(good.bin_dir, "2.3.2", paths={"tmp": "/tmp/opencode"})
    found = results_by_name(good)
    assert found["paths"].status == "fail"
    assert "/tmp/opencode" in found["paths"].detail
    assert found["paths"].fix == "re-run coven opencode setup"
    assert found["runtime"].status == "ok"


def test_an_edited_model_fails_config(good: Good):
    edit_config(good, lambda data: data.update(model="litellm/other"))
    found = results_by_name(good)
    assert found["config"].status == "fail"
    assert "model" in found["config"].detail
    assert found["config"].fix == "re-run coven opencode setup"


@pytest.mark.parametrize(
    "change",
    [
        lambda data: data["agents"]["reviewer"].update(model="litellm/other"),
        lambda data: data["providers"]["litellm"]["settings"].update(baseURL="http://x/v1"),
        lambda data: data["providers"]["litellm"].update(env=["OTHER"]),
        lambda data: data.update(model="litellm/"),
    ],
    ids=["agent-model", "base-url", "key-env", "empty-id"],
)
def test_other_edits_fail_config(good: Good, change: Change):
    edit_config(good, change)
    assert results_by_name(good)["config"].status == "fail"


def test_an_unparsable_config_fails_config(good: Good):
    (good.layout.config_dir / "opencode.jsonc").write_text("{")
    found = results_by_name(good)
    assert found["config"].status == "fail"
    assert found["coven root"].status == "fail"


def test_an_unset_key_warns_by_name_and_exits_zero(good: Good, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv(KEY_NAME)
    done = run_doctor(good.layout, good.cwd, read=Reader())
    found = dict(done)
    assert found["key"].status == "warn"
    assert f"${KEY_NAME}" in found["key"].detail
    assert exit_code(done) == 0


def test_the_key_value_is_in_no_output_line(good: Good):
    lines = render(run_doctor(good.layout, good.cwd, read=Reader()))
    assert any(f"${KEY_NAME}" in line for line in lines)
    assert KEY_VALUE not in "\n".join(lines)


def test_a_coven_root_without_the_guard_hook_fails(good: Good, tmp_path: Path):
    empty = tmp_path / "empty"
    empty.mkdir()

    def point(data: dict[str, Any]) -> None:
        data["plugins"][0]["options"]["covenRoot"] = str(empty)

    edit_config(good, point)
    found = results_by_name(good)
    assert found["coven root"].status == "fail"
    assert "deny_secrets.py" in found["coven root"].detail
    assert found["coven root"].fix == "re-run coven opencode setup"


def test_project_config_above_cwd_warns_naming_it(good: Good, tmp_path: Path):
    parent = tmp_path / "repo"
    (parent / ".opencode").mkdir(parents=True)
    (parent / "opencode.jsonc").write_text("{}")
    deep = parent / "a" / "b"
    deep.mkdir(parents=True)
    done = run_doctor(good.layout, deep, read=Reader())
    found = dict(done)["project config"]
    assert found.status == "warn"
    assert str(parent / ".opencode") in found.detail
    assert str(parent / "opencode.jsonc") in found.detail
    assert "ignored in the coven profile (project config is off)" in found.detail
    assert exit_code(done) == 0


@pytest.mark.parametrize(
    ("entries", "status"), [(["bin"], "ok"), ([], "warn"), (["bin", "opencode.json"], "warn")]
)
def test_an_opencode_folder_holding_only_bin_is_not_project_config(
    good: Good, tmp_path: Path, entries: list[str], status: str
):
    """The 1.x curl installer leaves `.opencode/bin`; that is no config."""
    repo = tmp_path / "repo"
    for name in entries:
        (repo / ".opencode" / name).mkdir(parents=True)
    (repo / ".opencode").mkdir(parents=True, exist_ok=True)
    found = dict(run_doctor(good.layout, repo, read=Reader()))["project config"]
    assert found.status == status


def test_config_is_compared_with_the_config_of_coven_s_real_root(
    good: Good, monkeypatch: pytest.MonkeyPatch
):
    asked: list[Path] = []
    real = checks_runtime.config_data

    def spy(profile: Profile, layout: Layout, coven_root: Path) -> dict[str, object]:
        asked.append(coven_root)
        return real(profile, layout, coven_root)

    monkeypatch.setattr(checks_runtime, "config_data", spy)
    assert dict(run_doctor(good.layout, good.cwd, read=Reader()))["config"].status == "ok"
    assert asked == [paths.root()]


def test_a_missing_superpowers_folder_warns_with_the_fix_and_exits_zero(good: Good):
    shutil.rmtree(good.layout.config_dir / "skills" / "superpowers-writing-plans")
    shutil.rmtree(good.layout.config_dir / "skills" / "superpowers-brainstorming")
    done = run_doctor(good.layout, good.cwd, read=Reader())
    found = dict(done)["superpowers"]
    assert found.status == "warn"
    assert found.detail == "missing superpowers-brainstorming, superpowers-writing-plans"
    assert found.fix == (
        "install the superpowers plugin in Claude Code, then re-run `coven opencode setup`"
    )
    assert exit_code(done) == 0


def test_every_superpowers_folder_present_passes(good: Good):
    found = results_by_name(good)["superpowers"]
    assert found == Outcome("ok", "5 superpowers skills copied")


def test_the_superpowers_check_needs_no_profile(good: Good):
    good.layout.profile_file.unlink()
    assert results_by_name(good)["superpowers"].status == "ok"


def test_a_check_that_raises_fails_alone(good: Good, monkeypatch: pytest.MonkeyPatch):
    def boom(*_args: object) -> None:
        raise RuntimeError("kaboom\nsecond line")

    monkeypatch.setattr("coven.opencode.checks_runtime.read_paths", boom)
    done = dict(run_doctor(good.layout, good.cwd, read=Reader()))
    assert done["paths"] == Outcome("fail", "RuntimeError: kaboom")
    assert [done[name].status for name in NAMES if name != "paths"] == ["ok"] * 8


def test_render_prints_one_line_per_check_with_the_fix_when_not_ok():
    done = [
        ("runtime", Outcome("ok", "2.3.2")),
        ("key", Outcome("warn", "$K is not set", "export it")),
        ("paths", Outcome("fail", "bad", "re-run coven opencode setup")),
        ("project config", Outcome("warn", "found")),
    ]
    assert render(done) == [
        "ok runtime: 2.3.2",
        "warn key: $K is not set (fix: export it)",
        "fail paths: bad (fix: re-run coven opencode setup)",
        "warn project config: found",
    ]
    assert exit_code(done) == 1
    assert exit_code(done[:2]) == 0


def test_run_env_is_what_the_server_gets(good: Good):
    reader = Reader()
    results_by_name(good, reader)
    profile = load_profile(good.layout)
    assert profile is not None
    expected = run_env(profile, good.layout, os.environ, offline=True)
    assert dict(reader.calls[0][1]) == expected
