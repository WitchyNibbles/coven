---
name: project-standard
description: Use when starting work in a Python or TypeScript repo that has no .coven/ folder, when `coven project --check` reports drift, or when asked to set up or update the code quality standard.
---

# Project Standard

Installs coven's code quality standard into a repo (`.coven/`, a CI workflow, wiring in the
project config) and gets the code to pass it. The standard is owned by coven: it is the same for
every repo and is changed in coven, never in the repo.

## 1. Whose repo, and a branch

Installing the standard writes files into the repo and adds a CI workflow. Run it on your own only
when the repo is the user's: it has no remote (`git remote` prints nothing), or the user has said
it is theirs. For a clone of someone else's project, or a remote you cannot place (`git remote -v`),
ask first. If the user declines, do not install anything: use the repo's own checks instead.

Once cleared, create the fix-up branch (for example `git switch -c coven-standard`) before running
anything else below, so no file is written on the current branch.

## 2. Install

Run `coven project` at the top of the repo (it refuses a subdirectory; use `coven project --check`
to see drift, it prints `coven project: no drift` or lists what differs and exits 1). It prints
`wrote` or `unchanged` per file, then the wiring left to do by hand. If a workflow, `ruff.toml` or
TypeScript config of the repo differs from coven's, it stops, shows the diff and writes nothing;
re-run with `--force` only after the user agrees to replace that file.

It finds the projects as `check.py` does: a Python project is a directory with `pyproject.toml`, a
TypeScript project one with `package.json` and `tsconfig.json` (an npm, pnpm or yarn workspace root
is one project). Monorepos: one `.coven/` at the repo root; wire every project. A wiring line for a
project below the root starts with its directory (`backend: ...`, `run (in web): ...`).

The standard supports uv projects: a Python project needs a committed `uv.lock` (`uv lock` creates
it). Without one, `check.py` stops with `error="no uv.lock (the standard supports uv projects)"`.
A TypeScript project needs its package manager's committed lockfile.

Do every wiring line:

- Python: edit `pyproject.toml` so it extends `.coven/ruff.toml` (the path the line names) and
  carries the mypy, pytest and coverage settings the line asks for. Run the `uv add --dev ...` line
  it prints.
- TypeScript, in each project the lines name:
  - Each tsconfig the lines name (every one that compiles files; a solution `tsconfig.json` with
    `"files": []` only refers to others): put the base's path last in `extends` (make it a list),
    keeping existing entries before it, so nothing after it can weaken it. A tsconfig whose
    `extends` chain ends with the base (each config extending the next last) is wired: a workspace
    whose packages extend a shared base wires that base once, and the line names the shared base.
  - `eslint.config.*` (create `eslint.config.mjs` if there is none): import the base and spread it
    last, so nothing after it can weaken it:
    `import covenStandard from "../.coven/eslint.config.mjs";` then
    `export default [...yourConfigs, ...covenStandard];` (the path the line names).
  - A project at the repo root also adds `{ ignores: [".coven/"] }` to that list: its own
    `eslint .` would otherwise lint the standard's files, which no tsconfig of the project
    compiles.
  - Run the `run (in <dir>): ... -D ...` line it prints to add the missing dev dependencies, and
    fix what it says keeps the project's tools from running (dependencies not installed, `node`
    not on PATH, Yarn Plug'n'Play, no lockfile).
  - Every TypeScript and JavaScript file git lists must be in a tsconfig, configs included (see
    section 3). Once the tools run, re-run `coven project`: it then lists the files no tsconfig
    compiles and every resolved setting that is weaker than the standard.
- A wiring line that says `not allowed` means delete that setting or file; the standard wins. When
  it names a setting inside a multi-purpose file (`setup.cfg`, `tox.ini`, `pyproject.toml`),
  delete only the flagged key or section and keep the rest of the file.
- A `delete .coven/<file>: no ... project uses it` line means delete that file: it belongs to a
  kind of project the repo no longer has.

Never edit files under `.coven/`; the next `coven project` overwrites them.

CI workflow: `.github/workflows/coven-check.yml` is one job that runs `check.py full`.

## 3. Fix-up

Run `python3 .coven/check.py full`. Its last line is the receipt, starting with `CHECK `. Without
`--base` it measures changed-line coverage from where the branch left the default branch (`base=`
in the receipt names that commit). Full mode runs the Python tests in parallel (`pytest -n auto`,
so `pytest-xdist` is a required dev tool); while a repo fixes tests that are not parallel-safe
(shared ports, paths, databases), `PYTEST_XDIST_AUTO_NUM_WORKERS=1` is the fallback.

Fix every finding, including any `not allowed` line from `check.py full` (same rule as in section
2), and re-run until the receipt says `exit=0`. Remove every inline suppression by fixing the code;
for an untyped library, add a stubs package or a typed wrapper. This is one dedicated task on its
own branch, before any other work.

Notebooks (`.ipynb`) are scanned line by line, never tokenized, so suppression-like text anywhere
in a code cell (strings and `%%html` or `%%bash` cells included) fails the check; move such text
out of the notebook.

In TypeScript and JavaScript files, suppression-like text anywhere (strings, template literals and
regex literals included, and markers split across the lines of a block comment) also fails the
check; build such strings from pieces instead. That covers ESLint directives (`eslint-disable`,
`/* eslint`, `/* global`, `/* exported`, `eslint-env`), `@ts-ignore`, `@ts-expect-error`,
`@ts-nocheck`, coverage hints (`c8`, `v8`, `istanbul`, `node:coverage`) and formatter or linter
ignores (`prettier-ignore`, `@noformat`, `@noprettier`, `biome-ignore`, `deno-lint-ignore`,
`deno-fmt-ignore`).

TypeScript projects (`python3 .coven/check.py install` installs every project's locked
dependencies, as CI does):

- The test runner is Vitest, or c8 with `node:test`; anything else stops the check (no migration:
  wire coverage on the runner the repo has). Every project needs `node`, bun projects too. Yarn
  Plug'n'Play is not supported: set `nodeLinker: node-modules` in `.yarnrc.yml`.
- CI supports npm and yarn projects only: the workflow sets up Node, which brings npm; yarn 1 is
  preinstalled on GitHub-hosted runners only (a self-hosted runner may lack it). A pnpm or bun
  project stops there with `error="<dir>: pnpm not found on PATH"` (or `bun`; a project at the repo
  root has no `<dir>: ` prefix: `error="pnpm not found on PATH"`), and yarn 2+ needs corepack or a
  committed `yarnPath` to run on the runner. Tell the user; do not edit the workflow.
- ESLint must lint every TypeScript and JavaScript file git lists in the project; one its config
  ignores is a settings problem (vendored or generated files are left out through
  `.gitattributes`, not ESLint ignores). The standard's rules are type-aware, so config files
  (`eslint.config.mjs`, `vite.config.ts`, ...) need a tsconfig that compiles them: add them to
  `include` (with `"allowJs": true` for `.mjs` and `.js`), or give them their own
  `tsconfig.node.json` referenced from `tsconfig.json`, as Vite does.
- Prettier checks every file git lists that it knows how to format. `.prettierignore`,
  `requirePragma` and `checkIgnorePragma` cannot skip files: the check overrides them.
- With Vitest, set `coverage.include` (Vitest counts untested files only then); full mode passes
  `--coverage.changed`.
- Full mode fails when a changed source file that holds code that runs is absent from the coverage
  report. Left out: files below a `test/`, `tests/` or `__tests__/` directory; test files by the
  runner's convention (Vitest `*.test.*` and `*.spec.*`; `node:test` `*.test.*`, `*-test.*`,
  `*_test.*`, `test-*` and `test.*`); the configs of known tools (`vite.config.*`,
  `vitest.config.*`, `eslint.config.*`, `prettier.config.*`, `jest.config.*` and the like; any
  other `*.config.*` is the project's own code); `.d.ts` files; and files of only types.
- Every tsconfig is type-checked, including every one another tsconfig refers to. Those with
  `references` (solution-style ones too) are built first, in one `tsc -b --force` that runs before
  the lint, as `npm run build` would; the others no build reaches get `tsc --noEmit`. The build
  writes its output into the tree: keep it git-ignored (`dist/`, and `*.tsbuildinfo`, which
  `tsc -b` leaves beside each tsconfig it builds that sets no `outDir` or `tsBuildInfoFile`), and
  have ESLint ignore it.
- `.vue`, `.svelte` and `.astro` files fail the check: the standard does not cover them yet.

Files git tracks below a `node_modules` directory fail the check: untrack them. A symlink inside
`.coven/` (or a symlinked `.coven/`) and any file in `.coven/__pycache__` also fail it; delete
them and re-run `coven project`.

No rule may be switched off in the repo. If a rule blocks real work, stop and tell the user: it is
changed in coven, for every repo.

## 4. Commit

Commit `.coven/`, the workflow and the wiring, then re-run
`python3 .coven/check.py full --base "$(git merge-base HEAD <default branch>)"`. The task is done
only when that final receipt says `exit=0`, `dirty=no` and `runner=uv`. Show the user the receipt
line. If the repo is on GitHub, suggest making the `coven-check` job a required check in the
branch protection settings; that is a setting only the user applies.
