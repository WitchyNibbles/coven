"""Fake `opencode` and `npm` programs: tests never run the real ones."""

from __future__ import annotations

import json
import shutil
import stat
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Final, Literal

from coven.opencode import superpowers

Serve = Literal["ok", "ignore-term", "silent"]
# The non-secret variables the fake npm reports the value of.
NPM_PATH_VARS: Final = (
    "XDG_DATA_HOME",
    "XDG_CONFIG_HOME",
    "XDG_STATE_HOME",
    "XDG_CACHE_HOME",
    "TMPDIR",
    "OPENCODE_CONFIG_DIR",
)

_OPENCODE_BODY: Final = """
import json
import os
import signal
import sys
import time

VERSION = {version!r}
PATHS = {paths!r}
SERVE = {serve!r}


def default_paths():
    env = os.environ
    home = env.get("OPENCODE_TEST_HOME") or env.get("HOME", "")
    data = env.get("XDG_DATA_HOME", home + "/.local/share") + "/opencode"
    cache = env.get("XDG_CACHE_HOME", home + "/.cache") + "/opencode"
    state = env.get("XDG_STATE_HOME", home + "/.local/state") + "/opencode"
    config = env.get("OPENCODE_CONFIG_DIR") or env.get("XDG_CONFIG_HOME", home + "/.config") + "/opencode"
    tmp = env.get("TMPDIR", "/tmp") + "/opencode"
    return {{
        "home": home, "data": data, "cache": cache, "config": config, "state": state,
        "tmp": tmp, "bin": cache + "/bin", "log": data + "/log", "repos": data + "/repos",
        "db": data + "/opencode.db",
    }}


def serve():
    if SERVE == "ignore-term":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    if SERVE != "silent":
        print("server listening on http://127.0.0.1:1", flush=True)
    while True:
        time.sleep(0.1)


def main(argv):
    if argv[:1] == ["--version"]:
        print(f"opencode v{{VERSION}}")
    elif argv[:2] == ["debug", "paths"]:
        shown = {{**default_paths(), **PATHS}}
        for name, value in shown.items():
            print(f"{{name:<10}} {{value}}")
    elif argv[:1] == ["serve"]:
        serve()
    else:
        print(json.dumps({{"args": argv}}))
    return 0


sys.exit(main(sys.argv[1:]))
"""

_NPM_BODY: Final = """
import json
import os
import stat
import sys
from pathlib import Path

LOG = Path({log!r})
ENV_LOG = Path({env_log!r})
PATHS_LOG = Path({paths_log!r})
# Path variables only: they hold no secret, so the test may read their values.
PATH_VARS = {path_vars!r}
OPENCODE = {opencode!r}

with LOG.open("a") as stream:
    stream.write(" ".join(sys.argv[1:]) + "\\n")
with ENV_LOG.open("a") as stream:
    for name in filter(None, os.environ.get("FAKE_NPM_WATCH", "").split(",")):
        stream.write(name + (" set" if name in os.environ else " unset") + "\\n")
with PATHS_LOG.open("w") as stream:
    stream.write(json.dumps({{
        "values": {{n: os.environ.get(n) for n in PATH_VARS}},
        "opencode_names": sorted(n for n in os.environ if n.startswith("OPENCODE_")),
        "tmp_exists": Path(os.environ.get("TMPDIR", "/nonexistent")).is_dir(),
    }}))
exit_code = os.environ.get("FAKE_NPM_EXIT")
if exit_code:
    for number in (1, 2, 3):
        print(f"npm ERR! line {{number}}", file=sys.stderr)
    sys.exit(int(exit_code))
args = sys.argv[1:]
prefix = Path(args[args.index("--prefix") + 1])
spec = args[-1]
version = os.environ.get("FAKE_OPENCODE_VERSION") or spec.rsplit("@", 1)[1]
binary = prefix / "node_modules" / ".bin" / "opencode"
binary.parent.mkdir(parents=True, exist_ok=True)
binary.write_text(OPENCODE.replace("__VERSION__", version))
binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
"""


def _opencode_source(version: str, paths: Mapping[str, str] | None, serve: Serve) -> str:
    body = _OPENCODE_BODY.format(version=version, paths=dict(paths or {}), serve=serve)
    return f"#!{sys.executable}\n{body}"


def _make_executable(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def fake_opencode(
    dir: Path,
    version: str = "2.3.2",
    *,
    paths: Mapping[str, str] | None = None,
    serve: Serve = "ok",
) -> Path:
    """An `opencode` that answers `--version`, `debug paths` and `serve`.

    `paths` overrides lines of `debug paths`; `serve` is how `serve` behaves: it listens until
    killed ("ok"), ignores SIGTERM ("ignore-term") or prints nothing ("silent").
    """
    return _make_executable(dir / "opencode", _opencode_source(version, paths, serve))


def npm_log(dir: Path) -> Path:
    """The file the fake npm in `dir` writes its argv to, one call per line."""
    return dir / "npm.log"


def npm_env_log(dir: Path) -> Path:
    """For each name in `$FAKE_NPM_WATCH` (comma separated), `NAME set` or `NAME unset`: by name only."""
    return dir / "npm.env"


def npm_paths_log(dir: Path) -> Path:
    """JSON the fake npm writes: the values of PATH_VARS, the OPENCODE_ names it saw, TMPDIR's state."""
    return dir / "npm.paths.json"


def fake_npm(dir: Path) -> Path:
    """An `npm` that logs its argv and installs a fake opencode into `--prefix`.

    The installed opencode reports the version of the package spec it was given, or
    `$FAKE_OPENCODE_VERSION` when that is set. It notes whether each variable named in
    `$FAKE_NPM_WATCH` is in its env. `$FAKE_NPM_EXIT` makes it print three stderr
    lines and exit with that code.
    """
    opencode = _opencode_source("__VERSION__", None, "ok")
    body = _NPM_BODY.format(
        log=str(npm_log(dir)),
        env_log=str(npm_env_log(dir)),
        paths_log=str(npm_paths_log(dir)),
        path_vars=NPM_PATH_VARS,
        opencode=opencode,
    )
    return _make_executable(dir / "npm", f"#!{sys.executable}\n{body}")


SUPERPOWERS_KEY: Final = "superpowers@claude-plugins-official"


def install_superpowers(
    plugins: Path, names: tuple[str, ...] = superpowers.SKILLS, *, as_list: bool = True
) -> Path:
    """A fake Claude Code plugins folder with superpowers installed; returns its `skills/`."""
    install = plugins / "cache" / "superpowers" / "5.0.0"
    for name in names:
        folder = install / "skills" / name
        folder.mkdir(parents=True)
        body = f"---\nname: {name}\n---\nNext, use superpowers:writing-plans and `Read`.\n"
        (folder / "SKILL.md").write_text(body)
        (folder / "notes.txt").write_text("kept as is: superpowers:writing-plans\n")
    entry = {"installPath": str(install), "version": "5.0.0"}
    data = {"version": 2, "plugins": {SUPERPOWERS_KEY: [entry] if as_list else entry}}
    (plugins / "installed_plugins.json").write_text(json.dumps(data))
    return install / "skills"


REAL_GIT: Final = shutil.which("git")


def git_on_path(folder: Path) -> Path:
    """Make the real `git` reachable from `folder`, for a test whose PATH is only that folder."""
    assert REAL_GIT is not None, "the tests need git"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "git").symlink_to(REAL_GIT)
    return folder


def superpowers_repo(folder: Path, names: tuple[str, ...] = superpowers.SKILLS) -> tuple[Path, str]:
    """A local git repo standing in for upstream superpowers, tagged with the pinned tag.

    Its skills say they come from the clone. Returns the repo and the commit the tag points at.
    """
    for name in names:
        skill = folder / "skills" / name
        skill.mkdir(parents=True)
        body = f"---\nname: {name}\n---\nFrom the clone: use superpowers:writing-plans.\n"
        (skill / "SKILL.md").write_text(body)
    steps = [
        ["init", "--quiet"],
        ["add", "."],
        ["commit", "--quiet", "-m", "superpowers"],
        ["tag", "-a", superpowers.PINNED_TAG, "-m", superpowers.PINNED_TAG],
    ]
    assert REAL_GIT is not None, "the tests need git"
    for step in steps:
        subprocess.run([REAL_GIT, "-C", str(folder), *step], check=True, capture_output=True)
    head = subprocess.run(
        [REAL_GIT, "-C", str(folder), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return folder, head.stdout.strip()
