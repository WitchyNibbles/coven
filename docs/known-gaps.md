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

The production guard (`hooks/prod_guard.py`, entered through `hooks/prod_guard_gate.py`) is
best effort against a PM that follows its role. Its rules are public. Its boundary is the committed
store and the approvals in it.

- The hook is registered with a PreToolUse `*` matcher that covers every tool, MCP tools and a
  subagent's calls included. That was checked from the docs, not live: no session refused a call
  through it in the build. Check one refused call and one approved call after you turn the guard
  on (`coven doctor` shows the refusals).
- Known limits. The guard reads a command's text; it does not run it, so it cannot see:
  - a path built by a loop, a variable assignment, a pipe into `xargs`, a `$(...)` or backticks,
    or interpreter code (`python3 -c`, `perl -e`) with a literal path to a folder that holds a
    guarded zone (`~`, `~/.config`, `~/.claude`, the store's parents); for Claude Code's user
    folder, a written word with such an expansion fails closed when it may lead there (see below);
  - a script written in one call and run in a later one;
  - plugins loaded with `--plugin-dir`;
  - `git clean` or `git mv` in `~`, when `~` is a git repo;
  - glob forms such as `.cla*` inside a project;
  - variables other than HOME, CLAUDE_CONFIG_DIR, CLAUDE_PLUGIN_ROOT and XDG_CONFIG_HOME;
  - what a script in `~/.claude/plugins/` does once a PM runs it: running one is allowed, like
    reading one, and only the command line is read;
  - a list of `~/.claude/plugins/` written to a file (`find ... > /tmp/l`) and acted on later
    (`xargs rm < /tmp/l`), in the same line or the next call: the same gap as a script written in
    one call and run in a later one;
  - the same gaps for the rest of Claude Code's user folder (`~/.claude`, CLAUDE_CONFIG_DIR and
    `~/.claude.json`), which a PM may read but not write in Bash: a script written in one call and
    run in a later one, a path built in code (`python3 -c "os.remove(h + '/.claude.json')"`), an
    `rg` whose `--pre` program comes from a config file (`RIPGREP_CONFIG_PATH`) or a variable, and
    what a script inside the folder does once a PM runs it;
  - for the user folder, a write to a word with a plain variable at its start and nothing after
    it that names or climbs toward the folder: `D=$(echo ~/.cla)ude; rm -rf $D/skills` deletes the
    skills folder and is allowed. The guard cannot tell where `$D` points (set by `$(...)`, `read`
    or an earlier call), and such a word is common;
  - on a case-insensitive file system (macOS by default), a path that spells a zone in other
    letters (`rm -rf ~/.CLAUDE/skills`, `~/.Claude/settings.json`, `~/.CLAUDE.JSON`, the Write
    tool too): every zone compares names as written, as it did before;
  - a job folder set by a builtin the guard does not read as an assignment: zsh's
    `zstyle -s :x y CLAUDE_JOB_DIR` and `zformat -f CLAUDE_JOB_DIR ...`, bash's
    `compgen -V CLAUDE_JOB_DIR`. The guard still writes out the hook's CLAUDE_JOB_DIR there;
  - a name a read may not set that only the user's shell startup files export, not the hook's
    environment: a lone `X=...` before a read of the user folder is then handed to its programs;
  - a file that names the user folder, written in one call and run by a later one (a git hook,
    a crontab, an npm script): the same gap as a script written in one call and run in a later
    one;
  - a file run with no mention of the user folder in the text (`sh /tmp/s`, zsh's
    `READNULLCMD=sh; < /tmp/s`), when an earlier call wrote that file: the same gap as a script
    written in one call and run in a later one; a zle widget (`zle -N x`) runs only in an
    interactive shell;
  - a brace expansion with more than 64 words, or a range the guard cannot work out, that makes
    a path of the memory store, coven's config, the settings or plugins: those zones read the word
    as it is written there;
  - a write through `/proc` or `/dev/fd` to a repo's own `.claude/settings.json`
    (`cd ~/proj/.claude && echo {} > /proc/self/cwd/settings.json`): the `/proc` rule covers only
    Claude Code's user folder.
- If a hook script is deleted and the hook runs under bash as sh, it exits 127, which does not
  block the call.
- False positives, which fail closed (the call is refused, and the PM asks the GM):
  `cd ~/.config/git && ls co*`, a Bash `cat` of a repo's `.claude/settings.json` (use the Read
  tool), a symlink in `~/projects` that points at `~`, a read of `~/.claude/plugins/` by a command
  outside the read list (`less`, `awk`, `xargs cat`, `jq`), inside a loop or group, or on a line
  with a `$(...)`, a program by an unquoted `$HOME/...` path (write `"$HOME/..."` or `~/...`), a sed
  operand by a variable that is not a plain folder assigned once at the start of the line (`CM=~/x;
  sed -n 1p $CM/a`) or on a line with a builtin that sets a variable by name (`read`, `printf`,
  `mapfile`...), a heredoc body read by python or node at a path outside `/usr/bin` and `/bin`
  (`~/.pyenv/.../python`: a session may write a shell there under that name), a sed script that some
  sed may read apart (a delimiter inside `[...]`, which GNU sed reads as part of the regex:
  `s/[/]/x/`), and a quoted glob on a line with any command other than plain data commands (`ls`,
  `grep`, `cat`, a sed whose script runs nothing, `find` without actions...) or with an unquoted `$`
  (`ls '.*' $d`, `ls '.*'; git status`).
- More false positives for the user folder, kept on purpose since they fail closed:
  - an MCP tool's repo-relative path that names `.claude` (github's `path=".claude/CLAUDE.md"`,
    serena's `relative_path=".claude/agents"`): the guard cannot tell the server's root, which
    may be home. Use the Read, Grep or Edit tools with the repo's absolute path, or `gh api`.
    Prose and web URLs (`https://...`) in an MCP string are not read this way;
  - a quoted `"$CLAUDE_JOB_DIR/x"` in a text that sets or changes IFS (`IFS=,; read ...`): the
    text gets no job folder at all, though a quoted word does not split;
  - a write of a brace word past 64 words, or with a range the guard cannot work out, that may
    name the folder, even when none of its words does (`rm -rf ~/.cl{a,b}x{1..100}`);
  - a count through a bare `xargs` (`n=$(ls ~/.claude | wc -l | xargs)`): a bare `xargs` after a
    read of the folder is refused on its own.
- The user folder (`~/.claude`, CLAUDE_CONFIG_DIR, `~/.claude.json`) is a zone with no `.claude`
  token, so a PM may write in a repo's own `.claude/worktrees/`, where Claude Code keeps its
  worktrees. A PM may read it in Bash with the readers (`cat`, `ls`, `grep`, `rg`, `find` without
  actions, `jq`, `sed -n`, `sort`, `uniq`, `cut`, `tr`, `column`, `wc`, `head`, `tail`, `[`...),
  behind `env LC_ALL=C`, `timeout`, `nice` and `command`, in pipes between them, with output to
  `/dev/null`, `/dev/stdout`, `/dev/stderr`, `/dev/tty` or to a file outside the folder, with input
  from a file, in `$(...)` that only reads, in a loop or an `if` that only reads, with `cp` out of
  it, and piped into an `xargs` that runs a plain reader (`cat`, `grep`, `wc`, `head`,
  `basename`...) with options it knows. It refuses, and a PM asks the GM: any write into it
  (`~/.claude.json`'s backups too), a program in it run under `env VAR=`, `git -C ~/.claude log`, an
  unknown program after a `cd` into it (`cd ~/.claude && othertool store cat x`), any other `xargs`
  (`-I`, `-i`, a shell, `env`, `sed`, `sort`, `find`), a read written to a file that the same text
  runs (`echo 'rm -rf ~/.claude/x' > /tmp/s; sh /tmp/s`, and with it a python script written by `cat
  > x.py <<EOF` that names the folder and run in the same call), a loop or `read` name that a shell
  ties to its search path (`PATH`, zsh's `path`, `fpath`...), a sed script held in a variable (`sed
  -E "$S"`), `rg` with an operand that is a variable (`rg x $f` in a loop), a path built from a loop
  variable (`f=~/.claude/skills/$s/SKILL.md`), `${...}`, `$((...))`, `<(...)` and `$'...'` in the
  text (the scanner gives up), a copy with an option that links or names a folder (`cp -l`, `cp -t`,
  `tar --remove-files`), a `cp` into it, and any MCP call that names a path in it, a read included.
  It also refuses a write the guard cannot pin to a path when the word names `.claude`
  (`$D/.claude/x`, `.cl${X}aude`, `"$(dirname ~/x)/.claude"`), puts an expansion right under home or
  `/` (`~/$N/x`, `/$X`), reads the current, last or a stacked folder it cannot pin (`$OLDPWD`, `~-`,
  `~1`) or a zone variable with an operator (`${HOME%/}`, `${CLAUDE_CONFIG_DIR:-x}`), or climbs from
  a folder an expansion names (`$X/..`, `{.,}./x`); a `cd` to such a word before a write counts the
  same. A word with a brace expansion is read as each word the shell makes of it (`{a,b}`, `{1..3}`,
  `{a..c}`, nested), for every zone, up to 64 words; past that, or for a range the guard cannot work
  out (`{a..$X}`), a write is refused for the user folder only, and the other zones read the word as
  it is written. A read stops being one when a command may read a value as a variable name or a
  number and the guard cannot tell the value is a number (`test -v "$D"`, `[ "$n" -eq 1 ]`, `${!D}`,
  `$((D))`, a printf `%d`, zsh's `exit "$n"`): a subscript in it runs its `$(...)`. A count (`$(grep
  -c x f)`, `$(... | wc -l)`, `$(grep -c x f || echo 0)`), an integer and a variable the text sets
  only to one of these are numbers. A `cd` or `pushd` with two operands (zsh's `cd old new`) is not
  followed: a relative write after it is refused. A redirect is no operand of it (`cd x 2>/dev/null`
  has one) unless the command holds a quote or a backslash, where `'>x'` may be a name, and a write
  to `/dev/null` or another stream after a `cd` the guard cannot follow is no write. A leading
  `$PWD` or `~+` is read as the folder the session is in. Any write under `/proc` or `/dev/fd`, or
  to `/dev/stdin`, is refused (`/proc/self/cwd` is the shell's folder, `/dev/fd/1` may be a file in
  the folder); the rest of `/dev` is read as any folder (`/dev/shm` is a tmpfs, `/dev/tcp/HOST/PORT`
  a bash socket). A `$(` or a backtick quoted as text in a word (``grep -o 'a[^`]*b'``, ``rg
  "x\`y"``) runs nothing when the command reads every word as a pattern, a name or text (`grep`,
  `rg`, `cat`, `echo`, `jq`, `sed`, `find`...) or is a shell whose `-c` text only reads; given to
  `eval`, `watch`, `test`, `printf`, a shell's text that writes, `xargs` or `find -exec` it is
  refused. The variable of a `for` loop over literal words (`for f in a/b.py c/d.py`: no `~`, `..`,
  glob, quote or expansion) is read as each word in turn when the text sets it no other way (`f=`,
  `read`, `printf -v`, a second loop over it...), holds no heredoc and no `IFS`, and uses it only as
  a plain `$f` inside the loop; up to 64 ways, past that it is read as before. A read stops being
  one when the text exports a name (`export TAR_OPTIONS=--remove-files`), sets one the hook's
  environment holds or any after `set -a`, sets one by `printf -v`, or puts one in front of the
  reader (`RIPGREP_CONFIG_PATH=x rg`), sets one zsh runs (`NULLCMD`, `READNULLCMD`, a prompt,
  `precmd_functions` and the other hook arrays, `ZDOTDIR`), but the locale, time zone, terminal,
  width and colour names (`LC_ALL`, `LANG`, `TZ`, `TERM`, `COLUMNS`, `NO_COLOR`...). A text that
  writes a file with text keeps its reads only when every other command only reads: beside `git`,
  `crontab`, `at`, `npm`, `make`, `uv run`, `chmod` or any other command the file the text wrote may
  run, so the call is refused when that text names the folder (`echo see ~/.claude/a > /tmp/n; git
  status` too). A `rg` operand that is a glob stays a read only when it starts with `/` or `~`.
- The user folder's code rule: python, `node`, `perl`, `ruby`, `awk`, `sqlite3` and the like are
  not readers of it. Their code is refused when it names the folder or looks up the home folder
  (`expanduser`, `HOME`), when it comes from a heredoc, and when a read of the folder is piped
  into it; `awk` with a file of the folder as operand is refused too, since `awk '{print >
  FILENAME}'` writes it without naming it. Running a script that sits in the folder stays a read
  of that file.
- Not a read of the folder, but refused all the same, since the guard cannot tell: a quoted word
  like `.*` or `s/.*//` in a text with a `cd` whose folder the guard cannot read from the text
  (`cd "$W"`, `cd $D`), or that may lead near the folder: `cd ~/proj` one level below home, and
  `cd /c` or `cd /tmp` with a `.*` the scanner cannot follow, since bash before 5.2 matches `..`
  with `.*` and `/` holds the folder. A `.*` that a write is given (`chmod -R 000 .*`) is read
  that way too, so it is refused one folder below home or in `/tmp`.
- A session's own scratch folders: a PM may write in its own job folder (the hook's CLAUDE_JOB_DIR,
  when it sits right under the folder's `jobs/`) and its own session folder (the payload's
  transcript path without `.jsonl`, under `projects/`), but not their `subagents/`, the folders
  themselves (`rm -rf "$CLAUDE_JOB_DIR"`, `mv`, `chmod`), a copy, sync or unpacking into the session
  folder itself (`cp -r x <folder>`, `tar -C <folder> -xf`, `unzip -d`, `cp x .` in it: a named file
  like `cp a <folder>/a.txt` is fine), a link made in one, other jobs and sessions, the transcripts
  or auto memory. The guard trusts that the shell sees the hook's CLAUDE_JOB_DIR, and writes out
  only a `$CLAUDE_JOB_DIR` or `${CLAUDE_JOB_DIR}` that starts a word and that the shell expands (not
  in single quotes, after a backslash, glued to a word or with a zsh modifier like `:h`); any other
  mention of the name in what a text writes is refused (`${CLAUDE_JOB_DIR%/*}`), and a text that may
  set the name (an assignment, `export`, `read`, `eval`, a loop) or change how its value splits
  into words (`IFS=/`, `unset IFS`, zsh's `setopt shwordsplit`, `emulate sh`) gets no job folder:
  `cd ~; IFS=/; rm -rf $CLAUDE_JOB_DIR/x` would remove `.claude` in bash. A link that
  already exists is followed, so one out of a scratch folder is refused; a link the same call makes
  some other way than `ln` (`cp -a` of a link, `tar x`) is not seen. MCP tools get no scratch
  folder.
- After a `cd` the guard cannot follow (`cd "$X"`, `cd -`), a write to a relative path that may
  name the folder is refused, a repo's own `.claude/worktrees/` included (`cd "$X" && git
  worktree remove .claude/worktrees/x`): use the absolute path.
- A write through a glob that starts with `*` (`rm ~/*.bak`) counts as one that may match
  `~/.claude.json`'s backups, though bash skips dot files there.
- Also a false positive, kept on purpose: glob-like text in a heredoc body (markdown `**x**`, a
  regex `[a-z]+x`) on a line that also names `.claude` or `.config`, even when python or `cat`
  reads the body. Python can hand such a word to a shell (`os.system`), and a copied script can
  run on the same line (`./x.sh`). Write the script with the Write tool and run it in its own
  Bash call; the refusal says so.
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
- Claude Code is the main target. OpenCode 2.3.2 is partly supported through `coven opencode` (see
  the OpenCode section below and [opencode/README.md](../opencode/README.md)); Codex is not.

## OpenCode

What `coven opencode` does not do, or does not know. The install steps are in
[opencode/README.md](../opencode/README.md#install).

- Context limits: OpenCode 2.3.2 assumes 200000 tokens of context and 32000 of output for a gateway
  model with no limit set, so a model with a smaller window can overflow before OpenCode compacts.
  Give each gateway model its real window with `coven opencode setup --limit ID=CONTEXT[:OUTPUT]`
  (see opencode/README.md); setup and doctor warn about a litellm model with no `--limit`.
- Every `shell` and `webfetch` call asks. A headless `opencode run` rejects the ask, so it cannot
  use shell.
- The GM/PM hierarchy does not run on OpenCode yet; it is planned.
- The production guard and aquelarre are not on OpenCode. Only the deny-secrets guard runs: the
  plugin layer and the `.env` rules.
- The copied superpowers skills keep Claude Code's tool names in their text.
- Superpowers skills come from the v7.0.2 clone that `coven opencode install` fetches with `git`,
  or from Claude Code's superpowers plugin. With neither, a step that loads one is skipped. Only
  v7.0.2 is supported; a newer superpowers needs a coven release that moves the pin. Setup uses
  any `superpowers/v7.0.2/skills` folder in the profile: the pin checks the download, not later
  edits to the profile, and setup does not re-verify it (running git there would run the clone's
  own `.git/config`).
- Coven runs from a full copy of the repo (a git clone), not an installed wheel: `opencode/` and
  `hooks/` must sit beside `src/`, and `coven opencode` refuses to start without them. Moving the
  clone breaks `covenRoot` and the deny-secrets guard until `coven opencode setup` runs again, and
  breaks the shim's fallback until `coven setup --shim` runs again.
- Tested on Linux and WSL 2 with Node 24 only. No CI job installs the real OpenCode: the
  end-to-end run needs `unshare` and a 2.3.2 binary.
