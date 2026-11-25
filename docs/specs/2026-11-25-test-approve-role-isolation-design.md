# test_approve: fixture stores ignore the session role of the process tree

## Problem

`python3 .coven/check.py full` run from inside a Claude session that has `COVEN_SESSION_ROLE`
set (a project manager, the GM) fails `tests/test_approve.py`: 10 failed and 93 errors, all from
one cause. The tests build a store with `coven.memory.store.init_store`, which installs the real
pre-commit hook (`hooks/memory_pre_commit.py`), and then commit in it (`make_store`, and
`coven approve` / `revoke` themselves). The hook refuses a commit when the variable is set in its
own environment or in any ancestor's (`has_session_role`, reading `/proc/<pid>/environ` up the
`PPid` chain). `tests/conftest.py` already clears the variable in the test process, but the
ancestors (the session, its shell) still carry it, and a hook that git starts as a subprocess
cannot be monkeypatched. From a plain shell the same run passes
(`CHECK full exit=0 ... tests=9809`).

## Decision

- No change to `hooks/memory_pre_commit.py`: its refusal and the ancestor walk stay as they are.
- `tests/test_approve.py` neutralises the installed hook of every store it makes, the way the
  memory tests already do: `neutral_hook` (now in `tests/memory/memory_support.py`) rewrites the
  hook copy's `PROC_ROOT = Path("/proc")` line to a folder without processes. The walk then reads
  nothing and finds no ancestor role. The environment check is untouched, so the tests that set the
  variable themselves (`monkeypatch.setenv(ROLE, ...)`) still see the refusal.
- `neutral_hook` moves to `tests/support.py`, shared by the memory tests and `test_approve.py`
  (its second user). `memory_support` imports it from there.
- Only the hook copy inside a test's temporary store changes. Real stores, the shipped hook and
  `coven memory init` are untouched.

## Test

A test that fails today in any shell and passes after: a store made by `test_approve`'s
`make_store` is committed by a `git commit` whose parent process has the variable set while the
`git` process itself does not (a child started with the variable, which clears it for git). Today
the hook finds the parent's variable and refuses; with the neutral hook the commit lands.

## Not in scope

- Other test files that make stores without committing, or already pass from a session.
- Running pytest detached from the session, or any change to how the check runs.
