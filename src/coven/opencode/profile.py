"""The OpenCode profile: where it lives, which roles it knows and what `setup` may accept."""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal
from urllib.parse import urlsplit

from coven import paths, safe_json
from coven.errors import CovenError, warn

PINNED_VERSION: Final = "2.3.2"
ROLES: Final = ("lead", "implementer", "reviewer", "explorer", "final-reviewer", "title")
AGENT_ROLES: Final = ("explorer", "implementer", "reviewer", "final-reviewer")
OPENCODE_KEY_ENV: Final = "OPENCODE_API_KEY"
PRESETS: Final = ("litellm", "opencode")
FORMAT: Final = 1

_ENV_NAME = re.compile(r"[A-Z_][A-Z0-9_]{0,63}")
_RESET_HINT = "re-run coven opencode setup"


class OpencodeError(CovenError):
    """An OpenCode setup, install or run failure with a message meant for the user."""


@dataclass(frozen=True)
class Layout:
    """Every path inside the profile folder `root`."""

    root: Path

    @property
    def config_home(self) -> Path:
        return self.root / "config"

    @property
    def config_dir(self) -> Path:
        return self.config_home / "opencode"

    @property
    def data(self) -> Path:
        return self.root / "data"

    @property
    def cache(self) -> Path:
        return self.root / "cache"

    @property
    def state(self) -> Path:
        return self.root / "state"

    @property
    def home(self) -> Path:
        return self.root / "home"

    @property
    def tmp(self) -> Path:
        return self.root / "tmp"

    @property
    def plugin_dir(self) -> Path:
        return self.root / "plugin" / "coven"

    @property
    def profile_file(self) -> Path:
        return self.root / "profile.json"

    def runtime_dir(self, version: str) -> Path:
        return self.root / "runtime" / version

    def runtime_binary(self, version: str) -> Path:
        return self.runtime_dir(version) / "node_modules" / ".bin" / "opencode"


def layout() -> Layout:
    return Layout(paths.opencode_home())


@dataclass(frozen=True)
class Profile:
    """What `setup` chose. `models` holds only the roles given; the others use `lead`."""

    preset: Literal["litellm", "opencode"]
    base_url: str | None
    key_env: str
    models: Mapping[str, str]

    def model(self, role: str) -> str:
        return self.models.get(role) or self.models["lead"]

    @property
    def provider_id(self) -> str:
        return self.preset


def _check_model_id(role: str, model_id: str) -> None:
    if not model_id:
        raise OpencodeError(f"--model {role}= is empty: give a model id after the =")
    if any(char.isspace() for char in model_id):
        raise OpencodeError(f"--model {role}= id must have no spaces")


def _parse_pair(pair: str) -> tuple[str, str]:
    role, separator, model_id = pair.partition("=")
    if not separator:
        raise OpencodeError(f"--model {pair!r} must be ROLE=ID, for example lead=my-model")
    if role not in ROLES:
        raise OpencodeError(f"unknown role {role!r} in --model: use one of {', '.join(ROLES)}")
    _check_model_id(role, model_id)
    return role, model_id


def _parse_models(pairs: Sequence[str]) -> dict[str, str]:
    models: dict[str, str] = {}
    for pair in pairs:
        role, model_id = _parse_pair(pair)
        if role in models:
            raise OpencodeError(f"--model {role}= was given twice")
        models[role] = model_id
    if "lead" not in models:
        raise OpencodeError("--model lead=ID is required: the other roles fall back to it")
    return models


def _check_key_env(name: str) -> str:
    if not _ENV_NAME.fullmatch(name):
        raise OpencodeError(
            "--key-env takes the name of an environment variable, such as GATEWAY_KEY "
            "(capitals, digits and _, at most 64 characters): the name, not its value"
        )
    if name.startswith("OPENCODE_") and name != OPENCODE_KEY_ENV:
        raise OpencodeError(
            f"--key-env must not be an OPENCODE_ setting: OpenCode reads those as config, "
            f"and {OPENCODE_KEY_ENV} is the only one allowed"
        )
    return name


def _check_base_url(url: str, *, announce: bool = True) -> str:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise OpencodeError("--base-url must start with http:// or https:// and name a host")
    if "@" in parts.netloc or "?" in url or "#" in url:
        raise OpencodeError(
            "--base-url must not carry credentials, a query or a fragment: "
            "the key is read from the --key-env variable"
        )
    trimmed = url.removesuffix("/")
    if announce and not urlsplit(trimmed).path.endswith("/v1"):
        warn("--base-url does not end in /v1: OpenCode appends /chat/completions to it as given")
    return trimmed


def _checked_profile(
    preset: str,
    base_url: str | None,
    key_env: str | None,
    models: dict[str, str],
    *,
    from_file: bool,
) -> Profile:
    """The profile for these parts: the one set of rules `setup` and `load_profile` share."""
    if preset == "opencode":
        if base_url is not None:
            raise OpencodeError("--preset opencode takes no --base-url")
        if key_env != (OPENCODE_KEY_ENV if from_file else None):
            raise OpencodeError(
                f"--preset opencode takes no --key-env: it reads {OPENCODE_KEY_ENV}"
            )
        return Profile("opencode", None, OPENCODE_KEY_ENV, models)
    if preset != "litellm":
        raise OpencodeError(f"unknown preset {preset!r}: use one of {', '.join(PRESETS)}")
    if base_url is None:
        raise OpencodeError("--preset litellm needs --base-url")
    if key_env is None:
        raise OpencodeError("--preset litellm needs --key-env")
    url = _check_base_url(base_url, announce=not from_file)
    return Profile("litellm", url, _check_key_env(key_env), models)


def make_profile(
    preset: str, base_url: str | None, key_env: str | None, model_pairs: Sequence[str]
) -> Profile:
    """The checked profile for `setup`'s flags, or an OpencodeError saying what to change."""
    return _checked_profile(preset, base_url, key_env, _parse_models(model_pairs), from_file=False)


def save_profile(layout: Layout, profile: Profile) -> None:
    """Write `profile.json` (no key, only the env var's name) through a temp file and a rename."""
    document = {
        "format": FORMAT,
        "preset": profile.preset,
        "baseURL": profile.base_url,
        "keyEnv": profile.key_env,
        "models": dict(profile.models),
    }
    layout.root.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(dir=layout.root, prefix="profile.", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(document, indent=2) + "\n")
        os.replace(temp_name, layout.profile_file)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise


def _from_document(document: object) -> Profile | None:
    """The profile a parsed `profile.json` describes, or None when it is not one.

    An OpencodeError says which `setup` rule the document breaks, never the bad value.
    """
    if not isinstance(document, dict) or document.get("format") != FORMAT:
        return None
    preset, base_url = document.get("preset"), document.get("baseURL")
    key_env, models = document.get("keyEnv"), document.get("models")
    if preset not in PRESETS or not isinstance(key_env, str) or not isinstance(models, dict):
        return None
    if base_url is not None and not isinstance(base_url, str):
        return None
    if "lead" not in models or any(
        role not in ROLES or not isinstance(model_id, str) or not model_id
        for role, model_id in models.items()
    ):
        return None
    for role, model_id in models.items():
        _check_model_id(role, model_id)
    return _checked_profile(preset, base_url, key_env, dict(models), from_file=True)


def load_profile(layout: Layout) -> Profile | None:
    """The saved profile, None when setup has not run, an OpencodeError when the file is bad."""
    try:
        text = layout.profile_file.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as error:
        raise OpencodeError(f"cannot read {layout.profile_file}: {error}; {_RESET_HINT}") from error
    try:
        loaded = _from_document(safe_json.loads(text))
    except OpencodeError as error:
        raise OpencodeError(
            f"{layout.profile_file} is not valid ({error}); {_RESET_HINT}"
        ) from error
    except ValueError:
        loaded = None
    if loaded is None:
        raise OpencodeError(f"{layout.profile_file} is not a valid coven profile; {_RESET_HINT}")
    return loaded
