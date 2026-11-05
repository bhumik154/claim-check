"""Finding the parts of a text that are quoted rather than asserted.

Measured across every real end-of-turn message and commit message on the
author's machine, not one genuine test-count claim was written inside double
quotes, while about 35 quoted ones were examples being discussed. These tests
pin how quotation is recognised; tests/test_claims.py pins what that does to
claim selection.
"""

import random
import time

import pytest

from claim_check.quoting import quoted_regions


def quoted_texts(text):
    return [text[a:b] for a, b in quoted_regions(text)]


@pytest.mark.parametrize("text, expected", [
    ('claims like "22 passed" here', ['"22 passed"']),
    ("tests show `22 passed` here", ["`22 passed`"]),
    ("curly “all tests pass” here", ["“all tests pass”"]),
    ("curly ‘all tests pass’ here", ["‘all tests pass’"]),
    ("single 'all tests pass' here", ["'all tests pass'"]),
    ('two "a" and "b"', ['"a"', '"b"']),
], ids=["double", "code-span", "curly-double", "curly-single", "single", "two"])
def test_each_kind_of_quotation_is_found(text, expected):
    assert quoted_texts(text) == expected


def test_plain_text_has_no_quoted_regions():
    assert quoted_regions("All 61 tests pass. 370 passed.") == []


def test_empty_text_has_no_quoted_regions():
    assert quoted_regions("") == []


def test_a_double_quote_spans_a_wrapped_line():
    # Commit messages wrap at 72 columns, and two real mentions in this
    # project's history were split across a line break.
    text = 'a probe written as "the last full-suite run I observed\nwas 443 passed in 25.68s" was ignored'
    assert quoted_texts(text) == ['"the last full-suite run I observed\nwas 443 passed in 25.68s"']


def test_an_unclosed_double_quote_runs_to_the_end_of_its_paragraph_only():
    # Safer to treat the rest of the paragraph as quoted than to guess where
    # the quotation ends; a blank line is as far as that guess may reach.
    text = 'He wrote "all tests pass and\nthen stopped.\n\n370 passed.'
    assert quoted_texts(text) == ['"all tests pass and\nthen stopped.']


def test_a_blank_line_with_crlf_also_ends_a_paragraph():
    text = 'He wrote "all tests pass\r\n\r\n370 passed.'
    assert quoted_texts(text) == ['"all tests pass']


def test_quote_marks_inside_a_code_span_are_not_quotation_marks():
    text = 'run `git commit -m "6 passed"` then 370 passed'
    assert quoted_texts(text) == ['`git commit -m "6 passed"`']


def test_a_double_backtick_span_can_hold_a_single_backtick():
    text = "see ``a`b`` and 370 passed"
    assert quoted_texts(text) == ["``a`b``"]


def test_an_unclosed_backtick_is_a_literal_character():
    # Markdown's rule: a backtick run with no matching run is just text.
    assert quoted_regions("a stray ` then 370 passed") == []


def test_a_code_span_may_span_a_line_break():
    text = "the line `22\npassed` here"
    assert quoted_texts(text) == ["`22\npassed`"]


@pytest.mark.parametrize("text", [
    "it's done, 370 passed",
    "the tests' names don't matter, 370 passed",
    "rock 'n' roll aside, 370 passed",
], ids=["contraction", "possessive", "rock-n-roll"])
def test_apostrophes_are_not_quotation(text):
    assert all("370 passed" not in q for q in quoted_texts(text))


def test_a_single_quote_pair_must_stay_on_one_line():
    text = "he said 'all\ntests pass' then"
    assert quoted_regions(text) == []


def test_single_quotes_inside_double_quotes_are_covered_by_the_outer_quote():
    text = "If I write \"this tool catches claims like 'all tests pass'\", I'm giving an example."
    regions = quoted_regions(text)
    start = text.index("all tests pass")
    assert any(a <= start < b for a, b in regions)


def test_regions_are_sorted_and_do_not_overlap():
    text = 'one "a `b` c" two `d "e" f` three “g”'
    regions = quoted_regions(text)
    assert regions == sorted(regions)
    for (a1, b1), (a2, b2) in zip(regions, regions[1:]):
        assert b1 <= a2


def test_non_string_input_has_no_regions():
    assert quoted_regions(None) == []


def test_adversarial_input_is_parsed_in_linear_time():
    # The heredoc stash was once quadratic on a crafted command. This runs in
    # a hook with a time budget, so a megabyte of quote characters has to be
    # cheap.
    rng = random.Random(7)
    text = "".join(rng.choice("\"'`“”‘’ a\n") for _ in range(1_000_000))
    start = time.perf_counter()
    quoted_regions(text)
    assert time.perf_counter() - start < 2.0


def test_a_quotation_inside_a_quotation_stays_quoted():
    # Pairing straight quotes in order leaves the inner example outside
    # every quote. Seen in a real end-of-turn message.
    text = 'For example, "people don\'t write "22 passed" in prose" gets checked.'
    start = text.index("22 passed")
    assert any(a <= start < b for a, b in quoted_regions(text))


def test_an_inch_mark_does_not_quote_the_rest_of_the_paragraph():
    text = 'Fits a 5" screen. 370 passed.'
    assert quoted_regions(text) == []


def test_an_inch_mark_does_not_break_a_later_quotation():
    text = 'Fits a 5" screen, and "22 passed" is an example.'
    assert quoted_texts(text) == ['"22 passed"']
