"""The commands that run another command, by name and by option: what prod_guard.py,
opencode_server_guard.py and pm_permissions.py all read a shell line with.

One table, so that a wrapper one guard learns is a wrapper for the others too. Each hook loads this
module by path (`hook_support.sibling("wrapper_commands")`). The name sets say which words run
another command; `WRAPPER_SYNTAX` says how a wrapper's options are read (`sudo -u root claude`), as
the real program reads them. How each guard follows a wrapper (what an option it does not know
means) is its own.
"""

import re
from dataclasses import dataclass

# Shells, and busybox's shell applets (`busybox sh -c ...` names `sh`).
SHELLS = frozenset(
    {"sh", "bash", "zsh", "dash", "fish", "ksh", "mksh", "ash", "hush", "csh", "tcsh"}
)

# Words that read a heredoc body, a pipe, a `<<<` string or a process substitution as shell
# commands, or start a shell without naming one: a body fed to one of them is shell text, and its
# globs are real.
SHELL_READERS = frozenset(
    {
        *SHELLS,
        *("source", ".", "eval", "parallel", "make", "su", "sudo", "doas", "script"),
        *("screen", "tmux"),
    }
)

# The words a shell puts before a command (`if`, `!`, `{`): not a wrapper's own options.
SHELL_KEYWORDS = frozenset(
    {"if", "then", "else", "elif", "do", "while", "until", "!", "coproc", "{", "}"}
)

# Wrappers whose options the table lists in full (`flags` and `valued`), so that a guard that
# follows them to the command they run knows every option it meets: an option that is in neither
# is one it cannot follow, and it checks the wrapper's words whole instead. A name is added here
# only with its full table.
WALKED = frozenset(
    {
        *("exec", "env", "nohup", "command", "builtin", "sudo", "time", "timeout", "xargs", "nice"),
    }
)

# Wrappers that run the rest of their words as a command, with options the guards that do not
# follow them read as words (a push, merge, send-pack, gh or claude word among them counts). A
# name here may have a table in WRAPPER_SYNTAX (valued options only: opencode_server_guard reads
# it) without being in WALKED.
EXEC_WRAPPERS = frozenset(
    {
        *(
            "setsid",
            "stdbuf",
            "ionice",
            "flock",
            "taskset",
            "chrt",
            "unshare",
            "nsenter",
            "setpriv",
        ),
        *("script", "doas", "su", "runuser", "chroot", "watch", "strace", "ltrace", "nocache"),
        *("unbuffer", "faketime", "firejail", "systemd-run", "sg", "numactl", "cgexec"),
        *("parallel", "proxychains", "proxychains4", "torsocks", "pkexec", "ssh", "busybox"),
        *("npx", "bunx", "pnpx", "screen", "tmux"),
    }
)

# Commands that run their arguments as a command, so `<wrapper> ... claude` starts claude. `eval`,
# `make` and `trap` take a command string, or run recipes (make) that hold commands. A command
# string (`sh -c`, `su -c`, `script -c`) is read as the option's value, quotes dropped.
WRAPPERS = frozenset({*SHELLS, *SHELL_KEYWORDS, *WALKED, *EXEC_WRAPPERS, *("eval", "make", "trap")})

# Wrappers that take one positional argument before the command: timeout's duration, flock's lock
# file, chrt's priority, taskset's mask, su's and runuser's user, script's log file, chroot's new
# root, sg's group, ssh's host.
POSITIONAL_WRAPPERS = frozenset(
    {*("timeout", "flock", "chrt", "taskset", "su", "runuser", "script", "chroot", "sg", "ssh")}
)

# Wrappers whose command may follow any of their own words (`tmux new-session -d -s pm 'claude'`).
ANYWHERE_WRAPPERS = frozenset({"tmux", "screen"})

# Wrappers that run a command string given after an option holding `c` (`sh -c`, `su -lc`).
COMMAND_STRING_RUNNERS = frozenset({*SHELLS, "su", "runuser", "script", "flock"})


@dataclass(frozen=True)
class WrapperSyntax:
    """How a wrapper reads its words before the command it runs.

    `valued` are the options that take a value as the next word (a short option's value may be
    glued on, and a long one's follows `=`); `flags` the options that take none, listed only for a
    name in WALKED (and `parallel`, which pm_permissions reads) and `numeric` whether `-<number>`
    is an option (nice). `operands` is how many words follow the options (timeout's duration), and
    `assignments` whether `NAME=value` words may come next (env, sudo).

    An option whose value is itself a command line (`env -S`, `sudo -s`) is in neither set: a
    guard that sees it cannot tell where the command starts."""

    flags: frozenset[str] = frozenset()
    valued: frozenset[str] = frozenset()
    operands: int = 0
    assignments: bool = False
    numeric: bool = False


# A `-<number>` option, where `numeric` says one is an option (`nice -5 cmd`).
NUMERIC_OPTION = re.compile(r"-\d+")


def _syntax(
    flags: str = "",
    valued: str = "",
    operands: int = 0,
    assignments: bool = False,
    numeric: bool = False,
) -> WrapperSyntax:
    return WrapperSyntax(
        frozenset(flags.split()), frozenset(valued.split()), operands, assignments, numeric
    )


# Each entry names the page it was checked against: a man page of this machine (`man <name>`:
# util-linux, GNU coreutils, procps-ng, sudo, GNU time, bash's builtins) or, for a program that is
# not installed here, its upstream page, which is written as it was and was not read again.
# An option that one copy of the table had as valued and the program has as a flag is a flag.
WRAPPER_SYNTAX = {
    # env(1), coreutils: -u NAME, -C DIR. `-S STRING` and the `--*-signal[=SIG]` options are left
    # out: -S's value is a command line, and the signal ones are not followed.
    "env": _syntax(
        flags="- -i -0 -v --ignore-environment --null --debug",
        valued="-u --unset -C --chdir",
        assignments=True,
    ),
    # bash(1) builtin `exec [-cl] [-a name] [command [arguments]]`.
    "exec": _syntax(flags="-c -l", valued="-a"),
    # bash(1) builtin `command [-pVv] command [arg ...]`.
    "command": _syntax(flags="-p -v -V"),
    # bash(1): `builtin shell-builtin [arguments]`, no options.
    "builtin": _syntax(),
    # nohup(1), coreutils: only --help and --version.
    "nohup": _syntax(),
    # nice(1), coreutils: -n N, --adjustment=N; the legacy `-N` is a number.
    "nice": _syntax(valued="-n --adjustment", numeric=True),
    # time(1), GNU time: [-apqvV] [-f FORMAT] [-o FILE] and their long forms.
    "time": _syntax(
        flags="-p -a -v -q --append --verbose --portability --quiet",
        valued="-f -o --format --output",
    ),
    # timeout(1), coreutils: -k DURATION, -s SIGNAL, then DURATION; -v, --foreground and
    # --preserve-status take no value. It has no -p and no -f.
    "timeout": _syntax(
        flags="-v --verbose --preserve-status --foreground",
        valued="-s -k --signal --kill-after",
        operands=1,
    ),
    # sudo(8), sudo 1.9: -C -D -g -p -R -r -T -t -U -u and their long forms (--close-from,
    # --chdir, --group, --prompt, --chroot, --role, --command-timeout, --type, --other-user,
    # --user), and --host. `-h` alone is --help (its host is glued on, `-hHOST`, or follows
    # `--host`); `-e -i -l -s` run an editor, a shell or a listing, and are left out.
    "sudo": _syntax(
        flags=(
            "-A -B -b -E -H -K -k -N -n -P -S -V -v --askpass --bell --background --preserve-env"
            " --set-home --remove-timestamp --reset-timestamp --non-interactive --stdin"
            " --preserve-groups"
        ),
        valued=(
            "-C -D -g -p -R -r -T -t -U -u --close-from --chdir --group --prompt --chroot --role"
            " --type --user --command-timeout --other-user --host"
        ),
        assignments=True,
    ),
    # xargs(1), findutils: -a -E -I -L -n -P -s -d (--arg-file, --max-args, --max-procs,
    # --max-chars, --delimiter, --process-slot-var) take a value. `-e`, `-i` and `-l` and their
    # long forms (--eof, --replace, --max-lines) take it only glued on (`-eEOF`, `--eof=EOF`), so
    # they are flags, and `-i` and `--replace` are listed so. `-e`, `-l`, `--eof` and `--max-lines`
    # are not: pm_permissions had the last two as valued, and as a flag the word after one would
    # be the command, which lets a push through that it refused; left out, it is read whole.
    "xargs": _syntax(
        flags=(
            "-0 -r -t -p -x -o -i --null --no-run-if-empty --verbose --interactive --exit"
            " --open-tty --replace"
        ),
        valued=(
            "-a -E -I -L -n -P -s -d --arg-file --max-args --max-procs --max-chars --delimiter"
            " --process-slot-var"
        ),
    ),
    # Below, valued options only (the guards that do not follow these wrappers read them as words).
    # doas(1), OpenBSD: -C config, -u user. Not installed here.
    "doas": _syntax(valued="-u -C"),
    # stdbuf(1), coreutils: -i -o -e MODE, --input --output --error.
    "stdbuf": _syntax(valued="-i -o -e --input --output --error"),
    # setsid(1), util-linux: -c -f -w are flags, it has no valued option.
    "setsid": _syntax(),
    # ionice(1), util-linux: -c class, -n level, -p PID, -P PGID, -u UID take a value; -t
    # (--ignore) is a flag, and was listed as valued.
    "ionice": _syntax(valued="-c -n -p -P -u --class --classdata --pid --pgid --uid"),
    # watch(1), procps-ng: -n seconds, -q cycles. `-d` (--differences[=permanent]) takes its value
    # only glued on: a flag, and was listed as valued.
    "watch": _syntax(valued="-n -q --interval --equexit"),
    # unbuffer, from expect: `unbuffer [-p] program [args]`. No man page here.
    "unbuffer": _syntax(),
    # npx(1) is `npm exec`: -p --package, -c --call (npm exec's own page). Not read again.
    "npx": _syntax(valued="-p --package -c --call"),
    # bunx, bun's docs: -p --package. Not installed as a page here, not read again.
    "bunx": _syntax(valued="-p --package"),
    # pnpx is `pnpm dlx` (pnpm's docs): --package/-p take a value; -c (--shell-mode) is a flag.
    "pnpx": _syntax(valued="-p --package"),
    # taskset(1), util-linux: `taskset mask command`; -a -c -p take no value.
    "taskset": _syntax(operands=1),
    # chrt(1), util-linux: `chrt [options] priority command`; -T -P -D (--sched-runtime,
    # --sched-period, --sched-deadline) take nanoseconds.
    "chrt": _syntax(valued="-T -P -D --sched-runtime --sched-period --sched-deadline", operands=1),
    # flock(1), util-linux: `flock [options] file command`; -c command, -E number, -w seconds
    # (--wait, --timeout) take a value.
    "flock": _syntax(valued="-w -E -c --timeout --wait --conflict-exit-code --command", operands=1),
    # unshare(1), util-linux: -S uid, -G gid, -R dir, -w dir, --propagation, --setgroups,
    # --map-user, --map-users, --map-group, --map-groups, --monotonic, --boottime take a value;
    # `--mount[=file]` and the other namespace options take one only with `=`.
    "unshare": _syntax(
        valued=(
            "-S -G -R -w --root --wd --setuid --setgid --propagation --setgroups --map-user"
            " --map-users --map-group --map-groups --monotonic --boottime"
        )
    ),
    # setpriv(1), util-linux: each of these takes a value.
    "setpriv": _syntax(
        valued=(
            "--reuid --regid --ruid --euid --rgid --egid --groups --inh-caps --bounding-set"
            " --ambient-caps --securebits --pdeathsig --selinux-label --apparmor-profile"
        )
    ),
    # runuser(1), util-linux: -u user, -g group, -G group, -s shell, -c command, -w list take a
    # value; `-l` (--login) is a flag, and was listed as valued.
    "runuser": _syntax(
        valued=(
            "-u -g -G -s -c -w --user --group --supp-group --shell --command --session-command"
            " --whitelist-environment"
        )
    ),
    # strace(1): -e expr, -o file, -p pid, -s size, -u user, -E var, -a column, -I n, -P path,
    # -S sortby, -X format, -b syscall, -O overhead, -U columns. Upstream page, not installed.
    "strace": _syntax(valued="-e -o -p -s -u -E -a -I -P -S -X -b -O -U --env --output"),
    # ltrace(1): -e -o -p -s -u -A -n -l. Upstream page, not installed.
    "ltrace": _syntax(valued="-e -o -p -s -u -A -n -l --output"),
    # systemd-run(1): -p property, -u unit, -E NAME=VALUE, -M machine, -H host take a value, with
    # --description, --slice, --uid, --gid, --nice, --service-type, --working-directory,
    # --expand-environment and the --on-* timers; `-G` (--collect) is a flag, and was listed as
    # valued. Flags such as -d -t -P -S -q -r --scope --user --system --wait --pty --pipe,
    # --on-clock-change and --on-timezone-change are not listed.
    "systemd-run": _syntax(
        valued=(
            "-p -u -E -M -H --property --unit --setenv --machine --host --slice --description"
            " --uid --gid --nice --service-type --working-directory --expand-environment"
            " --on-active --on-boot --on-startup --on-unit-active --on-unit-inactive"
            " --on-calendar --path-property --socket-property --timer-property"
        )
    ),
    # busybox: `busybox applet [args]`, no options of its own.
    "busybox": _syntax(),
    # GNU parallel's manual (not installed here): the options both old copies took a value for
    # (-j -a -S -N -n -L -P and their long forms) plus the long and short ones the manual gives a
    # required value (-C -E -I -d, --colsep, --joblog ...). `-l`, `-s`, `--max-lines` and
    # `--max-chars` are left out: the manual's form could not be told, and an option not listed
    # is read the refusing way. Its flags are the ones pm_permissions follows.
    "parallel": _syntax(
        flags=(
            "-0 -k -u -v -t -q -r -X -m -g --null --keep-order --ungroup --group --verbose --quote"
            " --dry-run --no-run-if-empty --progress --bar --eta --xargs --tag --line-buffer --lb"
            " --pipe --shuf --will-cite --plain"
        ),
        valued=(
            "-j -P -n -N -L -d -a -C -E -I -S --jobs --max-args --delimiter --arg-file --colsep"
            " --sshlogin --joblog --results --halt --timeout --delay --retries --tmpdir"
            " --workdir --wd --block --tagstring --header --env --memfree --load"
        ),
    ),
}

# Commands that run a program named later in their arguments (`uv run ssh`, `uvx`, `pipx run`,
# `python -m`, `poetry run`, `hatch run`, `pdm run`, `conda run -n env`, `mise exec --`, `direnv
# exec <dir>`, `nix-shell --run '...'`, `nix develop -c`, `nix shell <installable> -c`): the words
# after which the program comes.
LAUNCHER_RUN_WORDS = {
    **dict.fromkeys(("uv", "poetry", "hatch", "pdm", "conda"), frozenset({"run"})),
    "mise": frozenset({"--"}),
    "direnv": frozenset({"exec"}),
    "nix": frozenset({"-c", "--command"}),
    "nix-shell": frozenset({"--run", "--command"}),
}
LAUNCHERS = frozenset({"uvx", "pipx", *LAUNCHER_RUN_WORDS})
