# OpenCode M1 runtime (PR 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use coven:subagent-driven-development to implement
> this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `coven opencode install|setup|run|doctor` run a pinned OpenCode 2.3.2 in a profile that
coven owns, with coven's plugin loaded (canary `coven_ping`, the `tool_choice` fix, `await_agents`).

**Architecture:** A Python feature module `src/coven/opencode/` owns the profile folder `P`. It
validates the user's choices into `P/profile.json`, generates `P/config/opencode/opencode.jsonc`,
copies `opencode/src` into `P/plugin/coven`, and runs the binary with a fixed env. The TS plugin
(`opencode/src`) becomes one plugin with the id `coven`. It takes options, registers `coven_ping` and a
provider-scoped `http.request` hook, and refuses to load without `covenRoot`. The e2e harness
moves to `opencode/test/harness/` and gains a LiteLLM mode that strands tool calls the way #60202
reports.

**Tech Stack:** Python 3.12 stdlib (argparse, json, subprocess, urllib), TypeScript on Node 24
(`node --test` with type stripping), OpenCode 2.3.2 plugin API typed locally.

**Spec:** `docs/specs/2026-11-22-opencode-m1-runtime-design.md`. This plan covers only its
"PR 2, runtime". The content generator, skills, agent files, `AGENTS.md`, routing, deny-secrets
(hook and config `permissions`), SDD text, doctor check 8 (superpowers) and e2e 3 and 4 are PR 3.
In PR 2, setup writes only `opencode.jsonc` and the plugin copy, and PR 3 extends it.

## Global Constraints

- Commits: use the repo's public author identity, passed on the commit itself with
  `git -c user.name=<public name> -c user.email=<public address> commit ...`. Never change git
  config. No `Co-Authored-By` trailer, no "Generated with" line.
- Never write the word the local pre-commit refuses (the private memory store's name), a home
  path, a user name or an email address into tracked files.
- No network in any test. Anything that starts OpenCode runs inside `unshare -rn` with `lo`
  brought up. Tests use `COVEN_OPENCODE_BIN`. The trial binary's path is a local fact and is never
  committed. `coven opencode install` is tested only with a fake `npm` on PATH.
- Never read or write the real `~/.config/opencode`, `~/.local/share/opencode`,
  `~/.local/state/opencode`, `~/.cache/opencode`, `~/.agents` or `~/.local/bin/opencode`. Tests use
  a scratch HOME and scratch XDG dirs.
- The API key value is never stored, printed or logged. Only its env var name is.
- Code standard: run `python3 .coven/check.py fast <changed tests>` before each commit.
  Complexity ≤ 10, ≤ 300 lines a file, no suppressions of any kind. `src/coven` uses the Python
  3.12 stdlib only. TS strict as in `.coven/tsconfig.base.json`. The TS package keeps no runtime
  dependencies.
- Values from the spec: pinned version `2.3.2`; package `@opencode/cli@<v>`; roles `lead`,
  `implementer`, `reviewer`, `explorer`, `final-reviewer`, `title` (`lead` required, the others
  fall back to it); env name `^[A-Z_][A-Z0-9_]*$`; URL scheme http or https; opencode preset key env
  `OPENCODE_API_KEY`; `guardTimeoutMs` default 5000 with a floor of 5000; server stop is SIGTERM,
  then SIGKILL after 5 s; the canary needs the `coven` plugin active, then `coven_ping` and
  `await_agents`.
- `P = ${COVEN_OPENCODE_HOME:-${XDG_DATA_HOME:-~/.local/share}/coven/opencode}`, laid out exactly as
  in the spec's "Profile layout".

## Review Focus

- The caller's env already carries 1.x OpenCode settings (`OPENCODE_CONFIG`,
  `OPENCODE_CONFIG_CONTENT`, `OPENCODE_DB`, `OPENCODE_CONFIG_DIR`, `OPENCODE_DISABLE_*=0`). The run env
  must drop them, or the profile reads the user's 1.x config. Test in Task 5.
- The user runs from a repo under HOME while a real `~/.agents/skills` exists. 2.3.2's project walk
  goes up to `/` (`packages/core/src/config/discovery.ts:76-80`, `fs.up` with no `stop`). It
  then loads `$HOME/.agents` whatever `OPENCODE_TEST_HOME` says. The run env sets
  `OPENCODE_DISABLE_PROJECT_CONFIG=1` (`packages/cli/src/server-process.ts:123-125`). Tests in
  Task 5 (env) and Task 8 (e2e 1 runs from a cwd under the scratch HOME).
- A key pasted where a name goes (`--key-env sk-live...`) or a credential inside the URL
  (`https://u:secret@host/v1`, `?api_key=...`). Each is refused, and the error never echoes the
  value. Test in Task 1.
- Setup fails halfway (plugin source missing, disk error), or setup runs again over an existing
  profile. The old config dir and plugin stay whole, and no temp folder is left in `P`. Test in
  Task 4.
- `COVEN_OPENCODE_BIN` points at the user's 1.19.0 binary. Doctor fails `runtime`, and `run`
  refuses to start it, so a 1.x binary never runs with the coven env. Test in Task 5.

---

## File structure

- `src/coven/paths.py`: add `opencode_home()`.
- `src/coven/opencode/__init__.py`: re-exports `register`.
- `src/coven/opencode/profile.py`: layout, roles, validation, `profile.json` read and write.
- `src/coven/opencode/generate.py`: `opencode.jsonc` data and the atomic write of config dir and
  plugin copy.
- `src/coven/opencode/runtime.py`: binary path, version probe, run env, `run`.
- `src/coven/opencode/install.py`: `install`.
- `src/coven/opencode/canary.py`: starts and stops `serve` and reads plugins and skills.
- `src/coven/opencode/checks.py`: the doctor checks.
- `src/coven/opencode/cli.py`: argparse for the four subcommands.
- `src/coven/cli.py`: `COMMANDS` gains `opencode`.
- `tests/conftest.py`: `isolated_home` also sets `XDG_DATA_HOME` and `XDG_STATE_HOME` under the
  scratch home, and deletes `COVEN_OPENCODE_HOME`, `COVEN_OPENCODE_BIN` and every `OPENCODE_*` var.
- `tests/oc/`: `fakes.py` (fake opencode and fake npm writers) and `test_oc_*.py`. Basenames are
  unique across `tests/`, because test folders have no `__init__.py`. The folder is not named
  `opencode`, so it never shares an import name with the repo's `opencode/` folder.
- `tests/fixtures/opencode/litellm.jsonc`, `opencode.jsonc`: golden configs.
- `opencode/src/index.ts` (default export `{id: "coven", setup}`), `coven.ts` (`covenSetup`),
  `context.ts`, `options.ts`, `version.ts`, `ping.ts` and `tool-choice.ts`. `plugin.ts` keeps
  `joinSetup` and loses its default export.
- `opencode/test/harness/`: `fake-model.ts`, `chat.ts`, `guards.ts`, `history.ts`, `process.ts`,
  `namespace.ts`, `enter.ts`, `snapshot.ts`, `coven.ts` and `server.ts`.
- `opencode/test/e2e/`: the join suite (`script.ts`, `checks.ts`, `join-run.ts`, `project/`), the
  `runtime/` suite and `main.ts`.

---

### Task 1: Profile layout, roles and validation

**Files:**
- Modify: `src/coven/paths.py`, `tests/conftest.py`, `tests/test_paths.py`
- Create: `src/coven/opencode/__init__.py` (empty for now), `src/coven/opencode/profile.py`
- Test: `tests/oc/test_oc_profile.py`

**Interfaces:**
- Produces, in `paths.py`: `opencode_home() -> Path` returns `COVEN_OPENCODE_HOME` if set, else
  `${XDG_DATA_HOME:-~/.local/share}/coven/opencode`.
- Produces, in `profile.py`:
  `PINNED_VERSION: Final = "2.3.2"`;
  `ROLES: Final = ("lead", "implementer", "reviewer", "explorer", "final-reviewer", "title")`;
  `AGENT_ROLES: Final = ("explorer", "implementer", "reviewer", "final-reviewer")`;
  `OPENCODE_KEY_ENV: Final = "OPENCODE_API_KEY"`;
  `class OpencodeError(CovenError)`;
  `@dataclass(frozen=True) class Layout(root: Path)` with properties `config_home` (`P/config`),
  `config_dir` (`P/config/opencode`), `data`, `cache`, `state`, `home`, `tmp`, `plugin_dir`
  (`P/plugin/coven`) and `profile_file` (`P/profile.json`), plus `runtime_dir(version) -> Path`
  (`P/runtime/<v>`) and `runtime_binary(version) -> Path`
  (`P/runtime/<v>/node_modules/.bin/opencode`);
  `layout() -> Layout` (from `paths.opencode_home()`);
  `@dataclass(frozen=True) class Profile(preset: Literal["litellm", "opencode"], base_url: str | None,
  key_env: str, models: Mapping[str, str])`, where `models` holds only the roles given. It has
  `model(role: str) -> str` (falls back to `lead`) and `provider_id` (`"litellm"` or `"opencode"`);
  `make_profile(preset: str, base_url: str | None, key_env: str | None, model_pairs: Sequence[str]) -> Profile`
  (raises `OpencodeError`);
  `load_profile(layout: Layout) -> Profile | None`; `save_profile(layout: Layout, profile: Profile) -> None`.
- `profile.json` is exactly
  `{"format": 1, "preset": ..., "baseURL": <str|null>, "keyEnv": ..., "models": {...}}`. It is
  written to a temp file and moved into place with `os.replace`. For the opencode preset, `keyEnv`
  is `OPENCODE_API_KEY` and `baseURL` is `null`.

- [ ] **Step 1: Write the failing tests.**
  - `paths`: `COVEN_OPENCODE_HOME` wins. Otherwise `XDG_DATA_HOME/coven/opencode`, and without
    that `~/.local/share/coven/opencode`.
  - `Layout(tmp)` gives the exact paths listed above.
  - `make_profile("litellm", "http://127.0.0.1:4000/v1", "GATEWAY_KEY", ["lead=big", "explorer=small"])`:
    `model("explorer") == "small"`, `model("reviewer") == "big"`, `model("title") == "big"`, and
    `provider_id == "litellm"`.
  - `make_profile("opencode", None, None, ["lead=x"]).key_env == "OPENCODE_API_KEY"`.
  - Each case raises `OpencodeError`, with the message containing the quoted phrase:
    - no `lead=` gives `--model lead=ID is required`;
    - `boss=x` gives `unknown role`, and the message lists all six roles;
    - `lead=a` twice gives `given twice`;
    - `lead=` gives `is empty`;
    - `lead=a b` gives `no spaces`;
    - `nolabel` gives `ROLE=ID`;
    - litellm without a URL gives `needs --base-url`, and without a key env `needs --key-env`;
    - the opencode preset with a URL gives `takes no --base-url`, and with a key env
      `takes no --key-env`;
    - `ftp://h/v1` gives `http:// or https://`;
    - `https://u:secret@h/v1`, `https://h/v1?api_key=secret` and `https://h/v1#x` each give
      `must not carry`;
    - the key env names `sk-live-abc123`, `gateway_key`, `1KEY` and a 65-character name each give
      `name, not its value`.
  - For every refused URL and key env, the secret-looking text (`secret`, `sk-live-abc123`) is
    not in `str(error)`.
  - A URL whose path does not end in `/v1` is accepted with a warning on stderr: `OpenCode appends
    /chat/completions`. A single trailing `/` is stripped.
  - `save_profile` then `load_profile` round-trips. The file's JSON equals the exact shape above,
    and no value in it equals the value of the env var it names (the test sets
    `GATEWAY_KEY=sk-test-value`). `load_profile` on a missing file returns `None`. On bad JSON it
    raises `OpencodeError` naming the file and `re-run coven opencode setup`.
- [ ] **Step 2: Run** `uv run pytest tests/oc/test_oc_profile.py tests/test_paths.py -q`.
  Expected: FAIL (module not found).
- [ ] **Step 3: Implement** `paths.opencode_home` and `profile.py`. Update `isolated_home` in
  `tests/conftest.py` as described in File structure.
- [ ] **Step 4: Run** `python3 .coven/check.py fast tests/oc/test_oc_profile.py tests/test_paths.py`.
  Expected: PASS.
- [ ] **Step 5: Commit** `feat(opencode): profile layout and setup input checks`.

### Task 2: The `coven` plugin: options, canary and self-check

**Files:**
- Create: `opencode/src/index.ts`, `coven.ts`, `context.ts`, `options.ts`, `version.ts` and
  `ping.ts`
- Modify: `opencode/src/plugin.ts` (drop the default export), `opencode/test/plugin-fixture.ts`
  (fake `options`, `tool.list` and the `http.request` hook), `opencode/test/plugin.test.ts`,
  `opencode/test/e2e/project/.opencode/plugins/coven-join/index.ts`
- Test: `opencode/test/options.test.ts`, `opencode/test/ping.test.ts`,
  `opencode/test/plugin.test.ts`, `tests/oc/test_oc_plugin_version.py`

**Interfaces:**
- Produces:
  `export const VERSION = "0.1.0"` (`version.ts`, equal to `.claude-plugin/plugin.json`'s version);
  `interface CovenOptions { covenRoot: string; toolChoiceAuto: readonly string[]; guardTimeoutMs: number }`;
  `class OptionsError extends Error` (stable `name`, as the join errors have);
  `parseOptions(raw: unknown): CovenOptions`;
  `pingTool(covenRoot: string): ToolDefinition` with name `coven_ping`, input
  `{type: "object", properties: {}, additionalProperties: false}` and `options: {codemode: false}`.
  Its content is `JSON.stringify({plugin: "coven", version: VERSION, covenRoot})`;
  `interface HttpRequestEvent { readonly sessionID: string; readonly agent: string;
    readonly model: { readonly id: string; readonly providerID: string; readonly variant?: string };
    readonly kind: "primary" | "compaction" | "title" | "generate"; request: Request }`. This is
  copied from 2.3.2 `packages/plugin/src/promise/session.ts:93-99`, with `Model.Ref` from
  `packages/schema/src/model.ts:20-24`;
  `interface CovenContext extends PluginContext` (in `context.ts`) adds
  `readonly options?: Readonly<Record<string, unknown>>`, the overload
  `session.hook(name: "http.request", callback: (e: HttpRequestEvent) => Promise<void> | void,
  options?: { providerID?: string }): Promise<unknown>` (2.3.2 `ModelHooks`,
  `promise/registration.ts:10-18`) and `tool.list(): Promise<readonly { id: string }[]>`;
  `interface CovenDeps { now: () => number; log: (line: string) => void }`;
  `covenSetup(ctx: CovenContext, deps: CovenDeps, rawOptions: unknown): Promise<() => void>`;
  `index.ts` default export `{ id: "coven", setup: (ctx: CovenContext) => covenSetup(ctx,
  {now: () => Date.now(), log: (line) => console.error(line)}, ctx.options) }`. It re-exports
  nothing else, and setup copies it as the plugin's entry file.
- `covenSetup` order: `parseOptions` (throws first), then `joinSetup(ctx, {now})`, then add
  `pingTool`, then one `http.request` registration per entry of `toolChoiceAuto` (Task 3 fills in
  the callback; this task registers `toolChoiceHook` as a stub that returns), then a self-check.
  The self-check reads `ctx.tool.list()` and throws
  `coven: tools not registered: <missing ids>` unless both `coven_ping` and `await_agents` are
  listed. It calls the join cleanup before throwing. 2.3.2 has no HTTP tool list, so a plugin
  reported `active` by `GET /api/plugin` proves both tools are there.
- `parseOptions` rules:
  - `covenRoot` is a non-empty absolute path string;
  - `toolChoiceAuto` is an optional array of non-empty strings, default `[]`;
  - `guardTimeoutMs` is an optional integer in `5000..600000`, default `5000`;
  - any other key is refused.
  Each message starts with `coven plugin options:`. A missing `covenRoot` reads
  `coven plugin options: covenRoot is missing; run coven opencode setup`.
- The join e2e project file becomes a wrapper whose default export is `{id: "coven", setup: (ctx) =>
  covenSetup(ctx, deps, {covenRoot: <repo root from import.meta.url>})}`, so the join suite still
  loads through auto-load with no options entry.

- [ ] **Step 1: Write the failing tests.**
  - `parseOptions({covenRoot: "/c"})` deep-equals
    `{covenRoot: "/c", toolChoiceAuto: [], guardTimeoutMs: 5000}`. A full valid object passes
    through.
  - `OptionsError` is thrown for each of: `undefined`, `{}`, `covenRoot` `""`, `"rel/path"` and `1`;
    `toolChoiceAuto` `"litellm"`, `[""]` and `[1]`; `guardTimeoutMs` `4999`, `600001`, `5000.5` and
    `"5000"`; the extra key `covenroot`.
  - `pingTool("/c").execute({}, ctx)` content parses to `{plugin: "coven", version: VERSION, covenRoot: "/c"}`.
  - Plugin, using the fake ctx:
    - `index.ts` default has `id === "coven"`;
    - setup with `options: {covenRoot: "/c", toolChoiceAuto: ["litellm"]}` registers the tools
      `await_agents` and `coven_ping` and exactly one `http.request` hook, with
      `{providerID: "litellm"}`;
    - `toolChoiceAuto: []` registers no `http.request` hook;
    - no options rejects with `/covenRoot is missing/` and registers no tool;
    - a fake `tool.list` that omits `coven_ping` rejects with `/tools not registered: coven_ping/`,
      and the join subscription is aborted.
  - The existing plugin tests still pass, through `index.ts` or `joinSetup`.
  - Python `test_plugin_version_matches_coven`: the `VERSION` string in `opencode/src/version.ts`
    equals `version` in `.claude-plugin/plugin.json`.
- [ ] **Step 2: Run** `cd opencode && node --test test/options.test.ts test/ping.test.ts test/plugin.test.ts`.
  Expected: FAIL.
- [ ] **Step 3: Implement** the files above.
- [ ] **Step 4: Run** `python3 .coven/check.py fast opencode/test/options.test.ts opencode/test/ping.test.ts
  opencode/test/plugin.test.ts tests/oc/test_oc_plugin_version.py`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(opencode): one coven plugin with options and the coven_ping canary`.

### Task 3: The `tool_choice` hook

**Files:**
- Create: `opencode/src/tool-choice.ts`
- Modify: `opencode/src/coven.ts` (register the real hook)
- Test: `opencode/test/tool-choice.test.ts`

**Interfaces:**
- Consumes: `HttpRequestEvent`, `CovenDeps.log`.
- Produces:
  `addToolChoice(body: string): string | undefined`. It returns the new body only when the body
  parses to a JSON object, `tools` is a non-empty array, and the object has no own `tool_choice`
  key (a `null` value counts as present). The new body is the same object with
  `tool_choice: "auto"` added. In every other case it returns `undefined`;
  `toolChoiceHook(log: (line: string) => void): (event: HttpRequestEvent) => Promise<void>`. It
  reads `await event.request.clone().text()` (a null body passes). When `addToolChoice` gives a
  body, it sets `event.request` to a new `Request` with the same URL, method, headers (without
  `content-length`) and signal, and the new body. Otherwise it leaves `event.request` as the same
  object, so 2.3.2 sends the original bytes (`packages/core/src/session/model-request.ts:376-400`).
  A body that does not parse as JSON is logged once per `sessionID` with
  `coven: tool_choice: request body is not JSON; sent unchanged (session <id>)`.
- The file's doc comment says that the hook fails open: it is a compatibility shim for #60202, not
  a guard.

- [ ] **Step 1: Write the failing tests.**
  - `addToolChoice` on `{"model":"m","stream":true,"tools":[{...}],"messages":[]}` gives the same
    object plus `tool_choice: "auto"`. The same holds with `stream: false`.
  - `addToolChoice` returns `undefined` for each of: `tools: []`; no `tools`; `tools: "x"`;
    `tool_choice: "none"`; `tool_choice: null`; `[]`; `"text"`; `not json`.
  - Hook on a POST `Request` with tools: `event.request` is a new object, its `text()` parses with
    `tool_choice === "auto"`, the `authorization` and `content-type` headers survive, and the
    method is POST.
  - Hook on a body with no tools: `event.request` is the identical object (`assert.equal`), and
    `log` is not called.
  - Hook on a non-JSON body: identical object. Two requests in session `s1` and one in `s2` give
    exactly two `log` lines.
  - Hook on a GET with no body: identical object.
- [ ] **Step 2: Run** `cd opencode && node --test test/tool-choice.test.ts`. Expected: FAIL.
- [ ] **Step 3: Implement** `tool-choice.ts`. Wire `toolChoiceHook(deps.log)` into `covenSetup`.
- [ ] **Step 4: Run** `python3 .coven/check.py fast opencode/test/tool-choice.test.ts opencode/test/plugin.test.ts`.
  Expected: PASS.
- [ ] **Step 5: Commit** `feat(opencode): add tool_choice auto for listed providers (#60202)`.

### Task 4: Config generation and `coven opencode setup`

**Files:**
- Create: `src/coven/opencode/generate.py`, `src/coven/opencode/cli.py`,
  `tests/fixtures/opencode/litellm.jsonc`, `tests/fixtures/opencode/opencode.jsonc`
- Modify: `src/coven/opencode/__init__.py` (export `register`), `src/coven/cli.py` (`COMMANDS`)
- Test: `tests/oc/test_oc_generate.py`, `tests/oc/test_oc_setup.py`

**Interfaces:**
- Consumes: `Layout`, `Profile`, `make_profile`, `save_profile` and `load_profile` (Task 1);
  `opencode/src/` with `index.ts` (Task 2).
- Produces:
  `GUARD_TIMEOUT_MS: Final = 5000`;
  `plugin_source() -> Path` (`paths.root() / "opencode" / "src"`);
  `plugin_options(profile: Profile, coven_root: Path) -> dict[str, object]`, which gives
  `{"covenRoot": str(coven_root), "toolChoiceAuto": ["litellm"] | [], "guardTimeoutMs": 5000}`;
  `config_data(profile: Profile, layout: Layout, coven_root: Path) -> dict[str, object]`;
  `render_config(profile, layout, coven_root) -> str` returns `json.dumps(data, indent=2) + "\n"`.
  It is plain JSON with no comments, so doctor and the e2e guard parse it with a JSON parser;
  `write_profile(profile: Profile, layout: Layout, coven_root: Path, source: Path) -> None`;
  `register(subparsers)` adds `opencode` with the subcommand `setup` (Tasks 5 and 6 add the rest).
- `config_data` keys, in this order:
  - `update: "disable"`;
  - `model: "<provider>/<lead>"`;
  - `agents`: `{explorer, implementer, reviewer, final-reviewer, title}`, each `{"model": "<provider>/<id>"}`;
  - for litellm only, `providers.litellm = {"name": "LiteLLM", "package":
    "@opencode/ai/providers/openai-compatible", "env": [<keyEnv>], "settings": {"baseURL": <url>},
    "models": {<id>: {"name": <id>} for each distinct id, sorted}}`;
  - `plugins: [{"package": str(layout.plugin_dir), "options": plugin_options(...)}]`.
  There is no `permissions` key in PR 2.
- `write_profile`:
  1. build `P/.setup-<token>/config-opencode/opencode.jsonc`;
  2. copy every `*.ts` under `source` into `P/.setup-<token>/plugin-coven/`, keeping the tree;
  3. `save_profile`;
  4. for the config dir and then the plugin dir, rename the live dir to `.old`, rename the new one
     in, and remove `.old`;
  5. create `P/data`, `cache`, `state`, `home` and `tmp`.
  `P/.setup-*` is removed on every exit, and any failure before step 4 leaves the live dirs as
  they were. PR 3 adds `AGENTS.md`, `agents/`, `skills/` and `commands/` to the same temp tree.
- `coven opencode setup` flags: `--preset {litellm,opencode}`, `--base-url URL`, `--key-env NAME`
  and `--model ROLE=ID` (repeatable). With no flags it regenerates from `profile.json`. With no
  saved profile it fails with `no profile yet; run coven opencode setup --preset ...`. Any flag
  without `--preset` fails with `pass --preset with the other flags, or no flags to regenerate`. It
  prints `profile: <P>`, the preset line `preset: litellm (<url>, key from $<NAME>)` or
  `preset: opencode (key from $OPENCODE_API_KEY or a stored login)`, one `<role>: <id>` line per
  role in `ROLES` order, and `next: coven opencode doctor`.

- [ ] **Step 1: Write the failing tests.**
  - Golden litellm: input `--preset litellm --base-url http://127.0.0.1:4000/v1 --key-env
    GATEWAY_KEY --model lead=big --model explorer=small --model title=small`. `render_config`,
    with `str(layout.root)` replaced by `<P>` and the coven root by `<COVEN_ROOT>`, equals
    `tests/fixtures/opencode/litellm.jsonc` byte for byte. The golden holds `model: litellm/big`,
    `agents.explorer` and `title` on `litellm/small`, `models: {"big": {"name": "big"}, "small":
    {"name": "small"}}` and `toolChoiceAuto: ["litellm"]`.
  - Golden opencode, with `--preset opencode --model lead=big`: no `providers` key,
    `model: opencode/big`, every agent on `opencode/big`, `toolChoiceAuto: []`.
  - `cli.main(["opencode", "setup", ...litellm flags])` returns 0 and writes
    `P/config/opencode/opencode.jsonc` and `P/profile.json`. Every `*.ts` in `opencode/src` is
    byte-equal under `P/plugin/coven`, `index.ts` included, and there is no non-`.ts` file.
  - Re-run with no flags: same bytes. A stale `P/plugin/coven/old.ts` is gone afterwards.
  - Failure keeps the old state: monkeypatch `plugin_source` to a missing dir. The command
    returns 1 with an error naming it. The previous `opencode.jsonc` and `profile.json` bytes are
    unchanged, and no `P/.setup-*` remains.
  - With `GATEWAY_KEY=sk-test-value` set, the value is not in stdout, stderr or any file under `P`.
  - Each flag error from Task 1 exits 1 with `coven: ` and the message. `--model` without
    `--preset` gives the "pass --preset" message.
- [ ] **Step 2: Run** `uv run pytest tests/oc/test_oc_generate.py tests/oc/test_oc_setup.py -q`.
  Expected: FAIL.
- [ ] **Step 3: Implement** `generate.py`, `cli.py` (setup only) and the `COMMANDS` entry. Write
  the golden files from the data above.
- [ ] **Step 4: Run** `python3 .coven/check.py fast tests/oc/test_oc_generate.py tests/oc/test_oc_setup.py tests/test_cli.py`.
  Expected: PASS.
- [ ] **Step 5: Commit** `feat(opencode): coven opencode setup writes the profile config and plugin`.

### Task 5: Run env, `coven opencode run` and `coven opencode install`

**Files:**
- Create: `src/coven/opencode/runtime.py`, `src/coven/opencode/install.py`, `tests/oc/fakes.py`
- Modify: `src/coven/opencode/cli.py`
- Test: `tests/oc/test_oc_runtime.py`, `tests/oc/test_oc_install.py`

**Interfaces:**
- Consumes: `Layout`, `Profile`, `load_profile` and `PINNED_VERSION`.
- Produces:
  `PASSED_THROUGH: Final = ("OPENCODE_SERVER_PASSWORD", "OPENCODE_SERVER_USERNAME", "OPENCODE_DISABLE_MODELS_FETCH")`;
  `binary_path(layout: Layout) -> Path` returns `COVEN_OPENCODE_BIN` when it is set, else
  `layout.runtime_binary(PINNED_VERSION)`;
  `binary_version(binary: Path, env: Mapping[str, str]) -> str | None`, the stripped first line of
  `--version`, or `None` when the binary cannot run, with a 10 s timeout;
  `run_env(profile: Profile, layout: Layout, base: Mapping[str, str], *, offline: bool) -> dict[str, str]`;
  `run(args: Sequence[str], *, exec_fn: Callable[[str, list[str], dict[str, str]], object] = os.execve) -> int`;
  `install(layout: Layout, version: str, *, timeout: float = 900.0) -> Path` (raises `OpencodeError`).
  `fakes.py` has `fake_opencode(dir: Path, version: str = "2.3.2") -> Path`, a Python script that
  answers `--version` and `debug paths` (the latter prints the ten names of 2.3.2's `debug paths`
  built from the env, as probe 1 shows). It also has `fake_npm(dir: Path, log: Path) -> Path`,
  which logs its argv and writes a `fake_opencode` at `<prefix>/node_modules/.bin/opencode` that
  reports `$FAKE_OPENCODE_VERSION` (default `2.3.2`). When `$FAKE_NPM_EXIT` is set, it prints
  three stderr lines and exits with that code.
- `run_env`:
  - starts from `base` minus every `OPENCODE_*` key, except `PASSED_THROUGH` and the profile's
    `key_env`;
  - sets `OPENCODE_CONFIG_DIR`, `XDG_CONFIG_HOME`, `XDG_DATA_HOME`, `XDG_CACHE_HOME`,
    `XDG_STATE_HOME`, `OPENCODE_TEST_HOME` and `TMPDIR` to the layout paths;
  - sets `OPENCODE_DISABLE_AUTOUPDATE=1`, `OPENCODE_DISABLE_CLAUDE_CODE=1` and
    `OPENCODE_DISABLE_PROJECT_CONFIG=1`;
  - sets `OPENCODE_DISABLE_FREE_MODELS=1` for litellm only, and `OPENCODE_DISABLE_MODELS_FETCH=1`
    when `offline`;
  - keeps `HOME` and `PATH` as they are.
- `run`:
  - a missing binary gives `the OpenCode runtime is missing; run coven opencode install`;
  - a binary version other than 2.3.2 gives `<binary> reports <v>; coven needs 2.3.2 (run coven
    opencode install, or point COVEN_OPENCODE_BIN at 2.3.2)`;
  - no profile gives `no profile yet; run coven opencode setup`;
  - an unset key env warns `$<NAME> is not set; the run goes on`, by name only;
  - otherwise it calls `exec_fn(str(binary), [str(binary), *args], run_env(..., offline=False))`.
  CLI: `coven opencode run [-- ARGS...]` (`nargs=argparse.REMAINDER`, with a leading `--` dropped).
- `install`:
  - `--version` must match `^\d+\.\d+\.\d+$`, default 2.3.2;
  - it skips npm when `binary_version(runtime_binary(v)) == v` and prints `already installed`;
  - otherwise it runs `npm install --prefix <runtime_dir(v)> --save-exact @opencode/cli@<v>`;
  - errors: `npm not found on PATH; install Node.js with npm and re-run`; `npm install failed
    (exit <n>): <last 5 stderr lines>`; `installed opencode reports <x>, expected <v>`;
  - a version other than 2.3.2 warns `run and doctor use 2.3.2 only`.

- [ ] **Step 1: Write the failing tests.**
  - `run_env` with litellm and `offline=False` has exactly the keys and values listed above, plus
    `HOME` and `PATH` from `base`, and no `OPENCODE_DISABLE_MODELS_FETCH`. With `offline=True`
    it adds that one key. The opencode preset has no `OPENCODE_DISABLE_FREE_MODELS`.
  - `base` holding `OPENCODE_CONFIG=/x`, `OPENCODE_CONFIG_CONTENT={}`, `OPENCODE_DB=/x.db`,
    `OPENCODE_CONFIG_DIR=/old` and `OPENCODE_DISABLE_PROJECT_CONFIG=0`: none of the old values
    survive. `OPENCODE_SERVER_PASSWORD`, the key env (`GATEWAY_KEY`) and, for the opencode preset,
    `OPENCODE_API_KEY` pass through.
  - `run` with a fake exec: the argv is `[bin, "run", "--format", "json", "hi"]` from
    `cli.main(["opencode", "run", "--", "run", "--format", "json", "hi"])`, and the env equals
    `run_env(..., offline=False)`.
  - `run` refuses each of: no binary; `COVEN_OPENCODE_BIN` pointing at a fake that reports
    `1.19.0` (exec is never called); no profile. With the key env unset, stderr names
    `$GATEWAY_KEY` and exec is still called. With it set to `sk-test-value`, that value is in no
    output.
  - `install` with a fake npm first on PATH: the log holds exactly `install --prefix
    <P>/runtime/2.3.2 --save-exact @opencode/cli@<v>` with v = 2.3.2, and the return value is the binary. A
    second call adds no log line. `FAKE_NPM_EXIT=3` gives an error containing `exit 3` and the
    three stderr lines. `FAKE_OPENCODE_VERSION=2.3.1` gives `reports 2.3.1, expected 2.3.2`.
    `PATH` without npm gives `npm not found`. `--version 2.x` is refused. `--version 2.4.0`
    installs into `runtime/2.4.0` and warns.
- [ ] **Step 2: Run** `uv run pytest tests/oc/test_oc_runtime.py tests/oc/test_oc_install.py -q`.
  Expected: FAIL.
- [ ] **Step 3: Implement** `runtime.py`, `install.py`, the `run` and `install` subcommands, and
  `fakes.py`.
- [ ] **Step 4: Run** `python3 .coven/check.py fast tests/oc/test_oc_runtime.py tests/oc/test_oc_install.py`.
  Expected: PASS.
- [ ] **Step 5: Commit** `feat(opencode): coven opencode run and install`.

### Task 6: `coven opencode doctor`

**Files:**
- Create: `src/coven/opencode/canary.py`, `src/coven/opencode/checks.py`
- Modify: `src/coven/opencode/cli.py`
- Test: `tests/oc/test_oc_canary.py`, `tests/oc/test_oc_doctor.py`

**Interfaces:**
- Consumes: `binary_path`, `binary_version`, `run_env(offline=True)`, `load_profile`,
  `config_data` and `fakes.fake_opencode`.
- Produces:
  `type Fetch = Callable[[str, str, str], object]` (method, URL, password), which returns parsed
  JSON;
  `@dataclass(frozen=True) class ServerView(plugins: list[dict[str, object]], skills: list[dict[str, object]])`;
  `read_server(binary: Path, env: Mapping[str, str], cwd: Path, *, fetch: Fetch = http_json,
  start_timeout: float = 30.0, grace: float = 5.0) -> ServerView`;
  `stop_server(process: subprocess.Popen[str], grace: float) -> None` (SIGTERM, wait `grace`,
  then SIGKILL, then wait);
  `@dataclass(frozen=True) class Outcome(status: Literal["ok", "warn", "fail"], detail: str, fix: str = "")`;
  `run_doctor(layout: Layout, cwd: Path, *, read: Callable[..., ServerView] = read_server) -> list[tuple[str, Outcome]]`.
- `read_server`:
  - starts `<bin> serve --hostname 127.0.0.1 --port 0` with
    `env | {OPENCODE_SERVER_PASSWORD: secrets.token_urlsafe(24)}` and `cwd = layout.config_dir`.
    That cwd is the global config dir, where 2.3.2 skips the project walk (`discovery.ts:78-80`);
  - reads stdout until `^server listening on (http://127\.0\.0\.1:\d+)$`;
  - `POST /api/location/reload` (so skills planted before boot are listed, probe 2), then
    `GET /api/plugin` and `GET /api/skill`, taking `data` from each, with basic auth
    `opencode:<password>`;
  - always calls `stop_server`;
  - the password is never printed.
- Checks, in this order and with these names. Each failure has the fix shown:
  1. `runtime`: `binary_version == "2.3.2"`. Fix: `run coven opencode install`, or for
     `COVEN_OPENCODE_BIN`, `point COVEN_OPENCODE_BIN at 2.3.2`.
  2. `paths`: every path that `debug paths` prints is under `P`. The failure names the first one
     outside. Fix: `re-run coven opencode setup`.
  3. `config`: `opencode.jsonc` parses, and its `model`, each `agents.<role>.model` and (for
     litellm) `providers.litellm.settings.baseURL` and `env` equal `config_data` from
     `profile.json`. Every id is non-empty. Fix: `re-run coven opencode setup`.
  4. `key`: a `warn` naming `$<NAME>` when it is unset, else `ok` with the name. Never the value.
  5. `plugin`: an entry with `id == "coven"` and `state.status == "active"`. When it has failed,
     the detail shows the plugin's `state.error` on one line. Fix: `re-run coven opencode setup`.
  6. `skills`: every skill path starts with `skill://` or lies under `P/config/opencode/skills/`.
     The failure names the foreign ids. Fix: `remove them from <P>/home/.agents/skills or report
     an OpenCode isolation change`.
  7. `coven root`: `<covenRoot from opencode.jsonc>/hooks/deny_secrets.py` exists. Fix:
     `re-run coven opencode setup`.
  8. `project config`: a `warn` naming each `.opencode/`, `opencode.json` or `opencode.jsonc` in
     cwd and its ancestors, with `ignored in the coven profile (project config is off)`.
  Checks 5 and 6 share one `read_server` call. When the runtime check fails, or the server does
  not start in 30 s, both fail with that cause. A check that raises fails alone with a one-line
  cause, as `coven doctor` does. Output is one line per check:
  `<status> <name>: <detail>`, followed by ` (fix: <fix>)` when the check fails or warns. The exit
  code is 1 if any check fails, else 0.

- [ ] **Step 1: Write the failing tests.**
  - A good profile (setup from Task 4, a fake 2.3.2 binary through `COVEN_OPENCODE_BIN`, the key
    set, `read` injected to return `ServerView([{"id": "coven", "state": {"status": "active"}}],
    [{"id": "opencode", "path": "skill://opencode/SKILL.md"}])`): the eight names come in the
    order above, all `ok`, and the exit code is 0.
  - One failure each, with the status and the fix text:
    - the fake reports `1.19.0`, which fails `runtime`;
    - `debug paths` prints `tmp /tmp/opencode`, which fails `paths` naming `/tmp/opencode`;
    - an edited `model` fails `config`;
    - an unset key gives a `warn` on `key` with `$GATEWAY_KEY` and exit code 0;
    - a plugin `{"state": {"status": "failed", "error": "boom"}}` fails `plugin` with `boom`;
    - no `coven` entry fails `plugin`;
    - a skill at `<P>/home/.agents/skills/foreign/SKILL.md` fails `skills` with `foreign`;
    - `covenRoot` pointed at an empty dir fails `coven root`;
    - cwd under a dir that holds `.opencode/` gives a `warn` on `project config` naming it.
  - With no profile, `config` fails with `run coven opencode setup`, and the others still print.
  - `read_server` with a fake binary whose `serve` prints `server listening on
    http://127.0.0.1:1` and then ignores SIGTERM, with `fetch` injected (no socket opens) and
    `grace=0.2`: it returns the view. The process is dead afterwards (`poll()` is not `None`), the
    call takes under 3 s, and the fetch calls are `POST .../api/location/reload`, then
    `GET .../api/plugin`, then `GET .../api/skill`.
  - A fake `serve` that prints nothing, with `start_timeout=0.3`: `plugin` and `skills` fail with
    `did not start`.
  - The key value and the server password appear in no output line.
- [ ] **Step 2: Run** `uv run pytest tests/oc/test_oc_canary.py tests/oc/test_oc_doctor.py -q`.
  Expected: FAIL.
- [ ] **Step 3: Implement** `canary.py` (`http_json` uses `urllib.request` with a 10 s timeout),
  `checks.py` and the `doctor` subcommand.
- [ ] **Step 4: Run** `python3 .coven/check.py fast tests/oc/test_oc_canary.py tests/oc/test_oc_doctor.py`.
  Expected: PASS.
- [ ] **Step 5: Commit** `feat(opencode): coven opencode doctor with the plugin canary`.

### Task 7: Harness move, LiteLLM mode and the e2e entry

**Files:**
- Move (`git mv`): `opencode/test/e2e/{fake-model,guards,history}.ts` to `opencode/test/harness/`
- Create: `opencode/test/harness/chat.ts` (`ChatMessage`, `ToolCall`, `Reply` and `textOf`, moved
  out of `script.ts`), `process.ts` (`exec` and `cleanEnv`, moved out of `e2e/opencode.ts`),
  `namespace.ts` (`interfaceNames`, `onlyLoopback` and `NAMESPACE_MESSAGE` from `guards.ts`, plus
  `bringLoopbackUp` from `main.ts`), `enter.ts`, `snapshot.ts`, `coven.ts` and `server.ts`
- Rename: `opencode/test/e2e/opencode.ts` to `join-run.ts` (only `runScenario` is left)
- Modify: `opencode/package.json` (`e2e` script), `opencode/test/e2e/{main,script,checks}.ts`,
  `opencode/test/guards.test.ts`
- Test: `opencode/test/fake-model.test.ts`, `opencode/test/snapshot.test.ts`,
  `opencode/test/guards.test.ts`

**Interfaces:**
- Produces:
  `type FakeMode = "plain" | "litellm"`;
  `type Decide = (messages: readonly ChatMessage[], toolNames: readonly string[]) => Reply`;
  `interface RecordedRequest { tools: number; hasToolChoice: boolean; toolChoice: unknown; stream: boolean }`;
  `interface FakeModel { port: number; requests(): readonly RecordedRequest[]; close(): Promise<void> }`;
  `startFakeModel(options: { decide: Decide; mode?: FakeMode; port?: number }): Promise<FakeModel>`
  (the default mode is `plain`, the default port `FAKE_PORT`, and port `0` picks one);
  `replyFor(raw: string, mode: FakeMode, decide: Decide): { reply: Reply | { kind: "stranded"; text: string }; recorded: RecordedRequest }`,
  a pure function that `startFakeModel` calls once per chat request, so unit tests open no
  socket;
  `strandedText(calls: readonly ToolCall[]): string`, which joins one
  `<tool_call>{"name":<n>,"arguments":<args>}</tool_call>` per call with `\n`;
  `cleanEnv(home: string): Record<string, string>` (as before, plus `TMPDIR: <home>/tmp`);
  `snapshot(roots: readonly string[]): Promise<Map<string, string>>`, where the value is
  `absent`, `dir`, or `<size> <mtimeMs> <sha256>`, for every entry under each root;
  `snapshotDiff(before, after): string[]`;
  `coven(args: string[], options: { cwd: string; env: Record<string, string>; timeoutMs: number }): Promise<Finished>`,
  which runs `sh <repo>/bin/coven ...`;
  `withServer<T>(options: { cwd: string; env: Record<string, string> }, body: (api: Api) => Promise<T>): Promise<T>`.
  It starts `coven opencode run -- serve --hostname 127.0.0.1 --port 0` with a random
  `OPENCODE_SERVER_PASSWORD` and waits for the listening line. `Api` has
  `get(path: string): Promise<unknown>` and `post(path: string): Promise<unknown>`, with basic
  auth. It stops the server with SIGTERM, then SIGKILL after 5 s;
  `profileProblem(config: ProjectConfig): string | undefined`. It gives a reason unless the only
  provider is `litellm` and its baseURL is on `http://127.0.0.1`;
  `enter.ts` acts only when `COVEN_E2E_ENTER=1`. If `unshare -rn true` fails, it prints
  `unshare -rn is not available: <first stderr line>` and exits 2. Otherwise it runs
  `unshare -rn node test/e2e/main.ts <argv>` with `COVEN_E2E=1`, stdio inherited, and exits with
  that status.
- LiteLLM mode: a request with a non-empty `tools`, no `tool_choice` key and a `calls` reply from
  `decide` gets `strandedText(calls)` as text content with `finish_reason: "stop"`. With a
  `tool_choice` key it gets the normal `tool_calls` reply. Every request is recorded. The
  `plain` mode behaves as PR #17 did.
- `package.json`: `"e2e": "COVEN_E2E_ENTER=1 node test/harness/enter.ts"`. `main.ts` takes the
  suite names `join` and `runtime` as argv (default: both, runtime first), runs them one after
  the other, and numbers the `ok` and `not ok` lines across both. Without `COVEN_OPENCODE_BIN` it
  prints `skipped: set COVEN_OPENCODE_BIN` and exits 0 (PR #17 behaviour). No file under `test/`
  except `main.ts` and `enter.ts` has a top-level side effect, and each of those two is gated by its
  env var, so `node --test` stays a no-op for them.

- [ ] **Step 1: Write the failing tests.**
  - `replyFor`, with no socket: a `litellm` mode request with tools and no `tool_choice`, where
    `decide` returns a call to `coven_ping`, gives `{kind: "stranded", text: strandedText(...)}`.
    The same request with `tool_choice: "auto"` gives the `calls` reply. The `plain` mode gives
    `calls` in both cases. `recorded` has `tools: 1` and the right `hasToolChoice` each time. A
    request with no tools gives `tools: 0`.
  - `snapshot` over a temp tree: an unchanged tree gives an empty diff. Changing a file's bytes or
    its mtime, adding a file, or deleting one names that path. A missing root records `absent`.
  - `profileProblem`: `{providers: {litellm: {settings: {baseURL: "http://127.0.0.1:5/v1"}}}}`
    gives `undefined`. A second provider, or a non-loopback URL, gives a reason.
  - The existing `guards.test.ts` passes against the moved modules.
- [ ] **Step 2: Run** `cd opencode && node --test test/fake-model.test.ts test/snapshot.test.ts test/guards.test.ts`.
  Expected: FAIL.
- [ ] **Step 3: Move and implement.** The join suite keeps its five checks unchanged. Its
  `script.ts` `decide` is passed to `startFakeModel`.
- [ ] **Step 4: Run** `python3 .coven/check.py fast` with all `opencode/test/*.test.ts`. Then run
  `cd opencode && COVEN_OPENCODE_BIN=<the 2.3.2 trial binary> npm run e2e -- join`. Expected:
  PASS, and the join suite's five `ok` lines.
- [ ] **Step 5: Commit** `test(opencode): shared e2e harness with a LiteLLM mode`.

### Task 8: Runtime e2e (1 isolation, 2 LiteLLM mode, 5 doctor) and docs

**Files:**
- Create: `opencode/test/e2e/runtime/profile.ts`, `ping-script.ts`, `checks.ts` and `run.ts`
- Modify: `opencode/test/e2e/main.ts` (the runtime suite), `opencode/README.md`, `CHANGELOG.md`
  (`## Unreleased`)

**Interfaces:**
- Consumes: everything in Task 7, the four `coven opencode` commands, and `coven_ping`.
- Produces:
  `interface RuntimeWorld { home: string; profile: string; work: string; env: Record<string, string>; model: FakeModel }`;
  `makeWorld(root: string): Promise<RuntimeWorld>`;
  `decidePing: Decide`. When `coven_ping` is offered and has no result yet, it calls it with `{}`.
  After the result, it replies `PING DONE ` plus the result text. With no tools it replies `title`.
  When the tool is not offered, it replies `NO PING TOOL`;
  `runtimeChecks(world: RuntimeWorld, binary: string): Promise<{ title: string; problem: string | undefined }[]>`.
- `makeWorld`:
  - the scratch HOME is `<root>/home`. It holds a fake 1.x `~/.config/opencode/opencode.json`
    (a provider `onex` at `http://127.0.0.1:9/v1`), a file in `~/.local/share/opencode/`, and
    `~/.agents/skills/leak-e2e/SKILL.md`;
  - `work = <home>/work/repo`;
  - `P = <root>/profile` through `COVEN_OPENCODE_HOME`;
  - `env` is built from nothing: `cleanEnv(home)`, `COVEN_OPENCODE_HOME`,
    `COVEN_OPENCODE_BIN=<binary>`, `COVEN_E2E_KEY=offline` (a fixed placeholder, not a key) and
    `OPENCODE_DISABLE_MODELS_FETCH=1`;
  - the fake model starts in `litellm` mode on port 0 with `decidePing`;
  - it runs `coven opencode setup --preset litellm --base-url http://127.0.0.1:<port>/v1
    --key-env COVEN_E2E_KEY --model lead=fake`;
  - it refuses to go on when `profileProblem` reports a problem with the generated
    `P/config/opencode/opencode.jsonc`.
- A session is `coven opencode run -- run --standalone --format json PING`, run in `work`, then
  `coven opencode run -- session export --standalone <id>` and `readHistory`.
- The checks, run in this order in one world:
  1. **isolation**: snapshot `<home>` and `/tmp/opencode`. Run one session. Then, in
     `withServer` with cwd `work`, `POST /api/location/reload`, `GET /api/skill` and
     `GET /api/provider`. Snapshot again. Pass when the diff is empty, no skill id is
     `leak-e2e`, and no provider id is `onex`.
  2. **tool_choice**: the session from step 1 is the GREEN run. Pass when its history has a
     `coven_ping` tool run with status `completed` whose output holds `"covenRoot"` and `VERSION`,
     the final text starts with `PING DONE`, and every recorded request with `tools > 0` has
     `toolChoice === "auto"`. For the RED run, rewrite `toolChoiceAuto` to `[]` in
     `opencode.jsonc` (the plugin stays loaded, so `coven_ping` is still offered and only the hook
     differs) and run a second session. Pass when its history has no `coven_ping` tool run, its
     final text contains `strandedText([{name: "coven_ping", args: {}}])`, and its requests with
     tools have `hasToolChoice === false`.
  3. **doctor**:
     - run `coven opencode setup` with no flags to restore the config, then `coven opencode
       doctor`: exit 0, and no line starts with `fail`;
     - remove `P/plugin/coven`: doctor exits 1 and the `plugin` line starts with `fail`;
     - run setup again, then plant `P/home/.agents/skills/foreign/SKILL.md`: doctor exits 1 and
       the `skills` line names `foreign`. This shows the reload sees a skill planted before boot.
- If 2.3.2 writes into the project dir during a session, exclude `<home>/work` from the snapshot
  and record that ruling in the PR. If the reload does not list a skill planted before boot, use
  the wake from probe 2 (create and remove a dir under the watched skills dir) in both `server.ts`
  and `canary.py`, and record that.

- [ ] **Step 1: Write** the runtime suite with all three checks. Run it against a profile without
  the hook (set `toolChoiceAuto` to `[]` before the GREEN session too).
- [ ] **Step 2: Run** `cd opencode && COVEN_OPENCODE_BIN=<the 2.3.2 trial binary> npm run e2e -- runtime`.
  Expected: check 2 `not ok`, because its GREEN run strands. This is the RED proof that the hook is
  needed.
- [ ] **Step 3: Restore** the hook in the GREEN run and fix what the run shows.
- [ ] **Step 4: Run** `COVEN_OPENCODE_BIN=<the 2.3.2 trial binary> npm run e2e`. Expected: the
  three runtime `ok` lines, then the five join `ok` lines. Save the output for the PR body. Run
  `npm run e2e` with `unshare` absent from PATH. Expected: exit 2.
- [ ] **Step 5: Docs.** `opencode/README.md` covers:
  - the profile layout;
  - the four commands, with `install` the one networked step;
  - project config is off in the coven profile;
  - the `tool_choice` hook fails open by design;
  - `coven opencode doctor` and its checks;
  - the e2e command and its exit codes 0, 1 and 2;
  - PR 3 adds skills, agents, routing and deny-secrets.
  Add one `CHANGELOG.md` line under `## Unreleased`.
- [ ] **Step 6: Run** `python3 .coven/check.py fast tests/test_readme.py tests/test_privacy.py`
  with all `opencode/test/*.test.ts`. Expected: PASS.
- [ ] **Step 7: Commit** `test(opencode): runtime e2e for isolation, tool_choice and doctor`.
