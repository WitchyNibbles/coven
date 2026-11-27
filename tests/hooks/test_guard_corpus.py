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
`flips` field of an entry is `h` when a task of slice 1 of phase 2 (the four open holes) is
expected to move it, and `none` when no task may. An entry changes its expected value only for a
listed flip, and never from refuse to allow.

The runners, the context and the guards' fixtures are `conftest.py`'s, built from `corpus_harness.py`.
"""

import pytest
from corpus_harness import TABLES, Context, Runner, verdicts_of
from guard_corpus import ENTRIES, FLIPS, GUARDS, TOKENS, VERDICTS, A, Entry, R

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


BRACE_ONLY_FORMS = (
    "h-only-letter-range-wide",
    "h-only-step-one",
    "h-only-list-beside-numeric-range",
    "h-only-list-beside-padded-range",
    "h-only-list-beside-numeric-step",
    "h-only-list-nested-pair",
    "h-only-double-quoted-nested-range",
    "h-only-escaped-nested-range",
    "h-only-escaped-list",
    "h-only-copy-source",
    "h-only-coven-two-ranges",
    "h-cap-nine-groups-component-names",
    "h-cap-nine-groups-last-member",
    "h-cap-write-coven-hooks",
    "d-mail-first-word",
    "d-psql-first-word",
    "d-rm-claude-folder-tail",
)


@pytest.mark.parametrize("entry_id", BRACE_ONLY_FORMS)
def test_a_brace_form_that_alone_hides_a_protected_word_has_an_entry(entry_id: str) -> None:
    """Each form hides the protected path (or word) only behind braces: a regression of the
    expander moves one of them."""
    (entry,) = [entry for entry in ENTRIES if entry.id == entry_id]
    assert "{" in entry.command


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


SHELL_READERS = frozenset(
    {
        *("sh", "bash", "zsh", "dash", "fish", "ksh", "mksh", "ash", "hush", "csh", "tcsh"),
        *("source", ".", "eval", "parallel", "make", "su", "sudo", "doas", "script"),
        *("screen", "tmux"),
    }
)


def test_the_shared_table_holds_the_shell_readers_of_prod_and_mksh() -> None:
    assert TABLES["wrapper_commands"].SHELL_READERS == SHELL_READERS
    assert len(SHELL_READERS) == 22


@pytest.mark.parametrize("guard", ["prod_guard", "opencode_server_guard"])
def test_both_guards_use_the_shared_shell_readers_not_a_copy(guard: str) -> None:
    """The very object of wrapper_commands (the guard's own load of it), so a name added there
    reaches both guards."""
    module = TABLES[guard]
    shared = getattr(module, "_WRAPPER_COMMANDS", None) or module.wrapper_commands
    assert module.SHELL_READERS is shared.SHELL_READERS
