"""Regex-based extraction of test-count claims from commit-message text.

Deliberately not NLP: a small, fully enumerable set of phrasings, each with
an explicit guard against the false-positive shapes that actually show up
in real commit messages (decimal numbers, issue references, negation,
quotation).
"""

import bisect
import re
from typing import Optional

from .models import Claim
from .quoting import merged, quoted_block_regions, quoted_regions

# (?<![.\d#,]) blocks four false-positive shapes in one lookbehind: a digit
# preceded by another digit (a partial match into a larger number), by "."
# (the tail of a decimal like "0.22"), by "#" (an issue reference like
# "#22"), or by "," (the tail of a grouped number like "1,022", which
# otherwise parsed as a claim of 22). None of these are test-count claims.
_NOT_A_CLAIM_DIGIT_PREFIX = r"(?<![.\d#,])"

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
# "n't" deliberately carries no \b prefix: in a real contraction ("doesn't",
# "isn't") both neighbours of the "n" are word characters, so \b never holds
# there and the branch was dead code.
_NEGATION_BEFORE_RE = re.compile(r"(?:\bnot|\bnever|n't)\s*$", re.IGNORECASE)

# Used in two places: _drop_enclosed (below), to pick a winner when two matches
# cover the exact same span (not currently reachable given the three
# regexes above; guarded so a future regex change degrades predictably
# rather than arbitrarily by list order), and extract_claims's final
# selection, where it is very much reachable - it's what lets a more
# specific claim (e.g. n_of_m) outrank a later but less specific one
# (e.g. all_pass) instead of position alone deciding the winner.
_KIND_SPECIFICITY = {"n_of_m": 2, "all_pass": 1, "n_passed": 0}


def _is_negated(message: str, match_start: int) -> bool:
    window = message[max(0, match_start - 20) : match_start]
    return bool(_NEGATION_BEFORE_RE.search(window))


def _collect_candidates(message: str) -> list[Claim]:
    candidates: list[Claim] = []

    for m in _N_PASSED_RE.finditer(message):
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
        candidates.append(
            Claim(
                kind="all_pass",
                claimed_passed=None,
                claimed_total=int(m.group(1)) if m.group(1) else None,
                raw_text=m.group(0),
                span=m.span(),
            )
        )

    return candidates


def _drop_enclosed(candidates: list[Claim]) -> list[Claim]:
    """Discards any candidate wholly contained inside another.

    "22/22 passed" matches both _N_OF_M_RE (span 0-12) and, on the
    substring "22 passed", _N_PASSED_RE (span 3-12). They describe the same
    phrase, not two claims, and the enclosing match is the one that read it
    correctly - confirmed directly: keeping the contained match discarded
    the denominator entirely, so a real 22-passed-1-failed run verified
    "22/22 passed" as true, while the identical lie written "22/22 tests
    pass" was correctly blocked.

    One sweep in start order rather than comparing every pair: comparing
    pairs took 20 seconds on a message holding 10,000 claims. Sorted by start,
    then longest first, then most specific first, a candidate is enclosed
    exactly when some candidate before it reaches at least as far. Two
    matches on the identical span keep the more specific one.
    """
    order = sorted(
        candidates, key=lambda c: (c.span[0], -c.span[1], -_KIND_SPECIFICITY[c.kind])
    )
    enclosed = set()
    furthest = -1
    for candidate in order:
        if furthest >= candidate.span[1]:
            enclosed.add(id(candidate))
        furthest = max(furthest, candidate.span[1])
    return [c for c in candidates if id(c) not in enclosed]


def _drop_quoted(candidates: list[Claim], regions: list[tuple[int, int]]) -> list[Claim]:
    """Discards any candidate that touches quoted text.

    A claim in quotation marks or a code span is an example being talked
    about, not a result being reported: "It checks claims like "22 passed"",
    "an honest "all 150 tests pass" got flagged". Overlap rather than
    containment, so a half-quoted `"22/22" tests pass` goes too. Dropping only
    ever removes a claim, never invents one.

    `regions` is sorted and non-overlapping, so the only region that can
    touch a claim is the last one starting before the claim ends.
    """
    starts = [start for start, _ in regions]

    def touches(claim: Claim) -> bool:
        index = bisect.bisect_left(starts, claim.span[1])
        return index > 0 and regions[index - 1][1] > claim.span[0]

    return [c for c in candidates if not touches(c)]


def _selected(candidates: list[Claim]) -> Optional[Claim]:
    if not candidates:
        return None
    return max(candidates, key=lambda c: (_KIND_SPECIFICITY[c.kind], c.span[0]))


def extract_claims(message: str, *, blocks_removed: bool = False) -> list[Claim]:
    """Returns at most one Claim: the most specific one found in the
    message (n_of_m > all_pass > n_passed, per _KIND_SPECIFICITY), and
    among claims tied on specificity, the last one by starting position.

    compare.py checks every returned claim against a single pytest run, so
    two claims with different kinds or numbers can't both be true at once -
    confirmed directly, "14 passed. Fixed the bug, now 15/15 tests pass!"
    is a stale count restated and corrected using different phrasing
    (n_passed then n_of_m), not a real contradiction, and returning both
    flagged an honest correction as a mismatch. Position alone used to
    decide the winner outright, which let a later, weaker claim silence an
    earlier, stronger one: "Fix parser: 50/50 tests pass\n\nAll tests pass
    now." matched a real 3-passed/0-failed run, because the all_pass claim
    starts later in the text and is technically true of that run, even
    though the 50/50 claim just before it is false. Specificity-first fixes
    that while preserving the honest-correction case above, since a later
    n_of_m claim still outranks an earlier n_passed one on specificity
    alone, not merely on position. Among claims of equal specificity,
    last-position-wins is unchanged, and still doesn't resolve genuine
    tense/discourse ambiguity (a later sentence describing an earlier
    state is still possible); see the README.

    The stages run in this order, and the order is load-bearing: enclosed
    matches are dropped BEFORE the negation filter. "not 22/22 passed"
    negates the n_of_m match at offset 4, but the contained "22 passed"
    starts at offset 7, where the lookback window is "not 22/" and reads as
    un-negated. Filtering negation first leaves that contained match alive
    and registers a claim from a message that explicitly denies one. Do not
    reorder these.

    Last, any claim touching quoted material is dropped: a fenced or indented
    block, a blockquote, another runner's tally, quotation marks or a code
    span. That stage only ever removes claims, never invents one.

    And when quotation marks or a code span drop the claim that would have
    been checked, only a claim
    written AFTER that one may take its place. An earlier claim is just as
    likely to be the stale count the dropped one superseded: "Was 3 passed.
    Then I fixed "the parser and all 5 tests pass." once hid the real claim
    behind an unclosed quote and blocked a true commit on the stale "3
    passed". Checking nothing there costs a check; checking the earlier claim
    can cost the user's trust. The rule holds whatever the quote detection
    gets wrong, which is why it exists alongside it.
    """
    candidates = _drop_enclosed(_collect_candidates(message))
    candidates = [c for c in candidates if not _is_negated(message, c.span[0])]
    # Blocks first, and outside the rule above: a pasted tally or code block
    # is output, evidence rather than a newer claim by the writer, so a count
    # stated before it is still the one to check.
    # `blocks_removed` is for chat, which deletes them beforehand. Looking for
    # blocks again in what is left is not the same: a deleted tally line
    # leaves a blank line, and the indented line below it then reads as code.
    if not blocks_removed:
        candidates = _drop_quoted(candidates, merged(quoted_block_regions(message)))
    before = _selected(candidates)
    if before is None:
        return []

    kept = _selected(_drop_quoted(candidates, quoted_regions(message)))
    if kept is None:
        return []
    if kept is not before and kept.span[0] < before.span[0]:
        return []
    return [kept]
