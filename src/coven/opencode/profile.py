"""The OpenCode profile: where it lives, which roles it knows and what `setup` may accept."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Final, Literal
from urllib.parse import urlsplit

from coven import paths, safe_json
from coven.errors import CovenError, warn
from coven.settings import write_atomic

PINNED_VERSION: Final = "2.3.2"
ROLES: Final = (
    "lead",
    "implementer",
    "reviewer",
    "explorer",
    "final-reviewer",
    "title",
    "project-manager",
)
AGENT_ROLES: Final = ("explorer", "implementer", "reviewer", "final-reviewer")
# The roles whose model the generated config names under `agents`.
MODEL_ROLES: Final = (*AGENT_ROLES, "project-manager", "title")
OPENCODE_KEY_ENV: Final = "OPENCODE_API_KEY"
PRESETS: Final = ("litellm", "opencode")
FORMAT: Final = 1
# What OpenCode 2.3.2 assumes for a model whose config names no context limit.
ASSUMED_CONTEXT: Final = 200_000
MAX_LIMIT: Final = 10_000_000

_ENV_NAME = re.compile(r"[A-Z_][A-Z0-9_]{0,63}")
RESET_HINT = "re-run coven opencode setup"


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
    def serve_dir(self) -> Path:
        return self.state / "serve"

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

    def folders(self) -> tuple[Path, ...]:
        """The folders the run env points at; the postinstall step makes a temp folder in `tmp`."""
        return (self.config_dir, self.data, self.cache, self.state, self.home, self.tmp)

    def runtime_dir(self, version: str) -> Path:
        return self.root / "runtime" / version

    def superpowers_dir(self, tag: str) -> Path:
        return self.root / "superpowers" / tag

    def runtime_binary(self, version: str) -> Path:
        return self.runtime_dir(version) / "node_modules" / ".bin" / "opencode"


def layout() -> Layout:
    return Layout(paths.opencode_home())


@dataclass(frozen=True)
class Limit:
    """A model's token window: what it takes in (`context`) and what it may write (`output`)."""

    context: int
    output: int | None = None

    def data(self) -> dict[str, int]:
        """The JSON object for it, in profile.json and in the config: no `output` when unset."""
        return {"context": self.context} | (
            {"output": self.output} if self.output is not None else {}
        )


@dataclass(frozen=True)
class Profile:
    """What `setup` chose. `models` holds only the roles given; the others use `lead`.

    `limits` maps a model id to its `Limit`; a model with none is left to OpenCode's default.
    """

    preset: Literal["litellm", "opencode"]
    base_url: str | None
    key_env: str
    models: Mapping[str, str]
    limits: Mapping[str, Limit] = field(default_factory=dict)

    def model(self, role: str) -> str:
        return self.models.get(role) or self.models["lead"]

    @property
    def model_ids(self) -> list[str]:
        """Every model id the roles use, each once, sorted."""
        return sorted({self.model(role) for role in ROLES})

    @property
    def unlimited(self) -> tuple[str, ...]:
        """The model ids in `model_ids` that have no limit."""
        return tuple(model_id for model_id in self.model_ids if model_id not in self.limits)

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


def _whole_number(text: str, what: str) -> int:
    """The digits of `text` as a number; the range is checked later, for stored limits too."""
    if not (text.isascii() and text.isdigit()) or len(text) > len(str(MAX_LIMIT)):
        raise OpencodeError(_size_rule(what))
    return int(text)


def _size_rule(what: str) -> str:
    return f"--limit {what} must be a positive whole number, digits only, at most {MAX_LIMIT}"


def _parse_limit(pair: str) -> tuple[str, Limit]:
    model_id, separator, sizes = pair.rpartition("=")
    if not separator or not model_id:
        raise OpencodeError("--limit must be MODEL=CONTEXT[:OUTPUT], for example big=100000:8000")
    context, has_output, output = sizes.partition(":")
    return model_id, Limit(
        _whole_number(context, "context"),
        _whole_number(output, "output") if has_output else None,
    )


def _parse_limits(pairs: Sequence[str]) -> dict[str, Limit]:
    limits: dict[str, Limit] = {}
    for pair in pairs:
        model_id, limit = _parse_limit(pair)
        if model_id in limits:
            raise OpencodeError(f"--limit for model {model_id!r} was given twice")
        limits[model_id] = limit
    return limits


def _check_limits(limits: Mapping[str, Limit], models: Mapping[str, str]) -> None:
    for model_id, limit in limits.items():
        if model_id not in models.values():
            raise OpencodeError(f"--limit model {model_id!r} is not one of the profile's models")
        for what, size in (("context", limit.context), ("output", limit.output)):
            if size is not None and not 1 <= size <= MAX_LIMIT:
                raise OpencodeError(_size_rule(what))
        if limit.output is not None and limit.output >= limit.context:
            raise OpencodeError(f"--limit for {model_id!r}: output must be smaller than context")


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


def _with_limits(profile: Profile, limits: Mapping[str, Limit]) -> Profile:
    """`profile` with these limits, once they pass the rules."""
    if limits and profile.preset != "litellm":
        raise OpencodeError("--limit needs --preset litellm: OpenCode's own catalog has the limits")
    _check_limits(limits, profile.models)
    return replace(profile, limits=limits)


def make_profile(
    preset: str,
    base_url: str | None,
    key_env: str | None,
    model_pairs: Sequence[str],
    limit_pairs: Sequence[str] = (),
) -> Profile:
    """The checked profile for `setup`'s flags, or an OpencodeError saying what to change."""
    models, limits = _parse_models(model_pairs), _parse_limits(limit_pairs)
    return _with_limits(
        _checked_profile(preset, base_url, key_env, models, from_file=False), limits
    )


def save_profile(layout: Layout, profile: Profile) -> None:
    """Write `profile.json` (no key, only the env var's name) through a temp file and a rename."""
    document: dict[str, object] = {
        "format": FORMAT,
        "preset": profile.preset,
        "baseURL": profile.base_url,
        "keyEnv": profile.key_env,
        "models": dict(profile.models),
    }
    if profile.limits:
        document["limits"] = {model_id: limit.data() for model_id, limit in profile.limits.items()}
    text = json.dumps(document, indent=2) + "\n"
    write_atomic(layout.profile_file, text.encode("utf-8"), mode=0o600, backup=False)


def _stored_limit(entry: object) -> Limit | None:
    if not isinstance(entry, dict) or set(entry) - {"context", "output"}:
        return None
    context, output = entry.get("context"), entry.get("output")
    if type(context) is not int or ("output" in entry and type(output) is not int):
        return None
    return Limit(context, output)


def _stored_limits(stored: object) -> dict[str, Limit] | None:
    """The limits `profile.json` holds, or None when its `limits` is not the right shape."""
    if not isinstance(stored, dict):
        return None
    limits: dict[str, Limit] = {}
    for model_id, entry in stored.items():
        limit = _stored_limit(entry)
        if limit is None:
            return None
        limits[model_id] = limit
    return limits


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
    limits = _stored_limits(document.get("limits", {}))
    if limits is None:
        return None
    checked = _checked_profile(preset, base_url, key_env, dict(models), from_file=True)
    return _with_limits(checked, limits)


def load_profile(layout: Layout) -> Profile | None:
    """The saved profile, None when setup has not run, an OpencodeError when the file is bad."""
    try:
        text = layout.profile_file.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as error:
        raise OpencodeError(f"cannot read {layout.profile_file}: {error}; {RESET_HINT}") from error
    try:
        loaded = _from_document(safe_json.loads(text))
    except OpencodeError as error:
        raise OpencodeError(
            f"{layout.profile_file} is not valid ({error}); {RESET_HINT}"
        ) from error
    except ValueError:
        loaded = None
    if loaded is None:
        raise OpencodeError(f"{layout.profile_file} is not a valid coven profile; {RESET_HINT}")
    return loaded
