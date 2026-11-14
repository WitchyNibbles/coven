"""The install-smoke job's files, checked statically: the job itself needs Docker and a network."""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from support import REPO

from coven import claude_version

SMOKE = REPO / "ci" / "install-smoke"
DOCKERFILE = SMOKE / "Dockerfile"
RUN_SH = SMOKE / "run.sh"
WORKFLOW = REPO / ".github" / "workflows" / "install-smoke.yml"

PINNED_USES = re.compile(r"^\s*-?\s*uses:\s*\S+@[0-9a-f]{40}(\s+#.*)?$")
SECRET_WORDS = ("secrets.", "API_KEY", "ANTHROPIC_", "CLAUDE_CODE_OAUTH", "GITHUB_TOKEN")


def pinned_version() -> str:
    return claude_version.dotted(claude_version.LAST_TESTED)


def lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def test_workflow_has_no_secrets_and_pins_claude_version() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    for word in SECRET_WORDS:
        assert word not in text, word
    assert "pull_request_target" not in text
    assert re.search(r"^permissions:\n  contents: read$", text, re.MULTILINE)
    # Every third-party action is pinned by full commit SHA, as coven-check.yml does.
    uses = [line for line in lines(WORKFLOW) if "uses:" in line]
    assert uses
    for line in uses:
        assert PINNED_USES.match(line), line
    # The version Claude Code is installed at is the one coven was tested on, read from the code.
    assert re.search(
        rf'^\s+COVEN_SMOKE_CLAUDE_VERSION: "{re.escape(pinned_version())}"$', text, re.MULTILINE
    )
    assert '--build-arg COVEN_SMOKE_CLAUDE_VERSION="$COVEN_SMOKE_CLAUDE_VERSION"' in text


def test_workflow_checkout_keeps_no_credentials() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert re.search(r"actions/checkout@\S+.*\n\s+with:\n\s+persist-credentials: false\n", text)


def test_workflow_requires_the_install_and_passes_the_switch_on() -> None:
    # The first CI run (2026-11-14) installed the plugin, so a failed install now fails the job.
    code = [x for x in lines(WORKFLOW) if not x.lstrip().startswith("#")]
    assert any(re.search(r'COVEN_SMOKE_REQUIRE_INSTALL:\s*"1"', x) for x in code)
    assert any("--env COVEN_SMOKE_REQUIRE_INSTALL" in x for x in code)


def test_workflow_builds_the_image_and_runs_the_script_on_the_checkout() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "docker build" in text
    assert "ci/install-smoke" in text
    assert re.search(r"docker run .*:/src:ro", text)


def test_dockerfile_pins_its_inputs() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    from_lines = [line for line in lines(DOCKERFILE) if line.startswith("FROM ")]
    assert len(from_lines) == 1
    assert re.fullmatch(r"FROM debian:[a-z]+-slim@sha256:[0-9a-f]{64}", from_lines[0])
    # The Claude Code version has no default, so a build without the argument stops.
    assert re.search(r"^ARG COVEN_SMOKE_CLAUDE_VERSION$", text, re.MULTILINE)
    assert 'test -n "$COVEN_SMOKE_CLAUDE_VERSION"' in text
    assert "@anthropic-ai/claude-code@${COVEN_SMOKE_CLAUDE_VERSION}" in text
    assert re.search(r"COPY --from=ghcr\.io/astral-sh/uv:\d+\.\d+\.\d+@sha256:[0-9a-f]{64} ", text)
    assert "sha256sum -c" in text  # the node tarball is checked
    assert "latest" not in text
    assert "--exclude" not in text
    assert "USER " in text  # a stranger's machine is not root


def test_run_sh_is_posix_and_set_eu() -> None:
    script = lines(RUN_SH)
    assert script[0] == "#!/bin/sh"
    assert "set -eu" in script
    assert os.access(RUN_SH, os.X_OK)
    assert subprocess.run(["sh", "-n", str(RUN_SH)], check=False).returncode == 0
    text = RUN_SH.read_text(encoding="utf-8")
    for bashism in ("[[", "$'", "<<<", "function ", "local ", "source "):
        assert bashism not in text, bashism
    # The steps the job promises.
    for step in (
        "claude plugin validate",
        "--strict",
        "claude plugin marketplace add",
        "claude plugin install superpowers@claude-plugins-official",
        "claude plugin install coven@coven",
        "claude plugin marketplace add anthropics/claude-plugins-official",
        "bin/coven",
        "doctor",
        "project . --check",
        "python3 .coven/check.py full --base",
        "FALLBACK",
    ):
        assert step in text, step


def test_run_sh_marketplace_add_of_the_checkout_is_fatal_not_a_fallback() -> None:
    text = RUN_SH.read_text(encoding="utf-8")
    assert re.search(r'^claude plugin marketplace add "\$SRC" \|\| fail ', text, re.MULTILINE)
    chain = text.split("install_plugin() {", 1)[1].split("\n}\n", 1)[0]
    assert 'marketplace add "$SRC"' not in chain
    assert "claude plugin install coven@coven" in chain


def test_run_sh_fallback_raises_a_warning_annotation_and_can_be_made_fatal() -> None:
    text = RUN_SH.read_text(encoding="utf-8")
    assert (
        "::warning title=install smoke FALLBACK::plugin install failed, "
        "the install itself was NOT tested"
    ) in text
    assert "COVEN_SMOKE_REQUIRE_INSTALL" in text


def fake_claude(directory: Path, failing: str) -> Path:
    """A `claude` that validates and reports its version, and fails `failing` commands."""
    directory.mkdir()
    script = directory / "claude"
    script.write_text(
        "#!/bin/sh\n"
        f'case "$1 $2 $3" in\n  "--version "*) echo "{pinned_version()} (Claude Code)" ;;\n'
        '  "plugin validate "*) ;;\n'
        f'  {failing}) echo "fake: login required" >&2; exit 1 ;;\n'
        "  *) ;;\nesac\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return directory


def run_with_fake_claude(
    tmp_path: Path, failing: str, **extra: str
) -> subprocess.CompletedProcess[str]:
    path = fake_claude(tmp_path / "fakebin", failing)
    env = {
        **os.environ,
        "PATH": f"{path}:{os.environ['PATH']}",
        "CLAUDE_CONFIG_DIR": str(tmp_path / "claude-config"),
        "COVEN_SRC": str(REPO),
        "TMPDIR": str(tmp_path),
        **extra,
    }
    return subprocess.run(
        ["sh", str(RUN_SH)], env=env, capture_output=True, text=True, check=False, cwd=tmp_path
    )


def test_run_sh_stops_when_adding_the_checkout_as_a_marketplace_fails(tmp_path: Path) -> None:
    done = run_with_fake_claude(tmp_path, '"plugin marketplace add"')
    assert done.returncode != 0
    assert "FALLBACK" not in done.stdout
    assert "marketplace" in done.stderr


def test_run_sh_stops_when_install_fails_and_it_is_required(tmp_path: Path) -> None:
    done = run_with_fake_claude(
        tmp_path, '"plugin install coven@coven"', COVEN_SMOKE_REQUIRE_INSTALL="1"
    )
    assert done.returncode != 0
    assert "COVEN_SMOKE_REQUIRE_INSTALL" in done.stderr
    assert "FALLBACK" not in done.stdout


def test_run_sh_refuses_to_run_without_an_isolated_claude_dir(tmp_path: Path) -> None:
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CONFIG_DIR"}
    done = subprocess.run(
        ["sh", str(RUN_SH)], env=env, capture_output=True, text=True, check=False, cwd=tmp_path
    )
    assert done.returncode != 0
    assert "CLAUDE_CONFIG_DIR" in done.stderr
    assert not (tmp_path / ".claude").exists()


def test_run_sh_refuses_the_default_claude_dir(tmp_path: Path) -> None:
    env = {**os.environ, "CLAUDE_CONFIG_DIR": f"{os.environ['HOME']}/.claude"}
    done = subprocess.run(
        ["sh", str(RUN_SH)], env=env, capture_output=True, text=True, check=False, cwd=tmp_path
    )
    assert done.returncode != 0
    assert "default Claude Code folder" in done.stderr
    assert not (Path(os.environ["HOME"]) / ".claude").exists()


@pytest.mark.shellcheck
def test_run_sh_passes_shellcheck() -> None:
    venv_bin = str(Path(sys.executable).parent)
    shellcheck = shutil.which("shellcheck", path=venv_bin) or shutil.which("shellcheck")
    if shellcheck is None:
        pytest.fail("shellcheck is missing: run `uv sync` (the dev group holds shellcheck-py)")
    done = subprocess.run(
        [shellcheck, "--shell=sh", str(RUN_SH)], capture_output=True, text=True, check=False
    )
    assert done.returncode == 0, done.stdout


def test_known_gaps_describes_the_smoke_job_and_its_limits() -> None:
    text = " ".join((REPO / "docs" / "known-gaps.md").read_text(encoding="utf-8").split())
    assert "CI does not run `claude plugin validate`" not in text
    assert "install smoke" in text.lower()
    assert "first run" in text
    assert "--plugin-dir" in text
    assert "not hermetic" in text
    assert "official marketplace" in text
    assert "hooks, skills and agents" in text
    assert "FALLBACK" in text
    assert "warning annotation" in text
    assert "COVEN_SMOKE_REQUIRE_INSTALL" in text


def test_readme_says_what_to_do_when_the_official_marketplace_is_unknown() -> None:
    text = " ".join((REPO / "README.md").read_text(encoding="utf-8").split())
    assert "claude plugin marketplace add anthropics/claude-plugins-official" in text
