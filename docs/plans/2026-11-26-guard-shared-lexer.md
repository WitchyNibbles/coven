# Guards shared lexing layer, phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use coven:subagent-driven-development to implement this
> plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** the Python guards share one parse cache, one brace expander, one `SHELL_READERS` and one
wrapper table, with a cross-guard corpus proving no verdict moved toward allow.

**Architecture:** shared knowledge lives in `hooks/shell_words.py` (lexing, braces) and
`hooks/wrapper_commands.py` (names and option tables); the guards import it and drop their copies.

**Tech Stack:** Python 3 stdlib, pytest through `.coven/check.py`.

**Spec:** docs/specs/2026-11-26-guard-shared-lexer-design.md

## Global Constraints

- No verdict flips from refuse to allow, in the corpus or in any guard test. A flip from allow to
  refuse is allowed only when the task lists it, and each one goes in `flips.md` in the plan workspace
  (command, guard, old verdict, new verdict, why) for the PR table.
- The corpus lands in its own commit (Task 1) with today's verdicts. Later commits change an expected
  value only for a listed flip.
- The parse cache never crosses hook processes or calls: key it on the exact command text plus the
  full variant; a different text never reuses an entry.
- Linear time; fail closed (an exception in shared code is still a refusal in every guard).
- Guard tests that change an expected value say so in the commit message and in the report.
- No company LiteLLM configuration, no personal paths or user names in any file.

## Review Focus

1. A command the corpus marks `refuse` for some guard, after the change: still `refuse`.
2. Two different commands with the same tokens after lexing (quoting, spacing) in one process: each
   gets its own cache entry and its own verdict.
3. A brace word past the member cap in opencode's guard: refused, not partly expanded.
4. A wrapper option taken as valued by the new table that is a flag in the real program: the word
   after it must still be judged as the command.
5. A wrapper newly known to pm (`npx`, `bunx`, `tmux`, `screen`) with a guarded git word inside:
   judged, not skipped.

---

### Task 1: The cross-guard corpus

**Files:** Create `tests/hooks/guard_corpus.py` (data), `tests/hooks/test_guard_corpus.py`.

- [ ] Step 1: a harness that runs one command through deny_secrets, pm_permissions (guard mode, as a PM
  session), prod_guard (through the gate with a scratch guard config and store) and
  opencode_server_guard (with a scratch profile, serve folder with a password file, coven root), each
  the way its hook runs it, offline, and returns `allow`/`refuse` per guard. Reuse the fixtures of the
  four guards' own test files; do not copy them.
- [ ] Step 2: entries. At least: every behaviour difference of the map's section 9 (a-m) as concrete
  commands; for each of brace lists, ranges, steps, zero padding, the cap, `${a,b}`, quoted braces;
  each `SHELL_READERS` name with a piped body; each wrapper name with a valued option before the
  command and a guarded word after it; plain allowed controls. Expected values are today's verdicts,
  read from a run, not guessed.
- [ ] Step 3: all green on the current code; fast check; commit
  `test(hooks): a cross-guard corpus of today's verdicts`.

### Task 2: One parse per variant

**Files:** Modify `hooks/shell_words.py`; Test `tests/hooks/test_shell_words.py`.

- [ ] Step 1: tests: one public call per variant on the same text lexes once (count `_Lexer.tokenize`
  calls); two texts that differ only in quoting or spacing get two entries and different results; a
  caller that changes a returned list does not change the next result; the variants
  (substitutions, comments, the without_data / code_text / quoted_heredoc_spans modes) never share an
  entry.
- [ ] Step 2: cache the token stream by `(text, variant)` (bounded, e.g. 256 entries), returning
  immutable or copied results; corpus and all guard tests green; fast check; commit
  `perf(hooks): shell_words lexes one text once per variant`.

### Task 3: One brace expander

**Files:** Modify `hooks/shell_words.py`, `hooks/prod_guard.py` (remove 3575-3690 brace code),
`hooks/opencode_server_guard.py` (remove `BRACES`, `MAX_BRACE_WORDS`, `_braces`); Tests in
`tests/hooks/test_shell_words.py`, corpus.

**Interfaces:** Produces `shell_words.brace_words(word: str) -> list[str] | None` (`None`: cannot
tell; callers refuse) and `shell_words.BRACE_MEMBERS = 64`.

- [ ] Step 1: tests of `brace_words` against bash for lists, nested lists, numeric and letter ranges,
  steps, zero padding, `{a}`, `{}`; the conservative cases expand (`"{a,b}"`, `\{a,b\}`, `${a,b}`);
  past 64 members or 64 braces it returns `None`.
- [ ] Step 2: move prod's algorithm, add the `$`-brace expansion; prod and opencode call it; opencode
  refuses on `None` where it now keeps words literal. Listed flips: opencode refuses commands whose
  brace range, step or padding names a protected path, and any brace word past the cap; prod refuses a
  `${a,b}` word that names a protected path. Record each in `flips.md`.
- [ ] Step 3: corpus (listed flips only) and guard tests green; fast check; commit.

### Task 4: One SHELL_READERS

**Files:** Modify `hooks/wrapper_commands.py`, `hooks/prod_guard.py:220-226`,
`hooks/opencode_server_guard.py:627-630`.

- [ ] Step 1: test pinning `wrapper_commands.SHELL_READERS` (prod's 21 names) and that both guards use
  it (identity, not a copy).
- [ ] Step 2: move it; listed flips: opencode judges a body piped or fed to `eval parallel make su sudo
  doas script screen tmux` as shell text. Record them.
- [ ] Step 3: corpus and guard tests green; fast check; commit.

### Task 5: One wrapper table

**Files:** Modify `hooks/wrapper_commands.py`, `hooks/opencode_server_guard.py` (`OPTION_SYNTAX`,
`Wrapper`, `_wrapper`), `hooks/pm_permissions.py` (`WRAPPER_SYNTAX`, `PARALLEL_SYNTAX`, `WRAPPERS`,
`EXEC_WRAPPERS`, `SHELLS`), `hooks/prod_guard.py` (`_wrapper_end` and its constants only; the `_Due`
walk stays).

**Interfaces:** Produces `wrapper_commands.WrapperSyntax` (flags, valued, operands, assignments,
numeric) and `wrapper_commands.WRAPPER_SYNTAX: dict[str, WrapperSyntax]`; `WALKED` (names with a full
table) and the existing name sets, extended to the union (adds `make`, `trap`, `pnpx`, `mksh`,
`nsenter`, `chroot`, `nocache`, `faketime`, `firejail`, `sg`, `numactl`, `cgexec`, `proxychains`,
`proxychains4`, `torsocks`, `pkexec`, `ssh` where a copy has them).

- [ ] Step 1: tests: each table entry matches the real program (cite the man page or source in a
  comment per entry); an option valued in one old copy but a flag in reality is a flag; the word after
  a flag is still the command; a name in no old walk stays whole-word checked.
- [ ] Step 2: build the table from the union, checked option by option; switch the three guards; listed
  flips: pm judges `npx bunx tmux screen pnpx` and the other new names as wrappers; opencode reads
  `sudo` and `xargs` options it did not know. Record them.
- [ ] Step 3: corpus and guard tests green; fast check; commit.

### Task 6: Docs and the flip table

**Files:** `CHANGELOG.md` (under `## Unreleased`), `docs/known-gaps.md` (phase 2 items), the
README/guard docs only where they describe a changed verdict.

- [ ] Step 1: write them; `humanizer` on the prose; copy `flips.md` into the PR body later.
- [ ] Step 2: `python3 .coven/check.py full --base "$(git merge-base HEAD main)"` → `CHECK full exit=0`.
- [ ] Step 3: commit `docs(hooks): the shared lexing layer`.

## Estimate (agent wall-clock)

| Task | h |
|---|---|
| 1 corpus | 1.5 |
| 2 cache | 0.5 |
| 3 braces | 1 |
| 4 SHELL_READERS | 0.5 |
| 5 wrapper table | 1.5 |
| 6 docs, full check, final review | 0.5-1 |
| **Total** | **5.5-6 h** |
