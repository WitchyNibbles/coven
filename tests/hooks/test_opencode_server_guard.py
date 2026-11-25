"""The OpenCode server guard: what a PM session may not reach from its shell and file tools.

Everything lives under `tmp_path`: a fake profile (with the serve folder `<profile>/state/serve`),
a fake coven root, a sibling worktree of the coven repo and a project folder to work from. The
tests write `<...>` markers in commands and paths; `World.fill` swaps in the real folders.
"""

import io
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
from support import load_module

HOOKS = Path(__file__).resolve().parents[2] / "hooks"
SCRIPT = HOOKS / "opencode_server_guard.py"
PORT = 43123

guard = load_module(SCRIPT, "coven_opencode_server_guard", register=True, restore_path=True)
wrapper_commands = load_module(HOOKS / "wrapper_commands.py", "coven_wrapper_commands")


@dataclass(frozen=True)
class World:
    root: Path
    profile: Path
    serve: Path
    coven: Path
    other: Path
    project: Path
    port: int | None = PORT

    def fill(self, text: str) -> str:
        marks = {
            "<profile>": self.profile,
            "<home>": self.profile.parent,
            "<root>": self.root,
            "<serve>": self.serve,
            "<coven>": self.coven,
            "<other>": self.other,
            "<project>": self.project,
            "<port>": PORT,
        }
        for mark, value in marks.items():
            text = text.replace(mark, str(value))
        return text

    def call(self, tool: str, field: str, value: str, cwd: Path | None = None) -> str | None:
        payload = {
            "tool_name": tool,
            "tool_input": {field: self.fill(value)},
            "cwd": str(cwd or self.project),
        }
        return guard.refusal(payload, self.profile, self.serve, self.coven, self.port)


def touch(path: Path, text: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def world(tmp_path: Path) -> World:
    home = Path(os.environ["HOME"])
    profile = home / "profile"
    serve = profile / "state" / "serve"
    coven = tmp_path / "coven"
    other = tmp_path / "other"
    project = tmp_path / "project"
    touch(serve / "password", "secret")
    touch(serve / "port", str(PORT))
    touch(profile / "config" / "opencode" / "opencode.jsonc")
    touch(profile / "config" / "opencode" / "AGENTS.md")
    touch(profile / "config" / "opencode" / "agents" / "a.md")
    touch(profile / "plugin" / "coven" / "index.ts")
    touch(profile / "data" / "opencode" / "opencode.db")
    touch(profile / "cache" / "opencode-pty")
    touch(profile / "profile.json", "{}")
    touch(profile / "runtime" / "node_modules" / ".bin" / "opencode")
    touch(profile / "tmp" / "body.md")
    touch(coven / "hooks" / "deny_secrets.py")
    touch(coven / "opencode" / "src" / "guard.ts")
    touch(coven / "src" / "coven" / "cli.py")
    touch(coven / "bin" / "coven")
    touch(coven / "opencode" / "content" / "AGENTS.md")
    touch(coven / "pyproject.toml")
    touch(coven / ".claude" / "worktrees" / "x" / "hooks" / "deny_secrets.py")
    touch(other / "coven-worktree" / "hooks" / "deny_secrets.py")
    touch(project / "README.md")
    # Links under the writable `tmp/` that lead into the protected set.
    (profile / "tmp" / "to-password").symlink_to(serve / "password")
    (profile / "tmp" / "to-config").symlink_to(profile / "config")
    (profile / "tmp" / "to-serve").symlink_to(serve)
    (project / "to-hooks").symlink_to(coven / "hooks")
    (coven / ".claude" / "worktrees" / "x" / "to-src").symlink_to(coven / "src")
    return World(tmp_path, profile, serve, coven, other, project)


# Each refusal sits next to the near allow that must stay open.
OPENCODE_REFUSED = [
    "npx opencode api x",
    "<profile>/runtime/node_modules/.bin/opencode db",
    "opencode api session.list",
    "opencode session list",
    "opencode run --session x",
    "opencode db",
    "opencode export x",
    "opencode import f",
    "opencode --server http://127.0.0.1:4096",
    "opencode",
    "opencode --version --foo",
    "/usr/bin/opencode db",
    "env X=1 opencode db",
    "X=1 opencode db",
    "exec opencode db",
    "command opencode db",
    "env -u PATH opencode db",
    "timeout 5 opencode db",
    "nice -n 5 opencode db",
    "sudo -u x opencode db",
    "echo x | xargs opencode db",
    "find . -exec opencode db {} ;",
    "bash -c 'opencode db'",
    'sh -c "cd /tmp && opencode session list"',
    'bash -c "bash -c \\"opencode db\\""',
    "eval 'opencode db'",
    "echo $(opencode db)",
    "echo `opencode db`",
    "ls; opencode db",
    "true && opencode db",
    "opencode db | head",
    "'opencode' db",
    "npx -y opencode-ai@latest api x",
    "bunx opencode db",
    "pnpm dlx opencode db",
    "env -S 'opencode db'",
    "npx opencode --version db",
    "env -i opencode db",
    "FOO=bar bash -c 'FOO=1 opencode db'",
    "exec -a x opencode db",
    "time opencode db",
    "nohup opencode db &",
    "/bin/sh -c 'exec opencode db'",
    "bash -lc 'opencode db'",
    "sudo opencode db",
    "find . -name x -exec opencode db \\;",
    "find . -exec bash -c 'opencode db' \\;",
    "$OC db",
    "env OC=opencode $OC db",
    "OC=opencode; $OC db",
    "su -c 'opencode db'",
    "npx -c 'opencode db'",
    "npm exec -- opencode db",
    "pnpm exec opencode db",
    "bash <<'EOF'\nopencode db\nEOF",
    'bash <<< "opencode db"',
    "echo $(bash -c 'opencode db')",
    "echo $(echo $(opencode db))",
    # A program that runs another, and any word that names opencode after one that may run it.
    "npm --prefix opencode test",
    "taskset -c 0 opencode db",
    "flock /tmp/l opencode db",
    "flock -c 'opencode db' /tmp/l",
    "script -qc 'opencode db' /dev/null",
    "chrt -o 0 opencode db",
    "unshare -U opencode db",
    "unshare --map-root-user -- opencode db",
    "setpriv --reuid 1000 opencode db",
    "runuser -u x -- opencode db",
    "runuser x -c 'opencode db'",
    "coproc opencode db",
    "coproc OC { opencode db; }",
    "tmux new -d 'opencode db'",
    "tmux new-session -d -s pm opencode db",
    "screen -dm opencode db",
    "strace -f opencode db",
    "strace -o /tmp/t -f opencode db",
    "ltrace opencode db",
    "busybox sh -c 'opencode db'",
    "busybox ash -c 'opencode db'",
    "systemd-run --user opencode db",
    "parallel opencode db ::: x",
    "uv run opencode db",
    "pipx run opencode db",
    "nix-shell --run 'opencode db'",
    "env bash -c 'opencode db'",
    "hush -c 'opencode db'",
    "tcsh -c 'opencode db'",
    "python3 opencode db",
    "stdbuf -oL opencode db",
]
OPENCODE_ALLOWED = [
    "opencode --version",
    "opencode --help",
    "npx opencode --version",
    "env X=1 opencode --help",
    "bash -c 'opencode --version'",
    "cd opencode && npm test",
    "ls opencode",
    "git log --oneline -- opencode",
    "grep -rn opencode .",
    "echo opencode db",
    "git commit -m 'fix opencode db'",
    "git commit -m \"$(cat <<'EOF'\nmessage that names opencode db\nEOF\n)\"",
    "npm test",
    "git status",
    "git diff HEAD~1 -- opencode/src",
    "cd opencode && npm ci && npx vitest run",
    "pytest tests/ -k 'serve or password' -q",
    "echo 'password reset' > notes.txt",
    "curl -s https://example.com | head",
    "uv run coven pm --help",
    'git commit -m "docs: mention /proc and environ vars"',
    "ls -la ~",
    "for f in *.md; do echo $f; done",
    "awk '{print $1}' README.md",
    "find . -name '*.py' -exec grep -l foo {} +",
    "sleep 1 &",
    "if [ -f README.md ]; then echo y; fi",
    "ps -ef | grep node",
    "mkdir -p out && cd out && ls",
    "ls /proc",
    "bash <<'EOF'\nnpm test\nEOF",
    "taskset -c 0 opencode --version",
    "timeout 5 opencode --help",
    "which opencode",
    "man opencode",
    "type opencode",
    "find . -name opencode",
    "cp x opencode",
    "mkdir -p opencode/out",
    "tree opencode",
    "ls opencode | head",
    "wc -l opencode",
    "cat opencode",
    "coven opencode doctor",
    "tmux new -d 'npm test'",
    "busybox ls",
]


@pytest.mark.parametrize("command", OPENCODE_REFUSED)
def test_opencode_commands_but_version_and_help_are_refused(world: World, command: str) -> None:
    assert world.call("Bash", "command", command)


@pytest.mark.parametrize("command", OPENCODE_ALLOWED)
def test_opencode_version_and_help_and_other_words_are_allowed(world: World, command: str) -> None:
    assert world.call("Bash", "command", command) is None


PORT_REFUSED = [
    "curl -u x http://127.0.0.1:<port>/api/session",
    "curl localhost:<port>",
    'bash -c "curl 127.0.0.1:<port>"',
    "wget -qO- http://[::1]:<port>/",
    "curl --user x:y http://0.0.0.0:<port>/session",
    "python3 -c \"import urllib.request as u; u.urlopen('http://localhost:<port>')\"",
    "nc 127.0.0.1 <port>",
    "curl http://127.0.0.1:<port>",
    "echo $(curl localhost:<port>)",
]
PORT_ALLOWED = [
    "curl http://127.0.0.1:8080/",
    "curl localhost:8080",
    "curl https://example.com/",
    "echo 4312",
    "echo 431234",
]


@pytest.mark.parametrize("command", PORT_REFUSED)
def test_the_servers_port_is_refused(world: World, command: str) -> None:
    assert world.call("Bash", "command", command)


@pytest.mark.parametrize("command", PORT_ALLOWED)
def test_another_port_is_allowed(world: World, command: str) -> None:
    assert world.call("Bash", "command", command) is None


def test_with_no_port_known_any_loopback_port_is_refused(world: World) -> None:
    unknown = World(**{**world.__dict__, "port": None})
    assert unknown.call("Bash", "command", "curl http://127.0.0.1:8080/")
    assert unknown.call("Bash", "command", "curl localhost:9")
    assert unknown.call("Bash", "command", "curl https://example.com/") is None


def test_no_port_file_still_refuses_opencode_api(world: World) -> None:
    unknown = World(**{**world.__dict__, "port": None})
    assert unknown.call("Bash", "command", "opencode api session.list")
    assert unknown.call("Bash", "command", "npx opencode api x")
    assert unknown.call("Bash", "command", "opencode --version") is None


SECRETS_REFUSED = [
    ("Bash", "command", "cat <serve>/password"),
    ("Bash", "command", "cd <profile>/state && cat serve/password"),
    ("Bash", "command", "cat <profile>/tmp/../state/serve/password"),
    ("Bash", "command", "grep -r . <serve>"),
    ("Bash", "command", "cat /proc/123/environ"),
    ("Bash", "command", "ps eww"),
    ("Bash", "command", "ps -E"),
    ("Bash", "command", "ps aux e"),
    ("Read", "file_path", "<serve>/password"),
    ("Read", "file_path", "<profile>/tmp/to-password"),
    # More ways to write the same path.
    ("Bash", "command", 'cat <serve>/pass""word'),
    ("Bash", "command", "cat '<serve>/password'"),
    ("Bash", "command", "cat <serve>/pass\\word"),
    ("Bash", "command", "cat <serve>/pass*"),
    ("Bash", "command", "cat <profile>/st?te/ser[v]e/password"),
    ("Bash", "command", "cat <profile>/state/ser''ve/password"),
    ("Bash", "command", "cat $'<serve>/pass\\x77ord'"),
    ("Bash", "command", "cat <serve>/{password,port}"),
    ("Bash", "command", "cat ~/profile/state/serve/password"),
    ("Bash", "command", "cat $HOME/profile/state/serve/password"),
    ("Bash", "command", 'cat "${HOME}/profile/state/serve/password"'),
    ("Bash", "command", "P=<serve>; cat $P/password"),
    ("Bash", "command", "export P=<profile>/state && cat $P/serve/password"),
    ("Bash", "command", "cat $UNKNOWN_FOLDER/password"),
    ("Bash", "command", "cat <profile>/tmp/to-password"),
    ("Bash", "command", "cat <profile>/tmp/to-serve/password"),
    ("Bash", "command", "cd <profile>/tmp && cat to-password"),
    ("Bash", "command", "cd <profile>/tmp && cat ../state/serve/password"),
    ("Bash", "command", "cd <profile>/tmp && cd .. && cd state && cat serve/password"),
    ("Bash", "command", "bash -c 'cat <serve>/password'"),
    ("Bash", "command", "echo $(cat <serve>/password)"),
    ("Bash", "command", "python3 -c \"print(open('<serve>/password').read())\""),
    ("Bash", "command", "cat /proc/self/environ"),
    ("Bash", "command", "cat /proc/$$/environ"),
    ("Bash", "command", "cat /proc/*/environ"),
    ("Bash", "command", "cat /proc/self/task/1/environ"),
    ("Bash", "command", "tr '\\0' '\\n' < /proc/1/environ"),
    ("Bash", "command", "cat /proc/self/cwd/../environ"),
    ("Bash", "command", "ps axe"),
    ("Bash", "command", "ps -p 1 e"),
    ("Bash", "command", "ps ewww"),
    ("Bash", "command", "ps -eE"),
    ("Bash", "command", "ps aux; ps eww"),
    ("Read", "file_path", "/proc/self/environ"),
    ("Read", "file_path", "/proc/123/environ"),
    ("Read", "file_path", "<profile>/tmp/to-serve/password"),
    ("Read", "file_path", "<profile>/tmp/../state/serve/password"),
    ("Read", "file_path", "~/profile/state/serve/password"),
    ("Grep", "path", "<serve>"),
    ("Grep", "path", "<profile>/tmp/to-serve"),
    ("Glob", "path", "<serve>"),
    ("Glob", "pattern", "<serve>/*"),
    ("Glob", "pattern", "<profile>/state/**/password"),
    ("Bash", "command", "cat $(echo <serve>/password)"),
    ("Bash", "command", "cd <profile>/tmp/to-serve && cat password"),
    ("Bash", "command", "(cd <serve>; cat password)"),
    ("Bash", "command", "cat < <serve>/password"),
    ("Bash", "command", "cat <serve>//password"),
    ("Bash", "command", "cat <serve>/./password"),
    ("Bash", "command", "cat <(cat <serve>/password)"),
    ("Bash", "command", "bash <<'EOF'\ncat <serve>/password\nEOF"),
    ("Bash", "command", "x=pass; cat <serve>/${x}word"),
    ("Bash", "command", "cat /proc/self/fd/../environ"),
    ("Bash", "command", "cd /proc/self && cat environ"),
    ("Bash", "command", "python3 -c \"import os; os.system('cat <serve>/password')\""),
]
SECRETS_ALLOWED = [
    ("Bash", "command", "ps aux"),
    ("Bash", "command", "ps -ef"),
    ("Bash", "command", "ps -o etime -p 1"),
    ("Bash", "command", "ps -C nginx"),
    ("Bash", "command", "cat /proc/cpuinfo"),
    ("Bash", "command", "cat /proc/self/status"),
    ("Bash", "command", "cat <profile>/tmp/body.md"),
    ("Read", "file_path", "/proc/cpuinfo"),
    ("Read", "file_path", "<profile>/config/opencode/AGENTS.md"),
    ("Read", "file_path", "<profile>/tmp/body.md"),
    ("Read", "file_path", "<project>/README.md"),
    ("Read", "file_path", "README.md"),
    ("Grep", "path", "<profile>/config"),
    ("Glob", "path", "<profile>/config"),
    ("Glob", "pattern", "<project>/*.md"),
]


@pytest.mark.parametrize(("tool", "field", "value"), SECRETS_REFUSED)
def test_the_serve_folder_and_process_environments_are_refused(
    world: World, tool: str, field: str, value: str
) -> None:
    assert world.call(tool, field, value)


@pytest.mark.parametrize(("tool", "field", "value"), SECRETS_ALLOWED)
def test_near_reads_are_allowed(world: World, tool: str, field: str, value: str) -> None:
    assert world.call(tool, field, value) is None


def test_a_relative_path_resolves_against_the_cwd(world: World) -> None:
    assert world.call("Bash", "command", "cat password", cwd=world.serve)
    assert world.call("Bash", "command", "cat ../serve/password", cwd=world.profile / "tmp")
    assert world.call("Read", "file_path", "password", cwd=world.serve)
    assert world.call("Read", "file_path", "../state/serve/password", cwd=world.profile / "tmp")
    assert world.call("Bash", "command", "cat password", cwd=world.profile / "tmp") is None


def test_the_shell_workdir_counts_as_the_working_directory(world: World) -> None:
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": "cat password", "workdir": str(world.serve)},
        "cwd": str(world.project),
    }
    assert guard.refusal(payload, world.profile, world.serve, world.coven, world.port)
    payload["tool_input"] = {"command": "cat README.md", "workdir": str(world.project)}
    assert guard.refusal(payload, world.profile, world.serve, world.coven, world.port) is None
    payload["tool_input"] = {
        "command": "ls",
        "workdir": os.path.relpath(world.serve, world.project),
    }
    payload["cwd"] = str(world.project)
    assert guard.refusal(payload, world.profile, world.serve, world.coven, world.port)


PROTECTED_REFUSED = [
    ("Write", "file_path", "<profile>/config/opencode/opencode.jsonc"),
    ("Edit", "file_path", "<profile>/config/opencode/opencode.jsonc"),
    ("Bash", "command", "echo x > <profile>/config/opencode/agents/a.md"),
    ("Write", "file_path", "<profile>/plugin/coven/index.ts"),
    ("Bash", "command", "sqlite3 <profile>/data/opencode/opencode.db"),
    ("Edit", "file_path", "<profile>/profile.json"),
    ("Write", "file_path", "<profile>/tmp/to-config/opencode/opencode.jsonc"),
    ("Write", "file_path", "<coven>/hooks/deny_secrets.py"),
    ("Edit", "file_path", "<coven>/opencode/src/guard.ts"),
    ("Write", "file_path", "<profile>/cache/opencode-pty"),
    ("Bash", "command", "ln -s <profile>/config <profile>/tmp/c"),
    # More ways to write the same places.
    ("Write", "file_path", "<profile>/runtime/node_modules/.bin/opencode"),
    ("Write", "file_path", "<profile>/data/opencode/new.db"),
    ("Write", "file_path", "<profile>/cache/new-file"),
    ("Write", "file_path", "<profile>/state/serve/port"),
    ("Write", "file_path", "<profile>/new-top-level-file"),
    ("Write", "file_path", "<coven>/src/coven/cli.py"),
    ("Write", "file_path", "<coven>/src/coven/new_module.py"),
    ("Write", "file_path", "<coven>/hooks/new_hook.py"),
    ("Write", "file_path", "<coven>/opencode/src/new/file.ts"),
    ("Write", "file_path", "<profile>/tmp/../config/opencode/opencode.jsonc"),
    ("Write", "file_path", "~/profile/config/opencode/opencode.jsonc"),
    ("NotebookEdit", "notebook_path", "<profile>/config/x.ipynb"),
    ("Write", "file_path", "<project>/to-hooks/deny_secrets.py"),
    ("Write", "file_path", "to-hooks/deny_secrets.py"),
    ("Bash", "command", "cat > <profile>/plugin/coven/index.ts"),
    ("Bash", "command", "cd <profile>/config && echo x > opencode/opencode.jsonc"),
    ("Bash", "command", "cd <profile> && echo x > plugin/coven/index.ts"),
    ("Bash", "command", "sed -i s/a/b/ <profile>/config/opencode/opencode.jsonc"),
    ("Bash", "command", "echo x >> <coven>/hooks/deny_secrets.py"),
    ("Bash", "command", "rm <coven>/opencode/src/guard.ts"),
    ("Bash", "command", "cp x <profile>/tmp/to-config/opencode/opencode.jsonc"),
    ("Bash", "command", "ln -s ../config <profile>/tmp/c2 && cd <profile>/tmp"),
    ("Bash", "command", "cd <profile>/tmp && ln -s ../config c"),
    ("Bash", "command", "cat <profile>/config/opencode/opencode.jsonc"),
    ("Bash", "command", "cat ~/profile/config/opencode/opencode.jsonc"),
    ("Bash", "command", "cat $HOME/profile/data/opencode/opencode.db"),
    ("Bash", "command", "bash -c 'echo x > <profile>/config/a'"),
    ("Bash", "command", "touch <profile>/config/opencode/ag*"),
    ("Bash", "command", "ls <coven>/hooks"),
    ("Bash", "command", "cp <project>/to-hooks/deny_secrets.py /dev/null"),
    # The whole coven root is closed to writes, but its worktrees folder.
    ("Write", "file_path", "<coven>/bin/coven"),
    ("Write", "file_path", "<coven>/opencode/content/AGENTS.md"),
    ("Write", "file_path", "<coven>/pyproject.toml"),
    ("Write", "file_path", "<coven>/docs/notes.md"),
    ("Edit", "file_path", "<coven>/.claude/settings.json"),
    ("Write", "file_path", "<coven>/.claude/worktrees/x/to-src/coven/cli.py"),
    ("Bash", "command", "echo x > <coven>/pyproject.toml"),
    ("Bash", "command", "cat <coven>/bin/coven"),
    ("Bash", "command", "cd <coven> && echo x > pyproject.toml"),
]
PROTECTED_ALLOWED = [
    ("Write", "file_path", "<profile>/tmp/body.md"),
    ("Edit", "file_path", "<other>/coven-worktree/hooks/deny_secrets.py"),
    ("Write", "file_path", "<project>/notes.md"),
    ("Write", "file_path", "notes.md"),
    ("Write", "file_path", "<profile>/tmp/new/dir/file.txt"),
    ("Write", "file_path", "<coven>/.claude/worktrees/x/hooks/deny_secrets.py"),
    ("Edit", "file_path", "<coven>/.claude/worktrees/x/new.md"),
    ("Read", "file_path", "<coven>/pyproject.toml"),
    ("Read", "file_path", "<coven>/hooks/deny_secrets.py"),
    ("Grep", "path", "<coven>/src"),
    ("Bash", "command", "echo x > <profile>/tmp/body.md"),
    ("Bash", "command", "git -C <coven>/.claude/worktrees/x status"),
    ("Bash", "command", "cd <profile>/tmp && echo x > body.md"),
    ("Bash", "command", "cp <other>/coven-worktree/hooks/deny_secrets.py <project>/copy.py"),
    ("Bash", "command", "git status"),
    ("Bash", "command", "ls"),
    ("Bash", "command", "npm test"),
    ("Bash", "command", "cat README.md"),
    ("Bash", "command", "echo x > body.md"),
]


@pytest.mark.parametrize(("tool", "field", "value"), PROTECTED_REFUSED)
def test_the_protected_set_is_refused(world: World, tool: str, field: str, value: str) -> None:
    assert world.call(tool, field, value)


@pytest.mark.parametrize(("tool", "field", "value"), PROTECTED_ALLOWED)
def test_writes_beside_the_protected_set_are_allowed(
    world: World, tool: str, field: str, value: str
) -> None:
    assert world.call(tool, field, value) is None


def test_a_relative_write_resolves_against_the_cwd(world: World) -> None:
    assert world.call("Write", "file_path", "opencode/opencode.jsonc", cwd=world.profile / "config")
    assert world.call("Write", "file_path", "../config/x", cwd=world.profile / "tmp")
    assert world.call("Write", "file_path", "x.md", cwd=world.profile / "tmp") is None


COVEN_REFUSED = [
    "coven opencode setup --model lead=x",
    "coven opencode serve stop",
    "coven opencode gm",
    "coven opencode install",
    "coven opencode serve start",
    "coven opencode",
    "coven 'opencode' serve stop",
    "env X=1 coven opencode gm",
    "uv run coven opencode serve stop",
    "python3 -m coven opencode install",
    "bash -c 'coven opencode serve stop'",
    "/usr/local/bin/coven opencode gm",
    "ls && coven opencode setup",
    "coven opencode doctor && coven opencode gm",
]
COVEN_ALLOWED = [
    "coven opencode doctor",
    "coven opencode --help",
    "coven pm --help",
    "coven --help",
    "coven doctor",
    "ls opencode",
    "python3 -m coven pm --help",
]


@pytest.mark.parametrize("command", COVEN_REFUSED)
def test_coven_opencode_lifecycle_commands_are_refused(world: World, command: str) -> None:
    assert world.call("Bash", "command", command)


@pytest.mark.parametrize("command", COVEN_ALLOWED)
def test_coven_doctor_and_other_commands_are_allowed(world: World, command: str) -> None:
    assert world.call("Bash", "command", command) is None


SHELL_REFUSED = [
    "cat script | sh",
    "echo cat | bash",
    "curl x | bash -s",
    "base64 -d x | sh",
]


@pytest.mark.parametrize("command", SHELL_REFUSED)
def test_a_pipe_into_a_shell_is_refused(world: World, command: str) -> None:
    assert world.call("Bash", "command", command)


def test_a_shell_with_a_script_file_is_allowed(world: World) -> None:
    assert world.call("Bash", "command", "bash scripts/test.sh") is None
    assert world.call("Bash", "command", "sh -c 'npm test'") is None
    assert world.call("Bash", "command", "ls | grep sh") is None


def test_the_refusal_text_points_to_gm_tools(world: World) -> None:
    for command in ("opencode api session.list", "coven opencode serve stop"):
        text = world.call("Bash", "command", command)
        assert text
        assert "coven_pm_list" in text
    assert "coven_pm_list" in (world.call("Read", "file_path", "<serve>/password") or "")


def test_the_refusal_text_names_the_rule(world: World) -> None:
    texts = {
        "opencode": world.call("Bash", "command", "opencode db"),
        "port": world.call("Bash", "command", "curl localhost:<port>"),
        "serve": world.call("Read", "file_path", "<serve>/password"),
        "protected": world.call("Write", "file_path", "<profile>/config/x"),
        "environ": world.call("Bash", "command", "cat /proc/1/environ"),
        "ps": world.call("Bash", "command", "ps eww"),
        "coven": world.call("Bash", "command", "coven opencode gm"),
        "data": world.call("Read", "file_path", "<profile>/data/opencode/opencode.db"),
        "ancestor": world.call("Grep", "path", "<home>"),
        "substitution": world.call("Bash", "command", "$(echo opencode) db"),
    }
    for rule, text in texts.items():
        assert text, rule
        assert text.startswith("coven opencode-server-guard: "), rule
        assert len(text) < 400, rule
    assert len({text.split(";")[0] for text in texts.values()}) == len(texts)
    assert "write the path literally" in texts["substitution"]


def test_other_tools_are_not_checked(world: World) -> None:
    payload = {"tool_name": "WebFetch", "tool_input": {"url": "http://localhost:43123"}, "cwd": "/"}
    assert guard.refusal(payload, world.profile, world.serve, world.coven, world.port) is None


@pytest.mark.parametrize(
    "payload",
    [
        {"tool_name": "Bash", "tool_input": {"command": 7}, "cwd": "/"},
        {"tool_name": "Bash", "tool_input": "ls", "cwd": "/"},
        {"tool_name": "Bash", "tool_input": {}, "cwd": "/"},
        {"tool_name": "Write", "tool_input": {"file_path": 7}, "cwd": "/"},
        {"tool_name": "Read", "tool_input": {}, "cwd": "/"},
        {"tool_name": "Bash", "tool_input": {"command": "ls"}, "cwd": 7},
        {"tool_name": "Bash", "tool_input": {"command": "ls"}},
        {"tool_input": {"command": "ls"}, "cwd": "/"},
        {"tool_name": "Bash", "tool_input": {"command": "ls", "workdir": 3}, "cwd": "/"},
    ],
)
def test_a_payload_the_guard_cannot_read_is_refused(
    world: World, payload: dict[str, object]
) -> None:
    assert guard.refusal(payload, world.profile, world.serve, world.coven, world.port)


def test_a_command_nested_too_deeply_is_refused(world: World) -> None:
    command = "ls"
    for _ in range(12):
        command = "bash -c " + json.dumps(command)
    assert world.call("Bash", "command", command)


DATA_AND_ANCESTORS_REFUSED = [
    # The profile's data folder (credentials, sessions) is closed to reads too.
    ("Read", "file_path", "<profile>/data/opencode/auth.json"),
    ("Read", "file_path", "<profile>/data/opencode/opencode.db"),
    ("Read", "file_path", "<profile>/tmp/../data/opencode/opencode.db"),
    ("Grep", "path", "<profile>/data"),
    ("Glob", "path", "<profile>/data"),
    ("Glob", "pattern", "<profile>/data/**/*.db"),
    # A folder that holds the serve folder or the data folder is closed to the file tools.
    ("Grep", "path", "<profile>"),
    ("Grep", "path", "<profile>/state"),
    ("Grep", "path", "<home>"),
    ("Grep", "path", "/"),
    ("Glob", "path", "<profile>"),
    ("Read", "file_path", "<profile>"),
    ("Read", "file_path", "<home>"),
    ("Glob", "pattern", "<profile>/*"),
    ("Glob", "pattern", "<profile>/st*"),
    ("Glob", "pattern", "<home>/*"),
    # And to every shell program that may read what is under it.
    ("Bash", "command", "grep -r . <home>"),
    ("Bash", "command", "grep -r . <profile>/state"),
    ("Bash", "command", "grep -r . /"),
    ("Bash", "command", "grep -r x .."),
    ("Bash", "command", "cp -r <home> <project>/copy"),
    ("Bash", "command", "cp -r <root> <project>/copy"),
    ("Bash", "command", "rm -r <home>"),
    ("Bash", "command", "tar cf - -C <home> profile | tar xOf -"),
    ("Bash", "command", "rsync -a <home>/ <project>/copy/"),
    ("Bash", "command", "find <home> -type f -exec cat {} +"),
    ("Bash", "command", "cd <home> && grep -r . ."),
    ("Bash", "command", "cd <home> && cp -r . <project>/copy"),
    ("Bash", "command", "cat <home>/*/state/serve/password"),
    ("Bash", "command", "ls <home> | grep x"),
    ("Bash", "command", "grep -r . <coven>/.."),
    ("Bash", "command", "bash -c 'grep -r . <home>'"),
    ("Bash", "command", "grep -r . ~"),
]
DATA_AND_ANCESTORS_ALLOWED = [
    ("Read", "file_path", "<profile>/plugin/coven/index.ts"),
    ("Grep", "path", "<profile>/plugin"),
    ("Grep", "path", "<profile>/config"),
    ("Grep", "path", "<project>"),
    ("Glob", "pattern", "<project>/*.md"),
    ("Glob", "pattern", "<profile>/config/**/*.md"),
    ("Bash", "command", "ls <home>"),
    ("Bash", "command", "ls -la ~"),
    ("Bash", "command", "cd <home>"),
    ("Bash", "command", "cd .. && ls"),
    ("Bash", "command", "stat <home>"),
    ("Bash", "command", "test -d <home> && echo yes"),
    ("Bash", "command", "du -sh <root>"),
    ("Bash", "command", "mkdir -p <root>/out"),
    ("Bash", "command", "dirname <home>/profile.d"),
    ("Bash", "command", "echo <home>"),
    ("Bash", "command", "grep -r foo ."),
    ("Bash", "command", "grep -r foo <project>"),
    ("Bash", "command", "grep -rn x /etc/hostname"),
]


@pytest.mark.parametrize(("tool", "field", "value"), DATA_AND_ANCESTORS_REFUSED)
def test_the_data_folder_and_the_folders_above_the_serve_folder_are_refused(
    world: World, tool: str, field: str, value: str
) -> None:
    assert world.call(tool, field, value)


@pytest.mark.parametrize(("tool", "field", "value"), DATA_AND_ANCESTORS_ALLOWED)
def test_reads_that_cannot_reach_the_serve_or_data_folder_stay_allowed(
    world: World, tool: str, field: str, value: str
) -> None:
    assert world.call(tool, field, value) is None


def test_a_grep_or_glob_with_no_path_reads_the_cwd(world: World) -> None:
    assert world.call("Grep", "pattern", "password", cwd=world.profile.parent)
    assert world.call("Grep", "pattern", "password", cwd=world.profile)
    assert world.call("Glob", "pattern", "*.md", cwd=world.profile.parent)
    assert world.call("Grep", "pattern", "password") is None
    assert world.call("Glob", "pattern", "*.md") is None


def test_the_coven_worktrees_folder_is_open_to_the_shell_and_the_file_tools(world: World) -> None:
    worktree = world.coven / ".claude" / "worktrees" / "x"
    assert world.call("Bash", "command", "cat hooks/deny_secrets.py", cwd=worktree) is None
    assert world.call("Bash", "command", "git status", cwd=worktree) is None
    assert world.call("Write", "file_path", "hooks/new.py", cwd=worktree) is None
    assert world.call("Bash", "command", "cat ../../../pyproject.toml", cwd=worktree)
    assert world.call("Write", "file_path", "to-src/coven/cli.py", cwd=worktree)


SUBSTITUTION_REFUSED = [
    "$(echo opencode) db",
    "`echo opencode` db",
    "$(printf opencode) api session.list",
    "cat $(echo <home>)/profile/state/serve/password",
    "d=$(echo <home>); cat $d/profile/state/serve/password",
    "x=`echo <home>`; cat $x/profile/state/serve/password",
    'cd "$(printf %s <profile>/state)" && cat serve/password',
    "cd $(echo <profile>/state) && cat serve/password",
    "d=$(echo <profile>/state); cd $d && cat serve/password",
    'sqlite3 "$(printenv XDG_DATA_HOME)"/opencode/opencode.db',
    "echo x > `echo <home>`/profile/config/opencode/opencode.jsonc",
    "d=$(echo <profile>/tmp/body.md); echo x > $d",
    "cat /opt/$(echo x)",
    "env $(echo opencode) db",
    "sudo -u x $(echo opencode) db",
    "FOO=1 `echo opencode` db",
    "bash -c '$(echo opencode) db'",
    "ls && $(echo opencode) db",
    "(`echo opencode` db)",
    "echo ok; $(echo opencode) db",
]
SUBSTITUTION_ALLOWED = [
    "echo $(date)",
    "echo `date`",
    "echo $(date) > out.txt",
    'echo "today is $(date +%F)"',
    "x=$(date); echo $x",
    "sha=$(git rev-parse HEAD); git log $sha",
    "git commit -m \"$(cat <<'EOF'\nmessage\nEOF\n)\"",
    "echo $((1 + 2))",
]


@pytest.mark.parametrize("command", SUBSTITUTION_REFUSED)
def test_a_substitution_that_builds_a_program_or_a_path_is_refused(
    world: World, command: str
) -> None:
    text = world.call("Bash", "command", command)
    assert text
    assert "write the path literally" in text


@pytest.mark.parametrize("command", SUBSTITUTION_ALLOWED)
def test_a_substitution_in_an_argument_is_allowed(world: World, command: str) -> None:
    assert world.call("Bash", "command", command) is None


@pytest.mark.parametrize("name", sorted(wrapper_commands.WRAPPERS | wrapper_commands.LAUNCHERS))
def test_every_command_the_shared_table_names_is_followed_to_opencode(
    world: World, name: str
) -> None:
    assert world.call("Bash", "command", f"{name} opencode db")


@pytest.mark.parametrize("name", sorted(wrapper_commands.SHELLS))
def test_every_shell_of_the_shared_table_runs_command_strings_and_stdin(
    world: World, name: str
) -> None:
    assert world.call("Bash", "command", f"{name} -c 'opencode db'")
    assert world.call("Bash", "command", f"{name} -c 'cat <serve>/password'")
    assert world.call("Bash", "command", f"cat script | {name}")
    assert world.call("Bash", "command", f"{name} -c 'npm test'") is None
    assert world.call("Bash", "command", f"{name} -c 'opencode --version'") is None


def test_a_glob_that_reaches_the_match_cap_is_refused(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    for index in range(5):
        touch(world.project / "many" / f"f{index}.txt")
    touch(world.project / "few" / "a.txt")
    touch(world.project / "few" / "b.txt")
    monkeypatch.setattr(guard, "MAX_GLOB_MATCHES", 3)
    for tool, field, value in (
        ("Bash", "command", "cat <project>/many/*.txt"),
        ("Glob", "pattern", "<project>/many/*.txt"),
    ):
        text = world.call(tool, field, value)
        assert text
        assert "too many matches; narrow the glob" in text
    assert world.call("Bash", "command", "cat <project>/few/*.txt") is None
    assert world.call("Glob", "pattern", "<project>/few/*.txt") is None


# main: the way Claude Code and the plugin run it.


def run_main(
    world: World, payload: object, *extra: str, serve: Path | None = None
) -> subprocess.CompletedProcess[str]:
    arguments = [
        "--profile",
        str(world.profile),
        "--serve",
        str(serve or world.serve),
        "--coven-root",
        str(world.coven),
        *extra,
    ]
    stdin = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run(
        [sys.executable, "-I", str(SCRIPT), *arguments],
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
        cwd=world.project,
    )


def bash_payload(world: World, command: str) -> dict[str, object]:
    return {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(world.project)}


def test_main_allows_with_exit_0_and_no_output(world: World) -> None:
    proc = run_main(world, bash_payload(world, "npm test"))
    assert (proc.returncode, proc.stdout, proc.stderr) == (0, "", "")


def test_main_refuses_with_exit_2_and_the_text_on_stderr(world: World) -> None:
    proc = run_main(world, bash_payload(world, "opencode db"))
    assert proc.returncode == 2
    assert proc.stdout == ""
    assert proc.stderr.startswith("coven opencode-server-guard: ")
    assert "coven_pm_list" in proc.stderr


def test_main_reads_the_port_from_the_port_file_at_call_time(world: World) -> None:
    assert run_main(world, bash_payload(world, "curl http://127.0.0.1:8080/")).returncode == 0
    assert run_main(world, bash_payload(world, "curl http://127.0.0.1:43123/")).returncode == 2
    touch(world.serve / "port", "8080\n")
    assert run_main(world, bash_payload(world, "curl http://127.0.0.1:8080/")).returncode == 2
    assert run_main(world, bash_payload(world, "curl http://127.0.0.1:43123/")).returncode == 0


@pytest.mark.parametrize("content", ["", "abc", "0", "70000", "-5", "4096 4097"])
def test_a_port_file_with_no_valid_port_counts_as_no_port(world: World, content: str) -> None:
    touch(world.serve / "port", content)
    assert run_main(world, bash_payload(world, "curl http://127.0.0.1:8080/")).returncode == 2
    assert run_main(world, bash_payload(world, "curl https://example.com/")).returncode == 0


def test_no_port_file_counts_as_no_port(world: World) -> None:
    (world.serve / "port").unlink()
    assert run_main(world, bash_payload(world, "curl localhost:8080")).returncode == 2
    assert run_main(world, bash_payload(world, "opencode api x")).returncode == 2
    assert run_main(world, bash_payload(world, "opencode --version")).returncode == 0


@pytest.mark.parametrize("payload", ["", "not json", "[]", "7", '{"tool_name": "Bash"'])
def test_main_refuses_a_payload_it_cannot_parse(world: World, payload: str) -> None:
    proc = run_main(world, payload)
    assert proc.returncode == 2
    assert proc.stderr.startswith("coven opencode-server-guard: ")


@pytest.mark.parametrize(
    "arguments",
    [
        [],
        ["--profile", "p", "--serve", "s"],
        ["--profile", "p", "--coven-root", "c"],
        ["--serve", "s", "--coven-root", "c"],
        ["--profile", "p", "--serve", "s", "--coven-root", "c", "--bogus"],
    ],
)
def test_main_refuses_when_its_arguments_are_missing_or_wrong(
    world: World, arguments: list[str]
) -> None:
    proc = subprocess.run(
        [sys.executable, "-I", str(SCRIPT), *arguments],
        input=json.dumps(bash_payload(world, "ls")),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 2
    assert "coven opencode-server-guard" in proc.stderr


def test_main_refuses_when_the_check_itself_fails(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*_args: object) -> str | None:
        raise RuntimeError("boom")

    monkeypatch.setattr(guard, "refusal", broken)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(bash_payload(world, "ls"))))
    code = guard.main(
        [
            "--profile",
            str(world.profile),
            "--serve",
            str(world.serve),
            "--coven-root",
            str(world.coven),
        ]
    )
    assert code == 2
