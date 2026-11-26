# OpenCode: OS isolation of PM shells

Status: approved by the user through the GM (ANSWER opencode-m2/5, 2026-11-25). Follow-up to the M2
hierarchy spec (`2026-11-25-opencode-m2-hierarchy-design.md`, "Raised by the review" item 3).

## Why

On OpenCode a PM's shell runs as the same user as the `opencode serve` process. The server guard
refuses commands that name the serve folder or the server's `/proc/<pid>/environ`, but a guard reads
command text, and text can hide a path. The server's password, port file and session database are
one `cat` away for any command the guard misreads. OS isolation closes that by construction: a PM
shell cannot see the server's process or those folders at all.

## Goals

- Every shell command of a PM tree runs in new user, PID and mount namespaces: its `/proc` shows only
  its own processes, and the profile's serve and data folders are empty tmpfs mounts.
- Files, git, gh and the network work as before (no network namespace: PMs push and open PRs).
- The GM's shells and the user's own `!` shells are unchanged.
- Fail closed with no off switch: where namespaces are not available, a PM shell refuses with a
  message naming the exact missing piece (user namespaces off, AppArmor blocks them, util-linux too
  old, `unshare` missing). It never runs unisolated.
- `coven opencode doctor` says whether isolation works here, naming the missing piece when it does not.

Non-goals: isolating the GM; a network namespace; isolating MCP or non-shell tools; other platforms
than Linux (macOS has no user namespaces: there the PM shell refuses, and the doctor says so).

## Mechanism

Probe on 2.3.2 (`opencode/test/e2e/isolation-probe/`, report in the plan workspace): from coven's own
`shell.create.before`, setting `event.shell` to another executable takes effect. OpenCode spawns
`<shell> -c <command>` in the call's cwd, the wrapper's output is the tool result, a wrapper that exits
non-zero stops the command (`Exit code: N` in the result), and a missing executable is a spawn error.
The default `event.shell` is `/usr/bin/bash`. Rewriting `command` in `tool.execute.before` also works
but breaks the role pairing of `shell-env.ts`, so it is not used.

1. coven ships `hooks/pm-shell-isolate.sh` (POSIX sh).
2. In `createBefore`, after the queue says the shell is a PM's, coven keeps the original
   `event.shell` in the shell's env, puts the folders to hide in the env, and sets `event.shell` to the
   wrapper.
3. The wrapper checks what it needs, then runs
   `unshare --user --map-root-user --pid --fork --mount --mount-proc`, mounts an empty tmpfs over each
   folder to hide that exists, drops back to the caller's uid and gid with a nested
   `unshare --user --map-user=<uid> --map-group=<gid>`, and execs the real shell with `"$@"`.
4. Mounts made in the outer namespace are locked for the inner one: the PM's process cannot unmount
   the tmpfs or the new `/proc`.

Checked on this machine (WSL2, kernel 6.18, util-linux 2.39.3): the command runs as uid 1000 inside,
`/proc` shows 3 processes, a tmpfs hides a folder's file, writes and `git status` work,
`max_user_namespaces=79976`.

## Failure

- The wrapper exits non-zero, before the command runs, with one line on stderr:
  `coven: PM shells run isolated and isolation is not available here: <missing piece>; ask the user`.
  The missing piece is the first of: `unshare` not found; util-linux older than 2.38 (no
  `--map-user`); user namespaces off (`max_user_namespaces` is 0, or
  `unprivileged_userns_clone` is 0); AppArmor blocks unprivileged user namespaces
  (`apparmor_restrict_unprivileged_userns` is 1); or the `unshare` error text when none of those
  explains it. A missing hide list or real shell in the env is also a refusal.
- If the plugin cannot find the wrapper at start, a PM's shell is refused (a throw from `createBefore`
  stops the shell).
- No setting turns isolation off.

## Known limits

- The role queue's mixed race (`MIXED = "pm"`): a GM shell started in the same instant as a PM shell
  with the same command and folder can get the PM's treatment, so it runs isolated once. Safe
  direction; documented.
- The session messages show the model's command; only the result shows the wrapper ran.

## Tests

- Unit (Python, `tests/oc`): the wrapper with a fake `unshare` and fake `/proc/sys` files on PATH and in
  a temp root: the exact argv it builds; each missing piece gives its own message and a non-zero exit;
  a missing env value refuses; the real shell gets `"$@"` unchanged.
- Unit (TS): a PM's shell gets the wrapper and the env values; a GM's shell and a shell with no queue
  entry do not; a wrapper path that cannot be found refuses a PM's shell.
- Doctor: an ok line, and a line naming the missing piece (fake wrapper result).
- e2e (real 2.3.2, fake model, offline): from a PM shell, the server's pid is not in `/proc`, reading
  `/proc/<server pid>/environ` fails, the serve and data folders list empty, git and a file write work;
  a GM shell still sees the server's pid and the folders.
