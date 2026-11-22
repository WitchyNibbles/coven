"""The plugin's manifest, marketplace, hook wiring and agent definitions."""

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from support import REPO, commit_all, git

PLUGIN_JSON = REPO / ".claude-plugin" / "plugin.json"
MARKETPLACE_JSON = REPO / ".claude-plugin" / "marketplace.json"
AQUELARRE = REPO / "plugins" / "aquelarre"
AQUELARRE_JSON = AQUELARRE / ".claude-plugin" / "plugin.json"
HOOKS_JSON = REPO / "hooks" / "hooks.json"
AGENTS = REPO / "agents"
SKILLS = REPO / "skills"

# Top-level keys the docs list for plugin.json and marketplace.json: an unknown one is stripped
# or ignored by Claude Code, so a typo would load silently.
PLUGIN_KEYS = {"name", "version", "description", "author", "license", "homepage", "dependencies"}
MARKETPLACE_KEYS = {
    "name",
    "owner",
    "plugins",
    "description",
    "allowCrossMarketplaceDependenciesOn",
}

PRE_TOOL_USE_TOOLS = "Read|Grep|Glob|Bash|Write|Edit|NotebookEdit|MultiEdit"
SESSION_START_MATCHER = "startup|resume|clear|compact|fork"
ROOT = "${CLAUDE_PLUGIN_ROOT}"

# name -> (model, effort, disallowedTools), the spec's "Agents" table.
AGENT_TABLE = {
    "explorer": ("haiku", "medium", "Write, Edit, NotebookEdit"),
    "implementer": ("sonnet", "high", "Agent"),
    "reviewer": ("sonnet", "high", "Write, Edit, NotebookEdit"),
    "final-reviewer": ("opus", "high", "Write, Edit, NotebookEdit"),
    "project-manager": ("opus", "high", "AskUserQuestion"),
}
# Claude Code reads these in a plugin agent; permissionMode, hooks, mcpServers and initialPrompt
# are ignored there, so a file that sets one promises something it cannot deliver.
SUPPORTED_AGENT_KEYS = {"name", "description", "model", "effort", "disallowedTools", "tools"}


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_hook(name: str) -> ModuleType:
    """Import hooks/<name>.py by path: no bytecode is written, sys.path is restored and the loaded
    module is not registered in sys.modules. Modules it imports itself stay loaded."""
    path = REPO / "hooks" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"coven_test_{name}", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    path_before, bytecode_before = list(sys.path), sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path[:] = path_before
        sys.dont_write_bytecode = bytecode_before
    return module


def split_agent(name: str) -> tuple[dict[str, str], str]:
    """The `key: value` frontmatter of agents/<name>.md and its body."""
    text = (AGENTS / f"{name}.md").read_text(encoding="utf-8")
    match = re.fullmatch(r"---\n(.*?)\n---\n(.*)", text, re.DOTALL)
    assert match, f"{name}.md has no frontmatter"
    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        key, sep, value = line.partition(": ")
        assert sep, f"{name}.md frontmatter line is not `key: value`: {line!r}"
        fields[key] = value
    return fields, match.group(2)


def hook_commands(event: str) -> list[tuple[str, str]]:
    """(matcher, command) for every command hook wired to the event."""
    groups = load(HOOKS_JSON)["hooks"][event]
    return [
        (group.get("matcher", ""), hook["command"]) for group in groups for hook in group["hooks"]
    ]


def test_manifests_parse_and_match_docs_fields() -> None:
    plugin = load(PLUGIN_JSON)
    assert set(plugin) <= PLUGIN_KEYS
    assert plugin["name"] == "coven"
    assert plugin["version"] == "0.1.0"
    assert plugin["description"].strip()
    assert plugin["author"] == {"name": "WitchyNibbles"}
    assert plugin["license"] == "MIT"
    assert plugin["homepage"] == "https://github.com/WitchyNibbles/coven"
    assert plugin["dependencies"] == ["superpowers@claude-plugins-official", "aquelarre"]

    marketplace = load(MARKETPLACE_JSON)
    assert set(marketplace) <= MARKETPLACE_KEYS
    assert marketplace["name"] == "coven"
    assert marketplace["owner"] == {"name": "WitchyNibbles"}
    assert marketplace["allowCrossMarketplaceDependenciesOn"] == ["claude-plugins-official"]
    entries = {entry["name"]: entry for entry in marketplace["plugins"]}
    assert set(entries) == {"coven", "aquelarre"}
    assert entries["coven"]["source"] == "./"
    assert entries["aquelarre"]["source"] == "./plugins/aquelarre"


def test_aquelarre_manifest_matches_coven() -> None:
    mod = load(AQUELARRE_JSON)
    assert set(mod) <= PLUGIN_KEYS | {"types", "userConfig"}
    assert mod["name"] == "aquelarre"
    assert mod["version"] == load(PLUGIN_JSON)["version"]
    assert mod["description"].strip()
    assert mod["author"] == {"name": "WitchyNibbles"}
    assert mod["license"] == "MIT"
    assert mod["homepage"] == load(PLUGIN_JSON)["homepage"]
    assert mod["types"] == "./types/index.d.ts"
    assert (AQUELARRE / "hooks" / "hooks.json").is_file()


def test_hooks_json_uses_plugin_root_paths() -> None:
    commands = [
        command for event in load(HOOKS_JSON)["hooks"] for _, command in hook_commands(event)
    ]
    assert len(commands) == 6
    pattern = re.compile(
        rf'sh "{re.escape(ROOT)}/hooks/fail-closed\.sh" "{re.escape(ROOT)}/hooks/(\w+\.py)"(?: --pre-tool-use)?'
    )
    scripts = []
    for command in commands:
        match = pattern.fullmatch(command)
        assert match, f"not wrapped in fail-closed.sh with plugin-root paths: {command}"
        scripts.append(match.group(1))
        assert (REPO / "hooks" / match.group(1)).is_file()
    assert sorted(scripts) == [
        "deny_secrets.py",
        "memory_context.py",
        "pm_permissions.py",
        "pm_permissions.py",
        "prod_guard_gate.py",
        "session_context.py",
    ]


def test_pre_tool_use_runs_deny_secrets_on_every_file_tool() -> None:
    (matcher, command), *_ = hook_commands("PreToolUse")
    assert matcher == PRE_TOOL_USE_TOOLS
    assert command.endswith('hooks/deny_secrets.py"')


def test_deny_secrets_matcher_names_every_tool_the_hook_checks_as_a_writer() -> None:
    (matcher, _), *_ = hook_commands("PreToolUse")
    writers = load_hook("deny_secrets").WRITERS
    assert set(writers) <= set(matcher.split("|"))


def test_pre_tool_use_guards_a_pm_session_on_bash() -> None:
    # The PM profile's ask and deny rules match by command prefix: this guard sees the rest.
    _, (matcher, command), _ = hook_commands("PreToolUse")
    assert matcher == "Bash"
    assert command.endswith('hooks/pm_permissions.py" --pre-tool-use')


def test_pre_tool_use_runs_the_prod_guard_on_every_tool() -> None:
    # It sees MCP tools, Task and tools that do not exist yet: only a catch-all matcher covers them.
    # The entry is the gate, which hands over to prod_guard.py unless the guard is off.
    *_, (matcher, command) = hook_commands("PreToolUse")
    assert matcher == "*"
    assert command == f'sh "{ROOT}/hooks/fail-closed.sh" "{ROOT}/hooks/prod_guard_gate.py"'


def test_permission_request_runs_pm_permissions_for_every_tool() -> None:
    # The hook escalates any tool but Bash itself: a matcher on Bash alone would leave a
    # background PM waiting on every other prompt.
    ((matcher, command),) = hook_commands("PermissionRequest")
    assert matcher in ("", "*")
    assert command.endswith('hooks/pm_permissions.py"')


def test_session_start_matcher() -> None:
    commands = hook_commands("SessionStart")
    assert {matcher for matcher, _ in commands} == {SESSION_START_MATCHER}
    scripts = [command.rsplit("/", 1)[-1].rstrip('"') for _, command in commands]
    assert scripts == ["session_context.py", "memory_context.py"]


def test_every_hook_has_an_explicit_timeout() -> None:
    """Claude Code's default would apply to a hook with none; the wiring says what each may take."""
    timeouts = {
        event: {hook.get("timeout") for group in groups for hook in group["hooks"]}
        for event, groups in load(HOOKS_JSON)["hooks"].items()
    }
    assert timeouts == {
        "PreToolUse": {30, 15},
        "PermissionRequest": {15},
        "SessionStart": {30},
    }


def run_wired(
    event: str, payload: dict[str, object], *, index: int = 0, **env: str
) -> subprocess.CompletedProcess[str]:
    """Run the event's `index`th hook command the way Claude Code does, with the plugin root
    set."""
    _, command = hook_commands(event)[index]
    return subprocess.run(
        ["/bin/sh", "-c", command],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "CLAUDE_PLUGIN_ROOT": str(REPO), **env},
    )


def test_wired_deny_secrets_blocks_an_env_file_and_allows_a_readme(tmp_path: Path) -> None:
    def read(name: str) -> subprocess.CompletedProcess[str]:
        payload = {"tool_name": "Read", "tool_input": {"file_path": str(tmp_path / name)}}
        return run_wired("PreToolUse", payload)

    assert read(".env").returncode == 2
    assert read("README.md").returncode == 0


def test_wired_pm_permissions_decides_only_for_a_pm_session(tmp_path: Path) -> None:
    payload = {"tool_name": "WebFetch", "tool_input": {}, "cwd": str(tmp_path)}
    plain = run_wired("PermissionRequest", payload)
    assert (plain.returncode, plain.stdout) == (0, "")
    pm = run_wired("PermissionRequest", payload, COVEN_SESSION_ROLE="pm")
    assert pm.returncode == 0
    decision = json.loads(pm.stdout)["hookSpecificOutput"]["decision"]
    assert decision["behavior"] == "deny"


def test_wired_prod_guard_is_off_without_a_guard_and_refuses_a_target_with_one(
    tmp_path: Path,
) -> None:
    payload = {"tool_name": "Bash", "tool_input": {"command": "psql -h orders-db"}, "cwd": "/tmp"}
    assert run_wired("PreToolUse", payload, index=2).returncode == 0
    store = tmp_path / "coven-memory"
    (store / "systems").mkdir(parents=True)
    guard = {"targets": [{"system": "orders-db", "kind": "production", "match": ["orders-db"]}]}
    (store / "systems/guard.json").write_text(json.dumps(guard))
    git(store, "init", "-q")
    commit_all(store)
    config = Path(os.environ["HOME"]) / ".config" / "coven"
    config.mkdir(parents=True)
    (config / "config.json").write_text(json.dumps({"memory_path": str(store)}))
    refused = run_wired("PreToolUse", payload, index=2)
    assert refused.returncode == 2
    assert "coven prod-guard: 'orders-db' is production" in refused.stderr


def test_wired_pm_guard_blocks_a_prefix_bypass_only_in_a_pm_session(tmp_path: Path) -> None:
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": "git -C . push --force"},
        "cwd": str(tmp_path),
    }
    plain = run_wired("PreToolUse", payload, index=1)
    assert (plain.returncode, plain.stdout, plain.stderr) == (0, "", "")
    pm = run_wired("PreToolUse", payload, index=1, COVEN_SESSION_ROLE="pm")
    assert pm.returncode == 2
    assert pm.stderr.startswith("coven pm-permissions:")


def agent_references() -> dict[str, set[Path]]:
    """Each `coven:<name>` that skills, agents and instructions mention, with the files naming it."""
    found: dict[str, set[Path]] = {}
    for folder in (SKILLS, AGENTS, REPO / "instructions"):
        for path in sorted(folder.rglob("*.md")):
            for name in re.findall(r"coven:([a-z][a-z-]*[a-z])", path.read_text(encoding="utf-8")):
                found.setdefault(name, set()).add(path)
    return found


def test_every_agent_named_in_skills_exists() -> None:
    references = agent_references()
    agents = {path.stem for path in AGENTS.glob("*.md")}
    skills = {path.parent.name for path in SKILLS.glob("*/SKILL.md")}
    unresolved = set(references) - agents - skills
    assert not unresolved, {name: sorted(map(str, references[name])) for name in unresolved}
    # Not vacuous: the agent bodies name each other today.
    assert agents & set(references)


def test_agent_files_are_exactly_the_five_the_spec_names() -> None:
    assert {path.stem for path in AGENTS.glob("*.md")} == set(AGENT_TABLE)


@pytest.mark.parametrize("name", AGENT_TABLE)
def test_agent_frontmatter_models(name: str) -> None:
    model, effort, disallowed = AGENT_TABLE[name]
    fields, body = split_agent(name)
    assert fields["name"] == name
    assert fields["description"].strip()
    assert fields["model"] == model
    assert fields["effort"] == effort
    assert fields["disallowedTools"] == disallowed
    assert set(fields) <= SUPPORTED_AGENT_KEYS
    assert body.strip()


TASK_TOOLS = ("TaskCreate", "TaskUpdate", "TaskList")


def test_project_manager_may_use_the_task_tools() -> None:
    """The pane shows the PM's plan from its task files, so the role must be able to write them."""
    fields, _ = split_agent("project-manager")
    blocked = {tool.strip() for tool in fields.get("disallowedTools", "").split(",")}
    assert not blocked & set(TASK_TOOLS)
    if "tools" in fields:  # an allow-list that leaves them out would block them too
        allowed = {tool.strip() for tool in fields["tools"].split(",")}
        assert set(TASK_TOOLS) <= allowed


def test_final_reviewer_has_the_reviewers_body() -> None:
    assert split_agent("final-reviewer")[1] == split_agent("reviewer")[1]


def test_project_manager_is_the_pm_role() -> None:
    body = split_agent("project-manager")[1]
    for prefix in ("QUESTION", "PROD-STEP", "DRAFT", "DONE", "BLOCKED"):
        assert f"`{prefix} <project>/<task>:" in body
    for reply in ("ANSWER", "APPROVED", "DENIED", "TASK"):
        assert f"`{reply} <project>/<task>:" in body
    assert "COVEN_SESSION_ROLE" in body
    for agent in ("explorer", "implementer", "reviewer", "final-reviewer"):
        assert f"`coven:{agent}`" in body
    assert "never run a production step" in body.lower()
    assert "never merge" in body.lower()


def test_project_manager_names_the_pipeline_it_runs() -> None:
    body = split_agent("project-manager")[1]
    assert "the repo's pipeline" not in body
    assert '"Building software" steps' in body


def test_agents_say_nothing_of_the_private_setup() -> None:
    for name in AGENT_TABLE:
        text = (AGENTS / f"{name}.md").read_text(encoding="utf-8")
        assert not re.search(r"/home/|/Users/|~/projects", text)


def test_claude_plugin_validate() -> None:
    """Run the CLI's own validator on the repo, strictly: warnings fail too.

    A skip, not a failure, when `claude` is not installed: the CLI is not a dependency of this
    repo and CI does not install it. `check.py` counts passed tests only, so a skip does not
    change a receipt.
    """
    claude = shutil.which("claude")
    if claude is None:
        pytest.skip("claude is not on PATH, so the plugin was not validated by the CLI")
    done = subprocess.run(
        [claude, "plugin", "validate", str(REPO), "--strict"],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert done.returncode == 0, done.stdout + done.stderr


@pytest.mark.claude
def test_aquelarre_validates_strictly(tmp_path: Path) -> None:
    """The CLI's own validator, strictly (warnings fail, TypeScript errors among them), on the mod."""
    claude = shutil.which("claude")
    assert claude is not None, "claude is not on PATH"
    home = tmp_path / "claude-home"
    config = tmp_path / "claude-config"
    home.mkdir()
    config.mkdir()
    done = subprocess.run(
        [claude, "plugin", "validate", str(AQUELARRE), "--strict"],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
        env={**os.environ, "HOME": str(home), "CLAUDE_CONFIG_DIR": str(config)},
    )
    assert done.returncode == 0, done.stdout + done.stderr
