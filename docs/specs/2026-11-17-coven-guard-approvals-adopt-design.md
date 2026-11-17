# coven addendum: production guard, approvals, systems map, memory adopt

Date: 2026-11-17. Status: approved by the owner 2026-11-17, with both expansions in. Extends
[2026-11-13-coven-v1-design.md](2026-11-13-coven-v1-design.md) ("v1" below); where they disagree,
this addendum wins for the parts it names.

## Why

The owner runs the private toolkit coven was rebuilt from on a second machine, with a memory store
of facts worth keeping and a production guard that the work on that machine relies on. Keeping two
near-identical toolkits is the cost the owner wants gone. The study before this addendum found that
the guard, the approvals that open it and the systems map are generic by design: what ties them to
one site is wording, examples and test fixtures, while the real targets and approvals already live
only in the user's local store. So they can move into coven, cleaned, and that machine can switch
to coven by adopting its existing store in place.

## What the owner decided (2026-11-17)

In the owner's words (one name replaced): "Estoy de acuerdo en poner guard/approvals/systems-map
en publico tras limpiarlos y dejar los data tools en privado, luego que el proyecto que esta
haciendo [the private toolkit] este terminado, veremos de crear los datatools como un opt-in de
coven." and, on a public adopt command and one store on that machine: "si a las dos, empieza el
spec".

1. coven gains the production guard, approvals, the `systems/` and `approvals/` memory scopes, a
   neutral systems-map skill and the manager's approval rules, after the private code is cleaned.
2. coven gains a public `coven memory adopt`.
3. A machine with a guard uses one store: the adopted store. There is no second, read-only store.
4. The owner's data tools stay private (their own package plus a skills-only plugin). Making them
   an opt-in part of coven comes later and is out of scope here; nothing here may block it.

## What this reverses in v1

| v1 text | Was | Now | Why |
|---|---|---|---|
| Non-goal "Migrating a machine that already runs the private toolkit" (:44) | out of scope | `coven memory adopt` takes over any store in coven's format, in place | the owner decided (2); the command is generic: it also moves a coven store between paths or machines |
| Non-goal list naming `adopt` (:36-37) | the private toolkit's whole-machine `adopt` | still cut | that `adopt` copied tool config into the toolkit; `coven memory adopt` is a different, store-only command |
| Hooks: "the private version's extra guard and its Bash scanner (about 850 lines) are cut" (:207-210) | cut | the guard comes back as a hook; the scanner grows only as far as the guard's tests need (see "The scanner question") | the owner decided (1) |
| D2: the GM runs an approved production step in its own session; "The PM never runs a production step itself" (:332-336, :449-450) | always | with a guard on: the PM runs the exact approved commands and the guard refuses anything else. With no guard: D2 stands | the guard checks the PM against the approval; without it nothing does, and the anti-goal at :55-56 stays |

## Goals

1. A user who keeps live systems on a machine can name them once in the store (`systems/`), and
   from then on no Claude Code session can reach them except with a live approval for the exact
   command.
2. The GM approves a step from the PM's own `PROD-STEP` text, for at most 12 hours, with one
   command, and the approval is a committed file the guard reads.
3. A user with an existing store in coven's format (or the private toolkit's, which is the same
   format) runs one command and keeps every entry, with a backup and a count check.
4. Users without a guard see no change: same hooks output, same D2 flow, no new prompts.

## Non-goals

- The owner's data tools, in any form. Their later opt-in is not designed here.
- A second store, a home/work split, or a "read-only store" mode.
- Ticket or chat tools, or any named outside system. Targets are the user's data.
- Codex, OpenCode.
- A private overlay plugin or an extension seam for one.

## Anti-goals

coven fails, even if it works, when:

- a tracked file or commit carries the owner's private identifiers, including names of the systems
  the guard protects on the owner's machine (v1's anti-goal, now with more private text nearby);
- the guard fails open: a crash, a timeout, an unreadable or invalid `guard.json`, or a missing one
  on a store that requires a guard lets a PM call through;
- an approval covers more than the commands the user saw;
- adopting a store loses, rewrites or reorders an entry's text, or leaves no way back;
- a user without a guard pays for one (slower tool calls, prompts, warnings).

## The memory store

- Layout gains two scopes: `systems/` (one Markdown entry per system, same frontmatter as other
  entries, plus `systems/guard.json`) and `approvals/` (`<id>.step` from the GM; `<id>.json`, or
  `<id>-<system>.json` when a step spans systems, written by `coven approve`). Both formats are
  the private toolkit's, unchanged.
- `INDEX.md` is rebuilt for `systems/` too (by the pre-commit hook and `rebuild_indexes`).
  `approvals/` has no index.
- The memory-context hook shows `systems/INDEX.md` after the user index, inside the same 8,000
  character budget, only when the folder exists.
- A store with `systems/guard.json` committed is **local-only**: `coven memory sync` commits and
  rebuilds indexes but never pushes, printing `not pushed: guarded store` each time; setup and
  doctor warn when such a store has a remote. It does not fetch or rebase either: remote commits
  could change `guard.json` or approvals at HEAD.
  (Ruling: precedent from the private toolkit, whose guarded store never had a remote.)
- The store's pre-commit hook keeps refusing PM commits (`COVEN_SESSION_ROLE` set, any value) and
  token patterns; approval JSON goes through the same token scan.

## The production guard

A PreToolUse hook, `hooks/prod_guard.py`, registered for every tool (`*`), run with `python3 -I`
under `fail-closed.sh` like deny-secrets, stdlib only.

**When it acts.** The hook reads coven's config as plain JSON (as memory-context does) for
`memory_path` and a new key `guard`.
- **Off** (exit 0, no git call, no subprocess) when config has no `"guard": "required"` and
  `<store>/systems/guard.json` does not exist in the working tree. This is one JSON read and one
  `stat`, so users without a guard pay only the Python start-up every hook already pays. With no
  config file the store is the default `~/coven-memory`, so deleting the config never switches
  off a store that holds `guard.json`. No store folder at the path is off.
- **On** otherwise. `coven memory adopt` and `coven setup` set `"guard": "required"` (an owned,
  undoable change) when the store has a committed `guard.json`; the systems-map skill runs
  `coven setup` after the first `guard.json` commit, and doctor warns while a committed
  `guard.json` has no latch. With the latch, deleting the file can only fail closed.
- Config present but unreadable or invalid: restricted sessions lose Bash and MCP tools (fail
  closed, message points to `coven doctor`); other sessions go on as if off.

**Who it restricts.** The target rules apply to **every** session, the GM and the user's own Claude
sessions included: a call that reaches a target needs a live approval whoever makes it. A session
whose `COVEN_SESSION_ROLE` is **set to any value**, empty or unknown included, is also
*restricted* and gets the extra rules below; only an unset variable is unrestricted. (The PM
permission hook keeps its `== pm` test; the guard is the stricter of the two on purpose.)

**What it reads.** Everything from the store's git HEAD, never the working tree: an uncommitted
edit, deletion or approval has no effect.
- `guard.json` valid at HEAD: enforced.
- Invalid at HEAD: the newest valid version among the last 50 commits is enforced, and every
  refusal says so, so the GM can still commit a fix. None valid: every session may only read, and
  an unrestricted session may also edit files with Write and Edit; the user commits the fix or
  reverts it in their own terminal (the refusal says so).
- Absent at HEAD (latch set): restricted sessions lose Bash and MCP tools; unrestricted ones go on.
- git cannot read the store (no commits, a broken repo, a timeout): any call that reaches outside
  (Bash, MCP) is refused for every session, naming the git error.

**`guard.json`.** The same format as the private toolkit's, so an adopted store works as it is.

```json
{"targets": [
  {"system": "orders-db", "kind": "production", "match": ["orders-db\\.internal", "psql .*orders"], "tools": []},
  {"system": "mail-send", "kind": "gated", "match": [], "tools": ["mcp__mail__send"]}
]}
```

`kind` is `production` (a live system) or `gated` (an outward action that must be approved every
time, such as a send to an outside party). `match` are Python regexes over a Bash command's code
text; `tools` are exact MCP tool names. `coven guard check <file>` validates a file with the
hook's own rules (prints `ok: N targets` or the reason, exit 0 or 1; never reads stdin).

**`coven guard explain`.** `coven guard explain [--tool <name>] [--file <path>] [<command>]` runs the
hook's own decision, read-only, on one Bash command (the argument, or the whole text of `--file`
for a multi-line one) or one MCP tool, against the store's HEAD. It prints:

- each target whose `match` or `tools` hit, with its system, kind and the first pattern that hit;
- for each such system, the live approval that names the exact command or tool (id, expiry), or
  `needs approval` when none does;
- the restricted rule a PM would hit, if any;
- one verdict line: `allowed`, `refused for a PM`, or `refused for every session`.

With the guard off it prints `guard off: <why>` and exits 0. Exit 0 when it explained, 1 on bad
input or an unreadable store (with the reason). It writes nothing and logs nothing. The GM runs it
on a `BLOCKED` to see which rule refused, and on a `PROD-STEP` before approving, to see which
systems the step touches and so which approval files `coven approve` will write. A PM may run it
too: it reads only what the guard reads.

**Target rule (every session).** A Bash command whose code text matches a target's `match`, or an
MCP tool a target lists, is refused unless a live approval at HEAD for **that target's system**
names the exact command (whitespace-stripped) or tool. A command that matches two systems needs a
live approval for each.

**Restricted rules (role set), checked first; no approval lifts them.**
- Any write to the store: Write, Edit and NotebookEdit under its path, and any Bash that reaches
  the store, reads included (as the private guard). PMs read the store with the Read, Grep and Glob
  tools; they never write it.
- Writes to the files that switch the guard off: coven's config file, Claude Code's
  `settings.json` and `settings.local.json` (in `${CLAUDE_CONFIG_DIR:-~/.claude}` and in any
  folder's `.claude/`), and the `plugins/` folder where the hooks live. A PM reads them with the
  Read tool.
- A write target (`rm`, `mv`, `ln`, `chmod`, a `cp`/`rsync`/`install` destination, `tar -C`,
  `unzip -d`, a redirect) that is one of those zones or a folder holding one (`~`, `~/.config`,
  `~/.claude`, the store's parents), so a PM cannot swap a whole folder back in.
- `$HOME`, `$CLAUDE_CONFIG_DIR`, `$CLAUDE_PLUGIN_ROOT` and `$XDG_CONFIG_HOME`, braced or not, are
  resolved with the hook's value and with their default; either reaching a zone refuses.
- Changing `COVEN_SESSION_ROLE`, starting `claude` sessions, and `coven approve` (a PM cannot
  approve its own step).
- A remote CLI (ssh, scp, rsync, psql, pg_dump, mysql, curl, wget and the like) whose host is not
  written out, since a target pattern could not see it.

Known limits (the private guard shares them; listed in known-gaps): a path built by a loop, a
variable assignment, a pipe into `xargs`, a substitution or interpreter code; a script written in
one call and run in a later one; plugins loaded with `--plugin-dir`.

**Code text.** Data is blanked only where the command runs nothing it is given (a commit message, a
grep pattern, quoted echo text); a shell, an interpreter, `$(...)`, backticks or anything the
scanner cannot follow is read whole. Approvals match the whole command exactly, never its code text.

**Limits.** 20 s for the whole check, a timeout per git call; past either, refuse. Every target refusal
names the target or rule and says to send a `PROD-STEP`. Decisions and failures go to the hook log
(`prod-guard.jsonl`), as v1's other hooks do. The guard is best effort against a PM that follows
its role; its rules are public, as pm-permissions' are. Its boundary is the committed store and the
approvals in it: the user's own terminal, outside Claude Code, is never guarded.

**Errors, by name.**

| Error | Raised when | Result |
|---|---|---|
| `PayloadError` | the hook payload is not a tool call | refuse (exit 2) |
| `ConfigError` | config unreadable or invalid | restricted: refuse Bash and MCP; others: off |
| `StoreError` | a git call fails or times out | refuse calls that reach outside, every session |
| `GuardError` | `guard.json` invalid (shape, regex, kind) | enforce the last valid version, else read-only |
| `DeadlineError` | the check passes 20 s | refuse |
| anything else | a bug | `fail-closed.sh` turns it into exit 2 |

### The scanner question

The private guard reads code text with a Bash scanner of about 1,180 lines; coven's
`hooks/shell_words.py` kept about 740, only what deny-secrets needs. Whether coven's copy is enough
is not known. The plan's first task settles it: carry the guard's test suite over (cleaned) and run
it against coven's scanner. Each failing case either pulls in the scanner code it needs, with its
own tests, or is listed as a known gap only if the owner accepts it. No scanner code comes over
without a failing guard test that needs it.

## Approvals

**Flow with a guard on.**

```
PM ── PROD-STEP (system, exact commands, tools) ──> GM ── shows ──> user
                                                     │ yes
                                                     ▼
                     GM writes approvals/<id>.step, runs coven approve <id> --expires-in 2h
                                                     │ commits approvals/<id>[-<system>].json
                                                     ▼
PM <── APPROVED <project>/<task> <id>: ── GM (printed list == PROD-STEP)
 │ runs each command as written; prod guard reads HEAD: live approval names it -> allowed
 ▼
DONE ──> GM revokes the step's approval files (one commit)
```

1. The PM sends `PROD-STEP <project>/<task>:` with the system, the exact commands (one per line, a
   multi-line command in its own fenced block) and MCP tool names.
2. The GM shows it to the user. On yes, the GM writes the commands, copied character for character,
   to `<store>/approvals/<id>.step` (one per line, fenced blocks, `tool: <name>` lines), then runs
   `coven approve <id> --expires-in <duration>` (`90m`, `2h`, `1h30m`; at most `12h`).
3. `coven approve` reads only that step file; it refuses unless the step file is the only change in
   the store. It matches each command and tool to the committed targets, writes one approval file
   per system (`<id>.json`, or `<id>-<system>.json` for several; `{"id", "system", "commands",
   "tools", "expires"}`, `expires` ISO-8601 with offset), commits them, deletes the step file and
   prints every command per file. A command or tool no target matches is an error, never skipped
   silently; the GM decides whether it needs no approval or the map lacks a target. On any failure
   it rolls back and approves nothing.
4. The GM compares the printed list with the `PROD-STEP`. On a match it replies
   `APPROVED <project>/<task> <id>:`. On a mismatch it removes the files with one commit and tells
   the user.
5. The PM runs each approved command as its own Bash call, character for character. A refused call
   is reported as `BLOCKED` with the refusal, never reworded or routed through a file or script.

6. When the PM's `DONE` (or `BLOCKED`) for that task arrives, the GM removes the task's approval
   files in one commit (`coven approve --revoke <id>`). An approval otherwise covers its exact
   commands, any number of times, until it expires: the guard reads only committed state and a PM
   cannot commit, so it cannot count runs.

`coven approve` also deletes expired approval files in the same commit, so `approvals/` stays
small. The PM tells the flows apart by the reply: `APPROVED ... <id>:` means run it;
`APPROVED ...: ran, <result>` means the GM ran it. The `PROD-STEP` text is the same in both.

**Flow with no guard.** v1's D2, unchanged: the GM runs the step in the user's session and replies
`APPROVED <project>/<task>: ran, <result>`.

`coven approve` refuses in a restricted session (role variable set). The guard ignores an approval
that expires more than 12 hours ahead.

## The manager's approval rules

The manager skill gains, for a store with a guard:

- the steps above, and validating a changed `guard.json` with `coven guard check` before its
  commit;
- learning: every answer is recorded in `user/prod-<kind>.md` (`type: feedback`), one dated line per
  answer. After 20 answers of one kind since its last reset the GM may approve that kind itself,
  citing them, and tells the user in one line. A decision against a past answer, or a user
  correction, resets the count;
- the same shape as an approved step (differing only in ids, dates, counts or data file names, on
  the same system) goes to the user as one line with the commands one tap away; the user may give a
  standing answer for one project until it is done;
- never delegated, always shown in full: `gated` targets (outward sends) and deletions on a
  production system.

The PM role text gains the guard-on flow (run exactly what was approved; `BLOCKED` on a refusal;
`APPROVED ... <id>` versus `ran, <result>`), and keeps "never run a production step" for the
no-guard case.

## The systems-map skill

`skills/systems-map/SKILL.md`, rewritten neutral: when a project touches a live system with no
entry, the GM explores what the user's sources say about it, drafts `systems/<system>.md` (what it
is, how it is reached, which parts are production, where its logs and tickets are), asks the user
to check each production flag and each gated action, writes the matching `guard.json` targets,
validates them with `coven guard check`, commits, and after the first `guard.json` commit runs
`coven setup` to set the `guard` latch. Examples use invented systems. Only the GM
writes `systems/`; PMs report a missing or wrong entry in their next message.

## `coven memory adopt`

`coven memory adopt <path> [--yes]` makes an existing store at `<path>` coven's store, in place.
Without `--yes` it prints the plan and changes nothing (like `coven setup --dry-run`).

1. **Preflight.** `<path>` is a git work tree with a clean status and a HEAD; `git fsck` passes;
   entries (Markdown with `name` and `description` in frontmatter) are counted per scope (`user/`,
   `projects/<key>/`, `systems/`, `handoffs/<key>/`; approval and step files are counted apart).
   Files without frontmatter and top-level folders outside these scopes are listed and kept as
   they are. Missing scope folders are not created.
2. **Backup.** A gzip tar of the whole folder, `.git` included, mode 0600, under
   `${XDG_STATE_HOME:-~/.local/state}/coven/backups/<name>-<stamp>.tar.gz`, with a `.sha256` file
   beside it. The command verifies the tar can be read back before going on.
3. **Old hook.** A pre-commit hook coven did not install moves to
   `.git/hooks/pre-commit.pre-adopt-<stamp>`; a local `core.hooksPath` is recorded and replaced.
4. **Keys.** Folders under `projects/` and `handoffs/` whose name is not already coven's key form
   (lowercase, spaces as `-`) are renamed with `git mv`, and so is the GM's state record inside one
   (`<Key>-state.md` to `<key>-state.md`). Two folders that would collide stop the command before
   any change, listing both.
5. **Store setup.** The same steps as `coven memory init` on an existing store: `.gitattributes`,
   hook, `core.hooksPath`, indexes rebuilt, one commit for renames and indexes.
6. **Check.** Entry counts per scope must equal the preflight's; on a difference the command stops
   before touching the config, says what differs and prints the restore commands.
7. **Config.** `memory_path` points to `<path>` through setup's owned-key machinery, so
   `coven setup --undo` restores the previous value. A committed `guard.json` also sets
   `"guard": "required"`.
8. **Report.** The backup path and checksum, the renames, the hook's new name, and the way back
   (also written in the adopt commit's message, so a re-run can print it again):
   `git revert <commit>`, restore the old hook, `git config --local core.hooksPath <old>` (or
   unset), `coven setup --undo`, and as a last resort the tar.

Re-running after a stop is safe: each step checks whether it is already done (the coven hook in
place, keys already in form, config already set) and skips it; a new backup is made each run.
The backup folder is mode 0700.

It never pushes, never deletes an entry, never edits an entry's text and never touches a remote.

## Setup and doctor

- `coven setup` shows `"guard": "required"` as an owned change when the chosen store has a committed
  `guard.json`.
- `coven doctor` reports: guard on or off and why; `guard.json` valid at HEAD (via the check); live
  approvals and their expiry; a guarded store with a remote (warning).
- `coven doctor` shows the guard's last 5 refusals from `prod-guard.jsonl`, newest first, one line
  each: time, role (`pm`, another value, or `unset`), the target system or rule, and the command or
  tool cut to 80 characters as one printable line. It is an info line, not a warning: refusals are
  the guard working. With none logged it says `no guard refusals logged`. The manager already runs
  `coven doctor` at session start, so the GM sees it there; the manager skill says to read it and
  to ask the PM about a refusal it has no `BLOCKED` for.

## Privacy and porting

- Nothing comes over with history. Code, skills and tests are rewritten or copied by hand into
  fresh commits; every example names an invented system.
- The guard's test suite (about 160 tests) and the approval tests come over with every site-derived
  name, host and fixture replaced by synthetic ones. The local private-pattern scan runs on every
  commit, as in v1.
- Wording that names ticket or chat tools becomes "an outward send" or "a gated action".
- The builder ports from the private toolkit's repo on the owner's machine (read access, by hand,
  file by file), not from this spec alone. The private Python 3.10 compatibility test is dropped
  (coven needs 3.12).

## Testing

- The guard as a subprocess with JSON payloads, against a temporary store built in the test:
  guard off, on, `required` without `guard.json`, invalid `guard.json` with a valid older one,
  uncommitted edits ignored, approvals live, expired and too long, role unset, `pm`, empty and
  unknown, timeout and crash under `fail-closed.sh`.
- `coven approve`, `coven guard check` and `coven guard explain` against temporary stores; the
  doctor refusals line against a hand-written log.
- Adopt against temporary stores: a coven store, a store in the private toolkit's layout with a
  foreign hook, mixed-case keys, a colliding pair, a dirty tree, entries without frontmatter; counts
  equal, backup readable, undo restores config.
- No test reaches a real system: every target in tests is invented and every remote CLI call is a
  command string the guard reads, never runs.

## Rollout on the owner's guarded machine

Not part of the build; listed so the build leaves room for it.

1. Before switching, run coven's guard and the private toolkit's guard side by side over the same
   inputs on that machine (the carried test suite plus tool-call payloads replayed from local
   session transcripts, which never leave the machine). Every differing decision is a bug to fix
   before the switch.
2. Close every Claude Code session on the machine, then back up and adopt the store (`coven memory
   adopt`), remove the private toolkit's Claude-side install, install coven, check `coven doctor`
   and one guarded call refused without an approval and let through with one.
3. Keep the private toolkit's repo and its data tools; they no longer touch Claude Code's config.

The shadow run in step 1 is not part of the coven build; it happens on that machine before step 2.

## Data tools later

Nothing here may block a later opt-in: no config key or skill name is reserved for them, the guard
treats data-tool commands like any other Bash (their targets come from `guard.json`), and PMs may
run any CLI the user installs.

## Rulings (the builder's calls, for review)

- D2 stands where no guard is on; the reversal applies only where a committed
  `systems/guard.json` exists (the GM's ruling, 2026-11-17) — precedent: the owner accepted D2 for
  v1, and the 2026-11-17 decision named only the guard and approvals, which enforce only where a
  guard exists; v1's anti-goal at :55-56 — cost if wrong: one paragraph changes.

- A guarded store is local-only: sync never pushes — precedent: the private toolkit's guarded store
  had no remote and no sync — cost if wrong: one config switch to allow a private remote.
- `"guard": "required"` latch, set when a committed `guard.json` is found, so a later deletion
  fails closed instead of switching the guard off — source: the private guard refused Bash and MCP
  without `guard.json` on a guarded machine — cost if wrong: a user who wants the guard gone runs
  `coven setup --undo` or edits config.
- Guard restricts on any set role value, pm-permissions on `pm` only — source: the private guard's
  rule — cost if wrong: none for a user's own session, which never sets the variable.
- Command names `coven approve` (with `--revoke`) and `coven guard check` — cost if wrong: a
  rename before release.
- Target rules apply to every session, not only PMs; a command matching two systems needs both
  approvals; role rules come before approvals — precedent: the private guard's `decide` and
  `_target_refusal` — cost if wrong: the GM's own session needs approvals it did not before, and
  the user can drop the rule for unrestricted sessions.
- Off-path is config plus one `stat`, no git — source: goal 4 and the anti-goal on cost — cost if
  wrong: a committed `guard.json` deleted from the working tree before the latch is set switches
  the guard off; doctor warns about a missing latch.
- The GM revokes a task's approvals on its `DONE` or `BLOCKED`, and `coven approve` prunes expired
  ones — source: an approval is reusable until expiry and the guard cannot count runs — cost if
  wrong: one extra commit per step.
- Restricted sessions also may not write coven's config file or run `coven setup` or
  `coven memory adopt|init|sync`: the latch and the store path live in the config — precedent:
  the private guard refuses a PM its own installer commands — cost if wrong: a PM asks the GM for
  a setup change.
- Restricted sessions may not write Claude Code's settings files or `plugins/`, nor a folder that
  holds a guarded zone — source: a review showed a PM could set `disableAllHooks` or swap
  `~/.claude` back in with an edited hook; the private guard has the same gap — cost if wrong: a PM
  asks the GM for a settings change or a cleanup in `~`.
- The store's pre-commit hook refuses commits when the role variable has any value, not only
  `pm` (hook version bump) — source: the stricter role rule — cost if wrong: none for sessions
  that never set it.
- The PreToolUse `*` matcher covers every tool — source: the Claude Code hooks docs and the
  private toolkit's registration; checked live only at rollout — cost if wrong: one matcher change.
- Adopt backs up with gzip, not zstd — source: stdlib only (v1 constraint) — cost if wrong: larger
  backups.
- `systems/INDEX.md` joins the session-start block within the existing budget — cost if wrong:
  fewer project entries shown on a store with many systems.

## Estimate

From the study (judgement, not measured): guard core 14-18 h; approvals and CLI 8-10 h; memory
scopes 4-6 h; systems-map skill 4-6 h; manager and PM rules 3-4 h; cleaning the carried tests
6-10 h; adopt 4-6 h; setup, doctor, docs 3-5 h. About 45-65 h, plus 2-3 h for the two
expansions, plus 30-40 h if the scanner question needs most of the private scanner.

## CEO review (2026-11-17)

Run by the PM in a background session; the owner had fixed the scope, so questions went to the GM
only where the ladder found no answer (one: D2 without a guard, answered).

- **Mode:** hold scope (ruling: the owner decided the scope on 2026-11-17).
- **Premise:** right problem. Doing nothing keeps two toolkits; the private-overlay alternative was
  the owner's to reject, and they did.
- **Alternatives:** (A) guard and approvals only, adopt as a written runbook: about 40-55 h, less
  code, but the owner asked for a public command. (B) this addendum: about 45-65 h. (C) the guard
  as a generic policy engine with pluggable rule sources: more than 80 h, no second use today.
  Chosen: B, the owner's decision.
- **Strongest challenges, fixed in this text:** the guard's cost for users without one (now a config
  read and one `stat`); approvals reusable for 12 h (now revoked on `DONE`, expired ones pruned);
  target rules scoped to PMs only, against the private guard's precedent (now every session).
- **Also fixed:** a named error per failure, the approval flow diagram, adopt's re-run and state
  record rename, the `guard.json` states (valid, invalid, absent, unreadable) spelled out, a
  visible `not pushed` line for guarded stores.
- **Risk kept open:** the scanner question; it may add 30-40 h. It is the plan's first task.
- **Expansions:** `coven guard explain` and a doctor line with the last five refusals. The owner
  opted into both on 2026-11-17 ("si a los dos"); they are in the sections above.
- **Not in scope:** data tools, a second store, named outside systems, other agent tools.
