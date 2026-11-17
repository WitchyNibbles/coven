"""fail-closed.sh: a hook that crashes must block the tool call (exit 2), and an old or missing
python3 must not let a secret through."""

import json
import marshal
import os
import shutil
import stat
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parents[2] / "hooks"
WRAPPER = HOOKS / "fail-closed.sh"
DENY_SECRETS = HOOKS / "deny_secrets.py"
SECRET_NAME = ".env"
SECRET_FILE = "/p/" + SECRET_NAME
NEED_PYTHON = 'coven: hooks need Python 3.12+; see README "Requirements"'
# What the wrapper's fallback needs besides python3.
POSIX_TOOLS = ("sed", "grep", "cat", "date", "mkdir", "tr", "wc")

Run = Callable[..., subprocess.CompletedProcess[str]]


def executable(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


@pytest.fixture
def modern_python(tmp_path: Path) -> dict[str, str]:
    """PATH whose python3 is this interpreter, so the tests do not depend on the system one."""
    shim = tmp_path / "modern-bin"
    shim.mkdir()
    executable(shim / "python3", f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    return {"PATH": f"{shim}{os.pathsep}{os.environ['PATH']}"}


@pytest.fixture
def old_python(tmp_path: Path) -> dict[str, str]:
    """PATH whose python3 is too old: launch.py exits 86 at once, as it does on a 3.9, without
    reading stdin."""
    shim = tmp_path / "old-bin"
    shim.mkdir()
    executable(shim / "python3", "#!/bin/sh\nexit 86\n")
    return {"PATH": f"{shim}{os.pathsep}{os.environ['PATH']}"}


@pytest.fixture
def counted_python(tmp_path: Path) -> dict[str, str]:
    """PATH whose python3 is this interpreter and notes each start in the file STARTS names."""
    shim = tmp_path / "counting-bin"
    shim.mkdir()
    executable(
        shim / "python3",
        f'#!/bin/sh\necho "$*" >>"$STARTS"\nexec "{sys.executable}" "$@"\n',
    )
    return {"PATH": f"{shim}{os.pathsep}{os.environ['PATH']}", "STARTS": str(tmp_path / "starts")}


@pytest.fixture
def no_python(tmp_path: Path) -> dict[str, str]:
    """PATH that holds the POSIX tools the fallback uses and no python3 at all."""
    shim = tmp_path / "bare-bin"
    shim.mkdir()
    for tool in POSIX_TOOLS:
        found = shutil.which(tool)
        assert found, tool
        (shim / tool).symlink_to(found)
    return {"PATH": str(shim)}


def hook_script(tmp_path: Path, source: str, name: str = "boom.py") -> Path:
    script = tmp_path / name
    script.write_text(source, encoding="utf-8")
    return script


def read_payload(file_path: str, session: str | None = "s1") -> dict[str, object]:
    payload: dict[str, object] = {"tool_name": "Read", "tool_input": {"file_path": file_path}}
    if session is not None:
        payload["session_id"] = session
    return payload


def marker_files() -> list[Path]:
    root = Path(os.environ["XDG_CACHE_HOME"]) / "coven"
    return sorted(root.glob("old-python-*"))


def test_wrapper_is_a_plain_posix_sh_file() -> None:
    assert subprocess.run(["/bin/sh", "-n", str(WRAPPER)], check=False).returncode == 0


def test_no_hook_command_blocks(run_hook: Run) -> None:
    proc = run_hook(WRAPPER, "")
    assert (proc.returncode, proc.stdout) == (2, "")
    assert proc.stderr == "coven: fail-closed.sh: no hook command\n"


def test_exit_0_passes_stdin_and_stdout_through(
    run_hook: Run, tmp_path: Path, modern_python: dict[str, str]
) -> None:
    hook = hook_script(
        tmp_path,
        "import sys\nsys.stdout.write(sys.stdin.read().upper())\nsys.stderr.write('note\\n')\n",
    )
    proc = run_hook(WRAPPER, "payload", modern_python, args=(str(hook),))
    assert (proc.returncode, proc.stdout, proc.stderr) == (0, "PAYLOAD", "note\n")


def test_exit_2_passes_through_with_the_hooks_own_message(
    run_hook: Run, tmp_path: Path, modern_python: dict[str, str]
) -> None:
    hook = hook_script(tmp_path, "import sys\nsys.stderr.write('no\\n')\nsys.exit(2)\n")
    proc = run_hook(WRAPPER, "", modern_python, args=(str(hook),))
    assert (proc.returncode, proc.stdout, proc.stderr) == (2, "", "no\n")


def test_the_hook_gets_its_arguments(
    run_hook: Run, tmp_path: Path, modern_python: dict[str, str]
) -> None:
    hook = hook_script(tmp_path, "import sys\nprint(sys.argv[1:])\n")
    proc = run_hook(WRAPPER, "", modern_python, args=(str(hook), "a", "b c"))
    assert proc.stdout == "['a', 'b c']\n"


def test_the_hook_runs_in_isolated_mode(
    run_hook: Run, tmp_path: Path, modern_python: dict[str, str]
) -> None:
    hook = hook_script(tmp_path, "import sys\nsys.exit(0 if sys.flags.isolated else 1)\n")
    assert run_hook(WRAPPER, "", modern_python, args=(str(hook),)).returncode == 0


def test_crash_becomes_exit_2(run_hook: Run, tmp_path: Path, modern_python: dict[str, str]) -> None:
    hook = hook_script(tmp_path, "raise RuntimeError('boom')\n", name="deny_secrets.py")
    proc = run_hook(WRAPPER, "{}", modern_python, args=(str(hook),))
    assert proc.returncode == 2
    assert proc.stderr.endswith("coven: deny-secrets failed (exit 1); blocking\n")


@pytest.mark.parametrize(
    ("source", "code"),
    [
        ("import sys\nsys.exit(1)\n", 1),
        ("import sys\nsys.exit(3)\n", 3),
        ("def broken(:\n", 1),  # a SyntaxError before any of the hook's own code runs
        ("import no_such_module_anywhere\n", 1),
        ("import os\nos.kill(os.getpid(), 9)\n", 137),  # killed: 128 + signal
    ],
)
def test_any_other_exit_becomes_2(
    run_hook: Run, tmp_path: Path, modern_python: dict[str, str], source: str, code: int
) -> None:
    hook = hook_script(tmp_path, source, name="session_context.py")
    proc = run_hook(WRAPPER, "", modern_python, args=(str(hook),))
    assert proc.returncode == 2
    assert proc.stderr.endswith(f"coven: session-context failed (exit {code}); blocking\n")


def test_a_crash_is_logged_under_the_hook_name(
    run_hook: Run, tmp_path: Path, modern_python: dict[str, str]
) -> None:
    hook = hook_script(tmp_path, "import sys\nsys.exit(7)\n", name="memory_context.py")
    run_hook(WRAPPER, "", modern_python, args=(str(hook),))
    log = Path(os.environ["XDG_CACHE_HOME"]) / "coven" / "logs" / "memory-context.jsonl"
    record = json.loads(log.read_text().splitlines()[-1])
    assert record["error"] == "exit 7"
    assert "ts" in record


def test_a_name_with_a_backslash_does_not_cut_the_message(
    run_hook: Run, tmp_path: Path, modern_python: dict[str, str]
) -> None:
    hook = hook_script(tmp_path, "import sys\nsys.exit(1)\n", name="back\\cslash.py")
    proc = run_hook(WRAPPER, "", modern_python, args=(str(hook),))
    assert proc.stderr.endswith("coven: back\\cslash failed (exit 1); blocking\n")


def test_the_real_hook_runs_through_the_wrapper(
    run_hook: Run, modern_python: dict[str, str]
) -> None:
    blocked = run_hook(
        WRAPPER, read_payload("/p/" + ".env"), modern_python, args=(str(DENY_SECRETS),)
    )
    allowed = run_hook(
        WRAPPER, read_payload("/p/notes.txt"), modern_python, args=(str(DENY_SECRETS),)
    )
    assert blocked.returncode == 2
    assert "coven deny-secrets: access to" in blocked.stderr
    assert (allowed.returncode, allowed.stderr) == (0, "")


def test_a_broken_hook_next_to_its_sibling_still_blocks(
    run_hook: Run, tmp_path: Path, modern_python: dict[str, str]
) -> None:
    broken = tmp_path / "deny_secrets.py"
    broken.write_text(DENY_SECRETS.read_text() + "\ndef broken(:\n")
    proc = run_hook(WRAPPER, read_payload("/p/a.txt"), modern_python, args=(str(broken),))
    assert proc.returncode == 2
    assert proc.stderr.endswith("coven: deny-secrets failed (exit 1); blocking\n")


@pytest.mark.parametrize("python", ["old_python", "no_python"])
def test_old_python_fallback_blocks_env_path(
    request: pytest.FixtureRequest, run_hook: Run, python: str
) -> None:
    env: dict[str, str] = request.getfixturevalue(python)
    for target in ("/p/.env", "/p/.env.local", "/p/.envrc", "/home/user/.ssh/id_ed25519"):
        proc = run_hook(WRAPPER, read_payload(target), env, args=(str(DENY_SECRETS),))
        assert proc.returncode == 2, target
        assert "coven deny-secrets: access to '" in proc.stderr
        assert "' is blocked (secrets)" in proc.stderr


def test_old_python_fallback_blocks_a_bash_command_that_names_a_secret(
    run_hook: Run, old_python: dict[str, str]
) -> None:
    payload = {"tool_name": "Bash", "tool_input": {"command": "cat .env | head"}}
    assert run_hook(WRAPPER, payload, old_python, args=(str(DENY_SECRETS),)).returncode == 2


@pytest.mark.parametrize("python", ["old_python", "no_python"])
def test_old_python_fallback_allows_other(
    request: pytest.FixtureRequest, run_hook: Run, python: str
) -> None:
    env: dict[str, str] = request.getfixturevalue(python)
    for target in ("/p/notes.txt", "/p/.venv/bin/activate", "/p/environment.py"):
        proc = run_hook(WRAPPER, read_payload(target), env, args=(str(DENY_SECRETS),))
        assert proc.returncode == 0, target
    bash = {"tool_name": "Bash", "tool_input": {"command": "env | sort"}}
    assert run_hook(WRAPPER, bash, env, args=(str(DENY_SECRETS),)).returncode == 0


@pytest.mark.parametrize("name", [".env.example", ".env.sample", ".env.template"])
def test_old_python_fallback_allows_env_templates(
    run_hook: Run, old_python: dict[str, str], name: str
) -> None:
    proc = run_hook(WRAPPER, read_payload(f"/p/{name}"), old_python, args=(str(DENY_SECRETS),))
    assert proc.returncode == 0, proc.stderr


def test_old_python_fallback_blocks_a_template_next_to_a_secret(
    run_hook: Run, old_python: dict[str, str]
) -> None:
    payload = {"tool_name": "Bash", "tool_input": {"command": "cat .env.example .env"}}
    assert run_hook(WRAPPER, payload, old_python, args=(str(DENY_SECRETS),)).returncode == 2


@pytest.mark.parametrize(
    "command",
    [
        "cat .env.example.local",
        "cat .env.examplefoo",
        "cat .env.sample.local",
        "cat .env.templated",
        "cat .env.example-prod",
        "cat .env.example.env",
        "cat .env.example .env.example.local",
    ],
)
def test_old_python_fallback_blocks_a_longer_name_that_starts_like_a_template(
    run_hook: Run, old_python: dict[str, str], command: str
) -> None:
    payload = {"tool_name": "Bash", "tool_input": {"command": command}}
    assert run_hook(WRAPPER, payload, old_python, args=(str(DENY_SECRETS),)).returncode == 2


@pytest.mark.parametrize(
    "command",
    [
        "cat .env.example",
        "cat .env.example | head",
        "cat .env.example; cat .env.sample && cat .env.template",
        "cat .env.example .env.example",
        "cat '.env.example'",
    ],
)
def test_old_python_fallback_allows_a_template_by_itself(
    run_hook: Run, old_python: dict[str, str], command: str
) -> None:
    payload = {"tool_name": "Bash", "tool_input": {"command": command}}
    proc = run_hook(WRAPPER, payload, old_python, args=(str(DENY_SECRETS),))
    assert proc.returncode == 0, proc.stderr


def test_old_python_fallback_allows_a_template_that_ends_the_text(
    run_hook: Run, old_python: dict[str, str]
) -> None:
    # No JSON payload ends this way in practice, but the end of the text counts as a boundary.
    proc = run_hook(WRAPPER, "path /p/.env.example", old_python, args=(str(DENY_SECRETS),))
    assert proc.returncode == 0, proc.stderr


@pytest.mark.parametrize("tool", ["grep", "sed"])
def test_old_python_fallback_blocks_when_a_text_tool_fails(
    run_hook: Run, old_python: dict[str, str], tool: str
) -> None:
    # grep exits 1 for "no match" and 2 for an error: an error must not read as "no match".
    shim = Path(old_python["PATH"].split(os.pathsep)[0])
    executable(shim / tool, "#!/bin/sh\ncat >/dev/null\nexit 2\n")
    proc = run_hook(WRAPPER, read_payload("/p/notes.txt"), old_python, args=(str(DENY_SECRETS),))
    assert proc.returncode == 2
    assert "coven deny-secrets: could not check" in proc.stderr


def test_old_python_message_is_printed_once_per_session(
    run_hook: Run, old_python: dict[str, str]
) -> None:
    first = run_hook(WRAPPER, read_payload("/p/a", "sess-1"), old_python, args=(str(DENY_SECRETS),))
    again = run_hook(WRAPPER, read_payload("/p/b", "sess-1"), old_python, args=(str(DENY_SECRETS),))
    other = run_hook(WRAPPER, read_payload("/p/a", "sess-2"), old_python, args=(str(DENY_SECRETS),))
    assert first.stderr == NEED_PYTHON + "\n"
    assert again.stderr == ""
    assert other.stderr == NEED_PYTHON + "\n"
    cache = Path(os.environ["XDG_CACHE_HOME"]) / "coven"
    assert sorted(path.name for path in marker_files()) == [
        "old-python-sess-1",
        "old-python-sess-2",
    ]
    assert all(path.parent == cache for path in marker_files())


def test_old_python_message_is_printed_with_a_block_too(
    run_hook: Run, old_python: dict[str, str]
) -> None:
    proc = run_hook(WRAPPER, read_payload("/p/.env"), old_python, args=(str(DENY_SECRETS),))
    assert proc.returncode == 2
    assert proc.stderr.splitlines()[0] == NEED_PYTHON


def test_old_python_message_without_a_session_id_is_printed_once_a_day(
    run_hook: Run, old_python: dict[str, str]
) -> None:
    args = (str(DENY_SECRETS),)
    first = run_hook(WRAPPER, read_payload("/p/a", None), old_python, args=args)
    again = run_hook(WRAPPER, "not json at all", old_python, args=args)
    assert first.stderr == NEED_PYTHON + "\n"
    assert again.stderr == ""
    assert len(marker_files()) == 1


def test_a_session_id_with_odd_characters_falls_back_to_the_day_marker(
    run_hook: Run, old_python: dict[str, str]
) -> None:
    run_hook(WRAPPER, read_payload("/p/a", "../../etc"), old_python, args=(str(DENY_SECRETS),))
    names = [path.name for path in marker_files()]
    assert len(names) == 1
    assert names[0].removeprefix("old-python-").isdigit()


def test_old_python_skips_other_hooks_and_says_so(
    run_hook: Run, old_python: dict[str, str]
) -> None:
    other = HOOKS / "session_context.py"
    proc = run_hook(WRAPPER, read_payload("/p/.env"), old_python, args=(str(other),))
    assert proc.returncode == 0
    assert proc.stderr == NEED_PYTHON + "\n"


def test_missing_python_skips_other_hooks_and_says_so(
    run_hook: Run, no_python: dict[str, str]
) -> None:
    other = HOOKS / "memory_context.py"
    proc = run_hook(WRAPPER, '{"session_id": "abc"}', no_python, args=(str(other),))
    assert (proc.returncode, proc.stdout, proc.stderr) == (0, "", NEED_PYTHON + "\n")
    assert [path.name for path in marker_files()] == ["old-python-abc"]


def test_a_call_starts_one_interpreter(
    run_hook: Run, tmp_path: Path, counted_python: dict[str, str]
) -> None:
    hook = hook_script(tmp_path, "import sys\nsys.exit(0)\n")
    assert run_hook(WRAPPER, "", counted_python, args=(str(hook), "--arg")).returncode == 0
    starts = Path(counted_python["STARTS"]).read_text().splitlines()
    assert starts == [f"-I {HOOKS / 'launch.py'} {hook} --arg"]


def test_a_call_that_finds_python_too_old_has_started_one_interpreter_too(
    run_hook: Run, old_python: dict[str, str], tmp_path: Path
) -> None:
    shim = Path(old_python["PATH"].split(os.pathsep)[0])
    executable(shim / "python3", '#!/bin/sh\necho "$*" >>"$STARTS"\nexit 86\n')
    env = {**old_python, "STARTS": str(tmp_path / "starts")}
    proc = run_hook(WRAPPER, read_payload(SECRET_FILE), env, args=(str(DENY_SECRETS),))
    assert proc.returncode == 2
    assert len(Path(env["STARTS"]).read_text().splitlines()) == 1


def test_the_old_python_fallback_still_sees_stdin(
    run_hook: Run, old_python: dict[str, str]
) -> None:
    # The wrapper reads stdin only after the launcher has said "too old" (it never read it).
    proc = run_hook(WRAPPER, read_payload(SECRET_FILE), old_python, args=(str(DENY_SECRETS),))
    assert proc.returncode == 2
    assert f"access to '{SECRET_NAME}' is blocked" in proc.stderr


def test_a_hook_that_exits_86_is_a_block_and_not_an_old_python(
    run_hook: Run, tmp_path: Path, modern_python: dict[str, str]
) -> None:
    hook = hook_script(tmp_path, "import sys\nsys.exit(86)\n", name="deny_secrets.py")
    proc = run_hook(WRAPPER, read_payload("/p/notes.txt"), modern_python, args=(str(hook),))
    assert (proc.returncode, proc.stdout, proc.stderr) == (2, "", "")
    assert marker_files() == []


def test_the_second_run_of_a_hook_reads_its_cached_bytecode(
    run_hook: Run, tmp_path: Path, modern_python: dict[str, str]
) -> None:
    hook = hook_script(tmp_path, "import sys\nsys.stderr.write('from source\\n')\n")
    first = run_hook(WRAPPER, "", modern_python, args=(str(hook),))
    assert first.stderr == "from source\n"
    (cached,) = (tmp_path / "__pycache__").glob("boom.*.pyc")
    swapped = compile("import sys\nsys.stderr.write('from cache\\n')\n", "boom", "exec")
    cached.write_bytes(cached.read_bytes()[:16] + marshal.dumps(swapped))
    assert run_hook(WRAPPER, "", modern_python, args=(str(hook),)).stderr == "from cache\n"


def test_a_python_of_3_12_is_new_enough(
    run_hook: Run, tmp_path: Path, modern_python: dict[str, str]
) -> None:
    hook = hook_script(tmp_path, "import sys\nsys.exit(0)\n")
    proc = run_hook(WRAPPER, "", modern_python, args=(str(hook),))
    assert (proc.returncode, proc.stderr) == (0, "")
    assert marker_files() == []


# --- the prod guard has no fallback of its own: an old python3 must not switch it off ---

PROD_GUARD = HOOKS / "prod_guard.py"
OLD_PYTHON_GUARD = "coven prod-guard: this python3 is too old to run the production guard"


def bash_payload(command: str = "ls") -> dict[str, object]:
    return {"tool_name": "Bash", "tool_input": {"command": command}, "session_id": "s1"}


def guarded_store(
    *, latch: bool, guard_file: bool, folder: Path | None = None, configured: str | None = None
) -> None:
    """A memory store (`folder`, by default `~/coven-memory`) and coven's config, which names
    `configured` as its path when given."""
    home = Path(os.environ["HOME"])
    store = home / "coven-memory" if folder is None else folder
    (store / "systems").mkdir(parents=True)
    if guard_file:
        (store / "systems" / "guard.json").write_text('{"targets": []}')
    config: dict[str, str] = {} if configured is None else {"memory_path": configured}
    if latch:
        config["guard"] = "required"
    (home / ".config" / "coven").mkdir(parents=True)
    (home / ".config" / "coven" / "config.json").write_text(json.dumps(config, indent=2))


def raw_config(text: str) -> None:
    config = Path(os.environ["HOME"]) / ".config" / "coven"
    config.mkdir(parents=True)
    (config / "config.json").write_text(text)


# JSON allows a newline between a key, its colon and its value; python parses these the same as
# the one-line forms, so the fallback must too.
SPLIT_LATCHES = [
    '{"guard":\n "required"}',
    '{"guard"\n : "required"}',
    '{\n"guard"\r\n:\r\n"required"\n}',
]


@pytest.mark.parametrize("text", SPLIT_LATCHES)
@pytest.mark.parametrize("folder", [False, True], ids=["no-folder", "folder-no-guard-file"])
def test_old_python_reads_a_latch_split_over_lines(
    run_hook: Run, old_python: dict[str, str], text: str, folder: bool
) -> None:
    raw_config(text)
    if folder:
        (Path(os.environ["HOME"]) / "coven-memory").mkdir()
    proc = run_hook(WRAPPER, bash_payload(), old_python, args=(str(PROD_GUARD),))
    assert proc.returncode == 2
    assert OLD_PYTHON_GUARD in proc.stderr


@pytest.mark.parametrize("text", ['{{"memory_path":\n "{}"}}', '{{"memory_path"\n : "{}"}}'])
def test_old_python_reads_a_memory_path_split_over_lines(
    run_hook: Run, old_python: dict[str, str], tmp_path: Path, text: str
) -> None:
    store = tmp_path / "elsewhere"
    (store / "systems").mkdir(parents=True)
    (store / "systems" / "guard.json").write_text('{"targets": []}')
    raw_config(text.format(store))
    proc = run_hook(WRAPPER, bash_payload(), old_python, args=(str(PROD_GUARD),))
    assert proc.returncode == 2
    assert OLD_PYTHON_GUARD in proc.stderr


@pytest.mark.parametrize("python", ["old_python", "no_python"])
@pytest.mark.parametrize("tool", ["Bash", "mcp__mail__send", "mcp__x__y"])
def test_old_python_refuses_bash_and_mcp_while_the_guard_is_on(
    request: pytest.FixtureRequest, run_hook: Run, python: str, tool: str
) -> None:
    guarded_store(latch=True, guard_file=False)
    env: dict[str, str] = request.getfixturevalue(python)
    payload = {"tool_name": tool, "tool_input": {}, "session_id": "s1"}
    proc = run_hook(WRAPPER, payload, env, args=(str(PROD_GUARD),))
    assert proc.returncode == 2
    assert OLD_PYTHON_GUARD in proc.stderr
    assert NEED_PYTHON in proc.stderr


@pytest.mark.parametrize("tool", ["Read", "Write", "Task", "WebFetch"])
def test_old_python_leaves_the_other_tools_alone_while_the_guard_is_on(
    run_hook: Run, old_python: dict[str, str], tool: str
) -> None:
    guarded_store(latch=True, guard_file=True)
    payload = {"tool_name": tool, "tool_input": {}, "session_id": "s1"}
    proc = run_hook(WRAPPER, payload, old_python, args=(str(PROD_GUARD),))
    assert (proc.returncode, proc.stderr) == (0, NEED_PYTHON + "\n")


def test_old_python_treats_a_working_tree_guard_file_as_the_guard_being_on(
    run_hook: Run, old_python: dict[str, str]
) -> None:
    guarded_store(latch=False, guard_file=True)
    proc = run_hook(WRAPPER, bash_payload(), old_python, args=(str(PROD_GUARD),))
    assert proc.returncode == 2
    assert OLD_PYTHON_GUARD in proc.stderr


def test_old_python_reads_the_store_path_from_the_config(
    run_hook: Run, old_python: dict[str, str], tmp_path: Path
) -> None:
    elsewhere = tmp_path / "elsewhere"
    guarded_store(latch=False, guard_file=True, folder=elsewhere, configured=str(elsewhere))
    proc = run_hook(WRAPPER, bash_payload(), old_python, args=(str(PROD_GUARD),))
    assert proc.returncode == 2


def test_old_python_expands_a_tilde_in_the_store_path(
    run_hook: Run, old_python: dict[str, str]
) -> None:
    folder = Path(os.environ["HOME"]) / "notes-store"
    guarded_store(latch=False, guard_file=True, folder=folder, configured="~/notes-store")
    proc = run_hook(WRAPPER, bash_payload(), old_python, args=(str(PROD_GUARD),))
    assert proc.returncode == 2


@pytest.mark.parametrize("python", ["old_python", "no_python"])
def test_old_python_lets_everything_through_while_the_guard_is_off(
    request: pytest.FixtureRequest, run_hook: Run, python: str
) -> None:
    env: dict[str, str] = request.getfixturevalue(python)
    proc = run_hook(WRAPPER, bash_payload(), env, args=(str(PROD_GUARD),))  # no store at all
    assert (proc.returncode, proc.stderr) == (0, NEED_PYTHON + "\n")


@pytest.mark.parametrize("python", ["old_python", "no_python"])
def test_old_python_without_a_config_file_still_guards_the_default_store(
    request: pytest.FixtureRequest, run_hook: Run, python: str
) -> None:
    store = Path(os.environ["HOME"]) / "coven-memory"
    (store / "systems").mkdir(parents=True)
    (store / "systems" / "guard.json").write_text('{"targets": []}')
    env: dict[str, str] = request.getfixturevalue(python)
    proc = run_hook(WRAPPER, bash_payload(), env, args=(str(PROD_GUARD),))
    assert proc.returncode == 2
    assert OLD_PYTHON_GUARD in proc.stderr


def test_old_python_without_a_config_file_and_a_default_store_without_a_guard_file_is_off(
    run_hook: Run, old_python: dict[str, str]
) -> None:
    (Path(os.environ["HOME"]) / "coven-memory" / "systems").mkdir(parents=True)
    assert run_hook(WRAPPER, bash_payload(), old_python, args=(str(PROD_GUARD),)).returncode == 0


def test_old_python_with_no_latch_and_no_guard_file_is_off(
    run_hook: Run, old_python: dict[str, str]
) -> None:
    guarded_store(latch=False, guard_file=False)
    proc = run_hook(WRAPPER, bash_payload(), old_python, args=(str(PROD_GUARD),))
    assert proc.returncode == 0


def test_old_python_with_a_missing_store_folder_and_no_latch_is_off(
    run_hook: Run, old_python: dict[str, str], tmp_path: Path
) -> None:
    config = Path(os.environ["HOME"]) / ".config" / "coven"
    config.mkdir(parents=True)
    (config / "config.json").write_text(json.dumps({"memory_path": str(tmp_path / "nowhere")}))
    assert run_hook(WRAPPER, bash_payload(), old_python, args=(str(PROD_GUARD),)).returncode == 0


@pytest.mark.parametrize("tool", ["Bash", "mcp__x__y"])
def test_old_python_with_a_missing_store_folder_and_the_latch_refuses(
    run_hook: Run, old_python: dict[str, str], tmp_path: Path, tool: str
) -> None:
    config = Path(os.environ["HOME"]) / ".config" / "coven"
    config.mkdir(parents=True)
    (config / "config.json").write_text(
        json.dumps({"memory_path": str(tmp_path / "nowhere"), "guard": "required"})
    )
    payload = {"tool_name": tool, "tool_input": {}}
    proc = run_hook(WRAPPER, payload, old_python, args=(str(PROD_GUARD),))
    assert proc.returncode == 2
    assert OLD_PYTHON_GUARD in proc.stderr


def test_old_python_refuses_when_grep_fails(run_hook: Run, tmp_path: Path) -> None:
    guarded_store(latch=True, guard_file=False)
    shim = tmp_path / "broken-grep-bin"
    shim.mkdir()
    for tool in POSIX_TOOLS:
        found = shutil.which(tool)
        assert found, tool
        (shim / tool).symlink_to(found)
    (shim / "grep").unlink()
    executable(shim / "grep", "#!/bin/sh\nexit 2\n")
    proc = run_hook(WRAPPER, bash_payload(), {"PATH": str(shim)}, args=(str(PROD_GUARD),))
    assert proc.returncode == 2
