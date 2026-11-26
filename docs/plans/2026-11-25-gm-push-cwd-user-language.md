# GM fixes: `cd <dir> && git push`, and the user's language. Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use coven:subagent-driven-development to implement
> this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** pm-permissions resolves a plain push's branch in the directory the push really runs in,
and every coven text the GM reads keeps its replies in the user's language.

**Architecture:** Task 1 changes how `_plan` in `hooks/pm_permissions.py` carries candidate
directories across separators. Task 2 is prose plus text tests: routing, manager skill, handoff
skill, the OpenCode routing override and its regenerated content.

**Tech Stack:** Python 3.12 stdlib, pytest; the OpenCode content generator
`python -m coven.opencode.content`.

**Spec:** `docs/specs/2026-11-25-gm-push-cwd-user-language-design.md`

## Global Constraints

- Checks: `python3 .coven/check.py fast <tests>` while working; no suppressions, no weakened rules,
  never `--no-verify`.
- Commits carry no `Co-Authored-By` trailer and no "Generated with" line.
- No personal identifiers, user names, work paths or customer names in tracked files: use
  `<worktree>`, `<user>`.
- CHANGELOG: add a `## Unreleased` section above `## 0.2.0` (check `git log -p -- CHANGELOG.md`
  for how earlier unreleased entries looked) with one short bullet per task, in the file's style.
- Tests touching git never reach the network: the push tests must not run a real `git push`; the
  hook only reads config and refs. Any other outside service the code under test reaches is mocked
  or faked before the first test run.

## Review Focus

- `cd A && cd B && git push`: runs only in A/B; the old cwd is not a candidate.
- A newline between `cd X` and `git push` acts like `;` (check how `_tokens`/`_segments` see a
  newline and pin it with a refused case).
- `cd X && git status; git push`: the push may run in the old cwd (cd failed, status skipped),
  so the old cwd's `main` refuses it.
- `cd <relative> && git push` resolves against the payload's cwd, as `_after_cd` does today.
- The `gh` checks and `--body-file` checks that also read `candidates` keep today's (union)
  behaviour or become stricter, never looser by accident: say which in the commit.

---

### Task 1: plain push resolved where it runs

**Files:**
- Modify: `hooks/pm_permissions.py` (`Segment`, `_segments`, `_after_cd`, `_plan`, and the module
  docstring where it says what a `cd` does)
- Test: `tests/hooks/test_pm_permissions.py`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Produces: `Segment` gains `after: str`, the separator token before the segment (`""` for the
  first). `_plan(command: str, cwd: str) -> list[GitStep]` keeps its signature; each `GitStep`'s
  `directories` follows the spec's rule.

- [ ] **Step 1: Write the failing tests.** Add a `worktree` helper (or fixture) that runs
  `git -C <repo> worktree add -b <branch> <path>` and sets `branch.<branch>.remote=origin` and
  `branch.<branch>.merge=refs/heads/<branch>`, and one for a worktree on `main` (the `repo`
  fixture's checkout or a worktree with `main` checked out while `repo` sits on another branch).
  Tests, names as given, using the file's `verdict`/`denied` helpers:
  - `test_a_plain_push_from_a_feature_worktree_is_allowed`: `git push`, cwd = worktree → allow.
  - `test_a_plain_push_from_a_worktree_on_main_is_refused`: cwd = worktree on main → message has
    `pushing to main`.
  - `test_git_dash_c_a_feature_worktree_from_main_is_allowed`: `git -C <wt> push`, cwd = checkout
    on main → allow.
  - `test_cd_and_push_from_main_to_a_feature_worktree_is_allowed`, parametrized over
    `cd {wt} && git push` and `cd {wt} && git push 2>&1 | tail -n 2` → allow.
  - `test_cd_and_push_into_a_worktree_on_main_is_refused`: cwd on a feature branch, `cd <main wt>
    && git push` → `pushing to main`.
  - `test_a_cd_that_may_not_run_keeps_the_old_directory`, parametrized over `cd {wt}; git push`,
    `cd {wt}\ngit push`, `git status || cd {wt} && git push`, `(cd {wt}) && git push`,
    `cd {wt} && git status; git push`, cwd = checkout on main → `pushing to main`.
  - `test_cd_into_a_missing_directory_and_push_is_refused`: `cd <tmp>/missing && git push` →
    denied.
  - `test_chained_cds_leave_only_the_last_directory`: `cd <tmp> && cd <wt> && git push` from main
    → allow.
- [ ] **Step 2: Run them, see the cd/&& ones fail** (`python3 .coven/check.py fast
  tests/hooks/test_pm_permissions.py`). The plain-push and `-C` ones may already pass: say so.
- [ ] **Step 3: Implement.** In `_plan`, keep `possible` (every directory a segment may run in so
  far, today's `candidates`) and `chained` (the directories the previous segment ran in, or after
  a `cd` the moved ones only; `None` once the list is tainted). A segment with `after == "&&"` and
  `chained is not None` runs in `chained`; any other runs in `possible` and resets `chained` to
  `possible`. `||`, `|`, `(`, `)` set `chained = None` until the next `;` (or newline). A `cd`
  adds its moved directories to `possible` and sets `chained` to the moved ones only (no old ones).
  Pass each segment's directories to `_git_step`, `_after_cd` and `_check_gh` as today.
- [ ] **Step 4: Run the file's tests and the hook cost test** (`tests/test_hook_cost.py`): all pass.
- [ ] **Step 5: CHANGELOG bullet; commit** `fix(pm-permissions): a push after cd && runs only in
  the cd's directory`.

### Task 2: replies stay in the user's language

**Files:**
- Modify: `instructions/routing.md` ("Talking to people")
- Modify: `skills/manager/SKILL.md` (top of the skill; the brief list near line 164; "Always" at
  line 411)
- Modify: `skills/handoff/SKILL.md`, `opencode/overrides/skills/handoff/SKILL.md`
- Modify: `opencode/overrides/routing/AGENTS.md`; regenerate `opencode/content/` with
  `python -m coven.opencode.content`
- Check only: `agents/project-manager.md:91` (`DRAFT` "in the language of the hand-over")
- Test: `tests/test_routing_text.py`, `tests/test_skills_text.py`, the OpenCode content test under
  `tests/oc/` that compares rendered content
- Modify: `CHANGELOG.md`

**Interfaces:** none (text only).

- [ ] **Step 1: Write the failing text tests**, matching each file's existing style:
  - `test_routing_keeps_replies_in_the_users_language`: the "Talking to people" section names the
    user's language, says it holds after a compaction, and says briefs, subagent prompts and
    replies to PMs stay in English.
  - `test_manager_states_the_user_language_rule_first`: the rule appears before the skill's first
    `## ` heading, and the brief list includes the user's language.
  - `test_handoff_records_the_users_language` for both handoff skills.
  - The OpenCode routing override holds the same paragraph as `instructions/routing.md` (if the
    existing content test already compares them, extend it; do not duplicate).
- [ ] **Step 2: Run them, see them fail.**
- [ ] **Step 3: Write the text.** Routing paragraph, as plain as the section around it, content
  per the spec (user's language from the first line, also after compaction or resume; covers
  replies, questions, production-step summaries, reports and the text around drafts; text for
  agents stays English; repo text follows the repo; a draft follows its reader). Manager skill: a
  short paragraph after the opening one (relays of `QUESTION`, `PROD-STEP`, `DRAFT`, `DONE` are
  said in the user's language, with commands, paths, error text and the draft itself verbatim);
  "Always" keeps one pointer line; the brief gains "the user's language". Handoff: one line asking
  the document to name the language the user writes in. OpenCode override: the routing paragraph,
  with OpenCode's skill names where the routing names any. Regenerate the content.
- [ ] **Step 4: Run the text tests, the OpenCode content tests and `tests/test_plugin_structure.py`**:
  all pass.
- [ ] **Step 5: CHANGELOG bullet; commit** `docs(manager): keep replies in the user's language,
  also after compaction`.
