"""The cross-guard corpus: one list of Bash commands, each run through every guard that reads Bash,
with each guard's verdict pinned (`guard_corpus.ENTRIES`).

Each guard runs in-process, through the function its hook calls, the way that guard's own tests run
it: deny_secrets and opencode_server_guard through `main()` (a hook payload on stdin), pm_permissions
in guard mode through `main(["--pre-tool-use"], ...)` as a PM session, and prod_guard through the
gate's `main()` with a scratch config and a committed store that holds a made-up target. The
fixtures are the guards' own: the opencode test's `world` (a fake profile, serve folder with a
password file, and coven root), pm's `repo` (the working folder), prod's `store`.

A verdict is `allow` (exit 0), `refuse` (exit 2) or `n/a`: pm's guard says nothing about a command
with no push, merge, send-pack or gh word in it (`_is_guarded`), so it has no verdict on it.
Anything else (another exit code, or a refusal that is a crash or a payload it could not read) fails
the run.

A verdict that moved fails with the command, the guard, and the expected and actual values. The
`flips` field of an entry names the plan task (3, 4 or 5) expected to move it; `none` means no task
may. An entry changes its expected value only for a listed flip, and never from refuse to allow.
"""

import contextlib
import io
import json
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest
import test_opencode_server_guard as opencode_tests
import test_pm_permissions as pm_tests
import test_prod_guard as prod_tests
from guard_corpus import ENTRIES, FLIPS, GUARDS, TOKENS, VERDICTS, A, Entry, R
from support import load_module

# The guards' own fixtures and modules, bound by name: pytest finds a fixture in the namespace of
# the test module, and the fixtures ask for each other by these names (`store` wants `pg`).
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
WRAPPER_TABLES = (
    ("wrapper_commands", "WRAPPERS"),
    ("wrapper_commands", "LAUNCHERS"),
    ("pm_permissions", "WRAPPERS"),
    ("pm_permissions", "EXEC_WRAPPERS"),
    ("pm_permissions", "SHELLS"),
    ("opencode_server_guard", "OPTION_SYNTAX"),
    ("opencode_server_guard", "RUNNER_NAMES"),
    ("prod_guard", "WRAPPERS"),
    ("prod_guard", "LAUNCHERS"),
)
READER_TABLES = (("prod_guard", "SHELL_READERS"), ("opencode_server_guard", "SHELL_READERS"))


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


def _result(guard: str, code: int, text: str) -> Result:
    if code not in (0, 2):
        raise AssertionError(f"{guard} exited {code}: {text!r}")
    if code == 2 and (not text.startswith(PREFIXES[guard]) or any(b in text for b in BROKEN)):
        raise AssertionError(f"{guard} refused for another reason than the command: {text!r}")
    return Result("refuse" if code == 2 else "allow", text.strip())


def _payload(command: str, cwd: Path) -> str:
    return json.dumps({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd)})


Runner = Callable[[str, Path], Result]


@pytest.fixture
def runners(
    monkeypatch: pytest.MonkeyPatch,
    world,
    store: Path,
    repo: Path,
) -> dict[str, Runner]:
    """One runner per guard: (command, cwd) -> Result, in this process, each as its hook runs it."""
    monkeypatch.setenv(ROLE, "pm")
    # pm's guard refuses a push while GIT_* variables are set (pm's own `isolated_git` fixture
    # clears them, but it is autouse and would run before `store` commits): clear them now.
    for name in [name for name in os.environ if name.startswith("GIT_")]:
        monkeypatch.delenv(name)
    # The gate imports `prod_guard` by name: hand it the module the prod tests patch.
    monkeypatch.setitem(sys.modules, "prod_guard", pg)

    def stdin_of(command: str, cwd: Path) -> None:
        monkeypatch.setattr(sys, "stdin", io.StringIO(_payload(command, cwd)))

    def call(guard: str, main: Callable[[], int], command: str, cwd: Path) -> Result:
        stdin_of(command, cwd)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main()
        assert out.getvalue() == "", f"{guard} wrote to stdout: {out.getvalue()!r}"
        return _result(guard, code, err.getvalue())

    def run_deny_secrets(command: str, cwd: Path) -> Result:
        return call("deny_secrets", deny_secrets.main, command, cwd)

    def run_pm(command: str, cwd: Path) -> Result:
        if not pm._is_guarded(command):
            return Result("n/a", "")
        stdin = io.StringIO(_payload(command, cwd))
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = pm.main(["--pre-tool-use"], stdin, {ROLE: "pm"})
        assert out.getvalue() == "", f"pm_permissions wrote to stdout: {out.getvalue()!r}"
        return _result("pm_permissions", code, err.getvalue())

    def run_prod(command: str, cwd: Path) -> Result:
        monkeypatch.setattr(sys, "argv", [str(HOOKS / "prod_guard_gate.py")])
        return call("prod_guard", gate.main, command, cwd)

    def run_opencode(command: str, cwd: Path) -> Result:
        arguments = [
            "--profile",
            str(world.profile),
            "--serve",
            str(world.serve),
            "--coven-root",
            str(world.coven),
        ]
        return call("opencode_server_guard", lambda: opencode_guard.main(arguments), command, cwd)

    return {
        "deny_secrets": run_deny_secrets,
        "pm_permissions": run_pm,
        "prod_guard": run_prod,
        "opencode_server_guard": run_opencode,
    }


@pytest.fixture
def context(world, repo: Path, store: Path) -> Context:
    """The folders the entries name: `<project>` (the working folder, a git repo under tmp),
    `<home>` (the scratch HOME), `<claude>` (its user folder), `<store>` (the committed store with
    the made-up guard config), `<profile>`, `<serve>` (holds the password file) and `<coven>`."""
    home = Path(world.profile.parent)
    marks = {
        "<project>": str(repo),
        "<home>": str(home),
        "<claude>": str(home / ".claude"),
        "<store>": str(store),
        "<profile>": str(world.profile),
        "<serve>": str(world.serve),
        "<coven>": str(world.coven),
        "<tmp>": str(world.root),
    }
    return Context(repo, marks)


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


@pytest.mark.usefixtures("patient_hook", "patient_guard")
@pytest.mark.parametrize("guard", GUARDS)
def test_every_entry_keeps_its_verdict(
    guard: str, runners: dict[str, Runner], context: Context
) -> None:
    problems = [
        problem for entry in ENTRIES for problem in verdicts_of(entry, runners, context, (guard,))
    ]
    assert not problems, f"{len(problems)} verdict(s) moved:\n" + "\n".join(problems)


def test_the_corpus_is_well_formed() -> None:
    ids = [entry.id for entry in ENTRIES]
    assert len(ids) == len(set(ids)), "an entry id is used twice"
    assert len(ENTRIES) >= 120
    for entry in ENTRIES:
        assert len(entry.verdicts) == len(GUARDS), entry.id
        assert all(verdict in VERDICTS for verdict in entry.verdicts), entry.id
        assert entry.flips in FLIPS, entry.id


def test_every_behaviour_difference_of_the_map_has_entries() -> None:
    """Section 9 a to m of the guard lexer map, the brace forms and the plain controls."""
    ids = {entry.id for entry in ENTRIES}
    for letter in "abcdefghijklm":
        assert any(entry_id.startswith(f"9{letter}-") for entry_id in ids), letter
    for form in ("b-", "h-range", "h-step", "h-nine-groups", "h-dollar", "h-quoted"):
        assert any(entry_id.startswith(form) for entry_id in ids), form
    controls = [entry for entry in ENTRIES if entry.id.startswith("control-")]
    assert len(controls) >= 20
    assert all(R not in entry.verdicts for entry in controls)


def names_in(tables: tuple[tuple[str, str], ...]) -> set[str]:
    return {name for module, table in tables for name in getattr(TABLES[module], table, ())}


@pytest.mark.parametrize("name", sorted(names_in(WRAPPER_TABLES)))
def test_every_wrapper_name_has_entries_with_a_guarded_word_after_it(name: str) -> None:
    """A table that gains a name later must get entries here first (push for pm, an opencode
    command for the OpenCode guard, a claude start for prod)."""
    ids = {entry.id for entry in ENTRIES}
    token = TOKENS.get(name, name)
    for word in ("push", "opencode", "claude"):
        assert f"wrap-{token}-{word}" in ids, f"{name}: no entry wrap-{token}-{word}"


@pytest.mark.parametrize("name", sorted(names_in(READER_TABLES)))
def test_every_shell_reader_name_has_entries_with_a_piped_body(name: str) -> None:
    ids = {entry.id for entry in ENTRIES}
    token = TOKENS.get(name, name)
    for word in ("push", "opencode", "claude"):
        assert f"readers-{token}-{word}" in ids, f"{name}: no entry readers-{token}-{word}"


def test_a_moved_verdict_prints_the_command_the_guard_and_both_values(
    runners: dict[str, Runner], context: Context
) -> None:
    wrong = Entry("probe", "ls", (R, A, A, A))
    (problem,) = verdicts_of(wrong, runners, context, ("deny_secrets",))
    assert "probe: 'ls'" in problem
    assert "guard deny_secrets: expected refuse, got allow" in problem
