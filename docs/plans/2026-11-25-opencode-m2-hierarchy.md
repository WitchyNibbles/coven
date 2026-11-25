# coven on OpenCode, M2: the GM/PM hierarchy. Implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use coven:subagent-driven-development to implement
> this plan task by task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** a GM in an OpenCode TUI attached to a coven-owned `opencode serve` runs PMs as served
sessions in their repos (making their own worktrees), through coven plugin tools, with no prompt
that waits on nobody, and with every route the guards can see from a model to the server, its
password, the provider key or production refused.

**Architecture:** Python owns the server lifecycle (`coven opencode serve`, `gm`, doctor) and the
guards (`hooks/opencode_server_guard.py`, `prod_guard.py --no-approvals`, the existing
`pm_permissions.py`). The TypeScript plugin owns roles, the GM and PM tools, the shell env
(role and scrub), the ask-to-deny hook and turn-end notices, all in-process through `ctx`.

**Tech Stack:** Python 3 (argparse, stdlib `urllib`, `secrets`, `subprocess`), pytest; TypeScript
on Node (`node --test`), OpenCode 2.3.2 plugin API; the offline fake-model e2e harness.

**Spec:** [docs/specs/2026-11-25-opencode-m2-hierarchy-design.md](../specs/2026-11-25-opencode-m2-hierarchy-design.md)

## Global Constraints

- OpenCode 2.3.2 only, from coven's runtime; no other version, no network in any test.
- No company gateway configuration anywhere: no proxy URLs, keys or model names. Tests use
  `http://127.0.0.1:49999/v1`, `TEST_KEY` and model ids like `fake`.
- No personal identifiers in tracked files: no home-folder paths, no emails. The privacy test
  (`tests/test_privacy.py`) reads any `/home/<word>` as one, so tests and docs name the profile's
  folders without that shape.
- Never type coven's session-role variable name in a Bash command (the guard refuses it). In
  code, Python has no constant for it: it is the key of the `env` dict in `pm_settings`
  (`src/coven/pm/profile.py`). The TS constant `ROLE_ENV` in `opencode/src/pm/role.ts` has a
  contract test that reads that dict.
- Every hook fails closed: a throw, a timeout, a spawn error, an unknown exit or a failed role
  lookup refuses.
- Hook timeouts match `hooks/hooks.json`: deny-secrets and the server guard 5 000 ms,
  pm-permissions 15 000 ms, the production guard 30 000 ms.
- The server binds `127.0.0.1` only. The password is 32 random bytes, hex, file mode 0600. The
  serve folder is `<profile>/state/serve/` (`Layout.serve_dir`).
- PM prefixes: PM to GM `QUESTION`, `PROD-STEP`, `DRAFT`, `DONE`, `BLOCKED`; GM to PM `ANSWER`,
  `APPROVED`, `DENIED`, `TASK` (from `src/coven/pm/text.py`).
- Metadata shape: `{"coven": {"role": "gm" | "pm" | "former-gm", "code": "<code>"}}`; a PM finds
  the GM as the live root session (no `parentID`) holding `role: gm`.
- Notice text: `[coven notice] pm-<code> ended a turn (<outcome>) without a message. Last line:
  <first 200 chars>`.
- Checks: `python3 .coven/check.py fast <files>` during a task; the e2e by hand with
  `npm run e2e` (in `opencode/`), output pasted in the PR. No suppressions.

## Review Focus

1. Two shell calls with the same command and cwd from a PM and the GM at once: the PM's shell
   must get `pm`, never nothing (Task 6, `test_same_key_different_roles_gets_pm`).
2. A model that tries `ps eww`, `cat /proc/<pid>/environ`, the serve folder's password through a
   pipe, `bash -c`, a relative path or a symbolic link under `tmp/`: refused (Task 4).
3. A PM that removes or edits `systems/guard.json` or coven's config from the shell to switch the
   production guard off: refused (Task 5, Task 7).
4. A stale pid file after a crash, with a different process now on that pid: `serve start`
   must not refuse forever (Task 2, `test_stale_pid_of_other_process_is_replaced`).
5. The user opens a new GM session while PMs run: after its claim, PM messages and notices reach
   it, a GM's child is never taken for the GM, and a PM with no GM gets a clear tool error, not a
   silent drop (Task 8).

## File structure

Python:
- `src/coven/opencode/profile.py` (modify): `project-manager` role, `Layout.serve_dir`.
- `src/coven/opencode/generate.py`, `content.py` (modify): plugin options, the PM agent's model,
  the PM agent.
- `src/coven/opencode/serve.py` (create): server files, start, stop, status.
- `src/coven/opencode/canary.py` (modify): its HTTP and listening-line helpers become shared.
- `src/coven/opencode/cli.py`, `runtime.py`, `checks.py`, `check_types.py`, `checks_server.py`
  (modify): `serve`, `gm`, the `run` refusal, doctor checks.
- `hooks/opencode_server_guard.py` (create), `hooks/prod_guard.py`, `hooks/fail-closed.sh`
  (modify).
- `opencode/overrides/agents/project-manager.md`, `opencode/overrides/skills/manager/SKILL.md`
  (create); `src/coven/opencode/rewrite.py` (modify: ship `manager`).

TypeScript, under `opencode/src/`:
- `options.ts`, `guard.ts`, `coven.ts`, `context.ts`, `deny-secrets.ts` (modify).
- `pm/role.ts`, `pm/shell-env.ts`, `pm/asks.ts`, `pm/prefixes.ts`, `pm/tools.ts`, `pm/rules.ts`,
  `pm/state.ts`, `pm/notices.ts`, `pm/setup.ts` (create).
- Tests in `opencode/test/pm-*.test.ts`; e2e in `opencode/test/e2e/hierarchy/` and
  `opencode/test/e2e/served/`.

Docs: `opencode/README.md`, `docs/known-gaps.md`, `CHANGELOG.md`.

Suggested PRs (the builder may regroup): PR A Tasks 0-5 (Python), PR B Tasks 6-10 (plugin),
PR C Tasks 11-13 (content, e2e, docs).

---

### Task 0: Live checks the design rests on

No product code. A scratch profile under `/tmp`, offline, fake model, as in the spike. Each step
records what it saw.

- [ ] **Step 1: TUI attach.** Start `opencode serve` (2.3.2) with a password and find how the TUI
  attaches (`opencode --help`, `opencode attach --help`): open it under `script -qc` against the
  server and confirm a lead session is created on the server (`GET /api/session` lists it).
- [ ] **Step 2: Throw in `create.before`.** In a throwaway plugin, throw from
  `shell.hook("create.before")` and confirm the shell command does not run.
- [ ] **Step 3: Shell hook match.** In the same plugin, log `execute.before`'s `input.command`,
  `input.workdir` and the location directory, and `create.before`'s `command` and `cwd`, for a
  shell call with and without `workdir`; record whether they are equal.
- [ ] **Step 4: Permissions across a restart.** Create a session with `permissions`
  (`shell * deny`), restart the server for real, and check the rule still refuses.
- [ ] **Step 5: Server process.** Record what `node_modules/.bin/opencode` is (script or binary),
  which pid holds the listening port (`ss -ltnp`), and what that pid's `/proc/<pid>/exe` names.
- [ ] **Step 6: Plugin session calls.** From the plugin, `ctx.session.create` in another location
  with `agent`, `metadata` and `permissions`; then `ctx.session.list` and check it returns
  `metadata`.
- [ ] **Step 7: Shared module state.** Two locations on one server: confirm a module-scope value
  set by one location's plugin instance is seen by the other's.
- [ ] **Step 8:** Append the results to the spec as `## Amendment (Task 0)` and commit
  `docs(specs): M2 live checks`.

What a failure changes:
- Step 1 fails: stop and send `BLOCKED` (D1 goes back to the user).
- Step 2 fails: Task 6 uses the spec's fallback (empty `env` plus a refusal in `execute.before`).
- Step 3 differs: Task 6 keys the queue on what both hooks share, as Step 3 found. If nothing
  matches, stop: the role cannot reach the shell (an anti-goal), so `BLOCKED`.
- Step 4 fails: Task 10's `pmSetup` re-applies `pmRules` (Task 9) with `ctx.session.update` to
  every PM session when the plugin starts in the GM's location. Task 12's restart check covers it.
- Step 5: Task 2's `running` matches the process Step 5 found.
- Step 6 fails: `BLOCKED` (the launch tool cannot be built as designed).
- Step 7 fails: `pm/state.ts` keeps the shared state in a file under the profile's `tmp/`
  instead, keyed by session id. Record this as a ruling.

### Task 1: Profile role and plugin options

**Files:**
- Modify: `src/coven/opencode/profile.py:18`, `src/coven/opencode/generate.py` (`plugin_options`,
  `config_data`), `opencode/src/options.ts`, `opencode/README.md` (the role list)
- Test: `tests/oc/test_oc_profile.py`, `tests/oc/test_oc_generate.py`, `tests/oc/test_oc_setup.py`,
  `opencode/test/options.test.ts`

**Interfaces:**
- Produces: `ROLES` gains `"project-manager"`; `Profile.model("project-manager")` falls back to
  lead. `Layout.serve_dir: Path` (`<profile>/state/serve`). `config_data` writes
  `agents["project-manager"].model`. `plugin_options(profile, coven_root, layout, store)` adds
  `keyEnv: str`, `profileDir: str`, `serveDir: str`, `store: str | None`. TS `CovenOptions` adds
  `keyEnv: string`, `profileDir: string`, `serveDir: string`, `store: string | null`.

- [ ] **Step 1: Write the failing tests**
  - `test_project_manager_model_defaults_to_lead`: profile with only `lead=fake` →
    `model("project-manager") == "fake"`; with `--model project-manager=fake-pm` → `"fake-pm"`.
  - `test_config_data_sets_the_project_manager_model`: with `project-manager=fake-pm` the
    generated config has `agents["project-manager"]["model"]` ending in `fake-pm`.
  - `test_plugin_options_carry_key_env_profile_and_store`: `keyEnv == "TEST_KEY"`,
    `profileDir == str(layout.root)`, `serveDir == str(layout.serve_dir)`, `store` is the store
    path or `None`.
  - `options.test.ts` "parses keyEnv, profileDir, serveDir and store": a missing `keyEnv` throws
    `OptionsError`; `store: null` is accepted.
  - Update the setup and profile output tests for the new role.
- [ ] **Step 2: Run** `python3 .coven/check.py fast tests/oc/test_oc_profile.py tests/oc/test_oc_generate.py tests/oc/test_oc_setup.py` and `node --test test/options.test.ts` (in `opencode/`). Expected: FAIL.
- [ ] **Step 3: Implement** the role, `serve_dir`, the agent model in `config_data`, the option
  keys and `parseOptions`; add `project-manager` to the README's role list.
- [ ] **Step 4: Run** the same. Expected: PASS.
- [ ] **Step 5: Commit** `feat(opencode): project-manager role and plugin options for M2`.

### Task 2: Server lifecycle

**Files:**
- Create: `src/coven/opencode/serve.py`
- Modify: `src/coven/opencode/canary.py` (export its HTTP and listening-line helpers),
  `src/coven/opencode/cli.py`, `src/coven/opencode/runtime.py`
- Test: `tests/oc/test_oc_serve.py`, `tests/oc/test_oc_canary.py` (stays green)

**Interfaces:**
- Consumes: `Layout.serve_dir` (Task 1); the canary's `http_json`, `USERNAME`, basic-auth and
  `LISTENING` parsing, reused, not copied.
- Produces:
  - `serve.ServeFiles(password: Path, port: Path, pid: Path, log: Path)`;
    `serve.files(layout: Layout) -> ServeFiles`.
  - `serve.ServerInfo(pid: int, port: int)`; `serve.running(layout) -> ServerInfo | None` (a live
    pid that is the server process Task 0 Step 5 found).
  - `serve.start(layout, profile, port: int | None = None, *, spawn=subprocess.Popen,
    wait_s: float = 20.0) -> ServerInfo`; `serve.stop(layout, *, grace_s: float = 10.0) -> bool`.
  - `serve.status(layout) -> list[str]` (the lines `serve status` prints).
  - `serve.ServeError(Exception)` with the messages below.
  - CLI: `coven opencode serve start [--port N] | stop | status`.

- [ ] **Step 1: Write the failing tests** (a fake `opencode` script in `tmp_path` that prints the
  listening line for `--port 0` and answers `/api/session` when the password matches)
  - `test_start_writes_password_port_pid_and_answers`: files exist, password is 64 hex chars and
    mode `0o600`, pid file names the fake server's pid (not a wrapper), port file matches the
    listening line.
  - `test_password_is_kept_across_starts`.
  - `test_second_start_is_refused`: `ServeError("a server is already running on port N")`.
  - `test_stale_pid_of_other_process_is_replaced`: pid file names a live `sleep` → start works.
  - `test_stop_kills_and_removes_pid_and_port_keeps_password`.
  - `test_start_times_out_with_log_path`: fake never answers → `ServeError` naming the serve log.
  - `test_explicit_port_in_use_is_named`: `--port N` with N taken → "port N in use".
  - `test_status_lists_pm_sessions`: fake answers two sessions, one with `parentID` and one with
    `metadata.coven.role == "pm"`, code `x1` → status prints `running pid P port N` then one PM
    line with `pm-x1`.
  - `test_status_shows_a_waiting_ask`: a pending permission for the PM session → its line has
    `ASK WAITING (bug)` and the request id.
  - `test_run_refuses_while_server_runs`: `coven opencode run` → exit 1, "a coven server is
    running; use `coven opencode gm`".
- [ ] **Step 2: Run** `python3 .coven/check.py fast tests/oc/test_oc_serve.py tests/oc/test_oc_canary.py`. Expected: FAIL.
- [ ] **Step 3: Implement** with `runtime.run_env` for the child env plus
  `OPENCODE_SERVER_PASSWORD`; `start_new_session=True`; `--port 0` by default.
- [ ] **Step 4: Run** the same. Expected: PASS.
- [ ] **Step 5: Commit** `feat(opencode): coven opencode serve start/stop/status`.

### Task 3: `coven opencode gm` and doctor

**Files:**
- Modify: `src/coven/opencode/cli.py`, `src/coven/opencode/checks.py`,
  `src/coven/opencode/check_types.py` (`Server`, `read`), `src/coven/opencode/checks_server.py`
- Test: `tests/oc/test_oc_gm.py` (create), `tests/oc/test_oc_doctor_server.py` (extend)

**Interfaces:**
- Consumes: `serve.start`, `serve.running`, the canary's HTTP helper (Task 2).
- Produces: `coven opencode gm`; `checks_server.REQUIRED_OPERATIONS: tuple[str, ...]` (the
  operationIds the plugin and CLI call: session create, list, get, prompt, interrupt, update,
  permission list and reply, event).

- [ ] **Step 1: Write the failing tests**
  - `test_gm_starts_server_when_stopped_then_execs_attach`: the exec'd argv is the attach form
    Task 0 found, with the URL; `OPENCODE_SERVER_PASSWORD` is in the TUI env only.
  - `test_gm_stops_when_plugin_not_active`: `/api/plugin` without `coven` active → exit 1,
    "coven's plugin is not active on the server", no exec.
  - doctor: `test_doctor_server_password_mode`, `test_doctor_openapi_has_required_operations`
    (one missing id → the check fails naming it), `test_doctor_uses_the_running_server_and_starts_no_canary`
    (with a server running, the canary's `serve` is never spawned),
    `test_doctor_starts_the_canary_when_no_server_runs`, `test_doctor_names_a_too_old_python3`.
- [ ] **Step 2: Run** `python3 .coven/check.py fast tests/oc/test_oc_gm.py tests/oc/test_oc_doctor_server.py`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the same. Expected: PASS.
- [ ] **Step 5: Commit** `feat(opencode): coven opencode gm and server doctor checks`.

### Task 4: The server guard

**Files:**
- Create: `hooks/opencode_server_guard.py`
- Modify: `hooks/fail-closed.sh` (a refusing fallback for `opencode-server-guard`)
- Test: `tests/hooks/test_opencode_server_guard.py`, `tests/hooks/test_fail_closed.py`

**Interfaces:**
- Produces: `refusal(payload: dict, profile: Path, serve: Path, coven_root: Path, port: int | None) -> str | None`;
  `main(argv) -> int` reading the payload on stdin, `--profile DIR --serve DIR --coven-root DIR`
  (Task 7 passes all three from the plugin options), exit 0 allow, exit 2 with the text on
  stderr. The port comes from `<serve>/port` at call time. Command parsing reuses
  `hooks/shell_words` (as `pm_permissions.py` does), including nested `bash -c`/`sh -c` and
  pipelines. Paths resolve against the payload `cwd`, then `..`, `~`, `$HOME` and symbolic links
  (`Path.resolve`), before the protected-set check. In the tests `<serve>` is `<profile>/state/serve`.

- [ ] **Step 1: Write the failing tests** (parametrized; each refusal next to a near allow)
  - Refused, the `opencode` allowlist: `opencode api session.list`, `opencode session list`,
    `opencode run --session x`, `opencode db`, `opencode export x`, `opencode import f`,
    `opencode --server http://127.0.0.1:4096`.
  - Refused, the port: `curl -u x http://127.0.0.1:<port>/api/session`, `curl localhost:<port>`,
    `bash -c "curl 127.0.0.1:<port>"`.
  - Refused, secrets: `cat <serve>/password`, `cd <profile>/state && cat serve/password`,
    `cat <profile>/tmp/../state/serve/password`, `grep -r . <serve>`, `cat /proc/123/environ`,
    `ps eww`, `ps -E`, `ps aux e`, Read tool on `<serve>/password`, Read of a symbolic link under
    `<profile>/tmp/` that points at `<serve>/password`.
  - Refused, the protected set: Write/Edit on `<profile>/config/opencode/opencode.jsonc`,
    `echo x > <profile>/config/opencode/agents/a.md`, Write on `<profile>/plugin/coven/index.ts`,
    `sqlite3 <profile>/data/opencode/opencode.db`, Edit of `<profile>/profile.json`, Write
    through a symbolic link under `<profile>/tmp/` that points at `<profile>/config/`, Write on
    `<coven-root>/hooks/deny_secrets.py`, Edit of `<coven-root>/opencode/src/guard.ts`,
    `ln -s <profile>/config <profile>/tmp/c`.
  - Refused, coven commands: `coven opencode setup --model lead=x`, `coven opencode serve stop`,
    `coven opencode gm`, `coven opencode install`.
  - Allowed: `opencode --version`, `opencode --help`, `curl http://127.0.0.1:8080/` (another
    port), `ps aux`, `cat /proc/cpuinfo`, Read of `<profile>/config/opencode/AGENTS.md` (reading
    config with the Read tool is fine; writing it is not), `npm test`, Write on
    `<profile>/tmp/body.md`, Write in the profile's `cache` folder, Edit of
    `<other>/coven-worktree/hooks/deny_secrets.py` (a worktree of the coven repo outside
    `<coven-root>`), `coven opencode doctor`, `coven pm --help`.
  - `test_no_port_file_still_refuses_opencode_api`.
  - `test_refusal_text_points_to_gm_tools`: contains "coven_pm_list".
  - `test_fail_closed.py`: with a too-old `python3`, `opencode_server_guard.py` refuses (exit 2).
- [ ] **Step 2: Run** `python3 .coven/check.py fast tests/hooks/test_opencode_server_guard.py tests/hooks/test_fail_closed.py`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the same. Expected: PASS.
- [ ] **Step 5: Commit** `feat(hooks): guard the coven OpenCode server, its password and profile`.

### Task 5: Deny-only production guard

**Files:**
- Modify: `hooks/prod_guard.py` (argument parsing in `run()`, the approval lookup)
- Test: `tests/hooks/test_prod_guard.py`

**Interfaces:**
- Produces: `prod_guard.py --no-approvals`: approvals are treated as absent; the refusal text
  gains "a PM on OpenCode never runs a production step: send it to the GM as a PROD-STEP". `run()`
  accepts `--no-approvals` next to the existing `--check`; any other argument is still a usage
  error. The PreToolUse payload on stdin is unchanged, so no `--pre-tool-use` flag (PM call).

- [ ] **Step 1: Write the failing tests** (the existing store fixtures, with a made-up target
  host such as `db.example.invalid`)
  - `test_no_approvals_refuses_a_command_a_live_approval_covers`.
  - `test_without_the_option_the_live_approval_still_allows`.
  - `test_no_approvals_still_allows_a_command_no_target_matches`.
  - `test_no_approvals_with_no_store_allows` (as today without a store).
  - `test_no_approvals_unreadable_guard_json_blocks`.
  - `test_no_approvals_with_role_pm_refuses_a_shell_write_to_guard_json` and
    `..._to_coven_config` (`rm <store>/systems/guard.json`, `echo {} > <config>`).
  - `test_unknown_argument_is_still_a_usage_error`.
- [ ] **Step 2: Run** `python3 .coven/check.py fast tests/hooks/test_prod_guard.py`. Expected: FAIL.
- [ ] **Step 3: Implement** the option; touch nothing else in the matcher.
- [ ] **Step 4: Run** `python3 .coven/check.py fast tests/hooks/` (the adversarial suite too). Expected: PASS.
- [ ] **Step 5: Commit** `feat(hooks): prod guard deny-only mode for OpenCode PMs`.

### Task 6: Roles and the shell env

**Files:**
- Create: `opencode/src/pm/role.ts`, `opencode/src/pm/shell-env.ts`, `opencode/src/pm/state.ts`
- Modify: `opencode/src/context.ts` (the session, event and permission types the plugin now uses)
- Test: `opencode/test/pm-role.test.ts`, `opencode/test/pm-shell-env.test.ts`

**Interfaces:**
- Produces:
  - In `context.ts`: `SessionLike { id: string; parentID?: string; directory: string; metadata?: Record<string, unknown> }`;
    `SessionApi` (the `ctx.session` calls the plugin uses: `create`, `get`, `list`, `prompt`,
    `interrupt`, `update`); `PermissionRule { permission: string; pattern: string; action: "allow" | "deny" | "ask" }`;
    `EvaluateEvent` (the `permission.hook("evaluate")` event, with `sessionID` and a mutable
    `effect` and `reason`).
  - `ROLE_ENV: string` (equal to the key of `pm_settings`'s `env` dict; the contract test reads
    `src/coven/pm/profile.py`).
  - `type Role = "gm" | "pm" | "former-gm"`; `interface RoleInfo { role: Role; code?: string }`.
  - `resolveRole(sessionID: string, get: (id: string) => Promise<SessionLike | undefined>): Promise<RoleInfo | undefined>`
    (own `metadata.coven`, then up the `parentID` chain). A `get` that throws or returns nothing
    for a session in the chain, a cycle, or more than 32 hops returns `{role: "pm"}` (fail closed).
    A chain that ends cleanly with no role returns undefined.
  - `isRoot(s: SessionLike): boolean`.
  - `pm/state.ts`: module-scope `sharedState` with `queue: ShellQueue`, `messaged: Set<string>`,
    `stopped: Set<string>` (one per server process, Task 0 Step 7).
  - `class ShellQueue { record(e: {sessionID: string; role: Role | undefined; command: string; cwd: string}, now: number): void; take(command: string, cwd: string, now: number): Role | undefined }` (key `command + "\0" + cwd`, as Task 0 Step 3 confirms; entries expire after 30 000 ms; mixed roles for one key → `"pm"`).
  - `scrubEnv(env: Record<string, string | undefined>, keyEnv: string): void` (deletes
    `OPENCODE_SERVER_PASSWORD`, `OPENCODE_PASSWORD`, `keyEnv`, and any `OPENCODE_*` name ending in
    `_PASSWORD`, `_TOKEN` or `_KEY`).
  - `shellEnvHooks(deps): { executeBefore(event): Promise<void>; createBefore(event): void }`.

- [ ] **Step 1: Write the failing tests**
  - `role from own metadata`, `role from parent chain`, `no role`, `cycle → pm`,
    `get throws → pm`, `get returns nothing mid-chain → pm`.
  - `test_same_key_different_roles_gets_pm`; `oldest entry first`; `expired entry ignored`;
    `no entry sets no role`.
  - `scrub removes password and key, keeps PATH and HOME`.
  - `createBefore sets ROLE_ENV for a pm shell and scrubs first`.
  - `a scrub that throws makes createBefore throw` (the spawn is refused, Task 0 Step 2).
  - `a gm shell gets no ROLE_ENV` (the store's pre-commit hook refuses any value).
  - `ROLE_ENV matches the Python value`.
- [ ] **Step 2: Run** `node --test test/pm-role.test.ts test/pm-shell-env.test.ts`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the same, then `python3 .coven/check.py fast opencode`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(opencode): session roles and a scrubbed shell env`.

### Task 7: Guards wired per session

**Files:**
- Modify: `opencode/src/guard.ts` (run any hook script with extra args, child env and its own
  timeout), `opencode/src/deny-secrets.ts` (the payload `cwd`), `opencode/src/coven.ts` (the
  self-check refuses a missing or too-old `python3`)
- Create: `opencode/src/pm/asks.ts`
- Test: `opencode/test/guard.test.ts`, `opencode/test/pm-asks.test.ts`, `opencode/test/plugin.test.ts`

**Interfaces:**
- Consumes: `resolveRole`, the types in `context.ts` (Task 6), options (Task 1).
- Produces:
  - `SpawnFn = (command: string, args: readonly string[], env?: NodeJS.ProcessEnv) => GuardChild`.
  - `runHookScript(covenRoot: string, script: string, extraArgs: readonly string[], payload: Payload, timeoutMs: number, env?: NodeJS.ProcessEnv, spawn?: SpawnFn): Promise<void>`.
  - `HOOK_TIMEOUTS_MS = { denySecrets: 5000, serverGuard: 5000, pmPermissions: 15000, prodGuard: 30000 }`.
  - The payload `cwd` is `input.workdir` when the call has one, else the location directory.
  - One `execute.before` hook that, per mapped payload, runs in order: `deny_secrets.py` (all
    sessions), `opencode_server_guard.py --profile <profileDir> --serve <serveDir> --coven-root <covenRoot>`
    (all sessions), then for PM-tree sessions `pm_permissions.py --pre-tool-use` and
    `prod_guard.py --no-approvals`, each with `ROLE_ENV=pm` in the child env.
  - `askToDeny(resolve): (event: EvaluateEvent) => Promise<void>`: `effect "ask"` → `"deny"` for
    PM-tree sessions (including a failed lookup), reason "a PM may not wait on a prompt: send it
    to the GM as a QUESTION or PROD-STEP".

- [ ] **Step 1: Write the failing tests** (with `guard-fake.ts`)
  - `order: deny-secrets, server guard, pm-permissions, prod guard for a pm session`.
  - `gm session runs only deny-secrets and the server guard`.
  - `a failed role lookup runs the pm guards`.
  - `pm-permissions and prod guard children get ROLE_ENV=pm`.
  - `the payload cwd is the call's workdir`.
  - `each hook gets its own timeout` (a fake that answers at 10 s passes pm-permissions and
    times out the server guard).
  - `a failing pm-permissions refuses with its stderr`; `a prod guard timeout refuses`.
  - `ask becomes deny for a pm child`; `ask becomes deny when the lookup fails`;
    `ask stays ask for the gm`; `allow stays allow`.
  - `selfCheck fails when python3 is too old`.
- [ ] **Step 2: Run** `node --test test/guard.test.ts test/pm-asks.test.ts test/plugin.test.ts`. Expected: FAIL.
- [ ] **Step 3: Implement**, keeping `denySecretsHook`'s behaviour for existing callers.
- [ ] **Step 4: Run** the same and `python3 .coven/check.py fast opencode`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(opencode): server, pm-permissions and prod guards per session; asks deny in PM trees`.

### Task 8: Prefixes, `coven_gm_claim` and `coven_send`

**Files:**
- Create: `opencode/src/pm/prefixes.ts`, `opencode/src/pm/tools.ts` (first two tools)
- Test: `opencode/test/pm-prefixes.test.ts`, `opencode/test/pm-send.test.ts`

**Interfaces:**
- Consumes: `resolveRole`, `isRoot`, `sharedState.messaged`, `SessionApi` (Task 6).
- Produces: `PM_PREFIXES`, `GM_PREFIXES`, `headPrefix(text: string): string | undefined` (first
  line, the same rule as `parse_head`); `findGm(session: SessionApi): Promise<SessionLike | undefined>`
  (root sessions only); `gmClaimTool(deps)`, `sendTool(deps)` where
  `deps = { session: SessionApi; resolve: typeof resolveRole }`.

- [ ] **Step 1: Write the failing tests**
  - `prefix lists match src/coven/pm/text.py` (reads the file, extracts both tuples).
  - claim: `first claim sets metadata role gm`; `a second claim takes over: the old GM becomes
    former-gm, a PM's send now reaches the new GM`; `claim refused for a session with parentID`;
    `claim refused for a pm`.
  - `findGm ignores a GM's child that copied role gm`.
  - send: `gm to pm by code with ANSWER queues "[from gm] ANSWER ..."`; `pm to gm with DONE
    queues "[from pm-x1] DONE ..."`; `bad prefix is a tool error, nothing sent`; `a pm child
    cannot send`; `unknown code`; `no live session holds role gm → "no GM session; end the
    turn with the full message"`; `send marks the turn as messaged`; `send refused for a session
    with no role`.
- [ ] **Step 2: Run** `node --test test/pm-prefixes.test.ts test/pm-send.test.ts`. Expected: FAIL.
- [ ] **Step 3: Implement**; `delivery: "queue"` always.
- [ ] **Step 4: Run** the same. Expected: PASS.
- [ ] **Step 5: Commit** `feat(opencode): coven_gm_claim and coven_send`.

### Task 9: PM rules, launch, list, resume, stop

**Files:**
- Create: `opencode/src/pm/rules.ts`
- Extend: `opencode/src/pm/tools.ts`
- Test: `opencode/test/pm-rules.test.ts`, `opencode/test/pm-launch.test.ts`

**Interfaces:**
- Consumes: `resolveRole`, `findGm`, `sharedState.stopped` (Tasks 6, 8).
- Produces:
  - `pmRules(o: {store: string | null; storeResolved: string | null; tmp: string; skills: string; extraRepos: readonly string[]}): PermissionRule[]`
    (the spec's "PM rules on the session" list in its order: allows, `external_directory`
    allows, the `DENY` list, store edits by both paths, webfetch and question, then the secret
    rules from the generated config and the skills edit deny last).
  - `pmLaunchTool`, `pmListTool`, `pmResumeTool`, `pmStopTool`; GM-only.

- [ ] **Step 1: Write the failing tests** (fake `ctx.session`, a real `git init` repo in a tmp dir)
  - rules: `pmRules denies force pushes, main pushes, gh pr merge, store edits by both paths,
    webfetch, question`; `the secret denies come after the allows` (last-match: a `read` of a
    secrets file evaluates to deny against the ruleset alone); `the skills edit deny is last`.
  - launch: `creates a session with agent project-manager, location = the repo checkout, metadata
    {role pm, code}, permissions = pmRules, title pm-<code>, then prompts the brief`;
    `extraRepos become external_directory allows`; `refused for a live code`;
    `refused for a non-git repo`; `invalid code refused` (codes from `test_text.py`'s cases).
  - list: `shows code, id, busy/idle, outcome, ASK WAITING (bug) with request id, missing,
    child busy 30m`.
  - resume: `unarchives then prompts "RESUMED by gm\n<text>"`.
  - stop: `interrupts; archive true archives; marks the stop in sharedState`.
  - `each of launch, list, resume and stop is refused from a non-GM session`.
- [ ] **Step 2: Run** `node --test test/pm-rules.test.ts test/pm-launch.test.ts`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the same. Expected: PASS.
- [ ] **Step 5: Commit** `feat(opencode): PM rules and GM tools to launch, list, resume and stop PMs`.

### Task 10: Turn-end notices and plugin setup

**Files:**
- Create: `opencode/src/pm/notices.ts`, `opencode/src/pm/setup.ts`
- Modify: `opencode/src/coven.ts` (call `pmSetup`; add the new tools to `REQUIRED_TOOLS`)
- Test: `opencode/test/pm-notices.test.ts`, `opencode/test/plugin.test.ts`

**Interfaces:**
- Consumes: `follow` from `join/subscription.ts`; Tasks 6-9.
- Produces: `class NoticeTracker { constructor(directory: string, deps); onEvent(event): Promise<void> }`
  (reads `sharedState.messaged` and `sharedState.stopped`); `pmSetup(ctx, options, deps): Promise<() => void>`
  that registers the tools, the shell hooks, the evaluate hook and the tracker, and, if Task 0
  Step 4 failed, re-applies `pmRules` to every PM session when it starts in the GM's location.

- [ ] **Step 1: Write the failing tests**
  - `a pm turn with no message queues one notice into the GM with the exact text`.
  - `no notice when the pm sent to the GM in that turn`; `no notice for a child (parentID)`;
    `no notice for a session in another directory`; `no notice after coven_pm_stop`;
    `no notice while the PM has a busy child`; `a failed turn gets a notice even after a send`;
    `failed and interrupted outcomes are named`; `a missing GM writes the notice to the serve log`.
  - `plugin registers coven_gm_claim, coven_send, coven_pm_launch, coven_pm_list,
    coven_pm_resume, coven_pm_stop` (selfCheck fails if one is missing).
- [ ] **Step 2: Run** `node --test test/pm-notices.test.ts test/plugin.test.ts`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the same and `python3 .coven/check.py fast opencode`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(opencode): turn-end notices and the PM plugin setup`.

### Task 11: Manager and PM text for OpenCode

**Files:**
- Create: `opencode/overrides/skills/manager/SKILL.md` (and any file of the skill that names
  Claude Code tools), `opencode/overrides/agents/project-manager.md`
- Modify: `src/coven/opencode/rewrite.py` (`COVEN_SKILLS` gains `manager`),
  `src/coven/opencode/content.py` (a primary `project-manager` agent from the override),
  regenerated `opencode/content/`
- Test: `tests/oc/test_oc_content.py`, `tests/test_skills_text.py`

**Interfaces:**
- The agent override has its own `description` (the source's names `claude --bg`, which
  `rewrite.FORBIDDEN` refuses) and the `sha256` source-hash header `content.py` checks; so has
  the manager override.

- [ ] **Step 1: Write the failing tests**
  - `manager override names coven_pm_launch, coven_send, coven_pm_list and never claude --bg or SendMessage`.
  - `manager override has only the No guard flow and says production steps are run by the GM after the user's yes until M3`.
  - `project-manager agent is mode primary, has its own description and never names SendMessage or claude --bg`.
  - `rendering the content raises no RewriteError`.
  - The existing content snapshot tests updated for the new files.
- [ ] **Step 2: Run** `python3 .coven/check.py fast tests/oc/test_oc_content.py tests/test_skills_text.py`. Expected: FAIL.
- [ ] **Step 3: Write the text** (run `humanizer` on it), regenerate content.
- [ ] **Step 4: Run** the same. Expected: PASS.
- [ ] **Step 5: Commit** `feat(opencode): manager and project-manager text for the hierarchy`.

### Task 12: e2e on the fake model

**Files:**
- Create: `opencode/test/e2e/hierarchy/*.ts`, `opencode/test/e2e/served/*.ts`
- Modify: `opencode/test/e2e/main.ts` (register both groups)

- [ ] **Step 1: Write the hierarchy group**, one check per line of the spec's "Fake-model e2e"
  item 1, each printing `ok N - <name>` like the runtime group. The scratch store's `guard.json`
  has one made-up target (`db.example.invalid`).
- [ ] **Step 2: Write the served group**: the runtime group's deny-secrets probes (read and cat of
  the harness's secrets file) against a PM session on the server.
- [ ] **Step 3: Run** `npm run e2e` (in `opencode/`). Expected: every check `ok`, exit 0; save the
  output for the PR.
- [ ] **Step 4: Commit** `test(opencode): e2e of the hierarchy and of deny-secrets on serve`.

### Task 13: Docs and finish

**Files:**
- Modify: `opencode/README.md` (a "Hierarchy" section), `docs/known-gaps.md`, `CHANGELOG.md`
- Test: `tests/test_readme.py` (it pins README paragraphs and the known-gaps hierarchy bullet)

- [ ] **Step 1:** Write the section: `coven opencode serve start`, `coven opencode gm`, the GM tools,
  the No guard flow, "until M3 a production step on OpenCode is always run by the GM, after the
  user's yes", and that with no `guard.json` the deny-only guard has no targets. Run `humanizer`.
- [ ] **Step 2:** `docs/known-gaps.md`: drop the hierarchy bullet; rewrite "Only the deny-secrets
  guard runs" for M2's guards; approvals and the guard-on flow stay as M3.
- [ ] **Step 3:** Change `test_known_gaps_says_what_opencode_does_not_have` to pin the M3 bullet
  instead of the hierarchy one (say so in the PR), and add the new section's pins.
- [ ] **Step 4:** Add the CHANGELOG entry.
- [ ] **Step 5: Run** `python3 .coven/check.py full --base "$(git merge-base HEAD main)"`. Expected:
  `CHECK full exit=0`.
- [ ] **Step 6: Commit** `docs(opencode): the hierarchy`; then the whole-branch review
  (`coven:final-reviewer`) and the PRs.

## Estimate (agent wall-clock)

| Task | h |
|---|---|
| 0 live checks | 1-1.5 |
| 1 role and options | 0.5-1 |
| 2 server lifecycle | 1.5-2.5 |
| 3 gm and doctor | 1-1.5 |
| 4 server guard | 2-3 |
| 5 deny-only prod guard | 1-2 |
| 6 roles and shell env | 1.5-2 |
| 7 guards per session | 2-3 |
| 8 claim and send | 1-1.5 |
| 9 rules, launch, list, resume, stop | 2-3 |
| 10 notices and setup | 1.5-2 |
| 11 manager and PM text | 1-2 |
| 12 e2e | 2-3 |
| 13 docs, full check, final review | 1-2 |
| **Total** | **19-30 h** |

Human gates on top: the user's spec review, PR reviews, and a stop after Task 0 if the TUI cannot
attach, the shell hooks do not match, or the plugin cannot create a PM session. OS isolation of
PM shells (spec, "Raised by the review" item 3) is not in this plan; if the user adds it, about
3 to 5 h more.
