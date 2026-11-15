import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parents[2] / "hooks"
SCRIPT = HOOKS / "deny_secrets.py"

Run = Callable[..., subprocess.CompletedProcess[str]]


@pytest.fixture
def home() -> Path:
    """The private HOME the autouse fixture of tests/conftest.py set."""
    return Path(os.environ["HOME"])


@pytest.fixture
def run(run_hook: Run) -> Run:
    def runner(payload: object, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        return run_hook(SCRIPT, payload, cwd=cwd)

    return runner


@pytest.fixture
def bash(run: Run) -> Run:
    def runner(command: str) -> subprocess.CompletedProcess[str]:
        return run({"tool_name": "Bash", "tool_input": {"command": command}})

    return runner


@pytest.fixture
def call(run: Run) -> Run:
    def runner(tool: str, **tool_input: object) -> subprocess.CompletedProcess[str]:
        return run({"tool_name": tool, "tool_input": tool_input})

    return runner


@pytest.mark.parametrize(
    ("tool", "tool_input"),
    [
        ("Read", {"file_path": "/home/user/proj/.env"}),
        ("Read", {"file_path": "/home/user/proj/.env.local"}),
        ("Read", {"file_path": "/home/user/.ssh/id_ed25519"}),
        ("Bash", {"command": "cat .env | head"}),
        ("Bash", {"command": "ls ~/.ssh"}),
        ("Bash", {"command": "cp $HOME/.ssh/config /tmp"}),
        ("Grep", {"pattern": "KEY", "path": ".env"}),
        ("Glob", {"pattern": "**/.env*"}),
        ("Read", {"file_path": "/home/user/proj/.envrc"}),
        ("Read", {"file_path": "/home/user/proj/.env.example.local"}),
        ("Bash", {"command": "cat<.env"}),
        ("Bash", {"command": "cat {.env,x}"}),
        ("Bash", {"command": "echo x,.env"}),
        ("Bash", {"command": "cat \\.env"}),
        ("Bash", {"args": ["x", "~/.ssh/id"]}),
        ("Bash", {"command": "ls", "workdir": "/home/user/.ssh"}),
        ("Bash", {"command": "cat .env", "description": "Show the file"}),
        ("Agent", {"description": "read .env"}),
        ("Grep", {"pattern": "\\.env", "path": "src"}),
        ("Bash", {"command": "cat .env2"}),
        ("Bash", {"command": "cat .env_local"}),
        ("Bash", {"command": "cat .env-prod"}),
        ("Bash", {"command": "cat .env.prod"}),
    ],
)
def test_blocks_secret_paths(call: Run, tool: str, tool_input: dict[str, object]) -> None:
    proc = call(tool, **tool_input)
    assert proc.returncode == 2
    assert "coven deny-secrets: access to" in proc.stderr


def test_block_message_names_the_file_and_the_reason(call: Run) -> None:
    proc = call("Read", file_path="/home/user/proj/.env.local")
    assert proc.stderr.strip() == "coven deny-secrets: access to '.env.local' is blocked (secrets)"


@pytest.mark.parametrize(
    ("tool", "tool_input"),
    [
        ("Read", {"file_path": "/home/user/proj/environment.py"}),
        ("Read", {"file_path": "/home/user/proj/src/config.ts"}),
        ("Bash", {"command": "uv run pytest -v"}),
        ("Grep", {"pattern": "env", "path": "src"}),
        ("Bash", {"command": "cat ~/.config/coven/secrets.env"}),
        ("Bash", {"command": "node -e 'console.log(process.env.HOME)'"}),
        ("Grep", {"pattern": "import.meta.env", "path": "src"}),
        ("Bash", {"command": "uv pip install python-dotenv"}),
        # a name glued on its left to a digit, `_`, `.` or `-` passes
        ("Bash", {"command": "echo x1.env"}),
        ("Bash", {"command": "echo my_.env"}),
        ("Bash", {"command": "echo a..env"}),
        ("Bash", {"command": "echo a-.env"}),
        ("Bash", {"command": "ls x9.ssh"}),
        ("Bash", {"command": "ls my_.ssh"}),
        ("Bash", {"command": "ls a..ssh"}),
        ("Bash", {"command": "ls a-.ssh"}),
    ],
)
def test_allows_other_paths(call: Run, tool: str, tool_input: dict[str, object]) -> None:
    proc = call(tool, **tool_input)
    assert proc.returncode == 0, proc.stderr


def test_venv_not_matched(call: Run, bash: Run) -> None:
    assert call("Read", file_path="/home/user/proj/.venv/bin/activate").returncode == 0
    assert call("Glob", pattern="**/.venv/**").returncode == 0
    assert bash("ls .venv && source .venv/bin/activate").returncode == 0


def test_plain_env_command_not_matched(bash: Run) -> None:
    for command in ("env", "env | sort", "env FOO=1 make", "printenv HOME", "/usr/bin/env python3"):
        assert bash(command).returncode == 0, command


@pytest.mark.parametrize("name", [".env.example", ".env.sample", ".env.template"])
def test_env_example_sample_template_allowed(
    call: Run, run: Run, tmp_path: Path, name: str
) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    assert call("Read", file_path=f"{project}/{name}").returncode == 0
    assert call("Grep", pattern="KEY", path=f"{project}/{name}").returncode == 0
    for tool in ("Write", "Edit"):
        payload = {"tool_name": tool, "tool_input": {"file_path": f"{project}/{name}"}}
        assert run(payload).returncode == 0, tool


@pytest.mark.parametrize("name", [".envrc", ".env.local", ".env", ".env.bak"])
def test_envrc_blocked(call: Run, run: Run, tmp_path: Path, name: str) -> None:
    path = f"{tmp_path}/{name}"
    assert call("Read", file_path=path).returncode == 2
    for tool in ("Write", "Edit"):
        payload = {"tool_name": tool, "tool_input": {"file_path": path}}
        assert run(payload).returncode == 2, tool


@pytest.mark.parametrize(
    "command",
    [
        "git commit -m 'docs: ignore .env files'",
        'git commit -m "docs: never commit .env"',
        "git -C /repo commit --message='keep .ssh keys out'",
        'gh pr create --title "Ignore .env" --body "Keeps .env out of git"',
        "gh pr create --title t --body \"$(cat <<'EOF'\nKeeps .env and ~/.ssh out\nEOF\n)\"",
        "cat > notes.md <<'EOF'\nnever read .env\nEOF",
        "git commit -F - <<'EOF'\nignore .env\nEOF",
        "git commit -S -m 'ignore .env'",
        "grep -rn '\\.env' src",
        'rg -n "\\.env" docs',
        "grep -e '.env' -rn src",
        "git grep -n '\\.env'",
        "ssh host \"grep -rn 'app\\.env' /srv/demo/config\"",
        "grep -rn app\\.env /srv",
    ],
)
def test_allows_a_name_that_is_only_data(bash: Run, command: str) -> None:
    proc = bash(command)
    assert proc.returncode == 0, proc.stderr


@pytest.mark.parametrize(
    "command",
    [
        "cat .env",
        "source .env",
        ". .env",
        "cat < .env",
        "bash -c 'cat .env'",
        'sh -c "cat .env"',
        "eval 'cat .env'",
        'eval "$(cat .env)"',
        'git commit -m "`cat .env`"',
        'git commit -m "$(cat .env)"',
        'gh pr create --body "$(cat .env)"',
        "gh pr create --body-file .env",
        "gh pr create -F .env",
        "git commit -F .env",
        "git commit -m x -- .env",
        "git commit -m ok && cat .env",
        'cat <<< "$(cat .env)"',
        "bash <<< 'cat .env'",
        "diff <(cat .env) x",
        "bash <<'EOF'\ncat .env\nEOF",
        "cat <<'EOF' | sh\ncat .env\nEOF",
        "( cat <<'EOF'\ncat .env\nEOF\n) | sh",
        "cat <<EOF > x\n$(cat .env)\nEOF",
        "python3 - <<'EOF'\nprint(open('.env').read())\nEOF",
        "xargs cat <<'EOF'\n.env\nEOF",
        "cat <<'EOF'\ncat .env",
        "cat() { bash; }; cat <<'EOF'\ncat .env\nEOF",
        "bash -c \"$(cat <<'EOF'\ncat .env\nEOF\n)\"",
        "grep -f .env x",
        "grep KEY .env",
        "grep -rn -- KEY .env",
        "rg -g '.env*' KEY",
        "rg --pre 'cat .env' x y",
        "git -c core.pager='cat .env' commit -m x",
        "ssh host 'cat .env'",
        "git commit -m 'x' -m \"$(cat ~/.ssh/id_ed25519)\"",
        "cat \\.env",
        "cat ./\\.env",
        # a heredoc that bash ends earlier or later than a plain reading of the lines
        'gh pr create --body "$(cat <<\'EOF\'\nhi\nEOF)"\ncat .env\nEOF\n)"',
        "(( cat <<'X' ))\ncat .env\nX",
        "(( y = 1<<2 )); cat <<'Q' > f\ncat .env\nQ",
        "cat <<AB\nA\\\nB\ncat .env\nAB\n",
        "git commit -m 'a' <<EOF\ncat .env\nEOF",
        # a backslash-newline that bash reads differently
        "cat <<\\\n-'EOF'\ncat .env\n-EOF\n",
        'git commit -m "$\\\n(cat .env)"',
        # a number that is not a file descriptor
        "git commit -m 99999999999>x .env",
        "git commit -m 12>x .env",
        # a message flag that is the value of another option
        "git commit -m y --trailer -m .env",
        "git commit -C -m .env",
        "git commit -m y $=X -m .env",
        "gh release create v1 --target -n .env",
        "gh release create v1 -R -n .env",
        # a heredoc read as paths
        "git commit -m x --pathspec-from-file=- <<'EOF'\n.env\nEOF",
        "git commit -m x <<'EOF'\n.env\nEOF",
        # options after the pattern
        "grep .env -e KEY",
        "grep .env -f pats",
        "rg .env -e KEY",
        # a command that is redefined first
        "eval 'cat() { bash; }'; cat <<'EOF'\ncat .env\nEOF",
        "if true; then eval 'cat() { bash; }'; fi; cat <<'EOF'\ncat .env\nEOF",
        "hash -p /bin/sh grep; grep -c '.env' f",
        "hash cat=/bin/sh; cat <<'EOF'\ncat .env\nEOF",
        "shopt -s expand_aliases; BASH_ALIASES[cat]=sh\ncat <<'EOF'\ncat .env\nEOF",
        "functions[cat]=x; cat <<'EOF'\ncat .env\nEOF",
        "coproc sh; cat <<'EOF' >&p\ncat .env\nEOF",
        # an option table entry that takes a value
        "git commit --squash -m .env",
        "gh pr close 1 --comment -b .env",
        # zsh extendedglob, and options that change how the tools behave
        "git commit -m ^.env",
        "set -o extendedglob; git commit -m 'x .env'",
        "RIPGREP_CONFIG_PATH=/tmp/c; rg '\\.env' src",
        "GIT_CONFIG_COUNT=1 git commit -m '.env'",
        "gh pr create --body \"$(cat <<-'X'\n\t.env\n\tX\n)\"",
        # where commands are looked up
        "PATH=/tmp/x:$PATH; cat <<'EOF'\ncat .env\nEOF",
        "export PATH=/tmp/x; cat <<'EOF'\ncat .env\nEOF",
        # zsh: the target of >! is the next word
        "git commit -m >! .env x",
        # a comment (zsh reads it only with interactive_comments)
        "cat <<'EOF' # c\ncat .env\nEOF",
    ],
)
def test_still_blocks_a_command_that_reads_the_name(bash: Run, command: str) -> None:
    proc = bash(command)
    assert proc.returncode == 2
    assert "coven deny-secrets: access to" in proc.stderr


def test_ignores_the_bash_description(run: Run) -> None:
    tool_input = {"command": "ls -a", "description": "List .env files"}
    proc = run({"tool_name": "Bash", "tool_input": tool_input})
    assert proc.returncode == 0, proc.stderr


def test_bash_command_with_a_ssh_dir_in_a_message_only_is_allowed(bash: Run) -> None:
    assert bash("git commit -m 'never touch ~/.ssh'").returncode == 0
    assert bash("git commit -m 'x' && ls ~/.ssh").returncode == 2


def test_missing_shell_words_module_blocks(run_hook: Run, tmp_path: Path) -> None:
    lone = tmp_path / SCRIPT.name
    shutil.copy(SCRIPT, lone)
    shutil.copy(HOOKS / "hook_support.py", tmp_path / "hook_support.py")
    payload = {"tool_name": "Bash", "tool_input": {"command": "ls"}}
    proc = run_hook(lone, payload)
    assert proc.returncode == 2
    assert "internal error" in proc.stderr


def test_malformed_payload_blocks(run: Run) -> None:
    proc = run("not json")
    assert proc.returncode == 2
    assert "could not parse hook input" in proc.stderr


def test_empty_stdin_blocks(run: Run) -> None:
    proc = run("")
    assert proc.returncode == 2
    assert "could not parse hook input" in proc.stderr


def test_non_object_payload_blocks(run: Run) -> None:
    proc = run([])
    assert proc.returncode == 2
    assert "could not parse hook input" in proc.stderr


def test_unexpected_error_blocks(run: Run) -> None:
    proc = run("[" * 100000)
    assert proc.returncode == 2
    assert "internal error" in proc.stderr


def test_closed_stdin_blocks() -> None:
    proc = subprocess.run(
        [sys.executable, "-I", str(SCRIPT)],
        preexec_fn=lambda: os.close(0),  # sys.stdin is None: json.load raises AttributeError
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 2
    assert "internal error" in proc.stderr


def test_failures_are_logged(run: Run, home: Path) -> None:
    run("not json")
    log = home / ".cache" / "coven" / "logs" / "deny-secrets.jsonl"
    record = json.loads(log.read_text().splitlines()[-1])
    assert "could not parse hook input" in record["error"]
    assert "ts" in record


def test_a_block_is_not_a_failure_and_is_not_logged(call: Run, home: Path) -> None:
    call("Read", file_path="/home/user/.env")
    assert not (home / ".cache" / "coven" / "logs" / "deny-secrets.jsonl").exists()


BROKEN_LOGS = {
    "import": "raise RuntimeError('log module broken at import')\n",
    "call": "def log_event(hook, record):\n    raise RuntimeError('log call broken')\n",
    "syntax": "def broken(:\n",
}


@pytest.mark.parametrize("breakage", sorted(BROKEN_LOGS))
@pytest.mark.parametrize(
    "payload",
    [
        {"tool_name": "Read", "tool_input": {"file_path": "/home/user/proj/.env"}},
        "not json",
        "",
    ],
    ids=["secret-path", "malformed", "empty"],
)
def test_a_broken_hook_log_still_blocks_when_run_without_the_wrapper(
    run_hook: Run, tmp_path: Path, breakage: str, payload: object
) -> None:
    lone = tmp_path / "hooks"
    lone.mkdir()
    for name in ("deny_secrets.py", "shell_words.py", "hook_support.py"):
        shutil.copy(HOOKS / name, lone / name)
    (lone / "hooklog.py").write_text(BROKEN_LOGS[breakage], encoding="utf-8")
    proc = run_hook(lone / "deny_secrets.py", payload)
    assert proc.returncode == 2, proc.stderr
    assert "coven deny-secrets:" in proc.stderr


WRITERS = ("Write", "Edit", "MultiEdit", "NotebookEdit")
MENTIONS = "load .env, never ~/.ssh/config; cat .env"


def writer_input(tool: str, target: str, text: str = MENTIONS) -> dict[str, object]:
    """What `tool` gets to write `target`, with `text` in every part that is content."""
    if tool == "Write":
        return {"file_path": target, "content": text}
    if tool == "Edit":
        return {"file_path": target, "old_string": text, "new_string": text}
    if tool == "MultiEdit":
        return {"file_path": target, "edits": [{"old_string": text, "new_string": text}]}
    return {"notebook_path": target, "new_source": text, "cell_id": "c1", "edit_mode": "replace"}


@pytest.fixture
def project(home: Path) -> Path:
    path = home / "proj"
    (path / "sub").mkdir(parents=True)
    return path


@pytest.fixture
def write(run: Run) -> Callable[[str, object, Path], subprocess.CompletedProcess[str]]:
    def writer(tool: str, tool_input: object, cwd: Path) -> subprocess.CompletedProcess[str]:
        """The hook runs from elsewhere: a relative target is read from the payload's `cwd`."""
        payload = {"tool_name": tool, "tool_input": tool_input, "cwd": str(cwd)}
        return run(payload, cwd=cwd.parent.parent)

    return writer


def place(target: str, home: Path, project: Path) -> str:
    return target.replace("<H>", str(home)).replace("<P>", str(project))


@pytest.mark.parametrize("tool", WRITERS)
@pytest.mark.parametrize(
    "target",
    [
        ".env",
        "./.env",
        "sub/.env.local",
        "<P>/.env.production",
        ".envrc",
        ".env.example.local",
        ".env.ipynb",
        "proj/../.env",
        "a//b/./.env",
        "<P>/sub/../.env",
        ".environment/.env",
        "~/.ssh/config",
        "$HOME/.ssh/authorized_keys",
        "${HOME}/.ssh/id_ed25519.pub",
        "<H>/.ssh/x",
        "<H>/.ssh/notes.ipynb",
        "../.ssh/x",
        "x/.ssh/notes.md",
    ],
)
def test_a_write_to_a_secret_path_is_blocked(home, project, write, tool, target):
    target = place(target, home, project)
    proc = write(tool, writer_input(tool, target, "x"), project)
    assert proc.returncode == 2, target
    assert "' is blocked (secrets" in proc.stderr


@pytest.mark.parametrize("tool", WRITERS)
def test_a_relative_target_is_read_from_the_call_cwd(home, write, tool):
    ssh = home / ".ssh"
    ssh.mkdir()
    assert write(tool, writer_input(tool, "config", "x"), ssh).returncode == 2


def test_a_write_block_names_the_secret_as_written(project, write):
    proc = write("Write", writer_input("Write", "sub/.env.local"), project)
    assert proc.stderr.strip() == "coven deny-secrets: access to '.env.local' is blocked (secrets)"


def link(path: Path, target: str | Path) -> Path:
    path.symlink_to(target)
    return path


@pytest.mark.parametrize("tool", WRITERS)
def test_a_target_that_is_a_link_to_a_secret_is_blocked(project, write, tool):
    (project / ".env").write_text("KEY=x\n")
    link(project / "notes.md", ".env")
    proc = write(tool, writer_input(tool, "notes.md", "x"), project)
    assert proc.returncode == 2
    resolved = os.path.realpath(project / ".env")
    assert proc.stderr.strip() == (
        "coven deny-secrets: access to 'notes.md' is blocked "
        f"(secrets: it resolves into a guarded place, {resolved})"
    )


@pytest.mark.parametrize("tool", WRITERS)
def test_a_target_under_a_link_to_the_ssh_directory_is_blocked(home, project, write, tool):
    (home / ".ssh").mkdir()
    link(project / "keys", home / ".ssh")
    for target in ("keys/x", f"{project}/keys/authorized_keys"):
        proc = write(tool, writer_input(tool, target, "x"), project)
        assert proc.returncode == 2, target
        assert f"access to '{target}' is blocked (secrets: it resolves" in proc.stderr


@pytest.mark.parametrize("tool", WRITERS)
def test_a_dangling_link_is_read_by_the_text_it_points_to(project, write, tool):
    link(project / "draft.md", ".env")
    assert write(tool, writer_input(tool, "draft.md", "x"), project).returncode == 2


@pytest.mark.parametrize("tool", WRITERS)
@pytest.mark.parametrize(
    "target",
    [
        "README.md",
        "src/settings.py",
        "<P>/notes.ipynb",
        "docs/env.md",
        "secrets.env",
        "secrets.env.md",
        ".environment/notes.md",
        "notes.ssh.md",
        ".venv/bin/activate",
    ],
)
def test_a_write_elsewhere_passes_whatever_its_content_names(home, project, write, tool, target):
    proc = write(tool, writer_input(tool, place(target, home, project)), project)
    assert proc.returncode == 0, proc.stderr


@pytest.mark.parametrize("tool", WRITERS)
@pytest.mark.parametrize(
    "target",
    [
        ".env.example",
        "config/.env.sample",
        ".env.template",
        "<P>/.env.dist",
        "~/proj/.env.example",
        ".env.EXAMPLE",
        ".env.Sample",
    ],
)
def test_a_write_to_an_env_template_passes(home, project, write, tool, target):
    proc = write(tool, writer_input(tool, place(target, home, project)), project)
    assert proc.returncode == 0, proc.stderr


@pytest.mark.parametrize("tool", WRITERS)
@pytest.mark.parametrize(
    "target", [".env.example.local", ".env.examples", "x.env.example/.env", "<H>/.ssh/.env.example"]
)
def test_a_name_that_is_not_exactly_a_template_is_blocked(home, project, write, tool, target):
    proc = write(tool, writer_input(tool, place(target, home, project), "x"), project)
    assert proc.returncode == 2, target


@pytest.mark.parametrize("tool", WRITERS)
def test_a_template_name_that_links_to_a_secret_is_blocked(project, write, tool):
    link(project / ".env.example", ".env.local")
    proc = write(tool, writer_input(tool, ".env.example", "x"), project)
    assert proc.returncode == 2
    assert "access to '.env.example' is blocked (secrets: it resolves" in proc.stderr


@pytest.fixture
def read(run: Run) -> Callable[..., subprocess.CompletedProcess[str]]:
    def reader(
        tool_input: dict[str, object], project: Path, tool: str = "Read"
    ) -> subprocess.CompletedProcess[str]:
        payload = {"tool_name": tool, "tool_input": tool_input, "cwd": str(project)}
        return run(payload, cwd=project.parent.parent)

    return reader


@pytest.mark.parametrize(
    ("tool", "tool_input"),
    [
        ("Read", {"file_path": "<P>/.env.example"}),
        ("Read", {"file_path": "config/.env.sample"}),
        ("Read", {"file_path": "<P>/.env.local.example"}),
        ("Read", {"file_path": "~/proj/.ENV.Template"}),
        ("Grep", {"pattern": "KEY", "path": "<P>/.env.example"}),
        ("Grep", {"pattern": "KEY", "path": "<P>", "glob": ".env.example"}),
        ("Glob", {"pattern": "**/.env.example", "path": "<P>"}),
    ],
)
def test_a_read_or_search_of_an_env_template_passes(home, project, read, tool, tool_input):
    tool_input = json.loads(place(json.dumps(tool_input), home, project))
    proc = read(tool_input, project, tool)
    assert proc.returncode == 0, proc.stderr


@pytest.mark.parametrize(
    "path",
    [
        ".env",
        "<P>/.env.example.local",
        "~/.ssh/.env.example",
        "<P>/.env.example.bak",
        "/proc/self/cwd/.env.example",
    ],
)
def test_a_read_of_other_env_names_or_of_a_template_in_a_guarded_place_is_blocked(
    home, project, read, path
):
    proc = read({"file_path": place(path, home, project)}, project)
    assert proc.returncode == 2, path
    assert "' is blocked (secrets)" in proc.stderr or "is blocked (proc" in proc.stderr


def test_a_read_of_a_template_name_that_links_to_a_secret_is_blocked(project, read):
    link(project / ".env.example", ".env")
    for path in (".env.example", f"{project}/.env.example"):
        assert read({"file_path": path}, project).returncode == 2, path
        assert read({"pattern": "K", "path": path}, project, "Grep").returncode == 2, path


@pytest.mark.parametrize(
    "command",
    [
        "cat /mnt/c/p/ENV~1",
        "ls SSH~1",
        "cat 'ENV~1.LOC'",
        "cp ENVRC~1 /tmp/x",
        "cat /mnt/c/Users/user/ssh~1/config",
        "less x;cat env~2",
        "tar cf a.tar --file=x ENV~1",
    ],
)
def test_a_bash_word_that_is_a_dos_short_name_of_a_secret_is_blocked(bash, command):
    proc = bash(command)
    assert proc.returncode == 2, command
    assert "' is blocked (secrets)" in proc.stderr


@pytest.mark.parametrize(
    "command",
    ["git log HEAD~1", "echo a~1", "git diff HEAD~2 -- env.py", "git commit -m 'read ENV~1'"],
)
def test_a_bash_word_that_only_looks_like_a_short_name_passes(bash, command):
    assert bash(command).returncode == 0, command


def test_a_bash_read_of_an_env_template_is_still_blocked(bash):
    assert bash("cat .env.example").returncode == 2


def test_only_the_target_keys_are_checked(project, write):
    tool_input = {"file_path": "README.md", "content": "x", "note": "~/.ssh/config", "n": [".env"]}
    assert write("Write", tool_input, project).returncode == 0


@pytest.mark.parametrize("tool", WRITERS)
@pytest.mark.parametrize("target", [".ENV", ".Env.Local", "~/.SSH/config", "sub/.EnvRC"])
def test_a_write_target_is_matched_in_any_case(project, write, tool, target):
    proc = write(tool, writer_input(tool, target, "x"), project)
    assert proc.returncode == 2, target
    assert "' is blocked (secrets" in proc.stderr


@pytest.mark.parametrize("tool", WRITERS)
@pytest.mark.parametrize("target", [".ENV.EXAMPLE", ".Env.Sample"])
def test_a_template_in_any_case_passes_a_write(project, write, tool, target):
    proc = write(tool, writer_input(tool, target), project)
    assert proc.returncode == 0, proc.stderr


def test_a_read_and_a_bash_command_stay_case_sensitive(project, call, bash):
    assert call("Read", file_path=f"{project}/.ENV").returncode == 0
    assert call("Read", file_path="~/.SSH/config").returncode == 0
    assert bash("cat .Env.Local").returncode == 0


@pytest.mark.parametrize("tool", WRITERS)
@pytest.mark.parametrize(
    "tool_input",
    [
        {"content": "x"},
        {"old_string": "a", "new_string": "b"},
        {"file_path": ""},
        {"file_path": ["README.md"]},
        {"file_path": "README.md", "notebook_path": 3},
        {"notebook_path": None},
    ],
)
def test_a_write_without_a_target_path_is_blocked(project, write, tool, tool_input):
    proc = write(tool, tool_input, project)
    assert proc.returncode == 2, tool_input
    assert "is blocked (no file_path or notebook_path to check)" in proc.stderr


@pytest.mark.parametrize("tool", WRITERS)
def test_a_write_whose_input_is_not_an_object_is_blocked(project, write, tool):
    proc = write(tool, ["README.md"], project)
    assert proc.returncode == 2
    assert "is blocked (no file_path or notebook_path to check)" in proc.stderr


def test_a_chain_of_links_into_ssh_is_blocked(home, project, write):
    (home / ".ssh").mkdir()
    (project / "b").symlink_to(home / ".ssh")
    (project / "a").symlink_to(project / "b")
    for tool in WRITERS:
        proc = write(tool, writer_input(tool, "a/config", "x"), project)
        assert proc.returncode == 2, tool


def test_a_relative_link_into_ssh_is_blocked(home, project, write):
    (home / ".ssh").mkdir()
    (project / "keys").symlink_to("../.ssh")
    for tool in WRITERS:
        proc = write(tool, writer_input(tool, "keys/authorized_keys", "x"), project)
        assert proc.returncode == 2, tool


def test_a_link_loop_target_is_blocked_without_crashing_the_hook(project, write):
    # Where a loop leads cannot be checked, so it is blocked (fail closed).
    (project / "loop").symlink_to(project / "loop")
    (project / "ping").symlink_to("pong")
    (project / "pong").symlink_to("ping")
    for target in ("loop", "ping/x", f"{project}/pong"):
        for tool in WRITERS:
            proc = write(tool, writer_input(tool, target, "x"), project)
            assert proc.returncode == 2, (tool, target)
            assert "internal error" not in proc.stderr, tool
            assert "is blocked (a symlink loop: where it leads cannot be checked)" in proc.stderr


def test_a_proc_self_path_to_a_secret_is_blocked(project, write):
    for target in ("/proc/self/cwd/.env", "/proc/self/root/home/user/.ssh/x"):
        for tool in WRITERS:
            proc = write(tool, writer_input(tool, target, "x"), project)
            assert proc.returncode == 2, (tool, target)


def test_every_target_key_is_checked_whichever_tool_names_it(project, write):
    for tool in WRITERS:
        tool_input = {"file_path": "README.md", "notebook_path": ".env", "content": "x"}
        assert write(tool, tool_input, project).returncode == 2, tool
        tool_input = {"file_path": ".env", "notebook_path": "nb.ipynb", "new_source": "x"}
        assert write(tool, tool_input, project).returncode == 2, tool


# drvfs (WSL's /mnt/c) opens `.env` as `ENV~1`, `.env.local` as `ENV~1.LOC`, `.ssh` as `SSH~1`.
SHORT_NAMES = [
    "/mnt/c/proj/ENV~1",
    "/mnt/c/proj/ENV~1.LOC",
    "/mnt/c/proj/ENVRC~1",
    "/mnt/c/Users/user/SSH~1/config",
    "/mnt/c/proj/env~12",
    "sub/Ssh~2/id",
]
NOT_SHORT_NAMES = ["/mnt/c/proj/NOTES~1.MD", "/mnt/c/proj/MYENV~1", "/mnt/c/proj/ENV~X", "ENV-1"]


@pytest.mark.parametrize("tool", WRITERS)
@pytest.mark.parametrize("target", SHORT_NAMES)
def test_a_dos_short_name_of_a_secret_is_blocked_as_a_write_target(project, write, tool, target):
    proc = write(tool, writer_input(tool, target, "x"), project)
    assert proc.returncode == 2, target
    assert "' is blocked (secrets" in proc.stderr


@pytest.mark.parametrize("target", SHORT_NAMES)
def test_a_dos_short_name_of_a_secret_is_blocked_as_a_read_search_or_glob_path(call, target):
    assert call("Read", file_path=target).returncode == 2, target
    assert call("Grep", pattern="KEY", path=target).returncode == 2, target
    assert call("Glob", pattern=f"{target}/*").returncode == 2, target
    assert call("Glob", pattern="*", path=target).returncode == 2, target


@pytest.mark.parametrize("tool", WRITERS)
def test_a_target_under_a_directory_named_home_variable_is_checked_unexpanded(
    home, project, write, tool
):
    # Claude does not expand `$HOME`: it writes `<cwd>/$HOME/config`.
    (home / ".ssh").mkdir()
    link(project / "$HOME", home / ".ssh")
    proc = write(tool, writer_input(tool, "$HOME/config", "x"), project)
    assert proc.returncode == 2
    assert "access to '$HOME/config' is blocked (secrets: it resolves" in proc.stderr


@pytest.mark.parametrize("tool", WRITERS)
@pytest.mark.parametrize("target", [" notes.md", "notes.md ", "notes.md\n", "\t notes.md \n"])
def test_a_target_is_checked_with_its_blanks_stripped(project, write, tool, target):
    # Claude trims the path: the link `notes.md` -> `.env` is what it writes.
    link(project / "notes.md", ".env")
    proc = write(tool, writer_input(tool, target, "x"), project)
    assert proc.returncode == 2, repr(target)
    assert "(secrets: it resolves" in proc.stderr


@pytest.mark.parametrize("target", NOT_SHORT_NAMES)
def test_other_short_names_pass(project, write, call, target):
    assert write("Write", writer_input("Write", target), project).returncode == 0, target
    assert call("Read", file_path=target).returncode == 0, target


@pytest.mark.parametrize("tool", WRITERS)
@pytest.mark.parametrize(
    ("target", "cwd"),
    [
        ("/proc/self/cwd/notes.md", None),
        ("/proc/1/root/tmp/x", None),
        ("/proc/self/fd/1", None),
        ("/tmp/../proc/self/cwd/x", None),
        ("cwd/notes.md", "/proc/self"),
        ("/proc", None),
    ],
)
def test_a_write_under_proc_is_blocked(run, project, tool, target, cwd):
    payload = {"tool_name": tool, "tool_input": writer_input(tool, target, "x"), "cwd": cwd}
    proc = run(payload, cwd=project)
    assert proc.returncode == 2, target
    assert "is blocked (proc: /proc/self there is the hook's own process, not Claude's)" in (
        proc.stderr
    )


@pytest.mark.parametrize("tool", WRITERS)
@pytest.mark.parametrize("target", ["~/.ßh/config", ".ßh/x", "<P>/.ſſh/id", "sub/.ſsh"])
def test_a_write_target_is_compared_case_folded(home, project, write, tool, target):
    # str.casefold() turns `ß` into `ss` and `ſ` into `s`, as a filesystem that folds case may
    proc = write(tool, writer_input(tool, place(target, home, project), "x"), project)
    assert proc.returncode == 2, target
    assert "' is blocked (secrets" in proc.stderr


@pytest.mark.parametrize("tool", WRITERS)
@pytest.mark.parametrize(
    "target",
    [
        ".environment.yml",
        ".envoy.yaml",
        "config/.Envrc.d/notes.md",
        ".env.local.example",
        ".env.development.example",
        ".env-example",
        ".env_sample",
        ".ENV-Template",
        ".env.prod.dist",
    ],
)
def test_a_write_to_a_name_that_only_starts_like_env_or_is_a_template_passes(
    project, write, tool, target
):
    assert write(tool, writer_input(tool, target), project).returncode == 0, target


@pytest.mark.parametrize("tool", WRITERS)
@pytest.mark.parametrize(
    "target",
    [".env-prod", ".env_local", ".env.", ".env.bak", ".env.example.bak", ".env-sample-old"],
)
def test_a_write_to_an_env_name_whose_last_part_is_not_a_template_is_blocked(
    project, write, tool, target
):
    proc = write(tool, writer_input(tool, target, "x"), project)
    assert proc.returncode == 2, target
    assert f"access to '{target}' is blocked (secrets)" in proc.stderr


def test_a_read_of_a_name_that_only_starts_like_env_is_still_blocked(project, call, bash):
    assert call("Read", file_path=f"{project}/.environment.yml").returncode == 2
    assert bash("cat .env.local.example").returncode == 2


@pytest.mark.parametrize("target", ["/procedures/x.md", "proc/x.md"])
def test_a_write_to_a_name_like_proc_passes(project, write, target):
    assert write("Write", writer_input("Write", target), project).returncode == 0, target
