# test_approve role isolation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use coven:subagent-driven-development to implement
> this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `tests/test_approve.py` passes from inside a session whose process tree has
`COVEN_SESSION_ROLE` set, with no change to the memory pre-commit hook.

**Architecture:** the stores `test_approve` makes get the same neutral hook the memory tests use
(the installed copy's ancestor walk points at a folder without processes). The helper moves to
`tests/support.py`, its second user.

**Tech Stack:** Python 3.12, pytest, git.

**Spec:** `docs/specs/2026-11-25-test-approve-role-isolation-design.md`

## Global Constraints

- `hooks/memory_pre_commit.py` and everything under `src/` are not changed.
- No skip, xfail, suppression, or lint/type/CI config change.
- Existing tests are not edited except to call the shared helper; their assertions stay.
- No Co-Authored-By trailer or "Generated with" line in commits.

## Review Focus

1. A test that sets the role itself (`monkeypatch.setenv(ROLE, ...)`) must still be refused by the
   hook: the neutral hook only blinds the ancestor walk, not the environment check.
2. Every store `test_approve.py` builds, in `make_store` or inline, gets the neutral hook before
   its first commit.
3. The memory tests keep passing with `neutral_hook` imported from `tests/support.py`.

---

### Task 1: neutral hook for test_approve's stores

**Files:**
- Modify: `tests/support.py` (add `neutral_hook`, moved from `tests/memory/memory_support.py`)
- Modify: `tests/memory/memory_support.py` (import `neutral_hook` from `support`; drop its copy
  and its `PROC_LINE`)
- Modify: `tests/test_approve.py` (`make_store` calls `neutral_hook(path)` right after
  `memory_store.init_store(path, None)`; any other store the file makes, too)
- Test: `tests/test_approve.py`

**Interfaces:**
- Produces: `support.neutral_hook(store_path: Path) -> None` with the same body and docstring as
  today's `memory_support.neutral_hook`.

- [ ] **Step 1: Write the failing test** in `tests/test_approve.py`:
  `test_a_fixture_store_commits_under_a_parent_with_a_role(tmp_path)`. Make a store with
  `make_store(tmp_path / "store")`, write and `git add` one new file, then run
  `[sys.executable, "-I", "-c", <script>]` with `env` = the current environment plus
  `COVEN_SESSION_ROLE="pm"`, where the script runs `git -C <store> commit -q -m x` with an
  environment that drops `COVEN_SESSION_ROLE`. Assert the outer return code is 0 and
  `git status --porcelain` is empty. The parent of `git` carries the role, so the real hook's
  ancestor walk sees it.
- [ ] **Step 2: Run it and see it fail**:
  `uv run --locked pytest -q tests/test_approve.py -k parent_with_a_role` fails with the hook's
  "commit refused" message in the script's stderr.
- [ ] **Step 3: Move `neutral_hook` to `tests/support.py` and call it in `make_store`** (and in
  any other place in `tests/test_approve.py` that makes a store before committing).
- [ ] **Step 4: Run the touched tests**:
  `python3 .coven/check.py fast tests/test_approve.py tests/memory` gives exit=0.
- [ ] **Step 5: Commit**: `test(approve): neutral memory hook in fixture stores`.
