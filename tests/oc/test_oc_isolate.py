"""The wrapper that runs a PM's shell in its own namespaces (`hooks/pm-shell-isolate.sh`).

The tests run the script with a fake `unshare` and a fake `mount` first on PATH, a temp tree in place
of `/proc/sys` and a fake real shell. Each fake appends one JSON line (tool, argv, env) to a log and
then runs the rest of its argv or fails with a stderr text the test gives. No test needs namespaces
or root: the real run is the e2e's job.
"""

import json
import os
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from support import REPO

WRAPPER = REPO / "hooks" / "pm-shell-isolate.sh"
OUTER = (
    "unshare",
    "--user",
    "--map-root-user",
    "--pid",
    "--fork",
    "--kill-child",
    "--mount",
    "--mount-proc",
)
PREFIX = "coven: PM shells run isolated and isolation is not available here: "
SUFFIX = "; ask the user"
# What the wrapper and its inner stage need besides the fakes.
TOOLS = ("sh", "id", "mktemp", "rm", "tr", "cat", "true")
HELP = (
    "Usage: unshare [options] [<program> [<argument>...]]\n  --map-user=<uid>  map current user\n"
)
OLD_HELP = "Usage: unshare [options] [<program> [<argument>...]]\n  --map-root-user   map root\n"

FAKE = f"""#!{sys.executable}
import json
import os
import sys

tool = os.path.basename(sys.argv[0])
argv = sys.argv[1:]
stdin = sys.stdin.read() if tool == "shell" else None
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write(
        json.dumps({{"tool": tool, "pid": os.getpid(), "argv": argv, "stdin": stdin, "env": dict(os.environ)}}) + "\\n"
    )
if tool == "shell":
    sys.stderr.write(os.environ.get("FAKE_SHELL_ERR", ""))
    sys.exit(int(os.environ.get("FAKE_SHELL_EXIT", "0")))
if tool == "mount":
    if os.environ.get("FAKE_MOUNT_FAIL"):
        sys.stderr.write(os.environ["FAKE_MOUNT_FAIL"])
        sys.exit(32)
    sys.exit(0)
if argv == ["--help"]:
    sys.stdout.write({OLD_HELP!r} if os.environ.get("FAKE_UNSHARE_OLD") else {HELP!r})
    sys.exit(0)
if os.environ.get("FAKE_UNSHARE_FAIL") and "--map-root-user" in argv:
    sys.stderr.write(os.environ["FAKE_UNSHARE_FAIL"])
    sys.exit(1)
if os.environ.get("FAKE_NESTED_FAIL") and any(a.startswith("--map-user=") for a in argv):
    sys.stderr.write(os.environ["FAKE_NESTED_FAIL"])
    sys.exit(1)
rest = list(argv)
while rest and rest[0].startswith("--"):
    rest.pop(0)
os.execvp(rest[0], rest)
"""


@dataclass
class World:
    root: Path
    bin: Path
    sys_root: Path
    log: Path
    hide: Path
    other: Path
    shell: Path
    caller_bin: Path
    pwned: Path
    last_pid: int = 0

    def write_sys(self, relative: str, value: str) -> None:
        path = self.sys_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value + "\n")

    def calls(self, tool: str) -> list[dict[str, Any]]:
        if not self.log.exists():
            return []
        lines = [json.loads(line) for line in self.log.read_text().splitlines()]
        return [line for line in lines if line["tool"] == tool]

    def unshare_runs(self) -> list[list[str]]:
        """The argv of every `unshare` run that was not the `--help` question."""
        return [c["argv"] for c in self.calls("unshare") if c["argv"] != ["--help"]]

    def preflight_runs(self) -> list[list[str]]:
        """The outer unshare asked to run `true`, to learn whether it works before it is used."""
        return [a for a in self.unshare_runs() if a[7:] == ["true"]]

    def outer_runs(self) -> list[list[str]]:
        """The outer unshare that runs the inner stage (`sh -c <stage> ...`)."""
        return [a for a in self.unshare_runs() if a[8:9] == ["-c"] and len(a) > 9]

    def nested_runs(self) -> list[list[str]]:
        """The nested unshare that restores the user, asked first to run `true`, then the shell."""
        return [
            a for a in self.unshare_runs() if a[:1] == ["--user"] and a[1].startswith("--map-user=")
        ]

    def run(
        self,
        command: str = "echo hi",
        *,
        path: Path | None = None,
        hide: str | None = None,
        stdin: str = "",
        **extra: str,
    ) -> subprocess.CompletedProcess[str]:
        env = {
            # The caller's PATH has fakes that mark an attack; the trusted list is the seam's.
            "PATH": str(self.caller_bin),
            "COVEN_ISOLATE_PATH": str(path or self.bin),
            "COVEN_ISOLATE_SYS": str(self.sys_root),
            "COVEN_PM_REAL_SHELL": str(self.shell),
            "COVEN_PM_HIDE": f"{self.hide}:{self.root / 'absent'}:{self.other}"
            if hide is None
            else hide,
            "FAKE_LOG": str(self.log),
            "TMPDIR": str(self.root),
        }
        env.update(extra)
        env = {key: value for key, value in env.items() if value != "<unset>"}
        started = subprocess.Popen(
            [str(WRAPPER), "-c", command],
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=self.root,
        )
        self.last_pid = started.pid
        out, err = started.communicate(stdin)
        return subprocess.CompletedProcess(started.args, started.returncode, out, err)


def refusal(piece: str) -> str:
    return f"{PREFIX}{piece}{SUFFIX}\n"


@pytest.fixture
def world(tmp_path: Path) -> World:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("unshare", "mount", "shell"):
        fake = bin_dir / name
        fake.write_text(FAKE)
        fake.chmod(0o755)
    for name in TOOLS:
        found = shutil.which(name)
        assert found, name
        (bin_dir / name).symlink_to(found)
    hide, other = tmp_path / "serve", tmp_path / "data"
    hide.mkdir()
    other.mkdir()
    made = World(
        tmp_path,
        bin_dir,
        tmp_path / "proc-sys",
        tmp_path / "log.jsonl",
        hide,
        other,
        bin_dir / "shell",
        tmp_path / "caller-bin",
        tmp_path / "pwned",
    )
    # What a PM can plant ahead of /usr/bin: a tool of each name that marks the attack and fails.
    made.caller_bin.mkdir()
    for name in (*TOOLS, "unshare", "mount"):
        evil = made.caller_bin / name
        evil.write_text(f"#!/bin/sh\necho {name} >> '{made.pwned}'\nexit 99\n")
        evil.chmod(0o755)
    made.write_sys("user/max_user_namespaces", "79976")
    made.write_sys("kernel/unprivileged_userns_clone", "1")
    made.write_sys("kernel/apparmor_restrict_unprivileged_userns", "0")
    return made


def test_the_script_is_a_posix_sh_file_that_can_run() -> None:
    assert WRAPPER.read_text().startswith("#!/bin/sh\n")
    assert WRAPPER.stat().st_mode & stat.S_IXUSR
    mode = subprocess.run(
        ["git", "ls-files", "-s", str(WRAPPER)],
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO,
    ).stdout.split()[0]
    assert mode == "100755"


def test_the_outer_unshare_has_exactly_the_documented_flags(world: World) -> None:
    done = world.run()
    assert done.returncode == 0, done.stderr
    (outer,) = world.outer_runs()
    assert tuple(["unshare", *outer[:7]]) == OUTER
    assert outer[7:9] == [str(world.bin / "sh"), "-c"]
    # The inner stage is a fixed script: the caller's values arrive as arguments after it.
    assert outer[10] == "sh"
    assert outer[11] == str(world.shell)
    assert outer[12] == str(os.getuid())
    assert outer[13] == str(os.getgid())
    assert outer[14] == f"{world.hide}:{world.root / 'absent'}:{world.other}"
    assert outer[15] == str(world.bin / "unshare")
    assert outer[16] == str(world.caller_bin)
    assert outer[17:] == ["-c", "echo hi"]


def test_the_outer_unshare_replaces_the_wrapper_so_a_kill_reaches_the_namespace(
    world: World,
) -> None:
    done = world.run()
    assert done.returncode == 0, done.stderr
    # The same process: the wrapper exec'd it, so a signal for the wrapper is a signal for unshare,
    # and `--kill-child` takes the namespace's processes down with it.
    outer_calls = [c for c in world.calls("unshare") if c["argv"][8:9] == ["-c"]]
    assert [c["pid"] for c in outer_calls] == [world.last_pid]
    assert "--kill-child" in outer_calls[0]["argv"]


def test_a_preflight_with_the_same_flags_runs_before_the_outer_unshare(world: World) -> None:
    done = world.run()
    assert done.returncode == 0, done.stderr
    (preflight,) = world.preflight_runs()
    assert tuple(["unshare", *preflight]) == (*OUTER, "true")
    runs = world.unshare_runs()
    assert runs.index(preflight) < runs.index(world.outer_runs()[0])
    # It is a separate process: only the exec'd one is the wrapper's own pid.
    pids = [c["pid"] for c in world.calls("unshare") if c["argv"][7:] == ["true"]]
    assert world.last_pid not in pids


def test_inside_a_tmpfs_hides_each_folder_that_exists_then_the_user_is_restored(
    world: World,
) -> None:
    done = world.run("echo hi")
    assert done.returncode == 0, done.stderr
    mounts = [call["argv"] for call in world.calls("mount")]
    assert mounts == [
        ["-t", "tmpfs", "tmpfs", str(world.hide)],
        ["-t", "tmpfs", "tmpfs", str(world.other)],
    ]
    order = [call["tool"] for call in map(json.loads, world.log.read_text().splitlines())]
    mounted, nested = order.index("mount"), len(order) - 1 - order[::-1].index("unshare")
    assert mounted < nested < order.index("shell")
    user = ["--user", f"--map-user={os.getuid()}", f"--map-group={os.getgid()}"]
    # The nested unshare is first tried on `true`, so a failure can be told from the command's.
    assert world.nested_runs() == [[*user, "true"], [*user, str(world.shell), "-c", "echo hi"]]


COMMANDS = (
    "echo 'it'\"'\"'s' \"$HOME\" $(id) `x` \\n *.py ?",
    "line one\nline two with a trailing space \n\ttabbed $0 $1 ${2}\n",
    "-c",
    "--",
    "",
)


@pytest.mark.parametrize("command", COMMANDS)
def test_the_command_reaches_the_real_shell_byte_for_byte(world: World, command: str) -> None:
    done = world.run(command)
    assert done.returncode == 0, done.stderr
    assert world.calls("shell")[0]["argv"] == ["-c", command]
    (outer,) = world.outer_runs()
    if len(command) > 5:  # shorter texts also occur among the wrapper's own flags
        assert command not in outer[9]
        assert outer.count(command) == 1
        assert world.nested_runs()[1].count(command) == 1


def test_the_real_shells_exit_code_and_stderr_pass_through(world: World) -> None:
    done = world.run(FAKE_SHELL_EXIT="7", FAKE_SHELL_ERR="from the shell\n")
    assert done.returncode == 7
    assert done.stderr == "from the shell\n"
    assert done.stdout == ""


def test_the_commands_stdin_reaches_the_real_shell(world: World) -> None:
    done = world.run(stdin="piped in\nsecond line\n")
    assert done.returncode == 0, done.stderr
    assert world.calls("shell")[0]["stdin"] == "piped in\nsecond line\n"


def test_the_namespaces_first_process_takes_term_and_kills_the_rest(world: World) -> None:
    # The first process of a PID namespace ignores a TERM it has no handler for, and `--kill-child`
    # only acts when unshare dies. So the stage that is that process traps TERM, INT and HUP and
    # kills everything in the namespace. Sending the signal here would kill the test machine's
    # processes (the fake runs no namespace), so the real behaviour is the manual check's job.
    assert world.run().returncode == 0
    (outer,) = world.outer_runs()
    script = outer[9]
    assert "trap 'kill -s KILL -- -1; exit 143' TERM INT HUP" in script
    assert script.index("trap ") < script.index("mount -t tmpfs")


def test_a_folder_that_does_not_exist_is_skipped(world: World) -> None:
    done = world.run(hide=str(world.root / "absent"))
    assert done.returncode == 0, done.stderr
    assert world.calls("mount") == []
    assert len(world.calls("shell")) == 1


def test_a_hide_list_with_glob_text_is_not_expanded(world: World) -> None:
    folder = world.root / "a*b"
    folder.mkdir()
    (world.root / "axb").mkdir()
    done = world.run(hide=str(folder))
    assert done.returncode == 0, done.stderr
    assert [call["argv"][-1] for call in world.calls("mount")] == [str(folder)]


def test_a_mount_that_fails_refuses_and_the_command_does_not_run(world: World) -> None:
    done = world.run(FAKE_MOUNT_FAIL="mount: permission denied\nsecond line\n")
    assert done.returncode == 126
    assert done.stderr == refusal(
        f"cannot hide {world.hide} (mount: permission denied second line)"
    )
    assert world.calls("shell") == []


@pytest.mark.parametrize("name", ["COVEN_PM_REAL_SHELL", "COVEN_PM_HIDE"])
@pytest.mark.parametrize("value", ["<unset>", ""])
def test_a_missing_env_value_refuses_before_anything_runs(
    world: World, name: str, value: str
) -> None:
    done = world.run(**{name: value})
    assert done.returncode == 126
    assert done.stderr == refusal(f"{name} is not set")
    assert not world.log.exists()


def test_a_real_shell_that_is_not_an_absolute_path_refuses(world: World) -> None:
    done = world.run(COVEN_PM_REAL_SHELL="bash")
    assert done.returncode == 126
    assert done.stderr == refusal("COVEN_PM_REAL_SHELL is not an absolute path")
    assert not world.log.exists()


def test_a_real_shell_that_is_missing_or_not_executable_refuses_before_any_unshare(
    world: World,
) -> None:
    plain = world.root / "plain-file"
    plain.write_text("#!/bin/sh\n")
    plain.chmod(0o644)
    for bad in (world.root / "no-such-shell", plain, world.root):
        done = world.run(COVEN_PM_REAL_SHELL=str(bad))
        assert done.returncode == 126, bad
        assert done.stderr == refusal("COVEN_PM_REAL_SHELL is not an executable file")
    assert not world.log.exists()


def test_unshare_missing_refuses(world: World) -> None:
    (world.bin / "unshare").unlink()
    done = world.run()
    assert done.returncode == 126
    assert done.stderr == refusal("unshare not found")
    assert world.calls("shell") == []
    assert not world.pwned.exists()  # the caller's own unshare is never a fallback


@pytest.mark.parametrize("tool", ["sh", "id", "mktemp", "rm", "tr", "mount"])
def test_a_tool_the_wrapper_needs_that_is_missing_refuses(world: World, tool: str) -> None:
    (world.bin / tool).unlink()
    done = world.run()
    assert done.returncode == 126
    assert done.stderr == refusal(f"{tool} not found")
    assert world.calls("shell") == []
    assert not world.pwned.exists()


def test_the_tools_come_from_the_trusted_path_never_from_the_callers(world: World) -> None:
    # A PM can put an `unshare` that answers --help and drops the flags first on its own PATH.
    done = world.run("echo hi")
    assert done.returncode == 0, done.stderr
    assert not world.pwned.exists()
    # Every outer, nested and mount run was the trusted one, and the command sees the caller's PATH.
    assert len(world.outer_runs()) == 1
    assert len(world.nested_runs()) == 2
    assert len(world.calls("mount")) == 2
    assert world.calls("shell")[0]["env"]["PATH"] == str(world.caller_bin)


def test_the_stage_runs_on_the_trusted_path_until_the_real_shell(world: World) -> None:
    assert world.run().returncode == 0
    for call in world.calls("mount"):
        assert call["env"]["PATH"] == str(world.bin)
    probe, real = (
        c
        for c in world.calls("unshare")
        if c["argv"][1:2] and c["argv"][1].startswith("--map-user=")
    )
    assert probe["env"]["PATH"] == str(world.bin)
    assert real["env"]["PATH"] == str(world.caller_bin)


def test_util_linux_older_than_2_38_refuses(world: World) -> None:
    done = world.run(FAKE_UNSHARE_OLD="1")
    assert done.returncode == 126
    assert done.stderr == refusal("util-linux is older than 2.38 (unshare has no --map-user)")
    assert world.unshare_runs() == []
    assert world.calls("shell") == []


def test_user_namespaces_off_by_the_limit_refuses(world: World) -> None:
    world.write_sys("user/max_user_namespaces", "0")
    done = world.run()
    assert done.returncode == 126
    assert done.stderr == refusal("user namespaces are off (max_user_namespaces is 0)")
    assert world.unshare_runs() == []
    assert world.calls("shell") == []


def test_user_namespaces_off_by_the_debian_switch_refuses(world: World) -> None:
    world.write_sys("kernel/unprivileged_userns_clone", "0")
    done = world.run()
    assert done.returncode == 126
    assert done.stderr == refusal("user namespaces are off (unprivileged_userns_clone is 0)")
    assert world.unshare_runs() == []


def test_apparmor_blocking_user_namespaces_refuses(world: World) -> None:
    world.write_sys("kernel/apparmor_restrict_unprivileged_userns", "1")
    done = world.run()
    assert done.returncode == 126
    assert done.stderr == refusal(
        "AppArmor blocks unprivileged user namespaces (apparmor_restrict_unprivileged_userns is 1)"
    )
    assert world.unshare_runs() == []


def test_files_the_kernel_does_not_have_do_not_refuse(world: World) -> None:
    (world.sys_root / "kernel" / "unprivileged_userns_clone").unlink()
    (world.sys_root / "kernel" / "apparmor_restrict_unprivileged_userns").unlink()
    assert world.run().returncode == 0


def test_the_first_missing_piece_in_the_order_wins(world: World) -> None:
    world.write_sys("user/max_user_namespaces", "0")
    world.write_sys("kernel/unprivileged_userns_clone", "0")
    world.write_sys("kernel/apparmor_restrict_unprivileged_userns", "1")
    assert world.run().stderr == refusal("user namespaces are off (max_user_namespaces is 0)")
    world.write_sys("user/max_user_namespaces", "5")
    assert world.run().stderr == refusal("user namespaces are off (unprivileged_userns_clone is 0)")
    world.write_sys("kernel/unprivileged_userns_clone", "1")
    assert world.run().stderr.startswith(f"{PREFIX}AppArmor blocks")
    assert world.run(FAKE_UNSHARE_OLD="1").stderr.startswith(f"{PREFIX}util-linux is older")


def test_an_unshare_that_fails_without_a_known_cause_reports_its_stderr(world: World) -> None:
    done = world.run(FAKE_UNSHARE_FAIL="unshare: unshare failed: Operation not permitted\nmore\n")
    assert done.returncode == 126
    assert done.stderr == refusal(
        "unshare failed: unshare: unshare failed: Operation not permitted more"
    )
    assert world.calls("shell") == []
    assert world.outer_runs() == []
    assert len(world.preflight_runs()) == 1


def test_a_nested_unshare_that_fails_refuses_with_its_stderr(world: World) -> None:
    done = world.run(FAKE_NESTED_FAIL="unshare: write failed /proc/self/uid_map\nsecond\n")
    assert done.returncode == 126
    assert done.stderr == refusal(
        "cannot restore the user inside the namespaces (unshare: write failed "
        "/proc/self/uid_map second)"
    )
    assert world.calls("shell") == []
    assert len(world.nested_runs()) == 1


def test_the_test_seam_moves_the_diagnosis_files_and_cannot_skip_the_unshare(
    world: World,
) -> None:
    # A seam pointing at a tree that says everything is fine still runs the unshare.
    done = world.run()
    assert done.returncode == 0, done.stderr
    assert len(world.outer_runs()) == 1
    # Pointing it at a folder that does not exist leaves nothing to refuse on: unshare still runs.
    done = world.run(COVEN_ISOLATE_SYS=str(world.root / "nowhere"))
    assert done.returncode == 0, done.stderr
    assert len(world.outer_runs()) == 2
    # And a failing unshare refuses whatever the seam says.
    done = world.run(FAKE_UNSHARE_FAIL="nope")
    assert done.returncode == 126
    assert done.stderr == refusal("unshare failed: nope")


def test_the_temp_file_for_unshares_stderr_is_removed(world: World) -> None:
    before = set(world.root.iterdir())
    world.run()
    world.run(FAKE_UNSHARE_FAIL="nope")
    assert set(world.root.iterdir()) - before == {world.log}


@pytest.mark.shellcheck
def test_the_script_passes_shellcheck() -> None:
    venv_bin = str(Path(sys.executable).parent)
    shellcheck = shutil.which("shellcheck", path=venv_bin) or shutil.which("shellcheck")
    if shellcheck is None:  # a test marked `shellcheck` fails loudly: a skip would hide a gap
        pytest.fail("shellcheck is missing: run `uv sync` (the dev group holds shellcheck-py)")
    done = subprocess.run(
        [shellcheck, "--shell=sh", str(WRAPPER)], capture_output=True, text=True, check=False
    )
    assert done.returncode == 0, done.stdout
