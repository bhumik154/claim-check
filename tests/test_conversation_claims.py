"""Claim extraction tuned for chat prose rather than commit messages.

The difference that matters: a chat turn routinely contains pasted terminal
output. Measured across 43 real sessions, 3% of raw claim detections were a
verbatim quotation of pytest's own summary rather than an assertion the agent
was making. A commit message never contains that shape, so the commit-message
parser has never had to tell the two apart.
"""

from claim_check import conversation


def test_a_plain_assertion_is_a_claim():
    claims = conversation.extract_claims("Done. 22 passed.")
    assert len(claims) == 1
    assert claims[0].claimed_passed == 22


def test_quoted_pytest_output_in_a_fenced_block_is_not_a_claim():
    # The agent is showing you the output, not asserting a number.
    text = (
        "Here's what the suite reported:\n\n"
        "```\n"
        "=========== 22 passed in 1.40s ===========\n"
        "```\n\n"
        "Moving on to the next task.\n"
    )
    assert conversation.extract_claims(text) == []


def test_a_fenced_block_with_a_language_tag_is_also_stripped():
    text = "Output:\n\n```bash\n$ pytest -q\n22 passed in 1.4s\n```\n\nNext up: refactoring.\n"
    assert conversation.extract_claims(text) == []


def test_an_indented_block_is_treated_as_quoted_output():
    text = "The run said:\n\n    22 passed in 1.40s\n\nSo that is settled.\n"
    assert conversation.extract_claims(text) == []


def test_a_blockquote_is_treated_as_quoted_material():
    text = "You wrote:\n\n> 22 passed\n\nI have not run it yet.\n"
    assert conversation.extract_claims(text) == []


def test_a_bare_summary_shaped_line_is_not_an_assertion():
    # Agents paste the summary line without any block markup constantly.
    text = "I ran the suite.\n\n=========== 1 failed, 21 passed in 0.09s ===========\n\nLooking at the failure now.\n"
    assert conversation.extract_claims(text) == []


def test_an_undecorated_summary_line_is_also_stripped():
    text = "Ran it:\n\n22 passed in 1.40s\n\nOn to the next thing.\n"
    assert conversation.extract_claims(text) == []


def test_an_assertion_survives_alongside_quoted_output():
    # The whole point: strip the quotation, keep the sentence the agent
    # actually wrote. This is the shape that should still be checked.
    text = (
        "```\n"
        "=========== 21 passed in 1.40s ===========\n"
        "```\n\n"
        "All 22 tests pass now.\n"
    )
    claims = conversation.extract_claims(text)
    assert len(claims) == 1
    assert claims[0].kind == "all_pass"
    assert claims[0].claimed_total == 22


def test_the_commit_message_resolution_rules_still_apply():
    # Negation, decimals and issue references are handled by the shared
    # parser; conversation.py only adds the stripping layer on top.
    assert conversation.extract_claims("not all tests pass yet") == []
    assert conversation.extract_claims("coverage hit 0.22 passed threshold") == []
    assert conversation.extract_claims("closes #22 passed review") == []


def test_a_more_specific_claim_still_outranks_a_later_vaguer_one():
    text = "Fixed the parser: 50/50 tests pass.\n\nAll tests pass now.\n"
    claims = conversation.extract_claims(text)
    assert len(claims) == 1
    assert claims[0].kind == "n_of_m"
    assert claims[0].claimed_total == 50


def test_no_claim_at_all_returns_empty():
    assert conversation.extract_claims("Refactored the loop and tidied the imports.") == []


def test_non_string_input_returns_empty_rather_than_raising():
    assert conversation.extract_claims(None) == []
    assert conversation.extract_claims(42) == []


def test_an_unterminated_fence_swallows_the_rest_of_the_message():
    # Safer direction: an unclosed block means we cannot tell where the
    # quotation ends, so everything after it is treated as quoted. Missing a
    # claim costs a check; inventing one costs the user's trust.
    text = "Output below:\n\n```\n22 passed in 1.4s\n\nand then some prose that never closed the fence\n"
    assert conversation.extract_claims(text) == []


def test_stripping_leaves_ordinary_prose_untouched():
    cleaned = conversation.strip_quoted_output("Plain sentence. 22 passed. Another sentence.")
    assert "22 passed" in cleaned
