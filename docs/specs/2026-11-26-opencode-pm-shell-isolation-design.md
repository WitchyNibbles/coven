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

1. coven ships `hooks/pm-shell-isolate.sh` (POSIX sh). At plugin start the plugin copies it to
   `<serveDir>/isolate/pm-shell-isolate.sh` (folder 0700, file 0500, overwritten on each start): a PM
   could edit the source in its own checkout and so switch isolation off for its later shells, but
   the serve folder is an empty tmpfs inside a PM's namespace, so the copy is out of its reach. If the
   copy fails (no serve folder, a write error) PM shells are refused with `wrapper copy failed: <why>`.
2. In `createBefore`, after the queue says the shell is a PM's, coven keeps the original
   `event.shell` in the shell's env, puts the folders to hide in the env, and sets `event.shell` to the
   copy. The folders are the plugin's `serveDir` and `dataDir` options, which setup writes from the
   profile `Layout` (`serve_dir` and `data`).
3. The wrapper puts the caller's PATH aside and runs on a trusted PATH
   (`/usr/sbin:/usr/bin:/sbin:/bin`), so a PM cannot put an `unshare` of its own ahead of the system's
   and switch isolation off for later calls; it resolves every tool there, by absolute path for
   `unshare`. It checks what it needs, then runs a preflight, the same `unshare` flags on `true`
   with stderr captured, to name the cause when unshare fails. Then it `exec`s
   `unshare --user --map-root-user --pid --fork --kill-child --mount --mount-proc` (`--kill-child`, so
   that killing the process OpenCode spawned also kills the namespace's processes). Inside, the first
   process of the namespace traps TERM, INT and HUP and kills the rest, mounts an empty tmpfs over each
   folder to hide that exists, drops back to the caller's uid and gid with a nested
   `unshare --user --map-user=<uid> --map-group=<gid>` (tried first on `true`, so its failure is
   reported), and runs the real shell with `"$@"` and the caller's PATH. `COVEN_ISOLATE_SYS` and
   `COVEN_ISOLATE_PATH` are test seams read from the environment only; the plugin deletes both from a
   PM shell's env.
4. Mounts made in the outer namespace are locked for the inner one: the PM's process cannot unmount
   the tmpfs or the new `/proc`.
5. Stopping a PM reaches every tree it started. On 2.3.2 a shell call's `timeout` kills nothing: the
   call moves to the background and its namespace keeps running, so `--kill-child` alone is not
   enough. Before the final `exec`, the wrapper writes a pid record: a file named for its own pid (the
   `exec` keeps it, so it becomes the outer `unshare`) in `COVEN_PM_PIDS`, holding the process start
   time (field 22 of `/proc/<pid>/stat`), written to a temp file and moved into place; a write that
   fails refuses the shell. In `createBefore` the plugin sets `COVEN_PM_PIDS` to
   `<serveDir>/isolate/pids/<PM session id>` (made 0700 there; a failure refuses the shell) and
   `COVEN_PM_SESSION` to that id. The id is the session that holds the PM role, so the shell of a PM's
   child session files its tree under the PM that `coven_pm_stop` is given. A caller's values for both
   names are deleted. A shell whose PM cannot be placed (the role lookup failed) is refused, since no
   stop could reach it. `coven_pm_stop` interrupts the turn, then reads each record of that PM and
   sends SIGKILL to the pid only when its start time still matches, `/proc/<pid>/exe` is an `unshare`
   binary, its command line has `--kill-child` and its `/proc/<pid>/environ` holds
   `COVEN_PM_PIDS=<this PM's folder>` as a whole NUL-separated entry (so another PM's unshare is never
   killed by this stop); `--kill-child` then takes the namespace down. A pid that is gone (or a zombie)
   is not killed and its record is removed; a pid with another start time (a reused pid) is never
   killed, its record is removed and counted as stale; a pid that matches the start time but fails the
   exe, flags or environ check (or whose environ cannot be read) is not killed, counted as stale, and
   its record stays. After a kill the stop waits briefly for the pid to go: a tree counts as killed
   only when its pid is gone after the wait and its record is removed; a survivor is counted apart and
   its record stays. A leftover temp record (`.record.XXXXXX`) older than 60 s is removed; an empty
   folder is removed. The stop's text ends with `; killed N isolation tree(s)`, then `; skipped M stale
   record(s)` when M > 0, then `; K tree(s) still running after SIGKILL` when K > 0. A sweep that
   fails is a tool error that says the PM was interrupted.

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
  explains it. A missing hide list, real shell or pid folder in the env, or a pid record that cannot be
  written, is also a refusal.
- If the plugin cannot find the wrapper at start, a PM's shell is refused (a throw from `createBefore`
  stops the shell).
- No setting turns isolation off.

## Known limits

- The role queue's mixed race (`MIXED = "pm"`): a GM shell started in the same instant as a PM shell
  with the same command and folder can get the PM's treatment, so it runs isolated once. Safe
  direction; documented.
- The role queue's other race, in the unsafe direction: a user's `!` shell with the same command and
  folder as a PM call that is waiting can take that call's queue entry, and the PM's shell then
  starts with no role and runs unwrapped, once. Nothing on the 2.3.2 `shell.create.before` event
  tells the two apart: it carries `command`, `cwd`, `env`, `shell` and `timeout`, with no session id
  or call id. (A `!` shell was not probed on 2.3.2; the keys are those of the probe report.)
- The session messages show the model's command; only the result shows the wrapper ran.
- A shell that runs past its `timeout` is not killed by OpenCode 2.3.2 (the call moves to the
  background); its tree lives until `coven_pm_stop` of that PM, which kills it (mechanism 5), or until
  the OpenCode server exits. A tree started before the plugin wrote records (an older plugin) has no
  record and is not reached by a stop.
- A record whose start time cannot be compared (the server cannot read `/proc/<pid>/exe` or
  `cmdline` of the `unshare`) is skipped, not killed, and is reported as a stale record.
- Pid records of a PM that is never stopped stay in its folder until the next stop of that PM.
- The check and the kill of a stop are separate steps (Node has no pidfd). The start time and the
  environ are read microseconds before the kill, and a pid reused in that window would need a full pid
  wrap.
- The role queue's pairing: two PM shells with the same command and folder in the same instant can file
  a tree under the other PM. A stop of that other PM then kills it, and a stop of the right PM misses
  it until its next stop. This is the safe direction for isolation.

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
