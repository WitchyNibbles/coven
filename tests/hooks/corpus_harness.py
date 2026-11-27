"""What the cross-guard corpus runs on, apart from its tests: the guards' own fixtures and modules,
the way each guard's refusal is read, and the types of a run. `conftest.py` builds the `runners`
and `context` fixtures from it, so `test_guard_corpus.py` and `test_shell_words.py` (which runs the
corpus under a cache) read it from here and neither imports the other.

The fixtures are the guards' own: the opencode test's `world` (a fake profile, serve folder with a
password file, and coven root), pm's `repo` (the working folder), prod's `store`. pytest finds a
fixture in the namespace of the conftest, and the fixtures ask for each other by these names
(`store` wants `pg`), so `conftest.py` binds them by name.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import test_opencode_server_guard as opencode_tests
import test_pm_permissions as pm_tests
import test_prod_guard as prod_tests
from guard_corpus import Entry
from support import load_module

world = opencode_tests.world
repo = pm_tests.repo
patient_hook = pm_tests.patient_hook
store = prod_tests.store
patient_guard = prod_tests.patient_guard
opencode_guard = opencode_tests.guard
pm = pm_tests.pm
pg = prod_tests.pg
PROD_PREFIX = prod_tests.PREFIX

HOOKS = Path(__file__).resolve().parents[2] / "hooks"
ROLE = "COVEN_SESSION_ROLE"
KEPT_GIT = "GIT_CONFIG_NOSYSTEM"
PM_PREFIX = "coven pm-permissions:"
SECRETS_PREFIX = "coven deny-secrets:"
OPENCODE_PREFIX = "coven opencode-server-guard:"
PREFIXES = {
    "deny_secrets": SECRETS_PREFIX,
    "pm_permissions": PM_PREFIX,
    "prod_guard": PROD_PREFIX,
    "opencode_server_guard": OPENCODE_PREFIX,
}
# A hook that refuses because it broke, not because of what the command says.
BROKEN = ("internal error", "could not parse hook input", "hook files are damaged")

deny_secrets = load_module(HOOKS / "deny_secrets.py", "coven_deny_secrets_corpus")
gate = load_module(HOOKS / "prod_guard_gate.py", "coven_prod_guard_gate_corpus")
# The tables each guard reads wrapper and shell reader names from, loaded apart from the fixtures.
TABLES = {
    "wrapper_commands": load_module(HOOKS / "wrapper_commands.py", "coven_wrappers_corpus"),
    "pm_permissions": pm,
    "prod_guard": pg,
    "opencode_server_guard": opencode_guard,
}


@dataclass(frozen=True)
class Result:
    verdict: str
    text: str


@dataclass(frozen=True)
class Context:
    """Where an entry runs: the working folder, and the folders its markers name."""

    cwd: Path
    marks: dict[str, str]

    def fill(self, text: str) -> str:
        for mark, value in self.marks.items():
            text = text.replace(mark, value)
        return text


Runner = Callable[[str, Path], Result]


def result_of(guard: str, code: int, text: str) -> Result:
    if code not in (0, 2):
        raise AssertionError(f"{guard} exited {code}: {text!r}")
    if code == 2 and (not text.startswith(PREFIXES[guard]) or any(b in text for b in BROKEN)):
        raise AssertionError(f"{guard} refused for another reason than the command: {text!r}")
    return Result("refuse" if code == 2 else "allow", text.strip())


def payload_of(command: str, cwd: Path) -> str:
    return json.dumps({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd)})


def verdicts_of(
    entry: Entry, runners: dict[str, Runner], context: Context, guards: tuple[str, ...]
) -> list[str]:
    """The mismatches of one entry on `guards`: one line each, naming command, guard, expected
    and actual."""
    command = context.fill(entry.command)
    problems = []
    for guard in guards:
        expected = entry.verdict(guard)
        actual = runners[guard](command, context.cwd)
        if actual.verdict != expected:
            problems.append(
                f"{entry.id}: {entry.command!r}\n    guard {guard}: expected {expected},"
                f" got {actual.verdict}  ({actual.text[:160]!r})"
            )
    return problems
