"""Generated checks that treating quotation as mention can only remove claims.

The reference below is claim selection exactly as it was before quotation
was recognised. Two properties are checked against it on thousands of
generated messages:

- whatever claim is chosen now was a candidate before, so the change can
  never invent a claim or alter one's numbers;
- a message with no quotation at all is judged exactly as before.

The first property already caught a design mistake: deleting quoted blocks
from a commit message joined the text around them into a claim nobody wrote.
"""

import random

import pytest

from claim_check import conversation
from claim_check.claims import _KIND_SPECIFICITY, _collect_candidates, _drop_enclosed, _is_negated, extract_claims

_CLAIMS = ["22 passed", "all tests pass", "all 5 tests pass", "3/4 tests passing", "15/15 passed", "not 22 passed"]
_WORDS = ["the", "suite", "now", "it's", "tests'", "claims", "like", "fixed", "e.g.", "(see", "it)", "5.", "#12", "1,022"]
_QUOTING = ['"', "'", "`", "``", "```", "“", "”", "‘", "’", "\n    ", "\n> ", "\n\tTests  1 failed | 3 passed (4)"]
_SPACING = [" ", " ", " ", "\n", "\n\n", "\r\n"]


def _old_candidates(message):
    candidates = _drop_enclosed(_collect_candidates(message))
    return [c for c in candidates if not _is_negated(message, c.span[0])]


def _old_selection(message):
    candidates = _old_candidates(message)
    if not candidates:
        return []
    return [max(candidates, key=lambda c: (_KIND_SPECIFICITY[c.kind], c.span[0]))]


def _message(rng, quoting):
    parts = []
    for _ in range(rng.randint(1, 14)):
        pool = rng.choice([_CLAIMS, _WORDS, _WORDS] + ([_QUOTING] if quoting else []))
        parts.append(rng.choice(pool))
        parts.append(rng.choice(_SPACING))
    return "".join(parts)


_SEEDS = range(5000)


def test_a_commit_message_claim_was_always_a_candidate_before():
    for seed in _SEEDS:
        message = _message(random.Random(seed), quoting=True)
        old = _old_candidates(message)
        for claim in extract_claims(message):
            assert claim in old, (seed, message)


def test_a_chat_claim_was_always_a_candidate_before():
    for seed in _SEEDS:
        text = _message(random.Random(seed), quoting=True)
        old = _old_candidates(conversation.strip_quoted_output(text))
        for claim in conversation.extract_claims(text):
            assert claim in old, (seed, text)


def test_a_message_without_quotation_is_judged_exactly_as_before():
    for seed in _SEEDS:
        message = _message(random.Random(seed), quoting=False)
        if any(mark in message for mark in "'’"):
            # Apostrophes are not quotation, but "it's" and "tests'" are in
            # the word pool; strip them so this stays about unquoted text.
            message = message.replace("'", "").replace("’", "")
        assert extract_claims(message) == _old_selection(message), (seed, message)


@pytest.mark.parametrize("message", [
    "22\n    foo\npassed",
    "22\n> foo\npassed",
    "all\n```\nx\n```\ntests pass",
], ids=["indented", "blockquote", "fence"])
def test_quoted_blocks_do_not_join_the_text_around_them_into_a_claim(message):
    assert extract_claims(message) == []
