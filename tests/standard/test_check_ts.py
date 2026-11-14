import importlib.util
import json
import os
import py_compile
import shutil
import subprocess
from pathlib import Path

import pytest
from support import (
    CHECK_MODULES,
    REAL_RUNNER,
    REPO,
    TS_INDEX,
    TS_SUB,
    commit_all,
    git,
    receipt,
    ts_test,
    write_ts_project,
)

STANDARD = REPO / "standard"

# Marker text is assembled from pieces so this file never holds a suppression itself.
ESLINT = "eslint"
MARKERS = [
    ESLINT + "-disable",
    ESLINT + "-disable-next-line no-console",
    ESLINT + "-disable-line",
    ESLINT + "-env node",
    "@ts" + "-ignore",
    "@ts" + "-expect-error",
    "@ts" + "-nocheck",
    "c8 " + "ignore next",
    "v8 " + "ignore next",
    "istanbul " + "ignore next",
    "prettier" + "-ignore",
    "biome" + "-ignore lint/style: reason",
    "deno" + "-lint-ignore no-explicit-any",
    "deno" + "-fmt-ignore",
    "deno" + "-fmt-ignore-file",
    "node:" + "coverage ignore next",
    "node:" + "coverage disable",
    "@" + "noformat",  # Prettier's checkIgnorePragma skips such a file
    "@" + "noprettier",
]


@pytest.fixture(scope="module")
def check_ts(check):
    return check.load_ts()


def lines(check_ts, text: str) -> list[int]:
    return [number for number, _ in check_ts.suppression_lines(text)]


def scan(check, tmp_path: Path, text: str, name: str = "x.ts"):
    path = tmp_path / name
    path.write_text(text)
    return check.load_ts().scan_ts(path)


def test_ts_scan_line_and_block_comments(check, tmp_path):
    text = (
        "const a = 1\n"
        f"// {'@ts' + '-ignore'}\n"
        f"/* {ESLINT}-disable no-console */\n"
        "const b = 2 /* fine */\n"
        f"/*\n * c8 {'ignore'} next\n */\n"
    )
    found = scan(check, tmp_path, text)
    assert [hit.line for hit in found] == [2, 3, 6]
    assert found[0].text == "// @ts" + "-ignore"
    assert found[0].path == tmp_path / "x.ts"


@pytest.mark.parametrize("marker", MARKERS)
def test_ts_scan_finds_every_marker(check_ts, marker):
    assert lines(check_ts, f"// {marker}\n") == [1]
    assert lines(check_ts, f"/* {marker} */\n") == [1]


def test_ts_scan_is_case_insensitive(check_ts):
    assert lines(check_ts, "// " + "@TS" + "-Ignore\n") == [1]
    assert lines(check_ts, "// " + "ESLINT" + "-DISABLE\n") == [1]


@pytest.mark.parametrize(
    "marker",
    [
        "/* " + ESLINT + " no-console: off */",
        "/*" + ESLINT + " no-console: off */",
        "/* " + "global" + " window */",
        "/*" + "globals" + " window */",
        "/* " + ESLINT + "-env browser */",
        "/* " + "exported" + " main */",
        "/*" + "exported" + " main */",
    ],
)
def test_ts_scan_finds_inline_configuration(check_ts, marker):
    assert lines(check_ts, f"{marker}\n") == [1]


def test_ts_scan_counts_strings_too(check_ts):
    # A string, a template literal and a regex literal all count: no comment tokenizer.
    marker = "@ts" + "-ignore"
    text = f"const a = '{marker}'\nconst b = `x ${{1}} {marker}`\nconst c = /{marker}/\n"
    assert lines(check_ts, text) == [1, 2, 3]
    assert lines(check_ts, f'const j = <p>{marker}</p>\n<a title="{marker}" />\n') == [1, 2]


def test_ts_scan_template_literal_with_expression(check_ts):
    text = "const a = `${b} ${`${c}`}`\n" + "// " + "@ts" + "-expect-error\n"
    assert lines(check_ts, text) == [2]


def test_ts_scan_ordinary_text_is_clean(check_ts):
    text = (
        "// the word eslint alone, and global variables\nconst globalThis_ = 1\nlet v8 = 'ignore'\n"
    )
    assert lines(check_ts, text) == []


def test_ts_scan_splits_lines_like_eslint(check_ts):
    marker = "@ts" + "-ignore"
    # CR, LF, CRLF, U+2028 and U+2029 end a line (ESLint, TypeScript); \x0b, \x0c and \x85 do not.
    text = f"a\x0bb\x0cc\x85d\u2028e // {marker}\r\nx\r// {marker}\u2029y\n// {marker}\n"
    assert lines(check_ts, text) == [2, 4, 6]
    assert check_ts.suppression_lines(text)[0] == (2, f"e // {marker}")


@pytest.mark.parametrize(
    "text",
    [
        "/* istanbul\n   " + "ignore next */\n",
        "/*\n" + ESLINT + " no-console: off */\n",
        "/*\n" + "global" + " foo */\n",
        "/* c8\r\n" + "ignore next */\n",
        "/*\u2028" + "exported" + " main */\n",
    ],
)
def test_ts_scan_finds_markers_split_across_lines(check_ts, text):
    # The tools read these markers across line breaks, so the scan does; the hit is where it starts.
    first = text.splitlines()[0].strip()
    assert check_ts.suppression_lines("x\n" + text) == [(2, first)]


def test_ts_scan_reports_a_line_once(check_ts):
    hit = "// " + "@ts" + "-ignore " + ESLINT + "-disable"
    assert check_ts.suppression_lines(f"x\n{hit}\n") == [(2, hit)]


def test_ts_scan_reads_every_typescript_and_javascript_suffix(check, check_repo, tmp_path):
    for suffix in (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs"):
        assert suffix in check_repo.TS_SUFFIXES
        found = scan(check, tmp_path, "// " + "@ts" + "-nocheck\n", f"f{suffix}")
        assert len(found) == 1


def test_ts_scan_undecodable_bytes_still_count(check, tmp_path):
    path = tmp_path / "b.ts"
    path.write_bytes(b"\xff\xfe\n// " + ("@ts" + "-ignore").encode() + b"\n")
    assert [hit.line for hit in check.load_ts().scan_ts(path)] == [2]


# --- the stage, end to end --------------------------------------------------


@pytest.fixture
def check_env() -> dict[str, str | None]:
    """Run the real runner: no in-process override, whatever tools this machine has."""
    return REAL_RUNNER


# A package.json naming a supported test runner, for projects whose tools are never installed.
UNINSTALLED_PACKAGE = '{ "devDependencies": { "c8": "12.0.0" } }\n'


def typescript_repo(root: Path, lockfile: str = "package-lock.json") -> None:
    (root / "package.json").write_text(UNINSTALLED_PACKAGE)
    for name in ("tsconfig.json", lockfile):
        (root / name).write_text("{}")


def test_typescript_stage_counts_and_prints_each_hit(run_check, git_repo):
    typescript_repo(git_repo)
    marker = "@ts" + "-ignore"
    (git_repo / "src").mkdir()
    (git_repo / "src/a.ts").write_text(f"export const a = 1\n// {marker}\n")
    (git_repo / "src/b.jsx").write_text(f"const s = '{marker}'\n")
    commit_all(git_repo)
    out = run_check(git_repo, "fast")
    assert " suppressions=2 " in receipt(out)
    assert f"src/a.ts:2: // {marker}" in out.stdout
    assert f"src/b.jsx:1: const s = '{marker}'" in out.stdout


def test_typescript_stage_skips_node_modules_vendored_and_nested_projects(run_check, git_repo):
    typescript_repo(git_repo)
    marker = "// " + "@ts" + "-ignore\n"
    (git_repo / "node_modules/p").mkdir(parents=True)
    (git_repo / "node_modules/p/i.js").write_text(marker)
    (git_repo / "vendor").mkdir()
    (git_repo / "vendor/v.js").write_text(marker)
    (git_repo / ".gitattributes").write_text("vendor/** linguist-vendored\n")
    (git_repo / "tools").mkdir()
    typescript_repo(git_repo / "tools")
    (git_repo / "tools/t.ts").write_text(marker)
    commit_all(git_repo)
    out = run_check(git_repo, "fast")
    # The nested project counts its own file once; the root counts neither it nor the others.
    assert " projects=2 " in receipt(out)
    assert " suppressions=1 " in receipt(out)
    assert " skipped=1 " in receipt(out)
    assert "\nt.ts:1: " in out.stdout  # printed relative to its own project


def test_check_works_from_a_copy_with_its_sibling(run_check, git_repo):
    typescript_repo(git_repo)
    (git_repo / "a.ts").write_text("// " + "@ts" + "-ignore\n")
    (git_repo / ".coven").mkdir()
    for name in CHECK_MODULES:
        shutil.copy(STANDARD / name, git_repo / ".coven" / name)
    commit_all(git_repo)
    out = run_check(git_repo, "fast", check=git_repo / ".coven/check.py")
    assert " suppressions=1 " in receipt(out)
    assert "not allowed" not in out.stdout


def test_a_copy_without_its_sibling_says_so(run_check, git_repo):
    typescript_repo(git_repo)
    (git_repo / ".coven").mkdir()
    for name in CHECK_MODULES:
        if name != "check_ts.py":
            shutil.copy(STANDARD / name, git_repo / ".coven" / name)
    commit_all(git_repo)
    out = run_check(git_repo, "fast", check=git_repo / ".coven/check.py")
    assert out.returncode == 1
    assert "check_ts.py is missing (re-run coven project)" in out.stdout
    assert " suppressions=0 " in receipt(out)


def copy_standard(repo: Path) -> Path:
    """The repo's `.coven/` with check.py and its siblings; returns the copied check.py."""
    (repo / ".coven").mkdir(exist_ok=True)
    for name in CHECK_MODULES:
        shutil.copy(STANDARD / name, repo / ".coven" / name)
    return repo / ".coven/check.py"


STUB = "def suppression_lines(text):\n    return []\n"


def test_bytecode_next_to_the_sibling_is_never_loaded(run_check, git_repo):
    typescript_repo(git_repo)
    (git_repo / "a.ts").write_text("// " + "@ts" + "-ignore\n")
    check = copy_standard(git_repo)
    stub = git_repo / "stub.py"
    stub.write_text(STUB)
    sibling = str(git_repo / ".coven/check_ts.py")
    cached = Path(importlib.util.cache_from_source(sibling))
    py_compile.compile(
        str(stub),
        cfile=str(cached),
        dfile=sibling,
        invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH,
    )
    stub.unlink()
    commit_all(git_repo)
    out = run_check(git_repo, "fast", check=check)
    assert " suppressions=1 " in receipt(out)
    rel = cached.relative_to(git_repo).as_posix()
    assert f"settings: {rel}: not allowed" in out.stdout


def test_a_run_writes_no_bytecode_into_the_repo(run_check, git_repo):
    typescript_repo(git_repo)
    (git_repo / "a.ts").write_text("export const a = 1\n")
    check = copy_standard(git_repo)
    commit_all(git_repo)
    run_check(git_repo, "fast", check=check)
    assert not (git_repo / ".coven/__pycache__").exists()
    assert " dirty=no " in receipt(run_check(git_repo, "fast", check=check))


def test_a_symlinked_check_loads_the_sibling_in_coven(run_check, git_repo):
    typescript_repo(git_repo)
    (git_repo / "a.ts").write_text("// " + "@ts" + "-ignore\n")
    copy_standard(git_repo)
    (git_repo / "tools").mkdir()
    shutil.copy(STANDARD / "check.py", git_repo / "tools/check.py")
    (git_repo / "tools/check_ts.py").write_text(STUB)
    (git_repo / ".coven/check.py").unlink()
    (git_repo / ".coven/check.py").symlink_to("../tools/check.py")
    commit_all(git_repo)
    out = run_check(git_repo, "fast", check=git_repo / ".coven/check.py")
    assert " suppressions=1 " in receipt(out)
    assert "settings: .coven/check.py: not allowed (a symlink" in out.stdout


@pytest.mark.parametrize(
    "sibling",
    [
        "def suppression_lines(text:\n",  # a syntax error
        "import coven_no_such_module\n",
        "SUPPRESSIONS = []\n",  # a stale sibling without suppression_lines
    ],
)
def test_a_sibling_that_cannot_load_is_a_setup_error(run_check, git_repo, sibling):
    typescript_repo(git_repo)
    (git_repo / "a.ts").write_text("export const a = 1\n")
    check = copy_standard(git_repo)
    (git_repo / ".coven/check_ts.py").write_text(sibling)
    commit_all(git_repo)
    out = run_check(git_repo, "fast", check=check)
    assert out.returncode == 2
    assert receipt(out).startswith("CHECK fast exit=2 ")
    assert 'error=".coven/check_ts.py: cannot be loaded' in receipt(out)
    assert "Traceback" not in out.stderr


def test_a_file_that_vanishes_is_unreadable_not_a_missing_sibling(
    check, check_repo, tmp_path, capsys
):
    project = check_repo.Project(tmp_path, "typescript", "npm")
    count, passed = check.load_ts().ts_suppressions(project, [tmp_path / "gone.ts"], set())
    out = capsys.readouterr().out
    assert (count, passed) == (0, False)
    assert "gone.ts: unreadable" in out
    assert "is missing" not in out


def test_a_project_without_a_lockfile_runs_no_tool(
    check_ts, check_repo, check_stages, git_repo, capsys
):
    typescript_repo(git_repo)
    (git_repo / "package-lock.json").unlink()
    commit_all(git_repo)
    project = check_repo.Project(git_repo, "typescript", None)
    run = check_stages.Run(git_repo, "fast", None)
    stages = check_ts.typescript_stages(project, run, [project], [], git_repo / "out")
    failed = ("fail", False)
    assert stages == check_stages.Stages(failed, (0, True), failed, failed, failed, failed, failed)
    assert capsys.readouterr().out == "settings: no lockfile\n"


# --- resolved settings ------------------------------------------------------

FIXTURE = REPO / "tests/fixtures/ts-project"
GOOD_TSCONFIG = """{
  "extends": ["../.coven/tsconfig.base.json"],
  "compilerOptions": { "target": "es2022", "module": "nodenext", "noEmit": true },
  "include": ["src"]
}
"""
COVEN_IMPORT = 'import coven from "../.coven/eslint.config.mjs";\n\n'
GOOD_ESLINT = COVEN_IMPORT + 'export default [{ ignores: ["dist/"] }, ...coven];\n'
INDEX = "export function add(a: number, b: number): number {\n  return a + b;\n}\n"


@pytest.fixture(scope="session")
def node_modules(install_fixture) -> Path:
    """The c8 + node:test fixture's packages, installed once from its lockfile."""
    return install_fixture("ts-project")


def ts_repo(root: Path, node_modules: Path | None = None) -> Path:
    """The standard's TypeScript base in `<root>/.coven/` and a passing project in `app/`.

    The project sits below the repo root, as in a monorepo, so the ESLint base must find
    typescript-eslint in the project's packages rather than next to itself. Returns the project.
    """
    (root / ".coven").mkdir(parents=True, exist_ok=True)
    for name in ("tsconfig.base.json", "eslint.config.mjs"):
        shutil.copy(STANDARD / "typescript" / name, root / ".coven" / name)
    app = root / "app"
    (app / "src").mkdir(parents=True)
    for name in ("package.json", "package-lock.json"):
        shutil.copy(FIXTURE / name, app / name)
    (app / "tsconfig.json").write_text(GOOD_TSCONFIG)
    (app / "eslint.config.mjs").write_text(GOOD_ESLINT)
    (app / "src/index.ts").write_text(INDEX)
    if node_modules is not None:
        (app / "node_modules").symlink_to(node_modules, target_is_directory=True)
    return app


@pytest.fixture
def app(tmp_path, node_modules) -> Path:
    return ts_repo(tmp_path, node_modules)


def listed(check, app: Path) -> list[Path]:
    """The project's TypeScript and JavaScript files, as git lists them (no installed packages)."""
    found = []
    for directory, subdirs, names in os.walk(app):
        subdirs[:] = [name for name in subdirs if name != "node_modules"]
        found += [
            Path(directory) / name
            for name in names
            if Path(name).suffix in check.check_repo.TS_SUFFIXES
        ]
    return sorted(found)


def problems(check, app: Path, manager: str = "npm") -> list[str]:
    project = check.check_repo.Project(app, "typescript", manager)
    return check.load_ts().ts_settings_problems(project, app.parent, [], listed(check, app))


@pytest.mark.node
def test_good_ts_project_has_no_problems(check, app):
    assert problems(check, app) == []


@pytest.mark.node
def test_flag_turned_off_is_named(check, app):
    tsconfig = GOOD_TSCONFIG.replace(
        '"noEmit": true', '"noEmit": true, "noUncheckedIndexedAccess": false'
    )
    (app / "tsconfig.json").write_text(tsconfig)
    assert problems(check, app) == [
        "tsconfig.json compilerOptions.noUncheckedIndexedAccess: must be true (resolved: false)"
    ]


@pytest.mark.node
def test_a_flag_strict_implies_turned_off_is_named(check, app):
    tsconfig = GOOD_TSCONFIG.replace('"noEmit": true', '"noEmit": true, "strictNullChecks": false')
    (app / "tsconfig.json").write_text(tsconfig)
    assert problems(check, app) == [
        "tsconfig.json compilerOptions.strictNullChecks: must not be false (it weakens strict)"
    ]


@pytest.mark.node
def test_a_flag_left_unset_is_named(check, app):
    (app / "tsconfig.json").write_text('{ "include": ["src"] }\n')
    found = problems(check, app)
    assert "tsconfig.json compilerOptions.noUnusedLocals: must be true (resolved: unset)" in found
    assert len(found) == 7


@pytest.mark.node
def test_tsconfig_with_comments(check, app):
    (app / "tsconfig.json").write_text(
        "{\n"
        "  // the standard's flags\n"
        '  "extends": ["../.coven/tsconfig.base.json"],\n'
        "  /* a block comment */\n"
        '  "compilerOptions": { "noEmit": true, "exactOptionalPropertyTypes": false, },\n'
        '  "include": ["src"],\n'
        "}\n"
    )
    assert problems(check, app) == [
        "tsconfig.json compilerOptions.exactOptionalPropertyTypes: must be true (resolved: false)"
    ]


@pytest.mark.node
def test_workspace_package_weakens_flag(check, app):
    data = json.loads((app / "package.json").read_text())
    data["workspaces"] = ["packages/*"]
    (app / "package.json").write_text(json.dumps(data))
    package = app / "packages/a"
    (package / "src").mkdir(parents=True)
    (package / "package.json").write_text('{ "name": "a", "private": true }\n')
    (package / "tsconfig.json").write_text(
        GOOD_TSCONFIG.replace("../.coven", "../../../.coven").replace(
            '"noEmit": true', '"noEmit": true, "noUnusedParameters": false'
        )
    )
    (package / "src/a.ts").write_text(INDEX)
    assert problems(check, app) == [
        "packages/a/tsconfig.json compilerOptions.noUnusedParameters: must be true (resolved: false)"
    ]


@pytest.mark.node
def test_a_solution_tsconfig_that_compiles_nothing_needs_no_flags(check, app):
    (app / "tsconfig.json").write_text('{ "files": [], "references": [{ "path": "./src" }] }\n')
    (app / "src/tsconfig.json").write_text(
        GOOD_TSCONFIG.replace("../.coven", "../../.coven").replace('["src"]', '["."]')
    )
    assert problems(check, app) == []


@pytest.mark.node
def test_eslint_rule_downgraded_to_warn(check, app):
    (app / "eslint.config.mjs").write_text(
        COVEN_IMPORT
        + "export default [...coven, "
        + '{ rules: { "@typescript-eslint/switch-exhaustiveness-check": "warn" } }];\n'
    )
    found = problems(check, app)
    assert len(found) == 1
    assert found[0].startswith(
        "src/index.ts: ESLint rule @typescript-eslint/switch-exhaustiveness-check: "
        'must be "error" (resolved: [1, {'
    )


@pytest.mark.node
def test_coven_config_not_spread_last(check, app):
    mine = (
        '{ rules: { "@typescript-eslint/no-explicit-any": "off", "max-lines": ["error", 500] },'
        ' linterOptions: { noInlineConfig: false, reportUnusedDisableDirectives: "warn" } }'
    )
    (app / "eslint.config.mjs").write_text(
        COVEN_IMPORT + f"const mine = [{mine}];\nexport default [...coven, ...mine];\n"
    )
    assert problems(check, app) == [
        'src/index.ts: ESLint linterOptions.reportUnusedDisableDirectives: must be "error" '
        "(resolved: 1)",
        "src/index.ts: ESLint linterOptions.noInlineConfig: must be true (resolved: false)",
        'src/index.ts: ESLint rule @typescript-eslint/no-explicit-any: must be "error" '
        '(resolved: [0, {"fixToUnknown": false, "ignoreRestArgs": false}])',
        "src/index.ts: ESLint rule max-lines: max must be at most 300 (resolved: 500)",
    ]


@pytest.mark.node
def test_a_file_eslint_does_not_lint_is_named(check, app):
    (app / "eslint.config.mjs").write_text(
        COVEN_IMPORT + 'export default [...coven, { ignores: ["src/"] }];\n'
    )
    assert problems(check, app) == ["src/index.ts: ignored by ESLint but compiled by tsc"]


@pytest.mark.node
def test_a_tool_that_fails_is_named_with_its_output(check, app):
    (app / "tsconfig.json").write_text('{ "compilerOptions": { "strict": "yes" } }\n')
    (app / "eslint.config.mjs").write_text("export default [\n")
    found = problems(check, app)
    assert len(found) == 1  # without a resolved tsconfig there is no file to ask ESLint about
    assert found[0].startswith("tsconfig.json: tsc --showConfig failed (exit 1): ")
    assert "TS5024" in found[0]


@pytest.mark.node
def test_an_eslint_config_that_crashes_is_named(check, app):
    (app / "eslint.config.mjs").write_text("export default [\n")
    found = problems(check, app)
    assert len(found) == 1
    assert found[0].startswith("src/index.ts: ESLint cannot resolve its config (SyntaxError: ")


@pytest.mark.node
def test_a_project_that_compiles_none_of_its_own_files_is_named(check, app):
    (app.parent / "shared").mkdir()
    (app.parent / "shared/a.ts").write_text(INDEX)
    (app / "tsconfig.json").write_text(GOOD_TSCONFIG.replace('["src"]', '["../shared"]'))
    assert problems(check, app) == ["no tsconfig.json compiles a source file in the project"]


@pytest.mark.node
def test_settings_stage_is_ok_for_a_good_project(run_check, app):
    repo = app.parent
    (repo / ".gitignore").write_text("node_modules\n")
    for args in (
        ["init", "-q"],
        ["config", "user.name", "Test"],
        ["config", "user.email", "test@example.com"],
    ):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    commit_all(repo)
    out = run_check(repo, "fast")
    assert " projects=1 settings=ok suppressions=0 " in receipt(out), out.stdout
    (app / "tsconfig.json").write_text(GOOD_TSCONFIG.replace('"noEmit": true', '"strict": false'))
    commit_all(repo)
    out = run_check(repo, "fast")
    assert " settings=fail " in receipt(out)
    assert "settings: tsconfig.json compilerOptions.strict: must be true (resolved: false)" in (
        out.stdout
    )


# Options an earlier config sets for rules coven turns on.
EARLIER_OPTIONS = (
    '{ rules: { "@typescript-eslint/ban-ts-comment": ["error", { "ts-ignore": false }], '
    '"@typescript-eslint/no-unused-vars": ["error", { args: "none", varsIgnorePattern: ".*" }], '
    '"@typescript-eslint/switch-exhaustiveness-check": '
    '["error", { considerDefaultExhaustiveForUnions: true }] } }'
)


@pytest.mark.node
def test_options_set_before_coven_are_overwritten(check, app):
    (app / "eslint.config.mjs").write_text(
        COVEN_IMPORT + f"export default [{EARLIER_OPTIONS}, ...coven];\n"
    )
    assert problems(check, app) == []


@pytest.mark.node
def test_options_set_after_coven_are_named(check, app):
    (app / "eslint.config.mjs").write_text(
        COVEN_IMPORT + f"export default [...coven, {EARLIER_OPTIONS}];\n"
    )
    found = problems(check, app)
    assert [problem.split(": options must be")[0] for problem in found] == [
        "src/index.ts: ESLint rule @typescript-eslint/ban-ts-comment",
        "src/index.ts: ESLint rule @typescript-eslint/no-unused-vars",
        "src/index.ts: ESLint rule @typescript-eslint/switch-exhaustiveness-check",
    ]
    assert '(resolved: [{"ts-ignore": false}])' in found[0]


@pytest.mark.node
def test_a_processor_that_blanks_linting_is_named(check, app):
    blank = (
        '{ processor: { meta: { name: "blank" }, preprocess: () => [""], postprocess: () => [] } }'
    )
    (app / "eslint.config.mjs").write_text(COVEN_IMPORT + f"export default [...coven, {blank}];\n")
    assert problems(check, app) == [
        'src/index.ts: ESLint processor: must be unset (resolved: "blank")'
    ]


@pytest.mark.node
def test_every_compiled_file_is_resolved(check, app):
    # a files-scoped override after coven, and an ignored directory.
    (app / "src/legacy").mkdir()
    (app / "src/legacy/a.ts").write_text(INDEX)
    (app / "src/old").mkdir()
    for name in "abcde":
        (app / f"src/old/{name}.ts").write_text(INDEX)
    override = (
        '{ files: ["src/legacy/**"], rules: { "@typescript-eslint/no-explicit-any": "off" }, '
        "linterOptions: { noInlineConfig: false } }"
    )
    (app / "eslint.config.mjs").write_text(
        COVEN_IMPORT + f'export default [{{ ignores: ["src/old/"] }}, ...coven, {override}];\n'
    )
    found = problems(check, app)
    assert found[0] == (
        "src/legacy/a.ts: ESLint linterOptions.noInlineConfig: must be true (resolved: false)"
    )
    assert found[1].startswith(
        'src/legacy/a.ts: ESLint rule @typescript-eslint/no-explicit-any: must be "error"'
    )
    assert found[2] == (
        "src/old/a.ts, src/old/b.ts, src/old/c.ts and 2 more: ignored by ESLint but compiled by tsc"
    )
    assert len(found) == 3


@pytest.mark.node
def test_a_nested_eslint_config_is_named(check, app):
    # ESLint 10 uses the nearest eslint.config.* for the files below it.
    (app / "src/sub").mkdir()
    (app / "src/sub/a.ts").write_text(INDEX)
    (app / "src/sub/eslint.config.mjs").write_text(
        'import tseslint from "typescript-eslint";\n'
        'export default [{ files: ["**/*.ts"], languageOptions: { parser: tseslint.parser } }];\n'
    )
    found = problems(check, app)
    assert found[0] == (
        "src/sub/eslint.config.mjs: not allowed "
        "(ESLint uses it in place of the project's config below it)"
    )
    assert all(problem.startswith("src/sub/a.ts: ESLint ") for problem in found[1:])
    assert "src/sub/a.ts: ESLint linterOptions.noInlineConfig: must be true (resolved: unset)" in (
        found
    )


@pytest.mark.node
def test_no_check_is_named(check, app):
    # noCheck turns type checking off while tsc still exits 0.
    tsconfig = GOOD_TSCONFIG.replace('"noEmit": true', '"noEmit": true, "noCheck": true')
    (app / "tsconfig.json").write_text(tsconfig)
    assert problems(check, app) == [
        "tsconfig.json compilerOptions.noCheck: must not be true (it turns type checks off)"
    ]


@pytest.mark.node
def test_tools_run_directly_not_through_the_manager(check, app, tmp_path, monkeypatch):
    # `yarn tsc` (v1) prints "yarn run v1.22.22" and "$ ..." around the JSON.
    framing = tmp_path / "framing"
    for name in ("yarn", "npx", "pnpm"):
        fake_program(framing / name, "yarn run v1.22.22")
    monkeypatch.setenv("PATH", f"{framing}{os.pathsep}{os.environ['PATH']}")
    assert problems(check, app, "yarn") == []


@pytest.mark.node
def test_a_file_the_standard_does_not_lint_is_named(check, app):
    (app.parent / ".coven/eslint.config.mjs").write_text(
        'export default [{ files: ["**/*.js"] }];\n'
    )
    (app / "eslint.config.mjs").write_text(
        'import tseslint from "typescript-eslint";\nexport default tseslint.configs.recommended;\n'
    )
    found = problems(check, app)
    assert found == ["src/index.ts: the standard's ESLint config does not lint it"]


@pytest.mark.node
def test_tsconfigs_a_solution_refers_to_are_checked(check, app):
    # Vite's layout, a solution tsconfig.json and tsconfig.app.json.
    (app / "tsconfig.json").write_text(
        '{ "files": [], "references": [{ "path": "./tsconfig.app.json" }] }\n'
    )
    (app / "tsconfig.app.json").write_text(
        GOOD_TSCONFIG.replace('"noEmit": true', '"composite": true, "strict": false')
    )
    assert problems(check, app) == [
        "tsconfig.app.json compilerOptions.strict: must be true (resolved: false)"
    ]


# --- settings that need no Node --------------------------------------------


def all_on_path(monkeypatch, missing: str = "") -> None:
    monkeypatch.setattr(
        shutil, "which", lambda name: None if name == missing else f"/usr/bin/{name}"
    )


@pytest.mark.parametrize(
    ("manager", "install"),
    [
        ("npm", "npm ci"),
        ("pnpm", "pnpm install --frozen-lockfile"),
        ("yarn", "yarn install --frozen-lockfile"),  # without a yarn.lock to say which yarn
        ("bun", "bun install --frozen-lockfile"),
    ],
)
def test_uninstalled_project_says_how_to_install(check, tmp_path, monkeypatch, manager, install):
    app = ts_repo(tmp_path)
    all_on_path(monkeypatch)
    assert problems(check, app, manager) == [f"dependencies not installed: run (in app): {install}"]


@pytest.mark.parametrize(("manager", "program"), [("npm", "node"), ("bun", "bunx")])
def test_a_missing_program_is_named(check, tmp_path, monkeypatch, manager, program):
    app = ts_repo(tmp_path)
    all_on_path(monkeypatch, missing=program)
    assert problems(check, app, manager) == [
        f"{program} not found on PATH (needed to read the resolved settings)"
    ]


def test_missing_dev_dependencies_are_named(check, tmp_path, monkeypatch):
    app = ts_repo(tmp_path)
    all_on_path(monkeypatch)
    data = json.loads((app / "package.json").read_text())
    del data["devDependencies"]["prettier"]
    del data["devDependencies"]["c8"]
    (app / "package.json").write_text(json.dumps(data))
    assert problems(check, app)[0] == (
        "package.json: missing dev dependencies c8, prettier (run (in app): npm install -D c8 prettier)"
    )


def test_vitest_needs_its_coverage_package(check, tmp_path, monkeypatch):
    app = ts_repo(tmp_path)
    all_on_path(monkeypatch)
    data = json.loads((app / "package.json").read_text())
    data["dependencies"] = {"vitest": "5.0.5"}
    (app / "package.json").write_text(json.dumps(data))
    assert problems(check, app)[0] == (
        "package.json: missing dev dependencies @vitest/coverage-v8 "
        "(run (in app): npm install -D @vitest/coverage-v8)"
    )


def test_an_unreadable_package_json_is_named(check, tmp_path, monkeypatch):
    app = ts_repo(tmp_path)
    all_on_path(monkeypatch)
    (app / "package.json").write_text("{")
    assert problems(check, app)[0] == "package.json: cannot be read (JSONDecodeError)"


def test_a_missing_eslint_base_is_named(check, tmp_path, monkeypatch):
    app = ts_repo(tmp_path)
    all_on_path(monkeypatch)
    (tmp_path / ".coven/eslint.config.mjs").unlink()
    assert problems(check, app)[0] == (".coven/eslint.config.mjs: missing (re-run coven project)")


def test_no_lockfile_is_named(check, check_repo, tmp_path):
    app = ts_repo(tmp_path)
    project = check_repo.Project(app, "typescript", None)
    assert check.load_ts().ts_settings_problems(project, tmp_path) == ["no lockfile"]


def test_every_tsconfig_is_found_on_disk(check_ts, tmp_path):
    for directory in ("", "src", "packages/a", "node_modules/x", "nested", ".git", ".coven"):
        (tmp_path / directory).mkdir(parents=True, exist_ok=True)
        (tmp_path / directory / "tsconfig.json").write_text("{}")
    found = check_ts.tsconfigs(tmp_path, [tmp_path / "nested"])
    assert [path.relative_to(tmp_path).as_posix() for path in found] == [
        "tsconfig.json",
        "packages/a/tsconfig.json",
        "src/tsconfig.json",
    ]


def good_rules(check_ts) -> dict[str, list[object]]:
    return {**{name: [2] for name in check_ts.NAMED_RULES}, "complexity": [2, 10]}


GOOD_OPTIONS = {"noInlineConfig": True, "reportUnusedDisableDirectives": 2}


@pytest.mark.parametrize(
    ("rule", "entry", "problem"),
    [
        ("complexity", [2, 10], None),
        ("complexity", ["error", 5], None),
        ("complexity", [2, {"max": 8}], None),
        ("complexity", [2, {"maximum": 12}], "max must be at most 10 (resolved: 12)"),
        ("complexity", [2], "max must be at most 10 (resolved: 20)"),  # ESLint's default
        (
            "complexity",
            [2, {"max": 10, "variant": "modified"}],
            'variant must be "classic" (resolved: "modified")',
        ),
        ("complexity", [2, {"max": 10, "variant": "classic"}], None),
        ("complexity", [2, "ten"], 'max must be at most 10 (resolved: "ten")'),
        ("max-lines", [2], None),  # ESLint's default is 300
        ("max-lines", [2, 250], None),
        ("max-lines", [2, {"skipComments": False}], None),
        ("max-lines", [2, {"max": 301}], "max must be at most 300 (resolved: 301)"),
        ("max-lines", [True, 300], 'must be "error" (resolved: [true, 300])'),
        ("max-lines", None, 'must be "error" (resolved: unset)'),
    ],
)
def test_rule_limits(check_ts, rule, entry, problem):
    rules = good_rules(check_ts)
    rules[rule] = entry
    if entry is None:
        del rules[rule]
    found = check_ts.config_problems({"linterOptions": GOOD_OPTIONS, "rules": rules}, {})
    assert found == ([] if problem is None else [f"ESLint rule {rule}: {problem}"])


def test_a_rule_the_base_turns_on_must_stay_on(check_ts):
    rules = good_rules(check_ts)
    base = {"rules": {"no-var": [2], "no-undef": [0], "eqeqeq": ["warn"]}}
    resolved = {"linterOptions": GOOD_OPTIONS, "rules": {**rules, "no-var": "warn", "eqeqeq": [2]}}
    assert check_ts.config_problems(resolved, base) == [
        'ESLint rule no-var: must be "error" (resolved: "warn")'
    ]
    assert check_ts.config_problems({"rules": rules}, base) == [
        'ESLint linterOptions.reportUnusedDisableDirectives: must be "error" (resolved: unset)',
        "ESLint linterOptions.noInlineConfig: must be true (resolved: unset)",
        'ESLint rule eqeqeq: must be "warn" (resolved: unset)',
        'ESLint rule no-var: must be "error" (resolved: unset)',
    ]


def test_a_rule_keeps_the_standards_options(check_ts):
    rules = good_rules(check_ts)
    base = {"rules": {**rules, "no-unused-vars": [2, {}]}}
    resolved = {
        "linterOptions": GOOD_OPTIONS,
        "rules": {**rules, "no-unused-vars": [2, {"args": "none"}]},
    }
    assert check_ts.config_problems(resolved, base) == [
        'ESLint rule no-unused-vars: options must be [{}] (resolved: [{"args": "none"}])'
    ]


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        ({"processor": "blank"}, 'ESLint processor: must be unset (resolved: "blank")'),
        ({"language": "@/json"}, 'ESLint language: must be "@/js" (resolved: "@/json")'),
        (
            {"languageOptions": {"parser": "espree", "parserOptions": {"projectService": True}}},
            'ESLint languageOptions.parser: must be "ts@8" (resolved: "espree")',
        ),
        (
            {"languageOptions": {"parser": "ts@8", "parserOptions": {}}},
            "ESLint languageOptions.parserOptions.projectService: must be true (resolved: unset)",
        ),
    ],
)
def test_processor_language_and_parser_must_be_the_standards(check_ts, change, problem):
    base = {
        "language": "@/js",
        "languageOptions": {"parser": "ts@8", "parserOptions": {"projectService": True}},
        "rules": good_rules(check_ts),
    }
    resolved = {**base, "linterOptions": GOOD_OPTIONS, **change}
    assert check_ts.config_problems(resolved, base) == [problem]


def fake_program(path: Path, printed: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/sh\nprintf '%s\\n' {printed!r}\n")
    path.chmod(0o755)
    return path


@pytest.mark.parametrize(("printed", "problem"), [("nope", "no JSON"), ("[1]", "no JSON object")])
def test_tsc_output_that_is_no_config_is_named(check_ts, tmp_path, printed, problem):
    fake_program(tmp_path / "node_modules/.bin/tsc", printed)
    found = check_ts._show(tmp_path, "npm", tmp_path / "tsconfig.json")
    assert found == f"tsconfig.json: tsc --showConfig printed {problem}"


@pytest.mark.parametrize(
    ("printed", "problem"),
    [
        ("nope", "printed no JSON"),
        ("{}", "printed no JSON list"),
        ("[1]", "printed an unexpected group"),
    ],
)
def test_eslint_helper_output_that_is_no_config_is_named(
    check_ts, tmp_path, monkeypatch, printed, problem
):
    monkeypatch.setattr(check_ts, "NODE", str(fake_program(tmp_path / "node", printed)))
    found = check_ts._eslint_problems(tmp_path, tmp_path / "base.mjs", [tmp_path / "a.ts"])
    assert found == [f"resolving the ESLint configs {problem}"]


def test_a_tool_that_cannot_start_is_named(check_ts, tmp_path):
    found = check_ts._show(tmp_path, "npm", tmp_path / "tsconfig.json")
    assert found == "tsconfig.json: tsc --showConfig cannot be run (FileNotFoundError)"


def test_the_project_sources_are_its_own_lintable_files(check_ts, tmp_path):
    listed = "\n".join(
        [
            str(tmp_path / "node_modules/typescript/lib/lib.es2022.d.ts"),
            str(tmp_path.parent / "outside.ts"),
            str(tmp_path / "data.json"),
            "",
            "tsconfig.json(3,82): error TS5052: Option 'x' cannot be specified without 'y'.",
            "src/relative.ts",
            str(tmp_path / "src/a.tsx"),
            str(tmp_path / "src/types.d.ts"),
        ]
    )
    assert check_ts._sources(tmp_path, listed) == [
        tmp_path / "src/a.tsx",
        tmp_path / "src/types.d.ts",
    ]


def test_stray_eslint_configs_are_named(check_ts, tmp_path):
    for name in ("eslint.config.mjs", "src/eslint.config.ts", "lib/.eslintrc.json", ".eslintrc"):
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text("{}")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg/package.json").write_text('{"eslintConfig": {}}')
    (tmp_path / "package.json").write_text("{")
    (tmp_path / "node_modules/x").mkdir(parents=True)
    (tmp_path / "node_modules/x/eslint.config.js").write_text("")
    (tmp_path / ".coven").mkdir()
    (tmp_path / ".coven/eslint.config.mjs").write_text("")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested/eslint.config.js").write_text("")
    assert check_ts._stray_eslint_problems(tmp_path, [tmp_path / "nested"]) == [
        ".eslintrc: not allowed (a legacy ESLint config)",
        "lib/.eslintrc.json: not allowed (a legacy ESLint config)",
        "pkg/package.json eslintConfig: not allowed (a legacy ESLint config)",
        "src/eslint.config.ts: not allowed "
        "(ESLint uses it in place of the project's config below it)",
    ]


@pytest.mark.parametrize("lists_a_file", [True, False])
def test_a_listing_with_config_diagnostics_still_counts(check_ts, tmp_path, lists_a_file):
    flags = ", ".join(f'"{flag}": true' for flag in check_ts.BASE_FLAGS)
    source = tmp_path / "src/a.ts"
    listed = f"echo {source}" if lists_a_file else "true"
    tsc = tmp_path / "node_modules/.bin/tsc"
    tsc.parent.mkdir(parents=True)
    tsc.write_text(
        "#!/bin/sh\n"
        f"""if [ "$1" = --showConfig ]; then echo '{{"compilerOptions": {{{flags}}}}}'; exit 0; fi\n"""
        "echo 'tsconfig.json(1,1): error TS5052: an option needs another'\n"
        f"{listed}\n"
        "exit 1\n"
    )
    tsc.chmod(0o755)
    shown = check_ts._show(tmp_path, "npm", tmp_path / "tsconfig.json")
    found = check_ts._flag_problems(tmp_path, "npm", tmp_path / "tsconfig.json", shown)
    if lists_a_file:
        assert found == ([], [source])
    else:
        assert found == (
            [
                "tsconfig.json: tsc --listFilesOnly failed (exit 1): "
                "tsconfig.json(1,1): error TS5052: an option needs another"
            ],
            [],
        )


def test_a_standard_config_that_cannot_be_resolved_is_named(check_ts, tmp_path):
    group = {
        "files": [str(tmp_path / "a.ts")],
        "project": {"config": {}},
        "standard": {"error": "Error: Cannot find package 'typescript-eslint'"},
    }
    assert check_ts._group_problems(tmp_path, group) == [
        "a.ts: the standard's ESLint config cannot be resolved "
        "(Error: Cannot find package 'typescript-eslint')"
    ]


# --- the TypeScript stages ---------------------------------------------------


def only_programs(where: Path, *names: str) -> Path:
    """A directory holding links to these programs only, to use as PATH."""
    where.mkdir(parents=True, exist_ok=True)
    for name in names:
        found = shutil.which(name)
        if found is None:
            pytest.fail(f"{name} is needed")
        (where / name).symlink_to(found)
    return where


def staged(root: Path, node_modules: Path, runner: str) -> Path:
    """A committed repo on branch main whose `app/` TypeScript project passes every stage."""
    write_ts_project(root / "app", node_modules, root, runner)
    git(root, "branch", "-M", "main")
    commit_all(root)
    return root


@pytest.fixture
def c8_repo(git_repo, node_modules) -> Path:
    return staged(git_repo, node_modules, "c8")


@pytest.fixture
def vitest_repo(git_repo, install_fixture) -> Path:
    return staged(git_repo, install_fixture("ts-vitest"), "vitest")


GREEN_FAST = (
    " projects=1 settings=ok suppressions=0 skipped=0 format=ok lint=ok types=ok tests=1 "
    "coverage=skip base=- runner=uv"
)
# Inherited settings that would break or reshape every Node tool if they reached it.
POISONED_ENV = {
    "NODE_OPTIONS": "--require=/nonexistent/preload.cjs",
    "ESLINT_FLAGS": "no_such_flag",
    "TYPESCRIPT_ESLINT_PROJECT_SERVICE": "false",
    "NODE_TEST_CONTEXT": "child-v8",
}


@pytest.mark.node
def test_ts_fast_all_green(run_check, c8_repo):
    out = run_check(c8_repo, "fast", "app/test/add.test.ts", env=POISONED_ENV)
    assert out.returncode == 0, out.stdout
    assert receipt(out).endswith(GREEN_FAST)
    assert git(c8_repo, "status", "--porcelain") == ""


# The ESLint base spread into a typed config list, ESLint's defineConfig and typescript-eslint's.
TYPED_CONFIGS = {
    "typed.mjs": (
        'import coven from "{up}/eslint.config.mjs";\n\n'
        '/** @type {{import("eslint").Linter.Config[]}} */\n'
        'const configs = [{{ ignores: ["dist/"] }}, ...coven];\n\n'
        "export default configs;\n"
    ),
    "define.mjs": (
        'import {{ defineConfig }} from "eslint/config";\n\n'
        'import coven from "{up}/eslint.config.mjs";\n\n'
        'export default defineConfig([{{ ignores: ["dist/"] }}, ...coven]);\n'
    ),
    "tseslint.mjs": (
        'import tseslint from "typescript-eslint";\n\n'
        'import coven from "{up}/eslint.config.mjs";\n\n'
        'export default tseslint.config({{ ignores: ["dist/"] }}, ...coven);\n'
    ),
}


@pytest.mark.node
@pytest.mark.parametrize("where", [".", "app"])
def test_check_js_projects_type_check_the_base(run_check, git_repo, node_modules, where):
    """With `checkJs`, tsc checks the standard's ESLint base too, at the root and nested; and the
    base spreads into each typed way of writing an ESLint config."""
    app = git_repo / where
    write_ts_project(app, node_modules, git_repo, check_js=True)
    up = "../.coven" if where == "." else "../../.coven"
    for name, text in TYPED_CONFIGS.items():
        (app / "src" / name).write_text(text.format(up=up))
    git(git_repo, "branch", "-M", "main")
    commit_all(git_repo)
    out = run_check(git_repo, "fast", (Path(where) / "test/add.test.ts").as_posix())
    assert out.returncode == 0, out.stdout
    assert receipt(out).endswith(GREEN_FAST)


@pytest.mark.node
def test_ts_only_fast_needs_no_uv(run_check, c8_repo, tmp_path):
    path = only_programs(tmp_path / "bin", "git", "node")
    out = run_check(c8_repo, "fast", "app/test/add.test.ts", env={"PATH": str(path)})
    assert out.returncode == 0, out.stdout
    assert receipt(out).endswith(GREEN_FAST)


@pytest.mark.node
def test_vitest_fast_runs_the_named_tests(run_check, vitest_repo):
    out = run_check(vitest_repo, "fast", "app/src/add.test.ts")
    assert out.returncode == 0, out.stdout
    assert receipt(out).endswith(GREEN_FAST)
    assert git(vitest_repo, "status", "--porcelain") == ""


@pytest.mark.node
def test_fast_mode_without_named_tests_runs_none(run_check, c8_repo):
    assert " tests=0 coverage=skip " in receipt(run_check(c8_repo, "fast"))


def add_sub(root: Path, runner: str) -> None:
    """A feature branch that adds `sub` and its test to `app/`."""
    git(root, "checkout", "-q", "-b", "feature")
    (root / "app/src/sub.ts").write_text(TS_SUB)
    path, text = ts_test(runner, "sub", "3, 1", 2)
    (root / "app" / path).write_text(text)
    commit_all(root)


@pytest.mark.node
@pytest.mark.parametrize("runner", ["c8", "vitest"])
def test_ts_project_full_receipt_ok(run_check, git_repo, install_fixture, runner):
    repo = staged(
        git_repo, install_fixture({"c8": "ts-project", "vitest": "ts-vitest"}[runner]), runner
    )
    add_sub(repo, runner)
    out = run_check(repo, "full", "--base", "main")
    assert out.returncode == 0, out.stdout
    base = git(repo, "rev-parse", "--short=7", "main").strip()
    assert receipt(out).endswith(f" tests=2 coverage=100 base={base} runner=uv")
    assert git(repo, "status", "--porcelain") == ""


UNTESTED = "export function twice(n: number): number {\n  return n * 2;\n}\n"
TYPES_ONLY = "export interface Shape {\n  name: string;\n}\n"
USES_TYPE = (
    'import type { Shape } from "./shape.ts";\n\n'
    "export function named(shape: Shape): string {\n  return shape.name;\n}\n"
)


@pytest.mark.node
def test_a_changed_file_absent_from_the_report_is_named(run_check, c8_repo):
    add_sub(c8_repo, "c8")
    (c8_repo / "app/src/twice.ts").write_text(UNTESTED)  # nothing loads it
    (c8_repo / "app/src/shape.ts").write_text(TYPES_ONLY)  # no runtime code: exempt
    (c8_repo / "app/src/add.ts").write_text(TS_INDEX + "\n" + USES_TYPE)
    commit_all(c8_repo)
    out = run_check(c8_repo, "full", "--base", "main")
    assert out.returncode == 1
    assert " coverage=fail " in receipt(out)
    line = next(line for line in out.stdout.splitlines() if line.startswith("coverage: "))
    assert line.startswith("coverage: not measured: src/twice.ts (changed, but absent from")
    assert "shape.ts" not in line


@pytest.mark.node
def test_vitest_counts_an_untested_changed_file(run_check, vitest_repo):
    # coverage.include is set and full mode passes --coverage.changed: the file shows as 0%.
    git(vitest_repo, "checkout", "-q", "-b", "feature")
    (vitest_repo / "app/src/twice.ts").write_text(UNTESTED)
    commit_all(vitest_repo)
    out = run_check(vitest_repo, "full", "--base", "main")
    assert out.returncode == 1
    assert " tests=1 coverage=0 " in receipt(out)
    assert "app/src/twice.ts (0.0%)" in out.stdout


@pytest.mark.node
def test_each_failing_ts_stage_is_named(run_check, c8_repo):
    app = c8_repo / "app"
    (app / "src/ugly.ts").write_text("export const ugly   =  1;\n")
    (app / "src/lazy.ts").write_text(
        "export async function lazy(): Promise<number> {\n  return 1;\n}\n"
    )
    (app / "src/wrong.ts").write_text('export const wrong: number = "1";\n')
    path, text = ts_test("c8", "add", "1, 2", 4)
    (app / "test/broken.test.ts").write_text(text)
    commit_all(c8_repo)
    out = run_check(c8_repo, "fast", "app/test/broken.test.ts")
    assert out.returncode == 1
    assert " format=fail lint=fail types=fail tests=fail coverage=skip " in receipt(out)
    assert "src/ugly.ts" in out.stdout.split("--- format failed")[1]
    assert "require-await" in out.stdout.split("--- lint failed")[1]
    assert "TS2322" in out.stdout.split("--- types (tsconfig.json) failed")[1]
    assert "--- tests failed" in out.stdout


@pytest.mark.node
def test_types_stage_checks_the_configs_a_solution_refers_to(run_check, c8_repo):
    app = c8_repo / "app"
    (app / "tsconfig.app.json").write_text((app / "tsconfig.json").read_text())
    (app / "tsconfig.json").write_text(
        '{\n  "files": [],\n  "references": [{ "path": "./tsconfig.app.json" }]\n}\n'
    )
    (app / "src/wrong.ts").write_text('export const wrong: number = "1";\n')
    commit_all(c8_repo)
    out = run_check(c8_repo, "fast")
    assert " types=fail " in receipt(out)
    assert "TS2322" in out.stdout.split("--- types (tsc -b tsconfig.json) failed")[1]


@pytest.mark.node
def test_a_workspace_lints_its_packages_without_tsconfig_root_dir(run_check, c8_repo):
    app = c8_repo / "app"
    data = json.loads((app / "package.json").read_text())
    data["workspaces"] = ["packages/*"]
    (app / "package.json").write_text(json.dumps(data, indent=2) + "\n")
    package = app / "packages/a"
    (package / "src").mkdir(parents=True)
    (package / "package.json").write_text(
        json.dumps({"name": "a", "private": True}, indent=2) + "\n"
    )
    tsconfig = (app / "tsconfig.json").read_text().replace("../.coven", "../../../.coven")
    (package / "tsconfig.json").write_text(tsconfig.replace('["src", "test"]', '["src"]'))
    (package / "src/twice.ts").write_text(UNTESTED)
    commit_all(c8_repo)
    out = run_check(c8_repo, "fast")
    assert out.returncode == 0, out.stdout
    assert " settings=ok " in receipt(out)
    assert " lint=ok types=ok " in receipt(out)
    (package / "src/lazy.ts").write_text(
        "export async function lazy(): Promise<number> {\n  return 1;\n}\n"
    )
    commit_all(c8_repo)
    out = run_check(c8_repo, "fast")
    assert " lint=fail " in receipt(out)
    assert "packages/a/src/lazy.ts" in out.stdout


PACKAGE_TSCONFIG = """{
  "extends": ["../../.coven/tsconfig.base.json"],
  "compilerOptions": {
    "composite": true,
    "target": "es2022",
    "module": "nodenext",
    "rootDir": "src",
    "outDir": "dist"
  },
  "include": ["src"]REFERENCES
}
"""
APP_TSCONFIG = """{
  "extends": ["../.coven/tsconfig.base.json"],
  "compilerOptions": {
    "target": "es2022",
    "module": "nodenext",
    "noEmit": true
  },
  "include": ["src"]
}
"""
TOOLS_TSCONFIG = """{
  "extends": ["./.coven/tsconfig.base.json"],
  "compilerOptions": {
    "target": "es2022",
    "module": "nodenext",
    "noEmit": true,
    "allowJs": true
  },
  "include": ["eslint.config.mjs"]
}
"""
WORKSPACE_SOLUTION = (
    '{\n  "files": [],\n  "references": [\n    { "path": "./packages/a" },\n'
    '    { "path": "./packages/b" },\n    { "path": "./tsconfig.tools.json" }\n  ]\n}\n'
)
TWICE = (
    'import { add } from "@demo/a";\n\n'
    "export function twice(n: number): number {\n  return add(n, n);\n}\n"
)
FOUR = 'import { twice } from "@demo/b";\n\nexport const four: number = twice(2);\n'


def workspace_package(root: Path, name: str, source: str, references: str = "") -> None:
    """`packages/<name>`, published as `@demo/<name>` from its build output in `dist/`."""
    package = root / "packages" / name
    (package / "src").mkdir(parents=True)
    exports = {".": {"types": "./dist/index.d.ts", "default": "./dist/index.js"}}
    data = {"name": f"@demo/{name}", "private": True, "type": "module", "exports": exports}
    (package / "package.json").write_text(json.dumps(data, indent=2) + "\n")
    (package / "tsconfig.json").write_text(PACKAGE_TSCONFIG.replace("REFERENCES", references))
    (package / "src/index.ts").write_text(source)


def ts_workspace(root: Path, node_modules: Path) -> Path:
    """A committed npm workspace on branch main, as `tsc -b` builds it: `packages/b` refers to
    `packages/a`, each imported through its `exports` (in `dist/`, git-ignored); `app/` refers to
    nothing, sorts before `packages/` and imports `@demo/b`. Nothing is built yet."""
    (root / ".coven").mkdir()
    for name in ("tsconfig.base.json", "eslint.config.mjs"):
        shutil.copy(STANDARD / "typescript" / name, root / ".coven" / name)
    data = json.loads((FIXTURE / "package.json").read_text())
    data["workspaces"] = ["packages/*"]
    (root / "package.json").write_text(json.dumps(data, indent=2) + "\n")
    shutil.copy(FIXTURE / "package-lock.json", root / "package-lock.json")
    (root / "tsconfig.json").write_text(WORKSPACE_SOLUTION)
    (root / "tsconfig.tools.json").write_text(TOOLS_TSCONFIG)
    (root / "eslint.config.mjs").write_text(
        'import coven from "./.coven/eslint.config.mjs";\n\n'
        'export default [{ ignores: [".coven/", "**/dist/"] }, ...coven];\n'
    )
    workspace_package(root, "a", INDEX)
    workspace_package(root, "b", TWICE, ',\n  "references": [{ "path": "../a" }]')
    (root / "app/src").mkdir(parents=True)
    (root / "app/tsconfig.json").write_text(APP_TSCONFIG)
    (root / "app/src/main.ts").write_text(FOUR)
    # The installed packages, and the workspace's own linked in as npm links them.
    (root / "node_modules/@demo").mkdir(parents=True)
    for entry in node_modules.iterdir():
        (root / "node_modules" / entry.name).symlink_to(entry, target_is_directory=entry.is_dir())
    for name in ("a", "b"):
        (root / "node_modules/@demo" / name).symlink_to(f"../../packages/{name}")
    (root / ".gitignore").write_text("node_modules\ndist/\n*.tsbuildinfo\n")
    git(root, "branch", "-M", "main")
    commit_all(root)
    return root


@pytest.mark.node
def test_a_workspace_is_green_on_a_fresh_tree_and_after_its_build(
    run_check, git_repo, node_modules
):
    repo = ts_workspace(git_repo, node_modules)
    first = run_check(repo, "fast")
    assert first.returncode == 0, first.stdout
    assert receipt(first).endswith(GREEN_FAST.replace("tests=1", "tests=0"))
    assert (repo / "packages/b/dist/index.d.ts").is_file()  # built, in git-ignored output
    second = run_check(repo, "fast")  # tsc now lists the build output too: it is not linted
    assert second.returncode == 0, second.stdout
    assert " settings=ok " in receipt(second)
    assert git(repo, "status", "--porcelain") == ""


@pytest.mark.node
def test_stale_build_output_does_not_decide_the_types(run_check, git_repo, node_modules):
    repo = ts_workspace(git_repo, node_modules)
    assert run_check(repo, "fast").returncode == 0  # dist/ now holds `twice`
    renamed = TWICE.replace("twice", "double")
    (repo / "packages/b/src/index.ts").write_text(renamed)
    commit_all(repo)
    out = run_check(repo, "fast")
    assert " types=fail " in receipt(out)
    assert "app/src/main.ts" in out.stdout
    assert "TS2305" in out.stdout or "TS2724" in out.stdout


def fake_tsc(root: Path, configs: dict[str, str], log: Path) -> None:
    """A tsc that logs its arguments and prints the given --showConfig output per config."""
    cases = "".join(
        f"  '--showConfig {name}') echo '{shown}' ;;\n" for name, shown in configs.items()
    )
    tsc = root / "node_modules/.bin/tsc"
    tsc.parent.mkdir(parents=True)
    tsc.write_text(f'#!/bin/sh\necho "$*" >> {log}\ncase "$1 $3" in\n{cases}esac\nexit 0\n')
    tsc.chmod(0o755)
    for name in configs:
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text("{}")


def test_one_build_comes_first_then_the_configs_it_does_not_reach(check_ts, tmp_path):
    log = tmp_path / "calls.log"
    root = tmp_path / "app"
    fake_tsc(
        root,
        {
            "tsconfig.json": '{"files": ["./a.ts"], "references": [{"path": "./lib"}]}',
            "a/tsconfig.json": '{"files": ["./d.ts"]}',  # refers to nothing, sorts first
            "lib/tsconfig.json": '{"files": ["./b.ts"], "references": [{"path": "../shared"}]}',
            "shared/tsconfig.json": '{"files": ["./e.ts"]}',
            "solution/tsconfig.json": '{"references": [{"path": "./tsconfig.web.json"}]}',
            "solution/tsconfig.web.json": '{"files": ["./c.ts"]}',
        },
        log,
    )
    assert check_ts.types_stage(check_ts.Target(root, "npm", ".", tail), []) == ("ok", True)
    calls = [line for line in log.read_text().splitlines() if not line.startswith("--showConfig")]
    assert calls == [
        "-b --force tsconfig.json solution/tsconfig.json",  # lib/ is built as tsconfig.json's
        "--noEmit -p a/tsconfig.json",
    ]


def tsc_calls(check_ts, tmp_path: Path, refs: dict[str, list[str]]) -> list[str]:
    """The tsc runs the types stage makes over configs `<name>/tsconfig.json` with these refs."""
    log = tmp_path / "calls.log"
    root = tmp_path / "app"
    configs = {
        f"{name}/tsconfig.json": json.dumps(
            {"files": ["./x.ts"], "references": [{"path": f"../{ref}"} for ref in targets]}
        )
        for name, targets in refs.items()
    }
    fake_tsc(root, configs, log)
    check_ts.types_stage(check_ts.Target(root, "npm", ".", tail), [])
    return [line for line in log.read_text().splitlines() if not line.startswith("--showConfig")]


def test_configs_that_refer_to_each_other_are_all_built(check_ts, tmp_path):
    refs = {"a": ["b"], "b": ["a"]}
    assert tsc_calls(check_ts, tmp_path, refs) == [
        "-b --force a/tsconfig.json b/tsconfig.json"  # tsc names the loop
    ]


def test_a_reference_loop_beside_an_unrelated_build_is_built(check_ts, tmp_path):
    refs = {"a": ["b"], "b": ["a"], "c": ["d"], "d": []}
    # d is built as c's; nothing is left to check on its own
    assert tsc_calls(check_ts, tmp_path, refs) == [
        "-b --force a/tsconfig.json b/tsconfig.json c/tsconfig.json"
    ]


def test_a_loop_reached_from_outside_is_built_through_it(check_ts, tmp_path):
    refs = {"a": ["b"], "b": ["a"], "c": ["a"]}
    assert tsc_calls(check_ts, tmp_path, refs) == ["-b --force c/tsconfig.json"]


def test_a_self_reference_beside_an_unrelated_build_is_built(check_ts, tmp_path):
    refs = {"a": ["a"], "c": ["d"], "d": []}
    assert tsc_calls(check_ts, tmp_path, refs) == ["-b --force a/tsconfig.json c/tsconfig.json"]


def test_types_stage_with_nothing_to_compile_fails(check_ts, tmp_path, capsys):
    root = tmp_path / "app"
    solution = '{"references": [{"path": "./missing"}]}'  # refers to nothing that exists
    fake_tsc(root, {"tsconfig.json": solution}, tmp_path / "calls.log")
    assert check_ts.types_stage(check_ts.Target(root, "npm", ".", tail), []) == ("fail", False)
    assert (
        "types: no tsconfig.json compiles a source file in the project" in capsys.readouterr().out
    )


def test_node_tools_get_a_scrubbed_environment(check_ts, monkeypatch):
    scrubbed = (
        *POISONED_ENV,
        "NODE_PATH",
        "NODE_V8_COVERAGE",
        "ESLINT_USE_FLAT_CONFIG",
        "TS_NODE_PROJECT",
        "TSX_TSCONFIG_PATH",
        "TSESTREE_SINGLE_RUN",
        "VITEST_MODE",
        "VITEST",
        "UPDATE_SNAPSHOT",
        "npm_config_VITEST_MODULE_DIRECTORIES",
        "PRETTIER_EXPERIMENTAL_CLI",
        "EXPERIMENTAL_MONOCART",
        "FORCE_COLOR",
    )
    for name in scrubbed:
        monkeypatch.setenv(name, "1")
    monkeypatch.setenv("COVEN_KEPT", "yes")
    env = check_ts.node_env()
    assert [name for name in scrubbed if name in env] == []
    assert env["COVEN_KEPT"] == "yes"
    assert env["NO_COLOR"] == "1"


@pytest.mark.node
def test_an_eslint_config_that_prints_still_resolves(check, app):
    (app / "eslint.config.mjs").write_text(
        'console.log("loading");\nconsole.info("info");\nconsole.debug("debug");\n'
        'process.stdout.write("raw\\n");\n' + GOOD_ESLINT
    )
    assert problems(check, app) == []


@pytest.mark.node
def test_the_eslint_helper_names_why_eslint_cannot_load(check_ts, tmp_path):
    assert shutil.which("node") is not None, "node is needed"
    (tmp_path / "package.json").write_text("{}")
    found = check_ts._eslint_problems(tmp_path, tmp_path / "base.mjs", [tmp_path / "a.ts"])
    assert len(found) == 1
    assert found[0].startswith("resolving the ESLint configs failed (exit 1): cannot load ESLint")
    assert "Cannot find module 'eslint'" in found[0]
    assert "file://" not in found[0]


PNP = "Yarn Plug'n'Play is not supported: set nodeLinker: node-modules"


@pytest.mark.parametrize("marker", [".pnp.cjs", ".pnp.loader.mjs"])
def test_yarn_plug_n_play_is_named(check, tmp_path, monkeypatch, marker):
    app = ts_repo(tmp_path)
    all_on_path(monkeypatch)
    (app / marker).write_text("")
    assert problems(check, app, "yarn") == [PNP]


@pytest.mark.parametrize(
    ("yarnrc", "problem"),
    [
        ("yarnPath: .yarn/releases/yarn-4.cjs\n", PNP),  # berry links with PnP by default
        ("nodeLinker: pnp\n", PNP),
        (
            'nodeLinker: "node-modules"\n',
            "dependencies not installed: run (in app): yarn install --immutable",  # yarn 2+
        ),
    ],
)
def test_yarn_berry_without_node_modules_is_named(check, tmp_path, monkeypatch, yarnrc, problem):
    app = ts_repo(tmp_path)
    all_on_path(monkeypatch)
    (app / ".yarnrc.yml").write_text(yarnrc)
    (app / "yarn.lock").write_text(YARN_BERRY_LOCK)
    assert problems(check, app, "yarn") == [problem]


YARN_CLASSIC_LOCK = (
    "# THIS IS AN AUTOGENERATED FILE. DO NOT EDIT THIS FILE DIRECTLY.\n# yarn lockfile v1\n\n\n"
)
YARN_BERRY_LOCK = (
    '# This file is generated by running "yarn install" inside your project.\n'
    "# Manual changes might be lost - proceed with caution!\n\n"
    "__metadata:\n  version: 8\n  cacheKey: 10c0\n"
)


@pytest.mark.parametrize(
    ("lock", "command"),
    [
        (YARN_CLASSIC_LOCK, "yarn install --frozen-lockfile"),  # yarn 1 ignores --immutable
        (YARN_BERRY_LOCK, "yarn install --immutable"),
        ("{}", "yarn install --frozen-lockfile"),  # unknown: the flag both read
        (None, "yarn install --frozen-lockfile"),
    ],
)
def test_yarn_install_flag_follows_the_lockfile(check_ts, tmp_path, lock, command):
    if lock is not None:
        (tmp_path / "yarn.lock").write_text(lock)
    assert check_ts.install_command(tmp_path, "yarn") == command
    assert check_ts.install_command(tmp_path, "npm") == "npm ci"


FRAMEWORK_PROBLEM = (
    "not checked by the standard yet (tsc and the base ESLint config do not cover it)"
)


def test_framework_files_are_scanned_and_named(run_check, git_repo):
    typescript_repo(git_repo)
    marker = "@ts" + "-ignore"
    (git_repo / "src").mkdir()
    (git_repo / "src/App.vue").write_text(f"<script>\n// {marker}\n</script>\n")
    (git_repo / "src/Card.svelte").write_text("<p>card</p>\n")
    (git_repo / "src/page.astro").write_text("---\n---\n")
    (git_repo / "vendor").mkdir()
    (git_repo / "vendor/Old.vue").write_text("<p>old</p>\n")
    (git_repo / ".gitattributes").write_text("vendor/** linguist-vendored\n")
    commit_all(git_repo)
    out = run_check(git_repo, "fast")
    assert " suppressions=1 " in receipt(out)
    assert f"src/App.vue:2: // {marker}" in out.stdout
    for name in ("src/App.vue", "src/Card.svelte", "src/page.astro"):
        assert f"settings: {name}: {FRAMEWORK_PROBLEM}" in out.stdout
    assert "Old.vue" not in out.stdout


def test_no_supported_test_runner_is_a_setup_error(run_check, git_repo):
    (git_repo / "web").mkdir()
    typescript_repo(git_repo / "web")
    (git_repo / "web/package.json").write_text('{ "devDependencies": { "jest": "30.0.0" } }\n')
    commit_all(git_repo)
    out = run_check(git_repo, "fast")
    assert out.returncode == 2
    assert receipt(out).endswith(
        ' error="web: no supported test runner (vitest or c8 + node:test)"'
    )


MISSING_PROGRAMS = [
    (("git", "node"), "bun.lock", "fast", "bunx not found on PATH"),
    (
        ("git", "node"),
        "package-lock.json",
        "full",
        "uvx not found on PATH (full mode measures changed lines with diff-cover)",
    ),
]


@pytest.mark.parametrize("case", MISSING_PROGRAMS)
def test_a_missing_program_is_a_setup_error(run_check, git_repo, tmp_path, case):
    programs, lockfile, mode, error = case
    typescript_repo(git_repo, lockfile)
    commit_all(git_repo)
    path = only_programs(tmp_path / "bin", *programs)
    out = run_check(git_repo, mode, env={"PATH": str(path)})
    assert out.returncode == 2
    assert receipt(out).endswith(f' error="{error}"')


def test_missing_node_gives_setup_error(run_check, git_repo, tmp_path):
    typescript_repo(git_repo)
    commit_all(git_repo)
    path = only_programs(tmp_path / "bin", "git")
    out = run_check(git_repo, "fast", env={"PATH": str(path)})
    assert out.returncode == 2
    assert receipt(out).endswith(' error="node not found on PATH"')


# --- tools the project cannot switch off ------------------------------------------


def tail(stage: str, output: str) -> None:
    print(f"--- {stage} failed ---\n{output}")


UGLY = "export const ugly   =  1;\n"
# Repo settings that would make Prettier pass a badly formatted file.
PRETTIER_ESCAPES = [
    (".prettierignore", "src/ugly.ts\n", UGLY),
    (".prettierrc", '{ "requirePragma": true }\n', UGLY),
    ("prettier.config.mjs", "export default { requirePragma: true };\n", UGLY),
    ("src/.prettierrc", '{ "requirePragma": true }\n', UGLY),
    (
        ".prettierrc",
        '{ "overrides": [{ "files": "*.ts", "options": { "requirePragma": true } }] }\n',
        UGLY,
    ),
    (
        "prettier.config.mjs",
        "export default { checkIgnorePragma: true };\n",
        "/** @" + "noformat */\n" + UGLY,
    ),
]


@pytest.mark.node
@pytest.mark.parametrize("escape", PRETTIER_ESCAPES)
def test_repo_config_cannot_turn_the_format_stage_off(check_ts, tmp_path, node_modules, escape):
    name, config, source = escape
    (tmp_path / "src").mkdir()
    (tmp_path / "node_modules").symlink_to(node_modules, target_is_directory=True)
    (tmp_path / "src/ugly.ts").write_text(source)
    (tmp_path / name).write_text(config)
    target = check_ts.Target(tmp_path, "npm", ".", tail)
    assert check_ts.format_stage(target, [Path("src/ugly.ts")]) == ("fail", False)
    (tmp_path / "src/ugly.ts").write_text(source.replace("   =  ", " = "))
    assert check_ts.format_stage(target, [Path("src/ugly.ts")]) == ("ok", True)


@pytest.mark.node
def test_types_stage_ignores_a_build_info_newer_than_the_sources(check_ts, tmp_path, node_modules):
    # `tsc -b` trusts a .tsbuildinfo newer than an edited source.
    (tmp_path / "node_modules").symlink_to(node_modules, target_is_directory=True)
    (tmp_path / "lib").mkdir()
    (tmp_path / "src").mkdir()
    (tmp_path / "lib/tsconfig.json").write_text(
        '{ "compilerOptions": { "composite": true, "strict": true, "module": "nodenext", '
        '"outDir": "../build/lib" }, "include": ["."] }\n'
    )
    (tmp_path / "lib/a.ts").write_text("export const a: number = 1;\n")
    (tmp_path / "tsconfig.json").write_text(
        '{ "compilerOptions": { "composite": true, "strict": true, "module": "nodenext", '
        '"outDir": "build/app" }, "include": ["src"], "references": [{ "path": "./lib" }] }\n'
    )
    main = tmp_path / "src/main.ts"
    main.write_text('import { a } from "../lib/a.js";\n\nexport const b: number = a;\n')
    target = check_ts.Target(tmp_path, "npm", ".", tail)
    assert check_ts.types_stage(target, []) == ("ok", True)
    assert list((tmp_path / "build").rglob("*.tsbuildinfo"))
    main.write_text('import { a } from "../lib/a.js";\n\nexport const b: string = a;\n')
    os.utime(main, (946684800, 946684800))  # older than the build info
    assert check_ts.types_stage(target, []) == ("fail", False)


@pytest.mark.node
def test_a_weak_config_a_non_solution_tsconfig_refers_to_is_named(check, app):
    # references are followed from every tsconfig, not only solution ones.
    (app / "lib").mkdir()
    (app / "lib/b.ts").write_text(INDEX)
    (app / "tsconfig.lib.json").write_text(
        GOOD_TSCONFIG.replace('"noEmit": true', '"composite": true, "strict": false').replace(
            '["src"]', '["lib"]'
        )
    )
    (app / "tsconfig.json").write_text(
        GOOD_TSCONFIG.replace(
            '"include": ["src"]',
            '"include": ["src"], "references": [{ "path": "./tsconfig.lib.json" }]',
        )
    )
    assert problems(check, app) == [
        "tsconfig.lib.json compilerOptions.strict: must be true (resolved: false)"
    ]


@pytest.mark.node
def test_only_tool_configs_are_exempt_from_the_coverage_report(check_ts, tmp_path, node_modules):
    # `src/app.config.ts` is application code, not a tool's config.
    (tmp_path / "node_modules").symlink_to(node_modules, target_is_directory=True)
    (tmp_path / "package.json").write_text("{}")
    changed = [Path("src/app.config.ts"), Path("vite.config.ts"), Path("web/eslint.config.mjs")]
    for rel in changed:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("export const port = 3000;\n")
    gap = check_ts.coverage_gap(tmp_path, "c8", changed, set())
    assert gap is not None
    assert gap.startswith("not measured: src/app.config.ts (changed, but absent")


@pytest.mark.parametrize(
    ("rel", "runner", "test"),
    [
        ("tests/helpers.ts", "c8", True),
        ("tests/helpers.ts", "vitest", True),
        ("src/__tests__/util.ts", "vitest", True),
        ("src/__tests__/util.ts", "c8", True),
        ("test/setup.ts", "vitest", True),
        ("src/a.test.ts", "vitest", True),
        ("src/a.spec.tsx", "vitest", True),
        ("src/a_test.ts", "c8", True),
        ("src/test-a.mjs", "c8", True),
        ("src/latest.ts", "c8", False),
        ("src/contest/a.ts", "vitest", False),
        ("src/a_test.ts", "vitest", False),
    ],
)
def test_test_files_follow_the_runners_conventions(check_ts, rel, runner, test):
    assert check_ts.is_test_file(Path(rel), runner) is test


@pytest.mark.node
def test_a_file_eslint_ignores_is_named_even_if_tsc_does_not_compile_it(check, check_repo, app):
    # every listed TypeScript and JavaScript file must be linted.
    (app / "scripts").mkdir()
    (app / "scripts/x.js").write_text("export const x = 1;\n")
    (app / "eslint.config.mjs").write_text(
        COVEN_IMPORT + 'export default [{ ignores: ["scripts/**"] }, ...coven];\n'
    )
    project = check_repo.Project(app, "typescript", "npm")
    listed = [app / "scripts/x.js", app / "src/index.ts", app / "eslint.config.mjs"]
    found = check.load_ts().ts_settings_problems(project, app.parent, [], listed)
    assert found == ["scripts/x.js: ignored by ESLint"]
