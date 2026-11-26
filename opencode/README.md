# coven for OpenCode

Coven's own OpenCode 2.3.2 runtime. `coven opencode` sets up a profile that holds a pinned
OpenCode, its config and coven's plugin, apart from any OpenCode you already use. The plugin
`coven` (in `src/`) adds:

- the tool `await_agents`: the lead agent passes the ids of its subagents and the tool returns
  when they have sent their final report. The reports themselves stay in the conversation as
  `<subagent>` messages; the tool returns states;
- the tool `coven_ping`, which reports the plugin's name, version and root. `coven opencode doctor`
  and the end-to-end run use it to prove the plugin loaded;
- a `tool_choice` hook for gateways (see below);
- a `shell` hook that removes the profile's key variable (the plugin option `keyEnv`),
  `OPENCODE_SERVER_PASSWORD`, `OPENCODE_PASSWORD` and any `OPENCODE_` variable ending in
  `_PASSWORD`, `_TOKEN` or `_KEY` from the environment of every shell command the model runs;
  OpenCode itself keeps them;
- the GM/PM tools `coven_gm_claim`, `coven_pm_launch`, `coven_pm_list`, `coven_send`,
  `coven_pm_resume` and `coven_pm_stop`, and the guards that go with them (see
  [Hierarchy](#hierarchy)).

Setup also copies coven's skills, role agents, commands and routing (`opencode/content/`, generated
from the Claude Code content by `python -m coven.opencode.content`) and the deny-secrets guard,
described below.

## Install

You do not need Claude Code. You need:

- Linux or WSL 2. That is what was tested; macOS is untested on OpenCode.
- Python 3.12 or later as `python3`, and `git`.
- Node.js with `npm` (tested with Node 24.18.0 and npm 11.16.0; no minimum version is pinned).
  `coven opencode install` runs npm to fetch the npm package `@opencode/cli` at 2.3.2 into the
  profile. Without npm it stops with "npm not found on PATH".
- A gateway that serves an OpenAI-compatible API, and a key for it in an environment variable. Or
  the `opencode` preset, which reads `OPENCODE_API_KEY` (see Commands below).

The steps, as rehearsed on a clean HOME:

```sh
git clone https://github.com/WitchyNibbles/coven.git ~/coven
ln -s ~/coven/bin/coven ~/.local/bin/coven     # any folder on PATH; bin/coven works through a symlink
coven opencode install
coven opencode setup --preset litellm --base-url https://gateway.example/v1 --key-env GATEWAY_API_KEY --model lead=MODEL_ID
coven opencode doctor
coven opencode run
```

`doctor` prints one line per check and exits 1 when one failed. `coven opencode run` with no
arguments opens the TUI. A headless run is `coven opencode run -- run "prompt"`; it cannot use
shell, because shell asks first and a headless run rejects the ask (see Commands below). To use
OpenCode's own models instead of a gateway, pass `--preset opencode` to `setup` and set
`OPENCODE_API_KEY`.

**Run coven from a git checkout.** A wheel install (`pip install`, or `uv tool install` without
`-e`) ships only `src/` (`only-include = ["src"]` in `pyproject.toml`), so setup cannot find
`opencode/` and `hooks/`. Every `coven opencode` command then stops at once, saying it needs a full
copy of the coven repo (a git clone).

**The `coven` command.** The `ln -s` line above is enough. Or run `~/coven/bin/coven setup --yes
--shim` from the clone: it writes `~/.local/bin/coven` (and, as `setup` always does, coven's config
and the memory store). The shim runs Claude Code's coven plugin when that is installed. Without
Claude Code it falls back to the clone it was written from.

**Moving the clone.** Setup writes the checkout's path into `opencode.jsonc` as the plugin option
`covenRoot`. If you move or delete the clone, `doctor`'s `coven root` check fails, and the
plugin's deny-secrets guard cannot start `hooks/deny_secrets.py`. The guard fails closed, so the
tools it covers are refused. Run `coven opencode setup` from the new place; with no flags it
regenerates from `profile.json`. A shim written from the clone breaks the same way (its
fallback names the old path); run `coven setup --yes --shim` from the new place to rewrite it.

**The base URL.** `--base-url` is the URL the gateway serves the OpenAI-compatible API under; for a
LiteLLM proxy that usually ends in `/v1`. Coven drops one trailing `/` and hands the rest to
OpenCode as given, and OpenCode appends `/chat/completions`. Setup prints a warning when the URL
does not end in `/v1`. It refuses a URL that carries credentials, a query or a fragment; the key
comes from the `--key-env` variable.

**Superpowers.** Setup copies eight superpowers skills (listed under `setup` in Commands below).
`coven opencode install` fetches them with `git`: a clone of obra/superpowers at tag v7.0.2,
checked against its pinned commit, in `P/superpowers/v7.0.2`. Setup copies from that v7.0.2 clone
when it is there. If it is not (no `git`, or the clone failed, which install only warns about),
setup falls back to Claude Code's superpowers plugin, which it finds through Claude Code's
installed-plugins file, and prints which of the two it used. With neither, setup prints one
warning, adds a note to `AGENTS.md`, and `doctor`'s `superpowers` check warns. The routing still
names those skills, so a step that loads one is skipped.

**Memory.** `AGENTS.md` points at your memory store when the folder in `memory_path` of
`~/.config/coven/config.json` exists (default `~/coven-memory`; `coven memory init` creates it).
With no such folder, one line says there is none.

**opencode-harness** (the team's per-repo setup on OpenCode 1.18.x) is a different thing: it
installs into a repo's `.opencode/`, which coven's profile ignores
(`OPENCODE_DISABLE_PROJECT_CONFIG=1`), and it does not load coven. Use the harness in the team's
repos and coven for your own profile on 2.3.2.

What this setup does not cover is in the OpenCode section of
[docs/known-gaps.md](../docs/known-gaps.md#opencode).

## The profile

The profile folder `P` is `$XDG_DATA_HOME/coven/opencode`, which is `~/.local/share/coven/opencode`
when `XDG_DATA_HOME` is unset, or the folder in `COVEN_OPENCODE_HOME`.

    P/runtime/<version>/   npm prefix holding the pinned OpenCode
    P/config/opencode/     OPENCODE_CONFIG_DIR: opencode.jsonc, AGENTS.md, skills/, agents/, commands/
    P/plugin/coven/        a copy of src/, loaded only through the config's `plugins` entry
    P/config, data, cache, state   the four XDG folders OpenCode sees
    P/home/                OPENCODE_TEST_HOME: an empty home, so no .agents or .claude is read
    P/tmp/                 TMPDIR
    P/profile.json         what setup chose: preset, base URL, key env name, models, limits (no key)

`run` and `doctor` give OpenCode an environment built from the profile: the folders above, the
switches that turn off auto-update and Claude Code skills, and `OPENCODE_DISABLE_PROJECT_CONFIG=1`.
Project config is off in the coven profile, so a `.opencode/` folder or `opencode.json` in a repo is
ignored (doctor warns when it finds one). The free models are off only for the litellm preset
(`OPENCODE_DISABLE_FREE_MODELS=1`). Under the opencode preset with no key, OpenCode's free public
key can still be used. No other `OPENCODE_` variable of the caller reaches OpenCode, except the
server password and user name (`OPENCODE_SERVER_PASSWORD`, `OPENCODE_SERVER_USERNAME`), the models
fetch switch (`OPENCODE_DISABLE_MODELS_FETCH`) and the one holding the key.

## Commands

    coven opencode install
    coven opencode setup --preset litellm --base-url URL --key-env NAME --model lead=ID [--model ROLE=ID ...] [--limit ID=CONTEXT[:OUTPUT] ...]
    coven opencode run -- ARGS
    coven opencode serve start [--port N] | stop | status
    coven opencode gm
    coven opencode doctor

`serve` and `gm` are described under [Hierarchy](#hierarchy).

- `install` runs npm to put the pinned OpenCode into `P/runtime`, then runs `git clone` to put
  superpowers v7.0.2 into `P/superpowers/v7.0.2` (skipped when it is already there). These are the
  only steps that use the network.
  - npm runs with the profile env: the profile's XDG, temp and config folders, no key env and no
    inherited `OPENCODE_` setting, so the package's postinstall check never writes into your own
    OpenCode folders. `HOME`, `PATH` and the proxy variables stay, so your `.npmrc` and the npm
    cache still work. `COVEN_OPENCODE_BIN` points at an existing 2.3.2 binary instead.
  - The clone goes over https only, with no hooks and no prompt, in its own process group, which a
    timeout kills. Git's own config is not used for it (no mirrors, `insteadOf` rewrites or
    credential helpers), because the pinned commit already checks what was downloaded; the proxy
    environment variables (`https_proxy` and the rest) are used. A clone whose commit is not the
    pinned one is removed and install fails. Without `git`, or when the clone fails, install warns
    and still exits 0.
- `setup` writes the config (with the content below), the plugin copy and `profile.json`, all or
  none. The preset
  `litellm` needs a gateway URL, the name of the variable holding its key (the name, never the
  value) and `--model lead=ID`; the preset `opencode` takes only models and reads
  `OPENCODE_API_KEY`. Roles are lead, implementer, reviewer, explorer, final-reviewer, title and
  project-manager, and a role with no model uses the lead's. `--limit ID=CONTEXT[:OUTPUT]` (litellm only, repeat it
  per model) writes the model's context and output size in tokens into the config; without one
  OpenCode assumes 200000 tokens of context and 32000 of output, so a model with a smaller window
  overflows before OpenCode compacts. Setup prints one warning naming the litellm models with no limit.
  With no flags it regenerates from `profile.json` and keeps its limits.
  What setup puts in `P/config/opencode/`:
  - `skills/coven-*`, `agents/` and `commands/`, copied from `opencode/content/`;
  - `skills/superpowers-<x>` for brainstorming, writing-plans, test-driven-development,
    systematic-debugging, verification-before-completion, using-git-worktrees,
    finishing-a-development-branch and executing-plans, copied from the v7.0.2 clone in
    `P/superpowers` or, without it, from your Claude Code superpowers plugin (found through its
    installed-plugins file). Only the skill names in the text are renamed, so wording that is
    Claude Code's (its tool names) stays; that is a known gap. When neither source exists, setup
    prints one warning, appends a note to `AGENTS.md` and leaves the copies out. A re-run after
    installing one adds them; a re-run after removing both drops them;
  - `AGENTS.md`: the routing, then a pointer to your memory store (the folder of `memory_path` in
    the coven config; with no folder, one line says so). A coven config that cannot be read makes
    setup fail;
  - in `opencode.jsonc`: `experimental.todo` (turns on the task-list tool) and `permissions`: deny
    read and edit of `.env` files (an example file stays readable), then allow file access outside
    the session's folder for the profile's skills and the memory store, which OpenCode would
    otherwise ask about and a headless run cannot answer. Edits to the profile's installed
    skills are denied. `shell` and `webfetch` ask before they run, for every agent. A headless
    `opencode run` rejects an ask by itself, so `coven opencode run -- run ...` cannot use shell;
    the TUI asks you. There are no MCP rules, because the profile loads no MCP servers.

  The `.env` rules are the second layer. The first is the plugin's guard, which runs coven's
  `deny_secrets.py` before each `read`, `grep`, `glob`, `shell`, `write`, `edit` and `patch`.
  Config rules cannot read a shell line, so `shell` is covered by the plugin layer only. So are
  `grep` and `glob`: in 2.3.2 they check `external_directory` and then their own permission with
  the search pattern as the resource, never `read` on the files they search, so the `read` rules
  above do not apply to them (`grep` returns line previews; `glob` returns paths only). The guard
  fails closed: an error or a timeout (`guardTimeoutMs`, 5 s) refuses the call. Other tools (MCP,
  `webfetch`, `subagent`, `skill`) pass the guard, as on Claude Code.

- `run` checks that the runtime reports the pinned version, warns by name when the key variable is
  unset, and replaces itself with OpenCode in the profile's environment. Arguments for OpenCode
  follow `--`.
- `doctor` starts its server with the models fetch off and makes no request of its own outside the
  machine. One case can: with the opencode preset and a stored Console login in the profile, the
  server's Console provider fetches its remote config. With the litellm preset, or no login, it
  does not. `doctor` prints one line per check: `ok`, `warn` or
  `fail`, with the fix after a failure. It exits 1 when a check failed. The checks are:
  `runtime` (the binary reports the pinned version), `paths` (`opencode debug paths` stays under
  `P`), `config` (the config, limits included, matches `profile.json`), `key` (the variable is
  set), `python3` (3.12 or later, which the plugin's hooks need), `server` (the coven server
  answers, when one runs), `password` (the password file is mode 0600), `plugin` (the server lists
  `coven` as active; with no coven server running, `doctor` starts a server for the check and
  polls up to 10 s, because 2.3.2 lists no plugin until they have loaded), `skills` (the same
  server lists only built-in skills and the profile's), `operations` (the server's API document
  has every operation coven calls, so a runtime bump that drops one fails here and not in a PM),
  `coven root` (the plugin's root still holds `hooks/deny_secrets.py`), `PM shell isolation`
  (a PM's shell can start its namespaces here; see [Hierarchy](#hierarchy)) and `project config` (a
  warning for a repo config that is ignored here), `superpowers` (a warning when a
  `superpowers-<x>` skill is missing from the profile) and `limits` (a warning naming the litellm
  models that have no `--limit`).

## Hierarchy

The manager's way of working runs on OpenCode 2.3.2 too. You talk to one general manager (GM)
session. It runs one project manager (PM) session per project, each in its own repo, and a PM
runs the role agents. You hear only real questions, production steps and results.

    coven opencode setup ...          # once; --model project-manager=ID picks the PM's model
    coven opencode serve start
    coven opencode gm

`coven opencode serve start` runs one `opencode serve` on 127.0.0.1 for the profile. Its password
is generated into `P/state/serve/password` (mode 0600) and kept; a second server is refused.
`coven opencode serve stop` ends it and keeps the password. `coven opencode serve status` is
read-only: `running pid P port N` or `stopped`, then one line per PM session (code, busy or idle,
last outcome, any ask that waits). `coven opencode gm` starts the server when none runs, stops if
coven's plugin is not active on it, and opens the OpenCode TUI attached to it, with the password
in that process only. `coven opencode run` refuses while a server runs, because both would write
the profile's database; use `gm`. After a server restart sessions and their rules survive, but
turns in flight do not: the GM resumes the PMs it finds idle or interrupted.

**The GM and its tools.** The GM is the lead session in that TUI. The manager skill has it call
`coven_gm_claim` first, which marks the session as the GM. A later claim takes over: the earlier
GM session becomes a former GM and stops receiving PM messages. Only a root session that is not a
PM can claim.

- `coven_gm_claim`: marks this session as the GM.
- `coven_pm_launch {code, repo, brief, extraRepos?, title?}`: starts a session of the
  `project-manager` agent in the repo's git checkout with the brief. The PM makes its own
  worktrees under `<repo>/.claude/worktrees/`, one per task. It refuses a code that already has a
  live PM, an invalid code and a repo that is not a git checkout.
- `coven_pm_list {archived?}`: one line per PM: code, session id, busy or idle, last outcome, any
  waiting ask (`ASK WAITING (bug)`), a child busy for 30 minutes or more, and the head of its
  last message.
- `coven_send {to, text}`: a message between the GM and a PM, queued for the receiver's next turn.
  The first word is the prefix. The GM sends `ANSWER`, `APPROVED`, `DENIED` or `TASK` to the PM
  whose code is `to`; a PM sends `QUESTION`, `PROD-STEP`, `DRAFT`, `DONE` or `BLOCKED` to the GM.
  The tool adds the `[from gm]` or `[from pm-<code>]` frame.
- `coven_pm_resume {code, text}`: shows a hidden PM again and queues it a turn that starts with
  `RESUMED by gm`.
- `coven_pm_stop {code, archive?}`: interrupts the PM's turn, which also ends a waiting ask, and
  kills the isolation trees its shells started (see "Isolated PM shells" below). Its text ends
  `; killed N isolation tree(s)`, then `; skipped M stale record(s)` and
  `; M tree(s) still running after SIGKILL` when those counts are above zero.

`coven_pm_launch`, `coven_pm_list`, `coven_pm_resume` and `coven_pm_stop` refuse any session but
the GM. `coven_gm_claim` refuses a child session and a PM, so a session that is not the GM yet can
call it. `coven_send` serves the GM and a PM, which sends to the GM only. A role agent under a PM
has none of them. A PM's message arrives in the GM's session
as a turn; the GM never polls. When a PM's turn ends without a message to the GM, a line
`[coven notice] pm-<code> ended a turn (<outcome>) without a message` arrives instead. There is no
notice while the PM still has a role agent working, and none after a stop the GM made; a turn
that failed always gets one.

**Archive on 2.3.2.** OpenCode 2.3.2 gives a plugin no way to archive a session. `coven_pm_stop`
with `archive: true` hides the PM from `coven_pm_list` by a `coven.archived` metadata flag; it
does not archive the session in OpenCode. `coven_pm_list` with `archived: true` shows the hidden
PMs, marked `archived`, and the PMs archived in OpenCode itself. A resume clears the flag. A PM
archived in OpenCode is not resumed: unarchive it there before a resume.

**A PM never waits on a prompt.** A PM session carries the PM rules, and its role agents inherit
them. Shell, edit and read are allowed; force pushes, pushes to `main` or `master`,
`gh pr merge`, edits of the memory store, `webfetch` and `question` are denied, and so are `.env`
files. In a PM tree any other ask is refused on the spot, and the PM sends it to the GM as a
`QUESTION` or `PROD-STEP`. A PM's shells carry the session role, so the memory store's pre-commit
hook and the PM permissions work as on Claude Code; the GM's shells do not, because the GM writes
the store.

**The guards.** Every session, the GM's included, runs deny-secrets and the server guard. A PM
tree also runs pm-permissions (the engine Claude Code's PMs use) and the production guard in a
deny-only mode. All of them fail closed. They are best effort against a model that follows its
role, and none is a sandbox.

- The server guard keeps a model away from the server and the profile. From a shell or a file tool
  it refuses any `opencode` command but `--version` and `--help`, the server's port, the serve
  folder (the password file) and `/proc/<pid>/environ`, any write under the profile except
  `tmp/`, and `coven opencode setup`, `install`, `serve` and `gm`: you run those in a terminal.
  Coven's own checkout is read-only to every session: only programs that read run there, and a
  worktree under `.claude/worktrees/` is where work on coven happens.
- The deny-only production guard reads `systems/guard.json` as committed at the memory store's
  git HEAD and refuses every call of a PM tree that reaches a target in it. It has no approvals.
  With no `guard.json` in the store (and no `"guard": "required"` in coven's config) the
  production guard is off, and "a PM never runs a production step" rests on the PM's text alone.
  A `guard.json` with no targets counts nothing as a production target, so the guard has no
  targets to refuse; a PM tree still meets the restricted rules of the guard, which no approval
  lifts. They refuse a PM's writes to the memory store, coven's config and Claude Code's user
  folder, a change of the session role variable, `coven setup`, `coven approve` and
  `coven memory`, and a remote command (ssh, psql, curl and the like whose host is not written
  out).
  A `guard.json` that is in the store's working tree but not committed makes the guard refuse
  every Bash and MCP call of a PM until it is committed. Name your live systems first (see
  [Production guard](../README.md#production-guard)).

**Isolated PM shells.** Every shell command of a PM tree runs in new user, PID and mount
namespaces, through `unshare` on Linux. Its `/proc` shows only its own processes, and the profile's
serve and data folders are empty tmpfs mounts, so the server's password, port file and environment
are not there to read, however a command hides the path. Files, git, `gh` and the network work as
before: there is no network namespace, so a PM still pushes and opens PRs. The GM's shells and your
own `!` shells are not isolated.

Where namespaces are not available, a PM's shell refuses and never runs unisolated. No setting turns
isolation off. The refusal reads:

    coven: PM shells run isolated and isolation is not available here: <missing piece>; ask the user

The missing piece is `unshare` not found, util-linux older than 2.38, user namespaces off, AppArmor
blocking them, or the error `unshare` gave. `coven opencode doctor` shows the same check as
`PM shell isolation: ok` or `not available: <piece>`, a failed check.

Some limits are listed in [docs/known-gaps.md](../docs/known-gaps.md#opencode): non-shell tools run
in the server process, and the role queue has a mixed race (a GM shell started in the same instant as
a PM's shell with the same command and folder can run isolated once).

**Production steps.** The manager on OpenCode follows only the No guard flow of its skill. Every
`PROD-STEP` goes to the user with the exact commands. On a yes the GM runs them itself, in its own
session, where the user sees each permission prompt, and replies `APPROVED`; on a no it replies
`DENIED`. Approvals and the guard-on flow come with M3; until M3 a production step on OpenCode is
always run by the GM, after the user's yes.

Not in this version: the PM todo list in `serve status`, a Claude Code GM driving OpenCode PMs,
and Claude Code's `coven pm status`, `health` and `resume` reading OpenCode PMs (switching tools
in the middle of a project on one memory store is not supported). What else is missing is in
[docs/known-gaps.md](../docs/known-gaps.md#opencode).

## The tool_choice hook

OpenCode 2.x sends no `tool_choice` to an OpenAI-compatible provider on an ordinary turn. A LiteLLM
or vLLM gateway can then leave the model's tool call as text in the reply, and the turn ends
without running the tool (OpenCode issue 60202). For the providers named in the plugin option
`toolChoiceAuto` (the litellm preset sets `litellm`), the hook adds `"tool_choice": "auto"` to a
request that has tools and no `tool_choice`.

The hook fails open on purpose. It is a compatibility shim, not a guard: a request body it cannot
read or does not understand goes out exactly as OpenCode built it, and the plugin logs one line.

## How it waits

A call looks up every id, then ends when the children have sent their final report (`all`) or the
first one has (`any`), when `timeoutSeconds` runs out, or when the call is interrupted. It learns
of reports from three places:

- the OpenCode event stream, which the plugin follows from the start; it is the only place that
  sees a report arriving during the wait;
- the lead's conversation, read at the start of each call and every 15 s, which finds reports
  delivered before the call (for example before a plugin restart). OpenCode 2.3.2 adds a report to
  that conversation only after the running tool call returns, so this read cannot see one that
  arrives during the wait;
- `session.wait` for each child still pending: once the child is idle the plugin counts it as
  ended. A child that sent no report since its last start and sends no final one within 10 s comes
  back as `ended-without-report` with its last text, so the wait ends even when the event stream
  missed both the end and the report. A `running` report keeps the wait going.

## End-to-end run

The end-to-end run starts OpenCode 2.3.2 on a scripted fake model. Nothing leaves the machine: the
script runs inside `unshare -rn` (a network namespace with only `lo`) and refuses to start anywhere
else. The join suite refuses a project config that allows any provider but the fake one, and the
runtime suite refuses a generated profile whose provider is not litellm on 127.0.0.1. It needs a
2.3.2 binary, `unshare` and `ip` (it brings `lo` up).

    COVEN_OPENCODE_BIN=/path/to/opencode npm run e2e              # both suites
    COVEN_OPENCODE_BIN=/path/to/opencode npm run e2e -- runtime   # one suite

Exit codes: 0 when every check passed, or when `COVEN_OPENCODE_BIN` is unset (it prints
`skipped: set COVEN_OPENCODE_BIN`); 1 when a check failed, the suite name is unknown or the run
is not in the namespace; 2 when `unshare -rn` is not available, so nothing ran. Under `node --test`
it does nothing, because `main.ts` runs only when `COVEN_E2E=1`.

It prints one `ok` or `not ok` line for each check, numbered across the suites. The runtime suite
runs first, in a scratch HOME that holds a fake 1.x install (a config with a provider `onex`, a
data file and a skill under `.agents`) and a profile that `coven opencode setup` generates for a
fake LiteLLM gateway on a free loopback port:

1. Isolation: a session and a reloaded server change nothing under the scratch HOME or
   `/tmp/opencode`, list no skill `leak-e2e` and no provider `onex`.
2. The `tool_choice` hook is needed and works: with the hook on, the model's `coven_ping` call runs
   and every request with tools carries `"auto"`. With the hook off, the call comes back as text and
   the tool never runs.
3. Doctor passes on the generated profile, fails on the `plugin` line when the plugin folder is
   removed, and names a skill planted in the profile's home.
4. Deny-secrets: a `read` and a `cat` of a secrets file are refused.
5. SDD: two role subagents are awaited and each report arrives once.
6. Shell-env: a shell command sees neither the provider key nor the server password.

The join suite follows. It takes about two and a half minutes, because one check waits a real 120
seconds:

7. `all` and `any` return with 3 parallel workers.
8. Each worker report appears exactly once in the lead's history. This check passes without the
   plugin too, by design: it guards against the join adding a duplicate, not against a missing
   join.
9. A worker that sleeps 120 s is waited for.
10. A worker with its own worker: the wait ends after the final report.
11. An unknown id is named in the tool result.

The join project under `test/e2e/project/` loads the plugin by re-exporting `src/plugin.ts`. The
shared harness (fake model, loopback guards, snapshots, the `withServer` helper) is in
`test/harness/`.

## Tests

Unit tests run without OpenCode:

    npm test

## Content

`opencode/content/` holds the skills and commands the profile ships, rendered from the repo's
`skills/` folder by a generator. It is committed, and a test renders it again and compares. After a
change to a skill, to `opencode/overrides/` or to `src/coven/opencode/rewrite.py`, regenerate it:

    uv run python -m coven.opencode.content

Do not edit the output by hand. The rewrite table in `rewrite.py` maps the Claude Code names in a
skill (skill and agent ids, tool names, the `Subagent (...)` lines of the prompt templates) to their
OpenCode 2.3.2 names, line by line. A name with no equivalent (a Claude-only tool, a coven skill
that is not shipped, `${CLAUDE_SKILL_DIR}`) stops the generator with the file and line. A
`superpowers:<x>` name is renamed to `superpowers-<x>` whether or not setup copies it, so a test
checks that every `superpowers-<x>` the content names is one of the skills setup copies.

For such a file, write an override: `opencode/overrides/skills/<name>/<file>` replaces the source
file of the same path. Its first line is `<!-- opencode override of skills/<name>/<file> sha256:HEX -->`,
with the sha256 of that source file; the generator strips it. When the source changes, a test fails
until the override is updated and its hash with it. Overrides exist for `subagent-driven-development`,
`shared-memory`, `handoff` and the routing (`AGENTS.md`).
