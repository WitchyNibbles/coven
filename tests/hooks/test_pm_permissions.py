import contextlib
import importlib.util
import io
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "hooks" / "pm_permissions.py"
ROLE = "COVEN_SESSION_ROLE"
PREFIX = "coven pm-permissions:"
DENIED = "a PM may not run this. Send it to the GM as a QUESTION or a PROD-STEP and carry on."
BRANCHES = ("main", "pre", "staging", "feature")
# Patient limits for the in-process tests: the shipped ones (3 s per git call, 10 s in all) would
# deny a push on a loaded machine. A test of a limit sets its own.
PATIENT_SECONDS = 30.0


def load():
    """Import the script by path, as the hooks load each other (they are not a package)."""
    spec = importlib.util.spec_from_file_location("coven_pm_permissions", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    old, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = old
    return module


pm = load()


@pytest.fixture(autouse=True)
def patient_hook(monkeypatch):
    monkeypatch.setattr(pm, "GIT_TIMEOUT", PATIENT_SECONDS)
    monkeypatch.setattr(pm, "DEADLINE", PATIENT_SECONDS)


def clean_env(**extra):
    env = {k: v for k, v in os.environ.items() if k != ROLE and not k.startswith("GIT_")}
    return {**env, **extra}


def git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
        capture_output=True,
        text=True,
        check=True,
        env=clean_env(),
    )


@pytest.fixture(autouse=True)
def isolated_git(monkeypatch):
    """No GIT_* variables (a pre-commit run sets some); the conftest already moved HOME and the
    XDG folders, so there is no user git config either."""
    for name in [k for k in os.environ if k.startswith("GIT_")]:
        monkeypatch.delenv(name)


@pytest.fixture
def repo(tmp_path):
    """A clone on branch feature (tracking origin/feature); origin has main, pre, staging."""
    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True, env=clean_env()
    )
    work = tmp_path / "work"
    subprocess.run(["git", "init", "-q", "-b", "main", str(work)], check=True, env=clean_env())
    git(work, "commit", "-q", "--allow-empty", "-m", "first")
    git(work, "remote", "add", "origin", str(origin))
    for branch in BRANCHES[1:]:
        git(work, "branch", branch)
    for branch in BRANCHES:
        git(work, "push", "-q", "-u", "origin", branch)
    git(work, "checkout", "-q", "feature")
    return work


def request(command, cwd, tool="Bash"):
    tool_input = {"command": command} if tool == "Bash" else {}
    return {"tool_name": tool, "tool_input": tool_input, "cwd": str(cwd)}


def ask(payload, role="pm", argv=()):
    """(exit code, stdout) of main() on a payload (a dict, or a raw string for bad input)."""
    stdin = io.StringIO(payload if isinstance(payload, str) else json.dumps(payload))
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = pm.main(list(argv), stdin, {ROLE: role})
    return code, out.getvalue()


def verdict(command, cwd, tool="Bash"):
    """The decision object a PM session gets for a Bash command (or another tool)."""
    code, out = ask(request(command, cwd, tool))
    assert code == 0
    decision = json.loads(out)["hookSpecificOutput"]["decision"]
    assert decision["behavior"] in ("allow", "deny")
    return decision


def denied(command, cwd, tool="Bash"):
    decision = verdict(command, cwd, tool)
    assert decision["behavior"] == "deny", command
    assert decision["message"].startswith(PREFIX)
    return decision["message"]


def test_script_is_installed_as_pm_permissions():
    assert SCRIPT.is_file()


@pytest.mark.parametrize("role", [None, "", "gm", "PM", "pm "], ids=repr)
@pytest.mark.parametrize("what", ["push-main", "edit"])
def test_other_sessions_get_the_normal_prompt(repo, role, what):
    payload = (
        request("git push origin main", repo) if what == "push-main" else request("", repo, "Edit")
    )
    stdin = io.StringIO(json.dumps(payload))
    out = io.StringIO()
    environ = {} if role is None else {ROLE: role}
    with contextlib.redirect_stdout(out):
        assert pm.main([], stdin, environ) == 0
    assert out.getvalue() == ""


APPROVED = [
    "git push -u origin feature",
    "git push -q -u origin feature",
    "git push -uq origin feature",
    "git push --set-upstream --quiet --progress --verbose origin feature",
    "git push --dry-run --porcelain origin feature",
    "git push -n origin feature",
    "git push -- origin feature",
    "git --no-pager push origin feature",
    "git push origin feature:feature",
    "git push origin HEAD",
    "git push origin @",
    "git push origin HEAD:feature",
    "git push",
    "git push origin",
    "git push origin refs/heads/feature:refs/heads/feature",
    "git push origin refs/heads/feature",
    "cd {repo} && git push -u origin feature",
    "(cd {repo} && git push origin feature)",
    "(git push origin feature)",
    "cd {repo}; git push origin feature",
    "git push origin feature && git push origin feature",
]


@pytest.mark.parametrize("command", APPROVED)
def test_a_plain_feature_push_is_approved(repo, command):
    assert verdict(fill(command, repo), repo) == {"behavior": "allow"}


def test_git_dash_c_directory_is_resolved_against_the_cwd(repo):
    elsewhere = repo.parent
    assert verdict(f"git -C {repo} push origin feature", elsewhere) == {"behavior": "allow"}
    assert verdict(f"git -C {repo.name} push origin feature", elsewhere) == {"behavior": "allow"}
    assert verdict(f"git -C {elsewhere} -C {repo.name} push origin feature", Path("/")) == {
        "behavior": "allow"
    }


def test_a_push_in_a_directory_that_is_not_a_repo_is_refused(repo, tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    denied("git push origin feature", plain)
    denied(f"git -C {plain} push origin feature", repo)
    denied(f"git -C {tmp_path / 'missing'} push origin feature", repo)


def test_a_cd_that_may_fail_leaves_the_old_directory_a_candidate(repo):
    # `cd` fails and `;` carries on: the push then runs in the cwd, which is not a repo.
    denied(f"cd {repo}; git push origin feature", repo.parent)


@pytest.mark.parametrize("branch", ["main", "pre", "staging", "master", "MAIN", "Staging"])
def test_a_push_to_a_protected_branch_is_refused(repo, branch):
    message = denied(f"git push origin {branch}", repo)
    assert f"pushing to {branch} (protected: main, master, pre, staging, " in message


@pytest.mark.parametrize(
    "command",
    [
        "git push origin feature:main",
        "git push origin HEAD:refs/heads/pre",
        "git push origin HEAD:main",
        "git push origin feature:refs/heads/Staging",
        "git push -u origin feature main",
    ],
)
def test_a_refspec_with_a_protected_destination_is_refused(repo, command):
    assert "protected" in denied(command, repo)


def test_a_bare_push_on_a_protected_branch_is_refused(repo):
    git(repo, "checkout", "-q", "main")
    assert "pushing to main" in denied("git push", repo)
    assert "pushing to main" in denied("git push origin", repo)


def test_head_resolves_to_the_current_branch(repo):
    git(repo, "checkout", "-q", "staging")
    assert "pushing to staging" in denied("git push origin HEAD", repo)
    assert "pushing to staging" in denied("git push origin @", repo)


def test_a_branch_whose_upstream_is_main_is_refused(repo):
    git(repo, "checkout", "-q", "-b", "topic", "--track", "origin/main")
    assert "pushing to main" in denied("git push", repo)
    assert "pushing to main" in denied("git push origin", repo)


def test_an_upstream_outside_refs_heads_is_refused(repo):
    git(repo, "config", "branch.feature.merge", "refs/tags/v1")
    denied("git push", repo)


FORCES = [
    "--force",
    "-f",
    "-uf",
    "-fu",
    "--force-with-lease",
    "--force-with-lease=feature",
    "--force-if-includes",
]


@pytest.mark.parametrize("option", FORCES)
def test_a_force_option_is_refused(repo, option):
    assert "a force push" in denied(f"git push {option} origin feature", repo)


@pytest.mark.parametrize("refspec", ["+feature", "+feature:feature", "+HEAD"])
def test_a_plus_refspec_is_a_force_push(repo, refspec):
    assert "a force push" in denied(f"git push origin {refspec}", repo)


@pytest.mark.parametrize(
    ("command", "named"),
    [
        ("git push --delete origin feature", "--delete"),
        ("git push --mirror origin", "--mirror"),
        ("git push --all origin", "--all"),
        ("git push --tags origin", "--tags"),
        ("git push --no-verify origin feature", "--no-verify"),
        ("git push -o x origin feature", "-o"),
        ("git push --receive-pack=x origin feature", "--receive-pack=x"),
        ("git push --repo=x feature", "--repo=x"),
        ("git push --follow-tags origin feature", "--follow-tags"),
        ("git push --prune origin feature", "--prune"),
        ("git push --set-upstream-to origin feature", "--set-upstream-to"),
    ],
)
def test_other_push_options_are_refused_by_name(repo, command, named):
    assert f"git push option {named} is not approved automatically" in denied(command, repo)


@pytest.mark.parametrize(
    "command",
    [
        "git push origin :feature",
        "git -c push.default=matching push",
        "git --git-dir=x push origin feature",
        "git -Cx push origin feature",
        "git push origin feature:",
        "git push origin a:b:c",
        "git push origin feature:HEAD",
        "git push origin feature:@",
        "git push origin feature:refs/tags/v1",
        "git push origin feature:tags/v1",
        "git push origin feature:heads/main",
        "git push origin feature:remotes/origin/x",
        "git push origin refs/tags/v1",
        "git push origin v1",
        "git push origin nobranch",
        "git push https://example.com/x.git feature",
        "git push ../origin.git feature",
        "git push . feature",
        "git push upstream feature",
        "git",
        "git -C",
    ],
)
def test_pushes_that_cannot_be_checked_are_refused(repo, command):
    git(repo, "tag", "v1")
    denied(command, repo)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("remote.origin.push", "refs/heads/feature:refs/heads/feature"),
        ("remote.origin.mirror", "true"),
        ("push.default", "matching"),
        ("push.default", "nothing"),
    ],
)
def test_configuration_that_pushes_more_than_the_command_says_is_refused(repo, key, value):
    git(repo, "config", key, value)
    denied("git push origin feature" if key.startswith("remote") else "git push", repo)


WIDENING_CONFIGURATION = [
    ("push.followTags", "true"),
    ("push.followtags", "yes"),
    ("push.recurseSubmodules", "on-demand"),
    ("push.recurseSubmodules", "check"),
    ("submodule.recurse", "true"),
    ("push.pushOption", "ci.skip"),
    ("remote.origin.receivepack", "/bin/x"),
    ("remote.origin.pushurl", "/tmp/elsewhere.git"),
    ("remote.origin.PUSHURL", "/tmp/elsewhere.git"),
    ("remote.origin.vcs", "hg"),
    ("url.https://example.com/.pushInsteadOf", "/"),
    ("url.https://example.com/.insteadOf", "/"),
]


@pytest.mark.parametrize(("key", "value"), WIDENING_CONFIGURATION)
@pytest.mark.parametrize("command", ["git push origin feature", "git push"])
def test_configuration_that_redirects_or_widens_a_push_is_refused(repo, key, value, command):
    git(repo, "config", key, value)
    denied(command, repo)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("push.followTags", "false"),
        ("push.followTags", "no"),
        ("push.recurseSubmodules", "no"),
        ("push.recurseSubmodules", "false"),
        ("submodule.recurse", "false"),
        ("submodule.recurse", "no"),
    ],
)
def test_configuration_that_switches_a_widening_feature_off_is_accepted(repo, key, value):
    git(repo, "config", key, value)
    assert verdict("git push origin feature", repo) == {"behavior": "allow"}


def test_a_bare_push_goes_to_the_branch_push_remote(repo, tmp_path):
    git(repo, "remote", "add", "backup", str(tmp_path / "origin.git"))
    git(repo, "config", "branch.feature.pushRemote", "backup")
    assert verdict("git push", repo) == {"behavior": "allow"}
    git(repo, "config", "branch.feature.pushRemote", str(tmp_path / "elsewhere.git"))
    assert "is not a configured remote" in denied("git push", repo)


def test_a_bare_push_goes_to_the_push_default_remote(repo, tmp_path):
    git(repo, "remote", "add", "backup", str(tmp_path / "origin.git"))
    git(repo, "config", "remote.pushDefault", "backup")
    assert verdict("git push", repo) == {"behavior": "allow"}
    git(repo, "config", "remote.pushDefault", str(tmp_path / "elsewhere.git"))
    assert "is not a configured remote" in denied("git push", repo)


def test_a_branch_push_remote_wins_over_the_push_default(repo, tmp_path):
    git(repo, "config", "remote.pushDefault", str(tmp_path / "elsewhere.git"))
    git(repo, "config", "branch.feature.pushRemote", "origin")
    assert verdict("git push", repo) == {"behavior": "allow"}


def test_a_bare_push_goes_to_the_branch_remote(repo, tmp_path):
    git(repo, "config", "branch.feature.remote", str(tmp_path / "elsewhere.git"))
    denied("git push", repo)
    denied("git push -u", repo)


def test_a_bare_push_with_no_configured_remote_name_is_refused(repo):
    git(repo, "remote", "remove", "origin")
    denied("git push", repo)


@pytest.mark.parametrize("value", ["simple", "current", "upstream", "tracking"])
def test_a_safe_push_default_is_accepted(repo, value):
    git(repo, "config", "push.default", value)
    assert verdict("git push", repo) == {"behavior": "allow"}


def test_a_detached_head_is_refused(repo):
    git(repo, "checkout", "-q", "--detach")
    denied("git push", repo)
    denied("git push origin HEAD", repo)


def test_a_tag_name_as_refspec_is_refused(repo):
    git(repo, "tag", "release")
    denied("git push origin release", repo)


@pytest.mark.parametrize(
    "command",
    [
        "git push origin feature && git push origin main",
        "git push origin feature; git push origin pre",
        "git push origin feature || git push origin staging",
        "cd /tmp && git push origin main",
        "git push origin main && git push origin feature",
    ],
)
def test_every_push_of_a_compound_command_is_checked(repo, command):
    assert "protected" in denied(command, repo)


@pytest.mark.parametrize(
    "command",
    [
        "git add . && git push origin feature",
        "git add . && git commit -m x && git push",
        "git push origin feature | cat",
        "git push origin feature |& cat",
        "git push origin feature &",
        "git push origin feature & git push origin main",
        "git push origin feature\ngit push origin main",
        "git push origin feature\rgit push origin pre",
        "git push origin $(echo main)",
        "git push origin `echo main`",
        "git push origin $BRANCH",
        "git push origin 'main'",
        'git push origin "main"',
        "git push origin ma\\in",
        "git push origin feature # main",
        "git push origin feature > out",
        "git push origin feature < in",
        "git push origin {main,feature}",
        "git push origin mai*",
        "git push origin mai?",
        "git push origin [m]ain",
        "GIT_DIR=x git push origin feature",
        "env git push origin feature",
        "timeout 9 git push origin feature",
        "! git push origin feature",
        "/usr/bin/git push origin feature",
        "sudo git push origin feature",
        "echo hi; git push origin feature",
        "&& git push origin feature",
        "git push origin feature &&",
        "git push origin feature ;;",
        "git push origin feature;",
        "git push origin feature ;& git push origin pre",
        "( && git push origin feature)",
        "cd",
        "cd -",
        "cd -P /tmp && git push origin feature",
        "cd a b && git push origin feature",
        "pushd /tmp && git push origin feature",
        "popd && git push origin feature",
        "if true; then git push origin feature; fi",
        "bash -c git push origin feature",
        "git push origin feature\x00",
    ],
)
def test_a_command_that_is_more_than_plain_pushes_is_refused(repo, command):
    denied(command, repo)


# Control characters other than tab, and the Unicode spaces: not word breaks for the shell or
# shlex, but \S-style regexes would take them for one.
ODD_SPACES = ["\x0b", "\x0c", "\x1c", "\x1d", "\x1e", "\x1f", "\x7f", "\x85", "\xa0"]
ODD_SPACES += ["\u1680", "\u2003", "\u2028", "\u2029", "\u202f", "\u205f", "\u3000"]
FORBIDDEN_CHARACTERS = list("$`\\\"'#{}*?[]<>") + ["\n", "\r", "\x00", *ODD_SPACES]
# Approved when {c} is empty: the only reason to refuse the others is the character itself.
FORBIDDEN_TEMPLATE = "git push origin feature:fe{c}ature2"


@pytest.mark.parametrize("char", FORBIDDEN_CHARACTERS, ids=repr)
def test_each_forbidden_character_alone_refuses_an_otherwise_approved_push(repo, char):
    assert verdict(FORBIDDEN_TEMPLATE.format(c=""), repo) == {"behavior": "allow"}
    message = denied(FORBIDDEN_TEMPLATE.format(c=char), repo)
    assert f"{char!r} is not allowed in an automatically approved command" in message


@pytest.mark.parametrize("space", ODD_SPACES, ids=repr)
@pytest.mark.parametrize(
    "template",
    [
        "git log a{s}2>&1{s}b",
        "git log a{s}2>&1{s}out",
        "git status --porcelain=v2{s}2>&1{s}out",
        "cd .{s}2>&1{s}out && git log",
        "git log{s}2>&1",
        "git push origin feature{s}2>&1",
        "git log 2>&1{s}| tail",
    ],
)
def test_2_to_1_between_odd_spaces_is_not_a_redirect_to_merge(repo, template, space):
    # The shell reads `a<VT>2>&1<VT>out` as one word or a redirect to a file, never as `2>&1`.
    denied(template.format(s=space), repo)


@pytest.mark.parametrize(
    "command",
    [
        "git log a\t2>&1\tb",
        "git log\t2>&1\t--oneline",
        "git log\t2>&1 x",
    ],
)
def test_2_to_1_must_still_be_the_last_word_when_split_by_tabs(repo, command):
    denied(command, repo)


def test_2_to_1_between_tabs_is_the_same_standalone_word_as_between_spaces(repo):
    assert verdict("git log\t2>&1", repo) == {"behavior": "allow"}
    assert verdict("git push origin feature\t2>&1\t|\ttail -n 3", repo) == {"behavior": "allow"}


@pytest.mark.parametrize(
    "command",
    [
        "git -C ~- push origin feature",
        "cd ~+ && git push origin feature",
        "git -C ~1 push origin feature",
        "git -C ~root push origin feature",
        "git -C ./~ push origin feature",
        "git -C a~b push origin feature",
        "git push origin feature:fe~ature2",
    ],
)
def test_a_tilde_other_than_home_is_refused(repo, command):
    # bash expands ~-, ~+, ~N and ~user to directories the hook cannot know; the decoy directories
    # make the unexpanded text look like a valid repository.
    for name in ("~-", "~+", "~1", "~root", "~", "a~b"):
        (repo / name).mkdir(exist_ok=True)
    message = denied(command, repo)
    assert "~" in message


def test_a_plain_tilde_is_the_home_directory(repo, monkeypatch):
    monkeypatch.setenv("HOME", str(repo))
    assert verdict("git -C ~ push origin feature", repo.parent) == {"behavior": "allow"}
    assert verdict("git -C ~/ push origin feature", repo.parent) == {"behavior": "allow"}
    (repo / "sub").mkdir()
    assert verdict("cd ~/sub && git push origin feature", repo) == {"behavior": "allow"}


def test_protected_names_are_compared_with_casefold(repo):
    assert "protected" in denied("git push origin feature:\u017ftaging", repo)


def test_cd_and_git_dash_c_directories_may_not_hide_a_second_directory(repo, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    denied(f"cd {other} && git push origin feature", repo)
    denied(f"(cd {repo} && git push origin feature); git push origin pre", repo)
    denied(f"git -C {repo} -C {other} push origin feature", repo)


def test_cd_without_an_argument_goes_home(repo):
    # HOME is set to a plain directory (the isolated_git fixture): the push is not in a repo.
    denied("cd && git push origin feature", repo)


@pytest.mark.parametrize(
    "command",
    ["git merge feature", "python manage.py migrate", "git commit --amend", "git add ."],
)
def test_a_bash_command_without_a_push_is_escalated(repo, command):
    message = denied(command, repo)
    assert "an ask rule" not in message
    assert message.startswith(f"{PREFIX} {DENIED}")


@pytest.mark.parametrize("tool", ["Edit", "Write", "mcp__x__y", "AskUserQuestion", "WebFetch"])
def test_any_other_tool_is_escalated(repo, tool):
    message = denied("", repo, tool)
    assert f"{tool} asks for permission" in message
    assert message.startswith(f"{PREFIX} {DENIED}")


def test_a_refused_push_gets_the_message_that_sends_it_to_the_gm(repo):
    for command in ("git push origin main", "git push --force origin feature", "git push --all"):
        message = denied(command, repo)
        assert message.startswith(f"{PREFIX} {DENIED}")
        assert "Why:" in message


def test_a_command_with_a_push_among_other_things_gets_the_split_message(repo):
    for command in ("git add . && git push origin feature", "git push origin 'feature'"):
        message = denied(command, repo)
        assert message.startswith(f"{PREFIX} {DENIED}")
        assert "Only plain pushes of feature branches and read-only git" in message
        assert "run the push as its own command, for example git push -u origin <branch>" in message


def test_a_branch_that_cannot_be_resolved_gets_the_split_message(repo):
    git(repo, "checkout", "-q", "--detach")
    assert "Only plain pushes of feature branches" in denied("git push", repo)


def test_a_non_push_command_that_mentions_git_gets_the_escalate_message(repo):
    message = denied("git commit --amend", repo)
    assert "Only plain pushes" not in message
    assert message.startswith(f"{PREFIX} {DENIED}")


def test_the_decision_has_the_permission_request_shape(repo):
    _, allow_out = ask(request("git push origin feature", repo))
    _, deny_out = ask(request("git push origin main", repo))
    for out, behavior in ((allow_out, "allow"), (deny_out, "deny")):
        document = json.loads(out)
        assert list(document) == ["hookSpecificOutput"]
        output = document["hookSpecificOutput"]
        assert output["hookEventName"] == "PermissionRequest"
        assert output["decision"]["behavior"] == behavior
        assert ("message" in output["decision"]) == (behavior == "deny")
    assert allow_out.count("\n") == 1
    assert deny_out.count("\n") == 1


@pytest.mark.parametrize(
    ("payload", "why"),
    [
        ("not json", "bad input"),
        ("", "bad input"),
        ("[]", "payload is not an object"),
        ('"x"', "payload is not an object"),
        ({"cwd": "/tmp"}, "tool_name"),
        ({"tool_name": 3, "tool_input": {}}, "tool_name"),
        ({"tool_name": "Bash", "tool_input": "git push", "cwd": "/tmp"}, "tool_input"),
        ({"tool_name": "Edit", "tool_input": ["x"]}, "tool_input"),
        ({"tool_name": "Bash", "tool_input": {}, "cwd": "/tmp"}, "without a command"),
        ({"tool_name": "Bash", "tool_input": {"command": 5}, "cwd": "/tmp"}, "without a command"),
        (
            {"tool_name": "Bash", "tool_input": {"command": "  "}, "cwd": "/tmp"},
            "without a command",
        ),
        ({"tool_name": "Bash", "tool_input": {"command": "git push"}}, "cwd"),
        ({"tool_name": "Bash", "tool_input": {"command": "git push"}, "cwd": 4}, "cwd"),
    ],
)
def test_a_bad_payload_in_a_pm_session_is_refused(payload, why):
    code, out = ask(payload)
    assert code == 0
    decision = json.loads(out)["hookSpecificOutput"]["decision"]
    assert decision["behavior"] == "deny"
    assert decision["message"].startswith(f"{PREFIX} could not check this request (")
    assert why in decision["message"]
    assert decision["message"].endswith("; refused. Send the step to the GM as a QUESTION.")


def test_an_internal_error_in_a_pm_session_is_refused(repo, monkeypatch):
    def boom(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(subprocess, "run", boom)
    code, out = ask(request("git push origin feature", repo))
    assert code == 0
    decision = json.loads(out)["hookSpecificOutput"]["decision"]
    assert decision["behavior"] == "deny"
    assert "could not check this request" in decision["message"]
    assert "boom" in decision["message"]


def test_a_missing_git_is_refused(repo, monkeypatch):
    def no_git(*_args, **_kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(subprocess, "run", no_git)
    denied("git push origin feature", repo)


def test_a_git_call_past_its_timeout_is_refused(repo, monkeypatch):
    def slow(args, **_kwargs):
        raise subprocess.TimeoutExpired(args, 3)

    monkeypatch.setattr(subprocess, "run", slow)
    denied("git push origin feature", repo)


@pytest.mark.parametrize(
    "name",
    [
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_NAMESPACE",
        "GIT_CONFIG_COUNT",
        "GIT_CONFIG_KEY_0",
        "GIT_CONFIG_VALUE_0",
        "GIT_CONFIG_PARAMETERS",
        "GIT_CONFIG_GLOBAL",
        "GIT_SSH_COMMAND",
        "GIT_EXEC_PATH",
    ],
)
def test_a_git_variable_in_the_hook_environment_refuses_a_push(repo, monkeypatch, name):
    # The push runs in the shell's environment, which may carry config the hook's git cannot see
    # (GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=remote.origin.push ...): any GIT_* variable that can
    # change where a push goes refuses it.
    monkeypatch.setenv(name, "x")
    message = denied("git push origin feature", repo)
    assert name in message
    assert message.startswith(f"{PREFIX} {DENIED}")


@pytest.mark.parametrize(
    "name", ["GIT_TERMINAL_PROMPT", "GIT_EDITOR", "GIT_PAGER", "GIT_SEQUENCE_EDITOR"]
)
def test_a_git_variable_that_cannot_change_where_a_push_goes_is_allowed(repo, monkeypatch, name):
    monkeypatch.setenv(name, "0")
    assert verdict("git push origin feature", repo) == {"behavior": "allow"}


def test_the_hooks_git_calls_see_the_environment_the_push_would(repo, monkeypatch):
    seen = []
    real = subprocess.run

    def spy(args, **kwargs):
        seen.append(kwargs.get("env"))
        return real(args, **kwargs)

    monkeypatch.setenv("GIT_EDITOR", "true")
    monkeypatch.setattr(subprocess, "run", spy)
    assert verdict("git push origin feature", repo) == {"behavior": "allow"}
    assert seen
    assert all(env is None or env.get("GIT_EDITOR") == "true" for env in seen)


def test_a_check_running_past_the_deadline_is_refused(repo, monkeypatch):
    def hang(*_args, **_kwargs):
        start = time.monotonic()
        while time.monotonic() - start < 10:  # a broken deadline fails the test, not the run
            pass
        raise AssertionError("the deadline did not interrupt the check")

    monkeypatch.setattr(pm, "DEADLINE", 0.2)
    monkeypatch.setattr(subprocess, "run", hang)
    before = signal.getsignal(signal.SIGALRM)
    code, out = ask(request("git push origin feature", repo))
    assert code == 0
    decision = json.loads(out)["hookSpecificOutput"]["decision"]
    assert decision["behavior"] == "deny"
    assert "took too long" in decision["message"]
    assert signal.getsignal(signal.SIGALRM) == before
    assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)


def test_a_late_alarm_cannot_print_a_second_decision(repo, monkeypatch):
    # The alarm lands right after the check finished, at the moment the timer is disarmed.
    real = signal.setitimer

    def late(which, seconds, *rest):
        real(which, seconds, *rest)
        if seconds == 0:
            raise pm.DeadlineError

    monkeypatch.setattr(signal, "setitimer", late)
    code, out = ask(request("git push origin feature", repo))
    assert code == 0
    assert out.count("\n") == 1
    assert json.loads(out)["hookSpecificOutput"]["decision"]["behavior"] == "deny"


def test_unexpected_arguments_are_refused_in_a_pm_session(repo):
    code, out = ask(request("git push origin feature", repo), argv=["x"])
    assert code == 0
    assert json.loads(out)["hookSpecificOutput"]["decision"]["behavior"] == "deny"


# --- read-only git, `2>&1` and a final tail/head pipe -------------------------------------------

# Commands that change no ref, no work tree and no remote; each one is also run for real below.
READ_ONLY_APPROVED = [
    "git merge-base --is-ancestor main feature",
    "git merge-base main feature",
    "git merge-base --all main feature",
    "git -C {repo} merge-tree --write-tree main feature",
    "git merge-tree --write-tree --name-only --messages main feature",
    "git merge-tree main main feature",
    "git log --oneline -5 origin/main..HEAD",
    "git log --oneline --graph --decorate --all -n 3",
    "git log --format=%h --author=t --since=2.weeks --max-count=3",
    "git log --stat --no-ext-diff --no-textconv -p HEAD",
    "git log --diff-filter=ACM --name-status -Sfoo -- README",
    "git diff --stat origin/main...HEAD",
    "git diff --cached --name-only",
    "git diff --no-ext-diff --no-textconv HEAD",
    "git diff -U5 -M --find-renames=50% HEAD",
    "git show --stat HEAD",
    "git show --no-patch --format=%H HEAD",
    "git status",
    "git status --short",
    "git status -sb",
    "git status --porcelain=v2 --branch -uno",
    "git rev-parse --abbrev-ref HEAD",
    "git rev-parse --show-toplevel",
    "git rev-parse --verify --quiet main",
    "git rev-list --count origin/main..HEAD",
    "git rev-list --max-count=3 --first-parent HEAD",
    "git ls-files",
    "git ls-files --others --exclude-standard",
    "git cat-file -t HEAD",
    "git cat-file -p HEAD",
    "git cat-file -e HEAD",
    "git branch",
    "git branch --list",
    "git branch -a",
    "git branch -r",
    "git branch -v",
    "git branch -vv",
    "git branch -a -vv",
    "git branch --show-current",
    "git branch --contains HEAD",
    "git branch --merged main",
    "git branch -r --contains origin/main",
    "cd {repo} && git status --short",
    "cd {repo}; git merge-base --is-ancestor main feature",
    "git --no-pager log --oneline -3",
    "git -C {repo} status",
    "git diff --stat origin/main...HEAD && git log --oneline -3",
    "git push origin feature && git status",
    "git status && git push origin feature",
]
# Pushes and read-only commands with `2>&1` and/or a final pipe into tail or head.
PLUMBING_APPROVED = [
    "git push -u origin feature 2>&1 | tail -n 20",
    "git push -u origin feature 2>&1 | tail -20",
    "git push origin feature 2>&1 | head -n 5",
    "git push origin feature 2>&1",
    "git push origin feature | tail -n 20",
    "git push 2>&1 | tail",
    "git log --oneline | head -50",
    "git log --oneline | head -n 50",
    "git log | tail",
    "git log 2>&1 | tail -n 5",
    "git status 2>&1",
    "git diff --stat 2>&1 | head -20",
    "cd {repo} && git push origin feature 2>&1 | tail -n 20",
    "cd {repo} && git log --oneline -3 | head -n 2",
    "(cd {repo} && git log --oneline | tail -n 2)",
    "git push origin feature 2>&1 && git log --oneline -1 2>&1",
    "git log | tail -n 0",
]


def fill(command, repo):
    """The command with {repo} replaced (str.format would trip over a literal brace)."""
    return command.replace("{repo}", str(repo))


@pytest.mark.parametrize("command", READ_ONLY_APPROVED)
def test_a_read_only_git_command_is_approved(repo, command):
    assert verdict(fill(command, repo), repo) == {"behavior": "allow"}


@pytest.mark.parametrize("command", PLUMBING_APPROVED)
def test_2_to_1_and_a_final_tail_or_head_pipe_are_approved(repo, command):
    assert verdict(fill(command, repo), repo) == {"behavior": "allow"}


def snapshot(repo):
    refs = git(repo, "for-each-ref").stdout  # heads, tags and remote-tracking refs
    files = sorted(
        str(path.relative_to(repo)) for path in repo.rglob("*") if ".git" not in path.parts
    )
    return refs, files, (repo / ".git" / "HEAD").read_text()


@pytest.mark.parametrize("command", READ_ONLY_APPROVED)
def test_every_approved_read_only_command_really_moves_no_ref_and_writes_no_file(repo, command):
    # The allow-lists are a claim about git: run each approved command for real.
    before = snapshot(repo)
    done = subprocess.run(
        ["/bin/sh", "-c", fill(command, repo)],
        cwd=repo,
        capture_output=True,
        text=True,
        env=clean_env(),
        check=False,
    )
    assert done.returncode in (0, 1), done.stderr
    assert snapshot(repo) == before


READ_ONLY_REFUSED = [
    # not read-only: a merge, a ref move, a work-tree change
    "git merge feature",
    "git merge --no-ff feature",
    "git commit --amend",
    "git checkout main",
    "git switch main",
    "git reset --hard",
    "git tag x",
    "git remote add x y",
    "git config core.pager x",
    "git update-ref -d refs/heads/feature",
    "git symbolic-ref HEAD refs/heads/main",
    "git worktree add x",
    "git stash",
    "git clean -fd",
    "git rebase main",
    "git pull",
    "git gc",
    "git format-patch -o x HEAD",
    "git archive -o x HEAD",
    "git bundle create x HEAD",
    "git apply p",
    "git grep foo",
    "git show-ref",
    "git for-each-ref",
    "git mergetool",
    "git difftool",
    "git me",
    # an option that writes a file
    "git diff --output=f",
    "git diff --output f",
    "git diff --out=f",
    "git log --output=f",
    "git log --output f",
    "git show --output=f HEAD",
    "git status --output=f",
    "git log -o f",
    "git diff -o f",
    "git merge-tree --output=f main feature",
    # an option that runs a program or a filter
    "git diff --ext-diff",
    "git log -p --ext-diff",
    "git diff --textconv",
    "git show --textconv HEAD",
    "git cat-file --textconv HEAD",
    "git cat-file --filters HEAD",
    "git log --show-signature",
    "git log --exec=x",
    # config, repository or work tree taken from the command line
    "git -c core.pager=x log",
    "git -c alias.x=y log",
    "git -ccore.pager=x log",
    "git --config-env=core.pager=X log",
    "git --git-dir=x log",
    "git --git-dir x log",
    "git --work-tree=x status",
    "git --work-tree x status",
    "git --namespace=x log",
    "git --exec-path=x log",
    "git --super-prefix=x log",
    "git -C {repo} -c a=b status",
    "git rev-parse --git-dir",
    "git rev-parse --git-path x",
    "git rev-parse --parseopt",
    "git diff --no-index a b",
    # reads commands from stdin
    "git log --stdin",
    "git rev-list --stdin",
    "git merge-tree --stdin",
    "git cat-file --batch",
    "git cat-file --batch-check",
    "git ls-files --exclude-from=x",
    "git ls-files -x x",
    "git ls-files --recurse-submodules",
    # git abbreviates long options; an abbreviation is never on the list
    "git log --onel",
    "git diff --stat-wid=5",
    "git status --short --ignore",
    # a branch that is not a listing
    "git branch -D x",
    "git branch -d x",
    "git branch --delete x",
    "git branch new",
    "git branch new main",
    "git branch -m a b",
    "git branch -M a b",
    "git branch -c a b",
    "git branch -f x main",
    "git branch -u origin/main",
    "git branch --set-upstream-to=origin/main",
    "git branch --unset-upstream",
    "git branch --edit-description",
    "git branch --list feature",
    "git branch --contains HEAD x",
    "git branch --merged",
    "git branch -a x",
    "git branch -vv new",
    "git branch -av -D",
    "git branch --contains=HEAD",
    "git branch --track x",
    "git branch -l",
    # fetch moves refs, creates tags and talks to the network: it goes to the GM
    "git fetch origin main",
    "git fetch",
    "git fetch origin",
    "git fetch --prune origin",
    "git fetch --tags origin",
    "git fetch origin tag v1",
    "git fetch origin +a:b",
    "git fetch --prune --tags origin",
    "git fetch --quiet --no-tags origin main feature",
    "cd {repo} && git fetch origin main",
    "git status && git fetch origin main",
    # `2>&1` and pipes other than the one allowed
    "git push -u origin feature 2>/tmp/x",
    "git push -u origin feature 2> /tmp/x",
    "git push origin feature 2>&1 >/tmp/x",
    "git push origin feature 2>&1 > /tmp/x",
    "git push origin feature 2>&2",
    "git push origin feature 2>>x",
    "git log >&2",
    "git log 1>&2",
    "git log &> f",
    "git log &>> f",
    "git log 2 >& 1",
    "git log 2>&1x",
    "git log x2>&1",
    "git log 2>&10",
    "git log 2>&1|tail",
    "git log 2>&1 &",
    "git log | tail -n 5 2>&1",
    "git log 2>&1 | tail -n 5 2>&1",
    "git log 2>&1 2>&1",
    "git 2>&1 log",
    "git log 2>&1 --oneline",
    "git push 2>&1 origin feature",
    "cd {repo} 2>&1 && git log",
    "cd 2>&1 && git push origin feature",
    "cd {repo} && git push origin feature 2>&1 && cd 2>&1",
    "git diff > f",
    "git diff >f",
    "git log > f",
    "git log | sh",
    "git log | bash",
    "git log | cat",
    "git log | tee f",
    "git log | xargs rm",
    "git log | git status",
    "git log | tail -n 5 | sh",
    "git log | tail -n 5 | tail -n 3",
    "git log | head -n 5 | tail -n 3",
    "git log | tail -n 5 && git push origin feature",
    "git log | tail -n 5; git push origin feature",
    "git log | tail -f",
    "git log | tail -F",
    "git log | tail -n 5 file",
    "git log | tail /etc/passwd",
    "git log | head -c 5",
    "git log | head --bytes=5",
    "git log | tail -n5",
    "git log | tail -n +5",
    "git log | tail --lines=5",
    "git log | tail -n -5",
    "git log | tail -n",
    "git log | tail -n x",
    "git log | tail -x5",
    "git log | tail -n 5 6",
    "git log | tail -5 -n 5",
    "git log | tail -5q",
    "git log | head -n 1_0",
    "git log |& tail",
    "git log | (tail -n 5)",
    "git log | { tail; }",
    "cd {repo} | tail -n 5",
    "echo hi | tail -n 5",
    "tail -n 5",
    "head -50",
    "git log && tail -n 5",
    "git log; tail -n 5",
    "git log || head -n 5",
    "git log |",
    "| tail -n 5",
    "git log\x01",
    "git log 2>&1\x01",
    "git log \x01 | tail",
    # the same dangers next to an approved command
    "git status && git merge feature",
    "git merge feature && git status",
    "git log | tail -n 5 && git merge feature",
    "git diff --stat && git diff --output=f",
    "git push origin feature && git branch new",
    "cd {repo} && git branch -D feature",
]


@pytest.mark.parametrize("command", READ_ONLY_REFUSED)
def test_anything_else_stays_refused(repo, command):
    denied(fill(command, repo), repo)


@pytest.mark.parametrize(
    ("command", "named"),
    [
        ("git diff --output=f", "git diff option --output=f"),
        ("git log -o f", "git log option -o"),
        ("git branch -D x", "git branch option -D"),
        ("git branch new", "git branch argument new"),
    ],
)
def test_a_refused_read_only_command_names_the_reason(repo, command, named):
    message = denied(command, repo)
    assert named in message
    assert message.startswith(f"{PREFIX} {DENIED}")


@pytest.mark.parametrize(
    "command",
    [
        "git log --format=%GS",
        "git log --format=%h%x20%GS",
        "git log --pretty=format:%GG",
        "git log --pretty=%GK",
        "git show --format=%GG HEAD",
        "git show --pretty=format:%h%GF HEAD",
        "git rev-list --format=%GP HEAD",
        "git log --oneline --format=%GT",
    ],
)
def test_a_format_with_a_signature_placeholder_is_refused_because_it_runs_gpg(repo, command):
    assert "%G" in denied(command, repo)


@pytest.mark.parametrize(
    "command",
    [
        "git log --format=%h",
        "git log --format=%H%x20%s",
        "git log --pretty=format:%h%x20%an",
        "git show --format=%g HEAD",
        "git log --pretty=oneline",
    ],
)
def test_a_format_without_a_signature_placeholder_is_approved(repo, command):
    assert verdict(command, repo) == {"behavior": "allow"}


@pytest.mark.parametrize(
    "command",
    [
        "git push origin main 2>&1 | tail -n 20",
        "git push origin feature:main 2>&1",
        "git push origin main 2>&1 | head -5",
        "git push --force origin feature 2>&1 | tail -n 20",
        "git push -f origin feature 2>&1",
        "git push origin +feature 2>&1 | tail",
        "git log && git push origin main",
        "git push origin main && git log",
        "git push origin feature 2>&1 | tail -n 5; git push origin main",
        "git status && git push origin pre 2>&1 | tail",
        "git push --all 2>&1 | tail",
    ],
)
def test_the_push_checks_are_unchanged_with_plumbing_and_read_only_neighbours(repo, command):
    denied(command, repo)


def test_a_push_among_read_only_commands_is_checked_in_every_directory(repo, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    denied(f"cd {other} && git status && git push origin feature 2>&1 | tail", repo)


def test_the_2_to_1_text_alone_is_not_a_command_part(repo):
    # `2>&1` only trails a git segment; as a segment of its own it is no command at all.
    denied("2>&1", repo)
    denied(f"cd {repo} && 2>&1", repo)


@pytest.mark.parametrize("command", ["git status", "git log -1", "git merge-base main feature"])
def test_a_git_variable_that_changes_git_s_config_refuses_read_only_commands_too(
    repo, monkeypatch, command
):
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    assert "GIT_CONFIG_COUNT" in denied(command, repo)


# --- gh pr create, view and checks ----------------------------------------------------------------

GH_APPROVED = [
    "gh pr create --fill",
    "gh pr create --base develop --head feature --title Add_login --body-file body.md",
    "gh pr create --draft --fill --label bug,docs",
    "gh pr view",
    "gh pr view 12",
    "gh pr view 12 --json state,statusCheckRollup",
    "gh pr view feature --comments",
    "gh pr checks",
    "gh pr checks 12 --watch",
    "gh pr checks --required",
    "gh pr create --fill 2>&1",
    "gh pr view 12 2>&1 | tail -n 20",
    "gh pr checks | head -5",
    "gh pr create --fill 2>&1 | tail",
    "cd {repo} && gh pr create --fill 2>&1 | tail -n 5",
    "git push -u origin feature && gh pr create --fill",
    "gh pr view 12 && gh pr checks 12",
]


@pytest.mark.parametrize("command", GH_APPROVED)
def test_gh_pr_create_allowed(repo, command):
    assert verdict(fill(command, repo), repo) == {"behavior": "allow"}


@pytest.mark.parametrize(
    "command",
    [
        "gh pr merge 12",
        "gh pr merge --auto 12",
        "gh pr close 12",
        "gh pr reopen 12",
        "gh pr edit 12 --title x",
        "gh pr comment 12 --body-file b.md",
        "gh pr review 12 --approve",
        "gh pr ready 12",
        "gh pr checkout 12",
        "gh pr diff 12",
        "gh pr list",
        "gh pr",
        "gh",
        "gh issue create --fill",
        "gh repo delete x",
        "gh release create v1",
        "gh api repos/x/y -X DELETE",
        "gh auth token",
        "gh alias set x 'pr merge'",
        "gh -R x/y pr merge 12",
        "gh --repo x/y pr view 12",
        "gh pr view 12 && gh pr merge 12",
        "gh pr merge 12 && gh pr view 12",
        "git push origin feature && gh pr merge 12",
        "gh pr create --fill; gh pr merge",
    ],
)
def test_gh_pr_merge_denied(repo, command):
    message = denied(command, repo)
    assert message.startswith(f"{PREFIX} {DENIED}")


@pytest.mark.parametrize(
    "command",
    [
        "gh pr create --title $(id)",
        "gh pr create --title `id`",
        "gh pr create --title $TITLE",
        "gh pr create --fill > out",
        "gh pr create --fill >> out",
        "gh pr create --fill < in",
        "gh pr create --fill 2> err",
        "gh pr create --fill 2>/dev/null",
        "gh pr create --fill &> out",
        "gh pr create --fill 2>&1 > out",
        "gh pr create --fill; rm -rf x",
        "gh pr create --fill && rm -rf x",
        "gh pr create --fill || rm -rf x",
        "gh pr create --fill & rm -rf x",
        "gh pr create --fill | sh",
        "gh pr create --fill | tee out",
        "gh pr create --fill |& tail",
        "gh pr create --fill\nrm -rf x",
        "gh pr create --fill # comment",
        "gh pr create --title a\\ b",
        "gh pr create --title {a,b}",
        "gh pr create --title *",
        "gh pr create --title a?",
        "gh pr create --title [a]",
        "gh pr create --fill $(echo)",
        "gh pr view 12 2>&1 | tail -f",
        "gh pr view 12 2>&1 x",
        "gh pr view 2>&1 12",
        "gh pr view 12 | cat",
        "gh pr view 12 | tail -n 5 | sh",
        "echo hi | gh pr view 12",
        "gh pr view ~root",
        "gh pr view ~-",
        "FOO=1 gh pr view 12",
        "env gh pr view 12",
        "/usr/bin/gh pr view 12",
    ],
)
def test_gh_pr_refuses_shell_metacharacters_and_chaining(repo, command):
    denied(command, repo)


@pytest.mark.parametrize(
    "command",
    [
        'gh pr create --title "Add login" --body "Adds the form"',
        "gh pr create --title 'Add login' --fill",
        "gh pr create --fill --body 'x'",
    ],
)
def test_gh_pr_create_with_quotes_is_denied_and_points_to_body_file(repo, command):
    message = denied(command, repo)
    assert message.startswith(f"{PREFIX} {DENIED}")
    assert "--body-file" in message


def test_a_denied_git_command_does_not_get_the_body_file_hint(repo):
    assert "--body-file" not in denied("git merge feature", repo)


def test_a_denied_gh_merge_does_not_get_the_body_file_hint(repo):
    assert "--body-file" not in denied("gh pr merge 12", repo)


def test_a_cd_alone_is_allowed(repo, tmp_path):
    assert verdict(f"cd {tmp_path}", repo) == {"behavior": "allow"}
    assert verdict("cd ..", repo) == {"behavior": "allow"}


@pytest.mark.parametrize(
    "command", ["cd", "cd -", "cd a b", "cd ~root", "cd /tmp && rm -rf x", "cd /tmp | tail"]
)
def test_a_cd_with_anything_else_is_denied(repo, command):
    denied(command, repo)


# --- the named cases of the task ------------------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "git push origin main",
        "git push -u origin master",
        "git push origin pre",
        "git push origin staging",
        "git push origin HEAD:main",
        "git push origin HEAD:master",
        "git push origin HEAD:refs/heads/main",
        "git push origin feature:refs/heads/main",
        "git push origin feature:refs/heads/staging",
        "git push origin refs/heads/main",
        "git push origin feature:pre",
    ],
)
def test_push_to_main_denied(repo, command):
    message = denied(command, repo)
    assert "protected: main, master, pre, staging, develop, trunk, production" in message


@pytest.mark.parametrize(
    "command",
    [
        "git push --force origin feature",
        "git push -f origin feature",
        "git push --force-with-lease origin feature",
        "git push origin +feature",
        "git push origin +HEAD:feature",
    ],
)
def test_force_push_denied(repo, command):
    assert "a force push" in denied(command, repo)


@pytest.mark.parametrize("role", [None, "", "gm", "PM"], ids=repr)
def test_inactive_without_role(repo, run_hook, role):
    env = {} if role is None else {ROLE: role}
    for command in ("git push origin main", "gh pr merge 12"):
        done = run_hook(SCRIPT, request(command, repo), {**{ROLE: ""}, **env})
        assert (done.returncode, done.stdout) == (0, "")


@pytest.mark.parametrize(
    "payload", ["{{{", "", "[]", '"x"', {"cwd": "/tmp"}, {"tool_name": "Bash"}]
)
def test_malformed_payload_denies(run_hook, payload):
    done = run_hook(SCRIPT, payload, {ROLE: "pm"})
    assert done.returncode == 0
    decision = json.loads(done.stdout)["hookSpecificOutput"]["decision"]
    assert decision["behavior"] == "deny"
    assert decision["message"].startswith(f"{PREFIX} could not check this request")


def test_decision_line_is_one_json_document(repo, run_hook):
    done = run_hook(SCRIPT, request("git push -u origin feature", repo), {ROLE: "pm"})
    assert done.returncode == 0
    assert done.stdout.count("\n") == 1
    assert json.loads(done.stdout) == {
        "hookSpecificOutput": {
            "hookEventName": "PermissionRequest",
            "decision": {"behavior": "allow"},
        }
    }


def read_log():
    path = Path(os.environ["XDG_CACHE_HOME"]) / "coven" / "logs" / "pm-permissions.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_decision_logged(repo, run_hook):
    env = {ROLE: "pm"}
    run_hook(SCRIPT, request("git push -u origin feature", repo), env)
    run_hook(SCRIPT, request("git push origin main", repo), env)
    run_hook(SCRIPT, request("", repo, "Edit"), env)
    run_hook(SCRIPT, "{{{", env)
    allowed, protected, other_tool, malformed = read_log()
    assert allowed["decision"] == "allow"
    assert allowed["tool"] == "Bash"
    assert allowed["command"] == "git push -u origin feature"
    assert protected["decision"] == "deny"
    assert protected["command"] == "git push origin main"
    assert "pushing to main" in protected["message"]
    assert (other_tool["decision"], other_tool["tool"]) == ("deny", "Edit")
    assert malformed["decision"] == "deny"
    assert all("ts" in entry for entry in (allowed, protected, other_tool, malformed))


def test_nothing_is_logged_outside_a_pm_session(repo, run_hook):
    run_hook(SCRIPT, request("git push origin main", repo), {ROLE: "gm"})
    log = Path(os.environ["XDG_CACHE_HOME"]) / "coven" / "logs" / "pm-permissions.jsonl"
    assert not log.exists()


def test_a_long_command_is_cut_in_the_log(repo, run_hook):
    run_hook(SCRIPT, request("git log " + "a" * 5000, repo), {ROLE: "pm"})
    (entry,) = read_log()
    assert len(entry["command"]) <= 300


def test_a_missing_hook_log_module_does_not_change_the_decision(repo, run_hook, tmp_path):
    lone = tmp_path / "lone"
    lone.mkdir()
    (lone / SCRIPT.name).write_text(SCRIPT.read_text())
    allowed = run_hook(
        lone / SCRIPT.name, request("git push -u origin feature", repo), {ROLE: "pm"}
    )
    refused = run_hook(lone / SCRIPT.name, request("git push origin main", repo), {ROLE: "pm"})
    assert json.loads(allowed.stdout)["hookSpecificOutput"]["decision"] == {"behavior": "allow"}
    assert json.loads(refused.stdout)["hookSpecificOutput"]["decision"]["behavior"] == "deny"
    assert allowed.returncode == refused.returncode == 0


@pytest.mark.parametrize("command", ["git push origin feature 2>&1 | tail -n 20", "git status"])
def test_installed_form_approves_plumbing_and_read_only_git(repo, run_hook, command):
    done = run_hook(SCRIPT, request(command, repo), {ROLE: "pm"}, cwd=repo)
    assert done.returncode == 0
    assert json.loads(done.stdout)["hookSpecificOutput"]["decision"] == {"behavior": "allow"}


def test_installed_form_refuses_a_push_to_main(repo, run_hook):
    done = run_hook(SCRIPT, request("git push origin main", repo), {ROLE: "pm"}, cwd=repo)
    assert done.returncode == 0
    decision = json.loads(done.stdout)["hookSpecificOutput"]["decision"]
    assert decision["behavior"] == "deny"
    assert "pushing to main" in decision["message"]


# --- fix round 1 ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("command", "reason"),
    [
        ("git log --author=a|b", "a pipe into b"),
        ("git log | tail -n 5 && git status", "last part of a pipe"),
        ("python manage.py migrate", "runs python"),
        ("git merge feature", "git merge is not approved"),
        ("git commit --amend", "git commit is not approved"),
        ("git log $(id)", "'$' is not allowed"),
        ("git log > out", "'>' is not allowed"),
    ],
)
def test_a_denied_command_names_the_actual_cause(repo, command, reason):
    message = denied(command, repo)
    assert f"Why: {reason}" in message or reason in message
    assert "ask rule" not in message


@pytest.mark.parametrize(
    "command",
    [
        "gh pr create --fill --body-file .superpowers/pr-body.md",
        "gh pr create --fill -F body.md",
        "gh pr create --fill --body-file=docs/body.md",
        "gh pr create --fill --body-file ./body.md",
        "gh pr create --fill -Fbody.md",
        "gh pr create --fill --body-file .env.example",
        "cd {repo}/sub && gh pr create --fill --body-file b.md",
    ],
)
def test_gh_body_file_inside_the_worktree_is_allowed(repo, command):
    assert verdict(fill(command, repo), repo) == {"behavior": "allow"}


@pytest.mark.parametrize(
    "command",
    [
        "gh pr create --fill --body-file ~/.ssh/id_rsa",
        "gh pr create --fill --body-file /etc/passwd",
        "gh pr create --fill --body-file=/etc/passwd",
        "gh pr create --fill -F /etc/passwd",
        "gh pr create --fill -F/etc/passwd",
        "gh pr create --fill -dF /etc/passwd",
        "gh pr create --fill --body-file ~",
        "gh pr create --fill --body-file ../x",
        "gh pr create --fill --body-file a/../../x",
        "gh pr create --fill --body-file ..",
        "gh pr create --fill --body-file .env",
        "gh pr create --fill --body-file sub/.env.local",
        "gh pr create --fill --body-file .ssh/id_rsa",
        "gh pr create --fill --body-file=.env",
        "gh pr create --fill --body-file link/passwd",
        "gh pr create --fill --body-file",
        "gh pr create --fill --body-file=",
        "gh pr create --fill --body-file -",
        "cd .. && gh pr create --fill --body-file x",
    ],
)
def test_gh_body_file_outside_the_worktree_or_secret_is_denied(repo, command):
    (repo / "link").symlink_to("/etc")
    message = denied(fill(command, repo), repo)
    assert message.startswith(f"{PREFIX} {DENIED}")
    assert "inside the worktree" in message


@pytest.mark.parametrize("sub", ["create --fill", "view 12", "checks 12"])
@pytest.mark.parametrize(
    "option", ["-R other/repo", "--repo other/repo", "--repo=other/repo", "-Rother/repo", "-dR x/y"]
)
def test_gh_pr_refuses_another_repository(repo, sub, option):
    message = denied(f"gh pr {sub} {option}", repo)
    assert "another repository" in message


@pytest.mark.parametrize(
    "command",
    [
        "gh pr create --fill --web",
        "gh pr create --fill -w",
        "gh pr create --fill -fw",
        "gh pr create --editor",
        "gh pr create -e",
        "gh pr create --fill -de",
        "gh pr view 12 --web",
        "gh pr view 12 -w",
        "gh pr checks 12 --web",
    ],
)
def test_gh_pr_refuses_what_opens_a_browser_or_an_editor(repo, command):
    assert "browser or an editor" in denied(command, repo)


def test_gh_pr_checks_watch_is_still_allowed(repo):
    assert verdict("gh pr checks 12 --watch", repo) == {"behavior": "allow"}


def test_a_push_of_a_tag_is_refused(repo):
    git(repo, "branch", "tag")
    git(repo, "branch", "v1")
    assert "tag" in denied("git push origin tag v1", repo)
    assert verdict("git push origin v1", repo) == {"behavior": "allow"}


# --- fix round 2 ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "gh pr create --fill -F=/etc/passwd",
        "gh pr create --fill -F=../x",
        "gh pr create --fill -dF=/etc/passwd",
        "gh pr create --fill -F ok.md -F=/etc/passwd",
        "gh pr create --fill -F=",
        "gh pr create --fill -T /etc/passwd",
        "gh pr create --fill -T=/etc/passwd",
        "gh pr create --fill -T/etc/passwd",
        "gh pr create --fill -dT ../x",
        "gh pr create --fill --template /etc/passwd",
        "gh pr create --fill --template=../x",
        "gh pr create --fill --template=/etc/passwd",
        "gh pr create --fill --template .env",
        "gh pr create --fill --recover /etc/passwd",
        "gh pr create --fill --recover=/etc/passwd",
        "gh pr create --fill --recover=../x",
    ],
)
def test_gh_file_options_are_checked_in_every_spelling(repo, command):
    message = denied(command, repo)
    assert "inside the worktree" in message


@pytest.mark.parametrize(
    "command",
    [
        "gh pr create --fill --template .github/pr.md",
        "gh pr create --fill --template=.github/pr.md",
        "gh pr create --fill -T .github/pr.md",
        "gh pr create --fill -T=.github/pr.md",
        "gh pr create --fill -F=ok.md",
        "gh pr create --fill -dF=ok.md",
        "gh pr create --fill -F ok.md -F=also/ok.md",
        "gh pr create --fill --recover=abc123",
    ],
)
def test_gh_file_options_inside_the_worktree_are_allowed(repo, command):
    assert verdict(command, repo) == {"behavior": "allow"}


# --- more protected branches: a fixed list, and the remote's default branch -----------------------


def branch_on_origin(repo, name):
    """Check out a new branch `name` that tracks origin/<name>."""
    git(repo, "checkout", "-q", "-b", name)
    git(repo, "push", "-q", "-u", "origin", name)


@pytest.mark.parametrize("branch", ["develop", "trunk", "production", "Trunk"])
def test_the_fixed_list_protects_develop_trunk_and_production(repo, branch):
    message = denied(f"git push origin {branch}", repo)
    assert f"pushing to {branch} (protected: " in message
    assert "develop, trunk, production" in message


def test_head_on_trunk_is_refused(repo):
    branch_on_origin(repo, "trunk")
    assert "pushing to trunk" in denied("git push -u origin HEAD", repo)
    assert "pushing to trunk" in denied("git push", repo)


def test_a_refspec_to_develop_is_refused(repo):
    assert "pushing to develop" in denied("git push origin HEAD:develop", repo)


def release_line_is_the_default(repo):
    branch_on_origin(repo, "release-line")
    git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/release-line")


@pytest.mark.parametrize(
    "command",
    [
        "git push origin release-line",
        "git push -u origin HEAD",
        "git push",
        "git push origin HEAD:release-line",
        "git push origin feature:refs/heads/Release-Line",
    ],
)
def test_the_remotes_default_branch_is_protected(repo, command):
    release_line_is_the_default(repo)
    message = denied(command, repo)
    assert "pushing to" in message
    assert "the remote's default branch" in message


def test_an_unusual_branch_is_not_protected_when_it_is_not_the_default(repo):
    branch_on_origin(repo, "release-line")
    assert verdict("git push -u origin HEAD", repo)["behavior"] == "allow"


def test_the_default_branch_of_another_remote_does_not_protect_a_branch(repo, tmp_path):
    release_line_is_the_default(repo)
    other = tmp_path / "other.git"
    subprocess.run(["git", "init", "-q", "--bare", str(other)], check=True, env=clean_env())
    git(repo, "remote", "add", "fork", str(other))
    assert verdict("git push fork release-line", repo)["behavior"] == "allow"


# --- the PreToolUse guard: the forms the ask and deny rules miss ----------------------------------

GUARD_ARGV = ["--pre-tool-use"]


def guard(payload, role="pm"):
    """(exit code, stdout, stderr) of `pm_permissions.py --pre-tool-use` on a payload."""
    stdin = io.StringIO(payload if isinstance(payload, str) else json.dumps(payload))
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = pm.main(GUARD_ARGV, stdin, {ROLE: role})
    return code, out.getvalue(), err.getvalue()


# Each runs with no prompt in bypassPermissions: none matches `Bash(git push:*)` or `Bash(gh:*)`.
GUARD_DENIED = [
    "git -C . push --force",
    "git --no-pager push origin main",
    "git -c k=v push origin main",
    "command git push -f",
    'bash -c "git push --force"',
    "cd x && git -C y merge z",
    "/usr/bin/gh pr merge 1",
    # Words split by quoting, a newline or an escape: the shell joins them, a text search does not.
    "git pu''sh -f",
    'git pu"s"h -f',
    'git "pu"sh',
    'echo hi\ngit pu"s"h origin main',
    "git pu\\\nsh -f",
    "git $'pu\\x73h' -f",
    "git send-pack ../o.git main",
    "make push-docs",
    # A command that hides behind a prefix, a wrapper or a shell construct.
    "FOO=1 git push -f",
    "env GIT_TRACE=1 git push -f",
    "sudo git push -f",
    "{ git push -f; }",
    "sleep 1 & git push -f",
    "echo $(git push -f)",
    "git $sub -f",
    "bash -c \"git pu''sh -f\"",
    "\\git push -f",
    "git commit -m 'unbalanced",
]


@pytest.mark.parametrize("command", GUARD_DENIED)
def test_the_guard_denies_the_forms_the_rules_miss(repo, command):
    code, out, err = guard(request(command, repo))
    assert (code, out) == (2, "")
    assert err.startswith(PREFIX)
    assert err.strip() == pm.decide(pm.Call("Bash", command, str(repo)))


# A word in a message, a pattern, a file name or a comment is not a command.
GUARD_QUIET = [
    "pytest -q",
    "git status",
    "git push origin feature",
    'git commit -m "push the button"',
    "rg push",
    "pytest tests -k push",
    "cat docs/push-notes.md",
    "ls # merge notes",
    "git log --grep merge",
    "echo 'unbalanced",
    "git -c core.pager=cat log --grep push",
]


@pytest.mark.parametrize("command", GUARD_QUIET)
def test_the_guard_says_nothing_about_what_the_rules_handle(repo, command):
    # A plain push of a feature branch still reaches the ask rule and the PermissionRequest hook.
    assert guard(request(command, repo)) == (0, "", "")


@pytest.mark.parametrize("command", [*GUARD_DENIED, "git push origin main"])
@pytest.mark.parametrize("role", [None, "", "gm", "PM"], ids=repr)
def test_the_guard_does_nothing_outside_a_pm_session(repo, role, command):
    stdin = io.StringIO(json.dumps(request(command, repo)))
    environ = {} if role is None else {ROLE: role}
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        assert pm.main(GUARD_ARGV, stdin, environ) == 0
    assert (out.getvalue(), err.getvalue()) == ("", "")


def test_decide_denies_send_pack(repo):
    # send-pack pushes like `git push` and is on no approved list.
    message = pm.decide(pm.Call("Bash", "git send-pack ../o.git main", str(repo)))
    assert message is not None
    assert message.startswith(PREFIX)


@pytest.mark.parametrize(
    "payload",
    ["not json", "[]", json.dumps({"tool_name": "Bash", "tool_input": {}, "cwd": "/"})],
    ids=["not-json", "not-an-object", "no-command"],
)
def test_the_guard_denies_a_bad_payload_in_a_pm_session(payload):
    code, out, err = guard(payload)
    assert (code, out) == (2, "")
    assert err.startswith(PREFIX)


def test_the_guard_denies_on_an_internal_error(repo, monkeypatch):
    def broken(_call):
        raise RuntimeError("boom")

    monkeypatch.setattr(pm, "decide", broken)
    code, out, err = guard(request("git push -f", repo))
    assert (code, out) == (2, "")
    assert "internal error" in err


def test_the_guard_denies_a_check_past_the_deadline(repo, monkeypatch):
    def slow(_call):
        raise pm.DeadlineError

    monkeypatch.setattr(pm, "decide", slow)
    code, out, err = guard(request("git push -f", repo))
    assert (code, out) == (2, "")
    assert "took too long" in err


def test_the_guard_ignores_a_tool_that_is_not_bash(repo):
    assert guard(request("", repo, "Edit")) == (0, "", "")


def test_the_guard_runs_as_a_script(repo):
    payload = json.dumps(request("git -C . push --force", repo))
    env = clean_env(**{ROLE: "pm"})
    proc = subprocess.run(
        [sys.executable, "-I", str(SCRIPT), *GUARD_ARGV],
        input=payload,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert (proc.returncode, proc.stdout) == (2, "")
    assert "force push" in proc.stderr
