import json
import shutil
from collections.abc import Callable
from pathlib import Path
from subprocess import CompletedProcess

type Run = Callable[..., CompletedProcess[str]]

ROUTING = Path(__file__).resolve().parents[2] / "instructions" / "routing.md"


def test_prints_the_routing_text_in_the_session_start_envelope(
    run_hook: Run, hooks_dir: Path
) -> None:
    done = run_hook(hooks_dir / "session_context.py", {"hook_event_name": "SessionStart"})
    assert done.returncode == 0
    assert json.loads(done.stdout) == {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": ROUTING.read_text(encoding="utf-8"),
        }
    }


def test_malformed_stdin_still_gives_the_routing_text(run_hook: Run, hooks_dir: Path) -> None:
    done = run_hook(hooks_dir / "session_context.py", "{not json")
    assert done.returncode == 0
    assert "additionalContext" in done.stdout


def test_missing_routing_file_prints_nothing_and_logs(
    run_hook: Run, hooks_dir: Path, tmp_path: Path
) -> None:
    bare = tmp_path / "plugin" / "hooks"
    bare.mkdir(parents=True)
    for name in ("session_context.py", "hooklog.py", "hook_support.py"):
        shutil.copy(hooks_dir / name, bare / name)
    done = run_hook(bare / "session_context.py", {})
    assert done.returncode == 0
    assert done.stdout == ""
    log = tmp_path / "home" / ".cache" / "coven" / "logs" / "session-context.jsonl"
    assert "routing.md" in json.loads(log.read_text().splitlines()[-1])["error"]
