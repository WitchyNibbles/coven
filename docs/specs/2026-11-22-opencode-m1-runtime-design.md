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
4. In that profile, a session gets coven's routing, its skills, the 5 role agents with per-role
   models, the deny-secrets guard, and SDD on `subagent` plus `await_agents`.
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

- Package `@opencode/cli@2.3.2`. The platform binary comes from an optional dependency and is
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

Live (offline, namespaced) checks the plan's first task makes before anything leans on them: the
default channel and DB name, whether `OPENCODE_TEST_HOME` keeps `~/.agents` out, whether a
profile plugin loads with no install, whether `--standalone` works on the TUI and `serve`
commands, and whether a custom model with no catalog entry validates.

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
P/runtime/2.3.2/          npm prefix: node_modules/@opencode/cli@2.3.2 (save-exact)
P/config/opencode/        OPENCODE_CONFIG_DIR: opencode.jsonc, AGENTS.md, agents/, skills/, plugins/coven/
P/config, P/data, P/cache, P/state   XDG_CONFIG_HOME, XDG_DATA_HOME, XDG_CACHE_HOME, XDG_STATE_HOME
P/home/                   OPENCODE_TEST_HOME (empty: no ~/.agents, no ~/.claude)
P/profile.json            what setup chose: preset, base URL, key env name, models per role (no key)
```

The run env, set by coven and nothing else:
- `OPENCODE_CONFIG_DIR`, the four `XDG_*` above and `OPENCODE_TEST_HOME`.
- `OPENCODE_DISABLE_AUTOUPDATE=1`, `OPENCODE_DISABLE_CLAUDE_CODE=1`.
- `OPENCODE_DISABLE_FREE_MODELS=1` for the litellm preset.
- `OPENCODE_DISABLE_MODELS_FETCH=1` in tests and doctor.
- `HOME` stays as is, so git and `claude` keep working.

If the live check shows `OPENCODE_TEST_HOME` does not keep `~/.agents` out, doctor reports the
leak by name. The plan then picks the smallest working fix (for example an empty `~/.agents`
check, or `HOME` for the process with git's own env restored) as a recorded ruling.

### Commands

- `coven opencode install [--version 2.3.2]`: `npm install --prefix P/runtime/<v> --save-exact
  @opencode/cli@<v>`. It needs network: it is the one step that leaves the machine, it is started by
  the user, and it is never run by a test (tests use the already installed trial binary through
  `COVEN_OPENCODE_BIN`). It refuses a version other than the pinned one unless `--version` is given,
  and it is idempotent.
- `coven opencode setup --preset litellm --base-url URL --key-env NAME --model ROLE=ID ...` and
  `--preset opencode --model ROLE=ID ...`. Roles are `lead`, `implementer`, `reviewer`,
  `explorer`, `final-reviewer`, `title`. `lead` is required; a missing role falls back to `lead`.
  Setup validates the input (URL scheme http/https, env var name `^[A-Z_][A-Z0-9_]*$`, ids non-empty,
  no key-looking value passed as a name), writes `profile.json`, then regenerates the whole config
  dir from it. Re-running setup with no flags regenerates from the saved profile.
- `coven opencode run [-- <opencode args>]`: checks that the runtime and the profile exist, warns
  when the key env var is unset (by name only), then execs the pinned binary with the run env.
- `coven opencode doctor`: offline checks, each with a one-line fix.
  1. The runtime is the pinned version (`opencode --version`).
  2. `opencode debug paths`: every path is under P.
  3. The config parses and names the chosen provider and models.
  4. The key env var is set (name only).
  5. The canary: start `serve --standalone` with models fetch off, ask for the tool list or
     `plugin list`, and expect `coven_ping` and `await_agents`. Then stop it.
  6. No `.opencode/` or `opencode.json` in the cwd's ancestors that would override coven's config,
     reported as a warning.

### Generated config (`opencode.jsonc`)

- `update: "disable"`, `model: <provider>/<lead id>`, `agents.<role>.model` for the 4 role agents
  plus `agents.title.model`.
- litellm preset: `providers.litellm = {name, package: openai-compatible, env: [<KEY_ENV>],
  settings.baseURL, models: {<id>: {name}} for each distinct id}`.
- opencode preset: no `providers` entry beyond the model choice. The key comes from
  `OPENCODE_API_KEY` or the user's own `opencode auth login` in the profile; coven never handles it.
- `permissions`: the secret rules below.
- `experimental.subagent_depth` stays at the 2.3.2 default unless the live check shows SDD needs
  more.

### coven's OpenCode plugin (`opencode/src`, one plugin `coven`)

It grows from the join plugin and is installed by copy into `P/config/opencode/plugins/coven/`:
- `await_agents` (PR #17, unchanged).
- `coven_ping`: a canary tool that returns the plugin version.
- **tool_choice fix**: an `http.request` hook. When the request goes to a provider listed in the
  plugin's `toolChoiceAuto` option (the litellm preset sets it), the body is JSON, it has a
  non-empty `tools` array and no `tool_choice`, the hook adds `tool_choice: "auto"`. Every other
  request passes through byte for byte. A body it cannot parse passes through unchanged and is
  logged once.
- **deny-secrets**: a `tool.hook("execute.before")` that translates OpenCode tool calls to the
  Claude-shaped payload `hooks/deny_secrets.py` reads (`bash` to `Bash`/`command`, `read` to
  `Read`/`file_path`, `edit` and `write`, `apply_patch` per file in the patch). It runs the Python
  as a subprocess with a deadline and throws the refusal text on a block. A Python error, a timeout
  or a missing interpreter blocks the call (fail closed), with a message that names the cause.
- Plugin options come from the `plugins` entry in the generated config. The plugin is not loaded by
  auto-discovery, so its options are explicit.

Config `permissions` are the second layer and hold even if the plugin fails to load:
- `read` and `edit` of `*.env` and `*.env.*`: deny.
- `read` of `*.env.example`: allow, placed after the deny.
- bash patterns from deny-secrets that a rule can express (`cat *.env*` style): deny.

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
  - Any line that names a tool with no 2.x equivalent fails the generator, unless the skill has an
    override file. The generator never silently drops a line.
- Skills in M1: ceo-review, clean-code, office-hours, project-standard, shared-memory, handoff,
  requesting-code-review, subagent-driven-development. grill-me and handoff also become commands.
  manager, setup and systems-map wait for M2.
- SDD override: dispatch with `subagent`, keep the returned child ids in the ledger, and call
  `await_agents({ids: <pending only>, mode: "all"})` before reading a report. A
  `running`/progress report is not a result.
- superpowers skills (brainstorming, writing-plans, TDD, debugging, verification): coven does not
  vendor them in M1. `setup` copies them from the user's installed superpowers plugin when Claude
  Code's installed-plugins list names one. Otherwise routing names them as "if installed" and
  doctor warns.
- Agents: explorer, implementer, reviewer and final-reviewer become `agents/<name>.md` with
  `mode: subagent`. Read-only roles get `permissions` that deny edit and write. The model comes
  from config, not frontmatter, so setup can change it. project-manager waits for M2.
- `AGENTS.md`: routing text rendered from `instructions/routing.md`, followed by the memory-store
  pointer. M1 has no memory context hook: the text tells the model where the store is and to read
  `user/INDEX.md` and `projects/<key>/INDEX.md` itself.

### Code standard

`.coven/check.py` is plain Python and runs the same from an OpenCode bash tool. M1 only checks that
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
  1. **Isolation:** a run with a scratch HOME holding a fake 1.x `~/.config/opencode` and
     `~/.agents/skills` leaves those byte for byte unchanged, and nothing outside P is written
     except the scratch HOME's untouched files.
  2. **LiteLLM mode:** a tool call completes with coven's plugin, and the same run without the
     plugin strands the call. This is the RED proof that the hook is needed.
  3. **deny-secrets:** a model-issued `read` of `.env` is refused, and so is `cat .env` in bash.
  4. **SDD smoke:** the lead dispatches two subagents and calls `await_agents`, and both reports
     arrive once.
  5. **Doctor:** all checks pass on a good profile, and the canary fails when the plugin folder
     is removed.

## PRs

- PR 2, runtime: profile, install, setup, run, doctor, the tool_choice hook, the canary and the
  harness move. e2e 1, 2 and 5.
- PR 3, core content: the generator, skills, agents, routing, deny-secrets (plugin plus
  permissions) and the SDD text. e2e 3 and 4.

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
  3. deny-secrets on `apply_patch`: multi-file patches must be checked per file.
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
