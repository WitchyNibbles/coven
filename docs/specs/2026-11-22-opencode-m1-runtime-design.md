# coven on OpenCode 2.x, M1: runtime and single-session core

Status: design, 2026-11-22. Follows `docs/research/2026-11-22-coven-on-opencode.md` and
`docs/specs/2026-11-22-opencode-join-design.md` (the `await_agents` join, PR #17).

## Why

The user wants coven on OpenCode 2.x. M1 is single-session coven: one OpenCode session that has
coven's routing, skills, role agents, secret guard and code standard, and that runs the
subagent-driven-development loop on 2.x background subagents plus `await_agents`. M2 adds the
GM/PM hierarchy, M3 the production guard and M4 aquelarre.

## User decisions (2026-11-22, through the GM)

- Models: any OpenAI-compatible provider set in coven's own OpenCode config. That means a base URL,
  a model id per role, and the key read from an env var whose name the user gives; the key value is
  never stored. Two presets ship: a LiteLLM gateway, and OpenCode's own provider. No live prompt
  in any test.
- Location: inside coven, as the `opencode/` package, sharing the Python core and CI.
- Runtime: next to the local OpenCode 1.19.0, untouched. A pinned 2.x in coven's own folder with
  its own config, on this machine first.
- M1 ships as 2-3 PRs, each to main with CI green. The join tool (PR #17) was the first.

## Goals

1. `coven opencode install` puts OpenCode 2.3.2 in a coven-owned folder. `coven opencode run`
   starts it with a coven-owned profile, so the user's 1.x binary, config, data and sessions are
   never read or written.
2. `coven opencode setup --preset litellm|opencode ...` writes the profile config: provider,
   models per role and the key env var name.
3. Tool calls work through a LiteLLM or vLLM gateway despite #60202 (2.x omits `tool_choice`).
4. In that profile, a session gets:
   - coven's routing and its skills;
   - the 4 single-session role agents (explorer, implementer, reviewer, final-reviewer), each
     with its own model;
   - the deny-secrets guard;
   - SDD on `subagent` plus `await_agents`.

   The fifth agent, project-manager, belongs to M2. The lead is OpenCode's default primary agent
   (`build`) running the root `model`. It has no coven agent file.
5. `coven opencode doctor` proves the install is pinned, the profile is isolated and coven's plugin
   is loaded (a canary tool). It names what is wrong when one of these fails.
6. Every test runs on a fake local model. Anything that starts OpenCode runs inside `unshare -rn`.

## Non-goals (M1)

- The GM/PM hierarchy, `coven pm`, pm-permissions and the project-manager agent (M2).
- The production guard and approvals (M3). aquelarre (M4).
- OpenCode 1.x support. Windows or macOS paths beyond what falls out for free.
- Work machine setup.

## Anti-goals

- Touching `~/.config/opencode`, `~/.local/share/opencode`, `~/.local/bin/opencode` or the 1.x
  sessions in any way.
- Storing an API key, or writing its value anywhere (config, logs, test output).
- Any test that leaves the machine, including OpenCode's free public model, the models.dev fetch
  and the self-updater.
- A guard that fails open: if coven's plugin does not load, the session must not look guarded.

## 2.3.2 facts this design rests on

Source-read from the 2.3.2 tree; file:line receipts are in the research notes the plan carries.

- Package `@opencode/cli`, version `2.3.2`. The platform binary comes from an optional dependency and is
  hard-linked by postinstall. The self-updater runs only in the default TUI command; it is off with
  config `update: "disable"` plus env `OPENCODE_DISABLE_AUTOUPDATE=1`.
- Paths: config dir `OPENCODE_CONFIG_DIR` (else `$XDG_CONFIG_HOME/opencode`); data, cache and state
  follow `XDG_*`. Home for `~/.agents` and `~/.claude` lookups is
  `OPENCODE_TEST_HOME ?? os.homedir()`. `~/.agents/skills` is read whatever the
  `OPENCODE_DISABLE_CLAUDE_CODE*` flags say. A default command may attach to a background service
  registered in the state dir; `--standalone` avoids that.
- Provider config key `providers` (plural). OpenAI-compatible is
  `package: "@opencode/ai/providers/openai-compatible"`, `settings.baseURL` (ending in `/v1`),
  `env: [<VAR>]` for the key, and a `models` map. OpenCode's own provider is id `opencode`, called
  "OpenCode Console" in v2, with key env `OPENCODE_API_KEY`. With no key it falls back to a free
  public key unless `OPENCODE_DISABLE_FREE_MODELS=1`.
- Agent model: frontmatter `model: provider/id`, or config `agents.<name>.model`. The title model
  is `agents.title.model`.
- #60202 (open, reproduced, fix PR #60219 open): 2.3.2 sends `tool_choice` only when core sets
  one, and on ordinary turns it does not. vLLM-style gateways then return `finish_reason: "stop"`
  with the call stranded in the content, and the turn ends silently. A plugin `http.request` hook
  can add the field to the outgoing body.
- Skills from `skills/` under the config dir; agents from `agents/`; `AGENTS.md` in the config dir
  as global instructions. Plugins auto-load from `plugins/` in the config dir and can import
  `@opencode/plugin` without an install. Permission rules are
  `{action, resource, effect}`, last match wins.
- `opencode debug paths` runs without a server. `opencode plugin list` and `debug agents` need one
  (`--standalone`).

Live probes, run offline in `unshare -rn` on 2026-11-22 against the 2.3.2 binary. Hashes of the
real 1.x folders were the same before and after.
- `debug paths` stays inside the profile except `tmp` (`/tmp/opencode` unless `TMPDIR` is set). The
  DB is `<data>/opencode/opencode.db`.
- `~/.agents/skills` leaks with `HOME` alone. `OPENCODE_TEST_HOME` pointing at an empty folder
  removes the leak.
- A plugin folder `<dir>/index.ts` loads with no install, whether it uses `Plugin.define` or a
  plain `{id, setup}`. A `plugins` config entry `{package: <abs path>, options}` hands the options
  to `ctx.options`. `GET /api/plugin` lists the active plugins (`plugin list --standalone` printed
  "No plugins found", so doctor uses the API).
- `--standalone` is refused by `serve` and accepted by the root TUI, `run`, `debug` and `plugin`.
  Plain `serve` writes only its log and DB. `serve --service` writes `service.json` in the config
  dir.
- A custom `litellm` provider whose model has no cost or limit validates (default limits). A file
  agent's `model: litellm/m1` applies.
- The `http.request` hook gets `model.providerID` and can be registered scoped to one provider
  (`{providerID}`).
- In `unshare -rn`, loopback is down until `ip link set lo up`. A client must run in the same
  namespace as the server.

## Approaches

1. **Python `coven opencode` commands own a profile folder** (picked). Install, setup, run and
   doctor are a new feature module in `src/coven/opencode/`. It writes a generated profile from
   templates and generated content in the repo, and runs the pinned binary with a fixed env.
   Reuses coven's CLI, paths, tests and CI; one core.
2. **A shell launcher plus hand-written config.** Smallest, but no doctor, no tested config
   generation, and the env list drifts.
3. **An npm package that wraps OpenCode.** Fits OpenCode's ecosystem, but a second CLI and
   runtime for coven, and the Python core still has to be called.

## Design

### Profile layout

`P = ${XDG_DATA_HOME:-~/.local/share}/coven/opencode`, override `COVEN_OPENCODE_HOME`.

```
P/runtime/2.3.2/          npm prefix: node_modules/@opencode/cli, version 2.3.2 (save-exact)
P/config/opencode/        OPENCODE_CONFIG_DIR: opencode.jsonc, AGENTS.md, agents/, skills/, commands/
P/plugin/coven/           coven's plugin, loaded only through the `plugins` entry (outside the
                          auto-load `plugins/` folder, so it never loads twice or without options)
P/config, P/data, P/cache, P/state   XDG_CONFIG_HOME, XDG_DATA_HOME, XDG_CACHE_HOME, XDG_STATE_HOME
P/home/                   OPENCODE_TEST_HOME (empty: no ~/.agents, no ~/.claude)
P/tmp/                    TMPDIR
P/profile.json            what setup chose: preset, base URL, key env name, models per role (no key)
```

The run env, set by coven and nothing else:
- `OPENCODE_CONFIG_DIR`, the four `XDG_*` above, `OPENCODE_TEST_HOME` and `TMPDIR`.
- `OPENCODE_DISABLE_AUTOUPDATE=1`, `OPENCODE_DISABLE_CLAUDE_CODE=1`.
- `OPENCODE_DISABLE_PROJECT_CONFIG=1`: without it 2.3.2's project walk reaches `$HOME/.agents`, so
  project config is off in the coven profile.
- `OPENCODE_DISABLE_FREE_MODELS=1` for the litellm preset.
- `OPENCODE_DISABLE_MODELS_FETCH=1` in tests and doctor.
- No other `OPENCODE_` variable of the caller reaches OpenCode. Only `OPENCODE_SERVER_PASSWORD`,
  `OPENCODE_SERVER_USERNAME` and `OPENCODE_DISABLE_MODELS_FETCH` pass through, and the key env
  named in the profile. `setup` refuses a key env name that starts with `OPENCODE_`, except
  `OPENCODE_API_KEY`.
- `HOME` stays as is, so git and `claude` keep working.

`OPENCODE_TEST_HOME` is undocumented. Doctor checks the leak directly, so a future version that
drops it is caught: it plants nothing, it lists the loaded skills and fails on any skill not
generated by coven.

### Commands

- `coven opencode install [--version 2.3.2]`: `npm install --prefix P/runtime/<v> --save-exact
  @opencode/cli@<v>`. It needs network: it is the one step that leaves the machine, it is started by
  the user, and it is never run by a test (tests use the already installed trial binary through
  `COVEN_OPENCODE_BIN`). It refuses a version other than the pinned one unless `--version` is given,
  and it is idempotent. npm runs with the profile env (`isolated_env` over this process's env
  without the key env): the package's postinstall runs `opencode --version`, and every 2.x start
  creates its data, config and state folders and a log under `$XDG_DATA_HOME/opencode/log`, so
  without the profile env one install would write into the user's own OpenCode folders. `HOME`,
  `PATH` and the proxy variables stay, so `.npmrc` and the npm cache work. `P/tmp` and the other
  folders the env points at are created before npm runs.
- `coven opencode setup --preset litellm --base-url URL --key-env NAME --model ROLE=ID ...` and
  `--preset opencode --model ROLE=ID ...`. Roles are `lead`, `implementer`, `reviewer`,
  `explorer`, `final-reviewer`, `title`. `lead` is required; a missing role falls back to `lead`.
  Setup validates the input (URL scheme http/https, env var name `^[A-Z_][A-Z0-9_]*$`, ids non-empty,
  no key-looking value passed as a name), writes `profile.json`, then regenerates the whole config
  dir from it. Re-running setup with no flags regenerates from the saved profile.
- `coven opencode run [-- <opencode args>]`: checks that the runtime and the profile exist, warns
  when the key env var is unset (by name only), then execs the pinned binary with the run env.
- `coven opencode doctor`: checks that make no request of their own outside the machine, each with
  a one-line fix. One case can reach out: with the opencode preset and a stored Console login in
  the profile, the canary's `serve` loads the Console provider, which fetches its remote config.
  The litellm preset, or a profile with no login, makes none.
  1. The runtime is the pinned version. `opencode --version` prints `opencode v2.3.2`; the check
     reads that line (1.x printed the bare number).
  2. `opencode debug paths`: every path is under P.
  3. The config parses and names the chosen provider and models.
  4. The key env var is set (name only).
  5. The canary, read by two checks, `plugin` and `skills`: start `serve` (plain, on 127.0.0.1
     with port 0, models fetch off) and wait for its line `server listening on <url>`. Reload the
     location, then poll `GET /api/plugin` for up to 10 s (2.3.2 lists no plugin until they have
     loaded) and expect `coven` active (`plugin`). List skills and expect only built-in ones and
     the profile's (`skills`). Then stop the server (SIGTERM, then SIGKILL after 5 s).
  6. The coven root that the plugin options name still holds `hooks/deny_secrets.py`. If not, the
     fix is "re-run `coven opencode setup`".
  7. A warning for any `.opencode/` or `opencode.json` in the cwd's ancestors that would override
     coven's config.
  8. (PR 3) A warning when the superpowers skills were not found at setup. It is check 9 in
     doctor's output, because the canary (item 5) prints two lines, `plugin` and `skills`.

`COVEN_OPENCODE_BIN` replaces the runtime path for tests and for a user who already has 2.3.2.
Check 1 still applies to it: a binary that is not 2.3.2 fails.

### Generated config (`opencode.jsonc`)

- `update: "disable"`, `model: <provider>/<lead id>`, `agents.<role>.model` for the 4 role agents
  plus `agents.title.model`, and `plugins` with coven's entry.
- litellm preset: `providers.litellm = {name, package: openai-compatible, env: [<KEY_ENV>],
  settings.baseURL, models: {<id>: {name}} for each distinct id}`.
- opencode preset: no `providers` entry beyond the model choice. The key comes from
  `OPENCODE_API_KEY` or the user's own `opencode auth login` in the profile; coven never handles it.
- `permissions`: the secret rules below.
- `experimental.subagent_depth` stays at the 2.3.2 default unless the live check shows SDD needs
  more.

### coven's OpenCode plugin (`opencode/src`, one plugin `coven`)

It grows from the join plugin. Setup copies `opencode/src` into `P/plugin/coven/` and the
generated config loads it with `plugins: [{package: "<P>/plugin/coven", options}]`. The options are
`{covenRoot, toolChoiceAuto: [<provider ids>], guardTimeoutMs}`. A plugin started without
`covenRoot` throws in setup, so it does not load, and doctor's canary fails.
- `await_agents` (PR #17, unchanged).
- `coven_ping`: a canary tool that returns the plugin version and `covenRoot`.
- **tool_choice fix**: an `http.request` hook, registered scoped to each provider id in
  `toolChoiceAuto` (the litellm preset sets `["litellm"]`). It adds `tool_choice: "auto"` when all
  of these hold:
  - the body is JSON;
  - `tools` is a non-empty array;
  - there is no `tool_choice` key.
  
  Streaming and non-streaming bodies are treated alike. `tools: []` or no `tools` passes through
  byte for byte, and so does any request to another provider. A body it cannot parse passes
  through unchanged and is logged once per session.
- **deny-secrets**: a `tool.hook("execute.before")` that translates OpenCode tool calls into the
  Claude-shaped payload `hooks/deny_secrets.py` reads:
  - `shell` to `Bash`/`command`;
  - `read` to `Read`/`file_path`;
  - `edit` and `write`;
  - `patch` as one payload per file in the patch, refused if any one is refused.
  
  It runs `sh <covenRoot>/hooks/fail-closed.sh <covenRoot>/hooks/deny_secrets.py` with the payload
  on stdin, the same entry Claude Code uses. Exit 0 allows the call. Exit 2 throws stderr as the
  refusal. Anything else blocks the call (fail closed): another exit, a timeout
  (`guardTimeoutMs`, default 5000), a spawn error or a missing `sh`. Each one throws a message that
  names the cause. The Python stays in one place, the coven checkout or install, and is not copied.
  If coven moves, the `coven root` check (item 6 above) says so.

Config `permissions` are the second layer and hold even if the plugin fails to load. They are
global, so 2.3.2 appends them to every agent, subagents included. The generated list, in order:

```jsonc
"permissions": [
  { "action": "read", "resource": "*.env", "effect": "deny" },
  { "action": "read", "resource": "*.env.*", "effect": "deny" },
  { "action": "read", "resource": "*.env.example", "effect": "allow" },
  { "action": "edit", "resource": "*.env", "effect": "deny" },
  { "action": "edit", "resource": "*.env.*", "effect": "deny" }
]
```

M1 adds no shell rules to the config. `shell` is covered by the plugin layer only, and the README says
so. A config rule cannot see what a shell line reads, so a partial copy would look like coverage
it is not.

### Content: routing, skills, agents

One source of truth, the Claude Code content in the repo. A Python generator
(`src/coven/opencode/content.py`) renders the OpenCode variant into `opencode/content/`, which is
committed, so reviewers read the real text. A test fails when the committed output is stale (the
dogfood pattern).
- Skill names: `coven:<x>` becomes `coven-<x>`. Folders become `skills/coven-<x>/SKILL.md`, with
  their scripts and prompt files.
- Tool names, by a rewrite table: `Agent`/`subagent_type` to the 2.x `subagent` tool and agent
  ids, plus `await_agents`; `Read`/`Edit`/`Write`/`Bash`/`Grep`/`Glob` to 2.x tool ids;
  `TaskCreate`/`TaskUpdate` to `todowrite`.
  - Any line that names a tool with no 2.x equivalent fails the generator. That covers
    `AskUserQuestion`, `SendMessage`, `claude --bg`, `Skill`, `CLAUDE_PLUGIN_ROOT` and
    `CLAUDE_SKILL_DIR`. The way out is a per-skill override.
  - An override is a whole-file replacement under `opencode/overrides/<skill>/<file>`, with a
    first-line comment naming the source file it replaces. A test fails when the source file
    changes and the override's recorded source hash does not, so an edit to the Claude version
    flags its OpenCode twin.
  - The generator never silently drops a line.
- Skills in M1: ceo-review, clean-code, office-hours, project-standard, shared-memory,
  requesting-code-review and subagent-driven-development. grill-me and handoff become commands
  (`commands/coven-grill-me.md`, `commands/coven-handoff.md`, with `$ARGUMENTS`), not skills.
  manager, setup and systems-map wait for M2.
- The stale-output test re-runs the generator into a temp dir and compares it byte for byte with
  `opencode/content/`. It runs in the normal Python test suite, so both checks run it. Generated
  content never includes the superpowers skills.
- SDD override: dispatch with `subagent`, keep the returned child ids in the ledger, and call
  `await_agents({ids: <pending only>, mode: "all"})` before reading a report. A
  `running`/progress report is not a result.
- superpowers skills (brainstorming, writing-plans, TDD, debugging, verification): coven does not
  vendor them in M1.
  - `setup` copies them into `P/config/opencode/skills/` at setup time. The source is the
    `superpowers@*` entry in Claude Code's installed-plugins list (`paths.installed_plugins_file()`,
    read with the same parser `coven doctor` uses), taking its `installPath`'s `skills/`.
  - A missing or unreadable list, or no superpowers entry, is a warning and not a failure. Setup
    then writes the routing with those steps marked "if installed".
  - Doctor's `superpowers` check (item 8 above) repeats the warning.
  - The copy is never committed.
- Agents: explorer, implementer, reviewer and final-reviewer become `agents/<name>.md` with
  `mode: subagent`. Read-only roles get `permissions` that deny edit and write. The model comes
  from config, not frontmatter, so setup can change it. project-manager waits for M2.
- `AGENTS.md`: routing text rendered from `instructions/routing.md`, followed by the memory-store
  pointer. M1 has no memory context hook: the text tells the model where the store is and to read
  `user/INDEX.md` and `projects/<key>/INDEX.md` itself.

### Code standard

`.coven/check.py` is plain Python and runs the same from an OpenCode `shell` tool. M1 only checks that
nothing in the fast and full paths needs `claude` for a Python or TypeScript repo, and that the
routing text names the right command.

### Fake-model harness

The e2e harness from PR #17 moves to `opencode/test/harness/` and gains a LiteLLM mode. In that
mode, a request with `tools` and no `tool_choice` gets the tool call stranded in `content` with
`finish_reason: "stop"`, as #60202 reports for vLLM; with `tool_choice` it gets a proper
`tool_calls` reply. The harness refuses to start unless only `lo` exists (PR #17 guard).

## Error handling

- install: npm missing, npm failure (exit code and the last stderr lines), a wrong version found
  after install. Each one exits non-zero and names the fix.
- setup: each invalid input is named. A partial write never happens: generate into a temp dir,
  then rename over the old config dir.
- run: runtime or profile missing means exit non-zero with "run `coven opencode install`" or
  "`setup`". A key env var that is not set is a warning and the run goes on (the opencode preset
  may use a stored login).
- doctor: every failed check prints the check, the cause and the fix. Exit 1 if any fail, 0 with
  warnings.
- Plugin: deny-secrets fails closed. The tool_choice hook fails open, passing the body unchanged,
  because it is a compatibility shim, not a guard; this is stated in its doc.

## Tests

- Python unit tests for `src/coven/opencode/`:
  - config generation for both presets (golden files);
  - input validation;
  - the run env (exact keys, no key value, `HOME` unchanged);
  - doctor checks with a fake binary;
  - install with a fake npm on PATH;
  - the content generator, plus the stale-output test.
- TS unit tests:
  - tool_choice hook: adds it only with tools and no tool_choice, only for listed providers, and
    passes non-JSON through;
  - the deny-secrets translation and its fail-closed paths;
  - `coven_ping`.
- e2e, local only, `unshare -rn`, fake model, real 2.3.2:
  1. **Isolation:** the scratch HOME holds a fake 1.x `~/.config/opencode`,
     `~/.local/share/opencode` and `~/.agents/skills`. The test snapshots the scratch HOME and
     `/tmp/opencode` (path, size, mtime and sha256 of each file) before and after a full session.
     They must be equal. The planted `~/.agents` skill must not be listed.
  2. **LiteLLM mode:** a tool call completes with coven's plugin, and the same run without the
     plugin strands the call. This is the RED proof that the hook is needed.
  3. **deny-secrets:** a model-issued `read` of `.env` is refused, and so is `cat .env` in `shell`.
  4. **SDD smoke:** the lead dispatches two subagents and calls `await_agents`, and both reports
     arrive once.
  5. **Doctor:** all checks pass on a good profile, and the canary fails when the plugin folder
     is removed.

## PRs

- PR 2, runtime: profile, install, setup, run, doctor, the tool_choice hook, the canary and the
  harness move. e2e 1, 2 and 5.
- PR 3, core content: the generator, skills, agents, routing, deny-secrets (plugin plus
  permissions) and the SDD text. e2e 3 and 4.

The e2e suite runs locally, as in PR #17, and not in CI. CI has no 2.3.2 binary and must not
download one. It is started by hand (`npm run e2e`, which exits 2 when `unshare -rn` is not
available) and its output goes in the PR. No unit test skips; the e2e is a separate command, so
nothing in CI is silently skipped.

The opencode preset does not check model ids against the Console catalog, because that needs the
network. Doctor check 3 only checks the id is non-empty, and a wrong id shows at the first prompt.

If a live fact above fails later (a 2.3.x patch changes it), the builder rules on the smallest fix
that keeps the goals and anti-goals, and records the ruling. A change that would move work between
PR 2 and PR 3 is also the builder's call. An anti-goal that cannot be kept is a stop.

## Left to the builder

The exact rewrite table entries; golden-file layout; how doctor stops the standalone server;
subprocess deadline value (spec floor: 5 s, matching the Claude Code hooks).

## CEO review (HOLD SCOPE)

- Premise: right problem. Without M1 the join tool has no user. Doing nothing leaves coven
  Claude-only.
- Leverage: the Python core and checks, deny_secrets.py, the skill bodies and the join plugin are
  reused. New code is the launcher module, the generator and two plugin hooks.
- Biggest risks, in order:
  1. Profile leaks from `~/.agents` or a background service. Mitigated by a live check first, a
     doctor check and an isolation e2e.
  2. The generator's rewrite table goes stale against skill edits. Mitigated by failing loudly on
     an unknown tool name and the stale-output test.
  3. deny-secrets on `patch`: multi-file patches must be checked per file.
- Error map: install, setup, run, doctor and plugin failures are each named above. No catch-all.
- Security:
  - the key never stored, and only its name ever printed;
  - secrets guarded twice (plugin plus config rules);
  - fail closed on the guard;
  - the tool_choice hook only touches provider requests coven configured.
- Observability: doctor is the runbook; the plugin logs one line per failed-open tool_choice parse.
- Performance: one Python subprocess per guarded tool call, the same cost as on Claude Code.
- Deferred: memory context hook (M1 points to the store in text), superpowers vendoring, Windows
  and macOS, the 1.x line, the work machine. NOT in scope: M2-M4.

## Amendment (PR 3)

Rulings made while building the content, the guard and the superpowers copy. Where they differ from
the text above, they win.

1. The rewrite table maps to the real 2.3.2 tool ids, `shell` and `patch`, not `bash` and
   `apply_patch`. deny-secrets translates `shell` and `patch`.
2. Setup writes `experimental: {todo: true}`, so the task-list wording (`todowrite`) is real.
3. Setup writes `external_directory` allow rules for the profile's `skills/*` (skill files) and,
   when the store exists, for `<store>/*` (the store the routing tells the model to read). Without
   them every skill prompt file read asks, and a headless `run` cannot answer.
4. Skill ids: coven skills are `coven-<x>`, superpowers copies are `superpowers-<x>`. The table maps
   `coven:<skill>` and `superpowers:<x>`. `coven:<agent>` maps to the bare agent id (`explorer`,
   `implementer`, `reviewer`, `final-reviewer`), which PR 2's config already uses in
   `agents.<id>.model`.
5. `the Skill tool` maps to the `skill` tool, which 2.3.2 has. The list of tools with no
   equivalent above was wrong about `Skill`.
6. An override starts with one header line, `<!-- opencode override of <repo path> sha256:<hex> -->`,
   and the generator strips it, because a SKILL.md must start with its frontmatter. Overrides live
   at `opencode/overrides/skills/<name>/<file>`, and the routing's at `opencode/overrides/routing/`.
7. The routing (`AGENTS.md`) is an override, not a table render. It names the M2 skills (manager,
   setup, systems-map) that M1 does not ship, and it tells the model to read the repo's `AGENTS.md`
   and `CLAUDE.md` itself, because PR 2 turned project config off.
8. With the superpowers skills missing, setup appends a fixed note to `AGENTS.md` that those steps
   are skipped until setup finds them, instead of rendering a second routing.
9. Setup appends the memory pointer to `AGENTS.md` from the coven config's `memory_path`, worded
   like the header of `hooks/memory_context.py`. When the folder does not exist, it appends one line
   that no store is set up and writes no store allow rule. A coven config file that cannot be read
   makes setup fail with its error.
10. The doctor warning for the superpowers skills is check 9, `superpowers`. Check 8 is
    `project config`.
11. deny-secrets covers what the Claude matcher covers: `read`, `grep`, `glob`, `shell`, `write`,
    `edit` and `patch`. Other tools (MCP, `webfetch`, `subagent`, `skill`) pass, as on Claude Code.
    A `patch` with no file header is refused. The payload's `cwd` is always the session's
    directory, and the `workdir` of `shell` is checked as text.
12. The superpowers copies get only the skill-name rewrite (`superpowers:<x>` and `coven:<x>`), never
    the forbidden-name check. Coven does not vendor or edit them, and their Claude wording is a
    known gap that the README names.
13. `.gitattributes` marks `opencode/content/**` as `linguist-generated` and `opencode/overrides/**`
    as `linguist-vendored`. The generated output stays byte-equal to the generator. Overrides are
    hand-kept copies of text from `skills/`, which is outside the Prettier scope too.

Known gap: a plugin loaded after coven can rewrite a tool call in its own `execute.before` hook
after coven has checked it. The guard sees the call as it stands when coven's hook runs.
