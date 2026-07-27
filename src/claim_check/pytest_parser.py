"""Parses pytest's own summary line(s) into PytestCounts.

Never re-derives a count by counting individual test-result lines; the
summary line is pytest's own authoritative tally and is what a human or an
agent reading pytest's output would actually be quoting from.
"""

import re
from typing import Optional

from .models import PytestCounts

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

# The final "==== ... in Xs ====" line pytest prints after a run. Duration
# may carry a parenthetical H:MM:SS for long runs ("in 65.43s (0:01:05)").
_SUMMARY_RE = re.compile(
    r"^=+\s*(?P<body>.*?)\s+in\s+(?P<duration>[\d.]+)s(?:\s*\(\d+:\d+:\d+\))?\s*=*\s*$",
    re.MULTILINE,
)
# "no tests ran" is pytest's own distinct phrasing, never combined with the
# count-bearing body form above.
_NO_TESTS_RE = re.compile(
    r"^=+\s*no tests ran in\s+(?P<duration>[\d.]+)s\s*=*\s*$",
    re.MULTILINE,
)
_COUNT_RE = re.compile(
    r"(?P<count>\d+)\s+(?P<label>passed|failed|skipped|xfailed|xpassed|errors?|warnings?|deselected)\b"
)
_LABEL_MAP = {
    "passed": "passed",
    "failed": "failed",
    "skipped": "skipped",
    "xfailed": "xfailed",
    "xpassed": "xpassed",
    "error": "errors",
    "errors": "errors",
    "warning": "warnings",
    "warnings": "warnings",
    "deselected": "deselected",
}
_COUNT_FIELDS = ("passed", "failed", "skipped", "xfailed", "xpassed", "errors", "warnings", "deselected")


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def parse_summary_line(pytest_output: str) -> Optional[PytestCounts]:
    """Returns None if no summary line (and no "no tests ran" line) is found
    at all - that means pytest crashed before reaching normal reporting
    (an INTERNALERROR, or an invocation error like a missing path), which is
    a materially different situation from a summary that says 0 tests ran.

    When more than one summary line is present (multiple pytest invocations
    piped together, e.g. "pytest tests/unit && pytest tests/integration"),
    their counts are aggregated rather than only the last one being used.
    A single invocation only ever produces one summary line - confirmed
    directly against real pytest-xdist output, which prints exactly one
    final aggregate line and no per-worker partials - so more than one
    line in the output means more than one real run happened, and a claim
    like "all 100 tests pass" refers to their combined total, not whichever
    invocation happened to run last.
    """
    text = strip_ansi(pytest_output)

    no_tests_matches = [(m.start(), m.end(), "no_tests", m) for m in _NO_TESTS_RE.finditer(text)]
    no_tests_spans = [(start, end) for start, end, _, _ in no_tests_matches]

    # _SUMMARY_RE is deliberately generic (any "... in Xs ..." line), so it
    # also matches a "no tests ran" line at the exact same span - without
    # filtering that out here, the same line gets counted twice (confirmed
    # directly: it doubled the reported duration before this filter).
    summary_matches = [
        (m.start(), m.end(), "summary", m)
        for m in _SUMMARY_RE.finditer(text)
        if not any(m.start() < end and start < m.end() for start, end in no_tests_spans)
    ]

    all_matches = sorted(summary_matches + no_tests_matches, key=lambda item: item[0])

    if not all_matches:
        return None

    totals = {field: 0 for field in _COUNT_FIELDS}
    total_duration = 0.0
    raw_lines = []

    for _, _, kind, m in all_matches:
        raw_lines.append(m.group(0).strip())
        total_duration += float(m.group("duration"))
        if kind == "summary":
            for count_match in _COUNT_RE.finditer(m.group("body")):
                label = _LABEL_MAP[count_match.group("label")]
                totals[label] += int(count_match.group("count"))
        # "no_tests" contributes zero to every field; nothing to add.

    return PytestCounts(
        duration_s=total_duration,
        raw_summary_line=" | ".join(raw_lines),
        **totals,
    )
