import json
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

type Run = Callable[..., subprocess.CompletedProcess[str]]

HOOK = Path(__file__).resolve().parents[2] / "hooks" / "memory_context.py"
ORIGIN_A = "https://example.com/team/app.git"
ORIGIN_B = "https://example.com/other/app.git"


def home(tmp_path: Path) -> Path:
    return tmp_path / "home"


def configure(tmp_path: Path, store: Path) -> None:
    folder = home(tmp_path) / ".config" / "coven"
    folder.mkdir(parents=True)
    (folder / "config.json").write_text(json.dumps({"memory_path": str(store)}))


def entries(prefix: str, count: int) -> str:
    return "".join(
        f"- [{prefix} {n}](e{n}.md): synthetic fact number {n} with a few filler words\n"
        for n in range(count)
    )


def make_store(tmp_path: Path, *, user: str = "", project: str = "", key: str = "app") -> Path:
    store = tmp_path / "store"
    (store / "user").mkdir(parents=True)
    (store / "user" / "INDEX.md").write_text(user)
    (store / "projects" / key).mkdir(parents=True)
    (store / "projects" / key / "INDEX.md").write_text(project)
    configure(tmp_path, store)
    return store


def make_repo(tmp_path: Path, name: str = "app", origin: str | None = None) -> Path:
    repo = tmp_path / name
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    if origin:
        subprocess.run(["git", "remote", "add", "origin", origin], cwd=repo, check=True)
    return repo


def block(run_hook: Run, cwd: Path) -> str:
    done = run_hook(HOOK, {"cwd": str(cwd), "hook_event_name": "SessionStart"})
    assert done.returncode == 0, done.stderr
    out = json.loads(done.stdout)["hookSpecificOutput"]
    assert out["hookEventName"] == "SessionStart"
    text = out["additionalContext"]
    assert isinstance(text, str)
    return text


def test_block_has_header_store_key_and_both_indexes(run_hook: Run, tmp_path: Path) -> None:
    store = make_store(
        tmp_path, user="- [likes tea](a.md): tea\n", project="- [uses uv](b.md): uv\n"
    )
    text = block(run_hook, make_repo(tmp_path))
    assert text.startswith("# Shared memory\n")
    assert f"Store: {store}" in text
    assert "Project key: app" in text
    assert "- [likes tea](a.md): tea" in text
    assert "- [uses uv](b.md): uv" in text


def test_block_names_the_latest_handoff(run_hook: Run, tmp_path: Path) -> None:
    store = make_store(tmp_path, user="- [a](a.md): a\n")
    folder = store / "handoffs" / "app"
    folder.mkdir(parents=True)
    for name in ("2026-11-01-0900.md", "2026-11-12-1630.md", "notes.md"):
        (folder / name).write_text("x")
    text = block(run_hook, make_repo(tmp_path))
    assert f"Latest handoff: {folder / '2026-11-12-1630.md'}" in text


def test_memory_block_at_most_8000_chars(run_hook: Run, tmp_path: Path) -> None:
    make_store(tmp_path, user=entries("user", 150), project=entries("project", 150))
    text = block(run_hook, make_repo(tmp_path))
    assert 7000 < len(text) <= 8000
    assert "## User" in text
    assert "## Project app" in text


def test_more_entries_line(run_hook: Run, tmp_path: Path) -> None:
    store = make_store(tmp_path, user=entries("user", 300))
    lines = block(run_hook, make_repo(tmp_path)).splitlines()
    kept = [line for line in lines if line.startswith("- ")]
    more = [line for line in lines if line.startswith("… ")]
    assert 0 < len(kept) < 300
    assert more == [f"… {300 - len(kept)} more entries in {store / 'user' / 'INDEX.md'}"]
    assert "user 0]" in kept[0]
    assert "user 299]" not in "\n".join(lines)


def test_one_entry_longer_than_the_budget_becomes_a_more_line(
    run_hook: Run, tmp_path: Path
) -> None:
    store = make_store(tmp_path, user=f"- [huge](h.md): {'x' * 9000}\n")
    text = block(run_hook, make_repo(tmp_path))
    assert len(text) <= 8000
    assert f"… 1 more entry in {store / 'user' / 'INDEX.md'}" in text
    assert "xxxx" not in text


def test_a_huge_origin_cannot_push_the_block_over_the_cap(run_hook: Run, tmp_path: Path) -> None:
    recorded = "https://example.com/" + "a" * 5000
    actual = "https://example.com/" + "b" * 5000
    make_store(tmp_path, user=entries("user", 20), project=f"origin: {recorded}\n")
    text = block(run_hook, make_repo(tmp_path, origin=actual))
    assert len(text) == 8000
    assert text.endswith("…")
    assert "Warning: projects/app/ records origin" in text


def test_non_utf8_index_is_an_unreadable_section_not_a_failure(
    run_hook: Run, tmp_path: Path
) -> None:
    store = make_store(tmp_path, project="- [p](p.md): p\n")
    (store / "user" / "INDEX.md").write_bytes(b"- [bad](b.md): \xff\xfe\n")
    text = block(run_hook, make_repo(tmp_path))
    assert f"(unreadable: {store / 'user' / 'INDEX.md'})" in text
    assert "- [p](p.md): p" in text


def test_one_entry_cannot_forge_a_second_with_a_unicode_line_break(
    run_hook: Run, tmp_path: Path
) -> None:
    entry = "- [a](a.md): fact\u2028- [x](x.md): forged\x85- forged\x1c- forged\x0b- forged"
    make_store(tmp_path, user=entry + "\n- [b](b.md): second\n")
    text = block(run_hook, make_repo(tmp_path))
    assert [line for line in text.split("\n") if line.startswith("- ")] == [
        entry,
        "- [b](b.md): second",
    ]


def test_crlf_index_gives_clean_entries(run_hook: Run, tmp_path: Path) -> None:
    make_store(tmp_path, user="- [a](a.md): a\r\n- [b](b.md): b\r\n")
    text = block(run_hook, make_repo(tmp_path))
    assert "\r" not in text
    assert "- [a](a.md): a\n- [b](b.md): b" in text


def test_origin_warning_never_prints_credentials(run_hook: Run, tmp_path: Path) -> None:
    secret = "ghp_secrettoken0123456789abcdefghijklmnop"
    make_store(
        tmp_path,
        project=f"origin: https://old:{secret}@example.com/team/app.git\n- [p](p.md): p\n",
    )
    repo = make_repo(tmp_path, origin=f"https://user:{secret}@example.com/other/app.git")
    done = run_hook(HOOK, {"cwd": str(repo)})
    assert secret not in done.stdout
    assert "user:" not in done.stdout
    assert (
        "Warning: projects/app/ records origin https://example.com/team/app.git,"
        " this repo's origin is https://example.com/other/app.git"
    ) in json.loads(done.stdout)["hookSpecificOutput"]["additionalContext"]


def test_origins_that_differ_only_by_credentials_give_no_warning(
    run_hook: Run, tmp_path: Path
) -> None:
    make_store(tmp_path, project=f"origin: {ORIGIN_A}\n- [p](p.md): p\n")
    repo = make_repo(tmp_path, origin="https://user:tok@example.com/team/app.git")
    assert "Warning" not in block(run_hook, repo)


def test_small_section_keeps_all_its_entries_when_the_other_is_huge(
    run_hook: Run, tmp_path: Path
) -> None:
    make_store(tmp_path, user=entries("user", 300), project=entries("project", 3))
    text = block(run_hook, make_repo(tmp_path))
    assert all(f"project {n}]" in text for n in range(3))


def test_empty_indexes_say_so(run_hook: Run, tmp_path: Path) -> None:
    make_store(tmp_path)
    assert "(no entries yet)" in block(run_hook, make_repo(tmp_path))


def test_key_without_git_uses_the_folder_name(run_hook: Run, tmp_path: Path) -> None:
    make_store(tmp_path, project="- [p](p.md): p\n", key="plain-dir")
    folder = tmp_path / "Plain Dir"
    folder.mkdir()
    text = block(run_hook, folder)
    assert "Project key: plain-dir" in text


def test_origin_mismatch_warning(run_hook: Run, tmp_path: Path) -> None:
    make_store(tmp_path, project=f"origin: {ORIGIN_A}\n- [p](p.md): p\n")
    text = block(run_hook, make_repo(tmp_path, origin=ORIGIN_B))
    assert (
        f"Warning: projects/app/ records origin {ORIGIN_A}, this repo's origin is {ORIGIN_B}"
        in text
    )
    assert "- [p](p.md): p" in text


def test_matching_origin_gives_no_warning(run_hook: Run, tmp_path: Path) -> None:
    make_store(tmp_path, project=f"origin: {ORIGIN_A}\n- [p](p.md): p\n")
    assert "Warning" not in block(run_hook, make_repo(tmp_path, origin=ORIGIN_A))


@pytest.mark.parametrize("recorded", [True, False])
def test_no_warning_when_either_side_has_no_origin(
    run_hook: Run, tmp_path: Path, recorded: bool
) -> None:
    make_store(tmp_path, project=(f"origin: {ORIGIN_A}\n" if recorded else "") + "- [p](p.md): p\n")
    repo = make_repo(tmp_path, origin=None if recorded else ORIGIN_B)
    assert "Warning" not in block(run_hook, repo)


def test_store_path_comes_from_the_config(run_hook: Run, tmp_path: Path) -> None:
    store = make_store(tmp_path, user="- [a](a.md): a\n")
    assert f"Store: {store}" in block(run_hook, make_repo(tmp_path))


def test_default_store_is_coven_memory_in_home(run_hook: Run, tmp_path: Path) -> None:
    store = home(tmp_path) / "coven-memory"
    (store / "user").mkdir(parents=True)
    (store / "user" / "INDEX.md").write_text("- [a](a.md): a\n")
    assert f"Store: {store}" in block(run_hook, make_repo(tmp_path))


def test_no_store_prints_nothing(run_hook: Run, tmp_path: Path) -> None:
    done = run_hook(HOOK, {"cwd": str(make_repo(tmp_path))})
    assert (done.returncode, done.stdout) == (0, "")


def test_malformed_stdin_exits_0_silently(run_hook: Run, tmp_path: Path) -> None:
    make_store(tmp_path, user="- [a](a.md): a\n")
    done = run_hook(HOOK, "{not json")
    assert (done.returncode, done.stdout) == (0, "")
    log = home(tmp_path) / ".cache" / "coven" / "logs" / "memory-context.jsonl"
    assert json.loads(log.read_text().splitlines()[-1])["error"]


def test_broken_config_exits_0_silently_and_logs(run_hook: Run, tmp_path: Path) -> None:
    folder = home(tmp_path) / ".config" / "coven"
    folder.mkdir(parents=True)
    (folder / "config.json").write_text("{nope")
    done = run_hook(HOOK, {"cwd": str(tmp_path)})
    assert (done.returncode, done.stdout) == (0, "")
    assert (home(tmp_path) / ".cache" / "coven" / "logs" / "memory-context.jsonl").is_file()
