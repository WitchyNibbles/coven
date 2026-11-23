#!/usr/bin/env python3
"""PreToolUse hook: refuse tool calls that reach production or gated systems unless the user
approved that exact command.

Reads the hook payload on stdin and runs under fail-closed.sh like deny-secrets. Exit 2 refuses the
call and shows stderr to the agent; exit 0 lets it through. `prod_guard.py --check <guard.json>`
validates a file with the same rules before it is committed: it prints `ok: N targets` and exits 0,
or prints the reason and exits 1; it never reads stdin.

When it acts. The hook reads coven's config (`~/.config/coven/config.json`, as plain JSON, the way
memory-context does) for `memory_path` (default `~/coven-memory`, and it must be absolute) and `guard`, whose only valid value
is `"required"`. It is off, and exits 0 at once with no git call and no subprocess, when there is no
`"guard": "required"` and either no store folder or no `systems/guard.json` in its working tree. With
the latch a missing store folder is not off: Bash and MCP tools are refused for every session, since
the store held a guard.json once. With no config file the store is the default one, so deleting the file does not
switch off a store that holds a guard.json. It is on otherwise. A config that exists but cannot be read or is invalid
refuses Bash and MCP tools in a restricted session (the message points to `coven doctor`) and
leaves any other session as if the guard were off.

Who it restricts. The target rule applies to every session. A session whose COVEN_SESSION_ROLE is
set to any value, an empty or unknown one included, is also restricted: only an unset variable is
unrestricted. A subagent's call is judged like its session's. A restricted session meets the rules
in RESTRICTED_RULES first, and no approval lifts one. It may not:
- write the memory store, coven's config file (`~/.config/coven/config.json`, the one the hook
  reads), Claude Code's settings (`settings.json` and `settings.local.json` in CLAUDE_CONFIG_DIR or
  `~/.claude`, and in any folder's `.claude/`) or its `plugins/` folder: Write, Edit, MultiEdit and
  NotebookEdit under any of them, any MCP input that names one, and any Bash command that runs in
  one or whose text reaches it, by name, by a path that resolves into it (`~`, `$HOME`, symlinks),
  by a glob that can match it, or, after a `cd`, `env -C`, `git -C` or `find` in its holding folder,
  by its plain name. A glob character in quotes or after a backslash is literal (`ls '.*'`, `grep
  -E "[0-9]*"`, a quoted-delimiter heredoc fed to `cat` or python), except on a line where a shell, a
  wrapper, an interpreter, `ssh` or `watch` may run a quoted word. In Bash the `plugins/` folder
  may be read and its scripts run (READ_COMMANDS, `find` without actions, a printing `sed`, `bash`
  or python with a script, a program by its path, a lone `S=<dir>` used only there, a pipe into
  PIPE_FILTERS); any other use of it counts as a write. Read, Grep and Glob stay allowed;
- change COVEN_SESSION_ROLE (the lone `printenv COVEN_SESSION_ROLE` self-check stays allowed);
- start `claude` (`claude --version` and `claude -v` alone excepted) or run its package;
- run `coven approve`, `coven setup`, `coven memory adopt|init|sync`, or `coven` with a
  subcommand the guard cannot tell;
- run a remote CLI (ssh, scp, rsync, psql, curl and the like) whose host is not written out in the
  command, since a target pattern could not see it.

What it reads. Everything comes from the store's git HEAD (`git cat-file`, `git ls-tree`), never
from its working tree, so an uncommitted edit, deletion or approval has no effect. Every git call
runs without any GIT_* variable of the caller and with replace refs and grafts ignored, so neither
the environment nor a ref can change what the store holds.
- `systems/guard.json` valid at HEAD is enforced.
- Invalid at HEAD, the newest valid version among the last GUARD_HISTORY commits that changed it is
  enforced, and every target refusal says so. With no valid version only reads work, and an
  unrestricted session may also edit files, so the file can be repaired; the user fixes or reverts
  it in their own terminal.
- Absent at HEAD, a restricted session may not use Bash or MCP tools; an unrestricted one goes on.
- When git cannot read the store (no commits, a broken repository, a timeout) any call that reaches
  outside, Bash and MCP tools, is refused for every session, naming the git error.

guard.json is `{"targets": [{"system", "kind", "match", "tools"}]}`. `kind` is `production` or
`gated`; `match` are Python regexes searched in a Bash command's code text; `tools` are exact MCP
tool names. A call that reaches a target needs a live approval for that target's system, committed
as `approvals/*.json` (`{"id", "system", "commands", "tools", "expires"}`), that names the exact
command (whitespace stripped) or tool. A command that reaches two systems needs one approval each.
An approval is live when `now < expires <= now + MAX_APPROVAL`. Approvals match the whole command,
never its code text.

Code text. `shell_words.code_text` blanks data only where the command runs nothing it is given (a
commit message, a grep pattern, quoted echo text); a shell, an interpreter, `$(...)`, backticks or
anything the scanner cannot follow is read whole.

A write into a folder that holds a zone (`rm -rf ~/.claude`, `rsync -a x/ ~/`, `tar -C ~ -xf`,
`cp -r x/coven ~/.config/`, `cd ~ && cp -r x/.claude .`) is refused as a write to the zone: it can
replace the hooks as well as an edit can. A zone named only as the source of a copy, or listed, is
a read and stays allowed. The zone variables (`$HOME`, `$CLAUDE_CONFIG_DIR`, `$XDG_CONFIG_HOME`,
`$CLAUDE_PLUGIN_ROOT`, plain, quoted or braced) are read with the hook's value and with their
default, since the command may run under others.

Known limits of the restricted rules: a path built from strings or variables
(`python3 -c "...'~/cov'+'en-memory'"`, `d=coven-mem; ... ~/${d}ory`, `for d in ~/.claude; do rm
-rf $d; done`, `echo ~/.claude | xargs rm -rf`) is not read.

Limits. The whole check has a deadline of DEADLINE seconds and each git call a timeout; past either,
the call is refused. The guard is best effort against a session that follows its role: its rules are
public, and the committed store is its boundary. A process running as the same OS user can forge the
store's state, and the user's own terminal outside Claude Code is never guarded.

`decide` is built on `explain`, which also returns what `coven guard explain` shows (the hits, their
approvals, the restricted refusal and a verdict), so the two cannot disagree.
"""

import fnmatch
import importlib
import ipaddress
import logging
import os
import re
import signal
import subprocess
import sys
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Literal, NamedTuple, NoReturn, Protocol, TextIO, TypedDict, TypeGuard

# `-I` leaves this folder off sys.path, so put it there for the modules found so.
sys.path.insert(0, str(Path(__file__).resolve().parent))
# The config reads live in guard_config.py: the gate (prod_guard_gate.py) needs them without this
# module's imports. The names are the same objects here.
from guard_config import GUARD_FILE_NAME, ConfigError, config_path, off_reason_for, read_settings

# By name, not an import statement: ruff rejects one below code (E402) and suppressions are banned.
hook_support = importlib.import_module("hook_support")

HOOK = "prod-guard"
PREFIX = "coven prod-guard:"
USAGE = "usage: prod_guard.py | prod_guard.py --check <guard.json>"
CHECK_FLAG = "--check"
ROLE_VAR = "COVEN_SESSION_ROLE"
GUARD_FILE = Path(GUARD_FILE_NAME)
APPROVALS_DIR = "approvals"
# A hook still running at Claude's hook timeout lets the call through, so the whole check has a
# deadline below it (a backtracking guard.json pattern, hanging git calls), and each git call has
# its own timeout.
DEADLINE = 20.0
GIT_TIMEOUT = 5.0
TOO_SLOW = (
    f"{PREFIX} the check took too long (a guard.json pattern may backtrack, or the input is very"
    " large); blocking"
)
# Commits of guard.json searched for a valid version when HEAD's is invalid.
GUARD_HISTORY = 50
MAX_APPROVAL = timedelta(hours=12)
# Git reads replace refs and grafts as if they were history: whoever can write the store's refs
# could swap guard.json, an approval or HEAD without a commit, so git is told to ignore them.
NO_REPLACE = ("GIT_NO_REPLACE_OBJECTS", "1")
# One `git cat-file --batch` header: "<oid> <type> <size>"; a missing object is "<name> missing".
BATCH_HEADER = re.compile(r"(\S+) (\w+) (\d+)")
BATCH_MISSING = " missing"
# `git ls-tree -z` entries for regular files: approvals/<name>.json and guard.json.
REGULAR_BLOB = rb"100(?:644|755) blob ([0-9a-f]+)\t"
APPROVAL_ENTRY = re.compile(REGULAR_BLOB + APPROVALS_DIR.encode() + rb"/[^/]+\.json")
GUARD_ENTRY = re.compile(REGULAR_BLOB + re.escape(GUARD_FILE.as_posix().encode()))
MCP_PREFIX = "mcp__"
READ_TOOLS = frozenset({"Read", "Grep", "Glob"})
EDIT_TOOLS = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit"})
KINDS = ("production", "gated")
TARGET_KEYS = frozenset({"system", "kind", "match", "tools"})
APPROVAL_KEYS = frozenset({"id", "system", "commands", "tools", "expires"})
# The role a hit is explained for when the session has none: what a restricted session would meet.
ASSUMED_ROLE = "pm"
LOGGED_COMMAND_LIMIT = 200
CONFIG_ERROR = "cannot read the coven config"

# A shell word boundary: what can sit between a path and the rest of a command.
SHELL_SPLIT = re.compile(r"[\s;&|<>()`'\"=,{}]+")
# Where one simple command ends and the next begins (`$(` included through `(`).
COMMAND_SPLIT = re.compile(r"[;&|()`{}\n]")
ASSIGNMENT = re.compile(r"[A-Za-z_]\w*=")
SHELLS = frozenset({"sh", "bash", "zsh", "dash", "fish", "ksh"})
# Words that read a heredoc body as shell commands, or start a shell without naming one: a body
# fed by a line with one of them is shell text, and its globs are real.
SHELL_READERS = frozenset(
    {
        *SHELLS,
        *("source", ".", "eval", "parallel", "make", "su", "sudo", "doas", "script"),
        *("screen", "tmux"),
    }
)
CHARACTER_CLASS = re.compile(r"\[[^\]]*\]")  # a glob's `[a-z]`: one character from a set
ONE_CHARACTER_CLASS = re.compile(r"\[([^\]!^])\]")  # `[w]`: the set holds the one character
# Commands that read a heredoc body as a script or text, not as shell commands (python by
# PYTHON_NAME), or copy it to a file (cat, tee: shell_words.DATA_SINKS). Only their bodies get the
# narrower glob reading; any other reader is read strictly.
READS_BODIES_AS_TEXT = frozenset({"node", "perl", "ruby", "ssh", "cat", "tee"})
PYTHON_NAME = re.compile(r"python3?(?:\.\d+)?")
CODE_INTERPRETERS = frozenset({"node", "perl", "ruby", "php", "lua", "awk", "gawk", "mawk", "nawk"})
# What a restricted session may do with Claude Code's plugins folder in Bash: read its files and
# run its scripts (`_read_spans`). Every other use of it counts as a write.
# Commands that only read their arguments: all of them may name the folder.
READ_COMMANDS = frozenset(
    {
        *("cat", "head", "tail", "wc", "ls", "stat", "grep", "egrep", "fgrep", "diff", "cmp"),
        *("du", "realpath", "readlink", "md5sum", "sha1sum", "sha256sum", "test", "["),
        *("tac", "nl", "basename", "dirname"),
    }
)
# Commands that run a script file, and the short options each may take before it (python by
# PYTHON_NAME, with PYTHON_SCRIPT_OPTIONS): any other option may make the operand code.
SCRIPT_RUNNERS = {
    **dict.fromkeys(("bash", "sh", "zsh", "dash"), frozenset("eux")),
    "node": frozenset[str](),
}
PYTHON_SCRIPT_OPTIONS = frozenset("IBEsSuO")
SED_PRINT_OPTIONS = frozenset("nErsuz")  # sed options that change neither files nor the script
SED_PRINT_SCRIPT = re.compile(r"[\d,;pq=]+")  # line numbers and print or quit only: no w or e
FIND_ACTION_PREFIXES = ("-exec", "-ok", "-delete", "-fprint", "-fls")  # find writes or runs
# Commands a read may pipe into: they only print what they read, so no path is acted on.
PIPE_FILTERS = frozenset(
    {
        "cat",
        "head",
        "tail",
        "wc",
        "grep",
        "egrep",
        "fgrep",
        "cut",
        "tr",
        "nl",
        "tac",
        "sort",
        "uniq",
    }
)
CLASS_ESCAPE = re.compile(r"\\[wWdDsS]")  # a regex's `\w`: it can match any one name character
# Commands that run their arguments as a command, so `<wrapper> ... claude` starts claude. A
# command string (`sh -c`, `su -c`, `script -c`) is read as the option's value, quotes dropped.
WRAPPERS = frozenset(
    {
        *("exec", "env", "nohup", "command", "builtin", "sudo", "doas", "setsid", "time"),
        *("timeout", "xargs", "nice", "stdbuf", "watch", "parallel", "npx", "bunx"),
        *("script", "screen", "tmux", "flock", "systemd-run", "runuser", "su", "busybox"),
        *("chrt", "ionice", "taskset", "strace", "ltrace", "unbuffer"),
        *SHELLS,
        *("if", "then", "else", "elif", "do", "while", "until", "!", "eval", "coproc", "{", "}"),
    }
)
# Wrappers that take one positional argument before the command: timeout's duration, flock's lock
# file, chrt's priority, taskset's mask, su's and runuser's user, script's log file.
POSITIONAL_WRAPPERS = frozenset({"timeout", "flock", "chrt", "taskset", "su", "runuser", "script"})
# Wrappers whose command may follow any of their own words (`tmux new-session -d -s pm 'claude'`).
ANYWHERE_WRAPPERS = frozenset({"tmux"})
# Commands that run a program named later in their arguments (`uv run ssh`, `uvx`, `pipx run`,
# `python -m`, `poetry run`, `hatch run`, `pdm run`, `conda run -n env`, `mise exec --`, `direnv
# exec <dir>`, `nix-shell --run '...'`, `nix develop -c`, `nix shell <installable> -c`). The head
# walk reads one as a wrapper. Where one has a run word (LAUNCHER_RUN_WORDS), every word between
# the launcher and that word is skipped, whatever options it holds (`poetry -C dir run`); else
# a subcommand word may follow it (LAUNCHER_COMMANDS) and, for direnv, a directory. The
# store-writing check also reads every word after one as a program it may run.
LAUNCHER_RUN_WORDS = {
    **dict.fromkeys(("uv", "poetry", "hatch", "pdm", "conda"), frozenset({"run"})),
    "mise": frozenset({"--"}),
    "direnv": frozenset({"exec"}),
    "nix": frozenset({"-c", "--command"}),
    "nix-shell": frozenset({"--run", "--command"}),
}
LAUNCHERS = frozenset({"uvx", "pipx", *LAUNCHER_RUN_WORDS})
PYTHON = re.compile(r"python[\d.]*")
LAUNCHER_COMMANDS = frozenset({"run", "exec", "tool", "develop", "x"})
DIRECTORY_LAUNCHERS = frozenset({"direnv"})  # `direnv exec <dir> <program>`
# A redirect, which may sit before a command's name (`>out claude`), and a bare redirect operator,
# whose target is the next word (`> /dev/null claude`). `&>` reaches the head as `>`: `&` splits.
REDIRECT = re.compile(r"\d*(?:<|>|&>)")
REDIRECT_OPERATOR = re.compile(r"\d*(?:[<>]{1,3}[&|]?|&>{1,2})")
CHANGE_DIR = frozenset({"cd", "pushd"})
# Characters that continue a name: the store's name counts only as a whole token without them.
NAME_CHARS = "A-Za-z0-9_.-"
LOOKUP_OPTIONS = frozenset({"-v", "-V"})  # `command -v claude` only looks claude up
# Commands that print where claude is, and the openers of a command substitution, whose output
# can land in command position.
LOOKUPS = frozenset({"which", "command", "type", "whence", "whereis"})
SUBSTITUTIONS = ("$(", "`")
# COMMAND_SPLIT keeping the delimiters, which tell whether a pipe feeds the next command.
COMMAND_PARTS = re.compile(f"({COMMAND_SPLIT.pattern})")
# A redirect of stdin (`<file`, `<<<`, `<<EOF`), and what `sh <(...)` and `< <(...)` leave in the
# command once `(` splits it. What sources stdin as a script, and what runs its input as commands.
STDIN_REDIRECT = re.compile(r"0?<")
STDIN_FILES = ("/dev/stdin", "/dev/fd/", "/proc/self/fd/")
SOURCES = frozenset({"source", "."})
INPUT_RUNNERS = frozenset({"xargs", "parallel"})
PARALLEL = "parallel"
ENV, SPLIT_LETTER, SPLIT_STRING = "env", "S", "split-string"  # `env -S 'cmd args'`
# parallel's argument separators: the words after one are its inputs, and maybe its commands.
INPUT_SOURCES = frozenset({":::", "::::", ":::+", "::::+"})
PLACEHOLDER, PLACEHOLDER_WORD = "{}", "_"
SHELL_VALUE_OPTIONS = frozenset({"-o", "+o", "-O", "+O"})  # `bash -o pipefail`
# claude as a whole word: not `.claude`, `claude-code-guide` or `claude_x`.
CLAUDE_WORD = re.compile(r"(?<![\w.-])claude(?![\w.-])")
# `npx @anthropic-ai/claude-code`. Searched in the command without its data parts (a commit message,
# a PR body, a search pattern), so that `--body "... https://claude.com/claude-code"` passes.
CLAUDE_PACKAGE = "claude-code"
# The plain version queries start no session: the start-claude rule skips them, and only them.
CLAUDE_VERSION_QUERIES = (("claude", "--version"), ("claude", "-v"))
LINE_CONTINUATION = "\\\n"
# What separates words on one shell line. Python's str.split() also splits on characters bash keeps
# inside a word (\v, \x1c) and on a newline, which makes `--version` a second command.
LINE_BLANKS = re.compile(r"[ \t]+")
GLOB_CHARS = frozenset("*?[")
# Remote CLIs whose host a restricted session must write out in the command, and their
# names as whole words, for a text a shell reads from stdin.
REMOTE_CLIS = frozenset(
    {"ssh", "scp", "sftp", "rsync", "nc", "psql", "pg_dump", "pg_restore", "mysql", "sqlcmd"}
    | {"curl", "wget"}
)
REMOTE_WORD = re.compile(rf"(?<![\w.-])(?:{'|'.join(sorted(REMOTE_CLIS))})(?![\w.-])")
# Variables a remote CLI reads its host (or its proxy) from: one set on the line to a value that
# may expand makes a remote CLI named in the command a problem.
HOST_VARS = frozenset(
    {"PGHOST", "PGHOSTADDR", "DATABASE_URL", "SQLCMDSERVER", "MYSQL_HOST", "PGSERVICE"}
    | {"http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "all_proxy"}
    | {"ftp_proxy", "FTP_PROXY"}
    | {"SQLCMDINI", "PGSYSCONFDIR"}  # a file of commands or a service file, which name hosts
)
# Where a URL's authority ends: curl, libpq and wget end it at the first `/`, `?` or `#`.
AUTHORITY_END = re.compile(r"[/?#]")
SCHEME_MARK, DEFAULT_SCHEME = "://", "http://"  # curl and wget read `host/x` as `http://host/x`
# Commands that set the names they are given from input the guard cannot see (`read h`,
# `printf -v h`, zsh's `print -v h`), and the loop that binds its name to each of its words.
INPUT_BINDERS = frozenset({"read", "mapfile", "readarray", "getopts", "select", "printf", "print"})
LOOP = "for"
PRINTF_VARIABLE = "-v"  # `printf -vNAME` and zsh's `print -vNAME` set NAME
VARIABLE_PRINTERS = frozenset({"printf", "print"})
ARRAY_OPEN = "("  # right after `name=` or `name+=`: an array assignment
READ_ARRAY = "a"  # `read -raNAME` sets the array NAME
# After one of these first words, or a `&`, `|` or `(` before a command, an assignment may not run
# (or may run in a subshell): it can keep a name non-literal, never make it literal.
COMPOUND_WORDS = frozenset(
    {"if", "then", "else", "elif", "do", "while", "until", "for", "select", "case", "{", "!"}
)
# The keywords that open a simple command before its own first word (`then h=x`, `do ssh $h`).
LEADING_KEYWORDS = COMPOUND_WORDS - {LOOP, "select"}
# Commands that may hand a quoted word to a shell or to code of their own, which may expand a glob
# in it (python by PYTHON): shells and the words that run what they read, wrappers and launchers
# (`sudo sh -c`, `env -S`, `uv run`), ssh, watch and interpreters. A compound command's keyword
# (`do`, `if`) runs no string itself; the command after it is read on its own.
QUOTED_RUNNERS = frozenset(
    {*SHELL_READERS, *WRAPPERS, *LAUNCHERS, *INPUT_RUNNERS, *CODE_INTERPRETERS, "ssh", "watch"}
) - (LEADING_KEYWORDS | {"}"})
CONDITIONAL_MARKS = frozenset("&|(")
BINDING = re.compile(r"([A-Za-z_]\w*)(\+?)=")
VARIABLE_ONLY = re.compile(r"""['"]?\$([A-Za-z_]\w*)['"]?""")
VARIABLE_REFERENCE = re.compile(r"\$\{?([A-Za-z_]\w*)")
PLAIN_REFERENCE = re.compile(r"\$([A-Za-z_]\w*)")  # `$NAME`, which a bound value can replace
SPLITTING = frozenset(" \t\n*?[")  # in an unquoted value: it splits or globs
EXPANDING = frozenset("$`*?[]{}~")  # in a word whose value is unknown: this part may expand
# Wrappers that run a command string given after an option holding `c` (`sh -c`, `su -lc`).
COMMAND_STRING_RUNNERS = frozenset({*SHELLS, "su", "runuser", "script", "flock"})
MAX_SHELL_DEPTH = 3  # command strings inside command strings, followed this deep
# ssh's `-o` settings that name a host, and those that run a command (read as a command string).
HOST_SETTINGS = frozenset({"hostname", "proxyjump"})
COMMAND_SETTINGS = frozenset({"proxycommand", "localcommand", "knownhostscommand"})
SETTING = re.compile(r"""['"]?\s*([A-Za-z]+)\s*[=\s]\s*(.*)""", re.DOTALL)
REMOTE_SCHEMES = ("rsync://", "scp://")
ALIAS = "alias"
ALIAS_TABLE = re.compile(r"(?:BASH_ALIASES|aliases|galiases|saliases)[\[+=]")
# A heredoc and its delimiter word (not a `<<<` here-string). One with no quote in its delimiter
# expands its body, which the scanner does not read: the refusal then names the fix.
HEREDOC = re.compile(r"(?<!<)<<-?[ \t]*([^\s;&|<>()]+)")
QUOTE_MARKS = frozenset("'\"\\")
UNFOLLOWED = "cannot tell where its host comes from"
UNQUOTED_HEREDOC = (
    "cannot read an unquoted heredoc; quote the heredoc delimiter, as in <<'EOF', so the guard"
    " can read it"
)
BLANKS = " \t"
COMMAND_BLANKS = frozenset(" \t\n")  # in a word in command position: a command string

# What sits at the top of the memory store: a `*` before one of these after a cd can reach into it.
STORE_ENTRIES = (".git", "systems", "approvals", "projects", "user", "handoffs")
# What sits in coven's config folder: a `*` before one of these after a cd can reach into it.
CONFIG_ENTRIES = ("config.json", "owned.json")
# Claude Code's folder, the settings files it reads there and in every project, and its plugins.
CLAUDE_FOLDER = ".claude"
SETTINGS_FILES = ("settings.json", "settings.local.json")
PLUGINS_FOLDER = "plugins"
PLUGIN_ENTRIES = ("cache", "marketplaces", "data", "installed_plugins.json")
# Variables that can name a zone, and what a session may have set them to instead of the hook's
# own value: the default of each (the plugin root is a folder under the plugins folder).
ZONE_VARIABLES = ("HOME", "CLAUDE_CONFIG_DIR", "CLAUDE_PLUGIN_ROOT", "XDG_CONFIG_HOME")
ZONE_VARIABLE = re.compile(
    rf"\$(?:\{{({'|'.join(ZONE_VARIABLES)})(?::?[-=?+]([^}}]*))?\}}|({'|'.join(ZONE_VARIABLES)})(?!\w))"
)
PLUGIN_ROOT_DEFAULT = ("cache", "plugin")
# Commands whose every operand, or whose last one (the destination), they change or replace.
CHANGES_OPERANDS = frozenset(
    {"rm", "rmdir", "unlink", "mv", "ln", "chmod", "chown", "chgrp", "truncate", "shred", "tee"}
)
CHANGES_DESTINATION = frozenset({"cp", "install", "rsync"})
TARGET_DIRECTORY = re.compile(r"(?:-t|--target-directory=?)(.*)")
OUTPUT_REDIRECT = re.compile(r"\d*>>?\|?(.*)")
FIND_ACTIONS = frozenset(
    {"-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprintf", "-fls"}
)
# Fields whose value is a path in the built-in edit tools.
PATH_KEYS = frozenset({"file_path", "notebook_path", "path"})

# The coven subcommands that write the memory store or coven's config, each as the words the user
# types. `memory` takes a second word: only `adopt`, `init` and `sync` write.
STORE_WRITING_SUBCOMMANDS = frozenset(
    {"approve", "setup", "memory adopt", "memory init", "memory sync"}
)
NESTED_COMMANDS = frozenset(name.split()[0] for name in STORE_WRITING_SUBCOMMANDS if " " in name)
# The coven CLI as a word, once a path, `-m` and a `__main__` are dropped: a uvx or pipx spec
# (`coven@latest`, `coven==1.2`, `coven[x]`) names it too.
COVEN_PROGRAM = re.compile(r"coven(?:[\[@=<>~!].*)?")
MAIN_SUFFIXES = ("/__main__.py", ".__main__", "/")
MODULE_LETTER = "m"  # python -m, alone or inside a short option cluster (`-Imcoven`)
# `${NAME}`, which COMMAND_SPLIT would cut at its braces, read as a word holding `$`.
BRACED_PARAMETER = re.compile(r"\$\{[^{}]*\}")
BRACED_STAND_IN = "$_"
ANSI_C_QUOTE = "$'"  # `$'\x73etup'`: escapes build the word
BRACE_EXPANSION = re.compile(r"\{(?=[^\s}])")  # `{setup,}`, not a `{ ...; }` group
# Delimiters after which a simple command goes on: its first words came from a substitution or
# a brace word (`$(echo coven) setup`, `` `which coven` setup ``).
CONTINUED = frozenset(")`}")
BACKTICK = "`"
UNTOLD_SUBCOMMAND = (
    "with a subcommand it cannot tell (it may be approve, setup or memory adopt|init|sync)"
)


class PayloadError(ValueError):
    """The hook payload does not have the shape of a tool call."""


class StoreError(Exception):
    """git cannot read the memory store's committed files (no commits, failing, too slow, odd
    output)."""


class GuardError(Exception):
    """guard.json is unusable: not JSON, the wrong shape, a bad regex or kind, or unreadable."""


class DeadlineError(Exception):
    """The check ran past DEADLINE."""


def _past_deadline(_signum: int, _frame: object) -> NoReturn:
    raise DeadlineError


class Target(TypedDict):
    system: str
    kind: str
    match: list[str]
    tools: list[str]


class Enforced(NamedTuple):
    targets: list[Target] | None  # None: no guard.json committed at HEAD
    invalid_head: str | None = None  # why HEAD's guard.json is invalid, when an older one is used


@dataclass(frozen=True)
class Approval:
    id: str
    system: str
    commands: list[str]
    tools: list[str]
    expires: datetime


class GuardConfig(NamedTuple):
    store: Path
    latch: bool  # `"guard": "required"`: the store held a guard.json once, so it must keep one


class Bash(NamedTuple):
    command: str
    # The command with its data blanked (shell_words.code_text): what the target patterns read.
    code: str


@dataclass(frozen=True)
class Call:
    tool: str
    bash: Bash | None  # None for every tool but Bash
    tool_input: Mapping[str, object]
    cwd: str  # the session's working directory, or this process's when the payload has none


@dataclass(frozen=True)
class Hit:
    """One way a call reaches a target, and the live approval that grants it (None: it needs one)."""

    system: str
    kind: str  # the target's kind: production or gated
    via: Literal["command", "tool"]
    trigger: str  # the first pattern that matched, or the tool's name
    approval: Approval | None


type Verdict = Literal["allowed", "refused for a PM", "refused for every session"]


@dataclass(frozen=True)
class Explanation:
    """What the guard decides for one call, and why."""

    call: Call
    hits: list[Hit]
    restricted: str | None  # the refusal a restricted session meets, whatever this session is
    note: str  # said after a target refusal: an older guard.json is enforced
    message: str | None  # why this session's call is refused; None: allowed
    verdict: Verdict
    unreadable: str | None = None  # why the store or config could not be read
    system: str | None = None  # what the refusal of this session is about: see Refused


# --- config ---


def config_file() -> Path:
    """`~/.config/coven/config.json`: where `coven.paths.config_dir()` and memory-context look."""
    return Path(config_path())


def read_config() -> GuardConfig:
    """The store and latch of coven's config (guard_config.read_settings, which the gate reads
    too). With no config file that is the default store and no latch, so deleting the file cannot
    switch off a store that holds a guard.json. ConfigError when the file cannot be read or holds
    something else than expected."""
    store, latch = read_settings()
    return GuardConfig(Path(store), latch)


def off_reason(config: GuardConfig) -> str | None:
    """Why the guard is off for this config, or None when it is on. One stat or two, no git."""
    return off_reason_for(str(config.store), config.latch)


# --- guard.json ---


def _string_list(value: object) -> TypeGuard[list[str]]:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _target(item: object, where: str) -> Target:
    if not isinstance(item, dict):
        raise GuardError(f"{where} is not an object")
    unknown = sorted(set(item) - TARGET_KEYS)
    if unknown:
        raise GuardError(f"{where} has unknown key(s) {', '.join(unknown)}")
    system, kind = item.get("system"), item.get("kind")
    if not isinstance(system, str) or not system:
        raise GuardError(f"{where} needs a non-empty 'system'")
    if not isinstance(kind, str) or kind not in KINDS:
        raise GuardError(f"{where} 'kind' must be one of {', '.join(KINDS)}")
    match, tools = item.get("match", []), item.get("tools", [])
    if not _string_list(match) or not _string_list(tools):
        raise GuardError(f"{where} 'match' and 'tools' must be lists of strings")
    for pattern in match:
        try:
            re.compile(pattern)
        except re.error as exc:
            raise GuardError(f"{where} has a bad regex {pattern!r}: {exc}") from None
    return Target(system=system, kind=kind, match=list(match), tools=list(tools))


def _parse_targets(text: str, path: Path) -> list[Target]:
    try:
        raw = hook_support.loads(text)
    except ValueError as exc:
        raise GuardError(f"{path}: not valid JSON: {exc}") from None
    if not isinstance(raw, dict) or set(raw) != {"targets"} or not isinstance(raw["targets"], list):
        raise GuardError(f'{path}: must be an object {{"targets": [...]}}')
    return [_target(item, f"{path}: target {index}") for index, item in enumerate(raw["targets"])]


def check_file(path: Path) -> str:
    """`ok: N targets` for a guard.json file that follows the rules the committed one follows;
    GuardError otherwise."""
    if path.is_symlink():
        raise GuardError(f"{path}: cannot read: not a regular file")
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise GuardError(f"{path}: cannot read: {exc}") from None
    return f"ok: {len(_parse_targets(text, path))} targets"


# --- the store, read through git at HEAD ---


def _git(store: Path, *args: str, stdin: bytes = b"") -> bytes:
    """git's stdout, run on the store's own repository (never one found above it or named by the
    environment), with every GIT_* variable of the caller dropped and replace refs ignored.
    StoreError when git is missing, fails or times out."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env[NO_REPLACE[0]] = NO_REPLACE[1]
    try:
        proc = subprocess.run(
            ["git", f"--git-dir={store / '.git'}", *args],
            input=stdin,
            capture_output=True,
            timeout=GIT_TIMEOUT,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise StoreError(f"git timed out after {GIT_TIMEOUT:g} s") from None
    except OSError as exc:
        raise StoreError(f"cannot run git: {exc}") from None
    if proc.returncode != 0:
        lines = proc.stderr.decode("utf-8", errors="replace").strip().splitlines()
        raise StoreError(lines[-1] if lines else f"git exited with {proc.returncode}")
    return proc.stdout


def _head(store: Path) -> str:
    """The commit HEAD points to; StoreError in a store without a folder or without commits."""
    if not store.is_dir():
        raise StoreError(f"the folder {store} does not exist")
    out = _git(store, "cat-file", "--batch-check", stdin=b"HEAD\n")
    header = out.decode("utf-8", errors="replace").strip()
    if header == "HEAD" + BATCH_MISSING:
        raise StoreError("the store has no commits")
    match = BATCH_HEADER.fullmatch(header)
    if match is None or match[2] != "commit":
        raise StoreError(f"unexpected git cat-file output {header[:80]!r}")
    return match[1]


def _objects(store: Path, names: Sequence[str]) -> list[bytes]:
    """The content of each git object name; StoreError when one does not exist."""
    out = _git(store, "cat-file", "--batch", stdin="".join(f"{name}\n" for name in names).encode())
    contents: list[bytes] = []
    start = 0
    for _name in names:
        end = out.find(b"\n", start)
        header = out[start:end].decode("utf-8", errors="replace")
        match = BATCH_HEADER.fullmatch(header)
        if match is None:
            raise StoreError(f"unexpected git cat-file output {header[:80]!r}")
        size = int(match[3])
        contents.append(out[end + 1 : end + 1 + size])
        start = end + 1 + size + 1  # the content ends with a newline
    return contents


def _guard_text_at(store: Path, commit: str) -> str | None:
    """guard.json as committed in `commit`; None when that commit has none."""
    listing = _git(store, "ls-tree", "-z", commit, "--", GUARD_FILE.as_posix())
    if not listing:
        return None
    path = store / GUARD_FILE
    match = GUARD_ENTRY.fullmatch(listing.rstrip(b"\0"))
    if match is None:
        raise GuardError(f"{path}: cannot read: not a regular file in HEAD")
    (content,) = _objects(store, [match[1].decode()])
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise GuardError(f"{path}: cannot read: {exc}") from None


def _targets_at(store: Path, commit: str) -> list[Target] | None:
    """The targets committed in `commit`; None without guard.json, GuardError if it is unusable."""
    text = _guard_text_at(store, commit)
    return None if text is None else _parse_targets(text, store / GUARD_FILE)


def _last_valid_targets(store: Path, head: str) -> list[Target] | None:
    """The newest valid guard.json among the last GUARD_HISTORY commits that changed it."""
    log = _git(
        store,
        "log",
        f"--max-count={GUARD_HISTORY}",
        "--format=%H",
        head,
        "--",
        GUARD_FILE.as_posix(),
    )
    for commit in log.decode().split():
        try:
            targets = _targets_at(store, commit)
        except GuardError:
            continue
        if targets is not None:
            return targets
    return None


def load_targets(store: Path) -> Enforced:
    """The targets enforced at the store's HEAD: HEAD's own, or the last valid version's (with
    the reason HEAD's is invalid). `targets` is None without a committed guard.json. StoreError
    when git cannot read the store; GuardError when HEAD's guard.json is invalid and no earlier
    version is valid."""
    head = _head(store)
    try:
        return Enforced(_targets_at(store, head))
    except GuardError as exc:
        fallback = _last_valid_targets(store, head)
        if fallback is None:
            raise
        return Enforced(fallback, str(exc))


def _expiry(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        expires = datetime.fromisoformat(value)
    except ValueError:
        return None
    return expires if expires.utcoffset() is not None else None


def _approval(raw: object) -> Approval | None:
    """The approval record, or None when it does not follow the schema exactly."""
    if not isinstance(raw, dict) or set(raw) != APPROVAL_KEYS:
        return None
    ident, system, commands, tools = raw["id"], raw["system"], raw["commands"], raw["tools"]
    expires = _expiry(raw["expires"])
    if not isinstance(ident, str) or not isinstance(system, str) or expires is None:
        return None
    if _string_list(commands) and _string_list(tools):
        return Approval(ident, system, commands, tools, expires)
    return None


def _approval_records(store: Path) -> Iterator[Approval]:
    listing = _git(store, "ls-tree", "-z", "HEAD", "--", f"{APPROVALS_DIR}/")
    entries = (APPROVAL_ENTRY.fullmatch(entry) for entry in listing.split(b"\0"))
    oids = [match[1].decode() for match in entries if match]
    if not oids:
        return
    for content in _objects(store, oids):
        try:
            approval = _approval(hook_support.loads(content.decode("utf-8")))
        except ValueError:
            continue  # an unreadable approval approves nothing
        if approval is not None:
            yield approval


def approvals(store: Path) -> list[Approval]:
    """The approvals committed at HEAD as regular files approvals/*.json that follow the schema,
    live or not; StoreError when git cannot read them."""
    return list(_approval_records(store))


def is_live(approval: Approval, now: datetime) -> bool:
    """Whether an approval is in force: not expired, and ending at most MAX_APPROVAL from now."""
    return now < approval.expires <= now + MAX_APPROVAL


# --- the payload ---


def _call(payload: Mapping[str, object]) -> Call:
    tool, tool_input = payload.get("tool_name"), payload.get("tool_input")
    if not isinstance(tool, str) or not tool:
        raise PayloadError("no tool_name")
    if not isinstance(tool_input, dict):
        raise PayloadError("tool_input is not an object")
    cwd = payload.get("cwd")
    where = cwd if isinstance(cwd, str) and cwd else os.getcwd()
    if tool != "Bash":
        return Call(tool, None, tool_input, where)
    command = tool_input.get("command")
    if not isinstance(command, str):
        raise PayloadError("a Bash call without a command")
    # The PM's read-only self-check is blanked as a lone command.
    code: str = _shell_words().code_text(command, lone=("printenv", ROLE_VAR))
    return Call(tool, Bash(command, code), tool_input, where)


def _reaches_out(call: Call) -> bool:
    """Bash and MCP tools: the calls that can reach a system the guard cannot see without the store."""
    return call.bash is not None or call.tool.startswith(MCP_PREFIX)


# --- the rules for restricted sessions ---


class Refused(NamedTuple):
    """A refusal and what it is about: the target system, or the short stable name of the rule
    (`role-change`, `store-write`, `unreadable-store`...). The hook log records the name."""

    message: str
    system: str


type Rule = Callable[[Call, Path, str, datetime], Refused | None]


def _shell_words() -> ModuleType:
    """The shell_words scanner next to this file, loaded on first use inside the guard's catch-all,
    so a missing or broken module blocks."""
    module: ModuleType = hook_support.sibling("shell_words")
    return module


def _without_data(command: str) -> str:
    """shell_words.without_data: the command without its narrow data parts."""
    result: str = _shell_words().without_data(command)
    return result


def _without_patterns(command: str) -> str:
    """shell_words.without_patterns: the command with only its grep-like pattern words blanked."""
    result: str = _shell_words().without_patterns(command)
    return result


def _strings(value: object) -> Iterator[str]:
    """Every string value in an MCP tool input, at any depth and under any key: an MCP tool may
    name its path field anything (`destination`), and a path may hold spaces."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _resolve(text: str, cwd: str) -> str | None:
    """`text` as a real path (~ and $VARS expanded, relative to cwd); None if it cannot be one."""
    if "\0" in text:
        return None
    return os.path.realpath(os.path.join(cwd, os.path.expandvars(os.path.expanduser(text))))


def _inside(path: str | None, root: str) -> bool:
    return path is not None and (path == root or path.startswith(root.rstrip(os.sep) + os.sep))


def _unquoted(text: str) -> str:
    """text with quotes and backslashes dropped, as the shell drops them (`cl''aude` is claude):
    first every backslash-newline (a line continuation), then a `$` right before a quote (`$'x'`,
    `$"x"`), then the quotes and backslashes themselves."""
    joined = text.replace(LINE_CONTINUATION, "").replace("$'", "'").replace('$"', '"')
    return joined.replace("'", "").replace('"', "").replace("\\", "")


def _basename(word: str) -> str:
    return word.rsplit("/", 1)[-1]


@dataclass(frozen=True)
class _Due:
    """What the next word in a simple command's head may be, other than its command."""

    value: bool = False  # the word before is an option: this one may be its value
    positional: bool = False  # a wrapper's positional argument is due (timeout's duration)
    target: bool = False  # the word before is a bare redirect operator: this one is its target
    launched: bool = False  # a launcher came before: its subcommand word (`run`) may follow


def _launches(name: str) -> bool:
    """Whether a command named `name` runs a program named later in its arguments."""
    return name in LAUNCHERS or bool(PYTHON.fullmatch(name))


def _due_after(due: _Due, word: str) -> _Due | None:
    """What may follow `word` in a simple command's head; None when `word` is a command other
    than a wrapper, whose arguments are data."""
    name = _basename(word)
    if due.target:
        return replace(due, target=False)
    if due.launched and word in LAUNCHER_COMMANDS:  # `uv run`, `mise exec`, `nix develop`
        return due
    if name in WRAPPERS:
        return _Due(positional=name in POSITIONAL_WRAPPERS)
    if _launches(name):
        return _Due(positional=name in DIRECTORY_LAUNCHERS, launched=True)
    if REDIRECT.match(word):  # `>out`, `2>&1`, or a bare `>` before its target
        return replace(due, target=bool(REDIRECT_OPERATOR.fullmatch(word)))
    if word.startswith("-"):
        return replace(due, value=True)
    if due.value:
        return replace(due, value=False)
    if due.positional:
        return replace(due, positional=False)
    if ASSIGNMENT.match(_unquoted(word)) or word[0].isdigit():  # env takes `"A=1"` too
        return due
    return None


class _Head(NamedTuple):
    indices: list[int]
    # The words ran out with only wrappers, options and values: the command comes from elsewhere
    # (`xargs env` runs its input).
    open: bool


def _run_word(words: Sequence[str], index: int, unfound: set[str]) -> int | None:
    """The index of the run word (`run`, `exec`, `--`, `-c`) of a launcher at `index`, or None.
    A launcher with no run word ahead joins `unfound`, so no later one of its kind searches
    again and the walk stays linear."""
    name = _basename(words[index])
    if name not in LAUNCHER_RUN_WORDS or name in unfound:
        return None
    found = next(
        (i for i in range(index + 1, len(words)) if words[i] in LAUNCHER_RUN_WORDS[name]), None
    )
    if found is None:
        unfound.add(name)
    return found


def _command_head(words: Sequence[str]) -> _Head:
    """The indices of a simple command's head: its wrappers with their options, values,
    assignments and redirects, up to and including its first command other than a wrapper.

    Linear on purpose: a backtracking regex could outlast the hook timeout, and a hook that does
    not exit 2 lets the call through."""
    head: list[int] = []
    due = _Due()
    skip_to = -1  # the run word of a launcher before: the walk goes on after it
    unfound: set[str] = set()  # launchers with no run word ahead
    for index, word in enumerate(words):
        if index <= skip_to:
            head.append(index)  # between a launcher and its run word: its options and values
            continue
        if index and _basename(words[index - 1]) == "command" and word in LOOKUP_OPTIONS:
            return _Head(head, open=False)  # `command -v claude` only looks claude up
        head.append(index)
        if _basename(word) in ANYWHERE_WRAPPERS:
            return _Head([*head, *range(index + 1, len(words))], open=False)
        run_word = _run_word(words, index, unfound)
        if run_word is not None:
            skip_to = run_word
            due = _Due(positional=_basename(word) in DIRECTORY_LAUNCHERS, launched=True)
            continue
        after = _due_after(due, word)
        if after is None:
            return _Head(head, open=False)
        due = after
    return _Head(head, open=True)


def _command_candidates(words: Sequence[str]) -> list[int]:
    """The indices, in order, of the words in a simple command's head that the shell may run as
    its command: every head word but a wrapper's own name. That is each word after an option
    (which may be the option's value), the word where the walk stops, and, stricter than those
    two, a wrapper's positional argument, an assignment, a redirect and its target, and every word
    after `tmux`."""
    head = _command_head(words).indices
    return [index for index in head if _basename(words[index]) not in WRAPPERS]


def _split_env_strings(words: Sequence[str]) -> list[str]:
    """`words` with a `-S` or `--split-string=` glued to a word after env dropped, so the command
    string it starts reads as words (`env -S'claude -p'`, quotes dropped, gives `claude -p`)."""
    split: list[str] = []
    after_env = False
    for word in words:
        rest = _after_split_option(word) if after_env else None
        split.append(rest or word)
        after_env = after_env or _basename(word) == ENV
    return split


def _runs_claude(segment: str) -> bool:
    """Whether one simple command runs claude, maybe through wrappers such as `sudo -u me`.
    Quotes and backslashes are dropped as the shell drops them."""
    words = _split_env_strings(_unquoted(segment).split())
    return any(_basename(words[index]) == "claude" for index in _command_candidates(words))


def _fed_segments(text: str) -> Iterator[tuple[str, bool]]:
    """Each simple command in `text`, and whether the one before feeds its stdin: a pipe (`|`,
    `|&`, also into a subshell, `| (sh)`), not `||`, or an output process substitution
    (`> >(sh)`). An empty `{}` (xargs' and find's placeholder) stays a word: it groups nothing."""
    parts = COMMAND_PARTS.split(text.replace(PLACEHOLDER, PLACEHOLDER_WORD))
    run, before = "", ""
    for index, part in enumerate(parts):
        if index % 2:
            run += part  # the delimiters between two simple commands
        elif part.strip():
            piped = run[:1] == "|" and run[:2] != "||"
            yield part, piped or (run.endswith("(") and before.rstrip().endswith(">"))
            run, before = "", part


def _shell_reads_stdin(rest: Sequence[str]) -> bool:
    """Whether a shell given the words `rest` reads its program from stdin: a stdin redirect,
    `-` or `-s` before a `-c` or a script argument, a script that is stdin (`/dev/stdin`,
    `/dev/fd/0`), or neither of those."""
    skip = False
    for word in rest:
        if skip:
            skip = False
        elif word == "-" or STDIN_REDIRECT.match(word):
            return True
        elif REDIRECT.match(word) or word in SHELL_VALUE_OPTIONS:
            skip = word in SHELL_VALUE_OPTIONS or bool(REDIRECT_OPERATOR.fullmatch(word))
        elif word[0] not in "-+":
            return word.startswith(STDIN_FILES)  # a script, which may be stdin itself
        elif word[1:2] != "-" and "s" in word:
            return True
        elif word[1:2] != "-" and "c" in word:
            return False  # a command string
    return True


def _feeds_shell(rest: Sequence[str], fed: bool) -> bool:
    """Whether a shell given the words `rest`, and maybe fed by a pipe, runs what stdin holds."""
    given = fed or any(STDIN_REDIRECT.match(word) for word in rest)
    return given and _shell_reads_stdin(rest)


def _sources_stdin(rest: Sequence[str]) -> bool:
    """Whether `source` or `.` given the words `rest` reads stdin or a process substitution."""
    return bool(rest) and (rest[0].startswith(STDIN_FILES) or bool(STDIN_REDIRECT.match(rest[0])))


def _runs_its_input(names: Sequence[str], head: _Head) -> bool:
    """Whether xargs or parallel runs its input as a command: only wrappers, options and values
    follow it (`xargs env`, `xargs -I{} sh -c {}`), or it is a bare `parallel`."""
    runner = next((index for index, name in enumerate(names) if name in INPUT_RUNNERS), None)
    if runner is None or not head.open:
        return False
    return names[runner] == PARALLEL or any(name in WRAPPERS for name in names[runner + 1 :])


def _runs_stdin(segment: str, fed: bool) -> bool:
    """Whether one simple command runs a program it reads from stdin or its input: a shell fed
    by a pipe or a stdin redirect, `source` of stdin, or xargs and parallel running their input.
    Only the first shell or `source` in the head counts: it is the one given the words after."""
    words = _unquoted(segment).split()
    head = _command_head(words)
    names = [_basename(words[index]) for index in head.indices]
    shell = next((i for i, name in zip(head.indices, names, strict=True) if name in SHELLS), None)
    if shell is not None and _feeds_shell(words[shell + 1 :], fed):
        return True
    if names and names[-1] in SOURCES and _sources_stdin(words[head.indices[-1] + 1 :]):
        return True
    return _runs_its_input(names, head)


def _names_claude(text: str) -> bool:
    """Whether claude is a whole word in `text`, quotes dropped or not."""
    return bool(CLAUDE_WORD.search(text) or CLAUDE_WORD.search(_unquoted(text)))


def _looks_up_claude(segment: str) -> bool:
    """Whether one simple command looks claude up (`which claude`, `command -v claude`)."""
    names = [_basename(word) for word in _unquoted(segment).split()]
    lookup = next((index for index, name in enumerate(names) if name in LOOKUPS), None)
    return lookup is not None and "claude" in names[lookup + 1 :]


def _view_starts_claude(text: str) -> bool:
    """Whether a simple command in `text` runs claude; or a lookup of claude sits next to a
    substitution, which can put its path in command position (`$(which claude) -p`, or through a
    variable), while a bare lookup passes; or claude is a word in a text where a shell runs what
    stdin holds (`echo 'claude -p' | sh`)."""
    segments = COMMAND_SPLIT.split(text)
    if any(_runs_claude(segment) for segment in segments):
        return True
    substitutes = any(opener in text for opener in SUBSTITUTIONS)
    if substitutes and any(_looks_up_claude(segment) for segment in segments):
        return True
    return _names_claude(text) and any(_runs_stdin(seg, fed) for seg, fed in _fed_segments(text))


def _starts_claude(command: str) -> bool:
    """Whether `command` starts claude, read with its line continuations kept and joined: joined,
    `cla\\` newline `ude` is claude; kept, a newline after an escaped backslash (`echo a\\\\`
    newline `claude`) or after one in a comment still ends a command."""
    views = (command, command.replace(LINE_CONTINUATION, ""))
    return any(_view_starts_claude(view) for view in views)


def _is_version_query(command: str) -> bool:
    """Whether the whole command is `claude --version` or `claude -v`, on one line."""
    words = tuple(command.split())
    one_line = tuple(LINE_BLANKS.split(command.strip(" \t\n")))
    return words in CLAUDE_VERSION_QUERIES and one_line == words


class _Written(Protocol):
    """A word or part of one: the text as written, and its value (None when it may expand)."""

    @property
    def raw(self) -> str: ...

    @property
    def value(self) -> str | None: ...


class _ShellWord(_Written, Protocol):
    """A shell_words.Word, at text[start:end]."""

    @property
    def start(self) -> int: ...

    @property
    def end(self) -> int: ...


class _SimpleCommand(Protocol):
    """A shell_words.Command: its words, and whether a pipe joins it to another command."""

    @property
    def words(self) -> list[_ShellWord]: ...

    @property
    def in_pipeline(self) -> bool: ...


class _Bodies(Protocol):
    """A shell_words.HeredocBodies: the line with its bodies blanked, the bodies, their readers."""

    @property
    def rest(self) -> str: ...

    @property
    def bodies(self) -> list[str]: ...

    @property
    def readers(self) -> list[str | None]: ...


class _HostProblem(NamedTuple):
    cli: str
    why: str


def _names(text: str) -> frozenset[str]:
    """Option names written as one blank-separated text (`"h --host"`)."""
    return frozenset(text.split())


@dataclass(frozen=True)
class _CliSyntax:
    """How one remote CLI's arguments are written, and which of them name its host. An option is
    named by its letter (`J`) or by its long name (`--host`)."""

    values: str  # short options that take a value (glued or the next word)
    hosts: frozenset[str] = frozenset()  # options whose value names a host (ssh's -J)
    long_values: frozenset[str] = frozenset()  # other long options that take the next word
    first_host: bool = False  # the first positional is the host
    urls: bool = False  # every positional is a URL, read as http:// when it has no scheme
    conninfo: bool = False  # the host positional is a libpq URL or conninfo: judged whole
    scripts: frozenset[str] = frozenset()  # options whose value holds commands the CLI runs
    connect: re.Pattern[str] | None = None  # a meta-command in them that picks a new host
    remote_positionals: bool = False  # each positional in remote form (`host:path`) names a host
    no_host: str = ""  # flags after which no positional is a host (nc -l, -U)
    settings: bool = False  # `-o` takes ssh settings, some of which name a host
    commands: str = ""  # short options whose value is a command string (sftp's -D)
    glued: str = ""  # short options whose value, if any, is glued (mysql's -pPASSWORD)
    files: frozenset[str] = frozenset()  # options that read hosts the command does not show
    globs: str = ""  # characters the CLI expands in a host it is given (curl's {a,b} and [1-3])
    globoff: frozenset[str] = frozenset()  # options that turn those globs off (curl's -g)


# Each tool's own short options: psql's -t and pg_dump's -c and -v take no value, so a table
# shared between them would read the host after them as the option's value.
PG_HOSTS = _names("h d --host --dbname")
PG_LONG = _names("--file --format --jobs --port --role --schema --exclude-schema --username")
PG_TABLES = _names("--table --exclude-table --superuser --section --snapshot --filter")
CURL_HOSTS = _names(
    "x --url --proxy --connect-to --resolve --preproxy --proxy1.0 --socks4 --socks4a --socks5"
    " --socks5-hostname --doh-url --dns-servers"
)
CURL_LONG = _names(
    "--data --data-ascii --data-binary --data-raw --data-urlencode --header --request --user"
    " --output --max-time --connect-timeout --retry --cacert --cert --key --form --user-agent"
    " --referer --cookie --cookie-jar --write-out --upload-file --range --json --unix-socket"
    " --oauth2-bearer --proxy-user --aws-sigv4 --limit-rate --max-filesize --max-redirs"
    " --retry-delay --retry-max-time --output-dir --dump-header --trace --trace-ascii --stderr"
    " --url-query --form-string --cert-type --key-type --capath --pass --proto --interface"
    " --netrc-file --time-cond --speed-limit --speed-time --continue-at --quote --ciphers"
    " --proxy-header --request-target"
)
WGET_LONG = _names(
    "--header --user --password --post-data --post-file --output-document --output-file"
    " --directory-prefix --tries --timeout --wait --user-agent --referer --load-cookies"
    " --save-cookies --http-user --http-password --body-data --method --limit-rate --level"
    " --accept --reject --domains --ca-certificate --certificate --private-key --append-output"
)
CLI_SYNTAX = {
    "ssh": _CliSyntax(
        "BbcDEeFIiJLlmOoPpQRSWw", hosts=_names("J W"), first_host=True, settings=True
    ),
    "scp": _CliSyntax("cDFiJloPSX", hosts=_names("J"), remote_positionals=True, settings=True),
    "rsync": _CliSyntax("ef", long_values=_names("--rsh --rsync-path"), remote_positionals=True),
    "sftp": _CliSyntax(
        "BbcDFiJloPRSsX", hosts=_names("J"), first_host=True, settings=True, commands="D"
    ),
    "nc": _CliSyntax("ceIiMmOPpqsTVwXx", hosts=_names("x"), first_host=True, no_host="lU"),
    "psql": _CliSyntax(
        "cdfFhLoPpRTUv",
        hosts=PG_HOSTS,
        long_values=PG_LONG
        | _names("--command --field-separator --log-file --output --pset --record-separator")
        | _names("--set --table-attr --variable"),
        first_host=True,
        conninfo=True,  # `?host=`, and a `?` or `#` before an `@`, move the host
        scripts=_names("c --command v --set --variable"),  # `-v h=$H -c '\c - - :h'`
        connect=re.compile(r"\\c(?:onnect)?(?![A-Za-z])"),  # not \copy or \conninfo
    ),
    "pg_dump": _CliSyntax(
        "dEefFhjnNpStTUZ",
        hosts=PG_HOSTS,
        long_values=PG_LONG
        | PG_TABLES
        | _names("--compress --encoding --extension --exclude-table-data --lock-wait-timeout"),
        first_host=True,
        conninfo=True,  # `?host=`, and a `?` or `#` before an `@`, move the host
    ),
    "pg_restore": _CliSyntax(  # its positional is the archive file
        "dfFhIjLnNpPStTU",
        hosts=PG_HOSTS,
        long_values=PG_LONG | PG_TABLES | _names("--function --index --trigger --use-list"),
    ),
    "mysql": _CliSyntax(
        "DehPSu",
        hosts=_names("h --host --dns-srv-name"),
        long_values=_names("--database --execute --port --socket --user --protocol"),
        remote_positionals=True,  # `mysql://user@host/db`
        glued="p",
        scripts=_names("e --execute"),
        connect=re.compile(r"(?i)\bconnect\b|\\r(?![A-Za-z])"),  # `connect db host`, or `\r`
    ),
    "sqlcmd": _CliSyntax(
        "dioPQqSU",
        hosts=_names("S --server"),
        long_values=_names("--query --initial-query"),
        scripts=_names("Q q --query --initial-query"),
        connect=re.compile(r"(?i):connect\b"),
    ),
    "curl": _CliSyntax(
        "AbcCdDeEFHKmorTuUwXxYyz",
        hosts=CURL_HOSTS,
        long_values=CURL_LONG,
        urls=True,
        files=_names("K --config --variable"),  # --variable fills the URL of --expand-url
        globs="{[",
        globoff=_names("g --globoff"),
    ),
    "wget": _CliSyntax(
        "aABDeIilOoPQRtTUwX",
        hosts=_names("e B --execute --base"),
        long_values=WGET_LONG,
        urls=True,
        files=_names("i --input-file --input-metalink --config"),
    ),
}


class _Piece(NamedTuple):
    """A word, or the part of one that holds a host: its value (None when it may expand), and
    its text as written."""

    value: str | None
    raw: str


class _CliArgs(NamedTuple):
    flags: str  # short flags that take no value
    values: list[tuple[str, _Piece]]  # (option letter or long name, value)
    positionals: list[_ShellWord]
    expanding: list[str]  # option words whose option name may expand into any option
    longs: list[str]  # long option names as written (`--globoff`)


class _Bindings:
    """What each variable name holds at this point of the line: True for a literal, False for
    anything else (absent: unbound, so it may come from the environment)."""

    def __init__(self) -> None:
        self.literal: dict[str, bool] = {}
        self.values: dict[str, str] = {}  # the one literal value a name surely holds here
        self.conditional = False  # an assignment from here on may not run, or not in this shell

    def bind(
        self, name: str, literal: bool, *, conditional: bool, value: str | None = None
    ) -> None:
        """A conditional binding keeps a literal name literal only when its new value is literal
        too, and never makes a name literal. Only an unconditional binding of a known value
        gives the name one value to read it through."""
        kept = self.literal.get(name, False) if conditional else True
        self.literal[name] = literal and kept
        if self.literal[name] and not conditional and value is not None:
            self.values[name] = value
        else:
            self.values.pop(name, None)

    def substitute(self, inner: str, *, quoted: bool) -> str | None:
        """`inner` (a word's text, or what its double quotes hold) with each `$NAME` replaced by
        the value NAME surely holds; None when a name has no such value, something else in it
        may expand, or, unquoted, a value would split or glob."""
        names = PLAIN_REFERENCE.findall(inner)
        if not names or "${" in inner or any(name not in self.values for name in names):
            return None
        if EXPANDING.intersection(PLAIN_REFERENCE.sub("", inner)):
            return None
        if not quoted and any(SPLITTING.intersection(self.values[name]) for name in names):
            return None
        return PLAIN_REFERENCE.sub(lambda match: self.values[match[1]], inner)

    def holds_literal(self, raw: str) -> bool:
        """Whether `raw` is exactly `$NAME` or `"$NAME"` with NAME bound to a literal."""
        match = VARIABLE_ONLY.fullmatch(raw)
        return match is not None and self.literal.get(match[1], False)

    def from_input(self, raw: str) -> bool:
        """Whether `raw` names a variable bound to something other than a literal."""
        return any(self.literal.get(name) is False for name in VARIABLE_REFERENCE.findall(raw))


def _text(word: _ShellWord) -> str:
    """A word as the walk reads it: its value, or as written when that may expand or is empty."""
    return word.value or word.raw


def _is_literal(piece: _Piece, bindings: _Bindings) -> bool:
    if piece.value is not None or bindings.holds_literal(piece.raw):
        return True
    return not EXPANDING.intersection(piece.raw)


def _host_part(text: str) -> str:
    """The part of a word that names its host. In `host:path` or `user@host:path` it is the host.
    In a URL it is the scheme and the whole authority (`scheme://user:password@host:port`): a
    value that may expand there may move the host (`http://$H#@x/`, `http://x\\@$H/`), while
    the path, query and fragment after it may hold anything."""
    scheme, separator, rest = text.partition(SCHEME_MARK)
    if not separator:
        return text.split("/", 1)[0].split(":", 1)[0].rpartition("@")[2]
    return scheme + separator + _unbracketed(AUTHORITY_END.split(rest, 1)[0])


def _unbracketed(authority: str) -> str:
    """A URL authority with the brackets dropped around an IPv6 literal host (`[::1]:8080`), which
    are no curl URL glob; any other bracket stays."""
    user, at, host = authority.rpartition("@")
    inner, closed, after = host[1:].partition("]")
    if not host.startswith("[") or not closed:
        return authority
    try:
        ipaddress.IPv6Address(inner)
    except ValueError:
        return authority
    return user + at + inner + after


def _as_url(text: str) -> str:
    """A URL as curl and wget read it: one with no scheme is an http:// URL (`localhost:$PORT/x`
    has `localhost:$PORT` for its authority)."""
    return text if SCHEME_MARK in text else DEFAULT_SCHEME + text


def _host_piece(word: _ShellWord, *, url: bool = False) -> _Piece:
    """A word's host part; `url` reads it as a URL even when it has no scheme. A word that is
    only a variable (`"$URL"`) stays whole: it is literal only when the line binds it to one."""
    if word.value is not None:
        return _Piece(word.value, word.value)
    as_url = url and not VARIABLE_ONLY.fullmatch(word.raw)
    return _Piece(None, _host_part(_as_url(word.raw) if as_url else word.raw))


def _is_remote_form(text: str) -> bool:
    """`host:path` (no `/` before the first `:`), `rsync://...` or `scp://...`."""
    colon = text.find(":")
    return text.startswith(REMOTE_SCHEMES) or (colon >= 0 and "/" not in text[:colon])


def _option_text(word: _ShellWord) -> str:
    """An argument as an option parser reads it: its value, or, when that may expand, the word
    without its quotes (`"--host=$H"` and `"-S$H"` are options too)."""
    return word.value if word.value is not None else _unquoted(word.raw)


def _option_label(option: str) -> str:
    return option if option.startswith("--") else f"-{option}"


def _short_cluster(
    words: Sequence[_ShellWord], index: int, syntax: _CliSyntax
) -> tuple[str, tuple[str, _Piece] | None, bool]:
    """A cluster of short options (`-vJ host`, `-p2222`): its flags, the option that takes a
    value with that value (glued, or the next word unless it takes only a glued one), and
    whether the next word was used."""
    word = words[index]
    text = _option_text(word)
    for offset, letter in enumerate(text[1:], start=2):
        if letter not in syntax.values + syntax.glued:
            continue
        flags = text[1 : offset - 1]
        if offset < len(text):
            if word.value is not None:
                return flags, (letter, _Piece(text[offset:], text[offset:])), False
            # as written when the option letters are (`-c"\c ..."` keeps its `\c`)
            plain = word.raw.startswith(text[:offset])
            return flags, (letter, _Piece(None, (word.raw if plain else text)[offset:])), False
        if letter in syntax.glued or index + 1 >= len(words):
            return flags, None, False
        following = words[index + 1]
        return flags, (letter, _Piece(following.value, following.raw)), True
    return text[1:], None, False


def _long_name(name: str, syntax: _CliSyntax) -> str:
    """A long option's full name. getopt_long and curl take an unambiguous prefix (`--ho` for
    `--host`): a prefix of an option that reads hosts is read as that option, strictest first."""
    if name in syntax.long_values | syntax.hosts | syntax.files:
        return name
    for group in (syntax.files, syntax.hosts):
        full = next((option for option in sorted(group) if option.startswith(name)), None)
        if full is not None:
            return full
    return name


def _option(
    words: Sequence[_ShellWord], index: int, syntax: _CliSyntax
) -> tuple[str, tuple[str, _Piece] | None, bool]:
    """One option word: its option names as written (short flags, or a long name), the option
    that takes a value with that value (`--host=h`, `--host h`), and whether the next word was
    used."""
    text = _option_text(words[index])
    if not text.startswith("--"):
        return _short_cluster(words, index, syntax)
    name, equals, rest = text.partition("=")
    full = _long_name(name, syntax)
    if equals:
        if words[index].value is not None:
            return name, (full, _Piece(rest, rest)), False
        written = words[index].raw.partition("=")[2]  # keeps `\c` in `--command="\c ..."`
        return name, (full, _Piece(None, written)), False
    if full not in syntax.long_values | syntax.hosts | syntax.files or index + 1 >= len(words):
        return name, None, False
    following = words[index + 1]
    return name, (full, _Piece(following.value, following.raw)), True


def _cli_args(words: Sequence[_ShellWord], syntax: _CliSyntax) -> _CliArgs:
    """A remote CLI's arguments, split as its option parser splits them."""
    flags, values, positionals, expanding, longs = "", [], [], [], []
    index, options = 0, True
    while index < len(words):
        word = words[index]
        text = _option_text(word)
        if not options or not text.startswith("-") or text == "-":
            positionals.append(word)
        elif text == "--":
            options = False
        else:
            named, value, used_next = _option(words, index, syntax)
            if text.startswith("--"):
                longs.append(named)
            else:
                flags += named
            values += [value] if value else []
            if word.value is None and EXPANDING.intersection(named):  # `-"$O"`, `--"$O"`
                expanding.append(word.raw)
            index += used_next
        index += 1
    return _CliArgs(flags, values, positionals, expanding, longs)


def _setting(piece: _Piece) -> tuple[str, _Piece] | None:
    """An ssh `-o` setting's name in lower case and its value (`HostName=h`, `ProxyJump h`)."""
    match = SETTING.fullmatch(_written(piece))
    if match is None:
        return None
    return match[1].lower(), _Piece(match[2] if piece.value is not None else None, match[2])


def _settings(args: _CliArgs, syntax: _CliSyntax, names: frozenset[str]) -> Iterator[_Piece]:
    """The values of a remote CLI's `-o` settings named in `names`."""
    for letter, piece in args.values if syntax.settings else ():
        setting = _setting(piece) if letter == "o" else None
        if setting is not None and setting[0] in names:
            yield setting[1]


def _option_hosts(args: _CliArgs, syntax: _CliSyntax) -> Iterator[_Piece]:
    """The hosts a remote CLI's options name: -J, --host and the like, and ssh's host
    settings."""
    yield from (piece for option, piece in args.values if option in syntax.hosts)
    yield from _settings(args, syntax, HOST_SETTINGS)


def _host_value_problem(piece: _Piece, syntax: _CliSyntax, bindings: _Bindings) -> str | None:
    """Why a host value is not written out: it may expand, or the CLI expands it into hosts no
    target pattern sees whole (curl's `{a,b}` and `[1-3]`)."""
    if not _is_literal(piece, bindings):
        return f"{piece.raw} may expand"
    if set(syntax.globs) & set(_host_part(_as_url(piece.raw) if syntax.urls else piece.raw)):
        return f"{piece.raw} expands into hosts"
    return None


def _host_positionals(args: _CliArgs, syntax: _CliSyntax) -> list[_ShellWord]:
    """The positionals that name a host, as a host, a URL or a connection string."""
    if syntax.urls:
        return args.positionals
    if syntax.first_host and not set(syntax.no_host) & set(args.flags):
        return args.positionals[:1]
    return []


def _bound_value(word: _ShellWord, bindings: _Bindings) -> tuple[str, str] | None:
    """A word whose value may expand, read through the values the line binds its names to: the
    text it holds (inside its double quotes, if it is one double-quoted string) and its value;
    None when it holds other quotes or escapes, or cannot be read so."""
    raw = word.raw
    quoted = len(raw) >= 2 and raw[0] == raw[-1] == '"'
    inner = raw[1:-1] if quoted else raw
    if QUOTE_MARKS.intersection(inner) or "`" in inner:
        return None
    value = bindings.substitute(inner, quoted=quoted)
    return None if value is None else (inner, value)


def _url_host(url: str) -> str | None:
    """The host of a URL: after the last `@` of its authority, before its port; None when a `\\`
    in the authority may hide it."""
    rest = url.partition(SCHEME_MARK)[2]
    authority = AUTHORITY_END.split(rest, 1)[0]
    if "\\" in authority:
        return None
    host_port = authority.rpartition("@")[2]
    if host_port.startswith("["):
        return host_port.partition("]")[0] + "]"
    return host_port.split(":", 1)[0]


def _built_host_problem(raw: str, inner: str, value: str, syntax: _CliSyntax) -> str | None:
    """Why a URL read through bound values does not show its host: a name in it moves the host
    (`localhost:$PORT` with PORT=`1@pro-db`) or builds it with other text (`$H-db`), or, for a
    libpq URL, sets it in the query. A name that is the whole word or the whole host is fine:
    its value, host and all, is written out where the line binds it."""
    if PLAIN_REFERENCE.fullmatch(inner):
        return None
    if syntax.conninfo and (SCHEME_MARK not in inner or "$" in inner.partition("?")[2]):
        return f"{raw} may set its host through a variable"
    url_inner, url_value = (_as_url(inner), _as_url(value)) if syntax.urls else (inner, value)
    host = _url_host(url_inner)
    if host is not None and PLAIN_REFERENCE.fullmatch(host):
        return None
    if host is None or host != _url_host(url_value):
        return f"{raw} builds or moves its host through a variable"
    return None


def _host_positional_problem(
    word: _ShellWord, syntax: _CliSyntax, bindings: _Bindings
) -> str | None:
    """Why a host positional is not written out. A URL (curl, wget, a libpq URL) whose value may
    expand is read through the values the line binds its names to, when it can be; otherwise a
    libpq positional counts whole and a URL by its scheme and authority."""
    bound = _bound_value(word, bindings) if word.value is None else None
    if (syntax.urls or syntax.conninfo) and bound is not None:
        inner, value = bound
        why = _built_host_problem(word.raw, inner, value, syntax)
        return why or _host_value_problem(_Piece(value, value), syntax, bindings)
    if syntax.conninfo:
        whole = _Piece(word.value, _written(word))
        return _host_value_problem(whole, syntax, bindings)
    return _host_value_problem(_host_piece(word, url=syntax.urls), syntax, bindings)


def _positional_problem(args: _CliArgs, syntax: _CliSyntax, bindings: _Bindings) -> str | None:
    """Why a host among a remote CLI's positionals is not written out, or None."""
    for word in _host_positionals(args, syntax):
        why = _host_positional_problem(word, syntax, bindings)
        if why:
            return why
    for word in args.positionals if syntax.remote_positionals else ():
        if word.value is not None:
            continue
        if _is_remote_form(word.raw) and not _is_literal(_host_piece(word), bindings):
            return f"{word.raw} may expand"
        if bindings.from_input(word.raw):  # `read src`, then `rsync "$src" /d`
            return f"{word.raw} may hold a host"
    return None


def _option_commands(args: _CliArgs, syntax: _CliSyntax) -> Iterator[_Piece]:
    """The command strings a remote CLI's options give it: sftp's -D, and ssh settings that run
    a command (`ProxyCommand`, `LocalCommand`, `KnownHostsCommand`)."""
    yield from (piece for letter, piece in args.values if letter in syntax.commands)
    yield from _settings(args, syntax, COMMAND_SETTINGS)


def _connect_problem(args: _CliArgs, syntax: _CliSyntax) -> str | None:
    """A connect meta-command (`\\c`, `connect`, `:connect`) among the commands a database CLI
    is given, where one of those commands may expand (`-c "\\c - - $H"`, or `-v h=$H` with
    `-c '\\c - - :h'`)."""
    if syntax.connect is None:
        return None
    given = [piece for option, piece in args.values if option in syntax.scripts]
    connects = any(syntax.connect.search(_written(piece)) for piece in given)
    expanding = next((piece.raw for piece in given if piece.value is None), None)
    if connects and expanding is not None:
        return f"{expanding} may expand where a connect command picks the host"
    return None


def _written(piece: _Written) -> str:
    """A word or part of one as known: its value, or its text as written when that may expand."""
    return piece.value if piece.value is not None else piece.raw


def _fed_connect_problem(text: str) -> _HostProblem | None:
    """A database CLI fed on stdin (a pipe, a here-string or a heredoc) on a line that holds a
    connect meta-command and a `$`: the input may connect to a host the line does not show."""
    if "$" not in text:
        return None
    for segment, fed in _fed_segments(text):
        words = _unquoted(segment).split()
        for index in _command_candidates(words):
            name = _basename(words[index])
            syntax = CLI_SYNTAX.get(name)
            connect = syntax.connect if syntax else None
            if connect and (fed or "<<" in segment) and connect.search(text):
                return _HostProblem(name, "its input may connect to a host that may expand")
    return None


def _option_problem(args: _CliArgs, syntax: _CliSyntax, bindings: _Bindings) -> str | None:
    """Why a remote CLI's options may reach a host not written out: an option name that may
    expand into any option, an option that reads hosts from elsewhere (curl's -K, wget's -i),
    or a host option whose value is not written out."""
    if args.expanding:
        return f"{args.expanding[0]} may expand into any option"
    why = _connect_problem(args, syntax)
    if why:
        return why
    for option, _piece in args.values:
        if option in syntax.files:
            return f"{_option_label(option)} reads hosts the command does not show"
    for piece in _option_hosts(args, syntax):
        why = _host_value_problem(piece, syntax, bindings)
        if why:
            return why
    return None


def _cli_host_problem(
    name: str, words: Sequence[_ShellWord], bindings: _Bindings, depth: int
) -> _HostProblem | None:
    """The host problem of remote CLI `name` given the words after it: a host that is not
    written out, or one in a command its settings run (`-o ProxyCommand=...`)."""
    syntax = CLI_SYNTAX[name]
    args = _cli_args(words, syntax)
    if syntax.globoff.intersection([*args.flags, *args.longs]):
        syntax = replace(syntax, globs="")  # `curl -g`: no URL globs in this command
    why = _option_problem(args, syntax, bindings)
    if why:
        return _HostProblem(name, why)
    for piece in _option_commands(args, syntax):
        found = _nested_problem(name, piece, depth)
        if found:
            return found
    why = _positional_problem(args, syntax, bindings)
    return None if why is None else _HostProblem(name, why)


def _unfollowed_problem(text: str) -> _HostProblem | None:
    """For a text the scanner cannot follow: a remote CLI the wrapper walk finds in command
    position, quotes dropped, or one named anywhere in a text that defines an alias (`alias
    s='TERM=x ssh $h'`, `builtin alias`, `BASH_ALIASES[s]=`), whose value runs as a command. A
    remote CLI named elsewhere (a commit message) is no problem."""
    commands = [segment.split() for segment in COMMAND_SPLIT.split(_unquoted(text))]
    named = REMOTE_WORD.search(_unquoted(text))
    if named and any(_defines_alias(words) for words in commands):
        return _HostProblem(named[0], "an alias may run it with any host")
    for words in commands:
        for index in _command_candidates(words):
            if _basename(words[index]) in REMOTE_CLIS:
                return _HostProblem(_basename(words[index]), _unfollowed_reason(text))
    return None


def _defines_alias(words: Sequence[str]) -> bool:
    """Whether a simple command defines an alias: `alias` among its candidates, or an assignment
    to bash's or zsh's alias table."""
    candidates = [_basename(words[index]) for index in _command_candidates(words)]
    return ALIAS in candidates or any(ALIAS_TABLE.match(word) for word in words)


def _unfollowed_reason(text: str) -> str:
    """Why the scanner could not follow `text`, as far as it names a fix: unquoted heredocs, and
    nothing else, when quoting their delimiters lets the scanner follow the line."""
    quoted = HEREDOC.sub(_quote_delimiter, text)
    if quoted == text or _shell_words().simple_commands(quoted) is None:
        return UNFOLLOWED
    return UNQUOTED_HEREDOC


def _quote_delimiter(match: re.Match[str]) -> str:
    """A heredoc operator with its delimiter quoted, unless it is quoted already."""
    delimiter = match[1]
    if QUOTE_MARKS.intersection(delimiter):
        return match[0]
    return match[0][: match.start(1) - match.start(0)] + f"'{delimiter}'"


def _stdin_problem(text: str) -> _HostProblem | None:
    """A remote CLI named in a text where a shell runs what stdin holds (`echo "ssh $h" | sh`)."""
    named = REMOTE_WORD.search(_unquoted(text))
    if named and any(_runs_stdin(segment, fed) for segment, fed in _fed_segments(text)):
        return _HostProblem(named[0], "a shell runs what its input holds")
    return None


def _command_string_problem(
    words: Sequence[_ShellWord], head: Sequence[int], depth: int
) -> _HostProblem | None:
    """The host problem of a command string a wrapper in the head runs (`sh -c '...'`,
    `su -c '...'`), followed MAX_SHELL_DEPTH deep; one whose value may expand is read as text."""
    texts = [_text(word) for word in words]
    for index in head:
        name = _basename(texts[index])
        if name not in COMMAND_STRING_RUNNERS:
            continue
        given = next((j + 1 for j in range(index + 1, len(texts) - 1) if _holds_c(texts[j])), None)
        if given is not None:
            return _nested_problem(name, words[given], depth)
    return None


def _after_split_option(text: str) -> str | None:
    """What follows env's `-S` glued in a cluster (`-S'...'`, `-iS'...'`) or `--split-string=`
    (or an abbreviation of it); None for any other word."""
    if text.startswith("--"):
        name, equals, rest = text[2:].partition("=")
        return rest if equals and name and SPLIT_STRING.startswith(name) else None
    if text.startswith("-") and SPLIT_LETTER in text:
        return text[text.index(SPLIT_LETTER) + 1 :]
    return None


def _env_split_string(word: _ShellWord) -> _Written:
    """The command string an env option word holds: the word without a glued `-S` or
    `--split-string=`, or the word itself."""
    raw = _after_split_option(word.raw)
    if raw is None:
        return word
    value = None if word.value is None else _after_split_option(word.value)
    return _Piece(value, raw)


def _parallel_problem(
    words: Sequence[_ShellWord], head: Sequence[int], depth: int
) -> _HostProblem | None:
    """The host problem of each word after a `:::` (or `::::`, `:::+`) given to parallel, which
    may run it as a command (`parallel ::: "ssh $h uptime"`)."""
    texts = [_text(word) for word in words]
    runner = next((i for i in head if _basename(texts[i]) == PARALLEL), None)
    if runner is None:
        return None
    start = next((i for i in range(runner + 1, len(texts)) if texts[i] in INPUT_SOURCES), None)
    for word in words[start + 1 :] if start is not None else ():
        found = None if _text(word) in INPUT_SOURCES else _nested_problem(PARALLEL, word, depth)
        if found:
            return found
    return None


def _nested_problem(runner: str, word: _Written, depth: int) -> _HostProblem | None:
    """The host problem of a command string `runner` runs; one whose value may expand is read
    as text the scanner cannot follow."""
    if word.value is None:
        return _unfollowed_problem(word.raw)
    if depth >= MAX_SHELL_DEPTH:
        return _HostProblem(runner, f"command strings nested more than {MAX_SHELL_DEPTH} deep")
    return _host_problem(word.value, depth + 1)


def _holds_c(text: str) -> bool:
    """A short option cluster with `c` in it (`-c`, `-lc`, `-ec`)."""
    return text.startswith("-") and not text.startswith("--") and "c" in text


def _host_var_problem(
    words: Sequence[_ShellWord], head: Sequence[int], bindings: _Bindings
) -> _HostProblem | None:
    """A host variable (`PGHOST`) set to a value that may expand, for a simple command that
    names a remote CLI anywhere (a command string it runs inherits the variable): set in its
    head (`PGHOST=$H psql`, `env PGHOST=$H psql`) or, failing that, earlier on the line."""
    named = REMOTE_WORD.search(" ".join(_unquoted(word.raw) for word in words))
    if named is None:
        return None
    own: dict[str, bool] = {}
    for index in head:
        match = BINDING.match(_unquoted(words[index].raw))
        if match and match[1] in HOST_VARS:
            own[match[1]] = words[index].value is not None and not match[2]
    for name in sorted(HOST_VARS):
        if not own.get(name, bindings.literal.get(name, True)):
            return _HostProblem(named[0], f"{name} is set to a value that may expand")
    return None


def _remote_cli_problem(
    words: Sequence[_ShellWord], bindings: _Bindings, depth: int
) -> _HostProblem | None:
    """The host problem of one simple command that is not a binding: a host variable that may
    expand, through a command string (`sh -c '...'`, or any candidate holding a blank, as in
    `parallel 'ssh {}'`, `watch "ssh $h"` or `env -S '...'`), through xargs or parallel, or in a
    remote CLI's own arguments."""
    texts = [_text(word) for word in words]
    head = _command_head(texts).indices
    found = (
        _host_var_problem(words, head, bindings)
        or _command_string_problem(words, head, depth)
        or _parallel_problem(words, head, depth)
    )
    if found:
        return found
    names = [_basename(text) for text in texts]
    for index in _command_candidates(texts):
        name = _basename(texts[index])
        if COMMAND_BLANKS.intersection(texts[index]):
            given = _env_split_string(words[index]) if ENV in names[:index] else words[index]
            found = _nested_problem(_basename(texts[0]), given, depth)
            if found:
                return found
            continue
        if name not in REMOTE_CLIS:
            continue
        if any(_basename(texts[i]) in INPUT_RUNNERS for i in head if i < index):
            return _HostProblem(name, "xargs/parallel supplies its arguments")
        found = _cli_host_problem(name, words[index + 1 :], bindings, depth)
        if found:
            return found
    return None


def _bind_assignments(
    command: str, words: Sequence[_ShellWord], bindings: _Bindings, *, conditional: bool
) -> bool:
    """Bind the names of an assignment-only command (`h=x`, `h+=y`); False for any other. An
    append is never literal: it builds a value no target pattern sees whole (`h=192.0.2.;
    h+=10`), and neither is an array (`h=(...)`, which the scanner splits at its `(`): `$h`
    expands to its words in zsh."""
    matches = [BINDING.match(word.raw) for word in words]
    if not words or not all(matches):
        return False
    for word, match in zip(words, matches, strict=True):
        if match:
            array = command[word.end : word.end + 1] == ARRAY_OPEN
            literal = word.value is not None and not match[2] and not array
            value = word.value[len(match[0]) :] if word.value is not None else None
            bindings.bind(match[1], literal, conditional=conditional, value=value)
    return True


def _bind_inputs(words: Sequence[_ShellWord], bindings: _Bindings, *, conditional: bool) -> bool:
    """Bind the names a `for` loop or an input binder (`read h`) sets; False for any other."""
    texts = [_text(word) for word in words]
    head = _command_head(texts)
    if head.open:
        return False
    stop = head.indices[-1]  # the command the walk stops at
    name, rest = _basename(texts[stop]), words[stop + 1 :]
    loop_name = rest[0].value if rest else None
    if name == LOOP and loop_name is not None:
        listed = len(rest) > 1 and rest[1].value == "in"
        literal = listed and all(word.value is not None for word in rest[2:])
        bindings.bind(loop_name, literal, conditional=conditional)
        return True
    if name not in INPUT_BINDERS:
        return False
    for word in rest:
        bound = _input_name(name, _text(word))
        if bound is not None:
            bindings.bind(bound, False, conditional=conditional)
    return True


def _bind_prefix(words: Sequence[_ShellWord], bindings: _Bindings) -> None:
    """Bind the names of a command's prefix assignments (`h=$x :`) to a non-literal: dash keeps
    them when the command is a special builtin."""
    for word in words:
        match = BINDING.match(word.raw)
        if match is None:
            return
        bindings.bind(match[1], False, conditional=False)


def _input_name(binder: str, value: str) -> str | None:
    """The name an input binder's argument sets: the word itself (`read h`), or the name glued
    to printf's or zsh print's `-v` (`-vh`) or after `a` in a read option cluster (`-rah`). For
    an array element (`read h[0]`) it is the name before the `[`."""
    if binder in VARIABLE_PRINTERS and value.startswith(PRINTF_VARIABLE):
        value = value[len(PRINTF_VARIABLE) :]
    elif binder == "read" and value.startswith("-"):
        value = value.partition(READ_ARRAY)[2]
    name = value.partition("[")[0]
    return name if name.isidentifier() else None


def _leading_count(words: Sequence[_ShellWord]) -> int:
    """How many words open the command with a compound keyword (`then`, `do`, `{`)."""
    count = 0
    while count < len(words) and _text(words[count]) in LEADING_KEYWORDS:
        count += 1
    return count


def _may_not_bind(command: str, words: Sequence[_ShellWord], lead: int) -> bool:
    """Whether a binding in this simple command may not hold for the commands after it: it
    follows a compound keyword (`then h=x`) or a `&`, `|` or `(` earlier on the line, or runs in
    the background or in a pipeline (`h=x &`, `h=x | cat`)."""
    before = command[: words[0].start]
    after = command[words[-1].end :].lstrip(BLANKS)
    detached = after[:1] in ("&", "|") and after[:2] not in ("&&", "||")
    return lead > 0 or detached or bool(CONDITIONAL_MARKS & set(before))


def _line_problem(
    command: str, commands: Sequence[Sequence[_ShellWord]], depth: int
) -> _HostProblem | None:
    """The first host problem among a line's simple commands, binding names in line order."""
    bindings = _Bindings()
    for words in commands:
        if not words:
            continue
        lead = _leading_count(words)
        conditional = bindings.conditional or _may_not_bind(command, words, lead)
        body = words[lead:]
        bound = _bind_assignments(command, body, bindings, conditional=conditional) or _bind_inputs(
            body, bindings, conditional=conditional
        )
        if not bound:
            found = _remote_cli_problem(words, bindings, depth)
            if found:
                return found
            _bind_prefix(body, bindings)
        bindings.conditional = bindings.conditional or _text(words[0]) in COMPOUND_WORDS
    return None


def _host_problem(command: str, depth: int = 0) -> _HostProblem | None:
    """Why a remote CLI's host in `command` is not written out in it, or None.

    A command the scanner cannot follow is a problem only where the wrapper walk finds a remote
    CLI in command position. Otherwise each simple command is read in line order: an
    assignment-only command binds a name to a literal when its value is known, and so does `for
    NAME in` words that are all known; `read`, `mapfile`, `select` and the like bind theirs to
    input. A host is written out when its value is known, or when it is exactly `$NAME` with NAME
    bound to a literal; for `host:path`, `user@host` and `scheme://host/` only the host counts."""
    found = _stdin_problem(command) or _fed_connect_problem(command)
    if found:
        return found
    commands = _shell_words().simple_commands(command)
    if commands is None:
        return _unfollowed_problem(command)
    return _line_problem(command, [command.words for command in commands], depth)


class _Zone(NamedTuple):
    """A place a restricted session may not write: what a Bash or MCP text can name it by."""

    roots: tuple[Path, ...]  # the folders and files it is
    label: str  # how the refusal names it
    name: str  # the rule name the log holds
    tokens: frozenset[str]  # names that count wherever they are a whole token in a text
    globs: frozenset[str]  # names a relative component may stand for after a `cd`
    entries: tuple[str, ...]  # what sits at its top: a bare `*` before one can reach in
    parents: tuple[str, ...] = ()  # the folders that may hold it: a name counts when one is named
    suffixes: tuple[tuple[str, ...], ...] = ()  # paths in any folder that end in these parts
    writes_only: bool = False  # Bash may read it and run its files: only a write counts


def _store_zone(store: Path) -> _Zone:
    names = frozenset({store.name, os.path.basename(os.path.realpath(store))})
    return _Zone(
        (store,), f"the memory store ({store})", "store-write", names, names, STORE_ENTRIES
    )


def _config_zone() -> _Zone:
    """coven's config file and its folder: the memory path and the guard latch live there."""
    folder = config_file().parent
    token = f"{folder.parent.name}/{folder.name}"
    return _Zone(
        (folder,),
        f"coven's config ({config_file()})",
        "config-write",
        frozenset({token}),
        frozenset({folder.name}),
        CONFIG_ENTRIES,
        (folder.parent.name,),
    )


def claude_dir() -> Path:
    """Claude Code's user folder: CLAUDE_CONFIG_DIR, or `~/.claude`."""
    configured = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(os.path.abspath(configured)) if configured else Path.home() / CLAUDE_FOLDER


def _claude_folders() -> tuple[Path, ...]:
    """Claude Code's user folder as this hook sees it, and the default one: a command may run
    under another CLAUDE_CONFIG_DIR than the hook's."""
    return tuple(dict.fromkeys((claude_dir(), Path.home() / CLAUDE_FOLDER)))


def _settings_zone() -> _Zone:
    """Claude Code's settings files, the user's and any project's: they register the hooks."""
    folders = _claude_folders()
    return _Zone(
        tuple(folder / name for folder in folders for name in SETTINGS_FILES),
        "Claude Code's settings files",
        "claude-settings",
        frozenset(f"{CLAUDE_FOLDER}/{name}" for name in SETTINGS_FILES),
        frozenset(SETTINGS_FILES),
        (),
        (CLAUDE_FOLDER, *(folder.name for folder in folders)),
        tuple((CLAUDE_FOLDER, name) for name in SETTINGS_FILES),
    )


def _plugins_zone() -> _Zone:
    """Claude Code's plugins folder: it holds the installed hooks themselves, and the skills'
    own scripts, which a session runs by path. Bash may read and run what it holds."""
    folders = _claude_folders()
    return _Zone(
        tuple(folder / PLUGINS_FOLDER for folder in folders),
        f"Claude Code's plugins folder ({folders[0] / PLUGINS_FOLDER})",
        "claude-plugins",
        frozenset({f"{CLAUDE_FOLDER}/{PLUGINS_FOLDER}"}),
        frozenset({PLUGINS_FOLDER}),
        PLUGIN_ENTRIES,
        (CLAUDE_FOLDER, *(folder.name for folder in folders)),
        writes_only=True,
    )


def _zones(store: Path) -> tuple[_Zone, ...]:
    """The places a restricted session may not write, in the order a refusal names them."""
    return (_store_zone(store), _config_zone(), _settings_zone(), _plugins_zone())


def _glob_reaches(word: str, cwd: str, roots: Sequence[tuple[str, ...]]) -> bool:
    """Whether a glob word can match a root, one of its parents, or a path inside it."""
    pattern = Path(
        os.path.normpath(os.path.join(cwd, os.path.expandvars(os.path.expanduser(word))))
    ).parts
    for root in roots:
        depth = min(len(pattern), len(root))
        if all(fnmatch.fnmatchcase(root[i], pattern[i]) for i in range(depth)):
            return True
    return False


def _has_no_literal(part: str) -> bool:
    """Whether a glob component is only `*`, `?` and `[...]` classes: it can match almost any name.
    A class of one character (`[w]`) matches only that character, so it is a literal."""
    return set(CHARACTER_CLASS.sub("", ONE_CHARACTER_CLASS.sub(r"\1", part))) <= {"*", "?"}


def _matches_any(names: Iterable[str], part: str) -> bool:
    return any(fnmatch.fnmatchcase(name, part) for name in names)


def _part_names_zone(
    parts: Sequence[str], index: int, zone: _Zone, *, in_body: bool, parent_named: bool
) -> bool:
    """Whether component `index` can be the zone's own folder. A bare `*` can be any
    directory, so it counts only when the next component can be one of its entries
    (`*/.git/HEAD`, `*/systems/...`), not for `rm -rf *`, `*/node_modules` or `*/*.py`. In a
    heredoc body a component with no literal character (`[a-z]*`, `?*`) is as loose as a bare `*`
    and is read the same way. A zone named by a common word (`coven`) counts a component that can
    be its name only when the text also names the folder that holds it, or an entry follows."""
    part = parts[index]
    wildcard = _has_no_literal(part) if in_body else set(part) == {"*"}
    entry_next = any(_matches_any(zone.entries, after) for after in parts[index + 1 : index + 2])
    if wildcard:
        return entry_next
    return _matches_any(zone.globs, part) and (parent_named or entry_next)


def _names_parent(words: Iterable[str], zone: _Zone) -> bool:
    """Whether some word of the text can name a folder that holds the zone (`~/.config`)."""
    return any(
        fnmatch.fnmatchcase(parent, part)
        for word in words
        for part in word.split("/")
        if part
        for parent in zone.parents
    )


def _relative_glob_names_zone(words: Sequence[str], zone: _Zone, *, in_body: bool = False) -> bool:
    """A relative glob, or for a zone with a holding folder a plain name, with a component that
    can be the zone's own folder: after a `cd` (or `env -C`, `git -C`, `find`) the session cwd no
    longer tells where a relative path points."""
    parent_named = not zone.parents or _names_parent(words, zone)
    for word in words:
        if word.startswith(("/", "~", "$")):
            continue
        if not zone.parents and not GLOB_CHARS.intersection(word):
            continue
        parts = [part for part in word.split("/") if part not in ("", ".", "..")]
        if any(
            _part_names_zone(parts, index, zone, in_body=in_body, parent_named=parent_named)
            for index in range(len(parts))
        ):
            return True
    return False


def _words_of(text: str) -> list[str]:
    return [word for word in SHELL_SPLIT.split(_unquoted(text)) if word]


def _body_words(body: str) -> list[str]:
    """The words of a heredoc body, quotes dropped first (`co''ven-m*` is `coven-m*`), a regex class
    escape (`\\w \\W \\d \\D \\s \\S`) read as the `?` it can match."""
    return _words_of(CLASS_ESCAPE.sub("?", body))


def _reads_body_as_text(reader: str | None) -> bool:
    """Whether a command (named by its first word) reads a heredoc body as a script or text, not
    as shell commands: a listed interpreter, by basename. A reader the guard cannot name is not."""
    if reader is None:
        return False
    name = _basename(reader)
    return name in READS_BODIES_AS_TEXT or bool(PYTHON_NAME.fullmatch(name))


def _may_run_quoted(command: str) -> bool:
    """Whether a command on the line, or a wrapper before one, may hand a quoted word to a shell
    or to code of its own (QUOTED_RUNNERS, python), which may expand a glob in it."""
    for segment in COMMAND_SPLIT.split(command):
        words = _unquoted(segment).split()
        for index in _command_head(words).indices:
            name = _basename(words[index])
            if name in QUOTED_RUNNERS or PYTHON.fullmatch(name):
                return True
    return False


def _globbing_code(bash: Bash) -> str:
    """A Bash call's code text as its glob checks read it: a glob character the shell keeps
    literal (in quotes, after a backslash) is masked (shell_words.without_quoted_globs), so `ls
    '.*'` names no folder. The mask is read from the command as written, since the code text
    blanks a heredoc body newlines and all, which hides where the body ends, and copied onto the
    code text, which has the same length. Not on a line where a command may run a quoted word
    (`bash -c '...'`, `python3 -c "glob.glob(...)"`, `xargs`, `ssh`): there it is read as a glob
    as ever."""
    if _may_run_quoted(bash.command):
        return bash.code
    masked: str = _shell_words().without_quoted_globs(bash.command)
    mark: str = _shell_words().LITERAL_GLOB_MARK
    return "".join(
        mark if now != was and kept == was else kept
        for was, now, kept in zip(bash.command, masked, bash.code, strict=True)
    )


def _text_bodies(command: str) -> _Bodies | None:
    """The heredoc bodies of a line when every command that reads one reads it as text
    (`_reads_body_as_text`) and no word on the rest of the line is a shell reader; None
    otherwise, or when the scanner cannot follow the line (shell_words.heredoc_bodies). A line
    with no heredoc gives the line and no bodies."""
    found: _Bodies | None = _shell_words().heredoc_bodies(command)
    if found is None or not all(_reads_body_as_text(reader) for reader in found.readers):
        return None
    if SHELL_READERS.intersection(_basename(word) for word in _words_of(found.rest)):
        return None
    return found


def _shell_glob_words(command: str) -> list[str]:
    """The words of a Bash command's glob text that the shell may expand from the working
    folder: all of them, but in a heredoc body read as text (`_text_bodies`) only a word that
    starts at a fixed place (`/`, `~`, `$`). A relative regex in a script (`r'(a).*'`) is not a
    shell glob; the body's relative words are read after a `cd` by `_bash_glob_names_zone`."""
    found = _text_bodies(command)
    if found is None:
        return _words_of(command)
    fixed = [
        word
        for body in found.bodies
        for word in _body_words(body)
        if word.startswith(("/", "~", "$"))
    ]
    return [*_words_of(found.rest), *fixed]


def _bash_glob_names_zone(command: str, zone: _Zone) -> bool:
    """`_relative_glob_names_zone` for a Bash command's glob text (`_globbing_code`), with a
    narrower reading of a grep pattern and of a heredoc body.

    A pattern word of `grep`, `egrep`, `fgrep`, `rg` or `git grep` is blanked first
    (shell_words.without_patterns): the command searches with it, so `[A-Za-z]*` in it is a
    regex. Only the pattern: a file operand, an unquoted glob (`grep c* a.txt`, which the shell
    expands) and a pattern the scanner cannot read (`"$(...)"`) are read as ever. This check is
    the only one that blanks them: the name checks in `_text_reaches` read the whole text.

    The leniency for a body is an allowlist: only when every command that reads one is python,
    node, perl, ruby, ssh, or cat or tee (which copy it to a file for one of them to run later;
    READS_BODIES_AS_TEXT). Its body is a script, a remote command or file content, so a regex in
    it (`[A-Za-z_]*`, `\\w*`) is no shell glob: a component with no literal character counts
    only as a bare `*` does, and one with a literal (`c*-memory`, `[c]*`: a class of one
    character is a literal) counts as ever. The body is read with its quotes dropped, as the
    shell would, and `\\w \\W \\d \\D \\s \\S` read as `?`.

    A real shell glob can still get into a body through a program that runs what it is given
    (`os.system` in a python body), or through a file copied by `cat` or `tee` and run by a name
    SHELL_READERS does not list (`ash`, `./x`). Such a pass reaches no further than a bare `*`
    does outside a body.

    Everything else is read as ever: a body read by any other command or by one the guard cannot
    name, a line with one of the SHELL_READERS names as a word, a pipe, an unquoted heredoc, or
    anything the scanner cannot follow (shell_words.heredoc_bodies)."""
    blanked = _without_patterns(command)
    found = _text_bodies(blanked)
    if found is None:
        return _relative_glob_names_zone(_words_of(blanked), zone)
    body_words = [word for body in found.bodies for word in _body_words(body)]
    return _relative_glob_names_zone(_words_of(found.rest), zone) or _relative_glob_names_zone(
        body_words, zone, in_body=True
    )


def _names_token(text: str, names: Iterable[str]) -> bool:
    """Whether one of `names` is in `text` with no name character right before or after it."""
    return any(
        re.search(f"(?<![{NAME_CHARS}]){re.escape(name)}(?![{NAME_CHARS}])", text) for name in names
    )


def _default_value(name: str) -> str:
    """What a variable holds when the command sets it itself: the usual place, and for the plugin
    root a folder under the plugins folder, which it is whatever the hook's own value."""
    home = Path.home()
    return {
        "HOME": str(home),
        "CLAUDE_CONFIG_DIR": str(home / CLAUDE_FOLDER),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "CLAUDE_PLUGIN_ROOT": str(claude_dir().joinpath(PLUGINS_FOLDER, *PLUGIN_ROOT_DEFAULT)),
    }[name]


def _expanded(text: str, *, default: bool) -> str:
    """`text` with the zone variables written out, plain, quoted or braced (`${NAME:-word}` too),
    from the hook's environment or from their defaults."""

    def value(match: re.Match[str]) -> str:
        name = match[1] or match[3]
        found = _default_value(name) if default else os.environ.get(name, "")
        return found or match[2] or ""

    return ZONE_VARIABLE.sub(value, text)


def _expansions(text: str, glob_text: str) -> list[tuple[str, str]]:
    """The text as written, and with its zone variables expanded each way, each with its glob text
    expanded the same way: the check reads them all, since the command may run under other values
    than the hook sees."""
    pairs = (
        (text, glob_text),
        (_expanded(text, default=False), _expanded(glob_text, default=False)),
        (_expanded(text, default=True), _expanded(glob_text, default=True)),
    )
    return list(dict.fromkeys(pairs))


def _operands(args: Sequence[str]) -> list[str]:
    """The words that are not options (everything after `--` is an operand)."""
    found: list[str] = []
    options = True
    for word in args:
        if options and word == "--":
            options = False
        elif not options or not word.startswith("-"):
            found.append(word)
    return found


def _target_directory(args: Sequence[str]) -> list[str]:
    """The folder given as `-t DIR`, `-tDIR`, `--target-directory DIR` or `=DIR`."""
    for index, word in enumerate(args):
        match = TARGET_DIRECTORY.fullmatch(word)
        if match:
            return [match[1]] if match[1] else list(args[index + 1 : index + 2])
    return []


def _option_folders(args: Sequence[str], short: str, long: str | None = None) -> list[str]:
    """The folders of tar's `-C DIR` and unzip's `-d DIR`: the option alone, glued to its folder,
    or a letter in a cluster (`-xCf DIR file`: the words after it), and `--directory`."""
    found: list[str] = []
    for index, word in enumerate(args):
        if long and word.startswith(long + "="):
            found.append(word.partition("=")[2])
        elif long and word == long:
            found += args[index + 1 : index + 2]
        elif word.startswith("-") and not word.startswith("--") and short in word[1:]:
            glued = word.partition(short)[2]
            found += [glued] if glued else []
            found += [] if glued and not glued.isalpha() else args[index + 1 : index + 3]
        elif index == 0 and not word.startswith("-") and short in word and word.isalpha():
            found += args[index + 1 : index + 3]  # tar's old style: `tar xCf DIR file`
    return found


def _unpacks_here(args: Sequence[str], short: str, long: str) -> list[str]:
    """`["."]` when an archive command unpacks into the current folder: tar with its extract
    option, unzip unless it only lists, prints or tests (`-l`, `-p`, `-t`, `-v`, `-z`)."""
    letters = "".join(word[1:] for word in args if word.startswith("-") and word[:2] != "--")
    if long == "":
        return [] if set(letters) & set("lptvz") else ["."]
    old_style = bool(args) and not args[0].startswith("-") and short in args[0]
    return ["."] if short in letters or long in args or old_style else []


def _find_targets(args: Sequence[str]) -> list[str]:
    """The places `find` starts from, when it deletes or runs something."""
    if not FIND_ACTIONS.intersection(args):
        return []
    start = next((i for i, word in enumerate(args) if word[:1] in "-(!"), len(args))
    return list(args[:start])


def _command_targets(words: Sequence[str], index: int) -> list[str]:
    """The words a command at `index` changes or replaces: what it deletes, moves, links or
    changes the mode of, the destination of a copy, the folder an archive unpacks into."""
    name, args = _basename(words[index]), words[index + 1 :]
    if name in CHANGES_OPERANDS:
        return [*_operands(args), *_target_directory(args)]
    if name in CHANGES_DESTINATION:
        return _target_directory(args) or _operands(args)[-1:]
    if name == "tar":
        return _option_folders(args, "C", "--directory") or _unpacks_here(args, "x", "--extract")
    if name == "unzip":
        return _option_folders(args, "d") or _unpacks_here(args, "", "")
    return _find_targets(args) if name == "find" else []


def _redirect_targets(words: Sequence[str]) -> list[str]:
    """The files a simple command's output redirections write."""
    found: list[str] = []
    for index, word in enumerate(words):
        match = OUTPUT_REDIRECT.fullmatch(word)
        if match is None:
            continue
        target = match[1] or "".join(words[index + 1 : index + 2])
        if target and not target.startswith("&"):
            found.append(target)
    return found


class _Step(NamedTuple):
    """One simple command of a text: the words it changes or replaces, and whether it moves the
    session to another folder (`folder` is where, None when that cannot be told)."""

    targets: list[str]
    moves: bool = False
    folder: str | None = None


def _step(words: Sequence[str]) -> _Step:
    targets = _redirect_targets(words)
    for index in _runnable(words):
        targets += _command_targets(words, index)
    first = _basename(words[0]) if words else ""
    if first in CHANGE_DIR:
        operands = _operands(words[1:])
        return _Step(targets, True, operands[0] if operands else "~")
    # `popd`, or a `cd` further in (`(cd x; ...)`, `env -C`): the folder is not followed.
    return _Step(targets, first == "popd" or bool(CHANGE_DIR.intersection(words)))


def _write_targets(text: str) -> list[_Step]:
    """The simple commands of a text, in order, with what each changes or replaces: the operands
    of rm, mv, ln, chmod and the like, the destination of cp, install and rsync, the folder tar
    and unzip unpack into, a redirect's file, and what tee writes. A copy's source is only read."""
    return [
        _step(_split_env_strings(_unquoted(segment).split()))
        for segment in COMMAND_SPLIT.split(text)
    ]


def _is_root_or_parent(path: str, zone: _Zone) -> bool:
    """Whether a resolved path is a root of the zone or a folder that holds one (`~`, `~/.claude`),
    or a `.claude` folder, which holds the settings files of its project."""
    for root in zone.roots:
        for place in {str(root), os.path.realpath(root)}:
            if path == place or place.startswith(path.rstrip(os.sep) + os.sep):
                return True
    parts = Path(path).parts
    return any(
        len(suffix) > 1 and parts[-len(suffix) + 1 :] == suffix[:-1] for suffix in zone.suffixes
    )


def _holder_names(zone: _Zone) -> set[str]:
    """The names of the folders that hold the zone, below the home folder: what a relative
    operand can name after a `cd` that moved the session somewhere unknown."""
    home = Path.home().parts
    names: set[str] = {suffix[0] for suffix in zone.suffixes}
    for root in zone.roots:
        parts = root.parts[:-1]
        names.update(parts[len(home) :] if parts[: len(home)] == home else parts[1:])
    return names


def _may_hold_zone(target: str, zone: _Zone) -> bool:
    """Whether a relative operand, after a `cd` to somewhere unknown, can be a folder that holds
    the zone: `.`, `..`, or a name or glob that can stand for one of its holders (`.claude`,
    `.c*`, `.*`), not a bare `*`."""
    parts = [part for part in target.split("/") if part not in ("", ".", "..")]
    if not parts:
        return True
    names = _holder_names(zone)
    return not _has_no_literal(parts[0]) and _matches_any(names, parts[0])


def _changes_zone_holder(text: str, cwd: str, zone: _Zone) -> bool:
    """Whether something in the text changes or replaces a root of the zone or a folder holding
    one. The folder a relative operand is read from follows the `cd`s of the text; after one that
    cannot be followed (`cd "$X"`, `cd -`), a relative operand counts when it can name a holder."""
    current: str | None = cwd
    for step in _write_targets(text):
        if any(_changes_holder(target, current, zone) for target in step.targets):
            return True
        if step.moves:
            current = _after_cd(current, step.folder)
    return False


def _after_cd(current: str | None, folder: str | None) -> str | None:
    """Where the session is after `cd folder`; None when that cannot be told."""
    if current is None or folder is None or folder == "-" or "$" in folder:
        return None
    return None if GLOB_CHARS.intersection(folder) else _resolve(folder, current)


def _changes_holder(target: str, current: str | None, zone: _Zone) -> bool:
    """Whether a changed word is a root of the zone or a folder that holds one, read from the
    folder `current` (None: unknown, so a relative word counts when it can name a holder)."""
    if current is None and not target.startswith(("/", "~", "$")):
        return _may_hold_zone(target, zone)
    base = current or os.sep
    if GLOB_CHARS.intersection(target):
        roots = [root.parts for root in zone.roots]
        return _glob_reaches(target, base, roots)
    resolved = _resolve(target, base)
    return resolved is not None and _is_root_or_parent(resolved, zone)


def _text_reaches(
    text: str, cwd: str, zone: _Zone, *, cwd_known: bool, glob_text: str | None = None
) -> bool:
    """Whether a command-like text reaches the zone as written or with its variables expanded
    each way (`${CLAUDE_CONFIG_DIR}`, `$XDG_CONFIG_HOME`, `$HOME`, `$CLAUDE_PLUGIN_ROOT`).
    `glob_text` is the same text as its glob checks read it (`_globbing_code`); by default the
    text itself."""
    return any(
        _expanded_text_reaches(variant, cwd, zone, cwd_known=cwd_known, glob_text=globbing)
        for variant, globbing in _expansions(text, text if glob_text is None else glob_text)
    )


def _expanded_text_reaches(
    text: str, cwd: str, zone: _Zone, *, cwd_known: bool, glob_text: str
) -> bool:
    """Whether a command-like text names the zone, has a word that resolves into it (~, $VARS,
    symlinks), or has a glob that can match it, a parent of it or a path inside it, or changes
    or replaces the zone or a folder that holds it.

    `cwd_known` is False when the text may run elsewhere than `cwd` (an MCP tool's own working
    directory): a relative glob that can name the zone's directory then counts without a `cd`.

    The store's directory name counts wherever it is a whole token (`'coven-memory'`,
    `/coven-memory/`, `env -C ~ rm -rf coven-memory`, a Python path built from parts), but not
    inside a longer name (`coven-memory-notes`, `mycoven-memory`). A short, generic store name
    therefore blocks ordinary commands: doctor warns about it.

    A Bash command's globs are read from its glob text (`_globbing_code`: a quoted glob character
    is literal) and, in a heredoc body read as text, only from words at a fixed place
    (`_shell_glob_words`). Names, paths and writes are read from the text as written."""
    if _names_token(text, zone.tokens) or _names_token(_unquoted(text), zone.tokens):
        return True
    reals = [os.path.realpath(root) for root in zone.roots]
    roots = (*(root.parts for root in zone.roots), *(Path(real).parts for real in reals))
    words = _words_of(text)
    cwd_moves = (
        not cwd_known
        or bool(CHANGE_DIR.intersection(words))
        or bool(zone.parents and _names_parent(_words_of(glob_text), zone))
    )
    if cwd_moves and (
        _bash_glob_names_zone(glob_text, zone)
        if cwd_known
        else _relative_glob_names_zone(words, zone)
    ):
        return True
    globs = _shell_glob_words(glob_text) if cwd_known else words
    if any(GLOB_CHARS.intersection(word) and _glob_reaches(word, cwd, roots) for word in globs):
        return True
    if any(_in_zone(_resolve(word, cwd), zone) for word in words):
        return True
    return _changes_zone_holder(text, cwd, zone)


def _in_zone(path: str | None, zone: _Zone) -> bool:
    """Whether a resolved path is one of the zone's roots, inside one, or ends in its suffixes."""
    if path is None:
        return False
    if any(_inside(path, os.path.realpath(root)) for root in zone.roots):
        return True
    parts = Path(path).parts
    return any(parts[-len(suffix) :] == suffix for suffix in zone.suffixes)


def _blank(text: str, spans: Iterable[tuple[int, int]]) -> str:
    """`text` with each span replaced by spaces (same length)."""
    chars = list(text)
    for start, end in spans:
        chars[start:end] = " " * (end - start)
    return "".join(chars)


def _option_letters(word: _ShellWord) -> str | None:
    """The letters of a short option word (`-nE` gives `nE`); None for any other word."""
    value = word.value
    if value is None or not value.startswith("-") or value.startswith("--") or value == "-":
        return None
    return value[1:]


def _script_word(name: str, args: Sequence[_ShellWord]) -> _ShellWord | None:
    """The script file a shell, python or node runs (`bash -x f`, `python3 -I f`): its first
    operand, after short options from SCRIPT_RUNNERS only. None for any other command, any other
    option (`-c`, `-m`, `-e` make the operand code or a module) or standard input (`-`)."""
    allowed = PYTHON_SCRIPT_OPTIONS if PYTHON_NAME.fullmatch(name) else SCRIPT_RUNNERS.get(name)
    if allowed is None:
        return None
    for word in args:
        letters = _option_letters(word)
        if letters is None:
            return None if word.value in ("-", "--") else word
        if not set(letters) <= allowed:
            return None
    return None


def _sed_files(args: Sequence[_ShellWord]) -> list[_ShellWord]:
    """The files `sed` only reads: the operands after its script, when its options are only
    SED_PRINT_OPTIONS (no `-i`, no `-e` or `-f`, nothing that takes a value), its script only
    prints (SED_PRINT_SCRIPT: no `w` or `e` command), and no word holds a `$` or, after the
    script, starts with `-` (GNU sed reads an option there too)."""
    if any("$" in word.raw for word in args):
        return []
    index = 0
    while index < len(args) and (letters := _option_letters(args[index])) is not None:
        if not set(letters) <= SED_PRINT_OPTIONS:
            return []
        index += 1
    if index >= len(args):
        return []
    script, files = args[index].value, list(args[index + 1 :])
    if script is None or not SED_PRINT_SCRIPT.fullmatch(script):
        return []
    if any(_unquoted(word.raw).startswith("-") for word in files):
        return []
    return files


def _find_reads(args: Sequence[_ShellWord]) -> list[_ShellWord]:
    """Every word of a `find` that has no action (FIND_ACTION_PREFIXES: `-delete`, `-exec`,
    `-fprint` and the like) and no word holding a `$`, which may be one."""
    if any(
        "$" in word.raw or _unquoted(word.raw).startswith(FIND_ACTION_PREFIXES) for word in args
    ):
        return []
    return list(args)


def _command_reads(words: Sequence[_ShellWord]) -> list[_ShellWord]:
    """The words of one simple command that only read or run a file: a program run by its path
    (`./x`, `"/abs/x"`), every argument of READ_COMMANDS, of `find` without actions and the files
    of a printing `sed`, and the script a shell, python or node runs (`_script_word`). The
    arguments of a program that is run are never among them."""
    if not words or ASSIGNMENT.match(words[0].raw):
        return []
    name, args = words[0].value, words[1:]
    if name is None:
        return []
    if "/" in name:
        return [words[0]]
    if name in READ_COMMANDS:
        return list(args)
    if name == "find":
        return _find_reads(args)
    if name == "sed":
        return _sed_files(args)
    script = _script_word(name, args)
    return [script] if script else []


def _pipelines(commands: Sequence[_SimpleCommand]) -> Iterator[list[_SimpleCommand]]:
    """The commands in runs joined by pipes; a command with no pipe is a run of its own. Two
    pipelines with only `;` or `&&` between them may come out as one run, which reads stricter."""
    run: list[_SimpleCommand] = []
    for command in commands:
        if run and not (command.in_pipeline and run[-1].in_pipeline):
            yield run
            run = []
        run.append(command)
    if run:
        yield run


def _filters(run: Sequence[_SimpleCommand], reader: _SimpleCommand) -> bool:
    """Whether every other command of a pipeline only prints what it reads (PIPE_FILTERS), so no
    path the reader prints is acted on (`| xargs rm`, `| sh`, `| sed 's/^/rm /e'`)."""
    return all(
        command is reader or (bool(command.words) and command.words[0].value in PIPE_FILTERS)
        for command in run
    )


def _only_read(word: _ShellWord, command: str, spans: Sequence[tuple[int, int]]) -> bool:
    """Whether a lone assignment (`S=~/.claude/plugins/x`) binds a name whose every other mention
    in `command`, as a whole token in any form (`$S`, `${S}`, `export S`, a heredoc body), lies
    in `spans`, and whose value is one word that is not an option."""
    name, _, raw_value = word.raw.partition("=")
    value = _unquoted(raw_value)
    if value.startswith("-") or COMMAND_BLANKS.intersection(value):
        return False
    for match in re.finditer(rf"(?<!\w){re.escape(name)}(?!\w)", command):
        at = match.start()
        if not (word.start <= at < word.end or any(start <= at < end for start, end in spans)):
            return False
    return True


def _read_spans(command: str) -> list[tuple[int, int]]:
    """The spans of a Bash command, as written, that only read or run a file (`_command_reads`),
    for a zone a session may read: blanked, they leave what writes. A read in a pipeline counts
    only when the rest of it only prints (`_filters`). Then a lone assignment whose name is only
    used in those spans (`S=<dir>; bash $S/scripts/x`). None when the scanner cannot follow the
    command (shell_words.simple_commands): it is then read whole."""
    commands: list[_SimpleCommand] | None = _shell_words().simple_commands(command)
    if commands is None:
        return []
    spans = [
        (word.start, word.end)
        for run in _pipelines(commands)
        for reader in run
        if len(run) == 1 or _filters(run, reader)
        for word in _command_reads(reader.words)
    ]
    lone = [
        word
        for each in commands
        if each.words and all(ASSIGNMENT.match(word.raw) for word in each.words)
        for word in each.words
    ]
    return spans + [(word.start, word.end) for word in lone if _only_read(word, command, spans)]


def _bash_touches(call: Call, zone: _Zone) -> bool:
    """A command run in the zone, or whose text reaches it. For a zone a session may read
    (`writes_only`), every word that only reads or runs a file is blanked first
    (`_read_spans`): what is left reaches it only by a write."""
    cwd = os.path.realpath(call.cwd)
    if any(_inside(cwd, os.path.realpath(root)) for root in zone.roots):
        return True
    bash = _bash(call)
    code, glob_code = bash.code, _globbing_code(bash)
    if zone.writes_only:
        spans = _read_spans(bash.command)
        code, glob_code = _blank(code, spans), _blank(glob_code, spans)
    return _text_reaches(code, call.cwd, zone, cwd_known=True, glob_text=glob_code)


def _is_zone_path(text: str, cwd: str, zone: _Zone) -> bool:
    """Whether `text`, trimmed, is a path in the zone (a path may hold spaces)."""
    return _in_zone(_resolve(text.strip(), cwd), zone)


def _string_reaches(text: str, cwd: str, zone: _Zone) -> bool:
    """An MCP input string that is a path in the zone, or whose words reach it as a Bash
    command's would: an MCP tool may run a command or a query, in a directory of its own."""
    return _is_zone_path(text, cwd, zone) or _text_reaches(text, cwd, zone, cwd_known=False)


def _tool_writes(call: Call, zone: _Zone) -> bool:
    """An edit tool's file path in the zone, or any MCP input string that reaches it. An edit
    tool's `content` is never read: there free text is file content, not a command."""
    if call.tool in EDIT_TOOLS:
        return any(
            _is_zone_path(v, call.cwd, zone)
            for key in PATH_KEYS
            if isinstance(v := call.tool_input.get(key), str)
        )
    if call.tool.startswith(MCP_PREFIX):
        return any(_string_reaches(t, call.cwd, zone) for t in _strings(call.tool_input))
    return False


def _zone_refusal(zone: _Zone, role: str) -> Refused:
    return Refused(
        f"{PREFIX} a '{role}' session cannot write to {zone.label}, nor reach it through Bash or"
        " MCP tools (use Read); only the GM session may: ask the GM",
        zone.name,
    )


def _bash(call: Call) -> Bash:
    """A Bash call's command and code text; none here is a bug, and the guard blocks on it."""
    if call.bash is None:
        raise TypeError("a Bash rule was given a call without a command")
    return call.bash


def _role_variable_rule(call: Call, _store: Path, role: str, _now: datetime) -> Refused | None:
    code = _bash(call).code
    if ROLE_VAR in code or ROLE_VAR in _unquoted(code):  # `unset COVEN_S''ESSION_ROLE`
        return Refused(
            f"{PREFIX} a '{role}' session cannot change {ROLE_VAR}; ask the GM", "role-change"
        )
    return None


def _claude_code_rule(call: Call, _store: Path, role: str, _now: datetime) -> Refused | None:
    if CLAUDE_PACKAGE in _without_data(_bash(call).command):
        return Refused(
            f"{PREFIX} a '{role}' session cannot run {CLAUDE_PACKAGE}; ask the GM", "installer"
        )
    return None


def _start_claude_rule(call: Call, _store: Path, role: str, _now: datetime) -> Refused | None:
    """A claude start in the code text: data on an inert line (a note's heredoc, a commit
    message) starts nothing, and neither does the plain version query."""
    bash = _bash(call)
    if _is_version_query(bash.command):
        return None
    if _starts_claude(bash.code):
        return Refused(
            f"{PREFIX} a '{role}' session cannot start claude sessions; ask the GM to start one",
            "claude-start",
        )
    return None


def _remote_host_rule(call: Call, _store: Path, role: str, _now: datetime) -> Refused | None:
    """A remote CLI whose host is not written out in the command. No approval lifts it: a target
    pattern cannot see a host the command does not name."""
    problem = _host_problem(_bash(call).command)
    if problem is None:
        return None
    return Refused(
        f"{PREFIX} a '{role}' session cannot run {problem.cli} with a host that is not written"
        f" out in the command ({problem.why}); write the host literally",
        "remote-host",
    )


def _names_coven(word: str) -> bool:
    """Whether `word` names the coven CLI: by name, by path, as `-mcoven`, as its package
    directory or `__main__`, or as a uvx or pipx spec. In a short option cluster, what follows
    its `m` is a module name (`-Imcoven`)."""
    path = word
    if word.startswith("-") and not word.startswith("--") and MODULE_LETTER in word:
        path = word[word.index(MODULE_LETTER) + 1 :]
    for suffix in MAIN_SUFFIXES:
        path = path.removesuffix(suffix)
    return bool(COVEN_PROGRAM.fullmatch(_basename(path)))


def _arguments(words: Sequence[str], start: int) -> list[str]:
    """The words after index `start` that are not an option, a redirect or the target of a bare
    redirect operator: the subcommand a program at `start` is given, and what follows it."""
    found: list[str] = []
    target = False
    for word in words[start + 1 :]:
        if target:
            target = False
        elif REDIRECT.match(word):
            target = bool(REDIRECT_OPERATOR.fullmatch(word))
        elif not word.startswith("-"):
            found.append(word)
    return found


def _subcommand(words: Sequence[str], start: int) -> str | None:
    """The subcommand a program at `start` is given, with its own subcommand when it has them
    (`memory sync`)."""
    found = _arguments(words, start)
    if not found:
        return None
    return " ".join(found[:2]) if found[0] in NESTED_COMMANDS else found[0]


def _runnable(words: Sequence[str]) -> list[int]:
    """The indices of the words in one simple command that may run as a program: its command
    candidates, every word after a launcher candidate (`uv run`, `python -m`), and every word
    after parallel's `:::`, which it may run as commands."""
    candidates = _command_candidates(words)
    indices = list(candidates)
    # Any candidate, not only the last: in `sh -c uv run ...` uv sits in -c's value position.
    launcher = next((i for i in candidates if _launches(_basename(words[i]))), None)
    if launcher is not None:
        indices += range(launcher + 1, len(words))
    if PARALLEL in (_basename(words[index]) for index in _command_head(words).indices):
        start = next((i for i, word in enumerate(words) if word in INPUT_SOURCES), len(words))
        indices += range(start + 1, len(words))
    return indices


def _writer_at(words: Sequence[str], index: int, *, fed: bool, cut: bool) -> str | None:
    """The store-writing subcommand a program at `index` runs, UNTOLD_SUBCOMMAND when coven
    runs one the text does not show, or None. A word holding `$` counts as coven when a
    store-writing subcommand follows it (`$g setup`); coven's own subcommand is untold when it
    holds `$`, when a backtick cuts the command before it, or when xargs or parallel (`fed`)
    supplies the arguments. A coven name with an expansion glued after it
    (`coven${IFS}setup`) has an untold subcommand too."""
    word = words[index]
    name, dollar, _ = word.partition("$")
    if dollar and _names_coven(name):
        return UNTOLD_SUBCOMMAND
    sub = _subcommand(words, index)
    if not _names_coven(word):
        return sub if "$" in word and sub in STORE_WRITING_SUBCOMMANDS else None
    if sub in STORE_WRITING_SUBCOMMANDS:
        return sub
    if fed or (cut and sub is None) or (sub is not None and "$" in sub):
        return UNTOLD_SUBCOMMAND
    return None


def _segment_writer(segment: str, *, continued: bool, cut: bool) -> str | None:
    """The store-writing subcommand one simple command runs, or None. One that goes on after a
    substitution (`continued`) runs whatever that printed, so a store-writing subcommand as its
    first word counts."""
    words = _split_env_strings(_unquoted(segment).split())
    first = _subcommand(words, -1)
    if continued and first in STORE_WRITING_SUBCOMMANDS:
        return first
    head = _command_head(words).indices
    runner = next((i for i in head if _basename(words[i]) in INPUT_RUNNERS), len(words))
    for index in _runnable(words):
        found = _writer_at(words, index, fed=runner < index, cut=cut)
        if found:
            return found
    return None


def _writer_segments(text: str) -> Iterator[tuple[str, bool, bool]]:
    """Each simple command in `text`, whether it goes on after a substitution or brace word, and
    whether a backtick cuts it. xargs' `{}` is read as a word; `${NAME}`, an ANSI-C string
    (`$'\\x73etup'`) and a brace expansion (`{setup,}`) as words holding `$`, whose value the
    guard does not work out."""
    plain = BRACED_PARAMETER.sub(BRACED_STAND_IN, text.replace(PLACEHOLDER, PLACEHOLDER_WORD))
    plain = plain.replace(ANSI_C_QUOTE, BRACED_STAND_IN + "'")
    plain = BRACE_EXPANSION.sub(BRACED_STAND_IN + "{", plain)
    parts = COMMAND_PARTS.split(plain)
    for index in range(0, len(parts), 2):
        before = parts[index - 1] if index else ""
        after = parts[index + 1] if index + 1 < len(parts) else ""
        yield parts[index], before in CONTINUED, after == BACKTICK


def _named_writer(text: str) -> str | None:
    """A store-writing subcommand named as a word in `text` that also names coven, or None."""
    tokens = [word for word in SHELL_SPLIT.split(_unquoted(text)) if word]
    pairs = (f"{first} {second}" for first, second in zip(tokens, tokens[1:], strict=False))
    named = sorted({*tokens, *pairs} & STORE_WRITING_SUBCOMMANDS)
    if named and any(_names_coven(word) for word in tokens):
        return named[0]
    return None


def _candidate_expands(text: str) -> bool:
    """Whether a command candidate of a simple command in `text` holds `$` (`$g $s`)."""
    for segment in COMMAND_SPLIT.split(text):
        words = _unquoted(segment).split()
        if any("$" in words[index] for index in _command_candidates(words)):
            return True
    return False


def _line_writer(text: str) -> str | None:
    """A store-writing subcommand named in `text` with coven, where the line may assemble the
    call from what it names: a command candidate holds `$` (`g=coven s=setup; $g $s`), or a
    shell runs what stdin holds (`echo 'coven setup' | sh`)."""
    named = _named_writer(text)
    if named is None:
        return None
    if _candidate_expands(text):
        return named
    if any(_runs_stdin(segment, fed) for segment, fed in _fed_segments(text)):
        return named
    return None


def _view_writer(text: str) -> str | None:
    found = (
        _segment_writer(segment, continued=continued, cut=cut)
        for segment, continued, cut in _writer_segments(text)
    )
    return next(filter(None, found), None) or _line_writer(text)


def _store_writer(code: str) -> str | None:
    """The store-writing coven subcommand `code` runs (UNTOLD_SUBCOMMAND when it runs coven
    with one it does not show), or None. Read with line continuations kept and joined, as the
    claude-start check reads them."""
    views = (code, code.replace(LINE_CONTINUATION, ""))
    return next(filter(None, (_view_writer(view) for view in views)), None)


def _store_writer_rule(call: Call, _store: Path, role: str, _now: datetime) -> Refused | None:
    """coven approve, setup and memory adopt|init|sync in a code position: they change the memory
    store or coven's config."""
    sub = _store_writer(_bash(call).code)
    if sub is None:
        return None
    return Refused(
        f"{PREFIX} a '{role}' session cannot run coven {sub}: it changes the memory store or"
        " coven's config; ask the GM",
        "coven-write",
    )


def _zone_rule(call: Call, store: Path, role: str, _now: datetime) -> Refused | None:
    """A Bash command that runs in, or whose text reaches, a place a restricted session may not
    write."""
    for zone in _zones(store):
        if _bash_touches(call, zone):
            return _zone_refusal(zone, role)
    return None


# A restricted session's Bash checks, in order: the first refusal wins. New checks are new rules.
BASH_RULES: tuple[Rule, ...] = (
    _role_variable_rule,
    _claude_code_rule,
    _start_claude_rule,
    _remote_host_rule,
    _store_writer_rule,
    _zone_rule,
)


def _bash_refusal(call: Call, store: Path, role: str, now: datetime) -> Refused | None:
    """Why a restricted session may not run this Bash command: the first rule that refuses it."""
    if call.bash is None:
        return None
    for rule in BASH_RULES:
        refusal = rule(call, store, role, now)
        if refusal:
            return refusal
    return None


def _tool_refusal(call: Call, store: Path, role: str, _now: datetime) -> Refused | None:
    """Why a restricted session may not make this non-Bash call: it writes or reaches the memory
    store, coven's config, or Claude Code's settings and plugins."""
    if call.bash is not None:
        return None
    for zone in _zones(store):
        if _tool_writes(call, zone):
            return _zone_refusal(zone, role)
    return None


# The rules only a restricted session meets, tried in order before the target rule; each returns
# the refusal or None. No approval lifts one.
RESTRICTED_RULES: tuple[Rule, ...] = (_bash_refusal, _tool_refusal)


# --- the decision ---


def _restricted_refusal(call: Call, store: Path, role: str, now: datetime) -> Refused | None:
    for rule in RESTRICTED_RULES:
        refusal = rule(call, store, role, now)
        if refusal:
            return refusal
    return None


def _store_problem(store: Path, exc: StoreError) -> str:
    return f"cannot read the committed memory store at {store} ({exc})"


def _unreadable_store(store: Path, exc: StoreError) -> Refused:
    return Refused(
        f"{PREFIX} {_store_problem(store, exc)}; Bash and MCP tools are blocked until git can"
        " read it: run coven doctor",
        "unreadable-store",
    )


def _no_valid_guard(store: Path, exc: GuardError) -> Refused:
    return Refused(
        f"{PREFIX} {exc}; no earlier committed version is valid, so only reads work until the"
        f" user fixes or reverts it in their own terminal (git -C {store} revert HEAD)",
        "invalid-guard",
    )


def _fallback_note(enforced: Enforced) -> str:
    if enforced.invalid_head is None:
        return ""
    return (
        f"; the committed {GUARD_FILE} is invalid, so its last valid version is enforced"
        f" ({enforced.invalid_head})"
    )


def _triggers(call: Call, target: Target) -> Iterator[tuple[Literal["command", "tool"], str]]:
    """How this call reaches `target`: the first pattern its code text matches, and its tool name."""
    if call.bash is not None:
        code = call.bash.code
        pattern = next((p for p in target["match"] if re.search(p, code)), None)
        if pattern is not None:
            yield "command", pattern
    if call.tool in target["tools"]:
        yield "tool", call.tool


def _grants(approval: Approval, hit_system: str, via: str, call: Call, now: datetime) -> bool:
    """Whether a live approval of `hit_system` names the exact command (stripped) or tool."""
    if approval.system != hit_system or not is_live(approval, now):
        return False
    if via == "tool":
        return call.tool in approval.tools
    return call.bash is not None and call.bash.command.strip() in approval.commands


def _hits(call: Call, targets: Sequence[Target], store: Path, now: datetime) -> list[Hit]:
    """Every target the call reaches. Approvals are read only when it reaches one: StoreError."""
    reached = [(t, via, trigger) for t in targets for via, trigger in _triggers(call, t)]
    if not reached:
        return []
    found = approvals(store)
    return [
        Hit(
            target["system"],
            target["kind"],
            via,
            trigger,
            next((a for a in found if _grants(a, target["system"], via, call, now)), None),
        )
        for target, via, trigger in reached
    ]


def _target_refusal(hits: Sequence[Hit], note: str) -> Refused | None:
    """Why the call may not reach the targets it hits: no live approval names it for each."""
    needed = dict.fromkeys(
        (h.system, f"'{h.system}' is {h.kind}") for h in hits if h.approval is None
    )
    if not needed:
        return None
    return Refused(
        f"{PREFIX} {' and '.join(text for _system, text in needed)}; this exact step needs the"
        f" user's approval (send a PROD-STEP; the GM approves it with coven approve){note}",
        ",".join(dict.fromkeys(system for system, _text in needed)),
    )


@dataclass(frozen=True)
class _Reading:
    """What the store says about one call."""

    hits: list[Hit]
    note: str = ""
    everyone: Refused | None = None  # the refusal every session meets
    restricted_only: Refused | None = None  # the refusal only a restricted session meets
    unreadable: str | None = None  # why git could not read the store


def _no_valid_guard_reading(call: Call, store: Path, exc: GuardError) -> _Reading:
    """While no version of guard.json is valid, reads work; an unrestricted session may also edit,
    so the file can be repaired (a restricted one may not); everything else is refused."""
    if call.tool in READ_TOOLS:
        return _Reading([])
    refusal = _no_valid_guard(store, exc)
    if call.tool in EDIT_TOOLS:
        return _Reading([], restricted_only=refusal)
    return _Reading([], everyone=refusal)


def _read_store(call: Call, store: Path, now: datetime) -> _Reading:
    out = _reaches_out(call)
    try:
        enforced = load_targets(store)
    except StoreError as exc:
        return _Reading(
            [],
            everyone=_unreadable_store(store, exc) if out else None,
            unreadable=_store_problem(store, exc),
        )
    except GuardError as exc:
        return _no_valid_guard_reading(call, store, exc)
    if enforced.targets is None:
        missing = Refused(
            f"{PREFIX} {GUARD_FILE} is missing from the committed store; ask the user",
            "missing-guard",
        )
        return _Reading([], restricted_only=missing if out else None)
    note = _fallback_note(enforced)
    try:
        hits = _hits(call, enforced.targets, store, now)
    except StoreError as exc:
        # Only a call that reaches a target reads approvals: refuse it, whatever tool it is.
        return _Reading(
            [], note, everyone=_unreadable_store(store, exc), unreadable=_store_problem(store, exc)
        )
    return _Reading(hits, note, everyone=_target_refusal(hits, note))


def _verdict(everyone: Refused | None, restricted: Refused | None) -> Verdict:
    if everyone:
        return "refused for every session"
    return "refused for a PM" if restricted else "allowed"


def explain(
    payload: Mapping[str, object], store: Path, role: str | None, now: datetime
) -> Explanation:
    """What the guard decides for this tool call and why; PayloadError on a bad payload.

    `role` is COVEN_SESSION_ROLE: None (unset) is an unrestricted session; any value, empty or
    unknown included, restricts it. `restricted` is the refusal a restricted session meets even
    when `role` is None, and `message` is the refusal this session meets."""
    call = _call(payload)
    reading = _read_store(call, store, now)
    rule = _restricted_refusal(call, store, ASSUMED_ROLE if role is None else role, now)
    restricted = rule or reading.restricted_only
    chosen = restricted if role is not None and restricted else reading.everyone
    return Explanation(
        call,
        reading.hits,
        restricted.message if restricted else None,
        reading.note,
        chosen.message if chosen else None,
        _verdict(reading.everyone, restricted),
        reading.unreadable,
        chosen.system if chosen else None,
    )


def explain_without_config(
    payload: Mapping[str, object], exc: ConfigError, role: str | None
) -> Explanation:
    """What the guard decides for a tool call when coven's config cannot be read: a restricted
    session loses Bash and MCP tools, any other goes on as if the guard were off."""
    call = _call(payload)
    refusal = _config_refusal(exc, call)
    chosen = refusal if role is not None else None
    return Explanation(
        call,
        [],
        refusal.message if refusal else None,
        "",
        chosen.message if chosen else None,
        _verdict(None, refusal),
        str(exc),
        chosen.system if chosen else None,
    )


def decide(
    payload: Mapping[str, object], store: Path | None, role: str | None, now: datetime
) -> str | None:
    """The refusal for this tool call, or None to allow it; PayloadError on a bad payload.
    `store` None means the guard is off."""
    if store is None:
        _call(payload)
        return None
    return explain(payload, store, role, now).message


# --- the hook ---


def _payload(stdin: TextIO) -> dict[str, object]:
    try:
        payload = hook_support.loads(stdin.read())
    except ValueError as exc:
        raise PayloadError(str(exc)) from None
    if not isinstance(payload, dict):
        raise PayloadError("payload is not an object")
    return payload


def role_class(role: str | None) -> str:
    """`unset`, `pm` or `other`: what the log says about the session, never its raw role."""
    return "unset" if role is None else "pm" if role == "pm" else "other"


def _record(found: Explanation, role: str | None) -> None:
    """Log the decision. The log is best effort: a broken one must not change the answer."""
    command = found.call.bash.command if found.call.bash else None
    record = {
        "decision": "allow" if found.message is None else "deny",
        "role": role_class(role),
        "system": found.system,
        "tool": found.call.tool,
        "command": command if command is None else command[:LOGGED_COMMAND_LIMIT],
        "message": found.message,
    }
    try:
        hook_support.sibling("hooklog").log_event(HOOK, record)
    except Exception:
        logging.exception("coven %s: could not write the hook log", HOOK)


def _refuse_bad_input(exc: PayloadError) -> int:
    print(f"{PREFIX} could not parse hook input ({exc}); blocking", file=sys.stderr)
    hook_support.log_failure(HOOK, f"could not parse hook input ({exc})")
    return 2


def _guard(store: Path, role: str | None, stdin: TextIO) -> int:
    try:
        found = explain(_payload(stdin), store, role, datetime.now(UTC))
    except PayloadError as exc:
        return _refuse_bad_input(exc)
    _record(found, role)
    if found.message:
        print(found.message, file=sys.stderr)
        return 2
    return 0


def _config_refusal(exc: ConfigError, call: Call) -> Refused | None:
    """Why a restricted session may not make this call while the config cannot be read."""
    if not _reaches_out(call):
        return None
    return Refused(
        f"{PREFIX} {CONFIG_ERROR} ({exc}); Bash and MCP tools are blocked in this session: run"
        " coven doctor",
        "unreadable-config",
    )


def _unreadable_config(exc: ConfigError, role: str | None, stdin: TextIO) -> int:
    """A config that cannot be read: a restricted session loses Bash and MCP tools, any other
    session goes on as if the guard were off."""
    hook_support.log_failure(HOOK, f"{CONFIG_ERROR}: {exc}")
    if role is None:
        return 0
    try:
        call = _call(_payload(stdin))
    except PayloadError as bad:
        return _refuse_bad_input(bad)
    refusal = _config_refusal(exc, call)
    if refusal is None:
        return 0
    print(refusal.message, file=sys.stderr)
    return 2


def _check(argv: Sequence[str]) -> int:
    """`--check <guard.json>`: 0 and `ok: N targets` for a valid file, 1 and the reason if not."""
    if len(argv) != 1:
        print(f"{PREFIX} {USAGE}", file=sys.stderr)
        return 2
    try:
        print(check_file(Path(argv[0])))
    except GuardError as exc:
        print(f"{PREFIX} {exc}", file=sys.stderr)
        return 1
    return 0


def run(argv: Sequence[str], stdin: TextIO, environ: Mapping[str, str]) -> int:
    if argv[:1] == [CHECK_FLAG]:
        return _check(argv[1:])
    if argv:
        print(f"{PREFIX} {USAGE}; blocking", file=sys.stderr)
        return 2
    role = environ.get(ROLE_VAR)
    try:
        config = read_config()
    except ConfigError as exc:
        return _unreadable_config(exc, role, stdin)
    if off_reason(config) is not None:
        return 0
    return _guard(config.store, role, stdin)


def _checked(argv: Sequence[str], stdin: TextIO, environ: Mapping[str, str]) -> int:
    """run() under the deadline, disarmed before anything else; any other error blocks the call."""
    signal.setitimer(signal.ITIMER_REAL, DEADLINE)  # interrupts re.search and git waits alike
    try:
        try:
            return run(argv, stdin, environ)
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
    except DeadlineError:
        raise
    # Fail closed on any error (closed stdin, a broken sibling module, a bug): Claude treats every
    # exit code but 2 as non-blocking.
    except Exception as exc:
        print(f"{PREFIX} internal error ({exc!r}); blocking", file=sys.stderr)
        logging.exception("coven %s: internal error; blocking", HOOK)
        hook_support.log_failure(HOOK, f"internal error ({exc!r})")
        return 2


def _too_slow() -> int:
    print(TOO_SLOW, file=sys.stderr)
    hook_support.log_failure(HOOK, "the check took too long")
    return 2


def main(
    argv: Sequence[str] | None = None,
    stdin: TextIO | None = None,
    environ: Mapping[str, str] | None = None,
) -> int:
    previous = signal.signal(signal.SIGALRM, _past_deadline)
    try:
        return _checked(
            sys.argv[1:] if argv is None else argv,
            stdin or sys.stdin,
            os.environ if environ is None else environ,
        )
    # The one-shot alarm can land anywhere in _checked, its error handler included: still block.
    except DeadlineError:
        return _too_slow()
    finally:
        signal.signal(signal.SIGALRM, previous)  # _checked disarmed the timer already


def cli() -> int:
    """main() for the command line, with a last catch: an uncaught exception would exit 1, which
    lets the call through."""
    try:
        return main()
    except DeadlineError:
        return _too_slow()


if __name__ == "__main__":
    sys.exit(cli())
