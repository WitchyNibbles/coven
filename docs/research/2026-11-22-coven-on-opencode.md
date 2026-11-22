# coven on OpenCode: what maps, what has to be rebuilt

Date: 2026-11-22. Analysis only: no coven code changes.

coven here is origin/main d6bec72. The OpenCode versions are **1.x** = `opencode-ai` 1.19.6
(2026-11-17, the latest 1.x) and **2.x** = `@opencode/cli` 2.3.2 (2026-11-18, the latest 2.x).
The author's work OpenCode setup pins 1.18.34. Facts below were checked on 1.19.6 unless they say
otherwise; 1.18.34 was not checked separately.

Sources:
- OpenCode source at tags `v1.19.6` and `v2.3.2` (https://github.com/anomalyco/opencode), including
  the docs that live in the tree (`packages/web/src/content/docs/` for 1.x,
  `services/site/src/docs/content/` for 2.x, which is what https://opencode.ai/docs serves). Paths
  below are prefixed `v1:` or `v2:`.
- A scratch trial of both versions with HOME and XDG under `/tmp`, no provider keys (one prompt
  did reach OpenCode's free default model by accident; see "Not checked").
- The OpenCode adapter of the author's earlier dotfiles tool (private; targeted OpenCode 1.19.0).
- The author's work OpenCode harness on 1.18.34 (6 agents, 10 plugins, 11 custom tools,
  15 commands, 12 skills, a fake-model test suite of about a thousand tests). It is the best
  evidence that the plugin approach below works.
- coven's own code, line counts from `wc -l`.

## Short version

- **Maps directly:** skills (OpenCode has native `SKILL.md` and even reads `~/.claude/skills`),
  agents (markdown plus frontmatter), commands, AGENTS.md instructions, allow/deny permissions,
  MCP, a model per agent. The memory store, the approvals store, most of the code standard and the
  Python rule engines (deny-secrets, pm-permissions, prod-guard, `shell_words`) carry over behind a
  thin adapter, with their Claude-specific edges rewritten.
- **Has to be rebuilt:** every hook (OpenCode plugins are JS/TS, so a TS shim calls coven's
  Python), the GM/PM hierarchy (no `claude --bg`, no `SendMessage`; built on OpenCode's HTTP
  server instead), `coven pm status/health/resume` (on the HTTP API instead of Claude's
  transcripts), the aquelarre band and pane (a TUI plugin), setup and doctor.
- **No equivalent:** output styles; a `PermissionRequest` hook on 1.x (`permission.ask` is
  declared but never called); a detached background session on 1.x; the 2.x subagent join
  (subagents only run in the background there); a process env per session on 2.x.
- **Biggest blockers:** (1) model access: 1.x cannot use a Claude subscription, so coven there
  runs on an API key or another provider; (2) 2.x subagents never block and there is no join
  (#64073, #65316 open), which breaks the subagent-driven-development loop (a plugin join fixes
  this, see the follow-up section near the end); (3) on 1.x no hook can
  answer a permission prompt, so a PM must never meet an `ask`, and a plugin that fails to load
  fails open; (4) aquelarre is a full rewrite on an undocumented 1.x TUI API.
- **Size:** about 185-300 hours for everything on 1.x, 150-240 without aquelarre. A spike of
  10-14 hours comes first and can kill the plan cheaply.
- **Recommendation:** build on 1.x (1.19.x, also to be tested on 1.18.34), with the Python core
  behind a per-version adapter so a 2.x adapter later costs about 40-60 hours. Build first: the
  spike, then single-session coven (skills, agents, routing, memory, deny-secrets, SDD). The
  hierarchy comes second, aquelarre last.

## Component map

Size is the rough effort to reach the same behaviour on 1.x, including tests.

| coven part | Claude Code mechanism | OpenCode equivalent | Verdict | Hours |
|---|---|---|---|---|
| Hook adapter (new) | `hooks.json`, `fail-closed.sh`, `launch.py` | one TS plugin: translate tool calls to the payload the Python reads, run it as a subprocess with a deadline, throw on refusal; fake-model test harness | new foundation | 16-24 |
| Skills (12, 2,183 lines) | plugin skills, `coven:` namespace, `${CLAUDE_SKILL_DIR}` | native `SKILL.md` via the `skill` tool, from `.opencode/skills`, `~/.config/opencode/skills`, `.claude/skills`, `~/.claude/skills`, `.agents/skills` (both) | maps; tool references need rewriting | 12-20 |
| superpowers dependency | plugin `dependencies` | none; install or vendor its skills as files | rebuild install | in setup |
| Routing text (`session_context.py`, `instructions/routing.md`) | SessionStart `additionalContext` | `instructions` config key or `AGENTS.md` (both) | maps | 1-2 |
| Memory context (`memory_context.py`) | SessionStart hook, project key from `cwd` | `experimental.chat.system.transform` (1.x) / `session.hook("context")` (2.x) calling the Python per session, cached | rebuild glue, logic reused | 4-6 |
| Agents (5) | agent markdown: `model`, `effort`, `disallowedTools` | agent markdown: `mode: subagent`, `model`, `permission` (deny removes a tool); 2.x moves sampling to model variants | maps | 3-5 |
| Subagent dispatch (SDD, code review) | `Agent` tool, `subagent_type`, blocks | 1.x `task` tool blocks by default; 2.x `subagent` never blocks, no join | 1.x maps; 2.x blocker | in skills |
| PM task list | `TaskCreate/TaskUpdate`, files under `~/.claude/tasks` | 1.x `todowrite`, readable at `GET /session/{id}/todo`; 2.x off by default, no endpoint | 1.x maps | 2-4 |
| deny-secrets hook | PreToolUse, exit 2 blocks | `tool.execute.before`, throw blocks (1.x); `tool.hook("execute.before")` (2.x); plus `read` deny rules. Must cover OpenCode's own tools: `read`/`edit`/`write` take `filePath`, and `apply_patch` carries multi-file patch text | rebuild glue, logic reused | 8-14 |
| pm-permissions hook | PreToolUse + PermissionRequest decision JSON | `tool.execute.before` only; PM ruleset with no `ask` rules; config `deny` rules as the backstop | rebuild glue, logic reused | 6-10 |
| Session role (`COVEN_SESSION_ROLE`) | env from `--settings`, inherited by every child process | 1.x: per session in `shell.env` (input has `sessionID`), resolved through the `parentID` chain so a PM's implementer and reviewer keep the role; 2.x: no per-session env (see Hooks) | rebuild | 4-8 |
| Production guard + approvals | PreToolUse gate on every tool, approvals in the store | `tool.execute.before` on every tool; approvals unchanged; Claude-specific rules (zones, starting `claude`, MCP names) rewritten | rebuild glue, most logic reused | 20-34 |
| GM/PM hierarchy | `claude --bg`, `SendMessage`, `notify_when_idle`, `crossSessionInbound` | one long-running `opencode serve`; PMs are sessions created over HTTP with their own agent, directory and permission rules; messages with `prompt_async`; idle notices from a coven watcher on `/global/event` | rebuild | 30-50 |
| `coven pm status/health/resume` (`pm/rows.py`, `transcript.py`, `report.py`, `claude_version.py`) | `claude agents --json --all`, transcript JSONL (both undocumented) | `GET /session`, `/session/status`, `/permission`, `/session/{id}/message`, `/session/{id}/todo` (documented OpenAPI) | rebuild, simpler | 12-20 |
| aquelarre (1,265 lines + 4,303 lines of tests) | Claude Code mod API: band above the prompt, pane, toasts | TUI plugin with slots and toasts: in the 1.x source but undocumented; documented in 2.x | rebuild | 35-60 |
| Code standard (`standard/`, `.coven/check.py`) | `check_mod.py` (`claude plugin validate/test`), mod detection in `check.py`/`check_repo.py`, Claude Code install in the CI workflow | none needed for Python and TypeScript repos | reuse; mod parts skipped or replaced | 4-8 |
| Output style | `~/.claude/output-styles` (lives in the dotfiles tool, not coven) | none in either version; agent prompt or instructions | no equivalent; prompt text | 1-2 |
| Setup, doctor, shim, install (`setup.py`, `settings.py`, `doctor.py`, `paths.py`, `bin/coven`, `plugin.json` with its superpowers and aquelarre dependencies) | `claude plugin install`, `~/.claude/settings.json` | files under `~/.config/opencode` or a separate config dir; `opencode debug config` to check; a plugin-loaded canary | rebuild | 12-20 |

## What carries over

Pure stdlib Python with no Claude Code calls:

- `hooks/shell_words.py` (1,182 lines), `safe_json.py`, `hook_support.py`, `hooklog.py`,
  `guard_config.py`, `project_key.py`.
- `src/coven/memory/` store and entries code, `approve.py` (623), `config.py`, `guard.py`
  (`coven guard explain`), `pm/text.py` (the message prefixes).
- `standard/` (3,875 lines) for Python and TypeScript repos, and the GitHub Actions workflow.
- The skill bodies that only talk about method: ceo-review, clean-code, office-hours,
  project-standard, shared-memory, handoff, grill-me, systems-map.

Reused with Claude-specific edges rewritten:

- `deny_secrets.py` (384), `pm_permissions.py` (2,258) and `prod_guard.py` (3,032). Their input
  (Claude's `tool_name`, `tool_input`, `cwd`) and output (exit 2 or decision JSON) are
  Claude-shaped. `prod_guard.py` also has Claude-specific rules: zones under `CLAUDE_CONFIG_DIR`
  and the plugin root, refusing `claude` session starts, `claude --version` special cases, and MCP
  names. OpenCode names MCP tools `<server>_<tool>`, which cannot always be mapped back to
  `mcp__server__tool` when names hold underscores, so approvals would name OpenCode's spelling.
- `memory/adopt.py` (1,032 lines) imports `coven.settings`, which edits Claude's `settings.json`.
- `standard/check.py` and `check_repo.py` detect Claude mods and need `claude` on PATH for them;
  the workflow installs Claude Code when a plugin manifest exists.

The adapter idea: one TS plugin per OpenCode major that translates each tool call into the payload
the Python already reads (`tool_name` and `tool_input` with Claude's names: `Bash`/`command`,
`Read`/`file_path` ...), runs the hook as a subprocess and turns a refusal into a thrown error.
`apply_patch` needs its own translation: one patch can write several files. The Python tests keep
working; only the adapter and its tests are new per OpenCode version.

## Part by part

### Skills and routing

Maps on both lines. OpenCode loads `SKILL.md` natively and lists the skills in the `skill` tool's
description; the model loads one with `skill({name})` (1.x) or `skill({id})` (2.x). Both read
`~/.claude/skills` and `.claude/skills` (checked in the trial), but coven ships its skills inside a
plugin, so install them as files under `~/.config/opencode/skills/` or point `skills.paths` at
them.

What changes:
- Names: the folders are already `manager`, `clean-code` and so on, which fit 1.x's name rule
  (`^[a-z0-9]+(-[a-z0-9]+)*$`). `coven:` is Claude's plugin namespace and does not exist in
  OpenCode; a `coven-` prefix would avoid collisions with other skills, and every
  cross-reference in the text would follow it.
- Tool references: `manager` (412 lines) and `subagent-driven-development` (592 plus 519 lines of
  prompts) name `Agent`, `subagent_type`, `SendMessage`, `notify_when_idle`, `claude --bg`,
  `claude agents`, `claude stop/attach/logs`. These become `task`, the coven tools (below) and
  `coven pm ...`.
- `${CLAUDE_SKILL_DIR}`: no such variable, but not needed. The 1.x `skill` tool output appends the
  skill's base directory and says relative paths resolve against it (`v1:tool/skill.ts`); the 2.x
  docs say the same. The SDD scripts can be named relative to the skill.
- `grill-me` uses `disable-model-invocation`; 2.x honours that key, 1.x does not. It is
  user-started anyway, so it becomes a command (`.opencode/commands/grill-me.md`), and so does
  `handoff` (its `argument-hint` maps to `$ARGUMENTS`).
- Routing: `routing.md` is static, so the `instructions` config key is enough. The text names
  skills and tools, which change with the names above.

A trap from the trial: a shell started by Claude Code exports `OPENCODE_DISABLE_CLAUDE_CODE=1`,
which hides `.claude` skills from OpenCode. Tests run from a Claude session must unset it.

### Agents and subagents

The five role agents map to markdown agents with `mode: subagent`. Read-only roles use
`permission: {edit: deny}`; deny removes the tool. Models: `model: provider/model` per agent.
`effort` has no direct key on 1.x (the work harness passes `reasoningEffort` through as a provider
option); on 2.x it becomes a model variant (`provider/model#high`) and the agent's
`temperature`/`top_p` are no longer sent (v2 docs `agents.mdx`, "Request").

Subagents:
- 1.x: the `task` tool waits for the child and returns its result inline
  (v1:`packages/opencode/src/tool/task.ts:320-345`). Background children need
  `OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS=true` (`task.ts:97-102`). This matches how SDD works
  today.
- 2.x (2.3.0 on): "Subagents always run in the background", the call returns at once and the child
  reports with a `<subagent>` message when it goes idle (v2 docs `tools.mdx:219`). The model has no
  wait or join tool. Open issues: #64073 (restore foreground subagents, open, 24 comments) and
  #65316 (the parent reports done while a subagent still works, open, seen on 2.3.2). 2.2.7 is
  the last 2.x with a blocking option.
- Both: nesting is off by default (depth 1). A PM is a session, not a subagent, so PM -> implementer
  is one level and works.

### Hooks

OpenCode plugins are JS/TS modules (1.x: every export is an async function that returns a hooks
object; 2.x: `export default Plugin.define({id, setup(ctx)})`). The 1.x plugins do not load on 2.x
at all: the work harness saw all 10 of its plugins fail on 2.3.2 with only a log warning. One
package can carry both shapes on 1.18.29 and later (v2 docs `build/plugins/migrate-v1.mdx`).

| coven hook | 1.x | 2.x |
|---|---|---|
| PreToolUse, exit 2 blocks | `tool.execute.before`, throw an Error; runs before the tool's own permission check (v1:`session/tools.ts:102-131`) | `ctx.tool.hook("execute.before")`, throw; or `ctx.permission.hook("evaluate")` with `effect = "deny"` |
| PermissionRequest, answer an ask | none: `permission.ask` is in the type but has no call site in 1.19.6 (checked with grep). Workaround: watch `permission.asked` events and call `POST /permission/{id}/reply`, which is what `opencode run` does (v1:`cli/cmd/run.ts:791-810`) | `ctx.permission.hook("evaluate")` can set allow, ask or deny before a prompt shows (documented; registered in the trial, not seen firing) |
| SessionStart `additionalContext` | `experimental.chat.system.transform` (per model request) plus the `session.created` event | `ctx.session.hook("context")` or `ctx.session.instructions.entry.put` |
| Env per session | `shell.env`, whose input carries `sessionID` (v1:`packages/plugin/src/index.ts:270-273`; triggered from the bash tool and the `!` shell) | none: `ctx.shell.hook("create.before")` gets only `command, cwd, timeout, shell, env`, no session (v2:`packages/plugin/src/effect/shell.ts:3-9`). A role per session needs one server per role, or another mechanism |

The work harness is evidence that this works on 1.18.34: its guard plugin (2,652 lines) records
each session's agent from `chat.message` and judges bash in `tool.execute.before`, throwing to
block; a second plugin strips env vars in `shell.env` and rewrites output in
`tool.execute.after`. Its own limit applies to coven too: it sees only the words of a command, not
what a script does.

**Fail-open risk.** coven's hooks fail closed on purpose (`fail-closed.sh`). In OpenCode a plugin
that does not load leaves only a log warning, and then nothing guards the tool calls. Two layers
cover it: config `deny` rules for the dangerous commands (they hold without the plugin), and a
canary: the plugin registers a `coven_ping` tool, and `coven pm launch` and `coven doctor` refuse
to go on when it is missing.

Test gap: `opencode debug agent <name> --tool` resolves permissions without a model but does not
run plugin hooks. The work harness tests hooks with `opencode run` against a scripted
OpenAI-compatible fake model; coven would use the same pattern.

### Permissions

Allow/ask/deny with glob maps per tool, last match wins, global plus per agent (1.x `permission`
map; 2.x an ordered `permissions` array, with `bash` -> `shell`, `task` -> `subagent`, `write` and
`patch` -> `edit`). Read-deny for `.env` and key folders works, with two traps the dotfiles tool found:

- `read` patterns match worktree-relative paths, so a rule like `~/.ssh/**` never matches a read;
  the dotfiles tool added `external_directory` denies instead. coven's deny-secrets hook covers this anyway.
- A legacy `~/.config/opencode/config.json` merges under `opencode.json` and can undo deny rules
  (seen by the dotfiles tool). The doctor has to check it.

Defaults: `.env` reads ask by default on both (the 1.x docs say deny; 1.19.6 asks).
`external_directory` asks on 1.x and is allowed on 2.x (open bug #65290: an ask rule there is
never prompted). 1.x also asks on `doom_loop` (v1:`agent/agent.ts:121-126`). `question` is denied
for subagents by default.

An `ask` in a headless `opencode run` is rejected automatically (both). Over the HTTP API with no
client answering, it waits forever: the same hang as an unanswered prompt in `claude --bg`. On 1.x
no hook can answer it, so the PM ruleset has to name every key that asks by default:

- `bash`: allow, with `deny` rules for push to protected branches, force push and merge (the
  backstop that holds if the plugin fails), and pm-permissions in `tool.execute.before` for the
  rest.
- `external_directory`: allow for the memory store path (a PM reads it) and the PM's worktree
  root; deny for everything else.
- `read`: deny for `.env*`, allow otherwise (deny-secrets adds the key folders).
- `doom_loop`: deny (the loop stops instead of waiting).
- `edit`: deny under the memory store; allow in the worktree.

### GM/PM hierarchy

There is no `claude --bg` on 1.x. What 1.x has is a documented HTTP server, and the hierarchy maps
onto it:

- **One server per user:** `opencode serve` runs as a long-lived process (a systemd user unit or
  similar; coven would own its lifecycle). The GM is the TUI attached to it (`opencode attach
  <url>`).
- **PM launch:** `POST /session` with the PM agent, the worktree as `directory` and a `permission`
  ruleset, then `POST /session/{id}/prompt_async` with the brief. `coven pm launch` would run that
  instead of printing the `claude --bg` line.
- **Directories:** on 1.x, plugin state and `GET /event` are per directory instance
  (v1:`plugin/index.ts:134`), and PMs run in worktrees, which are other directories. So the GM's
  plugin does not see PM events, and every cross-session call must pass the target's `directory`.
  Only `GET /global/event` carries events from every directory, with a `directory` field.
- **Messages:** coven adds custom tools, for example `coven_send` (to a session by name, checking
  the `QUESTION`/`DONE`/... prefixes from `pm/text.py`) and `coven_sessions` (list). They call
  `prompt_async` with the target's directory.
- **Idle notices:** a small coven watcher process subscribes to `/global/event`, and on
  `session.idle` of a PM session it posts a short notice into the GM session.
- **Role:** `COVEN_SESSION_ROLE` cannot come from the process env when all sessions share one
  server. coven tags the PM session at creation (`POST /session` accepts `metadata`), and the
  plugin resolves the role of any session through its `parentID` chain, so a PM's implementer and
  reviewer get the role too. `shell.env` sets it for bash commands (the store's pre-commit refusal
  reads it), and the adapter passes it to the Python hooks. The alternative is one `opencode serve`
  per PM with its own env and config: it isolates more, and it is the only option on 2.x, but it
  multiplies processes and ports.
- **Auth:** the 1.x server has no auth unless `OPENCODE_SERVER_PASSWORD` is set. With PMs that can
  be prompted over HTTP, coven must set it; there is no `crossSessionInbound`-style gate.

On 2.x the shared background service (`opencode service start|stop`) is close to `claude --bg`
out of the box, and `ctx.session.prompt` with `delivery: queue` is a native message. But
`OPENCODE_CONFIG*` env vars only apply with `--standalone`, there is no per-session env, and the
subagent join is missing, so the PM's own work loop is the problem there, not the hierarchy.

### `coven pm status`, health and resume

Today these parse `claude agents --json --all` and session JSONL, both undocumented
(`pm/rows.py`, `pm/transcript.py`, `pm/report.py`, about 900 lines). On OpenCode the same facts
come from the documented OpenAPI: `GET /session` (list, titles, parent), `GET /session/status`
(idle, busy, retry), `GET /permission` (pending asks, which is "waiting on a prompt"), and
`GET /session/{id}/message` for the last messages in and out. Sessions persist in SQLite
(`~/.local/share/opencode/opencode.db`), so resume is "post a new prompt into the same session";
`coven pm resume` becomes simpler. Rewrite, but onto firmer ground.

### Production guard and approvals

The guard's engine, approval files and `coven approve` stay. The OpenCode adapter calls the gate
on every tool in `tool.execute.before`, including `apply_patch` and MCP tools (spelled
`<server>_<tool>`). The guard's "zones" need OpenCode's equivalents: `opencode.json` and
`opencode.jsonc`, the legacy `config.json`, `.opencode/plugins`, `~/.config/opencode/plugins`, and
`tui.json`. The rules that stop a PM from starting `claude` sessions need an `opencode` twin
(`opencode run`, `opencode serve`, and HTTP calls to the server).

### Code standard

Harness-agnostic for Python and TypeScript repos: ruff, mypy, pytest, coverage, tsconfig, eslint,
run by `.coven/check.py` locally and in CI. The Claude parts are the mod kind: `check_mod.py`
(`claude plugin validate --strict`, `claude plugin test`), mod detection in `check.py` and
`check_repo.py`, and the CI step that installs Claude Code. For an OpenCode plugin repo they would
be replaced by a load check (`opencode debug config` plus a plugin smoke run with the fake model).

### Output style

No output styles in either version (grep of both source and docs trees). The voice goes into the
GM agent's prompt or an instructions file. The WitchyNibbles style lives in the dotfiles tool, not in
coven; that tool already appends the same contract as plain instructions for OpenCode.

### aquelarre

The mod uses Claude Code's early-access mod API (`on('ui.render', {component: 'AbovePrompt'})`,
panes, toasts, `$.process.run`) and reads Claude's internal `jobs/` and `tasks/` folders. None of
it carries over. OpenCode equivalents:

- 1.x: TUI plugins exist in the source (`@opencode-ai/plugin/tui`, slots such as
  `session_prompt_right`, `sidebar_content`, `app_bottom`, `ui.toast`;
  v1:`packages/plugin/src/tui.ts:455-636`) but are not in the 1.x web docs and were not run in the
  trial. A toast can also come over HTTP (`POST /tui/show-toast`).
- 2.x: documented `./tui` plugin part with `prompt.footer.status`, `session.panel` and
  `ui.toast.show` (v2 docs `build/plugins/cli.mdx`).

The data side gets easier: the HTTP API replaces Claude's folders, and the cross-window status
cache may no longer be needed. The UI, the en/es text and the settings are a full rewrite on an
API that is undocumented on 1.x. This is the part most likely to change under a version bump.

## Lessons from the dotfiles tool and the work harness

- The dotfiles tool installed roles and skills into OpenCode 1.19.0 with no hooks at all, so its only guard
  was deny rules, which missed `grep`/`glob` and home-relative paths. Hooks are not optional for
  coven's guarantees.
- The dotfiles tool's global OpenCode files collided with the work harness on the work machine and were
  removed there. coven on OpenCode must not write globals where
  another OpenCode setup lives; it needs its own config dir or project-level files.
- The work harness shows that plugins, custom tools, per-agent permissions and fake-model tests
  all work on 1.18.34, and that a 1.x bump can break subtle behaviour (on 1.19.6 one approved
  prompt ran a statement several times, so the harness stayed on 1.18.34).
- The harness estimated its own 2.x port at 80-135 hours and decided to stay on 1.x until a
  blocking subagent mode ships.

## Phased plan (1.x)

| Phase | What | Hours | Done when |
|---|---|---|---|
| 0. Spike | On 1.19.6 and 1.18.34, with the fake model: a TS plugin that calls a Python hook and blocks a bash call; `shell.env` per session, and the role reaching a child session through `parentID` (an implementer under a PM tries `git push` and is refused); two sessions in different directories on one `opencode serve` messaging with `prompt_async`; a `session.idle` notice through `/global/event`; a PM ruleset with no asks running unattended; the plugin-loaded canary | 10-14 | each item seen working, or the plan stops here |
| 1. Single-session coven | hook adapter and fake-model harness, skills rewritten, routing and memory context, 5 agents, deny-secrets (including `apply_patch`), SDD on the `task` tool | 55-85 | the toy walkthrough runs in OpenCode: spec, plan, test-first, review, full check |
| 2. Hierarchy | server lifecycle, `coven pm launch/resume/status/health` on the HTTP API, `coven_send`/`coven_sessions` tools, the idle watcher, session roles, pm-permissions in `tool.execute.before`, PM todo list | 50-80 | a GM hands a toy project to a PM and gets a `DONE` with a PR |
| 3. Production guard | gate adapter for every tool, OpenCode zones, MCP names, `coven guard explain` | 20-34 | an approved command runs, everything else is refused |
| 4. aquelarre | TUI plugin: status in a prompt slot, pane, toasts | 35-60 | the band shows the live PMs |
| 5. Install and docs | setup, doctor (legacy `config.json`, server auth, canary), shim, code standard mod parts, README, known gaps, CI | 14-24 | a fresh machine installs from the README |
| **Total** | | **184-297** | |

A 2.x adapter later: about 40-60 hours if the Python core stays behind the adapter (new plugin
shape, permission array, one server per role, `subagent` without join handled in the SDD text,
TUI plugin port), plus whatever #64073 and #65316 look like by then.

## Follow-up: a join for 2.x subagents (spike)

A throwaway TypeScript plugin on 2.3.2 (and 1.19.6), run fully offline: a scripted fake
OpenAI-compatible model on 127.0.0.1, everything inside `unshare -rn` (only `lo`), and a policy
that denies `provider.use` for every provider but the fake one. Nothing left the machine.

The plugin adds two tools:

- `await_agents({ids, mode: "all" | "any"})`: for each id, `ctx.session.wait({sessionID})`, then
  the last assistant text from `ctx.session.context({sessionID})`. `all` is `Promise.all`, `any`
  is `Promise.any`. A 30 s heartbeat calls `context.progress`.
- `watch_agents({ids})`: returns at once. An `ctx.event.subscribe` loop waits for
  `session.execution.succeeded` on a watched child and sends the report to the parent with
  `ctx.session.prompt({sessionID: parent, text, delivery: "queue"})`.

What it showed:

| Question | Answer | Receipt |
|---|---|---|
| Can a tool block for minutes? | yes | `await_agents` held 351.9 s (3 children, 6 x 55 s each) and returned; no timeout hit |
| Reports for 3 in parallel? | yes, both modes | `all`: one result with 3 reports. `any`: 3 calls, each returned the next child to finish (10.2, 12.2, 10.0 s) |
| Wake a parent whose turn ended? | yes, two ways | 2.x already does it: every child sends a `<subagent>` report to the parent's inbox and wakes it. The plugin's `ctx.session.prompt` push woke it too |
| A hook that stops the turn ending? | no | 2.x session hooks have no turn-end hook. Closest: the `context` hook adds a system reminder before each model request, plus the native wake above. The reminder was written but not run |
| Which events see another session? | one server sees all | `ctx.event.subscribe` in a plugin and `GET /api/event` both carried events for sessions in two directories. A child created with `parentID` lands in the parent's directory |
| Works on 1.x? | yes, and less needed | 1.19.6 and 1.18.34 `task` blocks: 3 parallel calls returned all 3 reports. The same tools load as a 1.x plugin, and its `event` hook sees each child's `session.idle` |

Gotchas for a real version:

- The native `<subagent>` reports still arrive after `await_agents` returns, so the parent sees
  each report twice. Drop the ones already returned (a `context` hook can filter them) or skip
  `await_agents` and lean on the native wake.
- 2.x never sent `session.idle` here; `session.execution.succeeded` is the signal.
- 1.x backgrounds a `task` only with `background: true` and
  `OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS=true`; push through `prompt_async` was not run.

Size: about 12-20 hours to make it real (both tools, dedupe, reminder, abort through
`context.signal`, an optional timeout, report truncation, fake-model tests). It removes the 2.x
blocker above: the SDD loop can dispatch and then block on `await_agents` as it does on 1.x. It
fits inside the 40-60 hour 2.x adapter estimate.

## Decisions for the user

1. **Which models.** On 1.x the Claude Pro/Max subscription cannot be used: "Anthropic explicitly
   prohibits this" and the plugins were unbundled in 1.3.0 (v1 docs `providers.mdx:356-361`).
   coven would run on an Anthropic API key (pay per token) or another provider (for example the
   gateway models a work setup already uses). 2.x has a `claude-agent` provider that runs Claude
   Code 2.1.248+ locally and says "your plan covers it" (v2 docs `claude-agent.mdx`), but its page
   is hidden from the docs index and was not tried. This decides cost, and it is the only reason
   found to start on 2.x.
2. **Where it runs.** On a machine that already has an OpenCode setup, coven must live next to it
   without touching its globals. On a clean machine, 1.x with an API key is the straight path.
3. **Scope.** Phases 0-2 give the working method plus the hierarchy. The guard (3) and aquelarre
   (4) can wait.

## Not checked

- Anything that needs a real model run: tool hooks firing live, `permission.asked` replies,
  subagent behaviour, compaction, and whether AGENTS.md and CLAUDE.md reach the prompt. These are
  from source and docs only. During the trial one prompt ("hi") went by mistake to OpenCode's free
  default model `opencode/big-pickle`, which answers with no credentials; nothing else left the
  machine.
- 1.18.34 specifically (everything was read on 1.19.6).
- The 1.x TUI plugin API in practice (no TTY in the trial).
- 2.x `permission.hook("evaluate")` answering an ask, toasts from a server plugin, and the
  `claude-agent` provider.
- Whether superpowers' skills load unchanged in OpenCode.
- How long 1.x will be maintained: no end-of-life date is announced, and new work goes to 2.x.
- The hour estimates are judgement from line counts and the work harness's own port estimate, not
  measured.
