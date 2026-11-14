import tomllib

from support import REPO

from coven.project import drift

STANDARD = REPO / "standard"
# What a Python repo gets in `.coven/`, with the folder of `standard/` each file comes from.
INSTALLED = {
    "check.py": "",
    "check_python.py": "",
    "check_repo.py": "",
    "check_stages.py": "",
    "check_ts.py": "",
    "ruff.toml": "python",
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
