# coven guard, approvals, systems map and memory adopt Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: coven's subagent-driven-development (one
> implementer per task, a reviewer after each, a final whole-branch review). Steps use checkbox
> (`- [ ]`) syntax for tracking.

**Goal:** Add the production guard, approvals, the `systems/` and `approvals/` store scopes, a
neutral systems-map skill, the manager's approval rules, `coven guard check|explain`, the doctor
refusals line and `coven memory adopt` to coven.

**Architecture:** The guard is a stdlib PreToolUse hook (`hooks/prod_guard.py`) that reads the
memory store's git HEAD; the CLI (`src/coven/guard.py`, `src/coven/approve.py`) loads that same
module so every command decides with the hook's own rules. The Bash scanner `hooks/shell_words.py`
grows to what the guard needs. Most code is ported by hand from the private toolkit
(`<private>`, its path is in each dispatch prompt; read-only, never modified), with every
site-derived name, host, path and example replaced by invented ones.

**Tech Stack:** Python 3.12+ stdlib for hooks and CLI; pytest, ruff, mypy strict via
`python3 .coven/check.py`; git.

**Spec:** `docs/specs/2026-11-17-coven-guard-approvals-adopt-design.md` (approved 2026-11-17),
extending `docs/specs/2026-11-13-coven-v1-design.md`. Read the addendum before your task; it is
the source of every value below.

## Global Constraints

- Hooks and CLI: standard library only, run as `python3 -I`, floor Python 3.12. No new
  dependencies anywhere.
- Role variable `COVEN_SESSION_ROLE`. The guard and the store's pre-commit hook restrict on **any
  set value**, empty included; pm-permissions keeps its `== "pm"` test.
- Names: hook file `hooks/prod_guard.py`, log name `prod-guard` (`prod-guard.jsonl`), printed
  prefix `coven prod-guard:`; config key `guard` with the single value `"required"`; store files
  `systems/guard.json`, `systems/<system>.md`, `approvals/<id>.step`, `approvals/<id>.json`,
  `approvals/<id>-<system>.json`. Formats of `guard.json` and approval JSON are the private
  toolkit's, unchanged.
- Limits: approval at most 12 h (`MAX_APPROVAL`); guard deadline 20 s; pattern deadline in
  `coven approve` 5 s; `guard.json` history fallback 50 commits; doctor shows 5 refusals, command
  cut to 80 characters.
- Privacy: nothing is copied with history. Port by reading and rewriting; never carry over a user
  name, a home path, an email address other than the public author's, the name of any private
  organisation, system, ticket or chat tool, or the private toolkit's own name. Every example and
  fixture names an invented system (`orders-db`, `mail-send`, `billing-api`, hosts under
  `example.com`/`example.internal`). A local pre-commit hook scans each staged change against a
  private pattern list; if it refuses a commit, fix the text, never bypass it (`--no-verify` is
  forbidden).
- Tests reach nothing real: temporary `HOME`, `XDG_*` and `CLAUDE_CONFIG_DIR`; temporary git
  stores built in the test; no network; a remote CLI appears only as a command string the guard
  reads, never runs. Hooks are tested as subprocesses with JSON payloads on stdin.
- The repo has `.coven/`: no suppressions, no weakened lint or type rules. Fast check during work:
  `python3 .coven/check.py fast <tests>`; full check only at the end.
- Commits: author `WitchyNibbles` with the repo-local email; no `Co-Authored-By` trailer and no
  "Generated with" line.

## Review Focus

1. A config file present but naming a store path that does not exist, or a store with an unborn
   HEAD: the guard is off for the first (no store) and refuses outward calls for the second
   (`StoreError`); test both in Task 3.
2. A `guard.json` pattern that backtracks badly (`(a+)+$`): the hook refuses at its deadline and
   `coven approve` stops at 5 s, never hangs; tests in Tasks 3 and 6.
3. A step file with CRLF line ends, trailing spaces or a trailing blank line: commands are
   compared whitespace-stripped by the guard, so `coven approve` must store them stripped and
   reject a CR inside a command; test in Task 6.
4. A tool call from a PM's subagent (payload carries `agent_id`/`agent_type`) is judged like the
   PM's own; test in Task 4.
5. Adopt run twice, and adopt interrupted after the hook move: the second run finishes and counts
   still match; test in Task 8.

## Rulings made while planning

- Spike 1 (is coven's `shell_words.py` enough?) — settled from source: the private guard calls
  `code_text`, `heredoc_bodies`, `without_patterns` and `DATA_SINKS`, none of which coven's
  trimmed copy has, so the guard cannot import it. Task 1 ports the missing parts with their own
  tests — cost if wrong: none; the larger scanner is a superset, and deny-secrets' tests guard it.
- Spike 2 (does PreToolUse `*` catch every tool?) — settled from the hooks docs ("If you omit the
  matcher or use `"*"`, the group activates on every occurrence of the event"; PreToolUse "matches
  on any tool name except `EndConversation`"; hooks "also run inside subagents"; PreToolUse hooks
  "run before every tool call, whether or not it needs permission") and the private toolkit's
  registration (`"*"` for its guard). Task 3 pins the registration in a test; the live check is
  in the rollout notes — cost if wrong: a tool class bypasses the guard until a matcher is added.
- Restricted sessions also may not write coven's config file (`config.json`) or run
  `coven setup` or `coven memory adopt|init|sync`: the latch and the store path live there, so
  writing it could switch the guard off — source: the private guard refuses the PM its own
  installer commands — cost if wrong: a PM asks the GM for a setup change it could have made.
- The store's pre-commit hook moves from `== "pm"` to any set value (hook version bump) — source:
  the addendum's memory store section and the stricter role rule — cost if wrong: a session with
  an odd role value cannot commit to the store; only PMs set the variable.

---

### Task 1: Scanner for the guard

**Files:**
- Modify: `hooks/shell_words.py`
- Create: `tests/hooks/test_shell_words.py`
- Test: `tests/hooks/test_deny_secrets.py` stays green

**Interfaces:**
- Produces: `shell_words.code_text(command: str) -> str | None`, `without_data(...)`,
  `without_patterns(...)`, `heredoc_bodies(...)`, `simple_commands(...)`, `DATA_SINKS`, `Word`,
  with the private module's signatures and semantics (`<private>/.../hooks/shell_words.py`).

- [ ] Port the private scanner's missing functions and their helpers into `hooks/shell_words.py`,
  keeping coven's existing behaviour for deny-secrets. Comments and docstrings rewritten neutral.
- [ ] Port the private `test_shell_words.py` cases (about 43) with synthetic commands; they must
  fail against today's coven module (missing names) and pass after.
- [ ] Run `python3 .coven/check.py fast tests/hooks/test_shell_words.py tests/hooks/test_deny_secrets.py`;
  expect exit 0.
- [ ] Commit `feat(hooks): scanner functions the prod guard needs`.

### Task 2: Config latch and the new store scopes

**Files:**
- Modify: `src/coven/config.py`, `src/coven/settings.py`, `src/coven/setup.py`
- Modify: `src/coven/memory/store.py`, `src/coven/memory/entries.py`
- Modify: `hooks/memory_pre_commit.py`, `hooks/memory_context.py`
- Test: `tests/test_config.py`, `tests/test_settings.py`, `tests/test_setup_cli.py`,
  `tests/memory/test_entries.py`, `tests/memory/test_pre_commit.py`, `tests/memory/test_sync.py`,
  `tests/hooks/test_memory_context.py`

**Interfaces:**
- Produces: `Config.guard: str | None` (only `None` or `"required"`; anything else is a
  `CovenError` naming the file); `store.is_guarded(path: Path) -> bool` (HEAD holds
  `systems/guard.json`); `entries.rebuild_indexes` covers `systems/`.

- [ ] Tests first: config round-trips `guard`; an unknown value is refused; setup plans
  `guard: required` as an owned change when the chosen store has a committed `guard.json`, and
  `--undo` restores the previous value; `rebuild_indexes` writes `systems/INDEX.md`; the
  pre-commit hook indexes `systems/`, refuses a commit when `COVEN_SESSION_ROLE` is set to any
  value (also `""` and `"x"`) in the process or an ancestor, and scans staged approval JSON for
  the token patterns; memory-context shows `systems/INDEX.md` after the user index within the
  8,000-character budget, only when it exists; `coven memory sync` on a guarded store commits and
  rebuilds but never pushes and prints `not pushed: guarded store`.
- [ ] Implement. Bump the store hook version so doctor and sync see old hooks as old.
- [ ] Fast check on the touched tests; commit `feat(memory): systems and approvals scopes, guard latch`.

### Task 3: Prod guard core

**Files:**
- Create: `hooks/prod_guard.py`
- Modify: `hooks/hooks.json` (PreToolUse, matcher `"*"`, under `fail-closed.sh`, same form as
  deny-secrets)
- Test: `tests/hooks/test_prod_guard.py`, `tests/test_plugin_structure.py`

**Interfaces:**
- Consumes: Task 1 scanner; Task 2 `Config.guard` read as plain JSON (the hook does not import
  `src/coven`; follow `memory_context.store_path()`).
- Produces (module-level, used by Tasks 4-7): `MAX_APPROVAL: timedelta`, `DEADLINE: float`,
  `GUARD_HISTORY: int`, `class StoreError`, `class GuardError`, `class ConfigError`,
  `class PayloadError`, `class DeadlineError`, `Target` (TypedDict), `Approval`,
  `load_targets(store: Path) -> Enforced` (enforced targets plus the invalid-HEAD note),
  `approvals(store: Path) -> list[Approval]`,
  `decide(payload: Mapping[str, object], store: Path | None, role: str | None, now: datetime) -> str | None`,
  `explain(...)` data for Task 5 (shape left to the builder, but `decide` must be built on it so
  the two cannot disagree), `check_file(path: Path) -> str` (`ok: N targets` or `GuardError`),
  `main() -> int`, and `--check <file>` on the command line.

- [ ] Tests first (port the private suite's cases for these parts, synthetic targets):
  off path when no latch and no working-tree `guard.json` (assert no `git` subprocess runs, e.g.
  with `PATH` lacking git); off with no config or no store; on with the latch; the four
  `guard.json` states from the addendum (valid; invalid with a valid older one, refusal notes it;
  none valid: read-only except the repair commit for an unrestricted session; absent with latch:
  restricted lose Bash and MCP); unreadable store refuses outward calls for every session;
  uncommitted edits and approvals ignored; target rule for role unset **and** set; a command
  matching two systems needs both approvals; live, expired and too-far-ahead approvals;
  whitespace-stripped exact match; MCP tool targets; unknown tool names (`WebFetch`, `Agent`,
  `mcp__x__y`) handled without error; malformed payload refused; deadline refused; a crash under
  `fail-closed.sh` exits 2; decisions and failures logged to `prod-guard.jsonl`;
  `--check` prints `ok: N targets` or the reason. Structure test pins the hooks.json entry.
- [ ] Implement by porting the private guard's core, adapted to coven paths and names.
- [ ] Fast check; commit `feat(hooks): prod guard core`.

### Task 4: Prod guard restricted rules

**Files:**
- Modify: `hooks/prod_guard.py`
- Test: `tests/hooks/test_prod_guard.py`, `tests/hooks/test_prod_guard_adversarial.py`

**Interfaces:**
- Consumes: Task 3 `decide`; Task 1 scanner.
- Produces: the restricted refusals, checked before targets, that no approval lifts.

- [ ] Tests first (port the private suite's restricted and adversarial cases): Write, Edit and
  NotebookEdit under the store path and under coven's config file; Bash that writes either;
  reads allowed; changing `COVEN_SESSION_ROLE`; starting `claude` (wrappers, continuations,
  stdin-run shells, `claude --version` allowed); `coven approve`, `coven setup`,
  `coven memory adopt|init|sync`, and a `coven` subcommand the guard cannot tell; remote CLIs
  with a host not written out (the private table of CLIs and options); a subagent payload
  (`agent_id`) judged the same; an approved command that also breaks a restricted rule is still
  refused; role `""` and `"x"` restricted, unset not.
- [ ] Implement by porting the private restricted rules, mapping each private path or command to
  its coven equivalent (list the mapping in the report).
- [ ] Fast check; commit `feat(hooks): prod guard rules for restricted sessions`.

### Task 5: `coven guard check` and `coven guard explain`

**Files:**
- Create: `src/coven/guard.py` (CLI group `guard`, and `load_guard() -> ModuleType` that loads
  `hooks/prod_guard.py` from `paths.root()` the way the store code finds its hook)
- Modify: `src/coven/cli.py` (`COMMANDS`)
- Test: `tests/test_guard_cli.py`

**Interfaces:**
- Consumes: Task 3 `check_file`, `explain` data, `decide`.
- Produces: `load_guard()` for Tasks 6-7.

- [ ] Tests first: `check` prints `ok: N targets` / the reason, exits 0 / 1, never reads stdin;
  `explain` on a command, on `--file` (multi-line), on `--tool`; prints the hitting targets with
  system, kind and first pattern, the live approval (id, expiry) or `needs approval`, the
  restricted rule a PM would hit, and one verdict line (`allowed`, `refused for a PM`,
  `refused for every session`); `guard off: <why>` exit 0 when off; exit 1 with a reason on bad
  input or an unreadable store; writes nothing (store status unchanged, no log line). A test
  feeds the same cases to `explain` and to the hook and checks the verdicts agree.
- [ ] Implement; fast check; commit `feat(cli): coven guard check and explain`.

### Task 6: `coven approve`

**Files:**
- Create: `src/coven/approve.py`
- Modify: `src/coven/cli.py`
- Test: `tests/test_approve.py`

**Interfaces:**
- Consumes: `load_guard()`; the guard's `MAX_APPROVAL`, `StoreError`, `load_targets`.
- Produces: `coven approve <id> --expires-in <duration> [--system <s>]`,
  `coven approve --revoke <id>`.

- [ ] Tests first (port the private suite's cases, about 53): durations `90m`, `2h`, `1h30m`, at
  most `12h`, others refused; `MAX_DURATION` equals the guard's `MAX_APPROVAL`; reads only
  `approvals/<id>.step` in the store; refuses unless the step file is the only change; step
  syntax (one command per line, fenced blocks, `tool:` lines, CRLF and trailing blanks handled
  as Review Focus 3); a command or tool no target matches is an error; one file per system,
  `<id>.json` alone or `<id>-<system>.json` for several; `--system`; commits only those files,
  deletes the step, prints `approved ...` per file with every command and tool; rollback on any
  failure (nothing committed, step file kept); expired approval files deleted in the same
  commit; `--revoke <id>` removes that id's files in one commit and refuses an unknown id;
  refuses when `COVEN_SESSION_ROLE` is set to any value; a backtracking pattern stops at 5 s.
- [ ] Implement; fast check; commit `feat(cli): coven approve`.

### Task 7: Doctor and setup

**Files:**
- Modify: `src/coven/doctor.py`, `src/coven/setup.py` (if Task 2 left setup's detection here)
- Test: `tests/test_doctor.py`

**Interfaces:**
- Consumes: `load_guard()`, `store.is_guarded`, `Config.guard`.

- [ ] Tests first: guard on or off and why; `guard.json` valid at HEAD (via `check_file`); live
  approvals with expiry; warning for a guarded store with a remote; warning for a committed
  `guard.json` with no latch; info line with the last 5 refusals from a hand-written
  `prod-guard.jsonl`, newest first, one line each (time, role `pm`/other/`unset`, system or
  rule, command or tool cut to 80 characters, one printable line), and
  `no guard refusals logged` when there are none; a torn or hostile log line does not break it.
- [ ] Implement; fast check; commit `feat(doctor): guard status and recent refusals`.

### Task 8: `coven memory adopt`

**Files:**
- Create: `src/coven/memory/adopt.py`
- Modify: `src/coven/memory/__init__.py` (the `memory` CLI group)
- Test: `tests/memory/test_adopt.py`

**Interfaces:**
- Consumes: `store.install_hook`, `_pin_hooks_path` (or its public form), `rebuild_indexes`,
  setup's owned-change path for `memory_path` and `guard`.
- Produces: `coven memory adopt <path> [--yes]`.

- [ ] Tests first, against temporary stores: dry run without `--yes` changes nothing; preflight
  refuses a dirty tree, no HEAD, a failing `git fsck`; counts per scope; unknown folders and
  files without frontmatter listed and kept; backup `.tar.gz` mode 0600 in a 0700 folder under
  `${XDG_STATE_HOME:-~/.local/state}/coven/backups/`, `.sha256` beside it, read back before going
  on; a foreign pre-commit hook moved to `pre-commit.pre-adopt-<stamp>`; a local
  `core.hooksPath` recorded and replaced; `projects/` and `handoffs/` folders renamed to the key
  form with `git mv`, the state record renamed with its folder; a colliding pair stops before any
  change; one commit; config `memory_path` (and `guard` when `guard.json` is committed) set
  through the owned-change path, undone by `coven setup --undo`; counts equal after, else stop
  with the restore commands; the report lists backup, checksum, renames, hook name and the way
  back; never pushes; re-run after a stop finishes (Review Focus 5).
- [ ] Implement; fast check; commit `feat(memory): coven memory adopt`.

### Task 9: Skills, role text and docs

**Files:**
- Create: `skills/systems-map/SKILL.md`
- Modify: `skills/manager/SKILL.md`, `agents/project-manager.md`, `instructions/routing.md`,
  `README.md`, `docs/known-gaps.md`, `CHANGELOG.md`
- Test: `tests/test_skills_text.py`, `tests/test_routing_text.py`, `tests/test_readme.py`,
  `tests/test_plugin_structure.py`

- [ ] Tests first: the skill exists with valid frontmatter; texts name `coven approve`,
  `coven guard check`, `coven guard explain` and `coven approve --revoke` where the addendum puts
  them; the PM role text has both reply forms (`APPROVED <project>/<task> <id>:` and
  `APPROVED <project>/<task>: ran, <result>`) and "never run a production step" for the no-guard
  case; no text names a ticket or chat tool.
- [ ] Write the systems-map skill neutral (invented systems only), from the addendum and the
  private skill's steps. Add to the manager skill: the guard-on approval flow, validating a
  changed `guard.json`, learning by kind, same-shape one-liners and standing answers, the never-
  delegated cases (`gated` targets, production deletions), revoke on `DONE`/`BLOCKED`, using
  `coven guard explain` on a `BLOCKED` and before approving, reading the doctor refusals line at
  session start. Add the guard-on flow to the PM role text. Routing: when a project touches a
  live system with no `systems/` entry, use `systems-map`. README: a short "Production guard"
  section; known-gaps: the guard is best effort against a PM that follows its role, and the
  matcher was checked from docs, not live.
- [ ] Fast check; commit `docs: systems-map skill, approval rules, guard docs`.

### Rollout notes (not part of this build)

- On the owner's guarded machine, before switching: the shadow run of coven's guard against the
  private toolkit's over the same inputs (addendum, "Rollout").
- The live checks the build could not run: a PM session's tool call refused by the guard through
  the plugin's `*` registration (including an MCP tool and a subagent's call), and one let
  through with an approval.
