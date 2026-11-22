import pytest
from doctor_kit import KEY_VALUE, NAMES, Good, Reader, results_by_name

from coven import cli
from coven.opencode.canary import ServerView
from coven.opencode.checks import exit_code, render, run_doctor
from coven.opencode.profile import OpencodeError

SKILL = {"id": "opencode", "path": "skill://opencode/SKILL.md"}
NO_PROFILE = "skipped: no profile — run `coven opencode setup`"


def view(plugins: list[dict[str, object]], skills: list[dict[str, object]] | None = None):
    return Reader(ServerView(plugins, skills if skills is not None else [SKILL]))


def test_a_failed_plugin_shows_its_error_on_one_line(good: Good):
    failed = [{"id": "coven", "state": {"status": "failed", "error": "boom\nat line 3"}}]
    found = results_by_name(good, view(failed))
    assert found["plugin"].status == "fail"
    assert "boom at line 3" in found["plugin"].detail
    assert "\n" not in found["plugin"].detail
    assert found["plugin"].fix == "re-run coven opencode setup"
    assert found["skills"].status == "ok"


def test_a_server_without_the_coven_plugin_fails_plugin(good: Good):
    found = results_by_name(good, view([{"id": "other", "state": {"status": "active"}}]))
    assert found["plugin"].status == "fail"
    assert "coven" in found["plugin"].detail


def test_a_foreign_skill_fails_skills_naming_it(good: Good):
    foreign = {"id": "foreign", "path": str(good.layout.home / ".agents/skills/foreign/SKILL.md")}
    found = results_by_name(good, view([{"id": "coven", "state": {"status": "active"}}], [foreign]))
    assert found["skills"].status == "fail"
    assert "foreign" in found["skills"].detail
    assert str(good.layout.home / ".agents" / "skills") in found["skills"].fix
    assert found["plugin"].status == "ok"


def test_skills_under_the_profile_config_are_coven_s(good: Good):
    own = {"id": "mine", "path": str(good.layout.config_dir / "skills" / "mine" / "SKILL.md")}
    found = results_by_name(
        good, view([{"id": "coven", "state": {"status": "active"}}], [SKILL, own])
    )
    assert found["skills"].status == "ok"


def test_a_skill_path_that_climbs_out_of_the_profile_config_is_foreign(good: Good):
    climbing = {"id": "sneaky", "path": f"{good.layout.config_dir}/skills/../../x/SKILL.md"}
    found = results_by_name(
        good, view([{"id": "coven", "state": {"status": "active"}}], [climbing])
    )
    assert found["skills"].status == "fail"
    assert "sneaky" in found["skills"].detail


def test_a_skill_without_a_path_counts_as_foreign(good: Good):
    found = results_by_name(
        good, view([{"id": "coven", "state": {"status": "active"}}], [{"id": "x"}])
    )
    assert found["skills"].status == "fail"


def test_a_server_that_does_not_start_fails_plugin_and_skills_alone(good: Good):
    reader = Reader(error=OpencodeError("the server did not start within 30 s"))
    found = results_by_name(good, reader)
    for name in ("plugin", "skills"):
        assert found[name].status == "fail"
        assert "did not start" in found[name].detail
    assert len(reader.calls) == 1
    assert [found[name].status for name in NAMES if name not in ("plugin", "skills")] == ["ok"] * 6


def test_a_reader_that_crashes_fails_both_server_checks_with_one_line(good: Good):
    found = results_by_name(good, Reader(error=RuntimeError("kaboom")))
    assert found["plugin"].detail == found["skills"].detail == "RuntimeError: kaboom"


def test_with_no_profile_the_profile_checks_are_skipped_and_doctor_exits_one(good: Good):
    good.layout.profile_file.unlink()
    reader = Reader()
    done = run_doctor(good.layout, good.cwd, read=reader)
    found = dict(done)
    assert [name for name, _ in done] == NAMES
    assert found["config"].status == "fail"
    assert found["config"].fix == "run coven opencode setup"
    for name in ("key", "plugin", "skills"):
        assert found[name].status == "fail"
        assert found[name].detail == NO_PROFILE
    assert found["runtime"].status == "ok"
    assert found["paths"].status == "ok"
    assert found["coven root"].status == "ok"
    assert exit_code(done) == 1
    assert reader.calls == []


def test_a_broken_profile_is_a_config_failure_and_the_others_still_print(good: Good):
    good.layout.profile_file.write_text("{")
    found = results_by_name(good)
    assert found["config"].status == "fail"
    assert "profile.json" in found["config"].detail
    assert found["runtime"].status == "ok"


def test_no_output_line_holds_the_key_value_or_the_server_password(good: Good):
    reader = Reader(error=OpencodeError("the server did not start within 30 s"))
    lines = render(run_doctor(good.layout, good.cwd, read=reader))
    assert len(lines) == 8
    assert KEY_VALUE not in "\n".join(lines)
    assert "OPENCODE_SERVER_PASSWORD" not in "\n".join(lines)


def test_the_doctor_command_prints_a_line_per_check_and_exits_one_on_a_failure(
    good: Good, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
):
    good.layout.profile_file.unlink()
    monkeypatch.chdir(good.cwd)
    assert cli.main(["opencode", "doctor"]) == 1
    lines = capsys.readouterr().out.splitlines()
    assert [line.split(":")[0].split(" ", 1)[1] for line in lines] == NAMES
    assert lines[0].startswith("ok runtime: ")
    assert f"fail key: {NO_PROFILE}" in lines
