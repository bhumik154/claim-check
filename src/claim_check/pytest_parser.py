"""Parses pytest's own summary line(s) into PytestCounts.

Never re-derives a count by counting individual test-result lines; the
summary line is pytest's own authoritative tally and is what a human or an
agent reading pytest's output would actually be quoting from.

Scanning is line-based and segmented on pytest's session header rather than
regex-scanning the whole stream at once. That is what defeats the realistic
case: a test's own stdout is replayed inside pytest's failure report, which
always precedes that session's real summary line, so taking the last summary
line per session means an accidental or copy-pasted stray summary-shaped
line in a test's output can't win. It is not an absolute guarantee, though:
a forged summary line followed by a forged session header IS believed, since
the forged header closes the segment right after the forged line, making it
that segment's last line. See the "What this is not" section of the README
and the pinned tests in tests/test_pytest_parser.py
(test_forged_header_before_forged_summary_is_safe_real_count_wins and
test_forged_summary_before_forged_header_is_a_documented_limitation_not_a_regression)
for the exact boundary of what this defends against.
"""

import re
from typing import Optional

from .models import PytestCounts

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

# Opens a new run. Real piped output ("pytest tests/unit && pytest
# tests/integration") carries one of these per invocation, which is what
# lets separate runs be aggregated without trusting arbitrary stream text.
_SESSION_HEADER_RE = re.compile(r"^=+\s*test session starts\s*=+$", re.IGNORECASE)

# Applied to a line already stripped of its "=" padding, so there is no
# "=+" / ".*?" ambiguity for the engine to backtrack through. The duration
# is a strict number: "[\d.]+" also matched "1.2.3", which then raised
# ValueError inside float() and aborted the commit with a traceback.
_DURATION = r"(?P<duration>\d+(?:\.\d+)?)s(?:\s*\(\d+:\d+:\d+\))?"
_NO_TESTS_BODY_RE = re.compile(r"^no tests ran\s+in\s+" + _DURATION + r"$", re.IGNORECASE)
_SUMMARY_BODY_RE = re.compile(r"^(?P<body>.*?)\s+in\s+" + _DURATION + r"$")

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


def _classify_line(line: str):
    """Returns (kind, body, duration_s, raw_line) or None.

    "no tests ran" is checked first: the generic summary form also matches
    that line, and classifying each line exactly once is what removes the
    double-counting the previous span-overlap filter existed to handle.
    """
    stripped = line.strip()
    if not stripped.startswith("="):
        return None

    core = stripped.strip("=").strip()
    if not core:
        return None

    match = _NO_TESTS_BODY_RE.match(core)
    kind = "no_tests"
    if match is None:
        match = _SUMMARY_BODY_RE.match(core)
        kind = "summary"
    if match is None:
        return None

    try:
        duration = float(match.group("duration"))
    except ValueError:
        # Unreachable given the stricter duration pattern; kept so a future
        # loosening of that pattern degrades to "not a summary line" rather
        # than to an unhandled crash inside a commit-msg hook.
        return None

    body = match.group("body") if kind == "summary" else ""
    return kind, body, duration, stripped


def parse_summary_line(pytest_output: str) -> Optional[PytestCounts]:
    """Returns None if no summary line (and no "no tests ran" line) is found
    at all - that means pytest crashed before reaching normal reporting
    (an INTERNALERROR, or an invocation error like a missing path), which is
    a materially different situation from a summary that says 0 tests ran.

    Output is split into segments at each "test session starts" header, and
    the LAST summary line in each segment is the one that counts; segment
    totals are then summed. A single invocation only ever produces one
    authoritative summary line - confirmed directly against real
    pytest-xdist output, which prints exactly one final aggregate line and
    no per-worker partials - so more than one segment means more than one
    real run happened, and a claim like "all 100 tests pass" refers to their
    combined total.

    Taking the last line per segment rather than summing every match is
    what defeats the realistic forgery case: pytest replays a failing
    test's captured stdout inside the FAILURES report, which is printed
    before the summary, so a line a test prints can't be last - and
    therefore can't count - within its own session. This is not an
    absolute guarantee: a forged summary line followed by a forged session
    header IS believed, because the forged header ends the segment the
    forged line lives in. See the README's "What this is not" section and
    the pinned tests in tests/test_pytest_parser.py for the exact boundary.

    Output with no session header anywhere is treated as one segment, which
    covers a log captured mid-stream or trimmed before being handed to
    --pytest-output.
    """
    if not isinstance(pytest_output, str):
        return None

    text = strip_ansi(pytest_output)

    segments = [[]]
    for line in text.splitlines():
        if _SESSION_HEADER_RE.match(line.strip()):
            segments.append([])
            continue
        classified = _classify_line(line)
        if classified is not None:
            segments[-1].append(classified)

    finals = [segment[-1] for segment in segments if segment]
    if not finals:
        return None

    totals = {field: 0 for field in _COUNT_FIELDS}
    total_duration = 0.0
    raw_lines = []

    for kind, body, duration, raw in finals:
        raw_lines.append(raw)
        total_duration += duration
        if kind == "summary":
            for count_match in _COUNT_RE.finditer(body):
                label = _LABEL_MAP[count_match.group("label")]
                totals[label] += int(count_match.group("count"))
        # "no_tests" contributes zero to every field; nothing to add.

    return PytestCounts(
        duration_s=total_duration,
        raw_summary_line=" | ".join(raw_lines),
        **totals,
    )
