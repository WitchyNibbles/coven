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
  the same hook script catches those forms: it splits the command into parts (quotes resolved, a
  newline counted as `;`) and runs the same check on a `gh` command, a git `push`, `merge` or
  `send-pack`, a wrapper such as `bash -c`, `env`, `sudo` or `make` that names one, and any text it
  cannot read that mentions `git` or `gh`. Words in a commit message or a file name do not trigger
  it, and `make push-docs` does, on purpose. What still gets through:
  - git aliases (`git config alias.p push`, or `-c alias.p=push`, then `git p`), and any shell
    function or script that pushes;
  - a push built at run time by a variable or command substitution that the guard cannot see;
  - on Python older than 3.12 the guard fails open: the shell fallback in `fail-closed.sh` cannot
    see the session role, so only deny-secrets still runs there. Install Python 3.12 or later.

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
