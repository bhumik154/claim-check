from claim_check.claims import extract_claims


def test_no_claim_returns_empty_list_for_unrelated_message():
    assert extract_claims("Fix the off-by-one error in the loop bounds") == []


def test_extracts_simple_n_passed():
    claims = extract_claims("Ran the suite: 22 passed")
    assert len(claims) == 1
    assert claims[0].kind == "n_passed"
    assert claims[0].claimed_passed == 22
    assert claims[0].claimed_total is None


def test_extracts_n_of_m_slash_form_with_equal_numbers():
    claims = extract_claims("22/22 tests pass now")
    assert len(claims) == 1
    assert claims[0].kind == "n_of_m"
    assert claims[0].claimed_passed == 22
    assert claims[0].claimed_total == 22


def test_extracts_n_of_m_slash_form_with_unequal_numbers():
    # A common real phrasing this must not miss: still catching up, not
    # claiming full success.
    claims = extract_claims("15/17 tests passing so far")
    assert len(claims) == 1
    assert claims[0].kind == "n_of_m"
    assert claims[0].claimed_passed == 15
    assert claims[0].claimed_total == 17


def test_extracts_n_of_m_without_the_word_tests():
    claims = extract_claims("15/17 passing")
    assert len(claims) == 1
    assert claims[0].claimed_passed == 15
    assert claims[0].claimed_total == 17


def test_extracts_all_tests_pass_phrase():
    claims = extract_claims("Fixed the bug, all tests pass")
    assert len(claims) == 1
    assert claims[0].kind == "all_pass"
    assert claims[0].claimed_passed is None
    assert claims[0].claimed_total is None


def test_extracts_all_n_tests_pass_phrase():
    claims = extract_claims("Fixed the bug, all 22 tests pass")
    assert len(claims) == 1
    assert claims[0].kind == "all_pass"
    assert claims[0].claimed_total == 22


def test_ignores_issue_reference_number_adjacent_to_the_word_passed():
    # A contrived but real-shaped false positive: an issue number sitting
    # right next to the word "passed" must not be read as a count.
    claims = extract_claims("closes #22 passed review already")
    assert claims == []


def test_decimal_number_not_split_into_a_false_claim():
    # "0.22" ending in "22" must not be misread as "22 passed".
    claims = extract_claims("coverage improved to 0.22 passed threshold")
    assert claims == []


def test_multiple_claims_of_different_kinds_in_one_message_all_extracted():
    claims = extract_claims("17 passed. All tests pass.")
    kinds = {c.kind for c in claims}
    assert kinds == {"n_passed", "all_pass"}


def test_case_insensitivity_of_all_tests_pass_phrase():
    claims = extract_claims("ALL TESTS PASS")
    assert len(claims) == 1
    assert claims[0].kind == "all_pass"


def test_negated_all_tests_pass_phrase_is_not_treated_as_a_claim():
    assert extract_claims("not all tests pass yet, still debugging") == []


def test_partial_ratio_claim_with_unequal_numerator_and_denominator_is_still_extracted():
    claims = extract_claims("Down to 3/22 failing, rest passing: 19/22 tests pass")
    # Two n_of_m-shaped phrases; "3/22 failing" isn't a pass claim at all
    # (no "pass"/"passing" adjacent to it) so only the second is a claim.
    assert len(claims) == 1
    assert claims[0].claimed_passed == 19
    assert claims[0].claimed_total == 22


def test_multiline_commit_message_claim_found_on_a_later_line():
    message = "Fix the scale-invariance bug\n\nVerified directly before fixing.\n\n22 passed"
    claims = extract_claims(message)
    assert len(claims) == 1
    assert claims[0].claimed_passed == 22


def test_last_claim_wins_when_the_same_kind_repeats_with_conflicting_numbers():
    # The exact scenario this project exists for: a number restated and
    # corrected later in the same message. The later one is authoritative.
    message = "Thought it was 15 passed earlier in the session, but it's actually 22 passed"
    claims = extract_claims(message)
    assert len(claims) == 1
    assert claims[0].claimed_passed == 22
