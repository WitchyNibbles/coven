import json
from pathlib import Path

import pytest
from fakes import SUPERPOWERS_KEY, install_superpowers

from coven.opencode import superpowers


def test_the_skills_are_the_eight_the_routing_and_the_sdd_skill_name():
    assert superpowers.SKILLS == (
        "brainstorming",
        "writing-plans",
        "test-driven-development",
        "systematic-debugging",
        "verification-before-completion",
        "using-git-worktrees",
        "finishing-a-development-branch",
        "executing-plans",
    )


@pytest.mark.parametrize("as_list", [True, False])
def test_find_returns_the_skills_folder_of_the_install(claude_plugins: Path, as_list: bool):
    skills = install_superpowers(claude_plugins, as_list=as_list)
    assert superpowers.find() == skills


def test_find_follows_the_plugin_cache_variable_not_the_real_claude_folder(
    claude_plugins: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    install_superpowers(claude_plugins)
    monkeypatch.setenv("CLAUDE_CODE_PLUGIN_CACHE_DIR", str(tmp_path / "elsewhere"))
    assert superpowers.find() is None


def test_find_gives_none_without_a_file(claude_plugins: Path):
    assert superpowers.find() is None


def test_find_gives_none_for_bad_json(claude_plugins: Path):
    (claude_plugins / "installed_plugins.json").write_text("{")
    assert superpowers.find() is None


@pytest.mark.parametrize(
    "data",
    [
        [],
        {"plugins": []},
        {"plugins": {"other@x": [{"installPath": "/x"}]}},
        {"plugins": {SUPERPOWERS_KEY: []}},
        {"plugins": {SUPERPOWERS_KEY: [{"version": "1"}]}},
        {"plugins": {SUPERPOWERS_KEY: {"installPath": ""}}},
        {"plugins": {SUPERPOWERS_KEY: "text"}},
    ],
)
def test_find_gives_none_without_an_install_path(claude_plugins: Path, data: object):
    (claude_plugins / "installed_plugins.json").write_text(json.dumps(data))
    assert superpowers.find() is None


def test_find_skips_an_install_whose_folder_is_gone(claude_plugins: Path):
    skills = install_superpowers(claude_plugins)
    path = claude_plugins / "installed_plugins.json"
    data = json.loads(path.read_text())
    stale = {"installPath": str(claude_plugins / "gone")}
    data["plugins"]["superpowers@other"] = [stale]
    data["plugins"][SUPERPOWERS_KEY].insert(0, stale)
    path.write_text(json.dumps(data))
    assert superpowers.find() == skills


def test_copy_renames_skill_names_in_markdown_only(claude_plugins: Path, tmp_path: Path):
    source = install_superpowers(claude_plugins)
    target = tmp_path / "skills"
    target.mkdir()
    assert superpowers.copy(source, target) == []
    folder = target / "superpowers-brainstorming"
    assert (folder / "SKILL.md").read_text() == (
        "---\nname: brainstorming\n---\nNext, use superpowers-writing-plans and `Read`.\n"
    )
    assert (folder / "notes.txt").read_text() == "kept as is: superpowers:writing-plans\n"
    assert sorted(path.name for path in target.iterdir()) == sorted(
        f"superpowers-{name}" for name in superpowers.SKILLS
    )


def test_copy_returns_the_names_it_could_not_find(claude_plugins: Path, tmp_path: Path):
    source = install_superpowers(claude_plugins, ("brainstorming", "writing-plans"))
    target = tmp_path / "skills"
    target.mkdir()
    assert superpowers.copy(source, target) == [
        "test-driven-development",
        "systematic-debugging",
        "verification-before-completion",
        "using-git-worktrees",
        "finishing-a-development-branch",
        "executing-plans",
    ]
    assert sorted(path.name for path in target.iterdir()) == [
        "superpowers-brainstorming",
        "superpowers-writing-plans",
    ]


def test_copy_with_no_install_returns_every_name_and_writes_nothing(tmp_path: Path):
    target = tmp_path / "skills"
    target.mkdir()
    assert superpowers.copy(None, target) == list(superpowers.SKILLS)
    assert list(target.iterdir()) == []
