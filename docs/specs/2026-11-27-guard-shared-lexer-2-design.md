# Guards: a shared lexing layer, phase 2

Status: task opencode-m2/6b, after phase 1 (#47, merged as c202ca4). Phase 1's spec is
`docs/specs/2026-11-26-guard-shared-lexer-design.md`; its direction rule holds here unchanged.

## Why

Phase 1 gave the guards one lex cache, one brace expander, one `SHELL_READERS` and one wrapper
table. Each guard still has its own idea of which commands read their input as commands, its own
shell tree, and fallbacks that read text the lexer gives up on with a cruder split. Four holes
that were there before phase 1 are still open (`docs/known-gaps.md`): each is a command a guard
lets through where the same text through `sh` is refused.

## Goals

1. Close the four open holes:
   - prod_guard: a claude start piped into `sudo -s`, `su -s`, `doas -s`, `script`, `screen -`,
     `make -f -` or `eval` is refused, as it is piped into `sh`.
   - pm_permissions guard mode: a push piped into `doas`, `script` or `screen` is refused, as it is
     piped into `sudo`. The approval path already refuses it.
   - deny_secrets: a secret file name built by braces (`cat .{en,x}v`) is refused; a brace word it
     cannot expand (`shell_words.brace_words` gives None) is refused.
   - opencode_server_guard: a body piped into `sudo`, `doas` or `parallel` whose options leave no
     command (`echo 'opencode db' | sudo -s`) is read as shell text.
2. One "reads its input as commands" test, in `wrapper_commands`: given a command's words (after
   the shared wrapper walk), does it run what it reads on stdin as shell text? prod, pm and opencode
   call it for the pipe rule. Each guard keeps its own verdict logic around it.
3. One shell tree for the guard paths: pm's guard-mode `_Scanner` and prod's regex splitters
   (`SHELL_SPLIT`, `COMMAND_SPLIT`, `COMMAND_PARTS`, `CD_WORD`) read shell_words' commands instead,
   where a differential shows no verdict moves toward allow. A splitter that catches something the
   lexer path does not stays, named in the PR.
4. opencode's `_crude` fallback: the texts it reads are the ones shell_words gives up on (process
   substitution, `$'..'`, functions, unclosed quotes and heredocs). Slice 3 measures which of them
   opencode allows today and either reads them with shell_words, refuses them, or keeps `_crude`,
   whichever the differential shows moves nothing toward allow at the fewest new refusals. Its plan
   records the choice.

## Non-goals, ruled

- pm's approval path (`_split`, `_tokens`, `_segments`, `FORBIDDEN`) stays as it is. It is a
  whitelist: anything with a quote, `$`, `#`, `<`, a glob or a brace is refused before shlex runs.
  Reading it with shell_words would let it approve texts it refuses today, which the direction rule
  forbids.
- One hook entry. PreToolUse cannot allow; only PermissionRequest does. One entry for the three
  PreToolUse Bash guards would share one timeout, and a hook still running at its timeout lets the
  call through: deny (30 s), pm (15 s) and prod (30 s, a 2 s gate plus a 20 s guard) do not fit one
  budget. Each guard's `fail-closed.sh` fallback also differs on purpose. The entries stay separate.
- Capability tables per program: prod's `READ_COMMANDS`, opencode's `NEVER_READS_CONTENT` and pm's
  git tables answer different questions. Only name sets with the same meaning are shared (goal 2).
- The comments setting: `without_data` keeps `comments=False` so deny_secrets also reads comment
  text. Changing it would loosen deny_secrets.
- prod's narrow `UNWRAPPED_OPTIONS` stays; it does not read past options that change where a command
  runs.
- One Moves analysis across guards: the guards track different things (prod writes and targets,
  opencode cd only, pm one cd). A shared one is left for later; goal 3 shares the tree it reads from.

## The direction rule

As in phase 1: no verdict moves toward allow, in the corpus, the guards' own tests or a generated
differential against main. A move toward refuse is listed in the PR with the command, the guard and
the old and new verdicts.

## Slices

Each slice is its own PR against main, merged before the next starts, and each has a differential
against main and a final review. The PR that touches pm's guard path (slice 3) has its own final
review of that path.

1. **Holes** (goal 1). Lowest risk: each is a local change in one guard plus corpus flips.
2. **One reads-its-input test** (goal 2), with the four holes' rules moved onto it.
3. **One tree for the guard paths** (goals 3 and 4).

## Design notes for slice 1

- prod `_runs_stdin` (prod_guard.py ~1342): the head name test accepts the readers that run stdin
  as shell text after their options: `sudo`/`doas` with `-s` or `-i` and no command, `su` with a
  shell option or no command, `script` with no command, `screen` with `-` or no command, `make` only
  with `-f -` (pm's `_make_reads_its_makefile` rule), and `eval` (over-strict: `eval` reads no stdin,
  but `| eval "$(cat)"` does; refusing `| eval` is the stricter side).
- pm `_reads_commands` (pm_permissions.py ~2003): a wrapper in `EXEC_WRAPPERS` that is not walked and
  receives a pipe from a guarded command reads its input as commands unless its words name a command
  that does not (the stricter side: unknown means refuse in guard mode).
- deny_secrets `text_hit` (~379): for the command kind, each word that holds a brace is also read as
  its `brace_words` expansions, with the same patterns and short-name test; None is a refusal with a
  message that names braces.
- opencode `check_command` (~1333): when the walk leaves no command and the text is piped, a prefix
  word that is a shell reader or a walked wrapper that runs a shell on stdin (`sudo -s`, `doas -s`,
  `parallel` with no command) makes the piped body shell text; `_bodies` gets the same rule.

## Design notes for slice 2

Slice 2 also takes the duplicates the slice 1 review named: the screen option table, the
command-string test and prod's own option walker.

- One option walker in `wrapper_commands`: pm's `_option_words` / `_wrapped_start` (it already
  reads numeric options such as `nice -5`). An option a table does not list ends the walk with no
  command (the stricter side, as prod and pm read it today). pm's `_unwrapped` and prod's
  `_has_command` call it; opencode's own walk (`_skip_options`, which reads an unlisted option as a
  flag) is slice 3's tree work and stays.
- One table of the readers' options in `wrapper_commands`, beside `WRAPPER_SYNTAX`: sudo with its
  shell options as flags, doas with `-L -n -s`, screen's letters. Each name has one entry; prod's
  `STDIN_READER_OPTIONS`, pm's `SCREEN_SYNTAX` and the duplicated `STDIN_PATHS`,
  `MAKE_GLUED_FILE` and parallel-source sets go.
- `runs_stdin_as_commands(words)` in `wrapper_commands`: `words[0]` is the receiver after the
  guard's own walk, the rest its words. True for a shell that reads stdin (prod's
  `_shell_reads_stdin`, unknown means True), `eval`, `make` with `-f -`, `su`, `runuser` and
  `script` with no command string or one whose first word is a shell, sudo, doas and screen with no
  command after their options, and `parallel` with no command. False for any other name: each guard
  keeps its own policy for names the test does not know (pm's untabled-wrapper rule and its
  recursion, prod's bare `parallel`, opencode's refusal of a piped command named in
  `SHELL_READERS` whatever its options).
- Each guard's pipe rule asks the shared test; where a guard's own rule is broader, it stays beside
  the test as an extra refusal, named in the PR. A move toward allow in any guard is a bug in the
  shared test, which is then made stricter.
- pm's damaged-hooks stub defines every new name the module reads at load, and a missing or broken
  `wrapper_commands` still refuses in all three guards.

## Constraints

- The guards' own tests stay green unchanged, except expected values listed as tightening.
- Linear time; prod's 20 s deadline and 2 s gate, opencode's 5 s plugin timeout.
- Fail closed: an exception in shared code is a refusal in each guard.
- No company LiteLLM configuration, no personal paths or user names in any file.
- CHANGELOG under `## Unreleased`; `docs/known-gaps.md` updated as holes close.
