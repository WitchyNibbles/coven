"""The commands that run another command, by name: what prod_guard.py and
opencode_server_guard.py both read a shell line with.

One table, so that a wrapper the production guard learns is a wrapper for the server guard too.
Each hook loads this module by path (`hook_support.sibling("wrapper_commands")`). The tables hold
names only; how each guard follows a wrapper's options is its own.
"""

# Shells, and busybox's shell applets (`busybox sh -c ...` names `sh`).
SHELLS = frozenset({"sh", "bash", "zsh", "dash", "fish", "ksh", "ash", "hush", "csh", "tcsh"})

# Words that read a heredoc body, a pipe, a `<<<` string or a process substitution as shell
# commands, or start a shell without naming one: a body fed to one of them is shell text, and its
# globs are real.
SHELL_READERS = frozenset(
    {
        *SHELLS,
        *("source", ".", "eval", "parallel", "make", "su", "sudo", "doas", "script"),
        *("screen", "tmux"),
    }
)

# Commands that run their arguments as a command, so `<wrapper> ... claude` starts claude. A
# command string (`sh -c`, `su -c`, `script -c`) is read as the option's value, quotes dropped.
WRAPPERS = frozenset(
    {
        *("exec", "env", "nohup", "command", "builtin", "sudo", "doas", "setsid", "time"),
        *("timeout", "xargs", "nice", "stdbuf", "watch", "parallel", "npx", "bunx"),
        *("script", "screen", "tmux", "flock", "systemd-run", "runuser", "su", "busybox"),
        *("chrt", "ionice", "taskset", "strace", "ltrace", "unbuffer", "unshare", "setpriv"),
        *SHELLS,
        *("if", "then", "else", "elif", "do", "while", "until", "!", "eval", "coproc", "{", "}"),
    }
)

# Wrappers that take one positional argument before the command: timeout's duration, flock's lock
# file, chrt's priority, taskset's mask, su's and runuser's user, script's log file.
POSITIONAL_WRAPPERS = frozenset({"timeout", "flock", "chrt", "taskset", "su", "runuser", "script"})

# Wrappers whose command may follow any of their own words (`tmux new-session -d -s pm 'claude'`).
ANYWHERE_WRAPPERS = frozenset({"tmux", "screen"})

# Wrappers that run a command string given after an option holding `c` (`sh -c`, `su -lc`).
COMMAND_STRING_RUNNERS = frozenset({*SHELLS, "su", "runuser", "script", "flock"})

# Commands that run a program named later in their arguments (`uv run ssh`, `uvx`, `pipx run`,
# `python -m`, `poetry run`, `hatch run`, `pdm run`, `conda run -n env`, `mise exec --`, `direnv
# exec <dir>`, `nix-shell --run '...'`, `nix develop -c`, `nix shell <installable> -c`): the words
# after which the program comes.
LAUNCHER_RUN_WORDS = {
    **dict.fromkeys(("uv", "poetry", "hatch", "pdm", "conda"), frozenset({"run"})),
    "mise": frozenset({"--"}),
    "direnv": frozenset({"exec"}),
    "nix": frozenset({"-c", "--command"}),
    "nix-shell": frozenset({"--run", "--command"}),
}
LAUNCHERS = frozenset({"uvx", "pipx", *LAUNCHER_RUN_WORDS})
