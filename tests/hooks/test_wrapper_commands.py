"""wrapper_commands: the one wrapper name and option table that prod_guard, opencode_server_guard and
pm_permissions read. The table follows the real programs (the man page of this machine where there
is one, else the upstream page named in `wrapper_commands.py`), so an option an old copy listed
wrongly is corrected here, and the word after a flag is still the command. How each guard follows
the table is its own; the corpus (`test_guard_corpus.py`) pins the verdicts."""

from typing import NamedTuple

import corpus_harness as harness
import pytest

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
