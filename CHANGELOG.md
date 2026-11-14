# Changelog

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
- `examples/toy`, a tiny project for the README walkthrough.
- Not in this version: Codex or OpenCode support, and native Windows.
