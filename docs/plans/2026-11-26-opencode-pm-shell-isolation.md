# OpenCode PM shell isolation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use coven:subagent-driven-development to implement this
> plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** every shell of a PM tree on OpenCode runs in its own user, PID and mount namespaces, with the
profile's serve and data folders hidden, and refuses when isolation is not available.

**Architecture:** a POSIX sh wrapper in `hooks/` does the namespaces and execs the real shell; the
plugin's `shell.create.before` points a PM's shell at it; the doctor runs it once; an e2e proves it on
2.3.2.

**Tech Stack:** POSIX sh + util-linux `unshare`; TypeScript plugin (node --test); Python CLI (pytest).

**Spec:** docs/specs/2026-11-26-opencode-pm-shell-isolation-design.md

## Global Constraints

- No off switch: no setting, env value or flag lets a PM shell run unisolated.
- Refusal and doctor text name the exact missing piece, from the spec's list, in the spec's wording.
- GM shells and shells with no queue entry are never wrapped.
- Tests and e2e offline, fake model; the real profile under `~/.local/share/coven/opencode` and
  `~/.config/opencode` are never written (the 2.3.2 binary there is only executed).
- No company LiteLLM configuration and no personal paths or user names in any file.
- CHANGELOG lines go under the existing `## Unreleased` heading.

## Review Focus

1. A folder to hide that does not exist (a fresh profile with no serve folder yet): the wrapper skips it
   and the shell still runs isolated.
2. A command that is not plain (quotes, newlines, `$`, a heredoc): it reaches the real shell unchanged
   as the one `-c` argument.
3. The PM's shell env: the role variable and the scrubbed env survive the wrapper; the wrapper's own
   env values do not change what the command sees beyond those names.
4. The cwd: the command runs in the call's `workdir` (or the location directory), not in `/`.
5. A PM command that tries to undo the isolation (`umount /proc`, `umount <serve folder>`,
   `unshare -r mount -t proc proc /mnt`): the hidden folders and the server's pid stay out of reach.

---

### Task 1: The wrapper

**Files:**
- Create: `hooks/pm-shell-isolate.sh`
- Test: `tests/oc/test_oc_isolate.py`

**Interfaces:**
- Produces: `hooks/pm-shell-isolate.sh -c <command>`; reads env `COVEN_PM_REAL_SHELL` (absolute path)
  and `COVEN_PM_HIDE` (folders, `:`-separated); `COVEN_ISOLATE_SYS` (test-only root for the `/proc/sys`
  reads, default `/proc/sys`) and `COVEN_ISOLATE_PATH` (test-only trusted PATH the wrapper runs on,
  default `/usr/sbin:/usr/bin:/sbin:/bin`) are the test seams; neither can turn isolation off. They are
  read from the environment only, so Task 2 deletes both names from a PM shell's env. The wrapper never
  resolves a tool from the caller's PATH and gives the caller's PATH back only to the real shell.
- Exit: the real shell's exit code; on refusal 126 with the spec's one stderr line.
- Task 4b adds `COVEN_PM_PIDS` (absolute folder): before the final `exec` the wrapper writes the file
  `<pid>` (its own pid) there, holding the start time (field 22 of `/proc/<pid>/stat`), through a temp
  file and `mv`; a missing or relative value, or a failed write, refuses. The doctor probe gives it a
  temp folder.

- [ ] Step 1: tests with a fake `unshare` on PATH (records its argv, can fail with a given stderr) and a
  temp `/proc/sys` tree: the outer argv is exactly
  `unshare --user --map-root-user --pid --fork --kill-child --mount --mount-proc <inner>` (Ruling 2:
  `--kill-child`, a preflight of the same flags on `true` first, then an `exec`); inside, a tmpfs mount per
  existing hide folder, then `unshare --user --map-user=<uid> --map-group=<gid>` and the real shell with
  `-c <command>` byte for byte (a command with quotes, `$`, a newline). One test per missing piece and
  its message; missing `COVEN_PM_REAL_SHELL` or `COVEN_PM_HIDE` refuses. A test that runs the real
  `unshare` only when the namespaces probe works on the machine is not allowed (no skips): the real run
  is the e2e's job.
- [ ] Step 2: see them fail; write the wrapper; see them pass; `python3 .coven/check.py fast
  tests/oc/test_oc_isolate.py`.
- [ ] Step 3: commit `feat(hooks): a wrapper that runs a PM's shell in its own namespaces`.

### Task 2: The plugin wraps PM shells

**Files:**
- Modify: `opencode/src/pm/shell-env.ts` (`createBefore`), its deps wiring in `opencode/src/coven.ts`
- Test: `opencode/test/pm-shell-env.test.ts` (or the file that tests `shellEnvHooks` now)

**Interfaces:**
- Consumes: Task 1's wrapper (`<covenRoot>/hooks/pm-shell-isolate.sh`) and env names.
- At plugin start (`pmIsolation`) the wrapper is copied to `<serveDir>/isolate/pm-shell-isolate.sh`
  (folder 0700, file 0500, overwritten each start), so a PM cannot edit what its later shells run
  (Ruling 4). The `serveDir` and `dataDir` plugin options are written by `plugin_options` from
  `Layout.serve_dir` and `Layout.data` (Ruling 5); a hide path that contains `:` is refused.
- `createBefore`: when the role is `pm`, set `env.COVEN_PM_REAL_SHELL = event.shell`,
  `env.COVEN_PM_HIDE = <serveDir>:<dataDir>`, and `event.shell = <the copy>`. A source that is not an
  executable file makes every PM shell throw (refused) with the spec's message and
  `wrapper not found: <path>`; a copy that fails does the same with `wrapper copy failed: <why>`.
- Task 4b: `createBefore` also sets `env.COVEN_PM_SESSION = <the PM session that holds the role>` and
  `env.COVEN_PM_PIDS = <serveDir>/isolate/pids/<that id>` (folder made 0700; a failure, an id that is not
  a plain name, or a shell whose PM was not placed refuses the shell); both names are deleted from any
  other shell's env. `opencode/src/pm/trees.ts` sweeps a PM's folder for `coven_pm_stop` (SIGKILL only
  when start time, an `unshare` exe and `--kill-child` all match; stale and gone records removed) and
  the stop text gains `; killed N isolation tree(s)` (and `; skipped M stale record(s)`).

- [ ] Step 1: tests: PM shell gets the wrapper and both env values; GM shell and a shell with no queue
  entry keep `event.shell` and get neither value; a missing wrapper refuses a PM's shell and not a GM's;
  the role variable is still set on a PM's shell; delete `COVEN_ISOLATE_SYS` and `COVEN_ISOLATE_PATH`
  from a PM shell's env; pin the hide paths (the PM cannot change them). Find where the plugin knows the profile's serve and data
  folders (search `opencode/src` and `src/coven/opencode` for the serve folder name) and use that, never a
  hardcoded home path.
- [ ] Step 2: fail, implement, pass; `cd opencode && node --test`; fast check on the test file.
- [ ] Step 3: commit `feat(opencode): a PM's shell runs through the isolation wrapper`.

### Task 3: Doctor line

**Files:**
- Modify: the module behind `coven opencode doctor` (`src/coven/opencode/cli.py` `run_doctor_command`
  and the checks it calls)
- Test: the doctor's tests in `tests/oc`

- [ ] Step 1: tests: with a fake wrapper run that succeeds, the doctor prints an ok line
  `PM shell isolation: ok`; with one that refuses, it prints
  `PM shell isolation: not available: <missing piece>` and the doctor's exit code counts it as a problem.
  The doctor runs the wrapper as `-c true` with a temp folder to hide and `COVEN_PM_REAL_SHELL=/bin/sh`.
- [ ] Step 2: fail, implement, pass; fast check.
- [ ] Step 3: commit `feat(opencode): the doctor says whether PM shell isolation works`.

### Task 4: e2e on 2.3.2

**Files:**
- Create: `opencode/test/e2e/isolation/` (suite `isolation`, registered in `main.ts`)
- Delete: `opencode/test/e2e/isolation-probe/` and its `main.ts` entry

- [ ] Step 1: checks, from a PM session's shell: the server's pid is not in `/proc`; reading
  `/proc/<server pid>/environ` fails; the serve and data folders list empty; `git status` and a file write
  in the worktree work; the cwd is the call's `workdir`; the role variable is set; `umount` of `/proc` and
  of the serve folder fail and the folder still lists empty. From a GM session's shell: the server's pid is
  in `/proc` and the folders list their files.
  Task 4b: the timeout check drives a call past its `timeout` (2.3.2 moves it to the background, nothing
  killed), then `coven_pm_stop` of the PM; zero processes of that namespace may be left, and the stop
  text must say it killed a tree.
- [ ] Step 2: run `cd opencode && COVEN_OPENCODE_BIN=<2.3.2 binary> npm run e2e -- isolation`, then the
  whole `npm run e2e` once; every line ok.
- [ ] Step 3: commit `test(opencode): e2e of PM shell isolation on 2.3.2`.

### Task 5: Docs

**Files:** `opencode/README.md` (the Hierarchy guards part), `docs/known-gaps.md` (drop the
isolation follow-up line), `CHANGELOG.md` (under `## Unreleased`), the README pins in
`tests/test_readme.py`.

- [ ] Step 1: pins first, then text: what is isolated, what still works, the refusal and the doctor line,
  the mixed-race limit. `humanizer` on the prose.
- [ ] Step 2: `python3 .coven/check.py full --base "$(git merge-base HEAD main)"` → `CHECK full exit=0`.
- [ ] Step 3: commit `docs(opencode): PM shell isolation`.

## Estimate (agent wall-clock)

| Task | h |
|---|---|
| 1 wrapper | 1-1.5 |
| 2 plugin | 0.5-1 |
| 3 doctor | 0.5 |
| 4 e2e | 1-1.5 |
| 5 docs, full check, final review | 0.5-1 |
| **Total** | **3.5-5.5 h** |
