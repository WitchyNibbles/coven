# Guards phase 2, slice 1: close the four open holes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use coven:subagent-driven-development to implement
> this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** the four holes in `docs/known-gaps.md` ("Holes that were there before phase 1") are
closed, each a move toward refuse only.

**Architecture:** one local change per guard, each pinned by the cross-guard corpus
(`tests/hooks/guard_corpus.py`) and the guard's own tests. Slices 2 and 3 of the spec come later,
in their own plans.

**Tech Stack:** Python 3 stdlib hooks, pytest, `.coven/check.py`.

**Spec:** docs/specs/2026-11-27-guard-shared-lexer-2-design.md (goal 1, "Design notes for slice 1").

## Global Constraints

- No verdict moves toward allow, in the corpus, the guards' own tests, or a differential against
  main (c202ca4). Every move toward refuse is listed in the workspace's `flips.md` (command, guard,
  old verdict, new verdict) for the PR table.
- A corpus verdict is read from a run, never written by hand.
- Linear time. Fail closed: an exception is a refusal.
- No suppressions. No company LiteLLM configuration, no personal paths or user names.
- pm's approval path (`_split`, `_tokens`, `_segments`, `FORBIDDEN`) is not touched.
- CHANGELOG under `## Unreleased`.

## Review Focus

1. A wrapper with a command after its options (`sudo -s ls`, `doas -u x cat`, `script -c cmd`,
   `screen -dmS x cmd`) is judged on that command as today, and is not read as a stdin shell.
2. `make` without `-f -` piped into is not read as shell text by prod (`echo x | make -s`).
3. A brace word in deny_secrets that `brace_words` cannot expand is refused with a message naming
   braces, and a plain brace word with no secret name is still allowed (`cat {a,b}.txt`).
4. opencode: `echo x | sudo -u me ls` stays judged on `ls`; only an empty command after the walk
   with a piped body takes the new rule.
5. pm guard mode: a push piped into a walked wrapper with a command (`... | nice -n 5 cat`) keeps
   today's verdict.

---

### Task 1: Corpus tag and pins for slice 1

**Files:**
- Modify: `tests/hooks/guard_corpus.py`, `tests/hooks/test_guard_corpus.py` (docstring only)

**Interfaces:**
- Produces: flip tag `"h"` in `FLIPS` ("slice 1 of phase 2: the four open holes"), used by Tasks 2-5.

- [ ] Step 1: add `"h"` to `FLIPS` and describe it in both docstrings.
- [ ] Step 2: tag `"h"` on every entry a hole moves: `readers-sudo-*`, `readers-doas-*`,
  `readers-parallel-opencode`, `readers-parallel-push` (pm slot only if it moves), the prod slot of
  `readers-{eval,make,su,script,screen}-claude`, `b-list-split-env`, `9j-quoted-literal-env-name`.
- [ ] Step 3: add entries, verdicts read from a run of today's guards: a push piped into
  `doas -s`, `script -q /dev/null`, `screen -`; a claude start piped into `sudo -i`, `su` (no
  options), `screen` (no options), `eval "$(cat)"`; the Review Focus lines 1, 2, 4 and 5; deny lines
  `cat .{en,x}v`, `cat ~/.s{s,x}h/id_rsa`, `cat {a,b}.txt`, and a brace word past 64 words that makes
  `.env`.
- [ ] Step 4: `python3 .coven/check.py fast tests/hooks/test_guard_corpus.py` passes; commit
  `test(hooks): the corpus pins slice 1's holes as they are today`.

### Task 2: opencode reads a body piped into a wrapper that leaves no command

**Files:** Modify `hooks/opencode_server_guard.py` (`check_command` ~1333, `_bodies` ~1028).
Test: `tests/hooks/test_opencode_server_guard.py`, corpus.

- [ ] Step 1: failing tests: `echo 'opencode db' | sudo -s`, `| doas -s`, `| parallel` are refused;
  `echo 'opencode db' | sudo -u me ls` keeps today's verdict; the same body through a here-string
  (`sudo -s <<< 'opencode db'`) is refused.
- [ ] Step 2: when the walk leaves no command and the command is piped or has inputs, a prefix word
  whose base name is a shell reader or a walked wrapper (`sudo`, `doas`, `parallel`, `su`) makes the
  piped text a body to check as shell text; refuse if the body cannot be told.
- [ ] Step 3: corpus expected values for the opencode slot; flips.md rows; fast check; commit
  `fix(hooks): opencode reads a body piped into sudo, doas or parallel`.

### Task 3: pm guard mode refuses a push piped into an unwalked exec wrapper

**Files:** Modify `hooks/pm_permissions.py` (`_reads_commands` ~2003). Test:
`tests/hooks/test_pm_permissions.py`, corpus.

- [ ] Step 1: failing tests (guard mode): a push piped into `doas -s`, `script -q /dev/null`,
  `screen -` is refused; Review Focus line 5 keeps its verdict; the approval path's verdict on the
  same texts is unchanged (a test asserts it).
- [ ] Step 2: a receiver in `EXEC_WRAPPERS` that is not in `WALKED` reads its input as commands,
  unless its words carry a command after its options that is not a shell reader (then today's rule).
- [ ] Step 3: corpus pm slot; flips.md rows; fast check; commit
  `fix(hooks): pm guard refuses a push piped into doas, script or screen`.

### Task 4: deny_secrets reads brace words as their expansions

**Files:** Modify `hooks/deny_secrets.py` (`text_hit` ~379). Test: `tests/hooks/test_deny_secrets.py`,
corpus.

- [ ] Step 1: failing tests: `cat .{en,x}v`, `cat .e{n,x}v`, `cat '.e{n,m}v'`,
  `cat ~/.s{s,x}h/id_rsa` refused; `cat {a,b}.txt` allowed; a word past 64 words refused with a
  message naming braces.
- [ ] Step 2: for the command kind, each word holding `{` is also read as
  `shell_words.brace_words(word)`; each expansion goes through the same patterns and short-name test;
  None refuses. Load shell_words the way deny_secrets loads its siblings, so a missing module fails
  closed.
- [ ] Step 3: corpus deny slot; flips.md rows; fast check; commit
  `fix(hooks): deny-secrets reads brace words as the names they make`.

### Task 5: prod refuses a claude start piped into a reader that runs stdin

**Files:** Modify `hooks/prod_guard.py` (`_runs_stdin` ~1342, `_shell_reads_stdin` ~1299). Test:
`tests/hooks/test_prod_guard.py`, corpus.

- [ ] Step 1: failing tests: a claude start piped into `sudo -s`, `sudo -i`, `su -s /bin/sh me`,
  `su`, `doas -s`, `script -q /dev/null`, `screen -`, `screen`, `make -f -`, `eval`,
  `eval "$(cat)"` is refused; Review Focus lines 1 and 2 keep their verdicts.
- [ ] Step 2: the head test accepts these readers when their options leave no command (sudo/doas:
  `-s`, `-i` or nothing after options; su: no command, or `-s`; script: no command; screen: `-` or
  nothing; make: `-f -` only, as pm's `_make_reads_its_makefile`; eval: always), then
  `_feeds_shell` decides as for `sh`.
- [ ] Step 3: corpus prod slot; flips.md rows; fast check; commit
  `fix(hooks): prod refuses a claude start piped into sudo, su, doas, script, screen, make or eval`.

### Task 6: Docs and the slice differential

**Files:** `CHANGELOG.md`, `docs/known-gaps.md`.

- [ ] Step 1: CHANGELOG entry under `## Unreleased`: the four holes closed, one line each, plus
  "No command that was refused before is allowed now" only if the differential shows 0.
- [ ] Step 2: known-gaps: remove the closed holes; keep the phase 2 items with slices 2 and 3 named.
- [ ] Step 3: differential against a `git archive` of c202ca4: the corpus plus generated lines
  (every hole's reader with 0-2 options, each of the three guarded tails, piped, here-string and
  heredoc forms; brace words around `.env`, `.ssh`, `id_rsa`). Counts per guard in flips.md.
- [ ] Step 4: `python3 .coven/check.py full --base c202ca4` exit 0; commit
  `docs(hooks): slice 1 of phase 2 closes the four open holes`.
