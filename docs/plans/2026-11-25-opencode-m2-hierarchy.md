# coven on OpenCode, M2: the GM/PM hierarchy. Implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use coven:subagent-driven-development to implement
> this plan task by task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** a GM in an OpenCode TUI attached to a coven-owned `opencode serve` runs PMs as served
sessions in their own worktrees, through coven plugin tools, with no prompt that waits on nobody
and no model able to reach the server, its password, the provider key or production.

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
- No personal identifiers in tracked files (no `/home/<name>` paths, no emails).
- Never type coven's session-role variable name in a Bash command (the guard refuses it); in code
  it comes from one constant: Python `coven.pm.profile` (the `env` key of `pm_settings`) and a TS
  constant `ROLE_ENV` in `opencode/src/pm/role.ts` with a contract test against the Python value.
- Every hook fails closed: a throw, a timeout, a spawn error or an unknown exit refuses.
- The server binds `127.0.0.1` only. The password is 32 random bytes, hex, file mode 0600.
- PM prefixes: PM to GM `QUESTION`, `PROD-STEP`, `DRAFT`, `DONE`, `BLOCKED`; GM to PM `ANSWER`,
  `APPROVED`, `DENIED`, `TASK` (from `src/coven/pm/text.py`).
- Metadata shape: `{"coven": {"role": "gm" | "pm", "code": "<code>", "gm": "<GM session id>"}}`.
- Notice text: `[coven notice] pm-<code> ended a turn (<outcome>) without a message. Last line: <first 200 chars>`.
- Checks: `python3 .coven/check.py fast <files>` during a task; the e2e by hand with
  `npm run e2e` (in `opencode/`), output pasted in the PR. No suppressions.

## Review Focus

1. Two shell calls with the same command and cwd from a PM and the GM at once: the PM's shell
   must get `pm`, never nothing (Task 6, `test_same_key_different_roles_gets_pm`).
2. A model that tries `ps eww`, `cat /proc/<pid>/environ` or `cat <profile>/serve/password`
   through a pipe or `bash -c`: refused (Task 4, nested-command tests).
3. A PM turn interrupted by `coven_pm_stop`: no notice (Task 10).
4. A stale pid file after a crash, with a different process now on that pid: `serve start`
   must not refuse forever (Task 2, `test_stale_pid_of_other_process_is_replaced`).
5. A GM session archived while a PM still runs: the PM's `coven_send` gets a clear tool error,
   not a silent drop (Task 8).

## File structure

Python:
- `src/coven/opencode/profile.py` (modify): `project-manager` role.
- `src/coven/opencode/generate.py`, `content.py` (modify): plugin options, the PM agent.
- `src/coven/opencode/serve.py` (create): server files, start, stop, status.
- `src/coven/opencode/api.py` (create): a small authenticated loopback client.
- `src/coven/opencode/cli.py`, `runtime.py`, `checks_server.py` (modify): `serve`, `gm`, the `run`
  refusal, doctor checks.
- `hooks/opencode_server_guard.py` (create), `hooks/prod_guard.py` (modify).
- `opencode/overrides/agents/project-manager.md`, `opencode/overrides/skills/manager/SKILL.md`
  (create); `src/coven/opencode/rewrite.py` (modify: ship `manager`).

TypeScript, under `opencode/src/`:
- `options.ts`, `guard.ts`, `coven.ts`, `context.ts` (modify).
- `pm/role.ts`, `pm/shell-env.ts`, `pm/asks.ts`, `pm/prefixes.ts`, `pm/tools.ts`,
  `pm/worktree.ts`, `pm/notices.ts`, `pm/setup.ts` (create).
- Tests in `opencode/test/pm-*.test.ts`; e2e in `opencode/test/e2e/hierarchy/` and
  `opencode/test/e2e/served/`.

Suggested PRs (the builder may regroup): PR A Tasks 0-5 (Python), PR B Tasks 6-10 (plugin),
PR C Tasks 11-13 (content, e2e, docs).

---

### Task 0: Live checks the design rests on

No product code. A scratch profile under `/tmp`, offline, fake model, as in the spike.

- [ ] **Step 1:** Start `opencode serve` (2.3.2) with a password and find how the TUI attaches
  (`opencode --help`, `opencode attach --help`): open it under `script -qc` against the server and
  confirm a lead session is created on the server (`GET /api/session` lists it).
- [ ] **Step 2:** In a throwaway plugin, throw from `shell.hook("create.before")` and confirm the
  shell command does not run.
- [ ] **Step 3:** Create a session with `permissions` (`shell * deny`), restart the server for real
  (kill the `opencode` pid itself), and check the rule still refuses.
- [ ] **Step 4:** Append the three results to the spec as `## Amendment (Task 0)` and commit
  `docs(specs): M2 live checks`.

If Step 1 fails, stop and send `BLOCKED` (D1 goes back to the user). If Step 2 fails, Task 6 uses
the fallback in the spec. If Step 3 fails, Task 10's `pmSetup` re-applies `pmRules` (Task 9) with
`ctx.session.update` to every PM session of its location when the plugin starts, and Task 12 adds
a check for it.

### Task 1: Profile role and plugin options

**Files:**
- Modify: `src/coven/opencode/profile.py:18`, `src/coven/opencode/generate.py` (`plugin_options`,
  `config_data`), `opencode/src/options.ts`
- Test: `tests/oc/test_oc_profile.py`, `tests/oc/test_oc_generate.py`, `opencode/test/options.test.ts`

**Interfaces:**
- Produces: `ROLES` gains `"project-manager"`; `Profile.model("project-manager")` falls back to
  lead. `Layout.serve_dir: Path` (the `serve/` folder under the profile's run `state` folder;
  Task 2 puts its files there). `plugin_options(profile, coven_root, layout, store)` adds
  `keyEnv: str`, `profileDir: str`, `serveDir: str`, `store: str | None`. TS `CovenOptions` adds
  `keyEnv: string`, `profileDir: string`, `serveDir: string`, `store: string | null`.

- [ ] **Step 1: Write the failing tests**
  - `test_project_manager_model_defaults_to_lead`: profile with only `lead=fake` →
    `model("project-manager") == "fake"`; with `--model project-manager=fake-pm` → `"fake-pm"`.
  - `test_plugin_options_carry_key_env_profile_and_store`: `keyEnv == "TEST_KEY"`,
    `profileDir == str(layout.root)`, `serveDir == str(layout.serve_dir)`, `store` is the store
    path or `None`.
  - `options.test.ts` "parses keyEnv, profileDir and store": missing `keyEnv` throws
    `OptionsError`; `store: null` is accepted.
- [ ] **Step 2: Run** `python3 .coven/check.py fast tests/oc/test_oc_profile.py tests/oc/test_oc_generate.py` and `node --test test/options.test.ts` (in `opencode/`). Expected: FAIL.
- [ ] **Step 3: Implement** the role, the three option keys in `plugin_options` and `parseOptions`.
- [ ] **Step 4: Run** the same. Expected: PASS.
- [ ] **Step 5: Commit** `feat(opencode): project-manager role and plugin options for M2`.

### Task 2: Server lifecycle

**Files:**
- Create: `src/coven/opencode/serve.py`, `src/coven/opencode/api.py`
- Modify: `src/coven/opencode/cli.py`, `src/coven/opencode/runtime.py`
- Test: `tests/oc/test_oc_serve.py`, `tests/oc/test_oc_api.py`

**Interfaces:**
- Produces:
  - `serve.ServeFiles(password: Path, port: Path, pid: Path, log: Path)`;
    `serve.files(layout: Layout) -> ServeFiles` (under `layout.serve_dir`, Task 1).
  - `serve.ServerInfo(pid: int, port: int)`; `serve.running(layout) -> ServerInfo | None`
    (live pid whose `/proc/<pid>/exe` is the profile's `opencode` binary).
  - `serve.start(layout, profile, port: int | None = None, *, spawn=subprocess.Popen,
    wait_s: float = 20.0) -> ServerInfo`; `serve.stop(layout, *, grace_s: float = 10.0) -> bool`.
  - `serve.ServeError(Exception)` with the messages below.
  - `api.Client(port: int, password: str)`; `Client.get(path: str) -> object` (basic auth user
    `opencode`, as the 2.3.2 server expects; `ApiError` on non-2xx or connection failure).
  - CLI: `coven opencode serve start [--port N] | stop | status`.

- [ ] **Step 1: Write the failing tests** (a fake `opencode` script in `tmp_path` that listens on
  the given port and answers `/api/session` with `[]` when the password matches)
  - `test_start_writes_password_port_pid_and_answers`: files exist, password is 64 hex chars and
    mode `0o600`, pid file names the fake binary's pid (not a wrapper), port file matches.
  - `test_password_is_kept_across_starts`.
  - `test_second_start_is_refused`: `ServeError("a server is already running on port N")`.
  - `test_stale_pid_of_other_process_is_replaced`: pid file names a live `sleep` → start works.
  - `test_stop_kills_and_removes_pid_and_port_keeps_password`.
  - `test_start_times_out_with_log_path`: fake never answers → `ServeError` naming `serve/log`.
  - `test_port_taken_between_pick_and_bind_retries_once`.
  - `test_status_lists_pm_sessions`: fake answers two sessions, one with `parentID` and one with
    `metadata.coven.role == "pm"`, code `x1` → status prints `running pid P port N` then one PM
    line with `pm-x1`.
  - `test_run_refuses_while_server_runs`: `coven opencode run` → exit 1, "a coven server is
    running; use `coven opencode gm`".
- [ ] **Step 2: Run** `python3 .coven/check.py fast tests/oc/test_oc_serve.py tests/oc/test_oc_api.py`. Expected: FAIL.
- [ ] **Step 3: Implement** with `runtime.run_env` for the child env plus
  `OPENCODE_SERVER_PASSWORD`; `start_new_session=True`; free port by binding `127.0.0.1:0`.
- [ ] **Step 4: Run** the same. Expected: PASS.
- [ ] **Step 5: Commit** `feat(opencode): coven opencode serve start/stop/status`.

### Task 3: `coven opencode gm` and doctor

**Files:**
- Modify: `src/coven/opencode/cli.py`, `src/coven/opencode/checks_server.py`
- Test: `tests/oc/test_oc_gm.py`, `tests/oc/test_oc_doctor_server.py`

**Interfaces:**
- Consumes: `serve.start`, `serve.running`, `api.Client` (Task 2).
- Produces: `coven opencode gm`; `checks_server.REQUIRED_OPERATIONS: tuple[str, ...]` (the
  operationIds the plugin and CLI call: session create, list, get, prompt, interrupt, update,
  permission list and reply, event).

- [ ] **Step 1: Write the failing tests**
  - `test_gm_starts_server_when_stopped_then_execs_attach`: the exec'd argv is the attach form
    Task 0 found, with the URL; `OPENCODE_SERVER_PASSWORD` is in the TUI env only.
  - `test_gm_stops_when_plugin_not_active`: `/api/plugin` without `coven` active → exit 1,
    "coven's plugin is not active on the server", no exec.
  - doctor: `test_doctor_server_password_mode`, `test_doctor_openapi_has_required_operations`
    (one missing id → the check fails naming it).
- [ ] **Step 2: Run** `python3 .coven/check.py fast tests/oc/test_oc_gm.py tests/oc/test_oc_doctor_server.py`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the same. Expected: PASS.
- [ ] **Step 5: Commit** `feat(opencode): coven opencode gm and server doctor checks`.

### Task 4: The server guard

**Files:**
- Create: `hooks/opencode_server_guard.py`
- Test: `tests/hooks/test_opencode_server_guard.py`

**Interfaces:**
- Produces: `refusal(payload: dict, profile: Path, serve: Path, port: int | None) -> str | None`;
  `main(argv) -> int` reading the payload on stdin, `--profile DIR --serve DIR`, exit 0 allow,
  exit 2 with the text on stderr. The port comes from `<serve>/port` at call time. Command parsing
  reuses `hooks/shell_words` (as `pm_permissions.py` does), including nested `bash -c`/`sh -c`
  and pipelines. In the tests below `<serve>` is a tmp folder inside `<profile>`.

- [ ] **Step 1: Write the failing tests** (parametrized; each refusal next to a near allow)
  - Refused: `opencode api session.list`, `opencode session list --server http://127.0.0.1:4096`,
    `opencode run --server x`, `curl -u x http://127.0.0.1:<port>/api/session`,
    `curl localhost:<port>`, `bash -c "curl 127.0.0.1:<port>"`, `cat <serve>/password`,
    `grep -r . <serve>`, `cat /proc/123/environ`, `ps eww`, `ps -E`, `ps aux e`,
    Read tool on `<serve>/password`, Write/Edit on `<profile>/config/opencode/opencode.jsonc`,
    `echo x > <profile>/config/opencode/agents/a.md`.
  - Allowed: `opencode --version`, `curl http://127.0.0.1:8080/` (another port), `ps aux`,
    `cat /proc/cpuinfo`, Read of `<profile>/config/opencode/AGENTS.md` (reading config is fine;
    writing it is not), `npm test`.
  - `test_no_port_file_still_refuses_opencode_api`.
  - `test_refusal_text_points_to_gm_tools`: contains "coven_pm_list".
- [ ] **Step 2: Run** `python3 .coven/check.py fast tests/hooks/test_opencode_server_guard.py`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the same. Expected: PASS.
- [ ] **Step 5: Commit** `feat(hooks): guard the coven OpenCode server, its password and profile`.

### Task 5: Deny-only production guard

**Files:**
- Modify: `hooks/prod_guard.py` (argument parsing in `_checked`, the approval lookup)
- Test: `tests/hooks/test_prod_guard.py`

**Interfaces:**
- Produces: `prod_guard.py --no-approvals [--pre-tool-use]`: approvals are treated as absent;
  the refusal text gains "a PM on OpenCode never runs a production step: send it to the GM as a
  PROD-STEP".

- [ ] **Step 1: Write the failing tests** (the existing store fixtures, with a made-up target
  host such as `db.example.invalid`)
  - `test_no_approvals_refuses_a_command_a_live_approval_covers`.
  - `test_without_the_option_the_live_approval_still_allows`.
  - `test_no_approvals_still_allows_a_command_no_target_matches`.
  - `test_no_approvals_with_no_store_allows` (as today without a store).
  - `test_no_approvals_unreadable_guard_json_blocks`.
- [ ] **Step 2: Run** `python3 .coven/check.py fast tests/hooks/test_prod_guard.py`. Expected: FAIL.
- [ ] **Step 3: Implement** the option; touch nothing else in the matcher.
- [ ] **Step 4: Run** `python3 .coven/check.py fast tests/hooks/` (the adversarial suite too). Expected: PASS.
- [ ] **Step 5: Commit** `feat(hooks): prod guard deny-only mode for OpenCode PMs`.

### Task 6: Roles and the shell env

**Files:**
- Create: `opencode/src/pm/role.ts`, `opencode/src/pm/shell-env.ts`
- Modify: `opencode/src/context.ts` (the session and shell types the plugin now uses)
- Test: `opencode/test/pm-role.test.ts`, `opencode/test/pm-shell-env.test.ts`

**Interfaces:**
- Produces:
  - `ROLE_ENV: string` (equal to the Python constant; contract test reads `src/coven/pm/profile.py`).
  - `type Role = "gm" | "pm"`; `interface RoleInfo { role: Role; code?: string; gm?: string }`.
  - `resolveRole(sessionID: string, get: (id: string) => Promise<SessionLike | undefined>): Promise<RoleInfo | undefined>`
    (own `metadata.coven`, then up the `parentID` chain; a cycle or more than 32 hops returns `pm`
    if any hop had it, else undefined).
  - `class ShellQueue { record(e: {sessionID: string; role: Role | undefined; command: string; cwd: string}, now: number): void; take(command: string, cwd: string, now: number): Role | undefined }` (key `command + "\0" + cwd`, entries expire after 30 000 ms; mixed roles for one key → `"pm"`).
  - `scrubEnv(env: Record<string, string | undefined>, keyEnv: string): void` (deletes
    `OPENCODE_SERVER_PASSWORD`, `OPENCODE_PASSWORD`, `keyEnv`, and any `OPENCODE_*` name ending in
    `_PASSWORD`, `_TOKEN` or `_KEY`).
  - `shellEnvHooks(deps): { executeBefore(event): Promise<void>; createBefore(event): void }`.

- [ ] **Step 1: Write the failing tests**
  - `role from own metadata`, `role from parent chain`, `no role`, `cycle stops`.
  - `test_same_key_different_roles_gets_pm`; `oldest entry first`; `expired entry ignored`;
    `no entry sets no role`.
  - `scrub removes password and key, keeps PATH and HOME`.
  - `createBefore sets ROLE_ENV for a pm shell and scrubs first`.
  - `ROLE_ENV matches the Python constant`.
- [ ] **Step 2: Run** `node --test test/pm-role.test.ts test/pm-shell-env.test.ts`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the same, then `python3 .coven/check.py fast opencode`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(opencode): session roles and a scrubbed shell env`.

### Task 7: Guards wired per session

**Files:**
- Modify: `opencode/src/guard.ts` (run any hook script with extra args and child env),
  `opencode/src/coven.ts`
- Create: `opencode/src/pm/asks.ts`
- Test: `opencode/test/guard.test.ts`, `opencode/test/pm-asks.test.ts`, `opencode/test/plugin.test.ts`

**Interfaces:**
- Consumes: `resolveRole` (Task 6), options (Task 1).
- Produces:
  - `SpawnFn = (command: string, args: readonly string[], env?: NodeJS.ProcessEnv) => GuardChild`.
  - `runHookScript(covenRoot: string, script: string, extraArgs: readonly string[], payload: Payload, timeoutMs: number, env?: NodeJS.ProcessEnv, spawn?: SpawnFn): Promise<void>`.
  - One `execute.before` hook that, per mapped payload, runs in order: `deny_secrets.py` (all
    sessions), `opencode_server_guard.py --profile <profileDir> --serve <serveDir>` (all sessions), then for PM-tree
    sessions `pm_permissions.py --pre-tool-use` with `ROLE_ENV=pm` in the child env, and
    `prod_guard.py --no-approvals --pre-tool-use`.
  - `askToDeny(resolve): (event: EvaluateEvent) => Promise<void>`: `effect "ask"` → `"deny"` for
    PM-tree sessions, reason "a PM may not wait on a prompt: send it to the GM as a QUESTION or
    PROD-STEP".

- [ ] **Step 1: Write the failing tests** (with `guard-fake.ts`)
  - `order: deny-secrets, server guard, pm-permissions, prod guard for a pm session`.
  - `gm session runs only deny-secrets and the server guard`.
  - `pm-permissions child gets ROLE_ENV=pm`.
  - `a failing pm-permissions refuses with its stderr`; `a prod guard timeout refuses`.
  - `ask becomes deny for a pm child`; `ask stays ask for the gm`; `allow stays allow`.
- [ ] **Step 2: Run** `node --test test/guard.test.ts test/pm-asks.test.ts test/plugin.test.ts`. Expected: FAIL.
- [ ] **Step 3: Implement**, keeping `denySecretsHook`'s behaviour for existing callers.
- [ ] **Step 4: Run** the same and `python3 .coven/check.py fast opencode`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(opencode): server, pm-permissions and prod guards per session; asks deny in PM trees`.

### Task 8: Prefixes, `coven_gm_claim` and `coven_send`

**Files:**
- Create: `opencode/src/pm/prefixes.ts`, `opencode/src/pm/tools.ts` (first two tools)
- Test: `opencode/test/pm-prefixes.test.ts`, `opencode/test/pm-send.test.ts`

**Interfaces:**
- Produces: `PM_PREFIXES`, `GM_PREFIXES`, `headPrefix(text: string): string | undefined` (first
  line, the same rule as `parse_head`); `gmClaimTool(deps)`, `sendTool(deps)` where
  `deps = { session: SessionApi; resolve: typeof resolveRole; sent: (sessionID: string) => void }`.

- [ ] **Step 1: Write the failing tests**
  - `prefix lists match src/coven/pm/text.py` (reads the file, extracts both tuples).
  - claim: `first claim sets metadata role gm`; `second claim refused while the first GM is live`;
    `claim refused for a session with parentID`; `claim refused for a pm`.
  - send: `gm to pm by code with ANSWER queues "[from gm] ANSWER ..."`; `pm to gm with DONE
    queues "[from pm-x1] DONE ..."`; `bad prefix is a tool error, nothing sent`; `a pm child
    cannot send`; `unknown code`; `the GM session is archived → "the GM session is gone; end the
    turn with the full message"`; `send marks the turn as messaged` (calls `sent`).
- [ ] **Step 2: Run** `node --test test/pm-prefixes.test.ts test/pm-send.test.ts`. Expected: FAIL.
- [ ] **Step 3: Implement**; `delivery: "queue"` always.
- [ ] **Step 4: Run** the same. Expected: PASS.
- [ ] **Step 5: Commit** `feat(opencode): coven_gm_claim and coven_send`.

### Task 9: PM launch, list, resume, stop

**Files:**
- Create: `opencode/src/pm/worktree.ts`; extend `opencode/src/pm/tools.ts`
- Test: `opencode/test/pm-launch.test.ts`, `opencode/test/pm-worktree.test.ts`

**Interfaces:**
- Consumes: `resolveRole`, `sendTool` deps (Tasks 6, 8).
- Produces:
  - `pmRules(store: string | null): PermissionRule[]` (the spec's "PM rules on the session" list,
    in that order).
  - `makeWorktree(repo: string, code: string, run: RunFn): Promise<string>` (path
    `<repo>/.claude/worktrees/pm-<code>`, branch `pm/<code>` from the default branch:
    `git symbolic-ref --short refs/remotes/origin/HEAD`, else `main`).
  - `pmLaunchTool`, `pmListTool`, `pmResumeTool`, `pmStopTool`; GM-only.

- [ ] **Step 1: Write the failing tests** (fake `ctx.session`, a real `git init` repo in a tmp dir)
  - launch: `creates worktree, session with agent project-manager, location = worktree, metadata
    {role pm, code, gm}, permissions = pmRules, title pm-<code>, then prompts the brief`;
    `refused for a live code`; `refused when the worktree or branch exists (names it)`;
    `refused for a non-git repo`; `invalid code refused` (codes from `test_text.py`'s cases);
    `refused from a non-GM session`.
  - list: `shows code, id, busy/idle, outcome, ASK WAITING (bug) with request id, missing`.
  - resume: `unarchives then prompts "RESUMED by gm\n<text>"`.
  - stop: `interrupts; archive true archives; marks the stop for the notice tracker`.
  - `pmRules denies force pushes, main pushes, gh pr merge, store edits, webfetch, question`.
- [ ] **Step 2: Run** `node --test test/pm-launch.test.ts test/pm-worktree.test.ts`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the same. Expected: PASS.
- [ ] **Step 5: Commit** `feat(opencode): GM tools to launch, list, resume and stop PMs`.

### Task 10: Turn-end notices and plugin setup

**Files:**
- Create: `opencode/src/pm/notices.ts`, `opencode/src/pm/setup.ts`
- Modify: `opencode/src/coven.ts` (call `pmSetup`; add the new tools to `REQUIRED_TOOLS`)
- Test: `opencode/test/pm-notices.test.ts`, `opencode/test/plugin.test.ts`

**Interfaces:**
- Consumes: `follow` from `join/subscription.ts`; Tasks 6-9.
- Produces: `class NoticeTracker { constructor(directory: string, deps); onEvent(event): Promise<void>; messaged(sessionID): void; stopped(sessionID): void }`;
  `pmSetup(ctx, options, deps): Promise<() => void>` that registers the tools, the shell hooks,
  the evaluate hook and the tracker.

- [ ] **Step 1: Write the failing tests**
  - `a pm turn with no message queues one notice into the GM with the exact text`.
  - `no notice when the pm sent to the GM in that turn`; `no notice for a child (parentID)`;
    `no notice for a session in another directory`; `no notice after coven_pm_stop`;
    `failed and interrupted outcomes are named`; `a missing GM writes the notice to the log`.
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
  `src/coven/opencode/content.py` (a primary `project-manager` agent from the override, with its
  description from `agents/project-manager.md`), regenerated `opencode/content/`
- Test: `tests/oc/test_oc_content.py`, `tests/test_skills_text.py`

- [ ] **Step 1: Write the failing tests**
  - `manager override names coven_pm_launch, coven_send, coven_pm_list and never claude --bg or SendMessage`.
  - `manager override has only the No guard flow and says production steps are run by the GM after the user's yes until M3`.
  - `project-manager agent is mode primary and never names SendMessage`.
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
- Modify: `opencode/README.md` (a "Hierarchy" section), `docs/known-gaps.md`
- Test: `tests/test_readme.py` (it pins README paragraphs; add the new section's pins)

- [ ] **Step 1:** Write the section: `coven opencode serve start`, `coven opencode gm`, the GM tools,
  the No guard flow and "until M3 a production step on OpenCode is always run by the GM, after the
  user's yes". Run `humanizer`.
- [ ] **Step 2:** `docs/known-gaps.md`: the hierarchy leaves the OpenCode gaps; approvals and the
  guard-on flow stay.
- [ ] **Step 3: Run** `python3 .coven/check.py full --base "$(git merge-base HEAD main)"`. Expected:
  `CHECK full exit=0`.
- [ ] **Step 4: Commit** `docs(opencode): the hierarchy`; then the whole-branch review
  (`coven:final-reviewer`) and the PRs.

## Estimate (agent wall-clock)

| Task | h |
|---|---|
| 0 live checks | 0.5-1 |
| 1 role and options | 0.5-1 |
| 2 server lifecycle | 1.5-2.5 |
| 3 gm and doctor | 1-1.5 |
| 4 server guard | 1.5-2 |
| 5 deny-only prod guard | 1-2 |
| 6 roles and shell env | 1.5-2 |
| 7 guards per session | 1.5-2.5 |
| 8 claim and send | 1-1.5 |
| 9 launch, list, resume, stop | 2-3 |
| 10 notices and setup | 1.5-2 |
| 11 manager and PM text | 1-2 |
| 12 e2e | 2-3 |
| 13 docs, full check, final review | 1-2 |
| **Total** | **17.5-28 h** |

Human gates on top: the user's spec review, PR reviews, and a stop after Task 0 if the TUI cannot
attach.
