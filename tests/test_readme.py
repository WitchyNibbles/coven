"""The README keeps its promises: its commands exist, its links resolve, its install steps stand."""

import re
from pathlib import Path

import pytest
from support import REPO
from test_skills_text import cited_invocations, unknown_parts

from coven import settings

README = REPO / "README.md"
OPENCODE_README = REPO / "opencode/README.md"
DOCS = (README, REPO / "docs/known-gaps.md", REPO / "CHANGELOG.md", OPENCODE_README)


def readme() -> str:
    return README.read_text(encoding="utf-8")


def test_every_command_in_the_readme_is_registered() -> None:
    cited = cited_invocations(readme())
    assert cited, "the README cites no coven command"
    for words in cited:
        assert unknown_parts(words) == [], words
    seen = {tuple(words[:2]) for words in cited}
    for command in (("doctor",), ("setup", "--undo"), ("project",), ("memory", "init")):
        assert any(each[: len(command)] == command for each in seen), command


def test_the_readme_check_rejects_a_command_the_cli_does_not_have() -> None:
    assert unknown_parts(["pm", "launch", "x"]) == []
    assert unknown_parts(["pm", "launch", "--made-up"]) == ["coven pm launch --made-up"]
    assert unknown_parts(["made-up"]) == ["coven made-up"]


def github_slug(heading: str) -> str:
    return re.sub(r"[^\w\- ]", "", heading.strip().lower()).replace(" ", "-")


def heading_anchors(text: str) -> set[str]:
    return {github_slug(h) for h in re.findall(r"^#+ +(.+)$", text, re.MULTILINE)}


@pytest.mark.parametrize("doc", DOCS, ids=lambda path: str(path.relative_to(REPO)))
def test_relative_links_resolve(doc: Path) -> None:
    text = doc.read_text(encoding="utf-8")
    links = re.findall(r"(?<!!)\[[^\]]*\]\(([^)\s]+)\)", text)
    for target in links:
        if re.match(r"[a-z][a-z0-9+.-]*:", target):  # https:, mailto:
            continue
        path, _, anchor = target.partition("#")
        if not path:
            assert anchor in heading_anchors(text), f"{doc.name}: no heading for #{anchor}"
            continue
        linked = doc.parent / path
        assert linked.exists(), f"{doc.name}: {target} does not resolve"
        if anchor and linked.suffix == ".md":
            found = heading_anchors(linked.read_text(encoding="utf-8"))
            assert anchor in found, f"{doc.name}: {path} has no heading for #{anchor}"


def test_the_link_check_looks_for_the_anchor_in_the_linked_file() -> None:
    found = heading_anchors(OPENCODE_README.read_text(encoding="utf-8"))
    assert "install" in found
    assert "no-such-heading" not in found


def readme_images() -> tuple[list[tuple[str, str]], list[str]]:
    """The README's markdown images as (alt, target) and its HTML <img> tags."""
    text = readme()
    return re.findall(r"!\[([^\]]*)\]\(([^)\s]+)\)", text), re.findall(r"<img\b[^>]*>", text)


def test_every_readme_image_has_alt_text() -> None:
    markdown_images, html_images = readme_images()
    assert markdown_images or html_images, "the README shows no image"
    for alt, target in markdown_images:
        assert alt.strip(), f"markdown image {target} has no alt text"
    for tag in html_images:
        assert re.search(r'\balt="[^"]*"', tag), f"{tag} has no alt attribute"


def test_every_local_readme_image_source_resolves() -> None:
    markdown_images, html_images = readme_images()
    sources = [target for _, target in markdown_images]
    sources += [src for tag in html_images for src in re.findall(r'\bsrc="([^"]+)"', tag)]
    sources += re.findall(r'\bsrcset="([^"]+)"', readme())
    local = [source for source in sources if not re.match(r"[a-z][a-z0-9+.-]*:", source)]
    assert local, "the README shows no image from the repo"
    for source in local:
        assert (REPO / source).is_file(), f"{source} does not resolve"


BRAND_GLYPHS = ("✦", "✧", "⋆", "☾", "🕯️")


def section_lead_lines() -> dict[str, str]:
    """The first line of body text under each `##` heading, outside code fences and HTML."""
    leads: dict[str, str] = {}
    heading, fenced = "", False
    for line in readme().splitlines():
        if line.startswith("```"):
            fenced = not fenced
        elif line.startswith("## "):
            heading = line[3:]
        elif heading and heading not in leads and not fenced and line.strip():
            if not line.lstrip().startswith("<"):
                leads[heading] = re.sub(r"^\s*(?:[-*]|\d+\.)\s+", "", line)
    return leads


def test_every_section_lead_line_opens_with_a_brand_glyph() -> None:
    leads = section_lead_lines()
    assert len(leads) == len(re.findall(r"^## ", readme(), re.MULTILINE))
    for heading, lead in leads.items():
        assert lead.startswith(BRAND_GLYPHS), f"{heading}: {lead}"


def test_brand_glyphs_stay_out_of_headings_and_code() -> None:
    text = readme()
    headings = re.findall(r"^#+ +(.+)$", text, re.MULTILINE)
    code = re.findall(r"^```.*?^```", text, re.MULTILINE | re.DOTALL)
    code += re.findall(r"`([^`\n]+)`", text)
    for chunk in headings + code:
        assert not any(glyph in chunk for glyph in BRAND_GLYPHS), chunk


def test_the_readme_has_the_sections_the_spec_promises() -> None:
    headings = {h.lower() for h in re.findall(r"^#+ +(.+)$", readme(), re.MULTILINE)}
    for wanted in (
        "requirements",
        "install",
        "setup",
        "walkthrough",
        "cost",
        "code standard",
        "memory",
        "uninstall",
        "known gaps",
        "credits",
        "license",
    ):
        assert any(wanted in heading for heading in headings), wanted


def test_the_install_steps_are_the_four_in_the_spec() -> None:
    text = readme()
    steps = [
        "claude plugin marketplace add WitchyNibbles/coven",
        "claude plugin install superpowers@claude-plugins-official",
        "claude plugin install coven@coven",
        "/coven:setup",
    ]
    positions = [text.find(step) for step in steps]
    assert -1 not in positions, steps
    assert positions == sorted(positions)


def test_the_readme_states_the_requirements_and_cautions_plainly() -> None:
    text = readme()
    for needle in (
        "2.1.224",
        "research preview",
        "3.12",
        "private",  # the memory remote
        "default 2",  # the project manager cap
        "opus",
        "2-space",  # setup rewrites settings JSON with this indent
        "backup",
        "Windows",
        "coven setup --undo",
        "docs/known-gaps.md",
        "examples/toy",
    ):
        assert needle in text, needle


def test_the_walkthrough_clones_coven_before_it_copies_the_toy() -> None:
    text = readme()
    clone = text.index("git clone https://github.com/WitchyNibbles/coven")
    assert clone < text.index("cp -r coven/examples/toy")
    assert "cp -r examples/toy" not in text
    setup = (REPO / "skills/setup/SKILL.md").read_text(encoding="utf-8")
    assert "https://github.com/WitchyNibbles/coven" in setup
    assert "coven/examples/toy" in setup


def test_the_readme_says_how_to_use_the_hierarchy_commands() -> None:
    text = readme()
    assert "Not in this version" not in text
    for usage in (
        "coven pm launch <code> --repo <repo> --brief <file>",
        "coven pm resume <code> --gm <name> [--add <file>]...",
        "coven pm health --gm <name>",
    ):
        assert usage in text, usage
    assert "trust prompt" in text


def test_the_readme_lists_every_deny_rule_setup_adds() -> None:
    text = readme()
    for rule in settings.DENY_RULES:
        target = rule.removeprefix("Read(").removesuffix(")")
        assert target in text, rule


def test_the_readme_names_the_git_identity_and_what_undo_removes() -> None:
    text = readme()
    for needle in ("git config --global user.name", "git config --global user.email"):
        assert needle in text, needle
    assert "shim" in text.split("## Uninstall")[1].split("## Known gaps")[0]


def test_the_readme_says_what_installing_changes_and_how_to_switch_it_off() -> None:
    text = readme()
    section = text.split("## What changes when you install")[1].split("\n## ")[0]
    for needle in (
        "every Claude Code session",
        "project-standard",
        "co-author",
        "deny-secrets",
        "PM sessions",
        "claude plugin disable coven@coven",
        "coven setup --undo",
    ):
        assert needle in section, needle


def section(text: str, heading: str) -> str:
    return text.split(f"\n## {heading}")[1].split("\n## ")[0]


def test_the_readme_has_a_short_production_guard_section() -> None:
    body = section(readme(), "Production guard")
    for needle in (
        "systems-map",
        "coven setup",
        "coven approve",
        "--revoke",
        "coven guard explain",
        "coven guard check",
        "coven doctor",
        "known gaps",
        "your own terminal",
        "--file",
    ):
        assert needle in body, needle
    assert len(body.splitlines()) <= 30


def test_the_memory_section_mentions_adopt() -> None:
    assert "coven memory adopt" in section(readme(), "Memory store")


def known_gaps() -> str:
    return (REPO / "docs/known-gaps.md").read_text(encoding="utf-8")


def test_known_gaps_says_what_the_shell_fallback_does_on_an_old_python() -> None:
    text = " ".join(known_gaps().split())
    assert "only deny-secrets still runs there" not in text
    assert "degraded mode" in text
    fallback = " ".join(section(known_gaps(), "Production guard").split())
    assert "degraded mode" in fallback  # also said once in the Production guard section
    assert fallback.count("degraded mode") == 1
    older = text.split("on Python older than 3.12", 1)[1].split("Install Python 3.12", 1)[0]
    for needle in ("fail-closed.sh", "refuses Bash and MCP", "while the guard is on", "role"):
        assert needle in older, needle


def test_known_gaps_lists_the_production_guards_limits() -> None:
    body = section(known_gaps(), "Production guard")
    for needle in (
        "best effort",
        "`*` matcher",
        "from the docs, not live",
        "a loop",
        "a variable assignment",
        "xargs",
        "$(...)",
        "backticks",
        "python3 -c",
        "perl -e",
        "later one",
        "--plugin-dir",
        "git clean",
        "git mv",
        ".cla*",
        "HOME, CLAUDE_CONFIG_DIR, CLAUDE_PLUGIN_ROOT and XDG_CONFIG_HOME",
        "127",
        "cd ~/.config/git && ls co*",
        "cat",
        ".claude/settings.json",
        "symlink",
        "explain",
        "exits 1",
        "verdict line",
        "terminal",
        "inline `coven guard explain <command>`",
        "use `--file`",
    ):
        assert needle in body, needle


def test_known_gaps_lists_what_adopt_does_not_cover() -> None:
    body = section(known_gaps(), "coven memory adopt")
    for needle in ("INDEX.md", "rebuilt", "backup", "mid-commit", "dirty", "case-insensitive"):
        assert needle in body, needle


def test_the_changelog_lists_the_guard_work_in_0_2_0() -> None:
    text = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    entry = text.split("## 0.2.0 ")[1].split("\n## ")[0]
    for needle in (
        "production guard",
        "coven guard check",
        "coven guard explain",
        "coven approve",
        "doctor",
        "coven memory adopt",
        "systems-map",
        "hook",
        "coven memory init",
    ):
        assert needle in entry, needle


def test_the_readme_says_how_to_run_on_opencode() -> None:
    text = readme()
    headings = re.findall(r"^#+ +(.+)$", text, re.MULTILINE)
    assert any("OpenCode" in heading for heading in headings)
    assert "opencode/README.md#install" in text
    assert "works with Claude Code only" not in text


def flat(text: str) -> str:
    return " ".join(text.split())


def test_the_opencode_install_section_has_each_paragraph() -> None:
    body = flat(section(OPENCODE_README.read_text(encoding="utf-8"), "Install"))
    for needle in (
        "Python 3.12 or later",
        "npm not found on PATH",  # the requirement
        "git clone",
        "bin/coven",
        "only `src/`",  # why a clone, not a wheel
        "falls back to the clone",  # the shim works without Claude Code
        "`covenRoot`",  # moving the clone
        "`coven root` check fails",
        "regenerates from `profile.json`",
        "does not end in `/v1`",  # the base URL warning
        "copies from that v7.0.2 clone",  # superpowers: the pinned clone comes first
        "a step that loads one is skipped",  # superpowers with neither source
        "`superpowers` check warns",
        "memory_path",
        "opencode-harness",
        "installs into a repo's `.opencode/`",
        "OPENCODE_DISABLE_PROJECT_CONFIG=1",
        "docs/known-gaps.md",
    ):
        assert needle in body, needle
    assert not re.search(r"\d{4}-\d{2}-\d{2}", body), "the steps carry a date"


def test_the_readme_requirements_name_node_npm_and_git_for_opencode() -> None:
    body = section(readme(), "Requirements")
    assert "For OpenCode: `node` and `npm`, and `git` for the superpowers fetch" in body
    assert "opencode/README.md#install" in body


def test_the_changelog_counts_the_superpowers_skills_setup_copies() -> None:
    text = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    entry = flat(text.split("## 0.2.0 ")[1].split("\n## ")[0])
    assert "eight superpowers skills" in entry


def bullet_about(topic: str) -> str:
    """The one bullet of known-gaps.md's OpenCode section that mentions `topic`."""
    body = section(known_gaps(), "OpenCode")
    found = [flat(bullet) for bullet in body.split("\n- ")[1:] if topic in bullet]
    assert len(found) == 1, f"{topic}: {len(found)} bullets"
    return found[0]


def test_known_gaps_covers_opencode() -> None:
    text = known_gaps()
    assert "Only Claude Code is supported." not in text
    assert "OpenCode 2.3.2 is partly supported through `coven opencode`" in flat(text)
    headings = re.findall(r"^## +(.+)$", text, re.MULTILINE)
    assert any("OpenCode" in heading for heading in headings)


def test_known_gaps_says_what_opencode_does_not_have() -> None:
    assert "GM/PM hierarchy" not in known_gaps()  # built in M2; its bullet is gone
    assert "Only the deny-secrets guard runs" not in known_gaps()
    later = bullet_about("guard-on flow")
    assert "M3" in later
    assert "approvals" in later.lower()
    guard = bullet_about("production guard")
    assert "planned" not in guard  # only M3 and the isolation follow-up are planned
    assert "aquelarre" in guard.lower()
    for needle in ("server guard", "pm-permissions", "deny-only", "guard.json", "no targets"):
        assert needle in guard, needle
    assert "only barrier." not in guard  # too wide: the restricted rules apply to a PM tree
    for needle in ("production step", "restricted rules", "not committed"):
        assert needle in guard, needle


def test_known_gaps_says_what_archive_does_on_opencode_2_3_2() -> None:
    archive = bullet_about("coven.archived")
    for needle in (
        "no plugin-side archive",
        "`coven_pm_stop` with `archive: true`",
        "hides a PM from `coven_pm_list`",
        "does not archive the session in OpenCode",
        "`archived: true`",
        "unarchive it there before a resume",
    ):
        assert needle in archive, needle


def test_known_gaps_lists_the_limits_of_pm_shell_isolation() -> None:
    isolation = bullet_about("OS isolation")
    assert "planned follow-up" not in isolation
    assert "not built" not in isolation
    for needle in (
        "PID and mount namespaces",
        "There is no network namespace",
        "Non-shell tools",
        "server process",
        "mixed race",
        "moves a timed-out shell to the background",
        "until `coven_pm_stop` of that PM",
        "have no record and are not reached by a stop",
        "Linux only",
        "check-then-kill is not atomic",
    ):
        assert needle in isolation, needle


def test_the_opencode_readme_has_a_hierarchy_section() -> None:
    body = flat(section(OPENCODE_README.read_text(encoding="utf-8"), "Hierarchy"))
    for needle in (
        "coven opencode serve start",
        "coven opencode serve status",
        "coven opencode serve stop",
        "coven opencode gm",
        "`coven_gm_claim`",
        "`coven_pm_launch {",
        "`coven_pm_list {",
        "`coven_send {",
        "`coven_pm_resume {",
        "`coven_pm_stop {",
        "`project-manager`",
        "[coven notice]",
        "No guard",
        "until M3 a production step on OpenCode is always run by the GM, after the user's yes",
        "`systems/guard.json`",
        "has no targets",
        "deny-only",
    ):
        assert needle in body, needle


def test_the_hierarchy_section_says_what_archive_does_and_what_the_guards_keep() -> None:
    body = flat(section(OPENCODE_README.read_text(encoding="utf-8"), "Hierarchy"))
    for needle in (
        "`coven.archived`",
        "does not archive the session in OpenCode",
        "`coven_pm_list` with `archived: true`",
        "unarchive it there before a resume",
        "own checkout is read-only to every session",
        "`.claude/worktrees/`",
        "refuses while a server runs",
    ):
        assert needle in body, needle


def test_the_hierarchy_section_says_what_the_production_guard_does_without_targets() -> None:
    body = flat(section(OPENCODE_README.read_text(encoding="utf-8"), "Hierarchy"))
    assert "refuses nothing" not in body  # false: the restricted rules still apply
    for needle in (
        'With no `guard.json` in the store (and no `"guard": "required"` in coven\'s config) '
        "the production guard is off",
        "`guard.json` with no targets counts nothing as a production target",
        "a PM tree still meets the restricted rules",
        "a change of the session role variable",
        "`coven setup`, `coven approve` and `coven memory`",
        "ssh, psql, curl and the like whose host is not written out",
        "`guard.json` that is in the store's working tree but not committed",
        "refuse every Bash and MCP call of a PM",
    ):
        assert needle in body, needle


def test_the_hierarchy_section_says_how_pm_shells_are_isolated() -> None:
    body = flat(section(OPENCODE_README.read_text(encoding="utf-8"), "Hierarchy"))
    assert "a stop only interrupts" not in body
    for needle in (
        "new user, PID and mount namespaces",
        "empty tmpfs",
        "The GM's shells and your own `!` shells are not isolated",
        "git, `gh` and the network work as before",
        "No setting turns isolation off",
        "coven: PM shells run isolated and isolation is not available here: "
        "<missing piece>; ask the user",
        "`PM shell isolation: ok`",
        "`not available: <piece>`",
        "`coven_pm_stop {code, archive?}`: interrupts the PM's turn, which also ends a waiting ask,",
        "kills the isolation trees its shells started",
        "; killed N isolation tree(s)",
        "; skipped M stale record(s)",
        "; M tree(s) still running after SIGKILL",
        "mixed race",
        "docs/known-gaps.md#opencode",
    ):
        assert needle in body, needle


def test_the_hierarchy_section_lists_the_gm_only_tools() -> None:
    body = flat(section(OPENCODE_README.read_text(encoding="utf-8"), "Hierarchy"))
    assert "Every tool but `coven_send` refuses" not in body
    for needle in (
        "`coven_pm_launch`, `coven_pm_list`, `coven_pm_resume` and `coven_pm_stop` refuse any "
        "session but the GM",
        "`coven_gm_claim` refuses a child session and a PM",
        "`coven_send` serves the GM and a PM",
    ):
        assert needle in body, needle


def test_the_changelog_lists_the_opencode_hierarchy_under_unreleased() -> None:
    text = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    assert text.index("## Unreleased") < text.index("## 0.2.0 (2026-11-25)")
    between = text.split("## Unreleased", 1)[1].split("## 0.2.0 (2026-11-25)", 1)[0]
    assert "\n## " not in between  # one section, directly above 0.2.0
    entry = flat(between)
    for needle in (
        "coven opencode serve",
        "coven opencode gm",
        "coven_pm_launch",
        "project-manager",
        "server guard",
        "deny-only",
        "opencode/README.md#hierarchy",
        "PID and mount namespaces",
        "`coven_pm_stop` also kills",
        "`PM shell isolation`",
    ):
        assert needle in entry, needle
    released = text.split("## 0.2.0 (2026-11-25)", 1)[1]
    assert "coven_pm_launch" not in released  # the released section stays as it was


def test_known_gaps_says_where_the_opencode_superpowers_come_from() -> None:
    skills = bullet_about("Superpowers skills")
    assert "v7.0.2 clone that `coven opencode install` fetches" in skills
    assert "Claude Code's superpowers plugin" in skills


def test_known_gaps_names_the_opencode_context_limit_and_the_clone() -> None:
    limits = bullet_about("200000")
    assert "OpenCode 2.3.2 assumes 200000 tokens of context" in limits
    assert "setup --limit ID=CONTEXT[:OUTPUT]`" in limits
    clone = bullet_about("git clone")
    assert "not an installed wheel" in clone
    assert "`opencode/` and `hooks/` must sit beside `src/`" in clone
    assert "`covenRoot`" in clone
