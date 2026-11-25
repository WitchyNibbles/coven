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
            "<projects>": self.coven.parent,
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
    # Not under the profile's parents: a folder above the coven root is not above the profile.
    coven = tmp_path / "projects" / "coven"
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
    payload = {"tool_name": "TodoWrite", "tool_input": {"todos": "localhost:43123"}, "cwd": "/"}
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
    ("Bash", "command", "rg foo <home>"),
    ("Bash", "command", "cd <home> && git status"),
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


COVEN_ANCESTOR_REFUSED = [
    "rm -r <projects>",
    "cp -r <projects> <project>/copy",
    "mv <projects> <project>/copy",
    "rsync -a <projects>/ <project>/copy/",
    "tar cf <project>/x.tar <projects>",
    "find <projects> -name x -exec sed -i s/a/b/ {} +",
    "cd <projects> && rm -r .",
    "bash -c 'rm -r <projects>'",
    "cat <coven>/.. > <project>/out",
    "cat x > <projects>/..",
]
COVEN_ANCESTOR_ALLOWED = [
    "cd <projects> && git status",
    "rg foo <projects>",
    "grep -r foo <projects>",
    "ls <projects>",
    "cat <coven>/../other.md",
    "find <project> -name x",
    "git -C <projects> status",
]


@pytest.mark.parametrize("command", COVEN_ANCESTOR_REFUSED)
def test_a_folder_above_the_coven_root_is_refused_on_a_line_that_writes(
    world: World, command: str
) -> None:
    assert world.call("Bash", "command", command)


@pytest.mark.parametrize("command", COVEN_ANCESTOR_ALLOWED)
def test_a_folder_above_the_coven_root_is_open_to_programs_that_only_read(
    world: World, command: str
) -> None:
    assert world.call("Bash", "command", command) is None


def test_the_coven_and_profile_ancestors_follow_their_own_rules(world: World) -> None:
    assert world.call("Bash", "command", "rg foo <projects>") is None
    assert world.call("Bash", "command", "rg foo <home>")
    assert world.call("Bash", "command", "rg foo <root>")
    assert world.call("Bash", "command", "ls <home> <projects>") is None
    assert world.call("Grep", "path", "<projects>") is None
    assert world.call("Read", "file_path", "<projects>") is None
    assert world.call("Write", "file_path", "<projects>/new.md") is None


def test_a_folder_that_is_an_option_value_is_a_path_not_a_program(world: World) -> None:
    refused = ["npm --prefix opencode test", "tsc -p opencode", "xargs opencode"]
    for command in refused:
        assert world.call("Bash", "command", command)  # no such folder here
    touch(world.project / "opencode" / "package.json")
    assert world.call("Bash", "command", "npm --prefix opencode test") is None
    assert world.call("Bash", "command", "tsc -p opencode") is None
    assert world.call("Bash", "command", "npm --prefix ./opencode/ run build") is None
    assert world.call("Bash", "command", "npm --prefix opencode exec opencode db")
    assert world.call("Bash", "command", "xargs opencode")
    assert world.call("Bash", "command", "npm --prefix opencode exec -- opencode db")
    assert world.call("Bash", "command", "tsc -p opencode-ai") is not None


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
    "git commit -m \"$(cat <<'EOF'\nmessage\nEOF\n)\"",
    "echo $((1 + 2))",
]


@pytest.mark.parametrize("command", SUBSTITUTION_REFUSED)
def test_a_substitution_that_builds_a_program_or_a_path_is_refused(
    world: World, command: str
) -> None:
    text = world.call("Bash", "command", command)
    assert text
    assert "inline the substitution or write the path literally" in text


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


def call_in(world: World, command: str, workdir: Path) -> str | None:
    """A shell call whose working folder is `workdir`."""
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": command, "workdir": str(workdir)},
        "cwd": str(world.project),
    }
    return guard.refusal(payload, world.profile, world.serve, world.coven, world.port)


# Round 3, A: a program run in a folder that holds a closed place reads it without naming a path.
READS_IN_THE_WORKING_FOLDER = [
    "rg password",
    "grep -r password",
    "grep -R password",
    "git grep --no-index password",
    "ag password",
    "find -type f -name password",
]
BARE_CD_REFUSED = [
    "cd && grep -r --include=password ''",
    "cd -P && grep -rh --include=password ''",
    "cd -L && grep -r x .",
    "cd -- && grep -r x .",
    "cd && rg --hidden -g password ''",
    "cd ; git grep --no-index password",
    "cd ~ && rg password",
    "cd $HOME && rg password",
]
BARE_CD_ALLOWED = ["cd && ls", "cd -P && pwd", "cd .. && ls", "cd && ls -la", "cd -- && stat ."]


@pytest.mark.parametrize("command", READS_IN_THE_WORKING_FOLDER)
def test_a_program_that_reads_the_working_folder_is_refused_above_the_profile(
    world: World, command: str
) -> None:
    assert call_in(world, command, world.profile.parent)
    assert call_in(world, command, world.root)
    assert call_in(world, command, world.project) is None


def test_a_listing_in_a_folder_above_the_profile_is_allowed(world: World) -> None:
    assert call_in(world, "ls", world.profile.parent) is None
    assert call_in(world, "pwd", world.root) is None
    assert call_in(world, "cd .. && ls", world.profile.parent) is None


@pytest.mark.parametrize("command", BARE_CD_REFUSED)
def test_a_cd_to_a_folder_above_the_profile_then_a_reading_program_is_refused(
    world: World, command: str
) -> None:
    assert world.call("Bash", "command", command)


@pytest.mark.parametrize("command", BARE_CD_ALLOWED)
def test_a_cd_then_a_program_that_only_lists_is_allowed(world: World, command: str) -> None:
    assert world.call("Bash", "command", command) is None


# Round 3, B: a variable set from a command substitution has no value the guard knows.
UNKNOWN_VARIABLE_REFUSED = [
    "x=$(echo opencode); $x db",
    "x=`echo opencode`; $x db",
    "X=$(echo b3BlbmNvZGUgZGI= | base64 -d); eval $X",
    'X=$(echo b3BlbmNvZGUgZGI= | base64 -d); bash -c "$X"',
    "X=$(echo b3BlbmNvZGUgZGI= | base64 -d); $X",
    "d=$(echo <serve>/passwor)d; cat $d",
    "export X=$(date); sh -c $X",
    # Ruling 20: after eval, -c, source or `.`, next to a `/`, or as a cd target.
    "X=$(date); source $X",
    "X=$(date); . $X",
    'X=$(date); sh -c "$X"',
    'X=$(date); eval "$X"',
    "d=$(date); cat $d/password",
    "d=$(date); cat ${d}/password",
    "d=$(date); cd $d && ls",
]
UNKNOWN_VARIABLE_ALLOWED = [
    "d=$(date); echo $d",
    'd=$(date); echo "$d"',
    "d=$(date); printf %s $d",
    "x=1; cat README.md",
    "d=$(date)",
    # Ruling 20: a plain argument of any program.
    "sha=$(git rev-parse HEAD); git log $sha",
    "pid=$(cat p.pid); kill $pid",
    "branch=$(git branch --show-current); git push origin $branch",
    "d=$(date); cat $d",
    'd=$(date); cat "$d"',
    "d=$(date); cat ${d}",
    "d=$(date); cp $d <project>/copy",
]


@pytest.mark.parametrize("command", UNKNOWN_VARIABLE_REFUSED)
def test_a_variable_set_from_a_substitution_is_refused_where_it_runs_or_builds_a_path(
    world: World, command: str
) -> None:
    assert world.call("Bash", "command", command)


@pytest.mark.parametrize("command", UNKNOWN_VARIABLE_ALLOWED)
def test_a_variable_set_from_a_substitution_may_be_a_plain_argument(
    world: World, command: str
) -> None:
    assert world.call("Bash", "command", command) is None


# Round 3, C: a folder named opencode is a path only where a program is known to take one.
OPENCODE_FOLDER_REFUSED = [
    "uv run -q opencode db",
    "uv run --no-project opencode db",
    "uvx -q opencode db",
    "pipx run --no-cache opencode db",
    "pdm run -v opencode db",
    "conda run --no-capture-output opencode db",
    "valgrind -q opencode db",
    "firejail --quiet opencode db",
    "prlimit --nofile=1024 opencode db",
    "gdb --args opencode db",
    "ssh-agent -s opencode db",
    "dbus-run-session --config-file=x opencode db",
    "npm --prefix opencode exec opencode db",
    "npm --prefix opencode exec -- opencode db",
    "env npm --prefix opencode test",
    "tsc -x opencode",
    "npm -x opencode test",
]
OPENCODE_FOLDER_ALLOWED = [
    "npm --prefix opencode test",
    "tsc -p opencode",
    "tsc --project opencode",
    "pnpm -C opencode test",
    "yarn --dir opencode test",
    "npm --prefix ./opencode run build",
    "npm --prefix opencode/ run build",
    "git -C opencode status",
]


@pytest.mark.parametrize("command", OPENCODE_FOLDER_REFUSED)
def test_a_folder_named_opencode_is_not_a_path_after_a_launcher(world: World, command: str) -> None:
    touch(world.project / "opencode" / "package.json")
    assert world.call("Bash", "command", command)


@pytest.mark.parametrize("command", OPENCODE_FOLDER_ALLOWED)
def test_a_folder_named_opencode_is_a_path_for_a_program_that_takes_one(
    world: World, command: str
) -> None:
    touch(world.project / "opencode" / "package.json")
    assert world.call("Bash", "command", command) is None


# Round 3, D: options and variables that make git, man and rg run a command.
RUNS_A_COMMAND_REFUSED = [
    "git bisect run opencode db",
    "git bisect run sh -c 'echo x'",
    "git -c alias.x='!opencode db' x",
    "git -c core.pager='opencode db' log",
    "git -c core.sshCommand=x fetch",
    "git -ccore.pager=x log",
    "git -C . -c core.pager=x log",
    "git rebase --exec 'opencode db' HEAD~1",
    "git rebase -x 'opencode db' HEAD~1",
    "git submodule foreach 'opencode db'",
    "git difftool -y -x 'opencode db'",
    "git difftool --extcmd=x",
    "GIT_PAGER='opencode db' git log",
    "GIT_SSH_COMMAND=x git fetch",
    "export GIT_EXTERNAL_DIFF=x",
    "man -P 'opencode db' ls",
    "man --pager=x ls",
    "PAGER='opencode db' man ls",
    "MANPAGER=x man ls",
    "rg --pre opencode x",
    "rg --pre-glob '*.x' y",
    "env git -c core.pager=x log",
    # Round 4, M1: a short-flag bundle that holds the option.
    "git rebase -qx 'opencode db' HEAD~1",
    "git difftool -yx 'opencode db'",
    "man -aP 'opencode db' ls",
    # Round 4, M7: an assignment before the program, under env, or exported.
    "env GIT_PAGER=x git log",
    "sudo GIT_PAGER=x git log",
    "export PAGER=x; man ls",
    "EDITOR=x git commit",
    "bash -c 'GIT_PAGER=x git log'",
]
RUNS_A_COMMAND_ALLOWED = [
    "git commit -m 'fix opencode db'",
    "git log --oneline",
    "git bisect start",
    "git rebase -i HEAD~2",
    "git submodule status",
    "git difftool HEAD~1",
    "git -C . status",
    "man ls",
    "rg foo",
    "git commit -m 'set PAGER later'",
    # Round 4, M1: the same bundles without the option.
    "git rebase -qi HEAD~2",
    "git difftool -y HEAD~1",
    "man -a ls",
    # Round 4, M7: the names as text, not as an assignment.
    "git commit -m 'docs: set PAGER=less in the guide'",
    "echo GIT_DIR=x",
    "grep -rn GIT_PAGER= .",
]


@pytest.mark.parametrize("command", RUNS_A_COMMAND_REFUSED)
def test_an_option_or_variable_that_makes_a_program_run_a_command_is_refused(
    world: World, command: str
) -> None:
    assert world.call("Bash", "command", command)


@pytest.mark.parametrize("command", RUNS_A_COMMAND_ALLOWED)
def test_the_same_programs_without_such_an_option_are_allowed(world: World, command: str) -> None:
    assert world.call("Bash", "command", command) is None


# Round 3, E: a folder that holds the coven root is closed unless every program only reads.
COVEN_ANCESTOR_WRITERS_REFUSED = [
    "zip -rm <project>/z.zip <projects>",
    "gio trash <projects>",
    "7z a -sdel <project>/z.7z <projects>",
    "chgrp -R x <projects>",
    "setfacl -R -m u:x:rwx <projects>",
    "git -C <projects> clean -fdx",
    "find <projects> -delete",
    "find <projects> -name x -fprint <project>/out",
    "git -C <projects> branch -D x",
    "echo x <projects>",
]
COVEN_ANCESTOR_READERS_ALLOWED = [
    "cd <projects> && git status",
    "rg foo <projects>",
    "git -C <projects> status",
    "find <projects> -name x",
    "git -C <projects> branch",
    "git -C <projects> branch --list",
    "git -C <projects> log --oneline",
    "tree <projects>",
    "ag foo <projects>",
]


@pytest.mark.parametrize("command", COVEN_ANCESTOR_WRITERS_REFUSED)
def test_a_folder_above_the_coven_root_is_refused_to_a_program_that_is_not_read_only(
    world: World, command: str
) -> None:
    assert world.call("Bash", "command", command)


@pytest.mark.parametrize("command", COVEN_ANCESTOR_READERS_ALLOWED)
def test_a_folder_above_the_coven_root_is_open_to_the_read_only_allowlist(
    world: World, command: str
) -> None:
    assert world.call("Bash", "command", command) is None


def test_a_working_folder_above_the_coven_root_follows_the_same_rule(world: World) -> None:
    projects = world.coven.parent
    assert call_in(world, "find -delete", projects)
    assert call_in(world, "find -name pyproject.toml -delete", projects)
    assert call_in(world, "find . -exec rm {} +", projects)
    assert call_in(world, "git commit -am x", projects)
    assert call_in(world, "npm test", projects)
    assert call_in(world, "find -name pyproject.toml", projects) is None
    assert call_in(world, "rg foo", projects) is None
    assert call_in(world, "git status", projects) is None
    assert call_in(world, "ls", projects) is None


# Round 3, F: Python and bash read some glob brackets differently.
GLOB_BRACKETS_REFUSED = [
    "cat <home>/profil[^x]/state/serve/password",
    "cat <home>/profil[!x]/state/serve/password",
    "cat <home>/profil[[:alpha:]]/state/serve/password",
    "cat <home>/profil[[.e.]]/state/serve/password",
    "cat <profile>/stat[^x]/serve/password",
    "cd <home> && cat profil[^x]/state/serve/password",
    "cat <home>/zzz*/x",
    "cat <profile>/state/zzz*",
]
GLOB_BRACKETS_ALLOWED = [
    "cat <project>/zzz*",
    "cat <project>/*.nomatch",
    "cat <project>/[^x]*.md",
    "grep '[^a-z]' README.md",
    "grep -E '[[:alpha:]]+' README.md",
    "echo '[!x]'",
    "ls <home>/zzz*",
]


@pytest.mark.parametrize("command", GLOB_BRACKETS_REFUSED)
def test_a_glob_python_and_bash_read_differently_or_that_matches_nothing_is_refused(
    world: World, command: str
) -> None:
    assert world.call("Bash", "command", command)


@pytest.mark.parametrize("command", GLOB_BRACKETS_ALLOWED)
def test_the_same_globs_away_from_a_closed_place_are_allowed(world: World, command: str) -> None:
    assert world.call("Bash", "command", command) is None


def test_the_file_tools_refuse_a_glob_that_matches_nothing_above_a_closed_place(
    world: World,
) -> None:
    assert world.call("Glob", "pattern", "<profile>/state/zzz*")
    assert world.call("Glob", "pattern", "<profile>/stat[^x]/serve/*")
    assert world.call("Glob", "pattern", "<project>/zzz*") is None
    assert world.call("Glob", "pattern", "<profile>/config/zzz*") is None


# Round 3, G: a shell that reads a process substitution is a pipe into a shell.
PROCESS_SUBSTITUTION_REFUSED = [
    "bash <(echo ls)",
    "source <(echo ls)",
    ". <(echo ls)",
    "sh -x <(cat x)",
    "zsh < <(echo ls)",
    "exec bash <(curl x)",
    "dash <(echo ls)",
    # Round 4, N2: a shell given by its path.
    "/bin/bash <(echo opencode db)",
    "/usr/bin/bash <(echo opencode db)",
    "/bin/sh < <(echo ls)",
    "env /bin/bash <(echo ls)",
]
PROCESS_SUBSTITUTION_ALLOWED = [
    "diff <(sort a) <(sort b)",
    "cat <(echo hi)",
    "./script.sh <(echo hi)",
    "while read x; do echo $x; done < <(ls)",
    "/usr/bin/diff <(sort a) <(sort b)",
    "/bin/cat <(echo hi)",
]


@pytest.mark.parametrize("command", PROCESS_SUBSTITUTION_REFUSED)
def test_a_shell_that_reads_a_process_substitution_is_refused(world: World, command: str) -> None:
    assert world.call("Bash", "command", command)


@pytest.mark.parametrize("command", PROCESS_SUBSTITUTION_ALLOWED)
def test_a_process_substitution_for_another_program_is_allowed(world: World, command: str) -> None:
    assert world.call("Bash", "command", command) is None


# Round 4, N1: a `cd` whose target the guard cannot know (an unset variable goes home, so does a
# bare `cd` with a redirect; `cd -` goes back) is closed before a program that reads.
UNKNOWN_CD_REFUSED = [
    "cd $XDG_DATA_HOME && grep -r password",
    "cd $XDG_CONFIG_HOME && grep -r password",
    "cd ${NOPE} && grep -r password",
    "cd $NOPE && rg password",
    "cd 2>/dev/null && rg password",
    "cd >/dev/null; grep -r password",
    "cd - && grep -r password",
    "cd $OLDPWD && grep -r password",
    "cd $NOPE && cd state && cat serve/password",
]
UNKNOWN_CD_ALLOWED = [
    "cd $NOPE && ls",
    "cd - && pwd",
    "cd 2>/dev/null && ls",
    "cd <project> 2>/dev/null && rg password",
    "cd <project> >/dev/null && grep -r password",
]
UNSET_IN_THE_HOOK = ("XDG_DATA_HOME", "XDG_CONFIG_HOME", "NOPE", "OLDPWD")


@pytest.mark.parametrize("command", UNKNOWN_CD_REFUSED)
def test_a_cd_to_a_folder_the_guard_cannot_know_is_refused_before_a_reading_program(
    world: World, command: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in UNSET_IN_THE_HOOK:
        monkeypatch.delenv(name, raising=False)
    assert world.call("Bash", "command", command)


@pytest.mark.parametrize("command", UNKNOWN_CD_ALLOWED)
def test_a_cd_the_guard_cannot_know_then_a_listing_is_allowed(
    world: World, command: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in UNSET_IN_THE_HOOK:
        monkeypatch.delenv(name, raising=False)
    assert world.call("Bash", "command", command) is None


def test_an_unknown_cd_target_is_refused_where_home_does_not_hold_the_profile(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("NOPE", raising=False)
    monkeypatch.setenv("HOME", str(world.project))
    assert world.call("Bash", "command", "cd $NOPE && grep -r password")
    assert world.call("Bash", "command", "cd - && rg password")
    assert world.call("Bash", "command", "cd $NOPE && ls") is None


# Round 4, N3: `source` and `.` read a pipe, a heredoc or a here-string as a shell does.
SOURCE_STDIN_REFUSED = [
    "echo 'opencode db' | source /dev/stdin",
    "echo 'opencode db' | . /dev/stdin",
    "source /dev/stdin <<< 'opencode db'",
    ". /dev/stdin <<< 'opencode db'",
    "source /dev/stdin <<<'opencode db'",
    "echo ls | source /dev/stdin",
    "source /dev/stdin <<EOF\nopencode db\nEOF",
]
SOURCE_STDIN_ALLOWED = [
    "source scripts/setup.sh",
    ". scripts/setup.sh",
    "source /dev/stdin <<< 'echo hi'",
    "cat <<< 'opencode db'",
    "echo ls | cat",
]


@pytest.mark.parametrize("command", SOURCE_STDIN_REFUSED)
def test_source_reading_a_pipe_or_a_here_string_is_refused(world: World, command: str) -> None:
    assert world.call("Bash", "command", command)


@pytest.mark.parametrize("command", SOURCE_STDIN_ALLOWED)
def test_source_of_a_file_or_a_harmless_string_is_allowed(world: World, command: str) -> None:
    assert world.call("Bash", "command", command) is None


# PR A fix wave, 1: a quoted heredoc body is literal text, not a command substitution.
HEREDOC_REFUSED = [
    "cat <<EOF\n$(echo opencode) db\nEOF",
    "gh pr create --title t --body-file - <<EOF\n`echo opencode` db\nEOF",
    "bash <<'EOF'\nopencode db\nEOF",
    "sh <<'EOF'\n`echo opencode` db\nEOF",
]
HEREDOC_ALLOWED = [
    "gh pr create --title t --body-file - <<'EOF'\n## Summary\n\n`a` b\n\n```bash\nnpm test\n```\nEOF",
    "gh pr create --title t --body \"$(cat <<'EOF'\n## Summary\n`a` b\n```\nx\n```\nEOF\n)\"",
    "git commit -F - <<'EOF'\nfix: x\n\n`a` b\nEOF",
    'git commit -F - <<"EOF"\n`a` b\n$(c) d\nEOF',
    "git commit -F - <<\\EOF\n`a` b\nEOF",
    "git commit -F - <<-'EOF'\n\t`a` b\n\tEOF",
]


@pytest.mark.parametrize("command", HEREDOC_REFUSED)
def test_an_unquoted_heredoc_or_one_into_a_shell_is_still_refused(
    world: World, command: str
) -> None:
    assert world.call("Bash", "command", command)


@pytest.mark.parametrize("command", HEREDOC_ALLOWED)
def test_a_quoted_heredoc_body_with_backticks_is_literal_text(world: World, command: str) -> None:
    assert world.call("Bash", "command", command) is None


# PR A fix wave, 2: a working folder in the coven checkout runs the read-only allowlist and adds
# worktrees under .claude/worktrees/.
IN_COVEN_ALLOWED = [
    "git status",
    "git log --oneline",
    "git diff HEAD~1",
    "git worktree list",
    "git worktree add .claude/worktrees/t1 -b t1",
    "git worktree add -b t1 .claude/worktrees/t1",
    "git worktree add <coven>/.claude/worktrees/t2 main",
    "rg foo",
    "cat pyproject.toml",
    "ls hooks",
    "git status 2>/dev/null",
]
IN_COVEN_REFUSED = [
    "touch x",
    "git commit -am x",
    "rm pyproject.toml",
    "git worktree add /tmp/x",
    "git worktree add ../x",
    "git worktree remove .claude/worktrees/x",
    "npm test",
    "echo x > notes.md",
    "cat <profile>/profile.json",
]


@pytest.mark.parametrize("command", IN_COVEN_ALLOWED)
def test_a_working_folder_in_the_coven_checkout_runs_reading_programs(
    world: World, command: str
) -> None:
    assert call_in(world, world.fill(command), world.coven) is None


@pytest.mark.parametrize("command", IN_COVEN_REFUSED)
def test_a_working_folder_in_the_coven_checkout_refuses_writes(world: World, command: str) -> None:
    text = call_in(world, world.fill(command), world.coven)
    assert text


def test_the_coven_checkout_refusal_names_the_worktrees_folder(world: World) -> None:
    text = call_in(world, "touch x", world.coven)
    assert text
    assert ".claude/worktrees/" in text


# PR A fix wave, 3: from a working folder that holds the profile, a line that first moves to a
# literal folder holding no closed place is judged from there.
FROM_HOME_ALLOWED = [
    "cd proj && git log",
    "git -C proj status",
    "cd proj && grep -r x .",
    "pushd proj && rg x",
    "env -C proj grep -r x .",
    "cd proj && npm test",
]
FROM_HOME_REFUSED = [
    "grep -r password",
    "cat notes.md",
    "cd proj && cd .. && grep -r password",
    "cd . && grep -r password",
    "cd ~ && grep -r password",
    "git -C . grep password",
    "git -C proj status; grep -r password",
    "git -C proj status && grep -r password",
    "(cd proj && ls); grep -r password",
    "pushd proj && popd && grep -r password",
    "cd missing; grep -r password",
    "cd proj && cat ../profile/state/serve/password",
    "cd profile && ls -R",
]


@pytest.fixture
def home_with_a_project(world: World) -> Path:
    home = world.profile.parent
    touch(home / "proj" / "README.md")
    touch(home / "notes.md")
    return home


@pytest.mark.parametrize("command", FROM_HOME_ALLOWED)
def test_a_line_that_first_moves_into_a_project_is_judged_from_there(
    world: World, home_with_a_project: Path, command: str
) -> None:
    assert call_in(world, command, home_with_a_project) is None


@pytest.mark.parametrize("command", FROM_HOME_REFUSED)
def test_a_reading_program_run_from_a_folder_that_holds_the_profile_is_refused(
    world: World, home_with_a_project: Path, command: str
) -> None:
    assert call_in(world, command, home_with_a_project)


def test_the_working_folder_refusal_says_to_cd_into_a_project(
    world: World, home_with_a_project: Path
) -> None:
    text = call_in(world, "grep -r password", home_with_a_project)
    assert text
    assert "the working folder holds coven's profile; cd into a project folder first" in text


# PR A fix wave, 5: `opencode.exe` and `opencode serve` as one word name opencode.
OPENCODE_WORD_REFUSED = [
    "pkill -f 'opencode serve'",
    "pkill -f opencode.exe",
    "killall opencode.exe",
    "opencode.exe db",
]
OPENCODE_WORD_ALLOWED = ["pkill -f vite", "killall node", "grep 'opencode serve' notes.md"]


@pytest.mark.parametrize("command", OPENCODE_WORD_REFUSED)
def test_a_word_that_names_opencode_with_a_suffix_is_refused(world: World, command: str) -> None:
    assert world.call("Bash", "command", command)


@pytest.mark.parametrize("command", OPENCODE_WORD_ALLOWED)
def test_other_process_names_are_allowed(world: World, command: str) -> None:
    assert world.call("Bash", "command", command) is None


# PR A fix wave, 7: WebFetch to the server's port.
def test_web_fetch_to_the_server_port_is_refused(world: World) -> None:
    for url in (f"http://127.0.0.1:{PORT}/session", f"http://localhost:{PORT}/api"):
        assert world.call("WebFetch", "url", url)
    assert world.call("WebFetch", "url", "https://example.com/") is None


# PR A fix wave, 10 (Task 4 minors).
NULL_REDIRECTS_ALLOWED = [
    "ls <projects> 2>/dev/null",
    "ls <projects> >/dev/null",
    "ls <projects> &>/dev/null",
    "ls <projects> 2>&1",
    "rg foo <projects> 2>/dev/null | head",
]
WAVE_MINORS_REFUSED = [
    "ls <projects> > x",
    "ls <projects> 2>x",
    "x=$(echo opencode); uv run $x db",
    "x=$(echo opencode); uvx $x db",
    "x=$(echo opencode); pipx run $x db",
    "x=$(echo opencode); pdm run $x db",
    "x=$(echo opencode); poetry run $x db",
    "cd ~- && grep -r password",
    "cd ~-/ && grep -r password",
    "pushd ~- && grep -r password",
    "cd ~1 && grep -r password",
    "cd ~+1 && grep -r password",
    "cd ~-1 && grep -r password",
    "GIT_PAGER+=x git log",
    "git rebase --ex 'opencode db' HEAD~1",
    "git rebase --exe='opencode db' HEAD~1",
    "git difftool --ext 'opencode db'",
    "git difftool --ex='opencode db'",
    "man --pag 'opencode db' ls",
    "man --page='opencode db' ls",
    "GIT_PAGER=more git log",
    "GIT_PAGER=$X git log",
    "PAGER='cat x' man ls",
    "GIT_EDITOR=cat git commit",
]
WAVE_MINORS_ALLOWED = [
    *NULL_REDIRECTS_ALLOWED,
    "x=$(date); uv run pytest -k $x",
    "cd ~+ && grep -r x",
    "cd ~- && ls",
    "git rebase --edit-todo",
    "git rebase --empty=drop HEAD~1",
    "git difftool --dir-diff HEAD~1",
    "man --prompt=x ls",
    "GIT_PAGER=cat git log",
    "GIT_PAGER=less git log",
    "GIT_PAGER= git log",
    "GIT_PAGER='' git log",
    "PAGER=cat man ls",
]


@pytest.mark.parametrize("command", WAVE_MINORS_REFUSED)
def test_the_task_4_minors_of_the_fix_wave_are_refused(world: World, command: str) -> None:
    assert world.call("Bash", "command", command)


@pytest.mark.parametrize("command", WAVE_MINORS_ALLOWED)
def test_their_near_allows_stay_open(world: World, command: str) -> None:
    assert world.call("Bash", "command", command) is None


def test_the_unknown_cd_refusal_says_to_write_the_folder_literally(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("NOPE", raising=False)
    text = world.call("Bash", "command", "cd $NOPE && grep -r password")
    assert text
    assert "write the folder literally" in text


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
