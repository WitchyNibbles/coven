# coven

A Claude Code plugin for building software with agents. It packs one working method:

- a build pipeline: brainstorm, check the scope, plan, build test-first, review;
- a code standard that a Python or TypeScript repo installs and runs locally and in GitHub Actions;
- an agent hierarchy: a general manager (GM) session that runs one background project manager (PM)
  session per project;
- a small shared memory store (a git repo of short notes) that every session reads when it starts.

It works with Claude Code only. This is version 0.1.0; its rough edges are listed in
[docs/known-gaps.md](docs/known-gaps.md).

## Requirements

- Claude Code 2.1.224 or later. The hierarchy uses `claude --bg` (background sessions) and
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

## Install

```sh
claude plugin marketplace add WitchyNibbles/coven
claude plugin install superpowers@claude-plugins-official
claude plugin install coven@coven
```

Then, inside Claude Code:

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

## What changes when you install

Installing coven changes every Claude Code session you start, in every repo, before you run
`/coven:setup`:

- A short routing text is added to each session. It suggests `project-standard` where a repo has no
  `.coven/` folder, and it tells the agent never to add an agent co-author trailer to a commit.
- The deny-secrets hook blocks reads and copies of `.env` files and of the key folders (`~/.ssh`
  and the like), everywhere.
- The project manager hooks do something only in PM sessions; in your own sessions they stay quiet.

To switch it off, run `claude plugin disable coven@coven` (`claude plugin enable coven@coven`
brings it back), and `coven setup --undo` to restore the settings that setup changed.

## Setup

`/coven:setup` is a guided first run. It runs `coven doctor`, explains each optional change, and
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

## Walkthrough: the toy project

[examples/toy](examples/toy) is a tiny Python project (one function, one test) that passes the
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

## The hierarchy and what it costs

You talk to the GM. Hand it a project ("manage my projects") and it starts one background PM
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
  you and runs it in your own session, where you see each permission prompt.
- Each PM runs with its own permission mode, so your own sessions keep whatever mode you use. The
  pm-permissions hook decides each `git push` and `gh` command the PM tries, and denies the rest.
- Each repo needs one interactive trust prompt the first time a PM starts there: run `claude` in
  that repo once and accept it.

**Cost.** Each PM is a separate background session on the opus model, and its final reviews use opus
too. The more PMs run at once, the faster you spend. The cap is a setting, `pm_cap`, with default 2;
setup asks for it and the manager skill reads it. The hierarchy was designed for at most 4. coven
does not show what a PM has cost yet.

## The code standard

`coven project PATH` installs the standard in a repo: `.coven/check.py` and its parts, a ruff
config, and one GitHub Actions workflow (plus `tsconfig.base.json` and an ESLint config for
TypeScript). It refuses to overwrite a workflow or config that differs from coven's, shows the diff,
and overwrites only with `--force`. It prints the wiring it cannot do itself: dev dependencies to
add and `pyproject.toml` keys to set. `coven project PATH --check` reports drift and exits 1.

The check runs formatting, lint, strict types, tests with coverage and a few repo rules (no
suppressions, no agent co-author trailers). `python3 .coven/check.py fast <tests>` is for use while
you work; `python3 .coven/check.py full --base <ref>` is for the pull request, in CI too.

## Memory store

A git repo of short Markdown notes (default `~/coven-memory`): facts about you, standing rules,
facts per project, and handoff notes. Every session reads a short block from it at start. Only the
GM, or a plain session, writes to it; PMs read it and send facts up. A pre-commit hook in the store
refuses text that looks like a token and rebuilds its index files. `coven memory init` creates it
and `coven memory sync` fetches, rebases and pushes.

**The remote must be private.** The store holds personal notes. Setup asks `gh repo view` whether the
remote is private when it can, and warns when it cannot check. A remote is optional.

## Uninstall

1. `coven setup --undo` shows what it would restore; add `--yes` to do it. It restores only the keys
   setup wrote and skips, by name, any key that has changed since. `--undo --yes` also removes the
   shim and the `config.json` setup created; an empty `~/.config/coven` folder may remain. The
   timestamped backups beside `~/.claude/settings.json` are for you to read; coven never restores
   one over later changes.
2. Remove the plugin and its marketplace:
   `claude plugin uninstall coven@coven` and `claude plugin marketplace remove coven`.
3. Delete what is left of `~/.config/coven` and, if you want, the memory store (`~/coven-memory` or
   the path you chose). The store is your notes: copy out what you keep first. Logs live in
   `${XDG_CACHE_HOME:-~/.cache}/coven`.

## Known gaps

[docs/known-gaps.md](docs/known-gaps.md) lists what coven assumes about Claude Code, what it has
not verified and what it does not protect against. Read it before you let a PM loose on a repo.

## Credits

Some skills are adapted from MIT-licensed projects: [obra/superpowers](https://github.com/obra/superpowers),
[garrytan/gstack](https://github.com/garrytan/gstack) and
[mattpocock/skills](https://github.com/mattpocock/skills). Their notices are in [NOTICE](NOTICE)
and in each skill's `LICENSE`. See [CHANGELOG.md](CHANGELOG.md) for what changed between versions.

## License

MIT, see [LICENSE](LICENSE).
