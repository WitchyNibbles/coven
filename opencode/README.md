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

PR 3 adds the skills, the agents, the routing and deny-secrets.

## The profile

The profile folder `P` is `$XDG_DATA_HOME/coven/opencode` (the coven data folder when
`XDG_DATA_HOME` is unset), or the folder in `COVEN_OPENCODE_HOME`.

    P/runtime/<version>/   npm prefix holding the pinned OpenCode
    P/config/opencode/     OPENCODE_CONFIG_DIR: the generated opencode.jsonc
    P/plugin/coven/        a copy of src/, loaded only through the config's `plugins` entry
    P/config, data, cache, state   the four XDG folders OpenCode sees
    P/home/                OPENCODE_TEST_HOME: an empty home, so no .agents or .claude is read
    P/tmp/                 TMPDIR
    P/profile.json         what setup chose: preset, base URL, key env name, models (no key)

`run` and `doctor` give OpenCode an environment built from the profile: the folders above, the
switches that turn off auto-update, Claude Code skills and the free models, and
`OPENCODE_DISABLE_PROJECT_CONFIG=1`. Project config is off in the coven profile, so a
`.opencode/` folder or `opencode.json` in a repo is ignored (doctor warns when it finds one). No
other `OPENCODE_` variable of the caller reaches OpenCode, except the server password and user
name, and the one holding the key.

## Commands

    coven opencode install
    coven opencode setup --preset litellm --base-url URL --key-env NAME --model lead=ID [--model ROLE=ID ...]
    coven opencode run -- ARGS
    coven opencode doctor

- `install` runs npm to put the pinned OpenCode into `P/runtime`. It is the only step that uses the
  network, and it runs without the key env. `COVEN_OPENCODE_BIN` points at an existing 2.3.2
  binary instead.
- `setup` writes the config, the plugin copy and `profile.json`, all or none. The preset
  `litellm` needs a gateway URL, the name of the variable holding its key (the name, never the
  value) and `--model lead=ID`; the preset `opencode` takes only models and reads
  `OPENCODE_API_KEY`. Roles are lead, implementer, reviewer, explorer, final-reviewer and title,
  and a role with no model uses the lead's. With no flags it regenerates from `profile.json`.
- `run` checks that the runtime reports the pinned version, warns by name when the key variable is
  unset, and replaces itself with OpenCode in the profile's environment. Arguments for OpenCode
  follow `--`.
- `doctor` makes no request outside the machine and prints one line per check: `ok`, `warn` or
  `fail`, with the fix after a failure. It exits 1 when a check failed. The checks are:
  `runtime` (the binary reports the pinned version), `paths` (`opencode debug paths` stays under
  `P`), `config` (the config matches `profile.json`), `key` (the variable is set), `plugin`
  (a server started for the check lists `coven` as active; it polls up to 10 s, because 2.3.2 lists
  no plugin until they have loaded), `skills` (the same server lists only built-in skills and the
  profile's), `coven root` (the plugin's root still holds `hooks/deny_secrets.py`) and
  `project config` (a warning for a repo config that is ignored here).

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
