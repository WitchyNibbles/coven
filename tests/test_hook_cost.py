"""bench/hook_cost.py: the hook start-up benchmark. Its statistics, its argument handling, the
environment it gives each call and one real run over the shipped hooks, all under temporary homes."""

import json
import os
import re
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from types import ModuleType

import pytest
from support import REPO, load_module

LABELS = "abcdef"
# What a shell adds to the environment it starts with: nothing else may be there.
SHELL_VARIABLES = {"PATH", "HOME", "PWD", "OLDPWD", "SHLVL", "_"}


def load(path: Path, name: str) -> ModuleType:
    return load_module(path, name, register=True)


@pytest.fixture(scope="module")
def bench() -> ModuleType:
    return load(REPO / "bench" / "hook_cost.py", "coven_test_hook_cost")


@pytest.fixture(scope="module")
def guard_config() -> ModuleType:
    return load(REPO / "hooks" / "guard_config.py", "coven_test_bench_guard_config")


@pytest.fixture
def home(tmp_path: Path) -> Path:
    folder = tmp_path / "bench-home"
    folder.mkdir()
    return folder


def shell_case(bench: ModuleType, script: str, payload: str = "") -> object:
    return bench.Case("x", "shell", ["sh", "-c", script], payload, bench.prepare_nothing)


def fed(values: Sequence[float]) -> Callable[[object, Path], float]:
    """A stand-in for `call` that returns `values` in turn, one per call."""
    remaining: Iterator[float] = iter(values)

    def call(case: object, home: Path) -> float:
        del case, home
        return next(remaining)

    return call


# The statistics


def test_the_percentile_is_the_nearest_rank(bench: ModuleType) -> None:
    ten = [float(n) for n in range(1, 11)]
    assert bench.percentile(ten, 0.9) == 9.0
    assert bench.percentile(ten, 0.5) == 5.0
    assert bench.percentile(ten, 1.0) == 10.0
    assert bench.percentile([1.0, 2.0, 3.0], 0.9) == 3.0


def test_the_percentile_of_one_value_is_that_value(bench: ModuleType) -> None:
    assert bench.percentile([4.5], 0.9) == 4.5
    assert bench.percentile([4.5], 0.0) == 4.5


def test_measure_returns_the_sorted_times_and_leaves_out_the_warm_up(
    bench: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(bench, "call", fed([999.0, 4.0, 1.0, 3.0, 2.0]))
    assert bench.measure(shell_case(bench, "true"), 4) == [1.0, 2.0, 3.0, 4.0]


def test_measure_gives_each_case_a_fresh_home_that_is_prepared_and_then_removed(
    bench: ModuleType, tmp_path: Path
) -> None:
    homes: list[Path] = []

    def prepare(home: Path) -> None:
        homes.append(home)
        (home / "marker").write_text("prepared")

    case = bench.Case("x", "marker", ["sh", "-c", 'test -f "$HOME/marker"'], "", prepare)
    assert len(bench.measure(case, 2)) == 2  # the command fails, and so measure raises, if not
    assert len(homes) == 1
    assert not homes[0].exists()
    assert not homes[0].is_relative_to(Path(os.environ["HOME"]))


# The arguments


def test_the_default_is_30_calls_per_case(
    bench: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    asked: list[int] = []

    def measure(case: object, runs: int) -> list[float]:
        asked.append(runs)
        return [1.0]

    monkeypatch.setattr(bench, "measure", measure)
    assert bench.main([]) == 0
    assert asked == [30] * len(LABELS)
    assert " 30 calls per case" in capsys.readouterr().out.splitlines()[0]


def test_runs_sets_the_calls_per_case(
    bench: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    asked: list[int] = []

    def measure(case: object, runs: int) -> list[float]:
        asked.append(runs)
        return [1.0]

    monkeypatch.setattr(bench, "measure", measure)
    assert bench.main(["--runs", "7"]) == 0
    assert asked == [7] * len(LABELS)
    assert " 7 calls per case" in capsys.readouterr().out.splitlines()[0]


@pytest.mark.parametrize("runs", ["0", "-3", "many", "2.5"])
def test_a_bad_runs_value_is_a_usage_error_and_nothing_runs(
    bench: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    runs: str,
) -> None:
    def measure(case: object, runs: int) -> list[float]:
        raise AssertionError("a case ran")

    monkeypatch.setattr(bench, "measure", measure)
    with pytest.raises(SystemExit) as stop:
        bench.main(["--runs", runs])
    assert stop.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "--runs" in captured.err


def test_the_table_gives_the_median_and_the_90th_percentile_of_each_case(
    bench: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(bench, "measure", lambda case, runs: [1.0, 2.0, 3.0, 4.0])
    bench.main(["--runs", "4"])
    rows = capsys.readouterr().out.splitlines()[1:]
    assert len(rows) == len(LABELS)
    for row in rows:
        assert row.endswith("median     2.5  p90     4.0")


# The environment of a call


def test_a_call_gets_a_minimal_environment_and_never_the_real_home_or_xdg_folders(
    bench: ModuleType, monkeypatch: pytest.MonkeyPatch, home: Path
) -> None:
    for name, value in {
        "XDG_CONFIG_HOME": "/home/user/real/.config",
        "XDG_CACHE_HOME": "/home/user/real/.cache",
        "XDG_DATA_HOME": "/home/user/real/.local/share",
        "CLAUDE_CONFIG_DIR": "/home/user/real/.claude",
        "COVEN_SESSION_ROLE": "pm",
        "GIT_DIR": "/home/user/real/.git",
        "API_TOKEN": "not-a-real-token",
    }.items():
        monkeypatch.setenv(name, value)
    real_home = os.environ["HOME"]
    case = shell_case(bench, 'env > "$HOME/seen.txt"')
    bench.call(case, home)
    seen = dict(
        line.split("=", 1) for line in (home / "seen.txt").read_text().splitlines() if "=" in line
    )
    assert set(seen) <= SHELL_VARIABLES, sorted(set(seen) - SHELL_VARIABLES)
    assert seen["HOME"] == str(home)
    assert seen["HOME"] != real_home
    assert seen["PATH"] == os.environ["PATH"]
    assert "/home/user/real" not in "".join(seen.values())


def test_a_call_runs_in_the_repo_root_with_the_payload_on_stdin(
    bench: ModuleType, monkeypatch: pytest.MonkeyPatch, home: Path, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    case = shell_case(bench, 'pwd -P > "$HOME/cwd.txt"; cat > "$HOME/stdin.txt"', "the payload")
    bench.call(case, home)
    assert (home / "cwd.txt").read_text().strip() == str(REPO)
    assert (home / "stdin.txt").read_text() == "the payload"


def test_a_call_returns_its_time_in_milliseconds(bench: ModuleType, home: Path) -> None:
    elapsed = bench.call(shell_case(bench, "sleep 0.2"), home)
    assert 200 <= elapsed < 5000


def test_a_call_that_fails_stops_the_benchmark_with_the_reason(
    bench: ModuleType, home: Path
) -> None:
    case = shell_case(bench, "echo broken >&2; exit 3")
    with pytest.raises(SystemExit) as stop:
        bench.call(case, home)
    assert str(stop.value) == "bench: case x exited 3: broken"


# The cases


def test_the_shipped_command_is_the_one_hooks_json_runs_with_the_plugin_root_filled_in(
    bench: ModuleType,
) -> None:
    hooks = REPO / "hooks"
    assert bench.shipped_command("prod_guard") == [
        "sh",
        str(hooks / "fail-closed.sh"),
        str(hooks / "prod_guard_gate.py"),
    ]
    assert bench.shipped_command("pm_permissions", "--pre-tool-use") == [
        "sh",
        str(hooks / "fail-closed.sh"),
        str(hooks / "pm_permissions.py"),
        "--pre-tool-use",
    ]


def test_a_hook_that_hooks_json_does_not_run_is_an_error(bench: ModuleType) -> None:
    with pytest.raises(SystemExit, match="no PreToolUse command for nothing_like_it"):
        bench.shipped_command("nothing_like_it")
    with pytest.raises(SystemExit, match="no PreToolUse command for deny_secrets"):
        bench.shipped_command("deny_secrets", "--not-an-argument")


def test_the_payload_is_a_pre_tool_use_event_for_the_tool(
    bench: ModuleType, tmp_path: Path
) -> None:
    event = json.loads(bench.payload("Read", {"file_path": "x"}, tmp_path))
    assert event["hook_event_name"] == "PreToolUse"
    assert event["tool_name"] == "Read"
    assert event["tool_input"] == {"file_path": "x"}
    assert event["cwd"] == str(tmp_path)


def test_there_are_six_cases_labelled_a_to_f(bench: ModuleType, tmp_path: Path) -> None:
    assert [case.label for case in bench.cases(tmp_path)] == list(LABELS)


@pytest.fixture
def guard_is_off_after(
    bench: ModuleType, guard_config: ModuleType, monkeypatch: pytest.MonkeyPatch, home: Path
) -> Callable[[str], bool]:
    """A function: whether the guard is off for `home` once the case of that label prepared it."""

    def guard_is_off(label: str) -> bool:
        case = next(case for case in bench.cases(home) if case.label == label)
        case.prepare(home)
        monkeypatch.setenv("HOME", str(home))
        return guard_config.guard_is_off()

    return guard_is_off


@pytest.mark.parametrize(("label", "off"), [("b", True), ("c", True), ("d", False)])
def test_the_prod_guard_cases_have_the_guard_state_they_name(
    guard_is_off_after: Callable[[str], bool], label: str, off: bool
) -> None:
    assert guard_is_off_after(label) is off


def test_the_config_of_a_store_case_points_at_the_store_and_the_guard_on_case_adds_the_latch(
    bench: ModuleType, tmp_path: Path
) -> None:
    without, latched = tmp_path / "without", tmp_path / "latched"
    without.mkdir()
    latched.mkdir()
    bench.prepare_store_without_guard(without)
    bench.prepare_guard_on(latched)
    config = Path(".config") / "coven" / "config.json"
    assert json.loads((without / config).read_text()) == {
        "memory_path": str(without / "coven-memory")
    }
    assert json.loads((latched / config).read_text()) == {
        "memory_path": str(latched / "coven-memory"),
        "guard": "required",
    }
    assert not (without / "coven-memory" / "systems").exists()


def test_the_guard_on_case_commits_its_guard_file_to_a_store_with_an_approvals_folder(
    bench: ModuleType, home: Path
) -> None:
    bench.prepare_guard_on(home)
    store = home / "coven-memory"
    assert (store / "approvals").is_dir()
    assert bench.GUARD["targets"][0]["system"] in (store / "systems" / "guard.json").read_text()
    tracked = bench.subprocess.run(
        ["git", "-C", str(store), "ls-files"],
        capture_output=True,
        text=True,
        check=True,
        env={"PATH": os.environ["PATH"], "HOME": str(home)},
    ).stdout.split()
    assert tracked == ["systems/guard.json"]


# One real run


def test_a_run_times_every_case_on_the_shipped_hooks(
    bench: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    assert bench.main(["--runs", "1"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert re.fullmatch(r"\d+\.\d+\.\d+ 1 calls per case, ms \(median, p90\)", lines[0])
    assert len(lines) == 1 + len(LABELS)
    for label, row in zip(LABELS, lines[1:], strict=True):
        assert re.fullmatch(rf"\({label}\) .+ median +\d+\.\d  p90 +\d+\.\d", row), row
    assert "python3 -I -c pass" in lines[1]
