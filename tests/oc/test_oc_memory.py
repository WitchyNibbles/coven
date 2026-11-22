import json
from pathlib import Path

import pytest
from support import REPO, load_module

from coven import paths
from coven.errors import CovenError
from coven.opencode import memory

NO_STORE = "No shared memory store on this machine (run `coven setup`)."


def hook_head() -> tuple[str, ...]:
    module = load_module(REPO / "hooks" / "memory_context.py", "coven_test_memory_context")
    return module.HEAD


def test_the_pointer_repeats_the_hook_header_with_the_opencode_skill_name(tmp_path: Path):
    tail = memory.agents_tail(tmp_path / "store", [])
    wanted = "\n".join(hook_head()).replace("`coven:shared-memory`", "`coven-shared-memory`")
    assert tail.startswith(wanted.format(store=tmp_path / "store") + "\n")


def test_the_pointer_tells_where_the_indexes_are(tmp_path: Path):
    tail = memory.agents_tail(tmp_path / "store", [])
    assert "`user/INDEX.md` and `projects/<key>/INDEX.md`" in tail
    assert "main checkout (a worktree uses its parent repo)" in tail
    assert "superpowers" not in tail


def test_without_a_store_the_tail_is_one_line():
    assert memory.agents_tail(None, []) == NO_STORE + "\n"


def test_missing_skills_add_the_fixed_note_naming_them(tmp_path: Path):
    tail = memory.agents_tail(None, ["writing-plans", "brainstorming"])
    first, note = tail.rstrip("\n").split("\n\n")
    assert first == NO_STORE
    assert note == (
        "Superpowers skills not found on this machine: superpowers-writing-plans, "
        "superpowers-brainstorming. Steps that tell you to load one are skipped until "
        "`coven opencode setup` finds them."
    )


def test_store_path_is_none_when_the_folder_is_absent():
    assert memory.store_path() is None


def test_store_path_is_the_default_folder_when_it_exists(tmp_path: Path):
    default = Path.home() / "coven-memory"
    default.mkdir()
    assert memory.store_path() == default


def test_store_path_follows_the_coven_config(tmp_path: Path):
    store = tmp_path / "elsewhere"
    store.mkdir()
    config = paths.config_dir() / "config.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({"memory_path": str(store)}))
    assert memory.store_path() == store


def test_a_broken_config_raises_instead_of_falling_back():
    config = paths.config_dir() / "config.json"
    config.parent.mkdir(parents=True)
    config.write_text("{")
    with pytest.raises(CovenError, match="not valid JSON"):
        memory.store_path()
