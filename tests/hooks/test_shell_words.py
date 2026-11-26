"""shell_words: which parts of a Bash command line are only data. `without_data` serves
deny-secrets; `code_text`, `heredoc_bodies`, `without_patterns` and `simple_commands` serve the
production guard. The scanner only reads command strings: no test runs one."""

import subprocess
from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import corpus_harness as harness
import guard_corpus as corpus
import pytest
from guard_corpus import N
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
        '"$HOME/bin/tool" a',
        "cd /tmp && ~/bin/tool && echo $HOME",
        "HOME=/tmp; ~/bin/tool",
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
        # Unquoted, `$HOME` is split on IFS: a HOME or IFS set by any name, built ones too, may
        # make its first word `eval`.
        "$HOME/bin/x",
        "a=HO; b=ME; read $a$b; $HOME/bin/x",
    ],
)
def test_simple_commands_give_up_on_any_other_program_under_the_home_folder(command):
    assert module.simple_commands(command) is None


def test_simple_commands_sort_the_redirections_a_command_has():
    commands = module.simple_commands("ls >a 2>/dev/null 2>&1 <b <<< x")
    (command,) = (c for c in commands if c.words)
    assert command.redirect_targets == ["a", "/dev/null"]
    assert command.input_files == ["b"]
    assert command.redirect_inputs == ["1", "x"]
    assert command.heredocs == []


def test_simple_commands_read_a_plain_substitution_only_when_asked():
    command = "for f in $(ls -t '*.x' | head -3); do cat \"$f\"; done"
    assert module.simple_commands(command) is None
    commands = module.simple_commands(command, substitutions=True)
    first = commands[0].words
    assert [w.raw for w in first] == ["for", "f", "in", "$(ls -t '*.x' | head -3)"]
    assert first[3].value is None


def test_simple_commands_keep_the_text_of_each_plain_substitution_for_the_caller_to_judge():
    """The scanner only finds where one ends: `;`, `&&` and a redirection are the caller's to
    read, in the text it gets back, and a double-quoted one comes through the same way."""
    (command, *_) = module.simple_commands(
        'cat f=$(ls; rm x) g="$(ls > o | head)" "a$(cd d && ls)b"', substitutions=True
    )
    words = command.words
    assert [word.substitutions for word in words] == [
        (),
        ("ls; rm x",),
        ("ls > o | head",),
        ("cd d && ls",),
    ]


def test_simple_commands_read_a_variable_name_inside_a_plain_substitution():
    (command, *_) = module.simple_commands(
        'cat $(wc -l $D/j) "$(grep -c x "$f" 2>/dev/null)"', substitutions=True
    )
    assert [word.substitutions for word in command.words] == [
        (),
        ("wc -l $D/j",),
        ('grep -c x "$f" 2>/dev/null',),
    ]


def test_simple_commands_read_a_backslash_that_is_literal_in_a_quoted_string_of_a_substitution():
    (command, *_) = module.simple_commands('n=$(grep -c "a\\|b\\.c" "$f")', substitutions=True)
    assert command.words[0].substitutions == ('grep -c "a\\|b\\.c" "$f"',)


def test_simple_commands_read_a_lone_bracket_as_a_word_only_when_asked():
    command = "[ -f x ] && ls"
    assert module.simple_commands(command) is None
    first, _ = (c for c in module.simple_commands(command, substitutions=True) if c.words)
    assert [word.value for word in first.words] == ["[", "-f", "x", "]"]


def test_simple_commands_read_the_word_that_opens_a_group_as_a_word_only_when_asked():
    command = "ls && { echo a; }"
    first, group, *_ = (c for c in module.simple_commands(command, substitutions=True) if c.words)
    assert [word.value for word in group.words] == ["{", "echo", "a"]
    (_, group, *_) = (c for c in module.simple_commands(command) if c.words)
    assert group.words[0].value is None


def test_simple_commands_give_a_plain_home_target_and_an_input_file_as_written():
    (command, *_) = module.simple_commands("cat < ~/a < $f > ~/b > $g", substitutions=True)
    assert command.input_files == ["~/a", "$f"]
    assert command.write_paths == ["~/b", None]
    (command, *_) = module.simple_commands("cat < $(ls)", substitutions=True)
    assert command.input_files == [None]


def test_bind_names_reads_each_word_of_a_name_as_its_path():
    commands = module.simple_commands('cat $F "$F/x" $G "$F$G"', substitutions=True)
    bound = module.bind_names(commands, {"F": "$HOME/d", "G": "/e"})
    assert [(w.raw, w.value) for w in bound[0].words[1:]] == [
        ("~/d", None),
        ("~/d/x", None),
        ("/e", "/e"),
        ('"$F$G"', None),
    ]
    assert [w.raw for w in commands[0].words][1] == "$F"


@pytest.mark.parametrize(
    "command",
    [
        "for f in $(ls $(rm x)); do :; done",
        "for f in $(ls `rm x`); do :; done",
        "for f in $(ls ${x}); do :; done",
        "for f in $(ls $1); do :; done",
        'for f in $(ls "$"); do :; done',
        'for f in $(ls "$(x)"); do :; done',
        'for f in $(ls "a\\"b"); do :; done',
        'for f in $(ls "a\\\\"); do :; done',
        'for f in $(ls "a\\$x"); do :; done',
        'for f in $(ls "a\\`x`"); do :; done',
        "for f in $(ls \\; rm x); do :; done",
        "for f in $(ls # x); do :; done",
        "for f in $(cat <<EOF); do :; done",
        "for f in $(ls",
        "for f in $((1+2)); do :; done",
    ],
)
def test_simple_commands_give_up_on_any_other_substitution_even_when_asked(command):
    assert module.simple_commands(command, substitutions=True) is None


def test_simple_commands_mark_each_command_a_pipe_joins_to_the_one_before():
    commands = module.simple_commands("ls a | head; find b |& head")
    assert [(c.words[0].value, c.after_pipe) for c in commands if c.words] == [
        ("ls", False),
        ("head", True),
        ("find", False),
        ("head", True),
    ]


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


@pytest.mark.parametrize(
    ("command", "bodies"),
    [
        (
            "cat <<'A' | gh pr create --body-file - && <<-\"B\" cat\n`a`\nA\n\t`b`\n\tB\nls",
            ["`a`\n", "\t`b`\n"],
        ),
        ("gh pr create --body \"$(cat <<'B'\n`b`\nB\n)\" && ls", ["`b`\n"]),
    ],
)
def test_quoted_heredoc_spans_finds_each_body_and_the_cat_substitution_form(
    command: str, bodies: list[str]
) -> None:
    spans = module.quoted_heredoc_spans(command)
    assert [command[start:end] for start, end in spans] == bodies


def test_quoted_heredoc_spans_gives_up_on_an_unquoted_heredoc():
    assert module.quoted_heredoc_spans("cat <<A\n$(x)\nA") is None


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


# --- one lex per text and lexer variant ---

# A lexer variant is the set of `_Lexer` options: (comments, substitutions).
Variant = tuple[bool, bool]
Lex = tuple[str, bool, bool]  # the text and its variant


@pytest.fixture
def lexes(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[Lex]]:
    """Every `_Lexer.tokenize` call of `module`, as (text, comments, substitutions), starting from
    an empty cache."""
    seen: list[Lex] = []
    real = module._Lexer.tokenize

    def counting(self: Any) -> Any:
        seen.append((self.text, self.comments, self.substitutions))
        return real(self)

    module._lex.cache_clear()
    monkeypatch.setattr(module._Lexer, "tokenize", counting)
    yield seen
    module._lex.cache_clear()


# Each public function once, with the variant it lexes in.
PUBLIC_CALLS: dict[str, tuple[Callable[[str], object], Variant]] = {
    "without_data": (module.without_data, (False, False)),
    "without_patterns": (module.without_patterns, (False, False)),
    "code_text": (module.code_text, (True, False)),
    "heredoc_bodies": (module.heredoc_bodies, (True, False)),
    "simple_commands": (module.simple_commands, (True, False)),
    "simple_commands_substitutions": (
        lambda text: module.simple_commands(text, substitutions=True),
        (True, True),
    ),
    "quoted_heredoc_spans": (module.quoted_heredoc_spans, (True, True)),
}


def test_every_public_function_lexes_a_text_once_per_variant(lexes: list[Lex]) -> None:
    text = "git commit -m 'a b' && ls"
    for call, _variant in PUBLIC_CALLS.values():
        call(text)
        call(text)
    assert sorted(lexes) == sorted({(text, *variant) for _call, variant in PUBLIC_CALLS.values()})
    assert len(lexes) == 3


@pytest.mark.parametrize("name", sorted(PUBLIC_CALLS))
def test_a_second_call_on_the_same_text_does_not_lex(name: str, lexes: list[Lex]) -> None:
    call, _variant = PUBLIC_CALLS[name]
    call("ls -la")
    assert len(lexes) == 1
    call("ls -la")
    assert len(lexes) == 1


def test_a_text_the_scanner_cannot_follow_is_lexed_once_too(lexes: list[Lex]) -> None:
    text = "echo `date`"
    assert module.simple_commands(text) is None
    assert module.simple_commands(text) is None
    assert module.heredoc_bodies(text) is None
    assert len(lexes) == 1


def test_a_lexer_error_that_is_not_a_give_up_is_not_cached(
    monkeypatch: pytest.MonkeyPatch, lexes: list[Lex]
) -> None:
    def broken(self: Any) -> Any:
        raise RuntimeError("boom")

    monkeypatch.setattr(module._Lexer, "tokenize", broken)
    for _ in range(2):
        with pytest.raises(RuntimeError):
            module.simple_commands("ls")
    assert module._lex.cache_info().currsize == 0


def words_of(text: str) -> list[tuple[str, str | None, int, int]]:
    (command,) = module.simple_commands(text)
    return [(word.raw, word.value, word.start, word.end) for word in command.words]


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("echo a b", "echo  a b"),  # one more blank before
        ("echo a b", "echo a  b"),  # one more blank between
        ("echo 'a b'", "echo a b"),  # quoted or not
        ('echo "a  b"', 'echo "a b"'),  # a blank inside the quotes
        ("echo 'x'", 'echo "x"'),  # the kind of quote
        ("echo x", "echo \\x"),  # a backslash
    ],
)
def test_texts_that_differ_in_quoting_or_blanks_get_their_own_entries(
    first: str, second: str, lexes: list[Lex]
) -> None:
    one, two = words_of(first), words_of(second)
    assert len(lexes) == 2
    assert one != two
    assert (words_of(first), words_of(second)) == (one, two)
    assert len(lexes) == 2


def test_a_different_command_never_reuses_an_entry_whatever_the_order(lexes: list[Lex]) -> None:
    texts = ["ls a", "ls  a", "ls 'a'", 'ls "a"', "ls a ", " ls a", "ls a;", "ls\ta"]
    first = [module.without_data(text) for text in texts]
    second = [module.without_data(text) for text in reversed(texts)][::-1]
    assert first == second == texts  # nothing to blank: each comes back as it was written
    assert [text for text, *_ in lexes] == texts
    different = [text for text in texts if text not in ("ls a ", "ls\ta")]  # same words as "ls a"
    assert len({str(module.simple_commands(text)) for text in different}) == len(different)


def test_the_corpus_gives_the_same_answer_with_and_without_the_cache(lexes: list[Lex]) -> None:
    """The cache must not change any answer: every corpus command, every public function, once with
    a warm cache and once with a cold one."""

    def answers(text: str) -> list[str]:
        return [str(call(text)) for call, _variant in PUBLIC_CALLS.values()]

    texts = [entry.command for entry in corpus.ENTRIES]
    cold = []
    for text in texts:
        module._lex.cache_clear()
        cold.append(answers(text))
    module._lex.cache_clear()
    warm = [answers(text) for text in texts]
    assert warm == cold
    assert [answers(text) for text in texts] == cold  # a second, fully cached pass


def test_the_variants_never_share_an_entry(lexes: list[Lex]) -> None:
    """The same text under each set of options gets its own parse, and the results show it: a
    comment is read only with `comments`, a `$(...)` only with `substitutions`."""
    commented = "ls # note"
    assert module.without_data(commented) == commented  # gives up on the comment
    assert module.simple_commands(commented) is not None  # reads it
    assert len(lexes) == 2
    assert module.simple_commands(commented) is not None
    assert module.without_data(commented) == commented
    assert len(lexes) == 2

    substituted = "echo $(date)"
    assert module.simple_commands(substituted) is None
    (command,) = module.simple_commands(substituted, substitutions=True)
    assert command.words[1].substitutions == ("date",)
    assert module.simple_commands(substituted) is None  # the other variant's entry is no answer
    assert len(lexes) == 4
    keys = {(text, comments, substitutions) for text, comments, substitutions in lexes}
    assert keys == {
        (commented, False, False),
        (commented, True, False),
        (substituted, True, False),
        (substituted, True, True),
    }


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("without_data", "code_text"),
        ("without_patterns", "heredoc_bodies"),
        ("without_data", "quoted_heredoc_spans"),
        ("code_text", "quoted_heredoc_spans"),
        ("simple_commands", "simple_commands_substitutions"),
    ],
)
def test_functions_with_different_options_do_not_share(
    first: str, second: str, lexes: list[Lex]
) -> None:
    PUBLIC_CALLS[first][0]("echo x")
    PUBLIC_CALLS[second][0]("echo x")
    assert len(lexes) == 2


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("without_data", "without_patterns"),
        ("code_text", "heredoc_bodies"),
        ("heredoc_bodies", "simple_commands"),
        ("simple_commands_substitutions", "quoted_heredoc_spans"),
    ],
)
def test_functions_with_the_same_options_share_one_parse(
    first: str, second: str, lexes: list[Lex]
) -> None:
    """The parse is only read by each function, never changed, so the same options may share it."""
    PUBLIC_CALLS[first][0]("echo x")
    PUBLIC_CALLS[second][0]("echo x")
    assert len(lexes) == 1


def test_a_changed_command_list_does_not_change_the_next_result() -> None:
    text = "cat > f <<'EOF'\nbody\nEOF\nls | wc"
    expected = module.simple_commands(text)
    changed = module.simple_commands(text)
    changed.clear()
    again = module.simple_commands(text)
    assert again == expected
    for command in again:
        command.words.clear()
        command.heredocs.clear()
        command.redirect_targets.append("x")
        command.write_paths.append("x")
        command.argument_writes.append("x")
        command.input_files.append("x")
        command.redirect_inputs.append("x")
        command.writes = not command.writes
    assert module.simple_commands(text) == expected


def test_a_heredoc_changed_by_a_caller_does_not_change_the_next_result() -> None:
    text = "cat <<'EOF'\nbody\nEOF\n"
    (reader, *_) = module.simple_commands(text)
    (heredoc,) = reader.heredocs
    original = heredoc.body
    heredoc.body = (0, 0)
    heredoc.delimiter = "changed"
    (again, *_) = module.simple_commands(text)
    assert again.heredocs[0].body == original
    assert again.heredocs[0].delimiter == "EOF"


def test_a_changed_heredoc_result_does_not_change_the_next_result() -> None:
    text = "cat <<'EOF'\nbody\nEOF\n"
    found = module.heredoc_bodies(text)
    expected = (found.rest, list(found.bodies), list(found.readers))
    found.bodies.append("x")
    found.readers.clear()
    again = module.heredoc_bodies(text)
    assert (again.rest, again.bodies, again.readers) == expected


def test_a_changed_span_list_does_not_change_the_next_result() -> None:
    text = "cat <<'EOF'\nbody\nEOF\n"
    spans = module.quoted_heredoc_spans(text)
    expected = list(spans)
    spans.clear()
    assert module.quoted_heredoc_spans(text) == expected


def test_commands_bound_to_names_do_not_change_the_cached_parse() -> None:
    text = "cat $D/x"
    bound = module.bind_names(module.simple_commands(text), {"D": "/etc"})
    assert bound[0].words[1].raw == "/etc/x"
    assert module.simple_commands(text)[0].words[1].raw == "$D/x"


def test_the_cache_holds_at_most_256_texts(lexes: list[Lex]) -> None:
    for number in range(300):
        module.without_data(f"echo {number}")
    assert len(lexes) == 300
    assert module._lex.cache_info().currsize == 256
    assert module._lex.cache_info().maxsize == 256
    module.without_data("echo 299")  # the newest is still held
    assert len(lexes) == 300
    module.without_data("echo 0")  # the oldest was dropped, and is lexed again
    assert len(lexes) == 301


# --- the guards: one process, two calls ---


def first_entry_with(guard: str, verdict: str) -> str:
    """The id of the first corpus entry on which `guard` gives `verdict`."""
    return next(entry.id for entry in corpus.ENTRIES if entry.verdict(guard) == verdict)


def entry_named(entry_id: str) -> "corpus.Entry":
    return next(entry for entry in corpus.ENTRIES if entry.id == entry_id)


@pytest.mark.usefixtures("patient_hook", "patient_guard")
@pytest.mark.parametrize("guard", corpus.GUARDS)
def test_two_calls_in_one_process_each_get_their_own_verdict(
    guard: str, runners: dict[str, harness.Runner], context: harness.Context
) -> None:
    """Refuse, allow, refuse, allow (and the reverse order) in one process: a parse kept from one
    call never decides the next."""
    refused = entry_named(first_entry_with(guard, corpus.R))
    allowed = entry_named(first_entry_with(guard, corpus.A))
    for entry in (refused, allowed, allowed, refused, refused, allowed, allowed, refused):
        result = runners[guard](context.fill(entry.command), context.cwd)
        assert result.verdict == entry.verdict(guard), (guard, entry.id, result.text)


@pytest.mark.usefixtures("patient_hook", "patient_guard")
@pytest.mark.parametrize("guard", corpus.GUARDS)
def test_two_commands_that_differ_only_in_quoting_get_their_own_verdict(
    guard: str, runners: dict[str, harness.Runner], context: harness.Context
) -> None:
    """Every pair of corpus commands that differ only by blanks or quote marks, run back to back
    in one process, each keeps the verdict the corpus pins."""
    squeezed: dict[str, list[corpus.Entry]] = {}
    for entry in corpus.ENTRIES:
        key = "".join(char for char in entry.command if char not in " \t'\"")
        squeezed.setdefault(key, []).append(entry)
    pairs = [group for group in squeezed.values() if len(group) > 1]
    for group in pairs:
        for entry in [*group, *reversed(group)]:
            if entry.verdict(guard) == N:
                continue
            result = runners[guard](context.fill(entry.command), context.cwd)
            assert result.verdict == entry.verdict(guard), (guard, entry.id, result.text)


PROD_RUNS = ("control-16", "control-29", "9i-heredoc-cat-claude")


@pytest.mark.usefixtures("patient_hook", "patient_guard")
@pytest.mark.parametrize("entry_id", PROD_RUNS)
def test_one_prod_guard_run_lexes_each_text_and_variant_once(
    entry_id: str,
    runners: dict[str, harness.Runner],
    context: harness.Context,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """prod_guard read one Bash call about 14 times; the calls share a parse now."""
    lexer = harness.pg._shell_words()._Lexer
    seen: list[Lex] = []
    real = lexer.tokenize

    def counting(self: Any) -> Any:
        seen.append((self.text, self.comments, self.substitutions))
        return real(self)

    harness.pg._shell_words()._lex.cache_clear()
    monkeypatch.setattr(lexer, "tokenize", counting)
    entry = entry_named(entry_id)
    runners["prod_guard"](context.fill(entry.command), context.cwd)
    assert seen
    assert len(seen) == len(set(seen)), "a text was lexed twice in one variant"


# --- brace_words: one brace expander for prod_guard and opencode_server_guard ---

brace_words = module.brace_words

# What bash 5.2 makes of each word, as a list of words: `bash -c 'for w in WORD; do ...'`, a word
# that is empty after the expansion dropped (bash drops an unquoted empty word; `brace_words`
# keeps it, see the empty members below). A word bash does not expand comes back as it is.
BASH_BRACES: dict[str, list[str]] = {
    "a{b,c}d": ["abd", "acd"],
    "{a,b}": ["a", "b"],
    "{a,b,c}": ["a", "b", "c"],
    "{a,}": ["a"],
    "{,a}": ["a"],
    "{,}": [],
    "x{,y}": ["x", "xy"],
    "x{a,b}{c,d}y": ["xacy", "xady", "xbcy", "xbdy"],
    "{a,{b,c}}": ["a", "b", "c"],
    "{a,{b,c},d}": ["a", "b", "c", "d"],
    "{{a,b},{c,d}}": ["a", "b", "c", "d"],
    "a{b,{c,{d,e}}}f": ["abf", "acf", "adf", "aef"],
    "{1..3}": ["1", "2", "3"],
    "x{1..3}": ["x1", "x2", "x3"],
    "{3..1}": ["3", "2", "1"],
    "{-2..2}": ["-2", "-1", "0", "1", "2"],
    "{1..10..2}": ["1", "3", "5", "7", "9"],
    "{10..1..3}": ["10", "7", "4", "1"],
    "{1..3..-1}": ["1", "2", "3"],
    "{01..10}": ["01", "02", "03", "04", "05", "06", "07", "08", "09", "10"],
    "{1..010}": ["001", "002", "003", "004", "005", "006", "007", "008", "009", "010"],
    "{08..10}": ["08", "09", "10"],
    "{-01..01}": ["-01", "000", "001"],
    "{a..e}": ["a", "b", "c", "d", "e"],
    "{e..a}": ["e", "d", "c", "b", "a"],
    "{a..e..2}": ["a", "c", "e"],
    "{a..z..12}": ["a", "m", "y"],
    "{m..m}": ["m"],
    "{m..m..5}": ["m"],
    "ho{m..m}e": ["home"],
    "{x,{1..2},m}": ["x", "1", "2", "m"],
    "{x,{01..02},m}": ["x", "01", "02", "m"],
    "{{1..2},m}": ["1", "2", "m"],
    "{a..c}{1..2}": ["a1", "a2", "b1", "b2", "c1", "c2"],
    "{1..3}{a,b}": ["1a", "1b", "2a", "2b", "3a", "3b"],
    "{a,b}$": ["a$", "b$"],
    "{a,b}{c": ["a{c", "b{c"],
    "{a,b}}": ["a}", "b}"],
    "{{a,b}": ["{a", "{b"],
    # no expansion: a brace that holds no list and no range, or is not closed
    "{a}": ["{a}"],
    "{}": ["{}"],
    "a{}b": ["a{}b"],
    "a{b}c": ["a{b}c"],
    "{a": ["{a"],
    "a}": ["a}"],
    "{a,b": ["{a,b"],
    "{1..3": ["{1..3"],
    "{1..a}": ["{1..a}"],
    "{a..1}": ["{a..1}"],
    "{aa..bb}": ["{aa..bb}"],
    "{1.5..3}": ["{1.5..3}"],
    "{1..3.5}": ["{1..3.5}"],
}


@pytest.mark.parametrize("word", BASH_BRACES)
def test_brace_words_make_the_words_bash_makes(word: str) -> None:
    made = brace_words(word)
    assert made is not None
    assert [each for each in made if each] == BASH_BRACES[word]


def test_the_table_of_bash_braces_is_what_bash_makes_on_this_machine() -> None:
    """The expected words above were read from bash 5.2; read them again, so the table cannot rot.
    A word goes in unquoted as a `for` list, one printed line per word."""
    for word, expected in BASH_BRACES.items():
        run = subprocess.run(
            ["bash", "-c", f'for w in {word}; do printf "%s\\n" "$w"; done'],
            capture_output=True,
            text=True,
            check=True,
        )
        assert run.stdout.splitlines() == expected, word


@pytest.mark.parametrize(
    ("word", "words"),
    [
        ("{a,}", ["a", ""]),
        ("{,a}", ["", "a"]),
        ("{,}", ["", ""]),
        ("x{,y}", ["x", "xy"]),
        ("{a,}b{,c}", ["ab", "abc", "b", "bc"]),
    ],
)
def test_brace_words_keep_an_empty_member_as_the_guards_read_it(
    word: str, words: list[str]
) -> None:
    assert brace_words(word) == words


@pytest.mark.parametrize("word", ["plain", "", "a/b/c", "~/x", "$HOME/x", "a*b?c[d]", "x=1"])
def test_a_word_with_no_brace_comes_back_as_it_is(word: str) -> None:
    assert brace_words(word) == [word]


def test_brace_words_return_a_new_list_each_time() -> None:
    first = brace_words("a{b,c}")
    assert first is not None
    first.append("changed")
    assert brace_words("a{b,c}") == ["ab", "ac"]
    plain = brace_words("plain")
    assert plain is not None
    plain.append("changed")
    assert brace_words("plain") == ["plain"]


@pytest.mark.parametrize(
    ("word", "words"),
    [
        # bash does not expand a brace in quotes or after a backslash; both guards over-expand it
        ('"{a,b}"', ['"a"', '"b"']),
        ("'{a,b}'", ["'a'", "'b'"]),
        ('x"{a,b}"y', ['x"a"y', 'x"b"y']),
        ("\\{a,b\\}", ["\\a", "\\b\\"]),
        ("\\{a,b}", ["\\a", "\\b"]),
        ('"{1..3}"', ['"1"', '"2"', '"3"']),
        # a range reads its ends as written: a backslash in one is no number (a guard that reads
        # the word with its quoting taken off, as opencode does, gets the range)
        ("\\{1..3\\}", ["\\{1..3\\}"]),
        ("\\{1..3}", ["\\1", "\\2", "\\3"]),
        ("1..3", ["1..3"]),
        # a list after `$` is a parameter's brace to bash; opencode read it as a list, and so does
        # the one expander now
        ("${a,b}", ["$a", "$b"]),
        ("x${a,b}y", ["x$ay", "x$by"]),
        ("${a,{b,c}}", ["$a", "$b", "$c"]),
        ("${a,b}${c,d}", ["$a$c", "$a$d", "$b$c", "$b$d"]),
        ("${mail-cli,x}", ["$mail-cli", "$x"]),
        ("${x,mail-cli}", ["$x", "$mail-cli"]),
        ("$${a,b}", ["$$a", "$$b"]),
        ("${a:-b,c}", ["$a:-b", "$c"]),
        # a `$` brace that is no list is a parameter, and stays one word; so does a range after it
        ("${a}", ["${a}"]),
        ("${HOME}/x", ["${HOME}/x"]),
        ("${a:-b}", ["${a:-b}"]),
        ("${1..3}", ["${1..3}"]),
        ("${a..c}", ["${a..c}"]),
        ("$a{1..2}", ["$a1", "$a2"]),
        ("${a}{1..2}", ["${a}1", "${a}2"]),
    ],
)
def test_brace_words_expand_what_the_guards_over_expand(word: str, words: list[str]) -> None:
    assert brace_words(word) == words


def test_a_brace_word_that_holds_a_parameter_is_read_around_it() -> None:
    assert brace_words("a{b,c}") == ["ab", "ac"]
    assert brace_words("{a,b}${c}") == ["a${c}", "b${c}"]


def test_brace_words_are_at_most_the_member_cap() -> None:
    assert module.BRACE_MEMBERS == 64
    assert len(brace_words("{1..64}") or []) == 64
    assert brace_words("{1..65}") is None
    assert len(brace_words("{64..1}") or []) == 64
    assert brace_words("{65..1}") is None
    assert len(brace_words("{1..127..2}") or []) == 64
    assert brace_words("{1..129..2}") is None
    assert len(brace_words("{a..~}") or []) == 30
    assert brace_words("{a..一}") is None
    assert len(brace_words("{" + ",".join("a" for _ in range(64)) + "}") or []) == 64
    assert brace_words("{" + ",".join("a" for _ in range(65)) + "}") is None
    assert len(brace_words("{a,b}" * 6) or []) == 64
    assert brace_words("{a,b}" * 7) is None
    assert brace_words("{a,b}{1..33}") is None
    assert len(brace_words("{a,b}{1..32}") or []) == 64
    assert brace_words("{a,{b,c}}" * 4) is None  # 3 members each: 81
    assert len(brace_words("{a,{b,c}}" * 3) or []) == 27


def test_a_word_of_more_than_64_braces_is_none_even_when_none_expands() -> None:
    assert brace_words("{a}" * 64) == ["{a}" * 64]
    assert brace_words("{a}" * 65) is None
    assert brace_words("{}" * 65) is None
    assert brace_words("${x}" * 64) == ["${x}" * 64]
    assert brace_words("${x}" * 65) is None
    assert brace_words('{"a":{"b":1},' * 33) is None  # minified JSON


@pytest.mark.parametrize(
    "word", ["{1..$N}", "{a..$b}", "{$a..3}", "{1..`n`}", "{1..{2,3}}", "{1..{2..3}}", "x{1..$N}y"]
)
def test_a_brace_range_that_may_hold_an_expansion_is_none(word: str) -> None:
    assert brace_words(word) is None


def test_a_blank_ends_a_brace_unless_the_text_is_one_word() -> None:
    assert brace_words("{a b,c}") == ["{a b,c}"]
    assert brace_words("x{a b,c}") == ["x{a b,c}"]
    assert brace_words("{a b,c}", one_word=True) == ["a b", "c"]
    assert brace_words("x{a\tb,c}{1..2} y", one_word=True) == [
        "xa\tb1 y",
        "xa\tb2 y",
        "xc1 y",
        "xc2 y",
    ]
    assert brace_words("plain words", one_word=True) == ["plain words"]
    assert brace_words("{a b,c}" * 7, one_word=True) is None
    assert module.closing_brace("{a b}", 0) == -1
    assert module.closing_brace("{a b}", 0, one_word=True) == 4
    assert module.closing_brace("{a}", 0) == 2
    assert module.closing_brace("{a", 0) == -1


def test_a_range_beside_a_parameter_is_still_a_range() -> None:
    assert brace_words("{1..3}$N") == ["1$N", "2$N", "3$N"]


def test_a_range_with_a_huge_end_is_none_and_not_an_error() -> None:
    assert brace_words("{1.." + "9" * 5000 + "}") is None
    assert brace_words("{" + "9" * 5000 + "..1}") is None
    assert brace_words("{1..3.." + "9" * 5000 + "}") is None


def test_a_deep_nest_is_read_within_the_cap_and_does_not_recurse_without_end() -> None:
    assert len(brace_words("{a," * 63 + "x" + "}" * 63) or []) == 64
    assert brace_words("{a," * 64 + "x" + "}" * 64) is None
    assert brace_words("{" * 200_000) is None
    assert brace_words("{a," * 100_000) is None


def test_a_long_word_with_few_braces_is_read_in_one_pass() -> None:
    word = "a" * 200_000 + "{b,c}" + "d" * 200_000
    made = brace_words(word)
    assert made is not None
    assert [len(each) for each in made] == [400_001, 400_001]
