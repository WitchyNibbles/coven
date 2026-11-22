import re
import tomllib

from support import REPO

from coven import claude_version
from coven.project import drift

STANDARD = REPO / "standard"
# What a Python repo gets in `.coven/`, with the folder of `standard/` each file comes from.
INSTALLED = {
    "check.py": "",
    "check_mod.py": "",
    "check_python.py": "",
    "check_repo.py": "",
    "check_stages.py": "",
    "check_ts.py": "",
    "eslint.config.mjs": "typescript",
    "ruff.toml": "python",
    "tsconfig.base.json": "typescript",
}


def test_dogfood_coven_dir_matches_standard():
    installed = sorted(p.name for p in (REPO / ".coven").iterdir() if p.is_file())
    assert installed == sorted(INSTALLED)
    for name in installed:
        assert (REPO / ".coven" / name).read_bytes() == (
            STANDARD / INSTALLED[name] / name
        ).read_bytes(), name


def test_dogfood_workflow_matches_standard():
    assert (REPO / ".github/workflows/coven-check.yml").read_bytes() == (
        STANDARD / "workflows/coven-check.yml"
    ).read_bytes()


def test_dogfood_repo_has_no_drift():
    assert drift(REPO) == []


def test_dogfood_ruff_extends_the_installed_config():
    pyproject = tomllib.loads((REPO / "pyproject.toml").read_text())
    assert pyproject["tool"]["ruff"]["extend"] == ".coven/ruff.toml"


def test_workflow_template_runs_the_installed_check():
    text = (STANDARD / "workflows/coven-check.yml").read_text()
    assert "python3 .coven/check.py install" in text
    assert "python3 .coven/check.py full" in text


MOD_MANIFEST = "hashFiles('**/.claude-plugin/plugin.json') != ''"


def workflow_steps() -> list[str]:
    """The `check` job's steps as text blocks, in order."""
    text = (STANDARD / "workflows/coven-check.yml").read_text()
    return re.split(r"^      - ", text, flags=re.MULTILINE)[1:]


def step_named(name: str) -> str:
    return next(step for step in workflow_steps() if f"name: {name}" in step)


def test_workflow_installs_claude_code_at_the_tested_version_when_a_mod_exists():
    text = (STANDARD / "workflows/coven-check.yml").read_text()
    pinned = claude_version.dotted(claude_version.LAST_TESTED)
    assert re.search(rf'^    env:\n      CLAUDE_CODE_VERSION: "{re.escape(pinned)}"$', text, re.M)
    install = step_named("Install Claude Code")
    assert f"if: {MOD_MANIFEST}" in install
    assert 'npm install -g "@anthropic-ai/claude-code@$CLAUDE_CODE_VERSION"' in install


def test_workflow_installs_claude_code_after_node_and_before_the_dependencies():
    names = [step.split("\n")[0] for step in workflow_steps()]
    node = next(i for i, step in enumerate(workflow_steps()) if "actions/setup-node@" in step)
    install = names.index("name: Install Claude Code")
    assert node < install < names.index("name: Install locked dependencies")


def test_workflow_sets_node_up_for_a_package_or_a_plugin_manifest():
    node = next(step for step in workflow_steps() if "actions/setup-node@" in step)
    assert (
        "if: hashFiles('**/package.json') != '' || hashFiles('**/.claude-plugin/plugin.json') != ''"
        in node
    )
