"""Finds the parts of a text that are quoted rather than asserted.

A phrase like "22 passed" inside quotation marks or a code span is almost
always an example being talked about, not a result being reported. Measured
across every real end-of-turn message and commit message on the author's
machine: not one genuine claim was written in double quotes, while about 35
quoted ones were examples ("claims like "22 passed"", "an honest "all 150
tests pass" got flagged"). Checking those blocked true commits.

Code spans are the one place this costs something. Three genuine counts in
that corpus were written in backticks (`496 passed`), against about eight
examples, and nothing in the text tells the two apart. They are treated as
quotation all the same: missing a check is the safe direction, and the
honest-test-claims skill tells agents to state counts as plain prose.

One pass per kind of quotation, each linear in the length of the text. This
runs inside hooks with a time budget, and a regex with nested quantifiers
already went quadratic once in this project.
"""

import bisect
import re
from typing import List, Tuple

Region = Tuple[int, int]

# A fenced block, closed or not. An unterminated fence swallows everything
# after it on purpose: if the quotation has no end, there is no way to tell
# where prose resumes, and missing a claim costs a check while inventing one
# costs the user's trust.
_FENCED = re.compile(r"```.*?(?:```|\Z)", re.DOTALL)

# Markdown's indented code block.
_INDENTED = re.compile(r"^(?: {4}|\t).*$", re.MULTILINE)

# A quotation of someone or something else.
_BLOCKQUOTE = re.compile(r"^\s*>.*$", re.MULTILINE)

# Tallies from runners other than pytest. Captured from a real vitest 2.1.9
# run, whose summary is two lines with no "in Xs" duration:
#
#      Test Files  1 failed | 1 passed (2)
#           Tests  1 failed | 3 passed (4)
#
# Quoting one of those is no more an assertion than quoting pytest, and the
# "Test Files" line is worse than a plain false positive: its count is FILES,
# so reading it as a claim reports 1 passed when 3 tests passed.
#
# Whitespace here is spaces and tabs only. With \s, a line reading just
# "Tests:" matched through the newline into the next line, so "Tests:" followed
# by "22 passed" was removed as if it were vitest output. And the line has to
# end the way a real tally does, vitest's "(4)" or jest's "6 total"; otherwise
# a plain "Tests: 22 passed" is someone stating a result.
_OTHER_RUNNER_TALLY = re.compile(
    r"^[ \t]*(?:Test Files|Tests|Test Suites|Suites|Snapshots|Time)[ \t]*:?[ \t]+.*\b\d+[ \t]+"
    r"(?:passed|failed|skipped|todo|pending|total)\b.*(?:\(\d+\)|\b\d+[ \t]+total)[ \t]*\r?$",
    re.MULTILINE | re.IGNORECASE,
)

_BACKTICK_RUN = re.compile(r"`+")
_DOUBLE_OR_BREAK = re.compile(r"[\"“”]|\r?\n[ \t]*\r?\n")
_SINGLE_OR_NEWLINE = re.compile(r"['‘’\n]")
_OPENS_AFTER = set(" \t\r\n([{")


def strip_quoted_blocks(text: str) -> str:
    """Removes whole blocks of quoted material: fenced and indented code,
    blockquotes, and other runners' tallies.

    Order matters: fenced blocks go first, so their contents cannot be
    partially matched by the narrower patterns afterwards.
    """
    text = _FENCED.sub("\n", text)
    text = _BLOCKQUOTE.sub("", text)
    text = _INDENTED.sub("", text)
    text = _OTHER_RUNNER_TALLY.sub("", text)
    return text


def quoted_block_regions(text: str) -> List[Region]:
    """The spans `strip_quoted_blocks` would remove, without removing them.

    The commit path marks blocks instead of deleting them. Deleting joins the
    text on either side, and the claim patterns match across the join: "22",
    an indented line, then "passed" becomes "22", a blank line, "passed", a
    claim nobody wrote.
    Marking can only ever remove a claim.
    """
    return [
        m.span()
        for pattern in (_FENCED, _BLOCKQUOTE, _INDENTED, _OTHER_RUNNER_TALLY)
        for m in pattern.finditer(text)
    ]


def quoted_regions(text) -> List[Region]:
    """Sorted, non-overlapping (start, end) spans of quoted text, quote marks
    included.

    - A code span is a run of N backticks closed by the next run of exactly N,
      as in Markdown. It may span lines. An unmatched run is literal text.
    - Double quotes, straight or curly, pair in order within a paragraph, so a
      quotation can wrap across lines. One left open runs to the end of its
      paragraph: past that there is no telling where it ends.
    - Single quotes, straight or curly, pair only on one line, and only where
      they cannot be an apostrophe: the opener follows a space or bracket, the
      closer is not followed by a letter or digit.

    Quote marks inside a code span, and single quotes inside double quotes,
    are part of the outer quotation and not counted separately.
    """
    if not isinstance(text, str) or not text:
        return []
    code = _code_spans(text)
    doubles = _double_quotes(text, code)
    singles = _single_quotes(text, sorted(code + doubles))
    return merged(code + doubles + singles)


def merged(regions: List[Region]) -> List[Region]:
    """Sorted union of possibly overlapping regions. A code span inside a
    double quote, `"see `x` here"`, is one quotation."""
    merged: List[Region] = []
    for start, end in sorted(regions):
        if merged and start < merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


def _code_spans(text: str) -> List[Region]:
    runs = [(m.start(), m.end()) for m in _BACKTICK_RUN.finditer(text)]
    by_length = {}
    for index, (start, end) in enumerate(runs):
        by_length.setdefault(end - start, []).append(index)

    spans = []
    index = 0
    while index < len(runs):
        start, end = runs[index]
        same = by_length[end - start]
        later = bisect.bisect_right(same, index)
        if later == len(same):
            index += 1
            continue
        closer = same[later]
        spans.append((start, runs[closer][1]))
        index = closer + 1
    return spans


class _Outside:
    """Answers "is this position outside every region?" for positions that
    only ever increase, in constant amortised time."""

    def __init__(self, regions: List[Region]):
        self._regions = regions
        self._next = 0

    def __call__(self, position: int) -> bool:
        while self._next < len(self._regions) and self._regions[self._next][1] <= position:
            self._next += 1
        if self._next == len(self._regions):
            return True
        return position < self._regions[self._next][0]


def _double_quotes(text: str, code: List[Region]) -> List[Region]:
    """Outermost double-quoted regions.

    Straight quotes are paired by which way they face, not just in order.
    Quoting a quotation is common in exactly the text this reads, "people
    don't write "22 passed" in prose", and pairing in order there leaves the
    inner example outside every quote. A mark after a space facing a word
    opens; a mark after a word facing a space or punctuation closes. A mark
    that could only close, with nothing open, is ignored: that is the inch
    mark in `5" screen`, which must not quote the rest of the paragraph.
    """
    outside = _Outside(code)
    regions = []
    depth = 0
    start = 0
    for m in _DOUBLE_OR_BREAK.finditer(text):
        position = m.start()
        if not outside(position):
            continue
        mark = m.group()
        if len(mark) > 1:
            # A blank line: whatever is still open ends with its paragraph.
            if depth:
                regions.append((start, position))
                depth = 0
            continue
        if mark == "“":
            opens = True
        elif mark == "”":
            opens = False
        else:
            before = text[position - 1] if position else " "
            after = text[position + 1] if position + 1 < len(text) else " "
            can_open = (before.isspace() or before in "([{") and not after.isspace()
            can_close = not before.isspace() and not (after.isalnum() or after == "_")
            if can_open and not can_close:
                opens = True
            elif can_close and not can_open:
                if not depth:
                    continue
                opens = False
            else:
                opens = not depth
        if opens:
            if not depth:
                start = position
            depth += 1
        elif depth:
            depth -= 1
            if not depth:
                regions.append((start, position + 1))
    if depth:
        regions.append((start, len(text)))
    return regions


def _single_quotes(text: str, quoted: List[Region]) -> List[Region]:
    outside = _Outside(quoted)
    regions = []
    opened = None  # (start, closing character)
    for m in _SINGLE_OR_NEWLINE.finditer(text):
        position = m.start()
        mark = m.group()
        if mark == "\n":
            opened = None
            continue
        if not outside(position):
            continue
        after = text[position + 1] if position + 1 < len(text) else ""
        if opened is not None and mark == opened[1] and not (after.isalnum() or after == "_"):
            regions.append((opened[0], position + 1))
            opened = None
            continue
        before = text[position - 1] if position else " "
        if mark in "'‘" and before in _OPENS_AFTER and after and not after.isspace():
            # A later valid opener replaces an earlier one that never closed,
            # so one stray mark cannot hide the rest of its line.
            opened = (position, "'" if mark == "'" else "’")
    return regions
