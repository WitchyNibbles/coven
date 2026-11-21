"""`coven doctor`: one line per check, `fail` exits 1, nothing is ever written."""

import json
import os
import shutil
import stat
import sys
import threading
import time
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from memory.memory_support import neutral_hook
from pm.fixtures import SLEEP_CLI, fake_claude
from support import commit_all, git

from coven import claude_version, cli, config, doctor, guard, paths, settings
from coven.memory import store

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
FAKE = "#!{python}\nimport sys\n\nsys.stdout.write({out!r})\nsys.exit({code})\n"
SUPERPOWERS = "superpowers@claude-plugins-official"
AQUELARRE = "aquelarre@coven"
NAMES = [
    "claude code",
    "python3",
    "git",
    "uv",
    "gh",
    "superpowers plugin",
    "aquelarre plugin",
    "config",
    "memory store",
    "memory hook",
    "crossSessionInbound",
    "owned settings",
    "hook errors",
    "guard",
    "guard refusals",
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
    world.tool("claude", "2.1.325 (Claude Code)\n")
    world.tool("python3", "Python 3.12.4\n")
    world.tool("uv", "uv 0.5.1\n")
    world.tool("gh")
    world.plugins(
        {
            "version": 2,
            "plugins": {
                SUPERPOWERS: [{"scope": "user", "installPath": "/x"}],
                AQUELARRE: [{"scope": "user", "installPath": "/y"}],
            },
        }
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


def test_aquelarre_installed_is_ok(world):
    assert results()["aquelarre plugin"] == ("ok", AQUELARRE)


def test_aquelarre_in_the_object_form_is_ok(world):
    world.plugins({"plugins": {AQUELARRE: {"installPath": "/y"}}})
    assert status("aquelarre plugin") == "ok"


@pytest.mark.parametrize(
    "content",
    [
        {"plugins": {SUPERPOWERS: [{"scope": "user", "installPath": "/x"}]}},
        {"plugins": {AQUELARRE: []}},
        {"plugins": {AQUELARRE: [{"scope": "user"}]}},
        {"plugins": []},
        "{not json",
    ],
    ids=[
        "other plugin only",
        "empty list",
        "entry without installPath",
        "plugins a list",
        "not json",
    ],
)
def test_aquelarre_absent_or_unreadable_warns_and_says_how_to_reinstall(world, content):
    world.plugins(content)
    state, detail = results()["aquelarre plugin"]
    assert state == "warn"
    assert "not installed" in detail
    assert "reinstall coven@coven" in detail
    assert "\n" not in detail


def test_aquelarre_with_no_plugin_file_warns(world):
    paths.installed_plugins_file().unlink()
    state, detail = results()["aquelarre plugin"]
    assert state == "warn"
    assert "not installed" in detail


def test_aquelarre_on_a_claude_code_older_than_2_1_325_warns(world):
    world.tool("claude", "2.1.324 (Claude Code)\n")
    state, detail = results()["aquelarre plugin"]
    assert state == "warn"
    assert detail == "needs Claude Code 2.1.325 or newer"


def test_aquelarre_at_exactly_2_1_325_is_ok(world):
    assert doctor.AQUELARRE_TESTED_FROM == (2, 1, 325)
    world.tool("claude", "2.1.325 (Claude Code)\n")
    assert status("aquelarre plugin") == "ok"


def test_aquelarre_does_not_repeat_a_missing_claude(world):
    world.remove("claude")
    assert status("claude code") == "fail"
    assert results()["aquelarre plugin"] == ("ok", AQUELARRE)


@pytest.mark.parametrize(
    "output",
    ["not a version at all\n", "2.1.223 (Claude Code)\n"],
    ids=["unreadable version", "below the coven minimum"],
)
def test_aquelarre_reports_the_plugin_when_the_claude_version_cannot_be_used(world, output):
    world.tool("claude", output)
    assert status("claude code") == "fail"  # the one line that owns the version problem
    assert results()["aquelarre plugin"] == ("ok", AQUELARRE)
    paths.installed_plugins_file().unlink()
    state, detail = results()["aquelarre plugin"]
    assert state == "warn"
    assert "not installed" in detail


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
    # The aquelarre line reads the same version, so it crashes too, at its own (warn) level.
    assert found["aquelarre plugin"] == ("warn", "RuntimeError: boom")
    sharing = {"claude code", "aquelarre plugin"}
    assert [found[name][0] for name in NAMES if name not in sharing] == ["ok"] * (
        len(NAMES) - len(sharing)
    )


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


# ---- the production guard ------------------------------------------------------------------

GUARD_JSON = {
    "targets": [
        {
            "system": "orders-db",
            "kind": "production",
            "match": ["psql\\s+.*-h\\s*orders-db"],
            "tools": [],
        }
    ]
}
PROD_COMMAND = "psql -h orders-db.example.internal -c 'select 1'"
REMOTE = "https://git.example.internal/user/notes.git"


def write_config(**fields: object) -> None:
    file = config.config_file()
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps(fields))


def commit_guard(world: World, content: object = GUARD_JSON) -> None:
    (world.store / "systems").mkdir(exist_ok=True)
    text = content if isinstance(content, str) else json.dumps(content)
    (world.store / "systems" / "guard.json").write_text(text)
    commit_all(world.store)


def approval(world: World, name: str, delta: timedelta, **fields: object) -> str:
    expires = (NOW + delta).isoformat()
    record: dict[str, object] = {
        "id": name,
        "system": "orders-db",
        "commands": [PROD_COMMAND],
        "tools": [],
        "expires": expires,
        **fields,
    }
    folder = world.store / "approvals"
    folder.mkdir(exist_ok=True)
    (folder / f"{name}.json").write_text(json.dumps(record))
    commit_all(world.store)
    return str(record["expires"])


def guard_result(name: str = "guard") -> doctor.Result:
    [found] = [r for r in doctor.run_checks(NOW) if r.name == name]
    return found


def warnings(found: doctor.Result) -> list[str]:
    return [line for line in found.more if "warning" in line]


def test_the_guard_is_off_in_one_quiet_line_for_a_store_without_one(world):
    found = guard_result()
    assert (found.status, found.more) == ("ok", ())
    assert found.detail.startswith("off (")
    assert "systems/guard.json" in found.detail


def test_a_missing_store_folder_is_off_with_that_reason(world, tmp_path):
    write_config(memory_path=str(tmp_path / "nowhere"))
    found = guard_result()
    assert (found.status, found.more) == ("ok", ())
    assert found.detail.startswith("off (no memory store folder")


def test_a_committed_guard_file_with_no_latch_is_on_and_warns_to_run_setup(world):
    commit_guard(world)
    found = guard_result()
    assert found.status == "warn"
    assert found.detail.startswith("on (guard.json committed)")
    [line] = warnings(found)
    assert "coven setup --yes" in line


def test_a_set_latch_is_on_and_says_so_without_warning(world):
    commit_guard(world)
    write_config(guard="required")
    found = guard_result()
    assert (found.status, found.detail.split(";")[0]) == ("ok", "on (latch set)")
    assert warnings(found) == []


def test_the_latch_alone_keeps_the_guard_on_when_the_file_is_not_in_the_work_tree(world):
    commit_guard(world)
    (world.store / "systems" / "guard.json").unlink()
    write_config(guard="required")
    assert guard_result().detail.startswith("on (latch set)")


def test_a_committed_guard_file_deleted_from_the_work_tree_still_warns(world):
    commit_guard(world)
    (world.store / "systems" / "guard.json").unlink()
    found = guard_result()
    assert found.status == "warn"
    assert found.detail.startswith("off (")
    assert any("coven setup --yes" in line for line in warnings(found))


def test_a_valid_guard_file_at_head_is_reported_with_its_target_count(world):
    commit_guard(world)
    write_config(guard="required")
    assert any("guard.json at HEAD: ok: 1 targets" in line for line in guard_result().more)


def test_an_invalid_guard_file_at_head_warns_with_the_reason(world):
    commit_guard(world, "{not json")
    write_config(guard="required")
    found = guard_result()
    assert found.status == "warn"
    [line] = warnings(found)
    assert "guard.json at HEAD is not valid" in line
    assert "not valid JSON" in line


def test_an_invalid_head_with_an_older_valid_version_says_which_one_is_enforced(world):
    commit_guard(world)
    commit_guard(world, {"targets": [{"system": "orders-db", "kind": "prod"}]})
    write_config(guard="required")
    found = guard_result()
    [line] = warnings(found)
    assert "kind" in line
    assert "older" in line


def test_an_uncommitted_guard_file_is_on_but_not_committed(world):
    (world.store / "systems").mkdir(exist_ok=True)
    (world.store / "systems" / "guard.json").write_text(json.dumps(GUARD_JSON))
    found = guard_result()
    assert found.detail.startswith("on (")
    assert any("not committed" in line for line in found.more)


def test_live_approvals_show_id_system_and_expiry_one_line_each(world):
    commit_guard(world)
    write_config(guard="required")
    first = approval(world, "step-a", timedelta(hours=2))
    second = approval(world, "step-b", timedelta(hours=1), system="billing-api")
    lines = [line for line in guard_result().more if line.strip().startswith("approval ")]
    assert len(lines) == 2
    assert any("step-a" in line and "orders-db" in line and first in line for line in lines)
    assert any("step-b" in line and "billing-api" in line and second in line for line in lines)


def test_approval_lines_keep_their_indent_like_the_other_guard_lines(world):
    commit_guard(world)
    write_config(guard="required")
    approval(world, "step-a", timedelta(hours=2))
    [line] = [line for line in guard_result().more if "approval step-a" in line]
    assert line.startswith("  approval step-a")


def test_approval_expiry_is_shown_in_utc_whatever_zone_the_file_uses(world):
    commit_guard(world)
    write_config(guard="required")
    local = timezone(timedelta(hours=2))
    expires = (NOW + timedelta(hours=2)).astimezone(local).isoformat()
    approval(world, "step-a", timedelta(hours=2), expires=expires)
    [line] = [line for line in guard_result().more if "approval step-a" in line]
    assert line.endswith("until 2026-11-13T14:00:00+00:00")


def test_expired_and_too_long_approvals_are_not_live(world):
    commit_guard(world)
    write_config(guard="required")
    approval(world, "old", timedelta(hours=-1))
    approval(world, "forever", timedelta(hours=13))
    found = guard_result()
    assert any("no live approvals" in line for line in found.more)
    assert not any("old" in line or "forever" in line for line in found.more)


def test_a_malformed_approval_is_not_live_and_does_not_break_doctor(world):
    commit_guard(world)
    write_config(guard="required")
    approval(world, "odd", timedelta(hours=1), extra="field")
    assert any("no live approvals" in line for line in guard_result().more)


def test_a_guarded_store_with_a_remote_in_the_config_warns(world):
    commit_guard(world)
    write_config(guard="required", memory_remote=REMOTE)
    [line] = warnings(guard_result())
    assert "never pushes" in line
    assert "remote" in line


def test_a_guarded_store_with_an_origin_in_git_warns(world):
    commit_guard(world)
    write_config(guard="required")
    git(world.store, "remote", "add", "origin", REMOTE)
    [line] = warnings(guard_result())
    assert "never pushes" in line


def test_a_remote_on_a_store_without_a_guard_is_not_a_guard_warning(world):
    write_config(memory_remote=REMOTE)
    assert guard_result().more == ()


def test_doctor_gives_one_warning_line_when_the_guard_check_times_out(world, monkeypatch):
    commit_guard(world)
    write_config(guard="required")
    hook = guard.load_guard()
    monkeypatch.setattr(hook, "DEADLINE", 0.2)
    monkeypatch.setattr(hook, "load_targets", lambda _store: time.sleep(5))
    found = guard_result()
    assert found.status == "warn"
    assert guard.TIMED_OUT in found.detail
    assert "\n" not in found.detail


def test_a_latched_store_that_cannot_be_read_is_one_warning_not_a_crash(world, tmp_path):
    write_config(guard="required", memory_path=str(tmp_path / "gone"))
    found = guard_result()
    assert found.status == "warn"
    assert "gone" in found.detail
    assert "\n" not in found.detail


def test_an_unreadable_config_is_a_guard_warning_not_a_crash(world):
    config.config_file().parent.mkdir(parents=True, exist_ok=True)
    config.config_file().write_text("{broken")
    assert guard_result().status == "warn"


def test_doctor_writes_nothing_for_a_guarded_store_with_a_refusal_log(world, tmp_path):
    commit_guard(world)
    write_config(guard="required", memory_remote=REMOTE)
    approval(world, "step-a", timedelta(hours=2))
    world.log("prod-guard", refusal())

    def snapshot() -> dict[str, tuple[int, bytes | None]]:
        found = {}
        for folder, dirs, files in os.walk(tmp_path):
            for name in [*files, *dirs]:
                path = Path(folder, name)
                found[str(path)] = (
                    path.stat().st_mtime_ns,
                    path.read_bytes() if path.is_file() and ".git" not in path.parts else None,
                )
        return found

    before = snapshot()
    doctor.run_checks(NOW)
    assert snapshot() == before


# ---- the guard's refusals ------------------------------------------------------------------


def refusal(
    delta: timedelta = timedelta(hours=1),
    command: str | None = PROD_COMMAND,
    message: str = "coven prod-guard: 'orders-db' is production; this exact step needs the "
    "user's approval (send a PROD-STEP)",
    **more: object,
) -> dict[str, object]:
    return {
        "ts": at(delta),
        "decision": "deny",
        "tool": "Bash",
        "command": command,
        "message": message,
        "role": "pm",
        "system": "orders-db",
        **more,
    }


def refusal_lines() -> tuple[str, tuple[str, ...]]:
    found = guard_result("guard refusals")
    assert found.status == "ok"
    return found.detail, found.more


def test_no_log_says_no_guard_refusals_logged(world):
    assert refusal_lines() == ("no guard refusals logged", ())


def test_a_log_of_allowed_calls_only_says_no_guard_refusals_logged(world):
    world.log("prod-guard", {**refusal(), "decision": "allow", "message": None})
    assert refusal_lines()[0] == "no guard refusals logged"


def test_refusals_show_newest_first_one_line_each_without_warning(world):
    world.log(
        "prod-guard",
        refusal(timedelta(hours=3), command="first command"),
        {"ts": at(timedelta(hours=2)), "decision": "allow", "tool": "Bash", "command": "ls"},
        refusal(timedelta(hours=1), command="last command"),
    )
    _detail, lines = refusal_lines()
    assert len(lines) == 2
    assert "last command" in lines[0]
    assert "first command" in lines[1]
    assert "2026-11-13T11:00:00" in lines[0]
    assert " | pm | orders-db | " in lines[0]


def test_only_the_last_five_refusals_show(world):
    world.log(
        "prod-guard",
        *[refusal(timedelta(hours=10 - n), command=f"command {n}") for n in range(8)],
    )
    _detail, lines = refusal_lines()
    assert [n for n in range(8) if any(f"command {n}" in line for line in lines)] == [
        3,
        4,
        5,
        6,
        7,
    ]
    assert "command 7" in lines[0]


def test_older_refusals_come_from_the_rotated_log_when_the_current_one_is_short(world):
    folder = paths.logs_dir()
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "prod-guard.jsonl.1").write_text(
        json.dumps(refusal(timedelta(hours=9), command="rotated")) + "\n"
    )
    world.log("prod-guard", refusal(command="current"))
    _detail, lines = refusal_lines()
    assert ["current" in lines[0], "rotated" in lines[1]] == [True, True]


@pytest.mark.parametrize(
    ("role", "shown"),
    [("pm", "pm"), ("other", "other"), ("unset", "unset"), ("gm", "other"), (None, "other")],
    ids=["pm", "other", "unset", "raw-value", "null"],
)
def test_the_logged_role_class_shows_and_anything_else_reads_as_other(world, role, shown):
    world.log("prod-guard", refusal(role=role))
    [line] = refusal_lines()[1]
    assert f" | {shown} | " in line
    if role == "gm":
        assert "gm" not in line


def test_an_old_record_with_no_role_or_system_says_they_were_not_logged(world):
    old = {k: v for k, v in refusal().items() if k not in ("role", "system")}
    world.log("prod-guard", old)
    [line] = refusal_lines()[1]
    assert " | role not logged | system not logged | " in line


def test_the_system_is_what_the_hook_logged_not_parsed_from_the_message(world):
    message = "coven prod-guard: a 'secret-role-7' session cannot run claude; ask the GM"
    world.log("prod-guard", refusal(message=message, role="other", system="claude-start"))
    [line] = refusal_lines()[1]
    assert " | other | claude-start | " in line
    assert "secret-role-7" not in line
    assert "session cannot" not in line


def test_a_system_is_cut_and_stays_on_one_printable_line(world):
    world.log("prod-guard", refusal(system="orders-db\n\u202e\u200b\x00" + "y" * 500))
    [line] = refusal_lines()[1]
    assert "\n" not in line
    assert all(ch.isprintable() for ch in line)
    assert line.count("y") <= 80
    assert len(line) < 400


def test_a_command_is_cut_to_80_characters_on_one_printable_line(world):
    command = "psql\n-h orders-db.example.internal \u202e\u200b\x00 " + "x" * 300
    world.log("prod-guard", refusal(command=command))
    [line] = refusal_lines()[1]
    assert "\n" not in line
    assert all(ch.isprintable() for ch in line)
    assert "xxx" in line
    assert "x" * 81 not in line
    assert line.count("x") <= 80


def test_a_refusal_with_no_command_shows_the_tool(world):
    world.log("prod-guard", refusal(command=None, tool="mcp__orders__write"))
    [line] = refusal_lines()[1]
    assert "mcp__orders__write" in line


def test_hostile_log_lines_are_skipped_silently(world):
    folder = paths.logs_dir()
    folder.mkdir(parents=True, exist_ok=True)
    lines = [
        json.dumps(refusal(command="kept one")),
        "[1, 2, 3]",
        '"just a string"',
        "null",
        "{not json at all",
        json.dumps({"decision": "deny"}),  # no ts, tool or command
        json.dumps({**refusal(), "system": ["orders-db"]}),
        json.dumps({**refusal(), "tool": ["Bash"]}),
        json.dumps({**refusal(), "command": {"a": 1}}),
        json.dumps({**refusal(), "ts": 12}),
        "[" * 3000,
        json.dumps(refusal(command="kept two")),
    ]
    (folder / "prod-guard.jsonl").write_text("\n".join(lines) + "\n" + '{"decision": "de')
    _detail, shown = refusal_lines()
    assert len(shown) == 2
    assert "kept two" in shown[0]
    assert "kept one" in shown[1]


def test_a_one_megabyte_line_and_a_torn_last_line_do_not_break_it(world):
    folder = paths.logs_dir()
    folder.mkdir(parents=True, exist_ok=True)
    big = json.dumps(refusal(command="z" * (1024 * 1024)))
    (folder / "prod-guard.jsonl").write_text(
        json.dumps(refusal(command="early"))
        + "\n"
        + big
        + "\n"
        + json.dumps(refusal(command="after"))
        + "\n"
        + '{"ts": "2026-11-13T1'
    )
    _detail, shown = refusal_lines()
    assert len(shown) == 1
    assert "after" in shown[0]


def test_only_the_last_64_kib_of_the_log_is_read(world):
    folder = paths.logs_dir()
    folder.mkdir(parents=True, exist_ok=True)
    filler = json.dumps({"ts": at(timedelta(hours=1)), "decision": "allow", "pad": "p" * 200})
    body = json.dumps(refusal(command="ancient")) + "\n" + (filler + "\n") * 400
    (folder / "prod-guard.jsonl").write_text(body + json.dumps(refusal(command="recent")) + "\n")
    _detail, shown = refusal_lines()
    assert len(shown) == 1
    assert "recent" in shown[0]


def test_a_refusal_log_fifo_does_not_hang_doctor(world):
    paths.logs_dir().mkdir(parents=True, exist_ok=True)
    os.mkfifo(paths.logs_dir() / "prod-guard.jsonl")
    done: list[object] = []
    worker = threading.Thread(target=lambda: done.append(doctor.run_checks(NOW)), daemon=True)
    worker.start()
    worker.join(10)
    assert done, "doctor is stuck reading a FIFO"
