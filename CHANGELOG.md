# Changelog

## Unreleased

- A production guard: name your live systems in the memory store (`systems/guard.json`), and a
  PreToolUse hook refuses any tool call that reaches one unless a live approval names the exact
  command. It also holds project manager sessions off the store, coven's config, Claude Code's
  settings and `plugins/`.
- A call with no guard set costs one JSON read and one `stat` on top of Python's start-up: the
  hooks start one interpreter instead of two, cache their bytecode, and the guard's entry exits
  before it imports the guard.
- `coven guard check <file>` validates a `guard.json`; `coven guard explain` shows what the guard
  decides for one command or tool, and writes nothing.
- `coven approve <id> --expires-in <time>` turns a step file into committed approvals (at most 12
  hours); `coven approve --revoke <id>` removes them.
- `coven doctor` and `coven setup` report the guard, its `guard.json`, live approvals and the last
  five refusals.
- `coven memory adopt <path>` takes over an existing store in place, with a backup, a count check
  and the way back.
- New skill `systems-map`, plus the manager and project manager rules for approvals.
- The store's pre-commit hook is version 4: after you upgrade, run `coven memory init` so
  `coven memory sync` can push again.

## 0.1.0 (unreleased)

First version.

- Plugin and marketplace in one repo: `claude plugin marketplace add WitchyNibbles/coven`, then
  `claude plugin install coven@coven`.
- Skills: setup, manager, shared-memory, handoff, project-standard, clean-code, ceo-review,
  office-hours, grill-me, subagent-driven-development, requesting-code-review. Agents: explorer,
  implementer, reviewer, final-reviewer, project-manager.
- Hooks: deny-secrets (blocks `.env*` and `~/.ssh` access, fails closed), pm-permissions (decides a
  PM's `git push` and `gh` commands), routing instructions and a memory block at session start.
- CLI: `coven doctor`, `coven setup` (with `--dry-run` and `--undo`), `coven memory init|sync`,
  `coven pm status|launch|resume|health`, `coven project`.
- Code standard for Python and TypeScript repos, installed by `coven project`, with a GitHub
  Actions workflow.
- Shared memory store with a pre-commit hook that refuses token-like text.
- aquelarre, a second plugin in the marketplace and a Claude Code mod: `/aquelarre` opens a pane
  with one card per live project manager, a band above the prompt counts the ones working or
  waiting, and toasts say when one asks, needs a production step, finishes or dies. Settings
  (`language`, `band`, `toasts`) are in `/config`. coven installs it as a dependency, and
  `coven doctor` has an `aquelarre plugin` line (it needs Claude Code 2.1.325 or newer).
- `examples/toy`, a tiny project for the README walkthrough.
- Not in this version: Codex or OpenCode support, and native Windows.
