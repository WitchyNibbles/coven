"""Shared helpers for the doctor tests: a good profile, a stand-in server reader, name lookups."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from coven.opencode.canary import ServerOptions, ServerView
from coven.opencode.checks import Outcome, run_doctor
from coven.opencode.profile import Layout

NAMES = [
    "runtime",
    "paths",
    "config",
    "key",
    "plugin",
    "skills",
    "coven root",
    "project config",
    "superpowers",
]
KEY_NAME = "GATEWAY_KEY"
KEY_VALUE = "sk-live-value-1234567890"
LITELLM_FLAGS = [
    "--preset",
    "litellm",
    "--base-url",
    "http://127.0.0.1:4000/v1",
    "--key-env",
    KEY_NAME,
    "--model",
    "lead=big",
    "--model",
    "explorer=small",
]
GOOD_VIEW = ServerView(
    [{"id": "coven", "state": {"status": "active"}}],
    [{"id": "opencode", "path": "skill://opencode/SKILL.md"}],
)


@dataclass(frozen=True)
class Good:
    """A set-up profile, the folder of its fake binary and a work dir to run doctor from."""

    layout: Layout
    bin_dir: Path
    cwd: Path


@dataclass
class Reader:
    """A stand-in for read_server: records what it was given and answers with `view`."""

    view: ServerView = GOOD_VIEW
    error: Exception | None = None
    calls: list[tuple[Path, Mapping[str, str], Path, ServerOptions]] = field(default_factory=list)

    def __call__(
        self, binary: Path, env: Mapping[str, str], cwd: Path, opts: ServerOptions
    ) -> ServerView:
        self.calls.append((binary, env, cwd, opts))
        if self.error:
            raise self.error
        return self.view


def results_by_name(good: Good, reader: Reader | None = None) -> dict[str, Outcome]:
    done = run_doctor(good.layout, good.cwd, read=reader or Reader())
    assert [name for name, _ in done] == NAMES
    return dict(done)
