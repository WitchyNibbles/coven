# Guards phase 2, slice 2: one reads-its-input test — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use coven:subagent-driven-development to implement
> this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** prod, pm and opencode ask one test in `wrapper_commands` whether a receiver runs its stdin
as shell text, read with one option walker and one table of the readers' options; the duplicated
tables and walkers go.

**Architecture:** the test and the walker move into `hooks/wrapper_commands.py` first, with their own
tests and no guard change. Then each guard moves onto them in its own task, pinned by the
cross-guard corpus and a differential against main. Guard-specific policies stay in each guard.

**Tech Stack:** Python 3 stdlib hooks, pytest, `.coven/check.py`.

**Spec:** docs/specs/2026-11-27-guard-shared-lexer-2-design.md (goal 2, "Design notes for slice 2").

## Global Constraints

- No verdict moves toward allow, in the corpus, the guards' own tests, or a differential against
  main (1e124b6). Every move toward refuse is listed in the workspace's `flips.md` (command, guard,
  old verdict, new verdict) for the PR table.
- pm's approval path (`_split`, `_tokens`, `_segments`, `_plan`, `FORBIDDEN`) does not change: the
  differential shows 0 changes there.
- A corpus verdict is read from a run, never written by hand.
- Linear time; prod 20 s deadline and 2 s gate, pm 10 s deadline, opencode 5 s plugin timeout.
- Fail closed: a missing or damaged `wrapper_commands` refuses in all three guards; pm's
  damaged-hooks stub defines every name pm reads at load.
- No suppressions. No company LiteLLM configuration, no personal paths or user names.
- Tests that call `pm._unwrapped`, `pm.PARALLEL_SYNTAX`, `pm._parallel_runs_its_input`,
  `prod._wrapper_end` or `opencode._unwrap` keep passing; a name that moves keeps an alias in its
  guard only while a test needs it, and the task says so.
- prod's `UNWRAPPED_OPTIONS` and `XARGS_*` tables stay (ruled non-goals: different meanings).
- CHANGELOG under `## Unreleased`.

## Review Focus

1. A receiver with a command after its options keeps today's verdict in every guard:
   `| sudo -u me ls`, `| doas -u x cat`, `| screen -dmS x ls` in prod and pm, `| nice -n 5 cat`.
2. `| doas -n ls`: prod reads it as pm does today (no command: an unlisted option ends the walk).
3. `| nice -5 sh` and `| nice -5 cat` in prod: the numeric option is read, the verdict follows the
   command after it.
4. `| script -c sh`, `| script -qc sh /dev/null`, `| su -c sh`, `| runuser x`: refused in prod and
   pm when the body is guarded (a command string whose first word is a shell, or no command).
5. opencode keeps refusing a piped command named in `SHELL_READERS` whatever its options
   (`| su -c ls`, `| make`), and newly reads `| doas -n ls` as no command.

---

### Task 1: Corpus tag and pins for slice 2

**Files:** Modify `tests/hooks/guard_corpus.py`, `tests/hooks/test_guard_corpus.py` (docstring).

**Interfaces:** Produces flip tag `"s2"` in `FLIPS` ("slice 2 of phase 2: one reads-its-input
test"), used by Tasks 3-5.

- [ ] Step 1: add `"s2"` to `FLIPS`; describe it in both docstrings.
- [ ] Step 2: add entries, verdicts read from a run of today's guards, for every row of the map's
  disagreement list with each guard's guarded tail (a push for pm, a claude start for prod,
  `opencode db` for opencode): `doas -n ls`, `doas -u root -n ls`, `sudo -h h ls`,
  `su --session-command ls`, `su -c ls`, `bash -c ls`, `script -qc ls /dev/null`,
  `screen -dmS x ls`, `make`, `make -C x`, `parallel -j2 echo`, `nice -5 sh`, `nice -5 cat`, plus the
  Review Focus lines. Tag `"s2"` where the plan's rules move a slot; `"none"` otherwise.
- [ ] Step 3: re-tag `gap-` entries this slice closes (`script -c sh` family, `runuser x`) `"s2"`.
- [ ] Step 4: `python3 .coven/check.py fast tests/hooks/test_guard_corpus.py`; commit
  `test(hooks): the corpus pins slice 2's readers as they are today`.

### Task 2: The shared walker, table and test in wrapper_commands

**Files:** Modify `hooks/wrapper_commands.py`, `hooks/pm_permissions.py` (stub only). Test:
`tests/hooks/test_wrapper_commands.py`.

**Interfaces:** Produces, in `wrapper_commands`:
- `option_words(syntax: WrapperSyntax, words: Sequence[str], start: int) -> int | None`: index of
  the first word after the options, or None when an option is not in the table (moved from pm's
  `_option_words` / `_wrapped_start`, numeric options included).
- `READER_SYNTAX: Mapping[str, WrapperSyntax]`: sudo (its table plus `-s -i --shell --login` as
  flags), doas (its `WRAPPER_SYNTAX` entry as it is: `-u -C` valued, no flags, so `doas -n ls` is
  no command, Review Focus 2), screen (pm's and prod's letters, identical). Where a reader's option
  is a flag but a guard reads it as no command today, the table follows the stricter guard.
  `READER_SYNTAX` serves only `runs_stdin_as_commands`; pm's and prod's walks keep their own
  tables.
- A shell given `-c` whose string's first word is a shell reads stdin (`| sh -c sh`), as a
  command string does for su, runuser and script.
- `STDIN_PATHS`, `MAKE_GLUED_FILE`, `INPUT_SOURCES`, `COMMAND_STRING_OPTIONS` (`-c`, `--command`,
  `--session-command`, glued `-<flags>c`).
- `runs_stdin_as_commands(words: Sequence[str]) -> bool`, as the spec's design note for slice 2.

- [ ] Step 1: failing tests, one per rule and per Review Focus line (true and false cases), plus:
  an unlisted option is no command; `nice -5 sh` reads `sh`; an empty word list is False.
- [ ] Step 2: implement; no guard calls them yet. pm's stub gains every new name with a
  refusing default.
- [ ] Step 3: a test that pm, loaded with a `wrapper_commands` missing the new names, refuses
  through its stub. (prod and opencode read the names only from Tasks 3 and 5, which add the same
  test for their guard.)
- [ ] Step 4: fast check on `tests/hooks/test_wrapper_commands.py tests/hooks/test_hook_support.py`;
  commit `feat(hooks): one reads-its-input test and option walker in wrapper_commands`.

### Task 3: prod onto the shared test

**Files:** Modify `hooks/prod_guard.py` (`_runs_stdin_as_commands` ~1438, `_has_command` ~1383,
`_option_width` ~1367, `_has_command_string` ~1415, `_make_reads_stdin_file` ~1424,
`_shell_reads_stdin` ~1336, tables 335-358). Test: `tests/hooks/test_prod_guard.py`, corpus.

- [ ] Step 1: failing tests: Review Focus 2, 3 and 4 for prod; prod loaded with a
  `wrapper_commands` missing the new names refuses.
- [ ] Step 2: `_runs_stdin` asks `wrapper_commands.runs_stdin_as_commands`; prod's bare-`parallel`
  rule stays beside it. Remove the local walker and tables the shared ones replace.
- [ ] Step 3: differential of prod verdicts, main against the tree; corpus prod slot; flips.md;
  fast check; commit `refactor(hooks): prod asks the shared reads-its-input test`.

### Task 4: pm onto the shared test

**Files:** Modify `hooks/pm_permissions.py` (`_option_words` / `_wrapped_start` ~1840-1878,
`_reads_commands` ~2007, `_has_command_string` ~2037, `_make_reads_its_makefile` ~2088,
`SCREEN_SYNTAX`, `COMMAND_STRING_ONLY`, `STDIN_PATHS`, `MAKE_GLUED_FILE`, `PARALLEL_SOURCES`).
Test: `tests/hooks/test_pm_permissions.py`, corpus.

- [ ] Step 1: failing tests: Review Focus 1 and 4 for pm; the approval path's verdict on the same
  texts is unchanged (a test asserts it).
- [ ] Step 2: `_unwrapped` uses the shared walker; `_reads_commands` asks
  `runs_stdin_as_commands` for the names it knows; pm's untabled-wrapper rule (`UNKNOWN_COMMAND`),
  its `find` and inner-command recursion stay.
- [ ] Step 3: differential (guard mode and approval path) main against the tree; corpus pm slot;
  flips.md; fast check; commit `refactor(hooks): pm asks the shared reads-its-input test`.

### Task 5: opencode onto the shared test

**Files:** Modify `hooks/opencode_server_guard.py` (`_runs_stdin_as_shell` ~1018, `_bodies`
~1025, `_check_piped_without_command` ~1333). Test: `tests/hooks/test_opencode_server_guard.py`,
corpus.

- [ ] Step 1: failing tests: Review Focus 5; opencode loaded with a `wrapper_commands` missing the
  new names refuses.
- [ ] Step 2: `_runs_stdin_as_shell` asks `runs_stdin_as_commands` on the piped command's words as
  written (before opencode's own walk); the name rule in `check_command` stays.
- [ ] Step 3: differential main against the tree; corpus opencode slot; flips.md; fast check; commit
  `refactor(hooks): opencode asks the shared reads-its-input test`.

### Task 6: Docs and the slice differential

**Files:** `CHANGELOG.md`, `docs/known-gaps.md`.

- [ ] Step 1: CHANGELOG under `## Unreleased`: one line for the shared test, one per new refusal
  class, "No command that was refused before is allowed now" only if the differential shows 0.
- [ ] Step 2: known-gaps: drop the holes this slice closed (re-probed), keep slice 3's items.
- [ ] Step 3: differential against a `git archive` of 1e124b6, all four guards and pm's approval
  path, corpus plus generated lines (each reader with 0-2 options from its table and one unlisted
  option, a numeric option, the three guarded tails, piped, here-string and heredoc forms); counts
  per guard in flips.md; `flip-table.md` per corpus entry.
- [ ] Step 4: `python3 .coven/check.py full --base 1e124b6` exit 0; commit
  `docs(hooks): slice 2 of phase 2, one reads-its-input test`.
