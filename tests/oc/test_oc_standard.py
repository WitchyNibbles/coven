"""The standard's check never needs the `claude` binary on the paths OpenCode runs."""

import ast
from pathlib import Path

from support import REPO

# The modules a Python or TypeScript repo's fast and full runs load. `check_mod.py` is loaded only
# for a repo that has a mod (a `.claude-plugin/plugin.json`), whose own tests need `claude`.
RUN_MODULES = ("check.py", "check_python.py", "check_repo.py", "check_stages.py", "check_ts.py")
SPAWNERS = {"run", "Popen", "call", "check_call", "check_output", "system"}


def _name(func: ast.expr) -> str:
    return func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")


def _is_claude(node: ast.expr) -> bool:
    if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
        return False
    words = node.value.split()
    return bool(words) and words[0] == "claude"


def claude_commands(source: str) -> list[int]:
    """Line numbers where the code starts `claude`: a command list led by it, or a string passed to a spawner."""
    lines = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.List, ast.Tuple)) and node.elts and _is_claude(node.elts[0]):
            lines.append(node.lineno)
        if isinstance(node, ast.Call) and _name(node.func) in SPAWNERS:
            lines.extend(arg.lineno for arg in node.args if _is_claude(arg))
    return lines


def test_the_detector_sees_a_claude_command() -> None:
    assert claude_commands('subprocess.run(["claude", "plugin", "test"])') == [1]
    assert claude_commands('os.system("claude plugin test")') == [1]
    assert claude_commands('print("claude not found on PATH")') == []


def test_checks_need_no_claude() -> None:
    sources = [REPO / ".coven" / name for name in RUN_MODULES]
    sources += [REPO / "standard" / name for name in RUN_MODULES]
    found = {
        f"{path.relative_to(REPO)}:{line}"
        for path in sources
        for line in claude_commands(Path(path).read_text())
    }
    assert not found
