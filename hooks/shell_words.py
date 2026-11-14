"""Which parts of a Bash command line are only data, for the deny-secrets hook.

`without_data(command)` returns the command with its data parts (a commit message, a PR body, a
search pattern, a quoted-delimiter heredoc fed to cat, tee or a message command) replaced by
spaces, so a hook that matches names in text does not refuse a command that only mentions one.
When the scanner cannot follow the command it returns it unchanged: the hook then checks all of it.

It gives up on: `$(...)`, `${...}`, backticks, comments, process substitution, `((`, a function
or alias definition, `eval`, `hash` and the like, an unquoted heredoc, a wrapper or an assignment
before the command, and a word that may expand where a value is needed.

This is text matching, not a shell. It only ever blanks a word whose value it knows exactly (no
`$name`, glob or `~`), and only in a place where the word reaches `git`, `gh`, `grep`, `rg`, `cat`
or `tee` as plain data. Claude Code runs Bash commands in bash or zsh, so every rule has to hold
for both: where they differ the scanner gives up. Standard library only, one pass over the text.
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
PIPES = frozenset({"|", "|&"})
LITERAL_DOLLAR_FOLLOWERS = frozenset(' \t\n"')  # a `$` followed by anything else may expand
# A word holding one of these unquoted may expand to something else (zsh extendedglob: `^`, `#`,
# `~`): its value is unknown.
GLOB_CHARS = frozenset("*?[{}^#~")
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

    def __init__(self, text: str) -> None:
        self.text = text
        self.pos = 0
        self.tokens: list[_Token] = []
        self.pending: list[_Heredoc] = []  # heredocs whose body starts after the next newline

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
            raise _GiveUp  # an interactive zsh reads a comment only with interactive_comments
        else:
            operator = next((op for op in OPERATORS if self.text.startswith(op, self.pos)), None)
            if operator is None:
                self._command_word()
            else:
                self.pos += len(operator)
                self._operator(operator)

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
        self.tokens.append(_Redirect(heredoc))

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


@dataclass
class Command:
    """One simple command: its words (without redirections) and the heredocs it reads."""

    words: list[Word] = field(default_factory=list)
    heredocs: list[_Heredoc] = field(default_factory=list)


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
            if token.heredoc is not None:
                commands[-1].heredocs.append(token.heredoc)
        else:
            _check_separator(token, previous)
            piped = piped or token.text in PIPES
            commands.append(Command())
        previous = token
    for command in commands:
        _check_first_word(command.words)
    return commands, piped


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


def without_data(command: str) -> str:
    """`command` with its data parts replaced by spaces (same length), or `command` unchanged
    when the scanner cannot follow it."""
    try:
        commands, piped = _commands(_Lexer(command).tokenize())
    except _GiveUp:
        return command
    return _blanked(command, [span for c in commands for span in _command_data(c, piped=piped)])
