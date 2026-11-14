---
name: clean-code
description: "Use when writing, refactoring or reviewing Python or TypeScript code: per-language idioms, typing, error handling, module layout and test conventions beyond what the linters enforce."
---

# Clean code

Conventions the linters and type checkers cannot see. Load the file for the language you touch:

- Python (3.12+, uv, pytest, pydantic v2, FastAPI, SQLAlchemy 2, MCP servers): `python.md`
- TypeScript (tsc strict, typescript-eslint, zod, vitest or node:test, MCP SDK): `typescript.md`

## Authority

- The repo's `.coven` standard and its tools win over this file. For Python that is ruff and
  mypy; for TypeScript, the repo's lint and type config. If they disagree, follow them.
- Nothing here justifies a suppression, a weaker type, or a relaxed rule. A rule you cannot meet
  without one means the design is wrong: change the design, or stop and report.

## Rules for any language

- Organize by feature: the module that owns a feature holds its types, logic and tests' subject.
- Name for what a thing is or does; no `utils`, `helpers`, `manager`, `data`, `info`.
- One function, one job: if its name needs "and", split it.
- Prefer returning values over mutating arguments; keep side effects at the edges.
- Raise or throw specific error types, never swallow one, add context when re-raising.
- Validate at trust boundaries (I/O, network, user input, config), then trust the typed result.
- Comments say why, not what; delete commented-out code.
- Tests assert behaviour through the public interface, not implementation details.
- One behaviour per test, named for it; build data with factories, not shared mutable fixtures.
- Never use sleeps, real clocks or the network in unit tests.
