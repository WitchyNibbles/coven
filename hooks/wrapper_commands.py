"""The commands that run another command, by name and by option: what prod_guard.py,
opencode_server_guard.py and pm_permissions.py all read a shell line with.

One table, so that a wrapper one guard learns is a wrapper for the others too. Each hook loads this
module by path (`hook_support.sibling("wrapper_commands")`). The name sets say which words run
another command; `WRAPPER_SYNTAX` says how a wrapper's options are read (`sudo -u root claude`), as
the real program reads them. How each guard follows a wrapper (what an option it does not know
means) is its own.
"""

import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

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

# A wrapper's options are read as the real program reads them; `option_words` is the one walk of
# them that the guards share (it was pm_permissions' `_option_words` and `_wrapped_start`).
_ASSIGNMENT = re.compile(r"[A-Za-z_]\w*(?:\[[^\]]*\])?\+?=")


def _option_width(syntax: WrapperSyntax, word: str, following: int) -> int | None:
    """How many words the option `word` takes (itself and its value), or None when the wrapper
    may read it some other way. `following` is how many words come after it."""
    name, equals, _ = word.partition("=")
    long_value = bool(equals) and name.startswith("--")
    if word in syntax.flags or (long_value and (name in syntax.flags or name in syntax.valued)):
        return 1
    if word in syntax.valued:
        return 2 if following else None
    if syntax.numeric and NUMERIC_OPTION.fullmatch(word):
        return 1
    if word.startswith("--"):
        return None
    for position, letter in enumerate(word[1:], start=1):
        if f"-{letter}" in syntax.valued:
            return 1 if word[position + 1 :] else (2 if following else None)
        if f"-{letter}" not in syntax.flags:
            return None
    return 1


def option_words(syntax: WrapperSyntax, words: Sequence[str], start: int) -> int | None:
    """Where the command a wrapper runs starts, given where the words after its name start
    (`start`): the index of the first word after its options, its operands and its assignments.
    None when an option is not in the table, so the guard cannot tell where the command is."""
    index = start
    while index < len(words):
        word = words[index]
        if word == "--":
            index += 1
            break
        if not word.startswith("-") or (word == "-" and word not in syntax.flags):
            break
        taken = _option_width(syntax, word, len(words) - index - 1)
        if taken is None:
            return None
        index += taken
    index += syntax.operands
    while syntax.assignments and index < len(words) and _ASSIGNMENT.match(words[index]):
        index += 1
    return index


# The programs `runs_stdin_as_commands` reads the options of, to find the command each runs. Only
# that test reads this table: pm_permissions and prod_guard walk their own wrappers with
# WRAPPER_SYNTAX. sudo's shell options are flags here (`sudo -s` starts a shell, and a command
# after them is still its command); doas is its entry above, with no flags, so `doas -n ls` has no
# command it can follow (the stricter reading of pm_permissions and prod_guard); screen's letters
# are screen(1)'s, and a lone `-` reads the commands from stdin.
READER_SYNTAX: Mapping[str, WrapperSyntax] = {
    "sudo": replace(
        WRAPPER_SYNTAX["sudo"],
        flags=WRAPPER_SYNTAX["sudo"].flags | {"-s", "-i", "--shell", "--login"},
    ),
    "doas": WRAPPER_SYNTAX["doas"],
    "screen": WrapperSyntax(
        flags=frozenset({"-", *(f"-{letter}" for letter in "aAdDfFilLmMOqQrRUvVxZ")}),
        valued=frozenset(f"-{letter}" for letter in "cehpsStTX"),
    ),
}
# The paths a command reads its stdin from, and make's short options with a file glued on.
STDIN_PATHS = frozenset({"-", "/dev/stdin", "/dev/fd/0", "/proc/self/fd/0"})
# `-f-`, `-sf/dev/stdin`. DOTALL: without it a newline in the word ends `.+` early, and the
# pattern backtracks over every f of `-fff...f` (quadratic).
MAKE_GLUED_FILE = re.compile(r"-[A-Za-z]*f(?P<path>.+)", re.DOTALL)
# parallel's argument separators: the words after one are its inputs, and maybe its commands.
INPUT_SOURCES = frozenset({":::", "::::", ":::+", "::::+"})
# The options that give su, runuser and script a command string; `-c` also comes glued to other
# short options (`-qc`) or to its value (`-cls`), which `_glued_command_string` reads.
COMMAND_STRING_OPTIONS = frozenset({"-c", "--command", "--session-command"})
_LETTERS = re.compile(r"[A-Za-z]*")
_COMMAND_STRING_READERS = frozenset({"su", "runuser", "script"})

_REDIRECT = re.compile(r"\d*(?:<|>|&>)")
_REDIRECT_OPERATOR = re.compile(r"\d*(?:[<>]{1,3}[&|]?|&>{1,2})")
_STDIN_REDIRECT = re.compile(r"0?<")
_STDIN_FILES = ("/dev/stdin", "/dev/fd/", "/proc/self/fd/")
_SHELL_VALUE_OPTIONS = frozenset({"-o", "+o", "-O", "+O"})  # `bash -o pipefail`


def _starts_a_shell(text: str) -> bool:
    """Whether the first word of a command string names a shell (`sh`, `/bin/bash -s`)."""
    first = text.lstrip("'\"").split(maxsplit=1)
    return bool(first) and os.path.basename(first[0].rstrip("'\"")) in SHELLS


def _shell_reads_stdin(words: Sequence[str]) -> bool:
    """Whether a shell given the words after its name reads its program from stdin: a stdin
    redirect, `-` or `-s` before a `-c` or a script argument, or a script that is stdin
    (`/dev/stdin`, `/dev/fd/0`). A `-c` string is not stdin, unless its first word is a shell
    (`sh -c sh`). Words it cannot read count as stdin."""
    skip = False
    for index, word in enumerate(words):
        if skip:
            skip = False
        elif word == "-" or _STDIN_REDIRECT.match(word):
            return True
        elif _REDIRECT.match(word) or word in _SHELL_VALUE_OPTIONS:
            skip = word in _SHELL_VALUE_OPTIONS or bool(_REDIRECT_OPERATOR.fullmatch(word))
        elif word[:1] not in ("-", "+"):
            return word.startswith(_STDIN_FILES)  # a script, which may be stdin itself
        elif word[1:2] != "-" and "s" in word:
            return True
        elif word[1:2] != "-" and "c" in word:
            return index + 1 < len(words) and _starts_a_shell(words[index + 1])
    return True


def _glued_command_string(word: str) -> str | None:
    """What follows the `c` of a short option word whose letters before it are all options
    (`-qc` gives "", `-cls` gives "ls"); None for any other word. One scan, no backtracking."""
    if word[:1] != "-":
        return None
    letters, found, glued = word[1:].partition("c")
    return glued if found and _LETTERS.fullmatch(letters) else None


def _command_string_reads_stdin(words: Sequence[str]) -> bool:
    """Whether su, runuser or script given `words` run no command string, or one that starts a
    shell: `-c`, `-qc`, `--command` or `--session-command`, each with its value (glued on, after
    `=`, or the next word). An option with no value is no command."""
    for index, word in enumerate(words[1:], start=1):
        option, equals, value = word.partition("=")
        glued = _glued_command_string(word)
        if option in COMMAND_STRING_OPTIONS and equals:
            return _starts_a_shell(value)
        if option not in COMMAND_STRING_OPTIONS and glued is None:
            continue
        if glued:
            return _starts_a_shell(glued)
        return index + 1 >= len(words) or _starts_a_shell(words[index + 1])
    return True


def _make_reads_stdin_file(words: Sequence[str]) -> bool:
    """Whether make's words name stdin as its makefile: a stdin path as a word, after `=`
    (`--file=-`), or glued on (`-f-`, `-sf/dev/stdin`)."""
    for word in words[1:]:
        if word.startswith("--"):
            path = word.partition("=")[2]
        else:
            glued = MAKE_GLUED_FILE.fullmatch(word)
            path = glued["path"] if glued else ""
        if word in STDIN_PATHS or path in STDIN_PATHS:
            return True
    return False


def _command_index(words: Sequence[str], index: int, assignments: bool) -> int | None:
    """Where the command is, from `index` (just after a wrapper's options), or None when none
    follows: redirects (`>out`, or a bare `>` and its target) and, with `assignments`, `NAME=value`
    words come before it, and a stdin redirect leaves none (the stricter side)."""
    while index < len(words):
        word = words[index]
        if _STDIN_REDIRECT.match(word):
            return None
        if _REDIRECT.match(word):
            index += 2 if _REDIRECT_OPERATOR.fullmatch(word) else 1
        elif assignments and _ASSIGNMENT.match(word):
            index += 1
        else:
            return index
    return None


def _runs_no_command(name: str, words: Sequence[str]) -> bool:
    """Whether the words of sudo, doas or screen hold no command after their options: an option
    the table does not list, or nothing left but redirects (and assignments: doas has no entry
    for them, but `doas A=1` runs nothing)."""
    syntax = READER_SYNTAX[name]
    index = option_words(syntax, words, 1)
    assignments = syntax.assignments or name == "doas"
    return index is None or _command_index(words, index, assignments) is None


def _parallel_has_no_command(words: Sequence[str]) -> bool:
    """Whether parallel's words hold no command: none after its options and redirects and
    assignments, or arguments (`:::`) first, so it runs its input lines."""
    start = option_words(WRAPPER_SYNTAX["parallel"], words, 1)
    if start is None:
        return True
    index = _command_index(words, start, assignments=True)
    return index is None or words[index] in INPUT_SOURCES


def runs_stdin_as_commands(words: Sequence[str]) -> bool:
    """Whether a program that is fed text runs it as shell commands, given the receiver's words
    (`words[0]` is its name or path, after a guard's own walk past `nice`, `env` and the like).

    True for a shell that reads its program from stdin, `eval`, `make` given a makefile from stdin,
    `su`, `runuser` and `script` with no command string or one that starts a shell, `sudo`, `doas`
    and `screen` with no command after their options, and `parallel` with no command. False for any
    other name: each guard keeps its own rule for those. Never raises."""
    if not words:
        return False
    name = os.path.basename(words[0])
    if name in SHELLS:
        return _shell_reads_stdin(words[1:])
    if name == "eval":
        return True
    if name == "make":
        return _make_reads_stdin_file(words)
    if name in _COMMAND_STRING_READERS:
        return _command_string_reads_stdin(words)
    if name in READER_SYNTAX:
        return _runs_no_command(name, words)
    return name == "parallel" and _parallel_has_no_command(words)


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
