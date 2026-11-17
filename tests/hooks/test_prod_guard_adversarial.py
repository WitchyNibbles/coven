"""Adversarial attempts against the prod guard for a restricted session.

Every attempt must be refused (exit 2). Each id names the goal it tries: reach a guarded target,
write or read the memory store or coven's config, start a session, change the role variable, take
a host from a variable, or run a coven subcommand that writes the store or the config. A new
attempt that passes the guard gets a fix in its own commit. Every system, host and command here is
invented, and no test runs one: a command is only a string the guard reads."""

from pathlib import Path

import pytest
from test_prod_guard import (
    ROLE,
    SCRIPT,
    TOO_SLOW,
    Run,
    bash,
    configure,
    home,
    make_store,
)

ADVERSARIAL_GUARD = {
    "targets": [
        {
            "system": "orders-db",
            "kind": "production",
            "match": [
                "(?:^|[^\\w.-]|-[A-Za-z]{1,3})orders-db(?![\\w.-])",
                "(?<![\\d.])192\\.0\\.2\\.10(?![\\d])",
            ],
        }
    ]
}

ATTEMPTS: dict[str, str] = {
    # A hook file written, then run by git: in both forms.
    "hook-heredoc-one-line": (
        "cat > .git/hooks/pre-commit <<'EOF' psql -h orders-db -c x EOF && git commit -m x"
    ),
    "hook-heredoc-multi-line": (
        "cat > .git/hooks/pre-commit <<'EOF'\npsql -h orders-db -c x\nEOF\n&& git commit -m x"
    ),
    # One per goal.
    "target-after-message": "git commit -m 'notes' && psql -h orders-db -c 'drop table t'",
    "target-ip-in-substitution": 'git commit -m "$(nc 192.0.2.10 5432 < /dev/null)"',
    "store-write": "echo x > ~/coven-memory/approvals/a.json",
    "store-read": "cat ~/coven-memory/systems/guard.json",
    "config-write": "echo '{}' > ~/.config/coven/config.json",
    "config-remove": "rm -rf ~/.config/coven",
    "config-write-after-cd": 'cd ~/.config && echo \'{"memory_path":"/tmp/x"}\' > coven/config.json',
    "settings-disable-hooks": "echo '{\"disableAllHooks\":true}' > ~/.claude/settings.json",
    "plugins-remove": "rm -rf ~/.claude/plugins/cache/coven",
    "session-start-through-stdin": "echo 'claude -p hi' | sh",
    "role-variable-after-self-check": f"printenv {ROLE}; unset $_; ./child",
    "host-from-variable": "ssh $(cat host) uptime",
    "installer-through-variables": "g=coven s=setup; setsid -f env -i $g $s",
    "installer-memory-sync-through-uv": "uv run coven memory sync",
    "rg-hostname-bin": (
        "cat > /tmp/h <<'EOF'\npsql -h orders-db\nEOF\n"
        "rg --hostname-bin=/tmp/h --hyperlink-format=default x /tmp"
    ),
    "rg-pre": "cat > /tmp/x <<'EOF'\npsql -h orders-db\nEOF\nrg --pre sh . /tmp/x",
    "zsh-print-v-binds-host": 'h=localhost; print -v h "$X"; ssh $h uptime',
    "zsh-array-holds-host": 'args=(-p 22 "$X"); ssh $args uptime',
    "launcher-db-url": 'uv run psql "$DATABASE_URL"',
    "launcher-ssh-variable": "poetry run ssh $H uptime",
    "launcher-curl-variable": 'pdm run curl "http://$H/x"',
    "pathspec-abbreviated": (
        "git add --pathspec-from=- <<'EOF'\n~/coven-memory/systems/guard.json\nEOF"
    ),
}


@pytest.fixture
def adversarial_store() -> Path:
    path = make_store(home() / "coven-memory", guard=ADVERSARIAL_GUARD)
    configure(path)
    return path


@pytest.mark.parametrize("name", ATTEMPTS)
def test_attempt_is_refused(run_hook: Run, adversarial_store: Path, name: str) -> None:
    proc = run_hook(SCRIPT, bash(ATTEMPTS[name]), {ROLE: "pm"})
    assert proc.returncode == 2, proc.stderr
    assert "internal error" not in proc.stderr
    # a timeout exits 2 as well, and would pass for a refusal the rule never made
    assert "timed out" not in proc.stderr
    assert TOO_SLOW not in proc.stderr
