"""Parses pytest's own summary line into PytestCounts.

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
    """
    text = strip_ansi(pytest_output)

    summary_matches = list(_SUMMARY_RE.finditer(text))
    no_tests_matches = list(_NO_TESTS_RE.finditer(text))

    if not summary_matches and not no_tests_matches:
        return None

    last_summary = summary_matches[-1] if summary_matches else None
    last_no_tests = no_tests_matches[-1] if no_tests_matches else None

    # Whichever kind of match occurs latest in the output wins, guarding
    # against earlier partial-looking lines some plugins print mid-run.
    if last_summary is None or (last_no_tests is not None and last_no_tests.start() > last_summary.start()):
        m = last_no_tests
        return PytestCounts(
            passed=0,
            failed=0,
            skipped=0,
            xfailed=0,
            xpassed=0,
            errors=0,
            warnings=0,
            deselected=0,
            duration_s=float(m.group("duration")),
            raw_summary_line=m.group(0).strip(),
        )

    m = last_summary
    counts = {field: 0 for field in _COUNT_FIELDS}
    for count_match in _COUNT_RE.finditer(m.group("body")):
        label = _LABEL_MAP[count_match.group("label")]
        counts[label] += int(count_match.group("count"))

    return PytestCounts(
        duration_s=float(m.group("duration")),
        raw_summary_line=m.group(0).strip(),
        **counts,
    )
