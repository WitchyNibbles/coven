"""hooks/launch.py: the one interpreter every hook runs in. It refuses an old Python before reading
stdin, runs the hook as `__main__` through the import system (so its bytecode is cached) and keeps
exit code 86 for "too old"."""

import ast
import json
import marshal
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parents[2] / "hooks"
LAUNCHER = HOOKS / "launch.py"
TOO_OLD = 86

Run = Callable[..., subprocess.CompletedProcess[str]]


def launch(
    run_hook: Run, hook: Path, stdin: str = "", *args: str
) -> subprocess.CompletedProcess[str]:
    return run_hook(LAUNCHER, stdin, args=(str(hook), *args))


def hook_file(folder: Path, source: str, name: str = "demo_hook.py") -> Path:
    path = folder / name
    path.write_text(source, encoding="utf-8")
    return path


def test_the_launcher_is_written_in_syntax_python_2_also_parses() -> None:
    # An interpreter too old to run the hooks must still reach the version check.
    tree = ast.parse(LAUNCHER.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        assert not isinstance(node, ast.JoinedStr | ast.AnnAssign | ast.NamedExpr), node
        if isinstance(node, ast.FunctionDef):
            assert node.returns is None
            assert all(arg.annotation is None for arg in node.args.args)


def test_an_old_python_exits_86_before_it_reads_stdin(tmp_path: Path) -> None:
    ran = tmp_path / "ran"
    hook = hook_file(tmp_path, f"open({str(ran)!r}, 'w').close()\n")
    # The launcher under a python that reports 3.9; stdin is still whole when it has exited.
    code = (
        "import runpy, sys\n"
        "sys.version_info = (3, 9, 6, 'final', 0)\n"
        "try:\n"
        f"    runpy.run_path({str(LAUNCHER)!r}, run_name='__main__')\n"
        "except SystemExit as stop:\n"
        "    sys.stdout.write(sys.stdin.read())\n"
        "    raise\n"
    )
    proc = subprocess.run(
        [sys.executable, "-I", "-c", code, str(hook)],
        input="the payload",
        capture_output=True,
        text=True,
        check=False,
    )
    assert (proc.returncode, proc.stdout, proc.stderr) == (TOO_OLD, "the payload", "")
    assert not ran.exists()


def test_the_hook_runs_as_main_with_its_folder_first_on_the_path(
    run_hook: Run, tmp_path: Path
) -> None:
    hook = hook_file(
        tmp_path,
        "import json, sys\n"
        "if __name__ == '__main__':\n"
        "    print(json.dumps([__name__, sys.argv, sys.path[0], sys.stdin.read(), __file__]))\n",
    )
    proc = launch(run_hook, hook, "stdin text", "--flag", "a b")
    assert proc.returncode == 0, proc.stderr
    name, argv, first, stdin, file = json.loads(proc.stdout)
    assert (name, argv, first, stdin) == (
        "__main__",
        [str(hook), "--flag", "a b"],
        str(tmp_path),
        "stdin text",
    )
    assert Path(file) == hook


def test_a_hook_finds_the_modules_next_to_it(run_hook: Run, tmp_path: Path) -> None:
    (tmp_path / "neighbour.py").write_text("VALUE = 41\n", encoding="utf-8")
    hook = hook_file(tmp_path, "import neighbour\nprint(neighbour.VALUE + 1)\n")
    assert launch(run_hook, hook).stdout == "42\n"


@pytest.mark.parametrize("code", [0, 2, 1, 5])
def test_the_hooks_exit_code_passes_through(run_hook: Run, tmp_path: Path, code: int) -> None:
    hook = hook_file(tmp_path, f"import sys\nsys.exit({code})\n")
    assert launch(run_hook, hook).returncode == code


def test_a_hook_that_falls_off_its_end_exits_0(run_hook: Run, tmp_path: Path) -> None:
    assert launch(run_hook, hook_file(tmp_path, "pass\n")).returncode == 0


def test_a_hook_exit_of_86_becomes_2_so_86_only_means_too_old(
    run_hook: Run, tmp_path: Path
) -> None:
    hook = hook_file(tmp_path, f"import sys\nsys.exit({TOO_OLD})\n")
    assert launch(run_hook, hook).returncode == 2


def test_a_crashing_hook_exits_nonzero_with_its_traceback(run_hook: Run, tmp_path: Path) -> None:
    proc = launch(run_hook, hook_file(tmp_path, "raise RuntimeError('boom')\n"))
    assert proc.returncode == 1
    assert "RuntimeError: boom" in proc.stderr


def pycache_file(hook: Path) -> Path:
    found = list((hook.parent / "__pycache__").glob(f"{hook.stem}.*.pyc"))
    assert len(found) == 1, found
    return found[0]


def test_the_first_run_caches_the_hooks_bytecode_and_the_second_reads_it(
    run_hook: Run, tmp_path: Path
) -> None:
    hook = hook_file(tmp_path, "print('from source')\n")
    assert launch(run_hook, hook).stdout == "from source\n"
    cached = pycache_file(hook)
    # Swap the cached code, keeping the header that ties it to the source: only a run that reads
    # the cache prints the new text.
    header = cached.read_bytes()[:16]
    swapped = compile("print('from cache')\n", str(hook), "exec")
    cached.write_bytes(header + marshal.dumps(swapped))
    assert launch(run_hook, hook).stdout == "from cache\n"


def test_a_modules_bytecode_next_to_the_hook_is_cached_too(run_hook: Run, tmp_path: Path) -> None:
    neighbour = hook_file(tmp_path, "VALUE = 1\n", name="neighbour.py")
    hook = hook_file(tmp_path, "import neighbour\n")
    launch(run_hook, hook)
    assert pycache_file(neighbour).is_file()
