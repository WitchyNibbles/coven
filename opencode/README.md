# coven for OpenCode

Coven's own OpenCode 2.3.2 runtime. `coven opencode` sets up a profile that holds a pinned
OpenCode, its config and coven's plugin, apart from any OpenCode you already use. The plugin
`coven` (in `src/`) adds:

- the tool `await_agents`: the lead agent passes the ids of its subagents and the tool returns
  when they have sent their final report. The reports themselves stay in the conversation as
  `<subagent>` messages; the tool returns states;
- the tool `coven_ping`, which reports the plugin's name, version and root. `coven opencode doctor`
  and the end-to-end run use it to prove the plugin loaded;
- a `tool_choice` hook for gateways (see below).

Setup also copies coven's skills, role agents, commands and routing (`opencode/content/`, generated
from the Claude Code content by `python -m coven.opencode.content`) and the deny-secrets guard,
described below.

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
    coven opencode doctor

- `install` runs npm to put the pinned OpenCode into `P/runtime`. It is the only step that uses the
  network. npm runs with the profile env: the profile's XDG, temp and config folders, no key env and no
  inherited `OPENCODE_` setting, so the package's postinstall check never writes into your own
  OpenCode folders. `HOME`, `PATH` and the proxy variables stay, so your `.npmrc` and the npm cache
  still work. `COVEN_OPENCODE_BIN` points at an existing 2.3.2
  binary instead.
- `setup` writes the config (with the content below), the plugin copy and `profile.json`, all or
  none. The preset
  `litellm` needs a gateway URL, the name of the variable holding its key (the name, never the
  value) and `--model lead=ID`; the preset `opencode` takes only models and reads
  `OPENCODE_API_KEY`. Roles are lead, implementer, reviewer, explorer, final-reviewer and title,
  and a role with no model uses the lead's. `--limit ID=CONTEXT[:OUTPUT]` (litellm only, repeat it
  per model) writes the model's context and output size in tokens into the config; without one
  OpenCode assumes 200000 tokens of context and 32000 of output, so a model with a smaller window
  overflows before OpenCode compacts. Setup prints a warning for each litellm model with no limit.
  With no flags it regenerates from `profile.json` and keeps its limits.
  What setup puts in `P/config/opencode/`:
  - `skills/coven-*`, `agents/` and `commands/`, copied from `opencode/content/`;
  - `skills/superpowers-<x>` for brainstorming, writing-plans, test-driven-development,
    systematic-debugging, verification-before-completion, using-git-worktrees,
    finishing-a-development-branch and executing-plans, copied from your Claude Code
    superpowers plugin (found through its installed-plugins file). Only the skill names in the
    text are renamed, so wording that is Claude Code's (its tool names) stays; that is a known gap.
    When the plugin is not installed, setup prints one warning, appends a note to `AGENTS.md` and
    leaves the copies out. A re-run after installing it adds them; a re-run after removing it
    drops them;
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
  `P`), `config` (the config, limits included, matches `profile.json`), `key` (the variable is set), `plugin`
  (a server started for the check lists `coven` as active; it polls up to 10 s, because 2.3.2 lists
  no plugin until they have loaded), `skills` (the same server lists only built-in skills and the
  profile's), `coven root` (the plugin's root still holds `hooks/deny_secrets.py`) and
  `project config` (a warning for a repo config that is ignored here), `superpowers` (check 9, a
  warning when a `superpowers-<x>` skill is missing from the profile) and `limits` (check 10, a
  warning naming the litellm models that have no `--limit`).

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

The join suite follows. It takes about two and a half minutes, because one check waits a real 120
seconds:

4. `all` and `any` return with 3 parallel workers.
5. Each worker report appears exactly once in the lead's history. This check passes without the
   plugin too, by design: it guards against the join adding a duplicate, not against a missing
   join.
6. A worker that sleeps 120 s is waited for.
7. A worker with its own worker: the wait ends after the final report.
8. An unknown id is named in the tool result.

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
