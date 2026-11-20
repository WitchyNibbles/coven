"""The plugin's skills: frontmatter, agent names, upstream notices and the commands they cite."""

import contextlib
import io
import re
from pathlib import Path

import pytest
from support import REPO, SHIPPED_SKILLS

from coven import cli, settings

SKILLS = REPO / "skills"
SDD = SKILLS / "subagent-driven-development"
PM = REPO / "agents/project-manager.md"
# Both plugins ship these two, so a bare name could start the wrong one.
CONTESTED = ("subagent-driven-development", "requesting-code-review")

# skill -> the upstream project whose MIT notice the folder's LICENSE carries.
DERIVED = {
    "handoff": "Matt Pocock",
    "grill-me": "Matt Pocock",
    "ceo-review": "Garry Tan",
    "office-hours": "Garry Tan",
    "subagent-driven-development": "Jesse Vincent",
    "requesting-code-review": "Jesse Vincent",
}


def skill_files() -> list[Path]:
    return sorted(path for path in SKILLS.rglob("*") if path.is_file() and path.suffix == ".md")


def coven_texts() -> list[Path]:
    """Every Markdown file that tells an agent what to do: skills, agents and instructions."""
    return skill_files() + sorted((REPO / "agents").glob("*.md"))


def frontmatter(skill: str) -> dict[str, str]:
    text = (SKILLS / skill / "SKILL.md").read_text(encoding="utf-8")
    match = re.fullmatch(r"---\n(.*?)\n---\n.*", text, re.DOTALL)
    assert match, f"{skill}/SKILL.md has no frontmatter"
    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        key, sep, value = line.partition(": ")
        assert sep, f"{skill}: frontmatter line is not `key: value`: {line!r}"
        fields[key] = value.strip().strip('"')
    return fields


def body(skill: str) -> str:
    return (SKILLS / skill / "SKILL.md").read_text(encoding="utf-8")


def test_shipped_skills_are_the_skill_folders() -> None:
    assert {path.parent.name for path in SKILLS.glob("*/SKILL.md")} == SHIPPED_SKILLS
    assert {path.name for path in SKILLS.iterdir()} == SHIPPED_SKILLS


@pytest.mark.parametrize("skill", sorted(SHIPPED_SKILLS))
def test_skills_have_frontmatter(skill: str) -> None:
    fields = frontmatter(skill)
    assert fields["name"] == skill
    assert 20 < len(fields["description"]) <= 1024
    assert set(fields) <= {"name", "description", "argument-hint", "disable-model-invocation"}


def test_grill_me_is_user_invoked_only() -> None:
    fields = frontmatter("grill-me")
    assert fields["disable-model-invocation"] == "true"
    assert "user-invoked" in fields["description"].lower()
    for skill in SHIPPED_SKILLS - {"grill-me"}:
        assert "disable-model-invocation" not in frontmatter(skill)


def test_sdd_names_coven_agents() -> None:
    texts = {path.name: path.read_text(encoding="utf-8") for path in SDD.glob("*.md")}
    for agent in ("coven:implementer", "coven:reviewer", "coven:final-reviewer"):
        assert agent in texts["SKILL.md"]
    assert "coven:implementer" in texts["implementer-prompt.md"]
    assert "coven:reviewer" in texts["task-reviewer-prompt.md"]
    assert "coven:reviewer" in texts["re-review-prompt.md"]
    review = (SKILLS / "requesting-code-review").glob("*.md")
    review_text = "\n".join(path.read_text(encoding="utf-8") for path in review)
    assert "coven:reviewer" in review_text
    assert "coven:final-reviewer" in review_text
    everything = "\n".join(texts.values()) + review_text
    # A bare role name or another tool's dispatch form would start the wrong agent.
    assert not re.search(r'subagent_type: "(?!coven:)', everything)
    assert not re.search(r"^Subagent \((?!coven:)", everything, re.MULTILINE)
    for other_tool in ("Codex", "OpenCode"):
        assert other_tool not in everything


def test_review_skills_require_coven_check_receipts() -> None:
    for path in skill_files():
        if path.parent.name not in {"subagent-driven-development", "requesting-code-review"}:
            continue
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"(?<!\.coven/)check\.py", text), (
            f"{path.name}: a check.py not in .coven/"
        )
    reviewer = (SDD / "task-reviewer-prompt.md").read_text(encoding="utf-8")
    assert "python3 .coven/check.py fast" in reviewer
    assert "COVEN_CHECK_RUNNER" in reviewer
    final = (SKILLS / "requesting-code-review/code-reviewer.md").read_text(encoding="utf-8")
    assert "python3 .coven/check.py full --base" in final
    assert "coven-check" in final


def bare_mentions(text: str, name: str) -> list[str]:
    """Each mention of the skill name that is neither `coven:`-prefixed nor part of a path."""
    return [
        text[max(0, match.start() - 20) : match.end() + 10]
        for match in re.finditer(rf"(?<![\w-]){re.escape(name)}(?![\w-])", text)
        if text[max(0, match.start() - 6) : match.start()] != "coven:"
        and "/"
        not in {text[match.start() - 1 : match.start()], text[match.end() : match.end() + 1]}
        and not text[: match.start()].endswith("name: ")
    ]


def test_no_superpowers_sdd_reference_in_coven_text() -> None:
    for path in coven_texts():
        text = path.read_text(encoding="utf-8")
        for name in CONTESTED:
            assert bare_mentions(text, name) == [], f"{path.relative_to(REPO)} names {name} bare"


def test_bare_mentions_helper_sees_a_bare_name() -> None:
    assert bare_mentions("use subagent-driven-development now", "subagent-driven-development")
    assert not bare_mentions("use `coven:requesting-code-review`", "requesting-code-review")
    assert not bare_mentions("see ../requesting-code-review/x.md", "requesting-code-review")
    assert not bare_mentions("name: requesting-code-review\n", "requesting-code-review")


@pytest.mark.parametrize("skill", sorted(DERIVED))
def test_derived_skills_have_license(skill: str) -> None:
    text = (SKILLS / skill / "LICENSE").read_text(encoding="utf-8")
    assert text.startswith("MIT License\n")
    assert "Copyright (c) 20" in text
    assert DERIVED[skill] in text
    assert 'THE SOFTWARE IS PROVIDED "AS IS"' in text


def test_notice_lists_the_upstream_projects() -> None:
    notice = (REPO / "NOTICE").read_text(encoding="utf-8")
    for url in (
        "https://github.com/obra/superpowers",
        "https://github.com/garrytan/gstack",
        "https://github.com/mattpocock/skills",
    ):
        assert url in notice
    for holder in set(DERIVED.values()):
        assert holder in notice
    for skill in DERIVED:
        assert skill in notice


def cited_invocations(text: str) -> list[list[str]]:
    """The words after `coven` in every code span and fenced line of the text."""
    code = re.findall(r"`([^`\n]+)`", text) + re.findall(
        r"^[ \t]*(?:[$!] ?)?(coven .*)$", text, re.M
    )
    found: list[list[str]] = []
    for chunk in code:
        for match in re.finditer(r"(?<![\w:/.\-])coven (?=[a-z])(.*)", chunk):
            found.append(match.group(1).split())
    return found


def help_text(*words: str) -> str:
    """What `coven <words> --help` prints, through the real parser."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        cli.main([*words, "--help"])
    return out.getvalue()


def subcommands(*words: str) -> set[str]:
    """The subcommand names `coven <words> --help` lists (argparse indents them four spaces)."""
    return set(re.findall(r"^ {4}([a-z][\w-]*)\s", help_text(*words), re.MULTILINE))


def unknown_parts(words: list[str]) -> list[str]:
    """What in a cited `coven ...` line the real CLI does not have."""
    path: list[str] = []
    for word in words:
        names = subcommands(*path)
        if not names or not re.fullmatch(r"[a-z][\w-]*", word):
            break
        if word not in names:
            return [" ".join(["coven", *path, word])]
        path.append(word)
    shown = help_text(*path)
    return [
        f"{' '.join(['coven', *path])} {flag}"
        for flag in re.findall(r"(?<![\w-])(--[a-z][\w-]*)", " ".join(words[len(path) :]))
        if flag not in shown
    ]


def test_cited_invocations_helper() -> None:
    assert cited_invocations("run `coven doctor` or `!coven pm status`, not coven config") == [
        ["doctor"],
        ["pm", "status"],
    ]
    assert cited_invocations("```sh\ncoven memory init\n```\nuse `python3 .coven/check.py`") == [
        ["memory", "init"]
    ]
    assert cited_invocations("`coven:setup` and `.coven/x` and `coven ...`") == []


def test_unknown_parts_sees_a_made_up_command_and_flag() -> None:
    assert unknown_parts(["memory", "wipe"]) == ["coven memory wipe"]
    assert unknown_parts(["bogus"]) == ["coven bogus"]
    assert unknown_parts(["setup", "--yes", "--made-up"]) == ["coven setup --made-up"]
    assert unknown_parts(["pm", "status", "--gm", "NAME", "--nope"]) == ["coven pm status --nope"]
    assert unknown_parts(["pm", "launch", "x", "--repo", "r"]) == []
    assert unknown_parts(["setup", "--dry-run", "--pm-cap", "2", "--memory-path", "~/m"]) == []


def test_skill_commands_exist() -> None:
    cited: set[tuple[str, ...]] = set()
    for path in coven_texts():
        for words in cited_invocations(path.read_text(encoding="utf-8")):
            assert unknown_parts(words) == [], f"{path.relative_to(REPO)}: {words}"
            cited.add(tuple(words[:2]))
    # Not vacuous: the skills cite the commands they are built on.
    for command in (
        ("doctor",),
        ("setup", "--dry-run"),
        ("memory", "init"),
        ("memory", "sync"),
        ("project",),
        ("pm", "status"),
        ("pm", "launch"),
        ("approve", "--revoke"),
        ("guard", "check"),
        ("guard", "explain"),
    ):
        assert any(seen[: len(command)] == command for seen in cited), command


def test_setup_skill_walks_through_the_first_run() -> None:
    text = body("setup")
    for needle in (
        "coven doctor",
        "coven setup --dry-run",
        "--yes",
        "coven memory init",
        "examples/toy",
        "pm_cap",
        "default 2",
        "crossSessionInbound",
        "--shim",
        "private",
    ):
        assert needle in text, needle
    assert text.index("--dry-run") < text.index("--yes")


def test_setup_skill_lists_every_deny_rule_setup_adds() -> None:
    text = body("setup")
    for rule in settings.DENY_RULES:
        target = rule.removeprefix("Read(").removesuffix(")")
        assert target in text, rule


def test_setup_skill_saves_a_chosen_store_path_through_setup() -> None:
    # `coven memory init --path P` never writes memory_path to the config, so the hooks and doctor
    # would keep reading the default store.
    text = body("setup")
    assert "memory init --path" not in text
    assert "coven setup --memory-path" in text


def test_manager_skill_is_the_hierarchy_only() -> None:
    text = body("manager")
    for needle in (
        "coven pm launch",
        "coven pm resume",
        "coven pm health",
        "coven pm status",
        "pm_cap",
        "claude --bg",
        "APPROVED <project>/<task>: ran, <result>",
        "DENIED <project>/<task>:",
        "origin: <url>",
        "projects/<key>/INDEX.md",
        "<key>-state.md",
        "SendMessage",
        "notify_when_idle",
    ):
        assert needle in text, needle
    # The launch line comes from the CLI; the skill carries no flag list of its own.
    assert "--permission-mode" not in text
    assert "--settings" not in text
    assert "--agent" not in text


def test_skills_leave_out_what_v1_cuts() -> None:
    cut = (
        "/home/",
        "/Users/",
        "~/projects",
        "openai",
    )
    for path in coven_texts():
        lowered = path.read_text(encoding="utf-8").lower()
        for word in cut:
            assert word.lower() not in lowered, f"{path.relative_to(REPO)} mentions {word}"
    assert not list(SKILLS.rglob("openai.yaml"))


def test_sdd_workspace_is_named_the_same_in_text_and_scripts() -> None:
    text = body("subagent-driven-development")
    assert "<repo-root>/.superpowers/sdd/" in text
    for script in (SDD / "scripts").iterdir():
        assert ".superpowers/sdd" in script.read_text(encoding="utf-8")


def test_sdd_scripts_are_run_through_the_skill_directory_variable() -> None:
    # A bare `scripts/<name>` resolves against the user's repo, where there is none.
    for path in skill_files():
        text = path.read_text(encoding="utf-8")
        assert "bash scripts/" not in text, f"{path.relative_to(REPO)} runs a bare scripts/ path"
        assert "from this skill's directory" not in text
    skill = body("subagent-driven-development")
    for script in ("sdd-workspace", "task-brief", "review-package"):
        assert f'bash "${{CLAUDE_SKILL_DIR}}/scripts/{script}"' in skill, script
    # Prompt templates are not substituted: the controller passes the path in.
    for name in ("implementer-prompt.md", "task-reviewer-prompt.md", "re-review-prompt.md"):
        prompt = (SDD / name).read_text(encoding="utf-8")
        assert "CLAUDE_SKILL_DIR" not in prompt
    for name in ("task-reviewer-prompt.md", "re-review-prompt.md"):
        assert "the path the controller gives you" in (SDD / name).read_text(encoding="utf-8")


def test_manager_keeps_the_no_guard_flow_and_asks_the_user_every_time() -> None:
    lowered = body("manager").lower()
    assert "only on a yes" in lowered
    assert "record the user's decision and the result in the state record" in lowered
    assert "no guard" in lowered  # the flow with no guard is named, not dropped


def test_manager_has_the_guard_on_approval_flow() -> None:
    text = body("manager")
    for needle in (
        "approvals/<id>.step",
        "tool: <name>",
        "coven approve <id> --expires-in",
        "12h",
        "APPROVED <project>/<task> <id>:",
        "coven approve --revoke <id>",
        "coven guard check",
        "coven guard explain",
        "--cwd <PM worktree>",
        "--tool Write --path <file>",
        "--file",
    ):
        assert needle in text, needle
    # The steps come in order: the step file, then approve, then the reply.
    flow = text.split("### Guard on")[1]
    assert flow.index("approvals/<id>.step") < flow.index("coven approve <id> --expires-in")
    assert flow.index("coven approve <id> --expires-in") < flow.index(
        "APPROVED <project>/<task> <id>:"
    )
    # The no-guard reply is still taught next to it.
    assert "APPROVED <project>/<task>: ran, <result>" in text


def test_manager_learns_by_kind_and_never_delegates_gated_or_deletions() -> None:
    text = body("manager")
    for needle in (
        "user/prod-<kind>.md",
        "type: feedback",
        "20 answers",
        "reset",
        "standing answer",
        "one line",
        "`gated`",
        "deletion",
        "never delegated",
    ):
        assert needle in text, needle


def test_manager_commits_the_learning_line_before_writing_the_step_file() -> None:
    """`coven approve` refuses unless the step file is the only change in the store."""
    learning = " ".join(body("manager").split("**Learning.**")[1].split())
    assert "commit the learning line" in learning
    assert "before" in learning
    assert "step file" in learning
    assert "only change in the store" in learning


def test_manager_reads_the_doctor_refusals_and_the_hook_note() -> None:
    text = body("manager")
    for needle in (
        "guard refusals",
        "has no `BLOCKED`",
        "coven memory init",
        "coven approve --revoke <id>",
        "pre-commit hook is older than coven's",
    ):
        assert needle in text, needle


def section_of(text: str, start: str, end: str) -> str:
    return text.split(start)[1].split(end)[0]


def test_explain_goes_through_a_file_written_with_the_write_tool() -> None:
    # An inline command naming a target is itself refused, for the GM and for a PM.
    for text in (body("manager"), PM.read_text(encoding="utf-8")):
        flat = " ".join(text.split()).lower()
        assert "every command to explain goes through `--file`" in flat
        assert "written with the write tool, never from bash" in flat


def test_a_pm_does_not_explain_a_refused_edit_of_a_protected_path() -> None:
    pm = " ".join(PM.read_text(encoding="utf-8").split())
    assert "do not explain it" in pm
    assert "the store, coven's config, Claude Code's settings or `plugins/`" in pm
    manager = " ".join(body("manager").split())
    assert "you run `coven guard explain --tool Write --path <file>`" in manager


def test_learning_and_standing_answers_are_scoped_to_a_guard() -> None:
    text = body("manager")
    assert "**Learning.** With a guard on," in text
    intro = section_of(text, "## Production steps", "### No guard")
    assert "With a guard on," in intro
    assert "unless" not in intro.split("With a guard on,")[0]


def test_the_no_guard_section_keeps_d2() -> None:
    flow = section_of(body("manager"), "### No guard", "### Guard on")
    assert "you run the step yourself" in flow
    assert "APPROVED <project>/<task>: ran, <result>" in flow
    for reached in ("Learning", "20 answers", "standing answer"):
        assert reached not in flow


def pm_flat() -> str:
    """The PM role with every run of whitespace as one space, so line wrapping does not matter."""
    return " ".join(PM.read_text(encoding="utf-8").split())


def test_a_pm_keeps_a_task_list_with_one_entry_per_recognisable_step() -> None:
    pm = pm_flat()
    for needle in (
        "Keep a Claude Code task list: one entry per step a person would recognise",
        '("Read X", "Write the failing test", "Review", "Open the PR", "Send DONE")',
        "made with `TaskCreate`, never one per role-agent call",
        "Keep it current with `TaskUpdate`: `in_progress` when a step starts,",
        "`completed` only when it is really done, so the task files always show your plan",
        "A step blocked on a `QUESTION` or a `PROD-STEP` stays `in_progress`",
        "A `TASK` from the GM adds its steps",
    ):
        assert needle in pm, needle


def test_a_pm_rebuilds_its_task_list_when_resumed() -> None:
    pm = pm_flat()
    for needle in (
        "A prompt that starts `RESUMED by <gm>` is a resume:",
        "your task list starts new and empty",
        "rebuild it from the brief and the resume note before you go on:",
        "mark the tasks under `Done:` `completed` and the rest `pending` or `in_progress`",
        "Run `TaskList` first; if it still holds your steps, update them instead of adding them again",
    ):
        assert needle in pm, needle


def test_a_pm_keeps_task_subjects_short_for_the_pane() -> None:
    pm = pm_flat()
    assert "A subject is imperative, at most 50 characters and with no project prefix," in pm
    assert "because a status pane may show it in about 60 columns" in pm


def test_a_pm_loads_the_task_tools_with_toolsearch_when_they_are_deferred() -> None:
    assert (
        "If `TaskCreate` and `TaskUpdate` are deferred tools, load them first with `ToolSearch` "
        "(`select:TaskCreate,TaskUpdate,TaskList`)"
    ) in pm_flat()


def test_pm_role_has_the_guard_on_flow_and_keeps_the_no_guard_rule() -> None:
    text = PM.read_text(encoding="utf-8")
    for needle in (
        "APPROVED <project>/<task> <id>:",
        "APPROVED <project>/<task>: ran, <result>",
        "never run a production step",
        "its own Bash call",
        "BLOCKED",
        "Read, Grep and Glob",
        "coven guard explain",
        "systems/",
        "plugins/",
        "settings",
    ):
        assert needle in text, needle
    # Both replies are told apart, and the guard-on one comes with the exact commands.
    assert text.index("APPROVED <project>/<task> <id>:") < text.index(
        "APPROVED <project>/<task>: ran, <result>"
    )


def test_systems_map_skill_walks_the_map_and_the_guard_targets() -> None:
    text = body("systems-map")
    for needle in (
        "systems/<system>.md",
        "systems/guard.json",
        "production",
        "gated",
        "coven guard check",
        "coven setup --yes",
        "Only the general manager",
        "orders-db",
        "billing-api",
        "example.internal",
    ):
        assert needle in text, needle
    # Explore, draft, ask the user, validate, commit, then latch the guard.
    order = ["Explore", "Draft", "Ask", "coven guard check", "Commit", "coven setup --yes"]
    positions = [text.index(step) for step in order]
    assert positions == sorted(positions)
    assert "systems/" in frontmatter("systems-map")["description"]


# Tool names the guard's texts must not mention.
NOT_NAMED = ("slack", "zendesk", "servicenow", "grafana", "private toolkit")


def guard_texts() -> list[Path]:
    """The texts that teach the guard, approvals and the systems map (other skills are upstream)."""
    return [
        SKILLS / "manager/SKILL.md",
        SKILLS / "systems-map/SKILL.md",
        PM,
        REPO / "instructions/routing.md",
        REPO / "README.md",
        REPO / "CHANGELOG.md",
        REPO / "docs/known-gaps.md",
    ]


def test_no_text_names_a_ticket_or_chat_tool_or_the_private_toolkit() -> None:
    for path in guard_texts():
        lowered = path.read_text(encoding="utf-8").lower()
        for name in NOT_NAMED:
            assert name not in lowered, f"{path.relative_to(REPO)} names {name}"


def test_skills_describe_no_work_queue_feature() -> None:
    for path in skill_files():
        assert "queue" not in path.read_text(encoding="utf-8").lower(), path.relative_to(REPO)


# --- how the GM reports: only what is new, never the same status twice -------------------------


def manager_results() -> str:
    """The manager's Results section with every run of whitespace as one space."""
    return " ".join(section_of(body("manager"), "## Results", "## Overrides").split())


def test_the_gm_reports_only_what_is_new_one_short_line_per_event() -> None:
    assert (
        "Every message to the user carries only what is new since your last message to the user, "
        "one short line per event (a `DONE` keeps the parts listed above): a PM's question, a "
        "production step, a `DONE`, a PM that stopped or failed, a slot that freed up, a launch. "
        "Then comes the `Waiting on you` line."
    ) in manager_results()


def test_the_gm_never_relists_unchanged_pms_and_does_not_paste_pm_status() -> None:
    results = manager_results()
    assert (
        "Never re-list every PM's status, and never repeat a PM or a line whose state has not "
        "changed since your last message."
    ) in results
    assert "`coven pm status` output is for you to read, not to paste." in results


def test_the_gm_gives_the_full_picture_only_when_asked() -> None:
    results = manager_results()
    assert (
        'When the user asks for the full picture ("status?", "where are we?"), give it once, '
        "compactly. Asking is the only time a full listing is right."
    ) in results
    assert (
        "A live board, when the user has one, carries the full picture; without one, nothing "
        "here changes."
    ) in results


def test_waiting_on_you_keeps_its_items_and_shrinks_the_unchanged_ones_to_handles() -> None:
    results = manager_results()
    assert (
        "Every message that follows PM traffic ends with `Waiting on you: <N>` and its items "
        "(questions, production steps, drafts), from the state records, or "
        "`Waiting on you: nothing`."
    ) in results
    assert (
        "A new or changed item gets one full line. An item unchanged since your last message "
        "shrinks to its handle, the project/task plus a few words (`coven/12 PR to open`), and "
        "is not restated."
    ) in results
    assert (
        "When no item changed, the line is `Waiting on you: <N> (unchanged): <handle>; <handle>`."
    ) in results


def test_the_always_rule_agrees_with_results_on_status() -> None:
    always = " ".join(body("manager").split("## Always")[1].split())
    assert "No status the user did not ask for" in always
    assert "only what is new (Results)" in always
    assert "No status updates the user did not ask for" not in always


def test_no_board_or_pane_is_named_in_the_manager_reporting_rule() -> None:
    results = manager_results().lower()
    for name in ("pane", "tmux", "statusline", "artifact"):
        assert name not in results, name
