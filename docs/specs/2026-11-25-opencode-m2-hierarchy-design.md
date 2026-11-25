# coven on OpenCode, M2: the GM/PM hierarchy

Status: draft for the user's review (2026-11-25). Nothing is built until the user approves it.

## Why

On Claude Code the user hands projects to a general manager (GM), which runs one background
project manager (PM) per project with `claude --bg`, `SendMessage` and idle notices. M1 brought
single-session coven to OpenCode 2.3.2. M2 brings the hierarchy, so the same way of working runs
on OpenCode: the user talks to one GM, the GM runs PMs, PMs run role agents, and the user only
sees real questions, production steps and results.

The spike ([2026-11-25-opencode-m2-spike.md](../research/2026-11-25-opencode-m2-spike.md)) showed
every piece working on 2.3.2 with one server, and found two things the 1.x plan did not have:
secrets reach every shell command, and agent rules do not reach a PM's subagents.

## Decisions

The GM decided these on 2026-11-25 while the user was away, under the user's standing rule that
the GM decides the questions overnight. Each is a GM call the user can overturn when reviewing
this spec; the section that depends on each one is named.

| # | Decision (GM call, can be overturned) | Shapes |
|---|---|---|
| D1 | The GM is an OpenCode lead session in the TUI, attached to the server coven owns. A Claude Code GM driving OpenCode PMs over HTTP is out of M2. | GM session, tools |
| D2 | coven owns `opencode serve`, started on demand: `coven opencode serve start/stop/status`, a port file and a pid file, a password generated into the profile, and a second server is refused. 2.x's `opencode service` is not used. | Server lifecycle |
| D3 | A PM that meets an ask is denied in-process (`permission.hook("evaluate")`). The refusal goes to the GM as a `QUESTION` or `PROD-STEP` (fail fast), as pm-permissions does on Claude Code. | Asks |
| D4 | Turn-end notices go only for PM sessions (no `parentID`), and only when the turn ended without the PM messaging the GM in that turn. | Notices |
| D5 | M2 ships before M3 (the production guard). On OpenCode the manager follows the "No guard" flow: every `PROD-STEP` goes to the user, and the GM runs it on a yes. The docs say so. | Manager skill, docs |
| D6 | Models per role stay local config (`coven opencode setup --model ROLE=ID`). This spec names roles, never gateway models. | Profile |
| D7 | PM-tree sessions run the existing production guard in deny-only mode: every production match is refused, there are no approvals, and a guard that cannot load fails closed. Until M3, a production step on OpenCode is always run by the GM, after the user's yes. (Raised by the ceo-review, decided by the GM.) | Production guard |
| D8 | The PM todo list is out of M2. | Status |

The GM's brief also required these in the design, so they are not open to the builder: the
scrub of the password and the key from shell env, the guard twin against model access to the
server API, a guard on the profile config folder, the PM rules on session `permissions`, an e2e
of the hierarchy on the fake model, and an e2e of deny-secrets in a served session.

Calls marked "PM call" below were made by the project manager that wrote this spec
(pm-opencode-m2), from the code and the spike, and are open to the user too. Two of them
refine a GM decision:

- D4: no notice while the PM still has a busy child (its report will wake the PM), and a turn
  that ends `failed` always gets a notice, message or not.
- D1: a new `coven_gm_claim` takes over from the previous GM session instead of being refused.

## Goals

1. `coven opencode serve start` runs one password-protected `opencode serve` on loopback for the
   coven profile; `coven opencode gm` opens the lead TUI attached to it.
2. From the GM session, the GM launches, lists, messages, resumes and stops PMs, each in its
   repo (making its own worktrees), with its own agent, rules and role, and gets their
   `QUESTION`/`DONE`/... back as messages that wake it.
3. A PM and its role agents run unattended: no prompt ever waits for a person. Anything the PM
   rules would ask is refused and reported.
4. The session role reaches every shell command in a PM's tree, so the memory store's pre-commit
   refusal and the hooks work as on Claude Code.
5. No model that follows its role can read the server password or the provider key, reach the
   server's API, or change the profile's config or coven's guards: nothing it would naturally run
   exposes them (the scrub), and every direct route the guards can see is refused. This is best
   effort, as the production guard on Claude Code is: the server runs as the user, so a model
   that deliberately scripts around the guard (for example a Python one-liner that builds the
   `/proc/<pid>/environ` path) can still read the server's env. Closing that needs OS isolation
   of PM shells, which is a question for the user (see "Raised by the review").
6. An offline e2e on the fake model proves the hierarchy end to end, plus deny-secrets in a
   served session.

## Non-goals (M2)

- A Claude Code GM driving OpenCode PMs (D1). Two GMs on one server.
- `opencode service` (D2). Starting the server at login, or as a system unit.
- Approvals and the guard-on flow on OpenCode, and the production guard for the GM session (M3,
  D5). M2 has only the deny-only guard for PM trees (D7).
- The PM todo list in status. aquelarre (M4). OpenCode 1.x.
- Holding an ask for the GM to answer (D3).
- `coven pm status/health/resume` (the Claude Code commands) reading OpenCode sessions. An
  OpenCode GM's project state records hold `pm-<code>` names, not Claude session ids, so
  `coven pm health` on Claude Code does not read them; switching tools mid-project on one store
  is not supported in M2.

## Anti-goals

- A prompt that waits on nobody: any PM-tree session left waiting on an ask is a bug.
- The server password or the provider key in a shell env, a log or a test output, or in a file
  the guards let a model read.
- A model reaching the server API or the session database by any route the guards can see: any
  `opencode` subcommand but `--version` and `--help`, HTTP to the server's port, the password
  file, the profile's `data/`.
- A role that fails open: a shell command in a PM tree that runs without the role set, or a
  role lookup that fails and lets the call through.
- Touching `~/.config/opencode`, the user's own OpenCode, or the real coven profile in any test.
- Company LiteLLM configuration in the repo: no gateway URLs, keys or model names.

## 2.3.2 facts this design rests on

All seen in the spike (fake model, offline). The ones marked "not seen" are checked first by the
plan.

- Routes are `/api/*`, the document is `GET /openapi.json`. `POST /api/session` takes `agent`,
  `location.directory`, `metadata`, `permissions`, `title`; `POST /api/session/{id}/prompt` is
  async and takes `delivery: queue|steer`.
- From a plugin, `ctx.session.prompt({sessionID, text, delivery})` reaches a session in any
  location, and `ctx.session.create`, `list`, `get`, `interrupt`, `update` exist.
- `GET /api/event` and `ctx.event.subscribe` see every location. Turn end is
  `session.execution.succeeded|failed|interrupted`; `session.idle` is never sent.
- Session `metadata` and `permissions` are copied into child sessions at creation. Agent rules
  are not. Session rules beat the profile's global rules.
- `permission.hook("evaluate")` fires and `effect = "deny"` works; the model sees
  `permission.rejected`. An ask with no hook waits with no timeout.
- `shell.hook("create.before")` gets `command, cwd, timeout, shell, env` with a mutable `env`,
  and no session id. `tool.hook("execute.before")` gets `tool, sessionID, agent, input`.
- Plugins are set up once per location; the setup gets `ctx.location.directory`.
- Shell commands inherit the server's env, including `OPENCODE_SERVER_PASSWORD` and the
  provider key variable.
- Sessions, metadata and outcomes survive a server restart; in-flight turns and pending asks do
  not.
- Config writes hot-reload plugins (`plugin.updated`).
- Checked live in Task 0 (see "Task 0 results" at the end): TUI attach, a throw in
  `create.before`, the hook argument match, permissions across a restart, the server process,
  plugin `session.create` and `list`, the permissions schema, shared plugin state, code in
  `cache/` and `state/`. Still not seen: two PMs busy at once.

## Approaches

1. **PM control as plugin tools, server lifecycle as a CLI** (picked). The GM's tools
   (`coven_pm_launch`, `coven_pm_list`, `coven_send`, ...) live in coven's plugin and use
   `ctx.session` in-process, so no model ever needs the password. The CLI owns the server and a
   read-only `status` for the user's terminal. The guard can then refuse every model route to the
   API without exceptions.
2. **The Claude Code shape: `coven pm launch` is a CLI the GM runs in its shell.** Familiar and
   reuses `src/coven/pm/`, but the CLI needs the password, so the GM's shell would have to see
   it, and with it any model in that session can drive the API. Breaks goal 5.
3. **A separate coven daemon between the GM and the server.** Clean separation and a place for
   watchers, but a second long-running process with its own lifecycle, auth and tests, for work
   the plugin already does in-process.

## Design

### Server lifecycle (D2)

`coven opencode serve start|stop|status` in `src/coven/opencode/serve.py`, run in the same env as
`coven opencode run` (`runtime.run_env`).

- Files in the serve folder, `<profile>/state/serve/` (`Layout.serve_dir`): `password` (0600, 32
  random bytes, hex, made on first start and kept), `port`, `pid` (the pid of the process that
  holds the port, as Task 0 finds it, not a wrapper), `log`.
- `start`: refuses when the pid file names a live `opencode` process ("a server is already
  running on port N"). Otherwise it starts `opencode serve --hostname 127.0.0.1 --port 0`
  (or `--port N`) detached with `OPENCODE_SERVER_PASSWORD` from the file, reads the port from the
  server's listening line as the doctor's canary already does, waits for `GET /api/session` with
  the password to answer (timeout 20 s), and writes the pid and port files. Stale files from a
  dead pid, or a pid now held by another program, are replaced. (PM call: reusing the canary's
  port-0 start avoids a pick-then-bind race.)
- `stop`: SIGTERM to the pid, wait 10 s, then SIGKILL; removes the pid and port files, keeps the
  password.
- `status`: one line (`running pid P port N` or `stopped`), then the PM sessions from
  `GET /api/session` (no `parentID`, `metadata.coven.role == "pm"`): code, title, busy or idle,
  last outcome, waiting asks. Read-only.
- `coven opencode gm`: starts the server if needed, then opens the TUI attached to it,
  `opencode --server http://127.0.0.1:<port>` in `run_env`, with `OPENCODE_SERVER_PASSWORD` in
  the TUI process env only (`run_env` drops `OPENCODE_PASSWORD`, the name the TUI's own error
  text gives).
- `doctor` adds: the server answers when running, the plugin is `active` on it, the password
  file is 0600, and `/openapi.json` has every operation coven calls (so a runtime bump that drops
  one fails in doctor, not in a PM).
- `coven opencode run` refuses while the server is running ("a coven server is running; use
  `coven opencode gm`"): `run` and `serve` share the profile's database, and two OpenCode
  processes writing it at once is not something 2.3.2 promises to handle. For the same reason
  `doctor` runs its server checks against the running server instead of starting its canary
  server, and starts the canary only when no server runs.

### Roles and the GM session (D1)

- Roles live in session metadata: `{"coven": {"role": "gm"|"pm"|"former-gm", "code": "<project
  code>"}}`. Children copy it, so a PM's implementer is in the PM tree.
- The GM session is the lead TUI session. It has no metadata at creation, so the GM marks itself
  with a `coven_gm_claim` tool (sets `role: gm` on its own session). The manager skill calls it
  first. A claim takes over: the previous GM session, if any, becomes `role: former-gm` first and
  the claimer becomes `gm` second (a claim that fails between the two leaves no GM, which PMs
  see as "no GM session", never two GMs), so a
  user who opens a new TUI session tomorrow becomes the GM without archiving anything. A PM never
  stores the GM's id: `coven_send` to the GM and the notices go to the root session (no
  `parentID`) that holds `role: gm` when they are sent; a GM's child copies `role: gm` but is
  never the GM. A claim from a session that has a `parentID` or a role (`pm`) is refused, so a
  PM or its child can never become the GM. (PM calls: the TUI creates the session, so coven
  cannot tag it at create time; takeover instead of refusal, after the builder check.)
- Role lookup fails closed. When metadata cannot be read (a `get` that throws or returns
  nothing for a session in the chain, a cycle, more than 32 hops), the call is treated as a PM
  tree call: the PM guards run, an ask becomes a deny, and a GM tool is refused.
- Only PM-tree shells get the role variable. The GM's shells get none, because the memory store's
  pre-commit hook refuses a commit whenever the variable is set, and the GM writes the store.
- A new profile role `project-manager` joins `ROLES`, with a model from `--model
  project-manager=ID` and the lead's model as default (D6), written to the generated config as
  `agents.project-manager.model`. The GM uses the lead role.
- The PM agent (`mode: primary`) comes from an OpenCode override,
  `opencode/overrides/agents/project-manager.md`, with its own description and the source hash
  header the content generator checks: the Claude Code source names `claude --bg` and
  `SendMessage`, which the rewrite table forbids.

### GM tools in coven's plugin

Registered for every session, refused unless the calling session's role is `gm` (resolved from
metadata, then the `parentID` chain).

| Tool | Does |
|---|---|
| `coven_gm_claim` | marks the calling session as the GM |
| `coven_pm_launch {code, repo, brief, extraRepos?, title?}` | `ctx.session.create` with agent `project-manager`, `location.directory` = the repo's checkout, metadata `{role: pm, code}`, the PM rules as session `permissions`, title `pm-<code>`; then `ctx.session.prompt` with the brief. The PM makes its own worktrees under `<repo>/.claude/worktrees/`, inside its location, as on Claude Code, one per task, so parallel tasks and one PR per task stay. Refuses a code that already has a live PM, a repo that is not a git checkout, and an invalid code (`is_valid_code` in `pm/text.py`). (PM call, after the builder check: a worktree fixed at launch would rule out per-task worktrees.) |
| `coven_pm_list` | the PM sessions: code, id, busy/idle, outcome, waiting asks (`ASK WAITING (bug)`), last message head |
| `coven_send {to, text}` | `ctx.session.prompt(..., delivery: "queue")` to a PM by code (from the GM) or to the GM (from a PM; `to` is ignored there). Checks the first line's prefix: GM to PM `ANSWER|APPROVED|DENIED|TASK`, PM to GM `QUESTION|PROD-STEP|DRAFT|DONE|BLOCKED`. Prefixes the text with `[from pm-<code>]` or `[from gm]`. |
| `coven_pm_resume {code, text}` | unarchives the PM if needed and prompts it with `RESUMED by gm` plus the text |
| `coven_pm_stop {code, archive?}` | `ctx.session.interrupt`, optionally archives; marks the stop so no notice follows |

`coven_send` is also registered for PM sessions (to the GM only). A child of a PM (an
implementer) gets no hierarchy tool. The prefix lists are a TS constant in `opencode/src/pm/`
with a test that reads `src/coven/pm/text.py` and fails when the two differ (PM call: a contract
test instead of a generated file, since the lists change rarely).

State shared between tools and the notice tracker (the stop marks, the per-turn "messaged" marks,
the shell queue) lives on `globalThis` under one coven key, so every location's plugin instance
in the one server process sees it. Module scope is not enough: 2.3.2 evaluates the plugin once
per location, and Task 0 saw three module copies in one process share only `globalThis`.

### PM rules on the session (D3)

The PM session's `permissions` hold the PM ruleset, copied into every child. Session rules beat
the profile's global rules, so the ruleset repeats the global denies after its allows:

1. `shell *` allow, `edit *` allow, `read *` allow.
2. `external_directory` allow for the profile's `tmp/` and each of `extraRepos`.
3. `shell git push --force*`, `-f*`, `--force-with-lease*`, `git push origin main*`,
   `git push origin master*`, `gh pr merge*` deny (the `DENY` list of `pm/profile.py`).
4. `edit <memory store>/*` deny, with the store named by both its absolute and its resolved path
   (as `pm_settings` does).
5. `webfetch *` deny, `question *` deny.
6. Last, the profile's secret rules (`SECRET_RULES`: `read`/`edit` of `*.env` and `*.env.*`
   deny, `*.env.example` allow) and the `edit <skills>/*` deny, so a PM's `read .env` is refused
   by the permission layer as well as by deny-secrets.

`permission.hook("evaluate")` in coven's plugin: for any session in a PM tree, an `ask` becomes
`deny`, with the reason "a PM may not wait on a prompt: send it to the GM as a QUESTION or
PROD-STEP". The PM's skill text tells it to do exactly that.

pm-permissions: `tool.hook("execute.before")` runs `hooks/pm_permissions.py --pre-tool-use`
through `fail-closed.sh` for PM-tree sessions, with the same mapped payload deny-secrets gets
(`deny-secrets.ts` mapping) and the role variable set to `pm` in the child env. Exit 2 refuses the
call with the hook's message; any other failure refuses too. So `git push` of a feature branch,
`gh pr create` and the rest are decided by the same engine as on Claude Code.

The payload's `cwd`, for every hook the plugin runs, is the call's own working directory
(`input.workdir` for a shell call, resolved against the location directory when relative) when
it has one, and the location directory otherwise, so a PM
working in a worktree is judged in that worktree (pm-permissions resolves `--body-file` and the
branch against it).

Each hook gets its own timeout: deny-secrets keeps its existing 5 s and the server guard gets
5 s; pm-permissions 15 s and the production guard 30 s, as in `hooks/hooks.json`.

### Role in the shell env

`execute.before` for a shell call records `{sessionID, role, command, cwd}` in the shared queue,
keyed by `command + "\0" + cwd`, where `cwd` is `input.workdir` exactly as given (Task 0:
`create.before` sees the raw string, so a relative `sub` stays `sub`) or the location directory
when there is none. `create.before` takes the oldest entry with the same key
and sets coven's role variable in `event.env`. When there is no entry, or entries for the same
key have different roles, it sets the most restrictive role present (`pm`), and with no role at
all it sets none. Entries expire after 30 s. (PM call; the spike's single global variable is a
race under parallel calls.)

### Secret scrub

`create.before` deletes `OPENCODE_SERVER_PASSWORD`, `OPENCODE_PASSWORD`, the profile's key
variable (passed in the plugin options as `keyEnv`) and any `OPENCODE_*` name ending in
`_PASSWORD`, `_TOKEN` or `_KEY` from `event.env`, for every session, before anything else. A
scrub that throws refuses the command (the hook rethrows; Task 0 checks that a throw in
`create.before` stops the spawn, and the fallback if it does not is emptying `env` plus a refusal
in `execute.before`).

### The server guard (the guard twin)

A new Python hook, `hooks/opencode_server_guard.py`, run by the plugin next to deny-secrets for
every session, with the same payload and fail-closed rules. `fail-closed.sh` gets a refusing
fallback for it (with no usable `python3` it refuses, as for deny-secrets and the production
guard). pm-permissions keeps its Claude Code fallback (allow), so the plugin's self-check also
refuses to load when `python3` is missing or older than 3.12: the plugin is then not active, and
`coven opencode gm` stops before opening the TUI. It refuses:

- any `opencode` command but `opencode --version` and `opencode --help` (an allowlist: `api`,
  `session`, `run`, `db`, `export`, `import` and the rest all reach the session database or the
  server), and any command that names the server's `127.0.0.1:<port>` or `localhost:<port>` (port
  from the port file at call time) in any form the guard's command parser sees;
- any read, grep, glob or shell read of the serve folder (`<profile>/state/serve/`) and of
  `/proc/*/environ`, and `ps` with an option that prints other processes' env (`e`, `-E`, `eww`);
- any edit, write or patch, and any shell command that names the path, under the protected set
  (the profile config guard): the profile's `config/`, `plugin/`, `runtime/`, `data/` (the
  session database: a model with `sqlite3` could rewrite its own metadata or rules), `cache/`
  (Task 0 found the `opencode-pty` binary and Bun's compiled cache there, code OpenCode runs),
  `profile.json` and the serve folder, plus coven's own `hooks/` and `opencode/src/` under the
  plugin's `covenRoot` (the guards themselves). A PM's worktree of the coven repo is another
  path, so work on coven is not blocked. The profile's `tmp/` (where `TMPDIR` points) stays
  writable. Paths are judged after resolving `cwd`, `..`, `~`, `$HOME` and symbolic
  links where the tool names a path; for shell, as far as the command parser sees;
- `coven opencode setup`, `install`, `serve` and `gm` from any model shell, the GM's included:
  a config write hot-reloads the plugin, and `serve stop` needs no password. The user runs these
  in a terminal; after a server restart the GM resumes PMs with its tools.

`run_env` already sets `OPENCODE_DISABLE_PROJECT_CONFIG=1`, so a repo cannot add a plugin or
rules through its own OpenCode config; the design keeps it.

The refusal text says the server is coven's and the GM's tools are the way to reach PMs. The
guard reads its profile, serve and coven paths from its arguments and the port from the port
file; with no port file it still refuses the `opencode` commands.

The password is the boundary; the port and command refusals are a second layer. The scrub keeps
the password and the key out of every shell env, and the guard refuses every direct read of the
places they live (the password file and the server's env). This is best effort against a model
that follows its role (goal 5): a same-user script that builds the path at run time is not seen.

### Deny-only production guard (D7)

`tool.hook("execute.before")` runs `hooks/prod_guard.py --no-approvals` through `fail-closed.sh`
for PM-tree sessions, after pm-permissions, with the same mapped payload and the role variable
set to `pm` in the child env. The role matters: with it, the guard's restricted rules also refuse
a PM's writes to the memory store (including `systems/guard.json`) and to coven's config file,
which would otherwise let a PM turn the guard off from the shell. The new `--no-approvals` option
(parsed in `run()`, the only argument besides `--check` today) makes it treat every approval as
absent, so any call that reaches a production target is refused with the guard's usual text plus
"a PM on OpenCode never runs a production step: send it to the GM as a PROD-STEP".

It reads `systems/guard.json` from the store the same way the Claude Code hook does. With no
store, no `guard.json` or no targets it allows, as on Claude Code: on such a machine "a PM never
runs a production step" rests on the PM's text alone, and the docs say so. A spawn error, a
timeout, an unreadable `guard.json` or any exit other than 0 and 2 refuses the call. The GM
session does not run it in M2 (the user is at the TUI; M3 adds the GM side and approvals).

The docs say: until M3, a production step on OpenCode is always run by the GM, after the user's
yes.

### Turn-end notices (D4)

The plugin follows `ctx.event` (reusing `join/subscription.ts`). On
`session.execution.succeeded|failed|interrupted` for a session with no `parentID` and role `pm`,
only the plugin instance whose location is that session's directory acts, so one notice goes out
per turn. If the PM called `coven_send` to the GM during that turn (tracked per session from the
`session.execution.started` event to the end event), or the turn ended because the GM called
`coven_pm_stop`, or the PM still has a busy child session (its report will wake the PM, so the PM
has not stopped), no notice. A turn that ends `failed` always gets a notice, message or not;
after a message its text says `ended a turn (failed) after its message` instead.
Otherwise it queues into the GM `[coven notice] pm-<code> ended a turn (<outcome>) without a
message. Last line: <first 200 chars>`. (PM calls refining D4, listed under Decisions: a notice
for a PM that is still working costs a GM turn for nothing, and a failure must never be hidden
by an earlier message.)

A busy child cannot wait on an ask (the evaluate hook denies it), so it ends its turn and wakes
the PM. A child that is still busy after 30 minutes shows as `child busy 30m` in
`coven_pm_list`, so the GM can see a PM that has gone quiet.

After a server restart, in-flight turns are gone. `coven_pm_list` shows each PM's last outcome,
and the manager skill tells the GM to resume busy PMs after a restart.

### Manager and PM text (D5)

- `skills/manager` ships to OpenCode with an override: launch with `coven_pm_launch`, message with
  `coven_send`, status with `coven_pm_list`, resume and stop with the tools; idle notices are the
  `[coven notice]` lines.
- The OpenCode manager follows only the "No guard" flow: every `PROD-STEP` goes to the user, the
  GM runs it in its own session on a yes. The override says so, and `opencode/README.md` gets a
  "Hierarchy" section that says the guard-on flow comes with M3, and that with no `guard.json`
  in the store the deny-only guard has no targets. `docs/known-gaps.md` drops the hierarchy
  bullet, rewrites "Only the deny-secrets guard runs" (M2 adds the server guard, pm-permissions
  and the deny-only production guard), and keeps the guard-on flow and approvals there as M3;
  `tests/test_readme.py`, which pins the hierarchy bullet, changes with it. The `opencode/README.md`
  role list gains `project-manager`, and the CHANGELOG gets an entry.
- The PM agent's OpenCode text: `coven_send` instead of `SendMessage`; asks are refused, never
  waited on.

### Fake-model e2e

Two new e2e groups in `opencode/test/e2e/`, run by `npm run e2e`, offline in `unshare -rn`:

1. Hierarchy: `coven opencode serve start` in a scratch profile, a GM session (created over the
   API with `coven_gm_claim`), `coven_pm_launch` into a scratch repo, the PM's `coven_send DONE`
   wakes the GM, a PM turn without a message gives one notice, a PM-tree ask becomes a deny, the
   role variable reaches a PM child's shell, the scrub (password and key empty in a child shell),
   the server guard refusing `opencode api` and a read of the serve folder's `password`, the deny-only
   production guard refusing a command that matches a target in a scratch store's `guard.json`
   (a made-up host, never a real one), the same guard refusing a PM's shell write to the store's
   `systems/guard.json`, `coven_pm_stop`, and `serve stop` then `start` with the PM still listed
   and its rules still applied.
2. deny-secrets in a served session: the same read and cat probes as the runtime group's check
   4, against a PM session on the server.

## Error handling

- Every hook failure refuses (fail closed), as in M1. A plugin that does not load leaves sessions
  looking unguarded, so `coven opencode gm` checks `/api/plugin` shows `coven` active before it
  opens the TUI, and stops if not.
- `serve start` errors name the cause and the log path. A port in use is "port N in use"; a
  second server is "already running".
- A GM tool called by a non-GM session, a bad prefix or an unknown code is a tool error with the
  reason; nothing is sent.
- A PM whose session was deleted shows as `missing` in `coven_pm_list`.
- A PM's `coven_send` when no live session holds `role: gm` is a tool error ("no GM session;
  end the turn with the full message"), so the message stays in the PM's log, as on Claude Code.
  A notice that cannot be delivered is written to the serve folder's `log`.
- A waiting ask in a PM tree means the evaluate hook missed it: `serve status` and
  `coven_pm_list` show it as `ASK WAITING (bug)` with the request id, and `coven_pm_stop` clears
  it (the interrupt ends the waiting turn). The GM has no API access of its own.
- `coven_pm_launch` refuses when the repo is not a git checkout. An invalid code is refused with
  the rule from `pm/text.py` (`is_valid_code`).
- A role lookup that fails is treated as a PM tree (see "Roles and the GM session").
- With no usable `python3`, the server guard and the production guard refuse (`fail-closed.sh`
  fallbacks), the plugin does not load (its self-check), and doctor says why.

## Tests

- Python: `prod_guard.py --no-approvals` (a live approval does not lift a refusal; without the
  option it still does; with the role set it refuses a shell write to the store and to coven's
  config), `serve.py` (start/stop/status with a fake binary, stale files, second server, waiting
  asks), `opencode_server_guard.py` (each refusal and the allows next to it, with relative paths,
  `..`, `~`, `$HOME` and a symbolic link under `tmp/`, as `test_prod_guard_adversarial.py` does),
  the `fail-closed.sh` fallback for the new hook, the profile role and its model in the generated
  config, the generated PM agent and manager override, `doctor` checks.
- TypeScript: role resolution (including a lookup that throws), the shell queue, the scrub
  (including a scrub that throws), the evaluate hook, pm-permissions wiring with the call's
  `workdir`, per-hook timeouts, each GM tool against a fake `ctx` (each refused for a non-GM),
  the PM ruleset (a secrets-file read refused by the rules alone), the prefix contract test, the
  notice rules.
- e2e: the two groups above, run by hand and pasted in the PR, as in M1 (CI has no 2.3.2 binary).

## Left to the builder

- Module layout inside `opencode/src/pm/` and the split of PRs, inside the goals.
- A live fact that fails later: the smallest fix that keeps the goals and anti-goals, recorded as
  a ruling. An anti-goal that cannot be kept is a stop.

## CEO review (HOLD SCOPE)

Run on this spec on 2026-11-25. The mode is hold scope because the GM set the scope (D1 to D6);
any scope change below is a proposal for the user, not part of the design until they opt in.

Premise. The user asked for the hierarchy on OpenCode, and the spike shows it is mostly plugin
code on parts that exist. Doing nothing leaves OpenCode at one session. The approach builds on
what is already there: `pm/text.py` (prefixes, code rule), `hooks/pm_permissions.py` (the same
engine through a subprocess), `guard.ts` and `fail-closed.sh` (the fail-closed runner),
`join/subscription.ts` (the event follower), `runtime.run_env` and the fake-model harness. A
daemon (approach 3) was rejected again; the minimal version without the scrub and the server
guard was rejected because it breaks goal 5.

Fixed in the design during the review (bulletproofing, no scope change): `run` refuses while the
server runs (shared database); `doctor` checks the operations coven calls; a PM or a child can
never claim the GM role; `ps` env options are refused; the password is named as the boundary; no
notice for a turn the GM stopped; the error map in "Error handling" (a gone GM, a waiting ask in a
PM tree, worktree and code refusals, a port race).

Sections with no further findings: database and state (no new store beyond the profile's
serve folder; worktrees are left for the user, as on Claude Code), API contract (pinned to the
2.3.2 runtime, checked by doctor), performance (one event follower and one Python spawn per tool
call per PM; fine for the handful of PMs the user runs), UX (one entry point, `coven opencode
gm`).

Observability: the serve log, `serve status`, `coven_pm_list` with `ASK WAITING (bug)`, and the
notices. There is no alert outside the GM's session; the user sees state only through the GM
or `serve status`, as on Claude Code.

### Raised by the review

1. **Production reach with no guard.** As first written, nothing in M2 refused a PM's production
   commands: pm-permissions covers git and gh, not database clients or ssh, so "every PROD-STEP
   goes to the user" held only as far as the PM followed its text. The review proposed the
   deny-only production guard; the GM took it (D7), and it is in the design.
2. **For the user: if the TUI cannot attach to the server on 2.3.2.** Not seen in the spike. Then D1 needs a
   new answer: the GM runs as a plain lead TUI on the same profile with its own server process
   (which `run`'s refusal above rules out), or the GM moves to a Claude Code session after all.
   The plan checks this first and stops there if it fails. Settled by Task 0: the TUI attaches
   with `opencode --server URL`, so D1 holds as written.
3. **For the user: OS isolation for PM shells.** The scrub and the guards keep the password and
   the key away from a model that follows its role, but a same-user script that builds the path
   at run time can read the server's env (goal 5 is best effort, as the production guard is on
   Claude Code). Running PM-tree shells in their own PID and mount namespace (`unshare`, as the
   e2e does) would close it, at about 3 to 5 agent hours plus a check that `create.before` can
   wrap the command. M2 as written does not include it; the user can add it.

CEO REVIEW SUMMARY
- Mode: hold scope.
- Strongest challenges: production reach with no guard (item 1, now D7); the unchecked TUI
  attach (item 2); the password as the only real boundary, with the port refusal as a second
  layer.
- Recommended path: approve, then the plan as written.
- Accepted scope: goals 1 to 6 and the design above.
- Deferred: the guard-on flow and approvals (M3), the PM todo list, a Claude Code GM, `opencode
  service`.
- Not in scope: 1.x, aquelarre, two GMs, starting the server at login.
- Status: done with concerns (item 2 settled by Task 0; item 3 is the user's).

## Task 0 results

Run on 2026-11-25 against OpenCode 2.3.2, offline inside `unshare -rn` with the harness fake
model, in a scratch profile (`COVEN_OPENCODE_HOME`, `HOME`, the XDG folders and `TMPDIR` under
`/tmp`, the runtime copied in). A throwaway plugin `spike` sat next to `coven`. Nothing was
committed but this section; the real profile and `~/.config/opencode` have no file newer than
the run's start stamp, and no process of the run was left.

| Check | Result | Receipt |
|---|---|---|
| TUI attach | yes | No `attach` subcommand; the form is `opencode --server URL`. Under `script -qc`, `coven opencode run -- --server http://127.0.0.1:4096 --prompt ...` created a lead session on the served server (`GET /api/session` went from 6 to 7; agent `build`, no metadata) and the fake model answered. Without a password the TUI prints `requires a password; set OPENCODE_PASSWORD`; through `coven opencode run` only `OPENCODE_SERVER_PASSWORD` works, since `run_env` drops the other name. |
| Throw in `create.before` | yes, the spawn stops | `touch` of a marker left no file; the tool result was `{"status":"error","error":{"type":"unknown","message":"spike: refusing shell create ..."}}`. The control command ran. |
| Hook argument match | `command` equal; `cwd` is the raw `workdir` | No `workdir`: `cwd` is the location directory. Absolute `workdir`: equal. Relative `workdir` `sub`: `cwd` is the string `sub` (resolved later by OpenCode). `create.before` has `command, cwd, timeout, shell, env` and no session id. |
| Permissions across a restart | yes | Sessions with a `shell` deny on `*` and on `touch *` refused before and after a restart (SIGTERM to the port holder, new pid); `GET /api/session/{id}` still shows `permissions` and `metadata`. A control session ran shell. |
| Server process | no wrapper | `.bin/opencode` is a symlink to `@opencode/cli/bin/opencode.exe`, an ELF binary. The pid that holds the port is the one `coven opencode run -- serve` started (the CLI execs into it); `/proc/<pid>/exe` names that `opencode.exe` under the profile's runtime. |
| Plugin `session.create` and `list` | yes | `ctx.session.create({agent, location: {directory}, metadata, permissions, title})` made sessions in two other locations, one whose plugin instance had not loaded yet. `ctx.session.list({})` returns every location's sessions with `metadata` and `permissions`, plus a `cursor`. |
| Permissions schema | same as coven's | `permissions` is an array of `Permission.Rule` `{action: string, resource: string, effect: "allow" \| "deny" \| "ask"}`, all required, no other fields: the shape of `generate.py`'s `_rule`. |
| Shared plugin state | `globalThis` only | One server process evaluated the plugin three times (three module ids). A value set on `globalThis` from one location was seen from another; module-level values were not. |
| Code in `cache/` and `state/` | `cache/` yes, `state/` no | `cache/` holds the `opencode-pty` binary and Bun compiled-cache files; `state/` holds pty service and lock files, prompt history and tabs. A deeper look (`file`, `strings`) was refused by this session's guard and not routed around. |

What the results change:
- D1 holds as written; `coven opencode gm` uses `opencode --server URL` with
  `OPENCODE_SERVER_PASSWORD`.
- Shared state goes on `globalThis` (Roles and the GM session; plan Tasks 6 and 10).
- The shell queue key uses `workdir` exactly as given; the hook payload `cwd` resolves a
  relative `workdir` against the location directory (plan Tasks 6 and 7).
- No re-apply of PM rules after a restart is needed (plan Task 10 drops it).
- `cache/` joins the protected set (plan Task 4).
- Seen in passing: a session deny of `shell *` removes the shell tool from the model's list
  (`No tool named "shell" is currently available`), while a narrower deny gives
  `permission.rejected`. The PM ruleset denies only narrow patterns, so nothing changes.
- Not run: a SIGKILL restart (only SIGTERM), two PMs busy at once.
