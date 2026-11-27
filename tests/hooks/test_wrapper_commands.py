"""wrapper_commands: the one wrapper name and option table that prod_guard, opencode_server_guard and
pm_permissions read. The table follows the real programs (the man page of this machine where there
is one, else the upstream page named in `wrapper_commands.py`), so an option an old copy listed
wrongly is corrected here, and the word after a flag is still the command. How each guard follows
the table is its own; the corpus (`test_guard_corpus.py`) pins the verdicts."""

import sys
from pathlib import Path
from typing import NamedTuple

import corpus_harness as harness
import pytest
from support import load_module

table = harness.TABLES["wrapper_commands"]
opencode = harness.TABLES["opencode_server_guard"]
pm = harness.TABLES["pm_permissions"]
prod = harness.TABLES["prod_guard"]

# The wrappers pm_permissions followed before the table was shared, with a table that listed every
# option it followed.
PM_WALKED = {
    "env",
    "nice",
    "nohup",
    "builtin",
    "time",
    "timeout",
    "command",
    "exec",
    "sudo",
    "xargs",
}
# The names of the three old copies: prod's WRAPPERS, pm's WRAPPERS and EXEC_WRAPPERS, and the
# keys of opencode's OPTION_SYNTAX. Every one is a name of the shared table.
OLD_PROD = {
    *("exec", "env", "nohup", "command", "builtin", "sudo", "doas", "setsid", "time", "timeout"),
    *("xargs", "nice", "stdbuf", "watch", "parallel", "npx", "bunx", "script", "screen", "tmux"),
    *("flock", "systemd-run", "runuser", "su", "busybox", "chrt", "ionice", "taskset", "strace"),
    *("ltrace", "unbuffer", "unshare", "setpriv", "eval", "coproc", "{", "}", "!", "if", "then"),
    *("else", "elif", "do", "while", "until", "sh", "bash", "zsh", "dash", "fish", "ksh", "ash"),
    *("hush", "csh", "tcsh"),
}
OLD_PM = {
    *("bash", "sh", "zsh", "dash", "ksh", "mksh", "ash", "fish", "csh", "tcsh", "eval", "exec"),
    *("builtin", "xargs", "env", "command", "nohup", "time", "sudo", "nice", "timeout", "make"),
    *("trap", "setsid", "stdbuf", "ionice", "flock", "taskset", "chrt", "unshare", "nsenter"),
    *("script", "doas", "su", "runuser", "chroot", "watch", "strace", "ltrace", "nocache"),
    *("unbuffer", "faketime", "firejail", "systemd-run", "sg", "numactl", "cgexec", "parallel"),
    *("proxychains", "proxychains4", "torsocks", "pkexec", "ssh", "busybox"),
}
OLD_OPENCODE = {
    *("env", "exec", "command", "builtin", "nice", "nohup", "time", "timeout", "sudo", "doas"),
    *("stdbuf", "setsid", "ionice", "xargs", "watch", "unbuffer", "npx", "bunx", "pnpx", "taskset"),
    *("chrt", "flock", "unshare", "setpriv", "runuser", "strace", "ltrace", "parallel"),
    *("systemd-run", "busybox"),
}


@pytest.mark.parametrize("name", sorted(OLD_PROD | OLD_PM | OLD_OPENCODE))
def test_every_name_of_an_old_copy_is_a_wrapper_of_the_table(name: str) -> None:
    assert name in table.WRAPPERS


@pytest.mark.parametrize("name", sorted(table.WRAPPER_SYNTAX))
def test_every_name_with_a_table_is_a_wrapper(name: str) -> None:
    assert name in table.WRAPPERS


def test_the_table_has_every_name_opencode_walked_past() -> None:
    assert set(table.WRAPPER_SYNTAX) >= OLD_OPENCODE


def test_the_walked_names_are_the_ten_pm_followed_and_each_has_a_table() -> None:
    assert table.WALKED == PM_WALKED
    assert table.WALKED <= set(table.WRAPPER_SYNTAX)


@pytest.mark.parametrize("name", sorted(table.WALKED - {"nohup", "builtin", "nice"}))
def test_a_full_table_lists_flags(name: str) -> None:
    """`nohup` and `builtin` have no option, and `nice`'s are -n and a number."""
    assert table.WRAPPER_SYNTAX[name].flags


@pytest.mark.parametrize("name", sorted(table.WRAPPER_SYNTAX))
def test_an_option_is_valued_or_a_flag_never_both_and_starts_with_a_dash(name: str) -> None:
    syntax = table.WRAPPER_SYNTAX[name]
    assert not syntax.flags & syntax.valued
    assert all(option.startswith("-") for option in syntax.flags | syntax.valued)


@pytest.mark.parametrize(
    "name", ["nsenter", "chroot", "nocache", "faketime", "firejail", "sg", "numactl", "cgexec"]
)
def test_a_name_in_no_old_walk_is_a_wrapper_but_has_no_table(name: str) -> None:
    assert name in table.WRAPPERS
    assert name in table.EXEC_WRAPPERS
    assert name not in table.WALKED
    assert name not in table.WRAPPER_SYNTAX


def test_the_union_adds_the_names_one_copy_had() -> None:
    added = {"make", "trap", "pnpx", "mksh", "nsenter", "chroot", "nocache", "faketime", "firejail"}
    added |= {"sg", "numactl", "cgexec", "proxychains", "proxychains4", "torsocks", "pkexec", "ssh"}
    assert added <= table.WRAPPERS
    assert "mksh" in table.SHELLS
    assert "hush" in table.SHELLS


def test_the_shell_words_before_a_command_are_not_pm_wrappers() -> None:
    assert not table.SHELL_KEYWORDS & pm.WRAPPERS
    assert pm.WRAPPERS | table.SHELL_KEYWORDS == table.WRAPPERS


# One line per option of the table that an old copy had wrongly or that was added from the real
# program: the program, the option, and what its man page says. `page` is the page read on this
# machine, or the upstream page when the program is not installed.
class Option(NamedTuple):
    name: str
    option: str
    page: str


VALUED = [
    Option("sudo", "--host", "sudo(8): -h host, --host=host"),
    Option("sudo", "--chroot", "sudo(8): -R directory, --chroot=directory"),
    Option("sudo", "--role", "sudo(8): -r role, --role=role"),
    Option("sudo", "--type", "sudo(8): -t type, --type=type"),
    Option("sudo", "--other-user", "sudo(8): -U user, --other-user=user"),
    Option("sudo", "--command-timeout", "sudo(8): -T timeout, --command-timeout=timeout"),
    Option("sudo", "--close-from", "sudo(8): -C num, --close-from=num"),
    Option("xargs", "--max-chars", "xargs(1): -s max-chars, --max-chars=max-chars"),
    Option("xargs", "--process-slot-var", "xargs(1): --process-slot-var=name"),
    Option("stdbuf", "--output", "stdbuf(1): -o, --output=MODE"),
    Option("stdbuf", "-e", "stdbuf(1): -e, --error=MODE"),
    Option("chrt", "-T", "chrt(1): -T, --sched-runtime nanoseconds"),
    Option("chrt", "-D", "chrt(1): -D, --sched-deadline nanoseconds"),
    Option("flock", "--wait", "flock(1): -w, --wait, --timeout seconds"),
    Option("unshare", "--setgroups", "unshare(1): --setgroups allow|deny"),
    Option("unshare", "--map-group", "unshare(1): --map-group=gid|name"),
    Option("unshare", "--boottime", "unshare(1): --boottime offset"),
    Option("runuser", "--session-command", "runuser(1): --session-command=command"),
    Option("systemd-run", "-H", "systemd-run(1): -H, --host="),
    Option("systemd-run", "--nice", "systemd-run(1): --nice="),
    Option("systemd-run", "--service-type", "systemd-run(1): --service-type="),
    Option("ionice", "--classdata", "ionice(1): -n, --classdata level"),
    Option("watch", "-q", "watch(1): -q, --equexit <cycles>"),
    Option("timeout", "-s", "timeout(1): -s, --signal=SIGNAL"),
    Option("nice", "--adjustment", "nice(1): -n, --adjustment=N"),
    Option("env", "-C", "env(1): -C, --chdir=DIR"),
    Option("parallel", "--joblog", "GNU parallel manual: --joblog logfile (not installed here)"),
]
# An option a copy listed as valued and the program takes no separate value for: a flag, so the
# word after it is the command.
NOT_VALUED = [
    Option("ionice", "-t", "ionice(1): -t, --ignore takes no value"),
    Option("watch", "-d", "watch(1): -d, --differences[=permanent] takes its value glued on"),
    Option("runuser", "-l", "runuser(1): -, -l, --login takes no value"),
    Option("systemd-run", "-G", "systemd-run(1): -G, --collect takes no value"),
    Option("xargs", "--max-lines", "xargs(1): -l[max-lines], --max-lines[=max-lines]"),
    Option("xargs", "--eof", "xargs(1): -e[eof-str], --eof[=eof-str]"),
    Option("unshare", "--mount", "unshare(1): -m, --mount[=file]"),
    Option("sudo", "-h", "sudo(8): -h alone is --help; its host is glued on"),
]


@pytest.mark.parametrize("entry", VALUED, ids=lambda entry: f"{entry.name} {entry.option}")
def test_an_option_the_program_gives_a_value_is_valued(entry: Option) -> None:
    assert entry.option in table.WRAPPER_SYNTAX[entry.name].valued, entry.page


@pytest.mark.parametrize("entry", NOT_VALUED, ids=lambda entry: f"{entry.name} {entry.option}")
def test_an_option_the_program_takes_no_value_for_is_not_valued(entry: Option) -> None:
    assert entry.option not in table.WRAPPER_SYNTAX[entry.name].valued, entry.page


@pytest.mark.parametrize(
    ("name", "option"),
    [("timeout", "-p"), ("timeout", "-f"), ("sudo", "-s"), ("sudo", "-i"), ("sudo", "-h")]
    + [("env", "-S"), ("env", "--split-string"), ("xargs", "-l"), ("xargs", "--max-lines")],
)
def test_an_option_a_guard_cannot_follow_is_in_neither_set(name: str, option: str) -> None:
    """`timeout` has no -p and no -f; the others run a shell, a string, or take a value only
    glued on, so a guard that sees one reads the wrapper's words whole."""
    syntax = table.WRAPPER_SYNTAX[name]
    assert option not in syntax.flags
    assert option not in syntax.valued


# --- the word after a flag is the command --------------------------------------------------


@pytest.mark.parametrize(
    "words",
    [
        ["ionice", "-t", "opencode", "db"],
        ["watch", "-d", "opencode", "db"],
        ["runuser", "-l", "opencode", "db"],
        ["systemd-run", "-G", "opencode", "db"],
        ["xargs", "--max-lines", "opencode", "db"],
        ["sudo", "-h", "opencode", "db"],
    ],
)
def test_opencode_reads_the_word_after_a_flag_as_the_command(words: list[str]) -> None:
    unwrapped, start = opencode._unwrap(words)
    assert unwrapped[start] == "opencode"


@pytest.mark.parametrize(
    "words",
    [
        ["ionice", "-c", "3", "opencode", "db"],
        ["sudo", "--host", "h", "opencode", "db"],
        ["sudo", "--role", "r", "-u", "root", "opencode", "db"],
        ["xargs", "--max-chars", "10", "opencode", "db"],
        ["xargs", "--process-slot-var", "S", "opencode", "db"],
        ["chrt", "-T", "5", "0", "opencode", "db"],
        ["flock", "--wait", "5", "lock", "opencode", "db"],
        ["unshare", "--setgroups", "deny", "opencode", "db"],
        ["systemd-run", "-H", "h", "opencode", "db"],
        ["stdbuf", "--output", "L", "opencode", "db"],
        ["parallel", "--joblog", "j", "opencode", "db"],
    ],
)
def test_opencode_goes_past_an_option_the_program_gives_a_value(words: list[str]) -> None:
    unwrapped, start = opencode._unwrap(words)
    assert unwrapped[start] == "opencode"


def readings(words: list[str]) -> list[tuple[str, ...]]:
    """The commands opencode judges for `words`: the one the table gives, then one for each valued
    option read as a flag."""
    argv = opencode.Argv(tuple(words))
    return [argv.command, *(each.command for each in argv.loose_readings)]


@pytest.mark.parametrize(
    "words",
    [
        ["chrt", "-D", "opencode", "db"],
        ["chrt", "--sched-deadline", "opencode", "db"],
        ["chrt", "-P", "opencode", "db"],
        ["chrt", "--sched-period", "opencode", "db"],
        ["chrt", "-T", "opencode", "db"],
        ["chrt", "--sched-runtime", "opencode", "db"],
        ["flock", "--wait", "opencode", "db"],
        ["sudo", "-u", "root", "chrt", "-D", "opencode", "db"],
        ["env", "-i", "flock", "--wait", "opencode", "db"],
    ],
)
def test_opencode_also_reads_a_valued_option_as_a_flag(words: list[str]) -> None:
    """The table follows the program (`chrt -D` takes a number of nanoseconds), but opencode also
    reads the option as a flag, as it did before the table: the word the option swallowed then
    stays in the words the command is judged from (`db` runs with `opencode` named in them), where
    the table's own reading leaves no command."""
    assert readings(words)[0] == ()
    assert ("db",) in readings(words)


def test_opencode_reads_an_option_with_no_bare_value_once() -> None:
    assert readings(["sudo", "-u", "-x", "ls"]) == [("ls",)]
    assert readings(["ls", "-u", "root"]) == [("ls", "-u", "root")]
    assert readings(["chrt", "-f", "10", "ls"]) == [("ls",)]


def test_opencode_reads_each_valued_option_in_turn() -> None:
    words = ["nice", "-n", "5", "timeout", "-k", "9", "5", "ls"]
    assert readings(words) == [("ls",), ("5", "timeout", "-k", "9", "5", "ls"), ("5", "ls")]


@pytest.mark.parametrize(
    ("words", "command"),
    [
        (["sudo", "-u", "root", "--host", "h", "git", "push"], ["git", "push"]),
        (["sudo", "--chroot", "/r", "git", "push"], ["git", "push"]),
        (["xargs", "--max-chars", "10", "git", "push"], ["git", "push"]),
        (["timeout", "-k", "5", "-s", "KILL", "9", "git", "push"], ["git", "push"]),
        (["env", "-i", "-u", "X", "A=1", "git", "push"], ["git", "push"]),
        (["nice", "-n", "5", "git", "push"], ["git", "push"]),
    ],
)
def test_pm_follows_a_walked_wrapper_through_the_table(
    words: list[str], command: list[str]
) -> None:
    unwrapped = pm._unwrapped(words)
    assert unwrapped.followed
    assert unwrapped.command == command


@pytest.mark.parametrize(
    "words",
    [
        ["xargs", "--max-lines", "1", "git", "push", "-f"],
        ["xargs", "--eof", "x", "git", "push", "-f"],
        ["sudo", "-s", "git", "push", "-f"],
        ["env", "-S", "git push -f"],
        ["timeout", "-p", "5", "git", "push", "-f"],
    ],
)
def test_pm_reads_the_words_of_an_option_the_table_leaves_out_whole(words: list[str]) -> None:
    assert not pm._unwrapped(words).followed
    assert pm._segment_is_guarded(words)


@pytest.mark.parametrize(
    "name", ["nsenter", "chroot", "nocache", "faketime", "firejail", "sg", "numactl", "cgexec"]
)
def test_pm_reads_a_name_in_no_old_walk_whole_and_never_walks_it(name: str) -> None:
    words = [name, "-x", "git", "push", "-f"]
    assert pm._unwrapped(words).command[0] == name
    assert pm._segment_is_guarded(words)


@pytest.mark.parametrize("name", ["npx", "bunx", "pnpx", "tmux", "screen"])
def test_pm_judges_the_names_the_union_adds_as_wrappers(name: str) -> None:
    assert name in pm.EXEC_WRAPPERS
    assert pm._segment_is_guarded([name, "git", "push", "-f"])


def test_pm_reads_parallel_through_the_table() -> None:
    shared = table.WRAPPER_SYNTAX["parallel"]
    assert (pm.PARALLEL_SYNTAX.flags, pm.PARALLEL_SYNTAX.valued) == (shared.flags, shared.valued)
    assert pm._parallel_runs_its_input(["parallel", "-j", "2", "echo", "x"]) is False
    assert pm._parallel_runs_its_input(["parallel", "-l", "2", "echo", "x"]) is True


# --- prod: the read-only unwrapping --------------------------------------------------------


class Word(NamedTuple):
    raw: str
    value: str


def words_of(*words: str) -> list[Word]:
    return [Word(each, each.strip("'\"")) for each in words]


@pytest.mark.parametrize(
    ("words", "end"),
    [
        (("timeout", "5", "grep"), 2),
        (("timeout", "-k", "5", "-s", "KILL", "9m", "grep"), 6),
        (("nice", "grep"), 1),
        (("nice", "-n", "5", "grep"), 3),
        (("nice", "-5", "grep"), 2),
        (("env", "-i", "LC_ALL=C", "grep"), 3),
        (("env", "-u", "X", "grep"), 3),
        (("command", "grep"), 1),
    ],
)
def test_prod_reads_past_the_options_it_followed_before(words: tuple[str, ...], end: int) -> None:
    assert prod._wrapper_end(words_of(*words)) == end


@pytest.mark.parametrize(
    "words",
    [
        ("timeout", "--signal", "TERM", "5", "grep"),
        ("timeout", "-v", "5", "grep"),
        ("timeout", "x", "grep"),
        ("nice", "--adjustment=5", "grep"),
        ("nice", "-n", "x", "grep"),
        ("env", "grep"),
        ("env", "-C", "d", "grep"),
        ("env", "-S", "grep x"),
        ("env", "-u", "$X", "grep"),
        ("env", "FOO=1", "grep"),
        ("command", "-v", "grep"),
        ("sudo", "grep"),
        ("grep", "x"),
    ],
)
def test_prod_reads_past_nothing_else(words: tuple[str, ...]) -> None:
    assert prod._wrapper_end(words_of(*words)) == 0


def test_prod_reads_nice_past_one_option_only() -> None:
    """A second option is one it does not read past (`nice -n 5 -n 3`)."""
    assert prod._wrapper_end(words_of("nice", "-n", "5", "-n", "3", "grep")) == 0
    assert prod._wrapper_end(words_of("nice", "-5", "-n", "3", "grep")) == 0


# --- the shared option walker and the reads-its-input test ---------------------------------------

NEW_NAMES = (
    "option_words",
    "READER_SYNTAX",
    "STDIN_PATHS",
    "MAKE_GLUED_FILE",
    "INPUT_SOURCES",
    "COMMAND_STRING_OPTIONS",
    "runs_stdin_as_commands",
)


@pytest.mark.parametrize(
    ("name", "words", "end"),
    [
        ("nice", ["nice", "-5", "sh"], 2),  # the numeric option is read: the command is `sh`
        ("nice", ["nice", "-n", "5", "sh"], 3),
        ("nice", ["nice", "-n5", "sh"], 2),
        ("nice", ["nice", "--adjustment=5", "sh"], 2),
        ("nice", ["nice", "sh"], 1),
        ("nice", ["nice"], 1),
        ("nice", ["nice", "--", "-5"], 2),
        ("timeout", ["timeout", "-s", "KILL", "5", "sh"], 4),
        ("env", ["env", "-u", "X", "A=1", "sh"], 4),
        ("sudo", ["sudo", "-u", "me", "ls"], 3),
        ("sudo", ["sudo", "-n", "A=1", "ls"], 3),
        ("sudo", ["sudo", "-h", "h", "ls"], None),  # an option the table does not list
        ("nice", ["nice", "-n"], None),  # a valued option with no value
        ("nice", ["nice", "-x", "sh"], None),
        ("nice", ["nice", "--unknown", "sh"], None),
    ],
)
def test_the_walker_returns_where_the_command_starts(
    name: str, words: list[str], end: int | None
) -> None:
    assert table.option_words(table.WRAPPER_SYNTAX[name], words, 1) == end


@pytest.mark.parametrize("name", sorted(table.WRAPPER_SYNTAX))
@pytest.mark.parametrize(
    "rest",
    [
        [],
        ["ls"],
        ["-n", "5", "ls"],
        ["-5", "ls"],
        ["-s", "ls"],
        ["-x", "ls"],
        ["--", "ls"],
        ["--user=x", "ls"],
        ["--user", "x", "ls"],
        ["-u", "x", "A=1", "ls"],
        ["-qc", "ls"],
        ["-"],
        ["-j2", "echo"],
        ["-n"],
    ],
)
def test_the_walker_reads_as_pm_does(name: str, rest: list[str]) -> None:
    words = [name, *rest]
    syntax = table.WRAPPER_SYNTAX[name]
    assert table.option_words(syntax, words, 1) == pm._wrapped_start(syntax, words, 1)


def test_the_readers_table_names_sudo_doas_and_screen() -> None:
    assert set(table.READER_SYNTAX) == {"sudo", "doas", "screen"}


def test_sudo_reads_its_shell_options_as_flags_and_nothing_else_differs() -> None:
    shared, reader = table.WRAPPER_SYNTAX["sudo"], table.READER_SYNTAX["sudo"]
    assert reader.flags == shared.flags | {"-s", "-i", "--shell", "--login"}
    assert reader.valued == shared.valued
    assert reader.assignments is shared.assignments


def test_doas_is_its_wrapper_entry_with_no_flags() -> None:
    """`doas -n ls` is no command: the stricter guard reads `-n` as an option it cannot follow."""
    assert table.READER_SYNTAX["doas"] == table.WRAPPER_SYNTAX["doas"]
    assert table.READER_SYNTAX["doas"].flags == frozenset()


def test_screen_is_the_letters_pm_and_prod_both_had() -> None:
    screen = table.READER_SYNTAX["screen"]
    assert (screen.flags, screen.valued) == (pm.SCREEN_SYNTAX.flags, pm.SCREEN_SYNTAX.valued)
    old = prod.STDIN_READER_OPTIONS["screen"]
    assert (screen.flags, screen.valued) == (old.flags, old.valued)


def test_the_small_tables_are_the_ones_the_guards_had() -> None:
    assert table.STDIN_PATHS == pm.STDIN_PATHS == prod.STDIN_PATHS
    assert (
        table.MAKE_GLUED_FILE.pattern == pm.MAKE_GLUED_FILE.pattern == prod.MAKE_GLUED_FILE.pattern
    )
    assert table.INPUT_SOURCES == pm.PARALLEL_SOURCES == prod.INPUT_SOURCES
    assert table.COMMAND_STRING_OPTIONS == {"-c", "--command", "--session-command"}


READS_STDIN = [
    # A shell: unknown means yes; a stdin redirect, `-`, `-s` or a stdin script too.
    ["sh"],
    ["bash"],
    ["/bin/sh"],
    ["bash", "-x"],
    ["bash", "-s"],
    ["bash", "-"],
    ["bash", "-o", "pipefail"],
    ["bash", "/dev/stdin"],
    ["bash", "<in"],
    ["bash", "<", "in"],
    ["bash", "-s", "-c", "ls"],
    # A shell given `-c` whose string starts with a shell reads stdin (Ruling s2-5).
    ["sh", "-c", "sh"],
    ["bash", "-c", "bash -s"],
    ["bash", "-lc", "/bin/sh"],
    ["sh", "-o", "pipefail", "-c", "zsh"],
    # eval always.
    ["eval"],
    ["eval", "ls"],
    # make: only a makefile read from stdin.
    ["make", "-f", "-"],
    ["make", "--file=-"],
    ["make", "-f-"],
    ["make", "-sf/dev/stdin"],
    ["make", "--makefile=/dev/fd/0"],
    # su, runuser and script: no command string, or one that starts a shell.
    ["su"],
    ["su", "-"],
    ["su", "-l", "root"],
    ["su", "-c", "sh"],
    ["su", "-c", "bash -s"],
    ["su", "--command", "sh"],
    ["su", "--command=sh"],
    ["su", "--session-command", "sh"],
    ["su", "--session-command=/bin/sh"],
    ["su", "-c"],
    ["runuser", "x"],
    ["runuser", "-u", "x", "ls"],
    ["script"],
    ["script", "-q", "/dev/null"],
    ["script", "-c", "sh"],
    ["script", "-qc", "sh", "/dev/null"],
    ["script", "-qc"],
    # sudo, doas and screen with no command after their options.
    ["sudo"],
    ["sudo", "-s"],
    ["sudo", "-i"],
    ["sudo", "--shell"],
    ["sudo", "--login"],
    ["sudo", "-n"],
    ["sudo", "-u", "me"],
    ["sudo", "A=1"],
    ["sudo", "<in", "ls"],
    ["sudo", "-h", "h", "ls"],  # `-h` is not an option the table can follow
    ["doas"],
    ["doas", "-s"],
    ["doas", "-n", "ls"],  # Review Focus 2
    ["doas", "-u", "root", "-n", "ls"],
    ["screen"],
    ["screen", "-"],
    ["screen", "-dmS", "x"],
    ["screen", "-d", "-m"],
    # parallel with no command.
    ["parallel"],
    ["parallel", "-j", "2"],
    ["parallel", ":::", "a"],
    ["parallel", "-l", "2", "echo"],  # an option the table leaves out: no command
]

READS_NO_STDIN = [
    [],
    ["cat"],
    ["grep", "-c", "bash"],
    ["nice", "-5", "sh"],  # the guard walks `nice` itself: the receiver is `sh`
    ["env", "sh"],
    ["source"],
    # A shell with a command string that is no shell, or a script.
    ["bash", "-c", "ls"],
    ["bash", "-lc", "ls"],
    ["bash", "-c", "git status", "-s"],
    ["sh", "script.sh"],
    ["bash", ">out", "script.sh"],
    ["bash", "-c"],
    # make without -f -.
    ["make"],
    ["make", "-C", "x"],
    ["make", "-f", "Makefile"],
    ["make", "--file=Makefile"],
    ["make", "-sf", "Makefile"],
    # A command string that is no shell.
    ["su", "-c", "ls"],
    ["su", "--session-command", "ls"],
    ["su", "--command=ls", "root"],
    ["script", "-qc", "ls", "/dev/null"],
    ["script", "-c", "ls"],
    ["runuser", "-c", "ls", "x"],
    ["runuser", "--command=ls", "x"],
    # A command after the options keeps its verdict (Review Focus 1).
    ["sudo", "-u", "me", "ls"],
    ["sudo", "A=1", "ls"],
    ["sudo", "-s", "ls"],
    ["sudo", ">out", "ls"],
    ["doas", "-u", "x", "cat"],
    ["doas", "cat"],
    ["screen", "-dmS", "x", "ls"],
    ["screen", "ls"],
    ["parallel", "echo"],
    ["parallel", "-j", "2", "echo"],
    ["parallel", "-j2", "echo"],
    ["parallel", "--", "echo"],
]


@pytest.mark.parametrize("words", READS_STDIN, ids=" ".join)
def test_a_receiver_that_runs_its_stdin_as_commands(words: list[str]) -> None:
    assert table.runs_stdin_as_commands(words) is True


@pytest.mark.parametrize("words", READS_NO_STDIN, ids=lambda words: " ".join(words) or "empty")
def test_a_receiver_that_does_not(words: list[str]) -> None:
    assert table.runs_stdin_as_commands(words) is False


def test_a_path_receiver_is_read_by_its_basename() -> None:
    assert table.runs_stdin_as_commands(["/usr/bin/sudo", "-s"]) is True
    assert table.runs_stdin_as_commands(["/usr/bin/sudo", "-u", "me", "ls"]) is False
    assert table.runs_stdin_as_commands(["/usr/bin/make", "-f", "-"]) is True


def test_a_nice_numeric_option_is_walked_to_the_receiver() -> None:
    """A guard walks `nice -5 sh` with `option_words` and passes the receiver's words on."""
    words = ["nice", "-5", "sh"]
    end = table.option_words(table.WRAPPER_SYNTAX["nice"], words, 1)
    assert end == 2
    assert table.runs_stdin_as_commands(words[end:]) is True
    words = ["nice", "-5", "cat"]
    end = table.option_words(table.WRAPPER_SYNTAX["nice"], words, 1)
    assert table.runs_stdin_as_commands(words[end:]) is False


@pytest.mark.parametrize(
    "words",
    [
        [""],
        ["", ""],
        ["sh", ""],
        ["bash", "", "-c"],
        ["bash", "-c", ""],
        ["su", ""],
        ["su", "-c", ""],
        ["script", "-"],
        ["sudo", "--"],
        ["sudo", "-"],
        ["sudo", "--", "-"],
        ["-"],
        ["--"],
        ["screen", "-S"],
        ["make", "-f"],
        ["make", "--"],
        ["make", "-f", ""],
        ["parallel", "-j"],
        ["parallel", "--", ":::"],
        ["doas", "-u"],
        ["bash", "-o"],
        ["bash", "+o"],
        ["bash", ">"],
        ["bash", "<"],
        ["sudo", "<"],
        ["eval", ""],
    ],
)
def test_the_test_is_total(words: list[str]) -> None:
    assert isinstance(table.runs_stdin_as_commands(words), bool)


@pytest.mark.parametrize(
    "words",
    [
        ("sudo", "-n"),
        ("bash", "-c", "ls"),
        ("su", "-c", "ls"),
    ],
)
def test_a_tuple_reads_as_a_list(words: tuple[str, ...]) -> None:
    assert table.runs_stdin_as_commands(words) == table.runs_stdin_as_commands(list(words))


@pytest.mark.parametrize(
    "words",
    [
        ["sudo", *["-n"] * 100_000, "ls"],
        ["doas", *["-u", "x"] * 50_000, "ls"],
        ["bash", *["-o", "pipefail"] * 50_000],
        ["su", *["-l"] * 100_000],
        ["make", *["-C", "x"] * 50_000],
        ["parallel", *["-j", "2"] * 50_000],
        ["screen", *["-d"] * 100_000],
    ],
    ids=lambda words: words[0],
)
def test_the_test_reads_each_word_once(words: list[str]) -> None:
    """No clock: a walk that read the words more than once each would not end on this input."""
    assert isinstance(table.runs_stdin_as_commands(words), bool)


def test_pm_refuses_through_its_stub_when_wrapper_commands_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no wrapper_commands to load, pm loads its damaged-hooks stub: every new name is there
    and refuses (or is empty), so the module loads and main() answers with a refusal."""
    lone = tmp_path / "lone"
    lone.mkdir()
    (lone / "hook_support.py").write_text(
        "def sibling(name):\n    raise ImportError(name)\n", encoding="utf-8"
    )
    (lone / "pm_permissions.py").write_text(
        (Path(pm.__file__).resolve()).read_text(encoding="utf-8"), encoding="utf-8"
    )
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.delitem(sys.modules, "hook_support", raising=False)
    damaged = load_module(lone / "pm_permissions.py", "coven_pm_damaged", register=True)
    monkeypatch.delitem(sys.modules, "coven_pm_damaged")
    assert damaged.SUPPORT_ERROR is not None
    stub = damaged.wrapper_commands
    assert stub.runs_stdin_as_commands(["cat"]) is True
    assert stub.runs_stdin_as_commands([]) is True
    assert stub.option_words(stub.READER_SYNTAX, ["sudo"], 1) is None
    for name in ("READER_SYNTAX", "STDIN_PATHS", "INPUT_SOURCES", "COMMAND_STRING_OPTIONS"):
        assert not getattr(stub, name)
    assert not stub.MAKE_GLUED_FILE.search("-f-")
    assert all(hasattr(stub, name) for name in NEW_NAMES)
