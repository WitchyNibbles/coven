# aquelarre ships with coven: implementation plan

> **For agentic workers:** run this plan with coven:subagent-driven-development, one task at a
> time, test first. Steps use checkbox (`- [ ]`) syntax.

**Goal:** coven's marketplace ships the aquelarre mod as a second plugin that coven depends on,
checked by the code standard and tested in CI.

**Architecture:** the check standard learns a third project kind, a Claude Code mod, whose stages
run `claude plugin validate --strict` and `claude plugin test`. The mod moves into
`plugins/aquelarre/` in one scrubbed commit, gains `userConfig` settings (language, band, toasts),
and coven's manifest, doctor, smoke job, workflow and docs take it in.

**Tech stack:** Python 3.12 stdlib (check standard, coven CLI), pytest; TypeScript Claude Code
hooks module tested by `claude plugin test` (kit `claude-code/testing`).

**Spec:** `docs/specs/2026-11-21-aquelarre-in-coven-design.md` (read it first).

## Global constraints

- coven is public. No private toolkit name, no machine-specific path, no personal identifier in
  any tracked file or commit message. Fixture homes use `/home/user`. Commit author is the repo's
  configured identity (do not set another).
- No suppressions and no weakened rules anywhere (`.coven/` repo). No `--no-verify`.
- Every outside service a test reaches is faked: `claude` in the standard's tests is a fake script
  on `PATH` (except the tests marked `claude`, below); the mod's tests already fake
  `$.process.run`, env and clock through the test kit.
- Claude Code pin for CI: `2.1.325` (`LAST_TESTED` in `src/coven/claude_version.py`); tests keep
  every pin equal to it.
- aquelarre's version equals coven's (`0.1.0`).
- Commit messages: plain, no `Co-Authored-By`, no "Generated with".
- Fast check during work: `python3 .coven/check.py fast <tests>`; the mod alone:
  `claude plugin test plugins/aquelarre` and `claude plugin validate --strict plugins/aquelarre`.

## Review focus

1. A repo whose only project is a mod (no `pyproject.toml`, no `package.json`): the check finds a
   project and runs (Task 1 test `test_a_repo_with_only_a_mod_is_checked`).
2. coven's own root (`.claude-plugin/plugin.json` + `hooks/hooks.json` without `modules`) is not a
   mod (Task 1 test `test_command_hooks_alone_are_not_a_mod`).
3. English text in a narrow dock: the cut order still holds with shorter words (Task 3 test
   `English cards keep the narrow cut order`).
4. Band switched off while a PM waits: no row above the prompt, and the toasts still follow their
   own switch (Task 3 tests).
5. `coven` exits non-zero or is missing with the fallback gone: the read-error band, last cards
   kept (Task 2 test `when coven is missing the error is shown`).

---

### Task 1: The check standard learns Claude Code mods

**Files:**
- Create: `standard/check_mod.py` (mod stages), and its installed copy `.coven/check_mod.py`
- Modify: `standard/check_repo.py` (`Project.kind`, `discover`, `outside_problems`,
  `STANDARD_FILES`), `standard/check.py` (dispatch, setup problem, install, messages),
  `STANDARD_VERSION` in every standard module (5 -> 6), `src/coven/project.py:38` (files it
  installs), and the `.coven/` copies of every changed module (coven checks itself with them)
- Test: `tests/standard/test_check_mod.py`; adjust the file lists in
  `tests/standard/test_check_layout.py` only where they enumerate the standard's modules
- Modify: `pyproject.toml` markers: add `claude: needs Claude Code (claude) on PATH; fails, never
  skips, when it is missing`

**Interfaces:**
- Produces: `check_repo.Project.kind: Literal["python", "typescript", "mod"]` (`manager` is
  `None` for a mod); `check_repo.is_mod(repo: Path, directory: Path, files: set[Path]) -> bool`;
  `check_mod.mod_stages(project: check_repo.Project, run: check_stages.Run) -> check_stages.Stages`;
  `check_mod.MOD_SUFFIXES` = `SCANNED_TS_SUFFIXES`.
- A mod is a directory with `.claude-plugin/plugin.json` and `hooks/hooks.json` whose JSON object
  has a non-empty list under `modules`. A mod at or below a TypeScript project's root is not a mod
  project. Unreadable or non-JSON `hooks.json`: not a mod (the files then stay "outside every
  project", which names the problem).

- [ ] **Step 1: Write the failing tests** in `tests/standard/test_check_mod.py`, using the
  existing `run_check`/`git_repo` fixtures and a fake `claude` script put first on `PATH` that
  logs its argv and answers `plugin validate --strict <dir>` (exit 0, or 1 with
  `error TS2322` text) and `plugin test <dir>` (prints ` 7 pass\n 0 fail`, exit 0; or `1 fail`,
  exit 1):
  - `test_a_repo_with_only_a_mod_is_checked`: receipt has `exit=0 projects=1 format=n/a lint=n/a
    types=ok tests=7 coverage=n/a`, and the fake logged `plugin validate --strict <mod>` and
    `plugin test <mod>`.
  - `test_command_hooks_alone_are_not_a_mod`: a `hooks.json` with only `hooks` -> not discovered.
  - `test_a_mod_inside_a_typescript_project_is_left_to_it`.
  - `test_mod_sources_are_not_outside_every_project`, and `.tsx` outside a mod still is.
  - `test_a_type_error_fails_types` (`types=fail exit=1`), `test_a_failing_mod_test_fails_tests`.
  - `test_a_suppression_in_a_mod_fails` (`// @ts-ignore` in `hooks/register.tsx` ->
    `suppressions=1`, `exit=1`).
  - `test_without_claude_the_check_cannot_run`: `claude` off `PATH` -> setup error receipt
    `exit=2 ... error="claude not found on PATH"`.
  - `test_install_has_nothing_to_install_for_a_mod`: `install` exits 0 and prints a line naming
    the mod with `nothing to install`.
  - `test_python_and_mod_fold_together`: python project coverage `100` plus mod -> `coverage=100`,
    tests summed.
  - One `@pytest.mark.claude` test, `test_real_claude_checks_a_minimal_mod`: a three-file mod
    (manifest with `author`, `hooks.json`, `register.ts` plus one passing `*.test.ts`) passes with
    the real `claude`, run with `HOME` and `CLAUDE_CONFIG_DIR` set to `tmp_path` folders.
- [ ] **Step 2: Run them, expect failures**: `python3 .coven/check.py fast tests/standard/test_check_mod.py`.
- [ ] **Step 3: Implement** discovery, the outside rule, `check_mod.py` (suppressions with
  `check_ts.suppression_lines`' rules or a shared helper, `types` from validate, `tests` from the
  `N pass` line of `plugin test`; each run's tail shown on failure with `check_stages.show_tail`),
  dispatch in `check.py`, the `claude` setup problem, install skipping mods, and "no Python,
  TypeScript or Claude Code mod project found" where the message names the kinds. Copy changed
  modules to `.coven/`.
- [ ] **Step 4: Run** the new tests and `tests/standard/` -> PASS. The full suite's layout and
  dogfood tests must pass with the new sibling.
- [ ] **Step 5: Commit** `check: Claude Code mods are a project kind`.

### Task 2: Move aquelarre into plugins/aquelarre

**Files:**
- Create from the private aquelarre repo at `e6f138e` (its path is in the dispatch; `git archive`,
  never its history):
  `plugins/aquelarre/{.claude-plugin/plugin.json, .gitignore, hooks/hooks.json,
  hooks/register.tsx, hooks/register.test.tsx, types/index.d.ts, tsconfig.json}`; new
  `plugins/aquelarre/README.md`. Not its `docs/`.
- Modify: `.claude-plugin/marketplace.json` (second entry), `tests/test_plugin_structure.py`
- Test: `tests/test_plugin_structure.py`, the mod's own tests

**Interfaces:**
- Produces: plugin `aquelarre` at `./plugins/aquelarre`, manifest fields `name`, `version`
  (`0.1.0`), `description` ("A pane, a band and toasts that show the live coven project managers
  and what each one is doing"), `author` `{ "name": "WitchyNibbles" }`, `license` `MIT`,
  `homepage` (coven's), `types`.

- [ ] **Step 1: Write the failing Python tests**: marketplace lists `aquelarre` with
  `source: "./plugins/aquelarre"`; aquelarre's version equals coven's; a `@pytest.mark.claude`
  test runs `claude plugin validate --strict plugins/aquelarre` -> exit 0. No test names the
  private toolkit: the repo's local pre-commit hook scans every commit for private patterns and
  refuses one that holds the name, so a refused commit means a mention is left.
- [ ] **Step 2: Import and scrub.** Copy the files; in code and comments, the private toolkit
  where coven is meant becomes coven. Remove the status fallback (`STATUS_COMMANDS` keeps only
  `coven pm status`) and the second role variable; delete the tests that only covered them and
  turn "when neither ... exists" into `when coven is missing the error is shown`. Fixture `HOME`
  (a made-up user name) -> the placeholder `user`; PM names and senders using the private name -> `tower`,
  `gm-1`-style neutral names. Assertions otherwise unchanged; list every removed test in the
  commit body.
- [ ] **Step 3: Run** `claude plugin test plugins/aquelarre` (all pass, count printed),
  `claude plugin validate --strict plugins/aquelarre` (exit 0), `python3 tests/test_privacy.py`
  via the fast check, and the Python tests -> PASS.
- [ ] **Step 4: Commit** `aquelarre: import the mod into plugins/aquelarre` (one commit; the
  body says it is imported from a private repo at a point in time, without its history).

### Task 3: aquelarre settings: language, band, toasts

**Files:**
- Modify: `plugins/aquelarre/.claude-plugin/plugin.json` (`userConfig`),
  `plugins/aquelarre/hooks/register.tsx`, `plugins/aquelarre/hooks/register.test.tsx`,
  `plugins/aquelarre/types/index.d.ts` if state shape changes, `plugins/aquelarre/README.md`

**Interfaces:**
- `userConfig`: `language` string, options `["en", "es"]`, default `"en"`; `band` boolean,
  default `true`; `toasts` boolean, default `true`. Titles and descriptions in English. Exact
  manifest shape: what `claude plugin validate --strict` accepts on 2.1.325 and 2.1.330.
- One text table in `register.tsx`, `const TEXT: Record<'en' | 'es', Text>`; the Spanish entries
  are today's strings verbatim. English: working, idle ("paused"), waits / wait, "waits on the GM",
  "process dead", "no messages yet", "waiting for an answer", "new", "done" (plural form),
  "no plan yet", "no one working right now", "✕ couldn't read the PMs", "waits: ", "since HH:MM",
  "+N more", "✓ N done". The builder picks natural English for the rest and keeps glyphs.

- [ ] **Step 1: Write failing tests**: default options draw English (card, band, empty band,
  toast); `{ options: { language: 'es' } }` draws today's Spanish; `band: false` -> the
  `AbovePrompt` hook adds no row (`next(e)` only) while reads still feed the pane; `toasts: false`
  -> no `$.ui.toast` on a PM that starts waiting, finishes or dies; English cards keep the narrow
  cut order (state -> new -> name) at the existing narrow widths; English band compact form when
  narrow. Existing tests that assert Spanish text pass `{ options: { language: 'es' } }` and keep
  their assertions.
- [ ] **Step 2: Run** `claude plugin test plugins/aquelarre` -> new tests FAIL.
- [ ] **Step 3: Implement** the table, read `options` in `register`, gate the band and toasts.
- [ ] **Step 4: Run** `claude plugin test plugins/aquelarre` and
  `claude plugin validate --strict plugins/aquelarre` -> PASS / exit 0.
- [ ] **Step 5: Commit** `aquelarre: English by default, settings for language, band and toasts`.

### Task 4: coven installs aquelarre, doctor and smoke know it

**Files:**
- Modify: `.claude-plugin/plugin.json` (`"dependencies": ["superpowers@claude-plugins-official",
  "aquelarre"]`), `src/coven/doctor.py` (new check), `ci/install-smoke/run.sh`,
  `tests/test_doctor.py`, `tests/test_install_smoke_files.py`, `tests/test_plugin_structure.py`
- Docs: `README.md` (section on the pane + Uninstall line), `CHANGELOG.md` (0.1.0 unreleased),
  `docs/known-gaps.md`

**Interfaces:**
- Consumes: `paths.installed_plugins_file()`, the `_superpowers` reading pattern
  (`src/coven/doctor.py:148-166`), `claude_version` parsing.
- Produces: doctor check `aquelarre plugin`, after `superpowers plugin` in `CHECKS`: `ok` when an
  install entry for `aquelarre@coven` exists; `warn` "not installed; run claude plugin install aquelarre@coven"
  otherwise; `warn` "needs Claude Code 2.1.325 or newer" when the installed Claude Code is older.
  `AQUELARRE_TESTED_FROM = (2, 1, 325)`.

- [ ] **Step 1: Write failing tests**: manifest dependency present; doctor ok / not installed /
  too old (fake installed-plugins file and version, as the superpowers tests do); `run.sh` checks
  that `claude plugin list` shows `aquelarre@coven` enabled after installing `coven@coven`, runs
  `claude plugin validate --strict "$SRC/plugins/aquelarre"`, and allows the new doctor line;
  README has the section with a brand glyph lead (`tests/test_readme.py` rules) and the Uninstall
  section names `claude plugin prune`.
- [ ] **Step 2: Run** them -> FAIL.
- [ ] **Step 3: Implement.** README section: what the pane, band and toasts show, `/aquelarre`,
  the three settings in `/config`, that it reads `coven pm status` and writes only
  `~/.cache/aquelarre/`. known-gaps: mod API early access; types checked through `validate
  --strict` (no `tsc` in CI); tested from 2.1.325; format, lint and coverage are `n/a` for mods.
- [ ] **Step 4: Run** the fast check on the changed tests -> PASS; `claude plugin validate
  --strict .` at the root -> exit 0.
- [ ] **Step 5: Commit** `coven installs aquelarre; doctor and smoke check it`.

### Task 5: CI installs Claude Code for mods

**Files:**
- Modify: `standard/workflows/coven-check.yml` and `.github/workflows/coven-check.yml` (kept
  equal), `tests/test_install_smoke_files.py` (or the test that owns workflow pins)

**Interfaces:**
- Workflow: Node is set up when `**/package.json` or `**/.claude-plugin/plugin.json` exists. A
  step before "Install locked dependencies", `if: hashFiles('**/.claude-plugin/plugin.json') != ''`,
  runs `npm install -g "@anthropic-ai/claude-code@$CLAUDE_CODE_VERSION"` with
  `CLAUDE_CODE_VERSION: "2.1.325"` in the job's `env`.

- [ ] **Step 1: Write failing tests**: both workflow files equal; the pin equals `LAST_TESTED`;
  the install step exists and is conditional on a plugin manifest; Node's condition covers it.
- [ ] **Step 2: Run** -> FAIL. **Step 3: Implement.** **Step 4: Run** -> PASS.
- [ ] **Step 5: Commit** `ci: install Claude Code when the repo holds a plugin`.
