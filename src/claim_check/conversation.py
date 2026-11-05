"""Claim extraction for chat prose, as opposed to commit messages.

One difference drives this module: a chat turn routinely pastes pytest's own
summary line with no markup around it. Measured across 43 real sessions, 3% of
raw claim detections were a verbatim quotation of that line rather than an
assertion the agent was making. Flagging someone for accurately quoting output
would be the worst kind of false positive, so that line is removed before any
claim is looked for.

Everything else that marks text as quoted, fenced and indented blocks,
blockquotes, other runners' tallies, quotation marks and code spans, is handled
in `claims.extract_claims` for commit messages and chat alike. Commit messages
do carry pasted output: two in this project's history held an indented vitest
tally. The summary line stays chat-only because in a commit message a pasted
summary is how people state the result.

What is deliberately NOT changed: the resolution rules. `claims.extract_claims`
decides which claim survives, using the same most-specific-then-last-position
policy as commit messages. That policy was designed for a short message and a
long chat turn can hold several genuinely distinct claims, which stays
ambiguous here. Inventing a second resolution policy for an unmeasured problem
would add risk without evidence.
"""

import re
from typing import List

from .claims import extract_claims as _extract_claims
from .models import Claim
from .quoting import strip_quoted_blocks

# A line carrying pytest's own summary, decorated or not. These get pasted
# without any block markup constantly, which is exactly the 3% case.
_SUMMARY_LINE = re.compile(
    r"^.*\b\d+\s+(?:passed|failed|skipped|xfailed|xpassed|errors?|warnings?|deselected)\b"
    r".*\bin\s+\d+(?:\.\d+)?s.*$",
    re.MULTILINE,
)


def strip_quoted_output(text: str) -> str:
    """Removes the shapes that are quotation rather than assertion.

    Order matters: blocks go first, so their contents cannot be partially
    matched by the summary-line pattern afterwards.
    """
    text = strip_quoted_blocks(text)
    text = _SUMMARY_LINE.sub("", text)
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
