# Spike: the GM/PM hierarchy (M2) on OpenCode 2.3.2

Date: 2026-11-25. A throwaway spike, no coven code changes. It checks the "### GM/PM hierarchy"
section and phase 2 ("Hierarchy") of [2026-11-22-coven-on-opencode.md](2026-11-22-coven-on-opencode.md)
against the OpenCode that coven installs (`@opencode/cli` 2.3.2, `opencode v2.3.2`).

## Verdict

**Go with changes.** Every piece the hierarchy needs exists on 2.3.2 and was seen working: one
`opencode serve` holding sessions in several directories, messages between sessions, a turn-end
signal on one event stream, unattended PM sessions, a role that reaches child sessions, coven's
plugin loaded per directory, and launch, list, stop and resume over a documented OpenAPI. The
one thing not seen is deny-secrets refusing a read inside a served session (item f); the code
path is the same as on `run`, where it was seen.

The changes are mostly in how the 1.x plan names things (paths, events, where the role and the
ruleset live). Two of them are new work the plan did not have: secrets reach every shell
command unless the plugin scrubs them, and a PM tree must turn asks into refusals in-process.
The research doc said 2.x has "no per-session env" and assumed one server per role. That is
wrong on 2.3.2: one server is enough.

## How it was run

- OpenCode 2.3.2 from coven's runtime (`~/.local/share/coven/opencode/runtime/2.3.2`).
- A separate coven profile from `COVEN_OPENCODE_HOME=/tmp/m2spike/profile coven opencode setup
  --preset litellm --base-url http://127.0.0.1:49999/v1 --key-env SPIKE_KEY --model lead=fake`, with
  `HOME`, the XDG folders and `TMPDIR` under `/tmp/m2spike`. The real coven profile and the
  user's own OpenCode were not touched (their files kept their earlier modification times).
- Two scratch git repos, `repos/gm-repo` and `repos/pm-repo`.
- Everything ran inside `unshare -rn` (only `lo`), entered with `nsenter`, against a scripted
  OpenAI-compatible fake model on 127.0.0.1 built on coven's own test harness
  (`opencode/test/harness/fake-model.ts`). Each prompt carries a `STEPS:` list of tool calls the
  fake plays back. No real model was used.
- Two spike-only additions in the scratch profile: agents `gm` and `pm` (the `pm` agent allows
  `*`, denies `git push*`, `webfetch` and `question`), and a throwaway plugin `spike` next to
  `coven`. The plugin logs the plugin context, adds a `spike_send` tool, and hooks
  `tool.hook("execute.before")`, `shell.hook("create.before")` and `permission.hook("evaluate")`.
- The server was started the way coven would: `coven opencode run -- serve --hostname 127.0.0.1
  --port 4096`, with `OPENCODE_SERVER_PASSWORD` set.
- The role variable is called `SPIKE_ROLE` here. coven's guard on the Claude session that ran the
  spike refuses any command naming the real variable. The mechanics are the same.

## Results

| Item | Result | Receipt |
|---|---|---|
| a. one server, two sessions in two directories, own agent and ruleset | yes | `POST /api/session` with `agent`, `location.directory`, `metadata` and `permissions`: GM `ses_df041b4e…` (agent `gm`, gm-repo), PM `ses_df041b39…` (agent `pm`, pm-repo, session rule `edit README.md deny`). In the PM, `pwd` printed `/tmp/m2spike/repos/pm-repo`, the edit returned `permission.rejected ... edit` and `git push origin main` returned `permission.rejected ... shell` |
| b. one session messages another, the reply comes back | yes | the GM's `spike_send` called `ctx.session.prompt({sessionID, text, delivery: "queue"})` into the PM (another directory), the PM's `spike_send` answered, and the GM woke on `[from ses_df041b39…] DONE m2/b: ...`, 0.6 s end to end |
| c. a turn-end notice seen on the event stream by a watcher | yes, different names | `GET /api/event` carries every location. 909 events, 0 `session.idle` (it is in the schema, never sent). The signal is `session.execution.succeeded` (also `failed`, `interrupted`). The watcher posted `[coven idle notice] ...` into the GM, which woke and answered |
| d. PM ruleset with no asks runs unattended; meeting an ask anyway | yes, with session rules and a hook | PM shell, edit and push calls ran with nothing waiting. A child under the PM with its own agent (`implementer`) fell back to the profile's `shell * ask` and waited (`permission.asked` event, `GET /api/session/{child}/permission`) until an API reply `{"decision":"once"}`. With the PM's rules on the session instead (children copy them), the child ran unattended. With `permission.hook("evaluate")` the plugin turned an ask into a deny for the PM and for a child of the PM: `ask turned into deny for pm tree`, the tool got `permission.rejected`. A control ask with no hook was still pending after 2 min 26 s and its session read `running` in `/api/session/active` |
| e. a session role reaching child sessions | yes, two ways | metadata `{"coven":{"role":"pm"}}` given at create is copied to every child (`role=pm via=own metadata` for the implementer child). `PUT /api/session/{id}/environment` gave the PM's shell `ROLE=[pm]` but is not copied to children (`CHILDROLE=[]`) and is lost on restart. The plugin's `shell.hook("create.before")` can edit `event.env`: it set the role from metadata for the PM and its child (`HOOK=[pm]`), also after a restart. That hook has no session id, so the spike paired it with the preceding `execute.before`; under parallel shell calls the pairing is a guess (change 5) |
| f. coven's plugin in served sessions; deny-secrets refusing an env-file read | plugin yes; served refusal not checked | `GET /api/plugin` lists `coven` `active`; `coven_ping` in the PM session returned `{"plugin":"coven","version":"0.1.0",...}`. The repo's offline e2e: `ok 4 - deny-secrets: read and cat of .env are refused` (5/5, on `opencode run --standalone`). The served-session probe was not run: the Claude session's own guard refuses the probe text, and it was not routed around. From the code, the path is the same (see below) |
| g. launch, list, resume and stop over the HTTP API | yes | see "The API a `coven pm` would sit on" |

### Why f holds for a served session (from code and logs)

`covenSetup` registers deny-secrets with `ctx.tool.hook("execute.before", ...)` and takes `cwd`
from `ctx.location.directory` (`opencode/src/coven.ts`). On a served 2.3.2 server, plugins are
set up once per location: the spike plugin logged one `setup` for gm-repo and one for pm-repo,
each with its own `location.directory`. Its `execute.before` hook, registered through the same
`ctx.tool.hook` call, fired for the PM session and for the PM's child, with `location` =
pm-repo. So coven's deny-secrets hook is set up for every directory the server serves and sees
each session's calls with that session's directory as `cwd`. What was not seen is the refusal
text itself in a served session.

### The API a `coven pm` would sit on

From the server's own document (`GET /openapi.json`, OpenAPI 3.1, 458 KB). The 1.x names in the
research doc (`/session`, `prompt_async`, `/global/event`, `/session/status`) do not exist on
2.x; everything is under `/api`.

| Need | 2.3.2 call | Seen |
|---|---|---|
| launch | `POST /api/session` `{agent, location: {directory}, metadata, permissions, title}`, then `POST /api/session/{id}/prompt {text}` | yes; prompt returns at once ("durably admit one session input and schedule agent-loop execution") |
| message a PM, or the GM | `POST /api/session/{id}/prompt {text, delivery: "queue"\|"steer", resume}`; from a plugin `ctx.session.prompt` | yes; no `directory` needed, the session id carries its location |
| list | `GET /api/session?archived=&parentID=&directory=&search=` | yes; children show in the list too, filter by `parentID`. `opencode session list --server URL` lists only the current project |
| busy or idle | `GET /api/session/active` (busy ones only); `time.busy`, `time.idle`, `outcome` on `GET /api/session/{id}` | yes; a session waiting on an ask shows as `running` |
| waiting on a prompt | `GET /api/session/{id}/permission`, `GET /api/permission/request?location=`, event `permission.asked` | yes |
| answer an ask | `POST /api/session/{id}/permission/{requestID}/reply {decision: once\|always\|reject}` | yes |
| last messages | `GET /api/session/{id}/message` | yes |
| change role or rules | `PATCH /api/session/{id} {metadata, permissions, archived}` | yes |
| stop | `POST /api/session/{id}/interrupt` | yes; `{"interrupted":true}`, outcome `interrupted`, the running `sleep 120` was killed |
| park and resume | `PATCH {archived: true}`, later a new prompt | yes; sessions, metadata and outcomes survived a real server restart, and an archived PM answered a new prompt |
| delete | `DELETE /api/session/{id}` (with children) | not run |
| per-session env | `PUT /api/session/{id}/environment {variables}` | yes; replaces the whole env (HOME empty, default PATH), not copied to children, lost on restart |
| PM task list | none: no todo route in the document | no |
| CLI client | `opencode api --server URL <operationId>`, `opencode session list --server URL` with `OPENCODE_SERVER_PASSWORD` | yes; without it: "requires a password; set OPENCODE_PASSWORD" |
| TUI as the GM | `opencode --server URL` | not run (no TTY) |

## What 2.x changes against the 1.x plan

1. **Names.** `/api/...` instead of the 1.x routes, `POST /api/session/{id}/prompt` instead of
   `prompt_async`, `GET /api/event` instead of `/global/event`, and `session.execution.succeeded`
   (or `failed` / `interrupted`) instead of `session.idle`.
2. **No directory juggling.** On 1.x every cross-session call needed the target's directory. On
   2.x the session id is enough, from HTTP and from a plugin. Plugins still run once per location,
   so per-location state is per PM worktree.
3. **The PM ruleset lives on the session, not only on the agent.** Agent rules do not reach a
   child that runs another agent (the implementer fell back to the profile's `shell ask`).
   Session `permissions` are copied into each child at creation. So `coven pm launch` puts the
   PM rules on the session, and the role agents keep their own narrowing rules.
4. **Asks turn into refusals in-process.** On 1.x the plan was "name every key that asks". On 2.3.2
   `permission.hook("evaluate")` fires (the research doc had seen it register, not fire) and can
   set `effect = "deny"`. The spike plugin did that for any session whose role is `pm`, and the
   refusal reached the model as `permission.rejected`; coven's plugin would do the same. The same hook is the natural home for
   pm-permissions decisions, next to `execute.before`.
5. **The role comes from metadata, and a plugin hook puts it into the shell env.** `metadata`
   at create is copied to every child and survives restarts. `shell.hook("create.before")` has no
   session id (`command, cwd, timeout, shell, env`), so the spike paired it with the preceding
   `execute.before` of the same shell call. That pairing is a guess under parallel shell calls
   and needs a firmer key (for example the command and cwd) or an upstream session id.
   `PUT environment` is not the answer on its own: it is lost on restart and not inherited.
6. **Secrets reach every shell command.** A child's shell printed `PW=[set] KEY=[set]`: the
   server password and the provider key variable are in the env of every shell command not
   replaced by `PUT environment`. With the password, a model can drive the whole API (reply to
   its own asks, change its own rules and role, prompt the GM). The plugin's `create.before`
   hook removed both (`PW=[] KEY=[]`). coven's single-session profile builds its env the same
   way (`run_env` keeps the key variable), so its shell commands should see the key too; that
   was not run in the TUI.
7. **The watcher.** A child copies the PM's metadata, so a watcher keyed on `role` mistakes
   children for PMs (the spike's did). Key on "no `parentID`". A PM's turn also ends while its
   children still run (it is woken later by their `<subagent>` reports), so a notice on every
   turn end is noisy. The watcher can live in the plugin (`ctx.event.subscribe` sees every
   session) instead of a separate process, but only one location's instance should act on it.
8. **Server lifecycle.** coven has to own the real server pid. Killing a wrapper left the first
   server running, and the second one failed with `Is port 4096 in use?`. Sessions persist in the
   profile's database. A restart drops in-flight turns and pending asks (the control ask was gone
   and the GM idle afterwards), so after a restart the GM has to re-prompt busy PMs. 2.x also has
   its own `opencode service start|stop|status|get|set` (with settable env); not tried.
9. **Config hot reload.** Writing the profile's `opencode.jsonc` loaded the new plugin into the
   running server with no restart (`plugin.updated`), and `POST /api/location/reload` reloaded
   plugin code. Handy for setup, and a reason to guard the profile's config folder from PMs.

## Re-estimate at agent speed

Agent wall-clock time with an implementer and a reviewer per piece. These are judgement, with
one data point behind them: a one-file generator change in coven this week took about 40
minutes with review and CI. The human gates are separate: the brainstorm on the questions
below, the spec review, and PR reviews.

| Piece | Agent time |
|---|---|
| Server lifecycle: `coven opencode serve` start, stop, health, password, a port and pid file, refuse a second server | 1-2 h |
| `coven pm launch`: session with agent, worktree location, `metadata.coven`, PM `permissions`, first prompt | 1-2 h |
| `coven pm status/health/resume/stop` on the API (replaces about 900 lines of transcript parsing) | 3-4 h |
| Plugin: `coven_send` and `coven_sessions` tools (prefix check from `pm/text.py`), role from metadata, env injection and secret scrub in `create.before`, ask-to-deny in `permission.hook` | 3-5 h |
| pm-permissions in `execute.before` / `evaluate` (the Python engine behind the adapter) | 3-4 h |
| Turn-end watcher inside the plugin, one instance acting (it can reuse the `ctx.event.subscribe` loop in `opencode/src/join/subscription.ts`) | 1-2 h |
| Guard twin: refuse model access to the server API (`opencode api`, curl to its port, the password) | 1-2 h |
| Fake-model e2e for the hierarchy (two sessions, send and reply, notice, ask-to-deny, role in a child, restart, deny-secrets in a served session, two PMs with parallel shell calls) | 2-3 h |
| PM task list (phase 2 of the plan): 2.3.2 has no todo route, so `coven pm status` would read the todo tool's parts from the session messages, or drop it | 1 h, or out of scope |
| `manager` and SDD skill text for `coven_send` and `coven pm ...` | 1-2 h |
| Whole-branch review, docs, CI | 1-2 h |
| **Total** | **17-29 h** of agent time, about 3-4 days of PM runs with the human gates |

The research doc put phase 2 at 50-80 hours at human speed.

## Open questions for the user

1. **Who is the GM?** An OpenCode TUI attached to coven's server (`opencode --server`), or this
   Claude Code GM driving OpenCode PMs over HTTP, or both?
2. **Server lifecycle.** coven starts and stops its own `opencode serve` (on demand from
   `coven pm launch`, or as a user service), or it uses 2.x's `opencode service`?
3. **A PM that meets an ask.** Refuse it and tell the GM (fail fast, as `run` does), or hold it and
   let the GM answer over the API? Holding it means a GM can approve a PM's prompt, which the
   Claude Code design never allows.
4. **Turn-end notices.** On every PM turn end (noisy while children run), or only when a PM ends a
   turn without having sent the GM a message?
5. **The production guard.** PMs run shell unattended. Can M2 ship before the guard port (M3), or
   do they go together?
6. **Models.** Which gateway model runs the GM, the PMs and the role agents?

## Not checked

- Anything with a real model: tool choice, whether a model uses `coven_send` and its prefixes
  well, long PM runs, compaction.
- deny-secrets refusing a read in a served session (see f).
- The TUI attached to the server, and `opencode service`.
- Two PMs busy at once, and the `execute.before` / `create.before` pairing under parallel shell
  calls. This is a go condition for the role-in-env design.
- Whether session `permissions` survive a restart. After the restart the PM's shell ran
  unattended, but its `pm` agent allows shell too, so that run does not tell.
- `grep` or other tools reading the server's own environment through `/proc/self/environ`. The
  `read` tool failed only because the file is binary ("Cannot read binary file"), not because of
  a rule.
- How long an ask waits past 2.5 minutes, `DELETE /api/session`, a server crash in mid-turn, and
  `metadata` or `permissions` changed by a model with the password.
- OpenCode 1.x: nothing here was run on it.
