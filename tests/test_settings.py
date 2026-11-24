import dataclasses
import json
import os
import shlex
import stat
from pathlib import Path

import pytest

from coven import config, paths, settings
from coven.errors import CovenError
from coven.settings import MISSING, Choices


def settings_file() -> Path:
    return paths.claude_dir() / "settings.json"


def shim_file() -> Path:
    return Path.home() / ".local" / "bin" / "coven"


def owned_file() -> Path:
    return paths.config_dir() / "owned.json"


def write_settings(data: object, mode: int = 0o644) -> Path:
    file = settings_file()
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps(data), encoding="utf-8")
    file.chmod(mode)
    return file


def read_json(file: Path) -> dict[str, object]:
    loaded = json.loads(file.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def choices(
    *,
    deny_secrets: bool = False,
    cross_session_accept: bool = False,
    shim: bool = False,
    pm_cap: int = 2,
    memory_remote: str | None = None,
) -> Choices:
    return Choices(
        deny_secrets=deny_secrets,
        cross_session_accept=cross_session_accept,
        shim=shim,
        pm_cap=pm_cap,
        memory_path=Path("/store"),
        memory_remote=memory_remote,
    )


def guarded(guard: str | None = "required") -> Choices:
    return dataclasses.replace(choices(), guard=guard)


def settings_change(wanted: Choices) -> settings.Change:
    (change,) = (c for c in settings.plan(wanted) if c.file == settings_file())
    return change


def test_apply_keeps_backup_and_owned():
    file = write_settings({"theme": "dark"})
    before_bytes = file.read_bytes()

    settings.apply(settings.plan(choices(cross_session_accept=True)))

    assert read_json(file) == {"theme": "dark", "crossSessionInbound": "accept"}
    backups = list(file.parent.glob("settings.json.coven-backup-*"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == before_bytes
    owned = json.loads(owned_file().read_text(encoding="utf-8"))
    recorded = [(entry["file"], entry["key"]) for entry in owned["changes"]]
    assert (str(file), ["crossSessionInbound"]) in recorded
    assert stat.S_IMODE(owned_file().stat().st_mode) == 0o600


def test_apply_writes_config_keys():
    settings.apply(settings.plan(choices(pm_cap=3, memory_remote="git@example.com:a/b.git")))

    loaded = config.load_config()
    assert loaded.pm_cap == 3
    assert loaded.memory_path == Path("/store")
    assert loaded.memory_remote == "git@example.com:a/b.git"
    assert stat.S_IMODE(config.config_file().stat().st_mode) == 0o600


def test_guard_required_is_planned_applied_and_undone_with_its_previous_value():
    config.save_config(config.Config(memory_path=Path("/store"), guard=None))
    assert not config.load_config().guard
    settings.apply(settings.plan(guarded()))
    assert config.load_config().guard == "required"
    assert settings.plan(guarded()) == []

    settings.undo()

    assert read_json(config.config_file())["guard"] is None


def test_guard_undo_restores_a_guard_key_that_was_absent():
    config.config_file().parent.mkdir(parents=True)
    config.config_file().write_text('{"pm_cap": 3}', encoding="utf-8")
    settings.apply(settings.plan(dataclasses.replace(choices(pm_cap=3), guard="required")))

    settings.undo()

    assert read_json(config.config_file()) == {"pm_cap": 3}


def test_no_guard_choice_plans_no_guard_change():
    assert all(c.key != ("guard",) for c in settings.plan(choices()))


def test_describe_shows_the_guard_change():
    change = next(c for c in settings.plan(guarded()) if c.key == ("guard",))
    assert settings.describe(change).endswith('guard: (missing) -> "required"')


def test_guard_other_than_required_is_refused():
    with pytest.raises(CovenError, match="guard"):
        settings.plan(guarded("optional"))


def test_settings_with_comment_stops_with_parse_error():
    file = settings_file()
    file.parent.mkdir(parents=True)
    text = '{\n  // my notes\n  "theme": "dark"\n}\n'
    file.write_text(text, encoding="utf-8")

    with pytest.raises(CovenError, match=r"settings\.json.*line 2"):
        settings.plan(choices(cross_session_accept=True))

    assert file.read_text(encoding="utf-8") == text
    assert not list(file.parent.glob("*.coven-backup-*"))


def test_top_level_array_rejected():
    write_settings(["not", "an", "object"])

    with pytest.raises(CovenError, match=r"settings\.json.*JSON object"):
        settings.plan(choices(deny_secrets=True))


def test_missing_claude_dir_created():
    assert not paths.claude_dir().exists()

    settings.apply(settings.plan(choices(cross_session_accept=True)))

    assert read_json(settings_file()) == {"crossSessionInbound": "accept"}
    assert stat.S_IMODE(settings_file().stat().st_mode) == 0o600


def test_existing_file_mode_is_kept():
    file = write_settings({}, mode=0o640)

    settings.apply(settings.plan(choices(cross_session_accept=True)))

    assert stat.S_IMODE(file.stat().st_mode) == 0o640


def test_deny_list_merged_not_replaced():
    file = write_settings({"permissions": {"deny": ["Bash(rm -rf:*)"], "allow": ["Read"]}})

    settings.apply(settings.plan(choices(deny_secrets=True)))

    permissions = read_json(file)["permissions"]
    assert isinstance(permissions, dict)
    assert permissions["allow"] == ["Read"]
    deny = permissions["deny"]
    assert isinstance(deny, list)
    assert deny[0] == "Bash(rm -rf:*)"
    assert "Read(./.env*)" in deny
    assert "Read(~/.ssh/**)" in deny
    assert len(deny) == len(set(deny))


def test_a_second_plan_has_nothing_left_to_change():
    write_settings({})
    both = choices(deny_secrets=True, cross_session_accept=True)
    settings.apply(settings.plan(both))

    assert settings.plan(both) == []


def test_undo_restores_only_owned_keys():
    file = write_settings({"theme": "dark", "crossSessionInbound": "ask"})
    settings.apply(settings.plan(choices(cross_session_accept=True, deny_secrets=True)))
    later = read_json(file)
    later["editorMode"] = "vim"
    file.write_text(json.dumps(later), encoding="utf-8")

    skipped = settings.undo()

    assert skipped == []
    assert read_json(file) == {
        "theme": "dark",
        "crossSessionInbound": "ask",
        "editorMode": "vim",
    }
    assert not owned_file().exists()


def test_undo_removes_only_the_list_entries_coven_added():
    file = write_settings({"permissions": {"deny": ["Bash(rm -rf:*)"]}})
    settings.apply(settings.plan(choices(deny_secrets=True)))
    current = read_json(file)
    permissions = current["permissions"]
    assert isinstance(permissions, dict)
    permissions["deny"].append("WebFetch")
    file.write_text(json.dumps(current), encoding="utf-8")

    settings.undo()

    assert read_json(file) == {"permissions": {"deny": ["Bash(rm -rf:*)", "WebFetch"]}}


def test_undo_drops_keys_and_parents_coven_created():
    file = write_settings({"theme": "dark"})
    settings.apply(settings.plan(choices(deny_secrets=True)))

    settings.undo()

    assert read_json(file) == {"theme": "dark"}


def test_undo_skips_key_changed_since():
    file = write_settings({})
    settings.apply(settings.plan(choices(cross_session_accept=True, pm_cap=3)))
    changed = read_json(file)
    changed["crossSessionInbound"] = "ask"
    file.write_text(json.dumps(changed), encoding="utf-8")

    skipped = settings.undo()

    assert skipped == [f"{file}: crossSessionInbound: changed since coven wrote it, left as is"]
    assert read_json(file) == {"crossSessionInbound": "ask"}
    assert not config.config_file().exists()


def test_undo_with_nothing_recorded_does_nothing():
    assert settings.undo() == []


def test_second_setup_keeps_the_first_before_value():
    file = write_settings({"crossSessionInbound": "ask"})
    settings.apply(settings.plan(choices(cross_session_accept=True, pm_cap=3)))
    settings.apply(settings.plan(choices(cross_session_accept=True, pm_cap=4)))

    settings.undo()

    assert read_json(file) == {"crossSessionInbound": "ask"}
    assert not config.config_file().exists()


def test_concurrent_change_refused():
    file = write_settings({"crossSessionInbound": "ask"})
    changes = settings.plan(choices(cross_session_accept=True))
    file.write_text(json.dumps({"crossSessionInbound": "reject"}), encoding="utf-8")

    with pytest.raises(CovenError, match=r"changed since"):
        settings.apply(changes)

    assert read_json(file) == {"crossSessionInbound": "reject"}
    assert not owned_file().exists()


def test_pm_cap_below_one_refused():
    with pytest.raises(CovenError, match="pm_cap"):
        settings.plan(choices(pm_cap=0))


def test_describe_shows_file_key_before_and_after():
    file = write_settings({"crossSessionInbound": "ask"})
    change = settings_change(choices(cross_session_accept=True))

    assert settings.describe(change) == f'{file}: crossSessionInbound: "ask" -> "accept"'


def test_describe_marks_a_missing_before():
    change = settings_change(choices(cross_session_accept=True))

    assert change.before is MISSING
    assert settings.describe(change).endswith(': crossSessionInbound: (missing) -> "accept"')


def test_shim_is_written_executable_and_undone_when_untouched():
    settings.apply(settings.plan(choices(shim=True)))

    assert stat.S_IMODE(shim_file().stat().st_mode) == 0o755
    assert shim_file().read_text(encoding="utf-8").startswith("#!/bin/sh\n")
    assert settings.undo() == []
    assert not shim_file().exists()


def test_undo_leaves_a_shim_someone_edited():
    settings.apply(settings.plan(choices(shim=True)))
    shim_file().write_text("#!/bin/sh\necho mine\n", encoding="utf-8")

    skipped = settings.undo()

    assert skipped == [f"{shim_file()}: changed since coven wrote it, left as is"]
    assert shim_file().exists()


def at_root(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    monkeypatch.setattr(paths, "root", lambda: root)


def shim_changes() -> list[settings.Change]:
    return [c for c in settings.plan(choices(shim=True)) if c.file == shim_file()]


def write_shim(text: str) -> None:
    shim_file().parent.mkdir(parents=True, exist_ok=True)
    shim_file().write_text(text, encoding="utf-8")


def test_a_shim_written_from_a_plugin_cache_root_is_the_plugin_only_text(
    monkeypatch: pytest.MonkeyPatch,
):
    # A plugin-cache root is versioned and gets pruned: baking it in would point at nothing.
    at_root(monkeypatch, paths.installed_plugins_file().parent / "cache" / "coven" / "0.1.0")

    settings.apply(settings.plan(choices(shim=True)))

    assert shim_file().read_text(encoding="utf-8") == settings.SHIM_TEXT


def test_a_shim_written_from_a_clone_bakes_in_the_clone_once_and_quoted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    clone = tmp_path / "it's a clone"
    at_root(monkeypatch, clone)

    settings.apply(settings.plan(choices(shim=True)))

    text = shim_file().read_text(encoding="utf-8")
    assert text.count(f"\n  checkout={shlex.quote(str(clone))}\n") == 1
    assert shim_changes() == []


def test_todays_shim_text_is_replaced_not_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    write_shim(settings.SHIM_TEXT)
    at_root(monkeypatch, tmp_path / "clone")

    [change] = shim_changes()

    assert change.before == settings.SHIM_TEXT
    assert change.previously == (settings.SHIM_TEXT,)
    assert str(tmp_path / "clone") in str(change.after)


def test_a_shim_written_for_another_clone_is_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    at_root(monkeypatch, tmp_path / "old clone")
    settings.apply(settings.plan(choices(shim=True)))
    at_root(monkeypatch, tmp_path / "new clone")

    settings.apply(settings.plan(choices(shim=True)))

    new_text = shim_file().read_text(encoding="utf-8")
    assert str(tmp_path / "new clone") in new_text
    assert "old clone" not in new_text


def test_undo_removes_a_shim_that_replaced_todays_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    at_root(monkeypatch, paths.installed_plugins_file().parent / "cache" / "coven" / "0.1.0")
    settings.apply(settings.plan(choices(shim=True)))
    at_root(monkeypatch, tmp_path / "clone")
    settings.apply(settings.plan(choices(shim=True)))
    assert str(tmp_path / "clone") in shim_file().read_text(encoding="utf-8")

    assert settings.undo() == []

    assert not shim_file().exists()


def test_undo_removes_a_shim_still_holding_the_text_coven_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    write_shim(settings.SHIM_TEXT)
    at_root(monkeypatch, tmp_path / "clone")
    settings.apply(settings.plan(choices(shim=True)))
    write_shim(settings.SHIM_TEXT)

    assert settings.undo() == []

    assert not shim_file().exists()


def test_a_foreign_file_at_the_shim_path_is_not_overwritten():
    shim_file().parent.mkdir(parents=True)
    foreign = "#!/bin/sh\necho other tool\n"
    shim_file().write_text(foreign, encoding="utf-8")

    with pytest.raises(CovenError, match="not coven's shim"):
        settings.plan(choices(shim=True))

    assert shim_file().read_text(encoding="utf-8") == foreign
    assert os.access(shim_file(), os.R_OK)


def test_corrupt_owned_blocks_apply_before_any_write():
    owned_file().parent.mkdir(parents=True)
    owned_file().write_text("{not json")
    changes = settings.plan(choices(cross_session_accept=True))

    with pytest.raises(CovenError, match=r"owned\.json"):
        settings.apply(changes)

    assert not settings_file().exists()
    assert not config.config_file().exists()
    assert owned_file().read_text() == "{not json"


def test_corrupt_owned_blocks_undo_and_leaves_settings_alone():
    file = write_settings({"crossSessionInbound": "accept"})
    owned_file().parent.mkdir(parents=True)
    owned_file().write_text("{not json")

    with pytest.raises(CovenError, match=r"owned\.json"):
        settings.undo()

    assert read_json(file) == {"crossSessionInbound": "accept"}


def crash_replacing(monkeypatch: pytest.MonkeyPatch, target: Path, *, after_write: bool) -> None:
    """Make the rename onto `target` fail (or, with `after_write`, fail right after it worked)."""
    real = os.replace

    def crashing(source: str | os.PathLike[str], destination: str | os.PathLike[str]) -> None:
        if Path(destination) != target:
            real(source, destination)
            return
        if after_write:
            real(source, destination)
        raise OSError("crash at the write")

    monkeypatch.setattr(os, "replace", crashing)


def test_a_crash_right_after_the_write_leaves_a_record_undo_can_act_on(
    monkeypatch: pytest.MonkeyPatch,
):
    file = write_settings({"theme": "dark"})
    changes = settings.plan(choices(cross_session_accept=True))
    with monkeypatch.context() as crashing:
        crash_replacing(crashing, file.resolve(), after_write=True)
        with pytest.raises(OSError, match="crash"):
            settings.apply(changes)
    assert read_json(file)["crossSessionInbound"] == "accept"

    assert settings.undo() == []

    assert read_json(file) == {"theme": "dark"}


def test_a_crash_before_the_write_is_undone_without_complaint(monkeypatch: pytest.MonkeyPatch):
    file = write_settings({"theme": "dark"})
    changes = settings.plan(choices(cross_session_accept=True))
    with monkeypatch.context() as crashing:
        crash_replacing(crashing, file.resolve(), after_write=False)
        with pytest.raises(OSError, match="crash"):
            settings.apply(changes)
    assert owned_file().exists()
    assert read_json(file) == {"theme": "dark"}

    assert settings.undo() == []

    assert read_json(file) == {"theme": "dark"}
    assert not owned_file().exists()


def test_a_crash_before_a_rerun_write_still_undoes_to_the_original(
    monkeypatch: pytest.MonkeyPatch,
):
    settings.apply(settings.plan(choices(pm_cap=3)))
    changes = settings.plan(choices(pm_cap=5))
    with monkeypatch.context() as crashing:
        crash_replacing(crashing, config.config_file().resolve(), after_write=False)
        with pytest.raises(OSError, match="crash"):
            settings.apply(changes)
    assert config.load_config().pm_cap == 3

    assert settings.undo() == []

    assert not config.config_file().exists()


def test_a_crash_after_a_rerun_write_still_undoes_to_the_original(
    monkeypatch: pytest.MonkeyPatch,
):
    file = write_settings({"crossSessionInbound": "ask"})
    settings.apply(settings.plan(choices(cross_session_accept=True)))
    file.write_text(json.dumps({"crossSessionInbound": "accept"}))
    changes = [settings.Change(file, ("crossSessionInbound",), "accept", "always")]
    with monkeypatch.context() as crashing:
        crash_replacing(crashing, file.resolve(), after_write=True)
        with pytest.raises(OSError, match="crash"):
            settings.apply(changes)

    assert settings.undo() == []

    assert read_json(file) == {"crossSessionInbound": "ask"}


def test_undo_removes_files_coven_created_when_they_are_empty():
    settings.apply(settings.plan(choices(cross_session_accept=True)))

    settings.undo()

    assert not settings_file().exists()
    assert not config.config_file().exists()


def test_undo_keeps_a_created_file_that_holds_other_keys():
    settings.apply(settings.plan(choices(cross_session_accept=True)))
    later = read_json(settings_file())
    later["theme"] = "dark"
    settings_file().write_text(json.dumps(later), encoding="utf-8")

    settings.undo()

    assert read_json(settings_file()) == {"theme": "dark"}


def test_undo_keeps_a_file_that_existed_even_when_it_ends_up_empty():
    file = write_settings({})
    settings.apply(settings.plan(choices(cross_session_accept=True)))

    settings.undo()

    assert read_json(file) == {}


def test_duplicate_keys_are_refused_naming_file_and_key():
    file = settings_file()
    file.parent.mkdir(parents=True)
    text = '{"theme": "dark", "theme": "light"}'
    file.write_text(text, encoding="utf-8")

    with pytest.raises(CovenError, match=r"settings\.json.*duplicate key.*theme"):
        settings.plan(choices(cross_session_accept=True))

    assert file.read_text(encoding="utf-8") == text


def test_lone_surrogate_is_a_coven_error_with_no_backup_and_no_record():
    file = settings_file()
    file.parent.mkdir(parents=True)
    file.write_text('{"note": "\\ud800"}', encoding="utf-8")
    changes = settings.plan(choices(cross_session_accept=True))

    with pytest.raises(CovenError, match=r"settings\.json.*UTF-8"):
        settings.apply(changes)

    assert not list(file.parent.glob("*.coven-backup-*"))
    assert not owned_file().exists()
    assert file.read_text(encoding="utf-8") == '{"note": "\\ud800"}'


def test_the_temp_file_is_synced_before_every_rename(monkeypatch: pytest.MonkeyPatch):
    events: list[str] = []
    real_fsync = os.fsync
    real_replace = os.replace

    def fsync_spy(descriptor: int) -> None:
        events.append("fsync")
        real_fsync(descriptor)

    def replace_spy(source: str | os.PathLike[str], destination: str | os.PathLike[str]) -> None:
        events.append("replace")
        real_replace(source, destination)

    monkeypatch.setattr(os, "fsync", fsync_spy)
    monkeypatch.setattr(os, "replace", replace_spy)

    settings.apply(settings.plan(choices(cross_session_accept=True)))

    assert events.count("replace") >= 2
    assert all(events[i - 1] == "fsync" for i, event in enumerate(events) if event == "replace")


def test_undo_removes_one_occurrence_per_entry_coven_added():
    file = write_settings({"permissions": {"deny": ["WebFetch"]}})
    settings.apply(settings.plan(choices(deny_secrets=True)))
    current = read_json(file)
    permissions = current["permissions"]
    assert isinstance(permissions, dict)
    permissions["deny"].append("Read(./.env*)")
    file.write_text(json.dumps(current), encoding="utf-8")

    settings.undo()

    assert read_json(file) == {"permissions": {"deny": ["WebFetch", "Read(./.env*)"]}}


def test_symlinked_settings_file_is_written_through_and_stays_a_link(tmp_path: Path):
    real = tmp_path / "dotfiles" / "settings.json"
    real.parent.mkdir()
    real.write_text('{"theme": "dark"}', encoding="utf-8")
    settings_file().parent.mkdir(parents=True)
    settings_file().symlink_to(real)

    settings.apply(settings.plan(choices(cross_session_accept=True)))

    assert settings_file().is_symlink()
    assert read_json(real) == {"theme": "dark", "crossSessionInbound": "accept"}
    settings.undo()
    assert settings_file().is_symlink()
    assert read_json(real) == {"theme": "dark"}


def test_invalid_utf8_settings_is_a_coven_error_naming_the_file():
    file = settings_file()
    file.parent.mkdir(parents=True)
    file.write_bytes(b'{"theme": "\xff"}')

    with pytest.raises(CovenError, match=r"settings\.json"):
        settings.plan(choices(cross_session_accept=True))

    assert file.read_bytes() == b'{"theme": "\xff"}'


def test_an_empty_settings_file_counts_as_an_empty_object():
    file = settings_file()
    file.parent.mkdir(parents=True)
    file.write_text("", encoding="utf-8")

    settings.apply(settings.plan(choices(cross_session_accept=True)))

    assert read_json(file) == {"crossSessionInbound": "accept"}


def test_preview_undo_lists_restores_and_skips_without_writing():
    file = write_settings({"crossSessionInbound": "ask"})
    settings.apply(settings.plan(choices(cross_session_accept=True, pm_cap=3)))
    config.config_file().write_text(json.dumps({"pm_cap": 5, "memory_path": "/store"}))

    restores, skipped = settings.preview_undo()

    assert f'{file}: crossSessionInbound: "accept" -> "ask"' in restores
    assert skipped == [f"{config.config_file()}: pm_cap: changed since coven wrote it, left as is"]
    assert read_json(file) == {"crossSessionInbound": "accept"}


def test_an_existing_shim_that_differs_from_covens_is_refused_even_with_the_marker():
    shim_file().parent.mkdir(parents=True)
    shim_file().write_text("#!/bin/sh\n# coven shim: an old one\nexit 0\n", encoding="utf-8")

    with pytest.raises(CovenError, match=str(shim_file())):
        settings.plan(choices(shim=True))


def test_an_identical_existing_shim_needs_no_change():
    settings.apply(settings.plan(choices(shim=True)))

    assert [c for c in settings.plan(choices(shim=True)) if not c.key] == []
