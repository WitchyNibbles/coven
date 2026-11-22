import json
from pathlib import Path

import pytest

from coven import paths
from coven.errors import CovenError
from coven.opencode import profile
from coven.opencode.profile import Layout, OpencodeError, Profile, make_profile

LITELLM_URL = "http://127.0.0.1:4000/v1"


def test_layout_gives_the_documented_paths(tmp_path: Path):
    lay = Layout(tmp_path)
    assert lay.config_home == tmp_path / "config"
    assert lay.config_dir == tmp_path / "config" / "opencode"
    assert lay.data == tmp_path / "data"
    assert lay.cache == tmp_path / "cache"
    assert lay.state == tmp_path / "state"
    assert lay.home == tmp_path / "home"
    assert lay.tmp == tmp_path / "tmp"
    assert lay.plugin_dir == tmp_path / "plugin" / "coven"
    assert lay.profile_file == tmp_path / "profile.json"
    assert lay.runtime_dir("2.3.2") == tmp_path / "runtime" / "2.3.2"
    binary = tmp_path / "runtime" / "2.3.2" / "node_modules" / ".bin" / "opencode"
    assert lay.runtime_binary("2.3.2") == binary


def test_layout_function_reads_the_opencode_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("COVEN_OPENCODE_HOME", str(tmp_path / "oc"))
    assert profile.layout() == Layout(paths.opencode_home())
    assert profile.layout().root == tmp_path / "oc"


def test_constants():
    assert profile.PINNED_VERSION == "2.3.2"
    assert profile.ROLES == ("lead", "implementer", "reviewer", "explorer", "final-reviewer", "title")
    assert profile.AGENT_ROLES == ("explorer", "implementer", "reviewer", "final-reviewer")
    assert profile.OPENCODE_KEY_ENV == "OPENCODE_API_KEY"
    assert issubclass(OpencodeError, CovenError)


def test_litellm_profile_falls_back_to_lead():
    made = make_profile("litellm", LITELLM_URL, "GATEWAY_KEY", ["lead=big", "explorer=small"])
    assert made.model("explorer") == "small"
    assert made.model("reviewer") == "big"
    assert made.model("title") == "big"
    assert made.provider_id == "litellm"
    assert dict(made.models) == {"lead": "big", "explorer": "small"}


def test_opencode_preset_uses_the_fixed_key_env():
    made = make_profile("opencode", None, None, ["lead=x"])
    assert made.key_env == "OPENCODE_API_KEY"
    assert made.base_url is None
    assert made.provider_id == "opencode"


def refused(preset: str, base_url: str | None, key_env: str | None, pairs: list[str]) -> str:
    with pytest.raises(OpencodeError) as caught:
        make_profile(preset, base_url, key_env, pairs)
    return str(caught.value)


def test_unknown_preset_is_refused():
    assert "unknown preset" in refused("other", None, None, ["lead=x"])


def test_lead_is_required():
    assert "--model lead=ID is required" in refused("opencode", None, None, [])
    assert "--model lead=ID is required" in refused("opencode", None, None, ["title=t"])


def test_unknown_role_lists_all_roles():
    message = refused("opencode", None, None, ["boss=x"])
    assert "unknown role" in message
    for role in profile.ROLES:
        assert role in message


@pytest.mark.parametrize(
    ("pairs", "phrase"),
    [
        (["lead=a", "lead=b"], "given twice"),
        (["lead="], "is empty"),
        (["lead=a b"], "no spaces"),
        (["nolabel"], "ROLE=ID"),
    ],
)
def test_bad_model_pairs(pairs: list[str], phrase: str):
    assert phrase in refused("opencode", None, None, pairs)


def test_litellm_needs_a_url_and_a_key_env():
    assert "needs --base-url" in refused("litellm", None, "GATEWAY_KEY", ["lead=x"])
    assert "needs --key-env" in refused("litellm", LITELLM_URL, None, ["lead=x"])


def test_opencode_preset_takes_neither():
    assert "takes no --base-url" in refused("opencode", LITELLM_URL, None, ["lead=x"])
    assert "takes no --key-env" in refused("opencode", None, "GATEWAY_KEY", ["lead=x"])


def test_url_scheme_must_be_http_or_https():
    assert "http:// or https://" in refused("litellm", "ftp://h/v1", "K", ["lead=x"])
    assert "http:// or https://" in refused("litellm", "h/v1", "K", ["lead=x"])


@pytest.mark.parametrize(
    "url", ["https://u:secret@h/v1", "https://h/v1?api_key=secret", "https://h/v1#x"]
)
def test_url_must_not_carry_credentials_query_or_fragment(url: str):
    message = refused("litellm", url, "GATEWAY_KEY", ["lead=x"])
    assert "must not carry" in message
    assert "secret" not in message


@pytest.mark.parametrize("name", ["sk-live-abc123", "gateway_key", "1KEY", "K" * 65])
def test_key_env_must_be_a_name(name: str):
    message = refused("litellm", LITELLM_URL, name, ["lead=x"])
    assert "name, not its value" in message
    assert "sk-live-abc123" not in message


def test_longest_key_env_name_is_accepted():
    assert make_profile("litellm", LITELLM_URL, "K" * 64, ["lead=x"]).key_env == "K" * 64


def test_url_without_v1_warns(capsys: pytest.CaptureFixture[str]):
    made = make_profile("litellm", "http://gw:4000", "GATEWAY_KEY", ["lead=x"])
    assert "OpenCode appends /chat/completions" in capsys.readouterr().err
    assert made.base_url == "http://gw:4000"


def test_url_with_v1_is_quiet_and_loses_one_trailing_slash(capsys: pytest.CaptureFixture[str]):
    made = make_profile("litellm", LITELLM_URL + "/", "GATEWAY_KEY", ["lead=x"])
    assert made.base_url == LITELLM_URL
    assert capsys.readouterr().err == ""


def test_save_then_load_round_trips(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("GATEWAY_KEY", "sk-test-value")
    lay = Layout(tmp_path / "p")
    made = make_profile("litellm", LITELLM_URL, "GATEWAY_KEY", ["lead=big", "title=tiny"])
    profile.save_profile(lay, made)
    assert json.loads(lay.profile_file.read_text()) == {
        "format": 1,
        "preset": "litellm",
        "baseURL": LITELLM_URL,
        "keyEnv": "GATEWAY_KEY",
        "models": {"lead": "big", "title": "tiny"},
    }
    assert "sk-test-value" not in lay.profile_file.read_text()
    assert profile.load_profile(lay) == made
    assert sorted(p.name for p in lay.root.iterdir()) == ["profile.json"]


def test_opencode_preset_is_saved_with_a_null_url(tmp_path: Path):
    lay = Layout(tmp_path)
    profile.save_profile(lay, make_profile("opencode", None, None, ["lead=x"]))
    saved = json.loads(lay.profile_file.read_text())
    assert saved["baseURL"] is None
    assert saved["keyEnv"] == "OPENCODE_API_KEY"
    assert profile.load_profile(lay) == Profile("opencode", None, "OPENCODE_API_KEY", {"lead": "x"})


def test_save_replaces_the_previous_profile(tmp_path: Path):
    lay = Layout(tmp_path)
    profile.save_profile(lay, make_profile("opencode", None, None, ["lead=a"]))
    profile.save_profile(lay, make_profile("opencode", None, None, ["lead=b"]))
    assert json.loads(lay.profile_file.read_text())["models"] == {"lead": "b"}


def test_load_of_a_missing_file_is_none(tmp_path: Path):
    assert profile.load_profile(Layout(tmp_path)) is None


@pytest.mark.parametrize(
    "text",
    [
        "{not json",
        "[]",
        '{"format": 2, "preset": "opencode", "baseURL": null, "keyEnv": "K", "models": {"lead": "x"}}',
        '{"format": 1, "preset": "nope", "baseURL": null, "keyEnv": "K", "models": {"lead": "x"}}',
        '{"format": 1, "preset": "opencode", "baseURL": null, "keyEnv": "K", "models": {}}',
        '{"format": 1, "preset": "opencode", "baseURL": null, "keyEnv": "K", "models": {"boss": "x"}}',
        '{"format": 1, "preset": "opencode", "baseURL": null, "models": {"lead": "x"}}',
    ],
)
def test_load_of_a_bad_file_names_it_and_the_fix(tmp_path: Path, text: str):
    lay = Layout(tmp_path)
    lay.profile_file.write_text(text)
    with pytest.raises(OpencodeError) as caught:
        profile.load_profile(lay)
    assert str(lay.profile_file) in str(caught.value)
    assert "re-run coven opencode setup" in str(caught.value)
