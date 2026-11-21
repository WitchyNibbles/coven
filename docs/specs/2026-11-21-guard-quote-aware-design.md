# PreToolUse guard: a quote-aware reading (coven/16)

The guard in `hooks/pm_permissions.py --pre-tool-use` decides whether decide() must run on a PM's
Bash command. Its lenient path used `shlex` (posix) and lost the quoting, so a quoted `;` split a
segment (`git -C ";" push -f`), substitutions were never read (`echo "$(git push -f)"`), and every
heredoc body line was read as a command. The strict approval path (`_tokens`, `_segments` and the
coven/14 `--title` handling) does not change.

## Token model

A small scanner of its own (`_Scanner`; `shell_words.py` has one for the production guard, but this
guard keeps no sibling dependency for its reading). One pass, an index into the text, no regex
backtracking. A token is a word, a separator (`;` `&` `&&` `||` `|` `|&` `;;` `;&` `;;&` `(` `)`
and newline) or a redirect (REDIRECTS plus `<<-`). A word carries `quoted` (any part quoted or
escaped). Only unquoted text can form an operator, so a quoted `;`, `|`, `>` is a word. Quotes:
single, double (backslash escapes `$` `` ` `` `"` `\` newline), backslash outside quotes,
backslash-newline joins. `#` at the start of a word is a comment to the end of the line. Digits or
`{name}` written directly before `<` or `>` are the redirect's descriptor; with a space they are a
word. `<<-` is one operator only when written together. The token after a redirect is its target;
an operator there is a syntax error in bash, and the guard reads it as an operator (more is read).

## Substitutions

`$(...)`, `` `...` ``, `<(...)` and `>(...)` are found unquoted and inside double quotes (the
first, second also in an unquoted heredoc body), never inside single quotes. The inner text is
read by a nested scanner (a backtick body after its escapes are undone) and checked with the same
logic; any guarded substitution guards the whole command. `${...}` is read to its matching brace
with its own quotes and substitutions, so `${x:- #}` is not a comment. `$((...))` is guarded when
its text holds a git or gh word. Nesting is capped at MAX_NESTING levels.

## Heredocs

The delimiter word follows `<<`/`<<-`; the bodies start after the next unquoted newline, in
order. A line equal to the delimiter ends a body (tabs stripped for `<<-`; for an unquoted
delimiter after backslash-newline joining, as bash does). An unquoted delimiter's body is scanned
for substitutions, which always run. The body is read as commands when the command it feeds is a
shell (bash, sh, zsh, dash, ksh, mksh, ash, fish, csh, tcsh, with any arguments), `source`, `.`,
`eval`, a compound command (`} done fi esac )`), an expansion (`$x`), or a wrapper the guard
cannot unwrap. Anything else (cat, tee, python3 -, node, perl) is data. A shell fed by a pipe, `<`,
`<&`, `<>` or `<<<` is guarded when the text holds a git or gh word: what it runs is not in view.

## Wrappers

env, nice, nohup, time, timeout, command, exec, builtin, sudo and xargs are unwrapped through
their known options (and env's and sudo's `NAME=value` words) to the inner command, which gets the
normal git and gh check. An option the guard cannot follow (`env -S`, `sudo -s`, `xargs -I`)
falls back to the word check HOLDS_GUARDED, which now holds `send-pack`; shells, eval and make keep
that check. xargs in front of git with no subcommand is guarded (stdin names it). A command word
that expands (`$g push`, `$(echo git) push`) is guarded when its words hold a push, merge,
send-pack, git or gh word.

## Fail closed

Unsupported, then the old fallback (a git or gh word anywhere guards): an unbalanced quote,
`$(`, `${` or backtick; an unterminated heredoc; an unquoted `$'..'` or `$"..."`; a trailing
backslash; a NUL; a quote inside `${...}` inside double quotes; a heredoc line inside a
substitution that starts with the delimiter (bash ends the body at `EOF)`); nesting past the cap.

## coven/15, kept and removed

Kept: a redirect and its target are dropped, so `2>&1 gh pr merge 1` is still a gh segment.
Removed, as the scanner knows spacing and quoting: `_RedirectReader`, `_redirect_readings` and
MAX_AMBIGUOUS (descriptor or argument, `<<-` or `<< -`, operator after a redirect), and
`_is_push_past_a_redirect` with REDIRECT_MARK (`git -C ">" push`: the `>` is a word now).
`_tokens(lenient=True)`, `_segments(lenient=True)` and DOLLAR_QUOTE go too. PMs see fewer false
positives where the old readings guessed: `git log --grep push 2>&1`, `git status 2>a ... 2>g`
(the cap), `2 >&1 gh pr merge 1` (bash runs a command named `2`).

## The reported regression

Cause: the lenient path turned each newline into `;`, so heredoc body lines were read as commands
in every version. f75d02b (coven/14) changed `_segments` to treat a token as an operator only when
all of it is operator characters: a quoted word that starts with `(` or `;` used to be dropped as
a separator and is now a word. That fix was intended (`bash -c "(git push -f)"` was unguarded),
but its effect on heredoc bodies was not: a body line led by a wrapper word with such a quoted
word and a push or merge word later (`time "(5 min)" to merge`) is now guarded. A body line led by
`gh`, a `git push`, or an apostrophe (an unbalanced quote, then the git-word fallback) was guarded
before f75d02b too. With bodies read as data, none of these are guarded.
