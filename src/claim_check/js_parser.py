"""Parses vitest and jest tallies into the same counts pytest produces.

Both runners print a file-level tally immediately BEFORE the test-level one:

    Test Files  1 failed | 1 passed (2)      <- vitest, counts FILES
         Tests  1 failed | 3 passed (4)      <- vitest, counts tests

    Test Suites: 1 failed, 1 passed, 2 total <- jest, counts FILES
    Tests:       1 failed, 3 passed, 6 total <- jest, counts tests

Reading the first match reports a file count as a test count, which is worse
than reading nothing: it is a confident wrong number. Only the test-level
line is ever used, and a file tally with no test tally alongside it yields
nothing at all rather than a fallback.

Both runners also state their own total. That is used as a checksum: if the
labels this module recognises do not add up to the stated total, some label
went unmapped and the result is discarded. An unrecognised label therefore
costs a missed check rather than a silently undercounted one.

Formats captured from vitest 2.1.9 and jest 29.7.0 on Node 24.18.0; see
docs/other-test-runners.md.
"""

import re
from typing import Optional

from .models import PytestCounts
from .pytest_parser import strip_ansi

# The test-level tally. vitest writes "Tests" bare, jest writes "Tests:".
# "Test Files" and "Test Suites" are deliberately NOT matched.
_TESTS_LINE = re.compile(r"^\s*Tests\s*:?\s{2,}(?P<body>\S.*)$", re.MULTILINE)

# Items within it: "3 passed", separated by "|" (vitest) or "," (jest).
_ITEM = re.compile(r"(?P<count>\d+)\s+(?P<label>[a-z]+)", re.IGNORECASE)

# vitest states the total in parentheses, jest as a trailing "N total".
_VITEST_TOTAL = re.compile(r"\((?P<total>\d+)\)\s*$")

_DURATION_LINE = re.compile(
    r"^\s*(?:Duration|Time)\s*:?\s+(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>ms|s)\b",
    re.MULTILINE | re.IGNORECASE,
)

# A todo test does not run, exactly like a skipped one, and counting it as
# skipped is what makes the computed total match the total jest states.
_LABEL_MAP = {
    "passed": "passed",
    "failed": "failed",
    "skipped": "skipped",
    "todo": "skipped",
    "pending": "skipped",
}

_COUNT_FIELDS = ("passed", "failed", "skipped", "xfailed", "xpassed", "errors", "warnings", "deselected")


def parse_js_summary(output) -> Optional[PytestCounts]:
    """Counts from a vitest or jest run, or None if this is not one.

    Returns None rather than raising for every unusable input, because this
    runs inside hooks where an exception means a nonzero exit.
    """
    if not isinstance(output, str) or not output:
        return None

    text = strip_ansi(output)

    match = None
    for match in _TESTS_LINE.finditer(text):
        pass  # the last tally wins, mirroring the pytest parser
    if match is None:
        return None

    body = match.group("body")
    totals = {field: 0 for field in _COUNT_FIELDS}
    stated_total = None
    seen_any = False

    for item in _ITEM.finditer(body):
        label = item.group("label").lower()
        try:
            count = int(item.group("count"))
        except ValueError:
            return None
        if label == "total":
            stated_total = count
            continue
        field = _LABEL_MAP.get(label)
        if field is None:
            # An unrecognised label. Leaving it out would undercount, so the
            # checksum below is relied on to reject the whole result.
            continue
        totals[field] += count
        seen_any = True

    if not seen_any:
        return None

    vitest_total = _VITEST_TOTAL.search(body)
    if vitest_total is not None:
        stated_total = int(vitest_total.group("total"))

    computed = totals["passed"] + totals["failed"] + totals["skipped"]
    if stated_total is not None and stated_total != computed:
        # The parts do not account for the whole, so something was not
        # understood. Better to report nothing than a number that is wrong.
        return None

    return PytestCounts(
        duration_s=_duration_seconds(text),
        raw_summary_line=match.group(0).strip()[:500],
        **totals,
    )


def _duration_seconds(text: str) -> float:
    match = _DURATION_LINE.search(text)
    if match is None:
        return 0.0
    try:
        value = float(match.group("value"))
    except ValueError:
        return 0.0
    return value / 1000.0 if match.group("unit").lower() == "ms" else value
