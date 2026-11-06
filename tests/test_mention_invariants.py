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
from claim_check.quoting import merged, quoted_block_regions
from claim_check.claims import _KIND_SPECIFICITY, _collect_candidates, _drop_enclosed, _is_negated, extract_claims

_CLAIMS = ["22 passed", "all tests pass", "all 5 tests pass", "3/4 tests passing", "15/15 passed", "not 22 passed"]
_WORDS = ["the", "suite", "now", "it's", "tests'", "claims", "like", "fixed", "e.g.", "(see", "it)", "5.", "#12", "1,022"]
_QUOTING = ['"', '"', "'", "`", "``", "```", "\u201c", "\u201d", "\u201e", "\u2018", "\u2019", "**",
            "\n    ", "\n    - ", "\n  - ", "\n> ", "\n\tTests  1 failed | 3 passed (4)"]
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


def _selection_outside_blocks(message):
    # The rule guards against inline quotes hiding the writer's own newer
    # claim. Pasted blocks are output, so it starts from what they leave.
    regions = merged(quoted_block_regions(message))
    kept = [
        c for c in _old_candidates(message)
        if not any(a < c.span[1] and c.span[0] < b for a, b in regions)
    ]
    if not kept:
        return []
    return [max(kept, key=lambda c: (_KIND_SPECIFICITY[c.kind], c.span[0]))]


def _is_no_earlier(new, old):
    if not new:
        return True
    if not old:
        return False
    return new[0] == old[0] or new[0].span[0] > old[0].span[0]


def test_a_commit_message_never_falls_back_to_an_earlier_claim():
    # The 0.8.0 smoke test found 1,996 generated messages where skipping a
    # wrongly-quoted claim left an earlier, stale one to be checked. Whatever
    # is checked now is what was checked before, nothing, or a later claim.
    for seed in _SEEDS:
        message = _message(random.Random(seed), quoting=True)
        assert _is_no_earlier(extract_claims(message), _selection_outside_blocks(message)), (seed, message)


def test_a_chat_turn_never_falls_back_to_an_earlier_claim():
    for seed in _SEEDS:
        text = _message(random.Random(seed), quoting=True)
        old = _old_selection(conversation.strip_quoted_output(text))
        assert _is_no_earlier(conversation.extract_claims(text), old), (seed, text)


def _encloses_by_brute_force(outer, inner):
    if outer.span[0] > inner.span[0] or outer.span[1] < inner.span[1]:
        return False
    if outer.span != inner.span:
        return True
    return _KIND_SPECIFICITY[outer.kind] > _KIND_SPECIFICITY[inner.kind]


def test_the_enclosure_sweep_agrees_with_comparing_every_pair():
    # The sweep replaced an all-pairs comparison that took 20 seconds on
    # 10,000 claims. Both must drop exactly the same candidates.
    for seed in _SEEDS:
        message = _message(random.Random(seed), quoting=True)
        candidates = _collect_candidates(message)
        expected = [
            c for c in candidates
            if not any(_encloses_by_brute_force(o, c) for o in candidates if o is not c)
        ]
        assert _drop_enclosed(candidates) == expected, (seed, message)
