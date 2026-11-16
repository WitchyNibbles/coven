"""The README keeps its promises: its commands exist, its links resolve, its install steps stand."""

import re
from pathlib import Path

import pytest
from support import REPO
from test_skills_text import cited_invocations, unknown_parts

from coven import settings

README = REPO / "README.md"
DOCS = (README, REPO / "docs/known-gaps.md", REPO / "CHANGELOG.md")


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


@pytest.mark.parametrize("doc", DOCS, ids=lambda path: path.name)
def test_relative_links_resolve(doc: Path) -> None:
    text = doc.read_text(encoding="utf-8")
    links = re.findall(r"(?<!!)\[[^\]]*\]\(([^)\s]+)\)", text)
    anchors = {github_slug(h) for h in re.findall(r"^#+ +(.+)$", text, re.MULTILINE)}
    for target in links:
        if re.match(r"[a-z][a-z0-9+.-]*:", target):  # https:, mailto:
            continue
        path, _, anchor = target.partition("#")
        if not path:
            assert anchor in anchors, f"{doc.name}: no heading for #{anchor}"
            continue
        assert (doc.parent / path).exists(), f"{doc.name}: {target} does not resolve"


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
