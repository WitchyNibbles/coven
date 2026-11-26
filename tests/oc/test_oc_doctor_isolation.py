"""The doctor line for PM shell isolation: it runs the wrapper as `-c true` and reports the result.

The doctor-level tests inject the probe. The probe tests run small stand-in wrappers (scripts that
exit as a wrapper would). One test runs the real wrapper behind a fake `unshare`, as test_oc_isolate
does: no test creates a namespace.
"""

import os
import shutil
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import pytest
from doctor_kit import Good, Reader, results_by_name
from support import REPO

from coven import cli, paths
from coven.opencode.checks import Outcome, exit_code, render, run_doctor
from coven.opencode.checks_isolation import HOOK, probe_isolation
from coven.opencode.profile import Layout

PREFIX = "coven: PM shells run isolated and isolation is not available here: "
SUFFIX = "; ask the user"
NAME = "PM shell isolation"


def script(path: Path, body: str) -> Path:
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def refusing(tmp_path: Path, message: str, code: int = 126) -> Path:
    return script(tmp_path / "wrapper.sh", f"printf '%s\\n' '{message}' >&2\nexit {code}")


class Probe:
    """A stand-in for the probe: records what the doctor handed it and answers with `outcome`."""

    def __init__(self, outcome: Outcome) -> None:
        self.outcome = outcome
        self.calls: list[tuple[Path, Mapping[str, str]]] = []

    def __call__(self, wrapper: Path, base: Mapping[str, str]) -> Outcome:
        self.calls.append((wrapper, base))
        return self.outcome


# The doctor's line


def test_the_doctor_runs_the_source_wrapper_with_its_own_env(good: Good):
    probe = Probe(Outcome("ok", "ok"))
    done = dict(run_doctor(good.layout, good.cwd, read=Reader(), isolation=probe))
    assert done[NAME] == Outcome("ok", "ok")
    [(wrapper, base)] = probe.calls
    assert wrapper == paths.root() / "hooks" / "pm-shell-isolate.sh"
    assert wrapper.is_file()
    assert dict(base) == dict(os.environ)


def test_an_ok_probe_prints_the_ok_line_and_the_exit_code_stays_0(good: Good):
    done = run_doctor(good.layout, good.cwd, read=Reader(), isolation=Probe(Outcome("ok", "ok")))
    assert f"ok {NAME}: ok" in render(done)
    assert exit_code(done) == 0


def test_a_refused_probe_prints_the_missing_piece_and_counts_as_a_problem(good: Good):
    probe = Probe(Outcome("fail", "not available: unshare not found"))
    done = run_doctor(good.layout, good.cwd, read=Reader(), isolation=probe)
    assert f"fail {NAME}: not available: unshare not found" in render(done)
    assert exit_code(done) == 1


def test_the_default_probe_is_the_one_in_the_module(good: Good, monkeypatch: pytest.MonkeyPatch):
    probe = Probe(Outcome("fail", "not available: x"))
    monkeypatch.setattr("coven.opencode.checks_isolation.probe_isolation", probe)
    assert results_by_name(good)[NAME].status == "fail"
    assert len(probe.calls) == 1


def test_the_command_prints_the_line_and_fails_on_a_refusal(
    good: Good, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    probe = Probe(Outcome("fail", "not available: user namespaces are off"))
    monkeypatch.setattr("coven.opencode.checks_isolation.probe_isolation", probe)
    assert cli.main(["opencode", "doctor"]) == 1
    assert f"fail {NAME}: not available: user namespaces are off" in capsys.readouterr().out


@dataclass(frozen=True)
class ColonData(Layout):
    """A profile whose data folder alone has a ':' in its path."""

    @property
    def data(self) -> Path:
        return self.root / "da:ta"


def test_a_colon_in_the_serve_path_is_reported_without_running_the_wrapper(
    good: Good, tmp_path: Path
):
    # the plugin refuses such a path (the hide list is colon-joined): the doctor says so up front
    layout = Layout(tmp_path / "pro:file")
    probe = Probe(Outcome("ok", "ok"))
    done = dict(run_doctor(layout, good.cwd, read=Reader(), isolation=probe))
    assert done[NAME] == Outcome("fail", f"not available: {layout.serve_dir} contains ':'")
    assert probe.calls == []
    assert not layout.serve_dir.exists()  # nothing is checked on disk: serve makes the folder


def test_a_colon_in_the_data_path_alone_names_the_data_path(good: Good, tmp_path: Path):
    layout = ColonData(tmp_path / "profile")
    probe = Probe(Outcome("ok", "ok"))
    done = dict(run_doctor(layout, good.cwd, read=Reader(), isolation=probe))
    assert done[NAME] == Outcome("fail", f"not available: {layout.data} contains ':'")
    assert probe.calls == []


def test_paths_without_a_colon_go_on_to_the_probe(good: Good):
    probe = Probe(Outcome("ok", "ok"))
    done = dict(run_doctor(good.layout, good.cwd, read=Reader(), isolation=probe))
    assert done[NAME] == Outcome("ok", "ok")
    assert len(probe.calls) == 1


# The probe


def test_the_probe_runs_the_wrapper_as_dash_c_true_and_says_ok(tmp_path: Path):
    log = tmp_path / "log"
    wrapper = script(tmp_path / "wrapper.sh", f'printf "%s\\n" "$#" "$1" "$2" > {log}')
    assert probe_isolation(wrapper, {}) == Outcome("ok", "ok")
    assert log.read_text().split() == ["2", "-c", "true"]


def test_the_probe_env_has_the_shell_and_a_temp_folder_to_hide(tmp_path: Path):
    log = tmp_path / "log"
    wrapper = script(
        tmp_path / "wrapper.sh",
        f'printf "%s\\n" "$COVEN_PM_REAL_SHELL" "$COVEN_PM_HIDE" > {log}\n'
        f'[ -d "$COVEN_PM_HIDE" ] && echo folder >> {log}',
    )
    probe_isolation(wrapper, {})
    shell, hide, folder = log.read_text().split("\n")[:3]
    assert shell == "/bin/sh"
    assert folder == "folder"
    assert ":" not in hide
    assert Path(hide).is_absolute()
    assert not Path(hide).exists()


def test_each_probe_hides_a_fresh_folder(tmp_path: Path):
    log = tmp_path / "log"
    wrapper = script(tmp_path / "wrapper.sh", f'printf "%s\\n" "$COVEN_PM_HIDE" >> {log}')
    probe_isolation(wrapper, {})
    probe_isolation(wrapper, {})
    first, second = log.read_text().split()
    assert first != second


def test_the_probe_env_keeps_the_callers_and_drops_the_two_seams(tmp_path: Path):
    log = tmp_path / "log"
    wrapper = script(
        tmp_path / "wrapper.sh",
        f'printf "%s|%s|%s|%s\\n" "$KEEP" "${{COVEN_ISOLATE_SYS-gone}}" '
        f'"${{COVEN_ISOLATE_PATH-gone}}" "$PATH" > {log}',
    )
    base = {
        "KEEP": "kept",
        "PATH": os.environ["PATH"],
        "COVEN_ISOLATE_SYS": "/elsewhere",
        "COVEN_ISOLATE_PATH": "/elsewhere",
        "COVEN_PM_REAL_SHELL": "/caller/own",
        "COVEN_PM_HIDE": "/caller/own",
    }
    probe_isolation(wrapper, base)
    assert log.read_text().strip() == f"kept|gone|gone|{os.environ['PATH']}"
    assert base["COVEN_ISOLATE_SYS"] == "/elsewhere"


def test_the_callers_own_shell_and_hide_values_do_not_reach_the_wrapper(tmp_path: Path):
    log = tmp_path / "log"
    wrapper = script(
        tmp_path / "wrapper.sh", f'printf "%s\\n" "$COVEN_PM_REAL_SHELL" "$COVEN_PM_HIDE" > {log}'
    )
    probe_isolation(wrapper, {"COVEN_PM_REAL_SHELL": "/caller/own", "COVEN_PM_HIDE": "/caller/own"})
    shell, hide = log.read_text().split()
    assert shell == "/bin/sh"
    assert hide != "/caller/own"


def test_a_refusal_line_gives_the_missing_piece(tmp_path: Path):
    wrapper = refusing(
        tmp_path, f"{PREFIX}user namespaces are off (max_user_namespaces is 0){SUFFIX}"
    )
    assert probe_isolation(wrapper, {}) == Outcome(
        "fail", "not available: user namespaces are off (max_user_namespaces is 0)"
    )


def test_a_missing_piece_that_holds_a_semicolon_keeps_it(tmp_path: Path):
    wrapper = refusing(tmp_path, f"{PREFIX}cannot hide /x (a; b){SUFFIX}")
    assert probe_isolation(wrapper, {}).detail == "not available: cannot hide /x (a; b)"


def test_other_stderr_is_the_missing_piece_as_one_line(tmp_path: Path):
    wrapper = script(tmp_path / "wrapper.sh", "printf 'boom\\n  second line\\n' >&2\nexit 3")
    assert probe_isolation(wrapper, {}) == Outcome("fail", "not available: boom second line")


def test_a_failure_with_no_stderr_names_the_exit_code(tmp_path: Path):
    wrapper = script(tmp_path / "wrapper.sh", "exit 7")
    assert probe_isolation(wrapper, {}) == Outcome("fail", "not available: exit code 7")


def test_a_wrapper_that_hangs_times_out(tmp_path: Path):
    wrapper = script(tmp_path / "wrapper.sh", "exec sleep 30")
    found = probe_isolation(wrapper, {}, timeout=0.3)
    assert found == Outcome("fail", "not available: timed out after 0.3 s")


def alive(pid: int) -> bool:
    """Whether the process runs: a zombie waiting to be reaped by init is gone."""
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except (FileNotFoundError, ProcessLookupError):
        return False
    return state != "Z"


def test_a_timeout_kills_the_children_the_wrapper_started(tmp_path: Path):
    pid_file = tmp_path / "child.pid"
    wrapper = script(tmp_path / "wrapper.sh", f"sleep 30 &\necho $! > {pid_file}\nwait")
    assert probe_isolation(wrapper, {}, timeout=0.5).detail.startswith("not available: timed out")
    child = int(pid_file.read_text())
    try:
        assert not alive(child)
    finally:
        if alive(child):
            os.kill(child, 9)


def test_a_missing_wrapper_is_not_found(tmp_path: Path):
    wrapper = tmp_path / "absent.sh"
    assert probe_isolation(wrapper, {}) == Outcome(
        "fail", f"not available: wrapper not found: {wrapper}"
    )


def test_a_wrapper_that_is_not_executable_is_not_found(tmp_path: Path):
    wrapper = tmp_path / "plain.sh"
    wrapper.write_text("#!/bin/sh\n")
    wrapper.chmod(0o644)
    assert probe_isolation(wrapper, {}).detail == f"not available: wrapper not found: {wrapper}"


def test_a_wrapper_that_cannot_start_is_one_line(tmp_path: Path):
    wrapper = tmp_path / "bad.sh"
    wrapper.write_text("not a script\n")
    wrapper.chmod(0o755)
    found = probe_isolation(wrapper, {})
    assert found.status == "fail"
    assert found.detail.startswith("not available: ")
    assert "\n" not in found.detail


def test_the_hide_folder_is_removed_when_the_wrapper_fails(tmp_path: Path):
    log = tmp_path / "log"
    wrapper = script(tmp_path / "wrapper.sh", f'printf "%s" "$COVEN_PM_HIDE" > {log}\nexit 126')
    probe_isolation(wrapper, {})
    assert not Path(log.read_text()).exists()


def test_the_pid_record_folder_exists_while_the_wrapper_runs_and_is_removed_after(tmp_path: Path):
    log = tmp_path / "log"
    wrapper = script(
        tmp_path / "wrapper.sh", f'[ -d "$COVEN_PM_PIDS" ] && printf "%s" "$COVEN_PM_PIDS" > {log}'
    )
    assert probe_isolation(wrapper, {"COVEN_PM_PIDS": "/caller/value"}) == Outcome("ok", "ok")
    folder = Path(log.read_text())
    assert folder != Path("/caller/value")
    assert not folder.exists()


# The real wrapper behind a fake `unshare`

FAKE_UNSHARE = """\
case $1 in
--help) echo '  --map-user=<uid>  map current user'; exit 0 ;;
esac
while :; do
    case $1 in
    --*) shift ;;
    *) break ;;
    esac
done
exec "$@"
"""


def fake_machine(tmp_path: Path, max_user_namespaces: str) -> Path:
    """A shim that runs the real wrapper on a trusted PATH of fakes and a temp `/proc/sys`."""
    tools = tmp_path / "tools"
    tools.mkdir()
    for name in ("sh", "id", "mktemp", "rm", "tr", "true", "mv"):
        found = shutil.which(name)
        assert found
        (tools / name).symlink_to(found)
    script(tools / "unshare", FAKE_UNSHARE)
    script(tools / "mount", "exit 0")
    sys_root = tmp_path / "sys"
    (sys_root / "user").mkdir(parents=True)
    (sys_root / "user" / "max_user_namespaces").write_text(f"{max_user_namespaces}\n")
    return script(
        tmp_path / "shim.sh",
        f"export COVEN_ISOLATE_PATH={tools} COVEN_ISOLATE_SYS={sys_root} TMPDIR={tmp_path}\n"
        f'exec {REPO / HOOK} "$@"',
    )


def test_the_real_wrapper_with_a_working_fake_unshare_is_ok(tmp_path: Path):
    shim = fake_machine(tmp_path, "100")
    assert probe_isolation(shim, {"PATH": os.environ["PATH"]}) == Outcome("ok", "ok")


def test_the_real_wrapper_refusing_gives_its_missing_piece(tmp_path: Path):
    shim = fake_machine(tmp_path, "0")
    assert probe_isolation(shim, {"PATH": os.environ["PATH"]}) == Outcome(
        "fail", "not available: user namespaces are off (max_user_namespaces is 0)"
    )
