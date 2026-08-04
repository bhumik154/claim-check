"""Claim extraction for chat prose, as opposed to commit messages.

One difference drives this whole module: a chat turn routinely contains
pasted terminal output, and a commit message never does. Measured across 43
real sessions, 3% of raw claim detections were a verbatim quotation of
pytest's own summary line rather than an assertion the agent was making.
Flagging someone for accurately quoting output would be the worst kind of
false positive, so quoted material is removed before any claim is looked for.

What is deliberately NOT changed: the resolution rules. Once quotations are
gone, `claims.extract_claims` decides which claim survives, using the same
most-specific-then-last-position policy as commit messages. That policy was
designed for a short message and a long chat turn can hold several genuinely
distinct claims, which stays ambiguous here. The measured problem was quoted
output, so that is what this module solves; inventing a second, different
resolution policy for an unmeasured problem would add risk without evidence.
"""

import re
from typing import List

from .claims import extract_claims as _extract_claims
from .models import Claim

# A fenced block, closed or not. An unterminated fence swallows everything
# after it on purpose: if the quotation has no end, there is no way to tell
# where prose resumes, and missing a claim costs a check while inventing one
# costs the user's trust.
_FENCED = re.compile(r"```.*?(?:```|\Z)", re.DOTALL)

# Markdown's indented code block.
_INDENTED = re.compile(r"^(?: {4}|\t).*$", re.MULTILINE)

# A quotation of someone or something else.
_BLOCKQUOTE = re.compile(r"^\s*>.*$", re.MULTILINE)

# A line carrying pytest's own summary, decorated or not. These get pasted
# without any block markup constantly, which is exactly the 3% case.
_SUMMARY_LINE = re.compile(
    r"^.*\b\d+\s+(?:passed|failed|skipped|xfailed|xpassed|errors?|warnings?|deselected)\b"
    r".*\bin\s+\d+(?:\.\d+)?s.*$",
    re.MULTILINE,
)

# Tallies from runners this tool does not support. Captured from a real
# vitest 2.1.9 run, whose summary is two lines with no "in Xs" duration:
#
#      Test Files  1 failed | 1 passed (2)
#           Tests  1 failed | 3 passed (4)
#
# Quoting one of those is no more an assertion than quoting pytest, and the
# "Test Files" line is worse than a plain false positive: its count is FILES,
# so reading it as a claim reports 1 passed when 3 tests passed. Stripping is
# the right response even though these runners are unsupported - claim-check
# has no counts to compare against, so there is nothing here it could ever
# legitimately check.
_OTHER_RUNNER_TALLY = re.compile(
    r"^\s*(?:Test Files|Tests|Test Suites|Suites|Snapshots|Time)\s*:?\s+.*\b\d+\s+"
    r"(?:passed|failed|skipped|todo|pending|total)\b.*$",
    re.MULTILINE | re.IGNORECASE,
)


def strip_quoted_output(text: str) -> str:
    """Removes the shapes that are quotation rather than assertion.

    Order matters: fenced blocks go first, so their contents cannot be
    partially matched by the narrower patterns afterwards.
    """
    text = _FENCED.sub("\n", text)
    text = _BLOCKQUOTE.sub("", text)
    text = _INDENTED.sub("", text)
    text = _SUMMARY_LINE.sub("", text)
    text = _OTHER_RUNNER_TALLY.sub("", text)
    return text


def extract_claims(text) -> List[Claim]:
    """Claims asserted in a chat turn, ignoring anything quoted.

    Returns at most one claim, exactly as the commit-message parser does.
    Non-string input yields no claims rather than raising: this runs inside a
    hook, where an exception means a nonzero exit.
    """
    if not isinstance(text, str) or not text:
        return []
    return _extract_claims(strip_quoted_output(text))
