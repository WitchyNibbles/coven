#!/usr/bin/env python3
"""PreToolUse hook: refuse tool calls that reach production or gated systems unless the user
approved that exact command.

Reads the hook payload on stdin and runs under fail-closed.sh like deny-secrets. Exit 2 refuses the
call and shows stderr to the agent; exit 0 lets it through. `prod_guard.py --check <guard.json>`
validates a file with the same rules before it is committed: it prints `ok: N targets` and exits 0,
or prints the reason and exits 1; it never reads stdin.

When it acts. The hook reads coven's config (`~/.config/coven/config.json`, as plain JSON, the way
memory-context does) for `memory_path` (default `~/coven-memory`, and it must be absolute) and
`guard`, whose only valid value is `"required"`. It is off, and exits 0 at once with no git call and
no subprocess, when there is no `"guard": "required"` and either no store folder or no
`systems/guard.json` in its working tree. With the latch a missing store folder is not off: Bash
and MCP tools are refused for every session, since the store held a guard.json once. With no
config file the store is the default one, so deleting the file does not switch off a store that
holds a guard.json. It is on otherwise. A config that exists but cannot be read or is invalid
refuses Bash and MCP tools in a restricted session (the message points to `coven doctor`) and
leaves any other session as if the guard were off.

Who it restricts. The target rule applies to every session. A session whose COVEN_SESSION_ROLE is
set to any value, an empty or unknown one included, is also restricted: only an unset variable is
unrestricted. A subagent's call is judged like its session's. A restricted session meets the rules
in RESTRICTED_RULES first, and no approval lifts one. It may not:
- write the memory store, coven's config file (`~/.config/coven/config.json`, the one the hook
  reads), Claude Code's settings (`settings.json` and `settings.local.json` in CLAUDE_CONFIG_DIR or
  `~/.claude`, and in any folder's `.claude/`), its `plugins/` folder, or the rest of its user
  folder (CLAUDE_CONFIG_DIR or `~/.claude`: skills, agents, commands, memory) and `~/.claude.json`:
  Write, Edit, MultiEdit and NotebookEdit under any of them, any MCP input that names one, and any
  Bash command that runs in one or whose text reaches it, by name, by a path that resolves into it
  (`~`, `$HOME`, symlinks), by a glob that can match it, or, after a `cd`, `env -C`, `git -C` or
  `find` in its holding folder, by its plain name. A glob character in quotes or after a backslash
  is literal (`ls '.*'`, `grep -E "[0-9]*"`), but only on a line of plain data commands
  (GLOB_LITERAL_COMMANDS, `find` without actions, a `sed` whose script, read command by command in
  each way a sed may, runs and writes nothing) with no unquoted `$`; a heredoc body copied by `cat`
  or read by python or node (by name or from /usr/bin or /bin) with no glob call is read only for
  words at a fixed place. The user folder has no token (a `.claude` token would refuse writes in a
  repo's own `.claude/worktrees/`, where Claude Code keeps its worktrees); it is found by path,
  glob, a `cd` into it or a holder write (`rm -rf ~`), and it is checked last, so a settings or
  plugins refusal names its own zone. In Bash the `plugins/` folder and the user folder may be read
  and their scripts run (READ_COMMANDS, `find` without actions, a printing `sed` and its operands by
  a folder variable bound at the start of the line, `bash` or python with a script, a program by its
  path, `~/...` included, a lone `S=<dir>` used only there, a pipe into PIPE_FILTERS or a sed that
  only prints); any other use of it counts as a write. For the user folder only
  (CLAUDE_FOLDER_READERS, `_Zone.copy_reads`, `_Zone.cd_reads`, `_Zone.loop_reads`,
  `_Zone.shell_reads`): `rg` without `--pre` or `--hostname-bin`, `jq`, `echo`, `printf`, `sleep`,
  `sort` (not `-o`), `uniq`, `cut`, `tr`, `column`, `[` and `test` read too; the sources of `cp -a
  SRC DEST` and `tar -cf ARCHIVE SRC` are reads (the destination and the archive are not), and so is
  `cp` to a place outside it; and a text where every command only reads writes nothing wherever a
  `cd` goes (or wherever the session runs), so it is allowed whole (`_only_reads_text`): in it a
  command may sit behind `env`, `timeout`, `nice`, `command`, an assignment of a plain name or a
  reader's path in /usr/bin or /bin; a pipe joins readers and filters; a redirection may go to
  `/dev/null`, a file descriptor or a file outside the zone (`> /var/tmp/x`), or read from a file
  (`wc -l < f`, `while read f; do ...; done < f`); a `$(...)` of readers may stand in an argument or
  a plain assignment; a loop, an `if`, a `{ ...; }` group, `break`, `exit` and `sh -c 'STRING'` of
  such a text read too. The commands that are left when the reads are blanked (`_read_spans`) are
  checked for a way to the folder, a name bound to a path in the folder by a lone assignment
  included (`F=~/.claude/x; cat $F`). A heredoc fed to `cat > FILE` or `tee FILE`, FILE outside the
  zone and the body quoted, is file content. Code that an interpreter (INTERPRETERS, python) runs
  from a word, standard input or a heredoc is a read of nothing: when it names the folder, or looks
  up the home folder, the call is refused (`_interpreter_names_zone`); so is an `xargs` that runs
  more than a reader (`_xargs_prints_only`), a read that writes a file the same text may run, or
  beside any command that is no reader (`_feeds_code`), and a read in a text that sets what its
  programs read (`_arms_reads`: `export TAR_OPTIONS=...`). A written word the guard cannot pin to
  a path fails closed when it may lead to the folder (`_writes_unpinned`: `${HOME%/}/.claude`,
  `~/$N`, `$X/..`), and so does any write under /proc or /dev but the streams and the terminal
  (`_through_alias`). After a `cd` every word left is read from its folder (`_names_inside`), and a
  `cd` the scanner cannot follow is read from the text (`_written_cds_clear`). `~/.claude.json`'s
  siblings (`.backup`) are in the folder's zone. `cd ~/.claude && ls skills` runs, `cd ~/.claude &&
  rm -rf skills` does not. A restricted session may write in its own scratch folders there
  (`_with_own`): its job folder, the hook's own CLAUDE_JOB_DIR right under `jobs/`, and its session
  folder beside the payload's transcript, but not that one's `subagents/`, nor the folders
  themselves, nor a copy or unpacking into the session folder itself. Read, Grep and Glob stay
  allowed;
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

Known limits of the restricted rules: a path built in code (`python3 -c "...'~/cov'+'en-memory'"`)
is not read; in the user folder's zone a written `${...}`, `$X` or `$(...)` that may lead there
fails closed (`_writes_unpinned`), as do a loop and an `xargs` over it, but a plain `$D/x` stays
allowed (`D=$(echo ~/.cla)ude; rm -rf $D/skills` gets through).

Limits. The whole check has a deadline of DEADLINE seconds and each git call a timeout; past either,
the call is refused. The guard is best effort against a session that follows its role: its rules are
public, and the committed store is its boundary. A process running as the same OS user can forge the
store's state, and the user's own terminal outside Claude Code is never guarded.

`decide` is built on `explain`, which also returns what `coven guard explain` shows (the hits, their
approvals, the restricted refusal and a verdict), so the two cannot disagree.
"""

import fnmatch
import functools
import importlib
import ipaddress
import logging
import os
import re
import signal
import subprocess
import sys
import urllib.parse
from collections import Counter
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
SHELL_STRING_RUNNERS = frozenset({"sh", "bash", "zsh", "dash"})  # `sh -c STRING`, read as a text
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
BRACE_OR_GLOB = frozenset("{*?[")  # unquoted, a word may expand to other words
# A script operand that expands, but only to one path: `~/x`, `$S/x`, `"$CLAUDE_PLUGIN_ROOT/x"`.
SCRIPT_PATH = re.compile(r'(?P<q>"?)(?:~|\$[A-Za-z_]\w*)(?:/[\w./@+:,%=-]*)?(?P=q)')
# Ways the shell hands a command's words to a later one: the last argument, a trap's command.
WORDS_HANDED_ON = re.compile(
    r"\$\{?_(?!\w)|\b(?:BASH_COMMAND|BASH_ARGV|ZSH_EXECUTION_STRING|trap|fc|history)\b"
)
GROUP_CLOSERS = frozenset({"}", "done", "fi", "esac"})  # a compound command's last word
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
# A `cd` or `pushd` word of a text, quotes and all, bounded as SHELL_SPLIT bounds a word ...
CD_WORD = re.compile(r"(?<![^\s;&|<>()`'\"=,{}])(?:cd|pushd)(?![^\s;&|<>()`'\"=,{}])")
# ... and the one operand it may have for `_written_cds_clear`: a literal path from `/` or `~/`
# (a quoted `~` is literal: not from `~/`), maybe in quotes, that no other text goes on with and
# no other operand follows (zsh's `cd old new` swaps a part of the folder) before the command ends.
CD_OPERAND = re.compile(
    r"[ \t]+(?:--[ \t]+)?(?:(?P<q>['\"])(?P<quoted>/[\w./@+%:,=-]*)(?P=q)"
    r"|(?P<bare>[/~][\w./@+%:,=-]*))(?=['\"`]?[ \t]*(?:$|[\n;&|)]))"
)
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
# One unit of a glob: a `[...]` class, a backslash escape, or any other character.
GLOB_UNIT = re.compile(r"\[[!^]?\]?[^\]]*\]|\\.|.", re.DOTALL)
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
# Commands that take every quoted word as data: none expands a glob in one or runs one as code, so
# a glob character in quotes is literal on a line of these alone (`_quotes_stay_literal`; find,
# sed and rg by their own checks). Any other command, a wrapper or a keyword (`for`, `do`) keeps
# the strict reading.
GLOB_LITERAL_COMMANDS = frozenset(
    {
        *("cd", "pwd", "ls", "cat", "head", "tail", "wc", "grep", "egrep", "fgrep", "echo"),
        *("printf", "cut", "tr", "sort", "uniq", "nl", "tac", "jq", "diff", "cmp", "stat"),
        *("test", "[", "true", "false", ":"),
    }
)
# GNU sed: short options that take no value (`-i` takes the rest of its word as a suffix), and
# long ones. Any other (`-f`, `-l`, `--file`, `--posix`, an abbreviation) stops the reading.
SED_FLAG_LETTERS = frozenset("nErsuzb")
SED_LONG_FLAGS = frozenset(
    {"quiet", "silent", "regexp-extended", "separate", "unbuffered", "null-data", "binary"}
)
# GNU sed commands, by what follows their letter. `e`, `w`, `W` (run a command, write a file),
# `v` and any other letter are none of these: the script counts as one that may run.
SED_BARE_COMMANDS = frozenset("=dDgGhHnNpPxzF}")
SED_NUMBER_COMMANDS = frozenset("lLqQ")  # an optional number
SED_TEXT_COMMANDS = frozenset("aic")  # text to the end of the line, `\` + newline going on
SED_FILE_COMMANDS = frozenset("rR")  # a file name to the newline, a backslash or not
SED_LABEL_COMMANDS = frozenset("btT:")
SED_SUBSTITUTE_FLAGS = frozenset("gpiImM0123456789")  # not `e` (run) or `w` (write)
SED_COMMAND_ENDS = frozenset({"", ";", "\n", "}", "#"})
# Calls that expand a glob in a python or node heredoc body: such a body is read strictly.
GLOB_CALLS = ("glob", "fnmatch", "__import__", "importlib")  # an import by a built name too
BODY_COPIERS = frozenset({"cat", "tee"})  # read a heredoc body only to copy it
# Where a heredoc reader named by its path is the program its name says: a system bin folder a
# session cannot write (not `~/`, nor `/usr/local/bin/`, which Homebrew leaves writable).
TRUSTED_READER_FOLDERS = ("/usr/bin/", "/bin/")
CONDITIONAL_MARKS = frozenset("&|(")
BINDING = re.compile(r"([A-Za-z_]\w*)(\+?)=")
# A sed operand by a folder variable (`$CM/a.md`, `"$CM/a.md"`), and the plain path such a
# variable may be bound to (`/x`, `~/x`, `$HOME/x`: no blank, quote or glob); see _bound_folders.
FOLDER_OPERAND = re.compile(r'(?P<q>"?)\$(?P<name>[A-Za-z_]\w*)(?:/[\w./@+:,%=-]*)?(?P=q)')
FOLDER_VALUE = re.compile(r"(?:~|\$HOME)?/[\w./@+:,%=-]*")
# A bare mention of IFS or HOME (not `$IFS`) may set it: `$CM` may then split into an option.
SPLIT_VARIABLE = re.compile(r"(?<![\w$])(?:IFS|HOME)(?!\w)")
# Builtins that set a variable by a name given as a word, which may be built at run time
# (`read $n$m`): a line with one binds no folder variable.
NAME_SETTERS = frozenset(
    {
        "read",
        "printf",
        "print",
        "mapfile",
        "readarray",
        "getopts",
        "let",
        "typeset",
        "readonly",
        "unset",
        "wait",
        "coproc",
        "source",
        ".",
        "declare",
        "export",
        "local",
        "eval",
        "set",
    }
)
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
# Claude Code's user folder as a whole, and its state file in the home folder: what sits at the top
# of the folder, for a bare `*` before one after a cd.
CLAUDE_JSON = ".claude.json"
CLAUDE_FOLDER_ENTRIES = (
    *("CLAUDE.md", "settings.json", "settings.local.json", "keybindings.json", ".credentials.json"),
    *("agents", "skills", "commands", "hooks", "output-styles", "plugins", "projects", "todos"),
    *("history.jsonl", "shell-snapshots", "statsig"),
)
# A session's own scratch folders in the user folder: its background job's folder, which Claude
# Code names in the hook's CLAUDE_JOB_DIR (`<folder>/jobs/<id>`), and the folder beside its
# transcript (`<folder>/projects/<project>/<session id>.jsonl`), but for its subagents' transcripts.
JOB_VARIABLE = "CLAUDE_JOB_DIR"
JOBS_FOLDER = "jobs"
PROJECTS_FOLDER = "projects"
TRANSCRIPT_SUFFIX = ".jsonl"
SUBAGENTS_FOLDER = "subagents"
SESSION_ID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")
JOB_NAME = re.compile(rf"(?<!\w){JOB_VARIABLE}(?!\w)")
JOB_REFERENCE = re.compile(rf"\$\{{{JOB_VARIABLE}\}}|\${JOB_VARIABLE}(?!\w)")
# What may stand right before and right after a `$CLAUDE_JOB_DIR` that starts a word and expands
# as the hook has it: not zsh's `$CLAUDE_JOB_DIR:h` or `$CLAUDE_JOB_DIR[1]`, nor `x$CLAUDE_JOB_DIR`.
JOB_WORD_BEFORE = frozenset(' \t\n"=<>(;|&')
JOB_WORD_AFTER = frozenset("/ \t\n\"';&|)<>")
# What a written word may hold that the guard does not follow (`_may_name_zone`): a parameter,
# command or arithmetic expansion, a backtick, or a brace expansion.
UNPINNED_EXPANSION = re.compile(r"\$|`|\{[^{}]*(?:,|\.\.)[^{}]*\}")
# The text of those expansions, each read as a glob's `*` to see what a word may spell
# (`.cl${X}aude` may be `.claude`).
EXPANSION_TEXT = re.compile(r"\$\{[^}]*\}|\$\([^)]*\)|`[^`]*`|\$\w+|\$.|[{},]")
# A written word's leading current folder, which the guard knows where the text's `cd`s go.
CURRENT_FOLDER = re.compile(r"^(?:\$PWD|\$\{PWD\}|~\+)(?=/|$)")
# Folders whose paths stand for others (`/proc/self/cwd`, `/dev/fd/3`), and the files in them a
# write may still go to: a stream or the terminal.
ALIAS_FOLDERS = ("/proc", "/dev")
DEV_WRITABLE = frozenset({"/dev/null", "/dev/stdout", "/dev/stderr", "/dev/tty"})
# A brace expansion (`{a,b}`, `{1..3}`, nested too): the most words the guard reads one word as
# (`_brace_members`), a range's text, and a word of a text that holds a brace.
BRACE_MEMBERS = 64
BRACE_RANGE = re.compile(r"(-?\d+)\.\.(-?\d+)(?:\.\.(-?\d+))?|(\D)\.\.(\D)(?:\.\.(-?\d+))?")
# A word that holds a `{`, matched only from its start: a long word is scanned once.
BRACE_WORD = re.compile(r"(?<![^\s;&|()<>`])[^\s;&|()<>`]*\{[^\s;&|()<>`]*")
BRACE_OPENER = re.compile(r"(?<!\$)\{")
# What `_glued_expansions` keeps in an expansion's word: blanks, separators, parentheses, braces
# and backticks, as private-use characters.
GLUED = str.maketrans({char: chr(0xE000 + at) for at, char in enumerate(" \t\n;&|(){}`<>")})
UNGLUED = str.maketrans({chr(0xE000 + at): char for at, char in enumerate(" \t\n;&|(){}`<>")})
# What the user folder's zone adds to READ_COMMANDS: `rg` (it writes no file), and the commands
# that only print or do nothing, which a path may follow (`echo $CLAUDE_CONFIG_DIR`). A reader of
# PROGRAM_READERS has options that run a program (`rg --pre CMD` runs CMD on each file).
CLAUDE_FOLDER_READERS = frozenset(
    {
        *("rg", "jq", "echo", "printf", "true", "false", "pwd", "sleep", ":"),
        *("break", "continue", "exit"),  # a loop's or a text's end
        *("sort", "uniq", "cut", "tr", "column"),
    }
)
PROGRAM_READERS = frozenset({"rg"})
PROGRAM_OPTIONS = ("--pre", "--hostname-bin")
# The options of `cp` that neither link, name a folder nor take a value, and of `tar` that create
# an archive of its operands: `cp -a SRC DEST` and `tar -cf ARCHIVE SRC` read their sources.
COPY_OPTIONS = frozenset("arRpvfniuPHxdT")
ARCHIVE_OPTIONS = frozenset("czjJvfpPha")
RUNS_COMMAND = re.compile(r"\$\(|`|[<>]\(")  # a command substitution or a process substitution
# Commands whose code is a word, a file's body or standard input: the guard cannot tell what that
# code writes, so a zone with `shell_reads` refuses it when it names the zone (python by
# PYTHON_NAME).
INTERPRETERS = frozenset(
    {*("node", "perl", "ruby", "awk", "gawk", "mawk", "nawk", "sqlite3", "php", "lua", "deno")}
)
# Commands that make the paths they are given, outside CHANGES_OPERANDS.
PATH_CREATORS = frozenset({"touch", "mkdir"})
# What an `xargs` that only prints may run, for the user folder zone: readers that no option or
# operand xargs appends makes write or run anything (not sed, sort, find, whose options GNU reads
# anywhere: an appended `-i`, `-o x` or `-delete`) ...
XARGS_READERS = frozenset(
    {*("cat", "head", "tail", "wc", "ls", "stat", "grep", "egrep", "fgrep", "basename")}
    | {*("dirname", "realpath", "readlink", "du", "md5sum", "sha1sum", "sha256sum", "echo")}
)
# ... and the xargs options it may have: flags, and options with a value (glued or the next word).
# Any other (`-I`, `-i`, `--replace`, `-a`, `-E`, `-p`) stops the reading.
XARGS_FLAGS = frozenset("0rtx")
XARGS_VALUED = frozenset("nLPsd")
XARGS_LONG_FLAGS = frozenset({"null", "no-run-if-empty", "verbose", "exit"})
XARGS_LONG_VALUED = frozenset({"max-args", "max-lines", "max-procs", "max-chars", "delimiter"})
# What runs code from a file or standard input, by name: a file a read writes in the same text may
# be what it runs (`echo 'rm -rf ~/.claude' > /tmp/s; sh /tmp/s`), as may a program by its path.
CODE_RUNNERS = frozenset(
    {*SHELL_READERS, *INTERPRETERS, *SCRIPT_RUNNERS, "xargs", "exec", "nohup", "setsid", "env"}
    | {*("stdbuf", "watch", "flock", "chrt", "ionice", "taskset", "strace", "ltrace")}
)
# How code finds a home folder without writing it down: with `.claude` in the same code, it names
# the zone.
HOME_LOOKUP = re.compile(r"expanduser|Path\.home|homedir|HOME|getpwuid|getpass")
# A path with a `..` component, which may lead out of the folder a `cd` went to.
UP_PATH = re.compile(r"(?:^|/)\.\.(?:/|$)")
# A redirection target that is a file descriptor.
DESCRIPTOR_TARGET = re.compile(r"\d+|-")
# A variable a read may set (a loop's, `read`'s, an assignment's): a plain name that no program
# or the shell reads for what it does (the search path, the field separator, the home folder, a
# start-up file, the loader). zsh ties a lower-case array to some of them (`path` is PATH), so
# those count in both cases.
PLAIN_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
ZSH_TIED_NAMES = ("PATH", "CDPATH", "FPATH", "MANPATH", "MODULE_PATH", "MAILPATH", "FIGNORE")
# zsh runs NULLCMD for a redirection with no command (`< f`, a bare heredoc), READNULLCMD for a
# lone `< f`; it runs a prompt's `$(...)`, the functions its hook arrays name, and reads ZDOTDIR's
# startup files in a zsh it starts.
NULL_COMMANDS = ("NULLCMD", "READNULLCMD")
ZSH_HOOK_NAMES = (
    *("precmd_functions", "preexec_functions", "chpwd_functions", "periodic_functions"),
    *("zshaddhistory_functions", "zshexit_functions", "ZDOTDIR"),
    *("PROMPT", "PROMPT2", "PROMPT3", "PROMPT4", "RPROMPT", "RPROMPT2", "RPS1", "RPS2"),
)
BLOCKED_NAMES = frozenset(
    {*("PATH", "IFS", "HOME", "CDPATH", "ENV", "SHELL", "PWD", "OLDPWD", "TMPDIR", "USER")}
    | {*("PROMPT_COMMAND", "PS1", "PS2", "PS3", "PS4", "GLOBIGNORE", "FIGNORE", "MAIL", "LOGNAME")}
    | {*ZSH_TIED_NAMES, *(name.lower() for name in ZSH_TIED_NAMES)}
    | {*NULL_COMMANDS, *ZSH_HOOK_NAMES}
)
BLOCKED_PREFIXES = (
    "BASH",
    "LD_",
    "DYLD_",
    "GIT_",
    "CLAUDE",
    "PYTHON",
    "NODE_",
    "XDG_",
    "SHELLOPTS",
)
# What reads a variable's value as a name or a number, and so runs the command substitutions in a
# subscript of it (`D='a[$(rm x)]'`): an expansion that reads a name (NAME_EXPANSION: `${!D}`, a
# subscript `${a[D]}` or zsh's `$a[D]`, an offset `${x:D}`) or a number (ARITHMETIC_EXPANSION:
# `$((D))`, `$[D]`), a test's `-v` or `-R`, one of its number comparisons (zsh's test evaluates
# both sides), and the commands zsh gives a number (NUMBER_COMMANDS: `break $n`; `printf %d $n` by
# NUMBER_CONVERSION). What a word holds that is a number: an integer, a special parameter, a
# variable (VARIABLE_WORD) the text only sets to one, a count (`$(grep -c x f)`: COUNT_OPTIONS).
NAME_EXPANSION = re.compile(r"\$\{!|\$\{?#?\w+\[(?![@*]\]|\d+\])|\$\{#?\w+:(?![-=+?])")
ARITHMETIC_EXPANSION = re.compile(r"\$\(\(([^()]*)\)\)|\$\[([^\]]*)\]|\$\(\(|\$\[")
VARIABLE_WORD = re.compile(r'"?\$(?:\{(\w+)\}|(\w+)|[?#$!])"?')
SUBSTITUTION_WORD = re.compile(r'"?\$\(.*\)"?', re.DOTALL)
COUNTERS = frozenset({"grep", "egrep", "fgrep", "wc"})
COUNT_OPTIONS = frozenset("lcwm")  # wc's, which with no file print numbers alone
TEST_COMMANDS = frozenset({"test", "["})
NAME_TESTS = frozenset({"-v", "-R"})
NUMBER_TESTS = frozenset({"-eq", "-ne", "-lt", "-le", "-gt", "-ge"})
TEST_WORDS = frozenset({"!", "(", ")", "-a", "-o"})
NUMBER_COMMANDS = frozenset({"break", "continue", "exit", "return", "shift"})
NUMBER_CONVERSION = re.compile(r"%[-+ #0'\d.*]*[diouxXeEfFgGaA]")
INTEGER = re.compile(r"-?\d+")
# What a read may set or export for the programs it runs: their language, time zone, terminal,
# output width and colours, nothing a program runs by (`TAR_OPTIONS`, `RIPGREP_CONFIG_PATH`,
# `LESSOPEN`); and in front of a command, with a plain value (`LC_ALL=C grep`).
READ_NAME = re.compile(r"LC_[A-Z]+|LANG|LANGUAGE|TZ|COLUMNS|LINES|NO_COLOR|TERM|GREP_COLORS?")
READ_ENVIRONMENT = re.compile(rf"(?:{READ_NAME.pattern})=[\w.@:+-]*")
# The options of `sort` that name no file or program, and of `read` that only change how it reads.
SORT_OPTION = re.compile(r"-(?:[bdfghiMnRrsuVzcCm]+|[kt]\S*|S\S*)|--(?:reverse|unique|stable)")
READ_OPTIONS = frozenset("rs")
TIMEOUT_DURATION = re.compile(r"\d+(?:\.\d+)?[smhd]?")
NICE_OPTION = re.compile(r"-\d+")
# The words that open a command inside a compound one, and those that close it.
COMMAND_OPENERS = frozenset(
    {"if", "then", "elif", "else", "do", "while", "until", "!", "{", "time"}
)
COMPOUND_CLOSERS = frozenset({"done", "fi", "}", "esac"})
# Variables that can name a zone, and what a session may have set them to instead of the hook's
# own value: the default of each (the plugin root is a folder under the plugins folder).
ZONE_VARIABLES = ("HOME", "CLAUDE_CONFIG_DIR", "CLAUDE_PLUGIN_ROOT", "XDG_CONFIG_HOME")
# Expansions in a written word whose value is a folder the guard cannot pin: the current folder
# where it does not know it, the last one, the directory stack (`~-`, `~1`), and a zone variable
# or CLAUDE_JOB_DIR read with an operator (`${HOME%/}`, `${CLAUDE_JOB_DIR:-x}`). A plain zone
# variable is expanded each way (`_expansions`).
FOLDER_EXPANSION = re.compile(
    r"\$\{?(?:PWD|OLDPWD)\b|^~[-+\d]|"
    rf"\$\{{[#!]?(?:{'|'.join((*ZONE_VARIABLES, JOB_VARIABLE))})[^}}\w]"
)
ZONE_VARIABLE = re.compile(
    rf"\$(?:\{{({'|'.join(ZONE_VARIABLES)})(?::?[-=?+]([^}}]*))?\}}|({'|'.join(ZONE_VARIABLES)})(?!\w))"
)
PLUGIN_ROOT_DEFAULT = ("cache", "plugin")
# Commands whose every operand, or whose last one (the destination), they change or replace.
CHANGES_OPERANDS = frozenset(
    {"rm", "rmdir", "unlink", "mv", "ln", "chmod", "chown", "chgrp", "truncate", "shred", "tee"}
)
CHANGES_DESTINATION = frozenset({"cp", "install", "rsync"})
# IFS named other than in an expansion (`$IFS`, `${IFS}`), and zsh's ways to split words as sh does.
IFS_NAMED = re.compile(r"(?<![\w$\{])IFS\b")
WORD_SPLIT_OPTION = re.compile(r"shwordsplit")
SPLITTING_EMULATION = re.compile(r"\bemulate\b(?:\s+-\S+)*\s+(?:sh|ksh)\b")
STANDARD_INPUT = re.compile(r"-|/dev/stdin|/dev/fd/0|/proc/[^/]+/fd/0")
# A `file://` URL, its host left out: the path is what an MCP tool opens.
FILE_URL = re.compile(r"file://(?:localhost)?(/[^\s'\"]*)")
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
    transcript: str | None = None  # the payload's transcript_path, when it is a string


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
    found = payload.get("transcript_path")
    transcript = found if isinstance(found, str) else None
    if tool != "Bash":
        return Call(tool, None, tool_input, where, transcript)
    command = tool_input.get("command")
    if not isinstance(command, str):
        raise PayloadError("a Bash call without a command")
    return Call(tool, _bash_text(command), tool_input, where, transcript)


def _bash_text(command: str) -> Bash:
    """A Bash command and its code text. The PM's read-only self-check is blanked as a lone
    command."""
    code: str = _shell_words().code_text(command, lone=("printenv", ROLE_VAR))
    return Bash(command, code)


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
    """A shell_words.Word, at text[start:end], with the text of each plain `$(...)` it holds."""

    @property
    def substitutions(self) -> tuple[str, ...]: ...

    @property
    def start(self) -> int: ...

    @property
    def end(self) -> int: ...


class _Heredoc(Protocol):
    """A shell_words heredoc: where its body is in the text."""

    @property
    def body(self) -> tuple[int, int]: ...


class _SimpleCommand(Protocol):
    """A shell_words.Command: its words, its heredocs, the files it writes and its other
    redirection targets, whether it writes a file at all, and whether a pipe joins it to another
    command."""

    @property
    def words(self) -> list[_ShellWord]: ...

    @property
    def heredocs(self) -> list[_Heredoc]: ...

    @property
    def write_paths(self) -> list[str | None]: ...

    @property
    def redirect_inputs(self) -> list[str | None]: ...

    @property
    def input_files(self) -> list[str | None]: ...

    @property
    def argument_writes(self) -> list[str | None]: ...

    @property
    def writes(self) -> bool: ...

    @property
    def in_pipeline(self) -> bool: ...

    @property
    def after_pipe(self) -> bool: ...

    @property
    def start(self) -> int: ...

    @property
    def end(self) -> int: ...


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


class _Own(NamedTuple):
    """A session's own scratch folder inside a zone (its real path), the names right below it
    that stay the zone's (a session folder's `subagents`), and whether it is the job folder."""

    folder: str
    kept: tuple[str, ...] = ()
    job: bool = False


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
    readers: frozenset[str] = frozenset()  # commands besides READ_COMMANDS that only read it
    copy_reads: bool = False  # a copy or an archive only reads the sources it names
    cd_reads: bool = False  # a `cd` into it is no write when the text only reads
    loop_reads: bool = False  # a `for` loop over it is no write when the text only reads
    shell_reads: bool = False  # a read may follow a wrapper, hold a `$(...)`, read a `<` file
    writes_inside: bool = False  # a changed word inside it counts, not only a root or its holder
    own_folders: bool = False  # the session may write in its own scratch folders inside it
    own: tuple[_Own, ...] = ()  # those folders, for one call (`_with_own`)
    # Files whose siblings `<file>.<anything>` (a backup: `~/.claude.json.backup`) are in it too.
    siblings: tuple[Path, ...] = ()


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


def _claude_folder_zone() -> _Zone:
    """All of Claude Code's user folder and its `.claude.json`: skills, agents, commands, memory
    and the rest. It has no token: a `.claude` token would refuse every write in a repo's own
    `.claude` folder, where Claude Code keeps its worktrees. Bash may read it (rg too, and a `cd`
    into it when the text only reads) and run its scripts."""
    folders = _claude_folders()
    files = tuple(dict.fromkeys(parent / CLAUDE_JSON for parent in (Path.home(), claude_dir())))
    return _Zone(
        (*folders, *files),
        f"Claude Code's user folder ({folders[0]}) and {files[0]}",
        "claude-folder",
        frozenset(),
        frozenset({CLAUDE_FOLDER, CLAUDE_JSON, *(folder.name for folder in folders)}),
        CLAUDE_FOLDER_ENTRIES,
        writes_only=True,
        readers=CLAUDE_FOLDER_READERS,
        copy_reads=True,
        cd_reads=True,
        loop_reads=True,
        shell_reads=True,
        writes_inside=True,
        own_folders=True,
        siblings=files,
    )


def _job_folder(folders: Iterable[Path]) -> _Own | None:
    """The session's job folder: the hook's own CLAUDE_JOB_DIR (a command cannot set it), when it
    is an absolute path whose real path sits right under a user folder's `jobs/`."""
    value = os.environ.get(JOB_VARIABLE, "")
    if not os.path.isabs(value):
        return None
    real = Path(os.path.realpath(value))
    if any(real.parent == Path(os.path.realpath(folder / JOBS_FOLDER)) for folder in folders):
        return _Own(str(real), job=True)
    return None


def _session_folder(transcript: str | None, folders: Iterable[Path]) -> _Own | None:
    """The folder beside the session's transcript, its `subagents/` kept: when the payload's
    transcript_path is an absolute, normal path `<folder>/projects/<project>/<session id>.jsonl`
    and that folder's real path is where it says."""
    if transcript is None or not os.path.isabs(transcript):
        return None
    path = Path(transcript)
    if os.path.normpath(transcript) != transcript or path.suffix != TRANSCRIPT_SUFFIX:
        return None
    if not SESSION_ID.fullmatch(path.stem):
        return None
    real = Path(os.path.realpath(path.with_suffix("")))
    projects = {Path(os.path.realpath(folder / PROJECTS_FOLDER)) for folder in folders}
    if real.name != path.stem or real.parent.parent not in projects:
        return None
    return _Own(str(real), (SUBAGENTS_FOLDER,))


def _live_dollars(text: str) -> set[int]:
    """Where a `$` of a Bash text starts an expansion: not in single quotes, not after a
    backslash."""
    live: set[int] = set()
    quote = ""
    index = 0
    while index < len(text):
        char = text[index]
        if quote == "'":
            quote = "" if char == "'" else quote
        elif char == "\\":
            index += 1
        elif char == '"':
            quote = "" if quote else '"'
        elif char == "'" and not quote:
            quote = "'"
        elif char == "$":
            live.add(index)
        index += 1
    return live


def _job_references(text: str) -> list[tuple[int, int]]:
    """The spans of the `$CLAUDE_JOB_DIR` and `${CLAUDE_JOB_DIR}` of a Bash text that the shell
    expands to the hook's value as a word's first part: a live `$` (`_live_dollars`), after what
    starts a word (JOB_WORD_BEFORE) and before what ends one or its first component
    (JOB_WORD_AFTER). Not `'$CLAUDE_JOB_DIR'`, `\\$CLAUDE_JOB_DIR`, `x$CLAUDE_JOB_DIR` or zsh's
    `$CLAUDE_JOB_DIR:h`."""
    live = _live_dollars(text)
    return [
        match.span()
        for match in JOB_REFERENCE.finditer(text)
        if match.start() in live
        and (match.start() == 0 or text[match.start() - 1] in JOB_WORD_BEFORE)
        and (match.end() == len(text) or text[match.end()] in JOB_WORD_AFTER)
    ]


def _odd_job_mention(text: str) -> bool:
    """Whether a Bash text names CLAUDE_JOB_DIR otherwise than by `_job_references`, as written or
    with its quotes dropped (`${CLAUDE_JOB_DIR%/*}`, `'$CLAUDE_JOB_DIR'`, `$CLAUDE_JOB""_DIR`)."""
    named = len(JOB_NAME.findall(text))
    return named > len(_job_references(text)) or len(JOB_NAME.findall(_unquoted(text))) != named


def _job_variable_kept(command: str) -> bool:
    """Whether a Bash text leaves CLAUDE_JOB_DIR as the hook has it: it names it only by
    `_job_references` (`_odd_job_mention`), and when it does no builtin may set a name given as a
    word (NAME_SETTERS: `read`, `export`, `eval`...)."""
    if not any(JOB_NAME.search(text) for text in (command, _unquoted(command))):
        return True
    if _changes_word_splitting(command):
        return False
    return not _odd_job_mention(command) and not NAME_SETTERS.intersection(_words_of(command))


def _changes_word_splitting(command: str) -> bool:
    """Whether a Bash text may change how the shell splits an unquoted expansion: it names IFS
    other than by expanding it (`IFS=/`, `unset IFS`, `local IFS`), or turns on zsh's
    SH_WORD_SPLIT (`setopt shwordsplit`, any case, `_` ignored) or a mode that has it
    (`emulate sh`, `emulate -L ksh`). `$CLAUDE_JOB_DIR` then splits into words that are no job
    folder (`cd ~; IFS=/; rm -rf $CLAUDE_JOB_DIR/x` removes `.claude`)."""
    text = _unquoted(command)
    return bool(
        IFS_NAMED.search(text)
        or WORD_SPLIT_OPTION.search(text.replace("_", "").lower())
        or SPLITTING_EMULATION.search(text)
    )


def _with_own(zone: _Zone, call: Call) -> _Zone:
    """The zone with this call's own scratch folders (`_job_folder`, `_session_folder`), for a
    zone with `own_folders` and a Bash or edit call; an MCP tool gets none. A Bash text that may
    change CLAUDE_JOB_DIR (`_job_variable_kept`) gets no job folder."""
    if not zone.own_folders or (call.bash is None and call.tool not in EDIT_TOOLS):
        return zone
    folders = _claude_folders()
    job = _job_folder(folders)
    if call.bash is not None and not _job_variable_kept(call.bash.command):
        job = None
    session = _session_folder(call.transcript, folders)
    return zone._replace(own=tuple(own for own in (job, session) if own is not None))


def _job_written_out(call: Call, zone: _Zone) -> Call:
    """A Bash call with its `$CLAUDE_JOB_DIR` and `${CLAUDE_JOB_DIR}` that the shell expands
    (`_job_references`) written as the job folder, when the zone has one (`_with_own`): the checks
    then follow a `cd` there and see what a path leaves it for."""
    job = next((own for own in zone.own if own.job), None)
    if call.bash is None or job is None:
        return call
    command = call.bash.command
    for start, end in reversed(_job_references(command)):
        command = command[:start] + job.folder + command[end:]
    return replace(call, bash=_bash_text(command))


def _in_own(path: str | None, zone: _Zone) -> bool:
    """Whether a resolved path is one of the zone's own scratch folders or inside one, below none
    of its kept names."""
    if path is None:
        return False
    for own in zone.own:
        if path == own.folder:
            return True
        if _inside(path, own.folder):
            return Path(path).relative_to(own.folder).parts[0] not in own.kept
    return False


def _is_own_folder(path: str | None, zone: _Zone) -> bool:
    """Whether a resolved path is one of the zone's own scratch folders itself."""
    return any(path == own.folder for own in zone.own)


def _glob_in_own(word: str, base: str, zone: _Zone) -> bool:
    """Whether a glob, read from `base`, stays in one of the zone's own scratch folders: it has no
    `..`, and the folders before its first glob component are inside one (`_in_own`), or are one
    whose kept names (`subagents`) that component cannot match."""
    if not zone.own or UP_PATH.search(word):
        return False
    parts = _glob_parts(word, base)
    at = next((i for i, part in enumerate(parts) if GLOB_CHARS.intersection(part)), len(parts))
    if at == len(parts):
        return False
    prefix = _resolve(os.path.join(*parts[:at]), os.sep)
    for own in zone.own:
        if prefix == own.folder:
            return not _matches_any(own.kept, parts[at])
    return _in_own(prefix, zone)


def _glob_hits(word: str, base: str, zone: _Zone, roots: Sequence[tuple[str, ...]]) -> bool:
    """`_glob_reaches`, or one that can match a sibling of a zone file (`_glob_reaches_sibling`);
    but not for a glob that stays in an own scratch folder (`_glob_in_own`)."""
    reaches = _glob_reaches(word, base, roots) or _glob_reaches_sibling(word, base, zone)
    return reaches and not _glob_in_own(word, base, zone)


def _climbed(word: str) -> str:
    """A glob word with each component that can match `..` read as `..`: bash before 5.2 matches
    it with `.*`, so `.*/.claude` in `~/proj` is `~/.claude`. Only a component that starts with a
    literal dot can, as only such a glob matches a name that starts with one."""
    return "/".join(
        ".." if part.startswith(".") and _climbs(part) else part for part in word.split("/")
    )


def _climbs(part: str) -> bool:
    return bool(GLOB_CHARS.intersection(part)) and fnmatch.fnmatchcase("..", part)


def _climbing_hits(word: str, base: str, zone: _Zone) -> bool:
    """Whether a glob word that is written, as it climbs (`_climbed`), reaches the zone
    (`_glob_hits`), for a zone with `writes_inside`."""
    climbed = _climbed(word)
    roots = [root.parts for root in zone.roots]
    return zone.writes_inside and climbed != word and _glob_hits(climbed, base, zone, roots)


def _glob_reaches_sibling(word: str, base: str, zone: _Zone) -> bool:
    """Whether a glob word can match a sibling of a zone file (`~/.claude.json.*`), or a path
    inside one: its folder parts match the file's folder and the next part can start with the
    file's name and a dot (`_can_start_with`)."""
    pattern = _glob_parts(word, base)
    for file in zone.siblings:
        folder = Path(os.path.realpath(file.parent)).parts
        if (
            len(pattern) > len(folder)
            and all(
                fnmatch.fnmatchcase(name, part) for name, part in zip(folder, pattern, strict=False)
            )
            and _can_start_with(pattern[len(folder)], f"{file.name}.")
        ):
            return True
    return False


def _can_start_with(pattern: str, prefix: str) -> bool:
    """Whether some name that starts with `prefix` matches the glob `pattern` (read as fnmatch
    reads it: a leading `*` can match a dot)."""
    units = GLOB_UNIT.findall(pattern)

    @functools.cache
    def extends(at: int, done: int) -> bool:
        if done == len(prefix):
            return True
        if at == len(units):
            return False
        unit = units[at]
        if unit == "*":
            return extends(at + 1, done) or extends(at, done + 1)
        char = prefix[done]
        if len(unit) == 2 and unit[0] == "\\":  # an escaped character is itself
            matched = char == unit[1]
        else:
            matched = unit == "?" or fnmatch.fnmatchcase(char, unit)
        return matched and extends(at + 1, done + 1)

    return extends(0, 0)


def _zones(store: Path) -> tuple[_Zone, ...]:
    """The places a restricted session may not write, in the order a refusal names them: the
    user folder last, so a settings or plugins refusal names its own zone."""
    return (
        _store_zone(store),
        _config_zone(),
        _settings_zone(),
        _plugins_zone(),
        _claude_folder_zone(),
    )


def _glob_parts(word: str, base: str) -> tuple[str, ...]:
    """The components of a glob word read from `base`, `~` and variables expanded."""
    return Path(
        os.path.normpath(os.path.join(base, os.path.expandvars(os.path.expanduser(word))))
    ).parts


def _glob_reaches(word: str, cwd: str, roots: Sequence[tuple[str, ...]]) -> bool:
    """Whether a glob word can match a root, one of its parents, or a path inside it."""
    pattern = _glob_parts(word, cwd)
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


def _keeps_quotes_literal(words: Sequence[_ShellWord]) -> bool:
    """Whether one simple command takes its quoted words as data, never as a glob it expands or
    code it runs: a GLOB_LITERAL_COMMANDS command by its plain name, `find` with no action and
    no word that may expand to one (`-e{xec,xec}`), `sed` whose scripts run nothing
    (`_sed_prints_only`), and `rg` with no command option and no brace."""
    name, args = words[0].value, words[1:]
    raws = [word.raw for word in args]
    if name == "find":
        return not (
            _may_expand(args)
            or any(_unquoted(raw).startswith(FIND_ACTION_PREFIXES) for raw in raws)
        )
    if name == "sed":
        return _sed_prints_only(args)
    if name == "rg":
        return not any(raw.startswith(("--pre", "--hostname")) or "{" in raw for raw in raws)
    return name in GLOB_LITERAL_COMMANDS


def _sed_scripts(args: Sequence[_ShellWord]) -> list[str] | None:
    """The scripts of a GNU sed command: each `-e` or `--expression` value, or else its first
    operand. None when one may be another: a word that may expand, an `-f` script file, an
    option SED_FLAG_LETTERS or SED_LONG_FLAGS does not list, or an `-e` after the first operand
    (with POSIXLY_CORRECT set, sed reads it as a file and the operand as the script)."""
    scripts: list[str] = []
    operands: list[str] = []
    words = iter(args)
    for word in words:
        value = word.value
        if value is None:
            return None
        if value == "--":
            rest = [each.value for each in words]
            if any(each is None for each in rest):
                return None
            operands += [each for each in rest if each is not None]
            break
        if value == "-" or not value.startswith("-"):
            operands.append(value)
            continue
        script = _sed_option_script(value, words)
        if script is None:
            return None
        if script:
            if operands:
                return None
            scripts.append(script[0])
    return scripts or operands[:1] or None


def _sed_option_script(value: str, words: Iterator[_ShellWord]) -> list[str] | None:
    """The script an option word of sed gives (`-e x`, `-nex`, `--expression=x`): one, none for
    a flag, None for an option `_sed_scripts` does not take. Takes the next word when the
    script is in it."""
    if value.startswith("--"):
        name, equals, attached = value[2:].partition("=")
        if name == "expression":
            return [attached] if equals else _next_value(words)
        return [] if name in SED_LONG_FLAGS and not equals else None
    for at, letter in enumerate(value[1:], start=1):
        if letter == "e":
            attached = value[at + 1 :]
            return [attached] if attached else _next_value(words)
        if letter == "i":
            return []  # the rest of the word is the backup suffix
        if letter not in SED_FLAG_LETTERS:
            return None
    return []


def _next_value(words: Iterator[_ShellWord]) -> list[str] | None:
    word = next(words, None)
    return None if word is None or word.value is None else [word.value]


class _SedRuns(Exception):
    """A sed script may run a command or write a file, or it cannot be read."""


class _SedScript:
    """A reader of one GNU sed script, its `-e` parts joined by newlines as sed joins them.

    Where sed versions may read a regex apart (does `[/]` end at the `/`? does a backslash
    escape inside `[...]`?), `brackets` and `bracket_escapes` pick one reading; a script counts
    as printing only when every reading finds nothing that runs (`_sed_runs_nothing`)."""

    def __init__(self, text: str, *, brackets: bool, bracket_escapes: bool) -> None:
        self.text = text
        self.at = 0
        self.brackets = brackets
        self.bracket_escapes = bracket_escapes

    def peek(self) -> str:
        return self.text[self.at : self.at + 1]

    def take(self) -> str:
        char = self.peek()
        self.at += len(char)
        return char

    def skip(self, chars: str) -> None:
        while self.peek() and self.peek() in chars:
            self.at += 1

    def digits(self) -> bool:
        start = self.at
        while self.peek().isdigit():
            self.at += 1
        return self.at > start

    def text_to_line_end(self) -> None:
        """The text of `a`, `i` or `c`: up to a newline no backslash escapes."""
        while self.peek() and self.peek() != "\n":
            if self.take() == "\\":
                self.take()

    def to_newline(self) -> None:
        """A comment or a file name: up to the next newline, whatever comes before it."""
        while self.peek() and self.peek() != "\n":
            self.at += 1

    def delimited(self, delimiter: str, *, regex: bool = False) -> None:
        """A regex or a replacement: up to `delimiter`, a backslash escaping the next character
        and, in a regex read with `brackets`, a `[...]` holding it; an unescaped newline or the
        end of the script is an error in sed."""
        while (char := self.take()) != delimiter:
            if char in ("", "\n"):
                raise _SedRuns
            if char == "\\" and not self.take():
                raise _SedRuns
            if char == "[" and regex and self.brackets:
                self.bracket()

    def bracket(self) -> None:
        """The rest of a `[...]` after its `[`: a first `]` (after an optional `^`) is literal,
        and `[:x:]`, `[.x.]`, `[=x=]` nest."""
        if self.peek() == "^":
            self.take()
        if self.peek() == "]":
            self.take()
        while (char := self.take()) != "]":
            if char in ("", "\n"):
                raise _SedRuns
            if char == "\\" and self.bracket_escapes and not self.take():
                raise _SedRuns
            if char == "[" and self.peek() in (":", ".", "="):
                end = self.take() + "]"
                close = self.text.find(end, self.at)
                if close == -1 or "\n" in self.text[self.at : close]:
                    raise _SedRuns
                self.at = close + len(end)

    def label(self) -> None:
        """A label: read to its shortest end (a blank, `;`, `}`), so whatever sed may read as
        commands after it is read as commands here too."""
        while self.peek() and self.peek() not in " \t\n;}":
            self.at += 1

    def end_of_command(self) -> None:
        self.skip(" \t")
        if self.peek() not in SED_COMMAND_ENDS:
            raise _SedRuns

    def address(self) -> bool:
        """One address (`3`, `0~4`, `$`, `/re/I`, `\\%re%`); False when none is here."""
        char = self.peek()
        if char.isdigit():
            self.digits()
            if self.peek() == "~":
                self.take()
                self.digits()
            return True
        if char == "$":
            self.take()
            return True
        if char not in ("/", "\\"):
            return False
        self.take()
        delimiter = self.take() if char == "\\" else "/"
        if delimiter in ("", "\n", "\\"):
            raise _SedRuns
        self.delimited(delimiter, regex=True)
        self.skip("IM")
        return True

    def addresses(self) -> None:
        if not self.address() or self.peek() != ",":
            return
        self.take()
        self.skip(" \t")
        if self.peek() in ("+", "~"):
            self.take()
            if not self.digits():
                raise _SedRuns
        elif not self.address():
            raise _SedRuns

    def command(self) -> None:
        letter = self.take()
        if letter in SED_BARE_COMMANDS:
            self.end_of_command()
        elif letter == "{":
            pass
        elif letter in SED_NUMBER_COMMANDS:
            self.skip(" \t")
            self.digits()
            self.end_of_command()
        elif letter in SED_TEXT_COMMANDS:
            self.text_to_line_end()
        elif letter in SED_FILE_COMMANDS:
            self.to_newline()
        elif letter in SED_LABEL_COMMANDS:
            self.skip(" \t")
            self.label()
            self.end_of_command()
        elif letter in ("s", "y"):
            delimiter = self.take()
            if delimiter in ("", "\n", "\\"):
                raise _SedRuns
            self.delimited(delimiter, regex=letter == "s")
            self.delimited(delimiter)
            if letter == "s":
                self.skip("".join(SED_SUBSTITUTE_FLAGS))
            self.end_of_command()
        else:
            raise _SedRuns

    def read(self) -> None:
        while True:
            self.skip(" \t\n;")
            if not self.peek():
                return
            if self.peek() == "#":
                self.to_newline()
                continue
            self.addresses()
            self.skip(" \t")
            while self.peek() == "!":
                self.take()
                self.skip(" \t")
            self.command()


def _sed_runs_nothing(script: str) -> bool:
    """Whether a GNU sed script, read whole, has no `e`, `w` or `W` command, no `e` or `w` flag
    of `s`, and only commands the reader knows: it may only print. It must hold in each way a
    sed may read a regex (`_SedScript`); anything it cannot read counts as one that may run."""
    readings = ((False, False), (True, False), (True, True))
    try:
        for brackets, bracket_escapes in readings:
            _SedScript(script, brackets=brackets, bracket_escapes=bracket_escapes).read()
    except _SedRuns:
        return False
    return True


def _sed_prints_only(args: Sequence[_ShellWord]) -> bool:
    """Whether a sed command, by its arguments, only prints: its scripts can be told
    (`_sed_scripts`) and, read whole, run nothing and write no file (`_sed_runs_nothing`)."""
    scripts = _sed_scripts(args)
    return scripts is not None and _sed_runs_nothing("\n".join(scripts))


def _quotes_stay_literal(command: str) -> bool:
    """Whether every command on the line takes its quoted words as data (`_keeps_quotes_literal`),
    so a glob character in quotes is literal to the end. Not when the scanner cannot follow the
    line (shell_words.simple_commands)."""
    commands: list[_SimpleCommand] | None = _shell_words().simple_commands(command)
    if commands is None:
        return False
    return all(_keeps_quotes_literal(each.words) for each in commands if each.words)


def _globbing_code(bash: Bash) -> str:
    """A Bash call's code text as its glob checks read it: a glob character the shell keeps
    literal (in quotes, after a backslash) is masked (shell_words.without_quoted_globs), so `ls
    '.*'` names no folder. The mask is read from the command as written, since the code text
    blanks a heredoc body newlines and all, which hides where the body ends, and copied onto the
    code text, which has the same length. Only on a line of plain data commands
    (`_quotes_stay_literal`): where a command may run a quoted word or read it as a glob
    (`bash -c '...'`, `python3 -c "glob.glob(...)"`, `xargs`, `git rm '...'`, `for d in '...'`),
    it is read as a glob as ever. Nor on a line with an unquoted `$` (without_quoted_globs gives
    up there), which may hand a quoted word back to the shell unquoted (`d='.c*'; ls $d`)."""
    if not _quotes_stay_literal(bash.command):
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


def _plain_bodies(found: _Bodies) -> bool:
    """Whether no heredoc body may expand a relative glob of its own: each is copied (`cat`,
    `tee`) or read by python or node, by name or by a path in TRUSTED_READER_FOLDERS, with no glob call
    in any body (GLOB_CALLS)."""
    globbing = any(call in body for body in found.bodies for call in GLOB_CALLS)
    names = [
        _basename(reader)
        if reader is not None and ("/" not in reader or reader.startswith(TRUSTED_READER_FOLDERS))
        else None
        for reader in found.readers
    ]
    return all(
        name in BODY_COPIERS
        or (not globbing and name is not None and (name == "node" or PYTHON_NAME.fullmatch(name)))
        for name in names
    )


def _shell_glob_words(command: str) -> list[str]:
    """The words of a Bash command's glob text that the shell may expand from the working
    folder: all of them, but in a heredoc body that is copied, or read by python or node with no
    glob call (`_plain_bodies`), only a word that starts at a fixed place (`/`, `~`, `$`). A
    relative regex in a script (`r'(a).*'`) is not a shell glob; the body's relative words are
    read after a `cd` by `_bash_glob_names_zone`."""
    found = _text_bodies(command)
    if found is None or not _plain_bodies(found):
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
    """The text as written and with its brace expansions written out (`_brace_expanded`), each
    as it is and with its zone variables expanded each way, each with its glob text expanded the
    same way: the check reads them all, since the command may run under other values than the
    hook sees."""
    sources = [(text, glob_text)]
    braced, braced_glob = _brace_expanded(text), _brace_expanded(glob_text)
    if braced is not None and braced_glob is not None:
        sources.append((braced, braced_glob))
    pairs = [
        pair
        for plain, globbing in sources
        for pair in (
            (plain, globbing),
            (_expanded(plain, default=False), _expanded(globbing, default=False)),
            (_expanded(plain, default=True), _expanded(globbing, default=True)),
        )
    ]
    return list(dict.fromkeys(pairs))


def _operands(args: Sequence[str]) -> list[str]:
    """The words that are not options: everything after `--`, and a lone `-` (standard input or
    output, an operand to every command)."""
    found: list[str] = []
    options = True
    for word in args:
        if options and word == "--":
            options = False
        elif not options or not word.startswith("-") or word == "-":
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


def _replaced_operands(words: Sequence[str], index: int) -> list[str]:
    """The words a command at `index` changes in themselves: what it deletes, moves, links or
    changes the mode of, and where a `find` that acts starts."""
    name, args = _basename(words[index]), words[index + 1 :]
    if name in CHANGES_OPERANDS:
        return [*_operands(args), *_target_directory(args)]
    return _find_targets(args) if name == "find" else []


def _command_targets(words: Sequence[str], index: int) -> list[str]:
    """The words a command at `index` changes or replaces: what it deletes, moves, links or
    changes the mode of (`_replaced_operands`), the destination of a copy, the folder an archive
    unpacks into."""
    name, args = _basename(words[index]), words[index + 1 :]
    if name in CHANGES_OPERANDS or name == "find":
        return _replaced_operands(words, index)
    if name in CHANGES_DESTINATION:
        return _target_directory(args) or _operands(args)[-1:]
    if name == "tar":
        return _option_folders(args, "C", "--directory") or _unpacks_here(args, "x", "--extract")
    if name == "unzip":
        return _option_folders(args, "d") or _unpacks_here(args, "", "")
    return []


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
    """One simple command of a text: the words it changes or replaces, those it changes in
    themselves (`replaced`), the links it makes (`links`), all its words, and whether it moves the
    session to another folder (`folder` is where, None when that cannot be told)."""

    targets: list[str]
    moves: bool = False
    folder: str | None = None
    replaced: tuple[str, ...] = ()
    links: tuple[str, ...] = ()
    words: tuple[str, ...] = ()


def _step(words: Sequence[str]) -> _Step:
    targets = _redirect_targets(words)
    replaced: list[str] = []
    links: list[str] = []
    for index in _runnable(words):
        targets += _command_targets(words, index)
        replaced += _replaced_operands(words, index)
        if _basename(words[index]) == "ln":
            links += _operands(words[index + 1 :])
    found = _Step(targets, replaced=tuple(replaced), links=tuple(links), words=tuple(words))
    first = _basename(words[0]) if words else ""
    if first in CHANGE_DIR:
        operands = _operands(words[1:])
        return found._replace(moves=True, folder=operands[0] if operands else "~")
    # `popd`, or a `cd` further in (`(cd x; ...)`, `env -C`): the folder is not followed.
    return found._replace(moves=first == "popd" or bool(CHANGE_DIR.intersection(words)))


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
    if zone.writes_inside:
        names.update(root.name for root in zone.roots)
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
    if _has_no_literal(parts[0]):
        return False
    return _matches_any(names, parts[0]) or _names_sibling(parts[0], zone)


def _names_sibling(part: str, zone: _Zone) -> bool:
    """Whether a relative component that starts with a literal dot, as a sibling's name does,
    can name a sibling of a zone file (`.claude.json.*`, `.claude.json.bak`)."""
    return part.startswith(".") and any(
        _can_start_with(part, f"{file.name}.") for file in zone.siblings
    )


def _changes_zone_holder(text: str, cwd: str, zone: _Zone) -> bool:
    """Whether something in the text changes or replaces a root of the zone or a folder holding
    one, or for a zone with `writes_inside` names a path inside it (`_names_inside`) or writes a
    word the guard cannot pin that may name it (`_writes_unpinned`). The folder a relative word
    is read from follows the `cd`s of the text; after one that cannot be followed (`cd "$X"`,
    `cd -`), a relative operand counts when it can name a holder."""
    current: str | None = cwd
    for step in _write_targets(text):
        if any(_changes_holder(target, current, zone) for target in step.targets):
            return True
        if zone.own and _changes_own(step, current, zone):
            return True
        if zone.writes_inside and any(_names_inside(word, current, zone) for word in step.words):
            return True
        if zone.writes_inside and current and _creates_climbing(step, current, zone):
            return True
        if zone.writes_inside and any(_through_alias(word, current) for word in _step_writes(step)):
            return True
        if step.moves:
            current = (_after_pinned_cd if zone.writes_inside else _after_cd)(current, step.folder)
    return zone.writes_inside and _writes_unpinned(text, cwd, zone)


def _writes_unpinned(text: str, cwd: str, zone: _Zone) -> bool:
    """Whether a text writes a word the guard does not follow that may name the zone
    (`_may_name_zone`), or one whose leading current folder, written out (`_pinned`), is a holder
    of the zone or in it, or goes by such a `cd` to a write, or writes a relative word after a
    `cd old new` (`_swaps_folder`). Its command substitutions and brace expansions stay in their
    words (`_glued_expansions`), and its heredoc bodies read as text or code are no commands of it
    (`_without_text_bodies`)."""
    current: str | None = cwd
    shell = _without_text_bodies(text, zone)
    steps = [_restored(step) for step in _write_targets(_glued_expansions(shell))]
    for index, step in enumerate(steps):
        if any(_unpinned_word(word, current, zone) for word in _step_writes(step)):
            return True
        if step.moves:
            folder = _pinned(step.folder, current) if step.folder is not None else None
            later = any(map(_step_writes, steps[index + 1 :]))
            if folder and not _followed(folder) and later and _may_name_zone(folder, current, zone):
                return True
            if _swaps_folder(step) and _writes_relative(steps[index + 1 :]):
                return True
            current = _after_pinned_cd(current, step.folder)
    return False


def _swaps_folder(step: _Step) -> bool:
    """Whether a step is a `cd` or `pushd` with two operands: zsh's `cd old new` goes to the
    current folder with `old` replaced by `new`, an older bash to the first."""
    words = step.words
    return bool(words) and _basename(words[0]) in CHANGE_DIR and len(_operands(words[1:])) == 2


def _writes_relative(steps: Sequence[_Step]) -> bool:
    """Whether a step writes a word relative to the folder it runs in (not from `/` or `~`)."""
    return any(not word.startswith(("/", "~")) for step in steps for word in _step_writes(step))


def _through_alias(word: str, current: str | None) -> bool:
    """Whether a written word, as written or as a real path, is in a folder of ALIAS_FOLDERS but
    for a stream or a terminal (DEV_WRITABLE): `/proc/self/cwd/x` is where the shell is."""
    base = current or os.sep
    path = os.path.normpath(os.path.join(base, os.path.expanduser(word)))
    if path in DEV_WRITABLE:
        return False
    places = (path, _resolve(word, base))
    return any(_inside(place, folder) for place in places for folder in ALIAS_FOLDERS)


def _scanned_commands(text: str) -> list[_SimpleCommand] | None:
    """The simple commands of a text with its plain `$(...)` kept in their words
    (shell_words.simple_commands with `substitutions`); None when the scanner cannot follow it."""
    commands: list[_SimpleCommand] | None = _shell_words().simple_commands(text, substitutions=True)
    return commands


def _without_text_bodies(text: str, zone: _Zone) -> str:
    """`text` with the heredoc bodies blanked that a command in no pipe reads as text or code
    (`_reads_body_as_text`: python's, `cat`'s); as it is when the scanner cannot follow it. A
    piped one stays: `cat <<E | sh` runs its body."""
    commands = _scanned_commands(text)
    if commands is None:
        return text
    spans = [
        heredoc.body
        for each in commands
        if each.words and not each.in_pipeline
        if _reads_body_as_text(_program_name(each.words[0], zone))
        for heredoc in each.heredocs
    ]
    return _blank(text, spans)


def _step_writes(step: _Step) -> tuple[str, ...]:
    """The words a step changes, replaces or links."""
    return (*step.targets, *step.replaced, *step.links)


def _unpinned_word(word: str, current: str | None, zone: _Zone) -> bool:
    """Whether a written word the guard does not follow may name the zone (`_may_name_zone`),
    or its leading current folder, written out (`_pinned`), makes it a holder or a path in it."""
    pinned = _pinned(word, current)
    if not _followed(pinned):
        return _may_name_zone(pinned, current, zone)
    return pinned != word and _changes_holder(pinned, current, zone)


def _glued_expansions(text: str) -> str:
    """`text` with its `$(...)`, backtick and `${...}` expansions and its brace expansions glued
    (GLUED): their blanks, separators, parentheses and braces as private characters, so that
    COMMAND_SPLIT and a split at blanks keep each in its word (`"$(dirname ~/x)/.claude"`)."""
    found: list[str] = []
    index = 0
    while index < len(text):
        end = _expansion_end(text, index)
        found.append(text[index:end].translate(GLUED) if end > index else text[index])
        index = max(end, index + 1)
    return "".join(found)


def _expansion_end(text: str, index: int) -> int:
    """Where an expansion of `_glued_expansions` that starts at `index` ends; `index` when none
    starts there."""
    pairs = {"$(": ("(", ")"), "${": ("{", "}")}
    opener = text[index : index + 2]
    if opener in pairs:
        start, close = pairs[opener]
        depth = 0
        for at in range(index + 1, len(text)):
            depth += (text[at] == start) - (text[at] == close)
            if depth == 0:
                return at + 1
        return len(text)
    if text[index] == "`":
        close_at = text.find("`", index + 1)
        return len(text) if close_at < 0 else close_at + 1
    if text[index] != "{":
        return index
    group = text[index : _closing_brace(text, index) + 1]
    return index + len(group) if "," in group or ".." in group else index


def _closing_brace(word: str, start: int) -> int:
    """Where the brace that closes the `{` at `start` stands, nested braces counted; -1 when a
    blank or the end of the text comes first."""
    depth = 0
    for at in range(start, len(word)):
        char = word[at]
        if char.isspace():
            return -1
        depth += (char == "{") - (char == "}")
        if depth == 0:
            return at
    return -1


class _Braces(NamedTuple):
    """The first brace expansion of a word: where it starts, where it ends (past its `}`), and the
    words it stands for, None when the guard cannot tell them (`_brace_group`)."""

    start: int
    end: int
    alternatives: tuple[str, ...] | None


def _brace_group(word: str) -> _Braces | None:
    """The first brace expansion of a word: a `{...}` with a comma outside the braces in it
    (`{a,{b,c}}`), or a range (`_range_members`); None when it has none. Not a `${`, nor a brace
    that closes no list or range (`{}`, `{a}`). A range the guard cannot tell has no
    alternatives: zsh expands `{1..$N}` after `$N`."""
    index = 0
    while (start := word.find("{", index)) >= 0:
        index = start + 1
        close = _closing_brace(word, start)
        if close < 0 or word[start - 1 : start] == "$":
            continue
        content = word[start + 1 : close]
        alternatives = _alternatives(content)
        if len(alternatives) > 1:
            return _Braces(start, close + 1, tuple(alternatives))
        if ".." in content and UNPINNED_EXPANSION.search(content):
            return _Braces(start, close + 1, None)
        match = BRACE_RANGE.fullmatch(content)
        if match:
            return _Braces(start, close + 1, _range_members(match))
    return None


def _alternatives(content: str) -> list[str]:
    """A brace list's words: its text cut at each comma outside the braces in it."""
    found = [""]
    depth = 0
    for char in content:
        depth += (char == "{") - (char == "}")
        if char == "," and depth == 0:
            found.append("")
        else:
            found[-1] += char
    return found


def _range_members(match: re.Match[str]) -> tuple[str, ...] | None:
    """The words a brace range makes (BRACE_RANGE: `1..3`, `a..e`, `01..10..2`), zero-padded
    when an end is; None past BRACE_MEMBERS."""
    numbers = match[1] is not None
    first, last, every = (match[1], match[2], match[3]) if numbers else match.group(4, 5, 6)
    step = abs(int(every or 1)) or 1
    low, high = (int(first), int(last)) if numbers else (ord(first), ord(last))
    if abs(high - low) // step + 1 > BRACE_MEMBERS:
        return None
    values = range(low, high + (1 if high >= low else -1), step if high >= low else -step)
    if not numbers:
        return tuple(map(chr, values))
    padded = any(len(end.lstrip("-")) > 1 and end.lstrip("-")[0] == "0" for end in (first, last))
    width = max(len(first), len(last)) if padded else 0
    return tuple(f"{value:0{width}d}" for value in values)


def _brace_members(word: str) -> list[str] | None:
    """The words the shell makes of a word by its brace expansions, in its order (`a{b,c}d` is
    `abd acd`, `{1..3}` is `1 2 3`, nested lists too); `[word]` when it has none. None when they
    are more than BRACE_MEMBERS, or one cannot be told (`_brace_group`), or the word holds more
    braces than BRACE_MEMBERS words could (minified JSON): each list or range is one `{`."""
    if len(BRACE_OPENER.findall(word)) > BRACE_MEMBERS:
        return None
    group = _brace_group(word)
    if group is None:
        return [word]
    if group.alternatives is None:
        return None
    heads: list[str] = []
    for alternative in group.alternatives:
        made = _brace_members(alternative)
        if made is None:
            return None
        heads += made
    tails = _brace_members(word[group.end :])
    if tails is None or len(heads) * len(tails) > BRACE_MEMBERS:
        return None
    return [word[: group.start] + head + tail for head in heads for tail in tails]


def _brace_expanded(text: str) -> str | None:
    """`text` with each word that holds a brace expansion (BRACE_WORD) written as the words it
    makes (`_brace_members`), as the shell runs it; None when a word makes words the guard cannot
    tell or more than BRACE_MEMBERS."""
    found: list[str] = []
    last = 0
    for match in BRACE_WORD.finditer(text):
        members = _brace_members(match[0])
        if members is None:
            return None
        found += [text[last : match.start()], " ".join(members)]
        last = match.end()
    return "".join(found) + text[last:]


def _restored(step: _Step) -> _Step:
    """A step of a glued text (`_glued_expansions`) with its words as written."""

    def back(word: str) -> str:
        return word.translate(UNGLUED)

    return step._replace(
        targets=[back(word) for word in step.targets],
        folder=None if step.folder is None else back(step.folder),
        replaced=tuple(map(back, step.replaced)),
        links=tuple(map(back, step.links)),
        words=tuple(map(back, step.words)),
    )


def _pinned(word: str, current: str | None) -> str:
    """A word with its leading `$PWD`, `${PWD}` or `~+` written as the folder `current`, when the
    guard knows it (CURRENT_FOLDER)."""
    if current is None:
        return word
    return CURRENT_FOLDER.sub(lambda _match: current, word)


def _followed(word: str) -> bool:
    """Whether the guard reads a word as the shell does: it has no expansion of UNPINNED_EXPANSION or
    FOLDER_EXPANSION, and no brace expansion (`_brace_members`)."""
    if UNPINNED_EXPANSION.search(word) or FOLDER_EXPANSION.search(word):
        return False
    return _brace_members(word) == [word]


def _after_pinned_cd(current: str | None, folder: str | None) -> str | None:
    """Where the session is after `cd folder` (`_after_cd`) with its leading current folder
    written out (`_pinned`); None when the guard does not follow the folder (`_followed`)."""
    if folder is None:
        return None
    pinned = _pinned(folder, current)
    return _after_cd(current, pinned) if _followed(pinned) else None


def _may_name_zone(word: str, current: str | None, zone: _Zone) -> bool:
    """Whether a word the guard does not follow may name the zone or a folder that holds it: a
    word its brace expansion makes may (`_member_may_name_zone`), or makes more than the guard
    reads (`_brace_members`), or it has a folder expansion the guard cannot pin
    (FOLDER_EXPANSION) or a root's name (`.claude`), or a component of it may
    (`_part_may_name_zone`)."""
    members = _brace_members(word)
    if members is None:
        return True
    if members != [word]:
        return any(_member_may_name_zone(member, current, zone) for member in members)
    if FOLDER_EXPANSION.search(word) or any(root.name in word for root in zone.roots):
        return True
    parts = word.split("/")
    unknown = False  # whether an expansion names a folder before this component
    for index, part in enumerate(parts):
        followed = not UNPINNED_EXPANSION.search(part)
        if unknown and part == "..":
            return True
        if not followed and _part_may_name_zone(parts[:index], part, current, zone, unknown):
            return True
        unknown = unknown or not followed
    return False


def _member_may_name_zone(member: str, current: str | None, zone: _Zone) -> bool:
    """Whether a word a brace expansion makes may name the zone (`_may_name_zone`), or, when the
    guard follows it, changes a holder of the zone or a path in it, or may alias a folder."""
    if not _followed(member):
        return _may_name_zone(member, current, zone)
    return _changes_holder(member, current, zone) or _through_alias(member, current)


def _part_may_name_zone(
    before: Sequence[str], part: str, current: str | None, zone: _Zone, unknown: bool
) -> bool:
    """Whether a component with an expansion, its expansions read as `*` (EXPANSION_TEXT), may
    be `..`, or be a holder's name or a sibling's (`.cl${X}aude`) where the folder before it is
    unknown, or whether the folder before it holds the zone or is in it (`~/$N`, `/$X`)."""
    unknown = unknown or (not before and part.startswith(("$", "`")))  # `$D/x`: D is anywhere
    glob = EXPANSION_TEXT.sub("*", part)
    if not _has_no_literal(glob):
        if fnmatch.fnmatchcase("..", glob):
            return True
        named = _matches_any(_holder_names(zone), glob) or _names_sibling(glob, zone)
        if named and (unknown or current is None):
            return True
    if unknown:
        return False
    prefix = "/".join(before) if before else "."
    if current is None and not prefix.startswith(("/", "~")):
        return False
    resolved = _resolve(prefix or os.sep, current or os.sep)
    return resolved is not None and (_is_root_or_parent(resolved, zone) or _in_zone(resolved, zone))


def _creates_climbing(step: _Step, current: str, zone: _Zone) -> bool:
    """Whether a `touch` or `mkdir` (PATH_CREATORS) makes a path through a glob that, as it climbs
    (`_climbing_hits`), reaches the zone: `touch .*/.claude/x` one folder below home."""
    words = step.words
    return (
        bool(words)
        and _basename(words[0]) in PATH_CREATORS
        and any(_climbing_hits(word, current, zone) for word in _operands(words[1:]))
    )


def _word_paths(word: str) -> list[str]:
    """The paths a word may name: itself, what follows its `=` (`of=x`, `--output=x`) and what
    follows a short option's letter (`-ox`); never an option."""
    found = [word, word.partition("=")[2]]
    if word.startswith("-") and not word.startswith("--"):
        found.append(word[2:])
    return [path for path in found if path and not path.startswith("-")]


def _names_inside(word: str, current: str | None, zone: _Zone) -> bool:
    """Whether a word that is no glob names a path in the zone (`_word_paths`, `_in_zone`), read
    from the folder `current`. After a `cd` that cannot be followed (None), a relative path counts
    when a part of it can name the zone or a folder that holds it (`_may_hold_zone`), not a bare
    `.` or `..`. Globs are the glob checks' to judge."""
    for path in _word_paths(word):
        if GLOB_CHARS.intersection(path):
            continue
        if current is None and not path.startswith(("/", "~", "$")):
            named = any(part not in ("", ".", "..") for part in path.split("/"))
            if named and _may_hold_zone(path, zone):
                return True
        elif _in_zone(_resolve(path, current or os.sep), zone):
            return True
    return False


def _changes_own(step: _Step, current: str | None, zone: _Zone) -> bool:
    """Whether a step changes one of the session's own scratch folders in itself (`rm -rf`, `mv`,
    `chmod` of it), makes a link inside one, which a later write could go through, or copies,
    moves or unpacks into one with kept names (`_fills_kept`)."""
    base = current or os.sep
    known = [word for word in (*step.replaced, *step.links) if current or word.startswith("/")]
    into = [word for word in step.targets if current or word.startswith("/")]
    return (
        any(_is_own_folder(_resolve(word, base), zone) for word in known)
        or any(_in_own(_resolve(word, base), zone) for word in step.links if word in known)
        or any(_fills_kept(_resolve(word, base), zone) for word in into)
    )


def _fills_kept(path: str | None, zone: _Zone) -> bool:
    """Whether a resolved destination is an own scratch folder with kept names itself (the
    session's, whose `subagents/` is kept): what a copy, a sync or an archive puts there has
    names the guard does not see (`cp -r /tmp/subagents <folder>`)."""
    return any(path == own.folder and own.kept for own in zone.own)


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
        return _glob_hits(target, base, zone, roots) or _climbing_hits(target, base, zone)
    resolved = _resolve(target, base)
    return resolved is not None and (
        _is_root_or_parent(resolved, zone) or (zone.writes_inside and _in_zone(resolved, zone))
    )


class _Where(NamedTuple):
    """Where a text runs: its folder, whether that is known (False when it may run elsewhere than
    `cwd`: an MCP tool's own working directory), and whether every `cd` of the text goes to a
    folder that cannot lead to the zone (`_cds_clear`), so a relative glob after it does not
    count."""

    cwd: str
    known: bool = True
    cds_clear: bool = False


def _text_reaches(text: str, where: _Where, zone: _Zone, *, glob_text: str | None = None) -> bool:
    """Whether a command-like text reaches the zone as written or with its variables expanded
    each way (`${CLAUDE_CONFIG_DIR}`, `$XDG_CONFIG_HOME`, `$HOME`, `$CLAUDE_PLUGIN_ROOT`).
    `glob_text` is the same text as its glob checks read it (`_globbing_code`); by default the
    text itself."""
    return any(
        _expanded_text_reaches(variant, where, zone, glob_text=globbing)
        for variant, globbing in _expansions(text, text if glob_text is None else glob_text)
    )


def _expanded_text_reaches(text: str, where: _Where, zone: _Zone, *, glob_text: str) -> bool:
    """Whether a command-like text names the zone, has a word that resolves into it (~, $VARS,
    symlinks), or has a glob that can match it, a parent of it or a path inside it, or changes
    or replaces the zone or a folder that holds it.

    `where.known` is False when the text may run elsewhere than `where.cwd`: a relative glob that
    can name the zone's directory then counts without a `cd`.

    The store's directory name counts wherever it is a whole token (`'coven-memory'`,
    `/coven-memory/`, `env -C ~ rm -rf coven-memory`, a Python path built from parts), but not
    inside a longer name (`coven-memory-notes`, `mycoven-memory`). A short, generic store name
    therefore blocks ordinary commands: doctor warns about it.

    A Bash command's globs are read from its glob text (`_globbing_code`: a quoted glob character
    is literal) and, in a heredoc body read as text, only from words at a fixed place
    (`_shell_glob_words`). Names, paths and writes are read from the text as written."""
    cwd, cwd_known = where.cwd, where.known
    if _names_token(text, zone.tokens) or _names_token(_unquoted(text), zone.tokens):
        return True
    reals = [os.path.realpath(root) for root in zone.roots]
    roots = (*(root.parts for root in zone.roots), *(Path(real).parts for real in reals))
    words = _words_of(text)
    cwd_moves = (
        not cwd_known
        or (bool(CHANGE_DIR.intersection(words)) and not where.cds_clear)
        or bool(zone.parents and _names_parent(_words_of(glob_text), zone))
    )
    if cwd_moves and (
        _bash_glob_names_zone(glob_text, zone)
        if cwd_known
        else _relative_glob_names_zone(words, zone)
    ):
        return True
    globs = _shell_glob_words(glob_text) if cwd_known else words
    if any(GLOB_CHARS.intersection(word) and _glob_hits(word, cwd, zone, roots) for word in globs):
        return True
    if any(_in_zone(_resolve(word, cwd), zone) for word in words):
        return True
    return _changes_zone_holder(text, cwd, zone)


def _in_zone(path: str | None, zone: _Zone) -> bool:
    """Whether a resolved path is one of the zone's roots, inside one, a sibling of a zone file
    (`<file>.<anything>`), or ends in its suffixes; not when it is one of the session's own
    scratch folders or inside one (`_in_own`)."""
    if path is None or _in_own(path, zone):
        return False
    if any(_inside(path, os.path.realpath(root)) for root in zone.roots):
        return True
    if any(path.startswith(f"{os.path.realpath(file)}.") for file in zone.siblings):
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


def _bare(raw: str) -> str:
    """The characters of a word as written outside quotes and not escaped: those the shell may
    still read as a brace or a glob."""
    kept: list[str] = []
    quote: str | None = None
    index = 0
    while index < len(raw):
        char = raw[index]
        if quote is not None:
            if char == quote:
                quote = None
            elif quote == '"' and char == "\\":
                index += 1
        elif char in "'\"":
            quote = char
        elif char == "\\":
            index += 1
        else:
            kept.append(char)
        index += 1
    return "".join(kept)


def _may_expand(words: Sequence[_ShellWord]) -> bool:
    """Whether a word may expand to other words, an option among them (`{-i,x}`, `*` by a file
    named `-i`, `$v`)."""
    return any("$" in word.raw or BRACE_OR_GLOB.intersection(_bare(word.raw)) for word in words)


def _script_word(name: str, args: Sequence[_ShellWord]) -> _ShellWord | None:
    """The script file a shell, python or node runs (`bash -x f`, `python3 -I f`): its first
    operand, after short options from SCRIPT_RUNNERS only. None for any other command, any other
    option (`-c`, `-m`, `-e`, `--eval=` make the operand code or a module), standard input (`-`),
    or an operand that expands to anything but one path (SCRIPT_PATH: not `{-c,code}`, a glob, or
    `$Y-c$IFS"code"`)."""
    allowed = PYTHON_SCRIPT_OPTIONS if PYTHON_NAME.fullmatch(name) else SCRIPT_RUNNERS.get(name)
    if allowed is None:
        return None
    for word in args:
        value = word.value
        if value is None:
            return word if SCRIPT_PATH.fullmatch(word.raw) else None
        if not value.startswith("-"):
            return word
        if value.startswith("--") or value == "-" or not set(value[1:]) <= allowed:
            return None
    return None


def _sed_files(args: Sequence[_ShellWord], folders: Iterable[str] = ()) -> list[_ShellWord]:
    """The files `sed` only reads: the operands after its script, when its options are only
    SED_PRINT_OPTIONS (no `-i`, no `-e` or `-f`, nothing that takes a value), its script only
    prints (SED_PRINT_SCRIPT: no `w` or `e` command), and no word may expand (`_may_expand`) or,
    after the script, starts with `-` (GNU sed reads an option there too). A word that starts
    with a name of `folders` (`$CM/a.md`, `_bound_folders`) expands to a path, never an option."""
    names = frozenset(folders)
    if _may_expand([word for word in args if not _in_folder(word, names)]):
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


def _find_acts(args: Sequence[_ShellWord]) -> bool:
    """Whether a `find` has an action (FIND_ACTION_PREFIXES: `-delete`, `-exec`, `-fprint` and
    the like) or a word that may expand to one (`_may_expand`)."""
    return _may_expand(args) or any(
        _unquoted(word.raw).startswith(FIND_ACTION_PREFIXES) for word in args
    )


def _find_reads(args: Sequence[_ShellWord]) -> list[_ShellWord]:
    """Every word of a `find` that has no action (`_find_acts`)."""
    return [] if _find_acts(args) else list(args)


def _reader_acts(name: str, args: Sequence[_ShellWord]) -> bool:
    """Whether a reader of a zone's `readers` may write a file or run a program given these
    arguments: `sort` with an option that names a file or a program (`-o`, `--compress-program`),
    `uniq` with two operands (the second is its output), `printf -v NAME` or `-vNAME` (it sets a
    variable: `_printed_names`), or `_runs_program`."""
    if name == "sort":
        return not _sort_reads(args)
    if name == "uniq":
        return not _uniq_reads(args)
    if name in VARIABLE_PRINTERS:
        return bool(_printed_names(args))
    return _runs_program(name, args)


def _expands_to_option(words: Sequence[_ShellWord]) -> bool:
    """Whether a word may expand to something that starts with `-`: it may expand (`_may_expand`)
    and does not start with `/` or `~`, which expand to a path."""
    return _may_expand([word for word in words if not word.raw.startswith(("/", "~"))])


def _sort_reads(args: Sequence[_ShellWord]) -> bool:
    """Whether the options of `sort` only change how it sorts (SORT_OPTION: not `-o`, `-T`,
    `--output` or `--compress-program`, nor an abbreviation of one), and no word may be one."""
    if _expands_to_option(args):
        return False
    return all(
        SORT_OPTION.fullmatch(_unquoted(word.raw))
        for word in args
        if _unquoted(word.raw).startswith("-")
    )


def _uniq_reads(args: Sequence[_ShellWord]) -> bool:
    """Whether `uniq` has at most one operand (the second one is where it writes), every word
    after a `--` and a lone `-` one (`uniq a -- -out`, `uniq - out`)."""
    operands = _operands([_unquoted(word.raw) for word in args])
    return len(operands) <= 1 and not _expands_to_option(args)


def _runs_program(name: str, args: Sequence[_ShellWord]) -> bool:
    """Whether a reader of a zone's `readers` may run a program: an option of PROGRAM_OPTIONS
    (`rg --pre`), or a word that may expand to one (`_expands_to_option`). Only a PROGRAM_READERS
    command has such options."""
    return name in PROGRAM_READERS and (
        any(_unquoted(word.raw).startswith(PROGRAM_OPTIONS) for word in args)
        or _expands_to_option(args)
    )


def _copy_operands(args: Sequence[_ShellWord]) -> list[_ShellWord] | None:
    """The operands of `cp`, when its options are only COPY_OPTIONS letters; None otherwise."""
    options = [word for word in args if _unquoted(word.raw).startswith("-")]
    if any(
        letters is None or not set(letters) <= COPY_OPTIONS
        for letters in map(_option_letters, options)
    ):
        return None
    return [word for word in args if word not in options]


def _copy_sources(name: str, args: Sequence[_ShellWord]) -> list[_ShellWord]:
    """The sources of `cp` (every operand but the last, which is where it writes) and of `tar`
    creating an archive (`tar -cf ARCHIVE SRC...`: every operand after the archive), when its
    options are only COPY_OPTIONS or ARCHIVE_OPTIONS letters and no word may expand to an option
    or to another destination (`_may_expand`; a word that starts with `~` is a path). Nothing
    otherwise: `cp -t DIR`, `cp -l`, `tar -x`, `--remove-files` write or link."""
    if _may_expand([word for word in args if not word.raw.startswith("~")]):
        return []
    if name == "cp":
        operands = _copy_operands(args)
        return [] if operands is None else operands[:-1]
    cluster = (args[0].value or "").removeprefix("-") if args else ""
    operands = list(args[1:])
    if "c" not in cluster or not set(cluster) <= ARCHIVE_OPTIONS:
        return []
    if any(_unquoted(word.raw).startswith("-") for word in operands):
        return []
    return operands[1:] if "f" in cluster else operands


def _command_reads(
    words: Sequence[_ShellWord], zone: _Zone, folders: Iterable[str] = ()
) -> list[_ShellWord]:
    """The words of one simple command that only read or run a file: a program run by its path
    (`./x`, `"/abs/x"`, `~/x`), every argument of READ_COMMANDS and of the zone's `readers` that
    write and run no program (`_reader_acts`), of `find` without actions and the files of a
    printing `sed` (`_sed_files`, given `folders`; for a zone with `shell_reads`, `_sed_reads`),
    the script a shell, python or node runs (`_script_word`), and for a zone with `copy_reads` the
    sources of a copy or an archive (`_copy_sources`). A zone with `shell_reads` reads past the
    wrappers in front of the command (`_unwrap`). The arguments of a program that is run are never
    among them, nor an assignment (`X+=/a/b`)."""
    words = _unwrap(words, zone)
    if not words or BINDING.match(words[0].raw):
        return []
    name = _program_name(words[0], zone)
    args = words[1:]
    if name is None:
        return []
    if "/" in name:
        return [words[0]]
    if name in READ_COMMANDS:
        return list(args)
    if name in zone.readers:
        return [] if _reader_acts(name, args) else list(args)
    if zone.copy_reads and name in ("cp", "tar"):
        return _copy_sources(name, args)
    if name == "find":
        return _find_reads(args)
    if name == "sed":
        return (_sed_reads(args) or []) if zone.shell_reads else _sed_files(args, folders)
    script = _script_word(name, args)
    return [script] if script else []


def _pipelines(commands: Sequence[_SimpleCommand]) -> Iterator[list[_SimpleCommand]]:
    """The commands in runs joined by pipes; a command with no pipe is a run of its own."""
    run: list[_SimpleCommand] = []
    for command in commands:
        if run and not command.after_pipe:
            yield run
            run = []
        run.append(command)
    if run:
        yield run


def _filters(run: Sequence[_SimpleCommand], reader: _SimpleCommand, zone: _Zone) -> bool:
    """Whether every other command of a pipeline only prints what it reads (`_prints_only`), so
    no path the reader prints is acted on (`| xargs rm`, `| sh`, `| sed 's/^/rm /e'`)."""
    return all(command is reader or _prints_only(command.words, zone) for command in run)


def _prints_only(words: Sequence[_ShellWord], zone: _Zone) -> bool:
    """A PIPE_FILTERS command, or a sed whose scripts run nothing and write no file
    (`_sed_prints_only`), and for a zone with `shell_reads` one of its `readers` that writes and
    runs nothing (`_reader_acts`). A sed `-i` edits only its own operands, which are never blanked
    as reads."""
    if not words:
        return False
    if words[0].value in PIPE_FILTERS:
        return True
    if zone.shell_reads:
        name = _program_name(words[0], zone)
        if name in PIPE_FILTERS or (name in zone.readers and not _reader_acts(name, words[1:])):
            return True
        if name == "xargs" and _xargs_prints_only(words[1:], zone):
            return True
    return words[0].value == "sed" and _sed_prints_only(words[1:])


def _xargs_prints_only(args: Sequence[_ShellWord], zone: _Zone) -> bool:
    """Whether an `xargs`, by its arguments, only prints: options it knows (XARGS_FLAGS and the
    like), then a reader in XARGS_READERS by name or in TRUSTED_READER_FOLDERS. xargs appends the
    words it reads to that reader's own, so no word of them can make it write or run."""
    command = _xargs_command(args)
    return bool(command) and _program_name(command[0], zone) in XARGS_READERS


def _xargs_command(args: Sequence[_ShellWord]) -> Sequence[_ShellWord]:
    """The command an xargs runs, after the options `_xargs_option_length` reads; none when an
    option is not one of them."""
    at = 0
    while at < len(args):
        value = args[at].value
        if value is None:
            return []
        if value == "--":
            return args[at + 1 :]
        if not value.startswith("-"):
            return args[at:]
        length = _xargs_option_length(value)
        if length == 0:
            return []
        at += length
    return []


def _xargs_option_length(option: str) -> int:
    """How many words an xargs option takes, its value included; 0 for one it does not know."""
    if option.startswith("--"):
        name, equals, _ = option[2:].partition("=")
        if name in XARGS_LONG_FLAGS and not equals:
            return 1
        return (1 if equals else 2) if name in XARGS_LONG_VALUED else 0
    for at, letter in enumerate(option[1:], 1):
        if letter in XARGS_VALUED:
            return 1 if option[at + 1 :] else 2
        if letter not in XARGS_FLAGS:
            return 0
    return 1 if len(option) > 1 else 0


def _only_read(word: _ShellWord, command: str, spans: Sequence[tuple[int, int]]) -> bool:
    """Whether a lone assignment (`S=~/.claude/plugins/x`) binds a name whose every other mention
    in `command`, as a whole token in any form (`$S`, `${S}`, `export S`, a heredoc body), lies
    in `spans`, and whose value is one word that is not an option (or a `$(...)` that the
    caller found only reads: its text is no value)."""
    name, _, raw_value = word.raw.partition("=")
    value = _unquoted(raw_value)
    if not word.substitutions and (value.startswith("-") or COMMAND_BLANKS.intersection(value)):
        return False
    for match in re.finditer(rf"(?<!\w){re.escape(name)}(?!\w)", command):
        at = match.start()
        if not (word.start <= at < word.end or any(start <= at < end for start, end in spans)):
            return False
    return True


def _program_name(word: _ShellWord, zone: _Zone) -> str | None:
    """What a command word names: its value, and for a zone with `shell_reads` the name of a
    program in one of TRUSTED_READER_FOLDERS (`/usr/bin/grep` is `grep`)."""
    name: str | None = _shell_words().program_name(word)
    if name is not None and zone.shell_reads:
        for folder in TRUSTED_READER_FOLDERS:
            rest = name.removeprefix(folder)
            if rest != name and rest and "/" not in rest:
                return rest
    return name


def _wrapper_end(words: Sequence[_ShellWord]) -> int:
    """Where the command a wrapper runs starts: after `timeout DURATION`, `nice [-n N]` or `env
    [-i] [-u NAME] [NAME=VALUE]` (READ_ENVIRONMENT), or `command` and the program it runs. 0 when
    the first word is none of them, or has an option or a value this does not read (`env -C`,
    `timeout --signal`, `command -v`)."""
    name = words[0].value
    at = 1
    if name == "timeout":
        while at + 1 < len(words) and words[at].value in ("-k", "-s"):
            at += 2
        valid = at < len(words) and TIMEOUT_DURATION.fullmatch(words[at].raw)
        return at + 1 if valid else 0
    if name == "nice":
        if words[1:2] and words[1].value == "-n" and words[2:3] and words[2].raw.isdigit():
            return 3
        return 2 if words[1:2] and NICE_OPTION.fullmatch(words[1].raw) else 1
    if name == "command":
        return 0 if _unquoted(words[1].raw).startswith("-") else 1
    if name != "env":
        return 0
    while at < len(words):
        word = words[at]
        if word.value == "-i" or READ_ENVIRONMENT.fullmatch(word.raw):
            at += 1
        elif word.value == "-u" and at + 1 < len(words) and _plain_name(words[at + 1].raw):
            at += 2
        else:
            break
    return at if at > 1 and not _unquoted(words[min(at, len(words) - 1)].raw).startswith("-") else 0


def _unwrap(words: Sequence[_ShellWord], zone: _Zone) -> Sequence[_ShellWord]:
    """The words of the command that follows what only sets its environment or limits it
    (READ_ENVIRONMENT assignments, `timeout`, `nice`, `env`: `_wrapper_end`), for a zone with
    `shell_reads`; the words as they are for any other, and where nothing follows."""
    while zone.shell_reads and len(words) > 1:
        head = words[0]
        if BINDING.match(head.raw):
            follows = 1 if READ_ENVIRONMENT.fullmatch(head.raw) else 0
        else:
            follows = _wrapper_end(words)
        if not 0 < follows < len(words):
            break
        words = words[follows:]
    return words


def _plain_name(name: str) -> bool:
    """Whether a read may set the variable `name` (PLAIN_NAME, not in BLOCKED_NAMES or starting
    with one of BLOCKED_PREFIXES)."""
    return (
        PLAIN_NAME.fullmatch(name) is not None
        and name not in BLOCKED_NAMES
        and not name.startswith(BLOCKED_PREFIXES)
    )


def _outside_zone(target: str | None, zone: _Zone, cwd: str) -> bool:
    """Whether a file a command writes is `/dev/null` or a path from `/` or `~/`, as written and
    as each word its brace expansion makes (`_brace_members`), that is not in the zone: a path
    from a variable or relative to a `cd` is not read."""
    discard: str = _shell_words().DISCARD
    if target == discard:
        return True
    if target is None or (zone.writes_inside and _written_near_zone(target, cwd, zone)):
        return False
    members = _brace_members(target)
    return members is not None and all(
        word.startswith(("/", "~/")) and not _is_zone_path(word, cwd, zone)
        for word in (target, *members)
    )


def _written_near_zone(target: str, cwd: str, zone: _Zone) -> bool:
    """Whether a file a command writes may alias a folder (`_through_alias`), is a word the guard
    does not follow that may name the zone (`_unpinned_word`), or is an own scratch folder with
    kept names (`_fills_kept`)."""
    current = os.path.realpath(cwd)
    return (
        _through_alias(target, current)
        or _unpinned_word(target, current, zone)
        or _fills_kept(_resolve(target, current), zone)
    )


def _writes_nothing(each: _SimpleCommand, zone: _Zone, cwd: str) -> bool:
    """Whether a simple command writes no file but `/dev/null` and one outside the zone
    (`_outside_zone`), and its other redirections only read a file or name a file descriptor
    (`2>&1`, `>&2`). A heredoc is data."""
    discard = _shell_words().DISCARD
    return (
        all(_outside_zone(target, zone, cwd) for target in each.write_paths)
        and all(target is not None for target in each.input_files)
        and all(
            target is not None and (target == discard or DESCRIPTOR_TARGET.fullmatch(target))
            for target in each.redirect_inputs
        )
    )


def _text_reads(text: str, zone: _Zone, cwd: str) -> bool:
    """Whether a command text only reads (`_reads_only`); not when the scanner cannot follow it
    (shell_words.simple_commands, with the plain `$(...)` of `Word.substitutions` read)."""
    commands = _scanned_commands(text)
    return (
        commands is not None
        and not _evaluates_unknown(commands, zone)
        and _reads_only(_bound_view(commands, _bound_paths(text, commands), zone), zone, cwd)
    )


def _substitutions_read(word: _ShellWord, zone: _Zone, cwd: str) -> bool:
    """Whether a word runs no command (RUNS_COMMAND) but plain `$(...)` that the scanner read
    whole, whose texts only read (`_text_reads`)."""
    if not RUNS_COMMAND.search(word.raw):
        return True
    whole = word.raw.count("$(") == len(word.substitutions) and not re.search(r"`|[<>]\(", word.raw)
    return whole and all(_text_reads(inner, zone, cwd) for inner in word.substitutions)


def _loop_header(words: Sequence[_ShellWord], zone: _Zone, cwd: str) -> bool:
    """Whether `for NAME in WORDS` lists words that only name files: NAME is a variable a read
    may set (`_plain_name`) and no word runs a command but reads (`_substitutions_read`)."""
    return (
        len(words) >= 3
        and words[2].value == "in"
        and _plain_name(words[1].raw)
        and all(_substitutions_read(word, zone, cwd) for word in words[3:])
    )


def _sed_reads(args: Sequence[_ShellWord]) -> list[_ShellWord] | None:
    """The files a `sed` reads, when it only prints: its options are only SED_PRINT_OPTIONS and
    `-e SCRIPT`, and its scripts print only (`_sed_runs_nothing`: a regex address, `s`, a range
    ending in `$`) and write no file. None otherwise: `-i`, `-f`, an `s` with the `w` or `e`
    flag, a file that starts with `-` or may expand to one (`_expands_to_option`)."""
    scripts: list[str] = []
    operands: list[_ShellWord] = []
    words = iter(args)
    for word in words:
        letters = _option_letters(word)
        if word.value == "-e":
            script = next(words, None)
            if script is None or script.value is None:
                return None
            scripts.append(script.value)
        elif letters is not None:
            if not set(letters) <= SED_PRINT_OPTIONS:
                return None
        else:
            operands.append(word)
            operands += words
    if not scripts and operands:
        first = operands.pop(0)
        if first.value is None:
            return None
        scripts.append(first.value)
    if not scripts or not _sed_runs_nothing("\n".join(scripts)):
        return None
    if _expands_to_option(operands) or any(
        _unquoted(word.raw).startswith("-") for word in operands
    ):
        return None
    return operands


def _copies_out(args: Sequence[_ShellWord], zone: _Zone, cwd: str) -> bool:
    """Whether `cp` only copies to a place outside the zone: its options are only COPY_OPTIONS,
    its last operand is the destination (`_outside_zone`), and no source may expand to an option."""
    operands = _copy_operands(args)
    return (
        operands is not None
        and len(operands) >= 2
        and not _expands_to_option(operands)
        and _outside_zone(operands[-1].value, zone, cwd)
    )


def _proper_words(words: Sequence[_ShellWord]) -> Sequence[_ShellWord]:
    """The words of a simple command after those that open a command in a compound one
    (COMMAND_OPENERS: `do`, `then`, `!`, `{`)."""
    while words and words[0].value in COMMAND_OPENERS:
        words = words[1:]
    return words


def _command_only_reads(each: _SimpleCommand, zone: _Zone, cwd: str) -> bool:
    """Whether a simple command writes and runs nothing, whatever its operands: after the words
    that open a command in a compound one (COMMAND_OPENERS), a closer of one, a `for` header
    (`_loop_header`), a lone assignment, maybe after `export`, of a plain name (`_plain_name`)
    that keeps no command text (`_keeps_command_text`), or a program that only reads
    (`_program_only_reads`). No word may run a command but a plain `$(...)` that only reads
    (`_substitutions_read`)."""
    words = _proper_words(each.words)
    if not words:  # zsh runs NULLCMD or READNULLCMD for a bare redirection
        return not (zone.shell_reads and any(name in os.environ for name in NULL_COMMANDS))
    if not all(_substitutions_read(word, zone, cwd) for word in words):
        return False
    if zone.shell_reads and _sets_for_programs(words, exported=False):
        return False
    head = words[0].value
    if head in COMPOUND_CLOSERS:
        return len(words) == 1
    if head == "for":
        return _loop_header(words, zone, cwd)
    if head == "export":
        words = words[1:]
    if all(BINDING.match(word.raw) for word in words):
        kept = zone.shell_reads and any(map(_keeps_command_text, words))
        return not kept and all(map(_plain_assignment, words))
    return _program_only_reads(each, _unwrap(words, zone), zone, cwd)


def _program_only_reads(
    each: _SimpleCommand, words: Sequence[_ShellWord], zone: _Zone, cwd: str
) -> bool:
    """Whether a command, after its wrappers (`_unwrap`), is a program that only reads: a `cd`, a
    reader of READ_COMMANDS or of the zone's `readers` that writes and runs nothing
    (`_reader_acts`), a `find` without actions, a printing `sed` (`_sed_reads`: a `-i` is no
    print), a `read` of plain names, a `tee` or a `cp` to a place outside the zone, or for a zone
    with `shell_reads` an `xargs` that only prints (`_xargs_prints_only`). No command writes a file
    by an argument (`sort -o`) but `tee`."""
    name = _program_name(words[0], zone)
    if BINDING.match(words[0].raw) or name is None or "/" in name:
        return False
    args = words[1:]
    if name == "tee":
        return all(_outside_zone(file, zone, cwd) for file in each.argument_writes) and all(
            word.value in ("-a", None) or not word.value.startswith("-") for word in args
        )
    if each.argument_writes:
        return False
    if name == "read":
        return all(
            (_option_letters(word) or "x") in READ_OPTIONS or _plain_name(word.raw) for word in args
        )
    if name == "cp":
        return _copies_out(args, zone, cwd)
    if name in READ_COMMANDS or name in CHANGE_DIR:
        return True
    if name in SHELL_STRING_RUNNERS:
        return _shell_string_reads(args, zone, cwd)
    return _acting_reader_reads(name, args, zone)


def _acting_reader_reads(name: str, args: Sequence[_ShellWord], zone: _Zone) -> bool:
    """Whether a reader that can also act, by its arguments, only reads: one of the zone's
    `readers` (`_reader_acts`), `find` (`_find_acts`), a printing `sed` (`_sed_reads`) or, for a
    zone with `shell_reads`, `xargs` (`_xargs_prints_only`)."""
    if name in zone.readers:
        return not _reader_acts(name, args)
    if name == "find":
        return not _find_acts(args)
    if name == "xargs":
        return zone.shell_reads and _xargs_prints_only(args, zone)
    return name == "sed" and _sed_reads(args) is not None


def _shell_string_reads(args: Sequence[_ShellWord], zone: _Zone, cwd: str) -> bool:
    """Whether `sh -c STRING` runs a text that only reads (`_text_reads`): the string a literal, and
    nothing else on the command, no option or argument that sets `$0`, `$1`, ..."""
    return (
        len(args) == 2
        and args[0].value == "-c"
        and args[1].value is not None
        and _text_reads(args[1].value, zone, cwd)
    )


def _reads_only(commands: Sequence[_SimpleCommand], zone: _Zone, cwd: str) -> bool:
    """Whether every command writes and runs nothing (`_command_only_reads`, `_writes_nothing`),
    so the text changes no file wherever a `cd` goes. A loop or an `if` is a few commands, each
    one judged on its own: its keywords run nothing."""
    return all(
        _writes_nothing(each, zone, cwd) and _command_only_reads(each, zone, cwd)
        for each in commands
    )


def _only_reads_text(command: str, zone: _Zone, cwd: str) -> bool:
    """Whether a Bash command, for a zone with `cd_reads` or `loop_reads`, only reads
    (`_reads_only`): it writes nothing wherever it goes, so it reaches the zone by no write. Not
    when a read writes a file that code of the text may run (`_feeds_code`), nor when the text
    sets what its programs read (`_arms_reads`)."""
    if not (zone.cd_reads or zone.loop_reads):
        return False
    commands = _scanned_commands(command)
    return (
        commands is not None
        and not _feeds_code(commands, zone, cwd)
        and not _arms_reads(commands, zone)
        and _text_reads(command, zone, cwd)
    )


def _arms_reads(commands: Sequence[_SimpleCommand], zone: _Zone) -> bool:
    """Whether a text, for a zone with `shell_reads`, sets a variable its programs may read
    (`_sets_for_programs`), every lone assignment exported after `set -a` or `set -o allexport`:
    `export TAR_OPTIONS=--remove-files` makes `tar -c` delete what it reads."""
    if not zone.shell_reads:
        return False
    every = any(_exports_all(_proper_words(each.words)) for each in commands)
    return any(_sets_for_programs(_proper_words(each.words), exported=every) for each in commands)


def _evaluates_unknown(commands: Sequence[_SimpleCommand], zone: _Zone) -> bool:
    """Whether a text, for a zone with `shell_reads`, may read a value it does not know to be a
    number (`_number_word`) as a variable's name or a number (`_evaluated_words`), which runs the
    command substitutions in its subscript: `D='a[$(rm x)]'; test -v "$D"` runs `rm x`. Its reads
    are then no reads."""
    if not zone.shell_reads:
        return False
    numbers = _number_names(commands)
    for each in commands:
        evaluated = _evaluated_words(_proper_words(each.words))
        if evaluated is None or not all(_number_word(word, numbers) for word in evaluated):
            return True
    return False


def _evaluated_words(words: Sequence[_ShellWord]) -> list[_ShellWord] | None:
    """The words a command reads as numbers: those with an arithmetic expansion, a test's number
    comparison's operands (`_test_operands`), a NUMBER_COMMANDS one's arguments and the
    arguments of a printf with a number conversion (`_printf_operands`). None when it reads a
    word as a variable's name (NAME_EXPANSION, a test's `-v`) or may read any word as one."""
    if any(NAME_EXPANSION.search(word.raw) for word in words):
        return None
    found = [word for word in words if ARITHMETIC_EXPANSION.search(word.raw)]
    name, args = (words[0].value if words else None), words[1:]
    operands: list[_ShellWord] | None = []
    if name in TEST_COMMANDS:
        operands = _test_operands(args)
    elif name in NUMBER_COMMANDS:
        operands = list(args)
    elif name == "printf":
        operands = _printf_operands(args)
    return None if operands is None else [*found, *operands]


def _test_operands(args: Sequence[_ShellWord]) -> list[_ShellWord] | None:
    """The words a `test` or `[` reads as numbers: with a number comparison (NUMBER_TESTS) every
    word but its operators. None when it names a variable (`-v`, `-R`: NAME_TESTS) or a word
    the guard cannot read stands where an operator does (`test "$O" "$D"`)."""
    words = list(args)
    if words[-1:] and words[-1].value == "]":
        words = words[:-1]
    while words[:1] and words[0].value == "!":
        words = words[1:]
    values = [word.value for word in words]
    operator = {2: values[:1], 3: values[1:2]}.get(len(values), [])
    if NAME_TESTS.intersection(values) or None in operator:
        return None
    if not NUMBER_TESTS.intersection(values):
        return []
    return [word for word in words if word.value not in {*NUMBER_TESTS, *TEST_WORDS}]


def _printf_operands(args: Sequence[_ShellWord]) -> list[_ShellWord] | None:
    """The words a printf may read as numbers, which zsh evaluates: those after a format with a
    number conversion (NUMBER_CONVERSION); None after a format the guard cannot read."""
    if args[:1] and args[0].value == "--":
        args = args[1:]
    if len(args) < 2:
        return []
    format_text = args[0].value
    if format_text is None:
        return None
    return list(args[1:]) if NUMBER_CONVERSION.search(format_text) else []


def _number_word(word: _ShellWord, numbers: frozenset[str]) -> bool:
    """Whether a word holds a number: an integer, a special parameter (`$?`, `$#`), a variable of
    `numbers`, a count (`_counts`), or arithmetic of these alone (`_number_arithmetic`)."""
    return _holds_number(word.raw, word.value, word.substitutions, numbers)


def _holds_number(
    raw: str, value: str | None, substitutions: Sequence[str], numbers: frozenset[str]
) -> bool:
    """`_number_word` of a word given as its text, its value and its substitutions' texts."""
    if value is not None:
        return INTEGER.fullmatch(value) is not None
    variable = VARIABLE_WORD.fullmatch(raw)
    if variable:
        name = variable[1] or variable[2]
        return name is None or name in numbers
    if SUBSTITUTION_WORD.fullmatch(raw) and len(substitutions) == 1:
        return _counts(substitutions[0])
    return _number_arithmetic(raw, numbers)


def _number_arithmetic(raw: str, numbers: frozenset[str]) -> bool:
    """Whether every arithmetic expansion of a word (ARITHMETIC_EXPANSION) has no expansion of
    its own and names only variables of `numbers`, and nothing else of the word expands."""
    rest = raw
    for match in ARITHMETIC_EXPANSION.finditer(raw):
        expression = match[1] if match[1] is not None else match[2]
        if expression is None or RUNS_COMMAND.search(expression) or "[" in expression:
            return False
        if not set(re.findall(r"[A-Za-z_]\w*", expression)) <= numbers:
            return False
        rest = rest.replace(match[0], "")
    return "$" not in rest and "`" not in rest


def _counts(text: str) -> bool:
    """Whether a substitution's text prints only numbers: every pipeline of it (`a || echo 0`)
    ends in a command that prints a count or an integer, or a test that prints nothing
    (`_prints_number`)."""
    commands = _scanned_commands(text)
    if not commands:
        return False
    pairs = zip(commands, [*commands[1:], None], strict=True)
    ends = [each for each, after in pairs if not (after and after.after_pipe)]
    return all(_prints_number(_proper_words(each.words)) for each in ends)


def _prints_number(words: Sequence[_ShellWord]) -> bool:
    """Whether a command prints only a number or nothing: a `grep -c` of one file or of its
    input, a `wc` of its input with COUNT_OPTIONS alone, an `echo` of integers, or a test that
    reads no word as a number or name (`[ -f x ]`)."""
    if not words:
        return False
    name, args = words[0].value, words[1:]
    if name == "echo":
        return all(INTEGER.fullmatch(word.value or "x") for word in args)
    if name in TEST_COMMANDS:
        return _evaluated_words(words) == []
    if name not in COUNTERS:
        return False
    letters = [_option_letters(word) for word in args if (word.value or "").startswith("-")]
    operands = [word for word in args if not (word.value or "x").startswith("-")]
    if any(letter is None for letter in letters):
        return False
    flags = set("".join(letter or "" for letter in letters))
    if name == "wc":
        return not operands and flags <= COUNT_OPTIONS
    return "c" in flags and len(operands) <= (1 if flags & {"e", "f"} else 2)


def _number_names(commands: Sequence[_SimpleCommand]) -> frozenset[str]:
    """The variables a text only sets to numbers (`_number_value` of each assignment), and by no
    other word: no command is given the name (`read n`, `for n`) or `-vNAME` (`printf -vn`)."""
    assigned: dict[str, list[_ShellWord]] = {}
    given: set[str] = set()
    for each in commands:
        for word in each.words:
            if match := BINDING.match(word.raw):
                assigned.setdefault(match[1], []).append(word)
            elif word.value is not None:
                given |= {word.value, word.value.removeprefix(PRINTF_VARIABLE)}
    return frozenset(
        name
        for name, words in assigned.items()
        if name not in given and all(map(_number_value, words))
    )


def _number_value(word: _ShellWord) -> bool:
    """Whether an assignment's value holds a number (`_holds_number`, knowing no variable)."""
    value = None if word.value is None else word.value.partition("=")[2]
    return _holds_number(word.raw.partition("=")[2], value, word.substitutions, frozenset())


def _exports_all(words: Sequence[_ShellWord]) -> bool:
    """Whether a command is `set` with `-a` or `-o allexport`: each later assignment is exported."""
    if not words or words[0].value != "set":
        return False
    values = [word.value or "" for word in words[1:]]
    return "allexport" in values or any("a" in (_option_letters(word) or "") for word in words[1:])


def _sets_for_programs(words: Sequence[_ShellWord], *, exported: bool) -> bool:
    """Whether a command sets a name a program may read (`_arms_programs`): by an assignment
    alone, maybe after `export`, or by `printf -v NAME` (`-vNAME` too)."""
    if not words:
        return False
    head = words[0].value
    if head == "export":
        exported, words = True, words[1:]
    if words and all(BINDING.match(word.raw) for word in words):
        names = [match[1] for word in words if (match := BINDING.match(word.raw))]
    elif head in VARIABLE_PRINTERS:
        names = _printed_names(words[1:])
    else:
        return False
    return any(_arms_programs(name, exported=exported) for name in names)


def _printed_names(args: Sequence[_ShellWord]) -> list[str]:
    """The names `printf -v NAME` and `printf -vNAME` set."""
    found: list[str] = []
    for index, word in enumerate(args):
        value = word.value or ""
        if value == PRINTF_VARIABLE:
            found += [next_word.value or "" for next_word in args[index + 1 : index + 2]]
        elif value.startswith(PRINTF_VARIABLE):
            found.append(value.removeprefix(PRINTF_VARIABLE))
    return found


def _arms_programs(name: str, *, exported: bool) -> bool:
    """Whether setting `name` may change what a program does, not only how it prints (READ_NAME):
    when it is exported, set in the hook's environment (the shell then hands on the new value),
    or no name a read may set (`_plain_name`)."""
    if READ_NAME.fullmatch(name):
        return False
    return exported or name in os.environ or not _plain_name(name)


def _runs_code_from_file(each: _SimpleCommand, zone: _Zone) -> bool:
    """Whether a simple command may run code from a file or standard input: after its leading
    assignments and wrappers, a program by its path (but a reader in TRUSTED_READER_FOLDERS) or
    one the guard cannot name (`$RUN`), a CODE_RUNNERS or python program, or a `find` that runs a
    command. Not the closer of a compound command (`}`, `done`)."""
    words = _proper_words(each.words)
    while words and ASSIGNMENT.match(words[0].raw):
        words = words[1:]
    words = _unwrap(words, zone)
    if not words or words[0].raw in GROUP_CLOSERS:
        return False
    name = _program_name(words[0], zone)
    if name is None:
        return True
    base = _basename(name)
    if base == "xargs" and "/" not in name:
        return not _xargs_prints_only(words[1:], zone)
    if "/" in name or base in CODE_RUNNERS or PYTHON_NAME.fullmatch(base):
        return True
    return base == "find" and any(
        (word.value or "").startswith(("-exec", "-ok")) for word in words[1:]
    )


def _writes_text(each: _SimpleCommand, zone: _Zone) -> bool:
    """Whether a simple command writes a file (not /dev/null) with text it was given or read: any
    writer but code that runs (`_runs_code_from_file`: its output is its own), and a copy of
    what the text feeds it (`_copies_input`)."""
    if _copies_input(each):
        return True
    return each.writes and (_writes_for_many(each) or not _runs_code_from_file(each, zone))


def _copies_input(each: _SimpleCommand) -> bool:
    """Whether a copy (CHANGES_DESTINATION: `cp`, `install`, `rsync`) may write what the text
    gives it: it is fed a heredoc, a here-string, a file or a pipe, or copies standard input
    (STANDARD_INPUT: `/dev/stdin`, `/proc/self/fd/0`, `-`)."""
    words = _proper_words(each.words)
    if not words or _basename(words[0].value or "") not in CHANGES_DESTINATION:
        return False
    fed = (
        bool(each.heredocs)
        or each.after_pipe
        or bool(each.input_files)
        or any(not DESCRIPTOR_TARGET.fullmatch(target or "") for target in each.redirect_inputs)
    )
    return fed or any(STANDARD_INPUT.fullmatch(word.value or "") for word in words[1:])


def _writes_for_many(each: _SimpleCommand) -> bool:
    """Whether a simple command's redirection takes the output of other commands: the closer of a
    compound command (`} > f`, `done > f`, a subshell's `) > f`), or a bare `exec > f`."""
    words = [word.raw for word in _proper_words(each.words)]
    return each.writes and (not words or words[0] in GROUP_CLOSERS or words == ["exec"])


def _feeds_code(commands: Sequence[_SimpleCommand], zone: _Zone, cwd: str) -> bool:
    """Whether a text, for a zone with `shell_reads`, writes a file with text (`_writes_text`) and
    runs a command that may run that file: code (`_runs_code_from_file`), or any command that
    does not only read (`_command_only_reads`: `git commit` runs a hook, `crontab`, `at` and
    `npm run` a file's lines), but a compound command's closer (`} > f`: `_writes_for_many`),
    whose commands are judged on their own. What a read prints there, or a text names, may become
    a script (`ls -d ~/.claude/* | sed 's/^/rm -rf /' > /tmp/s; sh /tmp/s`)."""
    return (
        zone.shell_reads
        and any(_writes_text(each, zone) for each in commands)
        and any(
            _runs_code_from_file(each, zone)
            or not (_writes_for_many(each) or _command_only_reads(each, zone, cwd))
            for each in commands
        )
    )


def _read_runs(
    commands: Sequence[_SimpleCommand], zone: _Zone, *, feeding: bool
) -> Iterator[list[_SimpleCommand]]:
    """The runs of a text (`_pipelines`) whose reads may be blanked: in a text that feeds code
    (`feeding`, `_feeds_code`), not a run that writes a file with text (`_writes_text`), whose
    reads are what the file holds."""
    for run in _pipelines(commands):
        if not (feeding and any(_writes_text(each, zone) for each in run)):
            yield run


def _read_value(word: _ShellWord, zone: _Zone, cwd: str) -> bool:
    """Whether a lone assignment may be blanked as a read: its value runs nothing but `$(...)` that
    only read, of a plain name (`_only_reads_value`), and keeps no command text for later (a
    quoted or escaped `$(` or backtick, an ANSI-C `$'...'`), which a subscript of it would run."""
    if _keeps_command_text(word) or not _substitutions_read(word, zone, cwd):
        return False
    return not word.substitutions or _plain_assignment(word)


def _keeps_command_text(word: _ShellWord) -> bool:
    """Whether an assignment keeps command text for later, which a subscript of its name would
    run: a quoted or escaped `$(` (more than the word's own substitutions) or an ANSI-C `$'...'`,
    whose escapes may spell one."""
    return "$'" in word.raw or _unquoted(word.raw).count("$(") > len(word.substitutions)


def _only_reads_value(word: _ShellWord, zone: _Zone, cwd: str) -> bool:
    """Whether an assignment of a plain name (`_plain_assignment`) has only `$(...)` that read
    (`_substitutions_read`) in its value."""
    return _plain_assignment(word) and _substitutions_read(word, zone, cwd)


def _plain_assignment(word: _ShellWord) -> bool:
    """Whether an assignment (`X=...`, `X+=...`) sets a name a read may set (`_plain_name`)."""
    return _plain_name(word.raw.partition("=")[0].rstrip("+"))


def _binds_or_moves(each: _SimpleCommand) -> bool:
    """Whether a simple command sets a variable or changes the folder (`cd`, `pushd`, `for`,
    `read`, `printf -v`, an assignment), after the words that open a command in a compound one.
    Reading what such a command names does not make the rest of the text free of it: a name bound
    there is used later, and a `cd` moves where the relative words point."""
    words = _proper_words(each.words)
    return not words or (
        words[0].value in {*CHANGE_DIR, "for", "read"}
        or (words[0].value in VARIABLE_PRINTERS and bool(_printed_names(words[1:])))
        or all(BINDING.match(word.raw) for word in words)
    )


def _blankable(each: _SimpleCommand, zone: _Zone, cwd: str) -> bool:
    """Whether a whole simple command, redirections and heredoc included, only reads
    (`_command_only_reads`, `_writes_nothing`) and binds no name and moves nowhere
    (`_binds_or_moves`), and no compound command closes on a redirection (`done < ~/x` feeds the
    loop): its text may be blanked, since nothing it names is acted on later."""
    return (
        bool(each.words)
        and each.words[0].value not in COMPOUND_CLOSERS
        and not _binds_or_moves(each)
        and _writes_nothing(each, zone, cwd)
        and _command_only_reads(each, zone, cwd)
    )


def _command_spans(
    commands: Sequence[_SimpleCommand], zone: _Zone, cwd: str, *, feeding: bool
) -> list[tuple[int, int]]:
    """The spans of the commands of a text that only read (`_blankable`), for a zone with
    `shell_reads`, each in a pipeline only when the rest of it only prints (`_filters`), in a run
    whose reads may be blanked (`_read_runs`)."""
    return [
        (each.start, each.end)
        for run in _read_runs(commands, zone, feeding=feeding)
        for each in run
        if zone.shell_reads
        and (len(run) == 1 or _filters(run, each, zone))
        and _blankable(each, zone, cwd)
    ]


def _read_spans(command: str, zone: _Zone, cwd: str) -> list[tuple[int, int]]:
    """The spans of a Bash command, as written, that only read or run a file (`_command_reads`,
    given the zone's `readers`), for a zone a session may read: blanked, they leave what writes.
    A read in a pipeline counts only when the rest of it only prints (`_filters`). Then a lone
    assignment whose name is only used in those spans (`S=<dir>; bash $S/scripts/x`).

    None when the scanner cannot follow the command (shell_words.simple_commands), when the shell
    may hand a read's words to a later command (WORDS_HANDED_ON: `$_`, a trap's
    `$BASH_COMMAND`), or when a group, subshell, loop or `if` is piped, since its own commands
    are not marked as in the pipeline (`(find x) | xargs rm`): it is then read whole. So is a
    text that feeds code (`_feeds_code`) with a redirection that takes many commands' output
    (`_writes_for_many`); in one without, a run that writes such a file is not blanked
    (`_read_runs`)."""
    if WORDS_HANDED_ON.search(command):
        return []
    commands: list[_SimpleCommand] | None = _shell_words().simple_commands(
        command, substitutions=zone.shell_reads
    )
    if commands is None or any(
        each.in_pipeline and (not each.words or each.words[0].raw in GROUP_CLOSERS)
        for each in commands
    ):
        return []
    feeding = _feeds_code(commands, zone, cwd)
    if feeding and any(_writes_for_many(each) for each in commands):
        return []
    if _arms_reads(commands, zone) or _evaluates_unknown(commands, zone):
        return []  # a read may then write: `export TAR_OPTIONS=--remove-files; tar -c ~/x`
    lone = [word for word in _lone_assignments(commands) if _read_value(word, zone, cwd)]
    folders = (
        _bound_paths(command, commands) if zone.shell_reads else _bound_folders(command, commands)
    )
    while True:  # a folder name whose every mention is not in a read is no folder: read again
        view = _bound_view(commands, folders, zone)
        spans = [
            (word.start, word.end)
            for run in _read_runs(view, zone, feeding=feeding)
            for reader in run
            if len(run) == 1 or _filters(run, reader, zone)
            if not zone.shell_reads or all(_substitutions_read(w, zone, cwd) for w in reader.words)
            for word in _command_reads(reader.words, zone, folders)
        ] + _command_spans(view, zone, cwd, feeding=feeding)
        kept = {name: word for name, word in folders.items() if _only_read(word, command, spans)}
        if kept == folders:
            break
        folders = kept
    return spans + _read_assignments(lone, command, spans)


def _read_assignments(
    lone: Sequence[_ShellWord], command: str, spans: Sequence[tuple[int, int]]
) -> list[tuple[int, int]]:
    """The spans of the lone assignments whose name only reads use (`_only_read`), given the spans
    that read and, as it grows, these: `J=<file>; n=$(grep -c x $J)` has the second's value in a
    read only once the second is one. A name assigned more than once (`n=$(grep ...)` in a loop
    and again after it) counts its other assignments as reads: they all go or none does."""
    found: list[tuple[int, int]] = []
    pending = list(lone)
    while True:
        more = [
            word
            for word in pending
            if _only_read(word, command, [*spans, *found, *_assignments_of(word, pending)])
        ]
        if not more:
            return found
        found += [(word.start, word.end) for word in more]
        pending = [word for word in pending if word not in more]


def _assignments_of(word: _ShellWord, words: Sequence[_ShellWord]) -> list[tuple[int, int]]:
    """The spans of the assignment words among `words` that set the name `word` sets."""
    name = word.raw.partition("=")[0]
    return [(each.start, each.end) for each in words if each.raw.partition("=")[0] == name]


def _in_folder(word: _ShellWord, names: frozenset[str]) -> bool:
    """Whether a word is `$NAME` or `$NAME/path`, maybe in double quotes, for a NAME in `names`."""
    match = FOLDER_OPERAND.fullmatch(word.raw)
    return match is not None and match["name"] in names


def _bound_folders(command: str, commands: Sequence[_SimpleCommand]) -> dict[str, _ShellWord]:
    """The names the line's first command binds to a folder (`CM=~/x/y; sed -n 1p $CM/a.md`), by
    the assignment word: `$CM/...` then expands to one word that starts with `/`, never to an
    option. Only when that command is lone assignments at the very start of the line (not in a
    group, a subshell or after a test), followed by `;`, `&&` or a newline (not `&` or `|`: a
    subshell), each value a plain path (FOLDER_VALUE: from `/`, `~/` or `$HOME/`, no blank or
    glob), each name assigned once, and on a line that sets neither IFS nor HOME and runs no
    NAME_SETTERS (`read $n$m` sets a name `_only_read` cannot see). The caller keeps only names
    whose every mention is in a read (`_only_read`)."""
    first = next((each for each in commands if each.words), None)
    if first is None or command[: first.words[0].start].strip():
        return {}
    if not all(ASSIGNMENT.match(word.raw) for word in first.words):
        return {}
    after = command[first.words[-1].end :].lstrip(" \t")
    if not (after.startswith(("&&", "\n")) or (after[:1] == ";" and after[1:2] not in (";", "&"))):
        return {}
    if SPLIT_VARIABLE.search(command) or any(
        word.value in NAME_SETTERS for each in commands for word in each.words
    ):
        return {}
    assigned = Counter(
        match[1] for each in commands for word in each.words if (match := BINDING.match(word.raw))
    )
    folders: dict[str, _ShellWord] = {}
    for word in first.words:
        name, _, value = word.raw.partition("=")
        if FOLDER_VALUE.fullmatch(value) and assigned[name] == 1:
            folders[name] = word
    return folders


def _lone_assignments(commands: Sequence[_SimpleCommand]) -> list[_ShellWord]:
    """The assignment words of the commands that are assignments only (`do n=$(ls)` too)."""
    return [
        word
        for each in commands
        if (words := _proper_words(each.words)) and all(ASSIGNMENT.match(w.raw) for w in words)
        for word in words
    ]


def _bound_paths(command: str, commands: Sequence[_SimpleCommand]) -> dict[str, _ShellWord]:
    """The names a text binds once, in a lone assignment of a plain path (FOLDER_VALUE) wherever
    it stands (`F=~/x; sed -n 1p $F`), by the assignment word: `$F` then expands to a word that
    starts with `/` or `~`, never an option, or to nothing. Each such name has no other mention
    than `$NAME` (`_references`: not `for NAME`, `read NAME`), and the text sets neither IFS nor
    HOME and runs no NAME_SETTERS. The caller keeps the names a reader alone uses (`_only_read`)."""
    if SPLIT_VARIABLE.search(command) or any(
        word.value in NAME_SETTERS for each in commands for word in each.words
    ):
        return {}
    assigned = Counter(
        match[1] for each in commands for word in each.words if (match := BINDING.match(word.raw))
    )
    paths: dict[str, _ShellWord] = {}
    for word in _lone_assignments(commands):
        name, _, value = word.raw.partition("=")
        if FOLDER_VALUE.fullmatch(value) and assigned[name] == 1 and _references(name, command):
            paths[name] = word
    return paths


def _references(name: str, command: str) -> bool:
    """Whether every mention of a name in a command, as a whole token, is `$NAME` or `${NAME`, but
    for its one assignment."""
    mentions = len(re.findall(rf"(?<!\w){re.escape(name)}(?!\w)", command))
    return mentions == 1 + len(re.findall(rf"\$\{{?{re.escape(name)}(?!\w)", command))


def _bound_view(
    commands: Sequence[_SimpleCommand], names: Mapping[str, _ShellWord], zone: _Zone
) -> list[_SimpleCommand]:
    """The commands with each word of a bound name (`_bound_paths`) read as its path, for a zone
    with `shell_reads`: no word is then an option. The others as they are."""
    if not zone.shell_reads or not names:
        return list(commands)
    paths = {name: word.raw.partition("=")[2] for name, word in names.items()}
    bound: list[_SimpleCommand] = _shell_words().bind_names(commands, paths)
    return bound


def _clear_folder(path: str | None, zone: _Zone) -> bool:
    """Whether a resolved folder is neither in the zone nor one that holds it (or is it)."""
    return (
        path is not None
        and not _in_zone(path, zone)
        and not any(_inside(os.path.realpath(root), path) for root in zone.roots)
    )


def _clear_cd(each: _SimpleCommand, cwd: str, zone: _Zone) -> bool:
    """Whether a `cd` or `pushd` has one literal operand from `/` or `~` (after a `--`) that is a
    folder clear of the zone (`_clear_folder`)."""
    operands = [word for word in _proper_words(each.words)[1:] if word.value != "--"]
    if len(operands) != 1 or operands[0].value is None:
        return False
    return operands[0].value.startswith(("/", "~")) and _clear_folder(
        _resolve(operands[0].value, cwd), zone
    )


def _cds_clear(command: str, cwd: str, zone: _Zone, climb: int) -> bool:
    """Whether the folder a Bash command starts in and each `cd` of its text lead nowhere near the
    zone, so a relative word after one names no part of it: by the scanned commands
    (`_scanned_cds_clear`), or, where the scanner cannot follow the text or a `cd` sits in a
    quoted string (`ssh h 'cd /x && ...'`), by the text itself with the `climb` folders above
    each cd's (`_written_cds_clear`, `_glob_climb`)."""
    if not _clear_folder(os.path.realpath(cwd), zone):
        return False
    return _scanned_cds_clear(command, cwd, zone) or _written_cds_clear(command, cwd, zone, climb)


def _scanned_cds_clear(command: str, cwd: str, zone: _Zone) -> bool:
    """Whether each `cd` of a text is clear (`_clear_cd`): every `cd` word of the text
    (`_words_of`: one in a quote or a heredoc counts) is the command word of a `cd` of the scanned
    text, and no word has a `..` component."""
    commands = _scanned_commands(command)
    if commands is None:
        return False
    if any(UP_PATH.search(_unquoted(word.raw)) for each in commands for word in each.words):
        return False
    moves = [
        each
        for each in commands
        if (words := _proper_words(each.words)) and words[0].value in CHANGE_DIR
    ]
    named = [word for word in _words_of(command) if word in CHANGE_DIR]
    return len(named) == len(moves) and all(_clear_cd(each, cwd, zone) for each in moves)


def _written_cds_clear(command: str, cwd: str, zone: _Zone, climb: int) -> bool:
    """Whether each `cd` of a text, read from the text as written, is clear: every `cd` or `pushd`
    word (CD_WORD, as many as `_words_of` finds, so `c''d` is none of them) has one literal
    operand from `/` or `~/` (CD_OPERAND) whose folder and the ones a glob may climb to
    (`_glob_climb`) are clear of the zone, there is no `popd`, and no word has a `..` component."""
    words = _words_of(command)
    if "popd" in words or any(UP_PATH.search(word) for word in words):
        return False
    found = list(CD_WORD.finditer(command))
    if len(found) != sum(word in CHANGE_DIR for word in words):
        return False
    for each in found:
        operand = CD_OPERAND.match(command, each.end())
        path = operand and (operand.group("quoted") or operand.group("bare"))
        if not path or path == "~" or not _clear_up(_resolve(path, cwd), climb, zone):
            return False
    return True


def _glob_climb(glob_text: str) -> int:
    """How many folders up a glob of a Bash call's glob text (reads blanked) may climb: bash
    before 5.2 matches `..` with `.*`, so `.*/.claude` after `cd ~/proj` is `~/.claude`. The most
    components in one word the shell may expand (`_shell_glob_words`) that climb (`_climbed`)."""
    return max(
        (_climbed(word).split("/").count("..") for word in _shell_glob_words(glob_text)),
        default=0,
    )


def _clear_up(folder: str | None, climb: int, zone: _Zone) -> bool:
    """Whether a folder and the `climb` folders above it are clear of the zone (`_clear_folder`)."""
    if folder is None:
        return False
    path = Path(folder)
    return all(_clear_folder(str(above), zone) for above in [path, *path.parents][: climb + 1])


def _runs_code(each: _SimpleCommand, zone: _Zone) -> bool:
    """Whether a simple command, after its wrappers, is an interpreter (INTERPRETERS, python by
    PYTHON_NAME) that takes its code from a word, standard input or a heredoc: not one that runs
    a script file (`_script_word`), which stays a read of that file."""
    words = _unwrap(_proper_words(each.words), zone)
    name = _program_name(words[0], zone) if words else None
    if name is None:
        return False
    name = _basename(name)
    known = name in INTERPRETERS or PYTHON_NAME.fullmatch(name) is not None
    return known and _script_word(name, words[1:]) is None


def _spellings(zone: _Zone) -> list[str]:
    """The ways a text may write the roots of the zone: as paths, from `~` and from `$HOME`."""
    home = Path.home()
    found: list[str] = []
    for root in zone.roots:
        found += [str(root), os.path.realpath(root)]
        if root.is_relative_to(home):
            rest = root.relative_to(home).as_posix()
            found += [f"~/{rest}", f"$HOME/{rest}", f"${{HOME}}/{rest}"]
    return found


def _code_names_zone(text: str, zone: _Zone) -> bool:
    """Whether code names the zone: a root written out (`_spellings`), `CLAUDE_CONFIG_DIR`, or
    `.claude` with a way to find the home folder (HOME_LOOKUP) in the same code."""
    return (
        any(spelling in text for spelling in _spellings(zone))
        or "CLAUDE_CONFIG_DIR" in text
        or (CLAUDE_FOLDER in text and HOME_LOOKUP.search(text) is not None)
    )


def _interpreter_names_zone(command: str, zone: _Zone) -> bool:
    """Whether a Bash command runs interpreter code that names the zone (`_runs_code`,
    `_code_names_zone`). The code is the text of the command and of the commands piped into or
    out of it, heredocs included (`cat <<'E' | python3 -`). Not a script file it runs, nor one
    written by `cat > FILE <<'E'` to run later. False when the scanner cannot follow the text:
    the other checks read it whole."""
    commands = _scanned_commands(command)
    if commands is None:
        return False
    return any(
        _code_names_zone(
            "\n".join(command[each.start : each.end] for each in run if each.words), zone
        )
        for run in _pipelines(commands)
        if any(_runs_code(each, zone) for each in run)
    )


def _bash_touches(call: Call, zone: _Zone) -> bool:
    """A command run in the zone, or whose text reaches it. For a zone a session may read
    (`writes_only`), every word that only reads or runs a file is blanked first
    (`_read_spans`): what is left reaches it only by a write."""
    cwd = os.path.realpath(call.cwd)
    bash = _bash(call)
    if zone.writes_only and _only_reads_text(bash.command, zone, call.cwd):
        return False  # it writes nothing, in the zone or out of it
    if any(_inside(cwd, os.path.realpath(root)) for root in zone.roots) and not _in_own(cwd, zone):
        return True
    code, glob_code = bash.code, _globbing_code(bash)
    if zone.writes_only:
        if zone.shell_reads and _interpreter_names_zone(bash.command, zone):
            return True
        spans = _read_spans(bash.command, zone, call.cwd)
        code, glob_code = _blank(code, spans), _blank(glob_code, spans)
    if zone.writes_inside and _odd_job_mention(code):
        return True  # CLAUDE_JOB_DIR used otherwise than as written out: where it goes is unknown
    clear = zone.shell_reads and _cds_clear(bash.command, call.cwd, zone, _glob_climb(glob_code))
    return _text_reaches(code, _Where(call.cwd, cds_clear=clear), zone, glob_text=glob_code)


def _is_zone_path(text: str, cwd: str, zone: _Zone) -> bool:
    """Whether `text`, trimmed, is a path in the zone (a path may hold spaces)."""
    return _in_zone(_resolve(text.strip(), cwd), zone)


def _string_reaches(text: str, cwd: str, zone: _Zone) -> bool:
    """An MCP input string that is a path in the zone, or whose words reach it as a Bash
    command's would: an MCP tool may run a command or a query, in a directory of its own. For
    the user folder a `file://` URL is read as its path, and a relative path with a component
    that may be the folder or `.claude.json` (`.claude/x`, `../.claude.json`) counts: the
    tool's own folder may be home."""
    if zone.writes_inside:
        text = FILE_URL.sub(lambda url: urllib.parse.unquote(url[1]), text)
        if _relative_names_holder(text.strip(), zone):
            return True
    return _is_zone_path(text, cwd, zone) or _text_reaches(text, _Where(cwd, known=False), zone)


def _relative_names_holder(path: str, zone: _Zone) -> bool:
    """Whether a relative path has a component that names a root of the zone (`zone.globs`) or a
    sibling of one of its files (`.claude.json.bak`), as written: an MCP tool expands no glob."""
    if path.startswith(("/", "~", "$")):
        return False
    siblings = tuple(f"{file.name}." for file in zone.siblings)
    return any(part in zone.globs or part.startswith(siblings) for part in path.split("/"))


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
    reach = (
        "nor reach it through MCP tools; in Bash it may only read it (cat, ls, grep, find) and run"
        " its scripts"
        if zone.writes_only
        else "nor reach it through Bash or MCP tools (use Read)"
    )
    return Refused(
        f"{PREFIX} a '{role}' session cannot write to {zone.label}, {reach}; only the GM session"
        " may: ask the GM. In Bash, glob-like text in a heredoc body or a script counts too"
        " (`**x**`, `.c*`): write the script with the Write tool and run it in its own Bash call",
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
    for each in _zones(store):
        zone = _with_own(each, call)
        if _bash_touches(_job_written_out(call, zone), zone):
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
    for each in _zones(store):
        zone = _with_own(each, call)
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
