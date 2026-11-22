import json
import os
from pathlib import Path

import pytest
from support import FIXTURES, REPO

from coven import paths
from coven.opencode import generate
from coven.opencode.profile import Layout, OpencodeError, make_profile

GOLDEN = FIXTURES / "opencode"
URL = "http://127.0.0.1:4000/v1"
LITELLM = make_profile("litellm", URL, "GATEWAY_KEY", ["lead=big", "explorer=small", "title=small"])
OPENCODE = make_profile("opencode", None, None, ["lead=big"])


def rendered(profile, tmp_path: Path) -> str:
    layout, coven_root = Layout(tmp_path / "profile"), tmp_path / "coven"
    text = generate.render_config(profile, layout, coven_root)
    return text.replace(str(layout.root), "<P>").replace(str(coven_root), "<COVEN_ROOT>")


def test_litellm_config_matches_the_golden(tmp_path: Path):
    assert rendered(LITELLM, tmp_path) == (GOLDEN / "litellm.jsonc").read_text()


def test_opencode_config_matches_the_golden(tmp_path: Path):
    assert rendered(OPENCODE, tmp_path) == (GOLDEN / "opencode.jsonc").read_text()


def test_config_is_plain_json_with_the_documented_key_order(tmp_path: Path):
    data = json.loads(rendered(LITELLM, tmp_path))
    assert list(data) == ["update", "model", "agents", "providers", "plugins"]
    assert list(data["agents"]) == [
        "explorer",
        "implementer",
        "reviewer",
        "final-reviewer",
        "title",
    ]
    assert "providers" not in json.loads(rendered(OPENCODE, tmp_path))


def test_plugin_options(tmp_path: Path):
    assert generate.plugin_options(LITELLM, tmp_path) == {
        "covenRoot": str(tmp_path),
        "toolChoiceAuto": ["litellm"],
        "guardTimeoutMs": 5000,
    }
    assert generate.plugin_options(OPENCODE, tmp_path)["toolChoiceAuto"] == []
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
