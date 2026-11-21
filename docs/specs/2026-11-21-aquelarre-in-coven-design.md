# aquelarre ships with coven

aquelarre is a Claude Code mod (a plugin with a TypeScript hooks module) that shows the live coven
project managers: a pane opened with `/aquelarre` (one card per PM with its plan from its task
list), a one-line band above the prompt, and toasts when a PM starts waiting, finishes or dies. It
lived in a private repo of its own. After this change, installing coven also installs aquelarre,
and aquelarre is versioned, checked and tested with coven.

## Goals

- Installing `coven@coven` installs aquelarre. Same version number as coven.
- aquelarre's 253 tests keep passing, and run in coven's check and in CI (GitHub free tier: the
  two existing jobs, no new paid runners).
- The check standard holds aquelarre's TypeScript to a real check (types and tests), not an
  exemption.
- Nothing personal in tracked files or commits: no private toolkit name where coven is meant, no
  machine-specific path, the public author identity only.

## Non-goals

- No change to what the pane, band or toasts show beyond the "Behaviour calls" below.
- No release, tag or publishing of aquelarre's own repo (production steps, asked separately).
- No ESLint/Prettier/coverage for the mod: the mod runtime has no Node, and its test kit only runs
  under `claude plugin test`.

## Anti-goals

- A `.gitattributes` `linguist-vendored` mark (or any other exemption) to make the check stop
  flagging the `.tsx` files. aquelarre is the user's own code; the standard does not allow
  suppressions.
- A second copy of the mod that drifts: after the merge the coven copy is the only source.

## Approaches

Packaging:

- **A. Inside the coven plugin.** `hooks/hooks.json` gains a `modules` key next to `hooks`, and
  `register.tsx` sits in coven's `hooks/`. A scratch plugin with both keys passes
  `claude plugin validate`. Rejected: `claude plugin test .` would run from coven's root over the
  whole repo, `types/` and the mod's `tsconfig.json` would sit at coven's root, and aquelarre could
  never be enabled or disabled on its own.
- **B. A second plugin in coven's marketplace, listed in coven's `dependencies`.** Chosen. The mod
  keeps its own folder (`plugins/aquelarre/`), its own manifest and tests. Receipt (scratch
  marketplace, isolated `CLAUDE_CONFIG_DIR`, Claude Code 2.1.330): installing the dependent plugin
  printed `(+ 1 dependency: pane)`, and both were enabled. Trade-off: Claude Code refuses to
  disable the dependency while the dependent is on (`pane is still required by core`).
- **C. A second plugin, no dependency, installed by `coven setup`.** Keeps a clean opt-out but a
  plain `claude plugin install coven@coven` would not bring aquelarre, against the goal.
- **D. aquelarre's own repo and marketplace.** Rejected: it would not be versioned or tested with
  coven, and publishing it is a production step the user did not ask for.

Checking the TypeScript:

- **i. A new project kind in the check standard: a Claude Code mod.** Chosen. A directory with
  `.claude-plugin/plugin.json` and a `hooks/hooks.json` whose `modules` list is non-empty is a mod
  project. Its files are covered (no "outside every project"), and its stages run Claude Code's
  own tools: `claude plugin validate --strict <dir>` and `claude plugin test <dir>`. Receipts:
  `validate --strict` reports a planted `TS2322` as a warning and exits 1, and exits 1 on a clean
  copy only for the missing `author` field (fixed in the manifest); `claude plugin test` passes
  253/253 with an empty `HOME` and `CLAUDE_CONFIG_DIR` (no login) on 2.1.325 and 2.1.330.
- **ii. Mark the mod `linguist-vendored`.** Rejected: an exemption (anti-goal).
- **iii. Make it a full TypeScript project (package.json, lockfile, ESLint, vitest).** Rejected:
  the tests import `claude-code/testing`, which exists only under `claude plugin test`; it would
  mean a Node toolchain the mod never uses at run time and rewriting 253 tests.

History: one import commit, no aquelarre history. Precedent: coven v0.1.0 itself was published as
a one-commit export of its build branch. aquelarre's 41 commits and its specs and plans quote
private chat, `/tmp` paths and the private toolkit; the private repo stays on disk as the archive.

## Design

### Layout

```
.claude-plugin/marketplace.json   + entry {name: aquelarre, source: ./plugins/aquelarre}
.claude-plugin/plugin.json        + "dependencies": [..., "aquelarre"]
plugins/aquelarre/
  .claude-plugin/plugin.json      name, version = coven's, description, author, license, homepage, types
  .gitignore                      .claude-plugin/types/ (the engine writes it)
  README.md                       what it shows, /aquelarre, settings, where it reads from
  hooks/hooks.json                { "modules": ["./register.tsx"] }
  hooks/register.tsx
  hooks/register.test.tsx
  types/index.d.ts
  tsconfig.json                   extends ./.claude-plugin/types/tsconfig.json (editor and local tsc)
```

The manifest description drops the private toolkit's name ("the active coven project managers").
The version goes from aquelarre's 0.2.0 to coven's 0.1.0; it was never published. A test keeps the
two versions equal.

### Scrubbing

- Code and comments: every mention of the private toolkit where coven is meant becomes coven.
- Tests: the fixture home (a made-up user name) becomes the placeholder `user` (coven's privacy test allows only
  placeholder names); PM names and senders that use the private toolkit's name become neutral
  ones. Assertions do not change.
- The status fallback to the private toolkit and its role variable are removed (call 4 below),
  with their tests.

### The check standard learns mods

In `standard/check_repo.py` (and the installed `.coven/` copy):

- `Project.kind` gains `"mod"`. `discover` finds a mod as above. A mod inside a TypeScript project
  is left to that project (it has its own tools); a mod inside a Python project is still a mod
  (the Python project does not check `.ts`).
- `outside_problems` counts TypeScript files under a mod as covered.

In `standard/check.py` the mod stages run in `fast` and `full`:

- `mod types (<dir>)`: `claude plugin validate --strict <dir>`.
- `mod tests (<dir>)`: `claude plugin test <dir>`.
- `claude` missing from `PATH`: the stage fails with a message that names it (the standard fails,
  never skips, on a missing tool).
- Both run with the caller's environment; neither needs a login.

`install` does nothing for a mod (it has no lockfile). The workflow (`standard/workflows/coven-check.yml`,
equal to coven's `.github/workflows/coven-check.yml`) installs Node when the repo has a mod and
installs Claude Code at a pinned version (`npm install -g @anthropic-ai/claude-code@<pin>`) before
the check. In coven a test keeps that pin equal to `LAST_TESTED` (`src/coven/claude_version.py`),
like the smoke job's pin.

### CI

- `coven-check`: the full check now runs the mod stages (above).
- `install-smoke`: after `claude plugin install coven@coven`, aquelarre is installed and enabled
  (`claude plugin list`), with the dependency; `claude plugin validate --strict` also runs on
  `plugins/aquelarre`.

### coven doctor and setup

- `coven doctor` gains an `aquelarre plugin` line: ok when the plugin is in the installed-plugins
  file, warn otherwise (the same way `superpowers plugin` is read). The smoke allowlist adds it.
- `coven setup` does nothing new with B (the dependency installs it). With C it would offer the
  install.

### Docs

- README: a short section on the pane (what it shows, `/aquelarre`, how to turn parts off), with
  the brand glyph lead the README test requires; Uninstall notes that coven's uninstall leaves the
  auto-installed aquelarre (`claude plugin prune` removes it).
- CHANGELOG: an entry under 0.1.0 (unreleased).
- `docs/known-gaps.md`: the mod API is early access; the mod is type-checked through
  `claude plugin validate --strict` (no `tsc` run in CI, since the engine lays the types only for a
  live session); the tested Claude Code versions.

## Behaviour calls (decided by the user, 2026-11-21)

1. Opt-out: hard dependency plus plugin `userConfig` switches (rows in `/config`) to turn the band
   and the toasts off; both default on. The pane only opens on `/aquelarre`.
2. Language: English by default with a `language` setting (`en`, `es`, a picker). Tests that
   assert Spanish text run with `language: es` passed as options (`test(name, { options }, body)`);
   new tests cover the English text and the default.
3. Empty band: keep the dim empty-band line ("no one working right now" in English).
4. Private toolkit fallback: dropped. The mod reads only `coven pm status`, and only
   `COVEN_SESSION_ROLE` marks a PM session. A missing `coven` shows the existing read-error band.

## Settled after the builder check

- **Oldest Claude Code.** On 2.1.224 (coven's `MINIMUM`) a scratch dependency still installs
  (`+ 1 dependency`), but `claude plugin validate` rejects a modules-only `hooks.json`
  (`hooks: expected record`) and `claude plugin test` does not exist. 2.1.325 runs all 253 tests.
  coven's `MINIMUM` stays (coven itself does not need mods); `coven doctor`'s aquelarre line warns
  when Claude Code is older than 2.1.325, the oldest version aquelarre was tested on, and
  `docs/known-gaps.md` says so. `LAST_TESTED` and the CI pins stay at 2.1.325.
- **`coven` on PATH.** `$.process.run` runs with the host's environment, and a plugin's `bin/`
  "joins `PATH` whole" (the API's `PluginProvides.bin` doc). coven's `bin/coven` is therefore
  reachable from the mod with no extra lookup. A live check in a session at the end of the build
  confirms it (the receipt goes in the PR); if it fails, a lookup through the installed plugins
  file is a follow-up.
- **Receipt fields for a mod.** `settings`: the mod's manifest and hooks file read by `validate`,
  plus the standard's suppression scan (`@ts-ignore`, `@ts-expect-error`, `@ts-nocheck`,
  `eslint-disable`) over the mod's sources, which fails on any hit. `types`: `validate --strict`.
  `tests`: `plugin test`, its pass count added to `tests=`. `format`, `lint`, `coverage`: `n/a`
  for a mod, printed per project and listed in `docs/known-gaps.md` (the mod has no Node
  toolchain; `claude plugin test` reports no coverage). Python and TypeScript projects keep their
  gates; `n/a` is never produced for them.
- **Reach.** The mod kind is part of the shared standard: any repo with a mod gets it through
  `coven project`. The shared workflow installs Node and the pinned Claude Code only when
  `**/.claude-plugin/plugin.json` exists; drift tests keep the two workflow copies equal.

## Left to the builder

- How `check.py` scopes mod tests in `fast` with explicit test paths.
- Exact wording of the new doctor line and README section.
- Where the English and Spanish strings live (one table in `register.tsx`).

## After the merge (not in the PR)

On the user's machine a settings entry loads the private repo's folder as a session plugin; once
coven is reinstalled, two plugins named `aquelarre` would load. Removing that entry is a settings
change for the general manager, as is setting the user's own `language` to `es`.

## CEO review (hold scope, 2026-11-21)

Scope is the user's task; the review makes it hold.

- **coven off, aquelarre on.** Disabling or uninstalling coven leaves the auto-installed
  aquelarre enabled; the band then shows its read error in every session. The README's Uninstall
  section names `claude plugin prune` (removes auto-installed dependencies) and the band switch.
- **A copy inside coven's install.** coven's marketplace entry has `source: "./"`, so coven's own
  install folder also holds `plugins/aquelarre/`. coven never loads it (no plugin scans nested
  folders); the builder confirms `claude plugin validate --strict .` at the root and
  `test_plugin_structure.py` stay green.
- **English widths.** The layout code cuts words to fit narrow docks, and its tests were written
  against Spanish word lengths. English tests cover the narrow cut order and the band's compact
  form, not only the wide card.
- **Updates.** `claude plugin update` does nothing while a version stays the same (fact from
  2026-11-20); aquelarre now moves with coven's version, so a coven release bumps both. The
  version-equality test makes a forgotten bump fail.
- **Errors the user sees.** `coven` missing or failing: the existing read-error band, which keeps
  the last cards. `claude` missing in the check: the mod stage fails naming the tool. A Claude Code
  too old for mods: the doctor warning; the session itself logs that aquelarre did not load.
- **Security.** No new reach: the mod already runs `coven pm status` and writes only
  `~/.cache/aquelarre/` (mode 600). CI installs Claude Code from npm at a pinned version, no token.
- **Deferred.** Coverage, format and lint for mods (no tool in the mod runtime); a lookup of
  `coven` through the installed plugins file if the live check shows `bin/` is not on the mod's
  `PATH`.
