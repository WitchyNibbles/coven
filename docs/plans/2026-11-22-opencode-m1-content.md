# OpenCode M1 content (PR 3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use coven:subagent-driven-development to implement
> this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A session in coven's OpenCode 2.3.2 profile gets coven's routing, skills, commands and
the 4 role agents, is guarded by deny-secrets (plugin hook plus config `permissions`), and runs
SDD on `subagent` plus `await_agents`.

**Architecture:** A Python generator (`src/coven/opencode/content.py` with its rewrite table in
`rewrite.py`) renders the Claude Code content of this repo into `opencode/content/`, which is
committed, and fails on any Claude-only name it cannot map. Whole-file overrides live in
`opencode/overrides/` and record the source file's hash. `coven opencode setup` copies
`opencode/content/` into the profile's config dir, adds the superpowers skills from Claude Code's
install, and appends the memory-store pointer. The `coven` plugin gains an `execute.before` hook
that turns OpenCode tool calls into the Claude-shaped payload of `hooks/deny_secrets.py` and fails
closed.

**Tech Stack:** Python 3.12 stdlib, TypeScript on Node 24 (`node --test`), OpenCode 2.3.2.

**Spec:** `docs/specs/2026-11-22-opencode-m1-runtime-design.md`, its "PR 3, core content". PR 2
(`feat/opencode-runtime`, #18) is the base: profile, setup, run, doctor, the plugin with
`coven_ping`, the `tool_choice` hook and the e2e harness.

## Global Constraints

- Commits: `git -c user.name=<public name> -c user.email=<public address> commit ...` with the
  repo's public author identity. Never change git config. No `Co-Authored-By` trailer, no
  "Generated with" line, never `--no-verify`. Never a bare `git stash`.
- Never write the word the local pre-commit refuses (the private memory store's name), a home
  path, a user name or an email address into tracked files. Never write the npm package spec as
  name, at sign, literal version in tracked text.
- No network in any test. Anything that starts OpenCode runs inside `unshare -rn` with `lo` up.
  Never read or write the real `~/.config/opencode`, `~/.local/share/opencode`,
  `~/.local/state/opencode`, `~/.cache/opencode`, `~/.agents` or `~/.local/bin/opencode`.
- Code standard: `python3 .coven/check.py fast <changed tests>` before each commit. Complexity
  ≤ 10, ≤ 300 lines a file, no suppressions. `src/coven` uses the stdlib only. The TS package keeps
  no runtime dependencies.
- coven's deny-secrets hook refuses a Bash command whose text holds a dot followed by "env". Edit
  files with the Write and Edit tools; keep such strings out of shell command lines.
- Generated text (`opencode/content/`) holds no machine paths. Paths (`P`, the store) appear only
  in what setup writes into the profile.

## 2.3.2 facts this plan adds (source-read, file:line under the 2.3.2 `packages/` tree)

These correct or sharpen the spec; each is a ruling recorded in the spec's amendment (Task 7).
- Tool ids: `shell` (`command`, `workdir`, `timeout`, `background`), `read` (`path`), `write`
  (`path`, `content`), `edit` (`path`, `oldString`, `newString`, `replaceAll`), `patch`
  (`patchText`; headers `*** Add File: `, `*** Update File: `, `*** Move to: `,
  `*** Delete File: `), `grep` (`pattern`, `path`, `include`), `glob` (`pattern`, `path`),
  `skill` (`id`), `subagent` (`agent`, `description`, `prompt`; always background, returns a
  `sessionID`), `todowrite` (`todos`). Receipts: `core/src/tool/plugin/*.ts`. There is no `bash`
  or `apply_patch` id.
- `todowrite` exists only with `experimental.todo: true` in release builds
  (`schema/src/config/flag.ts:6-11`, `core/src/tool/plugin/todo.ts:17`).
- `ctx.tool.hook("execute.before", fn)` returns `Promise<{dispose()}>`. The event has `tool`,
  `sessionID`, `agent`, `messageID`, `id`, `input`. A throw fails that one call and its message
  reaches the model verbatim (`plugin/src/promise/adapter.ts:688-699`,
  `core/test/session-runner.test.ts:5535-5560`). It runs before the permission check, for child
  sessions and Code Mode inner calls too (`core/src/tool.ts:128-136`, `:197`, `:332`).
  `ctx.location.directory` is the session's directory (`plugin/src/promise/plugin.ts:29`).
- Agent files: an agent with no `mode` is `primary`; generated agents set `mode: subagent`. A file
  with a `permissions` key takes the native path, and its body becomes the system prompt. Global
  config `permissions` are pushed after an agent's defaults and before its own file rules, and the
  last match wins (`core/src/config/plugin/agent.ts:64-103`, `core/src/permission.ts:94-104`).
  So the global `.env` denies beat the default `read *.env ask`. A config `agents.<id>.model`
  is kept when the file has no `model` (`agent.ts:86-91`).
- `edit` permission covers `write`, `edit` and `patch` (`write.ts:47-49`, `patch.ts:71-73`). A
  resource glob's `*` crosses `/`, so `*.env` matches `sub/dir/.env` (`core/src/util/wildcard.ts`).
- Skills: id = folder name, no name rules; the `skill` tool output names
  `Base directory for this skill: <dir>` (`core/src/skill.ts:48-49`). Files outside the session's
  directory need the `external_directory` permission (`core/src/file-access.ts:109-116`), whose
  default is `ask`.
- Commands: `commands/<name>.md`, frontmatter `description`, body is the template, `$ARGUMENTS`
  is the argument string (`core/src/config/plugin/command.ts:119-186`).
- Global `AGENTS.md` in the config dir is loaded for every session of the location, subagents
  included (`core/src/config/plugin/instruction.ts:41`, `core/src/session/context.ts:140-165`).
- `experimental.subagent_depth` default 1: the lead can start a subagent, a subagent cannot
  (`core/src/session/subagent-depth.ts:14`). SDD needs no more, so it stays at the default.

## Rulings (recorded here and in the spec amendment)

1. The rewrite table maps to the real ids above (`shell`, `patch`), not the spec's `bash` and
   `apply_patch`. deny-secrets translates `shell` and `patch`.
2. Setup writes `experimental: {todo: true}` so the task-list wording (`todowrite`) is real.
3. Setup writes `external_directory` allow rules for `<P>/config/opencode/skills/*` (skill files)
   and, when the store exists, `<store>/*` (the memory store the routing tells the model to read).
   Without them every skill prompt file read asks, and a headless `run` cannot answer.
4. Skill ids: coven skills `coven-<x>`, superpowers copies `superpowers-<x>`. The table maps
   `coven:<skill>` and `superpowers:<x>` names; `coven:<agent>` maps to the bare agent id
   (`explorer`, `implementer`, `reviewer`, `final-reviewer`), which PR 2's config already uses
   in `agents.<id>.model`.
5. `the Skill tool` maps to the `skill` tool: 2.3.2 has one (`core/src/tool/plugin/skill.ts:10`).
   The spec listed `Skill` as having no equivalent; that was wrong.
6. Overrides start with one header line `<!-- opencode override of <repo path> sha256:<hex> -->`.
   The generator strips it, because a SKILL.md must start with its frontmatter.
7. The routing (`AGENTS.md`) is an override, not a table render: it names the M2 skills (manager,
   setup, systems-map) that M1 does not ship, and it must tell the model to read the repo's
   `AGENTS.md` and `CLAUDE.md` itself (PR 2 turned project config off).
8. With the superpowers skills missing, setup appends a fixed note to `AGENTS.md` that those steps
   are skipped until setup finds them, instead of rendering a second routing variant.
9. The memory pointer is appended by setup from `coven.config`'s `memory_path`, with the same
   wording as `hooks/memory_context.py`'s header. When the folder does not exist, setup appends
   one line that no store is set up and writes no store allow rule.
10. Doctor's superpowers warning is check 9 (`superpowers`). Check 8 is already `project config`.
11. deny-secrets covers what the Claude matcher covers: `read`, `grep`, `glob`, `shell`, `write`,
    `edit`, `patch`. Other tools (MCP, `webfetch`, `subagent`, `skill`) pass, as on Claude Code. A
    `patch` with no file header is refused.
12. Superpowers copies get only the skill-name rewrite (`superpowers:<x>` and `coven:<x>`), never
    the forbidden-name check: coven does not vendor or edit them, and their Claude wording is a
    known gap the README names.

## Review Focus

- A Claude skill is edited later and nobody regenerates: the stale-output test fails, and an
  override whose source changed fails on its recorded hash. Tests in Task 1 and Task 2.
- A `patch` that moves `notes.txt` to `.env`, or adds `a.txt` and `.env` at once: every path,
  move targets included, is checked, and one refusal refuses the patch. Test in Task 5.
- A `read` of `.env` by a relative path from the session's directory, or of `sub/.env`: the
  payload's `cwd` is `ctx.location.directory`, so `deny_secrets.py` resolves it. Test in Task 5
  and the e2e in Task 6.
- The guard child hangs or prints megabytes: it is killed at `guardTimeoutMs`, and the refusal
  message is cut to 2000 characters. Test in Task 5.
- Setup re-runs after superpowers was uninstalled: the old copies go with the old config dir, and
  doctor warns. Test in Task 4.

---

## File structure

- `src/coven/opencode/rewrite.py`: the rewrite table, the forbidden names and `rewrite_text`.
- `src/coven/opencode/content.py`: `render_content(repo, out)`, overrides and `main()`; run as
  `uv run python -m coven.opencode.content`.
- `src/coven/opencode/superpowers.py`: find the superpowers install and copy its skills.
- `src/coven/opencode/memory.py`: the `AGENTS.md` tail (memory pointer, superpowers note).
- `src/coven/opencode/generate.py`: config keys `experimental`, `permissions`; `write_profile`
  copies the content.
- `src/coven/opencode/checks.py`, `checks_runtime.py`: check 9.
- `opencode/overrides/`: `routing/AGENTS.md`, `skills/subagent-driven-development/SKILL.md`, and
  any other override Task 1 finds it needs.
- `opencode/content/`: generated, committed: `AGENTS.md`, `skills/coven-<x>/`, `agents/<id>.md`,
  `commands/coven-grill-me.md`, `commands/coven-handoff.md`.
- `opencode/src/deny-secrets.ts` (translation), `guard.ts` (the child process), `coven.ts`
  (registration), `context.ts` (hook types).
- `tests/oc/test_oc_rewrite.py`, `test_oc_content.py`, `test_oc_superpowers.py`,
  `test_oc_memory.py`; `opencode/test/deny-secrets.test.ts`, `guard.test.ts`.
- `opencode/test/e2e/runtime/`: two new checks.

---

### Task 1: Rewrite table and the content generator

**Files:**
- Create: `src/coven/opencode/rewrite.py`, `src/coven/opencode/content.py`,
  `tests/oc/test_oc_rewrite.py`, `tests/oc/test_oc_content.py`, `opencode/content/**`
- Modify: `opencode/README.md` (a "Content" section: how to regenerate)

**Interfaces:**
- Produces: `rewrite.rewrite_text(text: str, source: str) -> str`, raising
  `rewrite.RewriteError(f"{source}:{line}: {name} has no OpenCode equivalent (add an override)")`;
  `rewrite.rename_skills(text: str) -> str` (names only, used by Task 4);
  `content.render_content(repo: Path, out: Path) -> None`; `content.SKILLS`, `content.COMMANDS`,
  `content.AGENTS` (tuples of source names); `content.OVERRIDES = repo / "opencode/overrides"`;
  `content.CONTENT = repo / "opencode/content"`.

- [ ] **Step 1: Write the failing tests**

`test_oc_rewrite.py`, each a small input string:
- `coven:clean-code` → `coven-clean-code`; `superpowers:writing-plans` →
  `superpowers-writing-plans`; `coven:implementer` → `implementer` (each of the 4 agents);
  `` `coven:grill-me` `` → `` `/coven-grill-me` `` and `coven:handoff` → `/coven-handoff`.
- `subagent_type: "coven:reviewer"` → `agent: "reviewer"`; `Subagent (coven:implementer):` →
  `subagent (agent: implementer):`; `the Skill tool` → `the \`skill\` tool`.
- Tool phrases, only in these exact forms (bare English "Read the plan" stays as it is): `the Read
  tool` → `the \`read\` tool`, likewise Edit→`edit`, Write→`write`, Bash→`shell`, Grep→`grep`,
  Glob→`glob`; `Read, Grep and Glob` → `` `read`, `grep` and `glob` ``; `TaskCreate`/`TaskUpdate`
  → `todowrite`.
- Each forbidden name raises `RewriteError` naming source and 1-based line: `AskUserQuestion`,
  `SendMessage`, `claude --bg`, `claude agents`, `CLAUDE_PLUGIN_ROOT`, `CLAUDE_SKILL_DIR`,
  `CLAUDE_CONFIG_DIR`, `subagent_type` (left after the mapping), `ToolSearch`, `TaskList`, and a
  `coven:` or `superpowers:` name not in the table (`coven:manager`, `coven:setup`,
  `coven:systems-map`, `coven:project-manager`).
- A line with no match comes back byte for byte; the line count never changes (no line dropped).

`test_oc_content.py`:
- `test_content_is_fresh`: `render_content(REPO, tmp)` then every file under `tmp` equals the
  file under `opencode/content/` and the two file sets are equal.
- `test_skill_layout`: `opencode/content/skills/` holds exactly `coven-<x>` for ceo-review,
  clean-code, office-hours, project-standard, shared-memory, requesting-code-review and
  subagent-driven-development, each with `SKILL.md` and every other file of its source folder
  (prompt files, `scripts/` with the executable bit, `LICENSE`).
- `test_commands`: `commands/coven-grill-me.md` and `commands/coven-handoff.md` have frontmatter
  with only `description` (the source skill's description), and the body ends with a line
  `$ARGUMENTS`.
- `test_frontmatter_name`: each rendered `SKILL.md` frontmatter `name` is its folder name.
- `test_override_header_stripped`: no rendered file starts with `<!-- opencode override`.
- `test_override_hash_current`: for each override, the header's sha256 equals the sha256 of the
  source file it names. Message on mismatch: `<override> replaces <source>, which changed: update
  the override, then its hash`.
- `test_no_machine_paths`: no file under `opencode/content/` contains `/home/`, `/Users/` or `~/.`.

- [ ] **Step 2: Run them to see them fail** (`python3 .coven/check.py fast tests/oc/test_oc_rewrite.py tests/oc/test_oc_content.py`: import errors).

- [ ] **Step 3: Implement `rewrite.py`**

An ordered list of `(compiled regex, replacement)` applied per line, then a scan of the result for
the forbidden names. Keep the table as data at the top of the file, one entry per line.

- [ ] **Step 4: Implement `content.py`**

`render_content` clears `out`, then for each skill: an override at
`OVERRIDES/skills/<name>/<file>` wins over the source file (header stripped); `*.md` files go
through `rewrite_text`; other files are copied with their mode. `SKILL.md` frontmatter `name`
becomes `coven-<name>`. Commands come from the grill-me and handoff `SKILL.md` bodies. Agents and
`AGENTS.md` are Task 3; leave their hooks as functions Task 3 fills (`_agents`, `_routing`), each a
no-op now. `main()` renders into `CONTENT` and prints the file count.

- [ ] **Step 5: Generate and commit the output**

Run `uv run python -m coven.opencode.content`. Read the diff of each rendered file against its
source: every change must come from the table. A source line that hits a forbidden name gets an
override under `opencode/overrides/skills/<name>/` with the header of ruling 6 (the SDD skill's
override is Task 2: until then, add `subagent-driven-development` to a `PENDING_OVERRIDE` tuple in
`content.py` that renders the source untouched, and Task 2 removes it).

- [ ] **Step 6: Pass and commit** (`feat(opencode): content generator and rewrite table`).

---

### Task 2: The SDD override (`subagent` plus `await_agents`)

**Files:**
- Create: `opencode/overrides/skills/subagent-driven-development/SKILL.md`
- Modify: `src/coven/opencode/content.py` (drop `PENDING_OVERRIDE`), `opencode/content/**`,
  `tests/oc/test_oc_content.py`

**Interfaces:** Consumes Task 1's generator. Produces nothing new.

- [ ] **Step 1: Write the failing tests** in `test_oc_content.py`:
  - `test_sdd_dispatch_words`: the rendered SDD `SKILL.md` names the `subagent` tool with agent
    ids `implementer`, `reviewer` and `final-reviewer`, contains
    `await_agents({ids: <pending only>, mode: "all"})`, contains the sentence
    `A \`running\` or progress report is not a result.`, and never contains `${CLAUDE_`,
    `subagent_type`, `Claude Code` or `SendMessage`.
  - `test_sdd_scripts_by_base_dir`: every script call in it reads
    `bash "<base directory of this skill>/scripts/<name>"`, the base directory being the one the
    `skill` tool prints.
  - `test_no_pending_override`: `content.PENDING_OVERRIDE` is gone.
- [ ] **Step 2: Run them to see them fail.**
- [ ] **Step 3: Write the override.** Start from the Claude `SKILL.md` and change only what 2.x
  forces: dispatch with `subagent` (`agent`, `description`, `prompt`), keep each returned child id
  in the ledger, call `await_agents` with the pending ids before reading any report, resume an
  implementer for fix rounds 1-3 with `subagent`'s `sessionID`, a fresh one at round 4 or later,
  and the script paths by base directory. Model selection becomes "the agent's configured model;
  pass `model` only to depart from it". Keep every other rule word for word. Header per ruling 6.
- [ ] **Step 4: Regenerate, pass and commit** (`feat(opencode): SDD on subagent and await_agents`).

---

### Task 3: Role agents and the routing

**Files:**
- Create: `opencode/overrides/routing/AGENTS.md`, `opencode/content/agents/*.md`,
  `opencode/content/AGENTS.md`
- Modify: `src/coven/opencode/content.py`, `tests/oc/test_oc_content.py`
- Test: `tests/oc/test_oc_standard.py` (new)

**Interfaces:** Fills Task 1's `_agents` and `_routing`.

- [ ] **Step 1: Write the failing tests**
  - `test_agents`: `agents/` holds exactly `explorer.md`, `implementer.md`, `reviewer.md` and
    `final-reviewer.md`. Each frontmatter has exactly `description` (the source's), `mode:
    subagent` and `permissions`, and no `model`. Explorer, reviewer and final-reviewer:
    `permissions: [{action: edit, resource: "*", effect: deny}]`. Implementer:
    `[{action: subagent, resource: "*", effect: deny}]`. The body is the source body through
    `rewrite_text`.
  - `test_routing`: `AGENTS.md` equals the override with its header stripped; it names
    `python3 .coven/check.py`; it tells the model to read the repo's `AGENTS.md` and `CLAUDE.md`
    when they exist; it names every shipped skill id and both commands, and no `coven:` or
    `superpowers:` name; its override header hashes `instructions/routing.md`.
  - `test_oc_standard.py::test_checks_need_no_claude`: `.coven/check.py` and every file in
    `standard/` that the fast and full paths run never invoke `claude` (no `claude ` command
    token).
- [ ] **Step 2: Run them to see them fail.**
- [ ] **Step 3: Write the routing override** from `instructions/routing.md`: same pipeline and
  rules, OpenCode names, M2 items left out (manager, setup, systems-map, the GM/PM lines), plus a
  first rule: "Project config is off in this profile: at the start of work in a repo, read its
  `AGENTS.md` and `CLAUDE.md` if they exist and follow them." The memory section says where the
  pointer is (appended by setup below) and to read `user/INDEX.md` and `projects/<key>/INDEX.md`.
- [ ] **Step 4: Implement `_agents` and `_routing`**, regenerate, pass and commit
  (`feat(opencode): role agents and routing`).

---

### Task 4: Setup writes the content, superpowers, memory pointer and permissions; doctor check 9

**Files:**
- Create: `src/coven/opencode/superpowers.py`, `src/coven/opencode/memory.py`,
  `tests/oc/test_oc_superpowers.py`, `tests/oc/test_oc_memory.py`
- Modify: `src/coven/opencode/generate.py`, `checks.py`, `checks_runtime.py`, `cli.py` (setup
  prints the superpowers warning), `tests/oc/test_oc_generate.py`, `tests/oc/test_oc_doctor*.py`,
  `tests/fixtures/opencode/litellm.jsonc`, `opencode.jsonc` (deliberate golden updates)
- Modify: `src/coven/doctor.py` only to share the installed-plugins reader if that is a pure move.

**Interfaces:**
- Produces: `superpowers.SKILLS = ("brainstorming", "writing-plans", "test-driven-development",
  "systematic-debugging", "verification-before-completion")`;
  `superpowers.find() -> Path | None` (the `skills/` dir of the first `superpowers@*` install with
  an `installPath`, read from `paths.installed_plugins_file()`; any read or parse error gives
  `None`); `superpowers.copy(source: Path | None, skills_dir: Path) -> list[str]` (copies each of
  `SKILLS` into `skills_dir/superpowers-<x>/`, `*.md` through `rewrite.rename_skills`, returns the
  names it could not find); `memory.agents_tail(store: Path | None, missing: list[str]) -> str`;
  `memory.store_path() -> Path | None` (from `coven.config`, `None` when the folder is absent).
- `generate.config_data` gains, after `plugins`: `experimental: {"todo": true}` and
  `permissions`: the 5 secret rules of the spec in its order, then
  `{action: external_directory, resource: "<P>/config/opencode/skills/*", effect: allow}` and,
  with a store, the same for `"<store>/*"`.

- [ ] **Step 1: Write the failing tests**
  - superpowers: a fake installed-plugins file (both the list and the single-object shape, as in
    `doctor._has_install`) with `superpowers@claude-plugins-official` finds its `skills/`; no file,
    bad JSON, no entry or no `installPath` give `None`; `copy` writes `superpowers-brainstorming/
    SKILL.md` with `superpowers:writing-plans` renamed, and returns the missing names.
  - memory: with a store, the tail is the `hooks/memory_context.py` header with the path and the
    INDEX instruction; without one, the one line "No shared memory store on this machine (run
    `coven setup`)."; with `missing`, the fixed superpowers note naming them.
  - generate: the profile's `config/opencode/` holds `AGENTS.md` (content plus tail), `skills/`
    (coven plus superpowers), `agents/`, `commands/`; the goldens show the new keys with `<P>` and
    `<STORE>` placeholders; re-running setup after the superpowers install disappears leaves no
    `superpowers-*` folder.
  - doctor: check 9 `superpowers` warns (not fails) when any `superpowers-<x>` folder is missing,
    with fix "install the superpowers plugin in Claude Code, then re-run `coven opencode setup`",
    and passes when all are present. Exit code stays 0 on the warning.
  - setup's stdout names the missing superpowers skills on one warning line.
- [ ] **Step 2: Run them to see them fail.**
- [ ] **Step 3: Implement**, keeping `write_profile` all-or-nothing: copy and tail are written
  into the work folder before the swap.
- [ ] **Step 4: Pass, update `opencode/README.md` (what setup copies; bash is covered by the
  plugin layer only) and commit** (`feat(opencode): setup writes content, superpowers and
  permissions`).

---

### Task 5: deny-secrets in the plugin

**Files:**
- Create: `opencode/src/deny-secrets.ts`, `opencode/src/guard.ts`,
  `opencode/test/deny-secrets.test.ts`, `opencode/test/guard.test.ts`
- Modify: `opencode/src/context.ts` (`tool.hook` type), `opencode/src/coven.ts`,
  `opencode/test/plugin.test.ts`

**Interfaces:**
- Produces: `payloads(tool: string, input: unknown, cwd: string): Payload[] | "unchecked"` with
  `Payload = {tool_name: string; tool_input: Record<string, unknown>; cwd: string;
  hook_event_name: "PreToolUse"}`; `runGuard(covenRoot: string, payload: Payload, timeoutMs:
  number, spawnFn?): Promise<void>` (resolves on allow, throws `Error` on refusal or failure);
  `denySecretsHook(options, deps): (event) => Promise<void>`.
- Mapping: `shell` → `Bash {command}`; `read` → `Read {file_path: path}`; `write` → `Write
  {file_path: path, content}`; `edit` → `Edit {file_path: path, old_string, new_string}`; `grep`
  → `Grep {pattern, path, glob: include}` (absent fields left out); `glob` → `Glob {pattern,
  path}`; `patch` → one `Write {file_path}` per path in the `Add File`, `Update File`, `Move to`
  and `Delete File` headers; any other tool → `"unchecked"`.
- Child: `sh <covenRoot>/hooks/fail-closed.sh <covenRoot>/hooks/deny_secrets.py`, payload JSON on
  stdin, no shell string (argv array). Exit 0 allows. Exit 2 throws the trimmed stderr, or
  `coven deny-secrets: blocked` when it is empty. Any other exit throws
  `coven deny-secrets: guard failed (exit <n>); blocking`. A timeout kills the child (SIGKILL) and
  throws `coven deny-secrets: guard timed out after <ms> ms; blocking`. A spawn error throws
  `coven deny-secrets: could not start the guard (<message>); blocking`. Messages are cut to 2000
  characters.

- [ ] **Step 1: Write the failing tests**
  - `deny-secrets.test.ts`: each mapping above; `patch` with `*** Update File: a.txt` and
    `*** Move to: .env` gives two payloads; a patch with no header throws
    `coven deny-secrets: no file in patch; blocking`; a non-object input for a checked tool throws
    (fail closed); `cwd` is the given directory.
  - `guard.test.ts` with an injected spawn fake: each exit path and message above, the stdin
    payload is exactly the JSON, the argv is exactly the two paths, the timeout kills the child.
    One test runs the real `hooks/fail-closed.sh` and `deny_secrets.py` of this repo with `sh` on
    a `Read` of `.env` (refused, exit 2) and of `README.md` (allowed). This spawns local Python
    only.
  - `plugin.test.ts`: setup registers one `execute.before` hook; cleanup disposes it; a refused
    payload throws from the hook with the guard's text; the hook awaits every payload of a patch
    and throws on the first refusal; the hook uses `ctx.location.directory` as `cwd` and
    `options.guardTimeoutMs` as the timeout.
- [ ] **Step 2: Run them to see them fail** (`cd opencode && npm test`).
- [ ] **Step 3: Implement**, register in `covenSetup` next to the `tool_choice` hooks, inside the
  same `try` so a failure cleans up. The hook fails closed: it never catches the guard's error.
- [ ] **Step 4: Pass (`npm test`, coverage stays at the package threshold) and commit**
  (`feat(opencode): deny-secrets hook fails closed`).

---

### Task 6: e2e: deny-secrets and the SDD smoke

**Files:**
- Modify: `opencode/test/e2e/runtime/checks.ts`, `runtime/profile.ts`, and the fake-model script
  the runtime world uses; add `runtime/content.ts` if `checks.ts` would pass 300 lines.

**Interfaces:** Consumes the generated profile from Task 4 and the hook from Task 5.

- [ ] **Step 1: Add the checks**, by title (the e2e numbers are positional):
  - `deny-secrets: read and cat of .env are refused`. The world's project holds `.env` with a
    marker value. The scripted model asks for `read {path: ".env"}`, then
    `shell {command: "cat .env"}`. Both tool results carry `coven deny-secrets:`, and the marker
    never appears in the session export. Then the plugin folder is removed and the `read` is run
    again: it is still refused, by the config permission (the error does not carry
    `coven deny-secrets:`). That is the second layer holding without the plugin.
  - `sdd: two role subagents are awaited and each report arrives once`. The scripted lead calls
    `subagent` for `implementer` and for `reviewer`, then `await_agents` with both ids, mode
    `all`. Both children answer with a marker report. The lead's history holds each marker exactly
    once, after the `await_agents` result.
- [ ] **Step 2: Run** `npm run e2e` inside `unshare -rn` with the trial binary (as PR 2). Expected:
  every check `ok`, the new two included.
- [ ] **Step 3: Commit** (`test(opencode): e2e for deny-secrets and the SDD smoke`).

---

### Task 7: Docs

**Files:** `opencode/README.md`, `CHANGELOG.md`,
`docs/specs/2026-11-22-opencode-m1-runtime-design.md` (an "Amendment (PR 3)" section listing the
rulings above, and check 9 in the doctor list).

- [ ] **Step 1:** README: the content folder and how to regenerate it, the two permission layers
  and that bash is only checked by the plugin, the superpowers copy and its Claude wording gap, the
  memory pointer. CHANGELOG: one entry for PR 3.
- [ ] **Step 2:** `python3 .coven/check.py fast` and commit (`docs(opencode): content, guard and
  rulings`).
