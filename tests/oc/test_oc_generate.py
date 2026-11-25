import json
import os
from pathlib import Path

import pytest
from fakes import install_superpowers
from support import FIXTURES, REPO

from coven import paths
from coven.errors import CovenError
from coven.opencode import generate, superpowers
from coven.opencode.content import CONTENT
from coven.opencode.profile import Layout, OpencodeError, make_profile

GOLDEN = FIXTURES / "opencode"
URL = "http://127.0.0.1:4000/v1"
LITELLM = make_profile("litellm", URL, "GATEWAY_KEY", ["lead=big", "explorer=small", "title=small"])
OPENCODE = make_profile("opencode", None, None, ["lead=big"])


def rendered(profile, tmp_path: Path, store: Path | None = None) -> str:
    layout, coven_root = Layout(tmp_path / "profile"), tmp_path / "coven"
    text = generate.render_config(profile, layout, coven_root, store)
    text = text.replace(str(layout.root), "<P>").replace(str(coven_root), "<COVEN_ROOT>")
    return text.replace(str(store), "<STORE>") if store else text


def test_litellm_config_matches_the_golden(tmp_path: Path):
    assert rendered(LITELLM, tmp_path, tmp_path / "store") == (GOLDEN / "litellm.jsonc").read_text()


def test_opencode_config_matches_the_golden(tmp_path: Path):
    assert rendered(OPENCODE, tmp_path) == (GOLDEN / "opencode.jsonc").read_text()


def test_config_is_plain_json_with_the_documented_key_order(tmp_path: Path):
    data = json.loads(rendered(LITELLM, tmp_path))
    assert list(data) == [
        "update",
        "model",
        "agents",
        "providers",
        "plugins",
        "experimental",
        "permissions",
    ]
    assert list(data["agents"]) == [
        "explorer",
        "implementer",
        "reviewer",
        "final-reviewer",
        "title",
    ]
    assert "providers" not in json.loads(rendered(OPENCODE, tmp_path))


def test_permissions_hold_the_secret_rules_then_the_skill_and_store_allows(tmp_path: Path):
    layout, store = Layout(tmp_path / "profile"), tmp_path / "store"
    data = generate.config_data(OPENCODE, layout, tmp_path / "coven", store)
    assert data["experimental"] == {"todo": True}
    assert data["permissions"] == [
        {"action": "read", "resource": "*.env", "effect": "deny"},
        {"action": "read", "resource": "*.env.*", "effect": "deny"},
        {"action": "read", "resource": "*.env.example", "effect": "allow"},
        {"action": "edit", "resource": "*.env", "effect": "deny"},
        {"action": "edit", "resource": "*.env.*", "effect": "deny"},
        {
            "action": "external_directory",
            "resource": f"{layout.config_dir}/skills/*",
            "effect": "allow",
        },
        {"action": "external_directory", "resource": f"{store}/*", "effect": "allow"},
        {"action": "edit", "resource": f"{layout.config_dir}/skills/*", "effect": "deny"},
        {"action": "shell", "resource": "*", "effect": "ask"},
        {"action": "webfetch", "resource": "*", "effect": "ask"},
    ]


@pytest.mark.parametrize("profile", [LITELLM, OPENCODE], ids=["litellm", "opencode"])
@pytest.mark.parametrize("action", ["shell", "webfetch"])
def test_shell_and_webfetch_ask_last_so_no_agent_rule_re_allows_them(
    profile, action: str, tmp_path: Path
):
    data = generate.config_data(profile, Layout(tmp_path / "profile"), tmp_path / "coven")
    last = [rule for rule in data["permissions"] if rule["action"] == action][-1]
    assert last == {"action": action, "resource": "*", "effect": "ask"}


def test_without_a_store_there_is_no_store_allow(tmp_path: Path):
    data = generate.config_data(OPENCODE, Layout(tmp_path / "profile"), tmp_path / "coven")
    rules = data["permissions"]
    assert len(rules) == 9
    assert [rule["action"] for rule in rules[5:7]] == ["external_directory", "edit"]
    assert all(rule["resource"].endswith("/config/opencode/skills/*") for rule in rules[5:7])


def test_plugin_options(tmp_path: Path):
    assert generate.plugin_options(LITELLM, tmp_path) == {
        "covenRoot": str(tmp_path),
        "keyEnv": "GATEWAY_KEY",
        "toolChoiceAuto": ["litellm"],
        "guardTimeoutMs": 5000,
    }
    assert generate.plugin_options(OPENCODE, tmp_path)["toolChoiceAuto"] == []
    assert generate.plugin_options(OPENCODE, tmp_path)["keyEnv"] == "OPENCODE_API_KEY"
    assert generate.GUARD_TIMEOUT_MS == 5000


def test_plugin_source_is_the_checkout_folder():
    assert generate.plugin_source() == paths.root() / "opencode" / "src"
    assert (generate.plugin_source() / "index.ts").is_file()


def test_the_key_value_never_reaches_the_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("GATEWAY_KEY", "sk-test-value")
    assert "sk-test-value" not in rendered(LITELLM, tmp_path)


# write_profile

PLUGIN_NAMES = ["index.ts", "join/wait.ts"]


@pytest.fixture
def source(tmp_path: Path) -> Path:
    folder = tmp_path / "src"
    (folder / "join").mkdir(parents=True)
    for name in PLUGIN_NAMES:
        (folder / name).write_text(f"// {name}\n")
    (folder / "notes.md").write_text("not copied")
    (folder / "node_modules").mkdir()
    (folder / "node_modules" / "dep.js").write_text("not copied")
    (folder / "node_modules" / "x.ts").write_text("not copied")
    (folder / "join" / "node_modules").mkdir()
    (folder / "join" / "node_modules" / "y.ts").write_text("not copied")
    return folder


def written(tmp_path: Path, profile=LITELLM, source: Path | None = None) -> Layout:
    layout = Layout(tmp_path / "profile")
    generate.write_profile(profile, layout, tmp_path / "coven", source or tmp_path / "src")
    return layout


def tree(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): path.read_text()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def leftovers(layout: Layout) -> list[str]:
    return sorted(
        str(path.relative_to(layout.root))
        for path in layout.root.rglob("*")
        if path.name.startswith(".tmp-") or path.name.endswith(".old")
    )


def test_first_write_on_an_empty_profile(tmp_path: Path, source: Path):
    layout = written(tmp_path)
    config = (layout.config_dir / "opencode.jsonc").read_text()
    assert config == generate.render_config(LITELLM, layout, tmp_path / "coven")
    assert tree(layout.plugin_dir) == {name: f"// {name}\n" for name in PLUGIN_NAMES}
    assert json.loads(layout.profile_file.read_text())["preset"] == "litellm"
    for folder in (layout.data, layout.cache, layout.state, layout.home, layout.tmp):
        assert folder.is_dir()
    assert leftovers(layout) == []


def test_rewrite_replaces_config_and_plugin(tmp_path: Path, source: Path):
    layout = written(tmp_path)
    (layout.plugin_dir / "stale.ts").write_text("old")
    (layout.config_dir / "extra.txt").write_text("old")
    again = written(tmp_path, OPENCODE)
    assert again == layout
    assert tree(layout.plugin_dir) == {name: f"// {name}\n" for name in PLUGIN_NAMES}
    assert not (layout.config_dir / "extra.txt").exists()
    assert '"opencode/big"' in (layout.config_dir / "opencode.jsonc").read_text()
    assert leftovers(layout) == []


def test_a_missing_plugin_source_changes_nothing(tmp_path: Path, source: Path):
    layout = written(tmp_path)
    before = tree(layout.root)
    with pytest.raises(OpencodeError, match="nowhere"):
        written(tmp_path, OPENCODE, source=tmp_path / "nowhere")
    assert tree(layout.root) == before
    assert leftovers(layout) == []


def test_a_failure_in_the_second_swap_keeps_the_old_config_and_plugin(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, source: Path
):
    layout = written(tmp_path)
    before = tree(layout.root)
    real_rename = Path.rename

    def fail_on_plugin(self: Path, target: str | os.PathLike[str]) -> Path:
        if self.name == "plugin-coven":
            raise OSError("disk says no")
        return real_rename(self, target)

    monkeypatch.setattr(Path, "rename", fail_on_plugin)
    with pytest.raises(OSError, match="disk says no"):
        written(tmp_path, OPENCODE)
    monkeypatch.undo()
    assert tree(layout.root) == before
    assert leftovers(layout) == []


def test_a_failure_in_the_profile_save_rolls_both_swaps_back(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, source: Path
):
    layout = written(tmp_path)
    before = tree(layout.root)

    def broken_save(*_: object) -> None:
        raise OSError("no space")

    monkeypatch.setattr(generate, "save_profile", broken_save)
    with pytest.raises(OSError, match="no space"):
        written(tmp_path, OPENCODE)
    monkeypatch.undo()
    assert tree(layout.root) == before
    assert leftovers(layout) == []


def test_a_failed_first_run_leaves_no_half_profile(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, source: Path
):
    def broken_save(*_: object) -> None:
        raise OSError("no space")

    monkeypatch.setattr(generate, "save_profile", broken_save)
    layout = Layout(tmp_path / "profile")
    with pytest.raises(OSError, match="no space"):
        written(tmp_path)
    assert not layout.config_dir.exists()
    assert not layout.plugin_dir.exists()
    assert leftovers(layout) == []


def test_a_stale_old_folder_from_a_crashed_run_does_not_break_the_next_run(
    tmp_path: Path, source: Path
):
    layout = written(tmp_path)
    for target in (layout.config_dir, layout.plugin_dir):
        stale = target.with_name(target.name + ".old")
        stale.mkdir()
        (stale / "junk").write_text("from a crash")
    stale_tmp = layout.root / f".tmp-{os.getpid()}"
    stale_tmp.mkdir()
    written(tmp_path, OPENCODE)
    assert leftovers(layout) == []
    assert '"opencode/big"' in (layout.config_dir / "opencode.jsonc").read_text()


def test_the_real_plugin_source_copies_every_ts_file(tmp_path: Path):
    layout = written(tmp_path, source=REPO / "opencode" / "src")
    expected = sorted(
        str(path.relative_to(REPO / "opencode" / "src"))
        for path in (REPO / "opencode" / "src").rglob("*.ts")
    )
    assert sorted(tree(layout.plugin_dir)) == expected


# content, superpowers and the AGENTS.md tail

NO_STORE = "No shared memory store on this machine (run `coven memory init`)."
NOT_FOUND = "Superpowers skills not found"


def agents_text(layout: Layout) -> str:
    return (layout.config_dir / "AGENTS.md").read_text()


def test_the_config_dir_holds_the_content_and_the_tail(tmp_path: Path, source: Path):
    layout = written(tmp_path)
    names = {path.name for path in layout.config_dir.iterdir()}
    assert names == {"AGENTS.md", "agents", "commands", "skills", "opencode.jsonc"}
    for folder in ("agents", "commands"):
        assert tree(layout.config_dir / folder) == tree(CONTENT / folder)
    coven_skills = {
        name: text
        for name, text in tree(layout.config_dir / "skills").items()
        if not name.startswith("superpowers-")
    }
    assert coven_skills == tree(CONTENT / "skills")
    base = (CONTENT / "AGENTS.md").read_text()
    assert agents_text(layout).startswith(base)
    assert NO_STORE in agents_text(layout)[len(base) :]


def test_with_a_store_the_tail_points_at_it_and_the_config_allows_it(tmp_path: Path, source: Path):
    store = Path.home() / "coven-memory"
    store.mkdir()
    layout = written(tmp_path)
    assert f"Store: {store} (how to save: the `coven-shared-memory` skill)" in agents_text(layout)
    rules = json.loads((layout.config_dir / "opencode.jsonc").read_text())["permissions"]
    assert {"action": "external_directory", "resource": f"{store}/*", "effect": "allow"} in rules


def test_superpowers_are_copied_next_to_the_coven_skills(
    tmp_path: Path, source: Path, claude_plugins: Path
):
    install_superpowers(claude_plugins)
    layout = written(tmp_path)
    skills = {path.name for path in (layout.config_dir / "skills").iterdir()}
    assert {f"superpowers-{name}" for name in superpowers.SKILLS} <= skills
    assert {path.name for path in (CONTENT / "skills").iterdir()} <= skills
    assert NOT_FOUND not in agents_text(layout)


def test_write_profile_returns_the_missing_superpowers(
    tmp_path: Path, source: Path, claude_plugins: Path
):
    install_superpowers(claude_plugins, ("brainstorming",))
    layout = Layout(tmp_path / "profile")
    missing = generate.write_profile(LITELLM, layout, tmp_path / "coven", source)
    assert missing == [name for name in superpowers.SKILLS if name != "brainstorming"]
    assert "superpowers-writing-plans" in agents_text(layout)


def test_rerunning_after_superpowers_is_gone_leaves_no_copy(
    tmp_path: Path, source: Path, claude_plugins: Path
):
    install_superpowers(claude_plugins)
    layout = written(tmp_path)
    assert (layout.config_dir / "skills" / "superpowers-brainstorming").is_dir()
    (claude_plugins / "installed_plugins.json").unlink()
    written(tmp_path)
    assert not list((layout.config_dir / "skills").glob("superpowers-*"))
    assert NOT_FOUND in agents_text(layout)


def test_a_broken_coven_config_stops_setup_before_anything_changes(tmp_path: Path, source: Path):
    layout = written(tmp_path)
    before = tree(layout.root)
    config = paths.config_dir() / "config.json"
    config.parent.mkdir(parents=True)
    config.write_text("{")
    with pytest.raises(CovenError, match="not valid JSON"):
        written(tmp_path, OPENCODE)
    assert tree(layout.root) == before
    assert leftovers(layout) == []


def test_a_missing_content_folder_changes_nothing(tmp_path: Path, source: Path):
    layout = written(tmp_path)
    before = tree(layout.root)
    with pytest.raises(OpencodeError, match="nowhere"):
        generate.write_profile(
            OPENCODE, layout, tmp_path / "coven", source, content=tmp_path / "nowhere"
        )
    assert tree(layout.root) == before
    assert leftovers(layout) == []


def test_a_model_with_a_limit_carries_it_and_the_others_do_not(tmp_path: Path):
    limited = make_profile(
        "litellm",
        URL,
        "GATEWAY_KEY",
        ["lead=big", "explorer=small", "title=tiny"],
        ["big=100000:8000", "small=50000"],
    )
    models = json.loads(rendered(limited, tmp_path))["providers"]["litellm"]["models"]
    assert models == {
        "big": {"name": "big", "limit": {"context": 100000, "output": 8000}},
        "small": {"name": "small", "limit": {"context": 50000}},
        "tiny": {"name": "tiny"},
    }


def test_a_profile_without_limits_renders_plain_models(tmp_path: Path):
    models = json.loads(rendered(LITELLM, tmp_path))["providers"]["litellm"]["models"]
    assert models == {"big": {"name": "big"}, "small": {"name": "small"}}
