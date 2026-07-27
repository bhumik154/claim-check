"""Regex-based extraction of test-count claims from commit-message text.

Deliberately not NLP: a small, fully enumerable set of phrasings, each with
an explicit guard against the false-positive shapes that actually show up
in real commit messages (decimal numbers, issue references, negation).
"""

import re

from .models import Claim

# (?<![.\d#]) blocks three false-positive shapes in one lookbehind: a digit
# preceded by another digit (a partial match into a larger number), by "."
# (the tail of a decimal like "0.22"), or by "#" (an issue reference like
# "#22"). None of these are test-count claims.
_NOT_A_CLAIM_DIGIT_PREFIX = r"(?<![.\d#])"

_N_PASSED_RE = re.compile(
    _NOT_A_CLAIM_DIGIT_PREFIX + r"\b(\d+)\s+passed\b",
    re.IGNORECASE,
)
_N_OF_M_RE = re.compile(
    _NOT_A_CLAIM_DIGIT_PREFIX + r"\b(\d+)\s*/\s*(\d+)\s+(?:tests?\s+)?pass(?:ed|ing)?\b",
    re.IGNORECASE,
)
_ALL_PASS_RE = re.compile(
    r"\ball\s+(?:(\d+)\s+)?tests?\s+pass(?:ed|ing)?\b",
    re.IGNORECASE,
)
_NEGATION_BEFORE_RE = re.compile(r"\b(not|n't|never)\s*$", re.IGNORECASE)


def _is_negated(message: str, match_start: int) -> bool:
    window = message[max(0, match_start - 20) : match_start]
    return bool(_NEGATION_BEFORE_RE.search(window))


def extract_claims(message: str) -> list[Claim]:
    """Returns at most one Claim: the last one found in the message, by
    starting position, regardless of kind. compare.py checks every returned
    claim against a single pytest run, so two claims with different kinds
    or numbers can't both be true at once - confirmed directly, "14 passed.
    Fixed the bug, now 15/15 tests pass!" is a stale count restated and
    corrected using different phrasing (n_passed then n_of_m), not a real
    contradiction, and returning both flagged an honest correction as a
    mismatch. Keeping only the last claim in the text is the same
    last-occurrence-wins principle as before, just applied globally instead
    of per kind. This does not resolve genuine tense/discourse ambiguity
    (a later sentence describing an earlier state is still possible); see
    the README.
    """
    candidates: list[Claim] = []

    for m in _N_PASSED_RE.finditer(message):
        if _is_negated(message, m.start()):
            continue
        candidates.append(
            Claim(
                kind="n_passed",
                claimed_passed=int(m.group(1)),
                claimed_total=None,
                raw_text=m.group(0),
                span=m.span(),
            )
        )

    for m in _N_OF_M_RE.finditer(message):
        if _is_negated(message, m.start()):
            continue
        candidates.append(
            Claim(
                kind="n_of_m",
                claimed_passed=int(m.group(1)),
                claimed_total=int(m.group(2)),
                raw_text=m.group(0),
                span=m.span(),
            )
        )

    for m in _ALL_PASS_RE.finditer(message):
        if _is_negated(message, m.start()):
            continue
        claimed_total = int(m.group(1)) if m.group(1) else None
        candidates.append(
            Claim(
                kind="all_pass",
                claimed_passed=None,
                claimed_total=claimed_total,
                raw_text=m.group(0),
                span=m.span(),
            )
        )

    if not candidates:
        return []

    return [max(candidates, key=lambda c: c.span[0])]
