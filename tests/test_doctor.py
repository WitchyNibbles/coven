"""`coven doctor`: one line per check, `fail` exits 1, nothing is ever written."""

import json
import os
import shutil
import stat
import sys
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from memory.memory_support import neutral_hook
from pm.fixtures import SLEEP_CLI, fake_claude

from coven import claude_version, cli, doctor, paths, settings
from coven.memory import store

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
FAKE = "#!{python}\nimport sys\n\nsys.stdout.write({out!r})\nsys.exit({code})\n"
SUPERPOWERS = "superpowers@claude-plugins-official"
NAMES = [
    "claude code",
    "python3",
    "git",
    "uv",
    "gh",
    "superpowers plugin",
    "config",
    "memory store",
    "memory hook",
    "crossSessionInbound",
    "owned settings",
    "hook errors",
]


class World:
    """A healthy install under tmp_path, with fake tools alone on PATH."""

    def __init__(self, root: Path) -> None:
        self.bin = root / "fake-bin"
        self.bin.mkdir()
        self.store = Path.home() / "coven-memory"

    def tool(self, name: str, out: str = "", code: int = 0) -> None:
        script = self.bin / name
        script.write_text(FAKE.format(python=sys.executable, out=out, code=code))
        script.chmod(script.stat().st_mode | stat.S_IXUSR)

    def sleeper(self, name: str) -> None:
        script = self.bin / name
        script.write_text(SLEEP_CLI.format(python=sys.executable))
        script.chmod(script.stat().st_mode | stat.S_IXUSR)

    def remove(self, name: str) -> None:
        (self.bin / name).unlink()

    def plugins(self, content: object) -> None:
        file = paths.installed_plugins_file()
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(content if isinstance(content, str) else json.dumps(content))

    def settings(self, content: dict[str, object]) -> None:
        file = settings.settings_file()
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(json.dumps(content))

    def log(self, hook: str, *records: object, raw: str = "") -> None:
        folder = paths.logs_dir()
        folder.mkdir(parents=True, exist_ok=True)
        lines = [json.dumps(record) for record in records]
        (folder / f"{hook}.jsonl").write_text("\n".join(lines) + "\n" + raw)


def at(delta: timedelta) -> str:
    return (NOW - delta).isoformat()


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> World:
    world = World(tmp_path)
    store.init_store(world.store, None)
    neutral_hook(world.store)
    git = shutil.which("git")
    assert git
    (world.bin / "git").symlink_to(git)
    world.tool("claude", "2.1.300 (Claude Code)\n")
    world.tool("python3", "Python 3.12.4\n")
    world.tool("uv", "uv 0.5.1\n")
    world.tool("gh")
    world.plugins(
        {"version": 2, "plugins": {SUPERPOWERS: [{"scope": "user", "installPath": "/x"}]}}
    )
    world.settings({"crossSessionInbound": "accept"})
    monkeypatch.setenv("PATH", str(world.bin))  # the real claude, gh and uv are out of reach
    return world


def results() -> dict[str, tuple[str, str]]:
    found = doctor.run_checks(NOW)
    assert [result.name for result in found] == NAMES
    return {result.name: (result.status, result.detail) for result in found}


def status(name: str) -> str:
    return results()[name][0]


def test_a_healthy_install_is_ok_everywhere(world):
    assert {name: found[0] for name, found in results().items()} == dict.fromkeys(NAMES, "ok")


# ---- tools -------------------------------------------------------------------------------


def test_claude_older_than_the_minimum_fails(world):
    world.tool("claude", "2.1.223 (Claude Code)\n")
    state, detail = results()["claude code"]
    assert state == "fail"
    assert "2.1.223" in detail


def test_claude_newer_than_last_tested_warns(world):
    world.tool("claude", "9.0.0 (Claude Code)\n")
    state, detail = results()["claude code"]
    assert state == "warn"
    assert "2.1.325" in detail


def test_claude_at_exactly_the_last_tested_version_is_ok(world):
    world.tool("claude", f"{claude_version.dotted(claude_version.LAST_TESTED)} (Claude Code)\n")
    assert status("claude code") == "ok"


def test_claude_missing_fails(world):
    world.remove("claude")
    assert results()["claude code"] == ("fail", "claude not found on PATH")


def test_claude_that_does_not_answer_fails(world, monkeypatch):
    fake_claude(world.bin, SLEEP_CLI)
    monkeypatch.setattr(claude_version, "TIMEOUT", 0.2)
    assert status("claude code") == "fail"


def test_python3_older_than_3_12_fails(world):
    world.tool("python3", "Python 3.11.9\n")
    state, detail = results()["python3"]
    assert state == "fail"
    assert "3.11.9" in detail


def test_python3_missing_fails(world):
    world.remove("python3")
    assert status("python3") == "fail"


def test_git_missing_fails(world):
    (world.bin / "git").unlink()
    assert status("git") == "fail"


def test_uv_missing_only_warns(world):
    world.remove("uv")
    assert status("uv") == "warn"


def test_gh_missing_warns(world):
    world.remove("gh")
    state, detail = results()["gh"]
    assert state == "warn"
    assert "not found on PATH" in detail


def test_gh_that_does_not_answer_is_not_called_missing(world, monkeypatch):
    world.sleeper("gh")
    monkeypatch.setattr(claude_version, "TIMEOUT", 0.2)
    state, detail = results()["gh"]
    assert state == "warn"
    assert "did not answer" in detail
    assert "not installed" not in detail


def test_gh_not_logged_in_warns(world):
    world.tool("gh", code=1)
    state, detail = results()["gh"]
    assert state == "warn"
    assert "gh auth login" in detail


# ---- plugin, config, memory -----------------------------------------------------------------


@pytest.mark.parametrize(
    "content",
    [
        {"version": 2, "plugins": {SUPERPOWERS: [{"scope": "user", "installPath": "/x"}]}},
        {"plugins": {SUPERPOWERS: {"installPath": "/x"}}},
    ],
    ids=["list form", "object form"],
)
def test_superpowers_in_either_plugin_form_is_ok(world, content):
    world.plugins(content)
    assert status("superpowers plugin") == "ok"


@pytest.mark.parametrize(
    "content",
    [
        {"plugins": {"coven@coven": [{"installPath": "/x"}]}},
        {"plugins": {SUPERPOWERS: []}},
        {"plugins": {SUPERPOWERS: [{"scope": "user"}]}},
        {"plugins": {SUPERPOWERS: {"scope": "user"}}},
        {"plugins": []},
        ["not", "an", "object"],
        "{not json",
    ],
    ids=[
        "other plugin",
        "empty list",
        "list entry without installPath",
        "object without installPath",
        "plugins not an object",
        "not an object",
        "not json",
    ],
)
def test_superpowers_absent_or_unreadable_fails(world, content):
    world.plugins(content)
    state, detail = results()["superpowers plugin"]
    assert state == "fail"
    assert "\n" not in detail


def test_superpowers_with_no_plugin_file_fails(world):
    paths.installed_plugins_file().unlink()
    assert status("superpowers plugin") == "fail"


def test_an_invalid_config_fails(world):
    file = paths.config_dir() / "config.json"
    file.parent.mkdir(parents=True)
    file.write_text('{"pm_cap": 0}')
    state, detail = results()["config"]
    assert state == "fail"
    assert "pm_cap" in detail


def test_a_store_not_set_up_yet_warns_and_points_to_setup(world):
    shutil.rmtree(world.store)
    found = results()
    assert found["memory store"] == ("warn", "not set up yet; run /coven:setup")
    assert found["memory hook"][0] == "warn"


def test_a_folder_that_is_not_a_store_fails(world):
    shutil.rmtree(world.store)
    world.store.mkdir()
    state, detail = results()["memory store"]
    assert state == "fail"
    assert "coven memory init" in detail
    assert status("memory hook") == "fail"


def test_a_store_path_cannot_break_the_line(world):
    file = paths.config_dir() / "config.json"
    file.parent.mkdir(parents=True)
    file.write_text(json.dumps({"memory_path": "/nowhere/a\nfail git: fake\x1b[31m"}))
    for name in ("memory store", "memory hook"):
        detail = results()[name][1]
        assert "\n" not in detail
        assert "\x1b" not in detail


def test_an_outdated_store_hook_fails(world):
    hook = world.store / ".git" / "hooks" / "pre-commit"
    shipped = store.hook_version(store.HOOK_SOURCE.read_text())
    assert shipped
    line = f"# coven-hook-version: {shipped}"
    hook.write_text(hook.read_text().replace(line, f"# coven-hook-version: {shipped - 1}"))
    state, detail = results()["memory hook"]
    assert state == "fail"
    assert f"version {shipped - 1}" in detail


def test_a_missing_store_hook_fails(world):
    (world.store / ".git" / "hooks" / "pre-commit").unlink()
    assert status("memory hook") == "fail"


# ---- settings -------------------------------------------------------------------------------


def test_cross_session_inbound_unset_warns(world):
    world.settings({})
    state, detail = results()["crossSessionInbound"]
    assert state == "warn"
    assert "coven setup" in detail


def test_unreadable_settings_warn_without_crashing(world):
    world.settings({})
    settings.apply(settings.plan(settings.Choices(cross_session_accept=True)))
    settings.settings_file().write_text("{broken")
    assert status("crossSessionInbound") == "warn"
    assert status("owned settings") == "warn"


def test_owned_keys_still_as_written_are_ok(world):
    settings.apply(settings.plan(settings.Choices(deny_secrets=True)))
    state, detail = results()["owned settings"]
    assert state == "ok"
    assert "still as written" in detail


def test_an_owned_key_changed_since_setup_warns(world):
    world.settings({})
    settings.apply(settings.plan(settings.Choices(cross_session_accept=True)))
    world.settings({"crossSessionInbound": "ask"})
    state, detail = results()["owned settings"]
    assert state == "warn"
    assert "crossSessionInbound" in detail


def test_a_crash_in_a_warn_level_check_warns_not_fails(world):
    file = settings.owned_file()
    file.parent.mkdir(parents=True, exist_ok=True)
    nul_path = {"file": "a\x00b", "key": ["k"], "before": {"present": False}}
    nul_path["after"] = {"present": True, "value": 1}
    file.write_text(json.dumps({"version": 1, "changes": [nul_path], "created": []}))
    state, detail = results()["owned settings"]
    assert state == "warn"
    assert "\n" not in detail


def test_a_broken_owned_record_warns(world):
    file = settings.owned_file()
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text("{broken")
    assert status("owned settings") == "warn"


# ---- hook errors ---------------------------------------------------------------------------


def test_doctor_shows_recent_hook_errors(world):
    world.log("deny_secrets", {"ts": at(timedelta(hours=2)), "error": "bad payload"})
    [found] = [r for r in doctor.run_checks(NOW) if r.name == "hook errors"]
    assert found.status == "warn"
    assert any("deny_secrets" in line and "bad payload" in line for line in found.more)


def test_hook_errors_older_than_a_day_are_shown_but_do_not_warn(world):
    world.log("deny_secrets", {"ts": at(timedelta(hours=25)), "error": "old news"})
    [found] = [r for r in doctor.run_checks(NOW) if r.name == "hook errors"]
    assert found.status == "ok"
    assert any("old news" in line for line in found.more)


def test_only_the_last_five_hook_errors_show_newest_last(world):
    world.log(
        "memory_context",
        *({"ts": at(timedelta(hours=30 - n)), "error": f"failure {n}"} for n in range(8)),
    )
    [found] = [r for r in doctor.run_checks(NOW) if r.name == "hook errors"]
    text = "\n".join(found.more)
    assert [f"failure {n}" in text for n in range(8)] == [False] * 3 + [True] * 5
    assert text.index("failure 3") < text.index("failure 7")


def test_hook_errors_merge_the_logs_of_every_hook_by_time(world):
    world.log("deny_secrets", {"ts": at(timedelta(hours=3)), "error": "from deny"})
    world.log("memory_context", {"ts": at(timedelta(hours=1)), "error": "from memory"})
    [found] = [r for r in doctor.run_checks(NOW) if r.name == "hook errors"]
    assert "from deny" in found.more[0]
    assert "from memory" in found.more[1]


def test_decisions_and_garbage_in_the_logs_are_not_errors(world):
    world.log(
        "pm_permissions",
        {"ts": at(timedelta(hours=1)), "decision": "deny", "message": "to the GM"},
        {"ts": "yesterday-ish", "error": "no usable time"},
        raw='not json\n[1, 2]\n{"error": 5}\n\xff\n',
    )
    [found] = [r for r in doctor.run_checks(NOW) if r.name == "hook errors"]
    assert found.status == "ok"
    assert not any("to the GM" in line for line in found.more)


def test_a_hook_error_text_stays_on_one_short_line(world):
    world.log("deny_secrets", {"ts": at(timedelta(hours=1)), "error": "a\nb\x1b[31m" + "x" * 500})
    [found] = [r for r in doctor.run_checks(NOW) if r.name == "hook errors"]
    [line] = found.more
    assert "\n" not in line.strip()
    assert "\x1b" not in line
    assert len(line) < 250


def test_a_deeply_nested_log_line_is_skipped_and_real_errors_still_show(world):
    world.log(
        "deny_secrets",
        {"ts": at(timedelta(hours=1)), "error": "real failure"},
        raw="[" * 100_000 + "\n",
    )
    [found] = [r for r in doctor.run_checks(NOW) if r.name == "hook errors"]
    assert found.status == "warn"
    assert any("real failure" in line for line in found.more)


def test_a_fifo_named_like_a_log_does_not_hang_doctor(world):
    paths.logs_dir().mkdir(parents=True)
    os.mkfifo(paths.logs_dir() / "x.jsonl")
    done: list[object] = []
    worker = threading.Thread(target=lambda: done.append(doctor.run_checks(NOW)), daemon=True)
    worker.start()
    worker.join(10)
    assert done, "doctor is stuck reading a FIFO"


def test_a_log_file_name_cannot_break_the_line(world):
    world.log("a\nfail git: fake", {"ts": at(timedelta(hours=1)), "error": "boom"})
    [found] = [r for r in doctor.run_checks(NOW) if r.name == "hook errors"]
    [line] = found.more
    assert "\n" not in line.strip()


def test_no_hook_log_is_ok(world):
    state, detail = results()["hook errors"]
    assert state == "ok"
    assert "no" in detail


# ---- the command ---------------------------------------------------------------------------


def test_one_crashing_check_fails_alone_and_the_rest_still_run(world, monkeypatch):
    def boom(run=None):
        raise RuntimeError("boom\nsecond line")

    monkeypatch.setattr(claude_version, "installed", boom)
    found = results()
    assert found["claude code"] == ("fail", "RuntimeError: boom")
    assert [found[name][0] for name in NAMES if name != "claude code"] == ["ok"] * 11


def test_doctor_exit_1_on_any_fail(world, capsys):
    world.remove("claude")
    assert cli.main(["doctor"]) == 1
    out = capsys.readouterr().out.splitlines()
    assert "fail claude code: claude not found on PATH" in out
    assert "ok git: " in "\n".join(out)


def test_doctor_exits_0_with_only_warnings(world, capsys):
    world.remove("uv")
    assert cli.main(["doctor"]) == 0
    first = [line for line in capsys.readouterr().out.splitlines() if not line.startswith(" ")]
    assert [line.split(" ", 1)[0] for line in first].count("warn") == 1
    assert len(first) == len(NAMES)


def test_doctor_writes_nothing(world, tmp_path):
    world.log("deny_secrets", {"ts": at(timedelta(hours=1)), "error": "x"})
    settings.apply(settings.plan(settings.Choices(cross_session_accept=True)))

    def snapshot() -> dict[str, tuple[int, bytes | None]]:
        found = {}
        for folder, _dirs, files in os.walk(tmp_path):
            for name in [*files, *_dirs]:
                path = Path(folder, name)
                found[str(path)] = (
                    path.stat().st_mtime_ns,
                    path.read_bytes() if path.is_file() and ".git" not in path.parts else None,
                )
        return found

    before = snapshot()
    doctor.run_checks(NOW)
    assert snapshot() == before
