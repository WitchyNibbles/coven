import os
from pathlib import Path
from types import ModuleType

import pytest
from memory_support import Lab, load_hook

from coven.memory import entries
from coven.memory.entries import Entry

ORIGIN = "origin: https://example.com/team/app.git"


@pytest.fixture(scope="module")
def hook() -> ModuleType:
    return load_hook("memory_pre_commit")


@pytest.fixture(scope="module")
def context() -> ModuleType:
    return load_hook("memory_context")


def test_parse_frontmatter_reads_flat_fields_and_one_nested_level() -> None:
    text = "---\nname: Tabs\ndescription: 'Use tabs'\nmetadata:\n  type: user\n---\nbody: not me\n"
    assert entries.parse_frontmatter(text) == {
        "name": "Tabs",
        "description": "Use tabs",
        "metadata.type": "user",
    }


@pytest.mark.parametrize(
    "text",
    [
        "no frontmatter\n",
        "",
        "---\nname: never closed\n",
    ],
)
def test_parse_frontmatter_without_a_closed_block_is_empty(text: str) -> None:
    assert entries.parse_frontmatter(text) == {}


def test_parse_frontmatter_skips_block_scalars_and_handles_crlf() -> None:
    text = "---\r\nname: A\r\ndescription: >\r\n  folded\r\n  text\r\nmetadata:\r\n  type: project\r\n---\r\n"
    assert entries.parse_frontmatter(text) == {"name": "A", "metadata.type": "project"}


def test_index_line_format_and_order() -> None:
    text = entries.index_text([Entry("b.md", "Beta", "second"), Entry("a.md", "Alpha", "first")])
    lines = text.split("\n")
    assert lines[0] == entries.HEADER
    assert lines[1:] == ["- [Alpha](a.md) — first", "- [Beta](b.md) — second", ""]


def test_description_is_cut_at_150_characters() -> None:
    text = entries.index_text([Entry("a.md", "A", "word " * 60)])
    description = text.split("\n")[1].split(" — ", 1)[1]
    assert len(description) == 150
    assert description.endswith("word…")
    exact = entries.index_text([Entry("a.md", "A", "x" * 150)]).split("\n")[1]
    assert exact.endswith("x" * 150)


def test_entry_without_name_or_description_says_so() -> None:
    text = entries.index_text([Entry("a.md", None, None), Entry("b.md", "B", None)])
    assert "- [a.md](a.md) — (no description)" in text
    assert "- [b.md](b.md) — (no description)" in text


def test_brackets_and_parentheses_are_escaped() -> None:
    line = entries.index_text([Entry("a(1).md", "[x] y", "d")]).split("\n")[1]
    assert line == "- [\\[x\\] y](a\\(1\\).md) — d"


def test_origin_line_is_read_from_the_header_only() -> None:
    odd = "origin:\t https://example.com/t/a.git  "
    assert entries.origin_line(f"{entries.HEADER}\n{odd}\n- [A](a.md) — d\n") == odd
    assert entries.origin_line(f"{entries.HEADER}\n- [A](a.md) — d\n{ORIGIN}\n") is None
    assert entries.origin_line("origin: two words\n") is None
    assert entries.origin_line("") is None


@pytest.mark.parametrize("line", [ORIGIN, "origin:\t https://example.com/t/a.git  "])
def test_regenerated_index_keeps_the_origin_line_memory_context_reads(
    tmp_path: Path, context: ModuleType, line: str
) -> None:
    old = f"{entries.HEADER}\n{line}\n- [Old](old.md) — gone\n"
    text = entries.index_text([Entry("new.md", "New", "fresh")], entries.origin_line(old))
    index = tmp_path / "INDEX.md"
    index.write_text(text, encoding="utf-8")
    lines = context.read_index(index)
    assert line in lines
    assert context.recorded_origin(lines) == line.split(":", 1)[1].strip()
    assert context.entries(lines) == ["- [New](new.md) — fresh"]


def test_rebuild_indexes_writes_scopes_and_keeps_origin(tmp_path: Path, lab: Lab) -> None:
    lab.write(tmp_path, "user/a.md", "A", "first")
    lab.write(tmp_path, "projects/app/b.md", "B", "second")
    (tmp_path / "projects/app/INDEX.md").write_text(f"{entries.HEADER}\n{ORIGIN}\n")
    (tmp_path / "projects/empty").mkdir(parents=True)
    (tmp_path / "handoffs/app").mkdir(parents=True)
    (tmp_path / "handoffs/app/2026-01-01-0900.md").write_text("handoff\n")
    changed = entries.rebuild_indexes(tmp_path)
    assert sorted(changed) == ["projects/app/INDEX.md", "user/INDEX.md"]
    assert (tmp_path / "user/INDEX.md").read_text() == entries.index_text(
        [Entry("a.md", "A", "first")]
    )
    assert (tmp_path / "projects/app/INDEX.md").read_text() == entries.index_text(
        [Entry("b.md", "B", "second")], ORIGIN
    )
    assert not (tmp_path / "projects/empty/INDEX.md").exists()
    assert not (tmp_path / "handoffs/app/INDEX.md").exists()
    assert entries.rebuild_indexes(tmp_path) == []


def test_rebuild_indexes_covers_systems_but_not_approvals(tmp_path: Path, lab: Lab) -> None:
    lab.write(tmp_path, "systems/orders-db.md", "orders-db", "the orders database")
    (tmp_path / "systems" / "guard.json").write_text("{}\n")
    (tmp_path / "approvals").mkdir()
    (tmp_path / "approvals" / "step-1.json").write_text("{}\n")
    (tmp_path / "approvals" / "notes.md").write_text(
        "---\nname: N\ndescription: not an entry\n---\n"
    )
    assert entries.rebuild_indexes(tmp_path) == ["systems/INDEX.md"]
    assert (tmp_path / "systems/INDEX.md").read_text() == entries.index_text(
        [Entry("orders-db.md", "orders-db", "the orders database")]
    )
    assert not (tmp_path / "approvals/INDEX.md").exists()
    assert entries.rebuild_indexes(tmp_path) == []


def test_rebuild_indexes_drops_an_index_with_no_entries_and_no_origin(
    tmp_path: Path, lab: Lab
) -> None:
    lab.write(tmp_path, "user/a.md", "A", "first")
    entries.rebuild_indexes(tmp_path)
    (tmp_path / "user/a.md").unlink()
    assert entries.rebuild_indexes(tmp_path) == ["user/INDEX.md"]
    assert not (tmp_path / "user/INDEX.md").exists()


def test_rebuild_indexes_repairs_duplicate_lines(tmp_path: Path, lab: Lab) -> None:
    lab.write(tmp_path, "user/a.md", "A", "first")
    entries.rebuild_indexes(tmp_path)
    index = tmp_path / "user/INDEX.md"
    line = "- [A](a.md) — first\n"
    index.write_text(index.read_text() + line + line)
    assert entries.rebuild_indexes(tmp_path) == ["user/INDEX.md"]
    assert index.read_text().count(line) == 1


SAMPLES = [
    "---\nname: Tabs\ndescription: Use tabs\nmetadata:\n  type: user\n---\n",
    "---\nname: \"Quoted: yes\"\ndescription: 'it''s'\n---\n",
    "---\nname: A\ndescription: >\n  folded\n---\n",
    "---\nname: [odd] (name)\ndescription: " + "long " * 80 + "\n---\n",
    "---\nname: Only a name\n---\n",
    "---\r\nname: Crlf\r\ndescription: text\r\n---\r\n",
    "---\nname: Unclosed\ndescription: x\n",
    "plain text\n",
    "",
]


@pytest.mark.parametrize("text", SAMPLES)
def test_package_and_hook_parse_frontmatter_alike(hook: ModuleType, text: str) -> None:
    assert hook.parse_frontmatter(text) == entries.parse_frontmatter(text)
    assert tuple(hook.read_entry("f.md", text)) == tuple(entries.read_entry("f.md", text))


def test_package_and_hook_produce_the_same_index_text(hook: ModuleType) -> None:
    files = {f"f{n}.md": text for n, text in enumerate(SAMPLES)}
    mine = [entries.read_entry(file, text) for file, text in files.items()]
    theirs = [hook.read_entry(file, text) for file, text in files.items()]
    for origin in (None, ORIGIN):
        assert hook.index_text(theirs, origin) == entries.index_text(mine, origin)
    assert hook.HEADER == entries.HEADER
    assert hook.DESC_MAX == entries.DESC_MAX
    assert hook.ORIGIN_LINE.pattern == entries.ORIGIN_LINE.pattern


def test_hook_indexes_a_staged_tree_like_rebuild_indexes(
    tmp_path: Path,
    hook: ModuleType,
    lab: Lab,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "tree"
    repo.mkdir()
    lab.git(repo, "init", "-q")
    lab.write(repo, "user/a.md", "A", "first")
    lab.write(repo, "projects/app/b.md", "[B]", "second " * 40)
    lab.write(repo, "systems/orders-db.md", "orders-db", "the orders database")
    (repo / "systems/guard.json").write_text("{}\n")
    (repo / "approvals").mkdir()
    (repo / "approvals/step.json").write_text("{}\n")
    (repo / "projects/app/c.md").write_text("no frontmatter\n")
    (repo / "projects/app/INDEX.md").write_text(f"{entries.HEADER}\n{ORIGIN}\n")
    lab.git(repo, "add", "-A")
    monkeypatch.chdir(repo)
    hook.update_indexes(hook.staged_paths())
    written = {
        p: (repo / p).read_text()
        for p in ("user/INDEX.md", "projects/app/INDEX.md", "systems/INDEX.md")
    }
    assert entries.rebuild_indexes(repo) == []
    assert written["projects/app/INDEX.md"].split("\n")[1] == ORIGIN
    assert os.path.exists(repo / "user/INDEX.md")
    assert "- [orders-db](orders-db.md) — the orders database" in written["systems/INDEX.md"]
    assert not (repo / "approvals/INDEX.md").exists()
