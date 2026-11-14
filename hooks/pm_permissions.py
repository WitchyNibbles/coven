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

Quotes are refused everywhere, in gh commands too: the tokenizer cannot tell a quoted word from an
injection, so `gh pr create --body "..."` is denied, and the message tells the PM to write the body
to a file and pass `--body-file <path>` (words with no quote, `$` or other metacharacter are
fine: `--title Add_login`, `--fill`).

A `gh pr` command may not name another repository (`--repo`, `-R`) and may not open a browser or
an editor (`--web`, `-w`, `--editor`, `-e`: they hang a background session; `gh pr checks --watch`
is fine). `--body-file` (`-F`), `--template` (`-T`) and `--recover` are approved only for a
relative path with no `..` that
resolves inside the payload's cwd (from every directory a `cd` may lead to) and that
deny-secrets' `target_hit` would not block, so a PM cannot post a key file or `/etc/passwd` to a pull request.

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
them and, in bypassPermissions mode, run with no prompt and no PermissionRequest. The guard
tokenizes the command with the same tokenizer and segment parser as above (quotes resolved, a
newline a `;`, `$'..'` and unbalanced quotes unreadable) and runs decide() on it when a segment is
a `gh` command, a git `push`, `merge` or `send-pack` (after git's global options and any
`NAME=value` prefix), or a wrapper (bash, sh, env, command, sudo, make...) whose words hold `push`,
`merge` or `gh`; or when the text cannot be tokenized and holds a `git` or `gh` word. A word in a
commit message, a search pattern or a file name does not count. A refusal blocks the call (exit 2,
the message on stderr, which holds in bypassPermissions). Anything decide() approves, and every
other command, prints nothing, so the plain forms still reach the ask rule and this hook's
PermissionRequest answer. A bad payload, an internal error or a check past
DEADLINE blocks too. It is best effort against an agent that follows its role: GitHub branch
protection on the default branch is the real wall.
"""

import importlib
import json
import logging
import os
import re
import shlex
import signal
import subprocess
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn, TextIO

# Hooks run as `python3 -I`: write no bytecode into the installed plugin folder.
sys.dont_write_bytecode = True
# `-I` leaves this folder off sys.path, so put it there for hook_support, the one module found so.
sys.path.insert(0, str(Path(__file__).resolve().parent))
# By name, not an import statement: ruff rejects one below code (E402) and suppressions are banned.
hook_support = importlib.import_module("hook_support")

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
    " automatically when run on their own, with no quotes, $ or redirects (only 2>&1 and a final"
    " | tail or | head are accepted): write a pull request body to a file and pass"
    " --body-file <path>."
)
UNCHECKED = (
    f"{PREFIX} could not check this request ({{why}}); refused. Send the step to the GM as a"
    " QUESTION."
)
FORCE_REASON = "a force push (--force, --force-with-lease, -f or a +refspec)"
TOO_SLOW = "the check took too long"

BASH = "Bash"
# The text of a command that runs a push: how a refused command is told apart from a non-push.
HOLDS_PUSH = re.compile(r"\bgit\b.*\bpush\b", re.DOTALL)
# The same for the gh commands that are approved.
HOLDS_GH_PR = re.compile(r"\bgh\s+pr\s+(?:create|view|checks)\b")
# What the PreToolUse guard looks for in a command's words: a push or merge word, or a gh word.
HOLDS_GUARDED = re.compile(r"\b(?:push|merge)\b|(?<![\w.-])gh(?![\w.-])")
# A git or gh word, for a command the guard cannot tokenize.
HOLDS_GIT_OR_GH = re.compile(r"(?<![\w.-])(?:git|gh)(?![\w.-])")
# Quoting the tokenizer cannot follow (the shell turns `$'..'` into other characters).
DOLLAR_QUOTE = re.compile(r"\$['\"]")
# The git subcommands the guard runs decide() for, and the characters that make a subcommand word
# something the shell expands into an unknown one.
GUARDED_SUBCOMMANDS = frozenset({"push", "merge", "send-pack"})
EXPANDING = re.compile(r"[$`{*?\[~]")
# git's global options that take the next word as their value.
GIT_VALUED_OPTIONS = frozenset(
    {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--super-prefix", "--config-env"}
)
# Commands that run the rest of their words as a command (or that make does), and the words a
# shell puts before a command.
WRAPPERS = frozenset(
    {
        "bash",
        "sh",
        "zsh",
        "dash",
        "eval",
        "exec",
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
)
SHELL_PREFIXES = frozenset({"{", "}", "!", "if", "then", "else", "elif", "do", "while", "until"})
ASSIGNMENT = re.compile(r"[A-Za-z_]\w*=")
QUOTE_CHARS = re.compile(r"['\"\\]")
GH_PR_COMMANDS = frozenset({"create", "view", "checks"})
PIPE_SOURCES = frozenset({"git", "gh"})
REPO_REASON = "gh pr with --repo or -R would act on another repository"
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
TRIMMERS = frozenset({"tail", "head"})
TRIM_OPTIONS = re.compile(r"(?:-n [0-9]+|-[0-9]+)?")
SEPARATORS = frozenset({"&&", "||", ";", "|"})
PARENS = frozenset({"(", ")"})
OPERATOR_CHARS = frozenset("();<>|&")

REMOTE_NAME = re.compile(r"[A-Za-z0-9._-]+")
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


def _tokens(command: str, lenient: bool = False) -> list[str]:
    """The words of a command. Strict (for approving): anything the hook cannot follow is
    Unsupported. Lenient (for the guard, which only looks for a push): quotes, `#` and the rest are
    allowed, a newline is a `;`, and only an unbalanced quote or `$'..'` is Unsupported."""
    if lenient:
        if DOLLAR_QUOTE.search(command):
            raise Unsupported("the command uses $'..' or $\"..\" quoting")
        return _split(command.replace("\\\n", "").replace("\n", " ; "))
    if MERGED in command:
        raise Unsupported(f"{MERGED!r} is not allowed in an automatically approved command")
    text = MERGE_STDERR.sub(MERGED, command)
    found = FORBIDDEN.search(text.replace(MERGED, " "))
    if found:
        raise Unsupported(f"{found[0]!r} is not allowed in an automatically approved command")
    words = _split(text)
    for word in words:
        if MERGED in word and word != MERGED:
            raise Unsupported("2>&1 is approved only as a word of its own")
        if "~" in word and not _is_home(word):
            raise Unsupported(
                f"{word} would be expanded by the shell, which the hook cannot follow"
            )
    return words


def _segments(tokens: Iterable[str], lenient: bool = False) -> list[Segment]:
    """The non-empty command segments. Parentheses only split: whatever bash would run is one of
    the segments, and an unbalanced pair is a syntax error that runs nothing. Lenient (for the
    guard): any other operator (`&`, a redirect) splits too, and empty parts are fine."""
    parts: list[list[str]] = [[]]
    breaks: list[str] = []
    for token in tokens:
        if token in SEPARATORS or token in PARENS:
            breaks.append(token)
            parts.append([])
        elif token[0] in OPERATOR_CHARS and lenient:
            breaks.append(token)
            parts.append([])
        elif token[0] in OPERATOR_CHARS:
            raise Unsupported(f"the operator {token} is not approved automatically")
        else:
            parts[-1].append(token)
    for index, part in enumerate(parts):
        opens_after = index < len(breaks) and breaks[index] == "("
        closes_before = index > 0 and breaks[index - 1] == ")"
        if not part and not (opens_after or closes_before or lenient):
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


def _body_file_problem(value: str, cwd: str, directories: Sequence[str]) -> str | None:
    """Why `value` may not be read by a gh file option, or None. It must be a relative path with no
    `..` that resolves inside `cwd` from every directory the command may run in, and that
    deny-secrets (`target_hit`, the check it makes of a Write or Edit target) would not block."""
    if not value or value == "-":
        return "needs the path of a file (not stdin)"
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
    problem = _body_file_problem(value, cwd, directories)
    if problem:
        raise Escalate(
            f"gh pr {name} {value!r} {problem}: write the file inside the worktree and pass its"
            " relative path (for a body, --body-file <relative path>)"
        )


def _check_long_option(
    option: str, pending: list[str], cwd: str, directories: Sequence[str]
) -> None:
    name, equals, value = option.partition("=")
    if name == "--repo":
        raise Escalate(REPO_REASON)
    if name in ("--web", "--editor"):
        raise Escalate(INTERACTIVE_REASON)
    if name in FILE_OPTIONS:
        file = value if equals else (pending.pop(0) if pending else "")
        _check_file_option(name, file, cwd, directories)


def _check_short_options(
    letters: str, pending: list[str], cwd: str, directories: Sequence[str]
) -> None:
    """The letters of one `-xyz` word, read the way gh's option parser does: a letter that takes a
    value ends the word, and the value is its rest or the next word."""
    for position, letter in enumerate(letters):
        if letter == "R":
            raise Escalate(REPO_REASON)
        if letter in "we":
            raise Escalate(INTERACTIVE_REASON)
        if letter in SHORT_VALUED:
            rest = letters[position + 1 :]
            # pflag drops one `=` after a short letter: `-F=path` has the value `path`.
            value = rest.removeprefix("=") if rest else (pending.pop(0) if pending else "")
            if letter in SHORT_FILE_OPTIONS:
                _check_file_option(f"-{letter}", value, cwd, directories)
            return


def _check_gh(arguments: Sequence[str], cwd: str, directories: Sequence[str]) -> None:
    """A `gh` segment (the words after `gh`): only `pr create`, `pr view` and `pr checks`. The
    shell metacharacters were refused with the whole text. Options are free but for another
    repository (`--repo`, `-R`), a browser or editor (`--web`, `--editor`), and the file options
    (`--body-file`, `--template`, `--recover`, `-F`, `-T`), which read a file the PM can post."""
    if len(arguments) < 2 or arguments[0] != "pr" or arguments[1] not in GH_PR_COMMANDS:
        name = " ".join(["gh", *arguments[:2]])
        raise Escalate(f"{name} is not approved automatically")
    pending = list(arguments[2:])
    while pending:
        option = pending.pop(0)
        if option == "--":
            return
        if option.startswith("--"):
            _check_long_option(option, pending, cwd, directories)
        elif option.startswith("-") and option != "-":
            _check_short_options(option[1:], pending, cwd, directories)


def _plan(command: str, cwd: str) -> list[GitStep]:
    """The git steps of a command made only of `cd`, gh pr segments, git segments and a last
    `| tail`/`| head`. Every cd and gh segment is checked here; the git steps are returned."""
    candidates = [cwd]
    steps: list[GitStep] = []
    segments = _segments(_tokens(command))
    for index, segment in enumerate(segments):
        words, _ = _without_merge(segment.words)
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


def _segment_is_guarded(words: Sequence[str]) -> bool:
    """A segment that runs gh, a git push, merge or send-pack, or a wrapper with one of those words."""
    start = 0
    while start < len(words) and (ASSIGNMENT.match(words[start]) or words[start] in SHELL_PREFIXES):
        start += 1
    command = words[start:]
    if not command:
        return False
    name = os.path.basename(command[0])
    if name == "gh":
        return True
    if name == "git":
        subcommand = _git_subcommand(command[1:])
        return subcommand in GUARDED_SUBCOMMANDS or bool(EXPANDING.search(subcommand))
    if name in WRAPPERS:
        # The quotes inside a word (`bash -c "git pu''sh"`) are the inner shell's to resolve.
        return bool(HOLDS_GUARDED.search(QUOTE_CHARS.sub("", " ".join(command))))
    return False


def _is_guarded(command: str) -> bool:
    """Whether the guard must run decide() on a command: a segment that is a push, merge,
    send-pack or gh command, or a wrapper holding such a word, or text the tokenizer cannot read
    that holds a git or gh word. A word in a message, a pattern or a file name is not one."""
    try:
        segments = _segments(_tokens(command, lenient=True), lenient=True)
    except Unsupported:
        return bool(HOLDS_GIT_OR_GH.search(command))
    return any(_segment_is_guarded(segment.words) for segment in segments)


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
        payload = json.loads(stdin.read())
        if not isinstance(payload, dict):
            raise PayloadError("payload is not an object")
        call = _call(payload)
    except (PayloadError, json.JSONDecodeError) as exc:
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
    # Fail closed on any error (closed stdin, RecursionError on deeply nested input, a bug): Claude
    # lets the call through to the prompt on anything but a decision.
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


def main(
    argv: Sequence[str] | None = None,
    stdin: TextIO | None = None,
    environ: Mapping[str, str] | None = None,
) -> int:
    if (os.environ if environ is None else environ).get(ROLE_VAR) != PM_ROLE:
        return 0  # not a PM session: stay out of the way
    args = sys.argv[1:] if argv is None else argv
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
