<h1 align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/logo-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="docs/assets/logo-light.svg">
    <img src="docs/assets/logo-light.svg" alt="coven" width="220">
  </picture>
</h1>

<p align="center"><em>Plan, build test-first, review: one working method for your Claude Code agents.</em></p>

<div align="center">

[![coven-check](https://img.shields.io/github/actions/workflow/status/WitchyNibbles/coven/coven-check.yml?branch=main&label=coven-check&labelColor=171025)](https://github.com/WitchyNibbles/coven/actions/workflows/coven-check.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-7042B2?labelColor=171025)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-77D9FF?labelColor=171025)](#requirements)
[![Claude Code plugin](https://img.shields.io/badge/Claude%20Code-plugin-FFD477?labelColor=171025)](#install)

</div>

<div align="center">

[Install](#install) ⋆ [Setup](#setup) ⋆ [Walkthrough](#walkthrough-the-toy-project) ⋆
[Hierarchy](#the-hierarchy-and-what-it-costs) ⋆ [Code standard](#the-code-standard) ⋆
[Production guard](#production-guard) ⋆ [Known gaps](#known-gaps)

</div>

A Claude Code plugin for building software with agents. It packs one working method:

- a build pipeline: brainstorm, check the scope, plan, build test-first, review;
- a code standard that a Python or TypeScript repo installs and runs locally and in GitHub Actions;
- an agent hierarchy: a general manager (GM) session that runs one background project manager (PM)
  session per project;
- a small shared memory store (a git repo of short notes) that every session reads when it starts.

It works with Claude Code only. This is version 0.1.0; its rough edges are listed in
[docs/known-gaps.md](docs/known-gaps.md).

## Requirements

- ✧ Claude Code 2.1.224 or later. The hierarchy uses `claude --bg` (background sessions) and
  cross-session messaging; Claude Code calls background sessions a research preview, so they can
  change under coven.
- Python 3.12 or later as `python3` on your `PATH`. coven's hooks and CLI use only the standard
  library, so there is nothing to `pip install`. (macOS ships an older `python3`; install a newer
  one first.)
- `git`, with your name and email set (`git config --global user.name` and
  `git config --global user.email`): setup makes the first commit in the memory store, and the
  walkthrough below commits too.
- `gh`, logged in: PMs open pull requests with it, and setup uses it to check that the memory
  remote is private.
- `uv`: only to run the code standard's checks in a Python repo. A TypeScript repo needs `node` and
  its package manager instead.
- Linux, macOS or WSL 2. Native Windows is untested.

<p align="center"><picture><source media="(prefers-color-scheme: dark)" srcset="docs/assets/section-dark.svg"><source media="(prefers-color-scheme: light)" srcset="docs/assets/section-light.svg"><img src="docs/assets/section-light.svg" alt="" width="120"></picture></p>

## Install

```sh
claude plugin marketplace add WitchyNibbles/coven
claude plugin install superpowers@claude-plugins-official
claude plugin install coven@coven
```

✦ Then, inside Claude Code:

```
/coven:setup
```

If Claude Code says the `claude-plugins-official` marketplace is unknown, run
`claude plugin marketplace add anthropics/claude-plugins-official` first, then the second command.

The plugin uses the skills of the [superpowers](https://github.com/obra/superpowers) plugin
(brainstorming, writing plans, test-driven development and others). coven declares it as a
dependency, so installing coven installs and enables it too; the explicit line above is harmless
and covers an older Claude Code. `coven doctor` fails when it is missing.

If superpowers is installed but disabled, Claude Code refuses to load coven. Enable it with
`claude plugin enable superpowers@claude-plugins-official`.

<p align="center"><picture><source media="(prefers-color-scheme: dark)" srcset="docs/assets/section-dark.svg"><source media="(prefers-color-scheme: light)" srcset="docs/assets/section-light.svg"><img src="docs/assets/section-light.svg" alt="" width="120"></picture></p>

## What changes when you install

⋆ Installing coven changes every Claude Code session you start, in every repo, before you run
`/coven:setup`:

- A short routing text is added to each session. It suggests `project-standard` where a repo has no
  `.coven/` folder, and it tells the agent never to add an agent co-author trailer to a commit.
- The deny-secrets hook blocks reads and copies of `.env` files and of the key folders (`~/.ssh`
  and the like), everywhere.
- The project manager hooks do something only in PM sessions; in your own sessions they stay quiet.

To switch it off, run `claude plugin disable coven@coven` (`claude plugin enable coven@coven`
brings it back), and `coven setup --undo` to restore the settings that setup changed.

<p align="center"><picture><source media="(prefers-color-scheme: dark)" srcset="docs/assets/section-dark.svg"><source media="(prefers-color-scheme: light)" srcset="docs/assets/section-light.svg"><img src="docs/assets/section-light.svg" alt="" width="120"></picture></p>

## Setup

✦ `/coven:setup` is a guided first run. It runs `coven doctor`, explains each optional change, and
applies only the ones you pick. The changes:

- read-deny rules for `./.env*`, `~/.ssh/**`, `~/.aws/**` and `~/.gnupg/**` in
  `~/.claude/settings.json`;
- `crossSessionInbound: accept` in the same file, needed only for the GM and PMs to message each
  other;
- a `coven` shim in `~/.local/bin`, so the command works in your own terminal (inside Claude Code
  you can always run `!coven ...`);
- the PM cap (below) and the memory store's path and remote, written to
  `~/.config/coven/config.json`;
- the memory store itself (below).

Nothing is touched before you see it. Every settings change is shown as a diff first, merged key by
key, and written with a timestamped backup next to the file. The file is rewritten as JSON with
2-space indent, so a different layout of your own is not kept; the backup holds the original. Setup never edits `~/.claude/CLAUDE.md`, and it does not change
your main model or permission mode.

You can run the same steps by hand: `coven setup --dry-run --deny-secrets --shim` shows the plan,
and the same line with `--yes` in place of `--dry-run` applies it.

<p align="center"><picture><source media="(prefers-color-scheme: dark)" srcset="docs/assets/section-dark.svg"><source media="(prefers-color-scheme: light)" srcset="docs/assets/section-light.svg"><img src="docs/assets/section-light.svg" alt="" width="120"></picture></p>

## Walkthrough: the toy project

✧ [examples/toy](examples/toy) is a tiny Python project (one function, one test) that passes the
standard. Clone the coven repo, copy the toy out of it and put the standard on it. The commands
assume the `coven` shim from setup; without it, run the `coven` lines inside Claude Code as
`!coven ...`.

```sh
git clone https://github.com/WitchyNibbles/coven
cp -r coven/examples/toy ~/toy-demo && cd ~/toy-demo
git init && git add -A && git commit -m "toy"
coven project .
```

`coven project` writes `.coven/` and the GitHub Actions workflow, then tells you the one wiring it
cannot do for you: in `pyproject.toml`, `[tool.ruff] extend` must read `".coven/ruff.toml"`. (Inside
the coven repo the toy's line points two folders up, at coven's own copy; that is why it needs the
edit once it is on its own.) Open `pyproject.toml`, change `extend = "../../.coven/ruff.toml"` to
`extend = ".coven/ruff.toml"`, then:

```sh
coven project . --check
uv sync
git add -A && git commit -m "install the standard"
python3 .coven/check.py full --base "$(git rev-list --max-parents=0 HEAD)"
```

The last command prints one receipt line that starts `CHECK full exit=0` when everything passes.

Now try the pipeline in Claude Code, from `~/toy-demo`: ask it to add a feature, for example
"add a `wrap` function that wraps a value around a range", and watch the routing: a spec, a plan,
test-first work, and a full check before it says it is done. Throw the folder away afterwards.

<p align="center"><picture><source media="(prefers-color-scheme: dark)" srcset="docs/assets/section-dark.svg"><source media="(prefers-color-scheme: light)" srcset="docs/assets/section-light.svg"><img src="docs/assets/section-light.svg" alt="" width="120"></picture></p>

## The hierarchy and what it costs

✦ You talk to the GM. Hand it a project ("manage my projects") and it starts one background PM
session for that project, answers the PM's questions from evidence, and brings you only real
questions, production steps and results. A PM writes the code with its own helpers (an explorer, an
implementer, a reviewer, a final reviewer), opens a pull request, and never merges.

The GM drives three commands, and you can run them too. Each one prints a line to run and starts
nothing itself:

- `coven pm launch <code> --repo <repo> --brief <file>` prints the `claude --bg` line that starts
  `pm-<code>` in the repo with the brief as its prompt.
- `coven pm resume <code> --gm <name> [--add <file>]...` writes a resume prompt and prints the line
  that resumes an interrupted PM. It refuses while the PM still runs.
- `coven pm health --gm <name>` prints `ok` (exit 0), or one line per PM that needs the GM (exit 1).
  `coven pm status` shows each PM session's state, its last messages and what waits on the GM.

- A PM is held back from pushing to `main`, `master` or another protected branch (including the
  remote's default branch), from force-pushing, and from merging: a deny rule, a guard hook and
  the pm-permissions hook all check it. These guards are best effort against a PM that follows its
  role. The real wall is branch protection on the default branch in GitHub, so turn it on (see
  [known gaps](docs/known-gaps.md)). A production step (anything that
  changes a live system, a first push to a public repo, a release) goes to the GM, which shows it to
  you. With no guard it runs the step in your own session, where you see each permission prompt;
  with a [production guard](#production-guard) the PM runs only the commands you approved.
- Each PM runs with its own permission mode, so your own sessions keep whatever mode you use. The
  pm-permissions hook decides each `git push` and `gh` command the PM tries, and denies the rest.
- Each repo needs one interactive trust prompt the first time a PM starts there: run `claude` in
  that repo once and accept it.

🕯️ **Cost.** Each PM is a separate background session on the opus model, and its final reviews use opus
too. The more PMs run at once, the faster you spend. The cap is a setting, `pm_cap`, with default 2;
setup asks for it and the manager skill reads it. The hierarchy was designed for at most 4. coven
does not show what a PM has cost yet.

<p align="center"><picture><source media="(prefers-color-scheme: dark)" srcset="docs/assets/section-dark.svg"><source media="(prefers-color-scheme: light)" srcset="docs/assets/section-light.svg"><img src="docs/assets/section-light.svg" alt="" width="120"></picture></p>

## The code standard

✧ `coven project PATH` installs the standard in a repo: `.coven/check.py` and its parts, a ruff
config, and one GitHub Actions workflow (plus `tsconfig.base.json` and an ESLint config for
TypeScript). It refuses to overwrite a workflow or config that differs from coven's, shows the diff,
and overwrites only with `--force`. It prints the wiring it cannot do itself: dev dependencies to
add and `pyproject.toml` keys to set. `coven project PATH --check` reports drift and exits 1.

The check runs formatting, lint, strict types, tests with coverage and a few repo rules (no
suppressions, no agent co-author trailers). `python3 .coven/check.py fast <tests>` is for use while
you work; `python3 .coven/check.py full --base <ref>` is for the pull request, in CI too.

<p align="center"><picture><source media="(prefers-color-scheme: dark)" srcset="docs/assets/section-dark.svg"><source media="(prefers-color-scheme: light)" srcset="docs/assets/section-light.svg"><img src="docs/assets/section-light.svg" alt="" width="120"></picture></p>

## Memory store

☾ A git repo of short Markdown notes (default `~/coven-memory`): facts about you, standing rules,
facts per project, and handoff notes. Every session reads a short block from it at start. Only the
GM, or a plain session, writes to it; PMs read it and send facts up. A pre-commit hook in the store
refuses text that looks like a token and rebuilds its index files. `coven memory init` creates it
and `coven memory sync` fetches, rebases and pushes.

**The remote must be private.** The store holds personal notes. Setup asks `gh repo view` whether the
remote is private when it can, and warns when it cannot check. A remote is optional.

Already have a store in coven's format, at another path or from another machine?
`coven memory adopt <path>` makes it coven's store in place: a backup first, every entry kept and
counted, and the way back printed. Without `--yes` it prints the plan and changes nothing.

<p align="center"><picture><source media="(prefers-color-scheme: dark)" srcset="docs/assets/section-dark.svg"><source media="(prefers-color-scheme: light)" srcset="docs/assets/section-light.svg"><img src="docs/assets/section-light.svg" alt="" width="120"></picture></p>

## Production guard

✧ Optional. Name your live systems once, and no Claude Code session can reach them without an
approval for the exact command. With no guard set up, nothing changes for you.

- **Turn it on.** Ask the GM to map a system (`coven:systems-map`). It writes
  `systems/<system>.md` and `systems/guard.json` in the memory store, checks the targets with
  `coven guard check <file>`, commits them, then runs `coven setup --yes` to switch the guard on.
  A store with a guard never pushes: `coven memory sync` keeps it on your machine.
- **Approve.** A PM sends a production step, the GM shows it to you, and on a yes
  `coven approve <id> --expires-in 2h` commits an approval for those exact commands (at most 12
  hours). The PM runs them, the guard refuses anything else, and the GM takes the approval back
  with `coven approve --revoke <id>` when the task is done.
- **Explain.** `coven guard explain "<command>"` in your own terminal shows which targets a
  command hits, which approval covers it, and whether a PM or every session would be refused. It
  writes nothing. Inside a Claude Code session, write the command to a file and use
  `coven guard explain --file <path>`: an inline command that names a target is refused there.
- **Doctor.** `coven doctor` shows whether the guard is on and the last five refusals.

The guard is best effort against a PM that follows its role, and it never covers your own terminal
outside Claude Code. See [known gaps](docs/known-gaps.md).

## Aquelarre

☾ A second plugin in the same marketplace that installs with coven (coven lists it as a
dependency): a mod that shows your live project managers. It needs Claude Code 2.1.325 or newer;
`coven doctor` warns on an older one.

- **Pane.** `/aquelarre` opens a pane beside the chat with one card per live PM: its state, how
  long it has been in it, its plan, what it waits on and its last messages.
- **Band.** One row above the prompt that counts the PMs working or waiting.
- **Toasts.** One when a PM asks a question, needs a production step, finishes or dies.
- **Settings.** Three rows in `/config`: `language` (`en` or `es`, default `en`), `band` and
  `toasts` (both on by default). A change takes effect when the mod reloads, for example in the
  next session.

It reads `coven pm status` and writes only `~/.cache/aquelarre/`. See
[plugins/aquelarre](plugins/aquelarre/README.md).

<p align="center"><picture><source media="(prefers-color-scheme: dark)" srcset="docs/assets/section-dark.svg"><source media="(prefers-color-scheme: light)" srcset="docs/assets/section-light.svg"><img src="docs/assets/section-light.svg" alt="" width="120"></picture></p>

<p align="center"><picture><source media="(prefers-color-scheme: dark)" srcset="docs/assets/section-dark.svg"><source media="(prefers-color-scheme: light)" srcset="docs/assets/section-light.svg"><img src="docs/assets/section-light.svg" alt="" width="120"></picture></p>

## Uninstall

1. ⋆ `coven setup --undo` shows what it would restore; add `--yes` to do it. It restores only the keys
   setup wrote and skips, by name, any key that has changed since. `--undo --yes` also removes the
   shim and the `config.json` setup created; an empty `~/.config/coven` folder may remain. The
   timestamped backups beside `~/.claude/settings.json` are for you to read; coven never restores
   one over later changes.
2. Remove the plugin and its marketplace:
   `claude plugin uninstall coven@coven` and `claude plugin marketplace remove coven`. The uninstall
   leaves aquelarre, which Claude Code installed as its dependency, on and showing its band;
   `claude plugin prune` removes it, or switch the band off in `/config` (`band`).
3. Delete what is left of `~/.config/coven` and, if you want, the memory store (`~/coven-memory` or
   the path you chose). The store is your notes: copy out what you keep first. Logs live in
   `${XDG_CACHE_HOME:-~/.cache}/coven`.

<p align="center"><picture><source media="(prefers-color-scheme: dark)" srcset="docs/assets/section-dark.svg"><source media="(prefers-color-scheme: light)" srcset="docs/assets/section-light.svg"><img src="docs/assets/section-light.svg" alt="" width="120"></picture></p>

## Known gaps

✧ [docs/known-gaps.md](docs/known-gaps.md) lists what coven assumes about Claude Code, what it has
not verified and what it does not protect against. Read it before you let a PM loose on a repo.

<p align="center"><picture><source media="(prefers-color-scheme: dark)" srcset="docs/assets/section-dark.svg"><source media="(prefers-color-scheme: light)" srcset="docs/assets/section-light.svg"><img src="docs/assets/section-light.svg" alt="" width="120"></picture></p>

## Credits

⋆ Some skills are adapted from MIT-licensed projects: [obra/superpowers](https://github.com/obra/superpowers),
[garrytan/gstack](https://github.com/garrytan/gstack) and
[mattpocock/skills](https://github.com/mattpocock/skills). Their notices are in [NOTICE](NOTICE)
and in each skill's `LICENSE`. See [CHANGELOG.md](CHANGELOG.md) for what changed between versions.

<p align="center"><picture><source media="(prefers-color-scheme: dark)" srcset="docs/assets/section-dark.svg"><source media="(prefers-color-scheme: light)" srcset="docs/assets/section-light.svg"><img src="docs/assets/section-light.svg" alt="" width="120"></picture></p>

## License

✦ MIT, see [LICENSE](LICENSE).

<br>

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/divider-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="docs/assets/divider-light.svg">
    <img src="docs/assets/divider-light.svg" alt="" width="320">
  </picture>
</p>

<p align="center"><sub>☾ brewed by <a href="https://github.com/WitchyNibbles">WitchyNibbles</a></sub></p>
