---
name: setup
description: Use on the first run after installing coven, or when `coven doctor` reports a problem. Guided setup: checks the install, explains each optional change, applies the ones the user picks, creates the memory store and ends with what to try first.
---

# coven setup

A guided first run. Go in order, keep each step short, and never change a file before the user
agreed to that change. Everything this skill writes goes through `coven` commands that show a diff
first, keep a backup and can be undone; do not edit `~/.claude/settings.json` or the config file by
hand.

If `coven` is not found in the Bash tool, the plugin's `bin/` is not on the path: run it as
`"${CLAUDE_PLUGIN_ROOT}/bin/coven"` instead. In the user's own shell it is only available after the
shim (step 3).

## 1. Check the install

Run `coven doctor`. It prints one line per check, `ok`, `warn` or `fail`, and exits 1 when any
check fails. Read the lines to the user in plain words:

- `fail` lines come first: fix them with the user before going on. A missing superpowers plugin is
  fixed with `claude plugin install superpowers@claude-plugins-official`; an old Python or Claude
  Code needs an upgrade the user does.
- `warn` lines are optional. A memory store that is not set up yet is expected on a first run:
  steps 3 and 4 create it. A missing `uv` only affects the code standard's checks; a missing or
  logged-out `gh` only affects project managers.

## 2. Explain the optional changes

None of these is needed to use the skills. Ask about each one, one question at a time, and record
the answer. Defaults are "no" except where noted.

- **Secret read denies** (`--deny-secrets`): adds rules to `permissions.deny` in
  `~/.claude/settings.json` so Claude cannot read `./.env*` files or anything under `~/.ssh/**`, `~/.aws/**` and
  `~/.gnupg/**`. The plugin's
  deny-secrets hook enforces the same rule either way; the settings rules make Claude Code itself
  refuse first.
- **Cross-session accept** (`--cross-session-accept`): sets `crossSessionInbound` to `"accept"`.
  Only needed for the manager hierarchy (a general manager session and background project managers
  that message each other). Without it Claude Code can hold such messages for five minutes when
  the two sessions run in different permission modes.
- **Shell shim** (`--shim`): installs `~/.local/bin/coven`, so `coven` works in the user's own
  terminal and not only inside Claude Code. It finds the current plugin folder at run time, so
  plugin updates do not break it. `~/.local/bin` must be on `PATH`. Without the shim, use
  `!coven ...` inside Claude Code.
- **Project manager cap** (`--pm-cap N`): how many background project managers may run at once
  (default 2). Say the cost plainly: each project manager is a separate background session on the
  most capable model, and its final reviews use that model too, so cost grows with the cap. The
  hierarchy was designed for at most 4. The manager skill reads this number as `pm_cap` from
  `~/.config/coven/config.json`.
- **Memory store** (`--memory-path PATH`, default `~/coven-memory`): a git repo of short notes
  that outlive a session. **Private remote** (`--memory-remote URL`): optional, for syncing the
  store between machines. The remote must be private: the store holds personal notes. Setup checks
  with `gh repo view` when `gh` is available and warns when it cannot check or the repo is public.
- Not touched by default: the main model and the permission mode. Explain what project managers
  need: each one is launched with its own permission mode and settings on its command line, so the
  user's own sessions keep whatever mode they use. Nothing to change here.

Also tell the user two things about the hierarchy, whether or not they use it now:
`claude --bg` is a Claude Code research preview, and each repo needs one interactive trust prompt
the first time a project manager starts there (run `claude` in that repo once and accept it).

## 3. Show the plan, then apply it

Build the command from the answers, for example:

```sh
coven setup --dry-run --deny-secrets --shim --pm-cap 2 --memory-path ~/coven-memory
```

Run it and show the user its output as is: each change, and where the store will be created. With
`--dry-run` nothing is written. Ask whether to apply it. Only after a clear yes, run the same line
with `--yes` in place of `--dry-run`:

```sh
coven setup --deny-secrets --shim --pm-cap 2 --memory-path ~/coven-memory --yes
```

Settings changes are merged key by key, with a timestamped backup next to the file; malformed JSON
stops setup and changes nothing. Tell the user it can be reversed: `coven setup --undo` shows what
it would restore (add `--yes` to do it); it restores only the keys setup wrote and leaves any key
that has changed since.

## 4. The memory store

`coven setup ... --yes` creates the store as its last step. If the user wants the store without any
settings changes, or setup did not create it, run `coven memory init` (add `--remote URL` when
they chose one); it is safe to run again on an existing store. If they chose a folder for the
store, run `coven setup --memory-path PATH --yes` instead (add `--memory-remote URL` for a remote):
it also saves the path to the config, which the hooks and `coven doctor` read. A remote is checked
for privacy either way. Both install the store's pre-commit hook, which refuses text that looks
like a token and rebuilds the index files. Then run `coven doctor` again and show the result: no `fail` lines should be left.

## 5. What to try first

End with a short note, in the user's words, of what to do next:

- Walk through `examples/toy` (the README's walkthrough: clone
  `https://github.com/WitchyNibbles/coven` and copy `coven/examples/toy` out; the same folder is in
  the plugin): a tiny project to try `coven project`, a plan and a review on, with no risk.
- In a real repo of their own: ask Claude to set up the code standard (it runs the
  `coven:project-standard` skill), or hand over a project and say "manage my projects" to start
  the manager.
- Save a standing preference once, for example how they like replies written: say "from now on ..."
  and Claude saves it in the memory store.
- Later, `coven doctor` re-checks everything, and `/coven:setup` can run again.
