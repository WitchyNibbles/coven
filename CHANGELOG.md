# Changelog

## Unreleased

- `coven opencode install | setup | run | doctor`: coven's own OpenCode 2.3.2 in a profile of its
  own, apart from any OpenCode you use, with coven's plugin (`coven_ping`, `await_agents` and a
  `tool_choice` hook for LiteLLM and vLLM gateways) and an offline end-to-end run.
- `coven opencode setup` now ships coven's skills (`coven-*`), role agents, the `/coven-grill-me`
  and `/coven-handoff` commands and the routing, rendered from the Claude Code content by
  `python -m coven.opencode.content`; it copies five superpowers skills from your Claude Code
  install, points the model at your memory store and writes the secret rules and `todo` setting
  into the config. The plugin refuses a `read`, `grep`, `glob`, `shell`, `write`, `edit` or `patch`
  that `deny_secrets.py` refuses, and fails closed. `coven opencode doctor` adds a `superpowers`
  check.
- A production guard: name your live systems in the memory store (`systems/guard.json`), and a
  PreToolUse hook refuses any tool call that reaches one unless a live approval names the exact
  command. It also holds project manager sessions off the store, coven's config, Claude Code's
  settings and `plugins/`.
- The guard no longer refuses a project manager's quoted globs (`ls '.*'`, `grep -E "\s*"`, a
  quoted-delimiter heredoc) or its reads and script runs in Claude Code's `plugins/` folder;
  writes there are still refused (#24).
- The guard reads a sed script command by command, so a quoted glob next to an ordinary sed
  (`sed -E "s/[[:space:]]*.*/x/"`) stays literal; an `e`, `w` or `W`, a script file, an option
  or command it does not know, or a script some sed reads apart keeps it strict.
  It also follows a program run by `~/...` or `"$HOME/..."`, a sed operand by a folder variable
  (`CM=~/x; sed -n 1p $CM/a`), a piped sed that only prints, and each pipeline on a line apart.
  A sed script in a variable (`sed "$S" '.*'`) no longer counts as data. A refusal for glob-like
  text now says to write the script with Write and run it in its own call (#26).
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
