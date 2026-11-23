"""Which parts of a Bash command line are only data, for the prod-guard and deny-secrets hooks.

`without_data(command)` returns the command with its data parts (a commit message, a PR body, a
search pattern, a quoted-delimiter heredoc fed to cat, tee or a message command) replaced by
spaces, so a hook that matches names in text does not refuse a command that only mentions one.
When the scanner cannot follow the command it returns it unchanged: the hook then checks all of it.

`code_text(command)` is the wider form for prod-guard's target and store checks. When every simple
command on the line is inert (runs nothing it is given), it blanks: the messages of
`git commit|tag|notes` and `gh pr|issue|release create|edit|comment|view` (file flags such as
`--body-file` stay), the patterns of `grep`, `rg` and `ag`, comments, and every heredoc body and
every echo/printf word of known value without a `$`. Bodies and echo or printf text stay when the
line has a pipe. Bodies, echo or printf text and messages all stay when the line writes a file
other than `/dev/null` (a redirection, tee's file, `sort -o`, git `--output`) and either has git on
it or writes a target that something may run later without being told its name. Compared in any
case, after quote removal, that is: a git hook (a path with a `.git` or `.husky` component, a
component containing `hook`, or a basename that is a git hook's name, such as `pre-commit` or
`update`, since core.hooksPath may name any folder but the hook keeps its name), a shell, direnv,
readline, git or Claude start-up file (`.bashrc`, `.bash_aliases`, `.zshenv`, `.profile`, `.envrc`,
`.inputrc`, `.gitconfig`, `.config/git/config`, `.claude.json`, `.mcp.json` and the like), a file under
`.ssh`, `.claude`, `.bashrc.d`, `.zshrc.d`, `.config/fish/`, `.config/autostart/` or `/etc/`, a
target that is not a plain literal (`$f`, a glob, `~`), or a relative target after a `cd`,
`pushd` or `popd` earlier on the line (`cd /etc; echo x > profile`). `code_text(command, lone)`
also counts as inert, and blanks with the data, every simple command whose word values are exactly
`lone` (prod-guard's `printenv <role variable>`) when it has no redirection and no pipe and the
text has no `(` (zsh reads `a=(...)` and `for v (...)` words as data). Nothing is blanked when
anything left as code holds a `$` or a backtick (`cd $HOME/r`, `echo $[$_]`): bash's `$_` is the
last word of the previous command, a blanked message or the self-check's words, and arithmetic on
`a[$(...)]` runs it. Any other line comes back unchanged.

`heredoc_bodies(command)` hands prod-guard's relative-glob check the command with its heredoc bodies
blanked, the bodies as written and the first word of each command that reads one, or None when
the scanner cannot follow the line or it has a pipe (see its docstring). `without_patterns(command)`
is the narrowest form, for the same check: only the pattern words of `grep`, `egrep`, `fgrep`, `rg`
and `git grep` are blanked, on any line (a pipe or another command on it changes nothing), and the
command comes back unchanged when the scanner cannot follow it. `without_quoted_globs(command)`
serves prod-guard's glob checks: every `*`, `?` and `[` inside quotes or after a backslash, which the
shell keeps literal, is masked; heredoc bodies and comments are left as written (see its docstring).

Known limits: file content written now and run later by name is outside a text-only guard (`echo
... > x.sh` passes, and so does the later `bash x.sh`). So are files a tool runs implicitly that
are not listed above: `conftest.py`, a `Makefile`, `package.json` scripts, `tox.ini` and the like
pass when Bash writes them, and so does anything the Write tool writes (the Write and Edit tools
need their own path checks). The guard is a tripwire for mistakes, not a sandbox.

Inert, by first word (any other first word, an assignment, `if`, a wrapper or `$x`, is not):

    cat tee echo grep ls mkdir   always
    cd pwd wc head tail          always
    printf  no `-v NAME` (it may set PATH), also glued (`-vNAME`)
    rg      no `--pre`, `--pre-glob`, `--hostname-bin`, and no word that may expand
    ag      no abbreviation of `--pager` from `--pa`, and no word that may expand
    sort    no abbreviation of `--compress-program` from `--co`, and no word that may expand
    git     only `-C <dir>` before commit, log, diff, status, show, add or notes, or before
            tag with a `-m`/`--message` word
    gh      pr, issue or release, then create, edit, comment or view

`code_text` reads comments (a `#` at the start of a word after a blank, a newline, `;`, `&` or `|`)
because Claude runs Bash non-interactively (`zsh -c`, `bash -c`), where comments are always on.
`without_data` still gives up on them: its output is unchanged from before `code_text`.

One pass over the text, no backtracking regex (prod-guard runs under a deadline). Standard library
only: the hooks run as `python3 -I`.

This is text matching, not a shell. It only ever blanks a word whose value it knows exactly (no
`$name`, glob or `~`), and only in a place where the word reaches `git`, `gh`, `grep`, `rg`,
`cat` (or, for `code_text`, another inert command) as plain data. Claude Code runs Bash commands in
bash or zsh, so every rule has to hold for both: where they differ (`>!`, `12>f`, aliases and
functions kept in tables) the scanner gives up, and `without_data` also gives up on comments.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum, auto

BLANKS = " \t"
# What ends a word outside quotes: a blank or the first character of an operator.
WORD_BREAKS = frozenset(BLANKS + "\n;&|()<>")
# Longest first, so that `<<<` is not read as `<<` and `<`.
OPERATORS = (
    *("&>>", "<<<", "<<-"),
    *(";;", "&&", "||", "|&", ">>", ">|", "<>", "<&", ">&", "&>", "<<"),
    *("\n", ";", "&", "|", "(", ")", "<", ">"),
)
HEREDOC_OPERATORS = frozenset({"<<", "<<-"})
REDIRECT_OPERATORS = HEREDOC_OPERATORS | {
    "<",
    "<<<",
    "<>",
    "<&",
    ">",
    ">>",
    ">|",
    ">&",
    "&>",
    "&>>",
}
# Redirections that open their target for writing; `>&` also writes when its target is not a
# file descriptor.
WRITE_OPERATORS = frozenset({">", ">>", ">|", "&>", "&>>", "<>"})
PIPES = frozenset({"|", "|&"})
DISCARD = "/dev/null"  # a write there writes no file
# Written files that something runs later without being told their name (all compared in lower
# case, after quote removal). A path with one of these components, or a component holding
# HOOK_MARK, may be a hook git runs, and so may a file whose basename is a git hook's name:
# core.hooksPath may name any folder (`/tmp/h/pre-commit`), but the hook file keeps its name.
# The ssh and Claude folders hold files those tools run; `.bashrc.d` and `.zshrc.d` hold snippets
# a shell sources at start-up.
RUN_LATER_COMPONENTS = frozenset({".git", ".husky", ".ssh", ".claude", ".bashrc.d", ".zshrc.d"})
HOOK_MARK = "hook"
GIT_HOOK_NAMES = frozenset(
    {
        *("applypatch-msg", "pre-applypatch", "post-applypatch", "pre-commit"),
        *("pre-merge-commit", "prepare-commit-msg", "commit-msg", "post-commit", "pre-rebase"),
        *("post-checkout", "post-merge", "pre-push", "pre-receive", "update", "proc-receive"),
        *("post-receive", "post-update", "reference-transaction", "push-to-checkout"),
        *("pre-auto-gc", "post-rewrite", "sendemail-validate", "fsmonitor-watchman"),
        *("p4-changelist", "p4-prepare-changelist", "p4-post-changelist", "p4-pre-submit"),
        "post-index-change",
    }
)
# Basenames a shell, direnv, readline, git or Claude reads at start-up (git config can set
# core.fsmonitor, core.hooksPath or an alias that runs a command; Claude's and MCP's settings
# name hooks and servers to start).
STARTUP_NAMES = frozenset(
    {
        *(".bashrc", ".bash_profile", ".bash_login", ".bash_logout", ".bash_aliases", ".profile"),
        *(".zshenv", ".zshrc", ".zprofile", ".zlogin", ".zlogout", ".envrc", ".inputrc"),
        *(".gitconfig", ".claude.json", ".mcp.json"),
    }
)
XDG_GIT_CONFIG = (".config", "git", "config")  # a path ending in these components
# A path under one of these runs of components: fish's start-up files, desktop autostart entries.
RUN_LATER_FOLDERS = ((".config", "fish"), (".config", "autostart"))
SYSTEM_CONFIG = "etc"  # an absolute path whose first component is this (`//etc`, `/./etc`)
CHANGE_DIRS = frozenset({"cd", "pushd", "popd"})  # after one, a relative path may be anywhere
# A `#` after one of these (or at the start) begins a comment; after `(`, `)` or a word it does not.
COMMENT_AFTER = frozenset(BLANKS + "\n;&|")
LITERAL_DOLLAR_FOLLOWERS = frozenset(' \t\n"')  # a `$` followed by anything else may expand
# A word holding one of these unquoted may expand to something else (zsh extendedglob: `^`, `#`,
# `~`): its value is unknown.
GLOB_CHARS = frozenset("*?[{}^#~")
PATH_GLOB_MARKS = frozenset("*?[")  # what makes a word a path glob, unquoted
LITERAL_GLOB_MARK = "%"  # stands for a quoted one: no glob, name or word break reads it
QUOTE_MARKS = frozenset("'\"\\")  # in a heredoc delimiter: the body is not expanded
DOUBLE_QUOTE_ESCAPES = frozenset('$`"\\')
ASCII_DIGITS = frozenset("0123456789")  # a single one before `<` or `>` is a file descriptor
# Commands that can redefine what a later command name runs (a function, an alias, a hash entry, a
# shell option), or run text the scanner has not read. A command starting with one is checked whole.
REDEFINERS = frozenset(
    {
        *("function", "alias", "unalias", "eval", "source", ".", "enable", "hash", "shopt"),
        *("setopt", "unsetopt", "emulate", "autoload", "zmodload", "typeset", "declare", "builtin"),
        *("export", "readonly", "local", "set"),  # can set PATH, extendedglob, allexport
        "trap",  # a DEBUG trap runs code before each later command, and can read its text
    }
)
# Words that may stand before the command proper: skipped when looking for its first word.
LEADING_WORDS = frozenset(
    {
        *("if", "then", "else", "elif", "do", "while", "until", "!", "time", "{", "}"),
        *("command", "exec", "nocorrect", "noglob"),
    }
)
# bash and zsh tables whose assignment (`BASH_ALIASES[cat]=sh`, `functions[cat]=...`) redefines a command.
COMMAND_TABLES = frozenset(
    {
        *("BASH_ALIASES", "BASH_CMDS", "aliases", "galiases", "saliases", "functions"),
        *("commands", "builtins", "dis_aliases", "dis_functions"),
    }
)
# Assignments that change where commands are looked up, or what a shell reads when it starts.
STARTUP_ASSIGNMENTS = (
    *("PATH=", "PATH+=", "path=", "path+=", "path[", "FPATH=", "fpath=", "fpath+="),
    *("BASH_ENV=", "ENV=", "ZDOTDIR=", "CDPATH="),
    # Environment that changes how git, gh, grep and rg behave (config, options, aliases).
    *("GIT_", "GH_", "GREP_", "UGREP", "RIPGREP_CONFIG_PATH"),
)
COPROC = "coproc"  # zsh and bash: a background process the next commands can write to
PATHSPEC_OPTION = "--pathspec-from-file"  # makes git read paths from a heredoc
PATHSPEC_ABBREVIATION = "--pathspec-f"  # git takes any unique prefix of a long option

DATA_SINKS = frozenset({"cat", "tee"})  # copy a heredoc to a file or to their output

# Short options that take no value, per search tool. Anything else ends the walk over the arguments.
GREP_LETTERS = frozenset("abcEFGHhIiLlnoPqRrsTUuVvwxyZz")
SEARCH_LETTERS = {
    **dict.fromkeys(("grep", "egrep", "fgrep"), GREP_LETTERS),
    "rg": frozenset("abcFHhIiLlNnoPpqSsUuVvwxz"),
}
GIT_GREP_LETTERS = frozenset("aEFHhIiLlnoPqvwz")
REGEXP_FLAGS = frozenset({"-e", "--regexp"})
REGEXP_PREFIX = "--regexp="
NUMBER_FLAGS = frozenset(
    {"-A", "-B", "-C", "-m", "--max-count", "--context", "--after-context", "--before-context"}
)
QUIET_LONG_FLAGS = frozenset(
    {
        *("--recursive", "--line-number", "--ignore-case", "--fixed-strings", "--extended-regexp"),
        *("--perl-regexp", "--word-regexp", "--invert-match", "--count", "--files-with-matches"),
        *("--files-without-match", "--only-matching", "--quiet", "--no-filename"),
        *("--with-filename", "--hidden", "--no-ignore"),
    }
)
QUIET_LONG_PREFIXES = ("--color=", "--colour=")


class _GiveUp(Exception):
    """The scanner cannot follow this command."""


@dataclass
class _Heredoc:
    delimiter: str
    strip_tabs: bool
    body: tuple[int, int] = (0, 0)  # filled in when the line ends


@dataclass(frozen=True)
class Word:
    """One word of a simple command, at text[start:end]."""

    start: int
    end: int
    raw: str  # the text of the word as written
    value: str | None  # what the shell makes of the word; None when it may expand


@dataclass(frozen=True)
class _Redirect:
    heredoc: _Heredoc | None
    writes: bool = False  # opens a file for writing
    target: str | None = None  # the value of its target word; None when it may expand


@dataclass(frozen=True)
class _Separator:
    text: str


_Token = Word | _Redirect | _Separator


@dataclass
class _WordBuilder:
    start: int
    parts: list[str] = field(default_factory=list)
    known: bool = True


def _is_name_char(char: str) -> bool:
    return char.isalnum() or char == "_"


def _is_descriptor(word: Word) -> bool:
    """Whether the target of `>&` is a file descriptor (`2`) or `-`, not a file."""
    value = word.value
    return value is not None and (value == "-" or (value != "" and set(value) <= ASCII_DIGITS))


def _redirect_writes(operator: str, target: Word) -> bool:
    """Whether a redirection opens a file for writing."""
    return operator in WRITE_OPERATORS or (operator == ">&" and not _is_descriptor(target))


def _skip_blanks(text: str, pos: int) -> int:
    while pos < len(text) and text[pos] in BLANKS:
        pos += 1
    return pos


def _find_body(text: str, start: int, heredoc: _Heredoc) -> int:
    """Where the body of `heredoc` ends, from `start`, the first character after a newline.

    Sets `heredoc.body` and returns the index of the newline that ends the delimiter line (or the
    end of the text)."""
    pos = start
    while pos < len(text):
        end = text.find("\n", pos)
        end = len(text) if end == -1 else end
        line = text[pos:end]
        candidate = line.lstrip("\t") if heredoc.strip_tabs else line
        if candidate == heredoc.delimiter:
            heredoc.body = (start, pos)
            return end
        if candidate.startswith(heredoc.delimiter):
            raise _GiveUp  # bash ends a heredoc inside `$(...)` on a line such as `EOF)`
        pos = end + 1
    raise _GiveUp  # the delimiter line never comes


class _Lexer:
    """Splits a command line into words, redirections and separators, reading heredoc bodies."""

    def __init__(self, text: str, *, comments: bool = False) -> None:
        self.text = text
        self.pos = 0
        self.tokens: list[_Token] = []
        self.pending: list[_Heredoc] = []  # heredocs whose body starts after the next newline
        self.comments = comments
        self.comment_spans: list[tuple[int, int]] = []

    def tokenize(self) -> list[_Token]:
        while self.pos < len(self.text):
            self._step()
        if self.pending:
            raise _GiveUp
        return self.tokens

    def _step(self) -> None:
        char = self.text[self.pos]
        if char in BLANKS:
            self.pos += 1
        elif self.text.startswith("\\\n", self.pos):
            self._skip_continuation()
        elif char == "#":
            self._comment()
        else:
            operator = next((op for op in OPERATORS if self.text.startswith(op, self.pos)), None)
            if operator is None:
                self._command_word()
            else:
                self.pos += len(operator)
                self._operator(operator)

    def _comment(self) -> None:
        """A comment up to the next newline. Without `comments` the scanner gives up: an
        interactive zsh reads one only with interactive_comments. A non-interactive shell always
        does."""
        if not self.comments or (self.pos > 0 and self.text[self.pos - 1] not in COMMENT_AFTER):
            raise _GiveUp
        end = self.text.find("\n", self.pos)
        end = len(self.text) if end == -1 else end
        self.comment_spans.append((self.pos, end))
        self.pos = end

    def _command_word(self) -> None:
        word = self._read_word()
        if word.raw in ASCII_DIGITS and self.text[self.pos : self.pos + 1] in ("<", ">"):
            return  # the file descriptor of the redirection that follows
        self.tokens.append(word)

    def _operator(self, operator: str) -> None:
        if operator in REDIRECT_OPERATORS:
            if self.text.startswith("(", self.pos):
                raise _GiveUp  # process substitution
            self._redirect(operator)
        else:
            self.tokens.append(_Separator(operator))
            if operator == "\n":
                self._read_bodies()

    def _redirect(self, operator: str) -> None:
        """A redirection and its target word (a heredoc's delimiter, for `<<`)."""
        self.pos = _skip_blanks(self.text, self.pos)
        if self.pos >= len(self.text) or self.text[self.pos] in WORD_BREAKS | {"#"}:
            raise _GiveUp
        word = self._read_word()
        if word.raw.startswith("!"):
            raise _GiveUp  # zsh: `>! file` and `>>! file`
        heredoc = None
        if operator in HEREDOC_OPERATORS:
            # An unquoted body expands, and a backslash-newline moves its end: only a quoted one is read.
            if word.value is None or not any(char in QUOTE_MARKS for char in word.raw):
                raise _GiveUp
            heredoc = _Heredoc(word.value, operator == "<<-")
            self.pending.append(heredoc)
        self.tokens.append(_Redirect(heredoc, _redirect_writes(operator, word), word.value))

    def _read_bodies(self) -> None:
        """The bodies of the heredocs started on the line that just ended, in order."""
        for heredoc in self.pending:
            end = _find_body(self.text, self.pos, heredoc)
            self.pos = min(end + 1, len(self.text))
        self.pending.clear()

    def _read_word(self) -> Word:
        word = _WordBuilder(self.pos)
        while self.pos < len(self.text) and self.text[self.pos] not in WORD_BREAKS:
            self._word_part(word)
        raw = self.text[word.start : self.pos]
        if self.pending and "\n" in raw:
            raise _GiveUp  # where a heredoc body starts is unclear: do not guess
        return Word(word.start, self.pos, raw, "".join(word.parts) if word.known else None)

    def _word_part(self, word: _WordBuilder) -> None:
        char = self.text[self.pos]
        if char == "\\":
            self._backslash(word)
        elif char == "'":
            self._single_quoted(word)
        elif char == '"':
            self._double_quoted(word)
        elif char == "$":
            self._dollar(word, in_double_quotes=False)
        elif char == "`":
            raise _GiveUp
        else:
            if char in GLOB_CHARS:
                word.known = False
            word.parts.append(char)
            self.pos += 1

    def _backslash(self, word: _WordBuilder) -> None:
        following = self.text[self.pos + 1 : self.pos + 2]
        if not following:
            raise _GiveUp  # a trailing lone backslash
        if following == "\n":
            self._skip_continuation()
        else:
            word.parts.append(following)
            self.pos += 2

    def _skip_continuation(self) -> None:
        """Skip a backslash-newline at the current position. Only one that follows a blank is
        read: next to an operator or a `$` bash and zsh join the lines in ways this scanner does
        not follow."""
        if self.pos == 0 or self.text[self.pos - 1] not in BLANKS:
            raise _GiveUp
        self.pos += 2

    def _single_quoted(self, word: _WordBuilder) -> None:
        end = self.text.find("'", self.pos + 1)
        if end == -1:
            raise _GiveUp
        word.parts.append(self.text[self.pos + 1 : end])
        self.pos = end + 1

    def _double_quoted(self, word: _WordBuilder) -> None:
        self.pos += 1
        while self.pos < len(self.text):
            char = self.text[self.pos]
            if char == '"':
                self.pos += 1
                return
            if char == "\\":
                self._double_quote_backslash(word)
            elif char == "$":
                self._dollar(word, in_double_quotes=True)
            elif char == "`":
                raise _GiveUp
            else:
                word.parts.append(char)
                self.pos += 1
        raise _GiveUp  # an unterminated quote

    def _double_quote_backslash(self, word: _WordBuilder) -> None:
        following = self.text[self.pos + 1 : self.pos + 2]
        if following in DOUBLE_QUOTE_ESCAPES:
            word.parts.append(following)
            self.pos += 2
        elif following == "\n":
            self._skip_continuation()
        else:  # the backslash is literal
            word.parts.append("\\")
            self.pos += 1

    def _dollar(self, word: _WordBuilder, *, in_double_quotes: bool) -> None:
        following = self.text[self.pos + 1 : self.pos + 2]
        if following == "(" and in_double_quotes:
            word.parts.append(self._cat_heredoc_substitution())
        elif following in ("(", "{"):
            raise _GiveUp
        elif not in_double_quotes and following in ("'", '"'):
            raise _GiveUp  # ANSI-C or translated string
        elif following and following not in LITERAL_DOLLAR_FOLLOWERS:
            # `$name`, `$1`, `$@`, and in zsh `$=name`, `$~name`, `$^name`: the value is unknown.
            word.known = False
            self.pos += 1
        else:  # a literal dollar sign
            word.parts.append("$")
            self.pos += 1

    def _cat_heredoc_substitution(self) -> str:
        """The value of `$(cat <<'X'`, body, `X`, `)` at the current position (Claude Code's own
        form of a PR body or commit message); any other command substitution gives up."""
        text = self.text
        pos = _skip_blanks(text, self.pos + 2)
        if not text.startswith("cat", pos):
            raise _GiveUp
        pos = _skip_blanks(text, pos + len("cat"))
        if not text.startswith("<<", pos):
            raise _GiveUp
        pos += len("<<")
        if text.startswith("-", pos):
            raise _GiveUp  # the shell strips the tabs of the body; the scanner would keep them
        delimiter, pos = self._quoted_delimiter(_skip_blanks(text, pos))
        pos = _skip_blanks(text, pos)
        if not text.startswith("\n", pos):
            raise _GiveUp
        heredoc = _Heredoc(delimiter, strip_tabs=False)
        end = _find_body(text, pos + 1, heredoc)
        close = end
        while close < len(text) and text[close] in " \t\n":
            close += 1
        if not text.startswith(")", close):
            raise _GiveUp
        self.pos = close + 1
        return text[heredoc.body[0] : heredoc.body[1]].rstrip("\n")

    def _quoted_delimiter(self, pos: int) -> tuple[str, int]:
        """A quoted heredoc delimiter (`'X'`, `"X"` or `\\X`) of name characters, and the index
        after it; any other form gives up."""
        text = self.text
        if text[pos : pos + 1] in ("'", '"'):
            end = text.find(text[pos], pos + 1)
            if end == -1:
                raise _GiveUp
            name, after = text[pos + 1 : end], end + 1
        elif text.startswith("\\", pos):
            after = pos + 1
            while after < len(text) and _is_name_char(text[after]):
                after += 1
            name = text[pos + 1 : after]
        else:
            raise _GiveUp
        if not name or not all(_is_name_char(char) for char in name):
            raise _GiveUp
        return name, after


def _is_under(parts: Sequence[str], folder: tuple[str, ...]) -> bool:
    """Whether the path components `parts` lie inside the components `folder`, met in a row."""
    size = len(folder)
    return any(tuple(parts[i : i + size]) == folder for i in range(len(parts) - size))


def _may_run_later(target: str | None) -> bool:
    """Whether a written file may be one that something runs later without being told its name:
    a git hook, a shell, git or Claude start-up file, a file under `.ssh`, `.claude`, a shell's
    snippet folder, `.config/fish`, `.config/autostart` or `/etc`, or a target whose value is
    unknown (`$f`, a glob, `~`)."""
    if target is None:
        return True
    lowered = target.lower()
    parts = [part for part in lowered.split("/") if part not in ("", ".")]
    in_system_config = lowered.startswith("/") and parts[:1] == [SYSTEM_CONFIG]
    if in_system_config or any(_is_under(parts, folder) for folder in RUN_LATER_FOLDERS):
        return True
    basename = parts[-1:]
    if STARTUP_NAMES.intersection(basename) or GIT_HOOK_NAMES.intersection(basename):
        return True
    if tuple(parts[-3:]) == XDG_GIT_CONFIG:
        return True
    return any(part in RUN_LATER_COMPONENTS or HOOK_MARK in part for part in parts)


@dataclass
class Command:
    """One simple command: its words (without redirections), the heredocs it reads, whether it
    writes a file (an output redirection, or an argument such as tee's file), and whether that
    file may run later (a hook, a start-up file). Also whether it has any redirection at all and
    whether a pipe joins it to another command."""

    words: list[Word] = field(default_factory=list)
    heredocs: list[_Heredoc] = field(default_factory=list)
    redirect_targets: list[str | None] = field(default_factory=list)  # the files it writes to
    writes: bool = False
    writes_run_later: bool = False
    redirected: bool = False
    in_pipeline: bool = False

    def add_write(self, target: str | None, *, cwd_moved: bool) -> None:
        """Record a written file. After a cd earlier on the line, a relative target's place is
        unknown (`cd /etc; echo x > profile`), so it counts as one that may run later."""
        if target == DISCARD:
            return
        self.writes = True
        if cwd_moved and target is not None and not target.startswith("/"):
            target = None
        self.writes_run_later = self.writes_run_later or _may_run_later(target)


def _changes_dir(words: Sequence[Word]) -> bool:
    return bool(words) and words[0].value in CHANGE_DIRS


def _commands(tokens: Sequence[_Token]) -> tuple[list[Command], bool]:
    """The simple commands of a token list, and whether the line has a pipe anywhere."""
    commands = [Command()]
    piped = False
    previous: _Token | None = None
    for token in tokens:
        if isinstance(token, Word):
            _check_word(token)
            commands[-1].words.append(token)
        elif isinstance(token, _Redirect):
            commands[-1].redirected = True
            if token.heredoc is not None:
                commands[-1].heredocs.append(token.heredoc)
            if token.writes:
                commands[-1].redirect_targets.append(token.target)
        else:
            _check_separator(token, previous)
            pipe = token.text in PIPES
            piped = piped or pipe
            commands[-1].in_pipeline = commands[-1].in_pipeline or pipe
            commands.append(Command(in_pipeline=pipe))
        previous = token
    cwd_moved = False
    for command in commands:
        _check_first_word(command.words)
        for target in [*command.redirect_targets, *_written_by_argument(command.words)]:
            command.add_write(target, cwd_moved=cwd_moved)
        cwd_moved = cwd_moved or _changes_dir(command.words)
    return commands, piped


def _tee_files(args: Sequence[Word]) -> list[str | None]:
    files: list[str | None] = []
    options = True
    for word in args:
        value = word.value
        if options and value == "--":
            options = False
        elif not options or value is None or value == "-" or not value.startswith("-"):
            files.append(value)
    return files


def _long_name(value: str) -> str:
    """The name of a long option (`--output=f` -> `output`); empty for any other word."""
    return value[2:].partition("=")[0] if value.startswith("--") else ""


def _value_at(args: Sequence[Word], index: int) -> str | None:
    """The value of the word at `index`; None when it may expand or is missing."""
    return args[index].value if index < len(args) else None


def _long_output(args: Sequence[Word], index: int, shortest: int) -> list[str | None]:
    """The file of `--output=FILE` or `--output FILE` (or an abbreviation of at least `shortest`
    letters) at `index`; empty for any other word."""
    value = args[index].value or ""
    name = _long_name(value)
    if len(name) < shortest or not "output".startswith(name):
        return []
    _, equals, attached = value.partition("=")
    return [attached if equals else _value_at(args, index + 1)]


def _sort_outputs(args: Sequence[Word]) -> list[str | None]:
    """The files of `-o FILE`, glued or in a cluster (`-oFILE`, `-uo FILE`), or `--output` and its
    abbreviations. Any other short cluster with an `o` counts too (`-to` sets the separator):
    reading too many is safe. A word that may expand may be one."""
    files: list[str | None] = []
    for index, word in enumerate(args):
        value = word.value
        if value is None:
            files.append(None)
        elif value.startswith("--"):
            files += _long_output(args, index, shortest=1)
        elif value.startswith("-") and "o" in value[1:]:
            files.append(value[1:].partition("o")[2] or _value_at(args, index + 1))
    return files


def _git_outputs(args: Sequence[Word]) -> list[str | None]:
    """The files of `git log|diff|show --output=FILE` (git accepts `--out` and longer
    abbreviations). A word that may expand may be one."""
    files: list[str | None] = []
    for index, word in enumerate(args):
        if word.value is None:
            files.append(None)
        else:
            files += _long_output(args, index, shortest=len("out"))
    return files


def _written_by_argument(words: Sequence[Word]) -> list[str | None]:
    """The files a command's arguments name for it to write; None for one that may expand."""
    name = words[0].value if words else None
    if name == "tee":
        return _tee_files(words[1:])
    if name == "sort":
        return _sort_outputs(words[1:])
    if name == "git":
        subcommand = _git_subcommand(words) or 0  # `-C <dir>` before it names no output
        return _git_outputs(words[subcommand + 1 :])
    return []


PAREN = _Separator("(")


def _check_separator(token: _Separator, previous: _Token | None) -> None:
    if previous == PAREN and token.text in ("(", ")"):
        raise _GiveUp  # a function definition, or arithmetic such as `(( x << 2 ))`


def _assigns_command_table(raw: str) -> bool:
    """Whether a word starts like `BASH_ALIASES[cat]=sh` or `functions[cat]=...`."""
    end = 0
    while end < len(raw) and _is_name_char(raw[end]):
        end += 1
    return raw[:end] in COMMAND_TABLES and raw[end : end + 1] in ("[", "=", "+")


def _check_word(word: Word) -> None:
    """Give up on a word that redefines a command or changes where commands are looked up, starts
    a coprocess, or makes git read paths from a heredoc."""
    if (
        word.raw.startswith(STARTUP_ASSIGNMENTS)
        or _assigns_command_table(word.raw)
        or word.value == COPROC
        or _names_pathspec_file(word.raw)
        or _names_pathspec_file(word.value or "")
    ):
        raise _GiveUp


def _names_pathspec_file(text: str) -> bool:
    """Whether `text` is git's `--pathspec-from-file`, maybe abbreviated (`--pathspec-from=-`)."""
    name = text.partition("=")[0]
    return name.startswith(PATHSPEC_ABBREVIATION) and PATHSPEC_OPTION.startswith(name)


def _is_assignment(word: Word) -> bool:
    name, equals, _ = word.raw.partition("=")
    return bool(equals) and name.rstrip("+").isidentifier()


def _check_first_word(words: Sequence[Word]) -> None:
    """Give up when the command proper (after `if`, `command`, `X=1`, ...) is a redefiner, or one
    whose name is not known."""
    for word in words:
        if word.raw in LEADING_WORDS or word.value in LEADING_WORDS or _is_assignment(word):
            continue
        if word.value is not None and word.value.startswith("-"):
            continue  # an option of `command` or `time`
        if word.value is None or word.value in REDEFINERS:
            raise _GiveUp
        return


@dataclass(frozen=True)
class _MessageSyntax:
    """How the options of a command that takes a message are written."""

    flags: frozenset[str]  # followed by the message
    prefixes: tuple[str, ...]  # start of a word that holds the message (`--body=...`)
    quiet: frozenset[str]  # options that take no value
    file_flags: frozenset[str]  # followed by a file name, where `-` is standard input
    stdin_attached: frozenset[str]  # one word asking for standard input (`--file=-`)
    quiet_letters: frozenset[str] = frozenset()  # short options without a value, alone or glued
    letters_then_m: bool = False  # `-am`: quiet letters and then the message flag


GIT_MESSAGE = _MessageSyntax(
    flags=frozenset({"-m", "--message"}),
    prefixes=("--message=",),
    quiet=frozenset(
        {
            *("--all", "--amend", "--no-edit", "--edit", "--allow-empty", "--no-verify"),
            *("--allow-empty-message", "--signoff", "--quiet", "--verbose", "--annotate"),
            *("--sign", "--force", "--no-ff", "--ff", "--ff-only", "--no-commit"),
        }
    ),
    file_flags=frozenset({"-F", "--file"}),
    stdin_attached=frozenset({"--file=-"}),
    quiet_letters=frozenset("aenqsvS"),
    letters_then_m=True,
)
GH_MESSAGE = _MessageSyntax(
    flags=frozenset({"-b", "--body", "-t", "--title", "-n", "--notes", "--subject"}),
    prefixes=("--body=", "--title=", "--notes=", "--subject="),
    quiet=frozenset(
        {
            *("-d", "-f", "-w", "--draft", "--web", "--fill", "--fill-first", "--fill-verbose"),
            *("--prerelease", "--latest", "--generate-notes", "--verify-tag", "--dry-run"),
            *("--no-maintainer-edit", "--squash", "--merge", "--rebase", "--delete-branch"),
            *("--auto", "--admin", "--approve", "--request-changes", "--yes"),
        }
    ),
    file_flags=frozenset({"-F", "--body-file", "--notes-file"}),
    stdin_attached=frozenset({"--body-file=-", "--notes-file=-"}),
)
GIT_MESSAGE_COMMANDS = frozenset({"commit", "tag", "merge"})
GH_NOUNS = frozenset({"pr", "issue", "release"})
GH_ACTIONS = frozenset({"create", "edit", "comment", "review", "merge", "close", "reopen"})


def _is_message_flag(value: str, syntax: _MessageSyntax) -> bool:
    if value in syntax.flags:
        return True
    return (
        syntax.letters_then_m
        and len(value) > 1
        and value[0] == "-"
        and value[-1] == "m"
        and all(char in syntax.quiet_letters for char in value[1:-1])
    )


def _takes_no_value(value: str | None, syntax: _MessageSyntax) -> bool:
    """Whether the word before an option cannot be waiting for that option's value: it is a plain
    word, an `--opt=value` word, or an option known to take no value."""
    if value is None:
        return False
    if not value.startswith("-"):
        return True
    if value.startswith("--"):
        return "=" in value or value in syntax.quiet
    if value in syntax.quiet:
        return True
    return len(value) > 1 and all(char in syntax.quiet_letters for char in value[1:])


def _option_values(words: Sequence[Word], syntax: _MessageSyntax) -> list[Word]:
    """The message words among the arguments of a message command. A message flag counts only when
    the word before it cannot be an option waiting for its value: `--trailer -m x` has `-m` as the
    value of `--trailer`, and `x` may then be a path. The walk stops at `--`."""
    found: list[Word] = []
    settled = True
    index = 0
    while index < len(words) and words[index].value != "--":
        word = words[index]
        value = word.value
        if settled and value is not None and _is_message_flag(value, syntax):
            found += _known(words[index + 1 : index + 2])
            index += 2
        elif settled and value is not None and value.startswith(syntax.prefixes):
            found.append(word)
            index += 1
        elif settled and value in syntax.file_flags:
            index += 2  # `-F file`: the next word is its value
        else:
            settled = _takes_no_value(value, syntax)
            index += 1
    return found


def _reads_stdin(words: Sequence[Word], syntax: _MessageSyntax) -> bool:
    """Whether the message command is told to read its message from standard input."""
    for index, word in enumerate(words):
        if word.value == "--":
            return False
        if word.value in syntax.stdin_attached:
            return True
        following = words[index + 1 : index + 2]
        if word.value in syntax.file_flags and following and following[0].value == "-":
            return True
    return False


def _known(words: Sequence[Word]) -> list[Word]:
    return [word for word in words if word.value is not None]


class _Arg(Enum):
    REGEXP = auto()  # -e PATTERN
    REGEXP_ATTACHED = auto()  # --regexp=PATTERN
    NUMBER = auto()  # an option followed by a number
    FLAG = auto()  # an option without a value
    END = auto()  # --
    UNKNOWN = auto()  # an option this scanner does not know, or a word that may expand
    PLAIN = auto()


def _classify(value: str | None, letters: frozenset[str]) -> _Arg:
    if value is None:
        return _Arg.UNKNOWN
    if value in REGEXP_FLAGS:
        return _Arg.REGEXP
    if value.startswith(REGEXP_PREFIX):
        return _Arg.REGEXP_ATTACHED
    if value in NUMBER_FLAGS:
        return _Arg.NUMBER
    if value == "--":
        return _Arg.END
    if value.startswith("--"):
        quiet = value in QUIET_LONG_FLAGS or value.startswith(QUIET_LONG_PREFIXES)
        return _Arg.FLAG if quiet else _Arg.UNKNOWN
    if value.startswith("-") and len(value) > 1:
        return _Arg.FLAG if all(char in letters for char in value[1:]) else _Arg.UNKNOWN
    return _Arg.PLAIN


def _options_may_follow(words: Sequence[Word]) -> bool:
    """Whether a later word could be an option: GNU grep, ugrep and rg accept options after plain
    words, so `grep PATH -e PATTERN` reads PATH as a file. A word that may expand counts."""
    for word in words:
        if word.value == "--":
            return False
        if word.value is None or word.value.startswith("-"):
            return True
    return False


def _search_patterns(args: Sequence[Word], letters: frozenset[str]) -> list[Word]:
    """The pattern words of a grep-like command's arguments. The walk ends at the first word it
    does not understand, so `-f FILE`, `-g GLOB`, `--pre CMD` stay checked, and a plain word counts
    as the pattern only when no option can follow it. The files after it are never blanked."""
    patterns: list[Word] = []
    has_regexp = False
    index = 0
    while index < len(args):
        kind = _classify(args[index].value, letters)
        if kind is _Arg.REGEXP:
            has_regexp = True
            patterns += _known(args[index + 1 : index + 2])
            index += 2
        elif kind is _Arg.REGEXP_ATTACHED:
            has_regexp = True
            patterns.append(args[index])
            index += 1
        elif kind is _Arg.NUMBER:
            index += 2
        elif kind is _Arg.FLAG:
            index += 1
        else:
            if not has_regexp and kind is _Arg.PLAIN:
                if not _options_may_follow(args[index + 1 :]):
                    patterns.append(args[index])
            elif not has_regexp and kind is _Arg.END:
                patterns += _known(args[index + 1 : index + 2])
            break
    return patterns


def _git_subcommand(words: Sequence[Word]) -> int | None:
    """The index of the subcommand in `git [-C <dir>]... <subcommand>`; None for other commands."""
    if words[0].value != "git":
        return None
    index = 1
    while index + 1 < len(words) and words[index].value == "-C":
        index += 2
    return index if index < len(words) else None


def _is_gh_action(words: Sequence[Word], actions: frozenset[str]) -> bool:
    """Whether the command is `gh pr|issue|release <action>` with one of `actions`."""
    return (
        len(words) > 2
        and words[0].value == "gh"
        and words[1].value in GH_NOUNS
        and words[2].value in actions
    )


def _message_command(
    words: Sequence[Word],
    git_commands: frozenset[str] = GIT_MESSAGE_COMMANDS,
    gh_actions: frozenset[str] = GH_ACTIONS,
) -> tuple[_MessageSyntax, Sequence[Word]] | None:
    """The option syntax and the arguments of a `git <git_commands>` or `gh pr|issue|release
    <gh_actions>` command; None when it is neither."""
    subcommand = _git_subcommand(words)
    if subcommand is not None and words[subcommand].value in git_commands:
        return GIT_MESSAGE, words[subcommand + 1 :]
    return (GH_MESSAGE, words[3:]) if _is_gh_action(words, gh_actions) else None


def _pattern_words(words: Sequence[Word]) -> list[Word]:
    name = words[0].value
    if name is not None and name in SEARCH_LETTERS:
        return _search_patterns(words[1:], SEARCH_LETTERS[name])
    if name == "git" and len(words) > 1 and words[1].value == "grep":
        return _search_patterns(words[2:], GIT_GREP_LETTERS)
    return []


def _command_data(command: Command, *, piped: bool) -> list[tuple[int, int]]:
    """The spans of one simple command that are only data."""
    if not command.words:
        return []
    message = _message_command(command.words)
    if message is None:
        words = _pattern_words(command.words)
        is_sink = command.words[0].value in DATA_SINKS
    else:
        syntax, args = message
        words = _option_values(args, syntax)
        # Only a message read from standard input takes the heredoc as a message, not as paths.
        is_sink = _reads_stdin(args, syntax)
    spans = [(word.start, word.end) for word in words]
    if is_sink and not piped:  # a piped body may be run by the next command
        spans += [heredoc.body for heredoc in command.heredocs]
    return spans


def _blanked(command: str, spans: Sequence[tuple[int, int]]) -> str:
    chars = list(command)
    for start, end in spans:
        chars[start:end] = " " * (end - start)
    return "".join(chars)


def simple_commands(command: str) -> list[Command] | None:
    """The simple commands of `command`, in line order, comments read as comments (some come back
    without words: a separator ends one); None when the scanner cannot follow the command."""
    try:
        commands, _piped = _commands(_Lexer(command, comments=True).tokenize())
    except _GiveUp:
        return None
    return commands


@dataclass(frozen=True)
class HeredocBodies:
    """A line's heredoc bodies: the line with each body blanked (same length), the bodies as
    written, and the name of each command that reads one (its first word's value; None when
    that is unknown or the command has no word)."""

    rest: str
    bodies: list[str]
    readers: list[str | None]


def heredoc_bodies(command: str) -> HeredocBodies | None:
    """The heredoc bodies of `command`, in line order, and who reads them.

    None when the line cannot be read as plain words: the scanner cannot follow it (this includes
    an unquoted heredoc), or a pipe is on it (the next command may run a body)."""
    try:
        commands, piped = _commands(_Lexer(command, comments=True).tokenize())
    except _GiveUp:
        return None
    if piped:
        return None
    readers = [c for c in commands if c.heredocs]
    spans = [heredoc.body for c in readers for heredoc in c.heredocs]
    return HeredocBodies(
        rest=_blanked(command, spans),
        bodies=[command[start:end] for start, end in spans],
        readers=[c.words[0].value if c.words else None for c in readers],
    )


def without_patterns(command: str) -> str:
    """`command` with only the pattern words of its grep-like commands (`grep`, `egrep`, `fgrep`,
    `rg`, `git grep`) replaced by spaces (same length), or `command` unchanged when the scanner
    cannot follow it. A heredoc body, a file operand and a word the shell expands (`grep w*`) are
    never blanked: unlike `without_data`, this is for a check that must still read a body."""
    try:
        commands, _piped = _commands(_Lexer(command).tokenize())
    except _GiveUp:
        return command
    spans = [(w.start, w.end) for c in commands if c.words for w in _pattern_words(c.words)]
    return _blanked(command, spans)


class _QuotedGlobs:
    """One pass over a line that masks the glob characters the shell keeps literal."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.chars = list(text)
        self.pos = 0
        self.pending: list[_Heredoc] = []  # heredocs whose body starts after the next newline

    def masked(self) -> str:
        while self.pos < len(self.text):
            self._step()
        if self.pending:
            raise _GiveUp
        return "".join(self.chars)

    def _mask(self, start: int, end: int) -> None:
        for index in range(start, end):
            if self.text[index] in PATH_GLOB_MARKS:
                self.chars[index] = LITERAL_GLOB_MARK

    def _step(self) -> None:
        text, pos = self.text, self.pos
        char = text[pos]
        if char == "`" or text.startswith(("$'", '$"'), pos):
            raise _GiveUp  # a backtick's own quoting, an ANSI-C or translated string
        if char in "\\'\"":
            self._quoted(char)
        elif char == "#" and (pos == 0 or text[pos - 1] in COMMENT_AFTER):
            end = text.find("\n", pos)
            self.pos = len(text) if end == -1 else end
        elif text.startswith("<<<", pos):
            self.pos += 3
        elif text.startswith("<<", pos):
            self._heredoc()
        elif char == "\n":
            self._skip_bodies()
        else:
            self.pos += 1

    def _quoted(self, mark: str) -> None:
        """A backslash and the character it escapes, or a single- or double-quoted string."""
        text, pos = self.text, self.pos
        if mark == '"':
            self._double_quoted()
            return
        end = pos + 1 if mark == "\\" else text.find("'", pos + 1)
        if end == -1 or end >= len(text):
            raise _GiveUp  # a trailing backslash or an unterminated quote
        self._mask(pos + 1, end + 1 if mark == "\\" else end)
        self.pos = end + 1

    def _skip_bodies(self) -> None:
        """A newline, then the bodies of the heredocs started on the line it ends, as written."""
        self.pos += 1
        for heredoc in self.pending:
            self.pos = min(_find_body(self.text, self.pos, heredoc) + 1, len(self.text))
        self.pending.clear()

    def _double_quoted(self) -> None:
        """A double-quoted string. A `$(...)` or a backtick in one starts a new quoting context,
        which this pass does not follow."""
        text = self.text
        pos = self.pos + 1
        while pos < len(text):
            char = text[pos]
            if char == '"':
                self.pos = pos + 1
                return
            if char == "`" or text.startswith("$(", pos):
                raise _GiveUp
            if char == "\\":
                pos += 1  # the escaped character is literal either way
            self._mask(pos, pos + 1)
            pos += 1
        raise _GiveUp  # an unterminated quote

    def _heredoc(self) -> None:
        """A heredoc's delimiter word. Its body, quoted delimiter or not, is left as written: a
        body is read by its own rules."""
        text = self.text
        pos = self.pos + 2
        strip_tabs = text.startswith("-", pos)
        pos = _skip_blanks(text, pos + 1 if strip_tabs else pos)
        start = pos
        while pos < len(text) and text[pos] not in WORD_BREAKS:
            if text[pos] in "`$":
                raise _GiveUp
            pos += 1
        delimiter = "".join(char for char in text[start:pos] if char not in QUOTE_MARKS)
        if not delimiter:
            raise _GiveUp
        self.pending.append(_Heredoc(delimiter, strip_tabs))
        self.pos = pos


def without_quoted_globs(command: str) -> str:
    """`command` with every `*`, `?` and `[` the shell keeps literal, inside single or double
    quotes or after a backslash, replaced by LITERAL_GLOB_MARK (same length), so a check that
    reads globs skips them; or `command` unchanged when this pass cannot follow it: a backtick, a
    `$(...)` inside double quotes, an ANSI-C or translated string (`$'...'`, `$"..."`), an
    unterminated quote, or a heredoc whose delimiter line never comes.

    Heredoc bodies and comments are left as written, and a quote in them opens nothing. Whether a
    quoted word is still a glob to the program that gets it (`sh -c '...'`, `find -name '*'`) is
    the caller's to judge."""
    try:
        return _QuotedGlobs(command).masked()
    except _GiveUp:
        return command


def without_data(command: str) -> str:
    """`command` with its data parts replaced by spaces (same length), or `command` unchanged
    when the scanner cannot follow it."""
    try:
        commands, piped = _commands(_Lexer(command).tokenize())
    except _GiveUp:
        return command
    return _blanked(command, [span for c in commands for span in _command_data(c, piped=piped)])


# code_text: commands that run nothing they are given, and the data they take.
ALWAYS_INERT = frozenset(
    {"cat", "tee", "echo", "printf", "grep", "ls", "mkdir", "cd", "pwd", "wc", "head", "tail"}
)
# Long options that run a command, with the shortest abbreviation each tool accepts (ag and sort
# use getopt, which takes any unambiguous prefix; rg takes none). From `rg --help`, `ag --help`,
# `sort --help`.
COMMAND_OPTIONS: dict[str, tuple[tuple[str, int], ...]] = {
    "rg": (("pre", 3), ("pre-glob", 8), ("hostname-bin", 12)),
    "ag": (("pager", 2),),
    "sort": (("compress-program", 2),),
}
CODE_SEARCH_LETTERS = {**SEARCH_LETTERS, "ag": frozenset("acfFHhilLnQsStuUvwz0")}
CODE_GIT_MESSAGE_COMMANDS = frozenset({"commit", "tag", "notes"})
CODE_GIT_SUBCOMMANDS = frozenset({"commit", "log", "diff", "status", "show", "add", "notes"})
CODE_GH_ACTIONS = frozenset({"create", "edit", "comment", "view"})
TAG_MESSAGE_FLAGS = frozenset({"-m", "--message"})
TEXT_COMMANDS = frozenset({"echo", "printf"})
# Left as code, a word holding one of these may expand to what was blanked: bash's `$_` is the
# last word of the previous command (a message, the self-check's words), and arithmetic on
# `a[$(...)]` runs it.
EXPANSION_MARKS = frozenset("$`")
# `printf -v NAME` sets a variable, which may be PATH: the commands after it may then run anything.
PRINTF_VARIABLE = "-v"


def _runs_a_command(value: str | None, options: Sequence[tuple[str, int]]) -> bool:
    """Whether an argument may expand to anything, or is an option that runs a command."""
    if value is None:
        return True
    name = _long_name(value)
    return any(len(name) >= shortest and full.startswith(name) for full, shortest in options)


def _git_is_inert(words: Sequence[Word]) -> bool:
    subcommand = _git_subcommand(words)
    if subcommand is None:
        return False
    if words[subcommand].value in CODE_GIT_SUBCOMMANDS:
        return True
    return words[subcommand].value == "tag" and any(
        word.value in TAG_MESSAGE_FLAGS or (word.value or "").startswith("--message=")
        for word in words[subcommand + 1 :]
    )


def _is_inert(words: Sequence[Word]) -> bool:
    """Whether a simple command runs nothing it is given (the table in the module docstring)."""
    name = words[0].value
    if name == "printf":
        return not any((word.value or "").startswith(PRINTF_VARIABLE) for word in words[1:])
    if name in ALWAYS_INERT:
        return True
    if name is not None and name in COMMAND_OPTIONS:
        return not any(_runs_a_command(word.value, COMMAND_OPTIONS[name]) for word in words[1:])
    if name == "git":
        return _git_is_inert(words)
    return _is_gh_action(words, CODE_GH_ACTIONS)


def _text_words(words: Sequence[Word]) -> list[Word]:
    """The echo or printf words whose value is known and holds no `$`."""
    return [word for word in words[1:] if word.value is not None and "$" not in word.raw]


def _code_data(command: Command, *, keep_messages: bool, keep_text: bool) -> list[tuple[int, int]]:
    """The data spans of one inert simple command: search patterns, messages unless
    `keep_messages`, and heredoc bodies and echo/printf text unless `keep_text`."""
    words = command.words
    name = words[0].value
    found: list[Word] = []
    if name is not None and name in CODE_SEARCH_LETTERS:
        found += _search_patterns(words[1:], CODE_SEARCH_LETTERS[name])
    message = _message_command(words, CODE_GIT_MESSAGE_COMMANDS, CODE_GH_ACTIONS)
    if message is not None and not keep_messages:
        syntax, args = message
        found += _option_values(args, syntax)
    if keep_text:
        return [(word.start, word.end) for word in found]
    if name in TEXT_COMMANDS:
        found += _text_words(words)
    return [(word.start, word.end) for word in found] + [h.body for h in command.heredocs]


def _is_lone(command: Command, lone: Sequence[str]) -> bool:
    """Whether a simple command's word values are exactly `lone`, with no redirection (a heredoc
    included) and no pipe on either side."""
    return (
        bool(command.words)
        and not (command.redirected or command.in_pipeline)
        and tuple(word.value for word in command.words) == tuple(lone)
    )


def code_text(command: str, lone: Sequence[str] = ()) -> str:
    """`command` with its data replaced by spaces (same length) when every simple command on it is
    inert, or `command` unchanged (also when the scanner cannot follow it).

    On a line with a pipe, heredoc bodies and echo/printf text stay. On a line that writes a file
    that may be a hook, or has git and writes any file (a hook git then runs), messages stay too,
    since `git log` or `gh pr view` can write a stored message into that file.

    A simple command whose word values are exactly `lone` (a read-only self-check), with no
    redirection and no pipe, counts as inert and is blanked with the data, so only when every
    other simple command is inert too: a later command could read its words back (`$_`, a
    trap's `$BASH_COMMAND`, `$ZSH_EXECUTION_STRING`). Not when the text has a `(` anywhere: zsh
    reads the words of `a=(...)`, `for v (...)` and `foreach v (...)` as data, so the "command"
    there may be a list of names.

    When what is left as code holds a `$` or a backtick, nothing is blanked: it may expand to
    the blanked text (`$_` after a message, then `echo $[$_]` runs `a[$(...)]` in bash)."""
    reader = _Lexer(command, comments=True)
    try:
        tokens = reader.tokenize()
        commands, piped = _commands(tokens)
    except _GiveUp:
        return command
    if PAREN in tokens:
        lone = ()
    lone_spans = [(c.words[0].start, c.words[-1].end) for c in commands if _is_lone(c, lone)]
    simple = [c for c in commands if c.words and not _is_lone(c, lone)]
    if not all(_is_inert(c.words) for c in simple):
        return command
    has_git = any(c.words[0].value == "git" for c in simple)
    rewrites = any(c.writes_run_later for c in commands) or (
        has_git and any(c.writes for c in commands)
    )
    spans = [*reader.comment_spans, *lone_spans]
    for c in simple:
        spans += _code_data(c, keep_messages=rewrites, keep_text=piped or rewrites)
    blanked = _blanked(command, spans)
    if EXPANSION_MARKS.intersection(blanked):
        return command  # what is left may expand to the blanked text (`$_`, `$[...]`)
    return blanked
