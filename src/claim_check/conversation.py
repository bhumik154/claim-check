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


def strip_quoted_output(text: str) -> str:
    """Removes the shapes that are quotation rather than assertion.

    Order matters: fenced blocks go first, so their contents cannot be
    partially matched by the narrower patterns afterwards.
    """
    text = _FENCED.sub("\n", text)
    text = _BLOCKQUOTE.sub("", text)
    text = _INDENTED.sub("", text)
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
