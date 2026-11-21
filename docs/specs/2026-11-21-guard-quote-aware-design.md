# PreToolUse guard: a quote-aware reading (coven/16)

The guard in `hooks/pm_permissions.py --pre-tool-use` decides whether decide() must run on a PM's
Bash command. Its lenient path used `shlex` (posix) and lost the quoting, so a quoted `;` split a
segment (`git -C ";" push -f`), substitutions were never read (`echo "$(git push -f)"`), and every
heredoc body line was read as a command. The strict approval path (`_tokens`, `_segments` and the
coven/14 `--title` handling) does not change.

## Token model

A small scanner of its own (`_Scanner`, see "Why not shell_words" below). One pass, an index into
the text, no regex backtracking. A token is a word, a separator (`;` `&` `&&` `||` `|` `|&` `;;`
`;&` `;;&` `(` `)` and newline) or a redirect (REDIRECTS plus `<<-`). A word's text has its quotes
removed. Only unquoted text can form an operator, so a quoted `;`, `|`, `>` is a word. Quotes:
single, double (backslash escapes `$` `` ` `` `"` `\` newline), backslash outside quotes,
backslash-newline joins. `#` at the start of a word is a comment to the end of the line. Digits or
`{name}` written directly before `<` or `>` are the redirect's descriptor; with a space they are a
word. `<<-` is one operator only when written together. The token after a redirect is its target;
an operator there is a syntax error in bash, and the guard reads it as an operator (more is read).
A here-string's word is kept as a heredoc body.

## Substitutions

`$(...)`, `` `...` ``, `<(...)` and `>(...)` are found unquoted and inside double quotes (the
first, second also in an unquoted heredoc body), never inside single quotes. The inner text is
read by a nested scanner (a backtick body after its escapes are undone) and checked with the same
logic; any guarded substitution guards the whole command. A `case` opened at the start of a
command inside `$(` is followed to its `esac`, so a pattern's `)` does not close the `$(`.
`${...}` is read to its matching brace with its own quotes and substitutions, so `${x:- #}` is not
a comment. `$((...))` is guarded when its text mentions a guarded word (below). Nesting is capped
at MAX_NESTING levels.

## Heredocs, here-strings and pipes

The delimiter word follows `<<`/`<<-`; the bodies start after the next unquoted newline, in
order. A line equal to the delimiter ends a body (tabs stripped for `<<-`; for an unquoted
delimiter after backslash-newline joining, as bash does). An unquoted delimiter's body is scanned
for substitutions, which always run. A heredoc or here-string body is read as commands when the
command it feeds reads commands: a shell (bash, sh, zsh, dash, ksh, mksh, ash, fish, csh, tcsh,
with any arguments), `source`, `.`, `eval`, a shell, `source` or `eval` as a word of an exec
wrapper (`stdbuf -oL bash`, `busybox sh`), an expansion (`$x`), a wrapper the guard cannot
unwrap, or a compound command (`} done fi esac )`). The words of any other command are its own
data: `git diff --name-only | grep bash` and `git status --short | grep -c .` are not guarded. Anything else (cat, tee, python3 -, node,
perl) is data. Such a command is also guarded when its own words mention a guarded word, as its
script then reads the body as data (`bash -c 'git $(cat)' <<EOF`), and when an unquoted body or a
here-string has an expansion and mentions a guarded word: the outer shell expands it first, so
`x=$(echo merge gh)` reaches the inner shell as `x=merge gh`, which runs gh. Fed by a pipe, `<`,
`<&` or `<>` instead, it is guarded when the whole text mentions a guarded word: what it runs is
not in view. A pipe feeds the commands of its own pipeline only, and every command inside a group or a
compound command it feeds (`| { bash; }`, `| while ...; do sh; done`): `git log | head && bash x`
is not fed. An opener counts at the start of a command, also after `time`, `time -p`, `function
NAME` and `coproc [NAME]`, and a closer ends only the open group it matches (`}` a `{`, `fi` an
`if`, `done` a loop, `esac` a case, `)` a `(`). A closer that matches none ends nothing, so a pipe
into an outer group still feeds what follows: that fails closed.

## Wrappers

env, nice, nohup, time, timeout, command, exec, builtin, sudo and xargs are unwrapped through
their known options (and env's and sudo's assignment words) to the inner command, which gets the
normal git and gh check. An option the guard cannot follow (`env -S`, `sudo -s`, `xargs -i{}`)
falls back to the word check HOLDS_GUARDED, which holds `send-pack`; shells, eval, trap (its
action), make and the exec wrappers (setsid, stdbuf, ionice, flock, taskset, chrt, unshare,
nsenter, script, doas, su, runuser, chroot, watch, strace, busybox and others) keep that check.
find runs the command after each `-exec`, `-execdir`, `-ok` or `-okdir`, up to its `;` or a `+`
right after `{}` (to the end when neither comes), and that command gets the normal check: `find .
-exec grep -n 'git push' {} \;` is not guarded, `find . -exec git push -f \;` is. xargs in front of git with no subcommand is guarded (stdin names it), and so is a
wrapper under xargs with no command of its own. A command word that expands (`$g push`, `$(echo
git) push`) is guarded when its words mention a guarded word. Assignments are `NAME=`, `NAME+=`
and `NAME[i]=`; `function NAME` and `coproc [NAME]` come before the command as prefixes do.

## Fail closed

Unsupported, then the fallback: the text guards when it mentions a git, gh, push, merge or
send-pack word once quotes and backslashes are removed (`g''it`, `\git`). The same test is used for
`$((...))` and for a shell fed by a pipe or a file. Unsupported is: an unbalanced quote, `$(`, `${`
or backtick; an unterminated heredoc; an unquoted `$'..'` or `$"..."`; a trailing backslash; a
NUL; a quote inside `${...}` inside double quotes; a heredoc line inside a substitution that starts
with the delimiter (bash ends the body at `EOF)`); nesting past the cap.

## Why not shell_words

`hooks/shell_words.py` has a scanner for prod-guard and deny-secrets. It is not reused, because its
contract is the opposite of the one this guard needs. Its job is to blank data only when it is sure
of it. On anything else it gives up (`_GiveUp`), and its callers then check the whole text:
- every `$(...)` other than Claude Code's `$(cat <<'X' ...)`;
- every backtick, `${`, process substitution and unquoted-delimiter heredoc;
- a `#` outside its comments mode;
- a backslash-newline that does not follow a blank;
- zsh-only forms.

This guard must read exactly those forms: the inside of each substitution, scanned recursively, and
the body of an unquoted heredoc as data whose substitutions run. Giving up there would bring back
the git-word fallback for `echo "$(git log)"` and for every unquoted notes heredoc, which is the
false positive this work removes. Making its scanner read them would change what `without_data`,
`code_text` and `heredoc_bodies` return to prod-guard and deny-secrets. A mode flag would avoid
that, but it would be a second scanner inside the first. The guard also needs more than that
scanner keeps: the nested substitution texts, the arithmetic texts, and the group and pipeline
structure. So the two scanners share the bash facts (and the tests pin both), not the code.

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
