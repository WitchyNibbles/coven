import os
import stat
from pathlib import Path

import pytest
from doctor_kit import GOOD_VIEW, KEY_VALUE, NAMES, Good, Reader, results_by_name
from serve_kit import Procs, healthy_routes, install_fake, start

from coven import cli
from coven.opencode import checks_server, serve
from coven.opencode.canary import ServerView
from coven.opencode.checks import exit_code, render, run_doctor
from coven.opencode.checks_server import REQUIRED_OPERATIONS
from coven.opencode.profile import OpencodeError

SKILL = {"id": "opencode", "path": "skill://opencode/SKILL.md"}
SERVER_READS = ("plugin", "skills", "operations")
NO_PROFILE = "skipped: no profile — run `coven opencode setup`"


def view(plugins: list[dict[str, object]], skills: list[dict[str, object]] | None = None):
    shown = skills if skills is not None else [SKILL]
    return Reader(ServerView(plugins, shown, frozenset(REQUIRED_OPERATIONS)))


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


def test_a_server_that_does_not_start_fails_every_server_read_alone(good: Good):
    reader = Reader(error=OpencodeError("the server did not start within 30 s"))
    found = results_by_name(good, reader)
    for name in SERVER_READS:
        assert found[name].status == "fail"
        assert "did not start" in found[name].detail
    assert len(reader.calls) == 1
    assert [found[name].status for name in NAMES if name not in SERVER_READS] == ["ok"] * (
        len(NAMES) - len(SERVER_READS)
    )


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
    for name in ("key", "plugin", "skills", "operations"):
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
    assert len(lines) == len(NAMES)
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


@pytest.fixture
def running(
    good: Good,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    procs: Procs,
    interpreter_is_the_server: None,
) -> serve.ServerInfo:
    """A coven server (the fake) that runs, healthy, with every operation in its OpenAPI."""
    install_fake(tmp_path, monkeypatch, routes=healthy_routes(REQUIRED_OPERATIONS))
    return start(good, procs)


def test_the_required_operations_are_the_2_3_2_ids() -> None:
    assert REQUIRED_OPERATIONS == (
        "session.create",
        "session.list",
        "session.get",
        "session.prompt",
        "session.interrupt",
        "session.update",
        "session.permission.list",
        "session.permission.reply",
        "event.subscribe",
        "session.active",
        "session.context",
        "experimental.session.wait",
        "location.reload",
        "plugin.list",
        "skill.list",
    )


def test_doctor_uses_the_running_server_and_starts_no_canary(
    good: Good, running: serve.ServerInfo
) -> None:
    reader = Reader()

    found = results_by_name(good, reader)

    assert reader.calls == []
    assert found["server"].status == "ok"
    assert f"port {running.port}" in found["server"].detail
    assert found["plugin"].status == "ok"
    assert found["skills"].status == "ok"
    assert found["operations"].status == "ok"


def test_doctor_reads_the_plugin_of_the_running_server_not_the_reader_s(
    good: Good,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    procs: Procs,
    interpreter_is_the_server: None,
) -> None:
    other = {"data": [{"id": "other", "state": {"status": "active"}}]}
    routes = healthy_routes(REQUIRED_OPERATIONS) | {"/api/plugin": other}
    install_fake(tmp_path, monkeypatch, routes=routes)
    start(good, procs)

    found = results_by_name(good, Reader())

    assert found["plugin"].status == "fail"
    assert "other" in found["plugin"].detail


def test_doctor_starts_the_canary_when_no_server_runs(good: Good) -> None:
    reader = Reader()

    found = results_by_name(good, reader)

    assert len(reader.calls) == 1
    assert found["server"].status == "ok"
    assert "no server running" in found["server"].detail
    assert found["operations"].status == "ok"
    assert str(len(REQUIRED_OPERATIONS)) in found["operations"].detail


def test_doctor_fails_operations_when_the_canary_s_openapi_lacks_one(good: Good) -> None:
    kept = frozenset(REQUIRED_OPERATIONS) - {"session.interrupt"}
    reader = Reader(ServerView(GOOD_VIEW.plugins, GOOD_VIEW.skills, kept))

    found = results_by_name(good, reader)["operations"]

    assert len(reader.calls) == 1
    assert found.status == "fail"
    assert "session.interrupt" in found.detail
    assert "session.create" not in found.detail


def test_doctor_fails_server_when_the_running_one_does_not_answer(
    good: Good, interpreter_is_the_server: None
) -> None:
    files = serve.files(good.layout)
    files.pid.parent.mkdir(parents=True)
    files.pid.write_text(f"{os.getpid()}\n")
    files.port.write_text("1\n")
    files.password.write_text("pw\n")
    files.password.chmod(0o600)

    found = results_by_name(good, Reader())

    assert found["server"].status == "fail"
    assert "port 1" in found["server"].detail
    assert "serve stop" in found["server"].fix


def make_password(good: Good, mode: int) -> Path:
    path = serve.files(good.layout).password
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("pw\n")
    path.chmod(mode)
    return path


def test_doctor_server_password_mode(good: Good) -> None:
    path = make_password(good, 0o600)
    assert results_by_name(good)["password"].status == "ok"

    path.chmod(0o644)
    loose = results_by_name(good)["password"]

    assert loose.status == "fail"
    assert "0644" in loose.detail
    assert "0600" in loose.detail
    assert str(path) in loose.fix
    assert stat.S_IMODE(path.stat().st_mode) == 0o644


def test_doctor_password_is_ok_before_the_first_start(good: Good) -> None:
    found = results_by_name(good)["password"]

    assert found.status == "ok"
    assert "no password file yet" in found.detail


def test_doctor_openapi_has_required_operations(
    good: Good,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    procs: Procs,
    interpreter_is_the_server: None,
) -> None:
    kept = tuple(op for op in REQUIRED_OPERATIONS if op != "session.interrupt")
    install_fake(tmp_path, monkeypatch, routes=healthy_routes(kept))
    start(good, procs)

    found = results_by_name(good, Reader())["operations"]

    assert found.status == "fail"
    assert "session.interrupt" in found.detail
    assert "session.create" not in found.detail


def test_doctor_names_every_missing_operation(
    good: Good,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    procs: Procs,
    interpreter_is_the_server: None,
) -> None:
    install_fake(tmp_path, monkeypatch, routes=healthy_routes(("session.create",)))
    start(good, procs)

    found = results_by_name(good, Reader())["operations"]

    assert found.status == "fail"
    for operation in REQUIRED_OPERATIONS[1:]:
        assert operation in found.detail


@pytest.mark.parametrize(
    ("version", "detail"),
    [
        ((3, 11, 9), "python3 3.11.9 is too old; coven's plugin needs 3.12 or newer"),
        (None, "python3 was not found; coven's plugin needs 3.12 or newer"),
    ],
    ids=["too-old", "missing"],
)
def test_doctor_names_a_too_old_python3(
    good: Good,
    monkeypatch: pytest.MonkeyPatch,
    version: tuple[int, int, int] | None,
    detail: str,
) -> None:
    monkeypatch.setattr(checks_server, "python3_version", lambda _env: version)

    found = results_by_name(good)["python3"]

    assert found.status == "fail"
    assert found.detail == detail


def test_doctor_accepts_python3_12(good: Good, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(checks_server, "python3_version", lambda _env: (3, 12, 0))

    found = results_by_name(good)["python3"]

    assert found.status == "ok"
    assert "3.12.0" in found.detail


@pytest.mark.parametrize(
    ("line", "version"),
    [("Python 3.12.3", (3, 12, 3)), ("Python 3.14.0rc1", (3, 14, 0)), ("nonsense", None)],
)
def test_python3_version_reads_the_probe_output(
    good: Good, monkeypatch: pytest.MonkeyPatch, line: str, version: tuple[int, int, int] | None
) -> None:
    class Done:
        returncode = 0
        stdout = f"{line}\n"

    monkeypatch.setattr(checks_server, "probe", lambda _binary, _args, _env: Done())

    assert checks_server.python3_version({}) == version
