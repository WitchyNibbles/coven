# Known gaps

What coven assumes about Claude Code, what is not verified, and what it does not protect against.
The [README](../README.md) is the place to start.

## Background sessions (`claude --bg`)

The hierarchy starts each PM with `claude --bg`, which Claude Code documents as a research preview.
A spike on Claude Code 2.1.323 (2026-11-13) checked what carries into a background session:

- Verified: `env` inside `--settings` reaches the session (`COVEN_SESSION_ROLE` reads `pm`).
- Verified: `--permission-mode` is honoured. A session launched in `default` mode stopped at its
  first prompt; one launched with `bypassPermissions` ran edits and commands without prompts.
- Verified: `deny` rules hold under `bypassPermissions`; the call is refused at once.
- Verified: `ask` rules still prompt under `bypassPermissions`, and a prompt that no hook answers
  makes the background session wait for a person who is not there. In the spike it sat blocked
  for hours. This is why pm-permissions prints a decision for every request in a PM session, and
  why a PM that goes quiet is most likely stuck on a prompt: look at it with `claude agents`.
- Seen on Claude Code 2.1.325 with `--plugin-dir`: every `claude --bg ... --agent
  coven:project-manager` prints `warning: no agent named 'coven:project-manager' - spawning with
  default template`, yet the coven PM role did load and ran its start checks. Not checked for an
  installed plugin. Ignore the warning unless the PM fails its start check.
- Verified in the acceptance run (Claude Code 2.1.325, plugin loaded with `--plugin-dir`): the
  plugin's `bin/` and hooks load in a background session. The PM found `coven` on its PATH, and
  pm-permissions refused a force push and a push to `main` without leaving a prompt open.
  `claude --bg --resume <id>` together with `--name`, `--agent` and `--settings` resumed the
  session with its role. Not checked for an installed plugin.
- Not verified: what `crossSessionInbound: accept` changes. If the hooks ever fail to load, every
  `ask` rule blocks the PM as above.

## Claude Code internals

- `coven pm status` reads the output of `claude agents --json --all` and the session transcripts
  under `${CLAUDE_CONFIG_DIR:-~/.claude}/projects/`. Neither is a documented format. The parser
  keeps to one module, ignores fields and records it does not know, and shows `unknown` for what it
  cannot read. It was tested against Claude Code 2.1.325 (`LAST_TESTED` in
  `src/coven/claude_version.py`), and `coven doctor` warns when your version is newer.
- `coven doctor` checks that superpowers is installed, not which version. The spec has it warn when
  superpowers is newer than the version coven was tested with; that is not built in v1, so a
  superpowers update that renames a skill coven names shows up only as that skill failing to load.
- superpowers is a hard dependency in `plugin.json`. If it is installed but disabled, Claude Code
  refuses to load coven: `Dependency "superpowers@claude-plugins-official" is disabled. Enable
  "superpowers@claude-plugins-official" or disable "coven"`. Fix it with
  `claude plugin enable superpowers@claude-plugins-official`. With `--plugin-dir`, pass
  `--settings '{"enabledPlugins":{"superpowers@claude-plugins-official":true}}'` instead.
- Plugin agents ignore `permissionMode` and `hooks` in their frontmatter. A PM's permissions
  therefore come from the line the GM launches it with, not from the agent file.
- The `coven:<agent>` names resolved in the acceptance run (an interactive session dispatched
  `coven:explorer`). The `${CLAUDE_SKILL_DIR}` paths in the skills follow the current Claude Code
  documentation but were not exercised in a live session.

## What the guards are, and are not

- The pm-permissions hook matches command text. It allows a short list of commands and denies the
  rest, but matching text is not a sandbox: a command that looks harmless can still do more than it
  says, and the hook cannot see what a script or an alias does. It guards against a PM's mistakes,
  not against a determined one. A PM runs with your user's rights, so only hand it projects and
  credentials you are happy for an automated session to use.
- The `ask` and `deny` rules on `git push`, `git merge` and `gh` match a command by its start, so
  `git -C . push --force` or `command git push` matches none of them. A PreToolUse guard in
  the same hook script catches those forms: it reads the command as bash splits it (a quoted `;`
  is a word, not a separator) and runs the same check on a `gh` command or a git `push`, `merge` or
  `send-pack`, also inside `$(...)`, backticks and `<(...)`, behind wrappers such as `env`, `nice`,
  `timeout` or `sudo`, inside a `bash -c` or `make` that names one, and in a heredoc fed to a
  shell; and on any text it cannot read that mentions `git`, `gh` or a push. Words in a commit
  message, a quoted string, a file name or a heredoc fed to `cat` do not trigger it, and
  `make push-docs` does, on purpose. Some harmless forms are guarded because the guard fails
  closed: `echo $(( $(git rev-list --count HEAD) + 1 ))` (it does not read a substitution inside
  arithmetic, so it guards one that mentions git), and a pipe from git into any shell, even one
  whose `-c` script ignores stdin (`git log --oneline | sh -c 'head -2'`). Write the count as
  `n=$(git rev-list --count HEAD); echo $((n + 1))` and drop the `sh -c`. A pipe from git into
  parallel with a git, shell or wrapper command (`git ls-files | parallel git log -1`) or into
  `make -f -` is guarded too, as parallel adds what it reads to that command's words; use
  a `for` loop over `$(git ls-files)` instead. What still gets through:
  - git aliases (`git config alias.p push`, or `-c alias.p=push`, then `git p`), and any shell
    function or script that pushes;
  - a script file written now and run later, even in the same command: `tee f <<'EOF'`, a push in
    the body, `EOF`, then `bash f`. The body is data to tee, and the guard does not read files;
  - a push built at run time from a variable (`c="git push"; $c`) that the guard cannot see, and
    commands it does not know run others (an interpreter's `os.system`, `docker exec`, or any
    wrapper not in its list);
  - zsh-only syntax, such as `=git push` (zsh expands `=git` to git's path). The guard reads the
    command as bash does;
  - on Python older than 3.12 the git-push guard fails open: the shell fallback in `fail-closed.sh`
    cannot see the session role, so only deny-secrets still runs for that guard. The production
    guard has a degraded mode there: the fallback refuses Bash and MCP tool calls while the guard
    is on, ignoring role and config validity. Install Python 3.12 or later.

  The guards are best effort against an agent that follows its role.
  GitHub branch protection on the default branch (and any release branch) is the real wall: turn
  it on, with no force pushes and pull requests required.
- The protected branches are `main`, `master`, `pre`, `staging`, `develop`, `trunk`, `production`
  and the remote's default branch as git last recorded it (`refs/remotes/<remote>/HEAD`, read with
  no network). A repo whose default branch was renamed on the server after the clone keeps the
  old record until `git remote set-head <remote> --auto` runs, and other long-lived branch names
  are not known to the hook.
- The deny-secrets hook blocks reads and writes of `.env*` files and of `~/.ssh`, including Bash
  commands that name them. It cannot see a secret the command builds up at run time.
- The memory store's pre-commit hook refuses a short list of token patterns. It does not find every
  secret, and it does not stop a PM that commits with the hook removed. A PM is told to send facts
  to the GM instead of writing them.

## Production guard

The production guard (`hooks/prod_guard.py`, entered through `hooks/prod_guard_gate.py`) is best effort against a PM that follows its role.
Its rules are public. Its boundary is the committed store and the approvals in it.

- The hook is registered with a PreToolUse `*` matcher that covers every tool, MCP tools and a
  subagent's calls included. That was checked from the docs, not live: no session refused a call
  through it in the build. Check one refused call and one approved call after you turn the guard
  on (`coven doctor` shows the refusals).
- Known limits. The guard reads a command's text; it does not run it, so it cannot see:
  - a path built by a loop, a variable assignment, a pipe into `xargs`, a `$(...)` or backticks,
    or interpreter code (`python3 -c`, `perl -e`) with a literal path to a folder that holds a
    guarded zone (`~`, `~/.config`, `~/.claude`, the store's parents);
  - a script written in one call and run in a later one;
  - plugins loaded with `--plugin-dir`;
  - `git clean` or `git mv` in `~`, when `~` is a git repo;
  - glob forms such as `.cla*` inside a project;
  - variables other than HOME, CLAUDE_CONFIG_DIR, CLAUDE_PLUGIN_ROOT and XDG_CONFIG_HOME;
  - what a script in `~/.claude/plugins/` does once a PM runs it: running one is allowed, like
    reading one, and only the command line is read.
- If a hook script is deleted and the hook runs under bash as sh, it exits 127, which does not
  block the call.
- False positives, which fail closed (the call is refused, and the PM asks the GM):
  `cd ~/.config/git && ls co*`, a Bash `cat` of a repo's `.claude/settings.json` (use the Read
  tool), a symlink in `~/projects` that points at `~`, a read of `~/.claude/plugins/` by a command
  outside the read list (`less`, `awk`, `xargs cat`), and a quoted glob on a line that also runs a
  shell or an interpreter (`ls '.*'; bash x.sh`).
- `coven guard explain` exits 1 on an unreadable store even when the call would be allowed. The
  verdict line still prints.
- An inline `coven guard explain <command>` that names a target is refused inside a session, for
  the GM and a PM alike, because the guard reads the Bash line itself. Write the command to a
  file with the Write tool and use `--file`.
- On Python older than 3.12 the production guard runs in a degraded mode (see the git-push limits
  above): it refuses every Bash and MCP tool call while the guard is on, reads no targets or
  approvals, and so only ever refuses more than the real guard.
- The hooks cache their bytecode: the first call writes `__pycache__` into the plugin's `hooks/`
  folder (`launch.py` runs a hook through the import system), so a call no longer recompiles
  3000 lines. This leans on the PM zone rules that already refuse a PM's writes under
  `~/.claude/plugins/`, and it gives nobody a new power: whoever can write the cache can write the
  hooks. Python checks a cached file against its source's mtime and size, so an edited hook is
  recompiled. An unchecked-hash `.pyc` skips that check, but writing one needs the same write
  access as editing the source. A `--plugin-dir` checkout is outside the PM zone (see the
  `--plugin-dir` limit above). A plugin folder that cannot be written (a read-only install) still
  works, at the old cost per call.
- A `python3` older than 3.4 rejects `-I` and exits 2: the call is blocked, and the old-Python
  fallback does not run (it needs the launcher's exit 86). Python 3.4 to 3.11 reach the fallback.
- The user's own terminal, outside Claude Code, is never guarded.

## coven memory adopt

- A hand-written `INDEX.md` is rebuilt; the store's history and the backup keep the old one.
- A kill mid-commit leaves a dirty tree, and a re-run refuses until you reset it
  (`git -C <store> status` shows what).
- Adopt was not tested on case-insensitive filesystems.

## aquelarre

- The mod API is early access. aquelarre follows it as Claude Code ships it, and a release can
  change it.
- The mod's types are checked through `claude plugin validate --strict`. CI does not run `tsc`:
  the engine lays the types only for a live session.
- Its tests run with `claude plugin test`. aquelarre was tested from Claude Code 2.1.325 (the CI
  pin stays there). On 2.1.224, coven's minimum, the dependency installs but the mod cannot load:
  `validate` rejects its hooks file and `plugin test` does not exist. `coven doctor` warns below
  2.1.325.
- format, lint and coverage are `n/a` for a mod: it has no Node toolchain, and `claude plugin
  test` reports no coverage. The check prints `n/a` for them on the mod, never for a Python or
  TypeScript project.
- Disabling or uninstalling coven leaves aquelarre on. `claude plugin prune` removes it.
- Whether `coven` is on the mod's `PATH` rests on the plugin's `bin/` joining it; a live session
  confirms it, and a lookup through the installed plugins file is the follow-up if it does not.

## Checks and platforms

- The install smoke job (`.github/workflows/install-smoke.yml`, run by `ci/install-smoke/run.sh`)
  builds a clean Debian container with Claude Code at the pinned version, runs
  `claude plugin validate . --strict`, adds the checkout as a marketplace, installs superpowers and
  coven, and runs the installed `coven doctor` and the toy walkthrough. It makes no model call and
  has no API key. Its first CI run (2026-11-14, Claude Code 2.1.325) installed the plugin.
  - It is not hermetic: superpowers comes from the official marketplace over the network, and
    `uv sync` downloads the toy's tools, so an outside release or an outage can fail it.
  - It does not prove that hooks, skills and agents load in a session; that needs a model session
    and stays in the manual acceptance run.
  - Plugin install could ask for a login a container does not have; the first run showed it does
    not. If install ever fails and the switch below is unset, the script
    logs `FALLBACK`, raises a warning annotation on the run, and proves only what
    `claude plugin validate` and the checkout's own `bin/` prove (loading the plugin with
    `claude --plugin-dir` needs a session, so it is not part of the job). A fallback run is green
    but says nothing about the install itself. Adding the checkout as a marketplace never falls
    back: that failing is a coven bug.
  - The switch `COVEN_SMOKE_REQUIRE_INSTALL=1` makes a failed install fatal. The workflow sets it:
    the first CI run (2026-11-14) installed the plugin in the container. Unset it to allow the
    fallback again.
  - The plugin structure test also checks the manifests, the hooks file and the agent frontmatter,
    and runs `claude plugin validate` only on a machine that has `claude`.
- Windows is untested. Linux, macOS and WSL 2 are the target platforms.
- Setup rewrites a settings file as JSON with 2-space indent, so a hand-made layout is not kept; the
  backup beside the file holds the original.
- Only Claude Code is supported.
