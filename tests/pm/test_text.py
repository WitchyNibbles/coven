"""Text helpers for printing what a project-manager session wrote."""

import pytest

from coven.pm.text import (
    GM_PREFIXES,
    LINE_LIMIT,
    PM_PREFIXES,
    Head,
    clean_block,
    clean_line,
    is_valid_code,
    is_valid_gm_name,
    parse_head,
)


def test_clean_line_strips_escape_sequences():
    raw = "\x1b[31mDONE\x1b[0m acme-1/1: ok\x1b]0;title\x07\r\x00 end\nsecond line"
    assert clean_line(raw) == "DONE acme-1/1: ok end"


def test_clean_line_strips_an_osc_ended_by_string_terminator():
    assert clean_line("a\x1b]0;title\x1b\\b") == "ab"


def test_clean_line_cuts_at_the_limit():
    assert clean_line("x" * 300) == "x" * 199 + "…"


def test_clean_line_keeps_a_line_of_exactly_the_limit():
    assert clean_line("x" * LINE_LIMIT) == "x" * LINE_LIMIT


def test_clean_line_collapses_spaces_and_strips_the_ends():
    assert clean_line("  a \x07  b  ") == "a b"


def test_clean_block_keeps_newlines():
    assert clean_block("a\x1b[1m\nb\x07") == "a\nb"


# Bidi overrides and isolates (Cf), a zero-width space (Cf), and the line and paragraph separators.
INVISIBLE = "\u202e\u2066\u200b\u2028\u2029"


def test_clean_line_drops_format_characters_and_unicode_separators():
    assert clean_line(f"DONE acme-1/1: {INVISIBLE}ok") == "DONE acme-1/1: ok"


def test_clean_block_keeps_only_newlines_as_line_breaks():
    assert clean_block(f"a{INVISIBLE}\nb") == "a\nb"


def test_prefixes_in_their_documented_order():
    assert PM_PREFIXES == ("QUESTION", "PROD-STEP", "DRAFT", "DONE", "BLOCKED")
    assert GM_PREFIXES == ("ANSWER", "APPROVED", "DENIED", "TASK")


def test_parse_head_reads_prefix_and_key():
    assert parse_head("QUESTION acme-1/2: which?\nmore") == Head("QUESTION", "acme-1/2")
    assert parse_head("APPROVED acme-1/3 a1b2: go") == Head("APPROVED", "acme-1/3")


def test_parse_head_marks_a_bad_key():
    assert parse_head("DONE ../../etc: x") == Head("DONE", "?")


def test_parse_head_marks_a_missing_key():
    assert parse_head("BLOCKED") == Head("BLOCKED", "?")


def test_parse_head_takes_a_bare_task_after_and_with_the_first_projects_and_not_a_trailing_word():
    head = parse_head("APPROVED acme-1/7x-A1 and 7x-A1b 2026-10-30-trailing: go ahead")
    assert head is not None
    assert head.prefix == "APPROVED"
    assert head.key == "acme-1/7x-A1"
    assert head.keys == ("acme-1/7x-A1", "acme-1/7x-A1b")


@pytest.mark.parametrize(
    "line",
    [
        "DONE acme-1/1 and acme-1/2: x",
        "DONE acme-1/1 & 2: x",
        "DONE acme-1/1 + 2: x",
        "DONE acme-1/1, 2: x",
        "DONE acme-1/1 , acme-1/2: x",
        "DONE acme-1/1, and acme-1/2: x",
    ],
)
def test_parse_head_joins_keys_by_and_comma_ampersand_or_plus(line):
    head = parse_head(line)
    assert head is not None
    assert head.keys == ("acme-1/1", "acme-1/2")


def test_parse_head_takes_a_full_key_of_another_project_after_a_joiner():
    head = parse_head("DONE acme-1/1 and other-2/7, 8: x")
    assert head is not None
    # a bare task takes the first key's project
    assert head.keys == ("acme-1/1", "other-2/7", "acme-1/8")


def test_parse_head_strips_a_trailing_comma_from_the_first_key():
    head = parse_head("DONE acme-1/1,")
    assert head == Head("DONE", "acme-1/1")


@pytest.mark.parametrize(
    "line",
    [
        "DONE acme-1/1 2026-10-30-id and acme-1/2",  # no joiner before the id
        "DONE acme-1/1: and acme-1/2",  # the colon ends the keys
        "DONE acme-1/1 and ../etc and acme-1/2",  # a word that is no task stops the list
        "DONE acme-1/1 and",
        "DONE acme-1/1, ",
        "DONE acme-1/1 and other-2/7;x",  # a later full key has the shape of a key, whole
        "DONE acme-1/1 and other-2/7/../x",
    ],
)
def test_parse_head_stops_at_the_first_word_that_does_not_fit(line):
    head = parse_head(line)
    assert head is not None
    assert head.keys == ("acme-1/1",)


def test_parse_head_ends_the_keys_at_a_colon_on_a_later_key_too():
    head = parse_head("DONE acme-1/1 and acme-1/2: and acme-1/3")
    assert head is not None
    assert head.keys == ("acme-1/1", "acme-1/2")


def test_parse_head_has_only_the_unknown_key_when_the_key_is_unusable():
    for line in ("DONE ../../etc and acme-1/2: x", "BLOCKED"):
        head = parse_head(line)
        assert head is not None
        assert head.keys == ("?",)


def test_parse_head_ignores_other_text():
    assert parse_head("Reading the repo first.") is None
    assert parse_head("") is None


@pytest.mark.parametrize("code", ["acme-1", "a", "9x", "web-2026"])
def test_a_project_code_is_lower_case_words_and_dashes(code):
    assert is_valid_code(code)


@pytest.mark.parametrize("code", ["", "-a", "Acme", "a;touch x", "a b", "a\n", "a/b", "a_b", ".a"])
def test_other_project_codes_are_refused(code):
    assert not is_valid_code(code)


def test_a_gm_name_starts_with_a_letter_or_digit():
    for good in ("gm", "gm-main", "GM.2_x", "7"):
        assert is_valid_gm_name(good), good
    for bad in ("", "-gm", ".gm", "gm main", "gm;x", "gm\n", "gm'x"):
        assert not is_valid_gm_name(bad), bad
