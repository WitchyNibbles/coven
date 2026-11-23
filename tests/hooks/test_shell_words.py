"""shell_words: which parts of a Bash command line are only data. `without_data` serves
deny-secrets; `code_text`, `heredoc_bodies`, `without_patterns` and `simple_commands` serve the
production guard. The scanner only reads command strings: no test runs one."""

from pathlib import Path
from types import ModuleType

import pytest
from support import load_module

SCRIPT = Path(__file__).resolve().parents[2] / "hooks" / "shell_words.py"


def load() -> ModuleType:
    """Import the module by path, without writing a __pycache__ next to the hook."""
    return load_module(SCRIPT, "shell_words_under_test", register=True)


module = load()
without_data = module.without_data


def code_text(command: str, lone: tuple[str, ...] = ()) -> str:
    result: str = module.code_text(command, lone)
    return result


@pytest.mark.parametrize(
    ("command", "data"),
    [
        ("git commit -m 'a b' --amend", ["'a b'"]),
        ("git -C /r commit --message=hi", ["--message=hi"]),
        ('gh pr create --title "T" --body "B"', ['"T"', '"B"']),
        ("gh pr create --body=hello", ["--body=hello"]),
        ("grep -rn -e '.x' src", ["'.x'"]),
        ("grep --regexp=pat src", ["--regexp=pat"]),
        ("grep -A 3 pat f", ["pat"]),
        ("rg -n -- pattern src", ["pattern"]),
        ("cat > f <<'EOF'\nbody\nEOF", ["body\n"]),
        ("git commit -F - <<'EOF'\nbody\nEOF", ["body\n"]),
        ("git commit --file=- <<'EOF'\nbody\nEOF", ["body\n"]),
        ("git tag -F - <<'EOF'\nbody\nEOF", ["body\n"]),
        ("gh pr create --body-file - <<'EOF'\nbody\nEOF", ["body\n"]),
        ("gh release create v1 --notes-file=- <<'EOF'\nbody\nEOF", ["body\n"]),
        ("cat <<-'EOF' > f\n\tx\n\tEOF\n", ["\tx\n"]),
        ("cat <<'A' <<'B'\none\nA\ntwo\nB\n", ["one\n", "two\n"]),
        ("cat <<'A'; sh <<'B'\nonea\nA\ntwob\nB\n", ["onea\n"]),
        ("git commit -m 'a' \\\n -m 'b'", ["'a'", "'b'"]),
        ("git commit -m x 2>&1", ["x"]),
        ("git commit 2>&1 -m x", ["x"]),
        ("( git commit -m x )", ["x"]),
        ("git commit -am x", ["x"]),
        ("git commit -sam x", ["x"]),
        ("git commit -S -m x", ["x"]),
        ("git commit -Sam x", ["x"]),
        ("git commit --amend --no-edit -m x", ["x"]),
        ("git commit -m a -m b", ["a", "b"]),
        ("gh pr create -d --title T", ["T"]),
        ("gh release create v1 --target main --notes N", ["N"]),
        ("grep pat src", ["pat"]),
        ("grep -rn -- pat src", ["pat"]),
    ],
)
def test_blanks_only_the_data(command, data):
    expected = command
    for part in data:
        expected = expected.replace(part, " " * len(part), 1)
    assert without_data(command) == expected


@pytest.mark.parametrize(
    "word",
    [
        "\"$(cat <<'X'\nbody\nX\n)\"",
        '"$(cat <<\\X\nbody\nX\n)"',
        '"$(cat <<"X"\nbody\nX\n)"',
        "\"$(  cat  <<  'X'\nbody\nX\n  )\"",
    ],
)
def test_blanks_the_cat_heredoc_form_of_a_message(word):
    command = f"gh pr create --body {word}"
    assert without_data(command) == "gh pr create --body " + " " * len(word)


@pytest.mark.parametrize(
    "word",
    [
        "\"$(cat <<'X'\nbody\nX)\"",
        '"$(cat <<X\nbody\nX\n)"',
        "\"$(tac <<'X'\nbody\nX\n)\"",
        "\"$(cat <<'X' tail\nbody\nX\n)\"",
        "$(cat <<'X'\nbody\nX\n)",
        "\"$(cat <<-'X'\n\tbody\n\tX\n)\"",
    ],
)
def test_other_command_substitutions_are_not_data(word):
    command = f"gh pr create --body {word}"
    assert without_data(command) == command


@pytest.mark.parametrize(
    "command",
    [
        "git commit -m $MSG",
        'git commit -m "$(date)"',
        "git commit -m `date`",
        "git commit -m 'unterminated",
        "git commit -m x\\",
        "git commit -F msg.txt",
        "gh pr create --body-file b.md",
        "cat <<'EOF'\nno delimiter line",
        "cat <<EOF\nunquoted $(x)\nEOF",
        "git commit -m 'a' <<EOF\nx\nEOF",
        "cat <<AB\nA\\\nB\ntail\nAB\n",
        "cat <<'EOF' | sh\nbody\nEOF",
        "cat <<'A' |& sh\nbody\nA\n",
        "cat <(x)",
        "f() { :; }",
        "alias ll='git commit -m x'",
        "FOO=1 git commit -m 'a'",
        "sudo git commit -m 'a'",
        "grep -f pats x",
        "ls",
        "git commit -- -m x",
        # a heredoc that ends early in a substitution
        'gh pr create --body "$(cat <<\'EOF\'\nhi\nEOF)"\ncat tail\nEOF\n)"',
        "cat <<'EOF'\nhi\nEOFX\nEOF\n",
        # arithmetic
        "(( cat <<'X' ))\ncat tail\nX",
        "(( y = 1<<2 )); cat <<'Q' > f\nbody\nQ",
        # a line continuation that bash reads differently from a plain blank
        "cat <<\\\n-'EOF'\nsecret\n-EOF\n",
        'git commit -m "$\\\n(cat tail)"',
        "git commit -m 'x'\\\n-m 'y'",
        "git commit -m x\\\ny",
        # a message command with a heredoc read as paths
        "git commit -m x --pathspec-from-file=- <<'EOF'\ntail\nEOF",
        "git commit -m x --pathspec-from-file - <<'EOF'\ntail\nEOF",
        "git commit -m x --pathspec-from=- <<'EOF'\ntail\nEOF",
        "git commit -m x --pathspec-fr - <<'EOF'\ntail\nEOF",
        "git commit -m x '--pathspec-from=-' <<'EOF'\ntail\nEOF",
        # a command that may be redefined, or redefines one
        "eval 'cat() { bash; }'; cat <<'EOF'\ntail\nEOF",
        "if true; then eval x; fi; cat <<'EOF'\ntail\nEOF",
        "hash -p /bin/sh grep; grep -c 'tail' f",
        "hash cat=/bin/sh; cat <<'EOF'\ntail\nEOF",
        "shopt -s expand_aliases; BASH_ALIASES[cat]=sh\ncat <<'EOF'\ntail\nEOF",
        "functions[cat]=x; cat <<'EOF'\ntail\nEOF",
        "coproc sh; cat <<'EOF' >&p\ntail\nEOF",
        "X=1 eval x; git commit -m 'a'",
        "$X git commit -m 'a'",
        "unalias cat; cat <<'EOF'\ntail\nEOF",
        "source f; git commit -m 'a'",
        "typeset -f x; git commit -m 'a'",
        # a change of where commands are looked up, or of what a shell reads at start
        "PATH=/tmp/x:$PATH; cat <<'EOF'\ntail\nEOF",
        "PATH+=:/tmp/x; cat <<'EOF'\ntail\nEOF",
        "path=(/tmp/x); cat <<'EOF'\ntail\nEOF",
        "path+=/tmp/x; cat <<'EOF'\ntail\nEOF",
        "path[1]=/tmp/x; cat <<'EOF'\ntail\nEOF",
        "FPATH=/tmp/x; cat <<'EOF'\ntail\nEOF",
        "fpath+=/tmp/x; cat <<'EOF'\ntail\nEOF",
        "BASH_ENV=/tmp/x git commit -m 'a'",
        "ENV=/tmp/x git commit -m 'a'",
        "ZDOTDIR=/tmp/x git commit -m 'a'",
        "CDPATH=/tmp/x git commit -m 'a'",
        "export PATH=/tmp/x; cat <<'EOF'\ntail\nEOF",
        "readonly PATH=/tmp/x; cat <<'EOF'\ntail\nEOF",
        "local PATH=/tmp/x; cat <<'EOF'\ntail\nEOF",
        # zsh with extendedglob: ^, # and ~ in an unquoted word
        "git commit -m a^b",
        "git commit -m a#b",
        "git commit -m a~b",
        "set -o extendedglob; git commit -m 'a'",
        # environment that changes how git, gh, grep and rg behave
        "RIPGREP_CONFIG_PATH=/tmp/c; rg 'x' src",
        "GIT_CONFIG_COUNT=1 git commit -m 'a'",
        "GH_HOST=h; gh pr create --title 'a'",
        "GREP_OPTIONS=-f; grep 'x' src",
        "UGREP_COLORS=x; grep 'x' src",
        # zsh: >! takes the next word as its target
        "git commit -m >! tail x",
        # a comment
        "git commit -m 'x' # note",
        "cat <<'EOF' # c\nbody\nEOF",
    ],
)
def test_returns_the_command_unchanged_when_nothing_is_known_data(command):
    assert without_data(command) == command


@pytest.mark.parametrize(
    "command",
    [
        "git commit -m y --trailer -m tail",
        "git commit --squash -m tail",
        "gh pr close 1 --comment -b tail",
        "git commit -m y $=X -m tail",
        "git commit -m y $~X -m tail",
        'git commit -m y "$^X" -m tail',
        "git commit -C -m tail",
        "git commit -mfoo -m tail",
        "gh release create v1 --target -n tail",
        "gh release create v1 -R -n tail",
        "git commit -m x 99999999999>f tail",
        "git commit -m x ²>f tail",
        "git commit -m x 12>f tail",
        "git commit -m x <<'EOF'\ntail\nEOF",
        "grep tail -e KEY",
        "grep tail -f pats",
        "rg tail -e KEY",
        "grep tail src -n",
        "grep tail $X",
        "git grep -n tail -e KEY",
        "git commit -m x -- tail",
        "git commit -m x >f tail",
        "git commit -m x; cat tail",
        "git commit tail -m x",
    ],
)
def test_keeps_the_word_that_the_shell_may_read_as_a_path(command):
    assert "tail" in without_data(command)


def spaced(command, *data):
    for part in data:
        command = command.replace(part, " " * len(part), 1)
    return command


@pytest.mark.parametrize(
    ("command", "data"),
    [
        ("git commit -m 'psql -h orders-db'", ["'psql -h orders-db'"]),
        ("cat >> notes.md <<'EOF'\npsql -h orders-db\nEOF", ["psql -h orders-db\n"]),
        ('echo "cp -r ~/* /tmp/x" >> progress.md', ['"cp -r ~/* /tmp/x"']),
        ("printf '%s\\n' 'ssh 192.0.2.10' >> n.md", ["'%s\\n'", "'ssh 192.0.2.10'"]),
        ("grep -rn 'psql -h orders-db' docs/", ["'psql -h orders-db'"]),
        ("ag orders-db src", ["orders-db"]),
        ("ls docs # psql -h orders-db", ["# psql -h orders-db"]),
        ("git tag -a v1 -m 'orders-db'", ["'orders-db'"]),
        ("git notes add -m 'orders-db'", ["'orders-db'"]),
        (
            'gh pr create --title the-title --body "psql -h orders-db"',
            ["the-title", '"psql -h orders-db"'],
        ),
        ("cd docs && grep -n x a.md | head -3", ["x"]),
        ("git commit -m x && git status", ["x"]),
        # a heredoc appending a note that names a claude flag is data, not code
        (
            "cat >> progress.md <<'EOF'\nrefused: claude -p in a note\nEOF",
            ["refused: claude -p in a note\n"],
        ),
        ("cat >> n.md <<'EOF' # note\npsql -h orders-db\nEOF", ["# note", "psql -h orders-db\n"]),
        # a descriptor copy is not a write, so git on the line does not keep the body
        ("cat <<'EOF' >&2\npsql -h orders-db\nEOF\ngit status", ["psql -h orders-db\n"]),
        ("ls;# psql -h orders-db", ["# psql -h orders-db"]),
        # /dev/null is not a file git could run as a hook
        ("git commit -m 'psql -h orders-db' 2>/dev/null", ["'psql -h orders-db'"]),
        # `-C <dir>` names no output, even when the dir may expand
        ("git -C ~/r commit -m 'psql -h orders-db'", ["'psql -h orders-db'"]),
    ],
)
def test_code_text_blanks_data_on_inert_lines(command, data):
    assert code_text(command) == spaced(command, *data)


@pytest.mark.parametrize(
    "command",
    [
        "git commit -m x && psql -h orders-db",
        "cat > x.sh <<'EOF'\npsql -h orders-db\nEOF\nbash x.sh",
        "FOO=1 git commit -m x",
        "git -c core.pager=sh log",
        "git push origin main",
        "rg --pre sh . f",
        "rg --pre=sh . f",
        "rg --pre-glob '*' --pre sh . f",
        "rg --hostname-bin=./h --hyperlink-format=default x f",
        "rg --hostname-bin ./h x f",
        "ag --pager sh x",
        "ag --pag=sh x",
        "sort --compress-program=sh f",
        "sort --compress-prog=sh f",
        'git commit -m "$(date)"',
        "echo `id`",
        # the twin of the headless claude note: the heredoc is fed to a shell
        "bash <<'EOF'\nclaude -p x\nEOF",
        "cat <<'EOF' | sh\nclaude -p x\nEOF",
        "git tag v1",
        "gh pr merge 1 --body x",
        "{ echo x; }",
        # a `#` right after `(` or a redirection is not read as a comment
        "ls (#i)x",
        "cat > #x",
        # printf -v may set PATH, so a later cat may be a program that runs its input
        "printf -v PATH /tmp/x; cat <<'EOF'\npsql -h orders-db\nEOF",
        "printf -vPATH /tmp/x; cat <<'EOF'\npsql -h orders-db\nEOF",
    ],
)
def test_code_text_leaves_other_lines_whole(command):
    assert code_text(command) == command


@pytest.mark.parametrize("command", ["rg -l . *", "ag x $d", "sort $f"])
def test_unknown_word_makes_rg_ag_sort_not_inert(command):
    assert code_text(command) == command


@pytest.mark.parametrize(
    "command",
    [
        *(
            "rg --pre sh . f",
            "rg --pre=sh . f",
            "rg --pre-glob '*' . f",
            "rg --hostname-bin ./h x f",
        ),
        *("ag --pager sh x", "ag --pa=sh x", "sort --compress-program=sh f", "sort --co=sh f"),
        *("rg -l . *", "ag x $d", "sort $f", "git -c core.pager=sh log", "git push origin main"),
        *("git tag v1", "gh pr merge 1", "printf -v PATH /tmp/x"),
    ],
)
def test_a_command_that_is_not_inert_keeps_the_whole_line(command):
    """The command itself may hold no data, so a comment after it shows whether it was inert."""
    line = f"{command} # orders-db"
    assert code_text(line) == line


@pytest.mark.parametrize(
    "command",
    [
        "echo 'psql -h orders-db' | tee notes.md",
        "cat <<'EOF' | tee x\npsql -h orders-db\nEOF",
    ],
)
def test_pipe_keeps_bodies_and_echo_words(command):
    """Both lines are all inert, so only the pipe rule keeps the text."""
    assert "psql -h orders-db" in code_text(command)


@pytest.mark.parametrize(
    "command",
    [
        "cat > .git/hooks/pre-commit <<'EOF'\npsql -h orders-db\nEOF\ngit commit -m x",
        "echo 'psql -h orders-db' > .husky/pre-commit && git commit -m x",
        "tee .git/hooks/pre-commit <<'EOF'\npsql -h orders-db\nEOF\ngit commit -m x",
        "cat <<'EOF' >& .git/hooks/pre-commit\npsql -h orders-db\nEOF\ngit commit -m x",
        "cat <<'EOF' <> .git/hooks/pre-commit\npsql -h orders-db\nEOF\ngit commit -m x",
        # zsh runs $NULLCMD (cat) for a command of redirections only
        "> .git/hooks/pre-commit <<'EOF'\npsql -h orders-db\nEOF\ngit commit -m x",
        "sort -o .git/hooks/pre-commit <<'EOF'\npsql -h orders-db\nEOF\ngit commit -m x",
        "sort -uo .git/hooks/pre-commit <<'EOF'\npsql -h orders-db\nEOF\ngit commit -m x",
        "sort --output=.git/hooks/pre-commit <<'EOF'\npsql -h orders-db\nEOF\ngit commit -m x",
    ],
)
def test_written_file_and_git_keep_bodies(command):
    assert "psql -h orders-db" in code_text(command)


@pytest.mark.parametrize(
    "command",
    [
        "git commit -m 'psql -h orders-db' && git log -1 --format=%B > .git/hooks/pre-commit"
        " && git commit -m x",
        "git commit -m 'psql -h orders-db' && git log -1 --format=%B --output=.git/hooks/pre-commit"
        " && git commit -m x",
        "gh pr create --body 'psql -h orders-db' && gh pr view 1 > .git/hooks/pre-commit"
        " && git commit -m x",
        "git commit -m 'psql -h orders-db' && git log -1 --format=%B --out=.git/hooks/pre-commit"
        " && git commit -m x",
        "git commit -m 'psql -h orders-db' && git log -1 --format=%B $o && git commit -m x",
    ],
)
def test_written_file_and_git_keep_messages(command):
    """A message stored by git or gh can be written back into a hook that git then runs."""
    assert "psql -h orders-db" in code_text(command)


@pytest.mark.parametrize(
    "command",
    [
        "echo 'psql -h orders-db' > .git/hooks/pre-commit",
        "tee .git/hooks/pre-commit <<'EOF'\npsql -h orders-db\nEOF",
        "cat > .git/hooks/pre-commit <<'EOF'\npsql -h orders-db\nEOF",
        "printf 'psql -h orders-db' > .husky/pre-commit",
        "cd r && echo 'psql -h orders-db' > .git/hooks/pre-commit",
        "echo 'psql -h orders-db' > .git/HOOKS/pre-commit",
        "echo 'psql -h orders-db' > \"$HOME\"/r/.git/hooks/x",
        "echo 'psql -h orders-db' > ./.husky/x",
        "echo 'psql -h orders-db' > '.git/hooks/x'",
        "echo 'psql -h orders-db' >> r/.GIT/config",
        "echo 'psql -h orders-db' > tools/hooks/x",
        # any component holding "hook": a core.hooksPath folder may have any such name
        "echo 'psql -h orders-db' > .githooks/pre-commit",
        "echo 'psql -h orders-db' > tools/git-hooks/x",
        "echo 'psql -h orders-db' > HOOKS/x",
        "echo 'psql -h orders-db' > .hooks/x",
        "echo 'psql -h orders-db' > docs/githooks.md",
        # a target that is not a plain literal word
        "echo 'psql -h orders-db' > $f",
        "echo 'psql -h orders-db' > ~/x.sh",
        "echo 'psql -h orders-db' > *.sh",
        "echo 'psql -h orders-db' >& $f",
        "tee $f <<'EOF'\npsql -h orders-db\nEOF",
        "tee -a ~/x.sh <<'EOF'\npsql -h orders-db\nEOF",
        "sort -o $f <<'EOF'\npsql -h orders-db\nEOF",
        "sort -o .git/hooks/x <<'EOF'\npsql -h orders-db\nEOF",
        "sort -o.husky/x - <<'EOF'\npsql -h orders-db\nEOF",
        "sort --output .git/hooks/x <<'EOF'\npsql -h orders-db\nEOF",
        "sort --out=.git/hooks/x <<'EOF'\npsql -h orders-db\nEOF",
        "gh pr create --body 'psql -h orders-db' && gh pr view 1 > .husky/pre-commit",
    ],
)
def test_a_write_to_a_hook_or_unknown_target_keeps_the_data(command):
    """Without git on the line: the file may run as a hook on a later commit."""
    assert "psql -h orders-db" in code_text(command)


@pytest.mark.parametrize(
    "target",
    [
        # shell startup files
        "/home/user/.zshenv",
        "/home/user/.bashrc",
        "/HOME/USER/.BASHRC",
        "./.envrc",
        "'/home/user/.bash_profile'",
        "/home/user/.profile",
        "/home/user/.zprofile",
        "/home/user/.inputrc",
        # git config
        "/home/user/.gitconfig",
        "/home/user/.config/git/config",
        # a git hook by its basename, in any folder (core.hooksPath may name any folder)
        "/tmp/h/pre-commit",
        "/tmp/h/update",
        "/tmp/h/POST-CHECKOUT",
        "x/update",
        "fsmonitor-watchman",
        # folders whose files run later
        "/home/user/.ssh/rc",
        "/home/user/.claude/settings.json",
        "/home/user/.config/fish/config.fish",
        "/etc/profile.d/x.sh",
        "//etc/x",
        "/./etc/x",
        "/etc//x",
        # Claude and MCP configuration, shell snippets, desktop autostart
        "/home/user/.claude.json",
        "./.mcp.json",
        "/home/user/.bash_aliases",
        "/home/user/.bashrc.d/x.sh",
        "/home/user/.zshrc.d/x.zsh",
        "/home/user/.config/autostart/x.desktop",
    ],
)
def test_a_write_to_a_file_run_later_keeps_the_data(target):
    """Something runs these files later without naming them: a shell, git, ssh or Claude."""
    assert "psql -h orders-db" in code_text(f"echo 'psql -h orders-db' >> {target}")
    assert "psql -h orders-db" in code_text(f"cat > {target} <<'EOF'\npsql -h orders-db\nEOF")


@pytest.mark.parametrize(
    "command",
    [
        "cd /etc; echo 'psql -h orders-db' > profile",
        "cd ~/.ssh && echo 'psql -h orders-db' > rc",
        "cd r && cat > x.md <<'EOF'\npsql -h orders-db\nEOF",
        "cd /tmp && tee notes.md <<'EOF'\npsql -h orders-db\nEOF",
        "pushd /etc; echo 'psql -h orders-db' > profile",
        "popd; echo 'psql -h orders-db' > rc",
    ],
)
def test_a_relative_write_after_a_cd_keeps_the_data(command):
    """After a cd on the line the session cwd no longer tells where a relative target is."""
    assert "psql -h orders-db" in code_text(command)


@pytest.mark.parametrize(
    ("command", "data"),
    [
        ("cd r && git commit -m 'psql -h orders-db'", ["'psql -h orders-db'"]),
        ("cd r && echo 'psql -h orders-db' > /tmp/t/notes.md", ["'psql -h orders-db'"]),
        ("echo 'psql -h orders-db' > notes.md; cd r", ["'psql -h orders-db'"]),
    ],
)
def test_a_cd_without_a_relative_write_after_it_still_blanks(command, data):
    assert code_text(command) == spaced(command, *data)


@pytest.mark.parametrize(
    "target",
    [
        ".coven-docs/notes.md",
        ".superpowers/sdd/x/progress.md",
        "docs/update.md",
        "/tmp/t/task-12-fix-1.md",
        ".gitignore",
        "docs/bashrc.md",
        "notes/etc/x.md",
        "/tmp/fish/x",
        "/tmp/etc/x",
        "docs/etc.md",
        "docs/mcp.json",
        "/tmp/autostart/x",
        # a git hook name counts only as the file's own name
        "docs/update/x.md",
        ".superpowers/sdd/update/progress.md",
        "src/update/x.py",
        "/tmp/pre-commit/notes.md",
    ],
)
def test_a_write_to_a_note_stays_data(target):
    assert "psql -h orders-db" not in code_text(f"echo 'psql -h orders-db' >> {target}")
    assert "psql -h orders-db" not in code_text(f"cat > {target} <<'EOF'\npsql -h orders-db\nEOF")


@pytest.mark.parametrize(
    ("command", "data"),
    [
        ("cat >> .coven-docs/notes.md <<'EOF'\npsql -h orders-db\nEOF", ["psql -h orders-db\n"]),
        ("echo 'x' > progress.md", ["'x'"]),
        ("git commit -m 'psql -h orders-db'", ["'psql -h orders-db'"]),
        ("echo 'psql -h orders-db' > /dev/null", ["'psql -h orders-db'"]),
        ("tee /dev/null <<'EOF'\npsql -h orders-db\nEOF", ["psql -h orders-db\n"]),
        ("echo 'psql -h orders-db' > .gitignore", ["'psql -h orders-db'"]),
        ("echo 'psql -h orders-db' > docs/husky.md", ["'psql -h orders-db'"]),
        ("sort -o sorted.txt <<'EOF'\npsql -h orders-db\nEOF", ["psql -h orders-db\n"]),
    ],
)
def test_a_write_to_a_plain_file_stays_data(command, data):
    assert code_text(command) == spaced(command, *data)


def test_body_of_a_command_of_redirections_only_stays_code():
    assert "psql -h orders-db" in code_text("<<'EOF' > n.md\npsql -h orders-db\nEOF")


def test_code_text_keeps_file_flags_redirect_targets_and_unknown_echo_words():
    assert "~/w/x.md" in code_text("gh pr create --title t --body-file ~/w/x.md")
    assert "> ~/w/a" in code_text("echo x > ~/w/a")
    assert "~/*" in code_text("echo ~/*")
    assert "-v NAME" in code_text("printf -v NAME x")
    assert "-vNAME" in code_text("printf -vNAME x")
    assert "orders-db" in code_text("echo 'a $ orders-db' >> n.md")


def test_hash_inside_a_word_is_not_a_comment():
    assert code_text("echo a#$(id)") == "echo a#$(id)"


@pytest.mark.parametrize("command", ["ag orders-db src", "git notes add -m 'orders-db'", "ls # x"])
def test_without_data_output_is_unchanged_for_the_new_cases(command):
    assert without_data(command) == command


def test_lone_command_is_blanked_next_to_inert_commands():
    lone = ("printenv", "ROLE_X")
    assert "ROLE_X" not in code_text("printenv ROLE_X && git status", lone)
    assert "ROLE_X" in code_text("printenv ROLE_X > /tmp/r", lone)
    assert "ROLE_X" in code_text("printenv ROLE_X x", lone)


@pytest.mark.parametrize(
    "command",
    [
        "printenv ROLE_X && pytest -q",
        "printenv ROLE_X; unset $_",
        "printenv ROLE_X & unset $_",
        "printenv ROLE_X; v=$_; ls",
    ],
)
def test_lone_command_next_to_a_command_that_runs_stays_code(command):
    assert code_text(command, ("printenv", "ROLE_X")) == command


@pytest.mark.parametrize(
    "command",
    [
        # bash arithmetic on `a[$(...)]` runs the command: `$_` hands it the blanked message
        "git commit --allow-empty -m 'a[$(env -u ROLE_X ./child)]'; echo $[$_]",
        "git commit --allow-empty -m 'a[$(env -u ROLE_X ./child)]'; echo $(($_))",
        "git commit -m 'a[$(./c)]'; echo $[$_]",
        # the lone self-check's words reach an inert command that assigns or takes options
        "printenv ROLE_X; echo $[$_=1]; git commit -m x",
        "printenv ROLE_X; ls $[$_=0]",
        'printenv ROLE_X; echo "-v $_"; printf $_ gm; git commit -m y',
        "printenv ROLE_X; printf $=_ x",
        "printf $u $_ gm",
        # the accepted cost: any `$` left as code keeps the whole line
        "cd $HOME/r && git commit -m 'psql -h orders-db'",
    ],
)
def test_a_word_that_may_expand_keeps_the_line_whole(command):
    assert code_text(command, ("printenv", "ROLE_X")) == command


def test_trap_makes_the_scanner_give_up():
    # a DEBUG trap can run the text of the next command ($BASH_COMMAND), its message included
    command = "trap 'eval ${BASH_COMMAND#*-m }' DEBUG; git commit -m 'orders-db'"
    assert without_data(command) == command
    assert code_text(command) == command


@pytest.mark.parametrize(
    "command",
    [
        "printenv ROLE_X | sh",
        "printenv ROLE_X |& sh",
        "ls | printenv ROLE_X",
        "printenv ROLE_X < f",
        "printenv ROLE_X 2>&1",
        "printenv ROLE_X <<'EOF'\nx\nEOF",
        "echo $(printenv ROLE_X)",
        "env -u ROLE_X ls",
        "ROLE_X=x printenv ROLE_X",
    ],
)
def test_lone_command_in_a_pipe_or_with_a_redirection_stays_code(command):
    assert "ROLE_X" in code_text(command, ("printenv", "ROLE_X"))


def test_lone_command_counts_as_inert():
    command = "printenv ROLE_X; git commit -m 'orders-db'"
    assert code_text(command, ("printenv", "ROLE_X")) == spaced(
        command, "printenv ROLE_X", "'orders-db'"
    )


def test_no_lone_command_by_default():
    assert code_text("printenv ROLE_X && git status") == "printenv ROLE_X && git status"


def test_lone_command_in_a_parenthesised_list_stays_code():
    assert "ROLE_X" in code_text("a=(printenv ROLE_X)", ("printenv", "ROLE_X"))


def test_simple_commands_gives_words_and_writes():
    commands = module.simple_commands("h=192.0.2.10; ssh $h uptime > out # note")
    assert [[w.value for w in c.words] for c in commands if c.words] == [
        ["h=192.0.2.10"],
        ["ssh", None, "uptime"],
    ]
    assert [c.writes for c in commands if c.words] == [False, True]


def test_simple_commands_keep_the_raw_text_of_each_word():
    (command,) = module.simple_commands('scp f "$H":/tmp')
    assert [w.raw for w in command.words] == ["scp", "f", '"$H":/tmp']


@pytest.mark.parametrize("command", ["ssh $(cat h)", "ssh `cat h`", "ssh ${h}", "f() { ssh x; }"])
def test_simple_commands_is_none_on_give_up(command):
    assert module.simple_commands(command) is None


@pytest.mark.parametrize(
    "command",
    [
        "~/.pyenv/versions/x/bin/python -V",
        "$HOME/bin/tool a",
        '"$HOME/bin/tool" a',
        "cd /tmp && ~/bin/tool && echo $HOME",
    ],
)
def test_simple_commands_take_a_program_by_its_path_in_the_home_folder(command):
    """A path always holds a `/`, so it is never a builtin, a function or an alias."""
    assert module.simple_commands(command) is not None


@pytest.mark.parametrize(
    "command",
    [
        "~/bin/*",
        "~",
        "~/",
        "$HOME",
        "$HOME/bin/$X",
        "~user/bin/x",
        "$HOMEX/bin/x",
        # Unquoted, `$HOME` is split on IFS: a new HOME or IFS may make its first word `eval`.
        "IFS=/; $HOME/bin/x",
        "HOME=/tmp; $HOME/bin/x",
        "read HOME; $HOME/bin/x",
    ],
)
def test_simple_commands_give_up_on_any_other_program_under_the_home_folder(command):
    assert module.simple_commands(command) is None


def test_heredoc_bodies_names_a_reader_by_its_path_in_the_home_folder():
    command = "~/.pyenv/versions/x/bin/python - <<'A'\nx\nA"
    assert module.heredoc_bodies(command).readers == ["~/.pyenv/versions/x/bin/python"]


def test_heredoc_bodies_blanks_each_body_and_returns_it_as_written():
    command = "python3 - <<'A' && cat <<-'B'\nx = \"\\w*\"\nA\n\t[a-z]*\n\tB\nls"
    found = module.heredoc_bodies(command)
    assert found.bodies == ['x = "\\w*"\n', "\t[a-z]*\n"]
    assert len(found.rest) == len(command)
    expected = ["python3", "-", "<<'A'", "&&", "cat", "<<-'B'", "A", "B", "ls"]
    assert found.rest.split() == expected


def test_heredoc_bodies_names_the_first_word_of_each_command_that_reads_one():
    command = "cd src && python3 - <<'A' && /opt/venv/bin/perl <<'B'\nx\nA\ny\nB\nls"
    assert module.heredoc_bodies(command).readers == ["python3", "/opt/venv/bin/perl"]


def test_heredoc_bodies_reads_the_quoted_remote_command_as_a_word():
    command = "ssh h 'cd /srv && python -' > /tmp/o <<'EOF'\nre\nEOF"
    found = module.heredoc_bodies(command)
    assert "cd /srv && python -" in found.rest
    assert found.bodies == ["re\n"]
    assert found.readers == ["ssh"]


def test_heredoc_bodies_allows_an_expanding_word_in_a_command_that_reads_no_body():
    command = "cd ~ && ls s* && python3 $HOME/x.py - <<'EOF'\nx\nEOF"
    found = module.heredoc_bodies(command)
    assert found.bodies == ["x\n"]
    assert found.rest.startswith("cd ~ && ls s* && python3 $HOME/x.py - <<'EOF'\n")
    assert found.readers == ["python3"]


def test_heredoc_bodies_names_no_reader_for_a_command_without_words():
    assert module.heredoc_bodies("<<'EOF'\nx\nEOF").readers == [None]


def test_heredoc_bodies_of_a_line_without_one_is_the_line_and_no_bodies():
    found = module.heredoc_bodies("cd src && ls")
    assert (found.rest, found.bodies, found.readers) == ("cd src && ls", [], [])


@pytest.mark.parametrize(
    "command",
    [
        "python3 - <<EOF\nx\nEOF",
        "cat <<'EOF' | python3 -\nx\nEOF",
        "cat <<'EOF' |& python3 -\nx\nEOF",
        "$SHELL <<'EOF'\nx\nEOF",
        "/bin/ba?h <<'EOF'\nx\nEOF",
        "python3 - <<'EOF'\nx\n",
        "python3 - <<'EOF' $(id)\nx\nEOF",
    ],
    ids=[
        "unquoted-delimiter",
        "piped",
        "piped-with-stderr",
        "reader-is-a-variable",
        "reader-is-a-glob",
        "no-closing-delimiter",
        "substitution",
    ],
)
def test_heredoc_bodies_is_none_when_the_line_is_not_plain_words(command):
    assert module.heredoc_bodies(command) is None


@pytest.mark.parametrize(
    ("command", "words"),
    [
        ('grep -n "^function \\|^const [A-Za-z]* = " a.js | head -5', "grep -n a.js | head -5"),
        ("grep -e '[A-Za-z]*' file", "grep -e file"),
        ("grep -rn pat src/", "grep -rn src/"),
        ("rg -n 'a.*b' src", "rg -n src"),
        ("git grep -n 'a.*b' -- src", "git grep -n -- src"),
        # A file operand stays, and so does a glob the shell expands on another command.
        ("cd ~ && grep -n x a.md; ls s*", "cd ~ && grep -n a.md; ls s*"),
    ],
)
def test_without_patterns_blanks_only_the_pattern_word_and_keeps_the_length(command, words):
    blanked = module.without_patterns(command)
    assert len(blanked) == len(command)
    assert blanked.split() == words.split()


@pytest.mark.parametrize(
    "command",
    [
        # A word the shell expands or that holds an unknown value is no known pattern.
        "grep s* a.txt",
        # A later word the shell expands may be an option, so `x` is not known to be the pattern.
        "grep -n x s*/systems/a.md",
        "grep $PAT a.txt",
        "grep -f pats a.txt",
        # A body is not a pattern: the glob rule reads it.
        "cat > /tmp/x <<'EOF'\nrm -rf s*-store\nEOF",
        "tee x <<'EOF'\nrm -rf s*\nEOF",
        "ls s*",
        "",
    ],
)
def test_without_patterns_keeps_a_word_that_is_not_a_known_pattern(command):
    assert module.without_patterns(command) == command


@pytest.mark.parametrize(
    "command",
    [
        'grep "$(rm -rf s*)" a.txt',
        "grep `rm -rf s*` a.txt",
        "grep x a.txt # [a-z]*",
        "grep 'unclosed",
        "f() { grep x a; }",
    ],
)
def test_without_patterns_returns_the_command_unchanged_when_the_scanner_gives_up(command):
    assert module.without_patterns(command) == command


# without_quoted_globs: a glob character inside quotes or after a backslash is literal to the
# shell; it is masked (same length) so a glob reading skips it. Bodies and comments stay as written.
M = "%"


@pytest.mark.parametrize(
    ("command", "masked"),
    [
        ("ls '.*'", f"ls '.{M}'"),
        ('ls ".*"', f'ls ".{M}"'),
        ("ls .\\*", f"ls .\\{M}"),
        ("ls '[a-z]?'", f"ls '{M}a-z]{M}'"),
        ('grep x f ".*)"', f'grep x f ".{M})"'),
        # Only the quoted part of a word is literal: the bare `*` still globs.
        ('ls ".c"*', 'ls ".c"*'),
        ("ls .*", "ls .*"),
        ('echo "${x#*/}"', f'echo "${{x#{M}/}}"'),
        # A `$` that expands nothing.
        ("echo a$ '.*'", f"echo a$ '.{M}'"),
        ("grep -c x <<< '.*'", f"grep -c x <<< '.{M}'"),
        # A single quote inside double quotes is literal, and so is a double quote inside single.
        ("echo \"it's\" '.*'", f"echo \"it's\" '.{M}'"),
        ("echo '\"' '.*'", f"echo '\"' '.{M}'"),
    ],
)
def test_without_quoted_globs_masks_what_the_shell_does_not_expand(command, masked):
    assert module.without_quoted_globs(command) == masked


@pytest.mark.parametrize(
    ("command", "masked"),
    [
        # A heredoc body is left as written, and an apostrophe in it opens no quote.
        (
            "cat <<'EOF' > n.md\nit's '.*'\nEOF\nls '.*'",
            f"cat <<'EOF' > n.md\nit's '.*'\nEOF\nls '.{M}'",
        ),
        ("cat <<-'EOF'\n\tit's\n\tEOF\nls '.*'", f"cat <<-'EOF'\n\tit's\n\tEOF\nls '.{M}'"),
        ("cat <<EOF\n'.*'\nEOF", "cat <<EOF\n'.*'\nEOF"),
        ("ls # it's\nls '.*'", f"ls # it's\nls '.{M}'"),
        # A `#` inside a word starts no comment.
        ("echo a#b '.*'", f"echo a#b '.{M}'"),
    ],
)
def test_without_quoted_globs_leaves_bodies_and_comments_as_written(command, masked):
    assert module.without_quoted_globs(command) == masked


@pytest.mark.parametrize(
    "command",
    [
        "echo `ls` '.*'",
        "echo \"$(ls '.*')\"",
        'echo "`ls .*`"',
        "echo $'.*'",
        'echo $".*"',
        "ls '.*",
        'ls ".*',
        "ls \\",
        "cat <<'EOF'\n'.*'",
        "cat <<'EOF'",
        "echo $((1<<2)) '.*'",
        # An unquoted expansion splits and globs its value: a quoted glob may come back through it.
        "echo $(ls '.*')",
        "d='.*'; ls $d",
        "for d in '.*'; do ls $d; done",
    ],
)
def test_without_quoted_globs_returns_the_command_unchanged_when_it_cannot_follow(command):
    assert module.without_quoted_globs(command) == command
