"""safe_json: one depth limit on every Python, not whatever the interpreter's decoder happens to
manage (3.12 and 3.13 raise RecursionError on deep input; 3.14 decodes as deep as the C stack
allows, so the same input is an error or a value depending on `ulimit -s`)."""

import json
import time

import pytest

from coven import safe_json

LIMIT = 512


def lists(depth: int) -> str:
    return "[" * depth + "]" * depth


def objects(depth: int) -> str:
    return '{"a":' * depth + "1" + "}" * depth


@pytest.mark.parametrize("build", [lists, objects], ids=["lists", "objects"])
def test_nesting_up_to_the_limit_decodes(build):
    assert safe_json.loads(build(LIMIT)) == json.loads(build(LIMIT))


@pytest.mark.parametrize("build", [lists, objects], ids=["lists", "objects"])
def test_nesting_past_the_limit_is_a_value_error(build):
    with pytest.raises(ValueError, match="nested deeper than 512"):
        safe_json.loads(build(LIMIT + 1))


def test_nesting_far_past_the_limit_is_a_value_error():
    with pytest.raises(ValueError, match="nested deeper than 512"):
        safe_json.loads(lists(100_000))


def test_unclosed_nesting_past_the_limit_is_a_value_error():
    with pytest.raises(ValueError, match="nested deeper than 512"):
        safe_json.loads("[" * 100_000)


@pytest.mark.parametrize(
    "inside",
    [r"[" * 1000, r"{" * 1000, r"\"" + "[" * 1000, "\\\\" + "[" * 1000, "]" * 1000],
    ids=["open-square", "open-curly", "escaped-quote", "escaped-backslash", "close-square"],
)
def test_brackets_inside_strings_do_not_count(inside):
    text = f'{{"key": "{inside}", "list": [{{"k": "{inside}"}}]}}'
    assert safe_json.loads(text) == json.loads(text)


def test_a_string_ending_in_an_escaped_backslash_is_closed():
    text = '["\\\\", ' + lists(LIMIT - 1) + "]"
    assert safe_json.loads(text) == json.loads(text)


def test_brackets_in_keys_do_not_count():
    text = "{" + '"[[[[": 1, ' * 1000 + '"end": 2}'
    assert safe_json.loads(text) == {"[[[[": 1, "end": 2}


def test_the_limit_is_on_depth_not_on_how_many_brackets():
    text = "[" + ",".join(["[]"] * 100_000) + "]"
    assert len(safe_json.loads(text)) == 100_000


def test_other_errors_are_the_decoders():
    with pytest.raises(json.JSONDecodeError):
        safe_json.loads("[1,")


def test_a_multi_megabyte_line_is_checked_quickly():
    line = json.dumps({"text": "[{" * 500_000, "rows": [{"n": i} for i in range(100_000)]})
    started = time.perf_counter()
    safe_json.loads(line)
    assert time.perf_counter() - started < 2
