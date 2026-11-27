# Guards: a shared lexing layer, phase 1

Status: task opencode-m2/6; the GM ruled the split (ANSWER opencode-m2/6, 2026-11-26). Scope is
phase 1; phase 2 (one shell tree, one Moves analysis, capability tables per program, one hook entry)
is task opencode-m2/6b, after #43-#45 and this PR merge.

## Why

coven's Python guards read the same Bash text with their own copies of the same knowledge. A map of
the hooks at 3497a0c (plan workspace, `guard-lexer-map.md`) found five tokenisers, three wrapper
tables, two `SHELL_READERS` sets, two brace expanders and no test that runs one command through more
than one guard. The copies drift: opencode's server guard has no brace ranges and keeps words
literal past its brace cap, pm_permissions does not know `npx` or `tmux` as wrappers, opencode's
`sudo` and `xargs` tables miss options pm has. prod_guard lexes one Bash call about 14 times.

## Goals

1. A cross-guard corpus: one list of commands, each run through every guard that reads Bash
   (deny_secrets, pm_permissions guard mode, prod_guard, opencode_server_guard), with each guard's
   verdict pinned. It lands first, on the current code, and every later task keeps it green except
   for flips listed in the plan.
2. One lex per lexer variant per hook process: shell_words caches its token stream by
   `(text, variant)`, so prod's call sites share a parse.
3. One brace expander, in shell_words, used by prod_guard and opencode_server_guard.
4. `SHELL_READERS` defined once, in wrapper_commands.
5. One wrapper name and option table, in wrapper_commands, used by prod_guard,
   opencode_server_guard and pm_permissions.

## The direction rule

Every merge takes the stricter side. No verdict in the corpus or in the guards' own tests may flip
from refuse to allow. A flip from allow to refuse is allowed only when the plan lists it; the PR lists
every one. Where two copies differ and the stricter side is not clear, the copy stays as it is and the
case goes in the PR as a phase 2 item.

## Design

- **Corpus** (`tests/hooks/test_guard_corpus.py`, data in `tests/hooks/guard_corpus.py`): each entry is a
  command, the context it runs in (cwd, a scratch HOME with a fake serve folder, store, guard config),
  and the expected verdict per guard (`allow`, `refuse`, or `n/a`). Each guard is run the way its
  hook runs it (its `main`/`cli` with a hook payload on stdin, or its decide function where the hook
  only wraps it), offline. It includes every behaviour difference of the map's section 9 that the
  phase-1 tasks touch, so each flip shows as a changed expected value in a reviewed diff.
- **Cache**: a bounded `functools.lru_cache` (or a dict keyed by `(text, variant)`) around the
  `_Lexer` token stream, where the variant is the full set of lexer options (substitutions,
  comments, and the without_data / code_text / quoted_heredoc_spans mode). Results are immutable or
  copied before a caller can change them. No cache across processes.
- **Brace expander**: prod's algorithm (lists, nested lists, numeric and letter ranges, steps, zero
  padding), moved to `shell_words.brace_words(word) -> list[str] | None`, `None` meaning "cannot
  tell" (over the member cap or the brace cap). It keeps the conservative over-expansions of both
  copies: braces inside quotes or after a backslash are expanded, and a brace list after `$` is
  expanded too (opencode's rule; prod gains it). prod and opencode both refuse on `None`; opencode's
  silent partial expansion goes away. Cap: prod's 64 members.
- **SHELL_READERS**: the union (prod's 21 names, which include all of opencode's 12) in
  wrapper_commands; both guards import it.
- **Wrapper table**: wrapper_commands gains a `WrapperSyntax` table per wrapper name: valued options
  (short and long), flags, operands before the command, assignments. Every valued option is checked
  against the program's real syntax (man page of the version on this machine, or upstream source);
  an option that is valued in one copy and a flag in reality is a flag. Name sets are the union of the
  three copies. prod keeps its generic `_Due` head walk for the start-claude check (it is stricter
  than any table, map 9m); the table replaces prod's `_wrapper_end`, opencode's `OPTION_SYNTAX` and
  pm's `WRAPPER_SYNTAX`/`PARALLEL_SYNTAX`/`WRAPPERS`/`EXEC_WRAPPERS`. pm's "not walked" wrappers
  stay whole-word checked; a name moves to "walked" only with its full table.

## Non-goals (phase 2)

pm's `_Scanner` and its approval-path shlex; opencode's `Argv` and `_crude` fallback; prod's regex
splitters; one Moves analysis; capability tables per program; the comments setting; one hook entry.

## Constraints

- The guards' own tests stay green unchanged, except expected values the plan lists as tightening.
- Linear time: no step may make a walk super-linear (prod's 20 s deadline, the gate's 2 s).
- Fail closed: an exception in shared code is a refusal in each guard, as today.
- No company LiteLLM configuration, no personal paths or user names in any file.
- CHANGELOG under `## Unreleased`.
