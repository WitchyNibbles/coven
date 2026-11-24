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
- The guard now holds project manager sessions off all of Claude Code's user folder
  (`~/.claude` or CLAUDE_CONFIG_DIR, and `~/.claude.json`), not only `settings*.json` and
  `plugins/`: a `rm -rf ~/.claude` through broken quoting no longer gets through. Reads stay
  allowed (`cat`, `ls`, `grep`, `rg`, `find` without actions, the Read, Grep and Glob tools,
  `jq`, `cd ~/.claude && ls skills 2>/dev/null`, and a `for` loop whose body only reads), and so
  do writes in a repo's own `.claude/worktrees/`. A read now also works behind `env LC_ALL=C`,
  `timeout`, `nice` and `command`, in a pipe through `sort`, `uniq`, `cut`, `tr`, `column` and
  the like, with output to a file outside the folder, with input from a file, inside `$(...)`, a
  `{ ...; }` group, `[ ... ]` and `find -printf`, with `cp` out of it, and when the session runs
  in the folder itself, and through an `xargs` that runs a plain reader. A session may write in
  its own job folder (CLAUDE_JOB_DIR) and session folder, but not their `subagents/` or the
  folders themselves. Code in python, `node`, `perl`, `ruby`, `awk` or `sqlite3` that names the
  folder, a read written to a script the same call runs, and writes to `~/.claude.json`'s
  backups stay refused; a `cd` is followed for every word, and read from the text when the scanner
  cannot follow it. A write the guard cannot pin to a path (`${HOME%/}/.claude`, `$D/.claude`,
  `~/$N`, `{.,}./x`, `~-`, `$OLDPWD`, a climb from a variable's folder), a write through `/proc`
  or `/dev`, a read in a text that exports what its programs read (`TAR_OPTIONS`), a file naming
  the folder written beside a command that is not a reader (`git commit`, `crontab`, `at`), and a
  copy or unpacking into the session folder itself are refused there too. A word with a brace
  expansion (`{a,b}`, `{1..3}`, nested, up to 64 words) is now read as each word it makes, for
  the memory store, coven's config, the settings and plugins as well as the user folder, so a
  `rm -rf ~/.cl{a,}ude/skills` is refused everywhere; this only adds refusals. A `printf -vNAME`,
  a read that may evaluate a value as a name or number (`test -v`, `-eq`, `${!x}`, `%d`), a
  `uniq a -- b`, and zsh's `NULLCMD`, `READNULLCMD`, prompt and hook names keep a text from
  counting as a read of the folder, and a relative write after zsh's `cd old new` is refused
  (#PR).
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
