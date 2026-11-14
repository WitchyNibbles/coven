"""Constants and helpers shared by the tests; fixtures live in the conftest files."""

import fcntl
import os
import shutil
import subprocess
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
# The skills the spec ships (its "Skills" table); a test keeps this equal to the `skills/` folders.
SHIPPED_SKILLS = frozenset(
    {
        "setup",
        "manager",
        "shared-memory",
        "handoff",
        "project-standard",
        "clean-code",
        "ceo-review",
        "office-hours",
        "grill-me",
        "subagent-driven-development",
        "requesting-code-review",
    }
)
CHECK = REPO / "standard/check.py"
# The standard's Python modules: `check.py` and the siblings it loads from its own directory.
CHECK_MODULES = ("check.py", "check_python.py", "check_repo.py", "check_stages.py", "check_ts.py")


# The `check_env` a module returns to run the real runner: `run_check` copies it, never edits it.
REAL_RUNNER: dict[str, str | None] = {"COVEN_CHECK_RUNNER": None}


def receipt(out: subprocess.CompletedProcess[str]) -> str:
    """The receipt line: the last line `check.py` printed."""
    return out.stdout.splitlines()[-1]


# A pyproject.toml that passes the standard's settings stage (shared by the check tests).
GOOD: dict = {
    "project": {"name": "demo", "requires-python": ">=3.12", "dependencies": []},
    "tool": {
        "ruff": {
            "extend": ".coven/ruff.toml",
            "src": ["src"],
            "target-version": "py312",
            "lint": {"extend-select": ["N"]},
        },
        "mypy": {
            "strict": True,
            "enable_error_code": [
                "ignore-without-code",
                "possibly-undefined",
                "truthy-bool",
                "redundant-expr",
                "exhaustive-match",
            ],
            "files": ["src"],
        },
        "pytest": {"ini_options": {"strict": True, "filterwarnings": ["error"]}},
        "coverage": {"run": {"branch": True}},
    },
}


def _value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(_value(item) for item in value) + "]"
    assert isinstance(value, str)
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return '"' + escaped.replace("\n", "\\n").replace("\r", "\\r") + '"'


def dump_toml(data: dict, prefix: tuple[str, ...] = ()) -> str:
    """A tiny TOML writer: nested tables, strings, booleans and lists of those."""
    scalars = [(k, v) for k, v in data.items() if not isinstance(v, dict)]
    tables = [(k, v) for k, v in data.items() if isinstance(v, dict)]
    out = []
    if prefix:
        out.append("[" + ".".join(f'"{p}"' if "-" in p else p for p in prefix) + "]")
    out.extend(f"{k if '-' not in k else chr(34) + k + chr(34)} = {_value(v)}" for k, v in scalars)
    text = "\n".join(out) + "\n"
    for key, table in tables:
        text += "\n" + dump_toml(table, (*prefix, key))
    return text


CORE = "def add(a: int, b: int) -> int:\n    return a + b\n"
TEST_CORE = "from pkg.core import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n"


def git(root: Path, *args: str) -> str:
    done = subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)
    return done.stdout


def commit_all(root: Path) -> None:
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "change")


@contextmanager
def _exclusive(directory: Path) -> Iterator[None]:
    """Holds `<directory>.lock` exclusively, so parallel workers do the cached work one at a time."""
    lock = directory.with_name(directory.name + ".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)  # released when the file closes, crashes included
        yield


def install_once(home: Path, install: Callable[[Path], None]) -> None:
    """Runs `install(home)` unless `home/installed` says an earlier run finished it.

    Workers that wait for the lock find the marker and skip the work.
    """
    marker = home / "installed"
    with _exclusive(home):
        if not marker.is_file():
            install(home)
            marker.write_text("")


FIXTURES = REPO / "tests/fixtures"


def npm_ci(fixture: Path, home: Path) -> None:
    for file in ("package.json", "package-lock.json"):
        shutil.copy(fixture / file, home / file)
    done = subprocess.run(
        ["npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund"],
        cwd=home,
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode != 0:
        pytest.fail(f"npm ci failed: {done.stderr[-2000:]}")


# A TypeScript project that passes every stage (shared by the TypeScript stage tests). The test
# runner decides the layout: c8 + node:test keeps tests in `test/`, Vitest next to the code.
TS_INDEX = "export function add(a: number, b: number): number {\n  return a + b;\n}\n"
TS_SUB = "export function sub(a: number, b: number): number {\n  return a - b;\n}\n"
TS_TSCONFIG = {
    "c8": (
        '{{\n  "extends": ["{base}"],\n  "compilerOptions": {{\n    "target": "es2022",\n'
        '    "module": "nodenext",\n    "noEmit": true,\n    "allowJs": true,\n'
        '    "types": ["node"],\n'
        '    "allowImportingTsExtensions": true,\n    "erasableSyntaxOnly": true\n  }},\n'
        '  "include": ["src", "test", "eslint.config.mjs"]\n}}\n'
    ),
    "vitest": (
        '{{\n  "extends": ["{base}"],\n  "compilerOptions": {{\n    "target": "es2022",\n'
        '    "module": "nodenext",\n    "noEmit": true,\n    "allowJs": true\n  }},\n'
        '  "include": ["src", "eslint.config.mjs", "vitest.config.mjs"]\n}}\n'
    ),
}
TS_FIXTURES = {"c8": "ts-project", "vitest": "ts-vitest"}
# Config files are linted too, so the tsconfig compiles them. The base types its
# own export, so a plain spread is not an unsafe `any` even where .coven/ cannot resolve
# typescript-eslint (a project below the repo root).
VITEST_CONFIG = 'export default {\n  test: { coverage: { include: ["src/**"] } },\n};\n'
ESLINT_CONFIG = 'import coven from "{up}/eslint.config.mjs";\n\nexport default [{own}...coven];\n'
# A project at the repo root keeps the standard's own files out of its lint (`eslint .`).
ROOT_IGNORES = '{ ignores: [".coven/"] }, '


def ts_test(runner: str, function: str, args: str, result: int) -> tuple[str, str]:
    """A test file for `function` (from src/<function>.ts): its path in the project and its text."""
    if runner == "vitest":
        return f"src/{function}.test.ts", (
            'import { expect, test } from "vitest";\n\n'
            f'import {{ {function} }} from "./{function}.js";\n\n'
            f'test("{function}", () => {{\n  expect({function}({args})).toBe({result});\n}});\n'
        )
    return f"test/{function}.test.ts", (
        'import assert from "node:assert/strict";\nimport { test } from "node:test";\n\n'
        f'import {{ {function} }} from "../src/{function}.ts";\n\n'
        f'void test("{function}", () => {{\n  assert.equal({function}({args}), {result});\n}});\n'
    )


def write_ts_project(
    app: Path, node_modules: Path, repo: Path, runner: str = "c8", *, check_js: bool = False
) -> None:
    """A TypeScript project at `app` that passes every stage, using `repo`'s one `.coven/`.

    `node_modules` (installed from the runner's fixture) is linked in and ignored by git.
    `check_js` makes tsc type-check the JavaScript files too, the standard's ESLint base included.
    """
    (repo / ".coven").mkdir(exist_ok=True)
    for name in ("tsconfig.base.json", "eslint.config.mjs"):
        shutil.copy(REPO / "standard/typescript" / name, repo / ".coven" / name)
    up = Path(os.path.relpath(repo / ".coven", app)).as_posix()
    up = up if up.startswith("../") else f"./{up}"  # a bare `.coven/...` names a package
    (app / "src").mkdir(parents=True)
    for name in ("package.json", "package-lock.json"):
        shutil.copy(FIXTURES / TS_FIXTURES[runner] / name, app / name)
    tsconfig = TS_TSCONFIG[runner].format(base=f"{up}/tsconfig.base.json")
    if check_js:
        tsconfig = tsconfig.replace('"allowJs": true', '"allowJs": true,\n    "checkJs": true')
    (app / "tsconfig.json").write_text(tsconfig)
    own = ROOT_IGNORES if app == repo else ""
    (app / "eslint.config.mjs").write_text(ESLINT_CONFIG.format(up=up, own=own))
    (app / "src/add.ts").write_text(TS_INDEX)
    path, text = ts_test(runner, "add", "1, 2", 3)
    (app / path).parent.mkdir(exist_ok=True)
    (app / path).write_text(text)
    if runner == "vitest":
        (app / "vitest.config.mjs").write_text(VITEST_CONFIG)
    (app / "node_modules").symlink_to(node_modules, target_is_directory=True)
    ignore = repo / ".gitignore"
    ignore.write_text((ignore.read_text() if ignore.is_file() else "") + "node_modules\n")
