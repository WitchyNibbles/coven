import pytest

from coven import cli
from coven.errors import CovenError


def test_main_prints_a_coven_error_without_a_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    def fail(_args: object) -> int:
        raise CovenError("the thing broke")

    monkeypatch.setattr("coven.project.run", fail)
    assert cli.main(["project", "."]) == 1
    captured = capsys.readouterr()
    assert captured.err == "coven: the thing broke\n"
    assert captured.out == ""


def test_main_returns_the_errors_exit_code(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    class Special(CovenError):
        exit_code = 7

    def fail(_args: object) -> int:
        raise Special("special")

    monkeypatch.setattr("coven.project.run", fail)
    assert cli.main(["project", "."]) == 7
    assert capsys.readouterr().err == "coven: special\n"


def test_main_returns_the_handlers_exit_code(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("coven.project.run", lambda _args: 3)
    assert cli.main(["project", "."]) == 3


def test_a_missing_command_is_a_usage_error(capsys: pytest.CaptureFixture[str]):
    assert cli.main([]) == 2
    assert "usage: coven" in capsys.readouterr().err


def test_help_names_the_project_command(capsys: pytest.CaptureFixture[str]):
    assert cli.main(["--help"]) == 0
    assert "project" in capsys.readouterr().out


def test_coven_error_defaults_to_exit_code_1():
    assert CovenError("x").exit_code == 1
