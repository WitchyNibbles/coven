#!/usr/bin/env python3
"""PreToolUse hook: keep a PM session away from coven's OpenCode server and its profile.

A PM's shell and file tools run on the machine that runs the coven OpenCode server. This hook
refuses a call that would let the PM reach the server's API, its password or environment, the
profile's protected files, coven's own source or coven's lifecycle commands. The plugin runs it
for every session with `--profile DIR --serve DIR --coven-root DIR`, through fail-closed.sh, and
sends the payload deny_secrets.py gets: `tool_name` (Bash, Read, Grep, Glob, Write, Edit,
NotebookEdit or MultiEdit), `tool_input` (`command` and `workdir`, `file_path`, `path`, ...) and
`cwd`. Exit 0 allows; exit 2 refuses with the text on stderr. Any other failure refuses too.

What it refuses:

- an `opencode` command (a bare name, a path, under `env`, `npx`, `xargs`, `bash -c`, `$(...)`...)
  other than exactly `opencode --version` and `opencode --help`, and any word that names opencode
  after a program that may run it (everything but a short list of programs that never run their
  arguments: `echo`, `grep`, `ls`, `git`...);
- any text that names the server's port (read from `<serve>/port` at call time; with no usable
  port file, any loopback address with a port);
- a read, grep or glob of the serve folder or the profile's `data/` folder, and
  `/proc/<pid>/environ`, `ps` options that print environments (`e`, `-E`, `eww`);
- a folder that holds the serve or the `data/` folder (the profile, `/`...) for the file tools, and
  a folder that holds the profile for every shell program but the ones that never read content
  (`ls`, `cd`, `stat`...); a folder that holds the coven root, unless every program of the line
  only reads (`ls`, `cat`, `grep`, `rg`, `find` without `-delete` or `-exec`, `git status|log|
  diff|show|grep|blame`...) and nothing is redirected to a file. That covers the words of the
  line, the working folder and every folder a `cd` moves to (a bare `cd` is home), so `rg
  password` run in the folder that holds the profile is refused;
- an option or variable that makes git, man or rg run a command (`git -c`, `git bisect run`,
  `git rebase --exec`, `git submodule foreach`, `git difftool -x`, `GIT_*=`, `PAGER=`, `man -P`,
  `rg --pre`), a variable set from `$(...)` used as a program or as an argument of a program that
  reads, a shell that reads a process substitution (`bash <(...)`), and a glob that bash reads
  differently from Python (`[^x]`, `[!x]`, `[[:alpha:]]`) or that matches nothing under a closed
  place;
- a write, edit or patch, and any shell command that names a path, in the protected set: the
  profile except its `tmp/` folder, the serve folder, and the coven root except its
  `.claude/worktrees/` folder;
- a command substitution (`$(...)`, backticks) that builds a program name, a path or a `cd`
  target, because what it prints is not seen: the text says to write the path literally;
- `coven opencode` with anything but `doctor` or `--help`, and a pipe into a shell.

Paths are judged after `~`, `$HOME`, variables set on the same line, `..`, globs, braces, quote
removal and symbolic links (`Path.resolve`), against the payload's `cwd` (or the shell's
`workdir`) and every folder a `cd` of the line moves to. Shell text is read twice: as words (the
shell_words scanner, through nested `bash -c`, `eval`, `find -exec` and `$(...)`), and as plain
text with its quotes removed, whose every word is also tried as a path. This is a guard for a
model that follows its role, not a sandbox: a script that builds a path at run time is not seen.
"""

import argparse
import codecs
import glob
import importlib
import itertools
import logging
import os
import re
import shlex
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
from typing import NoReturn, Self

# `-I` leaves this folder off sys.path, so put it there for hook_support, the one module found so.
sys.path.insert(0, str(Path(__file__).resolve().parent))
# By name, not an import statement: ruff rejects one below code (E402) and suppressions are banned.
hook_support = importlib.import_module("hook_support")
# The names of the programs that run another are shared with prod_guard.py.
wrapper_commands = hook_support.sibling("wrapper_commands")

HOOK = "opencode-server-guard"
GM_TOOLS = "coven_pm_list, coven_pm_resume, coven_pm_stop"
POINTER = f"this is coven's own server; ask the GM with a QUESTION (its tools: {GM_TOOLS})"
MAX_NESTING = 8
MAX_GLOB_MATCHES = 2000
MAX_BRACE_WORDS = 256
MAX_UNWRAP = 16


class Mode(Enum):
    """What a call does with a path."""

    READ = auto()  # Read, Grep, Glob: only the serve folder and process environments are closed
    WRITE = auto()  # Write, Edit, NotebookEdit, MultiEdit: the whole protected set is closed
    SHELL = auto()  # a shell command that names the path, to read or to write


TOOL_MODES = {
    "Read": Mode.READ,
    "Grep": Mode.READ,
    "Glob": Mode.READ,
    "Write": Mode.WRITE,
    "Edit": Mode.WRITE,
    "NotebookEdit": Mode.WRITE,
    "MultiEdit": Mode.WRITE,
    "Bash": Mode.SHELL,
}
PATH_KEYS = ("file_path", "notebook_path", "path")


class Refused(Exception):
    """A call the guard refuses; the text says which rule."""

    def __init__(self, rule: str) -> None:
        super().__init__(f"coven {HOOK}: {rule}; {POINTER}")


@dataclass(frozen=True)
class Scope:
    """Where the protected places are, as real paths, and the server's port."""

    profile: Path
    serve: Path
    data: Path
    tmp: Path
    coven: Path
    worktrees: Path
    port: int | None
    home: Path

    @classmethod
    def of(cls, profile: Path, serve: Path, coven_root: Path, port: int | None) -> Self:
        real_profile = _real(profile)
        real_root = _real(coven_root)
        return cls(
            profile=real_profile,
            serve=_real(serve),
            data=real_profile / "data",
            tmp=real_profile / "tmp",
            coven=real_root,
            worktrees=real_root / ".claude" / "worktrees",
            port=port,
            home=Path.home(),
        )

    def closed_roots(self, mode: Mode) -> tuple[Path, ...]:
        """The closed folders whose parents the mode keeps out of reach: the serve and data folders
        for a read, the profile and the coven root otherwise."""
        return (self.serve, self.data) if mode is Mode.READ else (self.profile, self.coven)

    def roots_to_hold(self, mode: Mode, *, profile_side: bool, coven_side: bool) -> list[Path]:
        """The closed folders a call may not name the parents of: those of the profile side (what
        a listing of a parent reads: the serve and data folders, or the whole profile) and, for a
        call that writes, the coven root."""
        roots: list[Path] = []
        if profile_side:
            roots.extend((self.serve, self.data) if mode is Mode.READ else (self.profile,))
        if coven_side and mode is not Mode.READ:
            roots.append(self.coven)
        return roots


def _real(path: Path) -> Path:
    try:
        return path.resolve()
    except (OSError, RuntimeError, ValueError):
        return Path(os.path.abspath(path))


def _resolve(text: str, base: Path) -> Path | None:
    """The real path `text` names from folder `base`; None when it cannot be a path."""
    if "\0" in text:
        return None
    joined = Path(os.path.join(base, text))
    try:
        return joined.resolve()
    except (OSError, RuntimeError, ValueError):
        return Path(os.path.abspath(joined))


def hit(path: Path, scope: Scope, mode: Mode) -> str | None:
    """The rule a real path breaks, or None."""
    if path.is_relative_to(scope.serve):
        return "the server's serve folder (its password) is closed to a PM"
    if re.fullmatch(r"/proc/[^/]+(?:/task/[^/]+)?/environ", str(path)):
        return "another process's environment is closed to a PM"
    if mode is Mode.READ:
        if path.is_relative_to(scope.data):
            return "the profile's data folder (credentials and sessions) is closed to a PM"
        return None
    in_profile = path.is_relative_to(scope.profile) and not path.is_relative_to(scope.tmp)
    in_coven = path.is_relative_to(scope.coven) and not path.is_relative_to(scope.worktrees)
    if in_profile or in_coven:
        return "the profile and coven's own files are protected from a PM"
    return None


def holds_closed_place(path: Path, view: "View", mode: Mode) -> str | None:
    """The rule a folder breaks by holding a place closed to the mode (a listing, a recursive
    read or a copy of it reaches what is under it), or None."""
    roots = view.scope.roots_to_hold(
        mode, profile_side=view.profile_folders_closed, coven_side=view.coven_folders_closed
    )
    if any(root != path and root.is_relative_to(path) for root in roots):
        return (
            "a folder that holds the OpenCode profile or coven's own files is closed to this call"
        )
    return None


# ---- reading shell text as plain text --------------------------------------------------------

ANSI_C = re.compile(r"\$'((?:[^'\\]|\\.)*)'", re.DOTALL)
WORD_BREAKS = re.compile(r"[\s;|&<>()]+")
PART_BREAKS = re.compile(r"[=,:{}]")
BRACES = re.compile(r"\{([^{}]*,[^{}]*)\}")
ASSIGNMENT = re.compile(r"(?<![\w$])([A-Za-z_]\w*)=([^\s;|&<>()]*)")
VARIABLE = re.compile(r"\$(?:\{(\w+)\}|([A-Za-z_]\w*)|[$?!#@*0-9_-])")
UNRESOLVED = re.compile(r"\$[A-Za-z_{]")
CD_COMMAND = re.compile(r"(?:^|[;&|(\s{])(?:cd|pushd)\s+(?:-[LPe@]+\s+)?(?:--\s+)?([^\s;&|()<>]+)")
# `cd`, `cd -P`, `cd -L`, `cd --`, `cd -P --`: the home folder.
BARE_CD = re.compile(r"(?:^|[;&|(\s{])cd(?:\s+-[LPe@]+)*(?:\s+--)?\s*(?:$|[;&|)])")
# `NAME=$(...)` or NAME=`...`: a variable whose value is not known to the guard.
SUBSTITUTION_ASSIGNMENT = re.compile(r"(?<![\w$])([A-Za-z_]\w*)=(?:\$\(|`)")
# `GIT_PAGER=`, `GIT_SSH_COMMAND=`, `PAGER=`, `MANPAGER=`: variables git and man run as commands.
RUNNING_ENVIRONMENT = re.compile(r"(?<!\w)(?:GIT_\w+|\w*PAGER)=")
SHELL_READS_PROCESS = re.compile(
    r"(?:^|[;&|(\s{])(?:" + "|".join(sorted(wrapper_commands.SHELLS)) + r"|source|\.)"
    r"(?:\s[^;&|\n]*)?<\s*\("
)
# A glob bracket that bash reads differently from Python: `[^x]`, `[!x]`, `[[:alpha:]]`, `[[.x.]]`.
BRACKET_CLASS = re.compile(r"\[[\^!]|\[\[[:.=]")
GLOB_MARKS = "*?["
ENVIRON_TEXT = re.compile(r"/proc\b[^\s;|&<>()]*\benviron\b")
LOOPBACK_PORT = re.compile(
    r"(?:localhost|127\.\d+\.\d+\.\d+|0\.0\.0\.0|\[?::1?\]?)\s*[: ]\s*\d+", re.IGNORECASE
)


def _ansi_c(text: str) -> str:
    """`$'\\x70'` as the shell reads it."""

    def decode(match: re.Match[str]) -> str:
        try:
            raw = match.group(1).encode("latin-1", "backslashreplace")
            return codecs.decode(raw, "unicode_escape")
        except (UnicodeError, ValueError):
            return match.group(1)

    return ANSI_C.sub(decode, text)


def _variants(text: str) -> list[str]:
    """The text with its quoting taken off in the two ways a word can be cut: quotes as breaks,
    and quotes and backslashes removed (`pass""word` is `password`)."""
    found: list[str] = []
    for source in dict.fromkeys((text, _ansi_c(text))):
        found.append(re.sub(r"['\"`\\]", " ", source))
        found.append(re.sub(r"['\"\\]", "", source).replace("`", " "))
    return list(dict.fromkeys(found))


def _braces(word: str) -> list[str]:
    """The words `a{b,c}d` stands for (bash brace expansion), at most MAX_BRACE_WORDS."""
    pending = [word]
    done: list[str] = []
    while pending and len(done) + len(pending) <= MAX_BRACE_WORDS:
        current = pending.pop()
        match = BRACES.search(current)
        if match is None:
            done.append(current)
            continue
        for choice in match.group(1).split(","):
            pending.append(current[: match.start()] + choice + current[match.end() :])
    return done + pending


def _words_of(text: str) -> list[str]:
    """Every word, brace expansion of a word and part of a word (`--opt=/x`, `A=/x:/y`) of the
    text under each of its variants, without repeats."""
    found: dict[str, None] = {}
    for variant in _variants(text):
        for chunk in WORD_BREAKS.split(variant):
            for word in _braces(chunk) if chunk else []:
                found[word] = None
                found.update(dict.fromkeys(part for part in PART_BREAKS.split(word) if part))
    return list(found)


class Names:
    """The variables a command line can be told apart by: what it sets itself, then HOME and PWD,
    then this process's own environment."""

    def __init__(self, text: str, scope: Scope, cwd: Path) -> None:
        self.values: dict[str, str] = {}
        # What a `$(...)` or backtick prints is not seen, so a variable set from one has no value.
        self.unknown: set[str] = set()
        for source in dict.fromkeys((text, _ansi_c(text))):
            self.unknown.update(SUBSTITUTION_ASSIGNMENT.findall(re.sub(r"['\"\\]", "", source)))
        for variant in _variants(text):
            for name, value in ASSIGNMENT.findall(variant):
                if name not in self.unknown:
                    self.values[name] = value
        self.fallback = {"HOME": str(scope.home), "PWD": str(cwd)}

    def lookup(self, name: str) -> str | None:
        if name in self.unknown:
            return None
        if name in self.values:
            return self.values[name]
        return self.fallback.get(name, os.environ.get(name))

    def mentions_unknown(self, word: str) -> bool:
        """Whether the word uses a variable that was set from a command substitution."""
        return any(
            (match.group(1) or match.group(2)) in self.unknown for match in VARIABLE.finditer(word)
        )

    def expand(self, word: str) -> str:
        def replace(match: re.Match[str]) -> str:
            name = match.group(1) or match.group(2)
            if name is None:
                return "0"  # `$$`, `$?`, `$1`...: a number, whatever it is
            value = self.lookup(name)
            return match.group(0) if value is None else value

        for _ in range(4):
            expanded = VARIABLE.sub(replace, word)
            if expanded == word:
                break
            word = expanded
        return word


def _tilde(word: str, cwd: Path) -> str:
    if word == "~+" or word.startswith("~+/"):
        return str(cwd) + word[2:]
    return os.path.expanduser(word) if word.startswith("~") else word


def _drop_dollar(word: str) -> str:
    """`$"/x"` and `$'/x'` leave a `$` before the path once their quotes are gone."""
    return word[1:] if word.startswith("$") and word[1:2] in ("/", "~") else word


def _may_reach(folder: Path, scope: Scope, mode: Mode) -> bool:
    """Whether a pattern that starts in `folder` can match inside a place closed to the mode."""
    roots = scope.closed_roots(mode)
    return any(root.is_relative_to(folder) or folder.is_relative_to(root) for root in roots)


def _glob_matches(pattern: str, folder: Path, scope: Scope, mode: Mode) -> list[Path]:
    """What a glob matches (a `**` is followed only where it could reach a closed place, so that a
    pattern on a big tree stays cheap); raises Refused when it matches MAX_GLOB_MATCHES paths."""
    recursive = "**" in pattern and _may_reach(folder, scope, mode)
    try:
        found = list(itertools.islice(glob.iglob(pattern, recursive=recursive), MAX_GLOB_MATCHES))
    except (OSError, ValueError):
        return []
    if len(found) >= MAX_GLOB_MATCHES:
        raise Refused("the glob has too many matches; narrow the glob")
    return [_real(Path(each)) for each in found]


def _candidates(word: str, bases: Sequence[Path], cwd: Path, names: Names) -> list[str]:
    """The absolute forms of a word, from each folder it may be read in; raises Refused for a
    path built from a variable the guard cannot read."""
    expanded = _tilde(names.expand(_drop_dollar(word)), cwd)
    if UNRESOLVED.search(expanded) and "/" in expanded and not expanded.startswith("//"):
        raise Refused("a path built from an unknown variable cannot be checked")
    if os.path.isabs(expanded):
        return [expanded]
    return [os.path.join(base, expanded) for base in bases]


@dataclass(frozen=True)
class View:
    """What a call's words are read against: the places, the working folder and the variables."""

    scope: Scope
    cwd: Path
    names: Names
    # Whether a folder that holds a closed place is closed too: one that holds the serve or data
    # folder or the profile (`grep -r . <home>`, Grep on `/`), and one that holds the coven root,
    # which is closed only to a call that can write (`rm -r <projects>`).
    profile_folders_closed: bool = True
    coven_folders_closed: bool = False


def check_word(word: str, bases: Sequence[Path], view: View, mode: Mode) -> None:
    """Raise Refused when the word, as a path from any of the folders, is closed to the mode."""
    for candidate in _candidates(word, bases, view.cwd, view.names):
        if any(mark in candidate for mark in GLOB_MARKS):
            # The folder a glob starts in is not read; what it matches is.
            first = min(candidate.index(mark) for mark in GLOB_MARKS if mark in candidate)
            folder = _real(Path(candidate[: candidate.rfind("/", 0, first) + 1] or "/"))
            rule = hit(folder, view.scope, mode)
            if rule:
                raise Refused(rule)
            found = _glob_matches(candidate, folder, view.scope, mode)
            # Python and bash read some brackets differently (`[^x]`, `[[:alpha:]]`), and a
            # pattern that matches nothing here may match in what bash sees: neither is judged
            # by its matches where it starts in a folder that holds a closed place.
            if not found or BRACKET_CLASS.search(candidate):
                rule = holds_closed_place(folder, view, mode)
                if rule:
                    raise Refused(rule)
        else:
            resolved = _resolve(candidate, view.cwd)
            found = [] if resolved is None else [resolved]
        for path in found:
            rule = hit(path, view.scope, mode)
            if not rule:
                rule = holds_closed_place(path, view, mode)
            if rule:
                raise Refused(rule)


def _folders(text: str, cwd: Path, names: Names, scope: Scope) -> list[Path]:
    """`cwd` and every folder a `cd` or `pushd` of the line moves to, in order."""
    folders = [cwd]
    for variant in _variants(text):
        here = cwd
        for target in CD_COMMAND.findall(variant):
            expanded = _tilde(names.expand(target), here)
            if "$" in expanded or any(mark in expanded for mark in GLOB_MARKS):
                continue
            moved = _resolve(expanded, here)
            if moved is not None:
                folders.append(moved)
                here = moved
        if BARE_CD.search(variant):
            folders.append(_real(scope.home))
    return list(dict.fromkeys(folders))


# ---- reading shell text as commands ----------------------------------------------------------


@dataclass(frozen=True)
class Argv:
    """One simple command as words, whether a pipe feeds it, the text of each `$(...)` in its
    words, and the text it reads from a heredoc or `<<<` (a shell runs it)."""

    words: tuple[str, ...]
    piped: bool = False
    substitutions: tuple[str, ...] = ()
    inputs: tuple[str, ...] = ()


SEPARATORS = re.compile(r"(\|\||&&|\||;|&|\n)")
SHELLS: frozenset[str] = wrapper_commands.SHELLS
SHELL_PREFIXES = frozenset({"{", "}", "!", "if", "then", "else", "elif", "do", "while", "until"})
SHELL_ASSIGNMENT = re.compile(r"[A-Za-z_]\w*(?:\[[^\]]*\])?\+?=")
OPENCODE_NAME = re.compile(r"opencode(?:[-@].*)?")
RUNNER_ARGUMENTS = {
    "pnpm": frozenset({"dlx", "exec"}),
    "yarn": frozenset({"dlx", "exec"}),
    "npm": frozenset({"exec", "x"}),
    "bun": frozenset({"x"}),
}


@dataclass(frozen=True)
class Wrapper:
    """How a command that runs another reads its words: options that take a value, and the
    words that follow the options (timeout's duration)."""

    valued: frozenset[str] = frozenset()
    operands: int = 0
    assignments: bool = False


def _wrapper(valued: str = "", operands: int = 0, assignments: bool = False) -> Wrapper:
    return Wrapper(frozenset(valued.split()), operands, assignments)


# How the wrappers of wrapper_commands.WRAPPERS that this guard walks past read their options. A
# wrapper that is not here (a shell, `su`, `script`, `tmux`...) is not walked past; its command
# strings are read, and any word that names opencode after it is refused anyway.
OPTION_SYNTAX = {
    "env": _wrapper("-u --unset -C --chdir", assignments=True),
    "exec": _wrapper("-a"),
    "command": _wrapper(),
    "builtin": _wrapper(),
    "nice": _wrapper("-n --adjustment"),
    "nohup": _wrapper(),
    "time": _wrapper("-f -o --format --output"),
    "timeout": _wrapper("-s -k --signal --kill-after", operands=1),
    "sudo": _wrapper(
        "-C -D -g -p -R -r -T -t -U -u --chdir --group --prompt --user", assignments=True
    ),
    "doas": _wrapper("-u -C"),
    "stdbuf": _wrapper("-i -o -e"),
    "setsid": _wrapper(),
    "ionice": _wrapper("-c -n -p -P -u -t"),
    "xargs": _wrapper("-a -E -I -L -n -P -s -d --arg-file --max-args --max-procs --delimiter"),
    "watch": _wrapper("-n -d --interval"),
    "unbuffer": _wrapper(),
    "npx": _wrapper("-p --package -c --call"),
    "bunx": _wrapper("-p --package"),
    "pnpx": _wrapper("-p --package"),
    "taskset": _wrapper(operands=1),
    "chrt": _wrapper(operands=1),
    "flock": _wrapper("-w -E -c --timeout --conflict-exit-code --command", operands=1),
    "unshare": _wrapper("-S -G -R -w --root --wd --setuid --setgid --propagation --map-user"),
    "setpriv": _wrapper(
        "--reuid --regid --ruid --euid --rgid --egid --groups --inh-caps --bounding-set"
        " --ambient-caps --securebits --pdeathsig --selinux-label --apparmor-profile"
    ),
    "runuser": _wrapper("-u -g -G -s -c -l --user --group --supp-group --shell --command"),
    "strace": _wrapper("-e -o -p -s -u -E -a -I -P -S -X -b -O -U --env --output"),
    "ltrace": _wrapper("-e -o -p -s -u -A -n -l --output"),
    "parallel": _wrapper("-j -a -S -N -n -L -P --jobs --arg-file --sshlogin --max-args"),
    "systemd-run": _wrapper(
        "-p -u -E -M -G --property --unit --setenv --machine --slice --description --uid --gid"
        " --working-directory --on-active --on-calendar"
    ),
    "busybox": _wrapper(),
}
RUNNER_OPTIONS = _wrapper("-p --package -c --call -C --prefix --workspace -w")
FIND_ACTIONS = frozenset({"-exec", "-execdir", "-ok", "-okdir"})
COMMAND_STRING_OPTIONS = frozenset({"-c", "--call"})
RUNNER_NAMES = frozenset({"npx", "bunx", "pnpx", *RUNNER_ARGUMENTS})
# Programs that run the text of an option as a shell command, and that option (a short option that
# holds `c` for `sh -c`, `su -lc`, `script -qc`, `flock -c`).
LAUNCHER_STRING_OPTIONS = {
    "nix-shell": frozenset({"--run", "--command"}),
    "nix": frozenset({"-c", "--command"}),
}
# Programs that never run a word of their own arguments as a program, so a word that names
# opencode after one of them is only text (`grep -rn opencode .`, `cd opencode`). `find`'s actions
# and `coven opencode` are checked by their own rules.
NEVER_EXECUTES = frozenset(
    {
        *("echo", "printf", "git", "grep", "rg", "ls", "cd", "pwd", "cat", "head", "tail", "wc"),
        *("test", "[", "man", "which", "type", "coven"),
        # Added by the guard: they only read, copy or name paths.
        *("find", "tree", "stat", "du", "diff", "cp", "mv", "mkdir", "touch", "file", "realpath"),
        *("basename", "dirname"),
    }
)
# The options of the programs known to take a folder as their value: that folder may be named
# `opencode` without being run (`npm --prefix opencode test`, `tsc -p opencode`).
PATH_OPTIONS = {
    **dict.fromkeys(("npm", "pnpm", "yarn"), frozenset({"--prefix", "-C", "--dir"})),
    "tsc": frozenset({"-p", "--project"}),
}
# Programs that never read the content of a folder they are given, so a folder that holds the
# profile or coven's files may be named to them (`ls ~`, `cd ..`).
NEVER_READS_CONTENT = frozenset(
    {
        *("ls", "cd", "pushd", "popd", "pwd", "stat", "test", "[", "[[", "echo", "printf"),
        *("dirname", "basename", "realpath", "readlink", "du", "df", "mkdir", "true", "false", ":"),
    }
)
# A folder that holds the coven root is closed to a line unless every program of it is one that
# only reads (and nothing is redirected to a file): these, `find` without a find action that
# writes or runs a command, and `git` with one of the reading subcommands.
READ_ONLY_PROGRAMS = frozenset(
    {
        *("ls", "cd", "pushd", "popd", "pwd", "stat", "test", "[", "cat", "head", "tail", "wc"),
        *("grep", "rg", "ag", "tree", "du"),
    }
)
FIND_WRITING_ACTIONS = frozenset(
    {*("-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprint0", "-fprintf", "-fls")}
)
GIT_READING = frozenset({"status", "log", "diff", "show", "grep", "blame", "rev-parse"})
GIT_VALUED_OPTIONS = frozenset({"-C", "-c", "--git-dir", "--work-tree", "--namespace"})


def _base(word: str) -> str:
    return os.path.basename(word.rstrip("/")) if "/" in word else word


def _crude(text: str) -> list[Argv]:
    """The commands of a text the scanner could not follow: split at the separators, `(`, `)`,
    `$(` and backticks, then into words as the shell would (or at blanks when it cannot)."""
    flat = re.sub(r"\$\(|[`()]", ";", text)
    found: list[Argv] = []
    piped = False
    for part in SEPARATORS.split(flat):
        if SEPARATORS.fullmatch(part):
            piped = part == "|"
            continue
        try:
            words = shlex.split(part)
        except ValueError:
            words = part.split()
        if words:
            found.append(Argv(tuple(words), piped))
        piped = False
    return found


def _commands(text: str) -> list[Argv]:
    """The simple commands of a text."""
    scanner = hook_support.sibling("shell_words")
    scanned = scanner.simple_commands(text, substitutions=True)
    if scanned is None:
        return _crude(text)
    found: list[Argv] = []
    for command in scanned:
        words = tuple(word.value if word.value is not None else word.raw for word in command.words)
        if words:
            substitutions = tuple(inner for word in command.words for inner in word.substitutions)
            inputs = tuple(
                text[start:end] for start, end in (each.body for each in command.heredocs)
            )
            strings = tuple(value for value in command.redirect_inputs if value is not None)
            found.append(Argv(words, command.after_pipe, substitutions, inputs + strings))
    return found


def _skip_options(words: Sequence[str], start: int, wrapper: Wrapper) -> int:
    index = start
    while index < len(words):
        word = words[index]
        if word == "--":
            index += 1
            break
        if word.startswith("-") and word != "-":
            index += 2 if word in wrapper.valued else 1
        elif wrapper.assignments and SHELL_ASSIGNMENT.match(word):
            index += 1
        else:
            break
    return index + wrapper.operands


def _after_prefixes(words: Sequence[str], start: int) -> int:
    index = start
    while index < len(words) and (
        SHELL_ASSIGNMENT.match(words[index]) or words[index] in SHELL_PREFIXES
    ):
        index += 1
    return index


def _split_string(words: Sequence[str], index: int) -> list[str] | None:
    """`env -S 'a b'` at words[index]: the words with the string split in place."""
    word = words[index]
    if word in ("-S", "--split-string") and index + 1 < len(words):
        value, rest = words[index + 1], list(words[index + 2 :])
    elif word.startswith("--split-string="):
        value, rest = word.partition("=")[2], list(words[index + 1 :])
    elif word.startswith("-S") and len(word) > 2:
        value, rest = word[2:], list(words[index + 1 :])
    else:
        return None
    try:
        return [*words[:index], *shlex.split(value), *rest]
    except ValueError:
        return [*words[:index], *value.split(), *rest]


def _unwrapped(words: Sequence[str]) -> list[str]:
    """The command under its assignments and wrappers (env, timeout, xargs, npx, pnpm dlx...)."""
    current = list(words)
    index = _after_prefixes(current, 0)
    for _ in range(MAX_UNWRAP):
        if index >= len(current):
            break
        name = _base(current[index])
        wrapper = OPTION_SYNTAX.get(name)
        if (
            wrapper is None
            and current[index + 1 : index + 2]
            and (current[index + 1] in RUNNER_ARGUMENTS.get(name, frozenset()))
        ):
            wrapper, index = RUNNER_OPTIONS, index + 1
        if wrapper is None:
            break
        if name == "env":
            for position in range(index + 1, len(current)):
                split = _split_string(current, position)
                if split is not None:
                    current = split
                    break
        index = _after_prefixes(current, _skip_options(current, index + 1, wrapper))
    return current[index:]


def _short_c_option(word: str) -> bool:
    """`-c`, `-lc`, `-qc`: a short option that holds `c`."""
    return word.startswith("-") and not word.startswith("--") and "c" in word


def _command_strings(words: Sequence[str]) -> list[str]:
    """The texts that words run as shell, wherever the program that runs one stands in them (a
    wrapper may put options and operands before it): `bash -c X`, `su -c X`, `script -qc X`,
    `flock -c X`, `runuser u -c X`, `busybox sh -c X`, `nix-shell --run X`, `eval X`."""
    found: list[str] = []
    for index, word in enumerate(words):
        name, rest = _base(word), list(words[index + 1 :])
        if name == "eval":
            found.append(" ".join(rest))
        elif name in wrapper_commands.COMMAND_STRING_RUNNERS:
            found.extend(_after_option(rest, _short_c_option))
        elif name in LAUNCHER_STRING_OPTIONS:
            found.extend(_after_option(rest, LAUNCHER_STRING_OPTIONS[name].__contains__))
        elif name in wrapper_commands.ANYWHERE_WRAPPERS:
            found.extend(rest)
    return found


def _after_option(words: Sequence[str], is_option: Callable[[str], bool]) -> list[str]:
    return [words[index + 1] for index, word in enumerate(words[:-1]) if is_option(word)]


def _runner_strings(words: Sequence[str]) -> list[str]:
    """`npx -c X` and `pnpm dlx -c X`: the runner's own option is a shell string, and the
    unwrapping that follows the options past it does not keep it."""
    found: list[str] = []
    for index, word in enumerate(words):
        if _base(word) in RUNNER_NAMES:
            found.extend(_after_option(words[index + 1 :], COMMAND_STRING_OPTIONS.__contains__))
    return found


def _anywhere_commands(words: Sequence[str], piped: bool) -> list[Argv]:
    """`tmux new -d -s pm opencode db`, `screen -dm opencode db`: the command may follow any of
    their words, so every tail of the words is read as a command."""
    found: list[Argv] = []
    for index, word in enumerate(words):
        if _base(word) in wrapper_commands.ANYWHERE_WRAPPERS:
            found.extend(
                Argv(tuple(words[later:]), piped) for later in range(index + 1, len(words))
            )
    return found


def _find_actions(words: Sequence[str], piped: bool) -> list[Argv]:
    """The commands of a find's `-exec`, `-execdir`, `-ok` and `-okdir` actions."""
    actions: list[Argv] = []
    index = 0
    while index < len(words):
        if words[index] in FIND_ACTIONS:
            end = index + 1
            while end < len(words) and words[end] not in (";", "+", "\\;"):
                end += 1
            actions.append(Argv(tuple(words[index + 1 : end]), piped))
            index = end
        index += 1
    return actions


def _nested(argv: Argv, depth: int) -> list[Argv]:
    """The commands inside one: shell strings, `eval`, runner strings and find actions."""
    if depth > MAX_NESTING:
        raise Refused("a command nested this deeply cannot be checked")
    command = _unwrapped(argv.words)
    found: list[Argv] = []
    strings = [
        *_command_strings(argv.words),
        *_command_strings(command),
        *_runner_strings(argv.words),
        *argv.substitutions,
    ]
    if command and _base(command[0]) in SHELLS:
        strings.extend(argv.inputs)
    for inner in strings:
        found.extend(argvs(inner, depth + 1))
    if command and _base(command[0]) == "find":
        for action in _find_actions(command[1:], argv.piped):
            found.append(action)
            found.extend(_nested(action, depth + 1))
    found.extend(_anywhere_commands(argv.words, argv.piped))
    return found


SUBSTITUTION_RULE = (
    "a command substitution builds a program or a path that cannot be checked,"
    " so write the path literally"
)
# `$(` or a backtick where the program of a command goes: after a separator, a prefix word or a
# wrapper that runs what follows it, and any `NAME=value` words.
SUBSTITUTION_PROGRAM = re.compile(
    r"(?:^|[;&|(\n{]|\b(?:then|do|else|elif|while|until|if|exec|env|nohup|command|builtin|sudo"
    r"|doas|setsid|time|timeout|xargs|nice|stdbuf|watch|npx|bunx|eval)\b)"
    r"\s*(?:[A-Za-z_]\w*=\S*\s+)*(?:\$\(|`)"
)
SUBSTITUTION_CD = re.compile(r"(?:^|[;&|(\s{])(?:cd|pushd)\s+(?:-\S+\s+)*(?:\$\(|`)")
VARIABLE_TARGET = re.compile(r"(?:(?:^|[;&|(\s{])(?:cd|pushd)\s+(?:-\S+\s+)*|[<>]\s*)$")


def _substitution_spans(text: str) -> list[tuple[int, int]]:
    """Where each `$(...)` (to its closing parenthesis) and each backtick pair starts and ends."""
    spans: list[tuple[int, int]] = []
    index = 0
    while index < len(text):
        if text.startswith("$(", index):
            depth, end = 0, index + 1
            while end < len(text):
                depth += {"(": 1, ")": -1}.get(text[end], 0)
                if depth == 0:
                    break
                end += 1
            spans.append((index, min(end + 1, len(text))))
            index += 2
        elif text[index] == "`":
            close = text.find("`", index + 1)
            end = len(text) if close < 0 else close + 1
            spans.append((index, end))
            index = end
        else:
            index += 1
    return spans


def _variable_builds_a_path(plain: str, name: str) -> bool:
    """Whether `$name` stands next to a `/`, is a `cd` target or is a redirection target."""
    for use in re.finditer(rf"\$(?:\{{{name}\}}|{name}\b)", plain):
        before, after = plain[: use.start()], plain[use.end() :]
        if before.endswith("/") or after.startswith("/") or VARIABLE_TARGET.search(before):
            return True
    return False


def check_process_substitution(text: str) -> None:
    """Raise Refused for `bash <(...)`, `source <(...)` and `. <(...)`: a shell that reads what
    a command prints, the same as a pipe into a shell."""
    for source in dict.fromkeys((text, _ansi_c(text))):
        if SHELL_READS_PROCESS.search(re.sub(r"['\"\\]", "", source)):
            raise Refused("a shell that reads a process substitution cannot be checked")


def check_substitutions(text: str) -> None:
    """Raise Refused for a `$(...)` or backtick whose output is a program, a `cd` target or part
    of a path (next to a `/`, or in a variable that is): what it prints is not seen, so a path
    built by one cannot be judged. `echo $(date)` and `x=$(date); echo $x` are fine."""
    for source in dict.fromkeys((text, _ansi_c(text))):
        plain = re.sub(r"['\"\\]", "", source)
        spans = _substitution_spans(plain)
        if not spans:
            continue
        beside_path = any(
            plain[start - 1 : start] == "/" or plain[end : end + 1] == "/" for start, end in spans
        )
        if (
            beside_path
            or SUBSTITUTION_PROGRAM.search(plain)
            or SUBSTITUTION_CD.search(plain)
            or any(
                _variable_builds_a_path(plain, name)
                for name in dict.fromkeys(SUBSTITUTION_ASSIGNMENT.findall(plain))
            )
        ):
            raise Refused(SUBSTITUTION_RULE)


def argvs(text: str, depth: int = 0) -> list[Argv]:
    """Every command in the text, through nested shell strings, `eval`, `$(...)` and `find -exec`."""
    if depth > MAX_NESTING:
        raise Refused("a command nested this deeply cannot be checked")
    check_substitutions(text)
    check_process_substitution(text)
    found: list[Argv] = []
    for argv in _commands(text):
        found.append(argv)
        found.extend(_nested(argv, depth))
    return found


# ---- the command rules -----------------------------------------------------------------------

PS_VALUED = frozenset("oOpCUugGtsD")
PS_PIDS = re.compile(r"[\d,]+")
PS_LONG_VALUED = frozenset({"--sort", "--pid", "--user", "--group", "--format", "--ppid"})
ALLOWED_OPENCODE = (("--version",), ("--help",))


def _ps_prints_environments(arguments: Sequence[str]) -> bool:
    """`ps` with the BSD flag `e` (`aux e`, `eww`, `axe`) or the option `-E`."""
    skip = False
    for argument in arguments:
        if skip:
            skip = False
        elif argument.startswith("--"):
            skip = argument in PS_LONG_VALUED
        elif argument.startswith("-"):
            letters = argument[1:]
            cut = next((i for i, letter in enumerate(letters) if letter in PS_VALUED), None)
            flags = letters if cut is None else letters[:cut]
            if "E" in flags:
                return True
            skip = cut is not None and cut == len(letters) - 1
        elif not PS_PIDS.fullmatch(argument) and "e" in argument:
            return True
    return False


def _coven_lifecycle(words: Sequence[str]) -> bool:
    """`coven opencode <anything but doctor or --help>`, wherever `coven` stands in the words
    (`uv run coven`, `python3 -m coven`)."""
    for index, word in enumerate(words):
        if _base(word) != "coven":
            continue
        rest = [each for each in words[index + 1 :] if not each.startswith("-")]
        if rest[:1] != ["opencode"]:
            continue
        arguments = list(words[index + 1 :])
        subcommand = [each for each in arguments if not each.startswith("-")][1:2]
        helps = any(each in ("--help", "-h") for each in arguments)
        if not (helps or subcommand == ["doctor"]):
            return True
    return False


def _is_directory(word: str, cwd: Path) -> bool:
    resolved = _resolve(_tilde(word, cwd), cwd)
    return resolved is not None and resolved.is_dir()


def _names_opencode(words: Sequence[str], cwd: Path) -> bool:
    """Whether a word after the program names opencode (`uv run opencode db`, `coproc opencode`).
    A word that is a folder is a path, not a program, when it holds a `/` (`./opencode`,
    `opencode/`) or is the value of a path option of a program that is known to take one (npm's
    `--prefix`, tsc's `-p`); after any other program it may be run (`uv run -q opencode db`)."""
    start = _after_prefixes(words, 0)
    path_options = PATH_OPTIONS.get(_base(words[start]), frozenset()) if start < len(words) else ()
    for index in range(start + 1, len(words)):
        word = words[index]
        if not OPENCODE_NAME.fullmatch(_base(word)):
            continue
        if ("/" in word or words[index - 1] in path_options) and _is_directory(word, cwd):
            continue
        return True
    return False


def _uses_unknown_variable(command: Sequence[str], names: Names) -> bool:
    """Whether a program that is not one that only names paths is given a variable set from a
    command substitution (`d=$(...); cat $d`, `eval $X`, `bash -c "$X"`)."""
    return _base(command[0]) not in NEVER_READS_CONTENT and any(
        names.mentions_unknown(word) for word in command[1:]
    )


def _opencode_problem(words: Sequence[str], command: Sequence[str], cwd: Path) -> None:
    """The opencode rules: only `--version` and `--help`, and no word that names it after a
    program that may run it."""
    name = _base(command[0])
    if OPENCODE_NAME.fullmatch(name):
        if tuple(command[1:]) not in ALLOWED_OPENCODE:
            raise Refused("`opencode` is closed to a PM but for `opencode --version` and `--help`")
    elif name not in NEVER_EXECUTES and (
        _names_opencode(words, cwd) or _names_opencode(command, cwd)
    ):
        raise Refused("a program that may run `opencode` is closed to a PM when it names it")


def check_command(argv: Argv, names: Names, cwd: Path) -> None:
    words = [names.expand(word) for word in argv.words]
    command = [names.expand(word) for word in _unwrapped(argv.words)]
    if not command:
        return
    program = command[0]
    if "$(" in program or "`" in program or _uses_unknown_variable(command, names):
        raise Refused(SUBSTITUTION_RULE)
    if UNRESOLVED.search(program):
        raise Refused("a command whose program is built at run time cannot be checked")
    name = _base(program)
    if _runs_other_programs(name, command[1:]):
        raise Refused("an option that makes this program run a command is closed to a PM")
    _opencode_problem(words, command, cwd)
    if name == "ps" and _ps_prints_environments(command[1:]):
        raise Refused("`ps` options that print other processes' environments are closed to a PM")
    if _coven_lifecycle(command):
        raise Refused("`coven opencode` is closed to a PM but for `doctor` and `--help`")
    if argv.piped and name in SHELLS:
        raise Refused("a pipe into a shell cannot be checked")


def check_text(text: str, scope: Scope) -> None:
    """The rules that read the text of a command or a path as it stands."""
    for variant in (text, *_variants(text)):
        if scope.port is not None and re.search(rf"(?<!\d){scope.port}(?!\d)", variant):
            raise Refused("the coven OpenCode server's port is closed to a PM")
        if scope.port is None and LOOPBACK_PORT.search(variant):
            raise Refused("a loopback port is closed to a PM while the server's port is unknown")
        if ENVIRON_TEXT.search(variant):
            raise Refused("another process's environment is closed to a PM")
        if RUNNING_ENVIRONMENT.search(variant):
            raise Refused("a variable that makes git or man run a command is closed to a PM")


def _only_names_paths(commands: Sequence[Argv]) -> bool:
    """Whether every program of the line is one that never reads the content of a folder."""
    return all(
        _base(program[0]) in NEVER_READS_CONTENT
        for program in (_unwrapped(each.words) for each in commands)
        if program
    )


def _git_parts(arguments: Sequence[str]) -> tuple[list[str], str | None, list[str]]:
    """git's options before its subcommand, the subcommand, and the words after it."""
    options: list[str] = []
    index = 0
    while index < len(arguments):
        word = arguments[index]
        if not word.startswith("-"):
            return options, word, list(arguments[index + 1 :])
        options.append(word)
        if word in GIT_VALUED_OPTIONS and index + 1 < len(arguments):
            options.append(arguments[index + 1])
            index += 1
        index += 1
    return options, None, []


def _runs_other_programs(name: str, arguments: Sequence[str]) -> bool:
    """`git -c`, `git bisect run`, `git rebase --exec`, `git submodule foreach`, `git difftool -x`,
    `man -P` and `rg --pre`: options that make a program run a command that is given as text."""
    if name == "man":
        return any(each.startswith(("-P", "--pager")) for each in arguments)
    if name == "rg":
        return any(each.startswith("--pre") for each in arguments)
    if name != "git":
        return False
    options, subcommand, rest = _git_parts(arguments)
    if any(each.startswith(("-c", "--config-env")) for each in options):
        return True
    return (
        (subcommand == "bisect" and "run" in rest)
        or (subcommand == "submodule" and "foreach" in rest)
        or (subcommand == "rebase" and any(each.startswith(("--exec", "-x")) for each in rest))
        or (subcommand == "difftool" and any(each.startswith(("--extcmd", "-x")) for each in rest))
    )


def _reads_only(words: Sequence[str]) -> bool:
    """Whether the command is one that only reads (a short allowlist: `ls`, `cat`, `grep`, `rg`,
    `find` without an action that writes or runs, `git status|log|diff|show|grep|blame`...)."""
    if not words:
        return True
    name = _base(words[0])
    if name == "find":
        return not FIND_WRITING_ACTIONS.intersection(words[1:])
    if name == "git":
        _options, subcommand, rest = _git_parts(words[1:])
        return subcommand in GIT_READING or (subcommand == "branch" and rest in ([], ["--list"]))
    return name in READ_ONLY_PROGRAMS


def _only_reads(command: str, commands: Sequence[Argv]) -> bool:
    """Whether every program of the line only reads and nothing is redirected to a file."""
    return ">" not in command and all(_reads_only(_unwrapped(each.words)) for each in commands)


def shell_problem(command: str, cwd: Path, scope: Scope) -> None:
    check_text(command, scope)
    names = Names(command, scope, cwd)
    rule = hit(cwd, scope, Mode.SHELL)
    if rule:
        raise Refused(rule)
    commands = argvs(command)
    for argv in commands:
        check_command(argv, names, cwd)
    view = View(
        scope,
        cwd,
        names,
        profile_folders_closed=not _only_names_paths(commands),
        coven_folders_closed=not _only_reads(command, commands),
    )
    bases = _folders(command, cwd, view.names, scope)
    # The working folder and every folder a `cd` moves to (a bare `cd` is home): what a program
    # run there reads without naming a path (`rg password`, `grep -r x`, `find -name x`).
    for folder in bases:
        rule = holds_closed_place(folder, view, Mode.SHELL)
        if rule:
            raise Refused(rule)
    for word in _words_of(command):
        check_word(word, bases, view, Mode.SHELL)


# ---- the tools -------------------------------------------------------------------------------


def _text(tool_input: Mapping[str, object], key: str, *, required: bool = False) -> str | None:
    value = tool_input.get(key)
    if value is None and not required:
        return None
    if not isinstance(value, str):
        raise Refused(f"the {key} of this call cannot be read")
    return value


def _directory(payload: Mapping[str, object], tool_input: Mapping[str, object]) -> Path:
    cwd = payload.get("cwd")
    if not isinstance(cwd, str) or not cwd:
        raise Refused("the working directory of this call cannot be read")
    base = _real(Path(cwd))
    workdir = _text(tool_input, "workdir")
    if workdir is None:
        return base
    moved = _resolve(workdir, base)
    if moved is None:
        raise Refused("the working directory of this call cannot be read")
    return moved


def path_problem(
    tool_input: Mapping[str, object], cwd: Path, scope: Scope, mode: Mode, tool: str
) -> None:
    paths = [value for key in PATH_KEYS if (value := _text(tool_input, key)) is not None]
    pattern = _text(tool_input, "pattern") if tool == "Glob" else None
    if tool in ("Read", "Write", "Edit", "NotebookEdit", "MultiEdit") and not paths:
        raise Refused("the file of this call cannot be read")
    # A folder that holds a closed place is closed to a read of it; a write cannot replace one.
    view = View(scope, cwd, Names("", scope, cwd), profile_folders_closed=mode is Mode.READ)
    for path in paths or [str(cwd)]:
        check_word(path, [cwd], view, mode)
    if pattern is not None:
        where = [_resolve(path, cwd) or cwd for path in paths] or [cwd]
        for word in _braces(pattern):
            check_word(word, where, view, mode)


def refusal(
    payload: Mapping[str, object],
    profile: Path,
    serve: Path,
    coven_root: Path,
    port: int | None,
) -> str | None:
    """The text that refuses the call, or None to allow it."""
    try:
        tool = payload.get("tool_name")
        if not isinstance(tool, str):
            raise Refused("the tool of this call cannot be read")
        mode = TOOL_MODES.get(tool)
        if mode is None:
            return None
        tool_input = payload.get("tool_input")
        if not isinstance(tool_input, Mapping):
            raise Refused("the input of this call cannot be read")
        scope = Scope.of(profile, serve, coven_root, port)
        cwd = _directory(payload, tool_input)
        if mode is Mode.SHELL:
            command = _text(tool_input, "command", required=True)
            if command is None:
                raise Refused("the command of this call cannot be read")
            shell_problem(command, cwd, scope)
        else:
            path_problem(tool_input, cwd, scope, mode, tool)
    except Refused as refused:
        return str(refused)
    return None


# ---- the hook --------------------------------------------------------------------------------


def read_port(serve: Path) -> int | None:
    """The server's port from `<serve>/port`; None when the file is missing or holds no port."""
    try:
        text = (serve / "port").read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return None
    if not re.fullmatch(r"\d{1,5}", text):
        return None
    port = int(text)
    return port if 0 < port < 65536 else None


class _Arguments(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        print(f"coven {HOOK}: {message}; blocking", file=sys.stderr)
        sys.exit(2)


def _parse(argv: Sequence[str]) -> argparse.Namespace:
    parser = _Arguments(prog=f"coven {HOOK}", allow_abbrev=False)
    for option in ("--profile", "--serve", "--coven-root"):
        parser.add_argument(option, required=True, type=Path)
    return parser.parse_args(list(argv))


def run(argv: Sequence[str]) -> int:
    arguments = _parse(argv)
    try:
        payload = hook_support.loads(sys.stdin.read())
        if not isinstance(payload, dict):
            raise ValueError("payload is not an object")
    except ValueError as exc:
        print(f"coven {HOOK}: could not parse hook input ({exc}); blocking", file=sys.stderr)
        hook_support.log_failure(HOOK, f"could not parse hook input ({exc})")
        return 2
    text = refusal(
        payload,
        arguments.profile,
        arguments.serve,
        arguments.coven_root,
        read_port(arguments.serve),
    )
    if text:
        print(text, file=sys.stderr)
        return 2
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return run(sys.argv[1:] if argv is None else argv)
    # Fail closed on any error (closed stdin, a broken sibling module, a bug): Claude treats every
    # exit code but 2 as non-blocking.
    except Exception as exc:
        print(f"coven {HOOK}: internal error ({exc!r}); blocking", file=sys.stderr)
        logging.exception("coven %s: internal error; blocking", HOOK)
        hook_support.log_failure(HOOK, f"internal error ({exc!r})")
        return 2


if __name__ == "__main__":
    sys.exit(main())
