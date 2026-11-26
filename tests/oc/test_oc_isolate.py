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
    "--mount",
    "--mount-proc",
)
PREFIX = "coven: PM shells run isolated and isolation is not available here: "
SUFFIX = "; ask the user"
# What the wrapper and its inner stage need besides the fakes.
TOOLS = ("sh", "id", "mktemp", "rm", "tr", "cat")
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
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write(json.dumps({{"tool": tool, "argv": argv, "env": dict(os.environ)}}) + "\\n")
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
if os.environ.get("FAKE_UNSHARE_FAIL"):
    sys.stderr.write(os.environ["FAKE_UNSHARE_FAIL"])
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

    def run(
        self,
        command: str = "echo hi",
        *,
        path: Path | None = None,
        hide: str | None = None,
        **extra: str,
    ) -> subprocess.CompletedProcess[str]:
        env = {
            "PATH": str(path or self.bin),
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
        return subprocess.run(
            [str(WRAPPER), "-c", command],
            env=env,
            capture_output=True,
            text=True,
            check=False,
            cwd=self.root,
        )


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
    )
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
    outer = world.unshare_runs()[0]
    assert tuple(["unshare", *outer[:6]]) == OUTER
    assert outer[6:9] == ["sh", "-c", outer[8]]
    # The inner stage is a fixed script: the caller's values arrive as arguments after it.
    assert outer[9] == "sh"
    assert outer[10] == str(world.shell)
    assert outer[11] == str(os.getuid())
    assert outer[12] == str(os.getgid())
    assert outer[13] == f"{world.hide}:{world.root / 'absent'}:{world.other}"
    assert outer[14:] == ["-c", "echo hi"]


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
    assert world.unshare_runs()[1] == [
        "--user",
        f"--map-user={os.getuid()}",
        f"--map-group={os.getgid()}",
        str(world.shell),
        "-c",
        "echo hi",
    ]


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
    outer = world.unshare_runs()[0]
    if len(command) > 5:  # shorter texts also occur among the wrapper's own flags
        assert command not in outer[8]
        assert outer.count(command) == 1
        assert world.unshare_runs()[1].count(command) == 1


def test_the_real_shells_exit_code_and_stderr_pass_through(world: World) -> None:
    done = world.run(FAKE_SHELL_EXIT="7", FAKE_SHELL_ERR="from the shell\n")
    assert done.returncode == 7
    assert done.stderr == "from the shell\n"
    assert done.stdout == ""


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


def test_unshare_missing_refuses(world: World) -> None:
    (world.bin / "unshare").unlink()
    done = world.run()
    assert done.returncode == 126
    assert done.stderr == refusal("unshare not found")
    assert world.calls("shell") == []


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


def test_the_test_seam_moves_the_diagnosis_files_and_cannot_skip_the_unshare(
    world: World,
) -> None:
    # A seam pointing at a tree that says everything is fine still runs the unshare.
    done = world.run()
    assert done.returncode == 0, done.stderr
    assert len(world.unshare_runs()) == 2
    # Pointing it at a folder that does not exist leaves nothing to refuse on: unshare still runs.
    done = world.run(COVEN_ISOLATE_SYS=str(world.root / "nowhere"))
    assert done.returncode == 0, done.stderr
    assert len(world.unshare_runs()) == 4
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
