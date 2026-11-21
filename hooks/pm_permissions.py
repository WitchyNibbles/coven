#!/usr/bin/env python3
"""PermissionRequest hook: answer a project manager's permission prompts, because no one is there.
(With `--pre-tool-use` the same script is the PreToolUse guard; see the end of this text.)

Reads the hook payload on stdin. A project manager (PM) is a background `claude --bg` session
started with COVEN_SESSION_ROLE=pm. Its launch profile makes `git push`, `git merge` and `gh`
prompt even in bypassPermissions mode, and a PM would wait on the prompt until the user attaches.
This hook answers instead:

- A session whose COVEN_SESSION_ROLE is not exactly `pm` (unset, empty, `gm`...) gets nothing:
  exit 0 with no output, so its normal prompt shows. Only a PM session is ever decided for.
- In a PM session the hook always exits 0 and prints exactly one decision, and logs it with
  `hooklog.log_event` (the module next to this script). It approves a Bash command made only of
  `cd <dir>`, plain `git push` of a feature branch, read-only git, and `gh pr create`,
  `gh pr view` and `gh pr checks`; it refuses everything else, with a message that sends the step
  to the GM (the user's main session).

Claude Code ignores exit 2 on this event and lets any other failure through to the prompt, so the
only way to fail closed is a deny decision on stdout: a bad payload, an internal error and a check
running past DEADLINE all print one.

A command is approved only when the whole text is understood. Any of the characters in FORBIDDEN
(control characters but tab, Unicode spaces, quotes, `$`, backticks, redirects, globs, comments,
braces, backslash) refuses it, and so does a word with a `~` other than `~` or `~/...` (bash
expands `~-`, `~+`, `~N`). The one redirect accepted is the word `2>&1` (stderr into stdout, set
off by spaces or tabs) as the last word of a git or gh segment; the one pipe accepted is the last
segment, `tail` or `head` with no option, `-n <N>` or `-<N>`, fed by a git or gh segment. The rest
is tokenised like a shell would, split on `&&`, `||`, `;`, `|` and parentheses, and every segment
must be `cd` with one directory, `gh pr create|view|checks ...` (options as below) or
`git [-C dir] [--no-pager] <push|read-only command> ...`.

Quotes are refused everywhere but one place: the tokenizer cannot tell a quoted word from an
injection, so `gh pr create --body "..."` is denied, and the message tells the PM to write the body
to a file and pass `--body-file <path>` (words with no quote, `$` or other metacharacter are
fine: `--title Add_login`, `--fill`). The one quoted thing approved is the whole value of the title
option of `gh pr create` (`--title "..."`, `--title '...'`, `--title="..."`, `-t "..."`). Before
anything else reads the text, each such value is checked (ASCII letters, digits, spaces and
`-_.,:;()/#?!*&|<>[]{}+=@%^~` and the quote it is not wrapped in, so `"Don't"` and `'Say "hi"'`:
no `$`, backtick, backslash, control character or non-ASCII character, and not empty) and swapped for the control character TITLE_WORD, which the
original text may not hold. The quotes are then gone, the value is one word to the tokenizer, and a
`|` or `;` inside it splits nothing. The option parser (`_check_gh`) accepts a TITLE_WORD only as
the value of `--title` or `-t` of `gh pr create`; anywhere else (another option, `gh pr view`, a
non-gh command, after `--`) it refuses the command.

A `gh pr` command may not open a browser or an editor (`--web`, `-w`, `--editor`, `-e`: they hang a
background session; `gh pr checks --watch` is fine). It may name a repository (`--repo OWNER/NAME`,
`-R OWNER/NAME`, `--repo=OWNER/NAME`) only when it is the one the `origin` remote of every
directory it may run in names (case aside, read with git as a GitHub URL: `https://github.com/O/N`,
the scp form (`<user>@github.com:O/N`, user `git` only) or the ssh form
(`ssh://<user>@github.com/O/N`, user `git` only), with or without `.git`; the URL git uses after a
rewrite must agree); a missing origin, another host, another repository, a `HOST/OWNER/NAME`
value and any other spelling is refused. `--body-file` (`-F`), `--template` (`-T`) and `--recover`
are approved for a relative path with no `..` that resolves inside the payload's cwd (from every
directory a `cd` may lead to) and that deny-secrets' `target_hit` would not block, so a PM cannot
post a key file or `/etc/passwd` to a pull request. `--body-file` and `-F` alone are also approved
for an absolute path with no `..` whose realpath is inside the temp dir (`/tmp` and `$TMPDIR` of
the hook's environment, each after realpath; a `$TMPDIR` that is relative, `/`, the home directory
or above it is ignored; `tempfile` is not asked) and that, when the hook decides, is a regular file
and not a symlink, with one hard link, owned by the user running the hook, not writable by its
group or others, and not blocked by `target_hit`.

The read-only commands are merge-base, merge-tree, diff, log, show, status, rev-parse, rev-list,
ls-files, cat-file and branch (listing forms only). Each has an allow-list of options
(READ_ONLY_OPTIONS and the branch parser), never a deny-list: git accepts abbreviated long options
(`--out=f` for `--output=f`), and only an exact match passes. Nothing that writes a file, runs a
program (`--ext-diff`, `--textconv`), reads config or a repository from the command line (`-c`,
`--config-env`, `--git-dir`, `--work-tree`, `--namespace`) or reads a command list from stdin is on
a list, and a `%G` placeholder in `--format` or `--pretty` (git runs gpg for it) is refused.
`git fetch` is not approved: it moves refs, creates tags and can run programs through config.

Every directory a push could run in is checked with git (a `cd` may fail, and `;`, `||` and
subshells make the real directory uncertain, so each `cd` adds a candidate and none is dropped):
the remote (the one named, else the one git picks: branch.<b>.pushRemote, remote.pushDefault,
branch.<b>.remote, origin) must be a configured name; no configured push refspec, mirror,
receivepack, pushurl, vcs, URL rewrite (insteadOf), push option, followTags or submodule recursion
may widen or redirect the push; and every branch the push would update (named, `HEAD`, a refspec
destination such as `HEAD:main` or `feature:refs/heads/main`, or the current and upstream branch of
a bare `git push`) must not be main, master, pre, staging, develop, trunk, production or the
remote's default branch as recorded in refs/remotes/<remote>/HEAD (any letter case), and must be a
plain branch under refs/heads. A GIT_* variable in the hook's environment (other than GIT_TERMINAL_PROMPT,
GIT_EDITOR, GIT_PAGER, GIT_SEQUENCE_EDITOR) refuses every push: the shell's git would read config
the hook cannot see; the same variables refuse the read-only commands.

Limits: this is text analysis of one command, not a shell. It does not see aliases or functions
that shadow `git`, `gh` or `cd`, PATH, CDPATH, variables a CLAUDE_ENV_FILE adds to the shell, the
git hooks a push runs, or config that a push triggers beyond what is checked here. A read-only
command is not checked against the repo's config (core.fsmonitor, diff drivers, a pager, a gpg
program): writing that config needs no prompt, so approving the command adds no power the session
lacks. A word with a `~` anywhere but its start stays refused, so `HEAD~1` goes to
the GM. A push is approved by branch name in every repo, whatever the remote's own protections.
Safety-check prompts skip hooks in Claude Code, so they still wait for the user.

The PreToolUse guard (`pm_permissions.py --pre-tool-use`, Bash only, PM sessions only): the ask
and deny rules of the PM profile match a command by prefix, so `git -C . push --force`,
`git --no-pager push origin main`, `command git push` and `bash -c "git push -f"` match none of
them and, in bypassPermissions mode, run with no prompt and no PermissionRequest. The guard reads
the command with its own scanner (`_scan`), as bash splits it: quoting is kept, so a quoted `;`
or `>` is a word and only unquoted text is an operator; a redirect and its target are dropped (a
descriptor only when written against the operator, `2>f`); comments are skipped; and
`$(...)`, backticks, `<(...)`, `>(...)` (not inside single quotes) and `$((...))` are read too.
It runs decide() when a simple command, in the command or in any substitution, is a `gh` command
or a git `push`, `merge` or `send-pack` (after git's global options and any `NAME=value` prefix),
under any wrappers it can follow (env, nice, nohup, time, timeout, command, exec, builtin, sudo,
xargs, with their options; xargs before git with no subcommand counts); a wrapper it cannot follow,
a shell, eval or make whose words hold `push`, `merge`, `send-pack` or `gh`; a command name that
expands (`$g`, `$(...)`) next to such a word or a git word; or a `$((...))` with a git or gh word.
A heredoc body is data, but for its unquoted-delimiter substitutions, unless it feeds a shell,
`source`, `eval`, a compound command or a command the guard cannot name: then it is read as
commands. A shell fed by a pipe, an input redirect or a here-string guards a text with a git or gh
word. Text the scanner cannot follow (an unbalanced quote or substitution, an unended heredoc,
`$'..'`, nesting past MAX_NESTING) guards it when it holds a `git` or `gh` word. A word in a
commit message, a search pattern, a quoted string, a file name or a heredoc fed to cat does not
count. A refusal blocks the call (exit 2, the message on stderr, which holds in
bypassPermissions). Anything decide() approves, and every other command, prints nothing, so the
plain forms still reach the ask rule and this hook's PermissionRequest answer. A bad payload, an
internal error or a check past DEADLINE blocks too. It is best effort against an agent that follows its role: GitHub branch
protection on the default branch is the real wall.
"""

import importlib
import json
import logging
import os
import re
import shlex
import signal
import stat
import subprocess
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from types import ModuleType
from typing import NoReturn, TextIO

# `-I` leaves this folder off sys.path, so put it there for hook_support, the one module found so.
sys.path.insert(0, str(Path(__file__).resolve().parent))
# By name, not an import statement: ruff rejects one below code (E402) and suppressions are banned.
# A crash here would exit 2, which Claude Code ignores for a PermissionRequest: the permission
# prompt would stay open. So a broken or missing hook_support is kept and answered by main().
try:
    hook_support = importlib.import_module("hook_support")
    SUPPORT_ERROR: str | None = None
except Exception as exc:
    logging.exception("coven pm-permissions: hook_support cannot be loaded")
    hook_support = ModuleType("hook_support")  # never used: main() refuses before any check runs
    SUPPORT_ERROR = type(exc).__name__

HOOK = "pm-permissions"
PREFIX = f"coven {HOOK}:"
ROLE_VAR = "COVEN_SESSION_ROLE"
PM_ROLE = "pm"
# A hook still running at Claude's hook timeout lets the call through, so the whole check has a
# deadline below it (a hanging git call), and each git call has its own timeout.
DEADLINE = 10.0
GIT_TIMEOUT = 3.0
USAGE = "pm_permissions.py takes no arguments but --pre-tool-use"
GUARD_ARG = "--pre-tool-use"
LOGGED_COMMAND_LIMIT = 200
PROTECTED = ("main", "master", "pre", "staging", "develop", "trunk", "production")
# The remote's own default branch is protected too, whatever it is called (see _remote_default).
PROTECTED_LABEL = ", ".join(PROTECTED) + ", or the remote's default branch"

DENIED = "a PM may not run this. Send it to the GM as a QUESTION or a PROD-STEP and carry on."
ESCALATE = f"{PREFIX} {DENIED} Why: {{reason}}."
SPLIT = (
    f"{PREFIX} {DENIED} Why: {{reason}}. Only plain pushes of feature branches and read-only git"
    " are approved automatically: run the push as its own command, for example git push -u origin"
    " <branch>, with no other commands, quotes, $ or redirects (only 2>&1 and a final | tail or"
    " | head are accepted)."
)
GH_HINT = (
    f"{PREFIX} {DENIED} Why: {{reason}}. gh pr create, gh pr view and gh pr checks are approved"
    " automatically when run on their own or after a cd, with no $, backtick or redirect (only"
    " 2>&1 and a final | tail or | head are accepted). A quote is approved only around the whole"
    " value of --title (or -t) of gh pr create, which must be a double- or single-quoted --title of"
    " ASCII letters, digits, spaces and plain punctuation, with no `$`, backtick, backslash or the"
    " quote it is wrapped in. Put the body in a file and pass --body-file <path>, a relative path"
    " inside the worktree or an absolute path to a file you own (one link, not writable by others)"
    " under /tmp or $TMPDIR. --repo (or -R) is approved only for the repository of this"
    " directory's origin remote."
)
UNCHECKED = (
    f"{PREFIX} could not check this request ({{why}}); refused. Send the step to the GM as a"
    " QUESTION."
)
DAMAGED = (
    f"{PREFIX} could not check this request (coven's hook files are damaged: {{error}}); refused."
    " Reinstall the coven plugin, and send the step to the GM as a QUESTION."
)
FORCE_REASON = "a force push (--force, --force-with-lease, -f or a +refspec)"
TOO_SLOW = "the check took too long"

BASH = "Bash"
# The text of a command that runs a push: how a refused command is told apart from a non-push.
HOLDS_PUSH = re.compile(r"\bgit\b.*\bpush\b", re.DOTALL)
# The same for the gh commands that are approved.
HOLDS_GH_PR = re.compile(r"\bgh\s+pr\s+(?:create|view|checks)\b")
# What the PreToolUse guard looks for in the words of a command it cannot unwrap: a push, merge or
# send-pack word, or a gh word.
HOLDS_GUARDED = re.compile(r"\b(?:push|merge|send-pack)\b|(?<![\w.-])gh(?![\w.-])")
# A git or gh word, for a command the guard cannot read.
HOLDS_GIT_OR_GH = re.compile(r"(?<![\w.-])(?:git|gh)(?![\w.-])")
# The git subcommands the guard runs decide() for, and the characters that make a word something
# the shell expands into an unknown one (a subcommand, or a command name). `[` and `[[` are tests.
GUARDED_SUBCOMMANDS = frozenset({"push", "merge", "send-pack"})
EXPANDING = re.compile(r"[$`{*?\[~]")
TEST_COMMANDS = frozenset({"[", "[["})
# git's global options that take the next word as their value.
GIT_VALUED_OPTIONS = frozenset(
    {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--super-prefix", "--config-env"}
)
# Shells, and the commands that run text as shell commands: a heredoc fed to one is commands.
SHELLS = frozenset({"bash", "sh", "zsh", "dash", "ksh", "mksh", "ash", "fish", "csh", "tcsh"})
COMMAND_READERS = SHELLS | {"source", ".", "eval"}
# The last word of a compound command (`{ bash; } <<EOF`, `while ...; done <<EOF`): what it runs
# with a heredoc is not one command the guard can name.
COMPOUND_ENDS = frozenset({"}", "done", "fi", "esac"})
# What a command a heredoc or a pipe feeds is, when it is not a name: none (a lone redirect, or a
# compound command), or unknown (a name that expands, or a wrapper the guard cannot unwrap).
NO_COMMAND = ""
UNKNOWN_COMMAND = "$"
# Commands that run the rest of their words as a command (or that make does), and the words a
# shell puts before a command.
WRAPPERS = SHELLS | {
    "eval",
    "exec",
    "builtin",
    "xargs",
    "env",
    "command",
    "nohup",
    "time",
    "sudo",
    "nice",
    "timeout",
    "make",
}
SHELL_PREFIXES = frozenset({"{", "}", "!", "if", "then", "else", "elif", "do", "while", "until"})
ASSIGNMENT = re.compile(r"[A-Za-z_]\w*=")
QUOTE_CHARS = re.compile(r"['\"\\]")
GH_PR_COMMANDS = frozenset({"create", "view", "checks"})
PIPE_SOURCES = frozenset({"git", "gh"})
REPO_REASON = "gh pr with --repo or -R would act on another repository"
TITLE_REASON = "a quote is approved only around the whole value of --title (or -t) of gh pr create"
INTERACTIVE_REASON = (
    "gh pr with --web, -w, --editor or -e opens a browser or an editor, which would hang a"
    " background session"
)
# The short options of gh pr create, view and checks that take a value: the rest of the word, or
# the next word, is that value.
SHORT_VALUED = frozenset("aBbFHlmprTtiq")
# The options whose value is a file gh reads (`--recover` is held to the same rule): the long
# names, and the short letters of --body-file and --template.
FILE_OPTIONS = frozenset({"--body-file", "--template", "--recover"})
SHORT_FILE_OPTIONS = frozenset("FT")
# Only a body may come from the temp dir (an absolute path); a template or a recovery file may not.
TEMP_FILE_OPTIONS = frozenset({"--body-file", "-F"})
# The conventional temp dir, always a root besides $TMPDIR (a hook may run without it set).
STANDARD_TEMP = os.path.join(os.sep, "tmp")
# Long options of gh pr create, view and checks that take a value (besides the file options and
# `--repo`, which are checked). A title option word right after one is that option's value, not a
# title option.
LONG_VALUED = frozenset(
    {
        "--assignee",
        "--base",
        "--body",
        "--head",
        "--label",
        "--milestone",
        "--project",
        "--reviewer",
        "--json",
        "--jq",
        "--interval",
    }
)
TITLE_OPTIONS = frozenset({"--title", "-t"})
# Besides the shell's own metacharacters: every control character but tab, DEL and the Unicode
# spaces. shlex splits words on space, tab, CR and LF only, so these would be read as part of a
# word by the hook and as something else by a shell or a tool.
FORBIDDEN = re.compile(
    r"[\x00-\x08\x0a-\x1f\x7f\x85\xa0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000"
    r"`$<>\\#'\"*?\[\]{}]"
)
# `2>&1` as a word of its own (a space or tab, or an end, on both sides) is swapped for MERGED
# before the FORBIDDEN check; MERGED itself is refused in the original text, so it can only come
# from here, and only ever as a whole word.
MERGE_STDERR = re.compile(r"(?<![^ \t])2>&1(?![^ \t])")
MERGED = "\x01"
# The one quoted value an approved command may hold: the whole value of `--title`, `--title=` or
# `-t` of gh pr create, in one pair of quotes, as a word of its own. It is swapped for TITLE_WORD
# before the FORBIDDEN check and the split, so the hook never reads its text as shell. TITLE_WORD
# is a control character, refused in the original text, so it can only come from that swap.
TITLE_WORD = "\x02"
TITLE_OPTION = re.compile(
    r"""(?<![^ \t])(--title=|--title[ \t]+|-t[ \t]+)(?:'([^']*)'|"([^"]*)")(?![^ \t])"""
)
# What a title may hold: ASCII letters and digits, a space, and plain punctuation, and the quote it
# is not wrapped in (TITLE_OPTION ends the value at its own quote, so that one cannot occur). Never
# `$`, a backtick, a backslash, a control character or any non-ASCII character.
TITLE_CHARACTERS = re.compile(r"""[A-Za-z0-9 _.,:;()/#?!*&|<>\[\]{}+=@%^~'"-]*""")
TRIMMERS = frozenset({"tail", "head"})
TRIM_OPTIONS = re.compile(r"(?:-n [0-9]+|-[0-9]+)?")
SEPARATORS = frozenset({"&&", "||", ";", "|"})
PARENS = frozenset({"(", ")"})
OPERATOR_CHARS = frozenset("();<>|&")
# The guard's scanner. The shell's operators by length, longest first when read (`|&` is a pipe
# and a lone `&` runs in the background: both separate commands); the redirects among them; and
# the word written right before a redirect that names its descriptor: `2` in `2>&1`, `{fd}` in
# `{fd}>f` (with a space between, it is a word: `echo 2 >f`).
SCAN_OPERATORS = (
    frozenset({"<<-", "<<<", "&>>", ";;&"}),
    frozenset({"&&", "||", "|&", ";;", ";&", ">>", "<<", ">&", "<&", "&>", ">|", "<>"}),
    frozenset(OPERATOR_CHARS),
)
REDIRECTS = frozenset({">", ">>", "<", "<<", "<<-", "<<<", ">&", "<&", "&>", "&>>", ">|", "<>"})
HEREDOCS = frozenset({"<<", "<<-"})
INPUT_REDIRECTS = frozenset({"<", "<<<", "<&", "<>"})  # they feed a command's stdin
PIPES = frozenset({"|", "|&"})
DESCRIPTOR = re.compile(r"\d+|\{[A-Za-z_]\w*\}")
BLANKS = frozenset(" \t")
# Runs of characters with no meaning to the scanner in each context, read in one step.
PLAIN_RUN = re.compile(r"[^ \t\n'\"\\$`;&|()<>\0]+")
DOUBLE_QUOTED_RUN = re.compile(r"[^\"\\$`\0]+")
PARAMETER_RUN = re.compile(r"[^}\\'\"$`\0]+")
BACKTICK_RUN = re.compile(r"[^`\\]+")
BODY_RUN = re.compile(r"[^\\$`]+")
PAREN = re.compile(r"[()]")
# What a backslash escapes inside double quotes, inside backticks and in an unquoted heredoc body.
DOUBLE_QUOTE_ESCAPES = frozenset('$`"\\\n')
BACKTICK_ESCAPES = frozenset("$`\\")
# How deep substitutions, `${...}` and heredocs fed to a shell may nest before the guard gives up
# on the reading and looks for a git or gh word instead.
MAX_NESTING = 8

REMOTE_NAME = re.compile(r"[A-Za-z0-9._-]+")
# `--repo OWNER/NAME`, and the three forms of a GitHub remote URL a repository can be read from.
REPO_VALUE = re.compile(r"[A-Za-z0-9-]+/[A-Za-z0-9._-]+")
GITHUB_REMOTE = re.compile(
    r"(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)"
    r"([A-Za-z0-9-]+)/([A-Za-z0-9._-]+)",
    re.IGNORECASE,
)
HEADS = "refs/heads/"
# A destination starting with one of these is matched by git against more than refs/heads.
QUALIFIED_PREFIXES = ("refs/", "heads/", "tags/", "remotes/")
CURRENT_BRANCH_NAMES = frozenset({"HEAD", "@"})
SAFE_PUSH_DEFAULTS = frozenset({None, "simple", "current", "upstream", "tracking"})
SAFE_OPTIONS = frozenset(
    {
        "--set-upstream",
        "--quiet",
        "--verbose",
        "--progress",
        "--no-progress",
        "--porcelain",
        "--dry-run",
    }
)
SAFE_SHORT_OPTIONS = re.compile(r"-[uqvn]+")
FORCE_OPTIONS = frozenset({"--force", "--force-if-includes", "--force-with-lease"})
FORCE_SHORT_OPTIONS = re.compile(r"-[A-Za-z]*f[A-Za-z]*")
FORCE_WITH_LEASE_VALUE = "--force-with-lease="
# GIT_* variables that cannot change where a push goes or what it sends; any other one set in the
# hook's environment (GIT_DIR, GIT_CONFIG_COUNT/KEY_n/VALUE_n, GIT_CONFIG_PARAMETERS, GIT_NAMESPACE,
# GIT_SSH_COMMAND...) refuses a push, because the shell's git would see config the hook does not.
HARMLESS_GIT_VARIABLES = frozenset(
    {"GIT_TERMINAL_PROMPT", "GIT_EDITOR", "GIT_PAGER", "GIT_SEQUENCE_EDITOR"}
)
# Keys of the repo's effective config that send a push elsewhere or add to it, whatever their value.
# git lowercases the section and variable name of a key before it matches a --get-regexp pattern,
# so these patterns are lowercase and still catch `remote.origin.PushURL`.
REDIRECTING_CONFIG = (
    r"^(remote\..*\.(push|mirror|receivepack|pushurl|vcs)"
    r"|url\..*\.(pushinsteadof|insteadof)"
    r"|push\.pushoption)$"
)
# Boolean settings that widen a push when on (a value git cannot read as a boolean also refuses).
WIDENING_BOOLEANS = ("push.followTags", "submodule.recurse")
# push.recurseSubmodules is a boolean or one of check, on-demand, only, no; only these are off.
SUBMODULE_PUSH_OFF = frozenset({"no", "false", "off", "0"})
PUSH_REMOTE_DEFAULT = "origin"


def _allowed(flags: str = "", valued: str = "", short: str = "") -> re.Pattern[str]:
    """The options a read-only command may have: the exact `flags`, `--name=value` for each name in
    `valued`, and anything fullmatching the `short` regex. An option separated from its value
    (`--output f`) or abbreviated (`--out=f`) matches none of them."""
    alternatives = [re.escape(flag) for flag in flags.split()]
    alternatives += [re.escape(name) + "=.*" for name in valued.split()]
    alternatives += [short] if short else []
    return re.compile("|".join(alternatives))


# Options of diff, log, show and rev-list. None writes a file (no --output), runs a program named
# on the command line (no --ext-diff, --textconv, --show-signature) or reads a list from stdin (no
# --stdin, --no-index). One thing on the list can still run gpg: a %G placeholder in --format or
# --pretty; _check_read_only refuses that.
REVISION_OPTIONS = _allowed(
    flags="""-p -u -s -w -b -a -W -g -i -n --patch --no-patch --raw --stat --shortstat --numstat
        --dirstat --summary --name-only --name-status --check --quiet --exit-code --cached --staged
        --minimal --patience --histogram --no-renames --find-renames --find-copies --no-color
        --color --no-ext-diff --no-textconv --no-prefix --function-context --merge-base
        --ignore-all-space --ignore-space-change --ignore-space-at-eol --ignore-blank-lines
        --ignore-cr-at-eol --text --binary --full-index --abbrev --word-diff --color-words
        --ignore-submodules --submodule --compact-summary --relative --oneline --graph --decorate
        --no-decorate --all --branches --tags --remotes --first-parent --merges --no-merges
        --reverse --follow --abbrev-commit --no-abbrev-commit --left-right --left-only
        --right-only --cherry-pick --cherry-mark --cherry --simplify-by-decoration
        --ancestry-path --full-history --topo-order --date-order --author-date-order --not
        --use-mailmap --no-mailmap --parents --children --count --objects --walk-reflogs
        --regexp-ignore-case --all-match --invert-grep --boundary --no-walk --notes --no-notes
        --dense --sparse --cc""",
    valued="""--stat --dirstat --color --word-diff --color-words --unified --diff-filter --relative
        --find-renames --find-copies --abbrev --src-prefix --dst-prefix --ignore-submodules
        --submodule --diff-algorithm --inter-hunk-context --stat-width --stat-name-width
        --stat-count --max-count --skip --since --until --after --before --author --committer
        --grep --date --format --pretty --decorate --min-parents --max-parents""",
    short=r"-[0-9]+|-n[0-9]+|-U[0-9]+|-[MC][0-9]*%?|-[SG].+",
)
READ_ONLY_OPTIONS = {
    "diff": REVISION_OPTIONS,
    "log": REVISION_OPTIONS,
    "show": REVISION_OPTIONS,
    "rev-list": REVISION_OPTIONS,
    "merge-base": _allowed(flags="--is-ancestor --all -a --octopus --independent --fork-point"),
    # --write-tree writes objects, never a ref.
    "merge-tree": _allowed(
        flags="""--write-tree --name-only --messages --no-messages -z --allow-unrelated-histories
            --merge-base""",
        valued="--merge-base",
    ),
    "status": _allowed(
        flags="""-s -b -z -sb --short --branch --long --porcelain --ignored --show-stash
            --ahead-behind --no-ahead-behind --untracked-files --no-renames --renames""",
        valued="--porcelain --untracked-files --ignored --ignore-submodules",
        short=r"-u(?:no|normal|all)?",
    ),
    "rev-parse": _allowed(
        flags="""--abbrev-ref --short --verify -q --quiet --symbolic-full-name --symbolic
            --show-toplevel --is-inside-work-tree --is-bare-repository --show-prefix --show-cdup
            --branches --tags --remotes --all""",
        valued="--abbrev-ref --short",
    ),
    "ls-files": _allowed(
        flags="""-c -m -o -d -s -u -i -t -z --cached --modified --others --deleted --stage
            --unmerged --ignored --killed --directory --no-empty-directory --exclude-standard
            --full-name --error-unmatch --deduplicate --eol --abbrev""",
        valued="--abbrev --with-tree",
    ),
    "cat-file": _allowed(flags="-t -s -e -p"),
}
APPROVED_SUBCOMMANDS = frozenset({"push", "branch", *READ_ONLY_OPTIONS})
FORMAT_OPTIONS = ("--format=", "--pretty=")
SIGNATURE_PLACEHOLDER = "%G"  # git runs gpg to fill %G?, %GS, %GK...
BRANCH_FLAGS = re.compile(r"-[arv]+|--list|--all|--remotes|--verbose|--show-current")
BRANCH_REF_OPTIONS = frozenset({"--contains", "--merged"})  # each takes one ref


class PayloadError(ValueError):
    """The hook payload does not have the shape of a permission request."""


class DeadlineError(Exception):
    """The check ran past DEADLINE."""


class Unsupported(Exception):
    """The command is not a plain push this hook can check; the message says why."""


class Escalate(Exception):
    """The command is a push the hook will not approve; the GM decides. The message says why."""


@dataclass(frozen=True)
class Call:
    tool: str
    command: str | None  # the Bash command; None for every other tool
    cwd: str


@dataclass(frozen=True)
class Segment:
    """The words of one command, and whether a `|` directly precedes it."""

    words: list[str]
    piped: bool


@dataclass(frozen=True)
class GitStep:
    """One `git` segment: the subcommand, its arguments, and every directory it may run in."""

    directories: tuple[str, ...]
    subcommand: str
    arguments: tuple[str, ...]


@dataclass(frozen=True)
class Refspec:
    destination: str | None  # the branch updated; None: the current branch (HEAD, @)
    local: bool  # the name must also be a local branch (a refspec without a colon)


@dataclass(frozen=True)
class Push:
    remote: str | None
    refspecs: tuple[Refspec, ...]


def _past_deadline(_signum: int, _frame: object) -> NoReturn:
    raise DeadlineError


def _call(payload: Mapping[str, object]) -> Call:
    tool = payload.get("tool_name")
    if not isinstance(tool, str) or not tool:
        raise PayloadError("tool_name is missing")
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        raise PayloadError("tool_input is not an object")
    if tool != BASH:
        return Call(tool, None, "")
    command, cwd = tool_input.get("command"), payload.get("cwd")
    if not isinstance(command, str) or not command.strip():
        raise PayloadError("Bash without a command")
    if not isinstance(cwd, str) or not cwd:
        raise PayloadError("cwd is missing")
    return Call(tool, command, cwd)


def _is_home(word: str) -> bool:
    """`~` and `~/...` are the home directory; bash expands every other `~` form (`~-`, `~+`, `~N`,
    `~user`, `X=~/a`) to something the hook cannot know."""
    return word == "~" or (word.startswith("~/") and "~" not in word[1:])


def _split(text: str) -> list[str]:
    """The words of `text`, split like a shell does (quotes and backslashes resolved)."""
    splitter = shlex.shlex(text, posix=True, punctuation_chars=True)
    splitter.whitespace_split = True
    splitter.commenters = ""
    try:
        return list(splitter)
    except ValueError as exc:
        raise Unsupported(f"the command does not parse ({exc})") from None


def _hide_titles(command: str) -> str:
    """The command with each quoted title value (see TITLE_OPTION) swapped for TITLE_WORD, once its
    text is checked: Unsupported when it is empty or holds anything TITLE_CHARACTERS does not. Any
    other quote is left for the FORBIDDEN check, and whether TITLE_WORD sits in a `gh pr create`
    title option is checked where the words are parsed (`_check_gh`)."""

    def hide(found: re.Match[str]) -> str:
        text = found[2] if found[2] is not None else found[3]
        if not text:
            raise Unsupported("the --title text is empty")
        odd = next((char for char in text if not TITLE_CHARACTERS.fullmatch(char)), None)
        if odd is not None:
            raise Unsupported(f"{odd!r} is not allowed in a --title text of an approved command")
        return found[1] + TITLE_WORD

    return TITLE_OPTION.sub(hide, command)


def _tokens(command: str) -> list[str]:
    """The words of a command, for approving it: anything the hook cannot follow is Unsupported.
    (The guard reads a command with its own scanner, `_scan`.)"""
    for marker in (MERGED, TITLE_WORD):
        if marker in command:
            raise Unsupported(f"{marker!r} is not allowed in an automatically approved command")
    text = MERGE_STDERR.sub(MERGED, _hide_titles(command))
    found = FORBIDDEN.search(text.replace(MERGED, " ").replace(TITLE_WORD, " "))
    if found:
        raise Unsupported(f"{found[0]!r} is not allowed in an automatically approved command")
    words = _split(text)
    for word in words:
        if MERGED in word and word != MERGED:
            raise Unsupported("2>&1 is approved only as a word of its own")
        if TITLE_WORD in word and word not in (TITLE_WORD, f"--title={TITLE_WORD}"):
            raise Unsupported(TITLE_REASON)
        if "~" in word and not _is_home(word):
            raise Unsupported(
                f"{word} would be expanded by the shell, which the hook cannot follow"
            )
    return words


def _is_operator(token: str) -> bool:
    """Whether the tokenizer meant `token` as a shell operator: a run of operator characters and
    nothing else. The empty word (`''`) and a quoted word that only starts with one (`"(a b)"`)
    are words."""
    return bool(token) and all(char in OPERATOR_CHARS for char in token)


def _segments(tokens: Iterable[str]) -> list[Segment]:
    """The non-empty command segments. Parentheses only split: whatever bash would run is one of
    the segments, and an unbalanced pair is a syntax error that runs nothing. Any other operator is
    Unsupported."""
    parts: list[list[str]] = [[]]
    breaks: list[str] = []
    for token in tokens:
        if token in SEPARATORS or token in PARENS:
            breaks.append(token)
            parts.append([])
        elif _is_operator(token):
            raise Unsupported(f"the operator {token} is not approved automatically")
        else:
            parts[-1].append(token)
    for index, part in enumerate(parts):
        opens_after = index < len(breaks) and breaks[index] == "("
        closes_before = index > 0 and breaks[index - 1] == ")"
        if not part and not (opens_after or closes_before):
            raise Unsupported("the command has an empty part next to an operator")
    return [
        Segment(part, piped=index > 0 and breaks[index - 1] == "|")
        for index, part in enumerate(parts)
        if part
    ]


def _after_cd(candidates: Sequence[str], arguments: Sequence[str]) -> list[str]:
    """The candidate directories once a `cd` may have run: the old ones stay (the cd can fail)."""
    if len(arguments) != 1 or arguments[0].startswith("-"):
        raise Unsupported("cd is approved automatically only with one directory and no option")
    target = os.path.expanduser(arguments[0])
    moved = []
    for candidate in candidates:
        joined = os.path.join(candidate, target)
        # A symlink makes the shell's logical `..` differ from the filesystem's.
        moved += [joined, os.path.normpath(joined)]
    return list(dict.fromkeys([*candidates, *moved]))


def _git_step(words: Sequence[str], candidates: Sequence[str]) -> GitStep:
    """A `git` segment (the words after `git`) as a step; Unsupported when it is not a push or a
    read-only command."""
    arguments = list(words)
    directories = list(candidates)
    while arguments and arguments[0].startswith("-"):
        option = arguments.pop(0)
        if option == "-C" and arguments:
            target = os.path.expanduser(arguments.pop(0))
            directories = [os.path.join(directory, target) for directory in directories]
        elif option != "--no-pager":
            raise Unsupported(f"git option {option} is not approved automatically")
    subcommand = arguments[0] if arguments else ""
    if subcommand not in APPROVED_SUBCOMMANDS:
        raise Unsupported(f"git {subcommand} is not approved automatically")
    return GitStep(tuple(dict.fromkeys(directories)), subcommand, tuple(arguments[1:]))


def _without_merge(words: list[str]) -> tuple[list[str], bool]:
    """The words minus a trailing `2>&1`, and whether there was one; Unsupported when a `2>&1`
    comes anywhere else, or on a command that is not git or gh."""
    merged = words[-1] == MERGED
    body = words[:-1] if merged else words
    if not body or MERGED in body or (merged and body[0] not in PIPE_SOURCES):
        raise Unsupported("2>&1 is approved only as the last word of a git or gh command")
    return body, merged


def _check_trim(words: Sequence[str], follows_source: bool, is_last: bool) -> None:
    """`tail` or `head` fed by a git or gh segment, as the last part, with no option, -n N or -N."""
    if not (follows_source and is_last):
        raise Unsupported(f"{words[0]} is approved only as the last part of a pipe from git or gh")
    if not TRIM_OPTIONS.fullmatch(" ".join(words[1:])):
        raise Unsupported(f"{words[0]} is approved only with no option, -n <N> or -<N>")


def _temp_roots() -> list[str]:
    """The folders a body file may sit in: `/tmp` and `$TMPDIR` of the hook's environment, each
    after realpath. `tempfile.gettempdir()` is not asked: it writes a probe file and also reads
    TEMP and TMP. A `$TMPDIR` that is relative has no fixed meaning, and one that is `/`, the home
    directory or any folder above it (after realpath) would make a temp dir of the home, so it is
    ignored."""
    home = os.path.realpath(Path.home())
    roots = [os.path.realpath(STANDARD_TEMP)]
    configured = os.environ.get("TMPDIR", "")
    if configured and os.path.isabs(configured):
        root = os.path.realpath(configured)
        if os.path.commonpath([root, home]) != root:  # root is not home or one of its ancestors
            roots.append(root)
    return list(dict.fromkeys(roots))


def _temp_file_problem(value: str, cwd: str) -> str | None:
    """Why the absolute path `value` may not be a body file, or None. No `..`; its realpath is
    inside a temp dir; and now, at decision time, it is a regular file (a symlink is not one) with
    one hard link (a link to a file elsewhere would not be this file's own), owned by the user
    running the hook, that neither its group nor anyone else can write, and not one deny-secrets
    (`target_hit`) would block."""
    if ".." in value.split("/"):
        return "has a .. segment"
    real = os.path.realpath(value)
    if not any(os.path.commonpath([root, real]) == root for root in _temp_roots()):
        return "is not under the temp directory"
    try:
        info = os.lstat(value)
    except OSError:
        return "is not a file that exists"
    if not stat.S_ISREG(info.st_mode):
        return "is not a regular file"
    if info.st_nlink != 1:
        return "has more than one hard link"
    if info.st_uid != os.getuid():
        return "is not owned by the user running the hook"
    if info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        return "can be written by its group or by others"
    if hook_support.sibling("deny_secrets").target_hit(value, cwd, Path.home()):
        return "names a file that holds secrets"
    return None


def _body_file_problem(
    value: str, cwd: str, directories: Sequence[str], temp_allowed: bool = False
) -> str | None:
    """Why `value` may not be read by a gh file option, or None. It is a relative path with no
    `..` that resolves inside `cwd` from every directory the command may run in, and that
    deny-secrets (`target_hit`, the check it makes of a Write or Edit target) would not block; or,
    when `temp_allowed` (a body file), an absolute path that `_temp_file_problem` accepts."""
    if not value or value == "-":
        return "needs the path of a file (not stdin)"
    if temp_allowed and value.startswith("/"):
        return _temp_file_problem(value, cwd)
    if value.startswith(("/", "~")):
        return "is not a relative path"
    if ".." in value.split("/"):
        return "has a .. segment"
    root = os.path.realpath(cwd)
    guard = hook_support.sibling("deny_secrets")
    for directory in directories:
        if os.path.commonpath([root, os.path.realpath(os.path.join(directory, value))]) != root:
            return "leads outside the worktree"
        if guard.target_hit(value, directory, Path.home()):
            return "names a file that holds secrets"
    return None


def _check_file_option(name: str, value: str, cwd: str, directories: Sequence[str]) -> None:
    problem = _body_file_problem(value, cwd, directories, temp_allowed=name in TEMP_FILE_OPTIONS)
    if problem:
        raise Escalate(
            f"gh pr {name} {value!r} {problem}: write the file inside the worktree and pass its"
            " relative path, or for --body-file write it under /tmp or $TMPDIR and pass its"
            " absolute path (a regular file with one link that you own and no one else can write);"
            " a body is --body-file <path>"
        )


def _github_slug(url: str) -> str | None:
    """`owner/name` in lower case for a GitHub remote URL in one of the three forms
    `https://github.com/O/N(.git)`, the scp form (`<user>@github.com:O/N(.git)`) and the ssh form
    (`ssh://<user>@github.com/O/N(.git)`), where the user is `git` in both;
    None for any other text (another host, credentials, a port, a trailing path, a local path)."""
    found = GITHUB_REMOTE.fullmatch(url)
    if not found:
        return None
    name = found[2].removesuffix(".git")
    if not name.strip("."):
        return None
    return f"{found[1]}/{name}".casefold()


def _origin_slug(directory: str) -> str | None:
    """The GitHub repository the `origin` remote of `directory` names, or None when there is no
    origin or it is not a plain GitHub URL. Both the configured URL and the one git uses (after a
    URL rewrite, `insteadOf`) must name the same repository, and there must be one URL."""
    configured = _git(directory, "config", "--get-all", "remote.origin.url")
    if not configured or len(configured.splitlines()) != 1:
        return None
    in_use = _git(directory, "remote", "get-url", "origin")
    slug = _github_slug(configured)
    return slug if slug is not None and slug == _github_slug(in_use or "") else None


def _check_repo(value: str, directories: Sequence[str]) -> None:
    """`--repo`/`-R` is approved only as `OWNER/NAME` naming the origin remote of every directory
    the command may run in (case aside); anything else acts on another repository."""
    if not REPO_VALUE.fullmatch(value):
        raise Escalate(REPO_REASON)
    _refuse_git_environment()
    named = value.casefold()
    if any(_origin_slug(directory) != named for directory in directories):
        raise Escalate(REPO_REASON)


def _check_long_option(
    option: str, pending: list[str], cwd: str, directories: Sequence[str]
) -> int:
    """One `--name[=value]` word, with its value taken from `pending` when it has none; 1 when it
    is `--title` with a quoted value, else 0."""
    name, equals, value = option.partition("=")
    if name in ("--web", "--editor"):
        raise Escalate(INTERACTIVE_REASON)
    if name == "--title":
        quoted = value == TITLE_WORD if equals else bool(pending) and pending[0] == TITLE_WORD
        if quoted and not equals:
            pending.pop(0)
        return int(quoted)
    if name == "--repo" or name in FILE_OPTIONS:
        file = value if equals else (pending.pop(0) if pending else "")
        if name == "--repo":
            _check_repo(file, directories)
        else:
            _check_file_option(name, file, cwd, directories)
    elif name in LONG_VALUED and not equals and pending and pending[0] in TITLE_OPTIONS:
        pending.pop(0)  # `--base --title`: gh reads `--title` as the base, so no title option here
    return 0


def _check_short_options(
    letters: str, pending: list[str], cwd: str, directories: Sequence[str]
) -> int:
    """The letters of one `-xyz` word, read the way gh's option parser does: a letter that takes a
    value ends the word, and the value is its rest or the next word. 1 when it is `-t` with a
    quoted title, else 0. `-R` is read only as a word of its own."""
    for position, letter in enumerate(letters):
        if letter == "R":
            if letters != "R":
                raise Escalate(REPO_REASON)
            _check_repo(pending.pop(0) if pending else "", directories)
            return 0
        if letter in "we":
            raise Escalate(INTERACTIVE_REASON)
        if letter in SHORT_VALUED:
            rest = letters[position + 1 :]
            # pflag drops one `=` after a short letter: `-F=path` has the value `path`.
            value = rest.removeprefix("=") if rest else (pending.pop(0) if pending else "")
            if letter in SHORT_FILE_OPTIONS:
                _check_file_option(f"-{letter}", value, cwd, directories)
            return int(letter == "t" and not rest and value == TITLE_WORD)
    return 0


def _check_gh(arguments: Sequence[str], cwd: str, directories: Sequence[str]) -> None:
    """A `gh` segment (the words after `gh`): only `pr create`, `pr view` and `pr checks`. The
    shell metacharacters were refused with the whole text, but for a quoted title, which arrives
    here as TITLE_WORD: it must be the value of `--title` or `-t` of `pr create`, and nowhere
    else. Options are free but for `--repo`/`-R` (only this directory's origin), a browser or
    editor (`--web`, `--editor`), and the file options (`--body-file`, `--template`,
    `--recover`, `-F`, `-T`), which read a file the PM can post."""
    if len(arguments) < 2 or arguments[0] != "pr" or arguments[1] not in GH_PR_COMMANDS:
        name = " ".join(["gh", *arguments[:2]])
        raise Escalate(f"{name} is not approved automatically")
    quoted_titles = sum(word.count(TITLE_WORD) for word in arguments)
    if quoted_titles and arguments[1] != "create":
        raise Unsupported(TITLE_REASON)
    pending = list(arguments[2:])
    consumed = 0
    while pending:
        option = pending.pop(0)
        if option == "--":
            break
        if option.startswith("--"):
            consumed += _check_long_option(option, pending, cwd, directories)
        elif option.startswith("-") and option != "-":
            consumed += _check_short_options(option[1:], pending, cwd, directories)
    if consumed != quoted_titles:
        raise Unsupported(TITLE_REASON)


def _plan(command: str, cwd: str) -> list[GitStep]:
    """The git steps of a command made only of `cd`, gh pr segments, git segments and a last
    `| tail`/`| head`. Every cd and gh segment is checked here; the git steps are returned."""
    candidates = [cwd]
    steps: list[GitStep] = []
    segments = _segments(_tokens(command))
    for index, segment in enumerate(segments):
        words, _ = _without_merge(segment.words)
        if words[0] != "gh" and any(TITLE_WORD in word for word in words):
            raise Unsupported(TITLE_REASON)
        previous = segments[index - 1].words[0] if index else ""
        if words[0] in TRIMMERS and segment.piped:
            _check_trim(words, previous in PIPE_SOURCES, is_last=index == len(segments) - 1)
        elif segment.piped:
            raise Unsupported(f"a pipe into {words[0]} is not approved automatically")
        elif words[0] == "cd":
            candidates = _after_cd(candidates, words[1:])
        elif words[0] == "gh":
            _check_gh(words[1:], cwd, candidates)
        elif words[0] == "git":
            steps.append(_git_step(words[1:], candidates))
        else:
            raise Unsupported(f"the command runs {words[0]}, not only git, gh pr and cd")
    if not segments:
        raise Unsupported("the command has no command")
    return steps


def _branch_name(ref: str) -> str:
    """The branch a destination names; Unsupported for tags, other refs and ambiguous names."""
    name = ref.removeprefix(HEADS)
    if not name or name.startswith(QUALIFIED_PREFIXES):
        raise Unsupported(f"pushing to {ref} is not approved automatically")
    return name


def _refspec(spec: str) -> Refspec:
    if spec.startswith("+"):
        raise Escalate(FORCE_REASON)
    if spec in CURRENT_BRANCH_NAMES:
        return Refspec(None, local=False)
    if ":" not in spec:
        return Refspec(_branch_name(spec), local=True)
    source, _, destination = spec.partition(":")
    if not source:
        raise Escalate(f"git push refspec {spec} (a delete) is not approved automatically")
    if not destination or ":" in destination or destination in CURRENT_BRANCH_NAMES:
        raise Unsupported(f"the refspec {spec} cannot be checked")
    return Refspec(_branch_name(destination), local=False)


def _refuse_option(option: str) -> None:
    if option in SAFE_OPTIONS or SAFE_SHORT_OPTIONS.fullmatch(option):
        return
    if (
        option in FORCE_OPTIONS
        or option.startswith(FORCE_WITH_LEASE_VALUE)
        or FORCE_SHORT_OPTIONS.fullmatch(option)
    ):
        raise Escalate(FORCE_REASON)
    raise Escalate(f"git push option {option} is not approved automatically")


def _refuse_protected(branches: Iterable[str], remote_default: str | None = None) -> None:
    protected = PROTECTED if remote_default is None else (*PROTECTED, remote_default.casefold())
    for branch in branches:
        if branch.casefold() in protected:
            raise Escalate(f"pushing to {branch} (protected: {PROTECTED_LABEL})")


def _parse_push(arguments: Sequence[str]) -> Push:
    """The remote and refspecs of a push's arguments, after refusing options and force."""
    positional: list[str] = []
    options_ended = False
    for argument in arguments:
        if options_ended or argument == "-" or not argument.startswith("-"):
            positional.append(argument)
        elif argument == "--":
            options_ended = True
        else:
            _refuse_option(argument)
    if "tag" in positional[1:]:
        raise Escalate("git push tag <name> pushes a tag, not a branch")
    refspecs = tuple(_refspec(spec) for spec in positional[1:])
    named = (spec.destination for spec in refspecs if spec.destination is not None)
    _refuse_protected(named)
    return Push(positional[0] if positional else None, refspecs)


def _git(directory: str, *args: str) -> str | None:
    """Stdout of `git -C <directory> ...`; None when git exits 1 (config, rev-parse and
    symbolic-ref say "not found" that way). Unsupported when git is missing, slow or fails."""
    try:
        proc = subprocess.run(
            ["git", "-C", directory, *args],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=GIT_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise Unsupported(f"git timed out after {GIT_TIMEOUT:g} s") from None
    except OSError as exc:
        raise Unsupported(f"cannot run git: {exc}") from None
    if proc.returncode == 1:
        return None
    if proc.returncode != 0:
        raise Unsupported(f"git cannot read the repository at {directory}")
    return proc.stdout.strip()


def _current_branch(directory: str) -> str:
    branch = _git(directory, "symbolic-ref", "--quiet", "--short", "HEAD")
    if not branch:
        raise Unsupported("HEAD is detached, so the branch to push cannot be resolved")
    return _branch_name(branch)


def _default_branches(directory: str) -> list[str]:
    """The branches a push without refspecs updates: the current one and its upstream."""
    if _git(directory, "config", "--get", "push.default") not in SAFE_PUSH_DEFAULTS:
        raise Unsupported("push.default pushes more than the current branch")
    current = _current_branch(directory)
    upstream = _git(directory, "config", "--get", f"branch.{current}.merge")
    return [current, _branch_name(upstream)] if upstream else [current]


def _require_local_branch(directory: str, branch: str) -> None:
    if _git(directory, "rev-parse", "--verify", "--quiet", HEADS + branch) is None:
        raise Unsupported(f"{branch} is not a local branch")


def _updated_branches(directory: str, refspecs: Sequence[Refspec]) -> list[str]:
    if not refspecs:
        return _default_branches(directory)
    branches = []
    for spec in refspecs:
        if spec.destination is None:
            branches.append(_current_branch(directory))
            continue
        if spec.local:
            _require_local_branch(directory, spec.destination)
        branches.append(spec.destination)
    return branches


def _push_remote(directory: str) -> str:
    """The remote a bare `git push` uses, resolved as git does."""
    branch = _current_branch(directory)
    for key in (f"branch.{branch}.pushRemote", "remote.pushDefault", f"branch.{branch}.remote"):
        found = _git(directory, "config", "--get", key)
        if found:
            return found
    return PUSH_REMOTE_DEFAULT


def _check_remote(directory: str, remote: str | None) -> str:
    """The name of the remote the push goes to, once it is known to be a configured one."""
    name = _push_remote(directory) if remote is None else remote
    if not REMOTE_NAME.fullmatch(name) or not _git(
        directory, "config", "--get", f"remote.{name}.url"
    ):
        raise Unsupported(f"{name} is not a configured remote")
    return name


def _remote_default(directory: str, remote: str) -> str | None:
    """The default branch of `remote` as the clone last recorded it (refs/remotes/<remote>/HEAD,
    set by clone or `git remote set-head`): a local read, with no network. None when not recorded."""
    ref = _git(directory, "symbolic-ref", "--quiet", f"refs/remotes/{remote}/HEAD")
    prefix = f"refs/remotes/{remote}/"
    return ref[len(prefix) :] if ref and ref.startswith(prefix) else None


def _check_configuration(directory: str) -> None:
    if _git(directory, "config", "--get-regexp", REDIRECTING_CONFIG):
        raise Unsupported(
            "configured push targets, mirrors or URL rewrites change where a push goes"
        )
    for key in WIDENING_BOOLEANS:
        if _git(directory, "config", "--type=bool", "--get", key) == "true":
            raise Unsupported(f"{key} is on, which pushes more than the command says")
    recurse = _git(directory, "config", "--get", "push.recurseSubmodules")
    if recurse is not None and recurse.lower() not in SUBMODULE_PUSH_OFF:
        raise Unsupported("push.recurseSubmodules pushes more than the command says")


def _check_in(directory: str, push: Push) -> None:
    remote = _check_remote(directory, push.remote)
    _check_configuration(directory)
    _refuse_protected(
        _updated_branches(directory, push.refspecs), _remote_default(directory, remote)
    )


def _refuse_git_environment() -> None:
    for name in sorted(os.environ):
        if name.startswith("GIT_") and name not in HARMLESS_GIT_VARIABLES:
            raise Escalate(f"{name} is set in the environment, which can change what git does")


def _check_push(step: GitStep) -> None:
    push = _parse_push(step.arguments)
    for directory in step.directories:
        _check_in(directory, push)


def _options_before_paths(arguments: Sequence[str]) -> Iterable[str]:
    """The options of a command's arguments: what starts with `-` before a `--`."""
    for argument in arguments:
        if argument == "--":
            return
        if argument.startswith("-") and argument != "-":
            yield argument


def _check_branch_listing(arguments: Sequence[str]) -> None:
    """`git branch` may only list: its flags, and a ref after --contains or --merged. A bare word
    would create a branch (or be a pattern, for --list): both are refused."""
    pending: str | None = None
    for argument in arguments:
        if pending is not None and not argument.startswith("-"):
            pending = None
        elif argument in BRANCH_REF_OPTIONS:
            if pending is not None:
                raise Escalate(f"git branch {pending} needs a ref")
            pending = argument
        elif BRANCH_FLAGS.fullmatch(argument):
            if pending is not None:
                raise Escalate(f"git branch {pending} needs a ref")
        elif argument.startswith("-"):
            raise Escalate(f"git branch option {argument} is not approved automatically")
        else:
            raise Escalate(f"git branch argument {argument} is not approved automatically")
    if pending is not None:
        raise Escalate(f"git branch {pending} needs a ref")


def _check_read_only(step: GitStep) -> None:
    if step.subcommand == "branch":
        _check_branch_listing(step.arguments)
        return
    allowed = READ_ONLY_OPTIONS[step.subcommand]
    for option in _options_before_paths(step.arguments):
        if not allowed.fullmatch(option):
            raise Escalate(f"git {step.subcommand} option {option} is not approved automatically")
        if option.startswith(FORMAT_OPTIONS) and SIGNATURE_PLACEHOLDER in option:
            raise Escalate(f"{option} has a {SIGNATURE_PLACEHOLDER} placeholder, which runs gpg")


def _check_step(step: GitStep) -> None:
    _refuse_git_environment()
    if step.subcommand == "push":
        _check_push(step)
    else:
        _check_read_only(step)


def decide(call: Call) -> str | None:
    """None to approve the call, else the message that refuses it."""
    if call.command is None:
        return ESCALATE.format(reason=f"{call.tool} asks for permission")
    try:
        for step in _plan(call.command, call.cwd):
            _check_step(step)
    except Escalate as exc:
        return ESCALATE.format(reason=exc)
    except Unsupported as exc:
        if HOLDS_PUSH.search(call.command):
            return SPLIT.format(reason=exc)
        if HOLDS_GH_PR.search(call.command):
            return GH_HINT.format(reason=exc)
        return ESCALATE.format(reason=exc)
    return None


def _git_subcommand(arguments: Sequence[str]) -> str:
    """The subcommand of the words after `git`: the first that is not a global option or its value."""
    words = iter(arguments)
    for word in words:
        if word in GIT_VALUED_OPTIONS:
            next(words, None)
        elif not word.startswith("-"):
            return word
    return ""


class TokenKind(Enum):
    WORD = auto()
    SEPARATOR = auto()  # `;`, `&`, `&&`, `||`, a pipe, `;;`, `;&`, `;;&`, `(`, `)` or a newline
    REDIRECT = auto()


@dataclass
class Heredoc:
    """A heredoc: its delimiter as the shell reads it, whether any part of that was quoted (then
    the body is not expanded), whether it is `<<-` (leading tabs dropped), and the body, read
    once its line ends."""

    delimiter: str
    quoted: bool
    strip_tabs: bool
    body: str = ""


@dataclass(frozen=True)
class Token:
    """A word (its text with the quotes removed; a substitution in it stays as written), or an
    operator. Only unquoted text forms an operator, so a quoted `;` or `>` is a word. A heredoc
    operator carries its heredoc, delimiter and body."""

    kind: TokenKind
    text: str
    quoted: bool = False  # any part of the word was quoted or escaped
    heredoc: Heredoc | None = None


@dataclass(frozen=True)
class Scanned:
    """A command as the guard reads it: its text, its tokens, the scanned text of every command
    and process substitution in it (in a heredoc body too), and the text of each `$((...))`."""

    source: str
    tokens: list[Token]
    substitutions: list["Scanned"]
    arithmetic: list[str]


def _operator_at(text: str, pos: int) -> str:
    """The longest shell operator at `pos`, where an operator character is."""
    for size, operators in zip((3, 2, 1), SCAN_OPERATORS, strict=True):
        candidate = text[pos : pos + size]
        if candidate in operators:
            return candidate
    raise Unsupported(f"no operator at {pos}")


class _Scanner:
    """Reads shell text the way bash splits it, for the guard: one pass over `text` from `start`,
    no regex backtracking. See `_scan`."""

    def __init__(self, text: str, depth: int, start: int = 0) -> None:
        if depth > MAX_NESTING:
            raise Unsupported(f"the command nests more than {MAX_NESTING} levels")
        self.text = text
        self.depth = depth
        self.start = start
        self.pos = start
        self.tokens: list[Token] = []
        self.substitutions: list[Scanned] = []
        self.arithmetic: list[str] = []
        self.pending: list[Heredoc] = []  # heredocs whose body starts after the next newline
        self.parens = 0
        self.braces = 0  # `${` open inside the one being read

    def scan(self, closing: bool = False) -> Scanned:
        """The tokens to the end of the text or, when `closing`, to the `)` that closes the `$(`,
        `<(` or `>(` this scanner reads the inside of."""
        text = self.text
        while self.pos < len(text):
            char = text[self.pos]
            if char in BLANKS:
                self.pos += 1
            elif text.startswith("\\\n", self.pos):
                self.pos += 2
            elif char == "#":
                end = text.find("\n", self.pos)
                self.pos = len(text) if end == -1 else end
            elif char == "\n":
                self._newline()
            elif closing and char == ")" and not self.parens:
                return self._closed()
            elif char in OPERATOR_CHARS and not self._at_process_substitution():
                self._operator()
            else:
                self._word()
        if closing:
            raise Unsupported("a $(, <( or >( is not closed")
        if self.pending:
            raise Unsupported("a heredoc has no end")
        return self._result(self.pos)

    def expansions(self) -> None:
        """Reads the text as an unquoted heredoc body: quotes are plain characters, and only the
        substitutions and `${...}` are found."""
        text = self.text
        while self.pos < len(text):
            run = BODY_RUN.match(text, self.pos)
            if run:
                self.pos = run.end()
            elif text[self.pos] == "\\":
                self.pos += 2
            elif text[self.pos] == "$":
                self._dollar(in_double=True)
            else:
                self._backtick(in_double=False)

    def _result(self, end: int) -> Scanned:
        source = self.text[self.start : end]
        return Scanned(source, self.tokens, self.substitutions, self.arithmetic)

    def _closed(self) -> Scanned:
        if self.pending:
            raise Unsupported("a heredoc is not ended inside its $(")
        end = self.pos
        self.pos += 1
        return self._result(end)

    def _at_process_substitution(self) -> bool:
        return self.text[self.pos] in "<>" and self.text.startswith("(", self.pos + 1)

    def _newline(self) -> None:
        self.tokens.append(Token(TokenKind.SEPARATOR, "\n"))
        self.pos += 1
        for heredoc in self.pending:
            self._read_body(heredoc)
        self.pending = []

    def _operator(self) -> None:
        operator = _operator_at(self.text, self.pos)
        self.pos += len(operator)
        if operator == "(":
            self.parens += 1
        elif operator == ")" and self.parens:
            self.parens -= 1
        if operator not in REDIRECTS:
            self.tokens.append(Token(TokenKind.SEPARATOR, operator))
            return
        heredoc = self._heredoc(strip_tabs=operator == "<<-") if operator in HEREDOCS else None
        self.tokens.append(Token(TokenKind.REDIRECT, operator, heredoc=heredoc))

    def _heredoc(self, strip_tabs: bool) -> Heredoc:
        while self.pos < len(self.text) and self.text[self.pos] in BLANKS:
            self.pos += 1
        if self.pos == len(self.text) or self.text[self.pos] in "\n;&|()<>":
            raise Unsupported("a heredoc has no delimiter")
        delimiter, quoted = self._read_word()
        heredoc = Heredoc(delimiter, quoted, strip_tabs)
        self.pending.append(heredoc)
        return heredoc

    def _read_body(self, heredoc: Heredoc) -> None:
        """The body of `heredoc`, from here to its delimiter line, which is skipped too."""
        lines: list[str] = []
        while self.pos < len(self.text):
            line = self._body_line(joins=not heredoc.quoted)
            if heredoc.strip_tabs:
                line = line.lstrip("\t")
            if line == heredoc.delimiter:
                heredoc.body = "\n".join(lines)
                if not heredoc.quoted:
                    self._scan_body(heredoc.body)
                return
            if self.depth and line.startswith(heredoc.delimiter):
                raise Unsupported("bash ends a heredoc inside $( at a line such as EOF)")
            lines.append(line)
        raise Unsupported("a heredoc has no end")

    def _body_line(self, joins: bool) -> str:
        """The next line, with the lines a backslash-newline joins to it (as bash reads an
        unquoted heredoc before it compares a line with the delimiter)."""
        pieces: list[str] = []
        while True:
            end = self.text.find("\n", self.pos)
            end = len(self.text) if end == -1 else end
            piece = self.text[self.pos : end]
            self.pos = end + 1
            trailing = len(piece) - len(piece.rstrip("\\"))
            if not (joins and trailing % 2 and self.pos < len(self.text)):
                pieces.append(piece)
                return "".join(pieces)
            pieces.append(piece[:-1])

    def _scan_body(self, body: str) -> None:
        """The substitutions of an unquoted heredoc body, which run whoever reads the body."""
        scanner = _Scanner(body, self.depth + 1)
        scanner.expansions()
        self.substitutions.extend(scanner.substitutions)
        self.arithmetic.extend(scanner.arithmetic)

    def _word(self) -> None:
        text, quoted = self._read_word()
        at = self.text[self.pos : self.pos + 1]
        descriptor = not quoted and at in ("<", ">") and DESCRIPTOR.fullmatch(text)
        if not (descriptor and not self._at_process_substitution()):
            self.tokens.append(Token(TokenKind.WORD, text, quoted))

    def _read_word(self) -> tuple[str, bool]:
        """The next word's text, quotes removed, and whether any part of it was quoted."""
        parts: list[str] = []
        quoted = False
        text = self.text
        while self.pos < len(text):
            char = text[self.pos]
            run = PLAIN_RUN.match(text, self.pos)
            if run:
                parts.append(run.group())
                self.pos = run.end()
            elif text.startswith("\\\n", self.pos):
                self.pos += 2
            elif char in "\\'\"":
                parts.append(self._quoted(char))
                quoted = True
            elif char in "$`" or self._at_process_substitution():
                parts.append(self._expansion(char, in_double=False))
            elif char == "\0":
                raise Unsupported("the command holds a NUL")
            else:
                break  # a blank, a newline or an operator ends the word
        return "".join(parts), quoted

    def _quoted(self, char: str) -> str:
        if char == '"':
            return self._double()
        if char == "\\":
            if self.pos + 1 == len(self.text):
                raise Unsupported("a backslash ends the command")
            self.pos += 2
            return self.text[self.pos - 1]
        end = self.text.find("'", self.pos + 1)
        if end == -1:
            raise Unsupported("a single quote is not closed")
        value = self.text[self.pos + 1 : end]
        self.pos = end + 1
        return value

    def _double(self) -> str:
        parts: list[str] = []
        text = self.text
        self.pos += 1
        while self.pos < len(text):
            char = text[self.pos]
            run = DOUBLE_QUOTED_RUN.match(text, self.pos)
            if run:
                parts.append(run.group())
                self.pos = run.end()
            elif char == '"':
                self.pos += 1
                return "".join(parts)
            elif char == "\\":
                escaped = text[self.pos + 1 : self.pos + 2]
                escapes = bool(escaped) and escaped in DOUBLE_QUOTE_ESCAPES
                parts.append(escaped.strip("\n") if escapes else char)
                self.pos += 2 if escapes else 1
            elif char in "$`":
                parts.append(self._expansion(char, in_double=True))
            else:
                raise Unsupported("the command holds a NUL")
        raise Unsupported("a double quote is not closed")

    def _expansion(self, char: str, in_double: bool) -> str:
        if char == "$":
            return self._dollar(in_double)
        if char == "`":
            return self._backtick(in_double)
        return self._substitution(self.pos + 2)  # `<(` or `>(`

    def _dollar(self, in_double: bool) -> str:
        """`$(...)`, `$((...))`, `${...}` or a plain `$`, read from the `$`."""
        follower = self.text[self.pos + 1 : self.pos + 2]
        if self.text.startswith("$((", self.pos):
            return self._arithmetic()
        if follower == "(":
            return self._substitution(self.pos + 2)
        if follower == "{":
            return self._parameter(in_double)
        if follower and follower in "'\"" and not in_double:
            raise Unsupported("the command uses $'..' or $\"..\" quoting")
        self.pos += 1
        return "$"

    def _substitution(self, inner: int) -> str:
        """A `$(`, `<(` or `>(` substitution: its inside is scanned as commands."""
        start = self.pos
        scanner = _Scanner(self.text, self.depth + 1, inner)
        self.substitutions.append(scanner.scan(closing=True))
        self.pos = scanner.pos
        return self.text[start : self.pos]

    def _arithmetic(self) -> str:
        start, depth = self.pos, 2
        for paren in PAREN.finditer(self.text, start + 3):
            depth += 1 if paren.group() == "(" else -1
            if not depth:
                self.arithmetic.append(self.text[start + 3 : paren.start() - 1])
                self.pos = paren.end()
                return self.text[start : self.pos]
        raise Unsupported("a $(( is not closed")

    def _parameter(self, in_double: bool) -> str:
        """A `${...}`, to its first unquoted `}` (bash does not nest a bare `{`), with the quotes
        and substitutions inside it. A single quote inside one inside double quotes is read
        differently by bash and zsh, so it is Unsupported."""
        start = self.pos
        self.braces += 1
        if self.braces > MAX_NESTING:
            raise Unsupported(f"the command nests more than {MAX_NESTING} levels")
        self.pos += 2
        text = self.text
        while self.pos < len(text):
            char = text[self.pos]
            run = PARAMETER_RUN.match(text, self.pos)
            if run:
                self.pos = run.end()
            elif char == "}":
                self.pos += 1
                self.braces -= 1
                return text[start : self.pos]
            elif char == "\\":
                self.pos += 2
            elif char == "'" and in_double:
                raise Unsupported("a single quote inside ${...} inside double quotes")
            elif char in "'\"":
                self._quoted(char)
            elif char in "$`":
                self._expansion(char, in_double)
            else:
                raise Unsupported("the command holds a NUL")
        raise Unsupported("a ${ is not closed")

    def _backtick(self, in_double: bool) -> str:
        """A backtick substitution: its inside, once the backslashes that escape a backtick, `$`
        or `\\` (and `"` inside double quotes) are undone, is scanned as commands."""
        start = self.pos
        text = self.text
        pos = start + 1
        chars: list[str] = []
        while pos < len(text):
            run = BACKTICK_RUN.match(text, pos)
            if run:
                chars.append(run.group())
                pos = run.end()
                continue
            if text[pos] == "`":
                inner = _Scanner("".join(chars), self.depth + 1)
                self.substitutions.append(inner.scan())
                self.pos = pos + 1
                return text[start : self.pos]
            escaped = text[pos + 1 : pos + 2]
            escapes = bool(escaped) and (
                escaped in BACKTICK_ESCAPES or (in_double and escaped == '"')
            )
            chars.append(escaped if escapes else "\\")
            pos += 2 if escapes else 1
        raise Unsupported("a backtick is not closed")


def _scan(text: str, depth: int = 0) -> Scanned:
    """A command as bash splits it (see `_Scanner`): Unsupported for what the guard cannot follow
    (an unclosed quote, substitution or `${`, a heredoc with no end, `$'..'` or `$".."`, a
    backslash at the very end, a NUL, nesting past MAX_NESTING)."""
    return _Scanner(text, depth).scan()


@dataclass
class SimpleCommand:
    """One command of a scanned list: its words (no redirect or target), the heredocs fed to it,
    whether a pipe feeds it, and whether an input redirect or a here-string does."""

    words: list[str] = field(default_factory=list)
    heredocs: list[Heredoc] = field(default_factory=list)
    piped: bool = False
    fed: bool = False


def _simple_commands(tokens: Sequence[Token]) -> list[SimpleCommand]:
    """The commands between the separators, with each redirect and its target (the word right
    after it) left out. A newline right after `|`, `&&` or `||` continues the list."""
    commands = [SimpleCommand()]
    takes_target = False
    for token in tokens:
        current = commands[-1]
        if token.kind is TokenKind.WORD:
            if not takes_target:
                current.words.append(token.text)
            takes_target = False
            continue
        # An operator where a target belongs is a syntax error: read on, past the redirect.
        takes_target = token.kind is TokenKind.REDIRECT and token.heredoc is None
        if token.kind is TokenKind.REDIRECT:
            current.heredocs += [token.heredoc] if token.heredoc else []
            current.fed = current.fed or token.text in INPUT_REDIRECTS
        elif current.words or current.heredocs or current.fed:
            commands.append(SimpleCommand(piped=token.text in PIPES))
        else:  # an empty command: a newline continues the list, and `| (` still pipes
            current.piped = current.piped or token.text in PIPES
    return [command for command in commands if command.words or command.heredocs]


@dataclass(frozen=True)
class WrapperSyntax:
    """How a wrapper reads its words before the command it runs: options with no value, options
    that take one (the next word, the rest of a short option's word, or `--name=value`), how many
    words follow them (timeout's duration), whether `NAME=value` words may come next (env, sudo),
    and whether a `-<number>` option is one (nice)."""

    flags: frozenset[str] = frozenset()
    valued: frozenset[str] = frozenset()
    operands: int = 0
    assignments: bool = False
    numeric: bool = False


# The wrappers whose options the guard follows to the command they run. An option not listed
# (`env -S`, `sudo -s`, `sudo -h`, `xargs -I`) is not followed, and the wrapper's words are checked
# for a push, merge, send-pack or gh word instead (HOLDS_GUARDED).
WRAPPER_SYNTAX = {
    "env": WrapperSyntax(
        flags=frozenset({"-", "-i", "-0", "-v", "--ignore-environment", "--null", "--debug"}),
        valued=frozenset({"-u", "--unset", "-C", "--chdir"}),
        assignments=True,
    ),
    "nice": WrapperSyntax(valued=frozenset({"-n", "--adjustment"}), numeric=True),
    "nohup": WrapperSyntax(),
    "builtin": WrapperSyntax(),
    "time": WrapperSyntax(
        flags=frozenset(
            {"-p", "-a", "-v", "-q", "--append", "--verbose", "--portability", "--quiet"}
        ),
        valued=frozenset({"-f", "-o", "--format", "--output"}),
    ),
    "timeout": WrapperSyntax(
        flags=frozenset({"-v", "-p", "-f", "--verbose", "--preserve-status", "--foreground"}),
        valued=frozenset({"-s", "-k", "--signal", "--kill-after"}),
        operands=1,
    ),
    "command": WrapperSyntax(flags=frozenset({"-p", "-v", "-V"})),
    "exec": WrapperSyntax(flags=frozenset({"-c", "-l"}), valued=frozenset({"-a"})),
    "sudo": WrapperSyntax(
        flags=frozenset(
            {
                *("-A", "-B", "-b", "-E", "-H", "-K", "-k", "-N", "-n", "-P", "-S", "-V", "-v"),
                *("--askpass", "--bell", "--background", "--preserve-env", "--set-home"),
                *("--remove-timestamp", "--reset-timestamp", "--non-interactive", "--stdin"),
                "--preserve-groups",
            }
        ),
        valued=frozenset(
            {
                *("-C", "-D", "-g", "-p", "-R", "-r", "-T", "-t", "-U", "-u", "--close-from"),
                *("--chdir", "--group", "--prompt", "--chroot", "--role", "--type", "--user"),
                *("--command-timeout", "--other-user", "--host"),
            }
        ),
        assignments=True,
    ),
    "xargs": WrapperSyntax(
        flags=frozenset(
            {"-0", "-r", "-t", "-p", "-x", "-o", "--null", "--no-run-if-empty", "--verbose"}
            | {"--interactive", "--exit", "--open-tty"}
        ),
        valued=frozenset(
            {"-a", "-E", "-L", "-n", "-P", "-s", "-d", "--arg-file", "--eof", "--max-lines"}
            | {"--max-args", "--max-procs", "--max-chars", "--delimiter", "--process-slot-var"}
        ),
    ),
}
NUMERIC_OPTION = re.compile(r"-\d+")


def _option_words(syntax: WrapperSyntax, word: str, following: int) -> int | None:
    """How many words the option `word` takes (itself and its value), or None when the wrapper
    may read it some other way. `following` is how many words come after it."""
    name, equals, _ = word.partition("=")
    long_value = bool(equals) and name.startswith("--")
    if word in syntax.flags or (long_value and name in syntax.flags | syntax.valued):
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


def _wrapped_start(syntax: WrapperSyntax, words: Sequence[str], index: int) -> int | None:
    """Where the command a wrapper runs starts, given where the words after its name start; None
    when its options cannot be followed."""
    while index < len(words):
        word = words[index]
        if word == "--":
            index += 1
            break
        if not word.startswith("-") or (word == "-" and word not in syntax.flags):
            break
        taken = _option_words(syntax, word, len(words) - index - 1)
        if taken is None:
            return None
        index += taken
    index += syntax.operands
    while syntax.assignments and index < len(words) and ASSIGNMENT.match(words[index]):
        index += 1
    return index


def _command_start(words: Sequence[str], start: int = 0) -> int:
    """Where the command name is, from `start`: assignments and shell prefixes (`{`, `!`, `if`...)
    come before it."""
    while start < len(words) and (ASSIGNMENT.match(words[start]) or words[start] in SHELL_PREFIXES):
        start += 1
    return start


@dataclass(frozen=True)
class Unwrapped:
    """The command under a command's wrappers: its words, whether every wrapper's options could be
    followed (else `command` is the wrapper that stopped the reading, with its words), and whether
    xargs adds words it reads from stdin."""

    command: list[str]
    followed: bool = True
    stdin_words: bool = False


def _unwrapped(words: Sequence[str]) -> Unwrapped:
    start = _command_start(words)
    stdin_words = False
    while start < len(words):
        name = os.path.basename(words[start])
        syntax = WRAPPER_SYNTAX.get(name)
        if syntax is None:
            break
        inner = _wrapped_start(syntax, words, start + 1)
        if inner is None:
            return Unwrapped(list(words[start:]), followed=False, stdin_words=stdin_words)
        stdin_words = stdin_words or name == "xargs"
        start = _command_start(words, inner)
    return Unwrapped(list(words[start:]), stdin_words=stdin_words)


def _expands(name: str) -> bool:
    """Whether the shell expands a command name into one the guard cannot know (`$g`, `$(...)`)."""
    return name not in TEST_COMMANDS and bool(EXPANDING.search(name))


def _holds_a_guarded_word(command: Sequence[str]) -> bool:
    # The quotes inside a word (`bash -c "git pu''sh"`) are the inner shell's to resolve.
    return bool(HOLDS_GUARDED.search(QUOTE_CHARS.sub("", " ".join(command))))


def _segment_is_guarded(words: Sequence[str]) -> bool:
    """A command that runs gh, or a git push, merge or send-pack, under any wrappers; a wrapper
    the guard cannot unwrap, or a shell, eval or make, whose words hold such a word; a command
    name that expands, next to such a word or a git word; xargs running git with no subcommand."""
    inner = _unwrapped(words)
    command = inner.command
    if not command:
        return False
    name = os.path.basename(command[0])
    if not inner.followed:
        return _holds_a_guarded_word(command)
    if name == "gh":
        return True
    if name == "git":
        subcommand = _git_subcommand(command[1:])
        expands = bool(EXPANDING.search(subcommand))
        return (
            subcommand in GUARDED_SUBCOMMANDS or expands or (inner.stdin_words and not subcommand)
        )
    if _expands(command[0]):
        joined = " ".join(command)
        return bool(HOLDS_GUARDED.search(joined) or HOLDS_GIT_OR_GH.search(joined))
    return name in WRAPPERS and _holds_a_guarded_word(command)


def _receiver(words: Sequence[str]) -> str:
    """The name of the command that reads a simple command's stdin, under its wrappers; NO_COMMAND
    for none or a compound command, UNKNOWN_COMMAND when it expands or a wrapper stops the
    reading."""
    inner = _unwrapped(words)
    if not inner.followed:
        return UNKNOWN_COMMAND
    if not inner.command:
        return NO_COMMAND
    if _expands(inner.command[0]):
        return UNKNOWN_COMMAND
    name = os.path.basename(inner.command[0])
    return NO_COMMAND if name in COMPOUND_ENDS else name


def _runs_its_input(scanned: Scanned, commands: Sequence[SimpleCommand], depth: int) -> bool:
    """Whether a command runs text the guard has not read as commands yet: a heredoc fed to a
    shell, `source`, `eval`, a compound command or an unknown command (its body is checked as
    commands), or a shell or unknown command fed by a pipe or an input redirect, in a text with a
    git or gh word."""
    piped = any(command.piped for command in commands)
    receivers = [_receiver(command.words) for command in commands]
    runs = [name in COMMAND_READERS or name == UNKNOWN_COMMAND for name in receivers]
    fed = any(run and (piped or command.fed) for run, command in zip(runs, commands, strict=True))
    if fed and HOLDS_GIT_OR_GH.search(scanned.source):
        return True
    return any(
        _text_is_guarded(heredoc.body, depth + 1)
        for run, name, command in zip(runs, receivers, commands, strict=True)
        if run or name == NO_COMMAND
        for heredoc in command.heredocs
    )


def _scanned_is_guarded(scanned: Scanned, depth: int) -> bool:
    if any(HOLDS_GIT_OR_GH.search(text) for text in scanned.arithmetic):
        return True
    if any(_scanned_is_guarded(inner, depth + 1) for inner in scanned.substitutions):
        return True
    commands = _simple_commands(scanned.tokens)
    if any(_segment_is_guarded(command.words) for command in commands):
        return True
    return _runs_its_input(scanned, commands, depth)


def _text_is_guarded(text: str, depth: int) -> bool:
    try:
        scanned = _scan(text, depth)
    except Unsupported:
        return bool(HOLDS_GIT_OR_GH.search(text))
    return _scanned_is_guarded(scanned, depth)


def _is_guarded(command: str) -> bool:
    """Whether the guard must run decide() on a command: a simple command that is a push, merge,
    send-pack or gh command (see `_segment_is_guarded`), in the command, in any substitution in
    it, or in a heredoc fed to a shell (see `_runs_its_input`); a `$((...))` with a git or gh
    word; or text the scanner cannot follow that holds a git or gh word. A word in a message, a
    pattern, a file name, a quoted string or a heredoc fed to cat is not one."""
    return _text_is_guarded(command, 0)


@dataclass(frozen=True)
class Decision:
    """The answer to one request: `message` is None to approve, else the refusal's text. `tool` and
    `command` say what was asked, when the payload could be read. `guard` is set for the
    PreToolUse guard, which never approves: it answers with silence or a refusal."""

    message: str | None
    tool: str | None = None
    command: str | None = None
    guard: bool = False


def _verdict(message: str | None) -> str:
    """The hook's one stdout line: allow for None, else deny with the message."""
    decision = (
        {"behavior": "allow"} if message is None else {"behavior": "deny", "message": message}
    )
    return json.dumps(
        {"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": decision}}
    )


def _guarding(argv: Sequence[str]) -> bool:
    return list(argv) == [GUARD_ARG]


def run(argv: Sequence[str], stdin: TextIO) -> Decision:
    """The hook's decision for one request; main() prints it, once, after the alarm is off."""
    guard = _guarding(argv)
    try:
        if argv and not guard:
            raise PayloadError(USAGE)
        payload = hook_support.loads(stdin.read())
        if not isinstance(payload, dict):
            raise PayloadError("payload is not an object")
        call = _call(payload)
    except ValueError as exc:  # PayloadError, bad JSON or nesting past the limit
        return Decision(UNCHECKED.format(why=f"bad input: {exc}"), guard=guard)
    if guard and not (call.command and _is_guarded(call.command)):
        return Decision(None, call.tool, call.command, guard=True)
    return Decision(decide(call), call.tool, call.command, guard)


def _checked(argv: Sequence[str], stdin: TextIO) -> Decision:
    """run() under the deadline, disarmed before anything else; any other error refuses the call.
    Nothing is printed in here, so a late alarm cannot add a second document to stdout."""
    signal.setitimer(signal.ITIMER_REAL, DEADLINE)  # interrupts a hanging git call
    try:
        try:
            return run(argv, stdin)
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
    except DeadlineError:
        raise
    # Fail closed on any error (closed stdin, a broken sibling module, a bug): Claude lets the call
    # through to the prompt on anything but a decision.
    except Exception as exc:
        logging.exception("coven %s: internal error; refusing", HOOK)
        return Decision(UNCHECKED.format(why=f"internal error {exc!r}"), guard=_guarding(argv))


def _record(decision: Decision) -> None:
    """Log the decision. The log is best effort: a broken one must not change the answer."""
    command = decision.command
    record = {
        "decision": "allow" if decision.message is None else "deny",
        "tool": decision.tool,
        "command": command if command is None else command[:LOGGED_COMMAND_LIMIT],
        "message": decision.message,
    }
    try:
        hook_support.sibling("hooklog").log_event(HOOK, record)
    except Exception:
        logging.exception("coven %s: could not write the hook log", HOOK)


def _answer(decision: Decision) -> int:
    """Log the decision, then print it; the exit code. The guard approves nothing: silence lets the
    call reach the ask rule, and a refusal is exit 2 with the message on stderr (as deny-secrets
    blocks), which holds in bypassPermissions mode too."""
    if decision.guard and decision.message is None:
        return 0
    _record(decision)
    if decision.guard:
        print(decision.message, file=sys.stderr)
        return 2
    print(_verdict(decision.message))
    return 0


def _refuse_damaged(argv: Sequence[str], error: str) -> int:
    """Refuse a PM session's request because hook_support is unusable. It leans on nothing else in
    hooks/: not the log, not a sibling."""
    message = DAMAGED.format(error=error)
    if _guarding(argv):
        print(message, file=sys.stderr)
        return 2
    print(_verdict(message))
    return 0


def main(
    argv: Sequence[str] | None = None,
    stdin: TextIO | None = None,
    environ: Mapping[str, str] | None = None,
) -> int:
    if (os.environ if environ is None else environ).get(ROLE_VAR) != PM_ROLE:
        return 0  # not a PM session: stay out of the way
    args = sys.argv[1:] if argv is None else argv
    if SUPPORT_ERROR is not None:
        return _refuse_damaged(args, SUPPORT_ERROR)
    previous = signal.signal(signal.SIGALRM, _past_deadline)
    try:
        decision = _checked(args, sys.stdin if stdin is None else stdin)
    # The one-shot alarm can land anywhere in _checked, its error handler included: still refuse.
    except DeadlineError:
        decision = Decision(UNCHECKED.format(why=TOO_SLOW), guard=_guarding(args))
    finally:
        signal.signal(signal.SIGALRM, previous)  # _checked disarmed the timer already
    return _answer(decision)


def cli() -> int:
    """main() for the command line, with a last catch: an uncaught exception would exit 1, which
    lets the call through to the prompt."""
    try:
        return main()
    except DeadlineError:
        return _answer(Decision(UNCHECKED.format(why=TOO_SLOW), guard=_guarding(sys.argv[1:])))


if __name__ == "__main__":
    sys.exit(cli())
