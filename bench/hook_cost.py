"""What one tool call costs in hook start-up, per hook and per guard state.

Run it from the repo root, with the system `python3` first on PATH:

    python3 bench/hook_cost.py            # 30 calls per case
    python3 bench/hook_cost.py --runs 100

Every case runs `sh hooks/fail-closed.sh <hook>` the way hooks.json does (the command is read from
hooks/hooks.json, so the numbers are for what ships), with a payload on stdin, a fresh temporary
HOME and an environment of PATH and HOME only. One warm-up call runs first and is not counted. The
table gives the median and the 90th percentile of the wall time in milliseconds. Nothing leaves the
machine: the stores are temporary git repositories and no network is used.

Cases:
  a  `python3 -I -c pass`, the floor every hook pays
  b  prod guard, no config file and no store
  c  prod guard, a config and a store without guard.json (the guard is off)
  d  prod guard on (latch and a committed guard.json), a Read call that is allowed
  e  deny-secrets, a Read call that is allowed
  f  pm-permissions `--pre-tool-use`, a Bash call, no PM role
"""

import argparse
import json
import math
import os
import shlex
import statistics
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
HOOKS_JSON = REPO / "hooks" / "hooks.json"
PLUGIN_ROOT_VAR = "${CLAUDE_PLUGIN_ROOT}"
DEFAULT_RUNS = 30
GIT_IDENTITY = ("-c", "user.name=bench", "-c", "user.email=bench@example.invalid")
GUARD = {
    "targets": [
        {
            "system": "orders-db",
            "kind": "production",
            "match": ["psql\\s+.*-h\\s*orders-db"],
            "tools": ["mcp__orders__write"],
        }
    ]
}


@dataclass(frozen=True)
class Case:
    label: str
    name: str
    command: Sequence[str]
    payload: str
    # Fills a fresh HOME (config, store) before the first call.
    prepare: Callable[[Path], None]


def shipped_command(script_prefix: str, *args: str) -> list[str]:
    """The PreToolUse command of hooks.json whose hook file starts with `script_prefix` and whose
    arguments are `args`, with the plugin root filled in."""
    entries = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))["hooks"]["PreToolUse"]
    for entry in entries:
        for hook in entry["hooks"]:
            words = shlex.split(hook["command"].replace(PLUGIN_ROOT_VAR, str(REPO)))
            script = Path(words[2]).name
            if script.startswith(script_prefix) and words[3:] == list(args):
                return words
    raise SystemExit(f"bench: no PreToolUse command for {script_prefix} {args} in {HOOKS_JSON}")


def payload(tool: str, tool_input: dict[str, str], cwd: Path) -> str:
    return json.dumps(
        {
            "session_id": "bench",
            "cwd": str(cwd),
            "hook_event_name": "PreToolUse",
            "tool_name": tool,
            "tool_input": tool_input,
        }
    )


def git(store: Path, *args: str) -> None:
    subprocess.run(
        ["git", *GIT_IDENTITY, "-C", str(store), *args],
        check=True,
        capture_output=True,
        env={"PATH": os.environ["PATH"], "HOME": str(store.parent)},
    )


def write_config(home: Path, fields: dict[str, str]) -> None:
    config = home / ".config" / "coven" / "config.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps(fields), encoding="utf-8")


def prepare_nothing(home: Path) -> None:
    del home


def prepare_store_without_guard(home: Path) -> None:
    store = home / "coven-memory"
    store.mkdir()
    git(store, "init", "-q")
    write_config(home, {"memory_path": str(store)})


def prepare_guard_on(home: Path) -> None:
    store = home / "coven-memory"
    (store / "systems").mkdir(parents=True)
    (store / "approvals").mkdir()
    (store / "systems" / "guard.json").write_text(json.dumps(GUARD), encoding="utf-8")
    git(store, "init", "-q")
    git(store, "add", "-A")
    git(store, "commit", "-q", "-m", "guard")
    write_config(home, {"memory_path": str(store), "guard": "required"})


def cases(cwd: Path) -> list[Case]:
    read = payload("Read", {"file_path": str(cwd / "README.md")}, cwd)
    bash = payload("Bash", {"command": "git status"}, cwd)
    prod_guard = shipped_command("prod_guard")
    return [
        Case(
            "a", "python3 -I -c pass (floor)", ["python3", "-I", "-c", "pass"], "", prepare_nothing
        ),
        Case("b", "prod guard, no config, no store", prod_guard, read, prepare_nothing),
        Case(
            "c",
            "prod guard, store without guard.json",
            prod_guard,
            read,
            prepare_store_without_guard,
        ),
        Case("d", "prod guard on, Read allowed", prod_guard, read, prepare_guard_on),
        Case("e", "deny-secrets, Read", shipped_command("deny_secrets"), read, prepare_nothing),
        Case(
            "f",
            "pm-permissions --pre-tool-use, Bash",
            shipped_command("pm_permissions", "--pre-tool-use"),
            bash,
            prepare_nothing,
        ),
    ]


def call(case: Case, home: Path) -> float:
    """One call in milliseconds. The call must exit 0: a refused or crashing hook is not a
    measurement of the path under test."""
    env = {"PATH": os.environ["PATH"], "HOME": str(home)}
    started = time.perf_counter()
    done = subprocess.run(
        case.command,
        input=case.payload,
        text=True,
        capture_output=True,
        env=env,
        cwd=REPO,
        check=False,
    )
    elapsed = (time.perf_counter() - started) * 1000
    if done.returncode != 0:
        raise SystemExit(
            f"bench: case {case.label} exited {done.returncode}: {done.stderr.strip()}"
        )
    return elapsed


def percentile(sorted_values: Sequence[float], fraction: float) -> float:
    """Nearest-rank percentile of an already sorted, non-empty list."""
    return sorted_values[max(0, math.ceil(fraction * len(sorted_values)) - 1)]


def measure(case: Case, runs: int) -> list[float]:
    with tempfile.TemporaryDirectory(prefix="hook-cost-") as folder:
        home = Path(folder) / "home"
        home.mkdir()
        case.prepare(home)
        call(case, home)  # warm-up: bytecode, page cache
        return sorted(call(case, home) for _ in range(runs))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Time one hook call per case.")
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS, help="calls per case")
    runs = parser.parse_args(argv).runs
    if runs < 1:
        parser.error("--runs must be at least 1")
    print(f"{sys.version.split()[0]} {runs} calls per case, ms (median, p90)")
    with tempfile.TemporaryDirectory(prefix="hook-cost-cwd-") as folder:
        for case in cases(Path(folder)):
            times = measure(case, runs)
            median, p90 = statistics.median(times), percentile(times, 0.9)
            print(f"({case.label}) {case.name:<42} median {median:7.1f}  p90 {p90:7.1f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
